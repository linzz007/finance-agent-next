"""General tasks and specialist extensions share the durable execution path."""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from policy_impact.dispatch.contracts import DispatchError, SchedulerConfig, Spawn
from policy_impact.dispatch.finance_agent import ROOT_TOOLS, FinanceH2ChildExecutor, FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_rules import MANIFEST_PATH, FinanceRules, load_rules
from policy_impact.dispatch.worker import WorkInput
from policy_impact.runtime.research_contracts import TaskResult
from tests.test_dispatch_finance_continuation import (
    _business_observations,
    _child_results,
    _finish,
    _root_final,
    _runtime,
    _scheduler_observations,
    _tool,
)
from tests.test_dispatch_finance_review_loop import (
    SOURCE_TEXT,
    ReviewCapabilities,
    _analyst_result,
    _context,
    _dispatch_and_return_report,
)
from tests.test_dispatch_finance_rules import (
    _caps,
    _claim,
    _create,
    _refresh,
    _repository,
    _service,
)
from tests.test_dispatch_finance_rules import (
    rule_root as shared_rule_root,
)


@pytest.fixture
def rule_root(tmp_path):
    return shared_rule_root.__wrapped__(tmp_path)


def _spawn(key, role="general", **overrides):
    analyst = role == "analyst"
    return Spawn.model_validate({
        "dispatch_key": key, "role": role, "role_version": "2.0.0" if analyst else "1.0.0",
        "objective": "Independently examine the assigned argument.",
        "task_instructions": "Act as a blind method reviewer. Separate assumptions and evidence.",
        "success_criteria": ["Explain a concrete limitation and its implications."],
        "input_refs": [],
        "output_contract": {"name": "finance.analyst_result" if analyst else (
            "finance.task_result" if role == "general" else "finance.research_result"),
            "version": "2" if analyst else "1"},
        **overrides,
    }, strict=True)


def _public_spawn(task_name, role="general", *, input_refs=None, review_mode=None):
    arguments = {
        "task_name": task_name,
        "message": "Independently examine the assigned argument.",
        "profile": role,
        "task_instructions": "Act as a blind method reviewer. Separate assumptions and evidence.",
        "input_refs": list(input_refs or []),
    }
    if role != "general":
        arguments["success_criteria"] = ["Explain a concrete limitation and its implications."]
    if review_mode is not None:
        arguments["profile_options"] = {"review_mode": review_mode}
    return arguments


@pytest.mark.parametrize("role", ["general", "analyst", "verifier", "skeptic"])
def test_every_profile_delegates_one_remaining_level_without_expanding_tools(tmp_path, rule_root, role):
    repo = _repository(tmp_path)
    repo.migrate()
    root = _create(_service(repo, rule_root), "all-profiles")
    root_lease = _claim(repo, root.task_id)
    child = repo.spawn(root_lease.caller(), _spawn("child", role))["data"]
    child_lease = _claim(repo, child["task_id"])
    child_input = repo.execution_input(child_lease)
    assert "agent.spawn" in child_input["brief"]["allowed_tools"]
    assert set(child_input["brief"]["allowed_tools"]) <= set(ROOT_TOOLS)
    grandchild = repo.spawn(child_lease.caller(), _spawn("grandchild"))["data"]
    grandchild_lease = _claim(repo, grandchild["task_id"])
    assert repo.execution_input(grandchild_lease)["brief"]["depth"] == 2
    with pytest.raises(DispatchError) as rejected:
        repo.spawn(grandchild_lease.caller(), _spawn("too-deep"))
    assert rejected.value.code == "depth_exceeded"


@pytest.mark.parametrize("mode", [None, "required", "auto", "off"])
def test_simple_review_mode_defaults_to_five_and_persists_normalized_policy(tmp_path, rule_root, mode):
    repo = _repository(tmp_path)
    repo.migrate()
    root = _create(_service(repo, rule_root), "review-mode")
    lease = _claim(repo, root.task_id)
    request = _spawn("analyst", "analyst", **({"review_mode": mode} if mode else {}))
    child = repo.spawn(lease.caller(), request)["data"]
    assert repo.spawn(lease.caller(), request)["data"]["task_id"] == child["task_id"]
    brief = repo.execution_input(_claim(repo, child["task_id"]))["brief"]
    assert brief["review_policy"] == {
        "mode": mode or "auto", "max_rounds": 5,
        "scope": ["source_fidelity", "reasoning_conditions"],
    }


