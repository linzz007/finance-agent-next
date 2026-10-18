import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.skill_executors import recent_news_report as report


def personal_context():
    return {
        "profile": {"profile_type": "personal", "company_name": "个人学习 Wiki"},
        "pinned_facts": [
            {"fact_id": "personal.project", "value": "正在用 Codex 学习 Agent 工具调用。",
             "source_kind": "user_confirmed", "source_path": "wiki/background.md",
             "importance": 5, "confidence": 1.0},
            {"fact_id": "preference.focus", "value": "关注开发者工具",
             "source_kind": "user_confirmed", "source_path": "wiki/preferences.md"},
            {"fact_id": "profile.constraints", "value": "每周只有三小时，不推荐付费工具。",
             "source_kind": "user_confirmed", "source_path": "wiki/constraints.md"},
        ],
        "retrieved_company_facts": [],
    }


def news_event():
    return {
        "event_id": "event_001", "title": "Codex tool permission update",
        "summary": "Codex added tool permission checks and isolated sandbox regression tests for agent tasks.",
        "category": "market_signal", "source": "OpenAI", "source_id": "openai_codex_releases",
        "source_level": "L2", "source_type": "github_releases",
        "url": "https://github.com/openai/codex/releases/tag/test",
        "published_at": date.today().isoformat(), "retrieved_at": date.today().isoformat(),
        "matched_query_topics": ["developer_tools", "tool_use"],
        "data_mode": "live", "is_fixture": False,
    }


def test_specific_tool_query_does_not_admit_generic_agent_research():
    query = "本轮只看最近30天Agent工具调用与权限相关的官方发布"
    focus = report._topics_from_text(query)
    assert focus == ["tool_use"]
    relevant = news_event()
    generic = {**news_event(), "event_id": "unrelated", "title": "Research acceleration",
               "summary": "Coding agents are reshaping research and experiment velocity."}
    events, coverage = report._filter_events_for_profile(query, [generic, relevant], {"focus_topics": focus})
    assert [item["event_id"] for item in events] == ["event_001"]
    assert coverage == {"tool_use": True}


def test_explicit_harness_topic_can_still_broaden_tool_query():
    assert "agent_harness" in report._topics_from_text("Agent Harness和工具调用的最新发布")


def test_unspecified_news_window_defaults_to_seven_days():
    constraints = report._profile_query_constraints("结合我的 Wiki 分析新闻", {})
    assert constraints["date_from"] == (date.today() - timedelta(days=7)).isoformat()
    assert constraints["date_to"] == date.today().isoformat()


def test_personal_impact_and_rendering_do_not_invent_financial_products():
    context = personal_context()
    original = deepcopy(context)
    analysis = report._analyze_company_impact("personal", context, [news_event()])
    html = report._render_report_html(
        "personal", context, analysis, "test-run", query="最近新闻对我的影响",
        source_disclosure="测试来源", gate_reason="测试门控", source_manifest={},
    )
    for forbidden in ("同花顺", "问财", "iFinD", "投研", "影子流量", "生产升级"):
        assert forbidden not in html
    chain = " ".join(analysis[0]["impact_chain"])
    assert "event_001" in chain
    assert "personal.project" in chain
    assert "条件式" in chain
    assert context == original


def test_task_profile_overrides_wiki_focus_without_changing_wiki():
    context = personal_context()
    original = deepcopy(context)
    profile = report._resolve_task_profile(
        context, "", {"focus_topics": ["long_context"], "constraints": ["本轮最多投入一小时"]},
    )
    assert profile["focus_topics"] == ["long_context"]
    assert profile["constraints"] == ["本轮最多投入一小时"]
    assert context == original


def acceptance_persona(key):
    fixture = json.loads((Path(__file__).parent / "fixtures/persona_acceptance.json").read_text(encoding="utf-8"))
    persona = fixture["personas"][key]
    return {
        "profile": {"profile_type": persona["profile_type"], "company_name": persona["name"]},
        "all_company_facts": [
            {"fact_id": fact["id"], "value": fact["value"], "source": "user_confirmed",
             "source_kind": "user_confirmed", "source_path": "wiki/background.md", "confidence": 1.0}
            for fact in persona["facts"]
        ],
    }


def test_default_report_uses_both_acceptance_wikis_without_mutation():
    personal = acceptance_persona("personal")
    company = acceptance_persona("ths")
    before = deepcopy([personal, company])
    profile = report._resolve_task_profile(personal, "/report 生成一份报告。")
    company_profile = report._resolve_task_profile(company, "/report 生成一份报告。")
    assert profile["profile_type"] == "personal"
    assert company_profile["profile_type"] == "company"
    assert {"trace", "eval", "context", "memory"} <= set(profile["focus_topics"])
    assert "retrieval" in company_profile["focus_topics"]
    assert profile["constraints"] != company_profile["constraints"]
    constraints = report._profile_query_constraints("/report 生成一份报告。", profile)
    assert constraints["allow_research_papers"] is True
    assert constraints["date_from"] == (date.today() - timedelta(days=7)).isoformat()
    assert constraints["requested_count"] == 5
    assert [personal, company] == before


