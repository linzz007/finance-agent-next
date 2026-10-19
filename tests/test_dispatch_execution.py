"""Optional Harness 2.0 execution. No live credentials, corpus or network calls."""

import asyncio
import json
import sqlite3
from contextlib import suppress
from dataclasses import asdict
from uuid import uuid4

import fakeredis
import httpx
import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Cancel, CheckpointState, ModelPolicySet, Submission
from policy_impact.dispatch.execution import ExecutionCapabilities
from policy_impact.dispatch.model_execution import (
    GatewayModelAdapter,
    ModelRequest,
    profiles_from_registry,
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


def sse(protocol, *, complete=True, content=ACTION):
    if protocol == "openai-compatible":
        events = [
            {
                "id": "provider-response",
                "choices": [{"delta": {"reasoning_content": "private reasoning must not escape"}}],
            },
            {"id": "provider-response", "choices": [{"delta": {"content": json.dumps(content)}}]},
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
            "delta": json.dumps(content),
        },
    ]
    if complete:
        events.append(
            {"type": "response.completed", "sequence_number": 3, "response": responses(content)}
        )
    return "".join("data: " + json.dumps(e) + "\r\n\r\n" for e in events).encode()


def collect(model_adapter, req):
    async def run():
        return [event async for event in model_adapter.request(req)]

    return asyncio.run(run())


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


def test_model_db_failure_propagates_without_ack(runtime, monkeypatch):
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
        model_adapter=adapter(lambda req: httpx.Response(200, json=chat())),
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
