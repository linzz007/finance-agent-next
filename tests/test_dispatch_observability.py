"""Offline tests for runtime records and the explicit dispatch service loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis

from policy_impact.dispatch import CallerContext, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Submission
from policy_impact.dispatch.service import DispatchService
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.dispatch.worker import Worker


def repository(tmp_path: Path):
    config = SchedulerConfig(stream_name="h2:observability:fixture", publish_attempts=2)
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
