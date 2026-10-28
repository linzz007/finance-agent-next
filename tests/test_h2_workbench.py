"""No paid calls: browser entry through real H2 SQLite/Workers and HTTP fixtures."""

import json
import time

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from policy_impact.app.finance_research_app import create_app
from policy_impact.app.h2_workbench import H2Workbench
from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import Submission, Wait
from policy_impact.dispatch.finance_root import FinanceStep
from policy_impact.dispatch.model_execution import GatewayModelAdapter
from policy_impact.dispatch.transport import RedisStreams


class Corpus:
    def list_articles(self, **kwargs):
        return [
            {
                "article_id": "article_test",
                "author": "Test",
                "title": "Test article",
                "published_at": "2026-10-01",
                "current_hash": "test-hash",
            }
        ]

    def get_article(self, article_id):
        return {
            **self.list_articles()[0],
            "content": "# Test\nConditional view.\n",
            "source_url": "",
        }


@pytest.fixture
def web(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            action = {"type": "tool_call", "tool_name": "article.list", "arguments": {"limit": 5}}
        elif len(calls) == 2:
            action = {
                "type": "tool_call",
                "tool_name": "article.read",
                "arguments": {"article_id": "article_test", "start_line": 1, "limit": 100},
            }
        else:
            action = {
                "type": "final",
                "output": {
                    "answer": "Verified fixture.\n" + "abc " * 17000,
                    "gaps": ["Fixture gap"],
                },
            }
        return httpx.Response(
            200,
            json={
                "id": f"fixture-{len(calls)}",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(action)},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    repo = DispatchRepository(
        tmp_path / "h2.sqlite",
        tmp_path / "records",
        profiles={
            "fixture": {
                "model_id": "fixture",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
                "auth_env": "H2_FIXTURE_KEY",
            }
        },
        roles={},
        config=SchedulerConfig(stream_name="finance-agent-next:web:test", worker_concurrency=2),
    )
    repo.migrate()
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), repo.config.stream_name)
    service = FinanceHarnessV2Service.for_finance_corpus(
        repo,
        transport,
        corpus=Corpus(),
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _: "fixture-key", http_transport=httpx.MockTransport(handler)
        ),
    )
    wb = H2Workbench(
        service,
        transport_backend="fakeredis-local",
        default_profile="fixture",
        models=[{"id": "fixture", "name": "Fixture", "configured": True}],
    )
    return wb, calls


def submit(client, session, *, query="Explain the test article", key="same-request"):
    return client.post(
        f"/api/h2-workbench/sessions/{session}/messages",
        json={"query": query, "model_profile": "fixture", "request_id": key},
    )


def wait_answer(client, session):
    for _ in range(150):
        result = client.get(f"/api/h2-workbench/sessions/{session}").json()["runs"]
        if result and result[0]["answer"] is not None:
            return result[0]
        time.sleep(0.05)
    pytest.fail("H2 workbench did not project the terminal answer")


def test_web_path_reaches_real_h2_and_preserves_full_answer(web):
    wb, calls = web
    with TestClient(create_app(default_h2=True, h2_workbench=wb)) as client:
        assert client.get("/health").json()["runtime"] == "harness-v2"
        page = client.get("/workbench").text
        assert 'id="inspector"' in page and 'id="tab-sources"' in page
        assert 'id="query"' in page and 'id="artifact-dialog"' in page
        config = client.get("/api/h2-workbench/config").json()
        assert config["driver_running"] and config["transport_backend"] == "fakeredis-local"
        assert config["budget"] == {"root": 120, "child": 60, "run": 600, "minutes": 60}
        session = client.post("/api/h2-workbench/sessions", json={}).json()["session_id"]
        submitted = submit(client, session)
        assert submitted.status_code == 202, submitted.text
        run = wait_answer(client, session)
        assert run["status"] == "partial"
        assert len(run["answer"]) > 64000
        assert run["answer"] == ("Verified fixture.\n" + "abc " * 17000).strip()
        assert run["metadata"]["gaps"] == ["Fixture gap"]
        assert len(calls) == 3
        view = client.get(f"/api/h2-workbench/runs/{run['run_id']}?session_id={session}").json()
        assert len(view["status"]["tasks"][0]["attempts"]) == 3
        assert view["metrics"]["model_calls"]["succeeded"] == 3
        assert any(e["event_type"] == "task.continued" for e in view["status"]["events"])
        trace = view["trace"]
        assert trace["trace_id"] == run["run_id"]
        agent_spans = [span for span in trace["spans"] if span["span_type"] == "agent"]
        attempt_spans = [span for span in trace["spans"] if span["span_type"] == "attempt"]
        model_spans = [span for span in trace["spans"] if span["span_type"] == "model_call"]
        assert len(agent_spans) == 1
        assert len(attempt_spans) == len(model_spans) == 3
        assert all(span["parent_span_id"] == agent_spans[0]["span_id"] for span in attempt_spans)
        assert {span["parent_span_id"] for span in model_spans} == {
            span["span_id"] for span in attempt_spans
        }
        assert all(event["agent_path"] == ["main"] for event in trace["events"])
        assert "Explain the test article" not in json.dumps(trace)
        assert (
            client.get(f"/api/h2-workbench/sessions/{session}").json()["runs"][0]["answer"]
            == run["answer"]
        )
        assert len(calls) == 3  # Refresh is projection-only, not regeneration.
        assert (
            client.post(
                f"/companies/finance_observer/workbench/sessions/{session}/messages/stream",
                json={"message": "must not run legacy"},
            ).status_code
            == 404
        )


