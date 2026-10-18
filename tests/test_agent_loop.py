import copy
import hashlib
import importlib
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig, StopReason
from policy_impact.harness.context_manifest import build_context_manifest
from policy_impact.harness.context_offload import RunContextStore
from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import TOOL_REGISTRY, ToolGateway, register_tool
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.runtime.task_brief import SubagentResult
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry

ALLOWED_ACTIONS = {"final", "tool_call", "delegate", "request_approval"}
EXPECTED_STOP_REASONS = {
    "success",
    "deterministic_complete",
    "max_steps",
    "max_model_calls",
    "timeout",
    "model_error",
    "invalid_model_action",
    "tool_denied",
    "tool_failed",
    "tool_timeout",
    "unknown_outcome",
    "invalid_tool_output",
    "repeated_tool_failure",
    "context_budget_exceeded",
    "integrity_error",
    "persistence_error",
    "awaiting_approval",
    "gate_blocked",
    "cancelled",
    "delegate_failed",
}


class RecordingAdapter:
    def __init__(self, payloads, *, metadata=None):
        self.payloads = list(payloads)
        self.metadata = metadata or {}
        self.message_batches = []

    @property
    def call_count(self):
        return len(self.message_batches)

    def complete(self, messages, schema_name):
        self.message_batches.append(copy.deepcopy(messages))
        index = self.call_count - 1
        return ModelResponse(
            response_id=f"recording_{index + 1}",
            payload=self.payloads[index],
            metadata=copy.deepcopy(self.metadata),
        )


def _config(state: PolicyImpactState, **overrides) -> AgentLoopConfig:
    manifest = build_context_manifest(
        state=state,
        stage_name="agent_stage",
        agent_role="policy_agent",
        visible_fields=["company_context_pack"],
    )
    values = {
        "stage_name": "agent_stage",
        "agent_role": "policy_agent",
        "context_manifest": manifest,
        "output_key": "assessment",
        "max_steps": 4,
        "max_model_calls": 4,
        "timeout_seconds": 30,
        "max_calls_per_tool": 3,
        "max_repeated_failures": 2,
        "task_prompt": "Assess the policy impact.",
    }
    if "max_turns" in overrides and "max_steps" not in overrides:
        values.pop("max_steps")
    values.update(overrides)
    return AgentLoopConfig(**values)


def _actions_module():
    return importlib.import_module("policy_impact.runtime.actions")


def test_final_validator_can_stop_when_repair_budget_is_exhausted():
    from policy_impact.harness.agent_loop import FinalGuardBlocked
    state = PolicyImpactState(company_id="company_test")
    adapter = RecordingAdapter([{"type": "final", "output": {"answer": "candidate"}}])

    def stop(output):
        raise FinalGuardBlocked("Verification budget exhausted")

    result = AgentLoop(adapter, ToolGateway(), final_guard=stop).run(state, _config(state))
    assert result.stop_reason == "gate_blocked"
    assert result.final_output == {}
    assert adapter.call_count == 1
    assert result.turns[-1].observation["final_gate"]["retryable"] is False


def test_context_pressure_offloads_and_restores_exact_observations(tmp_path, monkeypatch):
    monkeypatch.setitem(TOOL_REGISTRY, "large_read", lambda: {"text": "evidence " * 400})
    gateway = _gateway("large_read")
    actions = [
        {"type": "tool_call", "tool_name": "large_read", "arguments": {}}
        for _ in range(6)
    ] + [{"type": "final", "output": {"answer": "done"}}]
    adapter = RecordingAdapter(actions)
    store = RunContextStore(tmp_path / "run")
    events = []
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(
        adapter, gateway, context_store=store, on_context_offload=events.append,
    ).run(state, _config(
        state, max_steps=7, max_model_calls=7, max_calls_per_tool=7,
        context_pressure_tokens=3000,
    ))
    assert result.stop_reason == "success"
    assert events and events[0]["before_tokens"] > 3000
    marker = next(
        message["content"]["offloaded_observation"]
        for message in adapter.message_batches[-1]
        if isinstance(message.get("content"), dict)
        and "offloaded_observation" in message["content"]
    )
    assert store.open(marker["context_id"])["content"] == {
        "tool_result": {"text": "evidence " * 400},
    }
    with pytest.raises(ValueError, match="generated observation ID"):
        store.open("../other-run")
    path = store.root / f"{marker['context_id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["content"]["tool_result"]["text"] = "tampered"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="content hash"):
        store.open(marker["context_id"])


def test_context_pressure_can_offload_recent_observations_and_reuses_restored_id(tmp_path):
    store = RunContextStore(tmp_path / "run")
    original = {"tool_result": {"text": "evidence " * 400}}
    saved = store.save(original, "large_read")
    messages = [
        {"role": "tool", "tool_name": "large_read", "content": copy.deepcopy(original)}
        for _ in range(6)
    ]
    messages.append({
        "role": "tool", "tool_name": "context.open",
        "content": {"tool_result": store.open(saved["context_id"])},
    })
    messages.extend(
        {"role": "tool", "tool_name": "large_read", "content": copy.deepcopy(original)}
        for _ in range(4)
    )
    loop = AgentLoop(RecordingAdapter([]), ToolGateway(), context_store=store)
    loop._offload_old_observations(messages, 3000)
    assert any("offloaded_observation" in item["content"] for item in messages[-4:])
    from policy_impact.harness.context_manifest import estimate_tokens
    assert estimate_tokens(messages) <= 3000
    assert messages[6]["content"]["offloaded_observation"]["context_id"] == saved["context_id"]
    assert list(store.root.glob("context_*.json")) == [store.root / f"{saved['context_id']}.json"]


@pytest.mark.parametrize("target", [None, 2600])
def test_context_target_is_explicit_and_preserves_generic_compatibility(tmp_path, target):
    from policy_impact.harness.context_manifest import estimate_tokens

    original = {"tool_result": {"text": "observation " * 230}}
    messages = [{"role": "tool", "tool_name": "article.read", "content": copy.deepcopy(original)}
                for _ in range(6)]
    store = RunContextStore(tmp_path / "run")
    events = []
    loop = AgentLoop(RecordingAdapter([]), ToolGateway(), context_store=store, on_context_offload=events.append)
    loop._offload_old_observations(messages, 3000, target)
    actual_target = 2250 if target is None else target
    assert events[0]["target_tokens"] == actual_target
    assert estimate_tokens(messages) <= actual_target
    for message in messages:
        marker = message["content"].get("offloaded_observation")
        if marker:
            assert store.open(marker["context_id"])["content"] == original


def test_tens_of_thousands_of_tokens_are_not_offloaded_under_new_finance_capacity(tmp_path):
    from policy_impact.harness.research_budget import ResearchLimits

    messages = [{"role": "tool", "tool_name": "article.read",
                 "content": {"tool_result": {"text": "article observation " * 10000}}}]
    original = copy.deepcopy(messages)
    store = RunContextStore(tmp_path / "run")
    loop = AgentLoop(RecordingAdapter([]), ToolGateway(), context_store=store)
    assert loop._offload_old_observations(messages, *ResearchLimits().context_pressure_limits()) == []
    assert messages == original
    assert not list(store.root.glob("context_*.json"))


@pytest.mark.parametrize(("pressure", "target"), [(None, 2000), (3000, 3000), (3000, 4000), (3000, 0)])
def test_context_target_requires_a_larger_trigger(pressure, target):
    state = PolicyImpactState(company_id="company_test")
    with pytest.raises(ValueError, match="context_target_tokens"):
        _config(state, context_pressure_tokens=pressure, context_target_tokens=target)