@pytest.mark.parametrize("instructions", [
    "Check the publication date; disclose unavailable market data.", "x" * 4096,
])
def test_optional_review_method_reaches_verifier_brief(tmp_path, rule_root, instructions):
    repo = _repository(tmp_path)
    repo.migrate()
    root = _create(_service(repo, rule_root), "review-options")
    lease = _claim(repo, root.task_id)
    request = _spawn("analyst", "analyst", review_mode="required", review_options={
        "scope": ["source_fidelity", "external_facts"], "max_rounds": 5,
        "instructions": instructions,
    })
    child = repo.spawn(lease.caller(), request)["data"]
    brief = repo.execution_input(_claim(repo, child["task_id"]))["brief"]
    ctx = _context(policy=brief["review_policy"])
    caps = ReviewCapabilities()
    step = asyncio.run(FinanceH2ResearchDomain(None)._final(ctx, ctx.state, _analyst_result(), caps))
    prepared = step.state.execution["review_workflow"]["prepared_spawn"]
    assert request.review_options.instructions in prepared["task_instructions"]
    assert prepared["review_request"]["scope"] == request.review_options.scope


@pytest.mark.parametrize("values", [
    {"review_mode": "required", "review_policy": {"mode": "off"}},
    {"review_options": {"max_rounds": 6}},
    {"review_options": {"max_rounds": True}},
    {"review_options": {"scope": ["external_facts", "external_facts"]}},
    {"review_options": {"instructions": "   "}},
])
def test_review_options_reject_ambiguous_or_invalid_input(values):
    with pytest.raises(ValidationError):
        _spawn("invalid", "analyst", **values)


