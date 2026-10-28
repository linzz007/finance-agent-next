"""Offline scheduling tests. All databases, records and Redis instances are fixtures."""

import asyncio
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import fakeredis
import pytest
from pydantic import ValidationError

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import (
    Cancel,
    CheckpointState,
    ModelPolicySet,
    Resume,
    Send,
    Spawn,
    Submission,
    Wait,
)
from policy_impact.dispatch.transport import Dispatcher, RedisStreams, validate_message
from policy_impact.dispatch.worker import Worker

TOOLS = [
    "agent.spawn",
    "agent.wait",
    "agent.status",
    "agent.send",
    "agent.cancel",
    "agent.resume",
    "result.get",
]


class Clock:
    def __init__(self):
        self.value = 1000000

    def __call__(self):
        return self.value


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    config = SchedulerConfig(
        budget_policy="hierarchical",
        stream_name="finance-agent-next:fixture:h2:dispatch", publish_attempts=2
    )
    repo = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        clock=clock,
        config=config,
        profiles={
            p: {
                "model_id": p,
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
            }
            for p in ["gpt", "deepseek"]
        },
        roles={"analyst@2.0.0": TOOLS},
    )
    repo.migrate()
    root = repo.create_run(
        run_id="fixture",
        session_ref="session",
        query="compare source reasoning",
        selected_profile="gpt",
        allowed_profiles=["gpt", "deepseek"],
        tools=TOOLS,
    )["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    return repo, clock, user, root


def spawn(repo, user, key="child", **changes):
    args = {
        "dispatch_key": key,
        "role": "analyst",
        "role_version": "2.0.0",
        "objective": "explain the reasoning",
        "success_criteria": ["preserve conditions"],
        "input_refs": [],
        "output_contract": {"name": "fixture.understanding", "version": "1"},
        "requested_budget": {"model_calls": 30, "wall_time_ms": 600000},
    }
    args.update(changes)
    return repo.spawn(user, Spawn.model_validate(args))


def delivery(repo, task_id):
    with repo.db.connection() as c:
        row = c.execute(
            "SELECT payload_json FROM h2_outbox WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return json.loads(row[0])


def claim(repo, task_id, worker="fixture-worker"):
    lease, safe = repo.claim(delivery(repo, task_id), worker)
    assert lease and not safe
    repo.start(lease)
    return lease


def state(repo, lease):
    ref = repo.store_text(lease.caller(), "context", "public execution context")
    repo.mailbox(lease)
    return CheckpointState(
        current_phase="reading",
        completed_steps=[],
        pending_steps=["finish"],
        input_refs=[],
        context_ref=ref,
        consumed_message_seq=1,
        last_event_id="0",
    )


def status(repo, caller, task_id):
    return repo.status(caller, [task_id])["tasks"][0]


def finish(repo, lease, **changes):
    args = {"result_key": "final", "content": "full useful analysis", "summary": "public summary"}
    args.update(changes)
    return repo.submit(lease, Submission(**args))


def test_schema_matches_contract_and_explicit_migration(setup):
    repo, _, _, _ = setup
    repo.migrate()
    sql = Path("src/policy_impact/dispatch/schema.sql").read_text(encoding="utf-8")
    doc = Path("docs/harness-v2/sqlite-schema.md").read_text(encoding="utf-8")
    block = re.search(r"```sql\n(.*?)```", doc, re.DOTALL)[1]
    assert sql.split("\n", 1)[1].strip() == block.strip()
    with repo.db.connection() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_schema_migrations").fetchone()[0] == 2
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_constructor_does_not_migrate_or_start_services(tmp_path):
    db = tmp_path / "missing.sqlite"
    DispatchRepository(db, tmp_path / "records", profiles={}, roles={})
    assert not db.exists()


def test_spawn_replay_conflict_and_root_replay(setup):
    repo, _, user, root = setup
    first = spawn(repo, user)
    assert spawn(repo, user)["replayed"]
    assert spawn(repo, user)["data"] == first["data"]
    with pytest.raises(DispatchError, match="idempotency_conflict"):
        spawn(repo, user, objective="different objective")
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM h2_threads").fetchone()[0] == 2
    repeated = repo.create_run(
        run_id="fixture",
        session_ref="session",
        query="compare source reasoning",
        selected_profile="gpt",
        allowed_profiles=["gpt", "deepseek"],
        tools=TOOLS,
    )
    assert repeated["replayed"] and repeated["data"] == root


def test_status_accepts_thread_targets_and_returns_followups_with_scope(setup):
    repo, _, user, root = setup
    child = spawn(repo, user, key="thread-status-child")["data"]

    by_thread = repo.status(user, thread_ids=[child["thread_id"]])
    assert [task["task_id"] for task in by_thread["tasks"]] == [child["task_id"]]

    by_both = repo.status(
        user,
        task_ids=[root["task_id"]],
        thread_ids=[child["thread_id"]],
    )
    assert {task["task_id"] for task in by_both["tasks"]} == {
        root["task_id"],
        child["task_id"],
    }

    with pytest.raises(DispatchError, match="invalid_arguments"):
        repo.status(user)
    with pytest.raises(DispatchError, match="not_found"):
        repo.status(user, thread_ids=["thr_missing"])


def test_spawn_budget_failure_is_atomic(setup):
    repo, _, user, root = setup
    before = status(repo, user, root["task_id"])["budget"]
    with pytest.raises(DispatchError, match="budget_denied"):
        spawn(repo, user, requested_budget={"model_calls": 10000, "wall_time_ms": 600000})
    assert status(repo, user, root["task_id"])["budget"] == before
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 1


def test_two_workers_only_one_can_claim(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    payload = delivery(repo, child["task_id"])
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(repo.claim, payload, f"worker-{i}") for i in range(2)]
        results = [f.result() for f in futures]
    assert sum(lease is not None for lease, _ in results) == 1
    assert all(not ack for _, ack in results)


def test_child_cannot_manage_sibling_or_cross_run(setup):
    repo, _, user, _ = setup
    a = spawn(repo, user, "a")["data"]
    b = spawn(repo, user, "b")["data"]
    lease = claim(repo, a["task_id"])
    with pytest.raises(DispatchError, match="not_found"):
        repo.status(lease.caller(), [b["task_id"]])
    foreign = repo.create_run(
        run_id="other",
        session_ref="session",
        query="other",
        selected_profile="gpt",
        allowed_profiles=["gpt"],
        tools=TOOLS,
    )["data"]
    with pytest.raises(DispatchError, match="not_found"):
        repo.cancel(user, Cancel(task_id=foreign["task_id"], idempotency_key="no"))


def test_two_levels_recursion_and_permission(setup):
    repo, _, user, _ = setup
    a = spawn(repo, user)["data"]
    first = claim(repo, a["task_id"])
    b = spawn(
        repo,
        first.caller(),
        "grandchild",
        requested_budget={"model_calls": 10, "wall_time_ms": 300000},
    )["data"]
    second = claim(repo, b["task_id"], "other")
    with pytest.raises(DispatchError, match="depth_exceeded"):
        spawn(
            repo,
            second.caller(),
            "forbidden",
            requested_budget={"model_calls": 1, "wall_time_ms": 30000},
        )


def test_cancel_subtree_fences_and_preserves_late_body(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    receipt = repo.cancel(user, Cancel(task_id=child["task_id"], idempotency_key="cancel"))
    assert receipt["data"]["settled"] is False
    assert status(repo, user, child["task_id"])["status"] == "running"
    with pytest.raises(DispatchError, match="cancelled"):
        repo.reserve_call(lease, "cannot-bill")
    with pytest.raises(DispatchError, match="cancelled"):
        finish(repo, lease)
    repo.settle_cancelled(lease)
    assert status(repo, user, child["task_id"])["status"] == "cancelled"
    with repo.db.connection() as c:
        r = c.execute("SELECT * FROM h2_results WHERE task_id=?", (child["task_id"],)).fetchone()
        assert r["disposition"] == "late"
        art = c.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?", (r["full_ref"],)
        ).fetchone()
    assert repo.artifacts.read(art) == b"full useful analysis"


def test_expired_lease_recovery_fences_old_and_keeps_budget(setup):
    repo, clock, user, _ = setup
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    repo.reserve_call(old, "physical-1")
    repo.call_started(old, "physical-1")
    clock.value += repo.config.lease_ms + 1
    with pytest.raises(DispatchError, match="stale_lease"):
        repo.heartbeat(old)
    repo.reconcile()
    new = claim(repo, child["task_id"])
    assert new.attempt_id != old.attempt_id
    with pytest.raises(DispatchError, match="stale_lease"):
        finish(repo, old)
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1
    with repo.db.connection() as c:
        assert (
            c.execute("SELECT status FROM h2_model_calls WHERE call_id='physical-1'").fetchone()[0]
            == "unknown"
        )


def test_finished_result_replay_and_event_immutable(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    first = finish(repo, lease)
    assert finish(repo, lease)["result_ref"] == first["result_ref"]
    assert repo.claim(delivery(repo, child["task_id"]), "repeat") == (None, True)
    with (
        pytest.raises(sqlite3.IntegrityError, match="append_only"),
        repo.db.connection(write=True) as c,
    ):
        c.execute("DELETE FROM h2_events")
    assert status(repo, user, child["task_id"])["status"] == "succeeded"


def test_wait_releases_attempt_and_wakes_same_task(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    old_state = state(repo, parent)
    repo.suspend(parent, old_state, Wait(task_ids=[child["task_id"]]))
    assert status(repo, user, root["task_id"])["status"] == "waiting"
    finish(repo, claim(repo, child["task_id"], "child-worker"))
    view = status(repo, user, root["task_id"])
    assert view["status"] == "pending_dispatch"
    assert [a["status"] for a in view["attempts"]] == ["suspended", "queued"]
    new = claim(repo, root["task_id"])
    assert new.task_id == parent.task_id and new.attempt_id != parent.attempt_id
    assert repo.execution_input(new)["checkpoint"]["context_ref"] == old_state.context_ref


def test_wait_timeout_does_not_cancel_target(setup):
    repo, clock, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[child["task_id"]], timeout_ms=10))
    clock.value += 11
    repo.reconcile()
    assert status(repo, user, root["task_id"])["status"] == "pending_dispatch"
    assert status(repo, user, child["task_id"])["status"] == "pending_dispatch"


def test_wait_snapshot_supports_any_and_specific_modes(setup):
    repo, _, user, _ = setup
    first = spawn(repo, user, "wait-a")["data"]
    second = spawn(repo, user, "wait-b")["data"]
    finish(repo, claim(repo, first["task_id"]))

    any_view = repo.wait_snapshot(
        user, Wait(task_ids=[first["task_id"], second["task_id"]], mode="any")
    )
    specific_view = repo.wait_snapshot(
        user, Wait(task_ids=[second["task_id"]], mode="specific", timeout_ms=0)
    )

    assert any_view["condition_met"] is True
    assert any_view["reason"] == "condition_met"
    assert specific_view["condition_met"] is False
    assert specific_view["reason"] == "timeout"


def test_wait_snapshot_honors_bounded_read_only_timeout(setup):
    repo, _, user, _ = setup
    target = spawn(repo, user, "wait-timeout")['data']
    started = time.monotonic()
    view = repo.wait_snapshot(
        user, Wait(task_ids=[target["task_id"]], mode="specific", timeout_ms=20)
    )
    elapsed = time.monotonic() - started
    assert view["condition_met"] is False
    assert view["reason"] == "timeout"
    assert elapsed >= 0.015
    assert elapsed < 0.5


@pytest.mark.parametrize("outcome", ["failed", "partial", "cancelled", "timed_out"])
def test_failed_dependency_stays_blocked(setup, outcome):
    repo, _, user, _ = setup
    a = spawn(repo, user, "a")["data"]
    b = spawn(repo, user, "b", depends_on_task_ids=[a["task_id"]])["data"]
    lease = claim(repo, a["task_id"])
    if outcome == "failed":
        repo.fail(lease, "quota_error")
    elif outcome == "timed_out":
        with repo.db.connection(write=True) as c:
            repo._terminate(c, repo._task(c, a["task_id"]), "timed_out", "timeout")
    else:
        finish(repo, lease, status=outcome)
    assert repo.claim(delivery(repo, b["task_id"]), "blocked") == (None, False)
    repo.reconcile()
    assert status(repo, user, b["task_id"])["status"] == "blocked"


def test_wait_and_dependency_cycle_rejected(setup):
    repo, _, _, root = setup
    parent = claim(repo, root["task_id"])
    spawn(repo, parent.caller())
    with pytest.raises(DispatchError):
        repo.suspend(parent, state(repo, parent), Wait(task_ids=[root["task_id"]]))


def test_sibling_wait_is_rejected_before_it_can_form_a_cycle(setup):
    repo, _, user, _ = setup
    first = spawn(repo, user, "cycle-a")["data"]
    second = spawn(repo, user, "cycle-b")["data"]
    first_lease = claim(repo, first["task_id"], "cycle-a-worker")
    second_lease = claim(repo, second["task_id"], "cycle-b-worker")
    with pytest.raises(DispatchError, match="not_found"):
        repo.suspend(first_lease, state(repo, first_lease), Wait(task_ids=[second["task_id"]]))
    assert status(repo, user, first["task_id"])["status"] == "running"
    assert status(repo, user, second["task_id"])["status"] == "running"

    with pytest.raises(DispatchError, match="not_found"):
        repo.suspend(second_lease, state(repo, second_lease), Wait(task_ids=[first["task_id"]]))


def test_send_sequence_and_followup_new_task_same_thread(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    first = repo.send(user, Send(thread_id=child["thread_id"], idempotency_key="m1", content="one"))
    assert repo.send(user, Send(thread_id=child["thread_id"], idempotency_key="m1", content="one"))[
        "replayed"
    ]
    second = repo.send(
        user, Send(thread_id=child["thread_id"], idempotency_key="m2", content="two")
    )
    assert second["data"]["sequence"] == first["data"]["sequence"] + 1
    finish(repo, claim(repo, child["task_id"]))
    next_work = repo.send(
        user,
        Send(
            thread_id=child["thread_id"],
            idempotency_key="m3",
            content="more",
            follow_up={
                "requested_budget": {"model_calls": 10, "wall_time_ms": 300000},
                "output_contract": {"name": "fixture", "version": "1"},
                "success_criteria": ["explain"],
            },
        ),
    )
    assert next_work["data"]["created_follow_up_task"]
    assert next_work["data"]["task_id"] != child["task_id"]
    assert status(repo, user, child["task_id"])["status"] == "succeeded"
    followup_lease = claim(repo, next_work["data"]["task_id"])
    brief = repo.execution_input(followup_lease)["brief"]
    assert brief["success_criteria"] == ["explain"]
    assert brief["previous_task_id"] == child["task_id"]
    assert brief["previous_result_ref"] == status(repo, user, child["task_id"])["result_ref"]


def test_resume_checkpoint_and_explicit_model_selection(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    ref = repo.save_checkpoint(old, state(repo, old))
    repo.fail(old, "quota_error")
    resumed = repo.resume(
        user,
        Resume(
            task_id=child["task_id"],
            idempotency_key="retry",
            checkpoint_ref=ref,
            model_profile="deepseek",
        ),
    )
    new = claim(repo, child["task_id"])
    assert repo.execution_input(new)["model"]["profile"] == "deepseek"
    assert resumed["data"]["new_attempt_id"] == new.attempt_id
    assert repo.resume(
        user,
        Resume(
            task_id=child["task_id"],
            idempotency_key="retry",
            checkpoint_ref=ref,
            model_profile="deepseek",
        ),
    )["replayed"]


def test_no_automatic_model_fallback(setup):
    repo, clock, user, _ = setup
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    repo.fail(old, "quota_error")
    clock.value += 200000
    repo.reconcile()
    view = status(repo, user, child["task_id"])
    assert view["status"] == "failed" and len(view["attempts"]) == 1


@pytest.mark.parametrize("competing_calls,allowed", [(551, False), (546, True)])
def test_resume_cannot_redelegate_parent_finalization_reserve(setup, competing_calls, allowed):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(old, state(repo, old))
    repo.fail(old, "quota_error")
    spawn(repo, user, "competing-allocation", requested_budget={
        "model_calls": competing_calls, "wall_time_ms": 600000,
    })
    with repo.db.connection() as c:
        before = {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "h2_attempts", "h2_budget_entries", "h2_snapshots", "h2_commands", "h2_events",
        )}
        old_policy = c.execute(
            "SELECT model_policy_ref FROM h2_threads WHERE thread_id=?", (child["thread_id"],),
        ).fetchone()[0]
    request = Resume(task_id=child["task_id"], idempotency_key="restore", checkpoint_ref=checkpoint,
                     model_profile="deepseek")
    if allowed:
        result = repo.resume(user, request)
        assert result["data"]["allocated_budget"] == {"model_calls": 30}
        assert status(repo, user, root["task_id"])["budget"]["local_available"] == 24
        assert status(repo, user, child["task_id"])["budget"]["local_available"] == 30
    else:
        with pytest.raises(DispatchError, match="budget_denied"):
            repo.resume(user, request)
        with repo.db.connection() as c:
            after = {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in before}
            assert after == before
            assert c.execute(
                "SELECT model_policy_ref FROM h2_threads WHERE thread_id=?", (child["thread_id"],),
            ).fetchone()[0] == old_policy
        assert status(repo, user, root["task_id"])["budget"]["local_available"] == 49
        assert status(repo, user, child["task_id"])["status"] == "failed"


def _failed_child_with_followup(repo, user):
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(old, state(repo, old))
    repo.fail(old, "quota_error")
    followup = repo.send(user, Send(
        thread_id=child["thread_id"], idempotency_key="followup", content="Clarify the reasoning",
        follow_up={"requested_budget": {"model_calls": 5, "wall_time_ms": 300000},
                   "output_contract": {"name": "fixture", "version": "1"},
                   "success_criteria": ["Preserve source conditions"]},
    ))["data"]
    return child, checkpoint, followup


@pytest.mark.parametrize("followup_state", ["queued", "running", "waiting"])
def test_resume_rejects_another_open_task_on_same_thread(setup, followup_state):
    repo, _, user, _ = setup
    child, checkpoint, followup = _failed_child_with_followup(repo, user)
    if followup_state != "queued":
        other = claim(repo, followup["task_id"])
        if followup_state == "waiting":
            repo.suspend(other, state(repo, other))
    with repo.db.connection() as c:
        before = c.execute("SELECT count(*) FROM h2_attempts").fetchone()[0]
        entries = c.execute("SELECT count(*) FROM h2_budget_entries").fetchone()[0]
    with pytest.raises(DispatchError, match="thread_busy"):
        repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                                 idempotency_key="resume-old"))
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_attempts").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM h2_budget_entries").fetchone()[0] == entries
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_closed_thread_cannot_resume_a_failed_task(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    old = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(old, state(repo, old))
    repo.fail(old, "quota_error")
    with repo.db.connection(write=True) as c:
        c.execute("UPDATE h2_threads SET status='closed' WHERE thread_id=?", (child["thread_id"],))
    with pytest.raises(DispatchError, match="invalid_state"):
        repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                                 idempotency_key="resume-closed"))
    assert len(status(repo, user, child["task_id"])["attempts"]) == 1


