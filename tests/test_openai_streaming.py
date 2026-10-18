from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread
from urllib import error

import pytest

from policy_impact.harness.model_config import (
    default_model_config, effective_model_config, model_runtime_status,
)
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter


class StreamResponse(BytesIO):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}


def event(payload: object) -> bytes:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {text}\n\n".encode("utf-8")


def chunk(content=None, finish=None, **extra) -> dict:
    return {"id": "stream-test", "model": "test-model", "choices": [{
        "index": 0, "delta": {"content": content}, "finish_reason": finish,
    }], **extra}


def answer(text="完整分析", finish="stop") -> bytes:
    content = json.dumps({"type": "final", "output": {"answer": text}}, ensure_ascii=False)
    return event(chunk(content)) + event(chunk(finish=finish)) + event("[DONE]")


def adapter(**kwargs):
    return OpenAICompatibleModelAdapter(
        model_id="test-model", base_url="https://example.test/v1", api_key="test-secret",
        stream=kwargs.pop("stream", True), max_attempts=kwargs.pop("max_attempts", 1), **kwargs,
    )


def complete(model):
    return model.complete([{"role": "user", "content": "分析文章"}], "general_chat_agent_loop.v1")


def test_stream_collects_content_ignores_reasoning_and_retains_usage(monkeypatch):
    requests = []
    content = json.dumps({"type": "final", "output": {"answer": "黄金观点完整分析"}}, ensure_ascii=False)
    raw = b"\xef\xbb\xbf: heartbeat\r\n\r\n"
    raw += event({"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "hidden"}}]})
    raw += event(chunk(content[:25])) + event(chunk(content[25:], finish="stop"))
    raw += event({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 20}})
    raw += event("[DONE]")
    response = StreamResponse(raw)

    def open_stream(req, timeout):
        requests.append(json.loads(req.data))
        assert req.get_header("Accept") == "text/event-stream"
        return response

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_stream)
    result = complete(adapter())

    assert result.payload["output"]["answer"] == "黄金观点完整分析"
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert result.metadata["usage"]["completion_tokens"] == 20
    assert result.metadata["stream_complete"] is True
    assert result.metadata["stream_content_chars"] == len(content)
    assert result.metadata["stream_event_count"] == 5
    assert result.metadata["time_to_first_token_ms"] is not None
    assert result.metadata["transport"] == "sse"
    assert result.metadata["transport_attempts"][0]["attempt"] == 1
    assert "hidden" not in str(result)
    assert response.closed


def test_multiline_data_and_crlf_events(monkeypatch):
    raw = event(chunk(json.dumps({"type": "final", "output": {"answer": "multiline"}})))
    raw += b'event: message\r\nid: 2\r\ndata: {"choices": [{"delta": {},\r\ndata: "finish_reason": "stop"}]}\r\n\r\n'
    raw += event("[DONE]")
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: StreamResponse(raw))
    assert complete(adapter()).payload["output"]["answer"] == "multiline"


@pytest.mark.parametrize("raw", [
    event(chunk('{"type":"final","output":{"answer":"partial"}}', finish="stop")),
    event(chunk('{"type":"final","output":{"answer":"partial"}}')) + event("[DONE]"),
    event("[DONE]"),
    b"data: [DONE]",
    b"data: {broken\n\n",
    b"data: \xff\n\n",
    event(["not an object"]),
    event({"error": {"message": "test-secret must never leak"}}),
    event({"choices": "bad"}),
    event({"choices": [None]}),
    event({"choices": [{"delta": "bad"}]}),
    event(chunk(["not text"])),
    event(chunk(finish="stop")) + event(chunk("late text")) + event("[DONE]"),
])
def test_incomplete_or_invalid_stream_never_recovers_partial_answer(monkeypatch, raw):
    response = StreamResponse(raw)
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ModelInvocationError) as failed:
        complete(adapter())
    assert not failed.value.structured_output
    assert failed.value.metadata["last_error_kind"] == "transport"
    assert failed.value.metadata["transport_attempts"][0]["stream_complete"] is False
    assert "test-secret" not in str(failed.value)
    assert response.closed


def test_disconnect_retry_discards_buffer_and_keeps_original_request(monkeypatch):
    requests = []
    responses = [StreamResponse(event(chunk("discard me"))), StreamResponse(answer("second complete answer"))]

    def open_stream(req, timeout):
        requests.append(json.loads(req.data))
        return responses[len(requests) - 1]

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_stream)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _: None)
    result = complete(adapter(max_attempts=2))
    assert result.payload["output"]["answer"] == "second complete answer"
    assert requests[0] == requests[1]
    assert result.metadata["attempts"] == 2
    assert [item["stream_complete"] for item in result.metadata["transport_attempts"]] == [False, True]
    assert all(response.closed for response in responses)


