"""Contract tests for the opt-in durable financial root bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis
import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Submission, Wait
from policy_impact.dispatch.finance_root import (
    FinanceRootContext,
    FinanceRootExecutor,
    FinanceRootState,
    FinanceStep,
)
from policy_impact.dispatch.transport import Dispatcher, RedisStreams
from policy_impact.dispatch.worker import Worker

TOOLS = [
    "agent.spawn",
    "agent.wait",
    "agent.status",
    "result.get",
]


@pytest.fixture
def runtime(tmp_path: Path):
    config = SchedulerConfig(
        budget_policy="hierarchical",
        stream_name="finance-agent-next:fixture:finance-root",
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
    root = repo.create_run(
        run_id="finance-root",
        session_ref="session",
        query="compare the two authors",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True),
        config.stream_name,
        group="finance-root-tests",
    )
    return repo, user, root, transport


def test_root_wait_resume_persists_finance_state_and_does_not_replay_spawn(runtime):
    repo, user, root, transport = runtime
    spawn_calls = []

    async def finance_step(context: FinanceRootContext, capabilities):
        if context.state.phase == "plan":
            receipt = await capabilities.tool(
                "agent.spawn",
                {
                    "dispatch_key": "article-a",
                    "role": "analyst",
                    "role_version": "2.0.0",
                    "objective": "read the first article",
                    "success_criteria": ["return a source-bound summary"],
                    "input_refs": [],
                    "output_contract": {"name": "finance.article", "version": "1"},
                    "requested_budget": {"model_calls": 2, "wall_time_ms": 300000},
                },
            )
            spawn_calls.append(receipt["data"]["task_id"])
            state = context.state.model_copy(
                update={
                    "phase": "research",
                    "completed_steps": ["root.plan"],
                    "pending_steps": ["root.synthesize"],
                    "research_plan": {"child_task_id": receipt["data"]["task_id"]},
                    "revision": 2,
                }
            )
            return FinanceStep(
                status="waiting",
                state=state,
                wait=Wait(task_ids=[receipt["data"]["task_id"]], mode="all", timeout_ms=30000),
            )

        assert context.state.phase == "research"
        assert len(context.observations) == 1
        assert context.observations[0]["tasks"][0]["status"] == "succeeded"
        state = context.state.model_copy(
            update={
                "phase": "delivery",
                "completed_steps": ["root.plan", "root.synthesize"],
                "pending_steps": [],
                "candidate_answer": {"used_task": spawn_calls[0]},
                "delivery": {"status": "ready"},
                "revision": 3,
            }
        )
        return FinanceStep(
            status="succeeded",
            state=state,
            submission=Submission(
                result_key="final",
                content="The answer is retained with the child evidence.",
                summary="A durable finance root result.",
            ),
        )

    root_executor = FinanceRootExecutor(finance_step)

    async def execute(work, capabilities):
        if work.brief["depth"] == 0:
            return await root_executor(work, capabilities)
        return Submission(
            result_key="article",
            content="Article A evidence",
            summary="Child article evidence",
        )

    async def run():
        worker = Worker(repo, transport, "finance-worker", executor=execute)
        dispatcher = Dispatcher(repo, transport)
        for _ in range(12):
            dispatcher.publish_batch()
            await worker.run_once(block_ms=0)
            status = repo.status(user, [root["task_id"]])["tasks"][0]
            if status["status"] == "succeeded":
                return
        pytest.fail("finance root did not complete")

    asyncio.run(run())

    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["status"] == "succeeded"
    assert len(spawn_calls) == 1
    assert [attempt["status"] for attempt in status["attempts"]] == [
        "suspended",
        "succeeded",
    ]
    with repo.db.connection() as connection:
        result = connection.execute(
            "SELECT * FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (root["task_id"],),
        ).fetchone()
        sections = json.loads(result["sections_json"])
        state_artifact = connection.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?", (sections["finance_state"],)
        ).fetchone()
        events = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM h2_events WHERE aggregate_id=? ORDER BY event_id",
                (root["task_id"],),
            )
        ]
    state = json.loads(repo.artifacts.read(state_artifact))
    assert state["schema_version"] == "finance.root_state.v1"
    assert state["phase"] == "delivery"
    assert state["revision"] == 3
    assert "task.waiting" in events and "task.woken" in events


def test_finance_state_requires_revision_progress_on_resume():
    state = FinanceRootState(query="q", phase="plan")
    assert state.revision == 1
    with pytest.raises(ValueError, match="waiting requires"):
        FinanceStep(status="waiting", state=state)
    for invalid in ({"wait": Wait(task_ids=["child"])},
                    {"submission": Submission(result_key="bad", content="bad")}):
        with pytest.raises(ValueError, match="continuing requires"):
            FinanceStep(status="continuing", state=state, **invalid)


def test_root_continue_restarts_worker_and_restores_state_without_replaying_action(runtime):
    repo, user, root, transport = runtime
    seen = []

    async def step(context, _capabilities):
        seen.append((context.state.phase, context.state.revision))
        if context.state.phase == "plan":
            return FinanceStep(status="continuing", state=context.state.model_copy(update={
                "phase": "research", "revision": 2, "completed_steps": ["durable-action"],
                "research_plan": {"objective": "preserve original goal"},
            }))
        assert context.state.completed_steps == ["durable-action"]
        assert context.state.research_plan["objective"] == "preserve original goal"
        return FinanceStep(status="succeeded", state=context.state.model_copy(update={
            "phase": "delivery", "revision": 3,
        }), submission=Submission(result_key="done", content="retained result", summary="done"))

    async def run():
        dispatcher = Dispatcher(repo, transport)
        dispatcher.publish_batch()
        await Worker(repo, transport, "first-worker", executor=FinanceRootExecutor(step)).run_once(block_ms=0)
        assert repo.status(user, [root["task_id"]])["tasks"][0]["status"] == "pending_dispatch"
        dispatcher.publish_batch()
        await Worker(repo, transport, "restarted-worker", executor=FinanceRootExecutor(step)).run_once(block_ms=0)

    asyncio.run(run())
    view = repo.status(user, [root["task_id"]])["tasks"][0]
    assert view["status"] == "succeeded" and view["budget"]["consumed"] == 0
    assert [a["status"] for a in view["attempts"]] == ["suspended", "succeeded"]
    assert seen == [("plan", 1), ("research", 2)]


def test_finance_state_revision_must_advance_on_initial_terminal_step(runtime):
    repo, user, root, transport = runtime

    async def finance_step(context: FinanceRootContext, _capabilities):
        return FinanceStep(
            status="succeeded",
            state=context.state,
            submission=Submission(
                result_key="same-revision",
                content="answer",
                summary="summary",
            ),
        )

    executor = FinanceRootExecutor(finance_step)

    async def run():
        worker = Worker(repo, transport, "revision-worker", executor=executor)
        Dispatcher(repo, transport).publish_batch()
        await worker.run_once(block_ms=0)

    asyncio.run(run())
    status = repo.status(user, [root["task_id"]])["tasks"][0]
    assert status["status"] == "failed"
    assert status["attempts"][-1]["error_code"] == "contract_error"


def test_unavailable_finance_state_is_a_checkpoint_failure():
    work = type(
        "Work",
        (),
        {"brief": {"depth": 0, "role": "main"}, "checkpoint": {"context_ref": "missing"}},
    )()

    class MissingContext:
        async def load_context(self, _ref):
            raise DispatchError("artifact_unavailable")

    executor = FinanceRootExecutor(lambda *_args: None)
    with pytest.raises(DispatchError) as error:
        asyncio.run(executor._load_state(work, MissingContext()))
    assert error.value.code == "checkpoint_invalid"


def test_terminal_thread_follow_up_can_start_a_root_task_without_initial_message():
    work = type(
        "Work",
        (),
        {
            "brief": {"depth": 0, "role": "main"},
            "checkpoint": None,
            "messages": [{"kind": "follow_up", "content": "analyze the new question"}],
        },
    )()

    state = asyncio.run(FinanceRootExecutor(lambda *_args: None)._load_state(work, object()))

    assert state.query == "analyze the new question"
    assert state.phase == "plan"


def test_terminal_root_followup_preserves_previous_result_as_reference_only():
    work = type("Work", (), {
        "brief": {
            "depth": 0, "role": "main", "previous_task_id": "task.previous",
            "previous_result_ref": "res_previous", "success_criteria": ["Explain the change"],
        },
        "checkpoint": None,
        "messages": [{"kind": "follow_up", "content": "Reconsider the previous conclusion"}],
    })()
    state = asyncio.run(FinanceRootExecutor(lambda *_args: None)._load_state(work, object()))
    assert state.query == "Reconsider the previous conclusion"
    assert state.execution["follow_up_context"] == {
        "previous_task_id": "task.previous", "previous_result_ref": "res_previous",
        "success_criteria": ["Explain the change"],
    }
    assert "res_previous" not in state.execution.get("evidence_refs", [])

