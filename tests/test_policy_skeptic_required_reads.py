"""Policy skeptic regression coverage, including the archived final H09 failure."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import get_tool_audit_log, reset_tool_audit_log
from policy_impact.runtime.gate_catalog import StateGateDecisionSink, build_default_gate_registry
from policy_impact.runtime.gates import GateRunner
from policy_impact.runtime.task_artifacts import InMemoryTaskArtifactResolver
from policy_impact.stages import generate_weekly_report as report_stage
from policy_impact.stages import review_evidence_and_risk as stage


H09 = Path(__file__).resolve().parents[1] / "data/acceptance/persona_20260909_final/cases/H09"
EMPTY_FINAL = {"type": "final", "output": {"counterexamples": [], "notes": []}}


@pytest.fixture(autouse=True)
def no_artifact_writes(monkeypatch):
    reset_tool_audit_log()
    monkeypatch.setattr(stage, "write_json_artifact", Mock())
    yield
    reset_tool_audit_log()


def _state():
    state = PolicyImpactState(run_id="policy-skeptic-test", company_id="offline")
    state.date_range = {"date_from": "2026-09-02", "date_to": "2026-09-09"}
    state.current_user_message = "Review the collected policy evidence."
    state.impact_assessments = [{
        "assessment_id": "impact_1", "policy_id": "policy_1", "impact_level": "P2",
        "applicability": "conditional", "binding_effect": "mandatory",
        "claim": "The trigger condition still needs confirmation.",
        "reasoning": ["The trigger condition still needs confirmation."],
        "policy_evidence": {"clause_id": "scope_1", "text": "Only triggered services apply."},
        "company_evidence": [{"fact_id": "goal", "value": "Read-only prototype."}],
    }]
    return state


def _gate(state):
    sink = StateGateDecisionSink(state)
    return GateEngine(
        runner=GateRunner(build_default_gate_registry(proof_resolver=sink), sink),
        run_id=state.run_id,
    )


def test_policy_direct_empty_final_requires_real_read_and_first_call_context(monkeypatch):
    state = _state()
    before = deepcopy(state.impact_assessments)
    events = []
    original_read = InMemoryTaskArtifactResolver.read

    def read(resolver, ref):
        events.append("read")
        return original_read(resolver, ref)

    monkeypatch.setattr(InMemoryTaskArtifactResolver, "read", read)

    class InspectingModel:
        def complete(self, messages, schema_name):
            assert events == ["read"]
            events.append("model")
            observation = messages[0]["content"]["visible_context"]["artifact_observations"][0]
            assert observation["artifact_ref"] == f"review_context:{state.run_id}"
            assert observation["tool_result"]["assessments"] == state.review_context
            assert observation["tool_result"]["assessments"][0]["claim"] == before[0]["claim"]
            assert get_tool_audit_log()[0]["status"] == "ok"
            return ModelResponse(response_id="empty-after-read", payload=EMPTY_FINAL)

    stage.run(state, model_adapter=InspectingModel(), gate_engine=_gate(state))

    assert events == ["read", "model"]
    assert state.impact_assessments == before
    assert state.review_result["passed"] is True
    assert state.review_result["completion_status"] == "completed"
    assert state.review_result["skeptic_review"]["completed"] is True
    assert state.review_result["skeptic_review"]["artifact_read_audit_refs"] == [
        state.tool_calls[0]["call_id"],
    ]
    assert state.review_bundles[-1]["role"] == "skeptic"
    assert state.review_bundles[-1]["stop_reason"] == "success"
    assert len(state.model_calls) == len(state.delegation_evidence) == 1


@pytest.mark.parametrize("error,stop_reason", [
    (PermissionError("missing context"), "tool_denied"),
    (RuntimeError("read failed"), "tool_failed"),
    (TimeoutError("read timeout"), "tool_timeout"),
])
def test_required_read_failure_is_visible_partial_without_model_or_success(
    monkeypatch, error, stop_reason,
):
    state = _state()
    monkeypatch.setattr(InMemoryTaskArtifactResolver, "read", Mock(side_effect=error))
    model = Mock()

    stage.run(state, model_adapter=model, gate_engine=_gate(state))

    model.complete.assert_not_called()
    assert state.model_calls == [] and state.delegation_evidence == []
    assert state.review_result["completion_status"] == "partial"
    status = state.review_result["skeptic_review"]
    assert status == state.quality_metrics["skeptic_review"]
    assert status["completed"] is False
    assert status["stop_reason"] == stop_reason
    assert status["fallback"] == "deterministic_evidence_gate"
    assert status["artifact_read_audit_refs"] == []
    assert [bundle["role"] for bundle in state.review_bundles] == [
        "skeptic", "skeptic_rule_engine",
    ]
    assert all(bundle["stop_reason"] == stop_reason for bundle in state.review_bundles)
    assert state.warnings[-1]["detail"] == stop_reason
    # The existing report consumes the failed bundle; no report/runtime patch is needed.
    scope = report_stage._report_scope(state)
    assert scope["completion_status"] == "partial"
    assert "skeptic_failed" in scope["limitation_codes"]
    assert state.review_result["claim_review_details"][0]["reviewer_role"] == (
        "deterministic_evidence_gate"
    )
    artifact = stage.write_json_artifact.call_args.args[2]
    assert artifact["completion_status"] == "partial"


def test_model_failure_after_read_is_not_successful_review():
    state = _state()
    model = Mock()
    model.complete.side_effect = RuntimeError("model unavailable")
    stage.run(state, model_adapter=model, gate_engine=_gate(state))
    model.complete.assert_called_once()
    assert state.tool_calls[0]["status"] == "ok"
    assert state.review_result["completion_status"] == "partial"
    assert state.review_result["skeptic_review"]["stop_reason"] == "model_error"
    assert state.delegation_evidence == []


def test_no_model_remains_explicitly_partial_and_deterministic_only():
    state = _state()
    stage.run(state, gate_engine=_gate(state))
    assert state.review_result["completion_status"] == "partial"
    assert state.review_result["skeptic_review"]["stop_reason"] == "model_not_configured"
    assert state.tool_calls == state.model_calls == state.delegation_evidence == []
    assert state.review_bundles[-1]["role"] == "skeptic_rule_engine"


def test_counterexample_after_preload_still_blocks_the_real_review_gate():
    state = _state()
    model = ScriptedModelAdapter([{"type": "final", "output": {
        "counterexamples": [{
            "claim_ref": "impact_1", "issue": "Obligation subject is unconfirmed.",
            "evidence_refs": [f"review_context:{state.run_id}"],
        }], "notes": [],
    }}])
    with pytest.raises(stage.ReviewGateBlocked):
        stage.run(state, model_adapter=model, gate_engine=_gate(state))
    assert state.review_result["passed"] is False
    assert state.review_result["claim_review_details"][0]["unresolved_skeptic_count"] == 1
    assert state.review_result["claim_review_details"][0]["decision"] != "pass"
    assert state.tool_calls[0]["status"] == "ok"
    assert len(state.model_calls) == 1


@pytest.mark.skipif(not (H09 / "run_artifact.json").exists(), reason="Local H09 archive absent")
@pytest.mark.parametrize("read_fails", [False, True])
def test_h09_archived_final_without_tool_action_requires_audited_context(monkeypatch, read_fails):
    archive = json.loads((H09 / "run_artifact.json").read_text(encoding="utf-8"))
    old_calls = [call for call in archive["model_calls"] if call["stage_name"] == "subagent.skeptic"]
    assert len(old_calls) == 1
    old_final = old_calls[0]["payload"]
    assert old_final["type"] == "final" and old_final["output"]["counterexamples"] == []
    assert [message["role"] for message in old_calls[0]["messages"]] == ["system", "user"]
    assert "artifact_observations" not in old_calls[0]["messages"][0]["content"]["visible_context"]
    assert not [call for call in archive["tool_calls"] if call["tool_name"] == "artifact_read"]

    state = _state()
    state.impact_assessments = deepcopy(archive["impact_assessments"])
    if read_fails:
        monkeypatch.setattr(InMemoryTaskArtifactResolver, "read", Mock(side_effect=PermissionError))
    # Replay the exact final-only response. No model-generated tool action is added.
    stage.run(state, model_adapter=ScriptedModelAdapter([old_final]), gate_engine=_gate(state))

    if read_fails:
        assert state.model_calls == [] and state.delegation_evidence == []
        assert state.tool_calls[0]["status"] == "blocked"
        assert state.review_result["completion_status"] == "partial"
        assert all(bundle["stop_reason"] != "success" for bundle in state.review_bundles)
        return

    assert len(state.model_calls) == len(state.tool_calls) == 1
    call = state.model_calls[0]
    assert call["payload"] == old_final
    observation = call["messages"][0]["content"]["visible_context"]["artifact_observations"][0]
    assert observation["tool_result"]["assessments"] == state.review_context
    assert {item["assessment_id"] for item in observation["tool_result"]["assessments"]} == {
        item["assessment_id"] for item in archive["impact_assessments"]
    }
    assert state.tool_calls[0]["status"] == "ok"
    assert observation["call_id"] == state.tool_calls[0]["call_id"]
    assert state.tool_calls[0]["finished_at"] <= call["created_at"]
    assert state.review_result["skeptic_review"]["artifact_read_audit_refs"] == [
        observation["call_id"],
    ]