def test_updated_goal_excludes_memory_and_reports_missing_eval():
    context = acceptance_persona("personal")
    next(fact for fact in context["all_company_facts"] if fact["fact_id"] == "personal_goal")["value"] = (
        "现在优先学习Agent评测Eval与可观测Trace，暂不关注Memory论文或迁移框架。"
    )
    profile = report._resolve_task_profile(context, "/report 按刚更新的Wiki生成最近30天技术报告，最多3条")
    assert "memory" in profile["excluded_topics"]
    matched, coverage = report._filter_events_for_profile("", [
        {**news_event(), "summary": "Added trace observability for Python agents."},
        {**news_event(), "event_id": "memory", "summary": "Added persistent memory storage for agents."},
    ], profile)
    assert [item["event_id"] for item in matched] == ["event_001"]
    assert coverage["eval"] is False
    assert coverage["trace"] is True


@pytest.mark.parametrize(("query", "selected_id", "count"), [
    ("最近30天GitHub上Agent框架、Trace和Eval相关的发布，最多3项", "event_001", 3),
    ("最近90天有哪些Agent评测或上下文管理论文值得我读？最多3篇", "paper", 3),
])
def test_github_and_papers_are_hard_source_constraints(query, selected_id, count):
    paper = {**news_event(), "event_id": "paper", "source_id": "arxiv_agent_papers",
             "url": "https://arxiv.org/abs/2609.00001", "summary": "Agent evaluation and context management study."}
    blog = {**news_event(), "event_id": "blog", "source_id": "openai_news",
            "url": "https://openai.com/news/test"}
    constraints = report._parse_query_constraints(query)
    selected, diagnostics = report._apply_query_constraints([news_event(), paper, blog], constraints)
    assert [event["event_id"] for event in selected] == [selected_id]
    assert constraints["requested_count"] == count
    assert diagnostics["outside_requested_source_kind"] == 2


def test_strict_policy_original_request_excludes_explainer_materials():
    constraints = report._parse_query_constraints(
        "只看最近90天正式发布、与金融数据有关的数据治理官方政策；不用新闻报道冒充政策原文，最多3条。"
    )
    original = {
        **news_event(),
        "event_id": "original",
        "title": "关于印发《金融信息服务数据分类分级指南》的通知",
        "source": "国家互联网信息办公室数据治理",
        "source_id": "cac_data_governance",
        "source_level": "L1",
        "source_type": "official_html_list",
        "url": "https://www.cac.gov.cn/2026-06/13/c_1782919789934988.htm",
        "published_at": date.today().isoformat(),
        "summary": "国家互联网信息办公室发布金融信息服务数据分类分级指南，说明金融信息服务数据分类分级、重要数据识别、数据安全管理和个人信息保护要求。",
    }
    qna = {
        **original,
        "event_id": "qna",
        "title": "数据出境安全管理政策法规问答（2026年9月）",
        "url": "https://www.cac.gov.cn/2026-09/11/c_1790876549989064.htm",
    }
    press = {
        **original,
        "event_id": "press",
        "title": "《网络数据安全风险评估办法》实施有关事项答记者问",
        "url": "https://www.cac.gov.cn/2026-08/20/c_1788889498173657.htm",
    }

    selected, diagnostics = report._apply_query_constraints([qna, press, original], constraints)

    assert constraints["require_policy_original"] is True
    assert [event["event_id"] for event in selected] == ["original"]
    assert selected[0]["policy_subtype"] == "policy_original"
    assert selected[0]["is_policy_original"] is True
    assert diagnostics["policy_explainer_excluded"] == 2


def test_strict_financial_policy_query_requires_financial_terms_in_policy_text():
    query = "只看最近90天正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；不用新闻报道冒充政策原文，最多3条。"
    financial_policy = {
        **news_event(),
        "event_id": "financial_policy",
        "title": "关于印发《金融信息服务数据分类分级指南》的通知",
        "summary": "为指导金融信息服务机构开展数据分类分级和重要数据识别工作，提升金融信息服务数据安全水平。",
        "source": "国家互联网信息办公室数据治理",
        "source_id": "cac_data_governance",
        "source_level": "L1",
        "source_type": "official_html_list",
        "url": "https://www.cac.gov.cn/2026-06/13/c_1782919789934988.htm",
        "published_at": date.today().isoformat(),
    }
    broad_policy = {
        **financial_policy,
        "event_id": "broad_policy",
        "title": "促进网信企业高质量发展行动计划（2026-2030年）",
        "summary": "建立并持续更新优质网信企业数据库，建立分类分级培育标准和工作机制，提升数据治理能力。",
        "source_id": "cac_policy_regulations",
        "url": "https://www.cac.gov.cn/2026-08/21/c_1789061449039952.htm",
    }
    constraints = report._parse_query_constraints(query)
    constrained, _ = report._apply_query_constraints([broad_policy, financial_policy], constraints)
    selected, coverage = report._filter_events_for_profile(
        query,
        constrained,
        {
            "focus_topics": ["financial_ai", "data_governance"],
            "topic_terms": {
                "financial_ai": ["金融信息服务", "金融数据", "投研"],
                "data_governance": ["数据治理", "数据安全", "数据分类分级", "重要数据"],
            },
        },
    )
    assert [event["event_id"] for event in selected] == ["financial_policy"]
    assert coverage == {"financial_ai": True, "data_governance": True}