def test_repeated_context_restore_advises_without_blocking_or_changing_source(tmp_path, monkeypatch):
    store = RunContextStore(tmp_path / "run")
    original = {"tool_result": {"text": "observed article passage"}}
    saved = store.save(original, "article.read")
    monkeypatch.setitem(TOOL_REGISTRY, "context.open", store.open)
    gateway = _gateway("context.open")
    actions = [
        {"type": "tool_call", "tool_name": "context.open",
         "arguments": {"context_id": saved["context_id"]}}
        for _ in range(3)
    ] + [{"type": "final", "output": {"answer": "done"}}]
    adapter = RecordingAdapter(actions)
    events = []
    checkpoint_events = []
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(
        adapter, gateway, context_store=store, on_context_restore=events.append,
        on_context_checkpoint=checkpoint_events.append,
    ).run(state, _config(state, max_steps=4, max_model_calls=4, max_calls_per_tool=4))

    assert result.stop_reason == "success"
    observations = [turn.observation["tool_result"] for turn in result.turns[:3]]
    assert "_context_restore_advisory" not in observations[0]
    assert [item["restore_count"] for item in events] == [2, 3]
    assert observations[1]["_context_restore_advisory"]["suggested_tool"] == "research.artifact"
    assert observations[2]["_context_restore_advisory"]["restore_count"] == 3
    assert checkpoint_events == []
    assert all(item["content"] == original for item in observations)
    assert store.open(saved["context_id"])["content"] == original

    messages = [
        {"role": "tool", "tool_name": "context.open", "content": {"tool_result": item}}
        for item in observations
    ] + [
        {"role": "tool", "tool_name": "article.read",
         "content": {"tool_result": {"text": "new passage " * 400}}}
        for _ in range(5)
    ]
    loop = AgentLoop(RecordingAdapter([]), gateway, context_store=store)
    loop._offload_old_observations(messages, 3000)
    assert messages[1]["content"]["offloaded_observation"]["context_id"] == saved["context_id"]


def test_successive_context_pages_do_not_trigger_repeated_restore_checkpoint(tmp_path, monkeypatch):
    store = RunContextStore(tmp_path / "run")
    saved = store.save({"tool_result": {"text": "source passage " * 1000}}, "article.read")
    monkeypatch.setitem(TOOL_REGISTRY, "context.open", store.view)
    actions = [
        {"type": "tool_call", "tool_name": "context.open", "arguments": {
            "context_id": saved["context_id"], "offset": offset, "limit": 1200}}
        for offset in (0, 1200, 2400, 3600)
    ] + [{"type": "final", "output": {"answer": "Read four different pages."}}]
    adapter = RecordingAdapter(actions)
    events, checkpoints = [], []
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(adapter, _gateway("context.open"), context_store=store,
                       on_context_restore=events.append, on_context_checkpoint=checkpoints.append).run(
        state, _config(state, max_steps=5, max_model_calls=5, max_calls_per_tool=5,
                       context_pressure_tokens=2000))
    assert result.stop_reason == "success"
    assert events == []
    assert not any(row.get("decision") in {"checkpoint", "deny"} for row in checkpoints)
    assert store.open(saved["context_id"])["content"]["tool_result"]["text"] == "source passage " * 1000


def test_context_checkpoint_releases_after_new_evidence_bound_note(tmp_path, monkeypatch):
    store = RunContextStore(tmp_path / "run")
    original = {"tool_result": {"text": "source passage " * 400}}
    saved = store.save(original, "article.read")
    monkeypatch.setitem(TOOL_REGISTRY, "context.open", store.open)
    monkeypatch.setitem(TOOL_REGISTRY, "large_read", lambda: {"text": "other passage " * 400})
    monkeypatch.setitem(TOOL_REGISTRY, "research.artifact", lambda **kwargs: {
        "kind": "research_note", "artifact_id": "note_new", "entry_count": 1,
    })
    gateway = ToolGateway(permission_engine=PermissionEngine([
        ToolPolicy("agent_stage", name, "allow", "test policy")
        for name in ("context.open", "large_read", "research.artifact")
    ]))
    open_action = {"type": "tool_call", "tool_name": "context.open",
                   "arguments": {"context_id": saved["context_id"]}}
    actions = [open_action] + [
        {"type": "tool_call", "tool_name": "large_read", "arguments": {}}
        for _ in range(6)
    ] + [open_action, open_action, {
        "type": "tool_call", "tool_name": "research.artifact",
        "arguments": {"operation": "save", "kind": "research_note", "payload": {}},
    }, open_action, {"type": "final", "output": {"answer": "qualified result"}}]
    events = []
    state = PolicyImpactState(company_id="company_test")
    adapter = RecordingAdapter(actions)
    result = AgentLoop(
        adapter, gateway, context_store=store, on_context_checkpoint=events.append,
    ).run(state, _config(state, max_steps=20, max_model_calls=20,
                         max_calls_per_tool=20, context_pressure_tokens=3000))

    assert result.stop_reason == "success"
    assert [event["decision"] for event in events] == ["checkpoint", "deny", "release"]
    assert result.turns[8].observation["context_checkpoint"]["decision"] == "deny"
    assert result.turns[10].observation["tool_result"]["content"] == original
    assert store.open(saved["context_id"])["content"] == original


def test_context_checkpoint_rejects_no_progress_and_stops_repeated_restores(tmp_path, monkeypatch):
    store = RunContextStore(tmp_path / "run")
    saved = store.save({"tool_result": {"text": "source passage " * 400}}, "article.read")
    monkeypatch.setitem(TOOL_REGISTRY, "context.open", store.open)
    monkeypatch.setitem(TOOL_REGISTRY, "large_read", lambda: {"text": "other passage " * 400})
    note_calls = []

    def incomplete_note(**kwargs):
        note_calls.append(kwargs)
        if len(note_calls) == 1:
            return {"kind": "research_note", "artifact_id": "note_empty", "entry_count": 0}
        raise ValueError("note requires current-run EvidenceRefs")

    monkeypatch.setitem(TOOL_REGISTRY, "research.artifact", incomplete_note)
    plan = {"goals": [{"goal_id": 1, "status": "pending", "note": ""}]}
    monkeypatch.setitem(TOOL_REGISTRY, "research.plan", lambda **kwargs: plan)
    gateway = ToolGateway(permission_engine=PermissionEngine([
        ToolPolicy("agent_stage", name, "allow", "test policy")
        for name in ("context.open", "large_read", "research.artifact", "research.plan")
    ]))
    open_action = {"type": "tool_call", "tool_name": "context.open",
                   "arguments": {"context_id": saved["context_id"]}}
    actions = [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {"operation": "get"}},
        open_action,
        *[{"type": "tool_call", "tool_name": "large_read", "arguments": {}}
          for _ in range(6)],
        open_action,
        {"type": "tool_call", "tool_name": "research.artifact",
         "arguments": {"operation": "save", "kind": "research_note", "payload": {}}},
        {"type": "tool_call", "tool_name": "research.artifact",
         "arguments": {"operation": "save", "kind": "research_note", "payload": {}}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {"operation": "get"}},
        {"type": "tool_call", "tool_name": "research.plan",
         "arguments": {"operation": "update", "goal_id": 1, "status": "pending", "note": ""}},
        open_action, open_action, open_action,
    ]
    events = []
    state = PolicyImpactState(company_id="company_test")
    adapter = RecordingAdapter(actions)
    result = AgentLoop(
        adapter, gateway, context_store=store, on_context_checkpoint=events.append,
    ).run(state, _config(state, max_steps=25, max_model_calls=25,
                         max_calls_per_tool=25, context_pressure_tokens=3000))

    assert result.stop_reason == "gate_blocked"
    assert not any(event["decision"] == "release" for event in events)
    assert [event["decision"] for event in events].count("deny") == 3
    assert len(note_calls) == 2
    assert adapter.call_count == len(actions)
    assert result.turns[-1].observation["context_checkpoint"]["retryable"] is False