def test_web_post_is_idempotent_and_conflicting_replay_rejected(web):
    wb, calls = web
    # No workers: replay only validates durable creation, never calls a model.
    session = wb.create_session()["session_id"]
    first = wb.submit(session, "Question", "fixture", "key")
    second = wb.submit(session, "Question", "fixture", "key")
    assert first["run_id"] == second["run_id"]
    assert len(wb.history(session)) == 1 and not calls
    with pytest.raises(ValueError, match="idempotency_conflict"):
        wb.submit(session, "Different", "fixture", "key")


def test_existing_queued_h2_run_is_driven_after_startup(web):
    wb, calls = web
    session = wb.create_session()["session_id"]
    run = wb.submit(session, "Question", "fixture", "before-restart")
    assert not calls
    restored = H2Workbench(
        wb.service,
        transport_backend=wb.transport_backend,
        default_profile=wb.default_profile,
        models=wb.models,
    )
    with TestClient(create_app(default_h2=True, h2_workbench=restored)) as client:
        answer = wait_answer(client, session)
        assert answer["run_id"] == run["run_id"] and answer["status"] == "partial"
        assert answer["metadata"]["gaps"] == ["Fixture gap"]
        assert len(calls) == 3


def test_session_boundary_origin_check_and_cancellation(web):
    wb, _ = web
    with TestClient(create_app(default_h2=True, h2_workbench=wb)) as client:
        denied = client.post(
            "/api/h2-workbench/sessions", json={}, headers={"Origin": "https://evil.invalid"}
        )
        assert denied.status_code == 403
        assert submit(client, "unknown-session").status_code == 404
        s1 = client.post("/api/h2-workbench/sessions", json={}).json()["session_id"]
        s2 = client.post("/api/h2-workbench/sessions", json={}).json()["session_id"]
        run = submit(client, s1).json()["run_id"]
        assert client.get(f"/api/h2-workbench/runs/{run}?session_id={s2}").status_code == 404
        stopped = client.post(
            f"/api/h2-workbench/runs/{run}/cancel?session_id={s1}",
            headers={"Idempotency-Key": "cancel-test"},
        )
        assert stopped.status_code == 200
        assert wait_answer(client, s1)["status"] == "cancelled"


def test_web_task_tree_exposes_real_child_and_wait_wake(web):
    wb, _ = web

    async def domain(context, caps):
        if context.state.phase == "plan":
            child = await caps.tool(
                "agent.spawn",
                {
                    "dispatch_key": "web-child",
                    "role": "analyst",
                    "role_version": "2.0.0",
                    "objective": "Test child handoff",
                    "success_criteria": ["Return complete result"],
                    "input_refs": [],
                    "output_contract": {"name": "fixture", "version": "1"},
                },
            )
            return FinanceStep(
                status="waiting",
                state=context.state.model_copy(
                    update={"phase": "research", "revision": context.state.revision + 1}
                ),
                wait=Wait(task_ids=[child["data"]["task_id"]]),
            )
        return FinanceStep(
            status="partial",
            state=context.state.model_copy(
                update={"phase": "delivery", "revision": context.state.revision + 1}
            ),
            submission=Submission(
                result_key="root-final",
                status="partial",
                content="Root result after child",
                gaps=["An explicit fixture gap"],
                warnings=["Fixture warning"],
            ),
        )

    async def child(work, caps):
        return Submission(result_key="child-final", content="Complete child result")

    wb.service = FinanceHarnessV2Service(
        wb.repository,
        wb.service.transport,
        finance_step=domain,
        child_executor=child,
        model_adapter=wb.service.model_adapter,
    )
    with TestClient(create_app(default_h2=True, h2_workbench=wb)) as client:
        session = client.post("/api/h2-workbench/sessions", json={}).json()["session_id"]
        assert submit(client, session).status_code == 202
        final = wait_answer(client, session)
        assert final["status"] == "partial", wb.view(final["run_id"], session)
        assert final["answer"] == "Root result after child"
        view = client.get(f"/api/h2-workbench/runs/{final['run_id']}?session_id={session}").json()
        assert [task["depth"] for task in view["status"]["tasks"]] == [0, 1]
        trace_agents = [span for span in view["trace"]["spans"] if span["span_type"] == "agent"]
        assert [(span["role"], span["depth"], span["parent_span_id"]) for span in trace_agents] == [
            ("main", 0, None), ("analyst", 1, trace_agents[0]["span_id"]),
        ]
        assert all(event["agent_path"] for event in view["trace"]["events"])
        kinds = {event["event_type"] for event in view["status"]["events"]}
        assert "task.waiting" in kinds and "task.woken" in kinds
        assert final["metadata"]["warnings"] == ["Fixture warning"]
        assert final["metadata"]["gaps"] == ["An explicit fixture gap"]
