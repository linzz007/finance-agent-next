"""Real process termination against isolated SQLite; no Redis service or model."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from contextlib import contextmanager
from pathlib import Path

import fakeredis
import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Cancel, CheckpointState, Spawn, Submission
from policy_impact.dispatch.transport import Dispatcher, RedisStreams
from policy_impact.dispatch.worker import Continue, Worker

NOW = 1_000_000
TOOLS = ["agent.spawn", "agent.status", "agent.cancel", "result.get"]


def _repo(directory, now=NOW):
    directory = Path(directory)
    return DispatchRepository(
        directory / "finance.sqlite", directory / "records", clock=lambda: now,
        config=SchedulerConfig(stream_name="finance-agent-next:fixture:process-faults", budget_policy="hierarchical"),
        profiles={"fixture": {"model_id": "fixture", "base_url": "https://fixture.invalid/v1",
                               "protocol": "openai-compatible"}},
        roles={"analyst@2.0.0": TOOLS},
    )


def _create(repo, run_id="fixture"):
    root = repo.create_run(
        run_id=run_id, session_ref="fixture-session", query="Preserve source conditions",
        selected_profile="fixture", allowed_profiles=["fixture"], tools=TOOLS,
    )["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    return user, root


@pytest.fixture
def setup(tmp_path):
    repo = _repo(tmp_path)
    repo.migrate()
    user, root = _create(repo)
    return repo, user, root


def _request():
    return Spawn(
        dispatch_key="durable-child", role="analyst", role_version="2.0.0",
        objective="Explain the conditional source reasoning", success_criteria=["Preserve gaps"],
        input_refs=[], output_contract={"name": "fixture.result", "version": "1"},
        requested_budget={"model_calls": 30, "wall_time_ms": 600000},
    )


def _delivery(repo, task_id):
    with repo.db.connection() as c:
        row = c.execute(
            "SELECT payload_json FROM h2_outbox WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return json.loads(row[0])


def _claim(repo, payload):
    lease, ack_safe = repo.claim(payload, "fixture-owner")
    assert lease and not ack_safe
    repo.start(lease)
    return lease


def _checkpoint(repo, lease):
    repo.mailbox(lease)
    ref = repo.store_text(lease.caller(), "context", "Completed read; next step is synthesis.")
    return CheckpointState(
        current_phase="synthesis", completed_steps=["read-source"], pending_steps=["synthesize"],
        input_refs=[], context_ref=ref, consumed_message_seq=1, last_event_id="0",
    )


@contextmanager
def _owned_process(target, *args):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(*args, child))
    try:
        process.start()
        child.close()
        yield process, parent
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join(15)
            assert not process.is_alive(), "test-owned child did not stop"
            process.close()
        parent.close()
        child.close()


def _receive(process, pipe):
    assert pipe.poll(30), f"process did not reach barrier; exit_code={process.exitcode}"
    try:
        return pipe.recv()
    except EOFError:
        pytest.fail(f"process exited before barrier; exit_code={process.exitcode}")


def _kill(process):
    process.terminate()
    process.join(15)
    assert not process.is_alive() and process.exitcode != 0


def _pause(pipe, value):
    pipe.send(value)
    pipe.recv()


def _spawn_process(directory, caller, boundary, pipe):
    repo = _repo(directory)
    if boundary == "before-commit":
        receipt = repo._receipt

        def pause_receipt(*args, **kwargs):
            result = receipt(*args, **kwargs)
            _pause(pipe, "uncommitted")
            return result

        repo._receipt = pause_receipt
    result = repo.spawn(caller, _request())
    _pause(pipe, result)


def _continue_process(directory, payload, pipe):
    repo = _repo(directory)
    lease = _claim(repo, payload)
    checkpoint = _checkpoint(repo, lease)
    event = repo._event

    def pause_event(c, task, kind, *args, **kwargs):
        result = event(c, task, kind, *args, **kwargs)
        if kind == "task.continued":
            _pause(pipe, "uncommitted")
        return result

    repo._event = pause_event
    repo.continue_task(lease, checkpoint)


class _PauseAck:
    def __init__(self, pipe):
        self.pipe = pipe

    def ack(self, entry_id):
        _pause(self.pipe, ("before-ack", entry_id))


class _Acks:
    def __init__(self):
        self.entries = []

    def ack(self, entry_id):
        self.entries.append(entry_id)


def _worker_process(directory, payload, outcome, pipe):
    repo = _repo(directory)

    async def handler(_work):
        return Submission(result_key="final", content="Full useful analysis.", summary="Source-bound summary")

    async def executor(work, capabilities):
        ref = await capabilities.store_context({"progress": "read-source", "next": "synthesize"})
        return Continue(CheckpointState(
            current_phase="synthesis", completed_steps=["read-source"], pending_steps=["synthesize"],
            input_refs=[], context_ref=ref, consumed_message_seq=1, last_event_id="0",
        ))

    worker = Worker(repo, _PauseAck(pipe), "before-crash", executor=executor) if outcome == "continue" else Worker(
        repo, _PauseAck(pipe), "before-crash", handler,
    )
    asyncio.run(worker.process("original-entry", payload))


def _active_process(directory, payload, pipe):
    repo = _repo(directory)
    lease = _claim(repo, payload)
    checkpoint = _checkpoint(repo, lease)
    repo.save_checkpoint(lease, checkpoint)
    repo.reserve_call(lease, "synthetic-call")
    repo.call_started(lease, "synthetic-call")
    _pause(pipe, (lease, checkpoint.model_dump()))


def _claim_process(directory, pipe):
    repo = _repo(directory)
    pipe.send("ready")
    while True:
        payload = pipe.recv()
        if payload is None:
            return
        pipe.send("armed")
        assert pipe.recv() == "claim"
        pipe.send(repo.claim(payload, f"contender-{multiprocessing.current_process().pid}"))


def _publisher_process(directory, pipe):
    repo = _repo(directory)

    class Transport:
        stream = repo.config.stream_name

        def publish(self, payload):
            _pause(pipe, payload)
            return "controlled-entry"

    Dispatcher(repo, Transport()).publish_batch()


def _view(repo, user, root):
    return repo.status(user, [root["task_id"]])["tasks"][0]


def _rows(repo, table):
    # Table identifiers are test literals, not external input.
    with repo.db.connection() as c:
        return [dict(row) for row in c.execute(f"SELECT * FROM {table}")]


def _integrity(repo):
    with repo.db.connection() as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert not c.execute(
            "SELECT task_id FROM h2_attempts WHERE status IN ('queued','leased','running') "
            "GROUP BY task_id HAVING count(*)>1",
        ).fetchall()


def test_process_kill_before_spawn_commit_rolls_back_all_accepted_work(setup, tmp_path):
    repo, user, root = setup
    with _owned_process(_spawn_process, tmp_path, user, "before-commit") as (process, pipe):
        assert _receive(process, pipe) == "uncommitted"
        assert len(_rows(repo, "h2_tasks")) == len(_rows(repo, "h2_budgets")) == 1
        _kill(process)
    reopened = _repo(tmp_path)
    assert len(_rows(reopened, "h2_tasks")) == len(_rows(reopened, "h2_threads")) == 1
    assert len(_rows(reopened, "h2_budgets")) == len(_rows(reopened, "h2_outbox")) == 1
    assert [row["operation"] for row in _rows(reopened, "h2_commands")] == ["run.create"]
    result = reopened.spawn(user, _request())
    assert result["replayed"] is False
    assert reopened.spawn(user, _request())["data"] == result["data"]
    assert len(_rows(reopened, "h2_tasks")) == len(_rows(reopened, "h2_budgets")) == 2
    budget = _view(reopened, user, root)["budget"]
    assert budget["local_available"] + budget["delegated"] == budget["allocated"] == 600
    assert budget["delegated"] == 30
    _integrity(reopened)


def test_process_kill_after_spawn_commit_retains_receipt_budget_and_outbox(setup, tmp_path):
    _repo_before, user, _root = setup
    with _owned_process(_spawn_process, tmp_path, user, "after-commit") as (process, pipe):
        original = _receive(process, pipe)
        assert original["ok"]
        _kill(process)
    reopened = _repo(tmp_path)
    replay = reopened.spawn(user, _request())
    assert replay["replayed"] is True and replay["data"] == original["data"]
    assert len(_rows(reopened, "h2_tasks")) == len(_rows(reopened, "h2_outbox")) == 2
    assert sorted(row["operation"] for row in _rows(reopened, "h2_commands")) == ["agent.spawn", "run.create"]
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), reopened.config.stream_name, group="fixture")
    Dispatcher(reopened, transport).publish_batch()
    assert transport.client.xlen(transport.stream) == 2
    child = replay["data"]
    budget = reopened.status(user, [child["task_id"]])["tasks"][0]["budget"]
    assert budget["requested"] == budget["allocated"] == 30
    _integrity(reopened)


def test_dispatcher_process_kill_after_notification_before_db_mark_republishes_safely(setup, tmp_path):
    repo, user, root = setup
    with _owned_process(_publisher_process, tmp_path) as (process, pipe):
        first_delivery = _receive(process, pipe)
        assert first_delivery == _delivery(repo, root["task_id"])
        assert _rows(repo, "h2_outbox")[0]["status"] == "publishing"
        _kill(process)
    reopened = _repo(tmp_path, NOW + repo.config.publisher_lease_ms + 1)
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), reopened.config.stream_name, group="fixture")
    assert Dispatcher(reopened, transport).publish_batch() == 1
    second_entry, second_delivery = transport.receive("fixture", block_ms=0)[0]
    assert second_delivery == first_delivery
    outbox = _rows(reopened, "h2_outbox")
    assert len(outbox) == 1 and outbox[0]["retry_count"] == 1 and outbox[0]["status"] == "published"
    acks, executed = _Acks(), []

    async def handler(work):
        executed.append(work.attempt_id)
        return Submission(result_key="final", content="Published once logically.", summary="Summary")

    worker = Worker(reopened, acks, "after-publisher-crash", handler)
    assert asyncio.run(worker.process("controlled-entry", first_delivery))
    assert asyncio.run(worker.process(second_entry, second_delivery)) is False
    assert len(executed) == len(_rows(reopened, "h2_results")) == 1
    assert acks.entries == ["controlled-entry", second_entry]
    assert _view(reopened, user, root)["budget"]["consumed"] == 0
    _integrity(reopened)


@pytest.mark.parametrize("outcome", ["final", "continue"])
def test_real_worker_kill_after_commit_before_ack_is_not_reexecuted(setup, tmp_path, outcome):
    repo, user, root = setup
    payload = _delivery(repo, root["task_id"])
    with _owned_process(_worker_process, tmp_path, payload, outcome) as (process, pipe):
        assert _receive(process, pipe) == ("before-ack", "original-entry")
        view = _view(repo, user, root)
        assert view["status"] == ("succeeded" if outcome == "final" else "pending_dispatch")
        _kill(process)
    reopened, transport = _repo(tmp_path), _Acks()
    executed = []

    async def executor(work, capabilities):
        executed.append(work.attempt_id)
        assert outcome == "continue", "old committed delivery executed twice"
        assert work.checkpoint["completed_steps"] == ["read-source"]
        assert work.checkpoint["current_phase"] == "synthesis"
        assert work.checkpoint["consumed_message_seq"] == 1
        assert not work.messages, "consumed mailbox was replayed"
        assert json.loads(await capabilities.load_context(work.checkpoint["context_ref"])) == {
            "progress": "read-source", "next": "synthesize",
        }
        return Submission(result_key="resumed-final", content="Saved progress resumed.", summary="Resumed summary")

    worker = Worker(reopened, transport, "after-crash", executor=executor)
    assert asyncio.run(worker.process("duplicate-entry", payload)) is False
    assert transport.entries == ["duplicate-entry"] and not executed
    if outcome == "continue":
        assert len(_rows(reopened, "h2_attempts")) == 2
        assert len(_rows(reopened, "h2_checkpoints")) == 1
        assert len(_rows(reopened, "h2_outbox")) == 2
        assert asyncio.run(worker.process("successor-entry", _delivery(reopened, root["task_id"])))
        assert len(executed) == 1
    result = _view(reopened, user, root)
    assert result["status"] == "succeeded" and result["budget"]["consumed"] == 0
    accepted = [row for row in _rows(reopened, "h2_results") if row["disposition"] == "accepted"]
    assert len(accepted) == 1
    full = reopened.result_get(user, result["result_ref"], view="full")
    assert full["content"] == ("Full useful analysis." if outcome == "final" else "Saved progress resumed.")
    _integrity(reopened)


def test_process_kill_during_continue_transaction_rolls_back_successor(setup, tmp_path):
    repo, user, root = setup
    payload = _delivery(repo, root["task_id"])
    with _owned_process(_continue_process, tmp_path, payload) as (process, pipe):
        assert _receive(process, pipe) == "uncommitted"
        assert len(_rows(repo, "h2_attempts")) == 1
        assert not _rows(repo, "h2_checkpoints")
        _kill(process)
    reopened = _repo(tmp_path, NOW + repo.config.lease_ms + 1)
    assert len(_rows(reopened, "h2_attempts")) == len(_rows(reopened, "h2_outbox")) == 1
    assert not _rows(reopened, "h2_checkpoints")
    assert not [row for row in _rows(reopened, "h2_events") if row["event_type"] == "task.continued"]
    reopened.reconcile()
    view = _view(reopened, user, root)
    assert [row["status"] for row in view["attempts"]] == ["expired", "queued"]
    assert len(_rows(reopened, "h2_outbox")) == 2
    assert view["budget"]["consumed"] == 0
    _integrity(reopened)


@pytest.mark.parametrize("cancel", [False, True])
def test_process_kill_restores_checkpoint_without_refunding_started_call(setup, tmp_path, cancel):
    repo, user, root = setup
    payload = _delivery(repo, root["task_id"])
    with _owned_process(_active_process, tmp_path, payload) as (process, pipe):
        old, checkpoint = _receive(process, pipe)
        if cancel:
            repo.cancel(user, Cancel(task_id=root["task_id"], idempotency_key="cancel-before-kill"))
        _kill(process)
    reopened = _repo(tmp_path, NOW + repo.config.lease_ms + 1)
    reopened.reconcile()
    view = _view(reopened, user, root)
    assert view["budget"]["consumed"] == 1
    assert view["budget"]["remaining"] + view["budget"]["consumed"] == 600
    assert not [row for row in _rows(reopened, "h2_budget_entries") if row["kind"] == "refund"]
    if cancel:
        assert view["status"] == "cancelled" and len(view["attempts"]) == 1
        with pytest.raises(DispatchError):
            reopened.reserve_call(old, "after-cancellation")
        assert not _rows(reopened, "h2_results")
    else:
        assert _rows(reopened, "h2_model_calls")[0]["status"] == "unknown"
        new = _claim(reopened, _delivery(reopened, root["task_id"]))
        data = reopened.execution_input(new)
        assert data["checkpoint"] == {"schema_version": "h2.checkpoint.v1", **checkpoint}
        assert not data["messages"]
        assert reopened.read_input(new.caller(), checkpoint["context_ref"]).decode() == "Completed read; next step is synthesis."
        saved = reopened.submit(new, Submission(result_key="replacement", content="Recovered analysis.", summary="Summary"))
        with pytest.raises(DispatchError, match="stale_lease"):
            reopened.submit(old, Submission(result_key="late", content="Old late result.", summary="Late summary"))
        assert _view(reopened, user, root)["result_ref"] == saved["result_ref"]
        assert len([row for row in _rows(reopened, "h2_results") if row["disposition"] == "late"]) == 1
    _integrity(reopened)


def test_two_real_processes_claim_exactly_one_lease_for_twenty_rounds(tmp_path):
    repo = _repo(tmp_path)
    repo.migrate()
    with _owned_process(_claim_process, tmp_path) as (first, first_pipe), _owned_process(
        _claim_process, tmp_path,
    ) as (second, second_pipe):
        assert _receive(first, first_pipe) == _receive(second, second_pipe) == "ready"
        for index in range(20):
            user, root = _create(repo, f"round-{index}")
            payload = _delivery(repo, root["task_id"])
            first_pipe.send(payload)
            second_pipe.send(payload)
            assert _receive(first, first_pipe) == _receive(second, second_pipe) == "armed"
            first_pipe.send("claim")
            second_pipe.send("claim")
            claims = [_receive(first, first_pipe), _receive(second, second_pipe)]
            winners = [lease for lease, _safe in claims if lease is not None]
            assert len(winners) == 1 and all(not safe for _lease, safe in claims)
            repo.start(winners[0])
            repo.submit(winners[0], Submission(result_key="final", content="One accepted result.", summary="Summary"))
            assert _view(repo, user, root)["status"] == "succeeded"
        first_pipe.send(None)
        second_pipe.send(None)
        first.join(15)
        second.join(15)
        assert first.exitcode == second.exitcode == 0
    assert len(_rows(repo, "h2_results")) == len(_rows(repo, "h2_attempts")) == 20
    _integrity(repo)


@pytest.mark.parametrize("artifact_kind", ["checkpoint", "context", "brief"])
@pytest.mark.parametrize("damage", ["corrupt", "remove"])
def test_fresh_worker_rejects_damaged_recovery_artifacts_with_specific_reason(setup, tmp_path, artifact_kind, damage):
    repo, user, root = setup
    payload = _delivery(repo, root["task_id"])
    with _owned_process(_worker_process, tmp_path, payload, "continue") as (process, pipe):
        assert _receive(process, pipe)[0] == "before-ack"
        _kill(process)
    checkpoint = _rows(repo, "h2_checkpoints")[0]
    if artifact_kind == "checkpoint":
        ref = checkpoint["state_ref"]
    elif artifact_kind == "context":
        envelope = next(row for row in _rows(repo, "h2_artifacts") if row["artifact_ref"] == checkpoint["state_ref"])
        ref = json.loads(repo.artifacts.read(envelope))["context_ref"]
    else:
        ref = _rows(repo, "h2_tasks")[0]["brief_ref"]
    artifact = next(row for row in _rows(repo, "h2_artifacts") if row["artifact_ref"] == ref)
    path = repo.artifacts.root / artifact["storage_uri"]
    if damage == "remove":
        path.unlink()
    else:
        path.write_bytes(b"corrupt fixture artifact")
    reopened, transport = _repo(tmp_path), _Acks()
    executed = []

    async def handler(work):
        executed.append(work.task_id)
        return Submission(result_key="must-not-run", content="Invalid success", summary="Invalid")

    worker = Worker(reopened, transport, "after-damage", handler)
    assert asyncio.run(worker.process("damaged-entry", _delivery(reopened, root["task_id"]))) is False
    assert not executed and transport.entries == ["damaged-entry"]
    view = _view(reopened, user, root)
    assert view["status"] == "failed"
    expected = "artifact_unavailable" if artifact_kind == "brief" else "checkpoint_invalid"
    assert view["attempts"][-1]["error_code"] == expected
    assert not _rows(reopened, "h2_results") and not _rows(reopened, "h2_model_calls")
    _integrity(reopened)