def test_explicit_agent_evolution_query_does_not_fill_with_generic_ai_or_policy():
    query = "最近30天 Agent 自进化、EvoAgentX、Hermes、Claude Code、Codex 相关技术变化，最多3条。"
    codex_release = {
        **news_event(),
        "event_id": "codex",
        "title": "Codex CLI improves subagent trace streaming",
        "summary": "Codex added subagent trace forwarding and tool permission diagnostics for coding agents.",
        "source": "OpenAI Codex Releases",
        "source_id": "openai_codex_releases",
    }
    generic_ai = {
        **news_event(),
        "event_id": "generic",
        "title": "Now everyone can put data to work",
        "summary": "OpenAI released a data agent for dashboards and business analytics.",
        "source": "OpenAI News",
        "source_id": "openai_news",
    }
    codex_use_case = {
        **news_event(),
        "event_id": "use_case",
        "title": "How a researcher uses Codex and ChatGPT to search for new molecules",
        "summary": "A lab uses Codex and ChatGPT for antimicrobial candidate discovery.",
        "source": "OpenAI News",
        "source_id": "openai_news",
    }
    business_news = {
        **news_event(),
        "event_id": "business",
        "title": "Sam Altman says OpenAI going public in 2026 would be ill-advised",
        "summary": "The interview mentions recursive self-improvement but focuses on IPO timing.",
        "source": "The Verge AI",
        "source_id": "theverge_ai",
    }
    customer_case = {
        **news_event(),
        "event_id": "customer_case",
        "title": "1Password increases engineering productivity 21% with Codex",
        "summary": "A customer story about engineers using Codex to build features and internal tools.",
        "source": "OpenAI News",
        "source_id": "openai_news",
    }
    generic_policy = {
        **news_event(),
        "event_id": "policy",
        "title": "2026年个人信息保护系列专项行动取得阶段性成效",
        "summary": "专项行动通报App、SDK个人信息核查检测与整改处置的阶段性数据。",
        "source": "国家互联网信息办公室数据治理",
        "source_id": "cac_data_governance",
        "source_level": "L1",
        "source_type": "official_html_list",
        "url": "https://www.cac.gov.cn/2026-08/19/c_1788889479389148.htm",
    }
    selected, coverage = report._filter_events_for_profile(
        query,
        [generic_policy, generic_ai, codex_use_case, business_news, customer_case, codex_release],
        {
            "profile_type": "personal",
            "focus_topics": ["developer_tools", "agent_evolution"],
            "topic_terms": {
                "developer_tools": ["codex", "claude code", "developer tool"],
                "agent_evolution": ["self-evolving agent", "skill evolution", "subagent"],
            },
        },
    )
    assert [event["event_id"] for event in selected] == ["codex"]
    assert coverage == {"developer_tools": True, "agent_evolution": True}


def test_chinese_future_date_range_never_falls_back_to_current_news():
    constraints = report._profile_query_constraints(
        "/report 只分析2099年1月1日至2099年1月7日已经发布的Agent论文，不允许使用窗口外资料。", {},
    )
    assert constraints["date_from"] == "2099-01-01"
    assert constraints["date_to"] == "2099-01-07"
    paper = {**news_event(), "event_id": "paper", "source_id": "arxiv_agent_papers",
             "url": "https://arxiv.org/abs/2609.00001"}
    selected, diagnostics = report._apply_query_constraints([news_event(), paper], constraints)
    assert selected == []
    assert diagnostics["outside_requested_source_kind"] == 1
    assert diagnostics["outside_date_window"] == 1


def test_current_source_and_window_request_override_wiki_default():
    profile = report._resolve_task_profile(personal_context(), "最近30天最多3条，官方和媒体都可以")
    profile["constraints"] = ["只看官方"]
    constraints = report._profile_query_constraints("最近30天最多3条，官方和媒体都可以", profile)
    assert constraints["date_from"] == (date.today() - timedelta(days=30)).isoformat()
    assert constraints["requested_count"] == 3
    assert constraints["official_primary_only"] is False


def test_rule_inference_and_project_design_are_not_used_as_wiki_facts():
    context = personal_context()
    context["pinned_facts"][0]["source_kind"] = "analysis_inference"
    context["pinned_facts"].append({"fact_id": "design", "value": "Codex 已提升成功率90%",
                                    "source_kind": "project_design", "source_path": "wiki/design.md"})
    item = report._analyze_company_impact("personal", context, [news_event()])[0]
    assert item["company_evidence"] == []
    assert item["analysis_status"] == "partial"
    assert "90%" not in item["impact_chain"][0]


