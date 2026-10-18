"""Opt-in repeated business runs with private snapshots and complete traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        secret = os.getenv(key, "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    path.write_text(text, encoding="utf-8")


def inventory(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): digest(p)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def copy_file(source: Path, target: Path) -> None:
    if source.resolve() != source.absolute():
        raise ValueError(f"Linked acceptance input is not allowed: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def validate_windows_generated_paths(output: Path, cases: list[str], repetitions: int) -> None:
    """Check known main-run paths before any paid work on a Windows host."""
    filenames = (
        Path("context_observations") / ("context_" + "0" * 24 + ".json"),
        Path("research_artifacts") / ("understanding_" + "0" * 24 + ".json"),
    )
    for case in cases:
        for repetition in range(1, repetitions + 1):
            run = (output / "cases" / f"{case}-r{repetition}" / "workspace"
                   / "data/companies/finance_observer/runs" / ("0" * 32))
            for filename in filenames:
                if len(str(run / filename).encode("utf-16-le")) // 2 >= 260:
                    raise ValueError(
                        "Acceptance-generated path reaches the conservative Windows 260-character "
                        "limit; use a shorter --output batch directory. No model calls were started."
                    )


def snapshot_inputs(project: Path, corpus: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"Acceptance snapshot already exists: {destination}")
    workspace = project / "data/companies/finance_observer"
    copy_file(workspace / "company.yaml", destination / "company/company.yaml")
    for path in sorted((workspace / "wiki").rglob("*.md")):
        copy_file(path, destination / "company" / path.relative_to(workspace))
    for name in ("current_holdings.md", "research_policy.md"):
        for path, target in ((corpus / name, destination / "finance/articles" / name),
                             (corpus.parent / name, destination / "finance" / name)):
            if path.is_file():
                copy_file(path, target)
    counts = Counter()
    for path in sorted(corpus.glob("*/articles/*.md")):
        copy_file(path, destination / "finance/articles" / path.relative_to(corpus))
        counts[path.parent.parent.name] += 1
    if not counts:
        raise ValueError("No article files in acceptance snapshot")
    return {"files": inventory(destination), "article_counts": dict(counts)}


def reuse_snapshot(source_run: Path, destination: Path) -> tuple[dict, str]:
    allowed = (ROOT / "data/acceptance").resolve()
    source_run = source_run.absolute()
    if source_run.resolve() != source_run or not source_run.is_relative_to(allowed) or source_run == allowed:
        raise ValueError("Snapshot source must be an unlinked batch inside data/acceptance")
    if destination.exists():
        raise FileExistsError(f"Acceptance snapshot already exists: {destination}")
    manifest = json.loads((source_run / "manifest.json").read_text(encoding="utf-8"))
    snapshot = manifest["snapshot"]
    source = source_run / "snapshot"
    files = inventory(source)
    if not files or files != snapshot["files"]:
        raise ValueError("Source snapshot differs from its original manifest")
    research_day = date.fromisoformat(manifest["research_day"]).isoformat()
    for name in files:
        copy_file(source / name, destination / name)
    if inventory(destination) != files:
        raise ValueError("Copied snapshot differs from the source manifest")
    return snapshot, research_day


def load_cases(document: Path) -> dict[str, str]:
    rows = re.findall(r"^\| (Q\d{2}) \| ([^|]+) \|", document.read_text(encoding="utf-8"), re.M)
    queries = {key: query.strip() for key, query in rows}
    if len(rows) != 30 or len(queries) != 30:
        raise ValueError("Expected exactly the 30 frozen business queries")
    return {key: queries[key] for key in ("Q04", "Q08", "Q14")}


def code_inventory() -> dict[str, str]:
    files = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "run_finance.py",
             ROOT / "scripts/finance_research_live_acceptance.py", ROOT / "config/model_profiles.json"]
    for directory, suffixes in (("src", {".py"}), ("plugins", {".md", ".json"}),
                                ("docs", {".md"})):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.suffix in suffixes)
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(files)}


def synthetic_messages(large: bool, limit: int) -> list[dict]:
    from policy_impact.harness.context_manifest import estimate_tokens

    instruction = (
        'Return only {"type":"final","output":{"answer":"alpha|beta|gamma"}}. '
        "The synthetic START, MIDDLE and END markers below identify those values.\n"
    )
    filler = "Synthetic financial observation: scenario conditional, not a real market fact.\n"
    def build(count: int) -> list[dict]:
        prompt = instruction + "START=alpha\n" + filler * count + "MIDDLE=beta\n" + filler * count + "END=gamma"
        return [{"role": "user", "content": prompt}]

    count = 1
    if large:
        base = estimate_tokens(build(0))
        unit = estimate_tokens(build(1)) - base
        count = max(1, (limit - base - 64) // max(1, unit))
    messages = build(count)
    if estimate_tokens(messages) > limit:
        raise ValueError("Synthetic probe exceeded the configured input limit")
    return messages


def probe_service(output: Path, limits) -> bool:
    from policy_impact.harness.context_manifest import estimate_tokens
    from policy_impact.harness.model_gateway import ModelInvocationError
    from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter
    from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter
    from policy_impact.harness.model_config import effective_model_config
    from policy_impact.harness.research_budget import execution_scope

    success = True
    for name, large in (("short", False), ("configured_input", True)):
        adapter_type = (ResponsesModelAdapter if effective_model_config()["protocol"] == "openai-responses"
                        else OpenAICompatibleModelAdapter)
        model = adapter_type.from_env()
        model.max_attempts = 1
        model.max_tokens = min(limits.output_tokens, max(16384, model.max_tokens))
        trigger, _ = limits.context_pressure_limits(model.max_tokens)
        probe_limit = min(limits.input_limit(model.max_tokens), trigger)
        messages = synthetic_messages(large, probe_limit)
        record = {"started_at": now(), "estimated_input_tokens": estimate_tokens(messages),
                  "requested_max_tokens": model.max_tokens, "capacity_certified": False,
                  "probe_input_limit": probe_limit, "context_pressure_tokens": trigger,
                  "scope": "near_pressure_trigger" if large else "short",
                  "purpose": "Synthetic transport/structure probe, not financial quality acceptance"}
        write_json(output / f"probe_{name}.input.json", messages)
        started = time.monotonic()
        try:
            with execution_scope(limits=limits, timeout_seconds=180, stage="acceptance_probe"):
                response = model.complete(messages, "general_chat_agent_loop.v1")
            record.update(payload=response.payload, metadata=response.metadata)
            record["status"] = ("response_valid" if response.payload.get("output", {}).get("answer")
                                == "alpha|beta|gamma" else "unexpected_response")
        except ModelInvocationError as exc:
            record.update(status="model_error", error=str(exc), metadata=exc.metadata)
        except Exception:
            record.update(status="probe_error", error=traceback.format_exc())
        record.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 2))
        write_json(output / f"probe_{name}.json", record)
        print(json.dumps({"probe": name, **{k: record[k] for k in ("status", "duration_seconds")}}), flush=True)
        if record["status"] != "response_valid":
            success = False
            break
    return success


def trace_counts(trace: dict) -> dict:
    steps = trace.get("steps") or []
    calls = [s for s in steps if s.get("step_type") == "model_call"]
    actions = []
    for step in calls:
        action = (step.get("output_payload") or {}).get("payload") or {}
        if isinstance(action, dict):
            actions.append({k: action[k] for k in ("type", "tool_name", "role", "role_version") if k in action})
    return {"step_types": dict(Counter(s.get("step_type") for s in steps)),
            "model_call_nodes": len(calls), "failed_model_nodes": sum(s.get("status") == "failed" for s in calls),
            "actions": actions, "business_acceptance": "manual_review_required"}


def progress(stop: threading.Event, store, run_id: str, case_id: str) -> None:
    while not stop.wait(45):
        try:
            steps = store.list_agent_steps(run_id)
            last = steps[-1] if steps else {}
            print(json.dumps({"case": case_id, "status": "running", "steps": len(steps),
                              "last_step": last.get("step_type"), "last_status": last.get("status")}), flush=True)
        except Exception as exc:
            print(json.dumps({"case": case_id, "progress_error": type(exc).__name__}), flush=True)


def run_case(output: Path, key: str, repetition: int, query: str, research_day: str) -> dict:
    from policy_impact.app.chat_workbench_service import ChatWorkbenchService
    from policy_impact.company_wiki import loader
    from policy_impact.harness import agent_runtime
    from policy_impact.harness.model_config import configured_model_id
    from policy_impact.memory.store import PolicyMemoryStore

    case_id = f"{key}-r{repetition}"
    case_dir = output / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=False)
    workspace = case_dir / "workspace"
    shutil.copytree(output / "snapshot/company", workspace / "data/companies/finance_observer")
    shutil.copytree(output / "snapshot/finance", workspace / "data/finance")
    before = inventory(workspace)
    article_path = workspace / "data/finance/articles"

    class ResearchDate(date):
        @classmethod
        def today(cls):
            return date.fromisoformat(research_day)

    record = {"case": case_id, "query_id": key, "repetition": repetition, "query": query,
              "started_at": now(), "research_clock": {"today": research_day, "timezone": "Asia/Shanghai"},
              "status": "running", "business_acceptance": "manual_review_required"}
    started = time.monotonic()
    trace = {}
    with patch.object(loader, "project_root", return_value=workspace), \
            patch.object(agent_runtime, "date", ResearchDate), \
            patch.dict(os.environ, {"FINANCE_ARTICLE_ROOT": str(article_path)}):
        service = ChatWorkbenchService()
        session = service.create_session("finance_observer", f"Acceptance {case_id}", "chat",
                                         configured_model_id(), "general_chat")
        record["session_id"] = session["session_id"]
        store = PolicyMemoryStore("finance_observer")
        stop = threading.Event()
        reporter = None
        try:
            runtime, prepared = service.prepare_message("finance_observer", record["session_id"], query)
            record["run_id"] = prepared.run_id
            write_json(case_dir / "record.json", record)
            reporter = threading.Thread(target=progress, args=(stop, store, prepared.run_id, case_id), daemon=True)
            reporter.start()
            result = runtime.coordinator.execute_prepared_turn(prepared)
            record.update(status="returned", answer=result.get("answer", ""), result=result)
        except Exception:
            record.update(status="execution_error", error=traceback.format_exc())
        finally:
            stop.set()
            if reporter:
                reporter.join(timeout=15)
            if record.get("run_id"):
                trace = service.trace_for_run("finance_observer", record["session_id"], record["run_id"])
                write_json(case_dir / "trace.json", trace)
                record["model_errors"] = [s.get("output_payload") for s in trace.get("steps", [])
                                          if s.get("step_type") == "model_call" and s.get("status") == "failed"]
                record["model_failure_metadata"] = [s.get("metadata") for s in trace.get("steps", [])
                                                    if s.get("step_type") == "model_call" and s.get("status") == "failed"]
    after = inventory(workspace)
    record["changed_initial_files"] = [p for p, sha in before.items() if after.get(p) != sha]
    record.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 2),
                  answer_chars=len(record.get("answer", "")), trace_counts=trace_counts(trace))
    record["infrastructure_blocker"] = infrastructure_blocker(record)
    write_json(case_dir / "record.json", record)
    answer = record.get("answer") or ""
    write_json(case_dir / "answer.json", {"answer": answer})
    print(json.dumps({k: record.get(k) for k in ("case", "status", "duration_seconds", "answer_chars", "run_id")}), flush=True)
    return record


def infrastructure_blocker(record: dict) -> str:
    text = json.dumps(record, ensure_ascii=True).lower()
    for marker in ("http 401", "http 403", "insufficient_quota", "insufficient balance",
                   "invalid_api_key", "model_not_found", "http 402"):
        if marker in text:
            return marker
    failed = record.get("model_failure_metadata") or []
    if record.get("trace_counts", {}).get("step_types", {}).get("research_failure_fallback") and failed:
        attempts = failed[-1].get("transport_attempts") or []
        if attempts and all(a.get("stream_complete") is False and a.get("time_to_first_token_ms") is None
                            for a in attempts):
            return "transport_before_first_content"
    return ""


def finalize_interrupted(output: Path, reason: str) -> None:
    """Export host-interrupted evidence without inventing a Runtime terminal state."""
    from policy_impact.app.chat_workbench_service import ChatWorkbenchService
    from policy_impact.company_wiki import loader

    output = output.resolve()
    if not output.is_relative_to((ROOT / "data/acceptance").resolve()):
        raise ValueError("Interrupted evidence must belong to the private acceptance root")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    for path in sorted((output / "cases").glob("*/record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "running":
            continue
        workspace = path.parent / "workspace"
        with patch.object(loader, "project_root", return_value=workspace):
            trace = ChatWorkbenchService().trace_for_run("finance_observer", record["session_id"], record["run_id"])
        write_json(path.parent / "trace.json", trace)
        record.update(status="host_interrupted", interruption_reason=reason, finished_at=now(),
                      trace_counts=trace_counts(trace), runtime_terminal_state_inferred=False)
        write_json(path, record)
        summary["cases"].append({k: record.get(k) for k in ("case", "status", "run_id")})
    summary.update(status="host_stopped_after_repeated_transport_failure", stop_reason=reason,
                   finished_at=now(), manual_acceptance="incomplete")
    write_json(output / "summary.json", summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly enable paid model calls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--cases", nargs="+", choices=("Q04", "Q08", "Q14"), default=["Q04", "Q08", "Q14"])
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--model", help="Override only this process's model ID, not application configuration")
    selection.add_argument("--model-profile", help="Select an independent named provider configuration")
    parser.add_argument("--snapshot-from", type=Path, help="Reuse an unchanged private batch's inputs and research date")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this command makes paid model calls")
    output = args.output.resolve()
    allowed = (ROOT / "data/acceptance").resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        parser.error("--output must be a new subdirectory inside data/acceptance")
    if output.exists():
        parser.error("Refusing to overwrite an existing acceptance run")
    if sys.platform == "win32":
        try:
            validate_windows_generated_paths(output, args.cases, args.repetitions)
        except ValueError as exc:
            parser.error(str(exc))
    from run_finance import configure_environment
    corpus = configure_environment(model_profile=args.model_profile)
    if args.model:
        if os.getenv("AGENT_WORKBENCH_MODEL_PROFILE", "").strip():
            parser.error("--model cannot override a selected profile; use --model-profile instead")
        os.environ["ANTHROPIC_MODEL"] = args.model
    from policy_impact.harness.model_config import model_runtime_status
    from policy_impact.harness.research_budget import ResearchLimits

    status = model_runtime_status()
    if not status["available"]:
        parser.error("No model credential configured")
    if status["protocol"] not in {"openai-compatible", "openai-responses"}:
        parser.error("This acceptance's synthetic probes require the configured OpenAI-compatible service")
    if len(set(args.cases)) != len(args.cases):
        parser.error("Duplicate case IDs are not allowed")
    output.mkdir(parents=True)
    available_queries = load_cases(ROOT / "docs/finance_agent_eval_v1_acceptance.md")
    queries = {key: available_queries[key] for key in args.cases}
    limits = ResearchLimits.from_env()
    research_day = date.today().isoformat()
    if args.snapshot_from:
        snapshot, research_day = reuse_snapshot(args.snapshot_from, output / "snapshot")
    else:
        snapshot = snapshot_inputs(ROOT, corpus, output / "snapshot")
    from policy_impact.domains.finance.article_profile import load_holdings, load_research_policy

    load_holdings(output / "snapshot/finance/articles")
    load_research_policy(output / "snapshot/finance/articles")
    code_files = code_inventory()
    for name in code_files:
        copy_file(ROOT / name, output / "provenance/code" / name)
    manifest = {"started_at": now(), "model": status, "research_limits": limits.describe(),
                "research_day": research_day, "queries": queries, "repetitions": args.repetitions,
                "snapshot_source": str(args.snapshot_from.resolve()) if args.snapshot_from else None,
                "snapshot": snapshot, "code_files": code_files, "automatic_semantic_scoring": False}
    write_json(output / "manifest.json", manifest)
    summary = {"started_at": now(), "status": "probing", "cases": [], "manual_acceptance": "pending"}
    write_json(output / "summary.json", summary)
    if not probe_service(output, limits):
        summary.update(status="blocked_by_synthetic_probe", finished_at=now())
        write_json(output / "summary.json", summary)
        return 2
    summary["status"] = "running"
    write_json(output / "summary.json", summary)
    failed_deliveries = 0
    previous_blocker = ""
    repeated_blockers = 0
    for repetition in range(1, args.repetitions + 1):
        for key, query in queries.items():
            if (output / "STOP").exists():
                summary.update(status="stopped_by_host_request", finished_at=now())
                write_json(output / "summary.json", summary)
                return 3
            if code_inventory() != manifest["code_files"] or inventory(output / "snapshot") != snapshot["files"]:
                raise RuntimeError("Code/design or frozen snapshot changed; start a separate acceptance run")
            record = run_case(output, key, repetition, query, manifest["research_day"])
            summary["cases"].append({k: record.get(k) for k in ("case", "status", "answer_chars", "duration_seconds", "run_id")})
            failed_deliveries = failed_deliveries + 1 if not record.get("answer") else 0
            blocker = infrastructure_blocker(record)
            repeated_blockers = repeated_blockers + 1 if blocker and blocker == previous_blocker else int(bool(blocker))
            previous_blocker = blocker
            fatal = bool(blocker and blocker != "transport_before_first_content")
            if fatal or repeated_blockers >= 2 or failed_deliveries >= 2:
                summary.update(status="stopped_after_blocker", blocker=blocker or "two_consecutive_empty_deliveries", finished_at=now())
                write_json(output / "summary.json", summary)
                return 3
            write_json(output / "summary.json", summary)
    summary.update(status="execution_complete_manual_review_pending", finished_at=now())
    write_json(output / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