def test_context_checkpoint_releases_after_concrete_plan_update(tmp_path, monkeypatch):
    store = RunContextStore(tmp_path / "run")
    saved = store.save({"tool_result": {"text": "source passage " * 400}}, "article.read")
    monkeypatch.setitem(TOOL_REGISTRY, "context.open", store.open)
    monkeypatch.setitem(TOOL_REGISTRY, "large_read", lambda: {"text": "other passage " * 400})
    plan = {"goals": [{"goal_id": 1, "status": "pending", "note": ""}]}

    def update_plan(**kwargs):
        if kwargs["operation"] == "update":
            plan["goals"] = [{"goal_id": 1, "status": kwargs["status"], "note": kwargs["note"]}]
        return copy.deepcopy(plan)

    monkeypatch.setitem(TOOL_REGISTRY, "research.plan", update_plan)
    gateway = ToolGateway(permission_engine=PermissionEngine([
        ToolPolicy("agent_stage", name, "allow", "test policy")
        for name in ("context.open", "large_read", "research.plan")
    ]))
    open_action = {"type": "tool_call", "tool_name": "context.open",
                   "arguments": {"context_id": saved["context_id"]}}
    actions = [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {"operation": "get"}},
        open_action,
        *[{"type": "tool_call", "tool_name": "large_read", "arguments": {}}
          for _ in range(6)],
        open_action,
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
            "note": "Reviewed the cited source and recorded the unresolved market-data gap.",
        }},
        open_action,
        {"type": "final", "output": {"answer": "qualified result"}},
    ]
    events = []
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(
        RecordingAdapter(actions), gateway, context_store=store,
        on_context_checkpoint=events.append,
    ).run(state, _config(state, max_steps=20, max_model_calls=20,
                         max_calls_per_tool=20, context_pressure_tokens=3000))

    assert result.stop_reason == "success"
    assert [event["decision"] for event in events] == ["checkpoint", "release"]
    assert events[-1]["kind"] == "research_plan"


def test_final_guard_requires_evidence_then_model_can_choose_tool(monkeypatch):
    monkeypatch.setitem(TOOL_REGISTRY, "read_evidence", lambda: {"known": True})
    gateway = _gateway("read_evidence")
    adapter = RecordingAdapter([
        {"type": "final", "output": {"answer": "not yet checked"}},
        {"type": "tool_call", "tool_name": "read_evidence", "arguments": {}},
        {"type": "final", "output": {"answer": "checked"}},
    ])
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(adapter, gateway, final_guard=lambda output: None if gateway.calls else "Read evidence before asserting absence").run(state, _config(state))
    assert result.stop_reason == "success"
    assert result.final_output["answer"] == "checked"
    assert result.turns[0].observation["final_gate"]["decision"] == "deny"
    assert "final_gate" in json.dumps(adapter.message_batches[1])


def test_final_guard_cannot_retry_outside_budget():
    state = PolicyImpactState(company_id="company_test")
    adapter = ScriptedModelAdapter([{"type": "final", "output": {"answer": "unchecked"}}] * 3)
    result = AgentLoop(adapter, _gateway("unused"), final_guard=lambda output: "missing evidence").run(state, _config(state, max_model_calls=2))
    assert result.stop_reason == "max_model_calls"
    assert result.final_output == {}


def test_repeated_identical_final_gate_stops_before_model_budget_is_exhausted():
    state = PolicyImpactState(company_id="company_test")
    adapter = ScriptedModelAdapter(
        [
            {"type": "final", "output": {"answer": f"rewritten rejected answer {index}"}}
            for index in range(20)
        ]
    )
    result = AgentLoop(
        adapter,
        _gateway("unused"),
        final_guard=lambda output: "missing evidence",
    ).run(state, _config(state, max_steps=20, max_model_calls=20))
    assert result.stop_reason == "gate_blocked"
    assert result.final_output == {}
    assert adapter._index == 3
    assert result.turns[-1].observation["final_gate"]["repeat_count"] == 3


def test_cached_read_does_not_reset_repeated_final_gate():
    tool_name = "cached_read_for_final_gate"
    calls = []
    register_tool(tool_name, lambda: calls.append(True) or {"cached": True, "text": ""})
    try:
        state = PolicyImpactState(company_id="company_test")
        adapter = RecordingAdapter([
            {"type": "final", "output": {"answer": "unfixed"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"answer": "still unfixed"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"answer": "unchanged evidence"}},
        ])
        result = AgentLoop(
            adapter, _gateway(tool_name), final_guard=lambda output: "missing citation"
        ).run(state, _config(state, max_steps=12, max_model_calls=12))
        assert result.stop_reason == "gate_blocked"
        assert result.turns[-1].observation["final_gate"]["repeat_count"] == 3
        assert len(calls) == 2
        assert adapter.call_count == 5
    finally:
        TOOL_REGISTRY.pop(tool_name, None)


def test_last_model_call_is_reserved_for_a_final_answer_after_tool_use():
    tool_name = "bounded_research_read"
    register_tool(tool_name, lambda: {"evidence": "read"})
    try:
        state = PolicyImpactState(company_id="company_test")
        adapter = RecordingAdapter(
            [
                {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
                {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
                {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
                {"type": "final", "output": {"answer": "grounded synthesis"}},
            ]
        )
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(state, max_steps=4, max_model_calls=4),
        )
        assert result.stop_reason == "success"
        assert result.final_output["answer"] == "grounded synthesis"
        assert "last permitted model call" in json.dumps(
            adapter.message_batches[-1], ensure_ascii=False
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)


def test_last_model_call_refuses_a_tool_instead_of_spending_the_final_slot():
    tool_name = "budget_boundary_tool"
    calls = []
    register_tool(tool_name, lambda: calls.append(True) or {"evidence": "read"})
    try:
        state = PolicyImpactState(company_id="company_test")
        adapter = RecordingAdapter(
            [{"type": "tool_call", "tool_name": tool_name, "arguments": {}}]
        )
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(state, max_steps=2, max_model_calls=1),
        )
        assert result.stop_reason == "max_model_calls"
        assert calls == []
        assert result.turns[-1].observation["budget_boundary"]["decision"] == "deny"
    finally:
        TOOL_REGISTRY.pop(tool_name, None)


def test_last_model_call_refuses_delegate_instead_of_starting_a_child():
    delegated = []
    state = PolicyImpactState(company_id="company_test")
    adapter = RecordingAdapter(
        [{"type": "delegate", "role": "skeptic", "task": "Review the evidence"}]
    )
    result = AgentLoop(
        adapter,
        _gateway("unused"),
        delegate_fn=lambda brief: delegated.append(brief),
        skill_manifest=_caller_manifest(),
        role_registry=build_default_subagent_role_registry(),
    ).run(state, _config(state, max_steps=2, max_model_calls=1))
    assert result.stop_reason == "max_model_calls"
    assert delegated == []
    assert result.turns[-1].observation["budget_boundary"]["action_type"] == "delegate"


def test_terminal_tool_observation_stops_without_spending_remaining_model_budget():
    tool_name = "terminal_research_scope"
    register_tool(tool_name, lambda: {"_runtime_terminal": {"kind": "empty_scope"}})
    try:
        state = PolicyImpactState(company_id="company_test")
        adapter = RecordingAdapter([
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
        ])
        result = AgentLoop(
            adapter,
            _gateway(tool_name),
        ).run(state, _config(state, max_steps=8, max_model_calls=8))
        assert result.stop_reason == "deterministic_complete"
        assert result.final_output == {}
        assert adapter.call_count == 1
        assert result.turns[0].observation == {
            "tool_result": {"_runtime_terminal": {"kind": "empty_scope"}}
        }
    finally:
        TOOL_REGISTRY.pop(tool_name, None)


def test_runtime_nudge_is_visible_and_allows_model_to_finalize():
    tool_name = "nudge_research_scope"
    register_tool(
        tool_name,
        lambda: {
            "_runtime_nudge": {
                "kind": "repeated_scope",
                "instruction": "Use a different scope or finalize.",
            },
            "items": [],
        },
    )
    try:
        state = PolicyImpactState(company_id="company_test")
        adapter = RecordingAdapter([
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"answer": "I changed scope and answered."}},
        ])
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_steps=4, max_model_calls=4)
        )
        assert result.stop_reason == "success"
        assert result.final_output["answer"] == "I changed scope and answered."
        assert "runtime_nudge" in json.dumps(adapter.message_batches[1], ensure_ascii=False)
    finally:
        TOOL_REGISTRY.pop(tool_name, None)