def test_resume_after_completed_followup_charges_the_restored_task_account(setup):
    repo, _, user, root = setup
    child, checkpoint, followup = _failed_child_with_followup(repo, user)
    other = claim(repo, followup["task_id"])
    repo.reserve_call(other, "followup-call")
    repo.call_started(other, "followup-call")
    finish(repo, other)
    result_ref = status(repo, user, followup["task_id"])["result_ref"]
    repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                             idempotency_key="restore-old"))
    restored = claim(repo, child["task_id"])
    repo.reserve_call(restored, "restored-call")
    with repo.db.connection() as c:
        task_budget = c.execute("SELECT budget_ref FROM h2_tasks WHERE task_id=?", (child["task_id"],)).fetchone()[0]
        thread_budget = c.execute("SELECT budget_ref FROM h2_threads WHERE thread_id=?", (child["thread_id"],)).fetchone()[0]
        call_budget = c.execute("SELECT budget_id FROM h2_model_calls WHERE call_id='restored-call'").fetchone()[0]
        charge_budget = c.execute("SELECT budget_id FROM h2_budget_entries WHERE call_id='restored-call' AND kind='charge'").fetchone()[0]
        assert task_budget == thread_budget == call_budget == charge_budget
    finish(repo, restored)
    assert status(repo, user, followup["task_id"])["result_ref"] == result_ref
    assert status(repo, user, followup["task_id"])["budget"]["consumed"] == 1
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1
    view = status(repo, user, root["task_id"])["budget"]
    assert view["remaining"] == 598 and view["consumed"] == 2