def test_length_with_done_is_still_truncation(monkeypatch):
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: StreamResponse(answer(finish="length")))
    with pytest.raises(ModelInvocationError, match="max_tokens") as failed:
        complete(adapter())
    assert failed.value.structured_output
    assert failed.value.metadata["transport_attempts"][0]["stream_complete"] is True


@pytest.mark.parametrize("failure", [
    TimeoutError("idle timeout"),
    http.client.IncompleteRead(b"partial"),
    ConnectionResetError("test-secret connection reset"),
])
def test_read_failures_are_sanitized_and_have_transport_metadata(monkeypatch, failure):
    class BrokenResponse(StreamResponse):
        def read1(self, *_args):
            raise failure

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: BrokenResponse())
    with pytest.raises(ModelInvocationError) as failed:
        complete(adapter())
    assert failed.value.metadata["last_error_kind"] == "transport"
    assert failed.value.metadata["transport_attempts"][0]["stream_complete"] is False
    assert "test-secret" not in str(failed.value)


def test_heartbeats_do_not_reset_total_duration(monkeypatch):
    now = [0.0]

    class Heartbeats(StreamResponse):
        def read1(self, *_args):
            now[0] += .6
            return b": ping\n\n"

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.time.perf_counter", lambda: now[0])
    response = Heartbeats()
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ModelInvocationError, match="超时") as failed:
        complete(adapter(stream_max_seconds=1))
    assert failed.value.metadata["transport_attempts"][0]["stream_duration_ms"] == 1200
    assert response.closed


def test_unfinished_dribbling_line_still_obeys_total_duration(monkeypatch):
    now = [0.0]

    class DribblingResponse(StreamResponse):
        def read1(self, *_args):
            now[0] += .6
            return b"x"

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.time.perf_counter", lambda: now[0])
    response = DribblingResponse()
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ModelInvocationError, match="超时") as failed:
        complete(adapter(stream_max_seconds=1))
    assert failed.value.metadata["transport_attempts"][0]["stream_duration_ms"] == 1200
    assert response.closed


def test_lone_carriage_return_sse_lines(monkeypatch):
    raw = answer().replace(b"\n", b"\r")
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: StreamResponse(raw))
    assert complete(adapter()).payload["output"]["answer"] == "完整分析"


@pytest.mark.parametrize("raw", [
    b"data: " + b"x" * (1024 * 1024),
    b": ping\n\n" * (1024 * 1024 + 1),
], ids=["event-limit", "stream-limit"])
def test_stream_resource_limits(monkeypatch, raw):
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: StreamResponse(raw))
    with pytest.raises(ModelInvocationError, match="size limit"):
        complete(adapter())


def test_auth_error_is_not_retried_and_is_redacted(monkeypatch):
    calls = []

    def unauthorized(*_args, **_kwargs):
        calls.append(1)
        raise error.HTTPError("https://example.test", 401, "unauthorized", {}, BytesIO(b"test-secret"))

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", unauthorized)
    with pytest.raises(ModelInvocationError) as failed:
        complete(adapter(max_attempts=3))
    assert len(calls) == 1
    assert "test-secret" not in str(failed.value)
    assert failed.value.metadata["attempts"] == 1


def test_sse_requires_declared_content_type(monkeypatch):
    response = StreamResponse(answer())
    response.headers = {"Content-Type": "application/json"}
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ModelInvocationError, match="text/event-stream"):
        complete(adapter())


@pytest.mark.parametrize("status", [401, 403])
def test_streamed_auth_error_is_not_retried(monkeypatch, status):
    calls = []

    def open_stream(*_a, **_k):
        calls.append(1)
        return StreamResponse(event({"error": {"code": status, "message": "test-secret"}}))

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_stream)
    with pytest.raises(ModelInvocationError, match=f"HTTP {status}") as failed:
        complete(adapter(max_attempts=3))
    assert len(calls) == 1
    assert "test-secret" not in str(failed.value)


def test_empty_complete_stream_retains_transport_diagnostics(monkeypatch):
    raw = event(chunk(finish="stop")) + event("[DONE]")
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", lambda *_a, **_k: StreamResponse(raw))
    with pytest.raises(ModelInvocationError, match="message.content") as failed:
        complete(adapter())
    assert failed.value.metadata["transport_attempts"][0]["stream_complete"] is True
    assert failed.value.metadata["transport_attempts"][0]["stream_content_chars"] == 0