def _contains_key(value, forbidden):
    if isinstance(value, dict):
        return any(key in forbidden or _contains_key(item, forbidden) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _gateway(tool_name: str, decision: str = "allow") -> ToolGateway:
    return ToolGateway(
        permission_engine=PermissionEngine(
            [ToolPolicy("agent_stage", tool_name, decision, "test policy")]
        )
    )


def _caller_manifest(*, roles=("skeptic",)) -> SkillManifest:
    base = SkillRegistry().get_manifest("policy_weekly_impact")
    return SkillManifest.model_validate(
        {**base.model_dump(), "allowed_subagents": tuple(roles)}
    )


def _persist_child_records(state, brief, *, artifact_refs=(), **overrides):
    values = {
        "task_id": brief.task_id,
        "parent_run_id": brief.parent_run_id,
        "role": brief.role,
        "role_version": "1.0.0",
        "context_manifest_id": "ctx-child",
        "parent_span_id": brief.parent_span_id,
        "span_id": "span-child",
    }
    values.update(overrides)
    result = SubagentResult(
        **values,
        output={"counterexamples": [{"claim_ref": "claim-1"}]},
        stop_reason="success",
        artifact_refs=artifact_refs,
    )
    role = build_default_subagent_role_registry().get(values["role"], "1.0.0")
    caller_manifest = _caller_manifest()
    allowed_tools = [
        tool for tool in role.allowed_tools if tool in caller_manifest.allowed_tools
    ]
    visible_content = {
        "task_brief": brief.model_dump(mode="json"),
        "input_artifact_refs": list(brief.input_artifact_refs),
    }
    manifest_payload = {
        "brief": visible_content["task_brief"],
        "role_version": values["role_version"],
        "span_id": values["span_id"],
        "allowed_tools": allowed_tools,
        "tool_descriptors": [],
    }
    checksum = hashlib.sha256(
        json.dumps(
            manifest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    context_manifest_id = values.get("context_manifest_id")
    if context_manifest_id == "ctx-child":
        context_manifest_id = f"ctx_subagent_{checksum[:16]}"
        values["context_manifest_id"] = context_manifest_id
        result = SubagentResult(
            **values,
            output={"counterexamples": [{"claim_ref": "claim-1"}]},
            stop_reason="success",
            artifact_refs=artifact_refs,
        )
    state.add_context_manifest(
        {
            "record_id": context_manifest_id,
            "context_type": "subagent_context_manifest",
            "source_id": values["parent_run_id"],
            "checksum": checksum,
            "metadata": {
                "task_id": values["task_id"],
                "parent_run_id": values["parent_run_id"],
                "role": values["role"],
                "role_version": values["role_version"],
                "parent_span_id": values["parent_span_id"],
                "span_id": values["span_id"],
                "output_schema_name": brief.output_schema_name,
                "visible_content": visible_content,
                "allowed_tools": allowed_tools,
                "tool_descriptors": [],
            },
        }
    )
    state.add_stage_trace(
        {
            "stage": "subagent_delegate",
            "status": "ok",
            "task_id": values["task_id"],
            "parent_run_id": values["parent_run_id"],
            "role": values["role"],
            "role_version": values["role_version"],
            "parent_span_id": values["parent_span_id"],
            "span_id": values["span_id"],
            "context_manifest_id": values["context_manifest_id"],
            "stop_reason": "success",
        }
    )
    state.add_delegation_evidence(result.evidence().model_dump(mode="json"))
    for ref in artifact_refs:
        state.add_artifact(ref, f"artifact://{ref}")
    return result


def test_agent_action_union_is_discriminated_frozen_and_extra_forbid():
    actions = _actions_module()
    adapter = TypeAdapter(actions.AgentAction)

    action = adapter.validate_python({"type": "final", "output": {"assessment": "low"}})

    assert isinstance(action, actions.FinalAction)
    assert action.model_config["frozen"] is True
    assert action.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError, match="frozen"):
        action.output = {}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        adapter.validate_python(
            {"type": "tool_call", "tool_name": "search", "arguments": {}, "analysis": "x"}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "final", "output": "not-a-dict"},
        {"type": "tool_call", "tool_name": " ", "arguments": {}},
        {"type": "tool_call", "tool_name": "search", "arguments": []},
        {"type": "delegate", "role": " ", "task": "inspect", "input_artifact_refs": []},
        {"type": "delegate", "role": "reviewer", "task": " ", "input_artifact_refs": []},
        {
            "type": "request_approval",
            "tool_name": " ",
            "arguments": {},
            "reason": "needed",
        },
    ],
)
def test_agent_actions_reject_blank_identifiers_tasks_and_non_dict_payloads(payload):
    actions = _actions_module()

    with pytest.raises(ValidationError):
        TypeAdapter(actions.AgentAction).validate_python(payload)


def test_stop_reason_is_the_exact_runtime_taxonomy():
    assert set(get_args(StopReason)) == EXPECTED_STOP_REASONS


@pytest.mark.parametrize(
    "field",
    [
        "max_steps",
        "max_model_calls",
        "timeout_seconds",
        "max_calls_per_tool",
        "max_total_tool_calls",
        "max_repeated_failures",
    ],
)
def test_agent_loop_config_rejects_non_positive_budgets(field):
    state = PolicyImpactState(run_id=f"run_invalid_{field}")

    with pytest.raises(ValueError, match=field):
        _config(state, **{field: 0})


def test_agent_loop_config_defaults_to_agent_loop_mode():
    state = PolicyImpactState(run_id="run_default_execution_mode")

    assert _config(state).execution_mode is ExecutionMode.AGENT_LOOP


def test_deterministic_mode_completes_without_model_or_tool_calls():
    state = PolicyImpactState(run_id="run_deterministic_mode")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "unused"}}])
    gateway = _gateway("unused_tool")

    result = AgentLoop(adapter, gateway).run(
        state,
        _config(state, execution_mode=ExecutionMode.DETERMINISTIC),
    )

    assert result.stop_reason == "deterministic_complete"
    assert result.final_output == {}
    assert result.turns == []
    assert adapter.call_count == 0
    assert gateway.calls == []
    assert state.model_calls == []
    assert result.actual_execution_mode is ExecutionMode.DETERMINISTIC
    assert result.delegation_evidence == ()


def test_model_once_blocks_non_final_action_after_exactly_one_model_call():
    state = PolicyImpactState(run_id="run_model_once_tool_block")
    tool_name = "agent_loop_model_once_tool"
    executions = []
    register_tool(tool_name, lambda: executions.append(True) or {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "must not run"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(
                state,
                execution_mode=ExecutionMode.MODEL_ONCE,
                max_model_calls=4,
            ),
            max_model_calls=8,
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "gate_blocked"
    assert adapter.call_count == 1
    assert executions == []
    assert result.turns[0].error == "gate_blocked"
    assert result.turns[0].observation == {
        "mode_error": {
            "action_type": "tool_call",
            "category": "gate_blocked",
            "execution_mode": "model_once",
        }
    }


def test_model_once_accepts_one_final_action():
    state = PolicyImpactState(run_id="run_model_once_final")
    adapter = RecordingAdapter(
        [
            {"type": "final", "output": {"assessment": "complete"}},
            {"type": "final", "output": {"assessment": "must not run"}},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, execution_mode=ExecutionMode.MODEL_ONCE),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "complete"}
    assert adapter.call_count == 1
    assert result.actual_execution_mode is ExecutionMode.MODEL_ONCE


def test_workflow_mode_is_rejected_before_any_model_or_tool_call():
    state = PolicyImpactState(run_id="run_workflow_rejected")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "unused"}}])
    gateway = _gateway("unused_tool")

    with pytest.raises(ValueError, match="WORKFLOW.*AgentLoop"):
        AgentLoop(adapter, gateway).run(
            state,
            _config(state, execution_mode=ExecutionMode.WORKFLOW),
        )

    assert adapter.call_count == 0
    assert gateway.calls == []


