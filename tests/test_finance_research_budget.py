"""Fault injection for bounded financial model requests; no network access."""

from dataclasses import replace
from io import BytesIO
import json
from types import SimpleNamespace

import pytest

from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter, ModelInvocationError
from policy_impact.harness.research_budget import (
    CURRENT, ResearchLimits, check_input, consume_request, execution_scope, read_response_bytes,
)


def test_default_capacity_reserves_output_and_uses_70_60_pressure():
    limits = ResearchLimits()
    assert limits.input_tokens == limits.context_tokens == 1_000_000
    assert limits.input_limit() == 979520
    assert limits.input_limit(32768) == 963136
    assert limits.context_pressure_limits() == (700000, 600000)
    described = limits.describe()
    assert described["context_pressure_tokens"] == 700000
    assert described["context_target_tokens"] == 600000
    assert described["effective_input_tokens"] == 963136
    assert described["capacity_source"] == "configuration"
    assert described["capacity_verified"] is False


@pytest.mark.parametrize(("input_cap", "context_cap", "expected_input", "expected_pressure"), [
    (48000, 0, 43904, (33600, 28800)),
    (1_000_000, 128000, 107520, (89600, 76800)),
    (40000, 128000, 35904, (28000, 24000)),
    (2_000_000, 2_000_000, 979520, (700000, 600000)),
    (2_000_000, 0, 979520, (700000, 600000)),
])
def test_capacity_honors_smaller_limits_unknown_window_and_hard_ceiling(
    input_cap, context_cap, expected_input, expected_pressure,
):
    limits = replace(ResearchLimits(), input_tokens=input_cap, context_tokens=context_cap)
    assert limits.input_limit() == expected_input
    assert limits.context_pressure_limits() == expected_pressure
    described = limits.describe()
    assert described["input_tokens"] == input_cap
    assert described["context_tokens"] == context_cap
    assert described["effective_context_ceiling_tokens"] <= 1_000_000
    assert described["capacity_status"] == ("configured" if context_cap else "provisional")


def test_small_window_preserves_hysteresis_after_output_reserve():
    limits = replace(ResearchLimits(), context_tokens=48000)
    trigger, target = limits.context_pressure_limits(limits.output_tokens)
    assert trigger == limits.input_limit(limits.output_tokens) == 11136
    assert target == trigger * 60 // 70
    assert 0 < target < trigger


@pytest.mark.parametrize(("pressure", "target"), [(0, 0), (70, 70), (60, 70), (101, 60), (70, -1)])
def test_invalid_context_percentages_are_explicit(pressure, target):
    with pytest.raises(ValueError, match="context percentages"):
        replace(ResearchLimits(), context_pressure_percent=pressure, context_target_percent=target)


def test_context_percentages_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("FINANCE_RESEARCH_INPUT_TOKENS", "1000000")
    monkeypatch.setenv("FINANCE_RESEARCH_CONTEXT_TOKENS", "1000000")
    monkeypatch.setenv("FINANCE_RESEARCH_CONTEXT_PRESSURE_PERCENT", "65")
    monkeypatch.setenv("FINANCE_RESEARCH_CONTEXT_TARGET_PERCENT", "55")
    assert ResearchLimits.from_env().context_pressure_limits() == (650000, 550000)


def test_full_input_gate_checks_main_and_child_without_network():
    limits = replace(ResearchLimits(), input_tokens=6000, safety_tokens=100)
    messages = [{"role": "user", "content": "x" * 28000}]
    with execution_scope(limits=limits, stage="finance.main") as parent:
        for child in (False, True):
            with execution_scope(stage="analyst@2.0.0" if child else "finance.main") as scope:
                assert scope.root is parent.root
                with pytest.raises(ValueError, match="context_budget_exceeded"):
                    check_input(messages)
                assert scope.root.requests == 0


def body():
    return {"schema_version": "finance.research_result.v1", "content": "Detailed public explanation. " * 100,
            "evidence_links": [], "material_disposition": [], "gaps": [], "limitations": []}


def wire(payload=None, *, stop="end_turn"):
    return {"id": "fixture-response", "content": [{"type": "text", "text": json.dumps(
        {"type": "final", "output": payload if payload is not None else body()})}], "stop_reason": stop}


