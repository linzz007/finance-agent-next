import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from policy_impact.company_wiki import loader
from policy_impact.domains.finance import workflow
from policy_impact.domains.finance.context import project_report
from policy_impact.domains.finance.metrics import compute_market_metrics
from policy_impact.domains.finance.profile import default_profile
from policy_impact.domains.finance.render import publish_report, render_html
from policy_impact.domains.finance.store import FinanceStore
from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context
from policy_impact.harness.report_context import read_report_evidence
from policy_impact.harness.tool_gateway import TOOL_REGISTRY


def sample():
    return {"snapshot_id": "fixture", "collected_at": "2026-09-22T08:00:00Z", "period": {"start": "2026-09-16", "end": "2026-09-22"},
            "series": [{"symbol": "sh000001", "name": "上证指数", "provider": "fixture-not-live", "unit": "点",
                        "source_url": "https://example.com/index", "retrieved_at": "2026-09-22T08:00:00Z",
                        "bars": [{"date": day, "open": n, "high": n, "low": n, "close": n} for day, n in
                                 [("2026-09-15", 100), ("2026-09-21", 105), ("2026-09-22", 106)]]}],
            "documents": [], "source_statuses": [{"source_id": "fixture", "status": "ok", "count": 3}], "warnings": []}


class GoodModel:
    def complete(self, **kwargs):
        if kwargs.get("schema_name") == "finance_verification.v1":
            return SimpleNamespace(payload={"output": {"supported": True, "issues": []}})
        return SimpleNamespace(payload={"output": {"overview": "指数呈现上涨，不能单凭共时变化判断原因。", "sections": [
            {"title": "指数观察", "content": "指数区间走高；这不足以证明未来仍然上涨。", "evidence_refs": ["market_change_pct:sh000001"]}],
            "watch_next": ["关注后续宏观发布"], "limitations": ["样例不代表实时行情"]}})


def test_analysis_allows_year_range_without_allowing_a_price():
    payload = GoodModel().complete().payload["output"]
    payload["sections"][0]["content"] = "原文讨论2014至2024年的历史变化，不能当作本日行情原因。"
    workflow.validate_analysis(payload, {"market_change_pct:sh000001"})
    payload["sections"][0]["content"] = "指数上涨至2014点。"
    with pytest.raises(ValueError, match="数值"):
        workflow.validate_analysis(payload, {"market_change_pct:sh000001"})


def test_quote_repair_returns_every_missing_reference_in_one_pass():
    payload = GoodModel().complete().payload["output"]
    payload["sections"][0]["evidence_refs"] = ["doc_a", "doc_b"]
    evidence = {"doc_a": {"text": "The first exact original passage."},
                "doc_b": {"text": "The second exact original passage."}}
    with pytest.raises(ValueError) as failure:
        workflow.validate_analysis(payload, set(evidence), evidence)
    assert "doc_a" in str(failure.value) and "doc_b" in str(failure.value)


def test_only_source_supported_history_duration_is_allowed():
    payload = GoodModel().complete().payload["output"]
    payload["limitations"] = ["仅约30天历史，不能解释长期趋势。"]
    workflow.validate_analysis(payload, {"market_change_pct:sh000001"}, technical_context=["仅约30天历史"])
    with pytest.raises(ValueError, match="数值"):
        workflow.validate_analysis(payload, {"market_change_pct:sh000001"}, technical_context=["仅约7天历史"])


def test_repair_collects_citation_and_numeric_errors_together():
    payload = GoodModel().complete().payload["output"]
    section = payload["sections"][0]
    section.update(evidence_refs=["doc"], content="委员会以12比0投票，利率上调。")
    evidence = {"doc": {"text": "The committee voted unanimously to raise rates."}}
    with pytest.raises(ValueError) as failure:
        workflow.validate_analysis(payload, set(evidence), evidence)
    assert "evidence_quotes" in str(failure.value)
    assert "金融数值" in str(failure.value)
    section.update(content="委员会一致同意上调利率。", evidence_quotes=[
        {"evidence_ref": "doc", "quote": evidence["doc"]["text"]}])
    assert workflow.validate_analysis(payload, set(evidence), evidence)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    root = tmp_path / "fin"
    root.mkdir()
    monkeypatch.setattr(loader, "company_dir", lambda cid: tmp_path / cid)
    monkeypatch.setattr("policy_impact.domains.finance.render.company_dir", lambda cid: tmp_path / cid)
    profile = {**default_profile(), "version": 1, "symbols": ["sh000001"], "quality_policy": "allow_partial"}
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile", lambda cid: profile)
    monkeypatch.setattr(workflow, "parse_request", lambda q, p: {"period": sample()["period"], "symbols": ["sh000001"], "include_documents": False, "question": q})
    monkeypatch.setattr(workflow, "build_company_context_pack", lambda *a, **kw: {"all_company_facts": [{"fact_id": "f1"}]})
    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: sample())
    monkeypatch.setitem(TOOL_REGISTRY, "finance.metrics", lambda snapshot: compute_market_metrics(snapshot))
    from policy_impact.domains.finance.research_audit import audit
    monkeypatch.setitem(TOOL_REGISTRY, "finance.research_audit", audit)
    monkeypatch.setitem(TOOL_REGISTRY, "finance.publish", publish_report)
    return root