def test_planned_subagent_workflow_without_delegation_reports_model_once():
    state = PolicyImpactState(run_id="run_subagent_workflow_mode")
    adapter = RecordingAdapter(
        [{"type": "final", "output": {"assessment": "subagent complete"}}]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, execution_mode=ExecutionMode.SUBAGENT_WORKFLOW),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "subagent complete"}
    assert adapter.call_count == 1
    assert result.actual_execution_mode is ExecutionMode.MODEL_ONCE
    assert result.delegation_evidence == ()


def test_invalid_action_gets_one_sanitized_replan_then_stops():
    state = PolicyImpactState(run_id="run_invalid_action")
    adapter = RecordingAdapter(
        [
            {"type": "unknown", "analysis": "FIRST_SECRET"},
            {"type": "still_unknown", "thinking": "SECOND_SECRET"},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "invalid_model_action"
    assert adapter.call_count == 2
    validation_message = adapter.message_batches[1][-1]
    assert validation_message["role"] == "system"
    assert set(validation_message["content"]) == {"allowed_actions", "validation_errors"}
    assert set(validation_message["content"]["allowed_actions"]) == ALLOWED_ACTIONS
    persisted = json.dumps({"calls": state.model_calls, "turns": result.to_dict()})
    assert "FIRST_SECRET" not in persisted
    assert "SECOND_SECRET" not in persisted
    assert all(
        set(turn.model_payload) <= {"action_type", "validation_errors"}
        for turn in result.turns
    )


def test_reasoning_fields_are_rejected_and_never_persisted():
    state = PolicyImpactState(run_id="run_no_reasoning")
    adapter = RecordingAdapter(
        [
            {
                "type": "final",
                "output": {"assessment": "must not persist yet"},
                "reasoning": "PAYLOAD_SECRET",
            },
            {"type": "final", "output": {"assessment": "safe"}},
        ],
        metadata={"analysis": "METADATA_SECRET", "provider": "test"},
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "safe"}
    persisted = {"calls": state.model_calls, "turns": result.to_dict()}
    serialized = json.dumps(persisted)
    assert "PAYLOAD_SECRET" not in serialized
    assert "METADATA_SECRET" not in serialized
    assert not _contains_key(persisted, {"reasoning", "thinking", "analysis"})


def test_model_call_budget_is_enforced_before_an_extra_call():
    state = PolicyImpactState(run_id="run_model_budget")
    tool_name = "agent_loop_model_budget_tool"
    register_tool(tool_name, lambda: {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(state),
            max_model_calls=1,
            max_steps=4,
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_model_calls"
    assert adapter.call_count == 1


def test_step_budget_is_enforced():
    state = PolicyImpactState(run_id="run_step_budget")
    tool_name = "agent_loop_step_budget_tool"
    register_tool(tool_name, lambda: {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_steps=1, max_model_calls=3)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_steps"
    assert adapter.call_count == 1


def test_monotonic_deadline_stops_before_model_call(monkeypatch):
    state = PolicyImpactState(run_id="run_timeout")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "late"}}])
    readings = iter([10.0, 12.0])
    monkeypatch.setattr("policy_impact.harness.agent_loop.time.monotonic", lambda: next(readings))

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state, _config(state, timeout_seconds=1)
    )

    assert result.stop_reason == "timeout"
    assert adapter.call_count == 0


def test_per_tool_call_budget_prevents_extra_execution():
    state = PolicyImpactState(run_id="run_tool_budget")
    tool_name = "agent_loop_call_budget_tool"
    executions = []
    register_tool(tool_name, lambda query: executions.append(query) or {"query": query})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "three"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_calls_per_tool=1)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "tool_denied"
    assert executions == ["one"]


def test_per_tool_call_budget_allows_finalizing_from_prior_observations():
    state = PolicyImpactState(run_id="run_tool_budget_final")
    tool_name = "agent_loop_call_budget_final_tool"
    executions = []
    register_tool(tool_name, lambda query: executions.append(query) or {"query": query})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
            {"type": "final", "output": {"assessment": "one observation suffices"}},
        ]
    )
    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_calls_per_tool=1)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "one observation suffices"}
    assert executions == ["one"]
    assert result.turns[1].observation["tool_error"]["category"] == "tool_denied"


def test_total_tool_budget_stops_new_research_but_allows_review_final():
    state = PolicyImpactState(run_id="run_total_tool_budget_final")
    tool_name = "agent_loop_total_budget_final_tool"
    executions = []
    register_tool(tool_name, lambda query: executions.append(query) or {"query": query})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
            {"type": "final", "output": {"assessment": "partial review"}},
        ]
    )
    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_calls_per_tool=3, max_total_tool_calls=1)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "partial review"}
    assert executions == ["one"]
    assert result.turns[1].observation["tool_error"]["reason"].startswith("research tool budget reached")


def test_repeated_identical_tool_failure_uses_normalized_argument_hash():
    state = PolicyImpactState(run_id="run_repeated_failure")
    tool_name = "agent_loop_repeated_failure_tool"

    def fail(**kwargs):
        raise RuntimeError("PRIVATE_FAILURE_DETAIL")

    register_tool(tool_name, fail)
    adapter = RecordingAdapter(
        [
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": {"company_id": "c1", "query": "missing"},
            },
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": {"query": "missing", "company_id": "c1"},
            },
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "repeated_tool_failure"
    assert adapter.call_count == 2
    assert "PRIVATE_FAILURE_DETAIL" not in json.dumps(result.to_dict())
    assert result.turns[0].observation["tool_error"]["category"] == "tool_failed"


@pytest.mark.parametrize("same_tool", [False, True])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, TimeoutError])
def test_tool_call_ids_follow_real_invocations_after_failure(same_tool, error_type):
    state = PolicyImpactState(company_id="company_test")

    def read(*, fail):
        if fail:
            raise error_type("injected failure")
        return {"text": "observed source"}

    gateway = ToolGateway(
        PermissionEngine([
            ToolPolicy("agent_stage", name, "allow", "test") for name in ("first", "second")
        ]), local_tools={"first": read, "second": read},
    )
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "first", "arguments": {"fail": True}},
        {"type": "tool_call", "tool_name": "first" if same_tool else "second", "arguments": {"fail": False}},
        {"type": "final", "output": {"answer": "source-bound analysis with the gap disclosed"}},
    ])
    result = AgentLoop(adapter, gateway).run(state, _config(state, recover_tool_timeouts=True))

    assert result.stop_reason == "success"
    assert [turn.tool_call_id for turn in result.turns] == [
        gateway.calls[0].call_id, gateway.calls[1].call_id, None,
    ]
    assert gateway.calls[0].status == "failed"
    assert gateway.calls[1].status == "ok"
    assert gateway.calls[0].call_id != gateway.calls[1].call_id
    assert result.turns[1].observation == {"tool_result": {"text": "observed source"}}
    assert "tool_call_id" not in json.dumps(adapter.message_batches)


