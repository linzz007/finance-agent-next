"""Chat report summaries must not expose raw evidence or unvalidated analysis."""

from copy import deepcopy

import pytest

from policy_impact.harness.agent_runtime import AgentRuntime


@pytest.fixture
def report_result():
    return {
        "relevance_gate": "allow",
        "analysis_status": "partial",
        "gate_reason": "模型分析未完整通过 Schema 与引用校验",
        "incomplete_reasons": ["部分事件尚未完成中文语义分析"],
        "source_disclosure": "实时来源快照；查询窗口 2026-09-03 至 2026-09-10",
        "source_manifest": {"source_statuses": [{"status": "ok"}, {"status": "error"}]},
        "event_count": 66,
        "matched_event_count": 1,
        "selected_event_count": 1,
        "topic_coverage": {"金融数据治理": True, "金融监管政策": False},
        "top_matches": [{
            "title": "数字化协同发展实施方案",
            "analysis_status": "partial",
            "analysis_mode": "rules",
            "summary": "RAW_SOURCE_PARAGRAPH " * 1000,
            "impact_chain": ["RAW_SOURCE_PARAGRAPH " * 1000, "RAW_WIKI_FACT " * 1000],
            "company_evidence": ["RAW_WIKI_FACT " * 1000],
            "recommended_action": "RAW_WIKI_FACT " * 1000,
        }],
    }


def _answer(result, artifacts=None):
    runtime = object.__new__(AgentRuntime)
    return runtime._news_workflow_answer(
        "分析当前资料", result,
        artifacts or {"html_report": "D:/test/reports/shared-report.html"},
    )


def test_sd03_shaped_partial_report_does_not_echo_source_or_wiki(report_result):
    original = deepcopy(report_result)
    answer = _answer(report_result)

    assert len(answer) <= 1200
    assert "报告部分完成" in answer
    assert "数字化协同发展实施方案" in answer
    assert "分析待完成" in answer
    assert "Schema 与引用校验" in answer
    assert "未覆盖主题：金融监管政策" in answer
    assert "来源：1/2 个成功" in answer
    assert "读取 66 条，匹配 1 条，入选 1 条" in answer
    assert "[查看完整报告](<D:/test/reports/shared-report.html>)" in answer
    assert "RAW_SOURCE_PARAGRAPH" not in answer
    assert "RAW_WIKI_FACT" not in answer
    assert report_result == original


def test_validated_entry_uses_only_bounded_semantic_fields(report_result):
    report_result.update(analysis_status="done", incomplete_reasons=[], gate_reason="通过")
    item = report_result["top_matches"][0]
    item.update(
        analysis_status="done", analysis_mode="model",
        event_summary_zh="方案提出分阶段推进数字化协同。",
        condition="若当前业务属于方案适用范围",
        conditional_impact="可能需要补充数据治理适用性核查。",
        recommended_action="先核验适用条款，再记录治理缺口与负责人。；执行边界：仅做核验，不自动改变业务配置。",
    )
    answer = _answer(report_result)

    assert "摘要：方案提出分阶段推进数字化协同。" in answer
    assert "若当前业务属于方案适用范围，可能需要补充" in answer
    assert "建议行动：先核验适用条款，再记录治理缺口与负责人。" in answer
    assert "；执行边界：" not in answer
    assert "分析待完成" not in answer
    assert "RAW_SOURCE_PARAGRAPH" not in answer
    assert "RAW_WIKI_FACT" not in answer
    assert "不代表已采用或已产生效果" in answer


def test_old_projection_has_bounded_fallback_and_keeps_validated_action(report_result):
    report_result.update(analysis_status="done", incomplete_reasons=[], gate_reason="通过")
    report_result["top_matches"][0].update(
        analysis_status="done", analysis_mode="model",
        event_summary_zh="论文提出以有限预算更新技能文档。",
        recommended_action="先复现基准，再小范围比较成本与成功率。",
    )
    answer = _answer(report_result)
    assert "适用条件与完整影响说明见报告" in answer
    assert "建议行动：先复现基准，再小范围比较成本与成功率。" in answer
    assert "RAW_SOURCE_PARAGRAPH" not in answer
    assert len(answer) <= 1200


def test_partial_analysis_never_publishes_action_or_conditional_impact(report_result):
    report_result["top_matches"][0].update(
        condition="未经验证的适用条件", conditional_impact="未经验证的影响结论",
        recommended_action="未经验证的行动建议", event_summary_zh="未经验证的摘要",
    )
    answer = _answer(report_result)
    assert "未经验证" not in answer
    assert "分析待完成" in answer


@pytest.mark.parametrize("status,mode,summary", [
    ("partial", "model", "未经验证的中文摘要不应展示。"),
    ("done", "rules", "规则拼装的中文摘要不应冒充模型分析。"),
    ("done", "model", "An untranslated source paragraph must not appear."),
    ("done", "model", "长篇原始来源正文不应当作语义摘要。" * 1000),
], ids=["partial", "rules", "untranslated", "source_sized"])
def test_unvalidated_or_source_sized_summary_stays_pending(report_result, status, mode, summary):
    report_result["analysis_status"] = "done"
    report_result["top_matches"][0].update(
        analysis_status=status, analysis_mode=mode, event_summary_zh=summary,
    )
    answer = _answer(report_result)

    assert "报告部分完成" in answer
    assert "分析待完成" in answer
    assert summary[:20] not in answer


def test_gate_denial_suppresses_entry_conclusions_and_keeps_report_link(report_result):
    report_result.update(relevance_gate="ask", gate_reason="future_window: 请求日期尚未到来")
    report_result["top_matches"][0].update(
        analysis_status="done", analysis_mode="model", event_summary_zh="不可发布的分析结论。",
    )
    answer = _answer(report_result)

    assert "门控未通过" in answer
    assert "不生成推断性结论" in answer
    assert "请改用已经过去的日期窗口" in answer
    assert "不可发布的分析结论" not in answer
    assert "查看完整报告" in answer


def test_long_report_remains_bounded_including_public_prefix(report_result):
    report_result["top_matches"] *= 30
    report_result["selected_event_count"] = 30
    report_result["gate_reason"] = "证据不足，不能形成结论。" * 1000
    report_result["incomplete_reasons"] = ["必须保留范围限制。" * 1000] * 30
    report_result["source_disclosure"] = "来源范围受限。" * 1000
    path = "D:/" + "a" * 330 + "/report.html"
    answer = _answer(report_result, {"html_report": path})
    runtime = object.__new__(AgentRuntime)
    wrapped = runtime._wrap_external_impact_result(
        {"answer": answer}, route="news", route_label="共享来源池研判（新闻、技术、政策与论文）",
        selected_sources=[*runtime._impact_selected_sources("news"), "policy_sources"],
    )

    assert len(answer) <= 1200
    assert len(wrapped["answer"]) <= 1500
    assert f"[查看完整报告](<{path}>)" in answer
    assert "报告部分完成" in answer
    assert "其余" in answer
    assert "RAW_SOURCE_PARAGRAPH" not in answer


def test_empty_selection_has_no_invented_entries(report_result):
    report_result.update(top_matches=[], selected_event_count=0)
    answer = _answer(report_result)

    assert "入选 0 条" in answer
    assert "当前没有通过来源门控且与问题匹配的事件" in answer
    assert "查看完整报告" in answer