@pytest.mark.parametrize("resumed_state", ["queued", "running", "waiting"])
@pytest.mark.parametrize("new_work", [False, True])
def test_send_targets_the_open_resumed_task_not_the_newest_created_task(setup, resumed_state, new_work):
    repo, _, user, _ = setup
    child, checkpoint, followup = _failed_child_with_followup(repo, user)
    completed = claim(repo, followup["task_id"])
    finish(repo, completed)
    previous_result = status(repo, user, followup["task_id"])["result_ref"]
    repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                             idempotency_key="resume-before-send"))
    if resumed_state != "queued":
        active = claim(repo, child["task_id"])
        if resumed_state == "waiting":
            repo.suspend(active, state(repo, active))
    with repo.db.connection() as c:
        budget_entries = c.execute("SELECT count(*) FROM h2_budget_entries").fetchone()[0]
    request = Send(
        thread_id=child["thread_id"], idempotency_key="message-to-resumed-task", content="Keep the conditions",
        follow_up={
            "requested_budget": {"model_calls": 5, "wall_time_ms": 300000},
            "output_contract": {"name": "fixture", "version": "1"},
            "success_criteria": ["Keep the conditions"],
        } if new_work else None,
    )
    if new_work:
        with pytest.raises(DispatchError) as error:
            repo.send(user, request)
        assert error.value.code == "invalid_arguments"
    else:
        response = repo.send(user, request)
        assert response["data"]["task_id"] == child["task_id"]
        assert response["data"]["created_follow_up_task"] is False
        assert repo.send(user, request)["replayed"]
        with repo.db.connection() as c:
            message = c.execute("SELECT task_id,payload_ref FROM h2_messages WHERE message_id=?",
                                (response["data"]["message_id"],)).fetchone()
            assert message["task_id"] == child["task_id"]
        assert repo.read_input(user, message["payload_ref"]).decode() == request.content
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM h2_budget_entries").fetchone()[0] == budget_entries
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert status(repo, user, followup["task_id"])["result_ref"] == previous_result


def test_send_detects_an_older_task_resumed_during_followup_preparation(setup, monkeypatch):
    repo, _, user, _ = setup
    child, checkpoint, followup = _failed_child_with_followup(repo, user)
    finish(repo, claim(repo, followup["task_id"]))
    original = repo.artifacts.json

    def prepare(run_id, kind, value):
        artifact = original(run_id, kind, value)
        if kind == "brief":
            repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                                     idempotency_key="resume-during-send"))
        return artifact

    monkeypatch.setattr(repo.artifacts, "json", prepare)
    with pytest.raises(DispatchError) as error:
        repo.send(user, Send(
            thread_id=child["thread_id"], idempotency_key="stale-followup", content="new work",
            follow_up={"requested_budget": {"model_calls": 5, "wall_time_ms": 300000},
                       "output_contract": {"name": "fixture", "version": "1"},
                       "success_criteria": ["Keep conditions"]},
        ))
    assert error.value.code == "state_conflict"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 3
        assert not c.execute("SELECT 1 FROM h2_commands WHERE idempotency_key='stale-followup'").fetchone()
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_budget_charge_idempotent_and_refund_only_unsent(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    repo.reserve_call(lease, "one")
    repo.reserve_call(lease, "one")
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1
    repo.call_started(lease, "one")
    with pytest.raises(DispatchError):
        repo.refund_unsent_call(lease, "one")
    with pytest.raises(DispatchError):
        repo.call_started(lease, "one")
    repo.reserve_call(lease, "two")
    repo.refund_unsent_call(lease, "two")
    repo.refund_unsent_call(lease, "two")
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("boundary", ["cancel_requested", "cancel_settled", "lease_replaced", "deadline_settled"])
def test_confirmed_unsent_settlement_keeps_original_account_after_fencing(setup, depth, boundary):
    repo, clock, user, root = setup
    target = root
    parent = user
    for level in range(depth):
        target = spawn(repo, parent, f"refund-depth-{level}", requested_budget={
            "model_calls": 10, "wall_time_ms": 300000 if level == 0 else 120000,
        })["data"]
        if level + 1 < depth:
            parent = claim(repo, target["task_id"]).caller()
    lease = claim(repo, target["task_id"])
    repo.reserve_call(lease, "confirmed-unsent")
    if boundary.startswith("cancel"):
        repo.cancel(user, Cancel(task_id=target["task_id"], idempotency_key="cancel-unsent"))
        if boundary == "cancel_settled":
            repo.settle_cancelled(lease)
    elif boundary == "lease_replaced":
        clock.value += repo.config.lease_ms + 1
        repo.reconcile()
        with repo.db.connection() as c:
            assert c.execute("SELECT current_attempt_id FROM h2_tasks WHERE task_id=?", (
                lease.task_id,
            )).fetchone()[0] != lease.attempt_id
    else:
        clock.value = status(repo, user, target["task_id"])["deadline_at"] + 1
        repo.reconcile()
    repo.refund_unsent_call(lease, "confirmed-unsent")
    repo.refund_unsent_call(lease, "confirmed-unsent")
    with repo.db.connection() as c:
        call = c.execute("SELECT * FROM h2_model_calls WHERE call_id='confirmed-unsent'").fetchone()
        assert call["status"] == "failed" and call["error_code"] == "not_sent"
        assert call["started_at"] is None
        credits = c.execute("SELECT * FROM h2_budget_entries WHERE kind='refund'").fetchall()
        assert len(credits) == 1 and credits[0]["budget_id"] == call["budget_id"]
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 600
        terminal = c.execute("SELECT status FROM h2_tasks WHERE task_id=?", (lease.task_id,)).fetchone()[0]
        if depth and terminal in {"cancelled", "timed_out"}:
            assert repo._balance(c, call["budget_id"]) == 0
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(DispatchError):
        repo.call_started(lease, "confirmed-unsent")


@pytest.mark.parametrize("field", ["run_id", "thread_id", "task_id", "attempt_id", "token"])
def test_unsent_settlement_rejects_forged_identity_without_budget_changes(setup, field):
    repo, _, _, root = setup
    lease = claim(repo, root["task_id"])
    repo.reserve_call(lease, "protected-reservation")
    forged = replace(lease, **{field: "forged"})
    with pytest.raises(DispatchError):
        repo.refund_unsent_call(forged, "protected-reservation")
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 599
    repo.refund_unsent_call(lease, "protected-reservation")


@pytest.mark.parametrize("parent_first", [False, True])
def test_late_unsent_refund_returns_through_terminated_ancestor_budgets(setup, parent_first):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    child_lease = claim(repo, child["task_id"])
    grandchild = spawn(repo, child_lease.caller(), "refund-grandchild", requested_budget={
        "model_calls": 10, "wall_time_ms": 120000,
    })["data"]
    grandchild_lease = claim(repo, grandchild["task_id"])
    repo.reserve_call(grandchild_lease, "unsent-in-cancelled-tree")
    repo.cancel(user, Cancel(task_id=root["task_id"], idempotency_key="cancel-all"))
    leases = [child_lease, grandchild_lease] if parent_first else [grandchild_lease, child_lease]
    for lease in leases:
        repo.settle_cancelled(lease)
    repo.refund_unsent_call(grandchild_lease, "unsent-in-cancelled-tree")
    repo.refund_unsent_call(grandchild_lease, "unsent-in-cancelled-tree")
    with repo.db.connection() as c:
        budgets = c.execute("SELECT budget_id,parent_budget_id FROM h2_budgets").fetchall()
        assert all(repo._balance(c, row["budget_id"]) == (0 if row["parent_budget_id"] else 600)
                   for row in budgets)
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_tasks WHERE status='cancelled'").fetchone()[0] == 3


@pytest.mark.parametrize("settled", [False, True])
def test_started_call_is_never_refunded_after_cancellation(setup, settled):
    repo, _, user, root = setup
    lease = claim(repo, root["task_id"])
    repo.reserve_call(lease, "started-request")
    repo.call_started(lease, "started-request")
    repo.cancel(user, Cancel(task_id=lease.task_id, idempotency_key="cancel-started"))
    if settled:
        repo.settle_cancelled(lease)
    with pytest.raises(DispatchError):
        repo.refund_unsent_call(lease, "started-request")
    with repo.db.connection() as c:
        call = c.execute("SELECT * FROM h2_model_calls").fetchone()
        assert call["status"] == ("unknown" if settled else "started")
        assert call["started_at"] is not None
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0
        assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 599


def test_start_and_confirmed_unsent_refund_are_mutually_exclusive(setup):
    repo, _, _, root = setup
    lease = claim(repo, root["task_id"])
    started = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index in range(20):
            call_id = f"competing-unsent-{index}"
            repo.reserve_call(lease, call_id)
            barrier = Barrier(2)

            def compete(method, barrier=barrier, call_id=call_id):
                barrier.wait(timeout=10)
                try:
                    method(lease, call_id)
                except DispatchError as exc:
                    return exc.code
                return "accepted"

            futures = [pool.submit(compete, method) for method in (
                repo.call_started, repo.refund_unsent_call,
            )]
            outcomes = [future.result(timeout=15) for future in futures]
            assert sorted(outcomes) == ["accepted", "invalid_state"]
            with repo.db.connection() as c:
                row = c.execute("SELECT * FROM h2_model_calls WHERE call_id=?", (call_id,)).fetchone()
                started += int(row["status"] == "started")
                assert (row["started_at"] is not None) == (row["status"] == "started")
                assert c.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 600 - started


def test_partial_metadata_retains_body_and_paginated_read_audit(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    candidate = finish(repo, lease, summary="", content="abcdefghij")
    assert candidate["metadata_status"] == "pending"
    repo.finalize_incomplete(lease, candidate["result_ref"])
    page = repo.result_get(user, candidate["result_ref"], view="full", limit=5)
    assert page["content"] == "abcde" and page["next_offset"] == 5
    with repo.db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed'"
            ).fetchone()[0]
            == 0
        )
    page = repo.result_get(user, candidate["result_ref"], view="full", offset=5, limit=5)
    assert page["content"] == "fghij" and page["metadata_status"] == "incomplete_metadata"
    with repo.db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("view", ["summary", "full", "section"])