class FaultModel(AnthropicCompatibleModelAdapter):
    def __init__(self, outcomes):
        super().__init__(model_id="fixture", base_url="https://invalid.test", api_key="test-secret",
                         max_tokens=16384, max_attempts=99, timeout_seconds=90)
        self.outcomes = list(outcomes)
        self.calls = []

    def _request(self, system, messages):
        self.calls.append({"system": system, "messages": messages, "timeout": self.timeout_seconds,
                           "max_tokens": self.max_tokens})
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_transient_retry_and_schema_feedback_share_one_decision_budget(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _seconds: None)
    missing = body()
    missing.pop("limitations")
    model = FaultModel([ModelInvocationError("HTTP 503: test-secret"), wire(missing), wire()])
    with execution_scope(stage="finance.main") as scope:
        response = model.complete([{"role": "user", "content": "Use the supplied source"}],
                                  "finance.research_result.v1")
        assert scope.root.requests == 3
        assert response.metadata["attempts"] == 3
        assert len(response.payload["output"]["content"]) > 900
        assert "test-secret" not in str(response.metadata)
        assert response.metadata["actual_model_inputs"][-1]["messages"][-1]["content"]["contract_error"]


def test_five_physical_attempts_total_not_per_layer(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _seconds: None)
    missing = body()
    missing.pop("limitations")
    model = FaultModel([ModelInvocationError("HTTP 503"), ModelInvocationError("HTTP 429"),
                        wire(missing), wire(missing), wire()])
    with execution_scope(stage="finance.main") as scope:
        response = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
        assert scope.root.requests == 5
        assert len(model.calls) == 5
        assert response.payload["output"]["content"] == body()["content"]
        assert response.metadata["actual_model_inputs"][0]["input_kind"].startswith("completed_generation")
        assert "output_json_schema" in model.calls[-1]["messages"][0]["content"]


def test_structure_repair_cannot_change_valid_explanation():
    missing = body()
    missing.pop("limitations")
    changed = {**body(), "content": "Different conclusion"}
    model = FaultModel([wire(missing), wire(missing), wire(changed)])
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert failed.value.metadata["attempts"] == 3
    assert len(model.calls) == 3


def test_truncation_changes_output_limit_without_shortening_instruction():
    model = FaultModel([wire(stop="max_tokens"), wire()])
    with execution_scope(stage="finance.main"):
        result = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert len(model.calls) == 2
    assert model.calls[1]["max_tokens"] == 32768
    assert model.calls[0]["messages"] == model.calls[1]["messages"]
    assert result.payload["output"]["content"] == body()["content"]


def test_unrecoverable_auth_does_not_retry_or_leak_secret():
    model = FaultModel([ModelInvocationError("HTTP 401 test-secret")])
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert len(model.calls) == 1
    assert "test-secret" not in str(failed.value.metadata)
    assert failed.value.metadata["failure_kind"] == "transport"


def test_complete_invalid_responses_are_retained_without_becoming_actions():
    text = '{"type":"tool_call","tool_name":"unused","arguments":{"key":"test-secret"}} {"extra":true}'
    invalid = {"id": "invalid-response", "stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "private protocol reasoning"},
        {"type": "text", "text": text},
    ]}
    model = FaultModel([invalid, invalid, invalid])
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
        assert scope.root.requests == 3
    records = failed.value.metadata["failed_public_responses"]
    assert [row["attempt"] for row in records] == [1, 2, 3]
    assert records[0]["response_id"] == "invalid-response"
    assert records[0]["text"] == text.replace("test-secret", "[REDACTED]")
    assert records[0]["response_chars"] == len(text)
    assert records[0]["truncated"] is False
    assert "test-secret" not in str(failed.value.metadata)
    assert "private protocol reasoning" not in str(failed.value.metadata)


def test_successful_schema_recovery_keeps_previous_public_diagnostic():
    invalid = wire()
    invalid["content"][0]["text"] += ' {"extra":true}'
    model = FaultModel([invalid, wire()])
    with execution_scope(stage="finance.main"):
        response = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert response.payload["output"]["content"] == body()["content"]
    assert response.metadata["attempts"] == 2
    assert len(response.metadata["failed_public_responses"]) == 1
    assert response.metadata["failed_public_responses"][0]["text"].endswith(' {"extra":true}')