def model_payload():
    return {"type": "final", "output": {"analyses": [{
        "event_id": "event_001", "event_quote": report._evidence_excerpt(news_event()["summary"], []),
        "wiki_facts": [{"fact_id": "personal.project", "quote": "正在用 Codex 学习 Agent 工具调用。"}],
        "event_summary_zh": "Codex 增加了工具权限检查及隔离沙箱回归测试。",
        "condition": "若当前学习项目的工具权限机制与发布所述一致",
        "conditional_impact": "可能需要为工具拒绝场景补充对照用例，尚不能确认现有项目受影响。",
        "recommended_action": "先阅读这条发布说明，为当前 Codex 学习项目设计一条工具拒绝的只读测试记录，不运行代码。",
    }]}}


def mocked_run(monkeypatch, tmp_path, *, adapter=None, callback=None, context=None, items=None):
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(context or personal_context()))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: (
        items if items is not None else [news_event()],
        {"mode": "source_snapshot", "collection_succeeded": True, "snapshot_path": "test-only",
         "source_statuses": [{"source_id": "openai_codex_releases", "status": "ok"}]},
    ))
    return report.run_recent_news_report(
        "personal", "最近7天最多1条", refresh_live_sources=False, model_adapter=adapter, on_stage=callback,
    )


def test_model_chinese_schema_citations_and_detached_actual_stages(monkeypatch, tmp_path):
    stages = []

    def callback(stage):
        stages.append(deepcopy(stage))
        if stage["stage"] == "wiki_context" and stage["status"] == "completed":
            stage["wiki_snapshot"]["profile"]["company_name"] = "must not leak"

    result = mocked_run(monkeypatch, tmp_path, adapter=ScriptedModelAdapter([model_payload()]), callback=callback)
    assert result["analysis_status"] == "done"
    assert result["analysis_diagnostics"]["model_calls"] == 1
    assert result["top_matches"][0]["analysis_mode"] == "model"
    assert result["top_matches"][0]["conditional_impact"] == model_payload()["output"]["analyses"][0]["conditional_impact"]
    assert result["top_matches"][0]["event_summary_zh"] == model_payload()["output"]["analyses"][0]["event_summary_zh"]
    assert [(stage["stage"], stage["status"]) for stage in stages] == [
        (name, status) for name in ("wiki_context", "source_collection", "filtering", "impact_analysis", "report_rendering")
        for status in ("started", "completed")
    ]
    for started, completed in zip(stages[::2], stages[1::2]):
        assert started["started_at"] == completed["started_at"]
        assert started["finished_at"] is None
        assert completed["finished_at"] >= completed["started_at"]
        assert "input" in started and "output" in completed and "gate" in completed
    assert stages[5]["gate"]["decision"] == "allow"
    assert stages[7]["output"]["analysis_status"] == "done"
    artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert artifact["wiki_snapshot"]["profile"]["company_name"] == "个人学习 Wiki"
    assert artifact["query_constraints"]["requested_count"] == 1
    assert artifact["task_profile"]["constraints"] == ["每周只有三小时，不推荐付费工具。"]
    assert artifact["stages"][3]["source_snapshot"]["items"][0]["title"] == news_event()["title"]
    assert result["workflow_graph"]["status"] == "done"
    assert artifact["workflow_graph"] == result["workflow_graph"]
    assert [edge["to"] for edge in result["workflow_graph"]["transitions"]] == [
        "source_collection", "filtering", "impact_analysis", "report_rendering", "done"]


def test_invalid_source_snapshot_blocks_filtering_and_records_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(personal_context()))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: ([], None))
    events = []
    with pytest.raises(ValueError, match="invalid source snapshot"):
        report.run_recent_news_report("personal", "最近7天有什么变化", refresh_live_sources=False,
                                      on_stage=events.append)
    assert events[-1]["stage"] == "source_collection" and events[-1]["status"] == "failed"
    assert events[-1]["gate"]["decision"] == "deny"
    assert not any(item["stage"] == "filtering" for item in events)


def test_single_event_cannot_be_expanded_into_two_analyses(monkeypatch, tmp_path):
    duplicate = model_payload()
    duplicate["output"]["analyses"].append(deepcopy(duplicate["output"]["analyses"][0]))
    result = mocked_run(monkeypatch, tmp_path, adapter=ScriptedModelAdapter([duplicate, model_payload()]))
    assert result["selected_event_count"] == 1
    assert result["analysis_status"] == "done"
    assert result["analysis_diagnostics"]["model_calls"] == 2
    assert result["analysis_diagnostics"]["repair_attempts"][0]["previous_errors"][0]["code"] == "invalid_analyses_array"


