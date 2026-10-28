"""Run opt-in, paid scheduling questions with concurrent Workers and trace facts."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import harness_v2_live_acceptance as live
from scripts.finance_research_live_acceptance import (
    copy_file,
    digest,
    inventory,
    now,
    reuse_snapshot,
    write_json,
)

CATALOG = ROOT / "tests/fixtures/h2_agent_scheduling_acceptance.json"


def source_inventory():
    hashes = live.source_inventory()
    for folder in ("prompts/finance", "config/agents"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                hashes[path.relative_to(ROOT).as_posix()] = digest(path)
    for path in (CATALOG, Path(__file__)):
        hashes[path.relative_to(ROOT).as_posix()] = digest(path)
    return dict(sorted(hashes.items()))


def differences(expected, actual):
    return {name: {"before": expected.get(name), "after": actual.get(name)}
            for name in sorted(expected.keys() | actual.keys())
            if expected.get(name) != actual.get(name)}


async def concurrent_drive(service, handle):
    from policy_impact.dispatch.worker import Worker

    workers = [service.worker]
    for index in range(1, service.repository.config.worker_concurrency):
        workers.append(Worker(
            service.repository, service.transport, f"scheduling-live-{index}",
            executor=service._execute, model_adapter=service.model_adapter,
            context_accepts=service._context_accepts,
        ))

    async def consume(worker):
        while True:
            await worker.run_once(block_ms=0)
            await asyncio.sleep(0.05)

    async def maintain():
        cursor = "0-0"
        while True:
            await asyncio.to_thread(service.publish_once)
            result = await workers[-1].recover_once(cursor=cursor, idle_ms=60000)
            cursor = result["cursor"]
            await asyncio.sleep(0.5)

    loops = [asyncio.create_task(consume(worker)) for worker in workers]
    loops.append(asyncio.create_task(maintain()))
    try:
        last_report = 0
        while True:
            for task in loops:
                if task.done():
                    task.result()
                    raise RuntimeError("acceptance Worker unexpectedly stopped")
            status = service.status(handle)
            if status["tasks"][0]["status"] in live.TERMINAL:
                return status
            elapsed = asyncio.get_running_loop().time()
            if elapsed - last_report >= 30:
                service.repository.export_run_records(handle.run_id)
                print(json.dumps({"run_id": handle.run_id,
                                  "metrics": service.repository.metrics_snapshot(handle.run_id)}), flush=True)
                last_report = elapsed
            await asyncio.sleep(0.5)
    finally:
        for task in loops:
            task.cancel()
        await asyncio.gather(*loops, return_exceptions=True)


def trace_facts(case_dir, record):
    trace_path = Path(record["exports"]["files"]["scheduling_trace"])
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    agents = [item for item in trace["spans"] if item["span_type"] == "agent"]
    events = trace["events"]
    full_reads = {(event["payload"].get("result_ref"), event["payload"].get("reader_task_id"))
                  for event in events if event["event_type"] == "result.full_viewed"
                  and event["payload"].get("reader_kind") == "agent"}
    connection = sqlite3.connect(f"file:{case_dir / 'h2.sqlite'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tasks = {row["task_id"]: dict(row) for row in connection.execute(
            "SELECT task_id,parent_task_id,status,result_ref FROM h2_tasks")}
        calls = [dict(row) for row in connection.execute(
            "SELECT call_id,task_id,started_at,finished_at,status FROM h2_model_calls "
            "WHERE started_at IS NOT NULL")]
    finally:
        connection.close()
    overlapping_siblings = []
    for index, left in enumerate(calls):
        for right in calls[index + 1:]:
            if (left["task_id"] == right["task_id"]
                    or not tasks[left["task_id"]]["parent_task_id"]
                    or tasks[left["task_id"]]["parent_task_id"] != tasks[right["task_id"]]["parent_task_id"]):
                continue
            if (left["finished_at"] and right["finished_at"]
                    and max(left["started_at"], right["started_at"])
                    < min(left["finished_at"], right["finished_at"])):
                overlapping_siblings.append([left["call_id"], right["call_id"]])
    children = [{**task, "full_read_by_parent": (task["result_ref"], task["parent_task_id"]) in full_reads}
                for task in tasks.values() if task["parent_task_id"]]
    return {
        "case": record["case"], "query": record["query"], "status": record["status"],
        "delivered": record.get("delivered"), "duration_seconds": record["duration_seconds"],
        "physical_requests": len(calls), "agent_spans": agents, "children": children,
        "all_children_succeeded": bool(children) and all(child["status"] == "succeeded" for child in children),
        "all_child_results_fully_read_by_parent": bool(children) and all(
            child["full_read_by_parent"] for child in children),
        "overlapping_sibling_requests": overlapping_siblings,
        "trace_path": str(trace_path), "semantic_acceptance": "manual_review_required",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-from", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--model-profile", default="deepseek")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: this runner makes paid requests")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    questions = {case["id"]: case["live_acceptance"]["query"] for case in catalog["cases"]}
    questions.update({case["id"]: case["query"] for case in catalog.get("natural_cases", [])})
    if len(set(args.cases)) != len(args.cases) or set(args.cases) - questions.keys():
        parser.error("select distinct IDs from the scheduling catalog")
    output = live.validate_output(args.output)

    from policy_impact.company_wiki import loader
    from policy_impact.dispatch.model_execution import profiles_from_registry
    from policy_impact.domains.finance.article_corpus import ArticleCorpus
    from run_finance import configure_environment

    configure_environment(model_profile=args.model_profile)
    profiles = profiles_from_registry([args.model_profile])
    output.mkdir(parents=True, exist_ok=False)
    snapshot, research_day = reuse_snapshot(args.snapshot_from, output / "snapshot")
    sources = source_inventory()
    for name in sources:
        copy_file(ROOT / name, output / "provenance/code" / name)
    manifest = {
        "schema_version": "finance.h2.scheduling_live.v2", "started_at": now(),
        "queries": {key: questions[key] for key in args.cases}, "source_hashes": sources,
        "snapshot": snapshot, "research_day": research_day, "profiles": profiles,
        "automatic_fallback": False, "transport_backend": "fakeredis", "real_redis_verified": False,
        "worker_count": live.acceptance_config("preflight").worker_concurrency,
        "per_case_max_model_calls": live.acceptance_config("preflight").root_calls,
    }
    write_json(output / "manifest.json", manifest)
    shutil.copytree(output / "snapshot/company", output / "workspace/data/companies/finance_observer")
    records = []
    try:
        with patch.object(loader, "project_root", return_value=output / "workspace"), \
                patch.object(live, "drive", concurrent_drive):
            corpus = ArticleCorpus("finance_observer", output / "snapshot/finance/articles")
            corpus.sync()
            for key in args.cases:
                if source_inventory() != sources or inventory(output / "snapshot") != snapshot["files"]:
                    raise RuntimeError("frozen sources or input snapshot changed; next case not started")
                record = live.run_h2_case(
                    output / "cases" / key, case_id=key, query=questions[key], corpus=corpus,
                    profiles=profiles, selected_profile=args.model_profile, backend="fake",
                    research_clock={"today": research_day, "timezone": "Asia/Shanghai"},
                )
                facts = trace_facts(output / "cases" / key, record)
                write_json(output / "cases" / key / "trace-facts.json", facts)
                records.append(facts)
                if live.environment_blocker(record):
                    break
    finally:
        write_json(output / "summary.json", {
            "finished_at": now(), "cases": records,
            "source_differences": differences(sources, source_inventory()),
            "snapshot_unchanged": inventory(output / "snapshot") == snapshot["files"],
            "actual_physical_requests": sum(record["physical_requests"] for record in records),
            "semantic_acceptance": "manual_review_required",
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
