from __future__ import annotations

import http.client
import json
from io import BytesIO

import pytest

from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelInvocationError,
)
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter


class _FakeHttpResponse(BytesIO):
    def __init__(self, payload: dict) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))


def test_openai_compatible_adapter_normalizes_chat_completions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeHttpResponse(
            {
                "id": "chatcmpl-test",
                "model": "gpt-6.1-sol",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"type":"general_chat","output":{"answer":"ok"}}'},
                }],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        )

    monkeypatch.setattr(
        "policy_impact.harness.openai_model_gateway.request.urlopen",
        fake_urlopen,
    )
    adapter = OpenAICompatibleModelAdapter(
        model_id="gpt-6.1-sol",
        base_url="https://example.test/v1",
        api_key="test-key",
        timeout_seconds=12,
    )

    response = adapter.complete(
        [
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "question"},
        ],
        "general_chat.v1",
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["timeout"] == 12
    payload = captured["payload"]
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["messages"] == [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "question"},
    ]
    assert "thinking" not in payload
    assert response.payload["output"]["answer"] == "ok"
    assert response.metadata["provider"] == "openai-compatible"


def test_openai_transport_remote_disconnect_is_wrapped_and_retried(monkeypatch) -> None:
    calls = 0

    def flaky_urlopen(_req, timeout):
        nonlocal calls
        assert timeout == 45.0
        calls += 1
        if calls == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _FakeHttpResponse(
            {
                "id": "chatcmpl-retry",
                "model": "gpt-6.1-sol",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"type":"general_chat","output":{"answer":"recovered"}}'},
                }],
            }
        )

    monkeypatch.setattr(
        "policy_impact.harness.openai_model_gateway.request.urlopen",
        flaky_urlopen,
    )
    adapter = OpenAICompatibleModelAdapter(
        model_id="gpt-6.1-sol",
        base_url="https://example.test/v1",
        api_key="test-key",
        max_attempts=2,
    )

    result = adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")

    assert result.payload["output"]["answer"] == "recovered"
    assert calls == 2
    assert result.metadata["attempts"] == 2
    assert "remote end closed" in result.metadata["retry_errors"][0].lower()


def test_transport_retry_preserves_messages_without_json_repair(monkeypatch) -> None:
    requests = []
    adapter = OpenAICompatibleModelAdapter(
        model_id="test-model", base_url="https://example.test/v1",
        api_key="test-key", max_attempts=2,
    )

    def flaky_request(system, messages):
        requests.append((system, [dict(item) for item in messages]))
        if len(requests) == 1:
            raise ModelInvocationError("Remote end closed connection without response")
        return {
            "id": "recovered", "stop_reason": "stop",
            "content": [{"type": "text", "text":
                         '{"type":"final","output":{"answer":"Recovered answer"}}'}],
        }

    monkeypatch.setattr(adapter, "_request", flaky_request)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _delay: None)
    messages = [{"role": "system", "content": "Research contract"},
                {"role": "user", "content": "Read each article carefully"}]

    response = adapter.complete(messages, "general_chat_agent_loop.v1")

    assert response.payload["output"]["answer"] == "Recovered answer"
    assert requests[1] == requests[0]
    assert response.metadata["attempts"] == 2


def test_openai_length_finish_reason_retries_instead_of_recovering_partial_answer(monkeypatch) -> None:
    requests = []

    def fake_urlopen(req, timeout):
        requests.append(json.loads(req.data.decode("utf-8")))
        if len(requests) == 1:
            return _FakeHttpResponse({
                "id": "truncated", "choices": [{"finish_reason": "length", "message": {
                    "content": '{"type":"final","output":{"answer":"Partial answer"}}',
                }}],
            })
        return _FakeHttpResponse({
            "id": "complete", "choices": [{"finish_reason": "stop", "message": {
                "content": '{"type":"final","output":{"answer":"Complete answer"}}',
            }}],
        })

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", fake_urlopen)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _delay: None)
    adapter = OpenAICompatibleModelAdapter(
        model_id="test-model", base_url="https://example.test/v1",
        api_key="test-key", max_attempts=2,
    )

    response = adapter.complete([{"role": "user", "content": "Deep article analysis"}],
                                "general_chat_agent_loop.v1")

    assert response.payload["output"]["answer"] == "Complete answer"
    assert response.metadata["attempts"] == 2
    assert "Partial answer" not in json.dumps(requests[1])
    assert "max_tokens" in response.metadata["retry_errors"][0]