def test_pre_gateway_rejection_has_no_borrowed_tool_call_id():
    class ValidatingGateway(ToolGateway):
        def call(self, stage_name, tool_name, **kwargs):
            if kwargs.get("unknown_field"):
                raise ValueError("tool_contract_error: unknown field")
            return super().call(stage_name, tool_name, **kwargs)

    gateway = ValidatingGateway(
        PermissionEngine([ToolPolicy("agent_stage", "read", "allow", "test")]),
        local_tools={"read": lambda: {"text": "observed source"}},
    )
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "read", "arguments": {"unknown_field": True}},
        {"type": "tool_call", "tool_name": "read", "arguments": {}},
        {"type": "final", "output": {"answer": "done"}},
    ])
    state = PolicyImpactState(company_id="company_test")
    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "success"
    assert len(gateway.calls) == 1
    assert result.turns[0].tool_call_id is None
    assert result.turns[1].tool_call_id == gateway.calls[0].call_id


def test_permission_denial_binds_the_blocked_record():
    state = PolicyImpactState(company_id="company_test")
    gateway = _gateway("read", decision="deny")
    adapter = RecordingAdapter([{"type": "tool_call", "tool_name": "read", "arguments": {}}])
    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "tool_denied"
    assert result.turns[0].tool_call_id == gateway.calls[0].call_id
    assert gateway.calls[0].status == "blocked"


def test_trace_ids_are_optional_for_a_gateway_without_a_call_ledger():
    class LocalGateway:
        def call(self, stage_name, tool_name, **kwargs):
            return {"text": "local observation"}

    state = PolicyImpactState(company_id="company_test")
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "read", "arguments": {}},
        {"type": "final", "output": {"answer": "done"}},
    ])
    result = AgentLoop(adapter, LocalGateway()).run(state, _config(state))
    assert result.stop_reason == "success"
    assert result.turns[0].tool_call_id is None
    assert result.turns[0].observation == {"tool_result": {"text": "local observation"}}


def test_research_plan_gate_has_one_runtime_recovery_without_fixing_the_workflow():
    state = PolicyImpactState(run_id="run_plan_gate_recovery")
    plan_created = False
    calls = []

    def article_list(**kwargs):
        calls.append("article.list")
        if not plan_created:
            raise ValueError(
                "research_plan_required: call research.plan first, then retry the selected tool"
            )
        return {"articles": [{"article_id": "a1"}]}

    def create_plan(**kwargs):
        nonlocal plan_created
        plan_created = True
        calls.append("research.plan")
        return {
            "goals": list(kwargs["goals"]),
            "pending_count": 2,
            "scope": kwargs.get("scope") or {},
        }

    gateway = ToolGateway(
        PermissionEngine([
            ToolPolicy("agent_stage", "article.list", "allow", "test"),
            ToolPolicy("agent_stage", "research.plan", "allow", "test"),
        ]),
        local_tools={"article.list": article_list, "research.plan": create_plan},
    )
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "article.list", "arguments": {}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {}},
        {"type": "final", "output": {"answer": "基于已读取文章回答。"}},
    ])

    result = AgentLoop(adapter, gateway).run(
        state,
        _config(state, max_steps=4, max_model_calls=4, max_calls_per_tool=3),
    )

    assert result.stop_reason == "success"
    assert calls == ["article.list", "article.list", "research.plan", "article.list"]
    assert [turn.tool_call_id for turn in result.turns] == [
        gateway.calls[0].call_id, gateway.calls[1].call_id, gateway.calls[3].call_id, None,
    ]
    assert gateway.calls[2].call_id not in {turn.tool_call_id for turn in result.turns}
    assert result.turns[0].observation["tool_error"]["category"] == "research_plan_required"
    recovery = result.turns[1].observation["plan_recovery"]
    assert recovery["ok"] is True
    assert recovery["plan"]["pending_count"] == 2
    assert "Runtime created only a minimal requirements plan" in json.dumps(
        adapter.message_batches[2], ensure_ascii=False
    )


def test_tool_failure_observation_allows_bounded_replan():
    state = PolicyImpactState(run_id="run_tool_replan")
    tool_name = "agent_loop_failure_replan_tool"
    register_tool(tool_name, lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE_DETAIL")))
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "replanned"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "replanned"}
    assert result.turns[0].observation["tool_error"] == {
        "category": "tool_failed",
        "tool_name": tool_name,
        "reason": "tool execution failed (RuntimeError); inspect the tool schema and prior observations",
    }
    assert "PRIVATE_DETAIL" not in json.dumps(result.to_dict())


def test_timeout_error_maps_to_tool_timeout():
    state = PolicyImpactState(run_id="run_tool_timeout")
    tool_name = "agent_loop_timeout_tool"
    register_tool(tool_name, lambda: (_ for _ in ()).throw(TimeoutError("PRIVATE_TIMEOUT")))
    adapter = RecordingAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {}}]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "tool_timeout"
    assert result.turns[0].observation["tool_error"]["category"] == "tool_timeout"
    assert result.turns[0].observation["tool_error"]["reason"].startswith("tool timed out;")
    assert "PRIVATE_TIMEOUT" not in json.dumps(result.to_dict())


def test_recoverable_timeout_allows_final_answer_with_gap():
    state = PolicyImpactState(run_id="run_recoverable_timeout")
    tool_name = "agent_loop_recoverable_timeout"
    register_tool(tool_name, lambda: (_ for _ in ()).throw(TimeoutError("PRIVATE_TIMEOUT")))
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
        {"type": "final", "output": {"answer": "公开网页超时，以下结论只依据已观察材料。"}},
    ])
    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, recover_tool_timeouts=True),
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)
    assert result.stop_reason == "success"
    assert result.turns[0].observation["tool_error"]["category"] == "tool_timeout"
    assert "网页超时" in result.final_output["answer"]


def test_finalization_reserve_denies_new_acquisition_but_keeps_plan_tool_available():
    state = PolicyImpactState(run_id="run_research_reserve")
    calls = []
    gateway = ToolGateway(permission_engine=PermissionEngine([
        ToolPolicy("agent_stage", "article.read", "allow", "test"),
        ToolPolicy("agent_stage", "research.plan", "allow", "test"),
    ]), local_tools={
        "article.read": lambda: calls.append("read") or {"text": "source"},
        "research.plan": lambda: calls.append("plan") or {"status": "done"},
    })
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {}},
        *[{"type": "tool_call", "tool_name": "research.plan", "arguments": {}}
          for _ in range(7)],
        {"type": "tool_call", "tool_name": "article.read", "arguments": {}},
        {"type": "final", "output": {"answer": "基于已有证据回答。"}},
    ])
    result = AgentLoop(adapter, gateway).run(
        state, _config(state, max_steps=10, max_model_calls=10,
                       max_calls_per_tool=10, finalization_reserve_calls=2),
    )
    assert result.stop_reason == "success"
    assert calls == ["read", *["plan"] * 7]
    assert result.turns[8].observation["tool_error"]["category"] == "tool_denied"
    assert result.turns[8].tool_call_id is None


def test_invalid_tool_output_fails_closed_without_stringifying_object():
    class UnsafeResult:
        def __str__(self):
            raise AssertionError("arbitrary result must not be stringified")

    state = PolicyImpactState(run_id="run_invalid_tool_output")
    tool_name = "agent_loop_invalid_output_tool"
    register_tool(tool_name, UnsafeResult)
    adapter = RecordingAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {}}]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "invalid_tool_output"
    assert result.turns[0].observation["tool_error"] == {
        "category": "invalid_tool_output",
        "tool_name": tool_name,
        "reason": "tool returned a non-JSON value; use structured fields from prior observations",
    }


def test_request_approval_stops_without_executing_tool():
    state = PolicyImpactState(run_id="run_request_approval")
    tool_name = "agent_loop_approval_tool"
    gateway = _gateway(tool_name)
    adapter = RecordingAdapter(
        [
            {
                "type": "request_approval",
                "tool_name": tool_name,
                "arguments": {"query": "policy"},
                "reason": "external write",
            }
        ]
    )

    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "awaiting_approval"
    assert gateway.calls == []


