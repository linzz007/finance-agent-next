"""Independent two-Wiki acceptance runner, using public HTTP APIs only.

Default execution is an offline plan. --live opts into potentially paid messages;
--setup-only creates fresh Wiki workspaces without sending model requests. No
runtime, connector or store imports, no source configuration writes, no retries
of message POSTs. HTTP paths and SSE framing follow run_persona_acceptance.py.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "fixtures" / "shared_discovery_acceptance.json"
WIKI_PATH = "wiki/shared_discovery_acceptance.md"
SHANGHAI = timezone(timedelta(hours=8))
REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(
    r"^(?:api[_-]?key|.*[_-]api[_-]?key|authorization|proxy[_-]authorization|"
    r"cookie|set[_-]cookie|password|passwd|client[_-]secret|secret|.*[_-]secret|"
    r"access[_-]token|refresh[_-]token|id[_-]token|token|credential[s]?|"
    r"private[_-]key|x[_-]api[_-]key)$", re.I,
)
SECRET_VALUE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"
)
ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:[A-Z0-9_]*API[_-]?KEY|authorization|proxy-authorization|"
    r"cookie|set-cookie|password|passwd|client_secret|secret|access_token|refresh_token|token)"
    r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;<>&]+)"
)
URL = re.compile(r"https?://[^\s<>\"']+")
OPTIONAL_FIELDS = (
    "discovery_plan", "report_request", "source_cleaning_diagnostics", "cleaning_diagnostics",
)


def redact_text(text: str) -> str:
    def clean_url(match: re.Match[str]) -> str:
        try:
            parts = parse.urlsplit(match.group())
            host = parts.netloc.rsplit("@", 1)[-1]
            query = parse.urlencode([
                (key, REDACTED if SECRET_KEY.fullmatch(key) or key.lower() in
                 {"key", "signature", "sig", "x-amz-signature", "x-amz-credential"} else value)
                for key, value in parse.parse_qsl(parts.query, keep_blank_values=True)
            ])
            return parse.urlunsplit((parts.scheme, host, parts.path, query, ""))
        except ValueError:
            return "[REDACTED_URL]"

    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  REDACTED, text, flags=re.S)
    text = URL.sub(clean_url, text)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=:-]+", REDACTED, text)
    text = SECRET_VALUE.sub(REDACTED, text)
    return ASSIGNMENT.sub(lambda match: match.group(1) + '"[REDACTED]"', text)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): REDACTED if SECRET_KEY.fullmatch(str(key)) else sanitize(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(payload), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def validate_base_url(value: str) -> str:
    parts = parse.urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError("base-url must be HTTP(S), without credentials, query or fragment")
    return value.rstrip("/")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never replay a paid POST or send test data to a redirected endpoint.
        return None


class Api:
    def __init__(self, base_url: str, out: Path, timeout: float = 240) -> None:
        self.base = validate_base_url(base_url)
        self.out, self.timeout = out, timeout
        self.opener = request.build_opener(NoRedirect())
        self.requests: list[dict[str, Any]] = []

    def _log(self, req: request.Request, started: float, status: int | None) -> None:
        self.requests.append({
            "method": req.get_method(),
            "path": parse.urlsplit(req.full_url).path,
            "status": status,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        })
        # Request bodies, headers, URL query strings and exception bodies are not logged.
        write_json(self.out / "api_requests.json", self.requests)

    def call(self, path: str, payload: Any = None, *, raw: bool = False) -> tuple[int, Any]:
        started = time.perf_counter()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.base + path, data=data,
                              headers={"Content-Type": "application/json"})
        status = None
        try:
            try:
                response = self.opener.open(req, timeout=self.timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                status, body = response.status, response.read()
            if raw:
                return status, body
            return status, json.loads(body.decode("utf-8-sig")) if body else {}
        finally:
            self._log(req, started, status)

    def ok(self, path: str, payload: Any = None) -> Any:
        status, value = self.call(path, payload)
        if not 200 <= status < 300:
            raise RuntimeError(f"HTTP {status}: {parse.urlsplit(path).path}")
        return value

    def stream(self, path: str, message: str, evidence_path: Path) -> tuple[dict, list]:
        started, status = time.perf_counter(), None
        req = request.Request(
            self.base + path,
            data=json.dumps({"message": message}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        events: list[dict[str, Any]] = []
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                status = response.status
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raise ValueError("Expected SSE Content-Type")
                for name, payload in sse_events(response):
                    events.append({"event": name, "data": payload,
                                   "at_ms": round((time.perf_counter() - started) * 1000, 2)})
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise
        finally:
            # Credentials can straddle chunk boundaries; save only chunk lengths.
            write_json(evidence_path, [
                {**event, "data": {"content_length": len(event["data"].get("content", ""))}}
                if event["event"] == "chunk" else event for event in events
            ])
            self._log(req, started, status)
        result: dict[str, Any] = {}
        chunks = []
        for event in events:
            if event["event"] == "result":
                result.update(event["data"])
            elif event["event"] == "chunk":
                chunks.append(event["data"].get("content", ""))
            elif event["event"] == "error":
                result["stream_error"] = event["data"]
        if chunks:
            result["answer"] = "".join(chunks)
        return result, events


def sse_events(lines: Iterable[bytes]) -> Iterable[tuple[str, dict[str, Any]]]:
    name, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith(":"):
            continue
        if not line:
            if data:
                payload = json.loads("\n".join(data))
                if not isinstance(payload, dict):
                    raise ValueError("SSE payload must be an object")
                yield name, payload
            name, data = "message", []
        elif line.startswith("event:"):
            name = line[6:].lstrip(" ")
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise ValueError("Truncated SSE frame; no message retry was attempted")


def load_suite() -> dict[str, Any]:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    cases = suite["cases"]
    ids = [case["id"] for case in cases]
    if not 10 <= len(cases) <= 14 or len(set(ids)) != len(ids):
        raise ValueError("Expected 10-14 uniquely identified cases")
    if set(suite["personas"]) != {"ths", "personal"}:
        raise ValueError("Expected exactly the ths and personal Wikis")
    seen: dict[str, dict] = {}
    for case in cases:
        if not re.fullmatch(r"SD\d{2}", case["id"]) or not case.get("review"):
            raise ValueError("Invalid case ID or missing manual review criteria")
        for dep in case.get("depends_on", []):
            prior = seen.get(dep)
            if not prior or (prior["persona"], prior["session"]) != (case["persona"], case["session"]):
                raise ValueError("Dependencies must precede the case in the same Wiki/session")
        seen[case["id"]] = case
    for pair in suite["pairs"]:
        left, right = [seen[item] for item in pair["case_ids"]]
        if (left["query"] != right["query"] or left["window"] != right["window"]
                or {left["persona"], right["persona"]} != {"ths", "personal"}):
            raise ValueError("Paired reports must have identical requests and time windows")
    return suite


def select_cases(suite: dict, wanted: str) -> list[dict]:
    selected = {item.strip() for item in wanted.split(",") if item.strip()}
    by_id = {case["id"]: case for case in suite["cases"]}
    unknown = selected - by_id.keys()
    if unknown:
        raise ValueError("Unknown case IDs: " + ", ".join(sorted(unknown)))
    if not selected:
        return list(suite["cases"])

    def include(case_id: str) -> None:
        for dep in by_id[case_id].get("depends_on", []):
            if dep not in selected:
                selected.add(dep)
                include(dep)

    for case_id in sorted(selected):
        include(case_id)
    return [case for case in suite["cases"] if case["id"] in selected]


def render_cases(cases: list[dict], suite: dict, anchor: date) -> list[dict]:
    rendered = []
    for case in cases:
        window = case.get("window")
        if window == "shared":
            window = {"lookback_days": suite["window"]["lookback_days"]}
        if isinstance(window, dict) and "lookback_days" in window:
            window = {"date_from": (anchor - timedelta(days=window["lookback_days"])).isoformat(),
                      "date_to": anchor.isoformat()}
        rendered.append({**case, "query": case["query"].format(**(window or {})),
                         "expected_window": window})
    return rendered


def fact_body(persona: dict[str, Any]) -> str:
    lines = [f"# {persona['name']}", "", "Acceptance profile; user-supplied context, not internal disclosure.", ""]
    for fact in persona["facts"]:
        lines.extend([f"## FACT: {fact['id']}", "- source: user_confirmed", "- confidence: 1.0",
                      "- importance: 5", f"- value: {fact['value']}",
                      fact["value"], ""])
    return "\n".join(lines)


def setup(api: Api, suite: dict, prefix: str) -> dict[str, str]:
    companies = {key: f"{prefix}_{key}" for key in suite["personas"]}
    listing = api.ok("/companies")
    existing = set(listing.get("company_ids") or [])
    existing.update(item["company_id"] for item in listing.get("companies", []))
    if existing.intersection(companies.values()):
        raise ValueError("Prefix already exists; use a fresh prefix. No existing Wiki was changed.")
    for key, cid in companies.items():
        persona = suite["personas"][key]
        status, _ = api.call("/companies", {"company_id": cid, "name": persona["name"],
                                            "profile_type": persona["profile_type"]})
        if status != 201:
            raise RuntimeError(f"Expected fresh company creation, got HTTP {status}; stopping setup")
        api.ok(f"/companies/{cid}/workbench/wiki/pages",
               {"path": WIKI_PATH, "body": fact_body(persona), "create": True})
        wiki = api.ok(f"/companies/{cid}/workbench/wiki")
        facts = {item["fact_id"]: item for item in wiki.get("facts", [])}
        if any(fact["id"] not in facts or facts[fact["id"]].get("value") != fact["value"]
               for fact in persona["facts"]):
            raise RuntimeError("Wiki API readback did not preserve all fixture facts")
        write_json(api.out / f"wiki_{key}_initial.json", wiki)
        write_json(api.out / "companies.json", {k: v for k, v in companies.items()
                                               if k == key or (api.out / f"wiki_{k}_initial.json").exists()})
    return companies


def checks_pass(checks: list[dict]) -> bool:
    return not any(item["status"] == "fail" for item in checks)


def add_check(checks: list[dict], name: str, passed: bool | None, actual: Any = None) -> None:
    checks.append({"check": name, "status": "skip" if passed is None else "pass" if passed else "fail",
                   "actual": actual})


def find_fields(value: Any, names: set[str], path: str = "$", depth: int = 0) -> list[dict]:
    if depth > 40:
        return []
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}"
            if key in names:
                found.append({"field": key, "path": current, "value": item})
            else:
                found.extend(find_fields(item, names, current, depth + 1))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_fields(item, names, f"{path}[{index}]", depth + 1))
    return found


def date_window(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if value.get("date_from") and value.get("date_to"):
        return {key: str(value[key])[:10] for key in ("date_from", "date_to")}
    period = value.get("period") or {}
    if isinstance(period, dict) and period.get("start") and period.get("end"):
        return {"date_from": str(period["start"])[:10], "date_to": str(period["end"])[:10]}
    for key in ("date_range", "date_window", "time_window", "window", "query_constraints"):
        if window := date_window(value.get(key)):
            return window
    return None


def identifier_pool(value: Any) -> list[str] | None:
    if isinstance(value, dict):
        for key in ("provider_ids", "source_ids", "providers", "sources"):
            if key in value:
                return identifier_pool(value[key])
        return None
    if not isinstance(value, list):
        return None
    ids = []
    for item in value:
        identifier = item if isinstance(item, str) else next(
            (item.get(key) for key in ("provider_id", "source_id", "id") if item.get(key)), None
        ) if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier:
            return None
        ids.append(identifier)
    return sorted(set(ids))


def inspect_artifact(report: dict, trace: dict) -> dict[str, Any]:
    optional = find_fields({"artifact": report, "trace": trace}, set(OPTIONAL_FIELDS))
    windows = []
    for field in optional:
        if field["field"] == "report_request" and (window := date_window(field["value"])):
            windows.append({"path": field["path"], "window": window})
    if window := date_window(report):
        windows.append({"path": "$.artifact", "window": window})
    pool_fields = find_fields(report, {"source_pool", "provider_pool", "shared_provider_pool", "available_providers"})
    pools = [{"path": item["path"], "ids": identifier_pool(item["value"])} for item in pool_fields]
    statuses = (report.get("source_manifest") or {}).get("source_statuses") or []
    return {
        "optional_fields": optional,
        "missing_optional_fields": [name for name in OPTIONAL_FIELDS
                                    if not any(item["field"] == name for item in optional)],
        "windows": windows,
        "declared_pools": pools,
        "source_registry_checksums": sorted({item["value"] for item in
            find_fields(report, {"source_registry_checksum"}) if isinstance(item["value"], str)}),
        "observed_source_ids": identifier_pool(statuses) or [],
        "source_manifest": report.get("source_manifest"),
        "legacy_diagnostics": {key: report.get(key) for key in
                               ("constraint_diagnostics", "quality_diagnostics", "analysis_diagnostics")
                               if key in report},
        "quality_diagnostics": report.get("quality_diagnostics"),
        "wiki_stage_planning": [stage for stage in report.get("stages", [])
                                if stage.get("stage") == "wiki_context"],
        "planning_model_calls": [step for step in trace.get("steps", [])
                                 if step.get("step_type") == "model_call" and
                                 "report_discovery_plan.v1" in json.dumps(step, ensure_ascii=False)],
        "task_profile": report.get("task_profile"),
        "analysis_status": report.get("analysis_status"),
        "incomplete_reasons": report.get("incomplete_reasons"),
        "selected_event_ids": [item.get("event_id") for item in report.get("analysis", [])],
        "selection_schema": "news" if "analysis" in report else "other_or_unavailable",
        "candidate_topic_coverage": report.get("candidate_topic_coverage"),
        "topic_coverage": report.get("topic_coverage"),
    }


def report_checks(case: dict, report: dict, inspection: dict, cid: str) -> list[dict]:
    checks: list[dict] = []
    add_check(checks, "report_company_matches", report.get("company_id") == cid)
    windows = inspection["windows"]
    add_check(checks, "report_window_available", bool(windows), windows)
    for index, window in enumerate(windows):
        add_check(checks, f"report_window_matches_{index}",
                  window["window"] == case["expected_window"], window)
    for name in OPTIONAL_FIELDS:
        fields = [item for item in inspection["optional_fields"] if item["field"] == name]
        add_check(checks, f"optional_{name}_structured",
                  all(isinstance(item["value"], dict) for item in fields) if fields else None,
                  [item["path"] for item in fields] or "not exposed; manual coverage gap")
    report_request = report.get("report_request")
    plan = report.get("discovery_plan")
    if isinstance(report_request, dict):
        add_check(checks, "request_subject_matches", report_request.get("subject_id") == cid)
        add_check(checks, "request_max_items_matches", report_request.get("max_items") == case["max_items"],
                  report_request.get("max_items"))
        refs = report_request.get("wiki_fact_refs") or []
        other = "personal_" if case["persona"] == "ths" else "ths_"
        add_check(checks, "request_wiki_refs_scoped", bool(refs) and
                  any(str(ref).startswith(case["persona"] + "_") for ref in refs) and
                  not any(str(ref).startswith(other) for ref in refs), refs)
        add_check(checks, "request_profile_version_recorded",
                  bool(re.fullmatch(r"[0-9a-f]{64}", str(report_request.get("profile_checksum") or ""))))
        origins = report_request.get("parameter_sources") or {}
        add_check(checks, "request_sources_from_shared_registry", origins.get("sources") == "shared_registry", origins)
        # Topic labels are model-generated and may be Chinese. Judge their meaning manually.
        if case.get("topic_origin"):
            add_check(checks, "request_focus_origin_matches", origins.get("focus") == case["topic_origin"],
                      origins.get("focus"))
    if isinstance(plan, dict):
        add_check(checks, "discovery_window_matches", date_window(plan) == case["expected_window"], date_window(plan))
        add_check(checks, "discovery_terms_available", isinstance(plan.get("search_terms"), list) and
                  bool(plan["search_terms"]) and all(isinstance(term, str) and term.strip() for term in plan["search_terms"]))
        if isinstance(report_request, dict):
            add_check(checks, "plan_uses_request_source_pool", bool(report_request.get("source_pool")) and
                      identifier_pool(plan.get("source_pool")) == identifier_pool(report_request["source_pool"]))
            add_check(checks, "plan_uses_request_registry_version", bool(report_request.get("source_registry_checksum")) and
                      plan.get("source_registry_checksum") == report_request["source_registry_checksum"])
        diagnostics = plan.get("diagnostics") or {}
        model_calls = diagnostics.get("model_calls")
        add_check(checks, "planning_model_call_budget", type(model_calls) is int and 0 <= model_calls <= 1,
                  diagnostics)
        add_check(checks, "planning_model_calls_audited", len(inspection["planning_model_calls"]) == model_calls,
                  {"diagnostic_calls": model_calls, "trace_calls": len(inspection["planning_model_calls"])})
        completed_wiki = [stage for stage in inspection["wiki_stage_planning"] if stage.get("status") == "completed"]
        add_check(checks, "planning_in_wiki_stage", any(
            (stage.get("discovery_plan") or (stage.get("output") or {}).get("discovery_plan"))
            for stage in completed_wiki))
    quality = report.get("quality_diagnostics") or {}
    count_fields = ("raw_count", "cleaned_count", "second_pass_removed_count")
    if all(field in quality for field in count_fields):
        valid_counts = all(type(quality[field]) is int and quality[field] >= 0 for field in count_fields)
        add_check(checks, "cleaning_counts_nonnegative", valid_counts, {field: quality[field] for field in count_fields})
        if valid_counts:
            add_check(checks, "second_pass_cleaning_counts_reconcile",
                      quality["raw_count"] - quality["cleaned_count"] == quality["second_pass_removed_count"])
            add_check(checks, "cleaning_counts_match_saved_items", quality["raw_count"] == len(report.get("raw_items", []))
                      and quality["cleaned_count"] == len(report.get("cleaned_items", [])))
    else:
        add_check(checks, "second_pass_cleaning_counts_reconcile", None, "new counts not exposed")
    collection = quality.get("collection_cleaning")
    add_check(checks, "collection_cleaning_structured", isinstance(collection, dict) if collection is not None else None)
    if isinstance(collection, dict) and "returned_count" in collection:
        add_check(checks, "collection_count_matches_second_pass_input",
                  collection["returned_count"] == len(report.get("raw_items", [])), collection["returned_count"])
    constraints = report.get("query_constraints") or {}
    if "requested_count" in constraints:
        add_check(checks, "requested_count_matches", constraints["requested_count"] == case["max_items"],
                  constraints["requested_count"])
    else:
        add_check(checks, "requested_count_matches", None, "not exposed in legacy query_constraints")
    profile = report.get("task_profile") or {}
    add_check(checks, "profile_type_matches",
              profile.get("profile_type") == ("company" if case["persona"] == "ths" else "personal")
              if "profile_type" in profile else None, profile.get("profile_type"))
    if case.get("topic_origin"):
        add_check(checks, "topic_origin_matches",
                  profile.get("focus_origin") == case["topic_origin"] if "focus_origin" in profile else None,
                  profile.get("focus_origin"))
    if "analysis" not in report:
        add_check(checks, "news_selection_integrity", None,
                  "non-news schema; inspect policy_documents/impact_assessments manually")
        return checks
    rows = report["analysis"]
    events = {item.get("event_id"): item for item in report.get("structured_events", [])}
    selected_ids = [item.get("event_id") for item in rows]
    add_check(checks, "selected_count_within_limit", len(rows) <= case["max_items"], len(rows))
    add_check(checks, "selected_ids_unique_and_resolvable",
              len(set(selected_ids)) == len(rows) and all(item and item in events for item in selected_ids),
              selected_ids)
    if case.get("expect_empty"):
        add_check(checks, "future_window_no_selected_evidence", not rows, selected_ids)
    if not rows:
        add_check(checks, "selected_evidence_fields", None, "no selections; not proof of useful content")
        return checks
    add_check(checks, "selected_evidence_not_fixture", report.get("data_mode") != "fixture"
              if report.get("data_mode") else None, report.get("data_mode"))
    for index, event_id in enumerate(selected_ids):
        event = events.get(event_id, {})
        try:
            source = parse.urlsplit(event.get("url", ""))
            valid_url = source.scheme in {"http", "https"} and bool(source.hostname)
            published = datetime.fromisoformat(event.get("published_at", "").replace("Z", "+00:00"))
            published_date = (published.astimezone(SHANGHAI) if published.tzinfo else published).date().isoformat()
            valid_date = case["expected_window"]["date_from"] <= published_date <= case["expected_window"]["date_to"]
        except (ValueError, TypeError):
            valid_url, valid_date = False, False
        add_check(checks, f"selected_{index}_source_url", bool(valid_url), event.get("url"))
        add_check(checks, f"selected_{index}_published_in_window", valid_date, event.get("published_at"))
    coverage = report.get("topic_coverage")
    if isinstance(coverage, dict):
        selected_topics = {topic for row in rows for topic in row.get("matched_query_topics", [])}
        add_check(checks, "coverage_matches_final_selection",
                  all(bool(value) == (topic in selected_topics) for topic, value in coverage.items()), coverage)
    return checks


def mechanical_checks(case: dict, result: dict, trace: dict, events: list,
                      artifacts: dict, report: dict | None, cid: str, sid: str) -> tuple[list, dict]:
    checks: list[dict] = []
    summary = trace.get("trace_summary") or {}
    steps = trace.get("steps") or []
    types = [item.get("step_type") for item in steps]
    add_check(checks, "answer_nonempty", bool(str(result.get("answer") or "").strip()))
    add_check(checks, "no_stream_error", not result.get("stream_error"))
    add_check(checks, "trace_run_matches", bool(result.get("run_id")) and result["run_id"] == trace.get("run_id"))
    add_check(checks, "session_matches", result.get("session_id") == sid == trace.get("session_id"))
    allowed = {"done", "blocked"} if case.get("expect_empty") else {"done"}
    add_check(checks, "run_completed_not_failed", trace.get("status") in allowed, trace.get("status"))
    add_check(checks, "context_manifest", bool(summary.get("has_context_manifest")))
    add_check(checks, "ordered_steps", bool(steps) and
              [item.get("step_index") for item in steps] == list(range(1, len(steps) + 1)))
    add_check(checks, "final_answer_and_stop", "final_answer" in types and "run_stopped" in types)
    add_check(checks, "memory_not_written", summary.get("memory_write_count") == 0,
              summary.get("memory_write_count"))
    skill = "external_impact_report" if case["kind"] == "report" else "general_chat"
    add_check(checks, "skill_routing", result.get("selected_skill_id") == skill == trace.get("skill_id"),
              {"result": result.get("selected_skill_id"), "trace": trace.get("skill_id")})
    delegates = [step for step in steps if step.get("step_type") == "subagent_delegate"]
    if case["kind"] == "simple":
        add_check(checks, "simple_chat_no_subagents", not delegates and
                  not summary.get("subagent_call_count") and not summary.get("subagent_roles"))
    elif delegates:
        for index, step in enumerate(delegates):
            output = step.get("output_payload") or {}
            add_check(checks, f"actual_delegate_{index}_auditable",
                      bool(output.get("task_id") and output.get("context_manifest_id")
                           and output.get("stop_reason")),
                      {key: output.get(key) for key in ("task_id", "context_manifest_id", "stop_reason")})
    if case["kind"] != "report":
        add_check(checks, "chat_has_no_report_artifacts", not result.get("artifacts"))
    if case["transport"] == "sse":
        names = [item["event"] for item in events]
        add_check(checks, "sse_complete_protocol", bool(names) and names[0] == "start" and
                  names[-1] == "done" and names.count("result") == 1 and
                  "chunk" in names and "error" not in names, names)
        if "result" in names and "chunk" in names:
            add_check(checks, "sse_result_before_chunks", names.index("result") < names.index("chunk"))
        if case["kind"] == "report" or delegates:
            add_check(checks, "sse_progress_before_result", "progress" in names and "result" in names
                      and names.index("progress") < names.index("result"))
    for kind, artifact in artifacts.items():
        add_check(checks, "artifact_accessible_" + kind,
                  artifact["http_status"] == 200 and artifact.get("saved", False), artifact)
    inspection = inspect_artifact(report or {}, trace)
    if case["kind"] == "report":
        required = not case.get("expect_empty") or bool(result.get("artifacts"))
        if required:
            add_check(checks, "html_report_accessible", bool(artifacts.get("html_report", {}).get("saved")))
            add_check(checks, "run_artifact_json_available", report is not None)
        if report is not None:
            checks.extend(report_checks(case, report, inspection, cid))
        else:
            add_check(checks, "report_fields_inspection", None, "no report artifact returned")
    return checks, inspection


def save_artifacts(api: Api, cid: str, case_dir: Path, result: dict) -> tuple[dict, dict | None]:
    saved, report = {}, None
    for index, (kind, server_path) in enumerate((result.get("artifacts") or {}).items()):
        if not isinstance(server_path, str):
            saved[kind] = {"http_status": None, "saved": False, "reason": "non-string artifact path"}
            continue
        status, content = api.call(f"/companies/{cid}/workbench/artifact?" +
                                   parse.urlencode({"path": server_path}), raw=True)
        suffix = Path(server_path).suffix.lower()
        name = f"{index:02d}_" + re.sub(r"[^A-Za-z0-9_-]", "_", kind)
        entry = {"server_path": server_path, "http_status": status, "saved": False}
        if status == 200 and suffix in {".json", ".md", ".html", ".txt"}:
            try:
                text = content.decode("utf-8-sig")
                path = case_dir / (name + suffix)
                if suffix == ".json":
                    payload = json.loads(text)
                    write_json(path, payload)
                    if kind == "run_artifact" and isinstance(payload, dict):
                        report = payload
                else:
                    path.write_text(redact_text(text), encoding="utf-8")
                entry.update({"saved": True, "path": str(path), "sanitized": True,
                              "saved_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "server_sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
            except (UnicodeError, ValueError):
                entry["reason"] = "invalid text/JSON artifact; raw body not saved"
        saved[kind] = entry
    return saved, report


def run_case(api: Api, companies: dict, sessions: dict, case: dict, prior: dict) -> dict:
    cid = companies[case["persona"]]
    base = f"/companies/{cid}/workbench"
    key = f"{case['persona']}:{case['session']}"
    case_dir = api.out / "cases" / case["id"]
    case_dir.mkdir(parents=True, exist_ok=False)
    write_json(case_dir / "case.json", case)
    before = api.ok(base + "/wiki")
    write_json(case_dir / "wiki_before.json", before)
    if key not in sessions:
        session = api.ok(base + "/sessions", {"title": f"Shared discovery {key}",
                                              "mode": "auto", "model_id": "deepseek-v4-flash"})
        sessions[key] = session["session_id"]
        write_json(api.out / "sessions.json", sessions)
    sid = sessions[key]
    started, events = time.perf_counter(), []
    if case["transport"] == "sse":
        result, events = api.stream(base + f"/sessions/{sid}/messages/stream", case["query"],
                                    case_dir / "sse.json")
    else:
        result = api.ok(base + f"/sessions/{sid}/messages", {"message": case["query"]})
    elapsed = round(time.perf_counter() - started, 3)
    write_json(case_dir / "response.json", result)
    (case_dir / "answer.md").write_text(redact_text(str(result.get("answer") or "")), encoding="utf-8")
    trace = api.ok(base + f"/sessions/{sid}/runs/{result['run_id']}/trace") if result.get("run_id") else {}
    write_json(case_dir / "trace.json", trace)
    write_json(case_dir / "messages.json", api.ok(base + f"/sessions/{sid}/messages"))
    after = api.ok(base + "/wiki")
    write_json(case_dir / "wiki_after.json", after)
    artifacts, report = save_artifacts(api, cid, case_dir, result)
    checks, inspection = mechanical_checks(case, result, trace, events, artifacts, report, cid, sid)
    add_check(checks, "wiki_not_silently_rewritten", before == after)
    if case["kind"] in {"followup", "autonomous"}:
        for dep in case.get("depends_on", []):
            parent = prior[dep]
            add_check(checks, "followup_same_session_" + dep, sid == parent.get("session_id"))
            reads = [item.get("output_payload", {}).get("read_audit", {})
                     for item in trace.get("steps", []) if item.get("step_type") == "artifact_read"]
            artifact = parent.get("artifacts", {}).get("run_artifact", {})
            expected = bool(parent.get("inspection", {}).get("selected_event_ids"))
            matches = any(read.get("path") == artifact.get("server_path") and
                          read.get("sha256") == artifact.get("server_sha256") for read in reads)
            add_check(checks, "same_report_artifact_read_" + dep, matches if expected else None,
                      reads or "no prior selected evidence; review insufficient-evidence response")
    write_json(case_dir / "inspection.json", inspection)
    record = {"id": case["id"], "persona": case["persona"], "query": case["query"],
              "company_id": cid, "session_id": sid, "run_id": result.get("run_id"),
              "elapsed_seconds": elapsed, "mechanical_pass": checks_pass(checks), "checks": checks,
              "answer_quality": {"status": "pending_manual_review", "criteria": case["review"]},
              "delegation_observation": trace.get("subagent_summary") or {},
              "inspection": inspection, "artifacts": artifacts, "evidence_dir": str(case_dir)}
    write_json(case_dir / "checks.json", record)
    return record


def compare_pairs(suite: dict, results: dict) -> list[dict]:
    comparisons = []
    for pair in suite["pairs"]:
        ids = pair["case_ids"]
        checks: list[dict] = []
        rows = [results.get(case_id) for case_id in ids]
        if not all(row and row.get("inspection") for row in rows):
            add_check(checks, "pair_available", None, "both report inspections are required")
        else:
            left, right = rows
            add_check(checks, "identical_user_query", left["query"] == right["query"])
            windows = [row["inspection"]["windows"] for row in rows]
            add_check(checks, "identical_effective_window", windows[0][0]["window"] == windows[1][0]["window"]
                      if all(windows) else None, windows)
            pools = [[item["ids"] for item in row["inspection"]["declared_pools"] if item["ids"] is not None]
                     for row in rows]
            add_check(checks, "shared_declared_provider_pool",
                      all(pool == pools[0][0] for group in pools for pool in group) if all(pools) else None,
                      pools if all(pools) else "not exposed; observed source IDs do not prove configured pool")
            checksums = [row["inspection"]["source_registry_checksums"] for row in rows]
            add_check(checks, "shared_source_registry_version", len(checksums[0]) == 1 and checksums[0] == checksums[1]
                      if all(checksums) else None, checksums)
        comparisons.append({"id": pair["id"], "case_ids": ids, "checks": checks,
                            "mechanical_pass": checks_pass(checks),
                            "observed_source_ids": {case_id: (results.get(case_id, {}).get("inspection") or {}).get("observed_source_ids")
                                                    for case_id in ids},
                            "answer_quality": {"status": "pending_manual_review", "criteria": pair["review"]}})
    return comparisons


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="", help="Comma-separated IDs; dependencies included automatically")
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--output", type=Path, help="Fresh output directory; never overwrites a nonempty directory")
    parser.add_argument("--prefix", help="Fresh workspace prefix; existing companies are never modified")
    parser.add_argument("--timeout", type=float, default=240, help="HTTP timeout in seconds; no POST retries")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Create Wikis and send potentially paid live messages")
    mode.add_argument("--setup-only", action="store_true", help="Create Wikis through API; no messages")
    mode.add_argument("--dry-run", action="store_true", help="Offline plan only (the default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        base = validate_base_url(args.base_url)
        suite = load_suite()
        now = datetime.now(SHANGHAI)
        stamp = now.strftime("%Y%m%d_%H%M%S_%f")
        prefix = args.prefix or f"sd_accept_{stamp}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,70}", prefix):
            raise ValueError("prefix must contain 1-71 ASCII letters, digits, underscores or hyphens")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        cases = render_cases(select_cases(suite, args.cases), suite, now.date())
        out = (args.output or ROOT / "data" / "acceptance" / f"shared_discovery_{stamp}").resolve()
    except (ValueError, KeyError) as exc:
        parser.error(redact_text(str(exc)))
    plan = {"suite_version": suite["version"], "fixture_sha256": hashlib.sha256(SUITE.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "base_url": base, "prefix": prefix, "output": str(out), "anchor_date": now.date().isoformat(),
            "timezone": suite["window"]["timezone"], "requested_cases": args.cases or "all",
            "effective_cases": [case["id"] for case in cases], "cases": cases,
            "provider_pool": suite["provider_pool"], "model_id": "deepseek-v4-flash",
            "message_calls": 0 if args.setup_only else len(cases),
            "mode": "live" if args.live else "setup_only" if args.setup_only else "offline_plan",
            "live_reproducibility": "Inputs and date anchor are saved; upstream content/model output can change. No server clock or source snapshot is forced."}
    if not args.live and not args.setup_only:
        print(json.dumps(sanitize(plan), ensure_ascii=False, indent=2))
        return 0
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error("output must be a fresh or empty directory; nothing was overwritten")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "plan.json", plan)
    write_json(out / "fixture.json", suite)
    print(f"Effective cases: {', '.join(plan['effective_cases'])}; mode={plan['mode']}", flush=True)
    api = Api(base, out, args.timeout)
    results: dict[str, dict] = {}
    sessions: dict[str, str] = {}
    try:
        companies = setup(api, suite, prefix)
        if not args.setup_only:
            for case in cases:
                print(f"RUN {case['id']} ({case['transport']})", flush=True)
                if any(not results.get(dep, {}).get("run_id") for dep in case.get("depends_on", [])):
                    raise RuntimeError("Missing prerequisite run; no followup message sent")
                results[case["id"]] = run_case(api, companies, sessions, case, results)
                write_json(out / "results.json", list(results.values()))
                print(f"{case['id']} mechanical_pass={results[case['id']]['mechanical_pass']}; quality=pending", flush=True)
                if not results[case["id"]]["mechanical_pass"]:
                    # A failed transport/run can still be executing or billing on the server.
                    failed = {item["check"] for item in results[case["id"]]["checks"] if item["status"] == "fail"}
                    if failed.intersection({"no_stream_error", "run_completed_not_failed", "sse_complete_protocol"}):
                        raise RuntimeError("Run/transport failed; stopped without automatic retries")
    except Exception as exc:
        # Exception repr/body may contain upstream credentials. Only the class is persisted.
        failure = {"error_type": type(exc).__name__, "stage": "case" if sessions else "setup",
                   "case_id": case["id"] if sessions and not args.setup_only else None,
                   "message": "Stopped; inspect sanitized evidence and HTTP status log. No messages retried.",
                   "server_run_may_still_be_running": isinstance(exc, (TimeoutError, error.URLError))}
        write_json(out / "failure.json", failure)
        print(f"STOP {failure['error_type']}; see failure.json and api_requests.json", flush=True)
    comparisons = compare_pairs(suite, results)
    write_json(out / "pair_checks.json", comparisons)
    complete = args.setup_only or len(results) == len(cases)
    failed = (out / "failure.json").exists()
    passed = complete and not failed and all(row["mechanical_pass"] for row in results.values()) and all(
        pair["mechanical_pass"] for pair in comparisons)
    review = {"status": "not_started" if args.setup_only else "pending_manual_review",
              "rubric": suite["review_rubric"],
              "cases": [{"id": case["id"], "status": "pending" if case["id"] in results else "not_run",
                         "criteria": case["review"], "reviewer": None, "verdict": None, "notes": "",
                         "evidence_dir": str(out / "cases" / case["id"])} for case in cases],
              "pairs": comparisons,
              "warning": "Mechanical pass is not answer-quality approval. Skipped checks are unverified, not passes."}
    write_json(out / "manual_review.json", review)
    write_json(out / "summary.json", {"mode": plan["mode"], "complete": complete and not failed,
               "requested_count": len(cases), "executed_count": len(results), "mechanical_pass": passed,
               "mechanical_pass_count": sum(row["mechanical_pass"] for row in results.values()),
               "skipped_checks": sum(item["status"] == "skip" for row in results.values() for item in row["checks"]),
               "answer_quality": review["status"], "data_entry": "public HTTP API only",
               "source_configuration": "unchanged", "output": str(out)})
    print(f"Output: {out}; mechanical_pass={passed}; answer_quality={review['status']}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
