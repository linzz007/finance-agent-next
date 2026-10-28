"""Optional Harness 2.0 execution. No live credentials, corpus or network calls."""

import asyncio
import json
import sqlite3
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import fakeredis
import httpx
import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import (
    Cancel,
    CheckpointState,
    ModelPolicySet,
    Spawn,
    Submission,
    Wait,
)
from policy_impact.dispatch.execution import ExecutionCapabilities
from policy_impact.dispatch.model_execution import (
    GatewayModelAdapter,
    ModelRequest,
    profiles_from_registry,
    public_validation_errors,
)
from policy_impact.dispatch.transport import Dispatcher, RedisStreams
from policy_impact.dispatch.worker import Worker, WorkInput

TOOLS = [
    "agent.spawn",
    "agent.status",
    "agent.wait",
    "agent.send",
    "agent.cancel",
    "agent.resume",
    "result.get",
]
SECRET = "sk-fixture-never-a-real-credential"
ACTION = {"type": "final", "output": {"answer": "conditional author view, not verified fact"}}


def snapshot(protocol="openai-compatible", stream=False, **parameters):
    return {
        "profile": "fixture",
        "model_id": "fixture-model",
        "base_url": "https://fixture.invalid/v1",
        "protocol": protocol,
        "auth_env": "H2_TEST_UNUSED_KEY",
        "stream": stream,
        "parameters": parameters,
    }


def adapter(handler, *, credential=SECRET):
    return GatewayModelAdapter(
        credential_resolver=lambda name: credential, http_transport=httpx.MockTransport(handler)
    )


def request(model=None, remaining=30):
    model = model or snapshot()
    return ModelRequest(
        "call_" + uuid4().hex,
        model,
        [{"role": "user", "content": "fixture public question"}],
        "agent_action.v1",
        GatewayModelAdapter.settings(model),
        remaining,
    )


def chat(payload=ACTION, **changes):
    data = {
        "id": "provider-response",
        "model": "fixture-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload),
                    "reasoning_content": "private reasoning must not escape",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70,
            "secret_extra": SECRET,
        },
    }
    data.update(changes)
    return data


def responses(payload=ACTION, **changes):
    data = {
        "id": "provider-response",
        "model": "fixture-model",
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": "private reasoning must not escape"},
            {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            },
        ],
        "usage": {"input_tokens": 50, "output_tokens": 20},
    }
    data.update(changes)
    return data


class Chunks(httpx.AsyncByteStream):
    def __init__(self, content, step=7):
        self.content, self.step, self.closed = content, step, False

    async def __aiter__(self):
        for i in range(0, len(self.content), self.step):
            yield self.content[i : i + self.step]

    async def aclose(self):
        self.closed = True


def sse(protocol, *, complete=True, content=ACTION, public_text=None):
    text_content = json.dumps(content) if public_text is None else public_text
    if protocol == "openai-compatible":
        events = [
            {
                "id": "provider-response",
                "choices": [{"delta": {"reasoning_content": "private reasoning must not escape"}}],
            },
            {"id": "provider-response", "choices": [{"delta": {"content": text_content}}]},
            {
                "id": "provider-response",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
        ]
        text = "".join("data: " + json.dumps(event) + "\r\n\r\n" for event in events)
        return (text + ("data: [DONE]\r\n\r\n" if complete else "")).encode()
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {"id": "provider-response", "status": "in_progress"},
        },
        {
            "type": "response.reasoning_text.delta",
            "sequence_number": 1,
            "delta": "private reasoning must not escape",
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 2,
            "output_index": 1,
            "content_index": 0,
            "item_id": "msg",
            "delta": text_content,
        },
    ]
    if complete:
        terminal = responses(content)
        terminal["output"][1]["content"][0]["text"] = text_content
        events.append(
            {"type": "response.completed", "sequence_number": 3, "response": terminal}
        )
    return "".join("data: " + json.dumps(e) + "\r\n\r\n" for e in events).encode()


def collect(model_adapter, req):
    async def run():
        return [event async for event in model_adapter.request(req)]

    return asyncio.run(run())


def public_response(text, protocol, stream, *, complete=True):
    if stream:
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=Chunks(sse(protocol, public_text=text, complete=complete)))
    data = chat() if protocol == "openai-compatible" else responses()
    if protocol == "openai-compatible":
        data["choices"][0]["message"]["content"] = text
    else:
        data["output"][1]["content"][0]["text"] = text
    return httpx.Response(200, json=data)


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("wrapper", ["```\n{}\n```", "```json\n{}\n```", " \r\n```JSON\r\n{}\r\n```\r\n "])
def test_h2_single_outer_json_fence_normalizes_without_changing_raw_output(protocol, stream, wrapper):
    raw = wrapper.format(json.dumps(ACTION))
    sent = []

    def http(req):
        sent.append(req)
        return public_response(raw, protocol, stream)

    events = collect(adapter(http), request(snapshot(protocol, stream)))
    assert [event.kind for event in events] == ["started", "text_delta", "usage", "action_candidate", "completed"]
    assert events[-2].data["payload"] == ACTION
    assert events[1].data["text"] == raw.strip() and len(sent) == 1


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_h2_uses_first_complete_action_and_discards_trailing_model_transcript(protocol, stream):
    raw = json.dumps(ACTION) + "\n<system>fabricated tool observation</system>\n" + json.dumps(ACTION)
    events = collect(adapter(lambda req: public_response(raw, protocol, stream)), request(snapshot(protocol, stream)))
    assert [event.kind for event in events] == ["started", "text_delta", "usage", "action_candidate", "completed"]
    assert events[-2].data["payload"] == ACTION
    assert events[-1].data["public_output_normalization"] == {
        "json_action_boundary": {
            "strategy": "first_complete_object",
            "leading_wrapper": "none",
            "trailing_output_ignored": True,
            "trailing_chars": len(raw) - len(json.dumps(ACTION)),
        }
    }


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("needs_repair", [False, True])
def test_h2_repair_metadata_counts_only_executed_first_object(protocol, stream, needs_repair):
    first = '{"type":"final","output":{"answer":"raw\nline\\q"}}' if needs_repair else json.dumps(ACTION)
    tail = '\n<system>fake observation</system>\n{"ignored":"raw\n\\q"}'
    raw = first + tail
    sent = []

    def http(req):
        sent.append(req)
        return public_response(raw, protocol, stream)

    events = collect(adapter(http), request(snapshot(protocol, stream)))
    assert events[-1].kind == "completed" and len(sent) == 1
    expected = {"type": "final", "output": {"answer": "raw\nline\\q"}} if needs_repair else ACTION
    assert events[-2].data["payload"] == expected
    assert events[1].data["text"] == raw
    assert events[-1].data["public_output_normalization"] == {
        **({"json_string_controls": 1, "json_string_invalid_escapes": 1} if needs_repair else {}),
        "json_action_boundary": {
            "strategy": "first_complete_object",
            "leading_wrapper": "none",
            "trailing_output_ignored": True,
            "trailing_chars": len(tail),
        },
    }


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_h2_does_not_skip_invalid_first_object_to_reach_later_action(protocol):
    raw = '{"type":"not-a-valid-action"}\n' + json.dumps(ACTION)
    events = collect(adapter(lambda req: public_response(raw, protocol, False)), request(snapshot(protocol)))
    assert events[-1].kind == "failed" and events[-1].data["code"] == "contract_error"
    assert not any(event.kind == "action_candidate" for event in events)


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_h2_accepts_only_xml_tag_wrapper_around_one_action(protocol):
    raw = "<tool_call>\n" + json.dumps(ACTION) + "\n</tool_call>"
    events = collect(adapter(lambda req: public_response(raw, protocol, False)), request(snapshot(protocol)))
    assert events[-2].data["payload"] == ACTION
    assert events[-1].data["public_output_normalization"]["json_action_boundary"]["leading_wrapper"] == "xml_tags"


