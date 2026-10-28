"""Focused acceptance for the explicit Harness 2.0 finance service entry."""

from __future__ import annotations

import asyncio
from pathlib import Path

import fakeredis
import pytest

from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import Submission, Wait
from policy_impact.dispatch.finance_root import FinanceRootContext, FinanceStep
from policy_impact.dispatch.transport import RedisStreams

TOOLS = ["agent.spawn", "agent.wait", "agent.status", "result.get"]


@pytest.fixture
def service(tmp_path: Path):
    config = SchedulerConfig(
        budget_policy="hierarchical",
        stream_name="finance-agent-next:fixture:finance-service",
        publish_attempts=2,
        lease_ms=60000,
    )
    repo = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        config=config,
        profiles={
            "fixture": {
                "model_id": "fixture-model",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
            }
        },
        roles={"analyst@2.0.0": []},
    )
    repo.migrate()
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True),
        config.stream_name,
        group="finance-service-tests",
    )
    calls: list[str] = []

    async def step(context: FinanceRootContext, capabilities):
        if context.state.phase == "plan":
            receipt = await capabilities.tool(
                "agent.spawn",
                {
                    "dispatch_key": "service-article-a",
                    "role": "analyst",
                    "role_version": "2.0.0",
                    "objective": "read one bounded article",
                    "success_criteria": ["return source-bound evidence"],
                    "input_refs": [],
                    "output_contract": {"name": "finance.article", "version": "1"},
                    "requested_budget": {"model_calls": 2, "wall_time_ms": 300000},
                },
            )
            calls.append(receipt["data"]["task_id"])
            return FinanceStep(
                status="waiting",
                state=context.state.model_copy(
                    update={
                        "phase": "research",
                        "completed_steps": ["root.plan", "child.article-a"],
                        "pending_steps": ["root.delivery"],
                        "research_plan": {"goals": ["answer the query"]},
                        "article_states": [{
                            "article_id": "article-a",
                            "read_status": "pending",
                            "use_status": "undecided",
                        }],
                        "revision": 2,
                    }
                ),
                wait=Wait(task_ids=[receipt["data"]["task_id"]], mode="all"),
            )
        assert context.state.phase == "research"
        assert context.observations[0]["tasks"][0]["status"] == "succeeded"
        return FinanceStep(
            status="partial",
            state=context.state.model_copy(
                update={
                    "phase": "delivery",
                    "pending_steps": [],
                    "candidate_answer": {"full_ref": "child-result"},
                    "evidence_ledger": [{"evidence_id": "article-a#line-1"}],
                    "coverage": {"status": "partial", "stop_rationale": "budget boundary"},
                    "review": {"status": "disputed", "findings": ["one gap remains"]},
                    "delivery": {"status": "partial", "disclosure": "review disagreement retained"},
                    "revision": 3,
                }
            ),
            submission=Submission(
                result_key="service-final",
                status="partial",
                content="The source-bound candidate is retained with its gap.",
                gaps=["one gap remains"],
                warnings=["review disagreement retained"],
            ),
        )

    async def child(work, _capabilities):
        return Submission(
            result_key="article-a",
            content="Article A evidence",
            summary="A bounded child result",
        )

    return FinanceHarnessV2Service(
        repo,
        transport,
        finance_step=step,
        child_executor=child,
        worker_id="finance-service-worker",
    ), calls


@pytest.mark.parametrize("parameters,expected", [
    ({}, True),
    ({"input_tokens": 1, "safety_tokens": 0}, False),
    ({"context_tokens": 512, "max_tokens": 256, "safety_tokens": 256}, False),
    ({"unexpected_parameter": True}, False),
])
def test_service_worker_uses_attempt_model_capacity_for_optional_result_bodies(service, parameters, expected):
    app, _ = service
    assert callable(app.worker.context_accepts)
    data = {"model": {"parameters": parameters}, "observations": ["source " * 1000]}
    assert app.worker.context_accepts(data) is expected
    with app.repository.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0


def test_service_creates_explicit_run_and_preserves_partial_finance_state(service):
    app, calls = service
    handle = app.create_run(
        run_id="finance-service-run",
        session_ref="session",
        query="compare two article viewpoints",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )

    status = asyncio.run(app.run_until_terminal(handle, max_cycles=20))

    assert status["tasks"][0]["status"] == "partial"
    assert len(calls) == 1
    assert len(status["tasks"][0]["attempts"]) == 2
    assert status["tasks"][0]["attempts"][0]["status"] == "suspended"

    with app.repository.db.connection() as connection:
        row = connection.execute(
            "SELECT sections_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (handle.task_id,),
        ).fetchone()
    assert row is not None


def test_root_state_rejects_article_body_before_persistence(service):
    app, _calls = service
    handle = app.create_run(
        run_id="finance-service-body",
        session_ref="session",
        query="read one article",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )

    original = app.root_executor._step

    async def bad_state(context, capabilities):
        state = context.state.model_copy(
            update={"article_states": [{"article_id": "a", "article_body": "x" * 20}]}
        )
        return FinanceStep(
            status="partial",
            state=state,
            submission=Submission(result_key="bad", status="partial", content="retained"),
        )

    app.root_executor._step = bad_state
    status = asyncio.run(app.run_until_terminal(handle, max_cycles=4))
    app.root_executor._step = original
    assert status["tasks"][0]["status"] == "failed"
