"""Replay Chinese user journeys exclusively through the public Workbench API.

Mechanical checks are separate from semantic review. No test calls a store,
executor, model adapter, or writes Wiki files directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import time
from typing import Any
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "fixtures" / "persona_acceptance.json"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class Api:
    def __init__(self, base_url: str, out: Path) -> None:
        self.base = base_url.rstrip("/")
        self.out = out
        log = out / "api_requests.json"
        self.requests: list[dict[str, Any]] = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []

    def call(self, path: str, payload: Any = None, *, raw: bool = False) -> tuple[int, Any]:
        started = time.perf_counter()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = request.urlopen(req, timeout=240)
        except error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            status = response.status
        if raw:
            value = body
        else:
            value = json.loads(body.decode("utf-8")) if body else {}
        self.requests.append({"method": req.get_method(), "path": path, "body": payload, "status": status,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
        write_json(self.out / "api_requests.json", self.requests)
        return status, value

    def ok(self, path: str, payload: Any = None) -> Any:
        status, data = self.call(path, payload)
        if status >= 400:
            raise RuntimeError(f"HTTP {status} {path}: {data}")
        return data

    def stream(self, path: str, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        started = time.perf_counter()
        req = request.Request(self.base + path, data=json.dumps({"message": message}, ensure_ascii=False).encode(),
                              headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        events: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        chunks: list[str] = []
        event, data_lines = "message", []
        with request.urlopen(req, timeout=240) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    payload = json.loads("\n".join(data_lines))
                    events.append({"event": event, "at_ms": round((time.perf_counter() - started) * 1000, 2),
                                   "data": payload})
                    if event == "result":
                        result.update(payload)
                    elif event == "chunk":
                        chunks.append(payload.get("content", ""))
                    elif event == "error":
                        result["stream_error"] = payload
                    event, data_lines = "message", []
        result["answer"] = "".join(chunks)
        self.requests.append({"method": "POST", "path": path, "body": {"message": message}, "status": 200,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
        write_json(self.out / "api_requests.json", self.requests)
        return result, events


def fact_body(persona: dict[str, Any], updates: dict[str, str] | None = None) -> str:
    lines = [f"# {persona['name']}", "", "以下为功能验收使用的用户自述背景和约束，不代表企业内部披露。", ""]
    for item in persona["facts"]:
        value = (updates or {}).get(item["id"], item["value"])
        lines += [f"## FACT: {item['id']}", "- source: user_confirmed", "- confidence: 1.0",
                  "- importance: 5", f"- value: {value}", "- policy_relevance: [agent, 技术跟踪]", value, ""]
    return "\n".join(lines)


def setup(api: Api, suite: dict[str, Any], prefix: str) -> dict[str, str]:
    companies = {}
    for key, persona in suite["personas"].items():
        cid = f"{prefix}_{key}"
        status, result = api.call("/companies", {"company_id": cid, "name": persona["name"],
                                               "profile_type": persona["profile_type"]})
        if status not in (200, 201, 409):
            raise RuntimeError(f"Cannot create persona through API: {status}: {result}")
        companies[key] = cid
        existing = api.ok(f"/companies/{cid}/workbench/wiki")
        create = not any(p["path"] == "wiki/acceptance_profile.md" for p in existing["pages"])
        api.ok(f"/companies/{cid}/workbench/wiki/pages", {"path": "wiki/acceptance_profile.md", "body": fact_body(persona), "create": create})
        wiki = api.ok(f"/companies/{cid}/workbench/wiki")
        actual = {x["fact_id"] for x in wiki["facts"]}
        assert {x["id"] for x in persona["facts"]} <= actual, "API Wiki write did not produce searchable facts"
        write_json(api.out / f"wiki_{key}_initial.json", wiki)
    write_json(api.out / "companies.json", companies)
    return companies


def api_probes(api: Api, companies: dict[str, str]) -> list[dict[str, Any]]:
    cid = companies["personal"]
    base = f"/companies/{cid}/workbench"
    probes = []

    def probe(name, path, payload=None, expected=200, predicate=lambda data: True):
        status, data = api.call(path, payload)
        passed = status == expected and predicate(data)
        probes.append({"name": name, "passed": passed, "status": status, "response": data})

    probe("flash_model_available_without_secret", base + "/models", predicate=lambda d: bool(d["models"][0]["metadata"]["available"]) and not re.search(r"sk-[A-Za-z0-9]{16,}", json.dumps(d)))
    probe("public_skill_catalog", base + "/skills", predicate=lambda d: {s["id"] for s in d["skills"]} == {"general_chat", "external_impact_report"})
    probe("invalid_model_rejected", base + "/sessions", {"model_id": "nonexistent-model"}, 400)
    probe("invalid_skill_rejected", base + "/sessions", {"active_skill_id": "missing-skill"}, 400)
    probe("model_management_explicitly_disabled", base + "/models", {"model_id": "test", "model_name": "test"}, 410)
    probe("wiki_path_traversal_rejected", base + "/wiki/pages", {"path": "wiki/../../out.md", "body": "must not write"}, 400)
    probe("workspace_traversal_rejected", "/companies", {"company_id": "../escape", "name": "invalid", "profile_type": "personal"}, 400)
    probe("workspace_overwrite_rejected", "/companies", {"company_id": cid, "name": "replacement", "profile_type": "company"}, 409)
    before = api.ok(base + "/wiki")
    duplicate = "## FACT: personal_goal\n- value: forbidden duplicate\n- source: user_confirmed\n"
    probe("duplicate_fact_rejected", base + "/wiki/pages", {"path": "wiki/duplicate.md", "body": duplicate, "create": True}, 409)
    probes.append({"name": "rejected_write_has_no_wiki_side_effect", "passed": before == api.ok(base + "/wiki")})
    probe("wiki_search_works", base + "/wiki/search?" + parse.urlencode({"query": "每天 两小时 Python", "top_k": 5}), predicate=lambda d: bool(d.get("facts") or d.get("results") or d.get("hits")))
    probe("wiki_search_unmatched", base + "/wiki/search?query=unmatchedsentinelqz0919&top_k=5", predicate=lambda d: not (d.get("facts") or d.get("results") or d.get("hits")))
    probe("wiki_search_budget_rejected", base + "/wiki/search?query=Python&top_k=5000", expected=400)
    # This endpoint must not serve configuration/Wiki or another workspace as a report.
    status, body = api.call(base + "/artifact?" + parse.urlencode({"path": "../" + companies["ths"] + "/company.yaml"}), raw=True)
    probes.append({"name": "cross_workspace_artifact_rejected", "passed": status == 403, "status": status})
    write_json(api.out / "api_probes.json", probes)
    return probes


def mechanical_checks(case: dict[str, Any], result: dict[str, Any], trace: dict[str, Any], events: list[dict[str, Any]],
                      artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []

    def check(name: str, passed: bool, actual: Any = None) -> None:
        checks.append({"check": name, "passed": bool(passed), "actual": actual})

    summary = trace.get("trace_summary") or {}
    steps = trace.get("steps") or []
    types = [s.get("step_type") for s in steps]
    check("answer_nonempty", bool(result.get("answer")))
    check("no_stream_error", not result.get("stream_error"), result.get("stream_error"))
    check("trace_run_matches", bool(result.get("run_id")) and result.get("run_id") == trace.get("run_id"))
    check("terminal_trace", trace.get("status") in ("done", "failed", "blocked", "cancelled"), trace.get("status"))
    check("context_manifest", bool(summary.get("has_context_manifest")))
    check("ordered_steps", [s.get("step_index") for s in steps] == list(range(1, len(steps) + 1)))
    check("final_and_stop", "final_answer" in types and "run_stopped" in types)
    check("skill_routing", trace.get("skill_id") == case.get("skill", "general_chat"), trace.get("skill_id"))
    roles = summary.get("subagent_roles") or []
    for role in case.get("roles", []):
        check("role_" + role, role in roles, roles)
        completed = [item for item in (trace.get("subagent_summary") or {}).get("results", [])
                     if item.get("role") == role and item.get("stop_reason") == "success" and item.get("output")]
        check("role_completed_" + role, bool(completed))
    if case.get("no_subagents"):
        check("simple_chat_no_subagents", not roles, roles)
    if case.get("no_artifacts"):
        check("chat_does_not_publish_report", not result.get("artifacts"), list(result.get("artifacts") or {}))
    if "memory_writes" in case:
        check("memory_write_boundary", summary.get("memory_write_count", 0) == case["memory_writes"], summary.get("memory_write_count"))
    if case.get("skill") == "external_impact_report":
        check("report_embedded_in_loop", any("report_workflow.run" in json.dumps(s, ensure_ascii=False) for s in steps))
        if case.get("expect_report", True):
            check("html_artifact_accessible", any(a.get("kind") == "html_report" and a.get("http_status") == 200 for a in artifacts.values()), list(artifacts))
    if events:
        names = [e["event"] for e in events]
        check("sse_protocol", names[0] == "start" and names[-1] == "done" and "chunk" in names, names)
        if case.get("skill") == "external_impact_report" or case.get("roles"):
            progress = [e for e in events if e["event"] == "progress"]
            result_at = next((e["at_ms"] for e in events if e["event"] == "result"), 0)
            check("live_progress_before_result", bool(progress) and progress[0]["at_ms"] < result_at)
    artifact = artifacts.get("run_artifact")
    if artifact and artifact.get("http_status") == 200:
        report = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        if report.get("skill") == "recent_news_report":
            analysis = report.get("analysis") or []
            constraints = report.get("query_constraints") or {}
            selected_ids = {item["event_id"] for item in analysis}
            selected = [item for item in report.get("structured_events", []) if item["event_id"] in selected_ids]
            if case.get("expect_report", True):
                check("useful_report_not_only_fallback", bool(analysis) and all(row.get("analysis_status") in ("done", "complete") for row in analysis),
                      {"status": report.get("analysis_status"), "count": len(analysis)})
            expected_kind = {"P03": "github_release", "P04": "research_paper", "P19": "research_paper"}.get(case["id"])
            if expected_kind:
                check("requested_source_kind", all(((item.get("source_type") == expected_kind or parse.urlparse(item.get("url", "")).hostname in ("arxiv.org", "export.arxiv.org")) if expected_kind != "github_release"
                      else parse.urlparse(item.get("url", "")).hostname == "github.com" and "/releases/" in item.get("url", "")) for item in selected),
                      [item.get("source_type") for item in selected])
            check("selected_dates_in_window", all(constraints.get("date_from", "") <= item.get("published_at", "")[:10] <= constraints.get("date_to", "9999-12-31") for item in selected))
            check("requested_count_respected", len(analysis) <= constraints.get("requested_count", 5), len(analysis))
            selected_topics = {topic for row in analysis for topic in row.get("matched_query_topics", [])}
            claimed_coverage = report.get("topic_coverage") or {}
            check("topic_coverage_matches_final_selection",
                  all(bool(covered) == (topic in selected_topics) for topic, covered in claimed_coverage.items()),
                  {"claimed": claimed_coverage, "selected_topics": sorted(selected_topics)})
            if case["id"] == "P19":
                check("future_window_has_no_evidence", not analysis and constraints.get("date_from") == "2099-01-01"
                      and constraints.get("date_to") == "2099-01-07", constraints)
            pairs = report.get("stages") or []
            for stage in {item.get("stage") for item in pairs}:
                start = next((i for i, item in enumerate(pairs) if item.get("stage") == stage and item.get("status") == "started"), None)
                end = next((i for i, item in enumerate(pairs) if item.get("stage") == stage and item.get("status") in ("completed", "failed")), None)
                check("workflow_stage_lifecycle_" + str(stage), start is not None and end is not None and start < end)
    return checks


def save_artifacts(api: Api, cid: str, case_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    saved = {}
    for kind, value in (result.get("artifacts") or {}).items():
        if not isinstance(value, str):
            continue
        status, content = api.call(f"/companies/{cid}/workbench/artifact?" + parse.urlencode({"path": value}), raw=True)
        suffix = Path(value).suffix or ".txt"
        path = case_dir / (re.sub(r"[^a-zA-Z0-9_-]", "_", kind) + suffix)
        path.write_bytes(content)
        saved[kind] = {"kind": kind, "path": str(path), "server_path": value, "http_status": status,
                       "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    return saved


def run_case(api: Api, suite: dict[str, Any], companies: dict[str, str], sessions: dict[str, str], case: dict[str, Any]) -> dict[str, Any]:
    cid = companies[case["persona"]]
    base = f"/companies/{cid}/workbench"
    key = f"{case['persona']}:{case['session']}"
    case_dir = api.out / "cases" / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / "case.json", case)
    if case.get("wiki_updates"):
        persona = suite["personas"][case["persona"]]
        api.ok(base + "/wiki/pages", {"path": "wiki/acceptance_profile.md", "body": fact_body(persona, case["wiki_updates"])})
    before_wiki = api.ok(base + "/wiki")
    write_json(case_dir / "wiki_before.json", before_wiki)
    if key not in sessions:
        session = api.ok(base + "/sessions", {"title": f"验收 {case['persona']} {case['session']}",
                                              "mode": "auto", "model_id": "deepseek-v4-flash"})
        sessions[key] = session["session_id"]
        write_json(api.out / "sessions.json", sessions)
    sid = sessions[key]
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    if case.get("sse"):
        result, events = api.stream(base + f"/sessions/{sid}/messages/stream", case["query"])
        write_json(case_dir / "sse.json", events)
    else:
        result = api.ok(base + f"/sessions/{sid}/messages", {"message": case["query"]})
    duration = round(time.perf_counter() - started, 3)
    write_json(case_dir / "response.json", result)
    (case_dir / "answer.md").write_text(result.get("answer", ""), encoding="utf-8")
    trace = api.ok(base + f"/sessions/{sid}/runs/{result['run_id']}/trace") if result.get("run_id") else {}
    write_json(case_dir / "trace.json", trace)
    messages = api.ok(base + f"/sessions/{sid}/messages")
    write_json(case_dir / "messages.json", messages)
    after_wiki = api.ok(base + "/wiki")
    write_json(case_dir / "wiki_after.json", after_wiki)
    artifacts = save_artifacts(api, cid, case_dir, result)
    checks = mechanical_checks(case, result, trace, events, artifacts)
    checks.append({"check": "chat_does_not_silently_rewrite_wiki", "passed": before_wiki == after_wiki})
    model_steps = [s for s in trace.get("steps", []) if s.get("step_type") == "model_call"]
    usage = [s.get("metadata", {}).get("usage", {}) for s in model_steps]
    record = {"id": case["id"], "persona": case["persona"], "session_id": sid, "company_id": cid,
              "query": case["query"], "run_id": result.get("run_id"), "elapsed_seconds": duration,
              "structural_pass": all(c["passed"] for c in checks), "checks": checks,
              "semantic_verdict": "pending_independent_review", "expected_semantics": case["expect"],
              "trace_summary": trace.get("trace_summary"), "model_call_count": len(model_steps),
              "input_tokens": sum(u.get("input_tokens", u.get("prompt_tokens", 0)) for u in usage),
              "output_tokens": sum(u.get("output_tokens", u.get("completion_tokens", 0)) for u in usage),
              "artifacts": artifacts, "sse_first_chunk_ms": next((e["at_ms"] for e in events if e["event"] == "chunk"), None),
              "evidence_dir": str(case_dir)}
    write_json(case_dir / "checks.json", record)
    return record


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--label", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--cases", default="")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--probes-only", action="store_true")
    args = parser.parse_args()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    assert 10 <= len(suite["cases"]) <= 50
    out = ROOT / "data" / "acceptance" / ("persona_" + args.label)
    out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base_url, out)
    companies = json.loads((out / "companies.json").read_text(encoding="utf-8")) if args.resume else setup(api, suite, "accept_" + args.label)
    if not args.resume or args.probes_only:
        probes = api_probes(api, companies)
        print(f"API probes: {sum(p['passed'] for p in probes)}/{len(probes)}", flush=True)
    if args.probes_only:
        return
    if args.setup_only:
        print(json.dumps(companies, ensure_ascii=False), flush=True)
        return
    sessions_path = out / "sessions.json"
    sessions = json.loads(sessions_path.read_text(encoding="utf-8")) if sessions_path.exists() else {}
    results_path = out / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if args.resume and results_path.exists() else []
    wanted = set(args.cases.split(",")) if args.cases else set()
    for case in suite["cases"]:
        if wanted and case["id"] not in wanted:
            continue
        previous = out / "cases" / case["id"]
        if args.resume and previous.exists():
            archived = out / "attempts" / datetime.now().strftime("%Y%m%d_%H%M%S_%f") / case["id"]
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(previous, archived)
        print(f"RUN {case['id']} {case['query']}", flush=True)
        try:
            record = run_case(api, suite, companies, sessions, case)
        except Exception as exc:
            record = {"id": case["id"], "query": case["query"], "structural_pass": False, "error": repr(exc)}
        results = [item for item in results if item["id"] != case["id"]]
        results.append(record)
        write_json(out / "results.json", results)
        print(json.dumps({k: record.get(k) for k in ("id", "structural_pass", "elapsed_seconds", "run_id", "error")}, ensure_ascii=False), flush=True)
        if "insufficient" in str(record.get("error", "")).lower() or "balance" in str(record.get("error", "")).lower():
            break
    times = [x["elapsed_seconds"] for x in results if "elapsed_seconds" in x]
    write_json(out / "summary.json", {"case_count": len(results), "structural_pass": sum(x["structural_pass"] for x in results),
                                      "semantic_status": "pending_independent_review", "median_seconds": statistics.median(times) if times else None,
                                      "total_input_tokens": sum(x.get("input_tokens", 0) for x in results),
                                      "total_output_tokens": sum(x.get("output_tokens", 0) for x in results),
                                      "data_entry": "HTTP API only", "base_url": args.base_url})


if __name__ == "__main__":
    main()
