from types import SimpleNamespace

from policy_impact.domains.finance.answer_verifier import FinanceAnswerVerifier
from policy_impact.domains.finance.answer_verifier import daily_value_issues
from policy_impact.domains.finance.metric_claims import metric_claim_issues
from policy_impact.domains.finance.metric_claims import research_claim_issues
from policy_impact.domains.finance.answer_verifier import source_evidence, source_availability_issues


def test_review_receives_saved_cited_body_and_explicit_excerpt_boundary():
    def doc(key, text):
        return dict(document_id=key, text=text, title="FOMC", kind="official", published_at="2026-09-16", url="https://example.com/"+key)
    rows = source_evidence([doc("vote", "For release at 2:00 p.m. EDT. Approved by a 12-0 vote."),
                            doc("long", "x"*7000), doc("unread", "Saved document")], {"vote", "long"})
    assert "12-0" in rows[0]["text_excerpt"] and not rows[0]["excerpt_truncated"]
    assert len(rows[1]["text_excerpt"]) == 6000 and rows[1]["saved_body_characters"] == 7000
    assert rows[2]["text_excerpt"] == "" and rows[2]["saved_body_characters"] > 0
    reports = [{"source_inventory": rows}]
    assert source_availability_issues("报告仅保存标题与引用片段，未保存声明原文", reports)
    assert not source_availability_issues("正文已保存，尚未展开，完整性未核实", reports)


def test_price_path_cannot_prove_driver_difference_or_invert_saved_mechanism():
    reports = [{"analysis": {"asset_assessments": [{"symbol": "ixic", "mechanism": "rates_discounting"}]}}]
    for answer in ("报告说加息导致纳指上行仍是待验证假设。", "文章只有发布日期。",
                   "反向波动也表明简单的单因素解释不足。", "白银跌幅小于黄金，提示两者驱动不完全相同。"):
        assert research_claim_issues(answer, reports)
    assert not research_claim_issues("加息可能压制估值，但纳指上涨，未确认该机制。", reports)
    assert not research_claim_issues("不能说加息导致纳指上行，也不是文章只有发布日期。", reports)
    assert not research_claim_issues("不能从反向波动推出驱动不同；结构化字段仅日期，原文时分尚未抽取核验。", reports)


def test_asset_baseline_and_path_rank_claims_are_not_left_to_model_judgment():
    reports = [{"metrics": [
        {"symbol": "xauusd", "name": "国际现货黄金", "start_date": "2026-08-24", "end_date": "2026-09-22",
         "up_days": 8, "down_days": 18, "max_drawdown_pct": 8.23},
        {"symbol": "xagusd", "name": "国际现货白银", "start_date": "2026-08-24", "end_date": "2026-09-22",
         "up_days": 15, "down_days": 11, "max_drawdown_pct": 8.70}]}]
    assert metric_claim_issues("金银基准即8/25当日参考价", reports)
    assert metric_claim_issues("|资产|基准日|期末|\n|黄金|2026-08-25|2026-09-22|", reports)
    assert len(metric_claim_issues("黄金单边偏弱、回撤最深", reports)) == 2
    assert metric_claim_issues("黄金是高位回落：8/26开始，之后一路走低", reports)
    assert metric_claim_issues("黄金8/26见顶后一路走弱", reports)
    assert not metric_claim_issues("请求从8/25观察黄金。黄金基准为8/24，震荡走低；白银回撤最大。", reports)
    assert not metric_claim_issues("黄金不是单边下跌，也不是回撤最深。白银基准8/24。", reports)
    assert not metric_claim_issues("黄金震荡走低，白银回撤最深。", reports)
    assert not metric_claim_issues("金银期间有单日反弹，削弱单边下跌叙事。", reports)
    assert not metric_claim_issues("黄金非单边下行。白银有反向日，不支持一路下跌。", reports)


def test_late_article_binary_denial_requires_repair(monkeypatch):
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids={"saved"}, observations=[])
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(verifier, "_load_evidence", lambda: [{"report_id": "saved"}])
    assert "事后回顾" in verifier.check("晚于行情截止的文章能解释此前涨跌吗", {"answer": "## 期后文章\n\n**不能。** 仅能回顾此前事件。"})
    assert verifier.reviews == 0


def daily_report():
    return [{"report_id": "saved", "metrics": [{"symbol": "ixic", "name": "纳斯达克综合指数",
        "daily_changes": [{"date": "2026-09-21", "change_pct": 2.26053}]}]}]


def test_cumulative_return_cannot_be_claimed_as_daily_change():
    assert daily_value_issues("9/21 单日 +4.40%", daily_report())
    assert daily_value_issues("2026-09-21 单日上涨4.40%", daily_report())
    assert not daily_value_issues("9-21 单日 +2.26%", daily_report())
    assert not daily_value_issues("9月21日当日上涨2.3%", daily_report())
    assert not daily_value_issues("观察期累计上涨4.40%", daily_report())
    assert daily_value_issues("纳指日线路径：连续4日上涨（9/21 +4.40%）", daily_report())
    assert not daily_value_issues("纳指连续上涨（9/21 **+2.26%**）", daily_report())
    assert not daily_value_issues("相对期前基准累计：9/21 +4.40%", daily_report())