def test_result_read_coverage_survives_continue_without_duplicate_completion(setup, view):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    source = claim(repo, child["task_id"])
    detail = repo.store_text(source.caller(), "section", "abcdefghij")
    result = finish(repo, source, content="abcdefghij", sections={"detail": detail})
    section = "detail" if view == "section" else None
    first = claim(repo, root["task_id"])
    prefix = repo.result_get(first.caller(), result["result_ref"], view=view, section=section, limit=5)
    assert prefix["has_more"] is True and prefix["next_offset"] == 5
    with repo.db.connection() as c:
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type=?", (f"result.{view}_viewed",)
        ).fetchone()[0] == 0
    repo.continue_task(first, state(repo, first))
    second = claim(repo, root["task_id"])
    assert second.attempt_id != first.attempt_id
    suffix = repo.result_get(second.caller(), result["result_ref"], view=view, section=section, offset=5)
    assert suffix["has_more"] is False
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type=?", (f"result.{view}_viewed",)
        ).fetchall()
    assert len(completed) == 1
    identity = json.loads(completed[0][0])
    assert identity["reader_attempt_id"] == second.attempt_id
    assert identity["reader_task_id"] == root["task_id"]
    assert identity["reader_kind"] == "agent"
    repo.continue_task(second, state(repo, second))
    third = claim(repo, root["task_id"])
    repo.result_get(third.caller(), result["result_ref"], view=view, section=section, offset=5)
    with repo.db.connection() as c:
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type=?", (f"result.{view}_viewed",)
        ).fetchone()[0] == 1
        pages = [json.loads(row[0]) for row in c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='result.page_viewed'"
        )]
    assert [item["reader_attempt_id"] for item in pages] == [
        first.attempt_id, second.attempt_id, third.attempt_id,
    ]


@pytest.mark.parametrize("older_reader", ["agent", "unscoped_user"])
def test_result_coverage_recovers_old_agent_page_scope_but_not_user_export(setup, older_reader):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    result = finish(repo, claim(repo, child["task_id"]), content="abcdefghij")
    first = claim(repo, root["task_id"])
    with repo.db.connection(write=True) as c:
        repo._event(c, repo._task(c, child["task_id"]), "result.page_viewed", {
            "result_ref": result["result_ref"],
            "reader_attempt_id": first.attempt_id if older_reader == "agent" else None,
            "reader_thread_id": first.thread_id,
            "view": "full", "section": None, "start": 0, "end": 5,
        })
    repo.continue_task(first, state(repo, first))
    second = claim(repo, root["task_id"])
    repo.result_get(second.caller(), result["result_ref"], view="full", offset=5)
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchall()
    assert len(completed) == int(older_reader == "agent")
    if completed:
        assert json.loads(completed[0][0])["reader_task_id"] == root["task_id"]


@pytest.mark.parametrize("other_scope", ["user_export", "section", "result", "reader_thread", "follow_up"])
def test_result_page_coverage_does_not_merge_unrelated_read_scopes(setup, other_scope):
    repo, _, user, root = setup
    source_task = spawn(repo, user, "source")["data"]
    source = claim(repo, source_task["task_id"])
    detail = repo.store_text(source.caller(), "section", "abcdefghij")
    result = finish(repo, source, content="abcdefghij", sections={"detail": detail})
    if other_scope in {"reader_thread", "follow_up"}:
        reader_task = spawn(repo, user, "reader", input_refs=[result["full_ref"]])["data"]
        reader = claim(repo, reader_task["task_id"])
    else:
        reader = claim(repo, root["task_id"])
    first_caller, first_result, first_view, first_section = reader.caller(), result, "full", None
    if other_scope == "user_export":
        first_caller = user
    elif other_scope == "section":
        first_view, first_section = "section", "detail"
    elif other_scope == "result":
        other = spawn(repo, user, "other-source")["data"]
        first_result = finish(repo, claim(repo, other["task_id"]), content="abcdefghij")
    repo.result_get(first_caller, first_result["result_ref"], view=first_view, section=first_section, limit=5)
    if other_scope == "reader_thread":
        reader_task = spawn(repo, user, "other-reader", input_refs=[result["full_ref"]])["data"]
        reader = claim(repo, reader_task["task_id"])
    elif other_scope == "follow_up":
        finish(repo, reader)
        follow_up = repo.send(user, Send(
            thread_id=reader.thread_id, idempotency_key="follow-up-reader", content="Read again.",
            follow_up={"requested_budget": {"model_calls": 5, "wall_time_ms": 300000},
                       "output_contract": {"name": "fixture", "version": "1"},
                       "success_criteria": ["Read assigned result"]},
        ))["data"]
        old = reader
        reader = claim(repo, follow_up["task_id"])
        assert old.thread_id == reader.thread_id and old.task_id != reader.task_id
    repo.result_get(reader.caller(), result["result_ref"], view="full", offset=5)
    with repo.db.connection() as c:
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchone()[0] == 0
    repo.result_get(reader.caller(), result["result_ref"], view="full", limit=5)
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchall()
    assert len(completed) == 1
    assert json.loads(completed[0][0])["reader_task_id"] == reader.task_id


def test_result_coverage_gap_survives_new_attempt_until_missing_range_is_read(setup):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    result = finish(repo, claim(repo, child["task_id"]), content="abcdefghij")
    first = claim(repo, root["task_id"])
    repo.result_get(first.caller(), result["result_ref"], view="full", limit=3)
    repo.continue_task(first, state(repo, first))
    second = claim(repo, root["task_id"])
    repo.result_get(second.caller(), result["result_ref"], view="full", offset=5)
    with repo.db.connection() as c:
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchone()[0] == 0
    repo.continue_task(second, state(repo, second))
    third = claim(repo, root["task_id"])
    assert repo.result_get(third.caller(), result["result_ref"], view="full", offset=3, limit=2)["content"] == "de"
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchall()
    assert len(completed) == 1
    assert json.loads(completed[0][0])["reader_attempt_id"] == third.attempt_id


def test_older_completed_result_view_is_not_reemitted_after_continue(setup):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    result = finish(repo, claim(repo, child["task_id"]), content="abcdefghij")
    first = claim(repo, root["task_id"])
    old_identity = {"result_ref": result["result_ref"], "reader_attempt_id": first.attempt_id,
                    "reader_thread_id": first.thread_id, "view": "full", "section": None,
                    "start": 0, "end": 10}
    with repo.db.connection(write=True) as c:
        source_task = repo._task(c, child["task_id"])
        repo._event(c, source_task, "result.page_viewed", old_identity)
        repo._event(c, source_task, "result.full_viewed", old_identity, key="old-completed-view")
    repo.continue_task(first, state(repo, first))
    second = claim(repo, root["task_id"])
    repo.result_get(second.caller(), result["result_ref"], view="full")
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json,event_key FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchall()
    assert [(json.loads(row[0]), row[1]) for row in completed] == [(old_identity, "old-completed-view")]


def test_result_section_coverage_does_not_mix_named_sections(setup):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    source = claim(repo, child["task_id"])
    detail = repo.store_text(source.caller(), "section", "abcdefghij")
    result = finish(repo, source, sections={"alpha": detail, "beta": detail})
    first = claim(repo, root["task_id"])
    repo.result_get(first.caller(), result["result_ref"], view="section", section="alpha", limit=5)
    repo.continue_task(first, state(repo, first))
    second = claim(repo, root["task_id"])
    repo.result_get(second.caller(), result["result_ref"], view="section", section="beta", offset=5)
    with repo.db.connection() as c:
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type='result.section_viewed'"
        ).fetchone()[0] == 0
    repo.result_get(second.caller(), result["result_ref"], view="section", section="beta", limit=5)
    with repo.db.connection() as c:
        completed = c.execute(
            "SELECT payload_json FROM h2_events WHERE event_type='result.section_viewed'"
        ).fetchall()
    assert len(completed) == 1
    assert json.loads(completed[0][0])["section"] == "beta"


def test_event_pagination_does_not_skip(setup):
    repo, _, user, root = setup
    spawn(repo, user, "a")
    spawn(repo, user, "b")
    cursor, seen = "0", []
    while True:
        page = repo.status(user, [root["task_id"]], after_event_id=cursor, max_events=1)
        cursor = page["next_event_id"]
        seen.extend(e["event_id"] for e in page["events"])
        if not page["has_more"]:
            break
    assert len(seen) == 3 and seen == sorted(seen, key=int)


@pytest.mark.parametrize(
    "field,value", [("caller_thread_id", "forged"), ("priority", True), ("unknown", 1)]
)
def test_model_cannot_forge_internal_fields(field, value):
    with pytest.raises(ValidationError):
        Spawn.model_validate(
            {
                "dispatch_key": "x",
                "role": "a",
                "role_version": "1",
                "objective": "x",
                "success_criteria": ["x"],
                "input_refs": [],
                "output_contract": {"name": "x", "version": "1"},
                "requested_budget": {"model_calls": 1, "wall_time_ms": 100},
                field: value,
            }
        )


def redis_transport(repo):
    return RedisStreams(fakeredis.FakeRedis(decode_responses=True), repo.config.stream_name)


def test_outbox_publish_crash_replay_and_fenced_publisher(setup):
    repo, clock, user, _ = setup
    spawn(repo, user)
    transport = redis_transport(repo)
    first = repo.take_outbox(1)[0]
    transport.publish(json.loads(first["payload_json"]))
    clock.value += repo.config.publisher_lease_ms + 1
    second = repo.take_outbox(1)[0]
    assert second["outbox_id"] == first["outbox_id"]
    assert not repo.published(first["outbox_id"], first["publisher_token"], "late")
    eid = transport.publish(json.loads(second["payload_json"]))
    assert repo.published(second["outbox_id"], second["publisher_token"], eid)
    assert transport.client.xlen(transport.stream) == 2


def test_deadletter_is_blocked_and_redrive_keeps_history(setup):
    repo, clock, user, root = setup
    for _ in range(2):
        row = repo.take_outbox(1)[0]
        repo.publish_failed(row["outbox_id"], row["publisher_token"])
        clock.value += 2000
    assert status(repo, user, root["task_id"])["status"] == "blocked"
    assert not repo.take_outbox()
    repo.redrive(user, root["task_id"], "redrive")
    assert status(repo, user, root["task_id"])["status"] == "pending_dispatch"
    with repo.db.connection() as c:
        assert (
            c.execute("SELECT count(*) FROM h2_outbox WHERE status='dead_letter'").fetchone()[0]
            == 1
        )