@pytest.mark.parametrize(("impact", "expected"), [
    ("若成立则需要检查工具边界，但尚未证明适配本项目。", "done"),
    ("必然降低项目成本并保证提升运行效果。", "partial"),
])
def test_conditional_language_not_restricted_to_one_keyword(monkeypatch, tmp_path, impact, expected):
    payload = model_payload()
    payload["output"]["analyses"][0]["conditional_impact"] = impact
    result = mocked_run(monkeypatch, tmp_path, adapter=ScriptedModelAdapter([payload]))
    assert result["analysis_status"] == expected


@pytest.mark.parametrize("broken", ["event_quote", "wiki_quote", "wiki_id", "persona", "empty", "schema"])
def test_invalid_model_analysis_is_partial_without_fabricated_success(monkeypatch, tmp_path, broken):
    payload = model_payload()
    row = payload["output"]["analyses"][0]
    if broken == "event_quote":
        row["event_quote"] = "This event claims a fictional improvement."
    elif broken == "wiki_quote":
        row["wiki_facts"][0]["quote"] = "实际使用了不存在的产品"
    elif broken == "wiki_id":
        row["wiki_facts"][0]["fact_id"] = "another_persona"
    elif broken == "persona":
        row["recommended_action"] = "建议为问财与iFinD开展金融产品的生产升级验证。"
    elif broken == "empty":
        payload["output"]["analyses"] = []
    else:
        payload = {"type": "final", "output": {"analyses": "not a list"}}
    result = mocked_run(monkeypatch, tmp_path, adapter=ScriptedModelAdapter([payload]))
    assert result["status"] == result["analysis_status"] == "partial"
    assert result["analysis_diagnostics"]["errors"]
    assert result["top_matches"][0]["analysis_mode"] == "rules"
    html = Path(result["html_report_path"]).read_text(encoding="utf-8")
    assert "部分完成 / partial" in html
    assert "规则归类（非模型翻译）" in html
    assert "门控通过" not in html


def test_model_failure_is_bounded_and_does_not_expose_transport_secrets(monkeypatch, tmp_path):
    class FailingAdapter:
        max_tokens = 10000
        timeout_seconds = 120
        max_attempts = 9

        def __init__(self):
            self.calls = []

        def complete(self, messages, schema_name):
            self.calls.append((self.max_tokens, self.timeout_seconds, self.max_attempts))
            raise RuntimeError("sensitive-provider-key")

    adapter = FailingAdapter()
    result = mocked_run(monkeypatch, tmp_path, adapter=adapter)
    assert adapter.calls == [(8192, 60, 2)]
    assert adapter.max_tokens == 10000
    assert result["analysis_status"] == "partial"
    assert "sensitive-provider-key" not in json.dumps(result)


def test_2048_token_adapter_batches_two_rows_and_obeys_report_call_budget():
    class BatchAdapter:
        max_tokens = 2048

        def __init__(self):
            self.calls = []

        def complete(self, messages, schema_name):
            request = json.loads(messages[1]["content"])
            self.calls.append(request)
            rows = []
            for event in request["events"]:
                row = deepcopy(model_payload()["output"]["analyses"][0])
                row["event_id"] = event["event_id"]
                rows.append(row)
            return ModelResponse(str(len(self.calls)), {"type": "final", "output": {"analyses": rows}})

    context = personal_context()
    profile = report._resolve_task_profile(context, "")
    analysis = report._analyze_company_impact("personal", context, [
        {**news_event(), "event_id": f"event_{index:03d}"} for index in range(1, 8)
    ])
    adapter = BatchAdapter()
    selected, diagnostics = report._model_analyze_impacts(analysis, profile, adapter, max_model_events=5)
    assert [len(request["events"]) for request in adapter.calls] == [2, 2, 1]
    assert diagnostics["model_calls"] == 3
    assert diagnostics["max_model_calls"] == 5
    assert sum(item["analysis_mode"] == "model" for item in selected) == 5
    assert all(item["analysis_status"] == "partial" for item in selected[5:])


def test_weak_or_future_sources_remain_unpublishable(monkeypatch, tmp_path):
    items = [
        {**news_event(), "title": "future", "published_at": "2099-01-01"},
        {**news_event(), "title": "weak", "summary": "No summary provided"},
        {**news_event(), "title": "wrong domain", "url": "https://example.invalid/fake"},
    ]
    result = mocked_run(monkeypatch, tmp_path, items=items)
    assert result["selected_event_count"] == 0
    assert result["analysis_status"] == "partial"
    assert result["relevance_gate"] == "ask"
    assert result["constraint_diagnostics"]["outside_date_window"] == 1


def test_gateway_output_envelope_and_long_verbatim_quote_are_accepted():
    event = news_event()
    event["summary"] = (
        "MeClear combines Leave One Out screening with sampled cooperative Shapley attribution "
        "to distribute utility across interacting evidence, effectively resolving redundant "
        "conflict masking where single removal evaluations fail."
    )
    context = personal_context()
    analysis = report._analyze_company_impact("personal", context, [event])
    payload = model_payload()
    payload.pop("type")
    payload["output"]["analyses"][0]["event_quote"] = event["summary"]
    selected, diagnostics = report._model_analyze_impacts(
        analysis, report._resolve_task_profile(context, ""), ScriptedModelAdapter([payload]), max_model_events=5,
    )
    assert diagnostics["errors"] == []
    assert selected[0]["analysis_status"] == "done"
    assert selected[0]["evidence_links"]["event_quote"] == event["summary"]


