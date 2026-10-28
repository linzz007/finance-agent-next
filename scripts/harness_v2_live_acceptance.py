"""Opt-in H2 model acceptance with durable tasks and explicit transport labeling."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.finance_research_live_acceptance import (
    DIAGNOSTIC_CASES,
    FROZEN_CASE_IDS,
    code_inventory,
    copy_file,
    digest,
    inventory,
    load_cases,
    now,
    reuse_snapshot,
    write_json,
)

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "timed_out"}
RECORDS_ROOT = ROOT.parent / "runtime-records"
MISSING_CAPABILITIES = {
    "Q03": ["market_or_public_sources"],
    "Q19": ["independent_public_sources"],
    "Q20": ["independent_public_sources"],
    "Q21": ["market_history", "independent_public_sources"],
    "Q22": ["official_source_fetch"],
    "Q23": ["wiki_context"],
    "Q24": ["holdings_context"],
    "Q25": ["wiki_context", "market_or_public_sources"],
    "Q30": ["external_adapter_fault_injection"],
}


def case_preflight(case_id: str, *, public_sources: bool = False, market_sources: bool = False,
                   personal_sources: bool = False) -> dict:
    """Report existing adapter limits without spending model calls or grading content."""
    if case_id not in FROZEN_CASE_IDS:
        raise ValueError("unknown frozen case")
    if case_id == "Q29":
        return {"status": "requires_isolated_environment", "model_calls": 0}
    supplied = {"market_or_public_sources", "independent_public_sources", "official_source_fetch"} if public_sources else set()
    if market_sources:
        supplied.update({"market_or_public_sources", "market_history"})
    if personal_sources:
        supplied.update({"wiki_context", "holdings_context"})
    missing = [name for name in MISSING_CAPABILITIES.get(case_id, []) if name not in supplied]
    if missing:
        return {"status": "blocked_missing_capability", "missing_capabilities": missing, "model_calls": 0}
    return {"status": "executable"}


def materialize_query(case_id: str, query: str, *, authors: set[str]) -> str:
    if case_id != "Q26":
        return query
    author = "acceptance_missing_author_Q26"
    while author in authors:
        author += "_absent"
    placeholder = "某位文章库中不存在的作者"
    if placeholder not in query:
        raise ValueError("Q26 no longer contains the approved missing-author placeholder")
    return query.replace(placeholder, author)


def ensure_frozen(output: Path, manifest: dict) -> None:
    if source_inventory() != manifest["source_hashes"]:
        raise RuntimeError("Code/design changed during acceptance; no next case will start")
    if inventory(output / "snapshot") != manifest["snapshot"]["files"]:
        raise RuntimeError("Frozen inputs changed during acceptance; no next case will start")


def environment_blocker(record: dict) -> bool:
    for task in record.get("runtime_status", {}).get("tasks", []):
        for attempt in task.get("attempts", []):
            if attempt.get("error_code") in {"quota_error", "provider_error"}:
                return True
    return False


def source_inventory() -> dict[str, str]:
    hashes = code_inventory()
    for name in ("AGENTS.md", "scripts/harness_v2_live_acceptance.py",
                 "tests/test_harness_v2_live_acceptance.py", "src/policy_impact/dispatch/schema.sql",
                 "src/policy_impact/dispatch/schema-independent-budgets.sql"):
        hashes[name] = digest(ROOT / name)
    return dict(sorted(hashes.items()))


def select_cases(cases: list[str] | None, all_cases: bool) -> list[str]:
    if all_cases and cases:
        raise ValueError("--all-cases and --cases are mutually exclusive")
    selected = list(FROZEN_CASE_IDS if all_cases else (cases or DIAGNOSTIC_CASES))
    if len(set(selected)) != len(selected) or set(selected) - set(FROZEN_CASE_IDS):
        raise ValueError("case IDs must be unique members of the frozen catalog")
    return selected


def validate_output(output: Path, records_root: Path = RECORDS_ROOT) -> Path:
    output, records_root = output.absolute(), records_root.resolve()
    if (
        output.resolve() != output
        or output == records_root
        or not output.is_relative_to(records_root)
        or output.exists()
    ):
        raise ValueError("output must be a new, unlinked batch inside runtime-records")
    if sys.platform == "win32":
        longest = output / "cases/Q04-r1/records/runs" / ("r" * 36) / "artifacts" / ("a" * 48)
        if len(str(longest).encode("utf-16-le")) // 2 >= 260:
            raise ValueError("batch path is too long for conservative Windows path safety")
    return output


def read_result(repository, caller, result_ref: str, *, view: str, section=None) -> str:
    chunks, offset = [], 0
    while True:
        page = repository.result_get(
            caller, result_ref, view=view, section=section, offset=offset, limit=64000,
        )
        chunks.append(page["content"])
        if not page["has_more"]:
            return "".join(chunks)
        next_offset = page["next_offset"]
        if type(next_offset) is not int or next_offset <= offset:
            raise ValueError("result pagination made no progress")
        offset = next_offset


async def drive(service, handle) -> dict:
    last_report = time.monotonic()
    cursor = "0-0"
    while True:
        status = service.status(handle)
        if status["tasks"][0]["status"] in TERMINAL:
            return status
        service.publish_once()
        processed = await service.worker_once(block_ms=0)
        recovered = await service.recover_once(cursor=cursor, idle_ms=60000)
        cursor = recovered["cursor"]
        if time.monotonic() - last_report >= 30:
            service.repository.export_run_records(handle.run_id)
            print(json.dumps({
                "run_id": handle.run_id, "task_status": service.status(handle)["tasks"][0]["status"],
                "metrics": service.repository.metrics_snapshot(handle.run_id),
            }), flush=True)
            last_report = time.monotonic()
        if not processed and not recovered["recovered"]:
            await asyncio.sleep(0.05)


def acceptance_config(run_id: str, *, max_model_calls: int | None = None):
    from policy_impact.dispatch import SchedulerConfig

    config = SchedulerConfig(stream_name=f"finance-agent-next:acceptance-{run_id}:h2:dispatch")
    if max_model_calls is None:
        return config
    if (
        type(max_model_calls) is not int
        or not config.root_reserve_calls < max_model_calls <= config.root_calls
    ):
        raise ValueError(
            f"max_model_calls must be an integer above the Root closing reserve "
            f"({config.root_reserve_calls}) and at most {config.root_calls}"
        )
    return replace(config, root_calls=max_model_calls)


def run_h2_case(
    case_dir: Path, *, case_id: str, query: str, corpus, profiles: dict,
    selected_profile: str, backend: str, redis_url: str | None = None,
    model_adapter=None, max_model_calls: int | None = None,
    public_sources=None, market_sources=None, personal_sources=None,
    research_clock: dict | None = None,
) -> dict:
    from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service
    from policy_impact.dispatch.model_execution import GatewayModelAdapter
    from policy_impact.dispatch.transport import RedisStreams

    if backend not in {"fake", "redis"} or (backend == "redis" and not redis_url):
        raise ValueError("select fake explicitly, or redis with H2_TEST_REDIS_URL")
    if set(profiles) != {selected_profile}:
        raise ValueError("each acceptance case binds exactly one authorized model profile")
    run_id = "h2-" + uuid4().hex
    config = acceptance_config(run_id, max_model_calls=max_model_calls)
    case_dir.mkdir(parents=True, exist_ok=False)
    repository = DispatchRepository(
        case_dir / "h2.sqlite", case_dir / "records",
        config=config, profiles=profiles, roles={},
    )
    repository.migrate()
    if backend == "fake":
        import fakeredis

        transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), config.stream_name)
    else:
        transport = RedisStreams.from_url(redis_url, config.stream_name)
        transport.client.ping()
    service = FinanceHarnessV2Service.for_finance_corpus(
        repository, transport, corpus=corpus,
        public_sources=public_sources, market_sources=market_sources,
        personal_sources=personal_sources,
        model_adapter=model_adapter or GatewayModelAdapter(), worker_id="h2-live-acceptance",
    )
    public_tools = ["web.search", "web.open"] if public_sources is not None else []
    from policy_impact.dispatch.finance_tool_contracts import MARKET_TOOLS, PERSONAL_TOOLS

    market_tools = sorted(MARKET_TOOLS) if market_sources is not None else []
    personal_tools = sorted(PERSONAL_TOOLS) if personal_sources is not None else []
    handle = service.create_run(
        run_id=run_id, session_ref=case_id, query=query,
        selected_profile=selected_profile, allowed_profiles=[selected_profile],
        tools=["agent.spawn", "agent.wait", "agent.status", "agent.send", "agent.cancel", "agent.resume",
               "result.get", "article.list", "article.read", "article.search", "article.claims", "tool.describe", "material.get",
               *public_tools, *market_tools, *personal_tools],
        research_clock=research_clock,
    )
    with repository.db.connection() as connection:
        persisted_clock = repository._research_clock(connection, run_id)
    record = {
        "case": case_id, "query": query, "started_at": now(),
        "execution_path": "FinanceHarnessV2Service.for_finance_corpus",
        "transport_backend": "fakeredis" if backend == "fake" else "redis-service",
        "real_redis_verified": False, "model_profile": selected_profile,
        "allowed_profiles": [selected_profile], "auto_fallback": False,
        "public_source_tools_bound": public_tools,
        "market_source_tools_bound": market_tools,
        "research_clock": persisted_clock, "research_clock_bound_to_run": True,
        "research_clock_propagated_to_model": False,
        "handle": asdict(handle), "scheduler_config": asdict(config),
        "status": "running", "semantic_acceptance": "manual_review_required",
    }
    write_json(case_dir / "record.json", record)
    started = time.monotonic()
    try:
        status = asyncio.run(drive(service, handle))
        record["status"] = status["tasks"][0]["status"]
    except KeyboardInterrupt:
        record.update(status="host_interrupted", runtime_terminal_state_inferred=False)
        raise
    except Exception as exc:  # noqa: BLE001 - preserve host error separately from task state
        record.update(status="host_error", host_error_type=type(exc).__name__, host_error=str(exc))
    finally:
        try:
            status = service.status(handle)
            record["runtime_status"] = status
            root = status["tasks"][0]
            record["delivered"] = bool(root.get("result_ref"))
            if root.get("result_ref"):
                caller = service.caller(handle)
                answer = read_result(repository, caller, root["result_ref"], view="full")
                state = json.loads(read_result(
                    repository, caller, root["result_ref"], view="section", section="finance_state",
                ))
                write_json(case_dir / "answer.json", {"answer": answer})
                write_json(case_dir / "finance-state.json", state)
                record["answer_chars"] = len(answer)
                record["finance_delivery"] = state.get("delivery")
                record["state_metadata_status"] = state.get("execution", {}).get("metadata_status")
            record["exports"] = repository.export_run_records(run_id)
            record["metrics"] = repository.metrics_snapshot(run_id)
            with repository.db.connection() as connection:
                record["task_count"] = connection.execute("SELECT count(*) FROM h2_tasks").fetchone()[0]
                record["checkpoint_count"] = connection.execute("SELECT count(*) FROM h2_checkpoints").fetchone()[0]
                record["research_clock_propagated_to_model"] = bool(connection.execute(
                    "SELECT count(*) FROM h2_model_calls WHERE run_id=? AND started_at IS NOT NULL",
                    (run_id,),
                ).fetchone()[0])
            record.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 2))
            write_json(case_dir / "record.json", record)
        finally:
            if backend == "redis":
                # Only this unique test stream; never FLUSHDB/FLUSHALL.
                transport.client.delete(transport.stream)
            transport.client.close()
    print(json.dumps({key: record.get(key) for key in (
        "case", "status", "delivered", "duration_seconds", "answer_chars", "task_count",
    )}), flush=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-from", type=Path, required=True)
    parser.add_argument("--model-profile", required=True)
    parser.add_argument("--backend", choices=["fake", "redis"], required=True)
    parser.add_argument("--cases", nargs="+", choices=FROZEN_CASE_IDS)
    parser.add_argument("--all-cases", action="store_true")
    parser.add_argument("--public-sources", action="store_true",
                        help="Explicitly bind safe web.search/web.open; does not add market or private context")
    parser.add_argument("--market-sources", action="store_true",
                        help="Explicitly bind legacy market acquisition/metrics/series with durable snapshots")
    parser.add_argument("--personal-sources", action="store_true",
                        help="Bind read-only Wiki/preferences/holdings from the isolated input snapshot")
    parser.add_argument("--repetitions", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument(
        "--max-model-calls", type=int,
        help="Per-case whole-tree cap, including children, repairs and retries; not a batch total",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this runner makes paid model calls")
    try:
        config = acceptance_config("preflight", max_model_calls=args.max_model_calls)
        selected = select_cases(args.cases, args.all_cases)
        output = validate_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    redis_url = os.environ.get("H2_TEST_REDIS_URL")
    if args.backend == "redis" and not redis_url:
        parser.error("redis backend requires H2_TEST_REDIS_URL")

    from unittest.mock import patch

    from policy_impact.company_wiki import loader
    from policy_impact.dispatch.model_execution import profiles_from_registry
    from policy_impact.domains.finance.article_corpus import ArticleCorpus
    from run_finance import configure_environment

    configure_environment(model_profile=args.model_profile)
    profiles = profiles_from_registry([args.model_profile])
    output.mkdir(parents=True, exist_ok=False)
    snapshot, research_day = reuse_snapshot(args.snapshot_from, output / "snapshot")
    hashes = source_inventory()
    for name in hashes:
        copy_file(ROOT / name, output / "provenance/code" / name)
    queries = load_cases(ROOT / "docs/finance_agent_eval_v1_acceptance.md")
    from policy_impact.harness.model_config import model_runtime_status

    preflight = {key: case_preflight(key, public_sources=args.public_sources, market_sources=args.market_sources,
                                   personal_sources=args.personal_sources)
                 for key in selected}
    executable_count = sum(value["status"] == "executable" for value in preflight.values())
    manifest = {
        "schema_version": "h2.live_acceptance.v1", "started_at": now(),
        "execution_path": "FinanceHarnessV2Service.for_finance_corpus",
        "transport_backend": args.backend, "real_redis_verified": False,
        "model_profiles": profiles, "auto_fallback": False,
        "public_source_tools_bound": ["web.search", "web.open"] if args.public_sources else [],
        "market_source_tools_bound": ["market.catalog", "market.metrics", "market.series", "market.snapshot"]
        if args.market_sources else [],
        "personal_source_tools_bound": ["personal.catalog", "context.open", "company_wiki_search"]
        if args.personal_sources else [],
        "queries": {key: queries[key] for key in selected}, "repetitions": args.repetitions,
        "case_preflight": preflight, "executable_case_count": executable_count,
        "per_case_max_model_calls": config.root_calls,
        "maximum_batch_model_calls": config.root_calls * executable_count * args.repetitions,
        "research_day": research_day, "snapshot_source": str(args.snapshot_from),
        "snapshot": snapshot, "source_hashes": hashes, "automatic_semantic_scoring": False,
        "model_config": model_runtime_status(),
        "research_clock": {"today": research_day, "timezone": "Asia/Shanghai"},
        "research_clock_binding": "immutable_run_brief",
        "research_clock_propagation_status": "per_case_record_after_execution",
    }
    write_json(output / "manifest.json", manifest)
    workspace = output / "workspace"
    shutil.copytree(output / "snapshot/company", workspace / "data/companies/finance_observer")
    records = []
    batch_status = "running"
    try:
        with patch.object(loader, "project_root", return_value=workspace):
            corpus = ArticleCorpus("finance_observer", output / "snapshot/finance/articles")
            corpus.sync()
            authors = {row["author"] for row in corpus.list_authors()}
            blocked_by_environment = False
            for key in selected:
                for repetition in range(1, args.repetitions + 1):
                    ensure_frozen(output, manifest)
                    case_id = f"{key}-r{repetition}"
                    query = materialize_query(key, queries[key], authors=authors)
                    case_dir = output / "cases" / case_id
                    # Always retain original instructions separately from Q26's approved fixture substitution.
                    write_json(output / "queries" / f"{case_id}.json", {
                        "case": case_id, "original_query": queries[key], "executed_query": query,
                        "research_clock": manifest["research_clock"],
                        "research_clock_binding": "immutable_run_brief",
                        "research_clock_propagated_to_model": False,
                        "preflight": preflight[key],
                    })
                    if preflight[key]["status"] != "executable":
                        record = {"case": case_id, "query": queries[key], **preflight[key],
                                  "delivered": False, "semantic_acceptance": "not_run",
                                  "execution_path": manifest["execution_path"]}
                        write_json(case_dir / "record.json", record)
                        print(json.dumps({"case": case_id, "status": record["status"]}), flush=True)
                    elif blocked_by_environment:
                        record = {"case": case_id, "query": queries[key],
                                  "status": "not_run_after_environment_blocker", "model_calls": 0,
                                  "delivered": False, "semantic_acceptance": "not_run"}
                        write_json(case_dir / "record.json", record)
                    else:
                        from policy_impact.dispatch.finance_market_sources import (
                            FinanceMarketSources,
                        )
                        from policy_impact.dispatch.finance_personal_sources import (
                            FinancePersonalSources,
                        )
                        from policy_impact.dispatch.finance_public_sources import (
                            FinancePublicSources,
                        )

                        record = run_h2_case(
                            case_dir, case_id=case_id, query=query,
                            corpus=corpus, profiles=profiles, selected_profile=args.model_profile,
                            backend=args.backend, redis_url=redis_url, max_model_calls=args.max_model_calls,
                            public_sources=FinancePublicSources() if args.public_sources else None,
                            market_sources=FinanceMarketSources() if args.market_sources else None,
                            personal_sources=FinancePersonalSources(
                                "finance_observer", output / "snapshot/finance/articles",
                            ) if args.personal_sources else None,
                            research_clock=manifest["research_clock"],
                        )
                        blocked_by_environment = environment_blocker(record)
                    query_path = output / "queries" / f"{case_id}.json"
                    query_record = json.loads(query_path.read_text(encoding="utf-8"))
                    query_record["research_clock_propagated_to_model"] = bool(
                        record.get("research_clock_propagated_to_model")
                    )
                    write_json(query_path, query_record)
                    records.append(record)
                    write_json(output / "summary.json", {
                        "status": "running", "cases": records, "semantic_acceptance": "manual_review_required",
                    })
            ensure_frozen(output, manifest)
            batch_status = "execution_complete"
    except KeyboardInterrupt:
        batch_status = "host_interrupted"
        raise
    except Exception:
        batch_status = "host_error"
        raise
    finally:
        # Include the just-exported interrupted case, without declaring a Runtime terminal transition.
        saved = [json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted((output / "cases").glob("*/record.json"))]
        write_json(output / "summary.json", {
            "status": batch_status, "finished_at": now(), "cases": saved,
            "snapshot_unchanged": inventory(output / "snapshot") == snapshot["files"],
            "sources_unchanged": source_inventory() == manifest["source_hashes"],
            "semantic_acceptance": "manual_review_required", "real_redis_verified": False,
        })
    return 0 if all(r["status"] in {"succeeded", "partial"} for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