@pytest.mark.parametrize("prefix", ["Here is the action:\n", "analysis: "])
def test_h2_does_not_salvage_natural_language_prefix(prefix):
    raw = prefix + json.dumps(ACTION)
    events = collect(adapter(lambda req: public_response(raw, "openai-compatible", False)), request())
    assert events[-1].kind == "failed" and events[-1].data["code"] == "contract_error"
    assert not any(event.kind == "action_candidate" for event in events)


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("raw", [
    "Explanation\n```json\n{}\n```",
    "```json\n{}\n```\nExplanation",
    "```python\n{}\n```",
    "````json\n{}\n````",
    "```json\n```json\n{}\n```\n```",
    "```json\n{}\n```\n```json\n{}\n```",
    "```json\n{} {{}}\n```",
    "```json\n{}\n<result>fabricated</result>\n```",
    "```json\n[]\n```",
    '```json\n{{"type":"final","output":{{}}\n```',
    '```json\n{{"type":"tool_call","tool_name":"article.list","arguments":[]}}\n```',
    '```json\n{{"type":"final","output":{{"answer":"inner ``` fence"}}}}\n```',
    "```json {} ```",
])
def test_h2_single_fence_does_not_salvage_invalid_envelopes_or_actions(protocol, raw):
    text = raw.format(json.dumps(ACTION), json.dumps(ACTION))
    events = collect(adapter(lambda req: public_response(text, protocol, False)), request(snapshot(protocol)))
    assert events[-1].kind == "failed" and events[-1].data["code"] == "contract_error"
    assert not any(event.kind == "action_candidate" for event in events)
    assert events[1].data["text"] == text


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_fenced_action_without_stream_terminal_is_never_exposed(protocol):
    raw = "```json\n" + json.dumps(ACTION) + "\n```"
    events = collect(adapter(lambda req: public_response(raw, protocol, True, complete=False)),
                     request(snapshot(protocol, True)))
    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].data["code"] == "transport_error"


def test_fence_does_not_relax_finance_tool_parameter_validation():
    from pydantic import ValidationError

    from policy_impact.dispatch.finance_tool_contracts import validate

    action = {"type": "tool_call", "tool_name": "article.list", "arguments": {"publication_date": "2026-09-30"}}
    raw = "```json\n" + json.dumps(action) + "\n```"
    events = collect(adapter(lambda req: public_response(raw, "openai-compatible", False)), request())
    assert events[-1].kind == "completed"
    with pytest.raises(ValidationError):
        validate(events[-2].data["payload"]["tool_name"], events[-2].data["payload"]["arguments"])


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fenced", [False, True])
def test_h2_raw_string_controls_normalize_only_complete_public_actions(protocol, stream, fenced):
    raw = '{"type":"final","output":{"answer":"first\nsecond\tthird"}}'
    if fenced:
        raw = "```json\n" + raw + "\n```"
    sent = []

    def http(req):
        sent.append(req)
        return public_response(raw, protocol, stream)

    events = collect(adapter(http), request(snapshot(protocol, stream)))
    assert [event.kind for event in events] == ["started", "text_delta", "usage", "action_candidate", "completed"]
    assert events[1].data["text"] == raw
    assert events[-2].data["payload"] == {"type": "final", "output": {"answer": "first\nsecond\tthird"}}
    assert events[-1].data["public_output_normalization"] == {"json_string_controls": 2}
    assert len(sent) == 1


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_h2_invalid_string_escape_is_repaired_only_inside_complete_action(protocol, stream):
    raw = '{"type":"final","output":{"answer":"C:\\q"}}'
    events = collect(
        adapter(lambda req: public_response(raw, protocol, stream)),
        request(snapshot(protocol, stream)),
    )
    assert events[-2].data["payload"] == {"type": "final", "output": {"answer": r"C:\q"}}
    assert events[-1].data["public_output_normalization"] == {
        "json_string_invalid_escapes": 1,
    }


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_controls_do_not_make_truncated_stream_executable(protocol):
    raw = '{"type":"final","output":{"answer":"raw\nline"}}'
    events = collect(adapter(lambda req: public_response(raw, protocol, True, complete=False)),
                     request(snapshot(protocol, True)))
    assert [event.kind for event in events] == ["started", "failed"]
    assert "public_output_normalization" not in events[-1].data


@pytest.mark.parametrize("raw", [
    '{"type":"final","output":{"answer":"raw\nline"}',
    '{"type":"final","output":{"answer":"raw\nline"},"invented":true}',
    '{"schema_version":"finance.h2.anchor_patch.v1","anchors":[],"note":"raw\nline"}',
    '{"type":"tool_call","tool_name":"article.read","arguments":[],"note":"raw\nline"}',
])
def test_control_normalization_does_not_relax_action_or_envelope_validation(raw):
    events = collect(adapter(lambda req: public_response(raw, "openai-compatible", False)), request())
    assert events[-1].kind == "failed" and events[-1].data["code"] == "contract_error"
    expected = {"json_string_controls": 1}
    if "\\q" in raw:
        expected["json_string_invalid_escapes"] = 1
    assert events[-1].data["public_output_normalization"] == expected
    assert not any(event.kind == "action_candidate" for event in events)
    assert events[1].data["text"] == raw


def test_string_controls_not_normalized_for_other_schemas():
    from dataclasses import replace

    raw = '{"type":"final","output":{"answer":"raw\nline"}}'
    req = replace(request(), schema_name="general_chat_agent_loop.v1")
    events = collect(adapter(lambda req: public_response(raw, "openai-compatible", False)), req)
    assert events[-1].kind == "failed" and events[-1].data["code"] == "contract_error"
    assert "public_output_normalization" not in events[-1].data


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("literal_control", [False, True])
def test_control_normalization_does_not_widen_existing_dsml_compatibility(protocol, literal_control):
    arguments = '{"note":"raw\nline"}' if literal_control else '{"note":"escaped\\nline"}'
    raw = (
        '<||DSML|| calls><||DSML|| invoke name="tool_call">'
        '<||DSML|| parameter name="tool_name" string="true">article.list</||DSML|| parameter>'
        '<||DSML|| parameter name="arguments" string="false">' + arguments + '</||DSML|| parameter>'
        '</||DSML|| invoke></||DSML|| calls>'
    )
    events = collect(adapter(lambda req: public_response(raw, protocol, False)), request(snapshot(protocol)))
    assert events[-1].kind == ("failed" if literal_control else "completed")
    assert "public_output_normalization" not in events[-1].data
    if literal_control:
        assert events[-1].data["code"] == "contract_error"
        assert not any(event.kind == "action_candidate" for event in events)
    assert events[1].data["text"] == raw


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_one_request_reuses_wire_contract_and_excludes_private_channels(protocol, stream):
    sent = []

    def http(req):
        sent.append(req)
        if stream:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Chunks(sse(protocol))
            )
        return httpx.Response(200, json=chat() if protocol == "openai-compatible" else responses())

    events = collect(adapter(http), request(snapshot(protocol, stream)))
    assert [e.kind for e in events] == [
        "started",
        "text_delta",
        "usage",
        "action_candidate",
        "completed",
    ]
    assert [e.seq for e in events] == list(range(1, 6))
    assert len({e.call_id for e in events}) == 1
    assert events[-2].data["payload"] == ACTION
    assert len(sent) == 1
    body = json.loads(sent[0].content)
    assert not {"tools", "tool_choice", "previous_response_id"} & body.keys()
    assert sent[0].headers["authorization"] == "Bearer " + SECRET
    if protocol == "openai-responses":
        assert body["store"] is False
        assert sent[0].url.path == "/v1/responses"
    else:
        assert sent[0].url.path == "/v1/chat/completions"
    assert SECRET not in str(events)
    assert "private reasoning" not in str(events)


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_invalid_action_emits_exact_bounded_field_diagnostic(protocol, stream):
    payload = {
        "type": "tool_call", "tool_name": "agent.wait",
        "arguments": {"task_ids": ["task-child"], "mode": "all", "timeout_ms": 60000},
        "next_action_key": SECRET,
    }
    sent = []

    def http(req):
        sent.append(req)
        if stream:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Chunks(sse(protocol, content=payload)))
        return httpx.Response(200, json=chat(payload) if protocol == "openai-compatible" else responses(payload))

    events = collect(adapter(http), request(snapshot(protocol, stream)))
    assert len(sent) == 1
    assert [event.kind for event in events] == ["started", "text_delta", "usage", "failed"]
    failed = events[-1].data
    assert failed["code"] == "contract_error" and failed["physical_status"] == "succeeded"
    assert failed["validation_stage"] == "action"
    assert failed["validation_errors"] == [{
        "loc": ["tool_call", "next_action_key"], "type": "extra_forbidden",
        "msg": "Extra inputs are not permitted",
    }]
    assert SECRET not in str(events) and "private reasoning" not in str(events)