def test_public_diagnostic_size_limit_is_explicit():
    text = "not-json " + "x" * 70000 + " sk-notarealcredential1234567890"
    invalid = {"id": "large-invalid", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    model = FaultModel([invalid, invalid, invalid])
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    for record in failed.value.metadata["failed_public_responses"]:
        assert record["response_chars"] == len(text)
        assert record["retained_chars"] == len(record["text"]) == 65536
        assert record["truncated"] is True


def test_other_credential_shapes_are_redacted_from_public_diagnostic():
    text = "not-json sk-notarealcredential1234567890"
    invalid = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    model = FaultModel([invalid, invalid, invalid])
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert all(row["text"] == "not-json [REDACTED]"
               for row in failed.value.metadata["failed_public_responses"])


@pytest.mark.parametrize("failure", [
    ModelInvocationError("HTTP 401", metadata={"transport_metadata": {"stream_complete": False}}),
    wire(stop="max_tokens"),
])
def test_no_public_diagnostic_from_disconnected_or_truncated_response(failure):
    model = FaultModel([failure])
    with execution_scope(limits=replace(ResearchLimits(), output_tokens=16384), stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert failed.value.metadata["failed_public_responses"] == []
    assert failed.value.metadata["failure_kind"] == ("transport" if isinstance(failure, Exception) else "truncated")


def test_input_capacity_rejects_before_any_request():
    model = FaultModel([wire()])
    with execution_scope(limits=replace(ResearchLimits(), input_tokens=4100), stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError):
            model.complete([{"role": "user", "content": "source" * 1000}], "finance.research_result.v1")
        assert scope.root.requests == 0
    assert model.calls == []


def test_child_deadline_cannot_spend_parent_finalization_reserve(monkeypatch):
    import policy_impact.harness.research_budget as budget
    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    with execution_scope(timeout_seconds=3600, reserve_seconds=600, stage="finance.main") as parent:
        clock[0] = 2700
        with execution_scope(timeout_seconds=1200, reserve_seconds=120, stage="analyst@2.0.0") as child:
            assert child.deadline == 3100
            assert child.remaining() == 400
            assert child.root is parent.root
        clock[0] = 3150
        with pytest.raises(TimeoutError):
            with execution_scope(timeout_seconds=1200):
                pytest.fail("must not start a child inside the parent reserve")
    assert CURRENT.get() is None


def test_request_reserve_is_shared_and_released_only_to_main():
    limits = replace(ResearchLimits(), root_requests=5, root_final_requests=2)
    with execution_scope(limits=limits, stage="finance.main") as parent:
        with execution_scope(timeout_seconds=60, stage="analyst@2.0.0"):
            for attempt in range(3):
                consume_request(attempt=attempt, schema_name="fixture")
            with pytest.raises(RuntimeError, match="reserve"):
                consume_request(attempt=4, schema_name="fixture")
        assert not parent.acquiring()
        consume_request(attempt=1, schema_name="final")
        consume_request(attempt=2, schema_name="final")
        with pytest.raises(RuntimeError):
            consume_request(attempt=3, schema_name="final")
        assert parent.root.requests == 5


def test_socket_read_timeout_shrinks_with_remaining_run_budget(monkeypatch):
    import policy_impact.harness.research_budget as budget
    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    timeouts = []

    class Response(BytesIO):
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))

        def read1(self, _size):
            clock[0] += 3
            return super().read(1)

    with execution_scope(timeout_seconds=10, stage="finance.main"):
        assert read_response_bytes(Response(b"ab"), 90) == b"ab"
    assert timeouts == [10, 7, 4]


def test_invalid_environment_budget_is_explicit(monkeypatch):
    monkeypatch.setenv("FINANCE_RESEARCH_ROOT_REQUESTS", "10")
    with pytest.raises(ValueError, match="root_final_requests"):
        ResearchLimits.from_env()


def test_structured_pass_cannot_multiply_adapter_retries(monkeypatch):
    from policy_impact.domains.finance.structured_pass import complete_structured
    from policy_impact.runtime.research_contracts import ResearchResult

    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _seconds: None)
    model = FaultModel([ModelInvocationError("HTTP 503"), ModelInvocationError("HTTP 503"), wire(),
                        ModelInvocationError("HTTP 503"), wire(), wire()])

    def complete(messages, schema_name):
        return model.complete(messages, schema_name).payload["output"]

    def unresolved(_batch):
        raise ValueError("fixture semantic issue remains unresolved")

    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError):
            complete_structured(complete, [{"role": "user", "content": "source"}],
                                 "finance.research_result.v1", ResearchResult, validate=unresolved)
        assert scope.root.requests == 5
        assert len(model.calls) == 5


