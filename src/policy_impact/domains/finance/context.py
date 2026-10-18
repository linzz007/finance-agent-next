"""Small evidence records keep report follow-ups inside the context budget."""

import re
from policy_impact.domains.finance.document_period import document_period_hint


def project_report(report: dict, message: str) -> list[dict]:
    # Put the current question's records last: ContextBuilder retains recent items.
    rows = [{"finance_report_id": report["report_id"], "period": report["request"]["period"],
             "profile_version": report["profile_snapshot"]["version"],
             "collected_at": report["snapshot"]["collected_at"],
             "data_status": report["data_status"], "warnings": report["warnings"][:4],
             "scope": "saved_report_snapshot_not_live_market_data", "holdings": "unknown_unless_user_confirms"}]
    selected = [m for m in report["metrics"] if m["name"] in message or m["symbol"] in message]
    for m in selected or report["metrics"]:
        rows.append({"finance_metric": {k: m.get(k) for k in (
            "metric_id", "name", "symbol", "start_date", "end_date", "start_close", "end_close",
            "change_pct", "unit", "price_unit", "source_url", "provider", "basis", "daily_cutoff_basis", "latest_daily_change_pct",
            "max_drawdown_pct", "max_drawdown_basis", "latest_consecutive_up_days",
            "latest_consecutive_up_start_date", "up_days", "down_days")},
            "formula": "(end_close/start_close-1)*100", "report_id": report["report_id"]})
    analysis = report.get("analysis") or {}
    sections = analysis.get("sections") or []
    if re.search("解读|观点|结论|建议|质疑|分析|重点|关注|缺什么", message):
        for s in sections[:3]:
            rows.append({"saved_finance_analysis": s, "report_id": report["report_id"]})
    if re.search("来源|原文|机构|宏观|引用|证据", message):
        cited = {ref for s in sections for ref in s["evidence_refs"]}
        documents = sorted(report["documents"], key=lambda d: (d["document_id"] in message, d["document_id"] in cited), reverse=True)
        for d in documents[:3]:
            rows.append({"financial_source": {k: d[k] for k in ("document_id", "title", "url", "published_at", "kind")},
                         "title_period_hint": document_period_hint(d),
                         "source_excerpt": d["text"][:700], "report_id": report["report_id"]})
    meta = rows.pop(0)
    meta["warnings"] = [*report["warnings"][:4], "仅已保存快照，不代表实时行情；可用证据的投影不等于全文。"]
    meta["available_saved_data"] = {"market_series": {m["symbol"]: len(m.get("points", [])) for m in report["metrics"]},
                                    "source_documents": len(report["documents"]), "analysis_sections": len(sections)}
    meta["read_tool"] = "finance.report_read"
    rows.append(meta)
    return rows