def test_public_validation_diagnostics_are_allowlisted_redacted_and_bounded():
    error = {
        "loc": ["tool_call", SECRET, 3] + ["x" * 300] * 20,
        "type": "custom_" + "x" * 500, "msg": SECRET + "x" * 1000,
        "input": "private input", "ctx": {"error": "private context"}, "url": "private URL",
    }
    errors = public_validation_errors([error] * 40, secret=SECRET)
    assert len(errors) == 12
    assert all(set(item) == {"loc", "type", "msg"} for item in errors)
    assert all(len(item["loc"]) <= 12 and len(item["type"]) <= 80 and len(item["msg"]) <= 320 for item in errors)
    assert all(len(part) <= 160 for item in errors for part in item["loc"] if isinstance(part, str))
    assert errors[0]["loc"][:3] == ["tool_call", "[REDACTED]", 3]
    assert SECRET not in str(errors) and "private" not in str(errors)
    assert public_validation_errors(None) == []
    assert public_validation_errors([None, {"loc": {"invalid": True}}]) == []


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_eof_before_terminal_does_not_expose_action_or_text(protocol):
    a = adapter(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks(sse(protocol, complete=False)),
        )
    )
    events = collect(a, request(snapshot(protocol, True)))
    assert [e.kind for e in events] == ["started", "failed"]
    assert events[-1].data["code"] == "transport_error"
    assert events[-1].data["physical_status"] == "unknown"


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "provider_error"),
        (401, "provider_error"),
        (402, "quota_error"),
        (403, "provider_error"),
        (429, "provider_error"),
        (500, "provider_error"),
    ],
)
def test_http_failures_do_not_retry_redirect_or_echo_error_body(status, code):
    sent = []

    def http(req):
        sent.append(req)
        return httpx.Response(
            status,
            headers={"location": "https://other.invalid/"},
            text=SECRET + " private reasoning",
        )

    events = collect(adapter(http), request())
    assert len(sent) == 1
    assert events[-1].data["code"] == code
    assert not events[-1].data["retryable"]
    assert SECRET not in str(events)
    assert "private reasoning" not in str(events)


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses"])
def test_truncated_but_parseable_action_is_never_a_candidate(protocol):
    data = chat(choices=[{"finish_reason": "length", "message": {"content": json.dumps(ACTION)}}])
    if protocol == "openai-responses":
        data = responses(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    events = collect(
        adapter(lambda req: httpx.Response(200, json=data)), request(snapshot(protocol))
    )
    assert [e.kind for e in events] == ["started", "failed"]
    assert events[-1].data["code"] == "contract_error"
    assert events[-1].data["physical_status"] == "succeeded"


def test_native_tool_calls_are_not_treated_as_text_actions():
    data = chat(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": json.dumps(ACTION),
                    "tool_calls": [{"function": {"name": "agent.spawn"}}],
                },
            }
        ]
    )
    events = collect(adapter(lambda req: httpx.Response(200, json=data)), request())
    assert [e.kind for e in events] == ["started", "failed"]
    assert not events[-1].data["retryable"]


def test_invalid_public_json_preserves_text_but_never_emits_action():
    events = collect(
        adapter(
            lambda req: httpx.Response(
                200,
                json=chat(
                    choices=[
                        {"finish_reason": "stop", "message": {"content": "not json " + SECRET}}
                    ]
                ),
            )
        ),
        request(),
    )
    assert [e.kind for e in events] == ["started", "text_delta", "usage", "failed"]
    assert events[-1].data["code"] == "contract_error"
    assert events[-1].data["physical_status"] == "succeeded"
    assert SECRET not in str(events)


@pytest.mark.parametrize("kind", ["missing_key", "context", "settings", "profile", "messages"])
def test_preflight_refusal_sends_nothing(kind):
    sent = []
    model = snapshot()
    credential = SECRET
    if kind == "missing_key":
        credential = ""
    if kind == "context":
        model["parameters"] = {"context_tokens": 512}
    if kind == "settings":
        model["parameters"] = {"max_attempts": 5}
    if kind == "profile":
        model["base_url"] = "https://user:password@fixture.invalid"
    with pytest.raises(DispatchError):
        req = request(model)
        if kind == "messages":
            req.messages[0]["role"] = []
        collect(adapter(lambda r: sent.append(r), credential=credential), req)
    assert sent == []


def test_total_timeout_cancels_underlying_request_without_retry():
    cancelled, sent = [], []

    async def http(req):
        sent.append(req)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    events = collect(adapter(http), request(remaining=0.03))
    assert len(sent) == 1 and cancelled == [True]
    assert events[-1].data == {
        "code": "timeout",
        "retryable": False,
        "physical_status": "unknown",
        "usage": {},
        "response_id": "",
        "provider_request_id": "",
    }


def test_explicit_adapter_cancel_is_local_and_has_no_candidate():
    async def run():
        entered, aborted = asyncio.Event(), asyncio.Event()

        async def http(req):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                aborted.set()

        a, req = adapter(http), request()

        async def consume():
            return [event async for event in a.request(req)]

        execution = asyncio.create_task(consume())
        await entered.wait()
        assert await a.cancel(req.call_id) == "abort_requested"
        events = await execution
        assert aborted.is_set()
        assert [e.kind for e in events] == ["started", "failed"]
        assert events[-1].data["code"] == "cancelled"
        assert await a.cancel(req.call_id) == "already_finished"

    asyncio.run(run())


@pytest.fixture
def runtime(tmp_path):
    config = SchedulerConfig(
        budget_policy="hierarchical",
        stream_name="h2:execution:fixture", run_concurrency=1, worker_concurrency=1, max_attempts=2
    )
    model = snapshot()
    model.pop("profile")
    repo = DispatchRepository(
        tmp_path / "h2.sqlite",
        tmp_path / "records",
        profiles={"fixture": model},
        roles={"analyst@2.0.0": TOOLS},
        config=config,
    )
    repo.migrate()
    root = repo.create_run(
        run_id="fixture",
        session_ref="session",
        query="coordinate articles",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), config.stream_name)
    return repo, root, user, transport


def claimed(runtime):
    repo, root, _, _ = runtime
    with repo.db.connection() as c:
        payload = json.loads(
            c.execute(
                "SELECT payload_json FROM h2_outbox WHERE task_id=?", (root["task_id"],)
            ).fetchone()[0]
        )
    lease, _ = repo.claim(payload, "manual")
    repo.start(lease)
    work = WorkInput(
        lease.run_id,
        lease.task_id,
        lease.thread_id,
        lease.attempt_id,
        **repo.execution_input(lease),
    )
    return lease, work


def ledger(repo):
    with repo.db.connection() as c:
        return [dict(row) for row in c.execute("SELECT * FROM h2_model_calls ORDER BY rowid")]


def spawn_arguments(key):
    return {
        "dispatch_key": key,
        "role": "analyst",
        "role_version": "2.0.0",
        "objective": "analyze " + key,
        "success_criteria": ["preserve original conditions"],
        "input_refs": [],
        "output_contract": {"name": "fixture.article", "version": "1"},
        "requested_budget": {"model_calls": 30, "wall_time_ms": 600000},
    }


def checkpoint(context_ref, sequence=1):
    return CheckpointState(
        current_phase="waiting",
        completed_steps=["spawn"],
        pending_steps=["synthesis"],
        input_refs=[],
        context_ref=context_ref,
        consumed_message_seq=sequence,
        last_event_id="0",
    )