def test_retry_after_is_respected_and_recorded(monkeypatch):
    delays = []
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", delays.append)
    model = FaultModel([ModelInvocationError("HTTP 429", metadata={"retry_after_seconds": 4}), wire()])
    with execution_scope(stage="finance.main"):
        result = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert delays == [4]
    assert result.metadata["retry_errors"][0]["retry_delay_seconds"] == 4
    assert [row["status"] for row in result.metadata["transport_attempts"]] == ["failed", "received"]


def test_retry_after_cannot_extend_deadline(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _: pytest.fail("no budget for retry"))
    model = FaultModel([ModelInvocationError("HTTP 429", metadata={"retry_after_seconds": 100}), wire()])
    with execution_scope(timeout_seconds=30, stage="finance.main"):
        with pytest.raises(ModelInvocationError):
            model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
    assert len(model.calls) == 1


def test_retry_after_supports_http_date_and_rejects_invalid_values(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.time", lambda: 0)
    parse = AnthropicCompatibleModelAdapter._retry_after
    assert parse({"Retry-After": "Thu, 01 Jan 1970 00:00:07 GMT"}) == 7
    assert parse({"Retry-After": "3"}) == 3
    assert parse({"Retry-After": "not-a-date"}) is None
    assert parse({"Retry-After": "inf"}) is None


def test_logical_request_cap_cannot_be_configured_above_contract(monkeypatch):
    monkeypatch.setenv("FINANCE_RESEARCH_DECISION_REQUESTS", "6")
    with pytest.raises(ValueError, match="must not exceed 5"):
        ResearchLimits.from_env()


def test_source_reads_cannot_spend_parent_finalization_reserve(monkeypatch):
    from policy_impact.source_connectors import http_client
    import policy_impact.harness.research_budget as budget

    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_client, "_validate_destination", lambda *_: None)
    timeouts = []

    class Response(BytesIO):
        status = 200
        headers = {"content-type": "text/plain"}
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))

        def geturl(self):
            return "https://public.example/source"

        def read1(self, _size):
            clock[0] += 4
            return super().read(1)

    monkeypatch.setattr(http_client, "build_opener", lambda *_: SimpleNamespace(open=lambda *_, **__: Response(b"abc")))
    with execution_scope(timeout_seconds=10, reserve_seconds=3, stage="finance.main"):
        with pytest.raises(TimeoutError, match="reserve"):
            http_client.fetch_url("https://public.example/source", allowed_domains=["public.example"], retries=0)
    assert timeouts == [7, 3]


def test_source_boundary_is_checked_before_dns_or_retries(monkeypatch):
    from policy_impact.source_connectors import http_client
    import policy_impact.harness.research_budget as budget

    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_client, "_validate_destination", lambda *_: pytest.fail("no DNS after boundary"))
    with execution_scope(timeout_seconds=10, reserve_seconds=3, stage="finance.main"):
        clock[0] = 107
        with pytest.raises(TimeoutError, match="reserve"):
            http_client.fetch_url("https://public.example/source", allowed_domains=["public.example"])


def test_collection_workers_inherit_parent_budget(monkeypatch):
    from policy_impact.domains.finance import acquisition

    observed = []

    def collect(_symbol, _config, _period, _cutoff, budget, _mode):
        observed.append((CURRENT.get(), budget.deadline))
        return None, [], []

    monkeypatch.setattr(acquisition, "_collect_instrument", collect)
    with execution_scope(timeout_seconds=60, reserve_seconds=10, stage="finance.main") as parent:
        acquisition.collect_finance_snapshot(start="2026-01-01", end="2026-01-02",
                                             symbols=["sh000001"], include_documents=False)
    assert len(observed) == 1
    assert observed[0][0] is parent
    assert observed[0][1] == parent.deadline - parent.reserve_seconds