def test_general_profile_rejects_article_review_controls(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    root = _create(_service(repo, rule_root), "general-review-guard")
    lease = _claim(repo, root.task_id)
    with pytest.raises(DispatchError) as rejected:
        repo.spawn(lease.caller(), _spawn("invalid", review_mode="required"))
    assert rejected.value.code == "invalid_arguments"


def test_depth_two_analyst_preserves_work_when_independent_review_is_impossible():
    ctx = _context(policy={"mode": "required", "max_rounds": 5})
    ctx = replace(ctx, brief={**ctx.brief, "depth": 2})
    caps = ReviewCapabilities()
    step = asyncio.run(FinanceH2ResearchDomain(None)._final(ctx, ctx.state, _analyst_result(), caps))
    assert step.status == "partial"
    assert "review_depth_limit_reached" in step.submission.warnings
    assert not caps.spawned
    assert caps.artifacts[step.submission.full_ref]["result"]["content"]


def test_general_final_schema_is_selected_by_contract(monkeypatch):
    selected = {}

    async def capture(_messages, _capabilities, **kwargs):
        selected.update(kwargs)
        return None, None

    monkeypatch.setattr(FinanceH2Domain, "_decide", staticmethod(capture))
    user = {"role": "general", "output_contract": {"name": "finance.task_result", "version": "1"}}
    asyncio.run(FinanceH2ResearchDomain._decide(
        [{"role": "user", "content": json.dumps(user)}], ReviewCapabilities(),
    ))
    assert selected["final_output_model"] is TaskResult
    assert set(TaskResult.model_json_schema()["required"]) == {"schema_version", "content"}


@pytest.mark.parametrize("change", [
    "input-as-output", "output-as-input", "root-as-method", "unknown-tool", "specialist-workflow",
])
def test_portable_profile_cannot_declare_unimplemented_contracts_or_template_authority(change):
    snapshot = load_rules().snapshot()
    path = "config/agents/profiles/general.json"
    profile = json.loads(snapshot["assets"][path]["content"])
    if change == "input-as-output":
        profile["output_contracts"] = ["finance.review_request@1"]
    elif change == "output-as-input":
        profile["input_contracts"] = ["finance.task_result@1"]
    elif change == "root-as-method":
        profile["instructions"] = "root"
    elif change == "specialist-workflow":
        profile["output_contracts"] = ["finance.review_report@1"]
    else:
        profile["allowed_tools"].append("shell.execute")
    snapshot["assets"][path]["content"] = json.dumps(profile)
    with pytest.raises(DispatchError):
        FinanceRules.from_snapshot(_refresh(snapshot))


def test_old_v2_snapshot_keeps_original_profile_permissions_and_default_three(tmp_path, rule_root):
    snapshot = load_rules(rule_root).snapshot()
    manifest = json.loads(snapshot["assets"][MANIFEST_PATH]["content"])
    manifest["schema_version"] = "finance.h2.agent_rules.v2"
    manifest["prompts"].pop("general")
    manifest["roles"] = {name: json.loads(snapshot["assets"][path]["content"])
                         for name, path in manifest["roles"].items() if name != "general"}
    for name, profile in manifest["roles"].items():
        profile["allowed_child_profiles"] = ["verifier"] if name == "analyst" else []
        if name != "analyst":
            profile["allowed_tools"] = [tool for tool in profile["allowed_tools"]
                                        if not tool.startswith(("agent.", "result."))]
    manifest["root_profile"]["allowed_child_profiles"] = list(manifest["roles"])
    snapshot["assets"] = {path: asset for path, asset in snapshot["assets"].items()
                          if not path.startswith("config/agents/profiles/")
                          and path != "prompts/finance/general.md"}
    snapshot["assets"][MANIFEST_PATH]["content"] = json.dumps(manifest)
    old = FinanceRules.from_snapshot(_refresh(snapshot))
    assert old.has_profiles and not old.portable_profiles
    assert "general" not in old.manifest["roles"]
    assert "agent.spawn" not in old.role_profile("verifier")["allowed_tools"]
    repo = _repository(tmp_path)
    repo.migrate()
    service = _service(repo, rule_root)
    service.rule_loader = lambda: old
    root = _create(service, "old-v2")
    lease = _claim(repo, root.task_id)
    child = repo.spawn(lease.caller(), _spawn("old-analyst", "analyst"))["data"]
    child_lease = _claim(repo, child["task_id"])
    assert repo.execution_input(child_lease)["brief"]["review_policy"]["max_rounds"] == 3
    with pytest.raises(DispatchError):
        repo.spawn(lease.caller(), _spawn("old-no-general"))
    with pytest.raises(DispatchError):
        repo.spawn(lease.caller(), _spawn("old-no-new-fields", "analyst", review_mode="off"))


def test_profile_and_prompt_remain_frozen_across_restart(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    service = _service(repo, rule_root)
    root = _create(service, "general-frozen")
    lease = _claim(repo, root.task_id)
    child = repo.spawn(lease.caller(), _spawn("general"))["data"]
    child_lease = _claim(repo, child["task_id"])
    original = _caps(repo, child_lease)[0].instruction_rules
    (rule_root / "prompts/finance/general.md").write_text("A changed default.", encoding="utf-8")
    path = rule_root / "config/agents/profiles/general.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["allowed_child_profiles"] = []
    path.write_text(json.dumps(profile), encoding="utf-8")
    assert load_rules(rule_root).sha256 != FinanceRules.from_snapshot(original).sha256
    restored = _repository(tmp_path)
    _service(restored, rule_root)
    assert _caps(restored, child_lease)[0].instruction_rules == original


def test_adding_a_specialist_profile_needs_no_python_role_registration(tmp_path, rule_root, monkeypatch):
    path = rule_root / MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["roles"]["method_reviewer"] = "config/agents/profiles/method_reviewer.json"
    manifest["root_profile"]["allowed_child_profiles"].append("method_reviewer")
    manifest["prompts"]["method-reviewer"] = "prompts/finance/method-reviewer.md"
    profile = json.loads((rule_root / "config/agents/profiles/general.json").read_text())
    profile["instructions"] = "method-reviewer"
    profile["description"] = "Independently examine research methods."
    (rule_root / "config/agents/profiles/method_reviewer.json").write_text(json.dumps(profile), encoding="utf-8")
    (rule_root / "prompts/finance/method-reviewer.md").write_text("Check research design and its limitations.", encoding="utf-8")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    repo = _repository(tmp_path)
    repo.migrate()
    root = _create(_service(repo, rule_root), "custom-profile")
    lease = _claim(repo, root.task_id)
    child = repo.spawn(lease.caller(), _spawn("review-method", "method_reviewer", output_contract={
        "name": "finance.task_result", "version": "1",
    }))["data"]
    child_lease = _claim(repo, child["task_id"])
    caps, data = _caps(repo, child_lease)
    seen = []

    async def capture(_self, context, _caps):
        seen.append(context.brief)
        raise DispatchError("fixture_stop")

    monkeypatch.setattr(FinanceH2ResearchDomain, "__call__", capture)
    work = WorkInput(child_lease.run_id, child_lease.task_id, child_lease.thread_id, child_lease.attempt_id, **data)
    with pytest.raises(DispatchError) as stopped:
        asyncio.run(FinanceH2ChildExecutor(object())(work, caps))
    assert stopped.value.code == "fixture_stop"
    assert seen[0]["role"] == "method_reviewer"
    assert "Check research design" in FinanceRules.from_snapshot(caps.instruction_rules).system(
        {}, role="method_reviewer",
    )


def test_five_review_rounds_resume_then_sixth_is_not_dispatched():
    domain = FinanceH2ResearchDomain(None)
    caps = ReviewCapabilities()
    caps.spawn_task_ids = [f"task.verifier.{i}" for i in range(1, 6)]
    ctx = _context(policy={"mode": "required"})
    for round_number in range(1, 6):
        output = _analyst_result(content=f"{SOURCE_TEXT} Draft number {round_number}.")
        step = asyncio.run(domain._final(ctx, ctx.state, output, caps))
        prepared = step.state.execution["review_workflow"]["prepared_spawn"]
        assert prepared["review_request"]["round_number"] == round_number
        assert prepared["review_request"]["draft_revision"] == round_number
        state, _ = asyncio.run(_dispatch_and_return_report(domain, ctx, step, caps, findings=False))
        ctx = replace(ctx, state=state)
    output = _analyst_result(content=SOURCE_TEXT + " A sixth changed draft requests a check.")
    final = asyncio.run(domain._final(ctx, ctx.state, output, caps))
    assert len(caps.spawned) == 5
    assert final.status == "partial"
    assert final.state.delivery["review"]["status"] == "round_limit_reached"
    assert caps.artifacts[final.submission.full_ref]["result"]["content"] == output["content"]


def test_dynamic_general_task_and_grandchild_return_through_real_worker_loop(tmp_path):
    def decide(state):
        if state.get("role") == "general":
            assert state["task_instructions"].endswith(
                "Act as a blind method reviewer. Separate assumptions and evidence."
            )
            if state["depth"] == 1:
                spawned = _scheduler_observations(state, "agent.spawn")
                if not spawned:
                    return _tool("agent.spawn", **_public_spawn(
                        "general-follow-up", input_refs=state["material_refs"],
                    ))
                if not _child_results(state):
                    return _tool("agent.wait", task_ids=[spawned[-1]["payload"]["data"]["task_id"]])
            return {"type": "final", "output": {
                "schema_version": "finance.task_result.v1",
                "content": "The method depends on a conditional assumption; test its failure case.",
            }}
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=1)
        if not _business_observations(state, "article.read"):
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        spawned = _scheduler_observations(state, "agent.spawn")
        if not spawned:
            refs = [a["material_ref"] for a in state["article_states"] if a.get("material_ref")]
            return _tool("agent.spawn", **_public_spawn("general-analysis", input_refs=refs))
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[spawned[-1]["payload"]["data"]["task_id"]])
        assert _child_results(state)[0]["result"]["schema_version"] == "finance.task_result.v1"
        return _root_final(state)

    runtime = _runtime(tmp_path, decide, config=SchedulerConfig(run_concurrency=1, worker_concurrency=1))
    status = asyncio.run(_finish(runtime))
    assert status["status"] == "succeeded", (status, runtime.mock_errors)
    assert not runtime.mock_errors
    assert {p.get("depth", 0) for p in runtime.prompts} == {0, 1, 2}
    assert len(runtime.corpus.reads) == 1
    with runtime.repo.db.connection() as c:
        tasks = c.execute("SELECT status FROM h2_tasks").fetchall()
        attempts = c.execute("SELECT count(*) FROM h2_attempts").fetchone()[0]
    assert len(tasks) == 3 and all(t[0] == "succeeded" for t in tasks)
    assert attempts > len(tasks)