def test_success_has_one_physical_charge_and_scoped_public_output(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: httpx.Response(200, json=chat()))
    )
    result = asyncio.run(
        session.complete([{"role": "user", "content": "question"}], "agent_action.v1")
    )
    assert result.payload == ACTION
    rows = ledger(repo)
    assert len(rows) == 1 and rows[0]["status"] == "succeeded"
    assert rows[0]["error_code"] is None
    assert SECRET not in str(rows)
    ref = result.metadata["output_ref"]
    assert json.loads(repo.read_input(lease.caller(), ref)) == ACTION
    assert "lease_token" not in json.dumps(asdict(work))
    expected_usage = {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
    assert json.loads(rows[0]["usage_json"]) == expected_usage
    exported = repo.export_run_records(lease.run_id)
    for name in ("model_calls", "trace"):
        records = [json.loads(line) for line in Path(exported["files"][name]).read_text(encoding="utf-8").splitlines()]
        call = next(row for row in records if row.get("call_id") == rows[0]["call_id"])
        assert call["usage"] == expected_usage
        assert call["status"] == "succeeded" and call["error_code"] is None


@pytest.mark.parametrize("invalid", [False, True])
def test_normalization_is_audited_with_raw_output_and_one_charge(runtime, invalid):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    raw = '{"type":"final","output":{"answer":"raw\nline"}' + (',"extra":true}' if invalid else '}')
    sent = []

    def http(req):
        sent.append(req)
        return public_response(raw, "openai-compatible", False)

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    if invalid:
        with pytest.raises(DispatchError, match="contract_error") as caught:
            asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
        output_ref = caught.value.details["output_ref"]
    else:
        result = asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
        assert result.metadata["public_output_normalization"] == {"json_string_controls": 1}
        assert result.payload["output"]["answer"] == "raw\nline"
        output_ref = result.metadata["output_ref"]
    assert repo.read_input(lease.caller(), output_ref).decode() == raw
    assert len(sent) == len(ledger(repo)) == 1
    assert ledger(repo)[0]["status"] == "succeeded"
    assert ledger(repo)[0]["error_code"] == ("contract_error" if invalid else None)
    with repo.db.connection() as c:
        events = c.execute("SELECT payload_json FROM h2_events WHERE event_type='model.call_finished'").fetchall()
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries WHERE kind='charge'").fetchone()[0] == -1
    assert len(events) == 1
    event = json.loads(events[0][0])
    assert event["output_ref"] == output_ref
    assert event["public_output_normalization"] == {"json_string_controls": 1}


@pytest.mark.parametrize("value", [None, True, False, "1", -1, 0, 1.5, 1_000_001, {"secret": SECRET}])
def test_repository_ignores_invalid_normalization_metadata(runtime, value):
    repo, _, _, _ = runtime
    lease, _ = claimed(runtime)
    call_id = "call_" + uuid4().hex
    repo.reserve_call(lease, call_id)
    repo.call_started(lease, call_id)
    repo.call_finished(lease, call_id, status="succeeded", public_output_normalization={
        "json_string_controls": value, "json_string_invalid_escapes": 0, "secret": SECRET,
    })
    with repo.db.connection() as c:
        event = json.loads(c.execute("SELECT payload_json FROM h2_events WHERE event_type='model.call_finished'").fetchone()[0])
    assert "public_output_normalization" not in event and SECRET not in str(event)


@pytest.mark.parametrize("url", [
    "https://example.invalid/news?page=2",
    "https://example.invalid/news?section=markets&date=2026-10-20",
    "https://example.invalid/news?cursor=next-page",
])
def test_secret_scan_allows_ordinary_public_url_query_parameters(url):
    DispatchRepository._no_secrets({"source_url": url, "text": "public page"})


@pytest.mark.parametrize("url", [
    "https://example.invalid/news?api_key=public-looking-value",
    "https://example.invalid/news?access_token=public-looking-value",
    "https://example.invalid/news?client-secret=public-looking-value",
    "https://user:password@example.invalid/news?page=2",
])
def test_secret_scan_still_rejects_credential_url_parameters(url):
    with pytest.raises(ValueError, match="credential-bearing"):
        DispatchRepository._no_secrets({"source_url": url})


def test_capability_and_repository_copy_only_known_normalization_count(runtime):
    from policy_impact.dispatch.model_execution import ModelEvent

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)

    class CountAdapter:
        settings = staticmethod(GatewayModelAdapter.settings)

        async def request(self, req):
            yield ModelEvent(req.call_id, 1, "started")
            yield ModelEvent(req.call_id, 2, "text_delta", {"text": json.dumps(ACTION)})
            yield ModelEvent(req.call_id, 3, "action_candidate", {"payload": ACTION})
            yield ModelEvent(req.call_id, 4, "completed", {
                "public_output_normalization": {
                    "json_string_controls": 7, "json_string_invalid_escapes": 2,
                    "secret": SECRET,
                },
                "private_reasoning": SECRET,
            })

    session = ExecutionCapabilities(repo, lease, work, model_adapter=CountAdapter())
    result = asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert result.metadata["public_output_normalization"] == {
        "json_string_controls": 7, "json_string_invalid_escapes": 2,
    }
    assert SECRET not in str(result.metadata)
    with repo.db.connection() as c:
        event = json.loads(c.execute("SELECT payload_json FROM h2_events WHERE event_type='model.call_finished'").fetchone()[0])
        assert event["public_output_normalization"] == {
            "json_string_controls": 7, "json_string_invalid_escapes": 2,
        }
    assert SECRET not in str(event)


def test_normalized_research_prose_still_uses_immutable_anchor_correction(runtime):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.dispatch.finance_anchor_repair import ResearchAnchorRepair
    from policy_impact.runtime.research_contracts import ResearchResult

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    output = {
        "schema_version": "finance.research_result.v1", "content": "Claim.\nStill conditional.",
        "evidence_links": [{"text": "Not verbatim", "evidence_refs": ["art_fixture"]}],
        "material_disposition": [], "gaps": [], "limitations": [],
    }
    original = {"type": "final", "output": output}
    raw = json.dumps(original).replace("\\n", "\n")
    sent = []

    def http(req):
        sent.append(json.loads(req.content))
        if len(sent) == 1:
            return public_response(raw, "openai-compatible", False)
        feedback = json.loads(sent[-1]["messages"][-1]["content"])
        assert feedback["input_policy"] == "finance.h2.immutable_anchor_correction.v1"
        return httpx.Response(200, json=chat({"type": "final", "output": {
            "schema_version": "finance.h2.anchor_patch.v1", "anchors": [{"link_index": 0, "text": "Claim."}],
        }}))

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    response, repair_ref = asyncio.run(FinanceH2Domain._decide(
        [{"role": "user", "content": "fixture task"}], session,
        final_output_model=ResearchResult, final_repair_factory=ResearchAnchorRepair.prepare,
    ))
    expected = {**output, "evidence_links": [{"text": "Claim.", "evidence_refs": ["art_fixture"]}]}
    assert response.payload == {"type": "final", "output": expected}
    assert len(sent) == len(ledger(repo)) == 2 and repair_ref
    with repo.db.connection() as c:
        events = [json.loads(row[0]) for row in c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='model.call_finished' ORDER BY event_id",
        )]
    assert events[0]["public_output_normalization"] == {"json_string_controls": 1}
    assert repo.read_input(lease.caller(), events[0]["output_ref"]).decode() == raw
    assert "public_output_normalization" not in events[1]


def test_normalization_does_not_grant_scheduler_tool_permissions(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    raw = '{"type":"tool_call","tool_name":"not.granted","arguments":{"note":"raw\nline"}}'
    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(
        lambda req: public_response(raw, "openai-compatible", False),
    ))

    async def run():
        result = await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        with pytest.raises(DispatchError, match="forbidden"):
            await session.tool(result.payload["tool_name"], result.payload["arguments"])

    asyncio.run(run())
    assert len(ledger(repo)) == 1


def test_normalization_does_not_relax_business_tool_parameters():
    from pydantic import ValidationError

    from policy_impact.dispatch.finance_tool_contracts import validate

    raw = '{"type":"tool_call","tool_name":"article.list","arguments":{"publication_date":"raw\nline"}}'
    events = collect(adapter(lambda req: public_response(raw, "openai-compatible", False)), request())
    assert events[-1].kind == "completed"
    action = events[-2].data["payload"]
    with pytest.raises(ValidationError):
        validate(action["tool_name"], action["arguments"])


def test_call_finish_normalization_event_is_atomic_with_settlement(runtime, monkeypatch):
    repo, _, _, _ = runtime
    lease, _ = claimed(runtime)
    call_id = "call_" + uuid4().hex
    repo.reserve_call(lease, call_id)
    repo.call_started(lease, call_id)
    event = repo._event

    def fail(c, task, name, payload):
        if name == "model.call_finished":
            raise sqlite3.OperationalError("synthetic event persistence failure")
        return event(c, task, name, payload)

    monkeypatch.setattr(repo, "_event", fail)
    with pytest.raises(sqlite3.OperationalError):
        repo.call_finished(lease, call_id, status="succeeded", public_output_normalization={
            "json_string_controls": 1,
        })
    assert ledger(repo)[0]["status"] == "started"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_events WHERE event_type='model.call_finished'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0


