"""Offline tests only: no application imports, providers, databases or model calls."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import run_shared_discovery_acceptance as runner


def cases():
    suite = runner.load_suite()
    return {case["id"]: case for case in runner.render_cases(suite["cases"], suite, date(2026, 9, 10))}


def trace_for(case, *, status="done", delegates=None):
    skill = "external_impact_report" if case["kind"] == "report" else "general_chat"
    steps = [{"step_type": "final_answer", "step_index": 1},
             {"step_type": "run_stopped", "step_index": 2}]
    for delegate in delegates or []:
        steps.append({"step_type": "subagent_delegate", "step_index": len(steps) + 1,
                      "output_payload": delegate})
    return {"run_id": "turn1", "session_id": "session1", "status": status, "skill_id": skill,
            "steps": steps, "trace_summary": {"has_context_manifest": True, "memory_write_count": 0,
                                                "subagent_call_count": len(delegates or [])}}


def report_for(case, *, new_fields=True, rows=True):
    report = {"company_id": "test_ths", "analysis_status": "partial", "data_mode": "live",
              "incomplete_reasons": ["Limited relevant sources; requires independent review"],
              "query_constraints": {**case["expected_window"], "requested_count": case["max_items"]},
              "task_profile": {"profile_type": "company", "focus_origin": case["topic_origin"],
                               "focus_topics": ["金融数据与投研"]},
              "source_manifest": {"source_statuses": [{"source_id": "shared_1", "status": "ok"}]},
              "analysis": [], "structured_events": [], "raw_items": [], "cleaned_items": []}
    if rows:
        report["analysis"] = [{"event_id": "event1", "analysis_status": "partial",
                               "matched_query_topics": ["金融数据与投研"]}]
        report["structured_events"] = [{"event_id": "event1", "url": "https://example.org/paper/1",
                                         "published_at": "2026-09-05T08:00:00Z"}]
        report["topic_coverage"] = {"金融数据与投研": True}
        report["raw_items"] = [{"title": "one"}, {"title": "two"}]
        report["cleaned_items"] = [{"title": "one"}]
    if new_fields:
        report["report_request"] = {
            "subject_id": "test_ths", "period": {"start": case["expected_window"]["date_from"],
                                                    "end": case["expected_window"]["date_to"]},
            "max_items": case["max_items"], "profile_checksum": "a" * 64, "wiki_fact_refs": ["ths_goal"],
            "source_pool": ["shared_1", "shared_2"], "source_registry_checksum": "b" * 64,
            "parameter_sources": {"focus": case["topic_origin"], "sources": "shared_registry"}}
        report["discovery_plan"] = {**case["expected_window"], "search_terms": ["金融数据", "investment research"],
                                     "focus_topics": ["金融数据与投研"],
                                     "source_pool": ["shared_1", "shared_2"], "source_registry_checksum": "b" * 64,
                                     "diagnostics": {"mode": "topic_rules", "model_calls": 0, "errors": []}}
        report["stages"] = [{"stage": "wiki_context", "status": "completed",
                             "discovery_plan": report["discovery_plan"]}]
        report["quality_diagnostics"] = {"raw_count": len(report["raw_items"]),
                                          "cleaned_count": len(report["cleaned_items"]),
                                          "second_pass_removed_count": int(rows),
                                          "collection_cleaning": {"returned_count": len(report["raw_items"])}}
    return report


def failed_names(checks):
    return {item["check"] for item in checks if item["status"] == "fail"}


def test_fixture_has_independent_revised_goals_and_bounded_cost():
    suite = runner.load_suite()
    assert len(suite["cases"]) == 14
    ths = {fact["id"]: fact["value"] for fact in suite["personas"]["ths"]["facts"]}
    personal = {fact["id"]: fact["value"] for fact in suite["personas"]["personal"]["facts"]}
    assert "Agent 工具调用与检索" not in ths["ths_goal"]
    assert "金融数据" in ths["ths_goal"] and "投研" in ths["ths_goal"]
    assert "Agent 自进化/自我改进" in personal["personal_goal"]
    assert "两小时" in personal["personal_constraints"]
    assert all(case.get("max_items", 0) <= 3 for case in suite["cases"])
    assert all("policy_relevance:" not in runner.fact_body(persona) for persona in suite["personas"].values())
    for case in suite["cases"]:
        if case["kind"] == "autonomous":
            assert not any(role in case["query"] for role in ("collector", "analyst", "skeptic", "verifier", "助手"))


def test_dependency_selection_is_ordered_and_deduplicated():
    selected = runner.select_cases(runner.load_suite(), "SD06, SD05,SD06")
    assert [case["id"] for case in selected] == ["SD03", "SD04", "SD05", "SD06"]
    with pytest.raises(ValueError, match="Unknown case"):
        runner.select_cases(runner.load_suite(), "typo")


def test_window_rendering_is_reproducible_and_pairs_are_identical():
    rendered = cases()
    assert rendered["SD03"]["query"] == rendered["SD04"]["query"] == "/report 生成一份报告。"
    assert rendered["SD03"]["expected_window"] == {"date_from": "2026-09-03", "date_to": "2026-09-10"}
    assert rendered["SD07"]["query"] == rendered["SD08"]["query"]
    assert rendered["SD07"]["expected_window"]["date_from"] == "2026-08-11"
    assert rendered["SD13"]["expected_window"]["date_from"] == "2026-06-12"
    assert rendered["SD14"]["expected_window"] == {"date_from": "2099-01-01", "date_to": "2099-01-07"}


@pytest.mark.parametrize("url", ["http://user:password@localhost", "http://localhost?api_key=x",
                                 "http://localhost#token", "file:///tmp/test", "localhost:8501"])
def test_base_url_rejects_credential_and_non_http_inputs(url):
    with pytest.raises(ValueError):
        runner.validate_base_url(url)


def test_redaction_preserves_evidence_and_usage_without_credentials(tmp_path):
    payload = {"API_KEY": "hidden-one", "DEEPSEEK_API_KEY": "hidden-two", "input_tokens": 32,
               "url": "https://user:hidden-three@example.org/paper?id=42&api_key=hidden-four",
               "error": 'Authorization: Bearer hidden-five; api_key="hidden-six"',
               "html": "<p>sk-abcdefghijklmnop123456</p>", "access_token": "hidden-seven"}
    path = tmp_path / "safe.json"
    runner.write_json(path, payload)
    text = path.read_text(encoding="utf-8")
    assert "hidden-" not in text and "sk-abcdefghijklmnop" not in text
    data = json.loads(text)
    assert data["input_tokens"] == 32
    assert "example.org/paper?id=42" in data["url"]


def test_sse_multiline_comments_unicode_and_truncation():
    lines = [b": heartbeat\n", b"event: result\n", b'data: {"run_id":\n', b'data: "one"}\n', b"\n",
             "event: chunk\ndata: {\"content\":\"中文\"}\n\n".encode("utf-8")]
    framed = b"".join(lines).splitlines(keepends=True)
    assert list(runner.sse_events(framed)) == [("result", {"run_id": "one"}), ("chunk", {"content": "中文"})]
    with pytest.raises(ValueError, match="Truncated"):
        list(runner.sse_events([b"event: done\n", b"data: {}\n"]))


def test_simple_chat_and_natural_task_do_not_require_roles():
    case = {**cases()["SD10"], "transport": "json"}
    trace = trace_for(case)
    result = {"run_id": "turn1", "session_id": "session1", "answer": "A bounded plan",
              "selected_skill_id": "general_chat", "artifacts": {}}
    checks, _ = runner.mechanical_checks(case, result, trace, [], {}, None, "test_personal", "session1")
    assert runner.checks_pass(checks)
    simple = {**cases()["SD01"], "transport": "json"}
    delegated = trace_for(simple, delegates=[{"task_id": "task", "context_manifest_id": "ctx", "stop_reason": "success"}])
    checks, _ = runner.mechanical_checks(simple, result, delegated, [], {}, None, "test_ths", "session1")
    assert "simple_chat_no_subagents" in failed_names(checks)


def test_failed_terminal_trace_is_not_a_pass():
    case = cases()["SD02"]
    result = {"run_id": "turn1", "session_id": "session1", "answer": "Something",
              "selected_skill_id": "general_chat"}
    checks, _ = runner.mechanical_checks(case, result, trace_for(case, status="failed"), [], {}, None,
                                         "test_personal", "session1")
    assert "run_completed_not_failed" in failed_names(checks)


def test_empty_sse_cannot_silently_skip_protocol_checks():
    case = cases()["SD01"]
    result = {"run_id": "turn1", "session_id": "session1", "answer": "one", "selected_skill_id": "general_chat"}
    checks, _ = runner.mechanical_checks(case, result, trace_for(case), [], {}, None, "test_ths", "session1")
    assert "sse_complete_protocol" in failed_names(checks)


def test_new_report_fields_and_chinese_topics_do_not_assert_answer_quality():
    case = cases()["SD03"]
    report = report_for(case)
    inspection = runner.inspect_artifact(report, {})
    checks = runner.report_checks(case, report, inspection, "test_ths")
    assert runner.checks_pass(checks), failed_names(checks)
    assert inspection["analysis_status"] == "partial"
    assert inspection["windows"][0]["window"] == case["expected_window"]


def test_legacy_artifact_optional_fields_are_skipped_not_passed():
    case = cases()["SD03"]
    report = report_for(case, new_fields=False)
    checks = runner.report_checks(case, report, runner.inspect_artifact(report, {}), "test_ths")
    assert runner.checks_pass(checks)
    assert next(item for item in checks if item["check"] == "optional_report_request_structured")["status"] == "skip"


@pytest.mark.parametrize("mutation,expected", [
    (lambda r: r["report_request"]["period"].update(start="2026-08-01"), "report_window_matches_0"),
    (lambda r: r["quality_diagnostics"].update(second_pass_removed_count=4), "second_pass_cleaning_counts_reconcile"),
    (lambda r: r["report_request"].update(wiki_fact_refs=["personal_goal"]), "request_wiki_refs_scoped"),
    (lambda r: r["discovery_plan"].update(source_pool=["other"]), "plan_uses_request_source_pool"),
    (lambda r: r["structured_events"][0].update(published_at="2026-08-01"), "selected_0_published_in_window"),
])
def test_report_contract_regressions_are_detected(mutation, expected):
    case = cases()["SD03"]
    report = report_for(case)
    mutation(report)
    checks = runner.report_checks(case, report, runner.inspect_artifact(report, {}), "test_ths")
    assert expected in failed_names(checks)


def test_planning_model_call_is_separately_audited():
    case = cases()["SD03"]
    report = report_for(case)
    report["discovery_plan"]["diagnostics"].update(mode="model", model_calls=1)
    trace = {"steps": [{"step_type": "model_call", "metadata": {"contract_id": "report_discovery_plan.v1"}}]}
    checks = runner.report_checks(case, report, runner.inspect_artifact(report, trace), "test_ths")
    assert runner.checks_pass(checks)
    checks = runner.report_checks(case, report, runner.inspect_artifact(report, {}), "test_ths")
    assert "planning_model_calls_audited" in failed_names(checks)


def test_same_pool_allows_different_topics_sources_and_selections():
    suite = runner.load_suite()
    report = report_for(cases()["SD03"])
    left = {"query": "/report 生成一份报告。", "inspection": runner.inspect_artifact(report, {})}
    right = deepcopy(left)
    right["inspection"]["selected_event_ids"] = ["other_event"]
    right["inspection"]["observed_source_ids"] = ["other_source"]
    checks = runner.compare_pairs(suite, {"SD03": left, "SD04": right})[0]["checks"]
    assert runner.checks_pass(checks)
    right["inspection"]["source_registry_checksums"] = ["c" * 64]
    checks = runner.compare_pairs(suite, {"SD03": left, "SD04": right})[0]["checks"]
    assert "shared_source_registry_version" in failed_names(checks)


class FakeResponse(io.BytesIO):
    def __init__(self, body, status=200, content_type="application/json"):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": content_type}


def test_actual_http_wrapper_and_sse_save_no_request_bodies_or_chunk_secrets(tmp_path):
    api = runner.Api("http://127.0.0.1:9999", tmp_path)
    posted = []

    def open_request(req, timeout):
        posted.append((req.get_method(), req.full_url, json.loads(req.data), timeout))
        if req.full_url.endswith("/stream"):
            frames = [("start", {}), ("result", {"run_id": "one"}),
                      ("chunk", {"content": "api_key="}), ("chunk", {"content": "hidden-value"}), ("done", {})]
            body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in frames).encode()
            return FakeResponse(body, content_type="text/event-stream")
        return FakeResponse(b'{"session_id":"session1"}', status=201)

    api.opener.open = open_request
    assert api.ok("/companies/test/workbench/sessions", {"title": "hidden-title"})["session_id"] == "session1"
    result, _ = api.stream("/companies/test/workbench/sessions/session1/messages/stream", "hidden-query", tmp_path / "sse.json")
    assert result["answer"] == "api_key=hidden-value"
    assert "hidden-" not in (tmp_path / "sse.json").read_text()
    assert "hidden-" not in (tmp_path / "api_requests.json").read_text()
    assert posted[-1][0] == "POST" and posted[-1][2] == {"message": "hidden-query"}


def test_setup_uses_only_api_and_refuses_existing_wiki(tmp_path):
    suite = runner.load_suite()
    api = runner.Api("http://127.0.0.1:9999", tmp_path)
    created, posts = {}, []

    def call(path, payload=None, **kwargs):
        if path == "/companies" and payload is None:
            return 200, {"company_ids": list(created)}
        if path == "/companies":
            created[payload["company_id"]] = payload
            posts.append((path, payload))
            return 201, payload
        if path.endswith("/wiki/pages"):
            posts.append((path, payload))
            assert payload["create"] is True
            assert "policy_relevance:" not in payload["body"]
            return 200, {}
        persona = "personal" if "fresh_personal" in path else "ths"
        return 200, {"facts": [{"fact_id": fact["id"], "value": fact["value"]}
                               for fact in suite["personas"][persona]["facts"]]}

    api.call = call
    assert runner.setup(api, suite, "fresh") == {"ths": "fresh_ths", "personal": "fresh_personal"}
    assert len(posts) == 4
    with pytest.raises(ValueError, match="Prefix already exists"):
        runner.setup(api, suite, "fresh")
    assert len(posts) == 4


def test_default_cli_is_offline_and_does_not_create_output(tmp_path, capsys):
    output = tmp_path / "offline"
    with patch.object(runner.Api, "__init__", side_effect=AssertionError("network client instantiated")):
        assert runner.main(["--cases", "SD05,SD06", "--output", str(output), "--prefix", "offline_test"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["effective_cases"] == ["SD03", "SD04", "SD05", "SD06"]
    assert plan["mode"] == "offline_plan"
    assert not output.exists()


def test_nonempty_output_is_never_overwritten(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("user data")
    with patch.object(runner.Api, "__init__", side_effect=AssertionError("network client instantiated")):
        with pytest.raises(SystemExit):
            runner.main(["--live", "--output", str(tmp_path)])
    assert marker.read_text() == "user data"