def test_recorded_p04_responses_produce_three_chinese_paper_analyses(monkeypatch, tmp_path):
    case_dir = Path(__file__).parents[1] / "data/acceptance/persona_20260909_round2/cases/P04"
    if not (case_dir / "trace.json").exists():
        pytest.skip("Local recorded acceptance trace is not present")
    trace = json.loads((case_dir / "trace.json").read_text(encoding="utf-8"))
    artifact = json.loads((case_dir / "run_artifact.json").read_text(encoding="utf-8"))
    calls = [step for step in trace["steps"] if
             step.get("input_payload", {}).get("schema_name") == "profile_aware_news_report.v1"]
    assert [call["metadata"]["usage"]["output_tokens"] for call in calls] == [441, 237]
    payloads = [call["output_payload"]["payload"] for call in calls]
    selected, diagnostics = report._model_analyze_impacts(
        artifact["analysis"], artifact["task_profile"], ScriptedModelAdapter(deepcopy(payloads)), max_model_events=5,
    )
    assert diagnostics["errors"] == []
    assert diagnostics["model_calls"] == 2
    assert len(selected) == 3
    assert all(item["analysis_status"] == "done" and item["event_summary_zh"] for item in selected)
    for item in selected:
        assert item["evidence_links"]["event_quote"] in item["news_evidence"]["original_summary"]
    html = report._render_report_html(
        "personal", artifact["wiki_snapshot"], selected, "offline-p04-replay", query=artifact["query"],
        source_disclosure="离线回放已采集 arXiv 摘要", gate_reason="引用校验完成，未核验全文或复现实验",
        source_manifest=artifact["source_manifest"],
    )
    assert all(item["event_summary_zh"] in html for item in selected)
    assert "规则归类（非模型翻译）" not in html
    assert "资料局限" in html and "复现前置条件" in html

    class RecordedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 9)

    monkeypatch.setattr(report, "date", RecordedDate)
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(artifact["wiki_snapshot"]))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: (
        deepcopy(artifact["raw_items"]), deepcopy(artifact["source_manifest"]),
    ))
    result = report.run_recent_news_report(
        "personal", artifact["query"], refresh_live_sources=False,
        model_adapter=ScriptedModelAdapter(deepcopy(payloads)),
    )
    assert result["status"] == result["analysis_status"] == "done"
    assert result["selected_event_count"] == 3
    assert result["analysis_diagnostics"]["model_calls"] == 2
    assert result["analysis_diagnostics"]["errors"] == []
    replay = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    for item in replay["analysis"]:
        assert item["event_summary_zh"] and item["limitations"] and item["reproduction_prerequisites"]
        assert item["news_evidence"]["summary"] == item["news_evidence"]["original_summary"]
    assert all(item["event_summary_zh"] in Path(result["html_report_path"]).read_text(encoding="utf-8")
               for item in replay["analysis"])


@pytest.mark.parametrize(("mutation", "code", "field"), [
    ("quote", "event_quote_mismatch", "event_quote"),
    ("wiki", "unknown_wiki_fact", "wiki_facts.fact_id"),
    ("action_type", "invalid_schema_envelope", None),
    ("oversized", "field_budget_exceeded", "event_quote"),
])
def test_accepted_gateway_shape_preserves_evidence_and_budget_rejections(mutation, code, field):
    context = personal_context()
    analysis = report._analyze_company_impact("personal", context, [news_event()])
    payload = model_payload()
    payload.pop("type")
    row = payload["output"]["analyses"][0]
    if mutation == "quote":
        row["event_quote"] += " A fabricated result unsupported by the source."
    elif mutation == "wiki":
        row["wiki_facts"][0]["fact_id"] = "other_persona.private_stack"
    elif mutation == "action_type":
        payload["type"] = "tool_call"
    else:
        row["event_quote"] = "A" * 1025
    selected, diagnostics = report._model_analyze_impacts(
        analysis, report._resolve_task_profile(context, ""), ScriptedModelAdapter([payload]), max_model_events=5,
    )
    assert selected[0]["analysis_status"] == "partial"
    assert diagnostics["errors"][0]["code"] == code
    assert diagnostics["errors"][0].get("field") == field
    assert diagnostics["response_ids"] == ["scripted_1", "scripted_2"]
    assert diagnostics["model_calls"] == 2
    assert len(diagnostics["repair_attempts"]) == 1