def run(**kwargs):
    return workflow.run_finance_report("fin", "最近一周只看指数", model_adapter=kwargs.pop("model", GoodModel()),
        allowed_tools=kwargs.pop("allowed_tools", ("finance.collect", "finance.metrics", "finance.research_audit", "finance.publish")), **kwargs)


def test_workflow_publishes_truthful_units_snapshot_and_stages(configured):
    stages = []
    result = run(on_stage=stages.append)
    assert result["metadata"]["data_status"] == "done"
    assert "+6.00%" in result["answer"]
    html = Path(result["artifacts"]["html_report"]).read_text(encoding="utf-8")
    assert "106.00" in html and "点" in html and "106.00 %" not in html
    assert "market_change_pct:sh000001" in html
    saved = FinanceStore("fin").get_report(result["metadata"]["report_id"])
    assert saved["snapshot"]["series"][0]["bars"][0]["close"] == 100
    assert saved["evidence_ref_schema"] == "finance.evidence_ref.v1"
    assert saved["evidence_ids"] == saved["evidence_bundle"]["evidence_ids"]
    assert all(ref.startswith("evidence_") for ref in saved["evidence_ids"])
    assert saved["evidence_aliases"]["market_change_pct:sh000001"] in saved["evidence_ids"]
    assert [s["stage"] for s in stages if s["status"] == "completed"] == list(workflow.STAGES)
    assert [c["tool_name"] for s in stages for c in s["tool_calls"]] == ["finance.collect", "finance.metrics", "finance.research_audit", "finance.publish"]
    assert result["workflow_graph"]["status"] == "done"
    assert [edge["to"] for edge in result["workflow_graph"]["transitions"]] == [
        *list(workflow.STAGES)[1:], "done"]


def test_bad_model_does_not_become_successful_analysis(configured):
    class Wrong:
        def complete(self, **kw):
            return SimpleNamespace(payload={"output": {"overview": "不知道"}})
    result = run(model=Wrong())
    assert result["metadata"]["analysis_status"] == "partial"
    assert result["report"]["analysis"] is None
    assert "分析未完成" in Path(result["artifacts"]["html_report"]).read_text(encoding="utf-8")


def test_strict_profile_rejects_empty_core_without_publishing(configured, monkeypatch):
    from policy_impact.domains.finance.quality import FinanceQualityError
    profile = {**default_profile(), "version": 1, "symbols": ["sh000001"], "quality_policy": "strict"}
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile", lambda cid: profile)
    empty = {**sample(), "series": []}
    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: empty)
    stages = []
    with pytest.raises(FinanceQualityError, match="核心数据"):
        run(on_stage=stages.append)
    assert not (configured / "reports").exists()
    assert stages[-1]["stage"] == "coverage" and stages[-1]["status"] == "failed"
    assert stages[-1]["transition"]["decision"] == "deny"
    assert not any(item["stage"] in {"research_audit", "analysis", "publication"} for item in stages)


def test_strict_profile_rejects_bad_analysis_without_publishing(configured, monkeypatch):
    from policy_impact.domains.finance.quality import FinanceQualityError
    profile = {**default_profile(), "version": 1, "symbols": ["sh000001"], "quality_policy": "strict"}
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile", lambda cid: profile)
    class Wrong:
        def complete(self, **kw):
            return SimpleNamespace(payload={"output": {"overview": "不知道"}})
    with pytest.raises(FinanceQualityError, match="未发布"):
        run(model=Wrong())
    assert not (configured / "reports").exists()


def test_chat_analysis_stores_followup_evidence_without_html(configured):
    result = run(publish_html=False, allowed_tools=("finance.collect", "finance.metrics", "finance.research_audit"))
    assert "html_report" not in result["artifacts"]
    assert not (configured / "reports").exists()
    assert result["report"]["delivery_mode"] == "chat_analysis"
    assert result["workflow_graph"]["current_stage"] == "done"
    assert "publication" not in result["workflow_graph"]["stage_output_keys"]
    history = [{"role": "assistant", "metadata": {"artifacts": result["artifacts"]}}]
    rows, audit = read_report_evidence(configured, history, "刚才的涨幅怎么算？")
    assert rows and audit["report_id"] == result["metadata"]["report_id"] and not audit["fresh_fetch"]


