from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.skills.registry import SkillRegistry


def test_role_schema_failure_is_repaired_inside_same_bounded_loop():
    bad = {"counterexamples": [], "notes": ["No proof supplied"], "evidence_refs": ["task-context"]}
    good = {"counterexamples": [], "notes": ["No proof supplied"]}
    adapter = ScriptedModelAdapter([
        {"type": "final", "output": bad},
        {"type": "final", "output": good},
    ])
    state = PolicyImpactState(company_id="test", run_id="repair-test")
    runner = SubagentRunner(model_adapter=adapter, skill_manifest=SkillRegistry().get_manifest("general_chat"))

    result = runner.run(state, "skeptic", task="Check evidence", input_artifact_refs=("task-context",),
                        artifact_payloads={"task-context": {"facts": []}})

    assert result.stop_reason == "success"
    assert "evidence_refs" not in result.output
    assert adapter._index == 2
    calls = state.model_calls
    assert "validation_errors" in calls[0]["payload"]
    assert calls[1]["messages"][0]["content"]["output_json_schema"]["additionalProperties"] is False


def test_role_schema_repair_is_not_unbounded():
    invalid = {"type": "final", "output": {"counterexamples": [], "notes": [], "extra": "invalid"}}
    adapter = ScriptedModelAdapter([invalid, invalid, invalid])
    state = PolicyImpactState(company_id="test", run_id="repair-budget-test")
    runner = SubagentRunner(model_adapter=adapter, skill_manifest=SkillRegistry().get_manifest("general_chat"))
    result = runner.run(state, "skeptic", task="Check evidence")
    assert result.stop_reason == "invalid_model_action"
    assert not result.output
    assert adapter._index == 2
