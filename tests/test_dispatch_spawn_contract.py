"""Model-facing Profile dispatch stays compact while Runtime Spawn remains explicit."""

import pytest
from pydantic import ValidationError

from policy_impact.dispatch.contracts import DispatchError, Spawn
from policy_impact.dispatch.finance_rules import FinanceRules, load_rules
from policy_impact.dispatch.finance_tool_contracts import describe, normalize_agent_spawn
from tests.test_dispatch_finance_rules import _refresh

FIXED_BUDGET = {"budget": {"policy": "fixed"}}
REQUESTED_BUDGET = {"budget": {"policy": "requested"}}


def _normalize(arguments, *, policy=FIXED_BUDGET, rules=None):
    return normalize_agent_spawn(
        arguments,
        dispatch_key="finance-action-generated-by-runtime",
        task_budget=policy,
        rules=rules or load_rules(),
    )


def test_general_minimal_spawn_maps_to_runtime_defaults():
    arguments = {
        "task_name": "论文盲审",
        "message": "Review the method and identify material limitations.",
        "input_refs": ["authorized-paper-ref"],
    }

    normalized = _normalize(arguments)
    request = Spawn.model_validate(normalized, strict=True)

    assert request.dispatch_key == "finance-action-generated-by-runtime"
    assert request.role == "general"
    assert request.role_version == "1.0.0"
    assert request.objective == arguments["message"]
    assert request.input_refs == arguments["input_refs"]
    assert request.success_criteria == [
        "Complete the bounded task and disclose material evidence gaps or limitations."
    ]
    assert request.output_contract.model_dump() == {"name": "finance.task_result", "version": "1"}
    assert request.task_instructions == "Task label: 论文盲审"


def test_analyst_keeps_professional_acceptance_output_and_review_fields():
    arguments = {
        "task_name": "分析文章观点",
        "message": "Reconstruct the author's argument and its conditions.",
        "profile": "analyst",
        "task_instructions": "Separate author claims from inference.",
        "success_criteria": ["Bind each main claim to the assigned source."],
        "input_refs": ["authorized-article-ref"],
        "profile_options": {
            "review_mode": "required",
            "review_options": {
                "scope": ["source_fidelity", "reasoning_conditions"],
                "max_rounds": 4,
            },
        },
    }

    request = Spawn.model_validate(_normalize(arguments), strict=True)

    assert request.role == "analyst"
    assert request.role_version == "2.0.0"
    assert request.success_criteria == arguments["success_criteria"]
    assert request.output_contract.model_dump() == {"name": "finance.analyst_result", "version": "2"}
    assert request.review_mode == "required"
    assert request.review_options.max_rounds == 4
    assert request.review_options.scope == ["source_fidelity", "reasoning_conditions"]
    assert request.task_instructions.startswith("Task label: 分析文章观点\n\n")


@pytest.mark.parametrize("arguments", [
    {
        "task_name": "specialist-without-criteria",
        "message": "Analyze the assigned article.",
        "profile": "analyst",
        "input_refs": ["article-ref"],
    },
    {
        "task_name": "wrong-profile-options",
        "message": "Check one claim.",
        "profile": "verifier",
        "success_criteria": ["Check the source."],
        "profile_options": {"review_mode": "required"},
    },
    {
        "task_name": "unregistered-contract",
        "message": "Review this paper.",
        "profile": "general",
        "output_contract": {"name": "finance.review_report", "version": "1"},
    },
    {
        "task_name": "runtime-fields-are-not-model-fields",
        "message": "Review this paper.",
        "dispatch_key": "model-invented-key",
    },
])
def test_spawn_rejects_missing_specialist_fields_wrong_profile_options_and_runtime_authority(arguments):
    with pytest.raises((DispatchError, ValidationError)):
        _normalize(arguments)


def test_spawn_description_exposes_compact_fields_and_profile_specific_options():
    rules = load_rules()
    description = describe("agent.spawn", FIXED_BUDGET, rules=rules)
    schema = description["parameters"]

    assert set(schema["required"]) == {"task_name", "message"}
    assert {"dispatch_key", "role", "role_version"}.isdisjoint(schema["properties"])
    assert "requested_budget" not in schema["properties"]
    assert "profile_options" in schema["properties"]
    assert {item["profile"] for item in description["profiles"]} == {
        "general", "analyst", "verifier", "skeptic",
    }
    assert all("versions" not in profile and "available_tools" not in profile
               for profile in description["profiles"])
    assert "omitted review_options.max_rounds uses five" in description["description"]
    assert "only when the user explicitly requests it" in description["description"]


def test_requested_budget_remains_available_only_for_historical_policy():
    fixed = describe("agent.spawn", FIXED_BUDGET, rules=load_rules())["parameters"]
    requested = describe("agent.spawn", REQUESTED_BUDGET, rules=load_rules())["parameters"]
    assert "requested_budget" not in fixed["properties"]
    assert "requested_budget" in requested["properties"]

    with pytest.raises(DispatchError, match="required for this historical Run"):
        _normalize({"task_name": "old", "message": "Do the bounded work."}, policy=REQUESTED_BUDGET)


def test_v3_rule_snapshot_keeps_the_old_model_spawn_contract():
    snapshot = load_rules().snapshot()
    path = "config/agents/finance.json"
    manifest = __import__("json").loads(snapshot["assets"][path]["content"])
    manifest["schema_version"] = "finance.h2.agent_rules.v3"
    snapshot["assets"][path]["content"] = __import__("json").dumps(manifest)
    old_rules = FinanceRules.from_snapshot(_refresh(snapshot))

    assert old_rules.portable_profiles
    assert not old_rules.compact_spawn
    schema = describe("agent.spawn", FIXED_BUDGET, rules=old_rules)["parameters"]
    assert "dispatch_key" in schema["properties"]
    assert "role_version" in schema["properties"]
    assert "requested_budget" not in schema["properties"]

    historical_schema = describe("agent.spawn", REQUESTED_BUDGET, rules=old_rules)["parameters"]
    assert "requested_budget" in historical_schema["properties"]