def test_absent_delegate_capability_adds_denied_observation_then_replans():
    state = PolicyImpactState(run_id="run_delegate_denied")
    gateway = _gateway("unused_tool")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Review the evidence",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "completed locally"}},
        ]
    )

    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "success"
    assert gateway.calls == []
    assert result.turns[0].observation == {
        "delegate_error": {"category": "tool_denied", "role": "skeptic"}
    }
    assert adapter.call_count == 2
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()


def test_successful_delegate_receives_task_brief_and_returns_only_public_result():
    state = PolicyImpactState(run_id="run-delegate-success")
    captured = []
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim-1",
                "input_artifact_refs": ["claim-1"],
            },
            {"type": "final", "output": {"assessment": "verified after challenge"}},
        ]
    )

    def delegate(brief):
        captured.append(brief)
        return _persist_child_records(
            state,
            brief,
            artifact_refs=("counterexample-1",),
        )

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert len(captured) == 1
    brief = captured[0]
    assert brief.parent_run_id == state.run_id
    assert brief.role == "skeptic"
    assert brief.task == "Challenge claim-1"
    assert brief.input_artifact_refs == ("claim-1",)
    assert brief.output_schema_name == "counterexample_set.v1"
    assert result.actual_execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert len(result.delegation_evidence) == 1
    assert result.delegation_evidence[0].span_id == "span-child"
    observation = result.turns[0].observation["subagent_result"]
    assert {"role", "output", "artifact_refs"}.issubset(observation)
    assert observation["task_id"] == brief.task_id
    assert observation["stop_reason"] == "success"
    assert observation["context_manifest_id"].startswith("ctx_subagent_")
    assert observation["output"] == {"counterexamples": [{"claim_ref": "claim-1"}]}
    assert observation["artifact_refs"] == ["counterexample-1"]
    assert "ctx-child" not in json.dumps(adapter.message_batches[1])


def test_roster_display_name_resolves_to_authorized_delegate_role():
    state = PolicyImpactState(run_id="run-delegate-roster-name")
    captured = []
    adapter = RecordingAdapter([
        {"type": "delegate", "role": "skeptic-reviewer", "task": "Challenge claim-1"},
        {"type": "final", "output": {"assessment": "reviewed"}},
    ])

    def delegate(brief):
        captured.append(brief)
        return _persist_child_records(state, brief, artifact_refs=("counterexample-1",))

    result = AgentLoop(
        adapter, _gateway("unused_tool"), delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
        role_aliases={"skeptic-reviewer": "skeptic"},
    ).run(state, _config(state))
    assert result.stop_reason == "success"
    assert captured[0].role == "skeptic"
    assert result.turns[0].observation["subagent_result"]["role"] == "skeptic"


def test_delegate_misencoded_as_tool_call_uses_action_repair():
    state = PolicyImpactState(run_id="run-delegate-action-repair")
    captured = []
    adapter = RecordingAdapter([
        {"type": "tool_call", "tool_name": "delegate", "arguments": {
            "role": "skeptic", "task": "Challenge claim-1",
        }},
        {"type": "delegate", "role": "skeptic", "task": "Challenge claim-1"},
        {"type": "final", "output": {"assessment": "reviewed"}},
    ])

    def delegate(brief):
        captured.append(brief)
        return _persist_child_records(state, brief, artifact_refs=("counterexample-1",))

    result = AgentLoop(
        adapter, _gateway("unused_tool"), delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))
    assert result.stop_reason == "success"
    assert len(captured) == 1
    assert "action type" in str(adapter.message_batches[1])


def test_roster_alias_cannot_grant_an_unauthorized_role():
    state = PolicyImpactState(run_id="run-delegate-roster-denied")
    adapter = RecordingAdapter([
        {"type": "delegate", "role": "fake-reviewer", "task": "Review"},
        {"type": "final", "output": {"assessment": "no delegation"}},
    ])
    result = AgentLoop(
        adapter, _gateway("unused_tool"), delegate_fn=lambda brief: pytest.fail("must deny"),
        skill_manifest=_caller_manifest(), role_aliases={"fake-reviewer": "verifier"},
    ).run(state, _config(state))
    assert result.turns[0].observation["delegate_error"]["category"] == "tool_denied"


def test_delegate_exception_is_sanitized_and_allows_one_replan():
    state = PolicyImpactState(run_id="run-delegate-error")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "SENSITIVE_DELEGATE_TASK",
                "input_artifact_refs": ["SECRET_ARTIFACT_REF"],
            },
            {"type": "final", "output": {"assessment": "completed locally"}},
        ]
    )

    def fail_delegate(brief):
        raise RuntimeError(f"PRIVATE_EXCEPTION::{brief.task}::{brief.input_artifact_refs[0]}")

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=fail_delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation == {
        "delegate_error": {"category": "delegate_failed", "role": "skeptic"}
    }
    persisted = json.dumps(result.to_dict())
    assert "PRIVATE_EXCEPTION" not in persisted
    assert "SENSITIVE_DELEGATE_TASK" not in persisted
    assert "SECRET_ARTIFACT_REF" not in persisted


def test_delegate_fn_is_not_called_when_caller_manifest_disallows_role():
    state = PolicyImpactState(run_id="run-delegate-role-denied")
    called = []
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        called.append(brief)
        raise AssertionError("disallowed delegate must not run")

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(roles=("collector",)),
    ).run(state, _config(state))

    assert called == []
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation == {
        "delegate_error": {"category": "tool_denied", "role": "skeptic"}
    }


@pytest.mark.parametrize(
    ("overrides", "mutation"),
    [
        ({"parent_run_id": "forged-run"}, None),
        ({"role_version": "9.9.9"}, None),
        ({"context_manifest_id": "ctx-forged"}, "drop_context"),
        ({"span_id": "span-forged"}, "drop_trace"),
        ({}, "drop_evidence"),
    ],
)
def test_forged_or_incomplete_child_records_never_enable_subagent_mode(overrides, mutation):
    state = PolicyImpactState(run_id="run-forged-delegation")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        result = _persist_child_records(state, brief, **overrides)
        if mutation == "drop_context":
            state.context_manifests.clear()
        elif mutation == "drop_trace":
            state.stage_trace.clear()
        elif mutation == "drop_evidence":
            state.delegation_evidence.clear()
        return result

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation["delegate_error"]["category"] == "delegate_failed"


def test_delegate_rejects_model_reported_unknown_artifact_refs():
    state = PolicyImpactState(run_id="run-forged-artifact-ref")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        result = _persist_child_records(
            state,
            brief,
            artifact_refs=("unknown-artifact",),
        )
        state.artifacts.clear()
        return result

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()


def test_final_action_recursively_removes_hidden_reasoning_from_output_and_audit():
    state = PolicyImpactState(run_id="run-final-reasoning-sanitize")
    adapter = RecordingAdapter(
        [
            {
                "type": "final",
                "output": {
                    "answer": "public",
                    "nested": {
                        "reasoning": "PRIVATE_REASONING",
                        "items": [{"analysis": "PRIVATE_ANALYSIS", "value": 1}],
                    },
                },
            }
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    serialized = json.dumps({"result": result.to_dict(), "calls": state.model_calls})
    assert "PRIVATE_REASONING" not in serialized
    assert "PRIVATE_ANALYSIS" not in serialized
    assert result.final_output == {"answer": "public", "nested": {"items": [{"value": 1}]}}


@pytest.mark.parametrize(
    "hidden_key",
    ["chain_of_thought", "private_reasoning", "reasoning_text"],
)
def test_final_action_removes_hidden_reasoning_aliases(hidden_key):
    state = PolicyImpactState(run_id=f"run-hidden-{hidden_key}")
    adapter = RecordingAdapter(
        [{"type": "final", "output": {"answer": "public", hidden_key: "SECRET"}}]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.final_output == {"answer": "public"}
    assert "SECRET" not in json.dumps({"result": result.to_dict(), "calls": state.model_calls})


def test_delegate_denial_does_not_consume_invalid_action_repair():
    state = PolicyImpactState(run_id="run_delegate_then_invalid")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Review the evidence",
                "input_artifact_refs": [],
            },
            {"type": "unknown", "analysis": "PRIVATE_INVALID_VALUE"},
            {"type": "final", "output": {"assessment": "repaired"}},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, max_steps=4, max_model_calls=4),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "repaired"}
    assert adapter.call_count == 3
    assert adapter.message_batches[2][-1]["role"] == "system"
    assert set(adapter.message_batches[2][-1]["content"]) == {
        "allowed_actions",
        "validation_errors",
    }
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(result.to_dict())


