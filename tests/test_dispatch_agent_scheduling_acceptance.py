"""Offline scheduling acceptance and parent/child Trace reconstruction."""

import asyncio
import json
from pathlib import Path

import pytest

from policy_impact.dispatch import SchedulerConfig
from tests.test_dispatch_finance_continuation import (
    _cycle,
    _finish,
    _root_final,
    _runtime,
    _spawn,
    _tasks,
    _tool,
)
from tests.test_dispatch_subagent_acceptance import (
    CASES as SUBAGENT_CASES,
)
from tests.test_dispatch_subagent_acceptance import (
    _assignment,
    _child_results,
    _run_case,
    _task_output,
)

CATALOG = json.loads((Path(__file__).parent / "fixtures/h2_agent_scheduling_acceptance.json")
                     .read_text(encoding="utf-8"))
SUBAGENT_CASE_BY_ID = {case["id"]: case for case in SUBAGENT_CASES}


def _trace(runtime):
    caller = runtime.service.caller(runtime.handle)
    tasks = _tasks(runtime)
    task_ids = [task["task_id"] for task in tasks]
    events, cursor = [], "0"
    while True:
        status = runtime.repo.status(caller, task_ids=task_ids, after_event_id=cursor, max_events=200)
        events.extend(status["events"])
        cursor = status["next_event_id"]
        if not status["has_more"]:
            break
    trace = runtime.repo.scheduling_trace(caller, task_ids, events)
    exported = runtime.repo.export_run_records(runtime.handle.run_id)
    saved = json.loads(Path(exported["files"]["scheduling_trace"]).read_text(encoding="utf-8"))
    return tasks, trace, saved, exported


def _assert_trace_tree(runtime, expected_agents, required_events):
    tasks, trace, saved, exported = _trace(runtime)
    agents = [span for span in trace["spans"] if span["span_type"] == "agent"]
    attempts = [span for span in trace["spans"] if span["span_type"] == "attempt"]
    calls = [span for span in trace["spans"] if span["span_type"] == "model_call"]
    by_id = {task["task_id"]: task for task in tasks}

    assert trace["trace_id"] == runtime.handle.run_id
    assert [(span["role"], span["depth"]) for span in agents] == [
        (expected["role"], expected["depth"]) for expected in expected_agents
    ]
    for task, expected in zip(tasks, expected_agents, strict=True):
        parent = by_id.get(task["parent_task_id"])
        assert (parent["role"] if parent else None) == expected["parent_role"]
        span = next(item for item in agents if item["span_id"] == task["task_id"])
        lineage, current = [], task
        while current:
            lineage.append(current["role"])
            current = by_id.get(current["parent_task_id"])
        assert span["agent_path"] == list(reversed(lineage))

    attempt_ids = {span["span_id"] for span in attempts}
    task_ids = {span["span_id"] for span in agents}
    assert attempts and calls
    assert all(span["parent_span_id"] in task_ids for span in attempts)
    assert all(span["parent_span_id"] in attempt_ids for span in calls)
    assert all(span["task_id"] in task_ids and span["agent_path"] for span in calls)
    assert all(event["trace_id"] == trace["trace_id"] for event in trace["events"])
    assert all(event["span_id"] in task_ids and event["agent_path"] for event in trace["events"])
    assert {event["event_type"] for event in trace["events"]} >= set(required_events)
    assert {span["span_id"] for span in saved["spans"]} == {
        span["span_id"] for span in trace["spans"]
    }
    assert {span["parent_span_id"] for span in saved["spans"]} == {
        span["parent_span_id"] for span in trace["spans"]
    }
    jsonl = [json.loads(line) for line in Path(exported["files"]["trace"]).read_text(
        encoding="utf-8").splitlines()]
    assert {row["span_id"] for row in jsonl if row["record_type"] in {
        "agent_span", "attempt_span", "model_call",
    }} == {
        span["span_id"] for span in trace["spans"]
    }
    serialized = json.dumps(trace, ensure_ascii=False)
    assert "SOURCE_BODY_ALPHA" not in serialized
    assert "Act as an independent blind reviewer" not in serialized
    assert '"messages"' not in serialized


@pytest.mark.parametrize("case_spec", CATALOG["cases"][:2], ids=lambda case: case["id"])
def test_agent_delegation_scenarios_preserve_tree_attempts_and_model_call_trace(tmp_path, case_spec):
    case = SUBAGENT_CASE_BY_ID[case_spec["business_case_id"]]
    runtime = _run_case(
        tmp_path,
        case,
        delegate_auxiliary=case_spec["scenario"] == "general_nested_delegation",
    )
    _assert_trace_tree(runtime, case_spec["expected_agents"], case_spec["required_events"])