def test_redis_loss_rebuilds_same_attempt_notification(setup):
    repo, clock, _, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    transport.client.flushall()
    clock.value += repo.config.republish_ms + 1
    repo.reconcile()
    Dispatcher(repo, transport).publish_batch()
    entry = transport.receive("rebuilt", block_ms=0)[0]
    assert entry[1]["attempt_id"] == root["attempt_id"]


def test_ack_crash_recovery_does_not_execute_twice(setup):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    calls = []

    async def handler(work):
        calls.append(work.task_id)
        return Submission(result_key="final", content="body", summary="summary")

    original_ack = transport.ack

    def broken_ack(entry):
        raise ConnectionError("fixture ACK failure")

    transport.ack = broken_ack
    worker = Worker(repo, transport, "first", handler)
    with pytest.raises(ConnectionError):
        asyncio.run(worker.run_once(block_ms=0))
    assert status(repo, user, root["task_id"])["status"] == "succeeded"
    transport.ack = original_ack
    asyncio.run(Worker(repo, transport, "recovery", handler).recover_once(idle_ms=0))
    assert calls == [root["task_id"]]
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0


def test_pel_reclaim_cannot_steal_valid_database_lease(setup):
    repo, _, _, _ = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    eid, payload = transport.receive("first", block_ms=0)[0]
    lease, _ = repo.claim(payload, "first")
    _, recovered, _ = transport.recover("second", idle_ms=0)
    assert recovered[0][0] == eid
    assert repo.claim(payload, "second") == (None, False)
    repo.heartbeat(lease)


def test_worker_metadata_failure_delivers_full_body(setup):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()

    async def handler(work):
        return Submission(result_key="final", content="important complete body")

    asyncio.run(Worker(repo, transport, "fixture", handler).run_once(block_ms=0))
    view = status(repo, user, root["task_id"])
    assert view["status"] == "partial"
    result = repo.result_get(user, view["result_ref"], view="full")
    assert result["content"] == "important complete body"


def test_wire_has_no_business_body_or_token(setup):
    repo, _, _, root = setup
    message = delivery(repo, root["task_id"])
    assert validate_message(message) == message
    with pytest.raises(DispatchError):
        validate_message({**message, "article": "body"})
    assert not {"content", "query", "api_key", "lease_token"} & message.keys()


@pytest.mark.parametrize("iteration", range(20))
def test_scripted_parent_child_wait_recovery_end_to_end(setup, iteration):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    a = spawn(repo, parent.caller(), "a")["data"]
    b = spawn(repo, parent.caller(), "b")["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[a["task_id"], b["task_id"]]))
    transport = redis_transport(repo)
    dispatcher = Dispatcher(repo, transport)
    dispatcher.publish_batch()

    async def handler(work):
        if work.task_id == root["task_id"]:
            targets = work.observations[0]["tasks"]
            assert {t["task_id"] for t in targets} == {a["task_id"], b["task_id"]}
            assert all(t["status"] == "succeeded" for t in targets)
            assert all(
                t["result"]["envelope"]["summary"] == "source-bound summary" for t in targets
            )
            assert all(not t["result"]["full_injected"] for t in targets)
            return Submission(
                result_key="final", content="combined source-bound findings", summary="combined"
            )
        return Submission(
            result_key="final", content="independent useful output", summary="source-bound summary"
        )

    worker = Worker(repo, transport, f"fixture-{iteration}", handler)

    async def execute():
        for _ in range(10):
            dispatcher.publish_batch()
            await worker.run_once(block_ms=0)
            if status(repo, user, root["task_id"])["status"] == "succeeded":
                break

    asyncio.run(execute())
    assert status(repo, user, root["task_id"])["status"] == "succeeded"
    assert all(
        t["status"] == "succeeded" for t in repo.status(user, [a["task_id"], b["task_id"]])["tasks"]
    )
    with repo.db.connection() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            c.execute(
                "SELECT count(*) FROM h2_attempts WHERE status IN ('queued','leased','running')"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("failure", ["raises", "changed_body", "still_incomplete", "invalid_type"])
def test_metadata_repair_failure_retains_original_body(setup, failure):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    calls = []

    async def handler(work):
        return Submission(result_key="final", content="important full analysis")

    async def repair(work, saved, original):
        calls.append(saved["result_ref"])
        if failure == "raises":
            raise RuntimeError("provider error must not erase analysis")
        if failure == "changed_body":
            return Submission(result_key="repair", content="replacement", summary="summary")
        if failure == "invalid_type":
            return {"summary": "not a validated Submission"}
        return original

    asyncio.run(
        Worker(repo, transport, "fixture", handler, metadata_repair=repair).run_once(block_ms=0)
    )
    view = status(repo, user, root["task_id"])
    assert view["status"] == "partial" and len(calls) == 1
    result = repo.result_get(user, view["result_ref"], view="full")
    assert result["content"] == "important full analysis"
    assert result["metadata_status"] == "incomplete_metadata"
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0


def test_successful_metadata_repair_reuses_body_artifact(setup):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()

    async def handler(work):
        return Submission(result_key="final", content="preserved body")

    async def repair(work, saved, original):
        return original.model_copy(update={"summary": "completed summary"})

    asyncio.run(
        Worker(repo, transport, "fixture", handler, metadata_repair=repair).run_once(block_ms=0)
    )
    assert status(repo, user, root["task_id"])["status"] == "succeeded"
    with repo.db.connection() as c:
        rows = c.execute(
            "SELECT full_ref,disposition FROM h2_results ORDER BY result_version"
        ).fetchall()
    assert len(rows) == 2 and rows[0]["full_ref"] == rows[1]["full_ref"]
    assert [r["disposition"] for r in rows] == ["candidate", "accepted"]


def test_result_database_failure_is_not_acknowledged(setup, monkeypatch):
    repo, clock, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    register = repo._register

    def fail_result(c, artifact):
        if artifact.kind == "result":
            raise sqlite3.OperationalError("fixture commit failure")
        register(c, artifact)

    async def handler(work):
        return Submission(result_key="final", content="body", summary="summary")

    monkeypatch.setattr(repo, "_register", fail_result)
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(Worker(repo, transport, "fixture", handler).run_once(block_ms=0))
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 1
    assert status(repo, user, root["task_id"])["status"] == "running"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 0
    monkeypatch.setattr(repo, "_register", register)
    clock.value += repo.config.lease_ms + 1
    asyncio.run(Worker(repo, transport, "recovery", handler).recover_once(idle_ms=0))
    Dispatcher(repo, transport).publish_batch()
    asyncio.run(Worker(repo, transport, "recovery", handler).run_once(block_ms=0))
    assert status(repo, user, root["task_id"])["status"] == "succeeded"


@pytest.mark.parametrize("place", ["full", "section", "checkpoint", "spawn"])
def test_child_cannot_smuggle_sibling_artifact(setup, place):
    repo, _, user, _ = setup
    a = spawn(repo, user, "a")["data"]
    b = spawn(repo, user, "b")["data"]
    owner, other = claim(repo, a["task_id"]), claim(repo, b["task_id"])
    ref = repo.store_text(other.caller(), "private", "sibling private text")
    with pytest.raises(DispatchError, match="not_found"):
        if place == "full":
            repo.submit(owner, Submission(result_key="bad", full_ref=ref, summary="stolen"))
        elif place == "section":
            finish(repo, owner, sections={"bad": ref})
        elif place == "checkpoint":
            checkpoint = state(repo, owner).model_copy(update={"context_ref": ref})
            repo.save_checkpoint(owner, checkpoint)
        else:
            spawn(
                repo,
                owner.caller(),
                "grandchild",
                input_refs=[ref],
                requested_budget={"model_calls": 10, "wall_time_ms": 300000},
            )
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 0


def test_created_artifact_can_be_passed_to_grandchild(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    ref = repo.store_text(lease.caller(), "context", "delegated public context")
    grandchild = spawn(
        repo,
        lease.caller(),
        "grandchild",
        input_refs=[ref],
        requested_budget={"model_calls": 10, "wall_time_ms": 300000},
    )["data"]
    assert repo.execution_input(claim(repo, grandchild["task_id"]))["brief"]["input_refs"] == [ref]


def test_child_without_spawn_permission_cannot_delegate(setup):
    repo, _, user, _ = setup
    repo.roles["analyst@2.0.0"] = ["result.get"]
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    with pytest.raises(DispatchError, match="forbidden"):
        spawn(repo, lease.caller(), "grandchild")


@pytest.mark.parametrize("damage", ["deleted", "corrupt"])
def test_corrupt_context_refuses_resume_without_model_call(setup, damage):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    checkpoint = state(repo, lease)
    ref = repo.save_checkpoint(lease, checkpoint)
    repo.fail(lease, "quota_error")
    path = repo.artifacts._path(lease.run_id, checkpoint.context_ref)
    if damage == "deleted":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(DispatchError, match="checkpoint_invalid"):
        repo.resume(
            user, Resume(task_id=child["task_id"], idempotency_key="resume", checkpoint_ref=ref)
        )
    assert len(status(repo, user, child["task_id"])["attempts"]) == 1


def test_corrupt_checkpoint_after_suspension_is_visible_failure(setup):
    repo, _, user, root = setup
    lease = claim(repo, root["task_id"])
    checkpoint = state(repo, lease)
    repo.suspend(lease, checkpoint)
    repo.send(user, Send(thread_id=root["thread_id"], idempotency_key="wake", content="continue"))
    repo.artifacts._path(lease.run_id, checkpoint.context_ref).unlink()
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    called = []

    async def handler(work):
        called.append(work.task_id)
        return Submission(result_key="final", content="body", summary="summary")

    asyncio.run(Worker(repo, transport, "fixture", handler).run_once(block_ms=0))
    assert not called
    assert status(repo, user, root["task_id"])["status"] == "failed"


@pytest.mark.parametrize("context_allowed", [None, False, True])
def test_wait_recovery_delivers_envelopes_and_optional_full(setup, context_allowed):
    repo, _, _, root = setup
    parent = claim(repo, root["task_id"])
    a = spawn(repo, parent.caller(), "a")["data"]
    b = spawn(repo, parent.caller(), "b")["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[a["task_id"], b["task_id"]]))
    for child in (a, b):
        finish(
            repo,
            claim(repo, child["task_id"]),
            key_points=[child["task_id"]],
            gaps=["explicit gap"],
        )
    restored = claim(repo, root["task_id"])
    checked = []

    def fits(data):
        tasks = data["observations"][0]["tasks"]
        assert all("result" in t for t in tasks)
        checked.append(len(data["messages"]))
        return context_allowed

    data = repo.execution_input(
        restored, context_accepts=fits if context_allowed is not None else None
    )
    targets = data["observations"][0]["tasks"]
    assert len(targets) == 2
    for target in targets:
        result = target["result"]
        assert result["envelope"]["gaps"] == ["explicit gap"]
        assert result["envelope"]["key_points"] == [target["task_id"]]
        assert result["full_injected"] is (context_allowed is True)
        assert ("full_content" in result) is (context_allowed is True)
    with repo.db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM h2_events WHERE event_type='result.summary_viewed'"
            ).fetchone()[0]
            == 2
        )
        assert c.execute(
            "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed'"
        ).fetchone()[0] == (2 if context_allowed else 0)


@pytest.mark.parametrize(
    "error,retryable,attempts",
    [
        ("transport_error", True, 2),
        ("transport_error", False, 1),
        ("quota_error", True, 1),
    ],
)
def test_worker_retry_is_bounded_same_model_only(setup, error, retryable, attempts):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()

    async def handler(work):
        raise DispatchError(error, retryable=retryable)

    asyncio.run(Worker(repo, transport, "fixture", handler).run_once(block_ms=0))
    view = status(repo, user, root["task_id"])
    assert len(view["attempts"]) == attempts
    if attempts == 2:
        assert repo.execution_input(claim(repo, root["task_id"]))["model"]["profile"] == "gpt"
    else:
        assert view["status"] == "failed"


def test_expired_queued_delivery_is_ack_safe(setup):
    repo, clock, user, _ = setup
    child = spawn(repo, user)["data"]
    clock.value += 600001
    assert repo.claim(delivery(repo, child["task_id"]), "fixture") == (None, True)
    assert status(repo, user, child["task_id"])["status"] == "timed_out"


def test_terminal_run_reconciliation_closes_children_and_conserves_budget(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    first = claim(repo, child["task_id"])
    grandchild = spawn(
        repo,
        first.caller(),
        "grandchild",
        requested_budget={"model_calls": 10, "wall_time_ms": 300000},
    )["data"]
    second = claim(repo, grandchild["task_id"])
    repo.reserve_call(second, "billable")
    repo.call_started(second, "billable")
    finish(repo, parent)
    repo.reconcile()
    assert status(repo, user, child["task_id"])["status"] == "cancelled"
    assert status(repo, user, grandchild["task_id"])["status"] == "cancelled"
    budget = status(repo, user, root["task_id"])["budget"]
    assert budget["local_available"] == 599 and budget["delegated"] == 0 and budget["consumed"] == 1


def test_parent_resume_keeps_descendant_spending(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    first = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(first, state(repo, first))
    grandchild = spawn(
        repo,
        first.caller(),
        "grandchild",
        requested_budget={"model_calls": 10, "wall_time_ms": 300000},
    )["data"]
    second = claim(repo, grandchild["task_id"])
    repo.reserve_call(second, "billable")
    finish(repo, second)
    repo.fail(first, "quota_error")
    repo.resume(
        user, Resume(task_id=child["task_id"], idempotency_key="resume", checkpoint_ref=checkpoint)
    )
    budget = status(repo, user, child["task_id"])["budget"]
    assert budget["consumed"] == 1 and budget["local_available"] == 29


def test_reconcile_cursor_reaches_later_tasks(setup):
    repo, clock, user, _ = setup
    children = [spawn(repo, user, f"child-{i}")["data"] for i in range(3)]
    clock.value += 600001
    cursor, changed = 0, []
    while True:
        page = repo.reconcile(after_task_rowid=cursor, limit=1)
        changed.extend(page["changed_task_ids"])
        cursor = page["next_task_rowid"]
        if not page["has_more"]:
            break
    assert set(changed) == {child["task_id"] for child in children}


def test_repeated_publisher_crashes_become_visible_deadletter(setup):
    repo, clock, user, root = setup
    first = repo.take_outbox(1)[0]
    clock.value += repo.config.publisher_lease_ms + 1
    second = repo.take_outbox(1)[0]
    assert first["outbox_id"] == second["outbox_id"]
    clock.value += repo.config.publisher_lease_ms + 1
    assert not repo.take_outbox(1)
    view = status(repo, user, root["task_id"])
    assert view["status"] == "blocked" and view["dispatch"]["retry_count"] == 2


@pytest.mark.parametrize(
    "parameters",
    [
        {"api_key": "fixture"},
        {"note": "sk-abcdefghijklmnopqrstuvwx"},
        {"note": "Bearer fixture"},
    ],
)
def test_credentials_rejected_before_snapshot_write(tmp_path, parameters):
    with pytest.raises(ValueError, match="credentials"):
        DispatchRepository(
            tmp_path / "missing.sqlite",
            tmp_path / "records",
            profiles={
                "model": {
                    "model_id": "fixture",
                    "base_url": "https://fixture.invalid",
                    "protocol": "fixture",
                    "parameters": parameters,
                }
            },
            roles={},
        )
    assert not (tmp_path / "missing.sqlite").exists()


def test_single_execution_slot_progresses_after_durable_wait(setup):
    repo, _, user, root = setup
    repo.config = replace(repo.config, run_concurrency=1, worker_concurrency=1)
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    assert repo.claim(delivery(repo, child["task_id"]), "fixture-worker") == (None, False)
    assert status(repo, user, child["task_id"])["status"] == "backpressured"
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[child["task_id"]]))
    finish(repo, claim(repo, child["task_id"]))
    assert status(repo, user, root["task_id"])["status"] == "pending_dispatch"
    finish(repo, claim(repo, root["task_id"]))
    assert status(repo, user, root["task_id"])["status"] == "succeeded"


def test_spawn_receipt_reports_backpressure_state(setup):
    repo, _, user, _ = setup
    repo.config = replace(repo.config, outbox_high_watermark=2, outbox_low_watermark=1)
    spawn(repo, user, "first-child")
    receipt = spawn(repo, user, "backpressured-child")
    assert receipt["data"]["status"] == "backpressured"
    assert receipt["data"]["dispatch_state"] == "backpressured"


def test_concurrent_spawn_cannot_overspend_parent(setup):
    repo, _, user, root = setup

    def allocate(i):
        try:
            return spawn(
                repo,
                user,
                f"child-{i}",
                requested_budget={"model_calls": 100, "wall_time_ms": 600000},
            )["ok"]
        except DispatchError as exc:
            assert exc.code == "budget_denied"
            return False

    with ThreadPoolExecutor(8) as pool:
        granted = list(pool.map(allocate, range(8)))
    assert sum(granted) == 5
    budget = status(repo, user, root["task_id"])["budget"]
    assert (
        budget["remaining"] == 600
        and budget["local_available"] == 100
        and budget["delegated"] == 500
    )


def test_concurrent_same_spawn_key_returns_one_task(setup):
    repo, _, user, root = setup
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: spawn(repo, user), range(2)))
    assert responses[0]["data"] == responses[1]["data"]
    assert sum(r["replayed"] for r in responses) == 1
    assert status(repo, user, root["task_id"])["budget"]["delegated"] == 30