def test_research_empty_complete_stream_can_recover_without_reading_reasoning(monkeypatch):
    from policy_impact.harness.research_budget import execution_scope

    requests = []
    raw = event({"id": "empty-stream", "choices": [{"delta": {"reasoning_content": "private thoughts"},
                                                          "finish_reason": "stop"}]}) + event("[DONE]")
    responses = [StreamResponse(raw), StreamResponse(answer("Recovered public analysis"))]

    def open_stream(req, timeout):
        requests.append(json.loads(req.data))
        return responses[len(requests) - 1]

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_stream)
    with execution_scope(stage="finance.main") as scope:
        result = complete(adapter())
        assert scope.root.requests == 2
    assert result.payload["output"]["answer"] == "Recovered public analysis"
    assert result.metadata["retry_errors"][0]["kind"] == "empty_public_response"
    assert [row["status"] for row in result.metadata["transport_attempts"]] == ["received", "received"]
    assert result.metadata["transport_attempts"][0]["stream_complete"] is True
    assert result.metadata["failed_public_responses"][0]["response_id"] == "empty-stream"
    assert "private thoughts" not in str(result.metadata) + str(requests)


def test_nonstream_content_filter_is_not_treated_as_empty_protocol_failure(monkeypatch):
    from policy_impact.harness.research_budget import execution_scope

    requests = []

    def open_response(req, timeout):
        requests.append(1)
        return BytesIO(json.dumps({"choices": [{"finish_reason": "content_filter", "message": {
            "content": "", "reasoning_content": "never expose this"}}]}).encode())

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_response)
    with execution_scope(stage="finance.main"):
        with pytest.raises(ModelInvocationError) as failed:
            complete(adapter(stream=False))
    assert requests == [1]
    assert failed.value.metadata["failure_kind"] == "transport"
    assert failed.value.metadata["failed_public_responses"] == []


def test_complete_stream_schema_failure_uses_existing_schema_repair(monkeypatch):
    requests = []
    responses = [StreamResponse(event(chunk('{"type":', finish="stop")) + event("[DONE]")), StreamResponse(answer())]

    def open_stream(req, timeout):
        requests.append(json.loads(req.data))
        return responses[len(requests) - 1]

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", open_stream)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _: None)
    result = complete(adapter(max_attempts=2))
    assert result.payload["output"]["answer"] == "完整分析"
    assert "JSON 契约" in requests[1]["messages"][-1]["content"]
    assert all(item["stream_complete"] for item in result.metadata["transport_attempts"])


def test_direct_model_failure_keeps_transport_trace_metadata():
    from policy_impact.harness.agent_runtime import AgentRuntime

    recorded = []

    class Store:
        def add_agent_step(self, *args, **kwargs):
            recorded.append(kwargs)

    diagnostics = {"attempts": 1, "transport_attempts": [{
        "attempt": 1, "transport": "sse", "stream_complete": False,
        "stream_content_chars": 35,
    }]}

    class FailedAdapter:
        def complete(self, **kwargs):
            raise ModelInvocationError("stream transport error: EOF", metadata=diagnostics)

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.store = Store()
    with pytest.raises(ModelInvocationError):
        runtime._complete_model(
            run_id="test-run", title="streamed article analysis", model_id="test-model",
            schema_name="test.v1", messages=[], prompt_contract={}, adapter=FailedAdapter(),
        )
    assert recorded[0]["status"] == "failed"
    assert recorded[0]["output_payload"]["error_metadata"] == diagnostics


def test_stream_configuration_propagates_to_adapter_and_status(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROTOCOL", "openai-compatible")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_STREAM", "true")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", "345")
    model = OpenAICompatibleModelAdapter.from_env()
    assert model.stream is True
    assert model.stream_max_seconds == 345
    assert effective_model_config()["stream"] is True
    assert model_runtime_status()["stream_max_seconds"] == 345
    assert default_model_config()["metadata"]["stream"] is True
    assert "test-secret" not in str(model_runtime_status())


@pytest.mark.parametrize("key,value", [
    ("AGENT_WORKBENCH_MODEL_STREAM", "maybe"),
    ("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", "0"),
    ("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", "901"),
    ("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", "nan"),
    ("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", "inf"),
])
def test_invalid_stream_configuration_is_rejected(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        effective_model_config()


def test_stream_is_opt_in_and_rejects_unsupported_protocol(monkeypatch):
    monkeypatch.delenv("AGENT_WORKBENCH_MODEL_STREAM", raising=False)
    assert effective_model_config()["stream"] is False
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_STREAM", "true")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROTOCOL", "anthropic")
    with pytest.raises(ValueError, match="openai-compatible"):
        effective_model_config()


def test_real_http_stream_handles_byte_fragments(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    received = []
    raw = answer("逐篇阅读黄金文章，保留时间与条件。")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for offset in range(0, len(raw), 3):
                self.wfile.write(raw[offset:offset + 3])
                self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = adapter()
        model.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = complete(model)
        assert result.payload["output"]["answer"] == "逐篇阅读黄金文章，保留时间与条件。"
        assert result.metadata["stream_complete"] is True
        assert received[0]["stream"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
