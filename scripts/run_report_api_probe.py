"""Run report cases through the public Workbench API with UTF-8 prompts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error, request


BASE_URL = "http://127.0.0.1:8503"
MODEL_ID = "deepseek-v4-flash"
OUT_PATH = Path("data/acceptance/latest_workbench_api_report_probe.json")


CASES = [
    {
        "case": "enterprise_strict_90d_policy",
        "company_id": "company_001",
        "title": "验收-企业严格政策报告",
        "messages": [
            "/report 只看最近90天正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；不用新闻报道冒充政策原文，最多3条。",
            "刚才报告里哪一条对问财和 iFinD 最值得优先处理？请只基于刚才报告和 Wiki 回答。",
        ],
    },
    {
        "case": "enterprise_default_report",
        "company_id": "company_001",
        "title": "验收-企业默认报告",
        "messages": [
            "/report 请基于当前 Wiki，生成最近一周值得同花顺 AI 产品关注的外部变化影响报告，最多3条，要求给出来源、影响判断和下一步建议。"
        ],
    },
    {
        "case": "personal_agent_evolution_report",
        "company_id": "agent_self_evolution",
        "title": "验收-个人Agent自进化报告",
        "messages": [
            "/report 请基于当前 Wiki，生成最近30天 Agent 自进化、EvoAgentX、Hermes、Claude Code、Codex 相关技术变化对我学习 Agent Harness 的影响报告，最多3条，要求给出来源和行动建议。"
        ],
    },
]


def http(method: str, path: str, payload: dict | None = None, *, timeout: int = 300) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail}") from exc


def create_session(company_id: str, title: str) -> str:
    payload = {
        "title": title,
        "mode": "chat",
        "model_id": MODEL_ID,
        "active_skill_id": "",
    }
    return str(http("POST", f"/companies/{company_id}/workbench/sessions", payload)["session_id"])


def send_message(company_id: str, session_id: str, message: str) -> dict:
    started = time.perf_counter()
    result = http(
        "POST",
        f"/companies/{company_id}/workbench/sessions/{session_id}/messages",
        {"message": message},
        timeout=420,
    )
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def get_trace(company_id: str, session_id: str) -> dict:
    return http("GET", f"/companies/{company_id}/workbench/sessions/{session_id}/trace")


def load_run_artifact(path: str) -> dict:
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize_turn(company_id: str, session_id: str, message: str, result: dict) -> dict:
    trace = get_trace(company_id, session_id)
    artifacts = result.get("artifacts") or {}
    run_artifact_path = str(artifacts.get("run_artifact") or "")
    artifact = load_run_artifact(run_artifact_path)
    analysis = artifact.get("analysis") or []
    artifact_summary = {}
    if artifact:
        artifact_summary = {
            "analysis_status": artifact.get("analysis_status"),
            "query_constraints": artifact.get("query_constraints"),
            "constraint_diagnostics": artifact.get("constraint_diagnostics"),
            "quality_diagnostics": artifact.get("quality_diagnostics"),
            "discovery_focus_topics": (artifact.get("discovery_plan") or {}).get("focus_topics"),
            "selected_titles": [item.get("title") for item in analysis],
            "selected_urls": [(item.get("news_evidence") or {}).get("url") for item in analysis],
            "html_report_path": artifact.get("html_report_path"),
            "gate_reason": artifact.get("gate_reason"),
        }
    return {
        "company_id": company_id,
        "session_id": session_id,
        "message": message,
        "run_id": result.get("run_id"),
        "answer_preview": str(result.get("answer") or "")[:1200],
        "latency_ms": result.get("latency_ms"),
        "artifacts": artifacts,
        "artifact_summary": artifact_summary,
        "trace_summary": trace.get("trace_summary") or {},
        "step_types": [step.get("step_type") for step in trace.get("steps", [])],
    }


def main() -> int:
    runs = []
    for case in CASES:
        company_id = case["company_id"]
        session_id = create_session(company_id, case["title"])
        turns = []
        for message in case["messages"]:
            result = send_message(company_id, session_id, message)
            turns.append(summarize_turn(company_id, session_id, message, result))
        runs.append({**case, "session_id": session_id, "turns": turns})
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"base_url": BASE_URL, "model_id": MODEL_ID, "cases": runs}
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = []
    for case in runs:
        turns = []
        for turn in case["turns"]:
            artifact_summary = turn.get("artifact_summary") or {}
            trace_summary = turn.get("trace_summary") or {}
            turns.append({
                "message": turn.get("message"),
                "run_id": turn.get("run_id"),
                "analysis_status": artifact_summary.get("analysis_status"),
                "selected_titles": artifact_summary.get("selected_titles") or [],
                "selected_urls": artifact_summary.get("selected_urls") or [],
                "gate_reason": artifact_summary.get("gate_reason"),
                "trace": {
                    "skill_id": trace_summary.get("skill_id"),
                    "total_steps": trace_summary.get("total_steps"),
                    "gate_count": trace_summary.get("gate_count"),
                    "artifact_count": trace_summary.get("artifact_count"),
                    "subagent_call_count": trace_summary.get("subagent_call_count"),
                },
            })
        summary.append({
            "case": case["case"],
            "company_id": case["company_id"],
            "session_id": case["session_id"],
            "turns": turns,
        })
    print(json.dumps({"output": str(OUT_PATH), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