def test_sibling_agents_wait_for_all_results_before_parent_merge(tmp_path):
    base = SUBAGENT_CASE_BY_ID["SA-G02"]
    child_cases = [
        {
            **base,
            "objective": "Explain the meaning of a claim.",
            "task_instructions": "Give one definition and a distinct example.",
            "content": "Claim explanation: a proposition asserted to be true.",
        },
        {
            **base,
            "objective": "Explain the meaning of an assumption.",
            "task_instructions": "Give one definition and a distinct example.",
            "content": "Assumption explanation: a premise accepted for the current reasoning.",
        },
    ]

    def decide(state):
        identity = state.get("task_identity", {})
        depth = identity.get("depth", state.get("depth", 0))
        if depth == 1:
            case = next(item for item in child_cases if item["objective"] == state["objective"])
            return _task_output(case, state, [])
        children = state["scheduled_children"]
        if len(children) < len(child_cases):
            return _tool("agent.spawn", **_assignment(child_cases[len(children)], state, []))
        results = _child_results(state)
        if len(results) < len(child_cases):
            return _tool("agent.wait", task_ids=[item["task_id"] for item in children], mode="all")
        content_by_child = {item["result"]["content"] for item in results}
        expected = {case["content"] for case in child_cases}
        assert content_by_child == expected
        final = _root_final(state)
        final["output"]["answer"] = "\n\n".join(case["content"] for case in child_cases)
        return final

    runtime = _runtime(
        tmp_path,
        decide,
        config=SchedulerConfig(run_concurrency=1, worker_concurrency=1),
    )
    status = asyncio.run(_finish(runtime))
    assert status["status"] == "succeeded"
    assert not runtime.mock_errors
    expected = CATALOG["cases"][2]["expected_agents"]
    tasks, trace, _, _ = _trace(runtime)
    _assert_trace_tree(runtime, expected, CATALOG["cases"][2]["required_events"])
    root_id = next(task["task_id"] for task in tasks if task["depth"] == 0)
    siblings = [task for task in tasks if task["depth"] == 1]
    assert len(siblings) == 2
    assert {task["parent_task_id"] for task in siblings} == {root_id}
    waits = [event for event in trace["events"] if event["event_type"] == "task.waiting"]
    assert waits and waits[0]["task_id"] == root_id


def test_parent_receives_terminal_child_failure_and_delivers_partial_without_rewaiting(tmp_path):
    def decide(state):
        depth = state["task_identity"]["depth"]
        if depth == 1:
            return {"type": "final", "output": {
                "schema_version": "finance.research_result.v1",
                "content": "Intentionally invalid Analyst output for failure handoff acceptance.",
                "evidence_links": [], "material_disposition": [], "gaps": [], "limitations": [],
            }}

        failed = next((item for item in state["normalized_observations"]
                       if item.get("status") in {"failed", "timed_out", "cancelled"}), None)
        if failed:
            diagnostic = failed.get("failure_diagnostic", {})
            failure = (
                f"Child {failed['task_id']} ended as {failed['status']}; "
                f"diagnostic {diagnostic.get('error_code', 'unavailable')} leaves the assignment unverified."
            )
            return {"type": "final", "output": {
                "answer": f"Partial result: {failure}",
                "evidence_refs": [],
                "gaps": [failure],
                "warnings": [failure],
            }}

        children = state["scheduled_children"]
        if not children:
            return _spawn("invalid-analyst", "analyst", [], calls=24, wall_ms=300_000)
        return _tool("agent.wait", task_ids=[children[0]["task_id"]], mode="all")

    runtime = _runtime(tmp_path, decide)
    async def finish_with_partial():
        for _ in range(60):
            await _cycle(runtime)
            current = runtime.service.status(runtime.handle)["tasks"][0]
            if current["status"] in {"succeeded", "partial", "failed", "cancelled", "timed_out"}:
                return current
        pytest.fail("fixture action loop did not terminate within 60 deliveries")

    status = asyncio.run(finish_with_partial())
    tasks = _tasks(runtime)
    root = next(task for task in tasks if task["depth"] == 0)
    child = next(task for task in tasks if task["depth"] == 1)
    assert status["status"] == "partial"
    assert child["status"] == "failed"
    failure_context = next(
        observation
        for prompt in runtime.prompts
        for observation in prompt.get("normalized_observations", [])
        if observation.get("task_id") == child["task_id"]
    )
    assert failure_context["status"] == "failed"
    assert failure_context["failure_diagnostic"]["error_code"] == "contract_error"
    result = runtime.repo.result_get(runtime.service.caller(runtime.handle), root["result_ref"], view="full")
    assert child["task_id"] in result["content"] and "contract_error" in result["content"]
    assert result["status"] == "partial"
    summary = runtime.repo.result_get(runtime.service.caller(runtime.handle), root["result_ref"], view="summary")
    assert any("contract_error" in gap for gap in json.loads(summary["content"])["gaps"])
    _, trace, _, _ = _trace(runtime)
    root_waits = [event for event in trace["events"]
                  if event["event_type"] == "task.waiting" and event["task_id"] == root["task_id"]]
    assert len(root_waits) == 1
    root_span = next(span for span in trace["spans"] if span["span_id"] == root["task_id"])
    assert root_span["status"] == "partial"
