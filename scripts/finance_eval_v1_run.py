"""Run approved finance evaluation queries with resumable per-case records."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import run_policy_api

run_policy_api.load_local_env()

from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.harness.model_config import configured_model_id


def load_queries() -> dict[str, str]:
    document = (ROOT / "docs/finance_agent_eval_v1_acceptance.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (Q\d{2}) \| ([^|]+) \|", document, re.MULTILINE)
    queries = {key: query.strip() for key, query in rows}
    if len(queries) != 30:
        raise ValueError(f"Expected 30 approved queries; found {len(queries)}")
    queries["Q26"] = queries["Q26"].replace(
        "某位文章库中不存在的作者", "验收测试不存在的作者_20261009"
    )
    return queries


def summarize_trace(trace: dict) -> dict:
    steps = trace.get("steps") or []
    actions = []
    for step in steps:
        if step.get("step_type") != "model_call":
            continue
        payload = (step.get("output_payload") or {}).get("payload") or {}
        if not isinstance(payload, dict):
            continue
        action = {"type": payload.get("type"), "tool": payload.get("tool_name")}
        if action["type"] == "delegate":
            action["role"] = payload.get("role")
        if action["type"] in {"tool_call", "delegate"}:
            actions.append(action)
    def latest_payload(step_type: str) -> dict:
        return next((step.get("output_payload") or {} for step in reversed(steps)
                     if step.get("step_type") == step_type), {})

    audit = latest_payload("evidence_audit")
    delivery = latest_payload("research_delivery")
    answer_audit = latest_payload("answer_claim_audit")
    review_resolution = latest_payload("answer_review_resolution")
    article_reads = audit.get("article_reads") or []
    article_ids = sorted({str(ref.get("article_id")) for ref in article_reads
                          if ref.get("article_id")})
    model_steps = [step for step in steps if step.get("step_type") == "model_call"]
    model_failures = [step for step in model_steps if step.get("status") == "failed"]
    return {
        "step_count": len(steps),
        "step_types": dict(Counter(step.get("step_type") for step in steps)),
        "model_call_steps": len(model_steps),
        "model_call_failures": len(model_failures),
        "actions": actions,
        "article_read_count": len(article_reads),
        "unique_article_ids": article_ids,
        "read_authors": sorted({str(ref.get("author")) for ref in article_reads
                                if ref.get("author")}),
        "article_list_history": audit.get("article_list_history"),
        "research_plan_before_delivery": audit.get("research_plan"),
        "research_delivery": delivery,
        "answer_claim_audit": {
            key: answer_audit.get(key) for key in (
                "status", "review_scope", "review_status", "reviewed_claim_count",
                "coverage_complete", "limitations",
            ) if key in answer_audit
        },
        "answer_review_resolution": review_resolution,
        "evidence_audit_keys": list(audit),
        "stop": [step.get("output_payload") for step in steps
                 if step.get("step_type") == "run_stopped"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="Query IDs, e.g. Q03 Q04")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "finance_eval_v1")
    parser.add_argument("--offline-web", action="store_true", help="Inject external-source failures for Q30")
    args = parser.parse_args()
    queries = load_queries()
    selected = args.ids or list(queries)
    unknown = set(selected) - set(queries)
    if unknown:
        parser.error(f"Unknown IDs: {sorted(unknown)}")
    if args.offline_web:
        if selected != ["Q30"]:
            parser.error("--offline-web is only valid for Q30")
        from policy_impact.domains.finance.article_gateway import FinanceArticleGateway
        from policy_impact.domains.finance.market_research import MarketResearch
        from policy_impact.domains.finance.web_research import WebResearch

        def offline(*_args, **_kwargs):
            raise ConnectionError("Finance evaluation: external source unavailable")

        for owner, names in (
            (FinanceArticleGateway, ("discover", "fetch")),
            (WebResearch, ("search", "open")),
            (MarketResearch, ("snapshot",)),
        ):
            for name in names:
                setattr(owner, name, offline)
    args.output.mkdir(parents=True, exist_ok=True)
    service = ChatWorkbenchService()
    for key in selected:
        destination = args.output / f"{key}.json"
        if destination.exists():
            print(json.dumps({"id": key, "status": "already_recorded"}), flush=True)
            continue
        if key == "Q29" or (key == "Q30" and not args.offline_web):
            print(json.dumps({"id": key, "status": "requires_isolation_or_fault_injection"}), flush=True)
            continue
        record = {"id": key, "query": queries[key], "started_at": datetime.now(timezone.utc).isoformat()}
        started = time.monotonic()
        try:
            session = service.create_session(
                "finance_observer", f"Finance Eval v1 {key}", "chat", configured_model_id(), "general_chat"
            )
            record["session_id"] = session["session_id"]
            result = service.send_message("finance_observer", record["session_id"], queries[key])
            record["run_id"] = result.get("run_id")
            record["answer"] = result.get("answer")
            record["trace"] = summarize_trace(service.latest_trace("finance_observer", record["session_id"]))
            record["status"] = "completed"
        except Exception:
            record["status"] = "error"
            record["error"] = traceback.format_exc()
        record["duration_seconds"] = round(time.monotonic() - started, 2)
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        destination.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"id": key, "status": record["status"], "run_id": record.get("run_id"),
                          "session_id": record.get("session_id"), "duration_seconds": record["duration_seconds"]},
                         ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
