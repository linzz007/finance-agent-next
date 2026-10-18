"""Bounded evidence projection from this session's own published report artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


def read_report_evidence(company_root: Path, history: list[dict[str, Any]], message: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not re.search(r"报告|刚才|上面|上一|这些论文|那条|那份|指数|收盘|涨跌|宏观|金融|基准|来源|机构|涨幅|跌幅|黄金|白银|美股|行情|反证|数据|风险|这条|这份|这些|原因|解释|接下来|观点", message):
        return [], {}
    root = company_root.resolve()
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        metadata = item.get("metadata") or {}
        value = (metadata.get("artifacts") or {}).get("run_artifact")
        if not value:
            continue
        path = Path(value).resolve()
        if not any(path.is_relative_to(root / folder) for folder in ("reports", "runs")):
            continue
        if path.suffix != ".json" or not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        try:
            data = path.read_bytes()
            report = json.loads(data)
        except (OSError, ValueError):
            continue
        if report.get("company_id") != root.name:
            continue
        if report.get("skill") == "financial_report":
            from policy_impact.domains.finance.context import project_report
            return project_report(report, message), {
                "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                "report_id": report["report_id"], "scope": "same_session_same_workspace", "fresh_fetch": False,
            }
        if report.get("skill") != "recent_news_report":
            continue
        events = {event["event_id"]: event for event in report.get("structured_events", [])}
        analysis = report.get("analysis") or []
        rows = []
        ordered = list(analysis)
        ordinal = re.search(r"第([一二三四五六七八九十]|\d{1,2})(?:条|项|篇)", message)
        if ordinal:
            value = ordinal[1]
            index = int(value) - 1 if value.isdigit() else "一二三四五六七八九十".index(value)
            if 0 <= index < len(ordered):
                ordered.insert(0, ordered.pop(index))
        for result in ordered[:3]:
            event = events.get(result.get("event_id"), {})
            links = result.get("evidence_links") or {}
            refs = links.get("company_fact_ids") or [f.get("fact_id") for f in result.get("company_evidence", []) if f.get("fact_id")]
            rows.append({"event_id": event.get("event_id"), "title": str(event.get("title", ""))[:100],
                         "published_at": event.get("published_at"), "url": event.get("url"),
                         "retrieved_at": event.get("retrieved_at"),
                         "source_excerpt": str(event.get("summary") or links.get("event_quote") or "")[:800],
                         "cited_quote": str(links.get("event_quote") or "")[:200],
                         "evidence_scope": event.get("evidence_scope") or "saved_source_excerpt",
                         "validated_analysis": {
                             "summary_zh": str(result.get("event_summary_zh") or "")[:200],
                             "condition": str(result.get("condition") or "")[:150],
                             "impact": str(result.get("conditional_impact") or "")[:250],
                             "action": str(result.get("recommended_action") or "").split("；执行边界：")[0][:250],
                         } if result.get("analysis_status") == "done" else {},
                         "wiki_fact_ids": refs, "analysis_status": result.get("analysis_status"),
                         "scope": "saved_source_excerpt_not_fresh_fulltext_verification"})
        manifest = report.get("source_manifest") or {}
        statuses = manifest.get("source_statuses") or []
        if rows or statuses:
            return [{"report_run_id": metadata.get("run_id"), "report_evidence": rows,
                     "snapshot_collected_at": manifest.get("collected_at"),
                     "source_coverage": {"total": len(statuses), "by_status": {
                         status: [entry.get("source_id") for entry in statuses if entry.get("status") == status]
                         for status in sorted({str(entry.get("status") or "unknown") for entry in statuses})
                     }, "scope": "configured_sources_not_entire_web"},
                     "popularity_sampled_at": None, "total_report_items": len(analysis),
                     "selection_scope": "all_published_report_items" if len(rows) == len(analysis)
                     else "up_to_three_report_items_not_a_complete_ranking"}], {
                "path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "event_ids": [r["event_id"] for r in rows],
                "scope": "same_session_same_workspace", "fresh_fetch": False,
            }
    return [], {}