def test_required_review_mode_executes_analyst_verifier_resume_and_root_delivery(tmp_path):
    statement = "The view remains conditional."

    def decide(state):
        role = state.get("role", "main")
        if role == "verifier":
            request = state["review_request"]
            assert request["draft_revision"] == 1
            assert state["depth"] == 2
            return {"type": "final", "output": {
                "schema_version": "finance.review_report.v1",
                "reviewed_draft_ref": request["draft_ref"],
                "reviewed_revision": request["draft_revision"],
                "checked_target_ids": [t["target_id"] for t in request["targets"]],
                "findings": [], "checked_scope": request["scope"],
                "unchecked_scope": [], "limitations": [],
            }}
        if role == "analyst":
            assert state["review_policy"]["mode"] == "required"
            assert state["review_policy"]["max_rounds"] == 5
            ref = state["material_refs"][0]
            reviewed = state["review_workflow"].get("status") == "report_ready"
            return {"type": "final", "output": {
                "schema_version": "finance.analyst_result.v2", "content": statement,
                "evidence_links": [{"text": statement, "evidence_refs": [ref]}],
                "material_disposition": [], "gaps": [], "limitations": [],
                "review_targets": [{"target_id": "condition", "kind": "author_view",
                                    "statement": statement, "evidence_refs": [ref]}],
                "review_decision": "skip" if reviewed else "request",
                "review_decision_reason": "The exact draft completed independent review." if reviewed else None,
                "review_dispositions": [],
            }}
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=1)
        if not _business_observations(state, "article.read"):
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        if not state["scheduled_children"]:
            refs = [a["material_ref"] for a in state["article_states"] if a.get("material_ref")]
            return _tool("agent.spawn", **_public_spawn(
                "article-analysis", "analyst", input_refs=refs, review_mode="required",
            ))
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        assert _child_results(state)[0]["review"]["status"] == "reviewed_with_resolutions"
        return _root_final(state)

    runtime = _runtime(tmp_path, decide, config=SchedulerConfig(run_concurrency=1, worker_concurrency=1))
    status = asyncio.run(_finish(runtime))
    assert status["status"] == "succeeded"
    assert not runtime.mock_errors
    assert len(runtime.corpus.reads) == 1
    with runtime.repo.db.connection() as c:
        roles = [tuple(row) for row in c.execute(
            "SELECT th.role,t.status FROM h2_tasks t JOIN h2_threads th ON th.thread_id=t.thread_id",
        )]
        analyst = c.execute(
            "SELECT r.sections_json FROM h2_results r JOIN h2_tasks t ON t.task_id=r.task_id "
            "JOIN h2_threads th ON th.thread_id=t.thread_id "
            "WHERE th.role='analyst' AND r.disposition='accepted'",
        ).fetchone()
        report_ref = json.loads(analyst["sections_json"])["finance_review"]
        report_artifact = c.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?", (report_ref,),
        ).fetchone()
        assert report_artifact is not None
        report = json.loads(runtime.repo.artifacts.read(report_artifact))
        assert report["payload"]["report"]["reviewed_revision"] == 1
    assert sorted(roles) == [("analyst", "succeeded"), ("main", "succeeded"), ("verifier", "succeeded")]
