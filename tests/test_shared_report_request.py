from copy import deepcopy
from datetime import date, timedelta
import json

from policy_impact.harness.model_gateway import ModelResponse
from policy_impact.skill_executors import recent_news_report as report
from policy_impact.skill_executors.report_request import build_discovery_plan, build_report_request, report_title


def context(name, focus):
    return {"profile": {"company_name": name, "focus_topics": focus},
            "pinned_facts": [{"fact_id": "background", "value": name, "confidence": 1.0}]}


def request_for(ctx, query="/report 生成一份报告。"):
    profile = report._resolve_task_profile(ctx, query)
    constraints = report._profile_query_constraints(query, profile)
    return build_report_request("test", query, ctx, profile, constraints, source_config={
        "news_sources": [{"source_id": "github"}, {"source_id": "papers"}],
        "policy_sources": [{"source_id": "government"}],
    }), profile


def test_two_wikis_share_pool_but_differ_in_topic_plan():
    ths = context("同花顺业务研究", ["金融数据服务", "数据治理"])
    personal = context("个人Agent自进化", ["Agent自进化与技能演化"])
    original = deepcopy([ths, personal])
    left, left_profile = request_for(ths)
    right, right_profile = request_for(personal)
    assert left["source_pool"] == right["source_pool"] == ["github", "government", "papers"]
    assert left["source_registry_checksum"] == right["source_registry_checksum"]
    assert left["profile_checksum"] != right["profile_checksum"]
    assert left_profile["focus_topics"] == ["financial_ai", "data_governance"]
    assert right_profile["focus_topics"] == ["agent_evolution"]
    assert [ths, personal] == original


def test_query_overrides_focus_and_window_not_shared_pool_or_wiki():
    ctx = context("同花顺业务研究", ["金融数据服务"])
    default, _ = request_for(ctx)
    override, profile = request_for(ctx, "/report 最近90天Agent自进化论文，最多3篇。")
    assert default["source_pool"] == override["source_pool"]
    assert profile["focus_origin"] == "query"
    assert profile["focus_topics"] == ["agent_evolution"]
    assert override["period"]["start"] == (date.today() - timedelta(days=90)).isoformat()
    assert override["max_items"] == 3


def test_wiki_time_default_and_natural_week():
    profile = {"report_defaults": {"lookback_days": 45}}
    default = report._profile_query_constraints("生成报告", profile)
    assert default["period_origin"] == "wiki"
    assert default["date_from"] == (date.today() - timedelta(days=45)).isoformat()
    week = report._profile_query_constraints("/report 上周的资料", profile)
    monday = date.today() - timedelta(days=date.today().weekday())
    assert week["date_from"] == (monday - timedelta(days=7)).isoformat()
    assert week["date_to"] == (monday - timedelta(days=1)).isoformat()
    assert week["period_origin"] == "query"
    assert week["query_timezone"] == "Asia/Shanghai"


class PlanningAdapter:
    supports_discovery_planning = True

    def __init__(self, output):
        self.output = output
        self.calls = []

    def complete(self, messages, schema_name):
        self.calls.append(schema_name)
        return ModelResponse("plan-response", {"type": "final", "output": self.output})


def test_planner_is_auditable_and_cannot_set_sources_or_budget():
    request, profile = request_for(context("个人", ["自进化"]))
    model = PlanningAdapter({"search_terms": ["self-evolving agents", "智能体自进化"],
                             "topic_rules": [{"topic": "经验改进", "terms": ["experience", "自进化"]}]})
    plan = build_discovery_plan(request, profile, {}, model)
    assert model.calls == ["report_discovery_plan.v1"]
    assert plan["diagnostics"]["mode"] == "model"
    assert plan["source_pool"] == request["source_pool"]
    assert plan["max_candidates"] == 500
    assert plan["topic_terms"]["经验改进"] == ["experience", "自进化"]
    bad = PlanningAdapter({**model.output, "source_pool": ["private.example"]})
    failed = build_discovery_plan(request, profile, {"agent_evolution": ["self-evolving agents"]}, bad)
    assert failed["diagnostics"]["errors"]
    assert failed["source_pool"] == request["source_pool"]


def test_planner_rejects_urls_as_search_terms():
    request, profile = request_for(context("个人", ["自进化"]))
    model = PlanningAdapter({"search_terms": ["https://private.invalid/secret"],
                             "topic_rules": [{"topic": "研究", "terms": ["self-evolving"]}]})
    plan = build_discovery_plan(request, profile, {"agent_evolution": ["self-evolving agents"]}, model)
    assert plan["diagnostics"]["errors"]
    assert "private.invalid" not in json.dumps(plan)