@pytest.mark.parametrize("content", ["", '{"type":"final","output":{"answer":"Partial answer'])
def test_openai_length_exhaustion_never_publishes_partial_answer(monkeypatch, content) -> None:
    monkeypatch.setattr(
        "policy_impact.harness.openai_model_gateway.request.urlopen",
        lambda _req, timeout: _FakeHttpResponse({
            "id": "truncated", "choices": [{"finish_reason": "length", "message": {
                "content": content,
            }}],
        }),
    )
    adapter = OpenAICompatibleModelAdapter(
        model_id="test-model", base_url="https://example.test/v1",
        api_key="test-key", max_attempts=1,
    )

    with pytest.raises(ModelInvocationError, match="max_tokens") as failure:
        adapter.complete([{"role": "user", "content": "Deep article analysis"}],
                         "general_chat_agent_loop.v1")

    assert failure.value.metadata["attempts"] == 1


def test_transport_exhaustion_reports_actual_attempts_and_failure_kind(monkeypatch) -> None:
    attempts = []
    adapter = OpenAICompatibleModelAdapter(
        model_id="test-model", base_url="https://example.test/v1",
        api_key="test-key", max_attempts=2,
    )

    def disconnected(_system, messages):
        attempts.append(messages)
        raise ModelInvocationError("Remote end closed connection without response")

    monkeypatch.setattr(adapter, "_request", disconnected)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _delay: None)

    with pytest.raises(ModelInvocationError) as failure:
        adapter.complete([{"role": "user", "content": "Read source"}],
                         "general_chat_agent_loop.v1")

    assert len(attempts) == failure.value.metadata["attempts"] == 2
    assert failure.value.metadata["last_error_kind"] == "transport"
    assert failure.value.structured_output is False
    assert attempts[0] == attempts[1]


def test_max_token_retry_discards_truncated_response() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=2,
    )
    requests: list[list[dict[str, str]]] = []

    def fake_request(_system: str, messages: list[dict[str, str]]) -> dict:
        requests.append([dict(item) for item in messages])
        if len(requests) == 1:
            return {
                "id": "truncated",
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": "x" * 5000}],
            }
        return {
            "id": "recovered",
            "stop_reason": "end_turn",
            "content": [
                {
                    "type": "text",
                    "text": '{"type":"general_chat","output":{"answer":"已恢复"}}',
                }
            ],
        }

    adapter._request = fake_request  # type: ignore[method-assign]
    result = adapter.complete(
        [{"role": "user", "content": "请回答"}],
        "general_chat.v1",
    )

    assert result.payload["output"]["answer"] == "已恢复"
    assert len(requests) == 2
    assert len(requests[1]) == 1
    assert "完整 JSON 不超过 900" in requests[1][0]["content"]
    assert "x" * 100 not in requests[1][0]["content"]
    assert result.metadata["attempts"] == 2
    assert result.metadata["retry_errors"]


def test_finance_loop_retry_does_not_shrink_answer_to_900_characters() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID, base_url="https://example.invalid",
        api_key="test-key", max_attempts=2,
    )
    requests = []

    def fake_request(_system, messages):
        requests.append(messages)
        if len(requests) == 1:
            return {"id": "cut", "stop_reason": "max_tokens", "content": [
                {"type": "text", "text": '{"type":"final","output":{"answer":"partial'}
            ]}
        answer = "跨时间比较与结论。" * 160
        return {"id": "full", "stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps({"type": "final", "output": {"answer": answer}})}
        ]}

    adapter._request = fake_request  # type: ignore[method-assign]
    result = adapter.complete([{"role": "user", "content": "Compare all articles"}], "general_chat_agent_loop.v1")
    assert len(result.payload["output"]["answer"]) > 900
    assert "不超过 900" not in requests[1][-1]["content"]
    assert "保留用户要求的比较" in requests[1][-1]["content"]


