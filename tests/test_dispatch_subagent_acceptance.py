"""Data-driven offline acceptance of actual subagent messages and delivery."""

import asyncio
import json
from pathlib import Path

import pytest

from policy_impact.dispatch import finance_rules
from policy_impact.dispatch.contracts import SchedulerConfig
from tests.test_dispatch_finance_continuation import (
    _artifact,
    _assert_ledger,
    _business_observations,
    _child_results,
    _finish,
    _root_final,
    _runtime,
    _scheduler_observations,
    _tasks,
    _tool,
)
from tests.test_dispatch_finance_rules import rule_root as shared_rule_root

CATALOG = json.loads((Path(__file__).parent / "fixtures/h2_subagent_acceptance.json").read_text(encoding="utf-8"))
CASES = CATALOG["cases"]


@pytest.fixture
def rule_root(tmp_path):
    return shared_rule_root.__wrapped__(tmp_path)


def _assignment(case, state, refs, **extra):
    profile_options = {
        key: case[key] for key in ("review_mode", "review_options") if key in case
    }
    return {
        "task_name": case.get("name", f"{case['role']} task"),
        "message": case["objective"],
        "profile": case["role"],
        "task_instructions": case["task_instructions"],
        "success_criteria": case["success_criteria"],
        "input_refs": refs,
        "output_contract": case["output_contract"],
        **({"profile_options": profile_options} if profile_options else {}),
        **extra,
    }


def _review_output(state, source_ref):
    request = state["review_request"]
    return {"type": "final", "output": {
        "schema_version": "finance.review_report.v2",
        "verdict": "revision_required" if request["round_number"] == 1 else "passed",
        "verdict_reason": "Revise the condition." if request["round_number"] == 1 else "All checks passed.",
        "blocking_finding_ids": ["condition-loss"] if request["round_number"] == 1 else [],
        "reviewed_draft_ref": request["draft_ref"], "reviewed_revision": request["draft_revision"],
        "checked_target_ids": [target["target_id"] for target in request["targets"]],
        "findings": [{
            "finding_id": "condition-loss", "target_id": request["targets"][0]["target_id"],
            "category": "condition_loss", "severity": "material", "assessment": "partly_supported",
            "explanation": "The first draft omitted the source condition.",
            "evidence_refs": [source_ref],
            "quotes": [{"evidence_ref": source_ref, "text": "The view remains conditional."}],
            "suggested_change": "Retain the condition.", "unchecked_scope": [], "limitations": [],
        }] if request["round_number"] == 1 else [],
        "checked_scope": request["scope"], "unchecked_scope": [], "limitations": [],
    }}


def _task_output(case, state, refs):
    reviewed = state["review_workflow"].get("status") == "report_ready"
    rounds = state["review_workflow"].get("rounds", 0)
    content = ("The author expects continuation."
               if case.get("review_mode") == "required" and not reviewed else case["content"])
    output = {
        "schema_version": case["output_contract"]["name"] + ".v" + case["output_contract"]["version"],
        "content": content,
    }
    if case["role"] != "general":
        output.update({
            "evidence_links": [{"text": content, "evidence_refs": refs}] if refs else [],
            "material_disposition": [{"material_ref": ref, "decision": "considered",
                                      "reason": "Assigned source was inspected."} for ref in refs],
            "gaps": [], "limitations": [],
        })
    if case["role"] == "analyst":
        output.update({
            "review_targets": [{"target_id": "condition", "kind": "author_view", "statement": content,
                                "evidence_refs": refs}],
            "review_decision": "request" if case.get("review_mode") == "required" and rounds < 2 else "skip",
            "review_decision_reason": "Only source attribution is requested; no external claim is being made.",
            "review_dispositions": [{"finding_id": "condition-loss", "disposition": "accepted"}]
            if reviewed else [],
        })
    return {"type": "final", "output": output}