def test_rejected_wiki_quote_can_be_repaired_without_weakening_citations():
    context = personal_context()
    analysis = report._analyze_company_impact("personal", context, [news_event()])
    invalid = model_payload()
    invalid["output"]["analyses"][0]["wiki_facts"][0]["quote"] = "invented background text"
    adapter = ScriptedModelAdapter([invalid, model_payload()])
    selected, diagnostics = report._model_analyze_impacts(
        analysis, report._resolve_task_profile(context, ""), adapter, max_model_events=5,
    )
    assert selected[0]["analysis_status"] == "done"
    assert diagnostics["errors"] == []
    assert diagnostics["model_calls"] == 2
    assert diagnostics["repair_attempts"][0]["previous_errors"][0]["code"] == "wiki_quote_mismatch"


@pytest.mark.parametrize(("summary", "expected"), [
    ("Accurate citations trace intellectual origins and substantiate claims.", False),
    ("Our agent traces citation origins across academic literature.", False),
    ("We trace the historical origins of agent research. Evaluation is separate.", False),
    ("Added request tracing spans for tool execution.", True),
    ("Added distributed tracing spans.", True),
    ("Added trace_policy controls for streamed session logs.", True),
    ("Added live streaming of a foreground subagent's tool calls and results to Remote Control clients.", True),
    ("The citation agent emits tool-call spans to an OTLP collector.", True),
    ("We describe a trace of academic citations without runtime instrumentation.", False),
])
def test_trace_requires_runtime_observability_context(summary, expected):
    event = {**news_event(), "title": "Evidence", "summary": summary}
    selected, coverage = report._filter_events_for_profile("Trace", [event], {"focus_topics": ["trace"]})
    assert bool(selected) is expected
    assert coverage == {"trace": expected}
    assert report._filter_events_for_query("Trace", [event])[1] == {"trace": expected}


def reportfix_case(case_id):
    path = Path(__file__).parents[1] / "data/acceptance/persona_20260909_reportfix/cases" / case_id
    if not (path / "run_artifact.json").exists():
        pytest.skip("Local reportfix acceptance evidence is not present")
    artifact = json.loads((path / "run_artifact.json").read_text(encoding="utf-8"))
    trace = json.loads((path / "trace.json").read_text(encoding="utf-8"))
    rows = {}
    for step in trace["steps"]:
        if step.get("input_payload", {}).get("schema_name") == "profile_aware_news_report.v1":
            for row in step["output_payload"]["payload"]["output"]["analyses"]:
                rows[row["event_id"]] = row
    return artifact, rows


@pytest.mark.parametrize("case_id", ["P01", "P04", "P16"])
def test_recorded_recite_is_eval_not_runtime_trace(case_id):
    artifact, _ = reportfix_case(case_id)
    recite = next(event for event in artifact["structured_events"] if event["title"].startswith("ReCite:"))
    selected, coverage = report._filter_events_for_profile("", [recite], {"focus_topics": ["trace", "eval"]})
    assert selected[0]["matched_query_topics"] == ["eval"]
    assert coverage == {"trace": False, "eval": True}


@pytest.mark.parametrize(("query", "supplied", "excluded"), [
    ("只看Eval和Trace", None, True),
    ("Eval、Trace与Memory", None, True),
    ("本轮也看Memory", None, False),
    ("只看Eval", {"focus_topics": ["memory"]}, True),
    ("只看Eval", {"excluded_topics": []}, False),
    ("本轮也看Memory，但不要分析Memory", None, True),
])
def test_positive_focus_preserves_wiki_exclusions_until_explicit_override(query, supplied, excluded):
    context = acceptance_persona("personal")
    next(fact for fact in context["all_company_facts"] if fact["fact_id"] == "personal_goal")["value"] = (
        "现在优先学习Agent评测Eval与可观测Trace，暂不关注Memory论文或迁移框架。"
    )
    before = deepcopy(context)
    profile = report._resolve_task_profile(context, query, supplied)
    assert ("memory" in profile["excluded_topics"]) is excluded
    assert context == before


def test_recorded_p04_has_distinct_reading_and_training_prerequisites():
    artifact, rows = reportfix_case("P04")
    validated = {}
    for item in artifact["analysis"]:
        if item["event_id"] == "event_056":
            # The recorded model claimed an existing memory pool. Do not hide that
            # failure by silently replacing its action with a canned reading task.
            with pytest.raises(report._AnalysisValidationError, match="unsupported_wiki_memory_pool"):
                report._validate_model_impact(rows[item["event_id"]], item, artifact["task_profile"])
        else:
            validated[item["event_id"]] = report._validate_model_impact(rows[item["event_id"]], item, artifact["task_profile"])
            assert validated[item["event_id"]]["recommended_action"].startswith(rows[item["event_id"]]["recommended_action"])
    notes = {item["event_id"]: report._paper_reading_guidance(item["news_evidence"]) for item in artifact["analysis"]}
    assert len({item["reproduction_prerequisites"] for item in notes.values()}) == 3
    for event_id in ("event_054", "event_051"):
        text = validated[event_id]["reproduction_prerequisites"]
        for requirement in ("训练", "模型权重或训练接口", "训练数据", "算力", "待核验", "不能仅凭普通推理API复现"):
            assert requirement in text
    assert "SQLite不证明已有记忆池" in notes["event_056"]["reproduction_prerequisites"]
    assert all("尚未核验全文" in item["limitations"] for item in notes.values())


