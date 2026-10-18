from copy import deepcopy

import pytest

from policy_impact.domains.finance.research_audit import audit, summarize, validate_assessments
from policy_impact.domains.finance.answer_verifier import evidence_footer
from policy_impact.domains.finance.answer_verifier import price_basis_issues
from policy_impact.domains.finance.metrics import compute_market_metrics
from test_finance_workflow import sample, configured, run, GoodModel


def example():
    metrics = compute_market_metrics(sample())
    docs = [{"document_id": f"doc_{day}", "published_at": day, "url": "https://example.com/" + day}
            for day in ("2026-09-15", "2026-09-17", "2026-09-22", "2026-09-23")]
    request = {"period": sample()["period"], "question": "涨跌原因是什么"}
    return audit(request=request, metrics=metrics, documents=docs)


def assessment():
    return {"symbol": "sh000001", "verdict": "unidentified", "hypothesis": "原因未识别",
            "evidence_refs": ["market_change_pct:sh000001"], "counterevidence": "未取得反向机制证据",
            "missing_evidence": ["利率"], "falsification_test": "本期利率与估值的方向是否与利率假说相反"}


def test_audit_never_treats_date_only_publication_as_confirmed_catalyst():
    rows = example()["assets"][0]["timeline"]
    assert [r["relation"] for r in rows] == ["before_observation_window", "within_observation_window",
                                             "same_day_order_unknown", "after_price_cutoff"]


def test_cutoff_is_per_asset_not_request_end():
    metrics = compute_market_metrics(sample())
    metrics.append({**metrics[0], "symbol": "other", "end_date": "2026-09-21"})
    result = audit(request={"period": sample()["period"]}, metrics=metrics,
                   documents=[{"document_id": "d", "published_at": "2026-09-22", "url": "https://example.com"}])
    assert result["assets"][0]["timeline"][0]["relation"] == "same_day_order_unknown"
    assert result["assets"][1]["timeline"][0]["relation"] == "after_price_cutoff"


def test_global_selection_does_not_promote_unrelated_official_speeches():
    from policy_impact.domains.finance.research_audit import select_documents
    request = {"symbols": ["ixic", "xauusd"], "period": sample()["period"], "question": "分析金银美股原因"}
    docs = [{"document_id": title, "title": title, "published_at": "2026-09-21", "source_id": "fed_speeches"}
            for title in ("Federal Reserve issues FOMC statement", "Speech on stress testing", "Discount window modernization")]
    result = select_documents(request, compute_market_metrics(sample()), docs)
    assert result["selected_ids"] == ["Federal Reserve issues FOMC statement"]
    assert len(result["excluded"]) == 2
    request["question"] = "银行压力测试影响"
    assert len(select_documents(request, compute_market_metrics(sample()), docs)["selected_ids"]) == 3


def test_coverage_duplicates_unknown_and_post_cutoff_refs_are_blocked():
    result = example()
    row = assessment()
    evidence = {row["evidence_refs"][0]: {}, "doc_2026-09-23": {"text": "retroactive source"}}
    validate_assessments({"asset_assessments": [row]}, result, evidence)
    for rows in ([], [row, row], [{**row, "symbol": "other"}], [{**row, "evidence_refs": ["missing"]}],
                 [{**row, "evidence_refs": [*row["evidence_refs"], "doc_2026-09-23"]}]):
        with pytest.raises(ValueError):
            validate_assessments({"asset_assessments": rows}, result, evidence)


def test_quality_is_not_overridden_by_model_supported():
    quality = summarize({"asset_assessments": [assessment()], "supported": True}, example())
    assert quality["answer_status"] == "partial"
    assert quality["causal_identification"] == "not_established"
    assert "single_source" in quality["price_verification"]


def test_colloquial_why_cannot_bypass_coverage_and_generated_hypotheses_always_checked():
    result = audit(request={"period": sample()["period"], "question": "黄金怎么会跌成这样"},
                   metrics=compute_market_metrics(sample()), documents=[])
    assert result["causal_requested"]
    with pytest.raises(ValueError):
        validate_assessments({"asset_assessments": []}, result, {})
    result["causal_requested"] = False
    with pytest.raises(ValueError):
        validate_assessments({"asset_assessments": [{**assessment(), "symbol": "unknown"}]}, result, {})


def test_period_later_review_allowed_but_never_certified_as_contemporaneous():
    result = example()
    row = {**assessment(), "evidence_refs": ["market_change_pct:sh000001", "doc_2026-09-23"],
           "document_uses": [{"document_id": "doc_2026-09-23", "usage": "retrospective_review"}]}
    evidence = {"market_change_pct:sh000001": {}, "doc_2026-09-23": {"text": "historical review"}}
    validate_assessments({"asset_assessments": [row]}, result, evidence)
    for usage in ("historical_context", "contemporaneous_catalyst"):
        row["document_uses"][0]["usage"] = usage
        with pytest.raises(ValueError):
            validate_assessments({"asset_assessments": [row]}, result, evidence)
    assert all(item["timestamp_order"] == "unknown" for item in result["assets"][0]["timeline"])


def test_hypothesis_requires_same_mechanism_and_historical_window():
    row = {**assessment(), "verdict": "hypothesis_only", "mechanism": "growth_earnings",
           "historical_test": {"mechanism": "growth_earnings", "window": {"start": "2026-09-15", "end": "2026-09-22"},
                               "variables": ["盈利"], "weakening_condition": "本窗口盈利下降而指数上涨将削弱该解释"}}
    evidence = {"market_change_pct:sh000001": {}}
    validate_assessments({"asset_assessments": [row]}, example(), evidence)
    for patch in ({"mechanism": "rates_discounting"}, {"window": {"start": "2026-10-01", "end": "2026-10-02"}}, {"weakening_condition": "TBD"}):
        invalid = {**row, "historical_test": {**row["historical_test"], **patch}}
        with pytest.raises(ValueError):
            validate_assessments({"asset_assessments": [invalid]}, example(), evidence)