def test_report_title_contains_subject_and_query_window():
    request, profile = request_for(context("同花顺", ["金融"]), "最近30天生成报告")
    title = report_title({"report_request": request, "task_profile": profile}, "id")
    assert "同花顺" in title and request["period"]["start"] in title and request["period"]["end"] in title
    assert "外部变化对" not in title


def test_semantic_rule_can_match_topic_not_known_by_legacy_catalog():
    profile = {"focus_topics": ["科研数据许可"], "topic_terms": {"科研数据许可": ["dataset licensing", "数据许可"]}}
    selected, coverage = report._filter_events_for_profile("", [
        {"event_id": "yes", "title": "Dataset licensing guidance", "summary": "Dataset licensing for research."},
        {"event_id": "no", "title": "Agent UI", "summary": "New chat color picker for agents."},
    ], profile)
    assert [item["event_id"] for item in selected] == ["yes"]
    assert coverage == {"科研数据许可": True}


def test_business_qualifier_excludes_generic_data_quality_not_financial_review():
    profile = {"focus_topics": ["金融资料治理"],
               "topic_terms": {"金融资料治理": ["data quality", "financial review", "数据质量"]},
               "topic_qualifiers": {"金融资料治理": ["financial", "金融", "投研"]}}
    selected, _ = report._filter_events_for_profile("", [
        {"event_id": "carbon", "summary": "加强碳排放数据质量管理"},
        {"event_id": "review", "summary": "A financial-review workflow checks documents for errors."},
    ], profile)
    assert [row["event_id"] for row in selected] == ["review"]


def test_default_negative_agent_clause_is_not_positive_focus():
    ctx = context("企业", ["金融数据治理；不默认追踪通用 Agent 开发技术。"])
    _, profile = request_for(ctx)
    assert "agent_harness" not in profile["focus_topics"]
    assert "data_governance" in profile["focus_topics"]


def test_feedback_improvement_query_does_not_fall_back_to_hardware_memory():
    ctx = context("企业", ["金融"])
    request, profile = request_for(ctx, "本轮关注Agent从任务反馈改进skill、memory和workflow，并通过Eval回归验证。")
    assert profile["focus_topics"] == ["agent_evolution"]
    plan = build_discovery_plan(request, profile, {"agent_evolution": ["self-evolving agent", "skill evolution"]})
    matches, _ = report._filter_events_for_profile("", [
        {"event_id": "hardware", "summary": "Shared memory stencil computation benchmark."},
        {"event_id": "agent", "summary": "Skill evolution using task feedback for agents."},
    ], {**profile, **plan})
    assert [row["event_id"] for row in matches] == ["agent"]


def test_official_blog_title_is_not_substantive_evidence():
    title = "Piloting the world's first double-blind AI evaluations"
    assert not report._has_publishable_summary({"title": title, "summary": title, "source_level": "L2"})


def test_negated_news_does_not_weaken_official_policy_request():
    constraints = report._parse_query_constraints("只看最近90天正式发布的数据治理官方政策，不用新闻报道冒充政策原文。")
    assert constraints["official_primary_only"] is True
    assert constraints["include_web_news"] is False
    assert constraints["requested_source_kinds"] == ["policy_document"]


def test_english_event_can_retrieve_chinese_wiki_via_matched_topic():
    ctx = {"profile": {"company_name": "测试企业", "focus_topics": ["金融数据"]},
           "pinned_facts": [{"fact_id": "business", "value": "业务包括金融资讯整理、公开资料检索与数据服务。",
                             "source_kind": "user_confirmed", "confidence": 1.0}]}
    event = {"event_id": "english", "title": "Financial-review workflow",
             "summary": "Document review tools check financial information for errors and missing references.",
             "category": "market_signal", "url": "https://example.test/review",
             "source_level": "L2", "matched_query_topics": ["financial_ai"]}
    item = report._analyze_company_impact("test", ctx, [event])[0]
    assert item["wiki_retrieval"]["selected_fact_ids"] == ["business"]
    assert item["company_evidence"][0]["value"] == ctx["pinned_facts"][0]["value"]
    assert item["analysis_status"] == "partial"  # Retrieval alone is not validated analysis.
