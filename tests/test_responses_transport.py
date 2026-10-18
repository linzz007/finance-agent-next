"""Responses protocol, completion safety and existing recovery integration."""

import http.client
import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from urllib import error

import pytest

from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.research_budget import ResearchLimits, execution_scope
from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter
from tests.test_openai_streaming import StreamResponse

ACTION = {"type": "final", "output": {"answer": "完整分析，保留条件与作者归因。"}}
TEXT = json.dumps(ACTION, ensure_ascii=False)


def body(text=TEXT, *, status="completed", reason=None):
    result = {"id": "resp_test", "model": "model-test", "status": status, "output": [
        {"id": "reasoning_test", "type": "reasoning", "summary": [{"text": "hidden reasoning"}]},
        {"id": "msg_test", "type": "message", "role": "assistant", "status": status,
         "content": [{"type": "output_text", "text": text, "annotations": []}]},
    ], "usage": {"input_tokens": 32, "output_tokens": 40, "total_tokens": 72}}
    if reason:
        result["incomplete_details"] = {"reason": reason}
    return result


def sse(event):
    return ("event: " + event["type"] + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()


def delta(text=TEXT, **extra):
    return {"type": "response.output_text.delta", "item_id": "msg_test",
            "output_index": 1, "content_index": 0, "delta": text, **extra}


def stream(snapshot=None, *, content=TEXT):
    snapshot = snapshot or body(content)
    return (sse({"type": "response.created", "response": {"id": snapshot["id"], "status": "in_progress"}})
            + sse(delta(content)) + sse({"type": "response." + snapshot["status"], "response": snapshot}))


def model(**kwargs):
    return ResponsesModelAdapter(model_id="model-test", base_url="https://example.test/v1",
                                 api_key="fixture-secret", max_attempts=kwargs.pop("max_attempts", 1), **kwargs)


def complete(adapter):
    return adapter.complete([{"role": "system", "content": "Fixture contract"},
                             {"role": "user", "content": "Analyze fixture"}], "general_chat_agent_loop.v1")


@pytest.fixture
def wire(monkeypatch):
    requests, responses = [], []

    def urlopen(req, timeout):
        requests.append((req, json.loads(req.data), timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", urlopen)
    monkeypatch.setattr("policy_impact.harness.model_gateway.time.sleep", lambda _: None)
    return requests, responses


@pytest.mark.parametrize("streaming", [False, True])
def test_completed_response_has_no_native_tools_or_hidden_reasoning(wire, streaming):
    requests, responses = wire
    response = StreamResponse(stream()) if streaming else BytesIO(json.dumps(body()).encode())
    responses.append(response)
    result = complete(model(stream=streaming))
    req, payload, _ = requests[0]
    assert req.full_url == "https://example.test/v1/responses"
    assert req.get_header("Authorization") == "Bearer fixture-secret"
    assert payload["instructions"] == "Fixture contract"
    assert payload["input"] == [{"role": "user", "content": "Analyze fixture"}]
    assert payload["store"] is False
    assert payload["stream"] is streaming
    assert payload["max_output_tokens"] == 2048
    assert not {"tools", "thinking", "max_tokens", "previous_response_id", "stream_options"}.intersection(payload)
    assert result.payload == ACTION
    assert result.metadata["usage"]["total_tokens"] == 72
    assert result.metadata["api_style"] == "responses"
    assert result.metadata["response_complete"] is True
    assert result.metadata["completion_state"] == "completed"
    assert result.metadata["response_id"] == "resp_test"
    assert "hidden reasoning" not in json.dumps(result.to_dict())
    assert "fixture-secret" not in json.dumps(result.to_dict())
    assert response.closed
    if streaming:
        assert result.metadata["stream_complete"] is True
        assert result.metadata["stream_content_chars"] == len(TEXT)
        assert result.metadata["time_to_first_token_ms"] is not None


@pytest.mark.parametrize("raw", [
    sse(delta()),
    sse(delta()) + b"data: [DONE]\n\n",
    sse({"type": "response.completed", "response": body(status="in_progress")}),
    sse({"type": "response.completed", "response": []}),
    sse({"type": "response.created", "response": {"id": "wrong"}}) + stream(),
    sse(delta("different")) + sse({"type": "response.completed", "response": body()}),
    sse(delta(item_id="wrong")) + sse({"type": "response.completed", "response": body()}),
    sse(delta(sequence_number=2)) + sse(delta("", sequence_number=1)),
    sse(delta(output_index=-1)), sse(delta(content_index=True)), sse(delta(item_id=None)),
    sse(delta(["not text"])),
    b'event: response.created\ndata: {"type":"response.completed"}\n\n',
    b"data: []\n\n", b"data: {invalid}\n\n", b"data: \xff\n\n",
])
def test_invalid_or_incomplete_stream_never_returns_parseable_action(wire, raw):
    requests, responses = wire
    response = StreamResponse(raw)
    responses.append(response)
    with pytest.raises(ModelInvocationError) as failed:
        complete(model(stream=True))
    assert len(requests) == 1
    assert failed.value.metadata["transport_attempts"][0]["response_complete"] is False
    assert failed.value.structured_output is False
    assert response.closed
    assert "完整分析" not in str(failed.value.metadata)


def test_retry_after_partial_stream_discards_first_body_and_preserves_inputs(wire):
    requests, responses = wire
    responses.extend([StreamResponse(sse(delta("partial must not execute"))), StreamResponse(stream())])
    with execution_scope(stage="finance.main") as scope:
        result = complete(model(stream=True))
        assert scope.root.requests == 2
    assert result.payload == ACTION
    assert requests[0][1] == requests[1][1]
    assert [row["stream_complete"] for row in result.metadata["transport_attempts"]] == [False, True]
    assert "partial must not execute" not in str(result.metadata)


@pytest.mark.parametrize("streaming", [False, True])
def test_truncated_valid_json_does_not_become_candidate_or_tool(wire, streaming):
    requests, responses = wire
    snapshot = body(status="incomplete", reason="max_output_tokens")
    responses.append(StreamResponse(stream(snapshot)) if streaming else BytesIO(json.dumps(snapshot).encode()))
    with (execution_scope(stage="finance.main", limits=ResearchLimits(output_tokens=2048)),
          pytest.raises(ModelInvocationError) as failed):
        complete(model(stream=streaming))
    assert len(requests) == 1
    assert failed.value.metadata["failure_kind"] == "truncated"
    assert failed.value.metadata["preserved_final_output"] == {}
    row = failed.value.metadata["transport_attempts"][0]
    assert row["response_complete"] is True
    assert row["completion_state"] == "incomplete"
    assert row["incomplete_reason"] == "max_output_tokens"


def test_truncation_uses_shared_output_recovery_budget(wire):
    requests, responses = wire
    responses.extend([StreamResponse(stream(body(status="incomplete", reason="max_output_tokens"))),
                      StreamResponse(stream())])
    with execution_scope(stage="finance.main") as scope:
        result = complete(model(stream=True))
        assert scope.root.requests == 2
    assert result.payload == ACTION
    assert requests[1][1]["max_output_tokens"] == 4096
    assert result.metadata["retry_errors"][0]["kind"] == "truncated"


@pytest.mark.parametrize("snapshot", [
    body(status="incomplete", reason="content_filter"),
    {**body(), "status": "failed", "error": {"code": "invalid_api_key", "message": "fixture-secret"}},
    {**body(), "output": [{"type": "function_call", "arguments": "{}"}]},
    {**body(), "output": [{"id": "m", "role": "assistant", "type": "message", "status": "completed",
                          "content": [{"type": "refusal", "refusal": "No"}]}]},
])
def test_provider_rejection_is_not_protocol_repaired_or_executed(wire, snapshot):
    requests, responses = wire
    responses.append(BytesIO(json.dumps(snapshot).encode()))
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            complete(model(max_attempts=5))
        assert scope.root.requests == 1
    assert len(requests) == 1
    assert failed.value.metadata["failed_public_responses"] == []
    assert "fixture-secret" not in str(failed.value)


@pytest.mark.parametrize("status", ["queued", "in_progress", "cancelled", None])
def test_nonterminal_json_is_not_accepted(wire, status):
    wire[1].append(BytesIO(json.dumps(body(status=status)).encode()))
    with pytest.raises(ModelInvocationError):
        complete(model())


def test_empty_completed_response_uses_existing_protocol_repair(wire):
    requests, responses = wire
    responses.extend([StreamResponse(stream(body(""), content="")), StreamResponse(stream())])
    with execution_scope(stage="finance.main") as scope:
        result = complete(model(stream=True))
        assert scope.root.requests == 2
    assert result.metadata["retry_errors"][0]["kind"] == "empty_public_response"
    assert result.metadata["transport_attempts"][0]["stream_complete"] is True
    assert "hidden reasoning" not in str(requests[1][1]) + str(result.metadata)


def test_complete_invalid_public_json_shares_original_schema_repair(wire):
    requests, responses = wire
    responses.extend([StreamResponse(stream(content="<DSML>not a public action</DSML>")), StreamResponse(stream())])
    with execution_scope(stage="finance.main") as scope:
        result = complete(model(stream=True))
        assert scope.root.requests == 2
    assert result.payload == ACTION
    assert result.metadata["retry_errors"][0]["kind"] == "format"
    assert "action_json_schema" in str(requests[1][1])


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_http_status_redaction_and_retry(wire, status):
    requests, responses = wire
    responses.append(error.HTTPError("https://example.test", status, "error", {"Retry-After": "0"},
                                     BytesIO(b"fixture-secret")))
    if status >= 429:
        responses.append(StreamResponse(stream()))
    with execution_scope(stage="finance.main") as scope:
        if status in {401, 403}:
            with pytest.raises(ModelInvocationError) as failed:
                complete(model(stream=True))
            assert "fixture-secret" not in str(failed.value) + str(failed.value.metadata)
            assert scope.root.requests == 1
        else:
            result = complete(model(stream=True))
            assert result.payload == ACTION
            assert result.metadata["transport_attempts"][0]["http_status"] == status
            assert scope.root.requests == 2
    assert len(requests) == (1 if status in {401, 403} else 2)


@pytest.mark.parametrize("kind", ["error", "response.failed"])
def test_in_stream_server_errors_use_shared_retry(wire, kind):
    problem = {"code": "server_error", "message": "fixture-secret temporary"}
    event = ({"type": kind, **problem} if kind == "error" else
             {"type": kind, "response": {**body(), "status": "failed", "error": problem}})
    wire[1].extend([StreamResponse(sse(event)), StreamResponse(stream())])
    with execution_scope(stage="finance.main") as scope:
        result = complete(model(stream=True))
        assert scope.root.requests == 2
    assert result.payload == ACTION
    assert "fixture-secret" not in str(result.metadata)


def test_stream_and_transport_repair_do_not_exceed_five_requests(wire):
    requests, responses = wire
    responses.extend([StreamResponse(sse(delta())), StreamResponse(stream(content="DSML")),
                      StreamResponse(sse(delta())), StreamResponse(stream(content="DSML")),
                      StreamResponse(stream(content="DSML"))])
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            complete(model(stream=True))
        assert scope.root.requests == 5
    assert failed.value.metadata["attempts"] == 5
    assert len(requests) == 5


@pytest.mark.parametrize("raw", [b"data: " + b"x" * (1024 * 1024 + 1),
                                 (b":" + b"x" * (256 * 1024) + b"\n\n") * 33],
                         ids=["event_limit", "stream_limit"])
def test_size_limits(wire, raw):
    wire[1].append(StreamResponse(raw))
    with pytest.raises(ModelInvocationError, match="size limit"):
        complete(model(stream=True))


@pytest.mark.parametrize("streaming", [False, True])
def test_heartbeat_and_dribbling_do_not_reset_deadline(monkeypatch, wire, streaming):
    now = [0.0]

    class Slow(StreamResponse):
        def read1(self, *_args):
            now[0] += .6
            return b": ping\n\n" if streaming else b" "

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.time.perf_counter", lambda: now[0])
    wire[1].append(Slow())
    with pytest.raises(ModelInvocationError, match="超时"):
        complete(model(stream=streaming, stream_max_seconds=1))


def test_completed_snapshot_without_deltas_is_authoritative(wire):
    wire[1].append(StreamResponse(sse({"type": "response.completed", "response": body()})))
    result = complete(model(stream=True))
    assert result.payload == ACTION
    assert result.metadata["stream_content_chars"] == 0
    assert result.metadata["response_complete"] is True


def test_sse_multiline_crlf_and_unknown_reasoning_events(wire):
    snapshot = json.dumps({"type": "response.completed", "response": body()}, ensure_ascii=False)
    split = snapshot.index('"response":')
    raw = b"\xef\xbb\xbf: ping\r\n\r\n"
    raw += sse({"type": "response.reasoning_text.delta", "delta": "never disclose"})
    raw += ("event: response.completed\r\ndata: " + snapshot[:split] + "\r\ndata: " + snapshot[split:]
            + "\r\n\r\n").encode()
    wire[1].append(StreamResponse(raw))
    result = complete(model(stream=True))
    assert result.payload == ACTION
    assert "never disclose" not in str(result.to_dict())


def test_actual_http_utf8_byte_fragments(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    captured = []
    raw = stream()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
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
        adapter = model(stream=True)
        adapter.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        assert complete(adapter).payload == ACTION
        assert captured[0][0] == "/v1/responses"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_responses_configuration_reaches_runtime_without_changing_default(monkeypatch):
    from policy_impact.harness.agent_runtime import AgentRuntime
    from policy_impact.harness.model_config import effective_model_config, model_runtime_status

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-secret")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROFILE", "gpt")
    assert effective_model_config()["protocol"] == "openai-compatible"
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROFILE", "gpt-responses")
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.model_adapter = None
    selected = runtime._get_model_adapter()
    assert isinstance(selected, ResponsesModelAdapter)
    assert selected.profile_id == "gpt-responses"
    assert selected.stream is True
    assert "fixture-secret" not in str(model_runtime_status())


def test_responses_rejects_deepseek_only_configuration():
    from pydantic import ValidationError

    from policy_impact.harness.model_config import NamedModelProfile, named_model_profiles

    for values in ({"thinking": True}, {"thinking": False}, {"thinking": True, "reasoning_effort": "high"}):
        with pytest.raises(ValueError):
            model(**values)
        payload = deepcopy(named_model_profiles()["gpt-responses"].model_dump())
        payload.update(values)
        with pytest.raises(ValidationError):
            NamedModelProfile.model_validate(payload)


@pytest.mark.parametrize("reason", [None, "", "unknown", "tool_calls", "content_filter"])
def test_chat_json_requires_supported_finish_reason(wire, reason):
    from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter

    wire[1].append(BytesIO(json.dumps({"choices": [{"finish_reason": reason, "message": {"content": TEXT}}]}).encode()))
    adapter = OpenAICompatibleModelAdapter(model_id="fixture", base_url="https://example.test", api_key="fixture-secret")
    with pytest.raises(ModelInvocationError):
        complete(adapter)
    assert len(wire[0]) == 1


def test_probe_keeps_single_physical_request_and_separates_structure_from_transport(wire):
    from scripts.finance_transport_comparison import one_request, probe_messages

    wire[1].append(StreamResponse(stream(content="DSML")))
    result = one_request(model(stream=True), probe_messages("short"), "short", 1)
    assert result["transport_completed"] is True
    assert result["contract_valid"] is False
    assert result["expectation_met"] is False
    assert len(wire[0]) == 1


def test_probe_multiple_turns_resend_full_context_and_do_not_execute_tools(wire, tmp_path):
    import time

    from scripts.finance_transport_comparison import EXPECTED_TOOL, run_probe

    for payload in (EXPECTED_TOOL, {"type": "final", "output": {"answer": "alpha|conditional"}},
                    {"type": "final", "output": {"answer": "alpha|conditional|beta|opposed"}}):
        wire[1].append(StreamResponse(stream(content=json.dumps(payload))))
    result = run_probe(model(stream=True), "multiturn", deadline=time.monotonic() + 10,
                       timeout_seconds=5, max_tokens=2048, directory=tmp_path / "probe")
    assert result["status"] == "passed"
    assert len(wire[0]) == 3
    assert len(wire[0][0][1]["input"]) == 1
    assert len(wire[0][2][1]["input"]) == 5
    assert "previous_response_id" not in wire[0][2][1]
    assert wire[0][2][1]["input"][1]["role"] == "assistant"


def test_live_comparison_requires_explicit_live_flag(tmp_path, monkeypatch):
    from scripts import finance_transport_comparison as probe

    monkeypatch.setattr("sys.argv", ["probe", "--output", str(tmp_path / "probe")])
    with pytest.raises(SystemExit) as stopped:
        probe.main()
    assert stopped.value.code == 2
    assert not (tmp_path / "probe").exists()


def test_long_input_probe_is_bounded_and_identical_across_protocols():
    from policy_impact.harness.context_manifest import estimate_tokens
    from scripts.finance_transport_comparison import probe_messages

    messages = probe_messages("long_input")
    assert 5000 < estimate_tokens(messages) < ResearchLimits().input_limit()
    assert messages == probe_messages("long_input")


def test_parent_deadline_limits_read_and_preserves_single_request(monkeypatch, wire):
    now = [0.0]

    class Slow(StreamResponse):
        def read1(self, *_args):
            now[0] += .6
            return b": ping\n\n"

    monkeypatch.setattr("policy_impact.harness.research_budget.time.monotonic", lambda: now[0])
    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.time.perf_counter", lambda: now[0])
    wire[1].append(Slow())
    with execution_scope(stage="finance.main", timeout_seconds=1) as scope:
        with pytest.raises(ModelInvocationError) as failed:
            complete(model(stream=True))
        assert scope.root.requests == 1
    assert failed.value.metadata["failure_kind"] == "time_budget"
    assert wire[0][0][2] <= 1


@pytest.mark.parametrize("exception", [http.client.RemoteDisconnected("fixture-secret closed"),
                                      http.client.IncompleteRead(b"private unfinished text"),
                                      ConnectionResetError("fixture-secret reset")])
def test_transport_read_failure_has_no_partial_text_in_trace(monkeypatch, wire, exception):
    class Broken(StreamResponse):
        def read1(self, *_args):
            raise exception

    wire[1].append(Broken())
    with pytest.raises(ModelInvocationError) as failed:
        complete(model(stream=True))
    assert "fixture-secret" not in str(failed.value.metadata)
    assert "private unfinished text" not in str(failed.value.metadata)
    assert failed.value.metadata["transport_attempts"][0]["stream_complete"] is False


@pytest.mark.parametrize("status", [[], {}], ids=["array", "object"])
def test_malformed_terminal_type_is_a_recorded_protocol_failure(wire, status):
    wire[1].append(BytesIO(json.dumps(body(status=status)).encode()))
    with pytest.raises(ModelInvocationError) as failed:
        complete(model())
    assert len(wire[0]) == 1
    assert failed.value.metadata["transport_attempts"][0]["response_complete"] is False


def test_malformed_message_status_is_a_recorded_protocol_failure(wire):
    snapshot = body()
    snapshot["output"][1]["status"] = []
    wire[1].append(BytesIO(json.dumps(snapshot).encode()))
    with pytest.raises(ModelInvocationError) as failed:
        complete(model())
    assert failed.value.metadata["transport_attempts"]


def test_malformed_chat_finish_reason_is_a_recorded_protocol_failure(wire):
    wire[1].append(BytesIO(json.dumps({"choices": [{"finish_reason": [], "message": {"content": TEXT}}]}).encode()))
    from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter

    adapter = OpenAICompatibleModelAdapter(model_id="fixture", base_url="https://example.test", api_key="fixture-secret")
    with pytest.raises(ModelInvocationError) as failed:
        complete(adapter)
    assert failed.value.metadata["transport_attempts"]


def test_stream_refusal_is_complete_transport_not_a_network_failure(wire):
    snapshot = body()
    snapshot["output"][1]["content"] = [{"type": "refusal", "refusal": "No"}]
    wire[1].append(StreamResponse(sse({"type": "response.completed", "response": snapshot})))
    with execution_scope(stage="finance.main") as scope:
        with pytest.raises(ModelInvocationError) as failed:
            complete(model(stream=True))
        assert scope.root.requests == 1
    row = failed.value.metadata["transport_attempts"][0]
    assert row["response_complete"] is True
    assert row["stream_complete"] is True
    assert row["completion_state"] == "completed"
    assert failed.value.metadata["failed_public_responses"] == []