def test_audit_preserves_actual_opposite_days_without_claiming_causality():
    metric = {**compute_market_metrics(sample())[0], "change_pct": -5,
              "daily_changes": [{"date": "2026-09-17", "direction": "up", "change_pct": 2},
                                {"date": "2026-09-18", "direction": "down", "change_pct": -5}]}
    result = audit(request={"period": sample()["period"]}, metrics=[metric], documents=[])
    assert result["assets"][0]["counter_direction_observations"][0]["date"] == "2026-09-17"
    assert "不能单独" in result["assets"][0]["path_notice"]


def test_footer_delivers_saved_market_sources_and_only_cited_documents():
    report = {"metrics": [{"name": "指数", "source_url": "https://example.com/prices"}],
              "analysis": {"sections": [{"evidence_refs": ["used"]}]},
              "source_inventory": [{"document_id": "used", "title": "政策原文", "url": "https://example.com/policy"},
                                   {"document_id": "unused", "title": "无关材料", "url": "https://example.com/unrelated"}],
              "research_quality": summarize({}, example())}
    answer = evidence_footer([report], "本次观察")
    assert "https://example.com/prices" in answer and "https://example.com/policy" in answer
    assert "unrelated" not in answer and "原因未证实" in answer
    report["metrics"][0]["source_url"] = "javascript:alert(1)"
    assert "javascript" not in evidence_footer([report], "回答")
    report["analysis"]["sections"] = []
    report["analysis"]["asset_assessments"] = [{"evidence_refs": ["used"]}]
    assert "https://example.com/policy" in evidence_footer([report], "只在资产假设中引用")


def test_audit_tool_permission_cannot_be_bypassed(configured):
    with pytest.raises(PermissionError):
        run(allowed_tools=("finance.collect", "finance.metrics", "finance.publish"))


def test_ambiguous_extrema_and_exchange_certification_are_not_price_validation():
    reports = [{"metrics": compute_market_metrics(sample())}]
    assert price_basis_issues("|区间最高/最低|最大回撤|\n106/105\n交易所完整日线\n三者方向相反", reports)
    assert not price_basis_issues("|收盘价最高/最低|窗口内日末价最大回撤|\n106/105\n供应商指数日线，不等于交易所完整日线认证", reports)
    assert not price_basis_issues("截至2026-09-22，未取得盘中最高价。", reports)
    assert price_basis_issues("收盘数据来自供应商。期间最高100，最低90。", reports)
    assert price_basis_issues("|最高/最低|\n|---|\n|100/90|", reports)


def test_research_record_is_saved_readable_and_rendered(configured):
    from policy_impact.domains.finance.report_reader import read
    from policy_impact.domains.finance.render import render_html
    report = run()["report"]
    result = read("fin", report["report_id"], section="research_audit")
    assert result["items"][0]["symbol"] == "sh000001"
    assert "归因核查" in render_html(report)
    assert "未独立交叉核验" in render_html(report)
    prices = read("fin", report["report_id"], section="market_series", symbol="sh000001")
    assert "change_pct" not in prices["items"][0]
    assert "cumulative_change_from_baseline_pct" in prices["items"][0]
    assert "change_pct" in prices["daily_changes"][0]
    report["research_audit"]["assets"][0]["timeline"].append({
        "document_id": "later_review", "url": "https://example.com/later", "published_at": "2026-09-23",
        "relation": "after_price_cutoff"})
    html = render_html(report)
    assert "可用于事后回顾" in html
    assert "不能解释此前涨跌" not in html


def test_publication_rechecks_old_saved_prose_without_new_acquisition(configured, monkeypatch):
    from policy_impact.domains.finance.store import FinanceStore
    from policy_impact.domains.finance.workflow import publish_saved_analysis
    from policy_impact.domains.finance.quality import FinanceQualityError
    report = run()["report"]
    report["analysis"]["overview"] = "美股按交易所完整日线。"
    monkeypatch.setattr(FinanceStore, "get_report", lambda *args: deepcopy(report))
    monkeypatch.setattr("policy_impact.domains.finance.workflow.require_market_coverage", lambda *args: None)
    with pytest.raises(FinanceQualityError, match="现行发布口径"):
        publish_saved_analysis("fin", report["report_id"], parent_run_id="test", allowed_tools=("finance.publish",))


def test_fresh_analysis_delivery_uses_validated_projection_not_second_llm_prose(configured, monkeypatch):
    from types import SimpleNamespace
    from policy_impact.domains.finance.render import render_chat_analysis
    from policy_impact.domains.finance.answer_verifier import FinanceAnswerVerifier
    report = run()["report"]
    metric = report["metrics"][0]
    metric["period_first_close"] = 99999
    text = render_chat_analysis(report)
    assert f"{metric['start_close']:,.2f}" in text
    assert "99,999" not in text
    gateway = SimpleNamespace(final_guard=lambda *args: None, analysis_result={"report": report})
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: (_ for _ in ()).throw(AssertionError("no duplicate rewrite")))
    monkeypatch.setattr(verifier, "_load_evidence", lambda: [{"report_id": report["report_id"]}])
    output = {"answer": "wrong freely rewritten numeric answer"}
    assert verifier.check("本月走势及原因", output) is None
    assert "wrong freely" not in output["answer"]
    assert output["delivery_mode"] == "validated_finance_projection"
    assert verifier.results[0]["stage"] == "validated_analysis_projection"
    report["analysis_status"] = "error"
    with pytest.raises(ValueError):
        render_chat_analysis(report)