def test_recorded_p01_cannot_infer_unpublished_details():
    artifact, rows = reportfix_case("P01")
    item = next(item for item in artifact["analysis"] if item["event_id"] == "event_072")
    with pytest.raises(report._AnalysisValidationError, match="unsupported_source_availability_claim"):
        report._validate_model_impact(rows["event_072"], item, artifact["task_profile"])
    row = {**rows["event_072"], "event_summary_zh": "OpenAI介绍编码代理研究中的早期使用数据；当前保存摘录未包含细节，未核验全文。"}
    assert report._validate_model_impact(row, item, artifact["task_profile"])["analysis_status"] == "done"


def test_sqlite_stack_cannot_ground_existing_memory_pool_claim():
    row = model_payload()["output"]["analyses"][0]
    row["conditional_impact"] = "可能帮助你管理现有SQLite记忆池，但效果仍需验证。"
    item = report._analyze_company_impact("personal", personal_context(), [news_event()])[0]
    with pytest.raises(report._AnalysisValidationError, match="unsupported_wiki_memory_pool"):
        report._validate_model_impact(row, item, report._resolve_task_profile(personal_context(), ""))


def test_recorded_p16_title_lead_does_not_fill_recommendation_slot(monkeypatch, tmp_path):
    artifact, rows = reportfix_case("P16")
    titles = {item["title"]: rows[item["event_id"]] for item in artifact["analysis"]}
    raw = [item for item in artifact["raw_items"] if item["title"] in titles]

    class RecordedRowsAdapter:
        def complete(self, messages, schema_name):
            events = json.loads(messages[1]["content"])["events"]
            output = [{**deepcopy(titles[event["title"]]), "event_id": event["event_id"]} for event in events]
            return ModelResponse("offline-p16", {"output": {"analyses": output}})

    class RecordedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 9)

    monkeypatch.setattr(report, "date", RecordedDate)
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(artifact["wiki_snapshot"]))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: (deepcopy(raw), deepcopy(artifact["source_manifest"])))
    result = report.run_recent_news_report("personal", artifact["query"], refresh_live_sources=False, model_adapter=RecordedRowsAdapter())
    assert result["selected_event_count"] == result["publishable_event_count"] == 2
    assert result["analysis_diagnostics"]["model_calls"] == 1
    assert result["analysis_diagnostics"]["errors"] == []
    assert "memory" in result["task_profile"]["excluded_topics"]
    assert result["quality_diagnostics"]["missing_or_weak_summary_count"] == 1
    assert "标题级线索未进入主要推荐" in result["gate_reason"]
    assert all("HarnessRisk" not in item["title"] for item in result["top_matches"])
    for item in result["top_matches"]:
        assert item["matched_topics"] == (["eval"] if item["title"].startswith("ReCite:") else ["trace"])
    html = Path(result["html_report_path"]).read_text(encoding="utf-8")
    table = html.split('<table class="summary-table">')[1].split("</table>")[0]
    assert "https://" not in table and "Wiki 事实" not in table
    assert "min-width: 960px" in html and "overflow-x: auto" in html
    assert "white-space: nowrap" in html
    assert "0 条分析仍不完整" not in html
    assert "规则排序分" in html and "不是收益、成功概率" in html
    assert "事件证据 [" in html


@pytest.mark.parametrize("summary_suffix", ["", " - News publisher"])
def test_long_title_repetition_is_not_substantive_l5_evidence(summary_suffix):
    title = "Agent Harness lifecycle evaluation benchmark with a very long descriptive title - News publisher"
    event = {**news_event(), "source_level": "L5", "title": title,
             "summary": title.removesuffix(" - News publisher") + summary_suffix}
    assert report._has_publishable_summary(event) is False
    event["summary"] += ". Researchers measured tool-call failures across a documented test suite and described the evaluation protocol."
    assert report._has_publishable_summary(event) is True


def test_future_window_explains_unpublished_evidence_without_source_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: personal_context())
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: ([news_event()], {"mode": "source_snapshot"}))
    result = report.run_recent_news_report(
        "personal", "只分析2099年1月1日至2099年1月7日已经发布的Agent论文", refresh_live_sources=False,
        model_adapter=ScriptedModelAdapter([]),
    )
    assert result["selected_event_count"] == 0
    assert result["analysis_diagnostics"]["model_calls"] == 0
    assert result["analysis_status"] == "partial" and result["relevance_gate"] == "ask"
    assert result["gate_reason"].startswith("future_window:")
    for key in ("report_path", "html_report_path"):
        rendered = Path(result[key]).read_text(encoding="utf-8")
        assert "资料尚未发布" in rendered
        assert "补充可审计数据源后重新生成" not in rendered
        assert "建议补充符合本轮来源限制的资料" not in rendered
