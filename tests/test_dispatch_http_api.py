"""Offline acceptance for the opt-in Harness 2.0 HTTP boundary."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

from policy_impact.app.finance_research_app import create_app
from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import Submission
from policy_impact.dispatch.finance_root import FinanceRootContext, FinanceStep
from policy_impact.dispatch.transport import RedisStreams

TOOLS = ["agent.spawn", "agent.wait", "agent.status", "agent.send", "agent.cancel", "agent.resume", "result.get"]


@pytest.fixture
def http_harness(tmp_path: Path):
    config = SchedulerConfig(
        stream_name="finance-agent-next:fixture:http-api",
        publish_attempts=2,
        lease_ms=60000,
    )
    repository = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        config=config,
        profiles={
            "fixture": {
                "model_id": "fixture-model",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
            },
            "fixture-alt": {
                "model_id": "fixture-alt-model",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
            },
        },
        roles={},
    )
    repository.migrate()

    async def step(context: FinanceRootContext, _capabilities):
        state = context.state.model_copy(
            update={
                "phase": "delivery",
                "revision": context.state.revision + 1,
                "delivery": {"status": "partial", "disclosure": "fixture"},
            }
        )
        return FinanceStep(
            status="partial",
            state=state,
            submission=Submission(
                result_key="http-final",
                status="partial",
                content="HTTP-visible finance result",
                summary="Fixture result",
            ),
        )

    async def child(_work, _capabilities):
        return Submission(result_key="unused", content="unused")

    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True),
        config.stream_name,
        group="finance-http-api-tests",
    )
    service = FinanceHarnessV2Service(
        repository,
        transport,
        finance_step=step,
        child_executor=child,
        worker_id="finance-http-api-worker",
    )
    app = create_app(harness_v2_service=service)
    with TestClient(app) as client:
        yield client, service


def _create(client: TestClient, *, run_id="http-run", session_ref="http-session"):
    return client.post(
        "/api/harness/v2/runs",
        json={
            "run_id": run_id,
            "session_ref": session_ref,
            "query": "Explain the fixture result.",
            "selected_profile": "fixture",
            "allowed_profiles": ["fixture", "fixture-alt"],
            "tools": TOOLS,
        },
    )


def test_http_create_status_and_session_isolation(http_harness):
    client, service = http_harness
    created = _create(client)
    assert created.status_code == 202, created.text
    run = created.json()["run"]
    assert run["run_id"] == "http-run"

    handle = service.handle_for("http-run", session_ref="http-session")
    assert handle.attempt_id
    headers = {"X-Harness-Session": "http-session"}
    status = client.post(
        "/api/harness/v2/runs/http-run/status",
        headers=headers,
        json={"thread_ids": [run["thread_id"]]},
    )
    assert status.status_code == 200, status.text
    assert status.json()["tasks"][0]["task_id"] == run["task_id"]

    invalid = client.post(
        "/api/harness/v2/runs/http-run/status",
        headers=headers,
        json={},
    )
    assert invalid.status_code == 422

    isolated = client.post(
        "/api/harness/v2/runs/http-run/status",
        headers={"X-Harness-Session": "another-session"},
        json={"task_ids": [run["task_id"]]},
    )
    assert isolated.status_code == 404


def test_http_result_and_path_parameter_validation(http_harness):
    client, service = http_harness
    created = _create(client, run_id="http-result", session_ref="result-session")
    assert created.status_code == 202, created.text
    run = created.json()["run"]
    handle = service.handle_for("http-result", session_ref="result-session")
    terminal = asyncio.run(service.run_until_terminal(handle, max_cycles=10))
    result_ref = terminal["tasks"][0]["result_ref"]
    headers = {"X-Harness-Session": "result-session"}

    result = client.get(
        f"/api/harness/v2/runs/http-result/results/{result_ref}",
        headers=headers,
        params={"view": "full"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["content"] == "HTTP-visible finance result"

    send = client.post(
        f"/api/harness/v2/runs/http-result/threads/{run['thread_id']}/messages",
        headers=headers,
        json={"thread_id": "wrong-thread", "idempotency_key": "send-1", "content": "retry"},
    )
    assert send.status_code == 409

    cancel = client.post(
        f"/api/harness/v2/runs/http-result/tasks/{run['task_id']}/cancel",
        headers=headers,
        json={"task_id": "wrong-task", "idempotency_key": "cancel-1"},
    )
    assert cancel.status_code == 409

    resume = client.post(
        f"/api/harness/v2/runs/http-result/tasks/{run['task_id']}/resume",
        headers=headers,
        json={"task_id": "wrong-task", "idempotency_key": "resume-1"},
    )
    assert resume.status_code == 409


def test_http_model_policy_change_is_explicit_and_idempotent(http_harness):
    client, service = http_harness
    created = _create(client, run_id="http-policy", session_ref="policy-session")
    assert created.status_code == 202, created.text
    policy = service.repository.db.path
    with sqlite3.connect(policy) as connection:
        policy_ref = connection.execute(
            "SELECT model_policy_ref FROM h2_runs WHERE run_id=?", ("http-policy",)
        ).fetchone()[0]

    response = client.put(
        "/api/harness/v2/runs/http-policy/model-policy",
        headers={"X-Harness-Session": "policy-session"},
        json={
            "idempotency_key": "policy-1",
            "expected_policy_ref": policy_ref,
            "selected_profile": "fixture-alt",
            "allowed_profiles": ["fixture", "fixture-alt"],
            "auto_fallback": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["auto_fallback"] is False
    assert service.handle_for("http-policy", session_ref="policy-session").selected_profile == "fixture-alt"

    replay = client.put(
        "/api/harness/v2/runs/http-policy/model-policy",
        headers={"X-Harness-Session": "policy-session"},
        json={
            "idempotency_key": "policy-1",
            "expected_policy_ref": policy_ref,
            "selected_profile": "fixture-alt",
            "allowed_profiles": ["fixture", "fixture-alt"],
            "auto_fallback": False,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == response.json()["data"]
    assert replay.json()["replayed"] is True


def test_http_create_requires_an_explicit_run_id(http_harness):
    client, _service = http_harness
    response = client.post(
        "/api/harness/v2/runs",
        json={
            "session_ref": "missing-id",
            "query": "query",
            "selected_profile": "fixture",
            "allowed_profiles": ["fixture"],
            "tools": ["agent.status"],
        },
    )
    assert response.status_code == 422

