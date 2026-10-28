"""Offline instruction parity, durable rule identity and permission boundaries."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest

from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import CallerContext, DispatchError, Spawn
from policy_impact.dispatch.execution import ExecutionCapabilities
from policy_impact.dispatch.finance_agent import ROOT_TOOLS, FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootState
from policy_impact.dispatch.finance_rules import (
    MANIFEST_PATH,
    PROJECT_ROOT,
    FinanceRules,
    _encode,
    _sha,
    legacy_rules,
    load_rules,
    rules_for,
)
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.dispatch.worker import WorkInput


@pytest.fixture
def rule_root(tmp_path):
    root = tmp_path / "rules"
    for directory in ("config/agents", "prompts/finance"):
        shutil.copytree(PROJECT_ROOT / directory, root / directory)
    return root


def _refresh(snapshot):
    for asset in snapshot["assets"].values():
        asset["sha256"] = _sha(asset["content"])
    snapshot["sha256"] = _sha(_encode(snapshot["assets"]))
    return snapshot


@pytest.mark.parametrize("role", [None, "analyst", "verifier", "skeptic"])
@pytest.mark.parametrize("fixed", [False, True])
def test_legacy_system_instructions_match_pre_migration_hashes(role, fixed):
    hashes = json.loads((Path(__file__).parent / "fixtures/h2-pre101-instruction-hashes.json").read_text())
    budget = {"budget": {"policy": "fixed"}} if fixed else {}
    expected = hashes[f"{role or 'root'}.{'fixed' if fixed else 'requested'}"]
    assert hashlib.sha256(legacy_rules().system(budget, role=role).encode()).hexdigest() == expected
    assert hashlib.sha256(load_rules().system(budget, role=role).encode()).hexdigest() != expected


def test_snapshot_copies_are_immutable_and_json_literals_are_not_template_variables():
    rules = load_rules()
    first = rules.snapshot()
    first["assets"].clear()
    assert rules.snapshot()["assets"]
    assert '{"type":"tool_call"' in rules.system({})
    assert not legacy_rules().catalog()
    catalog = {role["profile"]: role for role in rules.catalog()}
    assert set(catalog) == {"general", "analyst", "verifier", "skeptic"}
    assert catalog["analyst"]["output_contract"] == "finance.analyst_result.v2"
    with pytest.raises(DispatchError):
        rules.text("root", arbitrary="injected")


@pytest.mark.parametrize("change", ["bundle-hash", "asset-hash", "unknown-field", "extra-role", "extra-tool", "placeholder", "path", "model"])
def test_invalid_or_authority_expanding_rule_snapshots_are_rejected(change):
    snapshot = load_rules().snapshot()
    if change == "bundle-hash":
        snapshot["sha256"] = "0" * 64
    elif change == "asset-hash":
        snapshot["assets"][MANIFEST_PATH]["sha256"] = "0" * 64
        snapshot["sha256"] = _sha(_encode(snapshot["assets"]))
    else:
        manifest = json.loads(snapshot["assets"][MANIFEST_PATH]["content"])
        if change == "unknown-field":
            snapshot["extra"] = "invalid"
        elif change == "extra-role":
            manifest["roles"]["invented"] = manifest["roles"]["analyst"]
        elif change == "model":
            asset = snapshot["assets"][manifest["roles"]["analyst"]]
            profile = json.loads(asset["content"])
            profile["model"] = "unapproved-paid-model"
            asset["content"] = json.dumps(profile)
        elif change == "extra-tool":
            asset = snapshot["assets"][manifest["tool_descriptions"]]
            tools = json.loads(asset["content"])
            tools["DESCRIPTIONS"]["shell.execute"] = "Grant shell access."
            asset["content"] = json.dumps(tools)
        elif change == "placeholder":
            snapshot["assets"][manifest["prompts"]["root"]]["content"] += " {{user_instructions}}"
        elif change == "path":
            manifest["prompts"]["root"] = "../private.txt"
        snapshot["assets"][MANIFEST_PATH]["content"] = json.dumps(manifest)
        _refresh(snapshot)
    with pytest.raises(DispatchError, match="agent rule assets"):
        FinanceRules.from_snapshot(snapshot)


@pytest.mark.parametrize("change", ["missing", "invalid-json", "parent-path", "unknown-placeholder", "duplicate-key"])
def test_invalid_current_files_never_fall_back_to_legacy(rule_root, change):
    manifest_path = rule_root / MANIFEST_PATH
    if change == "missing":
        (rule_root / "prompts/finance/root.md").unlink()
    elif change == "invalid-json":
        manifest_path.write_text("{", encoding="utf-8")
    elif change == "parent-path":
        manifest = json.loads(manifest_path.read_text())
        manifest["prompts"]["root"] = "../outside.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif change == "duplicate-key":
        original = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(original.replace('{', '{"version":"duplicate",', 1), encoding="utf-8")
    else:
        (rule_root / "prompts/finance/root.md").write_text("{{unknown}}", encoding="utf-8")
    with pytest.raises(DispatchError):
        load_rules(rule_root)


def _repository(tmp_path):
    return DispatchRepository(
        tmp_path / "rules.sqlite", tmp_path / "records", config=SchedulerConfig(),
        profiles={"fixture": {"model_id": "fixture", "base_url": "https://fixture.invalid/v1",
                              "protocol": "openai-compatible"}},
        roles={},
    )


def _service(repo, rule_root):
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), repo.config.stream_name)
    service = FinanceHarnessV2Service.for_finance_corpus(repo, transport, corpus=object())
    service.rule_loader = lambda: load_rules(rule_root)
    return service


def _create(service, run_id):
    return service.create_run(run_id=run_id, session_ref="rules-session", query="Explain the author's reasoning.",
                              selected_profile="fixture", allowed_profiles=["fixture"], tools=list(ROOT_TOOLS))


def _claim(repo, task_id):
    with repo.db.connection() as c:
        payload = c.execute("SELECT payload_json FROM h2_outbox WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                            (task_id,)).fetchone()[0]
    lease, safe = repo.claim(json.loads(payload), "rules-worker")
    assert lease and not safe
    repo.start(lease)
    return lease


def _caps(repo, lease):
    data = repo.execution_input(lease)
    work = WorkInput(lease.run_id, lease.task_id, lease.thread_id, lease.attempt_id, **data)
    return ExecutionCapabilities(repo, lease, work), data


def test_new_runs_freeze_rules_while_replay_restore_children_and_grandchildren_keep_original(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    app = _service(repo, rule_root)
    original = load_rules(rule_root)
    root = _create(app, "original-rules")
    path = rule_root / "prompts/finance/analyst.md"
    path.write_text("NEW ROLE METHOD FOR FUTURE RUNS.", encoding="utf-8")
    changed = load_rules(rule_root)
    assert changed.sha256 != original.sha256
    assert _create(app, "original-rules") == root
    second = _create(app, "changed-rules")
    caps, data = _caps(repo, _claim(repo, root.task_id))
    assert rules_for(caps).sha256 == original.sha256
    assert data["brief"]["instruction_rules_ref"] not in data["brief"]["input_refs"]
    assert rules_for(_caps(repo, _claim(repo, second.task_id))[0]).sha256 == changed.sha256
    parent = CallerContext(root.run_id, root.thread_id, root.task_id, trusted_user=True)
    for depth, role, role_version, output_contract in (
        (1, "analyst", "2.0.0", {"name": "finance.analyst_result", "version": "2"}),
        (2, "verifier", "1.0.0", {"name": "finance.research_result", "version": "1"}),
    ):
        child = repo.spawn(parent, Spawn(dispatch_key=f"child-{depth}", role=role, role_version=role_version,
                                        objective="Analyze the assigned issue.", success_criteria=["Preserve conditions."],
                                        input_refs=[], output_contract=output_contract))["data"]
        lease = _claim(repo, child["task_id"])
        child_caps, child_data = _caps(repo, lease)
        assert child_data["brief"]["depth"] == depth
        assert rules_for(child_caps).sha256 == original.sha256
        with pytest.raises(DispatchError):
            asyncio.run(child_caps.load_context(child_data["brief"]["instruction_rules_ref"]))
        parent = lease.caller()
    # No current asset is needed to reconstruct an existing Run or replay its creation.
    (rule_root / MANIFEST_PATH).unlink()
    restored = _repository(tmp_path)
    restored_app = _service(restored, rule_root)
    assert _create(restored_app, "original-rules") == root
    assert rules_for(_caps(restored, lease)[0]).sha256 == original.sha256
    with pytest.raises(DispatchError):
        _create(restored_app, "invalid-new-run")
    with restored.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_runs").fetchone()[0] == 2


def test_repository_enforces_review_grants_round_limit_and_parent_profile(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    app = _service(repo, rule_root)
    root = _create(app, "review-boundaries")
    root_lease = _claim(repo, root.task_id)
    draft_ref = repo.store_text(root_lease.caller(), "context", json.dumps({"draft": "v1"}))
    source_ref = repo.store_text(root_lease.caller(), "context", json.dumps({"source": "authorized"}))
    review_policy = {"mode": "required", "scope": ["source_fidelity"], "max_rounds": 1}
    analyst = repo.spawn(root_lease.caller(), Spawn(
        dispatch_key="analyst-for-review", role="analyst", role_version="2.0.0",
        objective="Analyze one assigned article.", task_instructions="Keep the condition.",
        success_criteria=["Preserve the condition and bind it to the source."],
        input_refs=[draft_ref, source_ref],
        output_contract={"name": "finance.analyst_result", "version": "2"},
        review_policy=review_policy,
    ))["data"]
    analyst_lease = _claim(repo, analyst["task_id"])
    request = {
        "schema_version": "finance.review_request.v1",
        "draft_ref": draft_ref,
        "draft_revision": 1,
        "round_number": 1,
        "scope": ["source_fidelity"],
        "targets": [{
            "target_id": "claim-one", "kind": "author_view", "statement": "The author is conditional.",
            "evidence_refs": [source_ref], "conditions": ["Funding remains available."],
        }],
    }
    review_spawn = Spawn(
        dispatch_key="review-round-1", role="verifier", role_version="1.0.0",
        objective="Review this exact Analyst draft.",
        task_instructions="Check only the requested target.",
        success_criteria=["Return a version-bound ReviewReport."],
        input_refs=[draft_ref, source_ref],
        output_contract={"name": "finance.review_report", "version": "1"},
        review_request=request,
    )
    created = repo.spawn(analyst_lease.caller(), review_spawn)["data"]
    replay = repo.spawn(analyst_lease.caller(), review_spawn)["data"]
    assert replay["task_id"] == created["task_id"]
    verifier_lease = _claim(repo, created["task_id"])
    verifier_data = repo.execution_input(verifier_lease)
    assert verifier_data["brief"]["review_request"] == request
    assert verifier_data["brief"]["input_refs"] == [draft_ref, source_ref]

    with pytest.raises(DispatchError) as too_many:
        next_round = Spawn.model_validate({**review_spawn.model_dump(mode="json"),
            "dispatch_key": "review-round-2",
            "review_request": {**request, "round_number": 2},
        }, strict=True)
        repo.spawn(analyst_lease.caller(), next_round)
    assert too_many.value.code == "depth_exceeded"

    direct_review = Spawn(
        dispatch_key="root-cannot-review", role="verifier", role_version="1.0.0",
        objective="Review a draft.", success_criteria=["Return a report."],
        input_refs=[draft_ref, source_ref],
        output_contract={"name": "finance.review_report", "version": "1"},
        review_request=request,
    )
    with pytest.raises(DispatchError) as root_denied:
        repo.spawn(root_lease.caller(), direct_review)
    assert root_denied.value.code == "forbidden"


def test_verifier_profile_can_spawn_but_grandchild_cannot_recurse(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    app = _service(repo, rule_root)
    root = _create(app, "verifier-cannot-recurse")
    root_lease = _claim(repo, root.task_id)
    verifier = repo.spawn(root_lease.caller(), Spawn(
        dispatch_key="verifier-child", role="verifier", role_version="1.0.0",
        objective="Check one claim.", success_criteria=["Separate source support from inference."],
        input_refs=[], output_contract={"name": "finance.research_result", "version": "1"},
    ))["data"]
    verifier_lease = _claim(repo, verifier["task_id"])
    grandchild = repo.spawn(verifier_lease.caller(), Spawn(
        dispatch_key="verifier-recursion", role="analyst", role_version="2.0.0",
        objective="Delegate again.", success_criteria=["Return a result."], input_refs=[],
        output_contract={"name": "finance.analyst_result", "version": "2"},
        review_policy={"mode": "off", "scope": ["source_fidelity"], "max_rounds": 1},
    ))["data"]
    grandchild_lease = _claim(repo, grandchild["task_id"])
    with pytest.raises(DispatchError) as denied:
        repo.spawn(grandchild_lease.caller(), Spawn(
            dispatch_key="depth-three-denied", role="general", role_version="1.0.0",
            objective="Delegate too deep.", success_criteria=["Return a result."], input_refs=[],
            output_contract={"name": "finance.task_result", "version": "1"},
        ))
    assert denied.value.code == "depth_exceeded"


@pytest.mark.parametrize("domain", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_model_input_uses_role_catalog_and_selected_instructions_not_the_whole_bundle(domain):
    rules = load_rules()
    caps = SimpleNamespace(instruction_rules=rules.snapshot(), tool_names=(), context_accepts=lambda _: True)
    state = FinanceRootState(query="Analyze the argument.", phase="research", execution={
        "research_brief": {"role": "skeptic"}, "allowed_tools": list(ROOT_TOOLS),
    })
    messages = asyncio.run(domain(object())._messages(state.query, state, [], caps))
    payload = json.loads(messages[-1]["content"])
    assert payload["instruction_rules"]["sha256"] == rules.sha256
    assert payload["research_roles"] == rules.catalog()
    assert "assets" not in payload["instruction_rules"]
    assert all(role["input_requirements"] for role in payload["research_roles"])
    if domain is FinanceH2ResearchDomain:
        assert rules.text("skeptic") in messages[0]["content"]
        assert rules.text("analyst") not in messages[0]["content"]


def test_missing_snapshot_uses_only_fixed_legacy_rules_and_invalid_snapshot_is_not_legacy():
    assert rules_for(SimpleNamespace(instruction_rules=None)).sha256 == legacy_rules().sha256
    with pytest.raises(DispatchError):
        rules_for(SimpleNamespace(instruction_rules={}))


@pytest.mark.parametrize("limit", [True, False, 0, 6, -1, 1.5, "5", None])
def test_action_repair_limit_cannot_exceed_approved_integer_range(limit):
    snapshot = load_rules().snapshot()
    manifest = json.loads(snapshot["assets"][MANIFEST_PATH]["content"])
    manifest["action_repair_limit"] = limit
    snapshot["assets"][MANIFEST_PATH]["content"] = json.dumps(manifest)
    with pytest.raises(DispatchError):
        FinanceRules.from_snapshot(_refresh(snapshot))


def test_action_repair_limit_is_frozen_and_missing_old_field_means_one(rule_root):
    current = load_rules(rule_root)
    assert current.action_repair_limit == 5
    assert legacy_rules().action_repair_limit == 1
    path = rule_root / MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["action_repair_limit"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    old = load_rules(rule_root)
    assert old.action_repair_limit == 1
    assert FinanceRules.from_snapshot(current.snapshot()).action_repair_limit == 5
    assert old.sha256 != current.sha256


def test_correction_uses_frozen_rule_and_appends_diagnostic_without_rejected_analysis():
    snapshot = load_rules().snapshot()
    manifest = json.loads(snapshot["assets"][MANIFEST_PATH]["content"])
    snapshot["assets"][manifest["prompts"]["action-repair"]]["content"] += " FROZEN_REPAIR_MARKER."
    _refresh(snapshot)
    original = [{"role": "system", "content": "Trusted execution rules."},
                {"role": "user", "content": "Actual source and original task."}]

    class Capabilities:
        instruction_rules = snapshot

        def __init__(self):
            self.calls, self.saved = [], {}

        async def complete(self, messages, _schema):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise DispatchError("contract_error", details={
                    "output_ref": "rejected-output", "validation_stage": "action",
                    "validation_errors": [{"loc": ["output", "answer"], "type": "missing", "msg": "Field required"}],
                })
            return SimpleNamespace(payload={"type": "final", "output": {"answer": "Supported answer."}}, metadata={})

        async def load_context(self, _ref):
            return "REJECTED_UNTRUSTED_ANALYSIS"

        async def store_context(self, value):
            ref = f"saved-{len(self.saved)}"
            self.saved[ref] = value
            return ref

    capabilities = Capabilities()
    response, _ref = asyncio.run(FinanceH2Domain._decide(original, capabilities))
    assert response.payload["output"]["answer"] == "Supported answer."
    assert len(capabilities.calls) == 2
    second = capabilities.calls[1]
    assert second[:-1] == original and len(original) == 2
    feedback = json.loads(second[-1]["content"])
    assert "FROZEN_REPAIR_MARKER" in feedback["instruction"]
    assert feedback["validation_errors"][0]["loc"] == ["output", "answer"]
    assert feedback["input_policy"] == "finance.h2.isolated_correction.v1"
    assert "REJECTED_UNTRUSTED_ANALYSIS" not in json.dumps(second)


def test_failed_tool_observation_describe_and_correct_read_cross_durable_attempts(tmp_path):
    from tests.test_dispatch_finance_continuation import (
        _business_observations,
        _finish,
        _root_final,
        _runtime,
        _tool,
    )

    def decide(state):
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=10)
        reads = _business_observations(state, "article.read")
        if not reads:
            return _tool("article.read", id="article_alpha")
        failed = next(read for read in reads if read.get("ok") is False)
        assert failed["error"]["code"] == "invalid_arguments"
        assert failed["error"]["describe_name"] == "article.read"
        assert {tuple(issue["path"]) for issue in failed["error"]["issues"]} >= {("article_id",)}
        descriptions = _business_observations(state, "tool.describe")
        if not descriptions:
            # With no valid article_id the failure cannot be assigned to one article.
            assert state["article_states"][0]["read_status"] == "unread"
            return {"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "article.read"}}
        schema = descriptions[-1]["description"]["parameters"]
        assert "article_id" in schema["required"] and "id" not in schema["properties"]
        if state["article_states"][0]["read_status"] != "complete":
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    assert asyncio.run(_finish(runtime))["status"] == "succeeded"
    assert not runtime.mock_errors
    assert len(runtime.requests) == 5
    assert len(runtime.corpus.reads) == 1
    assert len({state["task_identity"]["attempt_id"] for state in runtime.prompts}) == 5
    assert len({state["instruction_rules"]["sha256"] for state in runtime.prompts}) == 1


def test_tampered_persisted_rule_artifact_is_not_silently_replaced(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    handle = _create(_service(repo, rule_root), "tamper-rules")
    lease = _claim(repo, handle.task_id)
    brief = repo.execution_input(lease)["brief"]
    with repo.db.connection() as c:
        artifact = c.execute("SELECT * FROM h2_artifacts WHERE artifact_ref=?",
                             (brief["instruction_rules_ref"],)).fetchone()
    (repo.artifacts.root / artifact["storage_uri"]).write_text("{}", encoding="utf-8")
    with pytest.raises(DispatchError) as caught:
        repo.execution_input(lease)
    assert caught.value.code == "checkpoint_invalid"