def test_long_result_not_injected_even_when_context_allows(setup):
    repo, _, _, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[child["task_id"]]))
    finish(repo, claim(repo, child["task_id"]), content="a" * (16 * 1024 + 1))
    data = repo.execution_input(claim(repo, root["task_id"]), context_accepts=lambda _: True)
    result = data["observations"][0]["tasks"][0]["result"]
    assert not result["full_injected"] and "full_content" not in result


def test_explicit_result_grant_does_not_authorize_unlisted_sections(setup):
    repo, _, user, _ = setup
    source = spawn(repo, user, "source")["data"]
    owner = claim(repo, source["task_id"])
    section = repo.store_text(owner.caller(), "section", "unlisted detail")
    saved = finish(repo, owner, sections={"detail": section})
    with repo.db.connection() as c:
        row = c.execute(
            "SELECT full_ref,summary_ref FROM h2_results WHERE result_ref=?", (saved["result_ref"],)
        ).fetchone()
    child = spawn(repo, user, "reader", input_refs=[row["full_ref"]])["data"]
    reader = claim(repo, child["task_id"])
    full = repo.result_get(reader.caller(), saved["result_ref"], view="full")
    assert full["content"] == "full useful analysis" and full["section_refs"] == {}
    assert repo.result_get(user, saved["result_ref"], view="summary")["section_refs"] == {"detail": section}
    with pytest.raises(DispatchError, match="not_found"):
        repo.result_get(reader.caller(), saved["result_ref"], view="summary")
    with pytest.raises(DispatchError, match="not_found"):
        repo.result_get(reader.caller(), saved["result_ref"], view="section", section="detail")
    with pytest.raises(DispatchError, match="not_found"):
        repo.status(reader.caller(), [source["task_id"]])


