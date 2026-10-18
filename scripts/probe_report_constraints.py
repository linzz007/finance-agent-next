"""Probe Chinese report constraints and summarize the persisted RunArtifact."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter
from policy_impact.skill_executors.recent_news_report import run_recent_news_report


ROOT = Path(__file__).resolve().parents[1]

CASES = {
    "recent_90d_official_policy": (
        "/report 只看最近90天正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；"
        "不用新闻报道冒充政策原文，最多3条。"
    ),
    "explicit_window_official_policy": (
        "/report 只看2026-06-12至2026-09-10正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；"
        "不用新闻报道冒充政策原文，最多3条。"
    ),
    "default_enterprise_report": (
        "/report 请基于当前 Wiki，生成最近一周值得同花顺 AI 产品关注的外部变化影响报告，最多3条，"
        "要求给出来源、影响判断和下一步建议。"
    ),
}


def _load_env_file() -> None:
    path = ROOT / ".env.local"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.getenv(key):
            os.environ[key] = value


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("news_evidence") or {}
    return {
        "title": item.get("title"),
        "source_id": evidence.get("source_id") or item.get("source_id"),
        "source_level": evidence.get("source_level") or item.get("source_level"),
        "published_at": evidence.get("published_at_query_timezone") or evidence.get("published_at"),
        "url": evidence.get("url"),
        "matched_topics": item.get("matched_query_topics") or [],
        "impact_level": item.get("impact_level"),
        "impact_score": item.get("impact_score"),
        "summary": item.get("chinese_summary") or item.get("summary"),
        "impact_chain": item.get("impact_chain") or item.get("impact_summary"),
        "recommended_action": item.get("recommended_action"),
    }


def _run_case(name: str, query: str, *, company_id: str, use_model: bool) -> dict[str, Any]:
    adapter = AnthropicCompatibleModelAdapter.from_env() if use_model else None
    result = run_recent_news_report(
        company_id,
        query,
        refresh_live_sources=True,
        model_adapter=adapter,
    )
    artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    analysis = artifact.get("analysis") or []
    return {
        "case": name,
        "query": query,
        "run_id": result["run_id"],
        "status": result["status"],
        "analysis_status": result["analysis_status"],
        "report_path": result["report_path"],
        "html_report_path": result["html_report_path"],
        "artifact_path": result["artifact_path"],
        "query_constraints": result["query_constraints"],
        "constraint_diagnostics": result["constraint_diagnostics"],
        "quality_diagnostics": result["quality_diagnostics"],
        "candidate_topic_coverage": result["candidate_topic_coverage"],
        "topic_coverage": result["topic_coverage"],
        "discovery_plan": {
            "mode": (result.get("discovery_plan") or {}).get("diagnostics", {}).get("mode"),
            "focus_topics": (result.get("discovery_plan") or {}).get("focus_topics"),
            "topic_terms": (result.get("discovery_plan") or {}).get("topic_terms"),
            "topic_mapping": (result.get("discovery_plan") or {}).get("diagnostics", {}).get("topic_mapping"),
            "errors": (result.get("discovery_plan") or {}).get("diagnostics", {}).get("errors"),
        },
        "analysis_count": len(analysis),
        "selected": [_item_summary(item) for item in analysis],
        "gate_reason": result["gate_reason"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-id", default="company_001")
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--model", action="store_true")
    args = parser.parse_args()
    _load_env_file()
    selected = args.case or list(CASES)
    payload = [
        _run_case(name, CASES[name], company_id=args.company_id, use_model=args.model)
        for name in selected
    ]
    output_dir = ROOT / "data" / "acceptance"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "latest_report_constraint_probe.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