def test_policy_applicability_accepts_top_level_assessments_envelope() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "policy-envelope",
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": '{"assessments":[{"policy_id":"p1","applicability":"conditional"}]}',
            }
        ],
    }

    result = adapter.complete(
        [{"role": "user", "content": "classify"}],
        "policy_applicability.v1",
    )

    assert result.payload["type"] == "final"
    assert result.payload["output"]["assessments"][0]["policy_id"] == "p1"


@pytest.mark.parametrize("schema_name,output", [
    ("finance_answer_audit.v1", {"checks": [], "coverage_complete": True}),
    ("finance_cross_answer_audit.v1", {"checks": [], "coverage_complete": True}),
    ("finance_synthesis_selection.v1", {"claim_ids": ["c1", "c2"]}),
    ("finance_relation_review.v1", {"judgments": []}),
    ("finance_cross_issue_review.v1", {"verdicts": []}),
    ("finance_cross_summary_selection.v1", {"lines": [1]}),
    ("finance_atomic_issue_review.v1", {"verdicts": []}),
])
def test_finance_structured_pass_accepts_direct_payload(schema_name, output) -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID, base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "finance-direct", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(output)}],
    }
    result = adapter.complete([{"role": "user", "content": "review"}], schema_name)
    assert result.payload == {"type": "final", "output": output}


def test_chat_tool_action_accepts_name_alias_from_compatible_model() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "tool-name-alias", "stop_reason": "end_turn",
        "content": [{"type": "text", "text":
                     '{"type":"tool_call","name":"article.list","arguments":{"author":"TT"}}'}],
    }
    result = adapter.complete(
        [{"role": "user", "content": "List TT articles"}], "general_chat_agent_loop.v1"
    )
    assert result.payload == {
        "type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}
    }


def test_chat_tool_action_normalizes_deepseek_dsml_text_without_native_tools() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    marker = chr(0xFF5C) * 2
    dsml = (
        f"<{marker}DSML{marker} calls>\n"
        f"<{marker}DSML{marker} invoke name=\"tool_call\">\n"
        f"<{marker}DSML{marker} parameter name=\"tool_name\" string=\"true\">article.list"
        f"</{marker}DSML{marker} parameter>\n"
        f"<{marker}DSML{marker} parameter name=\"arguments\" string=\"false\">"
        f"{{\"author\": \"TT\"}}</{marker}DSML{marker} parameter>\n"
        f"</{marker}DSML{marker} invoke>\n"
        f"</{marker}DSML{marker} calls>"
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "dsml-tool", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": dsml}],
    }

    result = adapter.complete(
        [{"role": "user", "content": "Browse TT articles"}],
        "general_chat_agent_loop.v1",
    )

    assert result.payload == {
        "type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}
    }


def test_chat_tool_action_accepts_current_deepseek_dsml_tool_call_dialect() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    marker = chr(0xFF5C) * 2
    dsml = (
        f"<{marker}DSML{marker} calls>\n"
        f"<{marker}DSML{marker} invoke name=\"tool.call\">\n"
        f"<{marker}DSML{marker} parameter name=\"name\" string=\"true\">article.list"
        f"</{marker}DSML{marker} parameter>\n"
        f"<{marker}DSML{marker} parameter name=\"arguments\" string=\"false\">"
        f"{{\"author\": \"TT\"}}</{marker}DSML{marker} parameter>\n"
        f"</{marker}DSML{marker} invoke>\n"
        f"</{marker}DSML{marker} calls>"
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "dsml-current-tool", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": dsml}],
    }

    result = adapter.complete(
        [{"role": "user", "content": "Browse TT articles"}],
        "general_chat_agent_loop.v1",
    )

    assert result.payload == {
        "type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}
    }


