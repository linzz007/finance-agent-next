"""Focused runtime integrations; no report executor or network is run here."""

import json

import pytest

from policy_impact.harness import agent_runtime as runtime_module
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter, ScriptedModelAdapter


class RecordingStore:
    def __init__(self):
        self.steps = []

    def add_agent_step(self, run_id, **step):
        self.steps.append({"run_id": run_id, **step})


@pytest.fixture
def runtime(monkeypatch):
    runtime = object.__new__(AgentRuntime)
    runtime.company_id = "personal_test"
    runtime.store = RecordingStore()
    runtime.model_adapter = ScriptedModelAdapter([
        {"type": "final", "output": {"answer": "Observed workflow result."}},
    ])

    def no_network(*args, **kwargs):
        pytest.fail("Unexpected model network call")

    monkeypatch.setattr(AnthropicCompatibleModelAdapter, "_request", no_network)
    return runtime


@pytest.mark.parametrize("query", [
    "分析本周政策和 Agent 技术进展的影响",
    "研究最新论文和技术进展，生成研判报告",
    "最新监管政策有什么影响",
])
def test_public_report_uses_shared_workflow_for_policy_and_research(runtime, monkeypatch, query):
    assert runtime._parse_slash_command(f"/report {query}") == ("external_impact_report", query)
    called = []

    def shared_report(run_id, message):
        called.append((run_id, message))
        return {"answer": "Shared report.", "artifacts": {}, "citations": []}

    def no_legacy(*args, **kwargs):
        pytest.fail("Public /report must not call a legacy policy or outline executor")

    monkeypatch.setattr(runtime, "_run_recent_news_report", shared_report)
    monkeypatch.setattr(runtime, "_run_policy_weekly_impact", no_legacy)
    monkeypatch.setattr(runtime, "_run_research_report", no_legacy)
    result = runtime._run_external_impact_report("run-test", "session", query, DEFAULT_MODEL_ID, {}, {})

    assert called == [("run-test", query)]
    assert result["internal_route"] == "news"
    assert result["stop_reason"] == "success"
    route = next(step["output_payload"] for step in runtime.store.steps
                 if step["step_type"] == "workflow_stage"
                 and step["output_payload"].get("public_skill_id"))
    assert route["workflow"] == "unified_shared_report"
    assert route["internal_route"] == "news"
    assert {"policy_sources", "research_feeds", "news_sources"} <= set(route["selected_source_types"])


def test_source_inventory_keeps_its_special_workflow(runtime, monkeypatch):
    calls = []

    def inventory(run_id, message):
        calls.append(message)
        return {"answer": "Inventory only.", "artifacts": {}, "citations": []}

    def no_report(*args, **kwargs):
        pytest.fail("Source inventory must not start a report")

    monkeypatch.setattr(runtime, "_run_impact_source_inventory", inventory)
    monkeypatch.setattr(runtime, "_run_recent_news_report", no_report)
    query = "列出当前配置的数据源"
    result = runtime._run_external_impact_workflow("run-test", "session", query, DEFAULT_MODEL_ID, {}, {})

    assert calls == [query]
    assert result["internal_route"] == "source_inventory"