def test_dsml_has_two_bounded_corrections_and_is_never_returned_as_an_action():
    invalid = {"id": "bad-format", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": '<invoke name="context.open">{"context_id":"context_fixture"}</invoke>'}]}
    valid = {"type": "tool_call", "tool_name": "context.open", "arguments": {"context_id": "context_fixture"}}
    model = FaultModel([invalid, invalid, {"id": "fixed", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": json.dumps(valid)}]}])
    with execution_scope(stage="finance.main") as scope:
        result = model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
        assert scope.root.requests == 3
    assert result.payload == valid
    assert len(result.metadata["failed_public_responses"]) == 2
    feedback = json.loads(model.calls[-1]["messages"][-1]["content"])
    assert feedback["failed_public_response"].startswith("<invoke")
    assert feedback["action_json_schema"]["discriminator"]["propertyName"] == "type"


def test_json_inside_native_wrapper_is_not_executable():
    text = '<invoke>{"type":"tool_call","tool_name":"market.catalog","arguments":{}}</invoke>'
    invalid = {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}
    model = FaultModel([invalid] * 3)
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
        assert scope.root.requests == 3
    assert failed.value.structured_output is True
    assert failed.value.metadata["failure_kind"] == "format"
    assert failed.value.metadata["preserved_final_output"] == {}


def test_context_rejection_is_not_reported_as_format_or_remote_failure():
    model = FaultModel([])
    with execution_scope(limits=replace(ResearchLimits(), input_tokens=4100), stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source" * 1000}], "general_chat_agent_loop.v1")
    assert failed.value.metadata["failure_kind"] == "context_budget"
    assert failed.value.metadata["attempts"] == 0
    assert model.calls == []


def test_failed_final_shape_preserves_complete_body_but_not_success_claims():
    invalid = {"type": "final", "extra": True, "output": {
        "answer": "Complete public analysis. " * 100, "verification_status": "verified"}}
    response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(invalid)}]}
    model = FaultModel([response] * 3)
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
    assert failed.value.metadata["preserved_final_output"] == {"answer": invalid["output"]["answer"]}


def empty_response():
    return {"id": "empty-public", "stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "hidden analysis must not become public output"}]}


def test_completed_empty_response_gets_protocol_feedback_not_transport_retry():
    model = FaultModel([empty_response(), wire()])
    with execution_scope(stage="finance.main") as scope:
        result = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
        assert scope.root.requests == 2
    assert result.metadata["retry_errors"][0]["kind"] == "empty_public_response"
    assert [row["status"] for row in result.metadata["transport_attempts"]] == ["received", "received"]
    diagnostic = result.metadata["failed_public_responses"][0]
    assert diagnostic["response_id"] == "empty-public"
    assert diagnostic["text"] == ""
    assert diagnostic["response_chars"] == 0
    feedback = json.loads(model.calls[1]["messages"][-1]["content"])
    assert feedback["contract_error"]
    assert "hidden analysis" not in str(result.metadata) + str(model.calls)


def test_repeated_completed_empty_response_stops_after_two_corrections():
    model = FaultModel([empty_response()] * 3)
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
        assert scope.root.requests == 3
    assert failed.value.metadata["attempts"] == 3
    assert failed.value.metadata["failure_kind"] == "empty_public_response"
    assert failed.value.structured_output is True
    assert failed.value.metadata["preserved_final_output"] == {}
    assert len(failed.value.metadata["failed_public_responses"]) == 3
    assert "hidden analysis" not in str(failed.value.metadata)


def test_empty_format_and_network_recovery_share_five_request_cap(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _: None)
    invalid = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "<invoke/>"}]}
    model = FaultModel([ModelInvocationError("HTTP 503"), empty_response(), invalid,
                        ModelInvocationError("HTTP 429"), wire()])
    with execution_scope(stage="finance.main") as scope:
        result = model.complete([{"role": "user", "content": "source"}], "finance.research_result.v1")
        assert scope.root.requests == result.metadata["attempts"] == 5
    assert [row["kind"] for row in result.metadata["retry_errors"]] == [
        "transport", "empty_public_response", "format", "transport"]


def test_actual_request_exhaustion_is_still_classified_as_budget():
    model = FaultModel([])
    with execution_scope(limits=replace(ResearchLimits(), root_requests=2, root_final_requests=1),
                         stage="finance.main"):
        consume_request(attempt=1, schema_name="fixture")
        consume_request(attempt=2, schema_name="fixture")
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
    assert failed.value.metadata["failure_kind"] == "request_budget"
    assert failed.value.metadata["attempts"] == 0
    assert not model.calls


def test_actual_deadline_exhaustion_is_classified_as_time_budget(monkeypatch):
    import policy_impact.harness.research_budget as budget
    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    model = FaultModel([])
    with execution_scope(timeout_seconds=10, stage="finance.main"):
        clock[0] = 111
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
    assert failed.value.metadata["failure_kind"] == "time_budget"
    assert failed.value.metadata["attempts"] == 0
    assert not model.calls


def test_unexpected_runtime_error_is_not_called_request_exhaustion():
    model = FaultModel([RuntimeError("unexpected adapter failure")])
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            model.complete([{"role": "user", "content": "source"}], "general_chat_agent_loop.v1")
    assert failed.value.metadata["failure_kind"] == "runtime"
    assert len(model.calls) == 1