def test_chat_tool_action_defaults_omitted_arguments_to_empty_object() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "tool-no-arguments", "stop_reason": "end_turn",
        "content": [{"type": "text", "text":
                     '{"type":"tool_call","tool_name":"article.list"}'}],
    }
    result = adapter.complete(
        [{"role": "user", "content": "Browse article authors"}], "general_chat_agent_loop.v1"
    )
    assert result.payload == {
        "type": "tool_call", "tool_name": "article.list", "arguments": {}
    }


def test_chat_tool_action_normalizes_args_alias_without_losing_filters() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "tool-args-alias", "stop_reason": "end_turn",
        "content": [{"type": "text", "text":
                     '{"type":"tool_call","tool_name":"article.list",'
                     '"args":{"author":"TT"}}'}],
    }
    result = adapter.complete(
        [{"role": "user", "content": "Browse TT articles"}], "general_chat_agent_loop.v1"
    )
    assert result.payload == {
        "type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}
    }


def test_subagent_schema_accepts_tool_action_before_final() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash", base_url="https://example.invalid",
        api_key="test-key", max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "subagent-tool", "stop_reason": "end_turn",
        "content": [{"type": "text", "text":
                     '{"type":"tool_call","name":"article.read",'
                     '"arguments":{"article_id":"article_123"}}'}],
    }
    result = adapter.complete(
        [{"role": "user", "content": "Read source before verdict"}], "claim_verdict_set.v1"
    )
    assert result.payload == {
        "type": "tool_call", "tool_name": "article.read",
        "arguments": {"article_id": "article_123"},
    }


def test_general_chat_recovers_answer_from_malformed_json_after_retries() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "malformed",
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": "{\"type\":\"general_chat\",\"output\":{\"answer\":\"可恢复回答\"",
            }
        ],
    }

    result = adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")

    assert result.payload["output"]["answer"] == "可恢复回答"
    assert result.metadata["contract_recovered"] is True


def test_finance_target_audit_accepts_top_level_checks_envelope() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=1,
    )

    def fake_request(_system: str, _messages: list[dict[str, str]]) -> dict:
        return {
            "id": "target-audit",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": json.dumps({"checks": [], "coverage_complete": True}),
            }],
        }

    adapter._request = fake_request  # type: ignore[method-assign]
    result = adapter.complete([{"role": "user", "content": "audit"}], "finance_target_answer_audit.v1")
    assert result.payload["output"]["checks"] == []
    assert result.payload["output"]["coverage_complete"] is True


def test_from_env_uses_the_same_effective_model_parameters(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.test/anthropic/")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_MAX_TOKENS", "1536")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_TEMPERATURE", "0")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_TIMEOUT", "45")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_ATTEMPTS", "2")

    adapter = AnthropicCompatibleModelAdapter.from_env()

    assert adapter.model_id == DEFAULT_MODEL_ID
    assert adapter.base_url == "https://example.test/anthropic"
    assert adapter.max_tokens == 1536
    assert adapter.temperature == 0
    assert adapter.timeout_seconds == 45
    assert adapter.max_attempts == 2


def test_non_retryable_auth_error_stops_after_one_attempt() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID,
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=3,
    )
    calls = 0

    def fail_auth(_system: str, _messages: list[dict[str, str]]) -> dict:
        nonlocal calls
        calls += 1
        raise ModelInvocationError("HTTP 401: unauthorized")

    adapter._request = fail_auth  # type: ignore[method-assign]

    with pytest.raises(ModelInvocationError, match="已尝试 1 次"):
        adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")
    assert calls == 1


def test_transient_remote_close_is_retried() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID,
        base_url="https://example.invalid",
        api_key="test-key",
        max_attempts=2,
    )
    calls = 0

    def retry_once(_system: str, _messages: list[dict[str, str]]) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelInvocationError("Remote end closed connection without response")
        return {
            "id": "retry-ok",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": '{"type":"general_chat","output":{"answer":"recovered"}}',
            }],
        }

    adapter._request = retry_once  # type: ignore[method-assign]
    result = adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")
    assert result.payload["output"]["answer"] == "recovered"
    assert calls == 2