def test_invalid_action_is_completed_physical_request_with_contract_failure(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    data = chat(
        {"type": "tool_call", "tool_name": "agent.spawn", "arguments": [], "secret": SECRET}
    )
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: httpx.Response(200, json=data))
    )
    with pytest.raises(DispatchError, match="contract_error"):
        asyncio.run(session.complete([{"role": "user", "content": "question"}], "agent_action.v1"))
    row = ledger(repo)[0]
    assert row["status"] == "succeeded" and row["error_code"] == "contract_error"
    with repo.db.connection() as c:
        event = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='model.call_finished'"
        ).fetchone()[0]
    ref = json.loads(event)["output_ref"]
    assert ref and SECRET not in repo.read_input(lease.caller(), ref).decode()
    expected_usage = json.loads(row["usage_json"])
    assert expected_usage == {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
    exported = repo.export_run_records(lease.run_id)
    for name in ("model_calls", "trace"):
        records = [json.loads(line) for line in Path(exported["files"][name]).read_text(encoding="utf-8").splitlines()]
        call = next(item for item in records if item.get("call_id") == row["call_id"])
        assert call["usage"] == expected_usage
        assert call["status"] == "succeeded" and call["error_code"] == "contract_error"


def test_capability_preserves_only_allowed_action_failure_details(runtime):
    from policy_impact.dispatch.model_execution import ModelEvent

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)

    class DiagnosticAdapter:
        settings = staticmethod(GatewayModelAdapter.settings)

        async def request(self, req):
            yield ModelEvent(req.call_id, 1, "started")
            yield ModelEvent(req.call_id, 2, "text_delta", {"text": '{"type":"tool_call"}'})
            yield ModelEvent(req.call_id, 3, "failed", {
                "code": "contract_error", "physical_status": "succeeded",
                "validation_stage": "action", "validation_errors": [{
                    "loc": ["tool_call", "next_action_key"], "type": "extra_forbidden",
                    "msg": "Extra inputs are not permitted", "input": SECRET, "ctx": {"error": SECRET},
                }],
                "private_reasoning": "not public", "lease_token": SECRET,
            })

    session = ExecutionCapabilities(repo, lease, work, model_adapter=DiagnosticAdapter())
    with pytest.raises(DispatchError) as caught:
        asyncio.run(session.complete([{"role": "user", "content": "question"}], "agent_action.v1"))
    details = caught.value.details
    assert set(details) == {"call_id", "output_ref", "validation_stage", "validation_errors"}
    assert details["validation_stage"] == "action"
    assert details["validation_errors"] == [{
        "loc": ["tool_call", "next_action_key"], "type": "extra_forbidden",
        "msg": "Extra inputs are not permitted",
    }]
    assert SECRET not in json.dumps(details)
    assert ledger(repo)[0]["error_code"] == "contract_error"
    assert ledger(repo)[0]["status"] == "succeeded"


@pytest.mark.parametrize("corrected", [True, False])
def test_root_wait_correction_receives_actual_field_error_without_third_request(runtime, corrected):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    valid = {
        "type": "tool_call", "tool_name": "agent.wait",
        "arguments": {"task_ids": ["task-child"], "mode": "all", "timeout_ms": 60000},
    }
    invalid = {**valid, "next_action_key": "input-only-key"}
    sent = []

    def http(req):
        body = json.loads(req.content)
        sent.append(body)
        if len(sent) == 2:
            feedback = json.loads(body["messages"][-1]["content"])
            assert feedback["validation_stage"] == "action"
            assert feedback["validation_errors"] == [{
                "loc": ["tool_call", "next_action_key"], "type": "extra_forbidden",
                "msg": "Extra inputs are not permitted",
            }]
            assert "failed_public_response" not in feedback
            assert "input-only-key" not in json.dumps(feedback)
            assert feedback["input_policy"] == "finance.h2.isolated_correction.v1"
            assert feedback["action_json_schema"]["discriminator"]["propertyName"] == "type"
        return httpx.Response(200, json=chat(valid if len(sent) == 2 and corrected else invalid))

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    decision = FinanceH2Domain._decide([{"role": "user", "content": "wait for child"}], session)
    if corrected:
        response, repair_ref = asyncio.run(decision)
        assert response.payload["tool_name"] == "agent.wait"
        assert "next_action_key" not in response.payload
        repair = json.loads(repo.read_input(lease.caller(), repair_ref))
        diagnostic = json.loads(repo.read_input(lease.caller(), repair["request_ref"]))
        assert diagnostic["validation_errors"][0]["loc"] == ["tool_call", "next_action_key"]
        assert json.loads(repo.read_input(lease.caller(), diagnostic["failed_output_ref"])) == invalid
    else:
        with pytest.raises(DispatchError, match="contract_error"):
            asyncio.run(decision)
    rows = ledger(repo)
    assert len(sent) == len(rows) == 2
    assert all(row["attempt_id"] == lease.attempt_id and row["status"] == "succeeded" for row in rows)
    assert sum(row["error_code"] == "contract_error" for row in rows) == (1 if corrected else 2)
    with repo.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM h2_waits").fetchone()[0] == 0
        assert connection.execute("SELECT sum(delta_calls) FROM h2_budget_entries WHERE kind='charge'").fetchone()[0] == -2


@pytest.mark.parametrize("corrected", [True, False])
def test_current_rule_five_corrections_are_individually_charged_in_same_attempt(runtime, corrected):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.dispatch.finance_rules import load_rules

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    work.brief["instruction_rules"] = load_rules().snapshot()
    invalid = {**ACTION, "next_action_key": "input-only-key"}
    sent = []

    def http(req):
        body = json.loads(req.content)
        sent.append(body)
        if len(sent) > 1:
            feedback = json.loads(body["messages"][-1]["content"])
            assert feedback["correction_attempt"] == len(sent) - 1
            assert feedback["correction_limit"] == 5
            assert len(body["messages"]) == 2
        return httpx.Response(200, json=chat(ACTION if corrected and len(sent) == 6 else invalid))

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    decision = FinanceH2Domain._decide([{"role": "user", "content": "Original research task."}], session)
    if corrected:
        response, ref = asyncio.run(decision)
        assert response.payload == ACTION
    else:
        with pytest.raises(DispatchError) as caught:
            asyncio.run(decision)
        assert caught.value.code == "contract_error" and not caught.value.retryable
        ref = caught.value.details["action_repair_ref"]
    summary = json.loads(repo.read_input(lease.caller(), ref))
    assert summary["limit"] == summary["correction_count"] == 5
    assert len(summary["request_refs"]) == len(summary["outcome_refs"]) == 5
    rows = ledger(repo)
    assert len(sent) == len(rows) == 6
    assert {row["attempt_id"] for row in rows} == {lease.attempt_id}
    assert len({row["model_snapshot_ref"] for row in rows}) == 1
    assert all(row["status"] == "succeeded" for row in rows)
    assert sum(row["error_code"] == "contract_error" for row in rows) == (5 if corrected else 6)
    with repo.db.connection() as c:
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries WHERE kind='charge'").fetchone()[0] == -6
        outputs = c.execute("SELECT payload_json FROM h2_events WHERE event_type='model.call_finished'").fetchall()
        assert len(outputs) == 6 and all(json.loads(row[0])["output_ref"] for row in outputs)


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("literal_control", [False, True])
def test_fenced_root_or_child_final_uses_one_charge_and_retains_public_fence(runtime, research, literal_control):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.runtime.research_contracts import ResearchResult

    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    output = {
        "schema_version": "finance.research_result.v1", "content": "No source was supplied.",
        "evidence_links": [], "material_disposition": [], "gaps": [], "limitations": [],
    } if research else ACTION["output"]
    if literal_control:
        field = "content" if research else "answer"
        output = {**output, field: output[field] + "\nStill conditional."}
    raw = "```json\n" + json.dumps({"type": "final", "output": output}) + "\n```"
    if literal_control:
        raw = raw.replace("\\n", "\n")
    sent = []

    def http(req):
        sent.append(req)
        return public_response(raw, "openai-compatible", False)

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    response, repair = asyncio.run(FinanceH2Domain._decide(
        [{"role": "user", "content": "fixture task"}], session,
        final_output_model=ResearchResult if research else None,
    ))
    assert response.payload["output"] == output and repair is None
    assert repo.read_input(lease.caller(), response.metadata["output_ref"]).decode() == raw
    assert len(sent) == len(ledger(repo)) == 1
    assert ledger(repo)[0]["error_code"] is None
    assert response.metadata.get("public_output_normalization") == (
        {"json_string_controls": 1} if literal_control else None
    )


def test_missing_key_is_not_charged(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: None, credential="")
    )
    with pytest.raises(DispatchError, match="credential unavailable"):
        asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert ledger(repo) == []


def test_cancel_during_reservation_sends_nothing(runtime, monkeypatch):
    repo, root, user, _ = runtime
    lease, work = claimed(runtime)
    calls = []
    original = repo.reserve_call

    def reserve(*args, **kwargs):
        result = original(*args, **kwargs)
        repo.cancel(user, Cancel(task_id=root["task_id"], idempotency_key="cancel-root"))
        return result

    monkeypatch.setattr(repo, "reserve_call", reserve)
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: calls.append(req))
    )
    with pytest.raises(DispatchError):
        asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert calls == []
    row = ledger(repo)[0]
    assert row["status"] == "failed" and row["error_code"] == "not_sent"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 1
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 600