def test_user_model_policy_update_cas_preserves_running_attempt(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    with repo.db.connection() as c:
        policy = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
    request = ModelPolicySet(
        idempotency_key="policy",
        expected_policy_ref=policy,
        selected_profile="deepseek",
        allowed_profiles=["gpt", "deepseek"],
    )
    receipt = repo.set_model_policy(user, request)
    assert receipt["data"]["running_attempts_unchanged"]
    assert repo.set_model_policy(user, request)["replayed"]
    assert repo.execution_input(parent)["model"]["profile"] == "gpt"
    child = spawn(repo, parent.caller())["data"]
    assert repo.execution_input(claim(repo, child["task_id"]))["model"]["profile"] == "deepseek"
    with pytest.raises(DispatchError) as error:
        repo.set_model_policy(user, request.model_copy(update={"idempotency_key": "stale-cas"}))
    assert error.value.code == "state_conflict"
    with pytest.raises(DispatchError, match="forbidden"):
        repo.set_model_policy(parent.caller(), request)


@pytest.mark.parametrize("explicit_profile", [None, "gpt"])
def test_spawn_rejects_model_policy_change_during_brief_preparation(setup, monkeypatch, explicit_profile):
    repo, _, user, root = setup
    running = claim(repo, root["task_id"])
    original = repo.artifacts.json
    before = {}
    tables = ("h2_threads", "h2_tasks", "h2_attempts", "h2_budgets", "h2_budget_entries",
              "h2_snapshots", "h2_artifacts", "h2_commands", "h2_events", "h2_outbox")

    def prepare(run_id, kind, value):
        artifact = original(run_id, kind, value)
        if kind == "brief" and not before:
            with repo.db.connection() as c:
                policy = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
            repo.set_model_policy(user, ModelPolicySet(
                idempotency_key="change-during-spawn", expected_policy_ref=policy,
                selected_profile="deepseek", allowed_profiles=["gpt", "deepseek"],
            ))
            with repo.db.connection() as c:
                before.update({table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in tables})
        return artifact

    monkeypatch.setattr(repo.artifacts, "json", prepare)
    with pytest.raises(DispatchError) as conflict:
        spawn(repo, running.caller(), "raced-policy", model_profile=explicit_profile)
    assert conflict.value.code == "state_conflict"
    with repo.db.connection() as c:
        assert {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in tables} == before
    assert status(repo, user, root["task_id"])["budget"]["local_available"] == 600
    child = spawn(repo, running.caller(), "raced-policy", model_profile=explicit_profile)["data"]
    expected = explicit_profile or "deepseek"
    child_input = repo.execution_input(claim(repo, child["task_id"]))
    assert child_input["model"]["profile"] == expected
    assert child_input["brief"]["model_profile"] == expected
    assert repo.execution_input(running)["model"]["profile"] == "gpt"
    assert spawn(repo, running.caller(), "raced-policy", model_profile=explicit_profile)["replayed"]


def test_model_revocation_stops_new_calls_without_fallback(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    lease = claim(repo, child["task_id"])
    with repo.db.connection() as c:
        policy = c.execute("SELECT model_policy_ref FROM h2_runs").fetchone()[0]
    repo.set_model_policy(
        user,
        ModelPolicySet(
            idempotency_key="revoke",
            expected_policy_ref=policy,
            selected_profile="deepseek",
            allowed_profiles=["deepseek"],
        ),
    )
    with pytest.raises(DispatchError, match="forbidden"):
        repo.reserve_call(lease, "not-allowed")
    assert len(status(repo, user, child["task_id"])["attempts"]) == 1
    assert repo.execution_input(lease)["model"]["profile"] == "gpt"


@pytest.fixture(params=["fake", "real"], ids=["fakeredis", "redis-service"])
def real_redis(setup, tmp_path, request):
    url = os.environ.get("H2_TEST_REDIS_URL")
    if request.param == "real" and not url:
        pytest.skip("H2_TEST_REDIS_URL not provided; no real Redis service started")
    source, clock, _, _ = setup
    config = replace(
        source.config, stream_name=f"finance-agent-next:integration-{uuid4().hex}:h2:dispatch"
    )
    repo = DispatchRepository(
        tmp_path / "real.sqlite",
        tmp_path / "real-records",
        profiles=source.profiles,
        roles=source.roles,
        config=config,
        clock=clock,
    )
    repo.migrate()
    root = repo.create_run(
        run_id="integration",
        session_ref="fixture",
        query="scripted test only",
        selected_profile="gpt",
        allowed_profiles=["gpt"],
        tools=TOOLS,
    )["data"]
    caller = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    transport = (
        RedisStreams.from_url(url, config.stream_name)
        if request.param == "real"
        else RedisStreams(fakeredis.FakeRedis(decode_responses=True), config.stream_name)
    )
    try:
        transport.client.ping()
        yield repo, clock, caller, root, transport
    finally:
        # Never FLUSHDB/FLUSHALL or touch another instance's keys.
        transport.client.delete(transport.stream)
        transport.client.close()


def test_real_redis_pel_recovery_does_not_steal_lease(real_redis):
    repo, _, user, root, transport = real_redis
    Dispatcher(repo, transport).publish_batch()
    entry, payload = transport.receive("first", block_ms=0)[0]
    lease, _ = repo.claim(payload, "first")
    repo.start(lease)
    _, recovered, _ = transport.recover("second", idle_ms=0)
    assert recovered[0][0] == entry
    assert repo.claim(payload, "second") == (None, False)
    saved = finish(repo, lease)
    assert repo.claim(payload, "second") == (None, True)
    transport.ack(entry)
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    assert (
        repo.result_get(user, saved["result_ref"], view="full")["content"] == "full useful analysis"
    )
    assert status(repo, user, root["task_id"])["status"] == "succeeded"


def test_real_redis_lost_stream_rebuilds_notification(real_redis):
    repo, clock, _, root, transport = real_redis
    Dispatcher(repo, transport).publish_batch()
    transport.client.delete(transport.stream)
    clock.value += repo.config.republish_ms + 1
    repo.reconcile()
    Dispatcher(repo, transport).publish_batch()
    entry, payload = transport.receive("rebuilt", block_ms=0)[0]
    assert payload["attempt_id"] == root["attempt_id"]
    lease, _ = repo.claim(payload, "rebuilt")
    repo.start(lease)
    finish(repo, lease)
    transport.ack(entry)


def test_redis_publish_commit_gap_duplicates_only_the_notification(real_redis, monkeypatch):
    repo, clock, user, root, transport = real_redis
    dispatcher = Dispatcher(repo, transport)

    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise sqlite3.OperationalError("fixture crash after XADD, before published commit")

        patch.setattr(repo, "published", unavailable)
        with pytest.raises(sqlite3.OperationalError):
            dispatcher.publish_batch()

    assert transport.client.xlen(transport.stream) == 1
    clock.value += repo.config.publisher_lease_ms + 1
    assert dispatcher.publish_batch() == 1
    entries = transport.receive("duplicates", count=2, block_ms=0)
    assert len(entries) == 2 and entries[0][1] == entries[1][1]
    executed = []

    async def handler(work):
        executed.append(work.task_id)
        await asyncio.sleep(0.02)
        return Submission(result_key="final", content="one accepted result", summary="summary")

    async def compete():
        workers = [Worker(repo, transport, f"duplicate-{i}", handler) for i in range(2)]
        await asyncio.gather(*(worker.process(*entry) for worker, entry in zip(workers, entries)))
        await workers[1].recover_once(idle_ms=0)

    asyncio.run(compete())
    assert executed == [root["task_id"]]
    assert status(repo, user, root["task_id"])["status"] == "succeeded"
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 1


def test_redis_result_commit_ack_failure_does_not_reexecute(real_redis, monkeypatch):
    from redis.exceptions import ConnectionError

    repo, _, user, root, transport = real_redis
    Dispatcher(repo, transport).publish_batch()
    executed = []

    async def handler(work):
        executed.append(work.task_id)
        return Submission(result_key="final", content="durable body", summary="summary")

    with monkeypatch.context() as patch:
        def unavailable(entry):
            raise ConnectionError("fixture ACK response lost")

        patch.setattr(transport, "ack", unavailable)
        with pytest.raises(ConnectionError):
            asyncio.run(Worker(repo, transport, "before-ack", handler).run_once(block_ms=0))

    assert status(repo, user, root["task_id"])["status"] == "succeeded"
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 1
    restarted = Worker(repo, transport, "after-ack", handler)
    asyncio.run(restarted.recover_once(idle_ms=0))
    assert executed == [root["task_id"]]
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 1


def test_redis_expired_attempt_fences_late_result_and_keeps_charge(real_redis):
    repo, clock, user, root, transport = real_redis
    dispatcher = Dispatcher(repo, transport)
    dispatcher.publish_batch()
    _, payload = transport.receive("lost-worker", block_ms=0)[0]
    old, _ = repo.claim(payload, "lost-worker")
    repo.start(old)
    repo.reserve_call(old, "synthetic-call")
    repo.call_started(old, "synthetic-call")
    clock.value += repo.config.lease_ms + 1
    executed = []

    async def handler(work):
        executed.append(work.attempt_id)
        return Submission(result_key="final", content="recovered result", summary="summary")

    recovery = Worker(repo, transport, "replacement", handler)
    asyncio.run(recovery.recover_once(idle_ms=0))
    assert not executed
    assert repo.delivery_settled(payload)
    dispatcher.publish_batch()
    asyncio.run(recovery.run_once(block_ms=0))
    assert len(executed) == 1 and executed[0] != old.attempt_id
    with pytest.raises(DispatchError, match="stale_lease"):
        finish(repo, old, content="late old result")
    view = status(repo, user, root["task_id"])
    assert view["status"] == "succeeded" and view["budget"]["consumed"] == 1
    assert [a["status"] for a in view["attempts"]] == ["expired", "succeeded"]
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert c.execute("SELECT status FROM h2_model_calls").fetchone()[0] == "unknown"
        assert c.execute("SELECT count(*) FROM h2_results WHERE disposition='late'").fetchone()[0] == 1


def test_redis_durable_wait_survives_worker_recreation(real_redis):
    repo, _, user, root, transport = real_redis
    dispatcher = Dispatcher(repo, transport)
    dispatcher.publish_batch()
    entry, payload = transport.receive("parent-before-wait", block_ms=0)[0]
    parent, _ = repo.claim(payload, "parent-before-wait")
    repo.start(parent)
    child = spawn(repo, parent.caller())["data"]
    checkpoint = state(repo, parent)
    repo.suspend(parent, checkpoint, Wait(task_ids=[child["task_id"]]))
    transport.ack(entry)
    assert status(repo, user, root["task_id"])["status"] == "waiting"

    reopened = DispatchRepository(
        repo.db.path,
        repo.artifacts.root,
        profiles=repo.profiles,
        roles=repo.roles,
        config=repo.config,
        clock=repo.clock,
    )
    observed = []

    async def handler(work):
        observed.append(work.task_id)
        if work.task_id == root["task_id"]:
            assert work.attempt_id != parent.attempt_id
            assert work.checkpoint["context_ref"] == checkpoint.context_ref
            returned = work.observations[0]["tasks"][0]
            assert returned["task_id"] == child["task_id"]
            assert returned["status"] == "succeeded" and returned["result_ref"]
        return Submission(result_key="final", content="source-bound result", summary="summary")

    worker = Worker(reopened, transport, "after-restart", handler)
    publisher = Dispatcher(reopened, transport)
    for _ in range(2):
        publisher.publish_batch()
        asyncio.run(worker.run_once(block_ms=0))
    assert observed == [child["task_id"], root["task_id"]]
    assert status(reopened, user, root["task_id"])["status"] == "succeeded"
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0


def test_redis_multiworker_campaign_has_one_accepted_result_per_task(real_redis):
    repo, _, user, root, transport = real_redis
    parent = claim(repo, root["task_id"])
    children = [
        spawn(
            repo, parent.caller(), f"campaign-{i}",
            requested_budget={"model_calls": 5, "wall_time_ms": 600000},
        )["data"]
        for i in range(24)
    ]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[c["task_id"] for c in children]))
    Dispatcher(repo, transport).publish_batch()
    executed = []

    async def handler(work):
        executed.append(work.task_id)
        await asyncio.sleep(0.01)
        if work.task_id == root["task_id"]:
            returned = work.observations[0]["tasks"]
            assert len(returned) == 24 and all(t["status"] == "succeeded" for t in returned)
        return Submission(result_key="final", content="campaign result", summary="summary")

    async def campaign():
        workers = [Worker(repo, transport, f"campaign-{i}", handler) for i in range(4)]
        for _ in range(30):
            Dispatcher(repo, transport).publish_batch()
            await asyncio.gather(*(w.run_once(block_ms=0) for w in workers))
            await workers[0].recover_once(idle_ms=0)
            if status(repo, user, root["task_id"])["status"] == "succeeded":
                await workers[0].recover_once(idle_ms=0)
                return
        pytest.fail("bounded multiworker campaign did not settle")

    asyncio.run(campaign())
    expected = {root["task_id"], *(child["task_id"] for child in children)}
    assert set(executed) == expected and len(executed) == len(expected)
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 25
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_child_error_wakes_parent_with_actual_failure(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[child["task_id"]]))
    repo.fail(claim(repo, child["task_id"]), "quota_error")
    assert status(repo, user, root["task_id"])["status"] == "pending_dispatch"
    task = repo.execution_input(claim(repo, root["task_id"]))["observations"][0]["tasks"][0]
    assert task["status"] == "failed" and task["result_ref"] is None
    assert task["attempts"][-1]["error_code"] == "quota_error"