@pytest.mark.parametrize("real_adapter", [True, False])
def test_report_model_discovery_flag_and_schema_specific_trace(runtime, monkeypatch, real_adapter):
    if real_adapter:
        adapter = AnthropicCompatibleModelAdapter(
            model_id=DEFAULT_MODEL_ID, base_url="https://offline.invalid",
            api_key="offline-test-token", max_attempts=1,
        )
        monkeypatch.setattr(adapter, "_request", lambda system, messages: {
            "id": "offline-response", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"type": "final", "output": {
                "search_terms": ["agent tools"],
                "topic_rules": [{"topic": "agent tools", "terms": ["agent tools"], "required_any": []}],
            }})}],
        })
        runtime.model_adapter = adapter
    else:
        runtime.model_adapter = ScriptedModelAdapter([
            {"type": "final", "output": {}}, {"type": "final", "output": {}},
        ])
    monkeypatch.setattr(runtime, "_news_project_scope_issue", lambda message: "")
    monkeypatch.setattr(runtime, "_news_workflow_answer", lambda *args: "Report result.")
    report_request = {"schema_version": "report_request.v1", "question": "Public news"}
    discovery_plan = {"schema_version": "discovery_plan.v1", "search_terms": ["agent tools"]}
    diagnostics = {"status": "partial", "missing_topics": ["papers"]}

    def report(company_id, *, query, model_adapter, on_stage):
        assert model_adapter.supports_discovery_planning is real_adapter
        messages = [{"role": "user", "content": "Offline structured output"}]
        if model_adapter.supports_discovery_planning:
            model_adapter.complete(messages, schema_name="report_discovery_plan.v1")
        model_adapter.complete(messages, schema_name="profile_aware_news_report.v1")
        return {
            "report_path": "report.md", "html_report_path": "report.html",
            "artifact_path": "report.json", "relevance_gate": "allow",
            "gate_reason": "Offline fixture", "data_mode": "TEST",
            "report_request": report_request, "discovery_plan": discovery_plan,
            "quality_diagnostics": diagnostics,
        }

    monkeypatch.setattr(runtime_module, "run_recent_news_report", report)
    runtime._run_recent_news_report("run-test", "Public news")
    calls = [step for step in runtime.store.steps if step["step_type"] == "model_call"]
    assert len(calls) == (2 if real_adapter else 1)
    analysis = calls[-1]
    assert analysis["input_payload"]["prompt_contract"]["contract_id"] == "profile_aware_news_report.v1"
    assert analysis["input_payload"]["prompt_contract"]["evidence_scope"] == "selected_events_and_wiki_facts"
    if real_adapter:
        assert calls[0]["input_payload"]["schema_name"] == "report_discovery_plan.v1"
        assert calls[0]["input_payload"]["prompt_contract"] == {
            "contract_id": "report_discovery_plan.v1",
            "evidence_scope": "wiki_and_query_to_public_search_terms",
        }
        assert calls[0]["title"] != analysis["title"]
    summary = next(step["output_payload"] for step in runtime.store.steps
                   if step["step_type"] == "workflow_summary")
    assert summary["report_request"] == report_request
    assert summary["discovery_plan"] == discovery_plan
    assert summary["quality_diagnostics"] == diagnostics


def _knowledge(monkeypatch, *, profile=None, facts=None, pages=None):
    knowledge = {
        "profile": profile or {"company_name": "My learning Wiki", "profile_type": "personal"},
        "facts": facts or [], "pages": pages or [],
    }
    monkeypatch.setattr(runtime_module, "load_company_knowledge", lambda company_id: knowledge)


@pytest.mark.parametrize("reference", ["我的项目", "这个项目", "本项目", "StudyForge 项目"])
def test_current_wiki_confirmed_project_resolves_subject(runtime, monkeypatch, reference):
    _knowledge(monkeypatch, facts=[{
        "value": "我的项目 StudyForge 是 Python 学习原型。",
        "source_kind": "user_confirmed",
    }])
    assert runtime._news_project_scope_issue(f"最新技术对{reference}有什么影响？") == ""


def test_user_confirmed_wiki_prose_resolves_current_project(runtime, monkeypatch):
    _knowledge(monkeypatch, pages=[{
        "meta": {"source": "user_confirmed"}, "facts": [],
        "body": "我的项目 StudyForge 使用 Python 和 SQLite。",
    }])
    assert runtime._news_project_scope_issue("最新政策对我的项目有什么影响？") == ""


def test_explicit_different_project_still_needs_its_own_wiki_context(runtime, monkeypatch):
    _knowledge(monkeypatch, facts=[{
        "value": "My project StudyForge is a Python prototype.", "source_kind": "user_confirmed",
    }])
    issue = runtime._news_project_scope_issue("最新论文对 OtherProject 项目有什么影响？")
    assert "My learning Wiki" in issue
    assert "已确认的画像与 Wiki 事实不足以绑定该项目" in issue
    assert "同花顺" not in issue and "company_001" not in issue


@pytest.mark.parametrize("source_kind", ["project_design", "analysis_inference"])
def test_unconfirmed_project_hints_do_not_bind_subject(runtime, monkeypatch, source_kind):
    _knowledge(monkeypatch, facts=[{"value": "项目 StudyForge 待填写。", "source_kind": source_kind}])
    assert runtime._news_project_scope_issue("最新技术对我的项目有什么影响？")


def test_legacy_company_coursepilot_scope_guard_is_preserved(runtime, monkeypatch):
    runtime.company_id = "company_001"
    _knowledge(monkeypatch, profile={"company_id": "company_001", "company_name": "同花顺"},
               facts=[{"value": "问财与 iFind 提供金融信息服务。", "source_kind": "public_disclosure"}])
    issue = runtime._news_project_scope_issue("CoursePilot 项目最近会受哪些新闻影响？")
    assert "你点名的项目" in issue
    assert "同花顺" in issue
    assert "没有调用新闻工作流" in issue


def test_project_name_in_current_profile_is_not_blocked(runtime, monkeypatch):
    _knowledge(monkeypatch, profile={"company_name": "My Wiki", "project_name": "CoursePilot"})
    assert runtime._news_project_scope_issue("最近技术对 CoursePilot 项目有什么影响？") == ""
