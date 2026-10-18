"""Discovery envelopes are normalized structurally, never semantically repaired."""

from copy import deepcopy
import json

import pytest

from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter, ModelInvocationError


PLAN = {
    "search_terms": ["financial data", "investment research"],
    "topic_rules": [{
        "topic": "financial data", "terms": ["data governance"],
        "required_any": ["financial", "investment"],
    }],
}


def _complete(monkeypatch, payload, schema="report_discovery_plan.v1"):
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID, base_url="https://offline.invalid",
        api_key="offline-test-token", max_attempts=1,
    )
    calls = []

    def request(system, messages):
        calls.append((system, messages))
        return {
            "id": "offline-discovery", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

    monkeypatch.setattr(adapter, "_request", request)
    result = adapter.complete([{"role": "user", "content": "Plan public search terms."}], schema)
    assert len(calls) == 1
    return result


@pytest.mark.parametrize("enveloped", [False, True])
def test_real_adapter_accepts_discovery_shape_without_changing_terms(monkeypatch, enveloped):
    plan = deepcopy(PLAN)
    payload = {"type": "final", "output": plan} if enveloped else plan
    original = deepcopy(payload)

    result = _complete(monkeypatch, payload)

    assert result.payload == {"type": "final", "output": plan}
    assert payload == original
    assert result.response_id == "offline-discovery"
    assert result.metadata["schema_name"] == "report_discovery_plan.v1"
    assert result.metadata["provider"] == "anthropic-compatible"
    assert result.metadata["attempts"] == 1
    assert result.metadata["usage"] == {"input_tokens": 20, "output_tokens": 10}


@pytest.mark.parametrize("field,value", [
    ("search_terms", "financial data"),
    ("search_terms", None),
    ("search_terms", []),
    ("search_terms", [123]),
    ("search_terms", [True]),
    ("search_terms", [" "]),
    ("search_terms", ["term"] * 9),
    ("topic_rules", {}),
    ("topic_rules", []),
    ("topic_rules", ["financial data"]),
    ("topic_rules", PLAN["topic_rules"] * 7),
], ids=["string", "null", "empty", "number", "bool", "blank", "too_many_terms",
        "rules_object", "rules_empty", "rule_string", "too_many_rules"])
@pytest.mark.parametrize("enveloped", [False, True])
def test_discovery_rejects_invalid_container_types(monkeypatch, field, value, enveloped):
    plan = deepcopy(PLAN)
    plan[field] = value
    with pytest.raises(ModelInvocationError):
        _complete(monkeypatch, {"type": "final", "output": plan} if enveloped else plan)


@pytest.mark.parametrize("field,value", [
    ("topic", 123), ("topic", ""), ("topic", "x" * 61),
    ("terms", "data governance"), ("terms", [123]), ("terms", []),
    ("required_any", None), ("required_any", "financial"), ("required_any", [False]),
    ("source_id", "unrequested-source"),
], ids=["topic_number", "topic_empty", "topic_long", "terms_string", "terms_number",
        "terms_empty", "required_null", "required_string", "required_bool", "extra_field"])
def test_discovery_rejects_invalid_rule_fields(monkeypatch, field, value):
    plan = deepcopy(PLAN)
    plan["topic_rules"][0][field] = value
    with pytest.raises(ModelInvocationError):
        _complete(monkeypatch, plan)


def test_discovery_does_not_add_optional_fields_or_repair_semantics(monkeypatch):
    plan = deepcopy(PLAN)
    del plan["topic_rules"][0]["required_any"]
    plan["search_terms"] = ["https://not-a-public-search-term.invalid"]
    result = _complete(monkeypatch, plan)
    # Privacy and relevance checks remain with the discovery planner.
    assert result.payload["output"] == plan


@pytest.mark.parametrize("payload", [
    {"search_terms": PLAN["search_terms"]},
    {**PLAN, "unexpected": True},
    {"type": "delegate", "output": PLAN},
    {"type": "final", "output": {**PLAN, "unexpected": True}},
])
def test_discovery_does_not_repair_missing_or_unexpected_fields(monkeypatch, payload):
    with pytest.raises(ModelInvocationError):
        _complete(monkeypatch, payload)


def test_discovery_normalization_is_schema_specific(monkeypatch):
    with pytest.raises(ModelInvocationError):
        _complete(monkeypatch, PLAN, schema="profile_aware_news_report.v1")
