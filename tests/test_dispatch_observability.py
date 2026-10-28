"""Offline tests for runtime records and the explicit dispatch service loop."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import fakeredis
import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Submission
from policy_impact.dispatch.observability import RuntimeRecords, redact
from policy_impact.dispatch.service import DispatchService
from policy_impact.dispatch.transport import Dispatcher, RedisStreams, validate_message
from policy_impact.dispatch.worker import Worker

USAGE_FIELDS = ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens")


@pytest.mark.parametrize("field", USAGE_FIELDS)
def test_redaction_preserves_known_nonnegative_usage_counts(field):
    value = {"usage": {field: 123}, "nested": [{field: 0}], "tuple": ({field: 2},)}
    expected = {"usage": {field: 123}, "nested": [{field: 0}], "tuple": [{field: 2}]}
    assert redact(value) == expected
    assert redact(redact(value)) == expected


@pytest.mark.parametrize("value", [
    True, -1, 1.5, "123", "sk-syntheticcredential000000000", {"secret": "hidden"}, [123], None,
])
def test_usage_field_names_do_not_exempt_invalid_values_from_redaction(value):
    payload = {"usage": dict.fromkeys(USAGE_FIELDS, value)}
    assert redact(payload) == {"usage": dict.fromkeys(USAGE_FIELDS, "[redacted]")}


def test_usage_exception_keeps_other_credentials_and_unknown_token_keys_hidden():
    payload = dict.fromkeys((
        "api_key", "Authorization", "password", "secret", "lease_token", "token",
        "access_token", "arbitrary_tokens", "total_tokens_secret", "PROMPT_TOKENS",
    ), "synthetic-sensitive-value")
    payload["max_tokens"] = 32768
    payload["note"] = "Bearer syntheticcredential000000000 sk-syntheticcredential000000000"
    cleaned = redact(payload)
    assert all(value == "[redacted]" for key, value in cleaned.items() if key != "note")
    assert cleaned["note"] == "[redacted] [redacted]"
    assert redact({"usage": {}}) == {"usage": {}}


@pytest.mark.parametrize("kind", ["json", "jsonl", "quarantine"])
def test_record_files_preserve_usage_without_leaking_usage_shaped_credentials(tmp_path, kind):
    records = RuntimeRecords(tmp_path / "records")
    payload = {
        "usage": {field: i for i, field in enumerate(USAGE_FIELDS)},
        "credential": {"total_tokens": "Bearer synthetic-sensitive-value"},
        "authorization": "Bearer synthetic-sensitive-value",
        "lease_token": "synthetic-sensitive-value",
    }
    if kind == "json":
        path = records.write_json("fixture", "usage.json", payload)
    elif kind == "jsonl":
        path = records.write_jsonl("fixture", "usage.jsonl", [payload])
    else:
        path = records.quarantine(entry_id="1-0", raw=payload, error_code="contract_error")
    text = path.read_text(encoding="utf-8")
    stored = json.loads(text)
    if kind == "quarantine":
        stored = stored["raw"]
    assert stored["usage"] == payload["usage"]
    assert stored["credential"]["total_tokens"] == "[redacted]"
    assert stored["authorization"] == stored["lease_token"] == "[redacted]"
    assert "synthetic-sensitive-value" not in text


@pytest.fixture
def directory_link(tmp_path):
    links = []

    def create(path, target):
        path, target = path.absolute(), target.absolute()
        assert path.is_relative_to(tmp_path) and target.is_relative_to(tmp_path)
        try:
            path.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                pytest.skip("directory links unavailable")
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(path), str(target)],
                capture_output=True, text=True, errors="replace", timeout=10, check=False,
            )
            if result.returncode:
                pytest.skip("directory symbolic links and junctions unavailable")
        links.append(path)
        assert path.resolve() == target.resolve()
        return path

    yield create
    for path in reversed(links):
        assert path.absolute().is_relative_to(tmp_path)
        if path.is_symlink():
            path.unlink()
        elif path.exists() and path.resolve() != path:
            path.rmdir()


@pytest.mark.parametrize("component", ["root", "errors"])
def test_quarantine_refuses_linked_directory_without_writing_outside_root(tmp_path, directory_link, component):
    root, outside = tmp_path / "records", tmp_path / "outside"
    outside.mkdir()
    if component == "root":
        link = directory_link(root, outside)
    else:
        root.mkdir()
        link = directory_link(root / "errors", outside)
    records = RuntimeRecords(root)
    with pytest.raises(ValueError, match="runtime record path escaped root"):
        records.quarantine(entry_id="1-0", raw={"bad": "wire"}, error_code="contract_error")
    assert list(outside.iterdir()) == []
    assert link.resolve() == outside.resolve()


def test_quarantine_refuses_hard_linked_log_without_modifying_target(tmp_path):
    root = tmp_path / "records"
    (root / "errors").mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"foreign sentinel\n")
    target = root / "errors" / "malformed-messages.jsonl"
    try:
        os.link(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip("file hard links unavailable")
    try:
        with pytest.raises(ValueError, match="linked quarantine file"):
            RuntimeRecords(root).quarantine(entry_id="1-0", raw={}, error_code="contract_error")
        assert outside.read_bytes() == target.read_bytes() == b"foreign sentinel\n"
    finally:
        target.unlink()


@pytest.mark.parametrize("component", ["root", "runs", "run", "artifacts"])
def test_orphan_scan_and_remove_reject_linked_parent_paths(tmp_path, directory_link, component):
    root, outside = tmp_path / "records", tmp_path / "outside"
    outside.mkdir()
    relative = {
        "root": (), "runs": ("runs",), "run": ("runs", "fixture"),
        "artifacts": ("runs", "fixture", "artifacts"),
    }[component]
    link = root.joinpath(*relative)
    link.parent.mkdir(parents=True, exist_ok=True)
    directory_link(link, outside)
    suffix = ("runs", "fixture", "artifacts", "foreign")[len(relative):]
    victim = outside.joinpath(*suffix)
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"foreign sentinel")
    candidate = root / "runs" / "fixture" / "artifacts" / "foreign"
    records = RuntimeRecords(root)
    assert records.scan_orphans(known_uris=set()) == []
    assert records.remove_orphans([candidate]) == []
    assert victim.read_bytes() == b"foreign sentinel"


def test_orphan_removal_rechecks_directory_replaced_after_scan(tmp_path, directory_link):
    root = tmp_path / "records"
    folder = root / "runs" / "fixture" / "artifacts"
    folder.mkdir(parents=True)
    orphan = folder / "orphan"
    orphan.write_bytes(b"local orphan")
    records = RuntimeRecords(root)
    paths = records.scan_orphans(known_uris=set())
    assert paths == [orphan]
    retained_folder = folder.with_name("retained-artifacts")
    folder.rename(retained_folder)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "orphan"
    victim.write_bytes(b"foreign sentinel")
    directory_link(folder, outside)
    assert records.remove_orphans(paths) == []
    assert victim.read_bytes() == b"foreign sentinel"
    assert (retained_folder / "orphan").read_bytes() == b"local orphan"


def test_local_orphans_keep_age_and_reference_filters_beside_linked_run(tmp_path, directory_link):
    root = tmp_path / "records"
    folder = root / "runs" / "local" / "artifacts"
    folder.mkdir(parents=True)
    for name in ("known", "fresh", "orphan"):
        (folder / name).write_bytes(name.encode())
        timestamp = 19 if name == "fresh" else 10
        os.utime(folder / name, (timestamp, timestamp))
    outside = tmp_path / "outside"
    (outside / "artifacts").mkdir(parents=True)
    sentinel = outside / "artifacts" / "foreign"
    sentinel.write_bytes(b"foreign sentinel")
    directory_link(root / "runs" / "linked", outside)
    records = RuntimeRecords(root)
    paths = records.scan_orphans(known_uris={"runs/local/artifacts/known"}, now=20_000, min_age_ms=5_000)
    assert paths == [folder / "orphan"]
    assert records.remove_orphans([*paths, sentinel]) == ["runs/local/artifacts/orphan"]
    assert (folder / "known").read_bytes() == b"known"
    assert (folder / "fresh").read_bytes() == b"fresh"
    assert sentinel.read_bytes() == b"foreign sentinel"


def test_unsafe_quarantine_keeps_message_pending_then_reprocesses_same_entry(tmp_path, directory_link):
    repo, _, config = repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    errors = directory_link(tmp_path / "records" / "errors", outside)
    client = fakeredis.FakeRedis(decode_responses=True)
    transport = RedisStreams(client, config.stream_name, group="quarantine-boundary-workers")
    transport.ensure_group()
    entry_id = client.xadd(config.stream_name, {"bad": "wire", "authorization": "Bearer synthetic-secret"})
    called = False

    async def handler(_work):
        nonlocal called
        called = True
        return Submission(result_key="unexpected", content="unexpected")

    worker = Worker(repo, transport, "quarantine-boundary-worker", handler=handler)
    with pytest.raises(ValueError, match="runtime record path escaped root"):
        asyncio.run(worker.run_once(block_ms=0))
    assert called is False
    assert client.xpending(config.stream_name, transport.group)["pending"] == 1
    assert list(outside.iterdir()) == []
    assert errors.resolve() == outside.resolve()
    if errors.is_symlink():
        errors.unlink()
    else:
        errors.rmdir()
    entries = client.xrange(config.stream_name, min=entry_id, max=entry_id)
    assert len(entries) == 1 and entries[0][0] == entry_id
    assert asyncio.run(worker.process(*entries[0])) is False
    assert called is False
    assert client.xpending(config.stream_name, transport.group)["pending"] == 0
    lines = (errors / "malformed-messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["entry_id"] == entry_id and record["error_code"] == "contract_error"
    assert record["raw"]["authorization"] == "[redacted]"
    assert list(outside.iterdir()) == []


def repository(tmp_path: Path):
    config = SchedulerConfig(stream_name="h2:observability:fixture", publish_attempts=2, budget_policy="hierarchical")
    repo = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        config=config,
        profiles={"fixture": {"model_id": "fixture", "base_url": "https://fixture.invalid", "protocol": "openai-compatible"}},
        roles={},
    )
    repo.migrate()
    run = repo.create_run(
        run_id="observability",
        session_ref="session",
        query="public query",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=["result.get"],
    )["data"]
    return repo, run, config


def test_export_metrics_and_orphan_gc_are_run_scoped(tmp_path: Path):
    repo, run, _ = repository(tmp_path)
    exported = repo.export_run_records(run["run_id"])
    assert exported["event_count"] >= 1
    assert Path(exported["files"]["query"]).read_text(encoding="utf-8").find("public query") >= 0
    metrics = repo.metrics_snapshot(run["run_id"])
    assert metrics["tasks"]["pending_dispatch"] == 1
    assert "p50" in metrics["latency_ms"]["task"]

    orphan = tmp_path / "records" / "runs" / run["run_id"] / "artifacts" / "orphan"
    orphan.write_text("orphan", encoding="utf-8")
    report = repo.gc_orphan_artifacts(min_age_ms=0, dry_run=True)
    assert str(orphan) in report["candidates"]
    removed = repo.gc_orphan_artifacts(min_age_ms=0, dry_run=False)
    assert "runs/observability/artifacts/orphan" in removed["removed"]
    assert not orphan.exists()


def test_malformed_wire_message_is_quarantined_and_acked(tmp_path: Path):
    repo, _, config = repository(tmp_path)
    client = fakeredis.FakeRedis(decode_responses=True)
    transport = RedisStreams(client, config.stream_name, group="observability-workers")
    transport.ensure_group()
    client.xadd(config.stream_name, {"bad": "wire"})
    called = False

    async def handler(_work):
        nonlocal called
        called = True
        return Submission(result_key="unexpected", content="unexpected")

    worker = Worker(repo, transport, "observability-worker", handler=handler)
    assert asyncio.run(worker.run_once(block_ms=0)) == 1
    assert called is False
    quarantine = tmp_path / "records" / "errors" / "malformed-messages.jsonl"
    record = json.loads(quarantine.read_text(encoding="utf-8").splitlines()[0])
    assert record["error_code"] == "contract_error"
    assert client.xpending(config.stream_name, transport.group)["pending"] == 0


def forged_delivery(tmp_path, field="outbox_id", value=None):
    repo, run, config = repository(tmp_path)
    client = fakeredis.FakeRedis(decode_responses=True)
    transport = RedisStreams(client, config.stream_name, group="outbox-mismatch-workers")
    transport.ensure_group()
    with repo.db.connection() as c:
        payload = json.loads(c.execute("SELECT payload_json FROM h2_outbox").fetchone()[0])
    payload[field] = value if value is not None else (
        "2" if field in {"dispatch_generation", "emitted_at"} else "untrusted-notification"
    )
    entry_id = client.xadd(config.stream_name, payload)
    return repo, run, client, transport, entry_id, payload


@pytest.mark.parametrize("generation", [chr(178), chr(9312), chr(8304), "9" * 5000], ids=[
    "superscript", "circled", "superscript-zero", "oversized-integer",
])
def test_invalid_generation_digits_are_quarantined_instead_of_crashing(tmp_path, generation):
    repo, _, client, transport, entry_id, payload = forged_delivery(
        tmp_path, "dispatch_generation", generation,
    )

    async def handler(_work):
        pytest.fail("a malformed generation must never execute")

    worker = Worker(repo, transport, "nondecimal-worker", handler=handler)
    assert asyncio.run(worker.run_once(block_ms=0)) == 1
    assert client.xpending(transport.stream, transport.group)["pending"] == 0
    record = json.loads((repo.records.root / "errors" / "malformed-messages.jsonl").read_text(encoding="utf-8"))
    assert record["entry_id"] == entry_id and record["raw"] == payload
    assert record["error_code"] == "contract_error"
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_attempts WHERE worker_id IS NOT NULL").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0


@pytest.mark.parametrize("generation", ["1", chr(1633)])
def test_numeric_wire_validation_retains_previously_valid_decimal_forms(tmp_path, generation):
    _, _, _, _, _, payload = forged_delivery(tmp_path, "dispatch_generation", generation)
    assert validate_message(payload) == payload


@pytest.mark.parametrize("field", [
    "outbox_id", "run_id", "task_id", "attempt_id", "attempt_key", "dispatch_key",
    "dispatch_generation", "emitted_at",
])
def test_well_shaped_outbox_mismatch_is_quarantined_without_execution(tmp_path, field):
    repo, _, client, transport, entry_id, payload = forged_delivery(tmp_path, field)
    with repo.db.connection() as c:
        before = list(c.iterdump())

    async def handler(_work):
        pytest.fail("a forged notification must never reach the executor")

    ack = transport.ack

    def audit_before_ack(actual_entry):
        records = repo.records.root / "errors" / "malformed-messages.jsonl"
        record = json.loads(records.read_text(encoding="utf-8").splitlines()[-1])
        assert record["entry_id"] == actual_entry == entry_id
        assert record["error_code"] == "contract_error"
        assert record["raw"] == payload
        assert "does not match persisted outbox" in record["error_message"]
        return ack(actual_entry)

    transport.ack = audit_before_ack
    worker = Worker(repo, transport, "mismatch-worker", handler=handler)
    assert asyncio.run(worker.run_once(block_ms=0)) == 1
    assert client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert list(c.iterdump()) == before


def test_new_worker_recovers_forged_pending_notification_without_claiming(tmp_path):
    repo, _, client, transport, entry_id, payload = forged_delivery(tmp_path)
    assert transport.receive("dead-consumer", block_ms=0) == [(entry_id, payload)]
    assert client.xpending(transport.stream, transport.group)["pending"] == 1

    async def handler(_work):
        pytest.fail("a forged pending notification must never run")

    worker = Worker(repo, transport, "recovery-worker", handler=handler)
    recovery = asyncio.run(worker.recover_once(idle_ms=0))
    assert recovery["recovered"] == 1
    assert client.xpending(transport.stream, transport.group)["pending"] == 0
    record = json.loads((repo.records.root / "errors" / "malformed-messages.jsonl").read_text(encoding="utf-8"))
    assert record["entry_id"] == entry_id and record["raw"] == payload
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_attempts WHERE worker_id IS NOT NULL").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0


def test_service_continues_to_genuine_outbox_after_forged_notification(tmp_path):
    repo, run, client, transport, _, _ = forged_delivery(tmp_path, "task_id")
    handled = []

    async def handler(work):
        handled.append(work.task_id)
        return Submission(result_key="genuine", content="original analysis", summary="done")

    worker = Worker(repo, transport, "service-mismatch-worker", handler=handler)
    service = DispatchService(repo, transport, worker)

    async def drive():
        first = await service.run_once()
        assert first.published == 1 and first.processed == 1 and handled == []
        second = await service.run_once()
        assert second.published == 0 and second.processed == 1

    asyncio.run(drive())
    assert handled == [run["task_id"]]
    assert client.xpending(transport.stream, transport.group)["pending"] == 0
    with repo.db.connection() as c:
        assert c.execute("SELECT status FROM h2_tasks WHERE task_id=?", (run["task_id"],)).fetchone()[0] == "succeeded"
        assert c.execute("SELECT count(*) FROM h2_results WHERE disposition='accepted'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_attempts").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0


def test_outbox_mismatch_quarantine_failure_leaves_entry_pending(tmp_path, monkeypatch):
    repo, _, client, transport, _, _ = forged_delivery(tmp_path)

    async def handler(_work):
        pytest.fail("untrusted notification must not execute")

    def unavailable(**_kwargs):
        raise OSError("fixture audit unavailable")

    monkeypatch.setattr(repo.records, "quarantine", unavailable)
    worker = Worker(repo, transport, "audit-failure-worker", handler=handler)
    with pytest.raises(OSError, match="fixture audit unavailable"):
        asyncio.run(worker.run_once(block_ms=0))
    assert client.xpending(transport.stream, transport.group)["pending"] == 1


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("fixture database busy"), DispatchError("forbidden")])
def test_noncontract_claim_failures_are_not_quarantined_or_acked(tmp_path, monkeypatch, failure):
    repo, _, config = repository(tmp_path)
    client = fakeredis.FakeRedis(decode_responses=True)
    transport = RedisStreams(client, config.stream_name, group="claim-failure-workers")
    assert Dispatcher(repo, transport).publish_batch() == 1

    async def handler(_work):
        pytest.fail("an unsuccessful claim must not execute")

    def failed_claim(*_args):
        raise failure

    monkeypatch.setattr(repo, "claim", failed_claim)
    worker = Worker(repo, transport, "claim-failure-worker", handler=handler)
    with pytest.raises(type(failure), match=str(failure)):
        asyncio.run(worker.run_once(block_ms=0))
    assert client.xpending(transport.stream, transport.group)["pending"] == 1
    assert not (repo.records.root / "errors" / "malformed-messages.jsonl").exists()


def test_outbox_mismatch_ack_failure_retains_audit_and_pending_entry(tmp_path, monkeypatch):
    repo, _, client, transport, entry_id, _ = forged_delivery(tmp_path)

    async def handler(_work):
        pytest.fail("untrusted notification must not execute")

    def unavailable(_entry_id):
        raise OSError("fixture ack unavailable")

    monkeypatch.setattr(transport, "ack", unavailable)
    worker = Worker(repo, transport, "ack-failure-worker", handler=handler)
    with pytest.raises(OSError, match="fixture ack unavailable"):
        asyncio.run(worker.run_once(block_ms=0))
    assert client.xpending(transport.stream, transport.group)["pending"] == 1
    record = json.loads((repo.records.root / "errors" / "malformed-messages.jsonl").read_text(encoding="utf-8"))
    assert record["entry_id"] == entry_id


def test_dispatch_service_runs_one_explicit_cycle(tmp_path: Path):
    repo, run, config = repository(tmp_path)
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True), config.stream_name, group="service-workers"
    )

    async def handler(_work):
        return Submission(result_key="final", content="done", summary="done")

    worker = Worker(repo, transport, "service-worker", handler=handler)
    cycle = asyncio.run(DispatchService(repo, transport, worker).run_once())
    assert cycle.published == 1
    assert cycle.processed == 1
    assert cycle.reconcile["has_more"] is False
    assert repo.status(
        CallerContext(run["run_id"], run["thread_id"], run["task_id"], trusted_user=True),
        [run["task_id"]],
    )["tasks"][0]["status"] == "succeeded"