def test_checkpoint_cannot_claim_unseen_mailbox_was_consumed(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    checkpoint = state(repo, parent)
    message = repo.send(
        user, Send(thread_id=root["thread_id"], idempotency_key="m2", content="new instruction")
    )
    unseen = checkpoint.model_copy(update={"consumed_message_seq": message["data"]["sequence"]})
    with pytest.raises(DispatchError) as error:
        repo.save_checkpoint(parent, unseen)
    assert error.value.code == "invalid_arguments"
    delivered = repo.mailbox(parent)
    assert delivered[-1]["sequence"] == message["data"]["sequence"]
    repo.save_checkpoint(parent, unseen)
    with pytest.raises(DispatchError) as error:
        repo.save_checkpoint(parent, checkpoint)
    assert error.value.code == "invalid_arguments"


def test_cancelling_waiting_parent_cannot_wake_a_new_attempt(setup):
    repo, _, user, root = setup
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())["data"]
    repo.suspend(parent, state(repo, parent), Wait(task_ids=[child["task_id"]]))
    repo.cancel(user, Cancel(task_id=root["task_id"], idempotency_key="cancel-tree"))
    assert status(repo, user, root["task_id"])["status"] == "cancelled"
    assert status(repo, user, child["task_id"])["status"] == "cancelled"
    with repo.db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM h2_attempts WHERE status IN ('queued','leased','running')"
            ).fetchone()[0]
            == 0
        )


def test_execution_brief_contains_trusted_runtime_boundaries(setup):
    repo, _, user, root = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    brief = repo.execution_input(lease)["brief"]
    assert brief["task_id"] == child["task_id"] and brief["thread_id"] == child["thread_id"]
    assert brief["parent_task_id"] == root["task_id"] and brief["depth"] == 1
    assert brief["allowed_tools"] == sorted(TOOLS)
    assert brief["budget"]["local_available"] == 30 and brief["deadline_at"] == child["deadline_at"]
    assert "lease_token" not in brief and "api_key" not in brief


def test_transport_retry_cap_keeps_each_charge_and_attempt(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    for i in range(repo.config.max_attempts):
        lease = claim(repo, child["task_id"])
        assert repo.execution_input(lease)["model"]["profile"] == "gpt"
        repo.reserve_call(lease, f"request-{i}")
        repo.call_started(lease, f"request-{i}")
        repo.fail(lease, "transport_error", retryable=True)
    view = status(repo, user, child["task_id"])
    assert view["status"] == "failed" and len(view["attempts"]) == repo.config.max_attempts
    assert view["budget"]["consumed"] == repo.config.max_attempts
    with repo.db.connection() as c:
        assert (
            c.execute("SELECT count(*) FROM h2_model_calls WHERE status='unknown'").fetchone()[0]
            == repo.config.max_attempts
        )


@pytest.mark.parametrize("fault", ["transport", "lease_expired"])
def test_normal_suspensions_do_not_consume_failure_recovery_allowance(setup, fault):
    repo, clock, user, root = setup
    child = spawn(repo, user)["data"]
    finish(repo, claim(repo, child["task_id"]))
    lease = claim(repo, root["task_id"])
    for _ in range(repo.config.max_attempts + 1):
        checkpoint = state(repo, lease)
        repo.suspend(lease, checkpoint, Wait(task_ids=[child["task_id"]]))
        lease = claim(repo, root["task_id"])
    assert status(repo, user, root["task_id"])["budget"]["consumed"] == 0
    repo.reserve_call(lease, "after-normal-progress")
    repo.call_started(lease, "after-normal-progress")
    if fault == "transport":
        repo.fail(lease, "transport_error", retryable=True)
    else:
        clock.value += repo.config.lease_ms + 1
        repo.reconcile()

    view = status(repo, user, root["task_id"])
    assert view["status"] == "pending_dispatch"
    assert view["budget"]["consumed"] == 1
    assert sum(a["status"] == "suspended" for a in view["attempts"]) == repo.config.max_attempts + 1
    recovered = claim(repo, root["task_id"])
    work = repo.execution_input(recovered)
    assert work["model"]["profile"] == "gpt"
    assert work["checkpoint"]["context_ref"] == checkpoint.context_ref
    with pytest.raises(DispatchError, match="stale_lease"):
        repo.reserve_call(lease, "late-call")
    finish(repo, recovered)
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 2
        assert c.execute(
            "SELECT status FROM h2_model_calls WHERE call_id='after-normal-progress'"
        ).fetchone()[0] == "unknown"


def test_mixed_faults_share_cap_without_reset_on_normal_progress(setup):
    repo, clock, user, root = setup
    child = spawn(repo, user)["data"]
    finish(repo, claim(repo, child["task_id"]))
    for i in range(repo.config.max_attempts):
        lease = claim(repo, root["task_id"])
        repo.suspend(lease, state(repo, lease), Wait(task_ids=[child["task_id"]]))
        lease = claim(repo, root["task_id"])
        call_id = f"mixed-fault-{i}"
        repo.reserve_call(lease, call_id)
        repo.call_started(lease, call_id)
        if i % 2:
            clock.value += repo.config.lease_ms + 1
            repo.reconcile()
        else:
            repo.fail(lease, "transport_error", retryable=True)
        expected = "failed" if i == repo.config.max_attempts - 1 else "pending_dispatch"
        assert status(repo, user, root["task_id"])["status"] == expected
    view = status(repo, user, root["task_id"])
    assert len(view["attempts"]) == repo.config.max_attempts * 2
    assert view["budget"]["consumed"] == repo.config.max_attempts
    with repo.db.connection() as c:
        assert repo._failed_attempt_count(c, root["task_id"]) == repo.config.max_attempts
        assert c.execute("SELECT count(*) FROM h2_model_calls WHERE status='unknown'").fetchone()[0] == repo.config.max_attempts


def test_continue_requeues_same_task_once_with_checkpoint_and_fences_old_lease(setup):
    repo, _, user, root = setup
    lease = claim(repo, root["task_id"])
    deadline = status(repo, user, root["task_id"])["deadline_at"]
    old_delivery = delivery(repo, lease.task_id)
    checkpoint = state(repo, lease)
    next_attempt = repo.continue_task(lease, checkpoint)
    view = status(repo, user, root["task_id"])
    assert view["status"] == "pending_dispatch"
    assert [a["status"] for a in view["attempts"]] == ["suspended", "queued"]
    assert view["budget"]["consumed"] == 0
    assert view["deadline_at"] == deadline
    assert repo.claim(old_delivery, "duplicate-worker") == (None, True)
    with pytest.raises(DispatchError, match="stale_lease"):
        repo.continue_task(lease, checkpoint)
    recovered = claim(repo, root["task_id"])
    assert recovered.attempt_id == next_attempt
    assert repo.execution_input(recovered)["checkpoint"]["context_ref"] == checkpoint.context_ref
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_waits").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_checkpoints").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_events WHERE event_type='task.continued'").fetchone()[0] == 1


def test_continue_storage_failure_rolls_back_and_worker_does_not_ack(setup, monkeypatch):
    repo, _, user, root = setup
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    acknowledgements = []
    monkeypatch.setattr(transport, "ack", acknowledgements.append)
    real_outbox = repo._outbox

    def fail_after_insert(*args):
        real_outbox(*args)
        raise sqlite3.OperationalError("fixture continue transaction failure")

    monkeypatch.setattr(repo, "_outbox", fail_after_insert)

    async def execute(_work, capabilities):
        ref = await capabilities.store_context({"step": "saved action receipt"})
        return await capabilities.continue_task(CheckpointState(
            current_phase="research", completed_steps=["action-1"], pending_steps=[],
            input_refs=[], context_ref=ref, consumed_message_seq=1, last_event_id="0",
        ))

    with pytest.raises(sqlite3.OperationalError, match="continue transaction"):
        asyncio.run(Worker(repo, transport, "continue-worker", executor=execute).run_once(block_ms=0))
    assert acknowledgements == []
    assert status(repo, user, root["task_id"])["status"] == "running"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_attempts").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_checkpoints").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_outbox").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_events WHERE event_type='task.continued'").fetchone()[0] == 0


def test_heartbeat_storage_failure_stops_handler_without_ack(setup, monkeypatch):
    repo, _, user, root = setup
    repo.config = replace(repo.config, lease_ms=60)
    transport = redis_transport(repo)
    Dispatcher(repo, transport).publish_batch()
    stopped = []

    async def handler(work):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(work.task_id)

    def unavailable(lease):
        raise sqlite3.OperationalError("fixture heartbeat DB failure")

    monkeypatch.setattr(repo, "heartbeat", unavailable)

    async def execute():
        await asyncio.wait_for(
            Worker(repo, transport, "fixture", handler).run_once(block_ms=0), timeout=3
        )

    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(execute())
    assert stopped == [root["task_id"]]
    assert transport.client.xpending(transport.stream, transport.group)["pending"] == 1
    assert status(repo, user, root["task_id"])["status"] == "running"