def test_deterministic_numeric_error_cannot_be_overridden_by_model(monkeypatch):
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids={"saved"}, observations=[])
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(verifier, "_load_evidence", daily_report)
    assert "单日" in verifier.check("走势如何", {"answer": "9/21 单日 +4.40%"})
    assert verifier.reviews == 0
    assert verifier.results[-1]["stage"] == "daily_numeric_check"


def test_review_evidence_retains_daily_changes(monkeypatch):
    import policy_impact.domains.finance.answer_verifier as module
    from test_finance_workflow import sample

    snapshot = sample()
    report = {"company_id": "test", "request": {"period": snapshot["period"]}, "snapshot": snapshot, "documents": []}
    monkeypatch.setattr(module, "FinanceStore", lambda _: SimpleNamespace(get_report=lambda _: report))
    verifier = FinanceAnswerVerifier(SimpleNamespace(company_id="test", report_ids={"saved"}), None)
    metric = verifier._load_evidence()[0]["metrics"][0]
    assert metric["daily_changes"][1]["change_pct"] == 0.952381
    assert "points" not in metric and "session_evidence" not in metric


def test_prerequisite_does_not_spend_review_budget():
    gateway = SimpleNamespace(final_guard=lambda q, o: "read sources first")
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: (_ for _ in ()).throw(AssertionError()))
    assert verifier.check("缺什么", {"answer": "test"}) == "read sources first"
    assert verifier.reviews == 0


def test_review_repair_and_bounded_budget(monkeypatch):
    calls = []
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids=set(), observations=[])

    def model(**kwargs):
        calls.append(kwargs)
        supported = len(calls) == 2
        return SimpleNamespace(payload={"output": {"supported": supported,
            "issues": [] if supported else ["上证跌幅更小，不是创业板最好"]}})

    verifier = FinanceAnswerVerifier(gateway, model)
    monkeypatch.setattr(verifier, "_load_evidence", lambda: [{"report_id": "fixture"}])
    assert "不是创业板最好" in verifier.check("排名", {"answer": "创业板最好"})
    assert verifier.check("排名", {"answer": "上证最好"}) is None
    assert "预算耗尽" in verifier.check("排名", {"answer": "再试一次"})
    assert len(calls) == 2
    assert calls[0]["schema_name"] == "finance_followup_verification.v1"


def test_followup_review_receives_bounded_conversation_context(monkeypatch):
    import json
    calls = []
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids=set(), observations=[])
    def model(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(payload={"output": {"supported": True, "issues": []}})
    history = [{"role": "assistant", "content": "因果未识别，权重只是候选假设"}]
    verifier = FinanceAnswerVerifier(gateway, model, conversation_context=history)
    monkeypatch.setattr(verifier, "_load_evidence", lambda: [{"report_id": "fixture"}])
    assert verifier.check("刚才的解释", {"answer": "尚不能归因"}) is None
    assert json.loads(calls[0]["messages"][1]["content"])["conversation_context"] == history


def test_malformed_review_fails_closed(monkeypatch):
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids=set(), observations=[])
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: SimpleNamespace(payload={"supported": True}))
    monkeypatch.setattr(verifier, "_load_evidence", lambda: [{"report_id": "fixture"}])
    # Missing issues uses the schema's default if present; explicit invalid types do not.
    verifier.complete = lambda **kw: SimpleNamespace(payload={"supported": "invalid", "issues": []})
    assert "复核失败" in verifier.check("核验", {"answer": "未知"})
    assert verifier.results[-1]["supported"] is False


def test_empty_authorized_reports_cannot_pass_without_evidence():
    gateway = SimpleNamespace(final_guard=lambda q, o: None, report_ids=set(), observations=[])
    verifier = FinanceAnswerVerifier(gateway, lambda **kw: (_ for _ in ()).throw(AssertionError()))
    assert "证据缺失或无效" in verifier.check("核验", {"answer": "回答"})
    assert verifier.reviews == 0


def test_per_item_verdict_aggregation_requires_full_coverage():
    import pytest
    from policy_impact.domains.finance.verification import normalize_verification
    checks = [{"item_id": name, "reason": "与证据一致", "verdict": "supported"} for name in ("a", "b")]
    assert normalize_verification({"checks": checks}, {"a", "b"}) == {"supported": True, "issues": []}
    checks[1]["verdict"] = "contradicted"
    checks[1]["reason"] = "排名与代码计算相反"
    assert not normalize_verification({"checks": checks}, {"a", "b"})["supported"]
    with pytest.raises(ValueError):
        normalize_verification({"checks": checks[:1]}, {"a", "b"})
    with pytest.raises(ValueError):
        normalize_verification({"checks": [checks[0], checks[0]]}, {"a", "b"})