def test_lease_loss_before_start_refunds_confirmed_unsent_call(runtime, monkeypatch):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    calls, validations = [], []
    original = repo.validate_lease

    def validate(value):
        validations.append(True)
        if len(validations) == 2:
            raise DispatchError("stale_lease")
        return original(value)

    monkeypatch.setattr(repo, "validate_lease", validate)
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: calls.append(req))
    )
    with pytest.raises(DispatchError, match="stale_lease"):
        asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert calls == []
    row = ledger(repo)[0]
    assert row["status"] == "failed" and row["error_code"] == "not_sent"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("agent.spawn", {**spawn_arguments("a"), "lease_token": "forged"}),
        ("agent.spawn", {k: v for k, v in spawn_arguments("a").items() if k != "dispatch_key"}),
        ("agent.status", {"task_ids": ["unknown"], "max_events": "2"}),
        ("result.get", {"result_ref": "unknown", "file_path": "C:/private"}),
        ("agent.wait", {"task_ids": ["unknown"]}),
    ],
)
def test_scoped_tool_validation_does_not_invent_keys_or_accept_internal_fields(
    runtime, name, arguments
):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    session = ExecutionCapabilities(repo, lease, work)
    with pytest.raises(DispatchError) as error:
        asyncio.run(session.tool(name, arguments))
    assert error.value.code == "invalid_arguments"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 1


def test_executor_does_not_expose_user_model_policy_or_fallback(runtime):
    repo, root, _, _ = runtime
    lease, work = claimed(runtime)
    session = ExecutionCapabilities(repo, lease, work)
    for name in ("run.model_policy.set", "unknown"):
        with pytest.raises(DispatchError, match="forbidden"):
            asyncio.run(session.describe(name))
    with pytest.raises(DispatchError) as error:
        asyncio.run(
            session.tool(
                "agent.resume",
                {
                    "task_id": root["task_id"],
                    "idempotency_key": "resume",
                    "model_profile": "fixture",
                },
            )
        )
    assert error.value.code == "forbidden"


def test_user_policy_revocation_blocks_next_physical_call_without_fallback(runtime):
    repo, _, user, _ = runtime
    lease, work = claimed(runtime)
    repo.profiles["other"] = {**repo.profiles["fixture"], "model_id": "other-model"}
    repo.set_model_policy(
        user,
        ModelPolicySet(
            idempotency_key="policy-update",
            expected_policy_ref=work.model["policy_ref"],
            selected_profile="other",
            allowed_profiles=["other"],
        ),
    )
    sent = []
    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(lambda req: sent.append(req))
    )
    with pytest.raises(DispatchError, match="forbidden"):
        asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert sent == [] and ledger(repo) == []


@pytest.mark.parametrize("target", ["root", "child"])
@pytest.mark.parametrize("boundary", ["before_reserve", "after_reserve", "after_one"])
def test_worker_model_revocation_preserves_permission_cause_and_unsent_accounting(
    runtime, monkeypatch, target, boundary
):
    repo, root, user, transport = runtime
    task = root if target == "root" else repo.spawn(
        user, Spawn.model_validate(spawn_arguments("revocation-child"))
    )["data"]
    repo.profiles["other"] = {**repo.profiles["fixture"], "model_id": "other-model"}
    sent = []

    def revoke():
        with repo.db.connection() as c:
            policy_ref = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
        repo.set_model_policy(user, ModelPolicySet(
            idempotency_key="revoke-original",
            expected_policy_ref=policy_ref,
            selected_profile="other",
            allowed_profiles=["other"],
        ))

    original_reserve = repo.reserve_call

    def reserve(*args, **kwargs):
        result = original_reserve(*args, **kwargs)
        revoke()
        return result

    if boundary == "after_reserve":
        monkeypatch.setattr(repo, "reserve_call", reserve)

    def http(req):
        sent.append(req)
        return httpx.Response(200, json=chat())

    async def execute(work, session):
        assert work.task_id == task["task_id"]
        if boundary == "before_reserve":
            revoke()
        for index in range(2):
            await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
            if boundary == "after_one" and index == 0:
                revoke()
        pytest.fail("revocation must stop execution before the next physical request")

    async def run():
        worker = Worker(repo, transport, "worker", executor=execute, model_adapter=adapter(http))
        Dispatcher(repo, transport).publish_batch()
        entries = transport.receive(worker.consumer, count=2, block_ms=0)
        entry_id, payload = next(item for item in entries if item[1]["task_id"] == task["task_id"])
        assert await worker.process(entry_id, payload)
        assert repo.delivery_settled(payload)
        assert entry_id not in {
            item["message_id"] for item in transport.client.xpending_range(
                transport.stream, transport.group, min="-", max="+", count=10
            )
        }

    asyncio.run(run())
    status = repo.status(user, [task["task_id"]])["tasks"][0]
    expected_calls = int(boundary == "after_one")
    assert len(sent) == expected_calls
    assert status["status"] == "failed" and status["result_ref"] is None
    assert len(status["attempts"]) == 1
    assert status["attempts"][0]["error_code"] == "forbidden"
    assert all(json.loads(req.content)["model"] == "fixture-model" for req in sent)
    calls = ledger(repo)
    assert len(calls) == int(boundary != "before_reserve")
    if calls:
        assert calls[0]["status"] == ("failed" if boundary == "after_reserve" else "succeeded")
        assert calls[0]["error_code"] == ("not_sent" if boundary == "after_reserve" else None)
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == int(
            boundary == "after_reserve"
        )
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 600 - expected_calls
        event = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='task.failed' AND aggregate_id=?",
            (task["task_id"],),
        ).fetchone()
        assert json.loads(event[0])["error_code"] == "forbidden"
    if target == "child":
        waited = repo.wait_snapshot(user, Wait(task_ids=[task["task_id"]], timeout_ms=0))
        assert waited["condition_met"] is True
        assert waited["tasks"][0]["attempts"][0]["error_code"] == "forbidden"
        assert repo.status(user, [root["task_id"]])["tasks"][0]["status"] == "queued"


@pytest.mark.parametrize("target", ["root", "child"])
def test_default_model_change_preserves_original_allowed_attempt_snapshot(runtime, target):
    repo, root, user, transport = runtime
    task = root if target == "root" else repo.spawn(
        user, Spawn.model_validate(spawn_arguments("allowed-original-child"))
    )["data"]
    repo.profiles["other"] = {**repo.profiles["fixture"], "model_id": "other-model"}
    sent = []

    def http(req):
        sent.append(req)
        return httpx.Response(200, json=chat())

    async def execute(work, session):
        with repo.db.connection() as c:
            policy_ref = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
        repo.set_model_policy(user, ModelPolicySet(
            idempotency_key="change-default-only",
            expected_policy_ref=policy_ref,
            selected_profile="other",
            allowed_profiles=["fixture", "other"],
        ))
        response = await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        return Submission(result_key="final", content=response.payload["output"]["answer"], summary="Result")

    async def run():
        worker = Worker(repo, transport, "worker", executor=execute, model_adapter=adapter(http))
        Dispatcher(repo, transport).publish_batch()
        entry_id, payload = next(
            item for item in transport.receive(worker.consumer, count=2, block_ms=0)
            if item[1]["task_id"] == task["task_id"]
        )
        assert await worker.process(entry_id, payload)

    asyncio.run(run())
    status = repo.status(user, [task["task_id"]])["tasks"][0]
    assert status["status"] == "succeeded"
    assert len(status["attempts"]) == len(sent) == len(ledger(repo)) == 1
    assert json.loads(sent[0].content)["model"] == "fixture-model"
    assert ledger(repo)[0]["model_snapshot_ref"] == status["attempts"][0]["model_snapshot_ref"]


