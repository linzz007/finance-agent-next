from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import fakeredis

from policy_impact.dispatch import CallerContext, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Spawn, Submission
from policy_impact.dispatch.transport import Dispatcher, RedisStreams


class Clock:
    def __init__(self, value=1_000_000):
        self.value = value

    def __call__(self):
        return self.value


def make_repo(tmp_path: Path, clock: Clock, config: SchedulerConfig):
    repo = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        clock=clock,
        config=config,
        profiles={
            "fixture": {
                "model_id": "fixture",
                "base_url": "https://fixture.invalid",
                "protocol": "fixture",
            }
        },
        roles={"analyst@1": ["agent.spawn"]},
    )
    repo.migrate()
    root = repo.create_run(
        run_id="pressure",
        session_ref="session",
        query="queue pressure",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=["agent.spawn"],
    )["data"]
    caller = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    return repo, root, caller


def spawn(repo, caller, key, *, priority=0):
    return repo.spawn(
        caller,
        Spawn(
            dispatch_key=key,
            role="analyst",
            role_version="1",
            objective="inspect queue",
            success_criteria=["return a result"],
            input_refs=[],
            output_contract={"name": "fixture.result", "version": "1"},
            requested_budget={"model_calls": 1, "wall_time_ms": 1000},
            priority=priority,
        ),
    )["data"]


def outbox_payload(repo, task_id):
    with repo.db.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM h2_outbox WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return json.loads(row[0])


def test_watermarks_do_not_lock_accepted_work_and_release_at_low_watermark(tmp_path):
    clock = Clock()
    config = SchedulerConfig(
        stream_name="h2:pressure",
        outbox_high_watermark=2,
        outbox_low_watermark=1,
    )
    repo, root, caller = make_repo(tmp_path, clock, config)
    first = spawn(repo, caller, "first")
    second = spawn(repo, caller, "second")

    assert repo.status(caller, [second["task_id"]])["tasks"][0]["status"] == "backpressured"
    pressure = repo.queue_pressure()
    assert pressure["over_high_watermark"] is True

    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), config.stream_name)
    dispatcher = Dispatcher(repo, transport)
    assert dispatcher.publish_batch(limit=1) == 1
    assert dispatcher.publish_batch(limit=1) == 1
    released = repo.queue_pressure()
    assert released["pending_outbox"] == 1
    remaining = repo.status(caller, [second["task_id"]])["tasks"][0]["status"]
    assert remaining in {"pending_dispatch", "queued"}
    assert repo.status(caller, [root["task_id"], first["task_id"]])["tasks"]


def test_aging_allows_an_older_lower_priority_task_to_be_published_first(tmp_path):
    clock = Clock()
    config = SchedulerConfig(
        stream_name="h2:aging",
        outbox_high_watermark=100,
        outbox_low_watermark=50,
        aging_step_ms=1000,
    )
    repo, root, caller = make_repo(tmp_path, clock, config)
    clock.value += 10_000
    newer = spawn(repo, caller, "newer", priority=5)
    row = repo.take_outbox(limit=1)[0]
    assert row["task_id"] == root["task_id"]
    assert row["task_id"] != newer["task_id"]


def test_safe_trim_keeps_unsettled_and_pending_entries(tmp_path):
    clock = Clock()
    config = replace(SchedulerConfig(stream_name="h2:trim"), outbox_high_watermark=1000)
    repo, root, _caller = make_repo(tmp_path, clock, config)
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), config.stream_name)
    dispatcher = Dispatcher(repo, transport)
    assert dispatcher.publish_batch() == 1
    pending_result = dispatcher.trim_safe(min_age_ms=0, dry_run=True)
    assert pending_result["candidates"] == []
    assert any(row["reason"] == "delivery_unsettled" for row in pending_result["retained"])

    payload = outbox_payload(repo, root["task_id"])
    lease, safe = repo.claim(payload, "trim-worker")
    assert lease is not None and safe is False
    repo.start(lease)
    saved = repo.submit(lease, Submission(result_key="final", content="done"))
    repo.finalize_incomplete(lease, saved["result_ref"])
    now = int(time.time_ns() // 1_000_000) + 1000
    candidate = dispatcher.trim_safe(min_age_ms=0, dry_run=True, now_ms=now)
    assert candidate["candidates"], candidate
    removed = dispatcher.trim_safe(min_age_ms=0, dry_run=False, now_ms=now)
    assert removed["removed"] == candidate["candidates"]
    assert transport.client.xlen(config.stream_name) == 0

    repo2, _, _ = make_repo(tmp_path / "pending", clock, replace(config, stream_name="h2:trim-pending"))
    transport2 = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True), "h2:trim-pending", group="pending-workers"
    )
    Dispatcher(repo2, transport2).publish_batch()
    assert transport2.receive("pending-worker", block_ms=0)
    pending = transport2.trim_safe(repo2, min_age_ms=0, dry_run=True, now_ms=now)
    assert pending["candidates"] == []
    assert any(row["reason"] == "pending" for row in pending["retained"])