def test_agent_loop_calls_allowed_tool_then_finishes_successfully():
    state = PolicyImpactState(run_id="run_agent_loop")
    tool_name = "agent_loop_test_tool"
    register_tool(tool_name, lambda query: {"answer": f"observed {query}"})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}},
            {"type": "final", "output": {"assessment": "low impact"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "low impact"}
    assert len(result.turns) == 2
    assert result.turns[0].observation == {"tool_result": {"answer": "observed policy"}}
    assert [call["response_id"] for call in state.model_calls] == ["scripted_1", "scripted_2"]
    assert all(call["stage_name"] == "agent_stage" for call in state.model_calls)
    assert all(call["agent_role"] == "policy_agent" for call in state.model_calls)
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP


def test_agent_loop_sends_assistant_tool_call_before_tool_observation():
    class RecordingAdapter:
        def __init__(self):
            self.message_batches = []
            self.payloads = [
                {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}},
                {"type": "final", "output": {"assessment": "low impact"}},
            ]

        def complete(self, messages, schema_name):
            self.message_batches.append(copy.deepcopy(messages))
            response_index = len(self.message_batches)
            return ModelResponse(
                response_id=f"recording_{response_index}",
                payload=self.payloads[response_index - 1],
            )

    state = PolicyImpactState(run_id="run_message_contract")
    tool_name = "agent_loop_message_contract_tool"
    register_tool(tool_name, lambda query: {"answer": query})
    adapter = RecordingAdapter()

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    second_call_messages = adapter.message_batches[1]
    assert [message["role"] for message in second_call_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert second_call_messages[2] == {
        "role": "assistant",
        "response_id": "recording_1",
        "content": {
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": {"query": "policy"},
        },
    }
    assert second_call_messages[3] == {
        "role": "tool",
        "tool_name": tool_name,
        "content": {"tool_result": {"answer": "policy"}},
    }


def test_agent_loop_normalizes_tool_observation_to_json_safe_content():
    state = PolicyImpactState(run_id="run_json_safe_observation")
    tool_name = "agent_loop_json_safe_tool"
    register_tool(tool_name, lambda: {"ids": {2, 1}, "path": Path("policy.txt")})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "serialized"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.turns[0].observation == {
        "tool_result": {"ids": [1, 2], "path": "policy.txt"}
    }
    assert json.loads(json.dumps(result.to_dict()))["turns"][0]["observation"] == {
        "tool_result": {"ids": [1, 2], "path": "policy.txt"}
    }


def test_agent_loop_stops_with_tool_denied_when_permission_blocks_tool():
    state = PolicyImpactState(run_id="run_tool_denied")
    tool_name = "agent_loop_denied_tool"
    adapter = ScriptedModelAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}}]
    )

    result = AgentLoop(adapter, _gateway(tool_name, decision="deny")).run(state, _config(state))

    assert result.stop_reason == "tool_denied"
    assert result.final_output == {}
    assert len(result.turns) == 1
    assert result.turns[0].error == "tool_denied"
    assert result.turns[0].observation == {
        "tool_error": {
            "category": "tool_denied",
            "tool_name": tool_name,
            "reason": "test policy",
        }
    }
    assert len(state.model_calls) == 1


def test_legacy_max_turns_constructor_maps_to_max_steps_stop_reason():
    state = PolicyImpactState(run_id="run_max_turns")
    tool_name = "agent_loop_repeat_tool"
    register_tool(tool_name, lambda query: {"answer": query})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state, max_turns=2))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_steps"
    assert result.final_output == {}
    assert len(result.turns) == 2
    assert len(state.model_calls) == 2


def test_agent_loop_returns_model_error_when_adapter_raises():
    class RaisingAdapter:
        model_id = "test-model"
        provider_name = "test-provider"
        max_attempts = 5

        def complete(self, messages, schema_name):
            raise RuntimeError("model unavailable")

    state = PolicyImpactState(run_id="run_model_error")

    result = AgentLoop(RaisingAdapter(), _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "model_error"
    assert result.final_output == {}
    assert len(result.turns) == 1
    assert result.turns[0].error == "model unavailable"
    assert state.errors[-1]["message"] == "model_error"
    assert len(state.model_calls) == 1
    assert state.model_calls[0]["payload"]["type"] == "model_error"
    assert state.model_calls[0]["metadata"]["model_id"] == "test-model"
    assert state.model_calls[0]["metadata"]["attempts"] is None
    assert state.model_calls[0]["metadata"]["max_attempts"] == 5
    assert state.model_calls[0]["metadata"]["context_summary"]["message_count"] == 2


def test_agent_loop_retains_failed_stream_attempt_diagnostics():
    from policy_impact.harness.model_gateway import ModelInvocationError

    diagnostics = [{"attempt": 1, "transport": "sse", "stream_complete": False,
                    "stream_content_chars": 53, "stream_event_count": 4}]

    class FailedStreamAdapter:
        max_attempts = 5

        def complete(self, messages, schema_name):
            raise ModelInvocationError("stream transport error: EOF", metadata={
                "attempts": 1, "transport_attempts": diagnostics,
            })

    state = PolicyImpactState(run_id="run_stream_error")
    result = AgentLoop(FailedStreamAdapter(), _gateway("unused_tool")).run(state, _config(state))
    assert result.stop_reason == "model_error"
    assert state.model_calls[0]["metadata"]["attempts"] == 1
    assert state.model_calls[0]["metadata"]["transport_attempts"] == diagnostics


@pytest.mark.parametrize("metadata", [None, [], {"preserved_final_output": "invalid body"}])
def test_model_failure_with_invalid_metadata_does_not_break_error_delivery(metadata):
    class InvalidMetadataAdapter:
        def complete(self, messages, schema_name):
            failure = RuntimeError("model unavailable")
            failure.metadata = metadata
            raise failure

    state = PolicyImpactState(run_id="run_bad_failure_metadata")
    result = AgentLoop(InvalidMetadataAdapter(), _gateway("unused_tool")).run(state, _config(state))
    assert result.stop_reason == "model_error"
    assert result.final_output == {}
    assert state.model_calls[0]["payload"]["type"] == "model_error"


def test_agent_loop_preserves_public_failure_diagnostics_only_in_trace():
    from policy_impact.harness.model_gateway import ModelInvocationError

    diagnostic = {"attempt": 1, "response_id": "invalid-public", "text": "public invalid action"}

    class InvalidResponseAdapter:
        def complete(self, messages, schema_name):
            raise ModelInvocationError("invalid response", metadata={
                "attempts": 1, "failed_public_responses": [diagnostic],
            })

    state = PolicyImpactState(run_id="run_invalid_public")
    gateway = _gateway("unused_tool")
    result = AgentLoop(InvalidResponseAdapter(), gateway).run(state, _config(state))
    assert result.stop_reason == "model_error"
    assert result.final_output == {}
    assert not gateway.calls
    assert state.model_calls[0]["metadata"]["failed_public_responses"] == [diagnostic]
    assert state.model_calls[0]["payload"]["type"] == "model_error"