def _run_case(tmp_path, case, *, background=None, delegate_auxiliary=False, rejected_tool=None):
    assigned_refs = []
    background_refs = []
    received = []
    auxiliary = {
        **CASES[1], "objective": "Identify one condition for the parent task.",
        "task_instructions": "Return a bounded auxiliary observation to your parent.",
        "content": "Auxiliary work completed with a conditional observation.",
    }

    def decide(state):
        depth = state.get("depth", 0)
        for observation in _scheduler_observations(state, "agent.spawn"):
            assert observation["status"] == "succeeded", observation
        if depth == 2:
            if state["role"] == "verifier" and state.get("review_request"):
                assert state["task_instructions"].endswith(case["review_options"]["instructions"])
                assert state["review_request"]["draft_revision"] == state["review_request"]["round_number"]
                return _review_output(state, assigned_refs[0])
            assert state["role"] == "general"
            assert state["task_instructions"].endswith(auxiliary["task_instructions"])
            return _task_output(auxiliary, state, [])
        if depth == 1:
            assert state["role"] == case["role"]
            assert state["objective"] == case["objective"]
            assert state["task_instructions"].endswith(case["task_instructions"])
            assert state["success_criteria"] == case["success_criteria"]
            assert state["output_contract"] == case["output_contract"]
            material_text = json.dumps(state["materials"])
            assert ("SOURCE_BODY_ALPHA" in material_text) == case["source_required"]
            assert ("SOURCE_BODY_BETA" in material_text) == (background == "explicit")
            assert all(obs.get("tool_name") not in {"article.list", "article.read"}
                       or obs.get("status") == "failed"
                       for obs in state["action_observations"])
            if rejected_tool is not None:
                rejected = _scheduler_observations(state, rejected_tool)
                if not rejected:
                    return _tool(rejected_tool, article_id="article_alpha", start_line=1, limit=100)
                assert rejected[-1]["status"] == "failed"
                assert rejected[-1]["payload"]["error"]["code"] == "forbidden"
            if delegate_auxiliary:
                spawned = _scheduler_observations(state, "agent.spawn")
                if not spawned:
                    return _tool("agent.spawn", **_assignment(auxiliary, state, assigned_refs))
                if not _child_results(state):
                    return _tool("agent.wait", task_ids=[spawned[-1]["payload"]["data"]["task_id"]])
                assert _child_results(state)[0]["result"]["content"] == auxiliary["content"]
            if case["source_required"]:
                descriptions = _scheduler_observations(state, "tool.describe")
                if not descriptions:
                    return {"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "material.get"}}
                description = _business_observations(state, "tool.describe")[-1]["description"]
                assert "material_ref" in description["parameters"]["properties"]
                reads = _scheduler_observations(state, "material.get")
                if not reads:
                    return _tool("material.get", material_ref=assigned_refs[0])
                assert reads[-1]["status"] == "succeeded"
                assert "SOURCE_BODY_ALPHA" in _business_observations(state, "material.get")[-1]["content"]
            return _task_output(case, state, assigned_refs)
        if case["source_required"] or background is not None:
            if not _business_observations(state, "article.list"):
                return _tool("article.list", limit=10)
            reads = _business_observations(state, "article.read")
            if not reads:
                return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
            if background is not None and len(reads) < 2:
                return _tool("article.read", article_id="article_beta", start_line=1, limit=100)
        if not state["scheduled_children"]:
            assigned_refs[:] = [article["material_ref"] for article in state["article_states"]
                                if article["article_id"] == "article_alpha" and article.get("material_ref")]
            background_refs[:] = [article["material_ref"] for article in state["article_states"]
                                  if article["article_id"] == "article_beta" and article.get("material_ref")]
            if not case["source_required"]:
                assigned_refs.clear()
            return _tool("agent.spawn", **_assignment(
                case, state, assigned_refs,
                parent_context_refs=background_refs if background == "explicit" else [],
            ))
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        result = _child_results(state)[0]
        assert result["result"]["content"] == case["content"]
        if case["role"] == "analyst":
            assert result["review"]["status"] == case["expected_review_status"]
            assert result["review"]["rounds"] == case["expected_review_rounds"]
            if case["review_mode"] == "required":
                assert result["review"]["draft_revision"] == 2
                assert result["review"]["dispositions"][0]["finding_id"] == "condition-loss"
                assert len(result["review"]["report_refs"]) == 2
        received.append(result)
        final = _root_final(state)
        final["output"]["answer"] = result["result"]["content"]
        return final

    runtime = _runtime(tmp_path, decide, config=SchedulerConfig(run_concurrency=1, worker_concurrency=1))
    status = asyncio.run(_finish(runtime))
    assert status["status"] == "succeeded"
    assert received and not runtime.mock_errors
    tasks = _tasks(runtime)
    assert all(task["status"] == "succeeded" for task in tasks)
    expected_tasks = 2 + case.get("expected_review_rounds", 0) + int(delegate_auxiliary)
    assert len(tasks) == expected_tasks
    _assert_ledger(runtime, len(runtime.requests))
    for request in runtime.requests:
        messages = json.loads(request.content)["messages"]
        state = json.loads(messages[-1]["content"])
        if state.get("depth") == 1:
            expected = finance_rules.load_rules().system(state["task_budget"], role=case["role"])
            assert messages[0] == {"role": "system", "content": expected}
            assert case["task_instructions"] not in messages[0]["content"]
    root = next(task for task in tasks if task["depth"] == 0)
    result = runtime.repo.result_get(runtime.service.caller(runtime.handle), root["result_ref"], view="full")
    assert case["content"] in result["content"]
    return runtime


def _snapshot(runtime, ref):
    with runtime.repo.db.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM h2_snapshots WHERE snapshot_id=?", (ref,),
        ).fetchone()
    assert row is not None, ref
    return json.loads(row["payload_json"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_subagent_acceptance_catalog_runs_through_worker_and_returns_full_content(tmp_path, case):
    _run_case(tmp_path, case)


@pytest.mark.parametrize("case", [CASES[0], CASES[2], CASES[5], CASES[6]], ids=lambda case: case["role"])
def test_each_current_role_delegates_and_integrates_a_general_grandchild(tmp_path, case):
    runtime = _run_case(tmp_path, case, delegate_auxiliary=True)
    assert [(task["role"], task["depth"]) for task in _tasks(runtime)] == [
        ("main", 0), (case["role"], 1), ("general", 2),
    ]


@pytest.mark.parametrize("background", ["omitted", "explicit"])
def test_parent_history_stays_out_unless_background_is_explicitly_granted(tmp_path, background):
    runtime = _run_case(tmp_path, CASES[0], background=background)
    assert runtime.corpus.reads == ["article_alpha", "article_beta"]


@pytest.mark.parametrize("new_role", [False, True], ids=["replace-general", "register-expert"])
def test_profile_prompt_and_whitelist_are_replaceable_on_the_shared_worker(tmp_path, rule_root, monkeypatch, new_role):
    role = "method_reviewer" if new_role else "general"
    manifest_path = rule_root / finance_rules.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads((rule_root / "config/agents/profiles/general.json").read_text(encoding="utf-8"))
    profile["instructions"] = "method-reviewer"
    profile["description"] = "Use the configured method-reviewing approach."
    profile["allowed_tools"] = ["tool.describe", "material.get"]
    profile["allowed_child_profiles"] = []
    manifest["roles"][role] = f"config/agents/profiles/{role}.json"
    manifest["prompts"]["method-reviewer"] = "prompts/finance/method-reviewer.md"
    if new_role:
        manifest["root_profile"]["allowed_child_profiles"].append(role)
    replacement = "REPLACED_METHOD: inspect the exact premise and state one concrete limitation."
    (rule_root / f"config/agents/profiles/{role}.json").write_text(json.dumps(profile), encoding="utf-8")
    (rule_root / "prompts/finance/method-reviewer.md").write_text(replacement, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rules = finance_rules.load_rules(rule_root)
    monkeypatch.setattr(finance_rules, "load_rules", lambda: rules)
    case = {**CASES[0], "role": role}
    runtime = _run_case(tmp_path, case, rejected_tool="article.read")
    assert runtime.corpus.reads == ["article_alpha"]
    child = next(task for task in _tasks(runtime) if task["depth"] == 1)
    brief = _artifact(runtime, child["brief_ref"])
    assert brief["role"] == role and brief["objective"] == case["objective"]
    permissions = _snapshot(runtime, child["permission_snapshot_ref"])
    assert set(permissions["tools"]) == {"tool.describe", "material.get"}
    child_inputs = [state for state in runtime.prompts if state.get("depth") == 1]
    assert all({tool["name"] for tool in state["tool_catalog"]} == {"tool.describe", "material.get"}
               for state in child_inputs)
    assert replacement in rules.system({}, role=role)


def test_catalog_retains_independent_cases_for_all_profiles_and_review_modes():
    assert CATALOG["schema_version"] == "finance.h2.subagent_acceptance.v1"
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert {case["role"] for case in CASES} == {"general", "analyst", "verifier", "skeptic"}
    assert {case["review_mode"] for case in CASES if case["role"] == "analyst"} == {"off", "auto", "required"}