def test_report_permission_and_mismatched_snapshot_block(configured, monkeypatch):
    with pytest.raises(PermissionError):
        run(allowed_tools=())
    other = sample()
    other["period"]["end"] = "2026-09-23"
    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: other)
    with pytest.raises(ValueError, match="报告期"):
        run()


def test_analysis_requires_real_refs_and_numeric_tables():
    payload = GoodModel().complete().payload["output"]
    with pytest.raises(ValueError):
        workflow.validate_analysis(payload, set())
    payload["overview"] = "指数上涨100%。"
    with pytest.raises(ValueError):
        workflow.validate_analysis(payload, {"market_change_pct:sh000001"})


def test_observed_session_counts_are_allowed_only_when_in_evidence():
    payload = GoodModel().complete().payload["output"]
    payload["overview"] = "本期观测到16个交易日，尚未验证交易日历连续性。"
    evidence = {"market_change_pct:sh000001": {"point_count": 16}}
    assert workflow.validate_analysis(payload, set(evidence), evidence)
    payload["overview"] = "本期观测到18个交易日。"
    with pytest.raises(ValueError):
        workflow.validate_analysis(payload, set(evidence), evidence)


@pytest.mark.parametrize("period", ["9月1日至22日", "9月1—22日", "9月15日和16日", "2026年9月1日至9月22日", "1—8月"])
def test_report_chinese_dates_are_not_financial_values(period):
    payload = GoodModel().complete().payload["output"]
    payload["overview"] = period + "的观察期与宏观统计期不同。"
    assert workflow.validate_analysis(payload, {"market_change_pct:sh000001"})


def test_followup_uses_exact_report_without_workspace_leak(configured):
    result = run()
    history = [{"role": "assistant", "metadata": {"artifacts": result["artifacts"]}}]
    question = "刚才报告上证指数涨幅怎么算？"
    rows, audit = read_report_evidence(configured, history, question)
    assert audit["fresh_fetch"] is False
    compiled = compile_chat_context(message=question, skill={}, history=history, conversation_summary={},
        memories=[], facts=[], tool_policy={}, report_evidence=rows, config=ChatContextConfig(artifact_token_budget=1800))
    packed = json.dumps(compiled["recent_artifacts"], ensure_ascii=False)
    assert "start_close" in packed and "106" in packed and "finance_report_id" in packed
    assert read_report_evidence(configured.parent / "another", history, question)[0] == []


def test_report_escapes_source_content_and_handles_unavailable_change(configured):
    report = deepcopy(run()["report"])
    report["analysis"]["overview"] = "<script>alert(1)</script>"
    report["metrics"][0]["change_pct"] = None
    page = render_html(report)
    assert "<script>" not in page and "暂无区间涨跌" in page


def test_report_read_tool_pages_prices_and_rejects_other_workspace(configured, monkeypatch):
    from policy_impact.domains.finance.report_reader import read, ReportReadGateway
    result = run()
    rid = result["metadata"]["report_id"]
    monkeypatch.setitem(TOOL_REGISTRY, "finance.report_read", read)
    gateway = ReportReadGateway("fin", {rid})
    first = gateway.call("chat_agent_loop", "finance.report_read", report_id=rid, section="market_series", symbol="sh000001", limit=2)
    assert first["next_offset"] == 2 and first["total"] == 3
    second = gateway.call("chat_agent_loop", "finance.report_read", report_id=rid, section="market_series", symbol="sh000001", offset=2)
    assert second["items"][0]["close"] == 106 and second["next_offset"] is None
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "finance.report_read", report_id="other", section="metrics")
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "finance.report_read", report_id=rid, company_id="another")
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "finance.collect", report_id=rid)


def test_semantic_verifier_rejection_does_not_publish_unchecked_analysis(configured):
    class Rejecting(GoodModel):
        def complete(self, **kwargs):
            if kwargs.get("schema_name") == "finance_verification.v1":
                return SimpleNamespace(payload={"output": {"supported": False, "issues": ["来源不支撑结论"]}})
            return super().complete(**kwargs)
    result = run(model=Rejecting())
    assert result["report"]["analysis"] is None
    assert result["report"]["analysis_verification"]["supported"] is False


def test_document_citation_must_quote_actual_supplied_text():
    analysis = GoodModel().complete().payload["output"]
    section = analysis["sections"][0]
    section["evidence_refs"] = ["doc"]
    evidence = {"doc": {"text": "已采集的官方工业数据原文摘录"}}
    with pytest.raises(ValueError):
        workflow.validate_analysis(analysis, {"doc"}, evidence)
    section["evidence_quotes"] = [{"evidence_ref": "doc", "quote": "官方工业数据原文摘录"}]
    assert workflow.validate_analysis(analysis, {"doc"}, evidence)
