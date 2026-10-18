"""Required delegated reads must execute and be audited before the first model call."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from policy_impact.harness import subagents
from policy_impact.harness.model_gateway import ModelResponse
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.harness.tool_gateway import get_tool_audit_log, reset_tool_audit_log
from policy_impact.runtime.subagent_roles import SubagentRoleRegistry
from policy_impact.runtime.task_artifacts import InMemoryTaskArtifactResolver
from policy_impact.skills.registry import SkillRegistry


REF = "review_context:required-read-test"
FINAL = {"type": "final", "output": {"counterexamples": [], "notes": ["Read it."]}}
PAYLOAD = {"assessments": [{"assessment_id": "claim-1", "claim": "Delegated claim"}]}


class RecordingModel:
    def __init__(self, events, actions=None):
        self.events = events
        self.actions = actions or [FINAL]
        self.messages = []

    def complete(self, messages, schema_name):
        self.events.append("model")
        self.messages.append(deepcopy(messages))
        action = self.actions[min(len(self.messages) - 1, len(self.actions) - 1)]
        return ModelResponse(response_id=f"response-{len(self.messages)}", payload=action)


class RecordingResolver(InMemoryTaskArtifactResolver):
    def __init__(self, events, payloads=None):
        super().__init__(payloads if payloads is not None else {REF: PAYLOAD})
        self.events = events

    def read(self, artifact_ref):
        self.events.append(f"read:{artifact_ref}")
        return super().read(artifact_ref)


@pytest.fixture(autouse=True)
def isolated_audit_log():
    reset_tool_audit_log()
    yield
    reset_tool_audit_log()


def _runner(model, *, tools=("artifact_read",), **kwargs):
    manifest = SkillRegistry().get_manifest("policy_weekly_impact").model_copy(update={
        "allowed_subagents": ("skeptic",), "allowed_tools": tools,
    })
    return SubagentRunner(model_adapter=model, skill_manifest=manifest, **kwargs)


def _run(runner, state, resolver, **kwargs):
    return runner.run(
        state, "skeptic", task="Challenge the delegated evidence.",
        input_artifact_refs=(REF,), required_artifact_reads=(REF,),
        artifact_resolver=resolver, **kwargs,
    )


def test_direct_final_has_real_audited_context_before_first_model_call():
    events = []
    state = PolicyImpactState(run_id="required-read-test")
    state.company_context = {"private": "NON_DELEGATED_SECRET"}
    resolver = RecordingResolver(events, {REF: PAYLOAD, "secret": state.company_context})
    model = RecordingModel(events)
    runner = _runner(model)

    result = _run(runner, state, resolver)

    assert events == [f"read:{REF}", "model"]
    assert result.stop_reason == "success"
    assert len(state.model_calls) == 1
    observation = model.messages[0][0]["content"]["visible_context"]["artifact_observations"][0]
    assert observation["tool_result"] == PAYLOAD
    assert observation["artifact_ref"] == REF
    audit = state.tool_calls[0]
    assert audit == get_tool_audit_log()[0] == runner.tool_gateway.calls[0].to_dict()
    assert audit["status"] == "ok" and audit["allowed"] is True
    assert audit["initiated_by"] == "harness_preload"
    assert audit["task_id"] == result.task_id
    assert audit["span_id"] == result.span_id
    assert audit["context_manifest_id"] == result.context_manifest_id
    assert audit["parent_run_id"] == state.run_id
    assert audit["call_id"] == observation["call_id"]
    canonical = json.dumps(PAYLOAD, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert audit["result_checksum"] == observation["result_checksum"] == sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert state.model_calls[0]["messages"] == model.messages[0]
    assert audit["finished_at"] <= state.model_calls[0]["created_at"]
    assert "NON_DELEGATED_SECRET" not in json.dumps(model.messages)
    assert state.stage_trace[-1]["artifact_read_audit_refs"] == [audit["call_id"]]
    assert len(state.delegation_evidence) == 1
    observation["tool_result"]["assessments"][0]["claim"] = "changed copy"
    assert resolver.read(REF) == PAYLOAD


@pytest.mark.parametrize("mode,expected", [
    ("no_tool", "tool_denied"), ("missing", "tool_denied"),
    ("error", "tool_failed"), ("timeout", "tool_timeout"),
    ("invalid", "invalid_tool_output"),
])
def test_failed_required_read_never_calls_model_or_emits_success(mode, expected):
    events = []
    model = RecordingModel(events)
    resolver = RecordingResolver(events, {} if mode == "missing" else None)
    if mode == "error":
        resolver.read = Mock(side_effect=RuntimeError("PRIVATE_RESOLVER_DETAIL"))
    elif mode == "timeout":
        resolver.read = Mock(side_effect=TimeoutError("PRIVATE_RESOLVER_DETAIL"))
    elif mode == "invalid":
        resolver.read = Mock(return_value={"invalid": float("nan")})
    state = PolicyImpactState(run_id="required-read-test")
    # An earlier read or a claim in notes must not satisfy this invocation.
    state.tool_calls.append({"tool_name": "artifact_read", "artifact_ref": REF, "status": "ok"})

    result = _run(_runner(model, tools=() if mode == "no_tool" else ("artifact_read",)),
                  state, resolver)

    assert result.stop_reason == expected
    assert result.output == {}
    assert model.messages == [] and state.model_calls == []
    assert state.delegation_evidence == []
    assert state.stage_trace[-1]["status"] == "failed"
    assert state.tool_calls[-1]["status"] in {"failed", "blocked"}
    assert "PRIVATE_RESOLVER_DETAIL" not in json.dumps(state.to_dict())


@pytest.mark.parametrize("required", [(REF, REF), ("secret",)])
def test_required_refs_cannot_expand_delegation_or_repeat(required):
    events = []
    model = RecordingModel(events)
    with pytest.raises(ValueError, match="unique delegated input"):
        _runner(model).run(
            PolicyImpactState(), "skeptic", input_artifact_refs=(REF,),
            required_artifact_reads=required,
            artifact_resolver=RecordingResolver(events, {REF: PAYLOAD, "secret": PAYLOAD}),
        )
    assert events == [] and get_tool_audit_log() == []


def test_all_required_inputs_are_read_and_second_failure_cannot_be_ignored():
    events = []
    model = RecordingModel(events)
    state = PolicyImpactState()
    result = _runner(model).run(
        state, "skeptic", input_artifact_refs=(REF, "missing"),
        required_artifact_reads=(REF, "missing"), artifact_resolver=RecordingResolver(events),
    )
    assert result.stop_reason == "tool_denied"
    assert events == [f"read:{REF}"]
    assert [call["status"] for call in state.tool_calls] == ["ok", "blocked"]
    assert state.delegation_evidence == []


def test_required_read_audit_failure_stops_before_read_and_model(monkeypatch):
    log = Mock()
    log.append.side_effect = OSError("audit unavailable")
    monkeypatch.setattr(subagents, "_current_audit_log", lambda: log)
    events = []
    state = PolicyImpactState()
    result = _run(_runner(RecordingModel(events)), state, RecordingResolver(events))
    assert result.stop_reason == "persistence_error"
    assert events == [] and state.delegation_evidence == []


def test_required_reads_share_the_subagent_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(subagents, "time", SimpleNamespace(monotonic=lambda: now[0]))
    events = []
    resolver = RecordingResolver(events)
    original_read = resolver.read

    def slow_read(ref):
        now[0] = _runner(RecordingModel([])).role_registry.get("skeptic").timeout_seconds + 1
        return original_read(ref)

    resolver.read = slow_read
    state = PolicyImpactState()
    result = _run(_runner(RecordingModel(events)), state, resolver)
    assert result.stop_reason == "timeout"
    assert events == [f"read:{REF}"]
    assert state.model_calls == [] and state.delegation_evidence == []


def test_deadline_expiring_during_context_persistence_does_not_start_model(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(subagents, "time", SimpleNamespace(monotonic=lambda: now[0]))
    events = []
    state = PolicyImpactState()
    original_add = state.add_context_manifest

    def add_manifest(manifest):
        original_add(manifest)
        now[0] = _runner(RecordingModel([])).role_registry.get("skeptic").timeout_seconds + 1

    monkeypatch.setattr(state, "add_context_manifest", add_manifest)
    result = _run(_runner(RecordingModel(events)), state, RecordingResolver(events))
    assert result.stop_reason == "timeout"
    assert events == [f"read:{REF}"]
    assert state.model_calls == [] and state.delegation_evidence == []


def test_required_reads_reserve_a_step_without_increasing_model_budget():
    events = []
    model = RecordingModel(events, [{
        "type": "tool_call", "tool_name": "artifact_read", "arguments": {"artifact_ref": REF},
    }])
    definition = _runner(model).role_registry.get("skeptic").model_copy(
        update={"max_model_calls": 3, "max_steps": 8}
    )
    runner = _runner(model, role_registry=SubagentRoleRegistry([definition]))
    state = PolicyImpactState()
    result = _run(runner, state, RecordingResolver(events))
    assert result.stop_reason == "max_model_calls"
    assert len(model.messages) == definition.max_model_calls
    assert state.delegation_evidence == []

    events.clear()
    runner = _runner(RecordingModel(events), role_registry=SubagentRoleRegistry([
        definition.model_copy(update={"max_steps": 1}),
    ]))
    assert _run(runner, PolicyImpactState(), RecordingResolver(events)).stop_reason == "max_steps"
    assert events == []


def test_preload_does_not_grant_skeptic_write_capability():
    events = []
    model = RecordingModel(events, [{
        "type": "tool_call", "tool_name": "report_write", "arguments": {"content": "unsafe"},
    }])
    state = PolicyImpactState()
    result = _run(_runner(model, tools=("artifact_read", "report_write")),
                  state, RecordingResolver(events))
    assert result.stop_reason == "tool_denied"
    assert state.delegation_evidence == []
    assert [call["tool_name"] for call in get_tool_audit_log()] == ["artifact_read"]