@pytest.mark.parametrize("target", ["root", "child"])
def test_revocation_after_request_start_keeps_original_inflight_charge(runtime, target):
    repo, root, user, transport = runtime
    task = root if target == "root" else repo.spawn(
        user, Spawn.model_validate(spawn_arguments("inflight-child"))
    )["data"]
    repo.profiles["other"] = {**repo.profiles["fixture"], "model_id": "other-model"}
    sent = []

    def http(req):
        sent.append(req)
        with repo.db.connection() as c:
            assert c.execute("SELECT status FROM h2_model_calls").fetchone()[0] == "started"
            policy_ref = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
        repo.set_model_policy(user, ModelPolicySet(
            idempotency_key="revoke-inflight",
            expected_policy_ref=policy_ref,
            selected_profile="other",
            allowed_profiles=["other"],
        ))
        return httpx.Response(200, json=chat())

    async def execute(work, session):
        response = await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        return Submission(result_key="final", content=response.payload["output"]["answer"], summary="Result")

    async def run():
        worker = Worker(repo, transport, "worker", executor=execute, model_adapter=adapter(http))
        Dispatcher(repo, transport).publish_batch()
        entry_id, payload = next(
            item for item in transport.receive(worker.consumer, count=2, block_ms=0)
            if item[1]["task_id"] == task["task_id"]
        )
        assert await worker.process(entry_id, payload)

    asyncio.run(run())
    status = repo.status(user, [task["task_id"]])["tasks"][0]
    assert status["status"] == "succeeded"
    assert len(status["attempts"]) == len(sent) == len(ledger(repo)) == 1
    assert json.loads(sent[0].content)["model"] == "fixture-model"
    assert ledger(repo)[0]["status"] == "succeeded"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 599


@pytest.mark.parametrize("retryable", [False, True])
def test_permission_failure_is_visible_but_never_a_transport_retry(runtime, retryable):
    repo, root, user, transport = runtime

    async def execute(work):
        raise DispatchError("forbidden", retryable=retryable)

    async def run():
        Dispatcher(repo, transport).publish_batch()
        worker = Worker(repo, transport, "worker", handler=execute)
        assert await worker.run_once(block_ms=0) == 1
        assert await worker.run_once(block_ms=0) == 0

    asyncio.run(run())
    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["status"] == "failed"
    assert len(status["attempts"]) == 1
    assert status["attempts"][0]["error_code"] == "forbidden"
    assert not ledger(repo)


@pytest.mark.parametrize("stream", [False, True])
def test_responses_provider_quota_is_not_retryable_transport(stream):
    data = responses(status="failed", error={"code": "insufficient_quota", "message": SECRET})
    if stream:
        raw = (
            "data: " + json.dumps({"type": "response.failed", "response": data}) + "\n\n"
        ).encode()
        http_response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Chunks(raw)
        )
    else:
        http_response = httpx.Response(200, json=data)
    events = collect(
        adapter(lambda req: http_response), request(snapshot("openai-responses", stream))
    )
    assert events[-1].data["code"] == "quota_error"
    assert events[-1].data["retryable"] is False
    assert SECRET not in str(events)


def test_native_tool_payload_is_rejected_even_with_stop_and_public_text():
    data = chat(
        choices=[
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(ACTION),
                    "tool_calls": [{"function": {"name": "agent.spawn"}}],
                },
            }
        ]
    )
    events = collect(adapter(lambda req: httpx.Response(200, json=data)), request())
    assert [e.kind for e in events] == ["started", "failed"]


def test_relay_quota_error_in_http403_is_distinct_from_generic_forbidden():
    events = collect(
        adapter(
            lambda req: httpx.Response(
                403, json={"error": {"code": "insufficient_user_quota", "message": SECRET}}
            )
        ),
        request(),
    )
    assert events[-1].data["code"] == "quota_error"
    assert not events[-1].data["retryable"]
    assert SECRET not in str(events)


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError])
def test_transport_timeout_and_disconnection_have_distinct_categories(error):
    def http(req):
        raise error(SECRET, request=req)

    events = collect(adapter(http), request())
    assert events[-1].data["code"] == ("transport_error" if error is httpx.ReadError else "timeout")
    assert events[-1].data["physical_status"] == "unknown"
    assert SECRET not in str(events)


def test_finite_model_snapshot_and_input_reservations():
    with pytest.raises(DispatchError, match="invalid model parameter"):
        GatewayModelAdapter.settings(snapshot(call_seconds=float("nan")))
    with pytest.raises(DispatchError, match="invalid model parameter"):
        GatewayModelAdapter.settings(snapshot(call_seconds=0.5))
    settings = GatewayModelAdapter.settings(snapshot(context_tokens=64000, input_tokens=48000))
    assert settings.input_limit() == 64000 - 32768 - 4096


def test_stream_has_one_physical_id_and_no_internal_retry():
    async def run():
        sent = []
        a = adapter(lambda req: (sent.append(req), httpx.Response(200, json=chat()))[1])
        req = request()
        assert (await anext(a.request(req))).kind == "started"
        with pytest.raises(DispatchError, match="already used"):
            await anext(a.request(req))
        assert sent == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "problem,expected",
    [
        ({"code": "insufficient_quota", "message": SECRET}, "quota_error"),
        ({"code": "invalid_api_key", "message": SECRET}, "provider_error"),
    ],
)
def test_chat_sse_provider_errors_do_not_become_retryable_disconnects(problem, expected):
    raw = ("data: " + json.dumps({"error": problem}) + "\n\n").encode()
    events = collect(
        adapter(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Chunks(raw)
            )
        ),
        request(snapshot(stream=True)),
    )
    assert events[-1].data["code"] == expected
    assert not events[-1].data["retryable"]
    assert SECRET not in str(events)


def test_sse_native_tool_delta_is_rejected_despite_stop():
    raw = (
        "data: "
        + json.dumps(
            {"choices": [{"delta": {"tool_calls": [{"function": {"name": "agent.spawn"}}]}}]}
        )
        + "\n\n"
    ).encode()
    events = collect(
        adapter(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Chunks(raw)
            )
        ),
        request(snapshot(stream=True)),
    )
    assert [e.kind for e in events] == ["started", "failed"]
    assert events[-1].data["code"] == "provider_error"


def test_explicit_named_profile_selection_contains_metadata_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECRET)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    profiles = profiles_from_registry(["gpt-responses", "deepseek"])
    assert set(profiles) == {"gpt-responses", "deepseek"}
    assert profiles["deepseek"]["thinking"] is True
    assert profiles["deepseek"]["reasoning_effort"] == "high"
    assert profiles["gpt-responses"]["protocol"] == "openai-responses"
    assert SECRET not in json.dumps(profiles)
    with pytest.raises(DispatchError):
        profiles_from_registry(["unknown"])


