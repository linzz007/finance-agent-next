"""Independent acceptance checks. No live model, profile writes, or database writes.

Run the default suite offline. Set FINANCE_REVIEW_LIVE=1 to include read-only
HTTP checks. FINANCE_REVIEW_ACCEPTANCE selects a filename label (default:
financial-first) so the same checks can review a new real-model acceptance run.
Assertions describe required behavior; known defects are intentionally not xfailed.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from policy_impact.domains.finance import context, workflow
from policy_impact.domains.finance.metrics import compute_market_metrics
from policy_impact.domains.finance.profile import default_profile
from policy_impact.domains.finance.request import parse_request
from policy_impact.harness.tool_gateway import TOOL_REGISTRY


TODAY = date(2026, 9, 22)
PERIOD = {"start": "2026-09-16", "end": "2026-09-22"}
METRIC_ID = "market_change_pct:sh000001"
TOOLS = ("finance.collect", "finance.metrics", "finance.research_audit", "finance.publish")
PROFILE = {**default_profile(), "version": 1, "symbols": ["sh000001"], "quality_policy": "allow_partial"}
LIVE = pytest.mark.skipif(os.environ.get("FINANCE_REVIEW_LIVE") != "1",
                          reason="Explicit opt-in required for read-only localhost API checks")


def analysis():
    return {"overview": "指数变动仅作学习观察，持仓未知。", "sections": [
        {"title": "区间观察", "content": "指数区间上涨，不能据此推断原因。",
         "evidence_refs": [METRIC_ID]}], "watch_next": [], "limitations": []}


def snapshot():
    return {"snapshot_id": "independent-in-memory-fixture",
            "collected_at": "2026-09-22T08:00:00Z", "period": dict(PERIOD),
            "series": [{"symbol": "sh000001", "name": "上证指数", "provider": "TEST ONLY",
                        "source_url": "https://example.invalid/not-live", "unit": "点",
                        "retrieved_at": "2026-09-22T08:00:00Z",
                        "bars": [{"date": day, "open": n, "high": n, "low": n, "close": n}
                                 for day, n in [("2026-09-15", 100), ("2026-09-21", 105),
                                                ("2026-09-22", 106)]]}],
            "documents": [], "warnings": [],
            "source_statuses": [{"source_id": "fixture", "status": "ok", "count": 3}]}


@pytest.mark.parametrize("query", ["/report 只看日经指数最近7天", "/report 最近7天比特币",
                                  "/report 最近7天沪深300与恒生指数"])
def test_review_unsupported_index_is_not_silently_substituted(query):
    with pytest.raises(ValueError):
        parse_request(query, PROFILE, today=TODAY)


def test_review_single_historical_date_is_exact_day():
    result = parse_request("/report 2026-08-03上证指数日报", PROFILE, today=TODAY)
    assert result["period"] == {"start": "2026-08-03", "end": "2026-08-03"}


@pytest.mark.parametrize("query", ["/report 2027-01-01上证指数日报", "/report 2026-02-30指数日报",
                                  "/report 最近0天", "/report 最近400天",
                                  "/report 2026-09-22至2026-09-01"])
def test_review_invalid_or_future_period_is_rejected(query):
    with pytest.raises(ValueError):
        parse_request(query, PROFILE, today=TODAY)


@pytest.mark.parametrize("query", ["/report 最近10000天上证指数", "/report 明天的指数日报",
                                  "/report 最近1.5天上证指数"])
def test_review_unrepresentable_period_does_not_fall_back_to_profile(query):
    with pytest.raises(ValueError):
        parse_request(query, PROFILE, today=TODAY)


def test_review_chinese_historical_date_is_respected_or_explicitly_rejected():
    try:
        result = parse_request("/report 2026年8月3日上证指数日报", PROFILE, today=TODAY)
    except ValueError:
        return
    assert result["period"] == {"start": "2026-08-03", "end": "2026-08-03"}


@pytest.mark.parametrize("field,text", [
    ("overview", "上证指数已经收于9999点。"),
    ("content", "沪深300现价9999.99。"),
    ("title", "区间收益999%"),
    ("limitations", "预计可获得999%收益。"),
    ("overview", "收益上涨百分之九十九。"),
])
def test_review_ungrounded_financial_numbers_cannot_bypass_validation(field, text):
    value = analysis()
    if field in {"title", "content"}:
        value["sections"][0][field] = text
    elif field == "limitations":
        value[field] = [text]
    else:
        value[field] = text
    with pytest.raises(ValueError):
        workflow.validate_analysis(value, {METRIC_ID})


def test_review_numeric_dates_and_index_names_remain_usable():
    value = analysis()
    value["overview"] = "2026年8月发布的沪深300相关材料需区分发布日与统计期。"
    actual = workflow.validate_analysis(value, {METRIC_ID})
    for key in ("overview", "watch_next", "limitations"):
        assert actual[key] == value[key]
    assert len(actual["sections"]) == len(value["sections"])
    for expected, section in zip(value["sections"], actual["sections"]):
        assert {key: section[key] for key in expected} == expected
        assert section["evidence_quotes"] == []


@pytest.mark.parametrize("prose", [
    "统计期为1—8月，不能当作九月当期数据。",
    "发布时间为9月15—16日，与统计期不同。",
    "报告期2026-09-01至09-22，指数仅表示区间变化。",
])
def test_review_statistical_periods_are_not_rejected_as_financial_values(prose):
    value = analysis()
    value["overview"] = prose
    assert workflow.validate_analysis(value, {METRIC_ID})["overview"] == prose


def test_review_unrecognized_evidence_is_rejected():
    with pytest.raises(ValueError):
        workflow.validate_analysis(analysis(), {"unrelated"})


@pytest.fixture
def memory_workflow(monkeypatch):
    state = {"snapshot": snapshot(), "published": [], "model_calls": 0,
             "verification_calls": [], "reviews": [{"supported": True, "issues": []}]}
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile", lambda cid: deepcopy(PROFILE))
    monkeypatch.setattr(workflow, "parse_request", lambda *a, **kw: {
        "period": dict(PERIOD), "symbols": ["sh000001"], "include_documents": False})
    monkeypatch.setattr(workflow, "build_company_context_pack", lambda *a, **kw: {"all_company_facts": []})
    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: deepcopy(state["snapshot"]))
    monkeypatch.setitem(TOOL_REGISTRY, "finance.metrics", lambda snapshot: compute_market_metrics(snapshot))
    from policy_impact.domains.finance.research_audit import audit
    monkeypatch.setitem(TOOL_REGISTRY, "finance.research_audit", audit)

    def publish(**kwargs):
        state["published"].append(deepcopy(kwargs["report"]))
        return {}

    monkeypatch.setitem(TOOL_REGISTRY, "finance.publish", publish)

    class Model:
        def complete(self, **kwargs):
            state["model_calls"] += 1
            if kwargs["schema_name"] == "finance_verification.v1":
                state["verification_calls"].append(kwargs)
                review = state["reviews"][0]
                if len(state["reviews"]) > 1:
                    state["reviews"].pop(0)
                if isinstance(review, Exception):
                    raise review
                return SimpleNamespace(payload={"output": deepcopy(review)})
            return SimpleNamespace(payload={"output": analysis()})

    def run(allowed_tools=TOOLS):
        return workflow.run_finance_report("TEST-NOT-A-LIVE-WORKSPACE", "fixture",
                                           model_adapter=Model(), allowed_tools=allowed_tools)

    return state, run


def test_review_empty_snapshot_is_not_success_or_model_input(memory_workflow):
    state, run = memory_workflow
    state["snapshot"]["series"] = []
    state["snapshot"]["source_statuses"] = [{"source_id": "fixture", "status": "empty", "count": 0}]
    result = run()
    assert result["metadata"]["data_status"] != "done"
    assert result["report"]["analysis"] is None
    assert state["model_calls"] == 0


def test_review_source_failure_remains_partial(memory_workflow):
    state, run = memory_workflow
    state["snapshot"]["source_statuses"].append({"source_id": "failed", "status": "error", "count": 0})
    result = run()
    assert result["metadata"]["data_status"] == "partial"
    assert result["report"]["analysis"] is not None


@pytest.mark.parametrize("review", [
    {"supported": False, "issues": ["证据不支持该结论"]},
    {"supported": True, "issues": ["仍有未解决问题"]},
    {"supported": False, "issues": []},
    RuntimeError("TEST ONLY: verification unavailable"),
])
def test_review_failed_semantic_gate_never_publishes_successful_analysis(memory_workflow, review):
    state, run = memory_workflow
    state["reviews"] = [review]
    result = run()
    assert result["metadata"]["analysis_status"] != "done"
    assert result["metadata"]["data_status"] != "done"
    assert result["report"]["analysis"] is None
    assert state["published"][0]["analysis"] is None
    assert len(state["verification_calls"]) == 2


def test_review_semantic_repair_is_bounded_and_rechecked(memory_workflow):
    state, run = memory_workflow
    state["reviews"] = [{"supported": False, "issues": ["引用不支持观点"]},
                        {"supported": True, "issues": []}]
    result = run()
    assert result["metadata"]["analysis_status"] == "done"
    assert result["report"]["analysis_verification"] == {"supported": True, "issues": []}
    assert len(state["verification_calls"]) == 2
    assert state["model_calls"] == 4


def test_review_semantic_gate_receives_only_each_sections_citations(memory_workflow):
    state, run = memory_workflow
    run()
    prompt = json.loads(state["verification_calls"][0]["messages"][1]["content"])
    for section in prompt["sections"]:
        assert set(section["cited_evidence"]) == set(section["section"]["evidence_refs"])


@pytest.mark.parametrize("quote", [None, "这段是伪造的原文引用，不存在于证据。"])
def test_review_article_claims_require_actual_source_quote(quote):
    ref = "test-only-source"
    value = analysis()
    value["sections"][0]["evidence_refs"] = [ref]
    if quote is not None:
        value["sections"][0]["evidence_quotes"] = [{"evidence_ref": ref, "quote": quote}]
    with pytest.raises(ValueError):
        workflow.validate_analysis(value, {ref}, {ref: {"text": "统计期与发布日期不同，测试原文没有提供数值。"}})


def test_review_verbatim_quote_is_accepted_without_rewriting_numbers():
    ref, quote = "test-only-source", "8月份工业增加值同比增长5.2%"
    value = analysis()
    value["sections"][0].update(evidence_refs=[ref], evidence_quotes=[{"evidence_ref": ref, "quote": quote}])
    result = workflow.validate_analysis(value, {ref}, {ref: {"text": quote + "，仅为离线测试样例。"}})
    assert result["sections"][0]["evidence_quotes"][0]["quote"] == quote


def test_review_tool_denial_blocks_publication(memory_workflow):
    state, run = memory_workflow
    with pytest.raises(PermissionError):
        run(allowed_tools=())
    assert state["published"] == []
    assert state["model_calls"] == 0


def test_review_snapshot_period_mismatch_blocks_publication(memory_workflow):
    state, run = memory_workflow
    state["snapshot"]["period"]["end"] = "2026-09-23"
    with pytest.raises(ValueError):
        run()
    assert state["published"] == []


@pytest.fixture(scope="module")
def first_acceptance():
    label = os.environ.get("FINANCE_REVIEW_ACCEPTANCE", "financial-first")
    assert Path(label).name == label, "Acceptance label must be a simple filename stem"
    path = Path(__file__).resolve().parents[1] / "data/acceptance" / (label + ".json")
    if not path.is_file():
        pytest.skip("Original real-model acceptance artifact is unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def saved_report(first_acceptance):
    return first_acceptance["reports"]["reports"][0]


def test_review_saved_market_percentages_recompute_from_all_recorded_closes(saved_report):
    for metric in saved_report["metrics"]:
        prices = {bar["date"]: bar["close"] for series in saved_report["snapshot"]["series"]
                  if series["symbol"] == metric["symbol"] for bar in series["bars"]}
        baseline = Decimal(str(prices[metric["start_date"]]))
        assert metric["start_close"] == prices[metric["start_date"]]
        assert metric["end_close"] == prices[metric["end_date"]]
        assert metric["start_date"] < saved_report["request"]["period"]["start"]
        for point in metric["points"]:
            assert point["close"] == prices[point["date"]]
            calculated = ((Decimal(str(point["close"])) / baseline - 1) * 100).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP)
            assert abs(Decimal(str(point["change_pct"])) - calculated) <= Decimal("0.000001")
        assert metric["change_pct"] == metric["points"][-1]["change_pct"]


def test_review_saved_daily_statistics_match_independent_pairwise_oracle(saved_report):
    from policy_impact.domains.finance.metrics import rank_market_metrics

    period = saved_report["request"]["period"]
    actual = compute_market_metrics(saved_report["snapshot"])
    expected_ratios = {}
    for metric in actual:
        series = next(s for s in saved_report["snapshot"]["series"] if s["symbol"] == metric["symbol"])
        bars = sorted(series["bars"], key=lambda bar: bar["date"])
        within = [bar for bar in bars if period["start"] <= bar["date"] <= period["end"]]
        before = [bar for bar in bars if bar["date"] < period["start"]]
        prices = [Decimal(str(bar["close"])) for bar in within]
        baseline = Decimal(str(before[-1]["close"]))
        expected_ratios[metric["symbol"]] = prices[-1] / baseline
        # Enumerate every ordered peak/trough pair rather than reuse the implementation's running peak.
        drawdowns = [(Decimal(1) - prices[j] / prices[i], i, j)
                     for j in range(1, len(prices)) for i in range(j) if prices[i] > prices[j]]
        maximum, peak, trough = max(drawdowns, key=lambda row: (row[0], -row[2], -row[1]))
        assert abs(Decimal(str(metric["max_drawdown_pct"])) - maximum * 100) <= Decimal("0.000001")
        assert metric["max_drawdown_peak_date"] == within[peak]["date"]
        assert metric["max_drawdown_trough_date"] == within[trough]["date"]
        previous = [baseline] + prices[:-1]
        directions = ["up" if current > prior else "down" if current < prior else "unchanged"
                      for prior, current in zip(previous, prices)]
        assert [day["direction"] for day in metric["daily_changes"]] == directions
        for direction in ("up", "down", "unchanged"):
            assert metric[direction + "_days"] == directions.count(direction)
        streak = 0
        for direction in reversed(directions):
            if direction != "up":
                break
            streak += 1
        assert metric["latest_consecutive_up_days"] == streak
        assert metric["latest_consecutive_up_start_date"] == (within[-streak]["date"] if streak else None)
        assert metric["point_count"] == len(within)
    ranking = rank_market_metrics(actual)
    assert [row["symbol"] for row in ranking["rankings"]] == sorted(
        expected_ratios, key=lambda symbol: (-expected_ratios[symbol], symbol))


def test_review_missing_interior_bar_cannot_change_fixed_endpoint_return():
    full = snapshot()
    sparse = deepcopy(full)
    sparse["series"][0]["bars"].pop(1)
    actual = compute_market_metrics(full)[0]
    with_gap = compute_market_metrics(sparse)[0]
    for key in ("start_close", "end_close", "start_date", "end_date", "change_pct"):
        assert with_gap[key] == actual[key]


def test_review_saved_report_has_chinese_analysis_not_only_source_inventory(saved_report):
    assert saved_report["analysis_status"] == "done"
    assert saved_report["analysis"] and saved_report["analysis"]["sections"]
    assert any("\u4e00" <= char <= "\u9fff" for char in saved_report["analysis"]["overview"])
    if "analysis_verification" in saved_report:
        assert saved_report["analysis_verification"] == {"supported": True, "issues": []}


def test_review_saved_documents_hashes_and_publication_window(saved_report):
    period = saved_report["request"]["period"]
    for doc in saved_report["documents"]:
        assert period["start"] <= doc["published_at"] <= period["end"]
        assert hashlib.sha256(doc["text"].encode("utf-8")).hexdigest() == doc["content_hash"]


def test_review_saved_followup_does_not_assert_continuous_rise_across_recorded_drop(first_acceptance, saved_report):
    answer = first_acceptance["cases"][2]["response"]["answer"]
    normalized = "".join(answer.split())
    declining = []
    for metric in saved_report["metrics"]:
        points = [p for p in metric["points"] if "2026-09-15" <= p["date"] <= "2026-09-22"]
        for previous, current in zip(points, points[1:]):
            if current["close"] < previous["close"]:
                declining.append((metric["symbol"], current["date"], previous["close"], current["close"]))
    if declining:
        for false_claim in ("9月16日起连续回升", "9-16至9-22连续回升", "此后5个观测交易日方向一致向上",
                            "9月16、18、21、22日连续4个交易日收涨"):
            assert false_claim not in normalized, f"Recorded declines contradict claim: {declining}"


def test_review_saved_followup_does_not_rank_chinext_decline_smallest_when_shanghai_is_smaller(first_acceptance, saved_report):
    metrics = {metric["symbol"]: metric for metric in saved_report["metrics"]}
    shanghai, chinext = metrics["sh000001"]["change_pct"], metrics["sz399006"]["change_pct"]
    answer = "".join(first_acceptance["cases"][2]["response"]["answer"].split())
    if chinext < shanghai < 0:
        assert "但期末区间跌幅反而最小" not in answer, f"Shanghai {shanghai}% declines less than Chinext {chinext}%"


def test_review_saved_followup_cannot_make_interior_rebound_change_fixed_endpoint_return(first_acceptance, saved_report):
    metric = next(m for m in saved_report["metrics"] if m["symbol"] == "sz399006")
    sparse = deepcopy(saved_report["snapshot"])
    for series in sparse["series"]:
        if series["symbol"] == metric["symbol"]:
            series["bars"] = [bar for bar in series["bars"] if bar["date"] != "2026-09-07"]
    recomputed = next(m for m in compute_market_metrics(sparse) if m["symbol"] == metric["symbol"])
    assert metric["start_date"] < "2026-09-07" < metric["end_date"]
    for key in ("start_date", "start_close", "end_date", "end_close", "change_pct"):
        assert recomputed[key] == metric[key]
    answer = "".join(first_acceptance["cases"][2]["response"]["answer"].split())
    assert "拉高了起点，使区间首尾差被压缩" not in answer


def test_review_followup_resolves_cited_macro_source_not_first_three(saved_report):
    ref = "finance_doc_2cf97d1fad701f99ed04"
    rows = context.project_report(saved_report, f"报告宏观分析引用的{ref}是什么来源？请给原文与统计期。")
    expected = next(doc for doc in saved_report["documents"] if doc["document_id"] == ref)
    assert expected["url"] in json.dumps(rows, ensure_ascii=False)


def test_review_followup_exposes_saved_daily_path_or_readable_availability(saved_report):
    rows = context.project_report(saved_report, "报告里创业板指期内逐日路径能判断持续走弱还是某一天跳变吗？")
    meta = next(row for row in rows if row.get("finance_report_id") == saved_report["report_id"])
    assert meta["read_tool"] == "finance.report_read"
    assert meta["available_saved_data"]["market_series"] == {
        metric["symbol"]: len(metric["points"]) for metric in saved_report["metrics"]}
    assert meta["available_saved_data"]["source_documents"] == len(saved_report["documents"])


def test_review_reader_authorization_survives_real_context_budget(saved_report):
    from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context

    message = "刚才报告的宏观结论有什么依据，日线能反映走势吗？"
    rows = context.project_report(saved_report, message)
    compiled = compile_chat_context(message=message, skill={}, history=[], conversation_summary={},
                                    memories=[], facts=[], tool_policy={}, report_evidence=rows,
                                    config=ChatContextConfig(artifact_token_budget=1800))
    authorized = [row for row in compiled["recent_artifacts"]
                  if row.get("finance_report_id") == saved_report["report_id"]]
    assert authorized and authorized[0]["read_tool"] == "finance.report_read"


@pytest.fixture
def memory_reader(monkeypatch, saved_report):
    from policy_impact.domains.finance import report_reader

    class MemoryStore:
        def __init__(self, company_id):
            self.company_id = company_id

        def get_report(self, report_id):
            if self.company_id == saved_report["company_id"] and report_id == saved_report["report_id"]:
                return deepcopy(saved_report)
            return None

    monkeypatch.setattr(report_reader, "FinanceStore", MemoryStore)
    monkeypatch.setitem(TOOL_REGISTRY, "finance.report_read", report_reader.read)
    gateway = report_reader.ReportReadGateway(saved_report["company_id"], {saved_report["report_id"]})
    return gateway


@pytest.mark.parametrize("bad_args", [
    {"report_id": "fin_not_in_this_session"},
    {"company_id": "company_001"},
    {"url": "https://example.invalid/not-authorized"},
    {"path": "../other/reports/report.json"},
])
def test_review_report_reader_rejects_unscoped_arguments(memory_reader, saved_report, bad_args):
    args = {"report_id": saved_report["report_id"], "section": "sources", **bad_args}
    with pytest.raises(PermissionError):
        memory_reader.call("chat_agent_loop", "finance.report_read", **args)


@pytest.mark.parametrize("bad_args", [{"limit": 0}, {"limit": 31}, {"limit": True},
                                     {"offset": -1}, {"offset": 100001}, {"offset": False}])
def test_review_report_reader_rejects_invalid_bounds(memory_reader, saved_report, bad_args):
    with pytest.raises(ValueError):
        memory_reader.call("chat_agent_loop", "finance.report_read",
                           report_id=saved_report["report_id"], **bad_args)


def test_review_report_reader_full_original_source_can_be_reconstructed(memory_reader, saved_report):
    document = saved_report["documents"][0]
    offset, parts = 0, []
    while offset is not None:
        result = memory_reader.call("chat_agent_loop", "finance.report_read",
                                    report_id=saved_report["report_id"], section="source",
                                    document_id=document["document_id"], offset=offset)
        assert result["scope"] == "saved_report_snapshot_not_live_market_data"
        assert result["source"]["published_at"] == document["published_at"]
        parts.append(result["text"])
        offset = result["next_offset"]
    assert "".join(parts) == document["text"]


def test_review_report_reader_daily_pages_are_complete_and_unchanged(memory_reader, saved_report):
    metric = saved_report["metrics"][0]
    offset, points = 0, []
    while offset is not None:
        result = memory_reader.call("chat_agent_loop", "finance.report_read",
                                    report_id=saved_report["report_id"], section="market_series",
                                    symbol=metric["symbol"], offset=offset, limit=7)
        assert result["total"] == len(metric["points"])
        points.extend(result["items"])
        offset = result["next_offset"]
    assert points == [{**{k: v for k, v in p.items() if k != "change_pct"},
                       "cumulative_change_from_baseline_pct": p["change_pct"]} for p in metric["points"]]


def test_review_report_reader_cannot_call_acquisition_or_wrong_stage(memory_reader, saved_report):
    with pytest.raises(PermissionError):
        memory_reader.call("chat_agent_loop", "finance.collect", report_id=saved_report["report_id"])
    with pytest.raises(PermissionError):
        memory_reader.call("unapproved_stage", "finance.report_read", report_id=saved_report["report_id"])


def http_get(path):
    base = os.environ.get("FINANCE_REVIEW_BASE_URL", "http://127.0.0.1:8503")
    try:
        with urlopen(base + path, timeout=20) as response:
            raw = response.read()
            return response.status, raw
    except HTTPError as exc:
        return exc.code, exc.read()


@LIVE
def test_review_live_report_read_matches_original_immutable_snapshot(saved_report):
    code, raw = http_get("/companies/finance_observer/workbench/finance/reports/" + saved_report["report_id"])
    assert code == 200
    actual = json.loads(raw)["report"]
    for key in ("snapshot", "metrics", "analysis", "profile_snapshot", "report_id"):
        assert actual[key] == saved_report[key]


@LIVE
@pytest.mark.parametrize("cid", ["company_001", "agent_self_evolution"])
def test_review_live_three_workspace_report_and_artifact_isolation(cid, saved_report):
    code, raw = http_get(f"/companies/{cid}/workbench/finance/profile")
    assert code == 200 and json.loads(raw)["profile"] is None
    code, raw = http_get(f"/companies/{cid}/workbench/finance/reports/{saved_report['report_id']}")
    assert code in {400, 404}
    query = urlencode({"path": saved_report["artifacts"]["run_artifact"]})
    code, raw = http_get(f"/companies/{cid}/workbench/artifact?{query}")
    assert code == 403
    assert saved_report["snapshot_id"].encode() not in raw


@LIVE
def test_review_live_original_artifacts_are_readable(saved_report):
    for path in saved_report["artifacts"].values():
        code, raw = http_get("/companies/finance_observer/workbench/artifact?" + urlencode({"path": path}))
        assert code == 200 and raw


@LIVE
def test_review_live_trace_contains_real_model_loop_tools_and_artifacts(first_acceptance):
    case = first_acceptance["cases"][0]
    response = case["response"]
    code, raw = http_get(f"/companies/finance_observer/workbench/sessions/{response['session_id']}"
                         f"/runs/{response['run_id']}/trace")
    assert code == 200
    trace = json.loads(raw)
    steps = trace["steps"]
    calls = [call for step in steps for call in step.get("tool_calls", [])]
    assert set(TOOLS) <= {call["tool_name"] for call in calls if call.get("allowed")}
    assert any(step["step_type"] == "model_call" and step.get("metadata", {}).get("usage")
               and not step.get("metadata", {}).get("deterministic_action") for step in steps)
    assert any(step.get("metadata", {}).get("embedded_loop") == "external_impact_report" for step in steps)
    assert any(step["step_type"] == "artifact_write" for step in steps)


@LIVE
def test_review_live_draft_holdings_remain_unknown():
    code, raw = http_get("/companies/finance_observer/workbench/finance/profile")
    assert code == 200
    profile = json.loads(raw)["profile"]
    assert profile["status"] == "draft"
    assert profile["holdings"] is None


def test_review_finance_gate_feedback_survives_anthropic_conversion(monkeypatch):
    from policy_impact.domains.finance.report_reader import ReportReadGateway
    from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig
    from policy_impact.harness.context_manifest import build_context_manifest
    from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter, ModelResponse
    from policy_impact.harness.state import PolicyImpactState
    from policy_impact.runtime.execution_mode import ExecutionMode

    report_id = "independent-memory-only"
    calls = []

    def read_fixture(**arguments):
        calls.append(deepcopy(arguments))
        return {"report_id": report_id, "items": [], "total": 0,
                "scope": "saved_report_snapshot_not_live_market_data"}

    monkeypatch.setitem(TOOL_REGISTRY, "finance.report_read", read_fixture)
    gateway = ReportReadGateway("finance_observer", {report_id})
    question = "还缺什么证据？"

    def read_action(section):
        arguments = {"report_id": report_id, "section": section}
        if section == "market_series":
            arguments["symbol"] = "sh000001"
        return {"type": "tool_call", "tool_name": "finance.report_read", "arguments": arguments}

    rejected = {"type": "final", "output": {"answer": "尚未展开日线。"}}
    accepted = {"type": "final", "output": {"answer": "已检查工具结果，不推断未知数据。"}}
    actions = [read_action("sources"), read_action("metrics"), rejected,
               read_action("market_series"), read_action("research_audit"), accepted]
    converted = []

    class OfflineAdapter:
        def complete(self, messages, schema_name):
            system, wire_messages = AnthropicCompatibleModelAdapter._split_messages(deepcopy(messages))
            index = len(converted)
            converted.append((system, wire_messages))
            if index == 3:
                assert wire_messages[-1]["role"] == "user"
                feedback = json.loads(wire_messages[-1]["content"])
                assert "market_series" in feedback["final_gate"]
                assert "final_gate" not in system
                assert wire_messages[-2]["role"] == "assistant"
                assert json.loads(wire_messages[-2]["content"]) == rejected
            return ModelResponse(response_id=f"offline-review-{index}", payload=actions[index])

    state = PolicyImpactState(company_id="finance_observer")
    manifest = build_context_manifest(state=state, stage_name="chat_agent_loop",
                                      agent_role="main_chat_agent", visible_fields=["company_context_pack"])
    result = AgentLoop(OfflineAdapter(), gateway,
                       final_guard=lambda output: gateway.final_guard(question, output)).run(
        state, AgentLoopConfig(stage_name="chat_agent_loop", agent_role="main_chat_agent",
                               context_manifest=manifest, output_key="general_chat_agent_loop.v1",
                               execution_mode=ExecutionMode.AGENT_LOOP, max_steps=8,
                               max_model_calls=8, max_calls_per_tool=6, timeout_seconds=10,
                               task_prompt=question))
    assert result.stop_reason == "success"
    assert result.final_output == accepted["output"]
    assert len(converted) == 6
    assert [call["section"] for call in calls] == ["sources", "metrics", "market_series", "research_audit"]
    assert all(call["company_id"] == "finance_observer" for call in calls)
    assert result.turns[2].observation["final_gate"]["decision"] == "deny"
    assert len(gateway.observations) == 4


@pytest.mark.parametrize("report", [None, {"company_id": "finance_observer", "snapshot": {}}],
                         ids=["missing-report", "corrupt-snapshot"])
def test_review_answer_verifier_unavailable_snapshot_denies_without_model(monkeypatch, report):
    from policy_impact.domains.finance import answer_verifier
    from policy_impact.domains.finance.report_reader import ReportReadGateway

    requested = []
    model_calls = []

    class ReadOnlyMemoryStore:
        def __init__(self, company_id):
            assert company_id == "finance_observer"

        def get_report(self, report_id):
            requested.append(report_id)
            return deepcopy(report)

    def complete(**arguments):
        model_calls.append(arguments)
        return SimpleNamespace(payload={"checks": [
            {"item_id": item, "reason": "Offline fixture", "verdict": "supported"}
            for item in ("numbers", "sources", "gaps", "causality")]})

    monkeypatch.setattr(answer_verifier, "FinanceStore", ReadOnlyMemoryStore)
    gateway = ReportReadGateway("finance_observer", {"missing-memory-report"})
    verifier = answer_verifier.FinanceAnswerVerifier(gateway, complete)
    issue = verifier.check("解释区间涨跌", {"answer": "这是已核验的结论。"})
    assert issue, "An unavailable snapshot cannot be treated as verified evidence"
    assert requested == ["missing-memory-report"]
    assert model_calls == [], "Deterministic evidence failure must not consume model review budget"