def test_handler_mutation_cannot_change_bound_model_snapshot(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    sent = []

    def http(req):
        sent.append(req)
        return httpx.Response(200, json=chat())

    session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
    work.model["base_url"] = "https://other.invalid"
    work.model["model_id"] = "other-model"
    asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert sent[0].url.host == "fixture.invalid"
    assert json.loads(sent[0].content)["model"] == "fixture-model"


@pytest.mark.parametrize("retryable", [False, True])
def test_worker_transport_retry_creates_new_same_model_attempt_not_hidden_call(runtime, retryable):
    repo, root, user, transport = runtime
    sent = []

    def http(req):
        sent.append(req)
        if retryable and len(sent) == 1:
            raise httpx.ReadError("fixture disconnect", request=req)
        if not retryable:
            return httpx.Response(403, json={"error": {"code": "insufficient_user_quota"}})
        return httpx.Response(200, json=chat())

    async def execute(work, session):
        response = await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        return Submission(
            result_key="final", content=response.payload["output"]["answer"], summary="result"
        )

    async def run():
        worker = Worker(repo, transport, "worker", executor=execute, model_adapter=adapter(http))
        for _ in range(3):
            Dispatcher(repo, transport).publish_batch()
            await worker.run_once(block_ms=0)

    asyncio.run(run())
    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["status"] == ("succeeded" if retryable else "failed")
    assert len(sent) == len(ledger(repo)) == (2 if retryable else 1)
    assert len(status["attempts"]) == len(sent)
    assert all(json.loads(req.content)["model"] == "fixture-model" for req in sent)
    assert ledger(repo)[0]["status"] == ("unknown" if retryable else "failed")


@pytest.mark.parametrize("boundary", ["calls", "reserved_time"])
def test_worker_preserves_budget_denial_without_another_physical_request(runtime, boundary):
    repo, root, user, transport = runtime
    clock = repo.clock()
    repo.clock = lambda: clock
    sent = []

    def http(req):
        sent.append(req)
        return httpx.Response(200, json=chat())

    with repo.db.connection(write=True) as c:
        budget = c.execute("SELECT * FROM h2_budgets WHERE parent_budget_id IS NULL").fetchone()
        if boundary == "calls":
            repo._entry(c, root["run_id"], budget["budget_id"], "fixture-spend", "charge", -574)
        else:
            clock = budget["deadline_at"] - budget["final_reserve_ms"]

    async def execute(work, session):
        for _ in range(3):
            await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        pytest.fail("a denied reservation must stop before another physical request")

    async def run():
        worker = Worker(repo, transport, "worker", executor=execute, model_adapter=adapter(http))
        Dispatcher(repo, transport).publish_batch()
        assert await worker.run_once(block_ms=0) == 1

    asyncio.run(run())
    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["status"] == "failed"
    assert status["attempts"][-1]["error_code"] == "budget_denied"
    assert len(status["attempts"]) == 1
    assert len(sent) == len(ledger(repo)) == (2 if boundary == "calls" else 0)
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0
        event = c.execute("SELECT payload_json FROM h2_events WHERE event_type='task.failed'").fetchone()
    assert json.loads(event[0])["error_code"] == "budget_denied"


def test_worker_keeps_unknown_execution_error_distinct_from_budget_denial(runtime):
    repo, root, user, transport = runtime

    async def execute(work):
        raise DispatchError("fixture_unknown_error")

    async def run():
        Dispatcher(repo, transport).publish_batch()
        assert await Worker(repo, transport, "worker", handler=execute).run_once(block_ms=0) == 1

    asyncio.run(run())
    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["attempts"][-1]["error_code"] == "internal_error"
    assert not ledger(repo)


def test_attempt_deadline_is_not_extended_by_stream_or_call_settings(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)

    async def http(req):
        await asyncio.sleep(30)
        return httpx.Response(200, json=chat())

    session = ExecutionCapabilities(
        repo, lease, work, model_adapter=adapter(http), attempt_deadline_at=repo.clock() + 200
    )
    with pytest.raises(DispatchError, match="timeout"):
        asyncio.run(session.complete([{"role": "user", "content": "q"}], "agent_action.v1"))
    assert ledger(repo)[0]["status"] == "unknown"


def test_chat_content_parts_do_not_merge_hidden_thinking_into_public_json():
    data = chat(
        choices=[
            {
                "finish_reason": "stop",
                "message": {
                    "content": [
                        {"type": "thinking", "text": "private reasoning must not escape"},
                        {"type": "text", "text": json.dumps(ACTION)},
                    ]
                },
            }
        ]
    )
    events = collect(adapter(lambda req: httpx.Response(200, json=data)), request())
    assert events[-2].data["payload"] == ACTION
    assert "private reasoning" not in str(events)


@pytest.mark.parametrize("dribble", [False, True])
def test_real_loopback_http_idle_and_continuous_stream_total_timeout_close_socket(dribble):
    async def run():
        closed = asyncio.Event()
        handlers = set()

        async def http(reader, writer):
            current = asyncio.current_task()
            handlers.add(current)
            monitor = None
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                size = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(size)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                monitor = asyncio.create_task(reader.read(1))
                if dribble:
                    while not monitor.done():
                        writer.write(b": keepalive\n\n")
                        await writer.drain()
                        await asyncio.sleep(0.01)
                await monitor
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                if monitor and not monitor.done():
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
                closed.set()
                handlers.discard(current)

        server = await asyncio.start_server(http, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            model = snapshot(stream=True, read_idle_seconds=0.05 if not dribble else 1.0)
            model["base_url"] = f"http://127.0.0.1:{port}/v1"
            a = GatewayModelAdapter(credential_resolver=lambda env: SECRET)
            req = request(model, remaining=0.25 if dribble else 3)
            events = [event async for event in a.request(req)]
            assert [e.kind for e in events] == ["started", "failed"]
            assert events[-1].data["code"] == "timeout"
            await asyncio.wait_for(closed.wait(), 3)
        finally:
            server.close()
            await server.wait_closed()
            for task in list(handlers):
                task.cancel()
            if handlers:
                await asyncio.gather(*list(handlers), return_exceptions=True)

    asyncio.run(run())


def test_context_roundtrip_and_closed_capability_refuses_further_work(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)
    session = ExecutionCapabilities(repo, lease, work)

    async def run():
        ref = await session.store_context({"phase": "analysis", "observed": ["author", "date"]})
        assert json.loads(await session.load_context(ref))["phase"] == "analysis"
        await session.checkpoint(checkpoint(ref))
        await session.close()
        with pytest.raises(DispatchError, match="stale_lease"):
            await session.mailbox()

    asyncio.run(run())


@pytest.mark.parametrize("literal_control", [False, True])
def test_model_db_failure_propagates_without_ack(runtime, monkeypatch, literal_control):
    repo, _, _, transport = runtime
    Dispatcher(repo, transport).publish_batch()

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture persistence failure")

    monkeypatch.setattr(repo, "call_finished", fail)

    async def execute(work, session):
        await session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        return Submission(result_key="never", content="must not publish", summary="no")

    worker = Worker(
        repo,
        transport,
        "worker",
        executor=execute,
        model_adapter=adapter(lambda req: public_response(
            '{"type":"final","output":{"answer":"raw\nline"}}', "openai-compatible", False,
        ) if literal_control else httpx.Response(200, json=chat())),
    )
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(worker.run_once(block_ms=0))
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 1
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 0


def test_external_cancel_stops_inflight_model_and_retains_unknown_charge(runtime):
    repo, _, _, _ = runtime
    lease, work = claimed(runtime)

    async def run():
        entered, aborted = asyncio.Event(), asyncio.Event()

        async def http(req):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                aborted.set()

        session = ExecutionCapabilities(repo, lease, work, model_adapter=adapter(http))
        call = asyncio.create_task(
            session.complete([{"role": "user", "content": "q"}], "agent_action.v1")
        )
        await entered.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert aborted.is_set()
        await session.close()

    asyncio.run(run())
    assert ledger(repo)[0]["status"] == "unknown"
    with repo.db.connection() as c:
        assert (
            c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("repeat", range(20))
def test_bound_executor_root_wait_resume_receives_both_results_with_one_slot(runtime, repeat):
    repo, root, user, transport = runtime
    inputs = []

    def http(req):
        body = json.loads(req.content)
        content = json.loads(body["messages"][-1]["content"])
        inputs.append(content)
        if content["phase"] == "spawn":
            output = {
                "type": "tool_call",
                "tool_name": "agent.spawn",
                "arguments": spawn_arguments(content["key"]),
            }
        elif content["phase"] == "child":
            output = {
                "type": "final",
                "output": {
                    "content": "full analysis " + content["objective"],
                    "summary": "summary " + content["objective"],
                },
            }
        else:
            assert len(content["observations"][0]["tasks"]) == 2
            assert all(
                t["result"]["envelope"]["summary"].startswith("summary analyze")
                for t in content["observations"][0]["tasks"]
            )
            output = ACTION
        return httpx.Response(200, json=chat(output))

    async def complete(session, content):
        return await session.complete([{"role": "user", "content": content}], "agent_action.v1")

    async def execute(work, session):
        if work.brief["depth"]:
            response = await complete(
                session, {"phase": "child", "objective": work.brief["objective"]}
            )
            return Submission(result_key="result", **response.payload["output"])
        if work.checkpoint is None:
            ids = []
            for key in ("a", "b"):
                response = await complete(session, {"phase": "spawn", "key": key})
                action = response.payload
                receipt = await session.tool(action["tool_name"], action["arguments"])
                ids.append(receipt["data"]["task_id"])
            ref = await session.store_context(
                {"child_ids": ids, "original_query": "coordinate articles"}
            )
            return await session.wait({"task_ids": ids, "mode": "all"}, checkpoint(ref))
        restored = json.loads(await session.load_context(work.checkpoint["context_ref"]))
        assert restored["original_query"] == "coordinate articles"
        for task in work.observations[0]["tasks"]:
            full = await session.tool(
                "result.get", {"result_ref": task["result_ref"], "view": "full"}
            )
            assert full["content"].startswith("full analysis analyze")
        response = await complete(
            session, {"phase": "synthesis", "observations": work.observations}
        )
        return Submission(
            result_key="final", content=response.payload["output"]["answer"], summary="synthesis"
        )

    async def run():
        dispatcher = Dispatcher(repo, transport)
        worker = Worker(repo, transport, "one-slot", executor=execute, model_adapter=adapter(http))
        for _ in range(8):
            dispatcher.publish_batch()
            await worker.run_once(block_ms=0)
            if repo.status(user, [root["task_id"]])["tasks"][0]["status"] == "succeeded":
                break

    asyncio.run(run())
    result = repo.status(user, [root["task_id"]])["tasks"][0]
    assert result["status"] == "succeeded"
    assert [a["status"] for a in result["attempts"]] == ["suspended", "succeeded"]
    assert len(inputs) == len(ledger(repo)) == 5
    assert all(row["status"] == "succeeded" for row in ledger(repo))
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM h2_attempts WHERE status IN ('queued','leased','running')"
            ).fetchone()[0]
            == 0
        )
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
