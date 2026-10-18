"""Independent offline acceptance boundaries; no live models or market sources."""

from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from policy_impact.company_wiki import loader
from policy_impact.domains.finance import workflow
from policy_impact.domains.finance.chat_gateway import FinanceChatGateway, requests_report
from policy_impact.domains.finance.metrics import compute_market_metrics
from policy_impact.domains.finance.profile import default_profile
from policy_impact.domains.finance.quality import FinanceQualityError, require_market_coverage
from policy_impact.domains.finance.render import publish_report
from policy_impact.domains.finance.runtime_adapter import execute
from policy_impact.domains.finance.store import FinanceStore
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelInvocationError,
    ScriptedModelAdapter,
)
from policy_impact.harness.tool_gateway import TOOL_REGISTRY
from policy_impact.memory.store import PolicyMemoryStore


COMPANY = "independent_finance_fixture"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    article_root = tmp_path / "articles"
    article_root.mkdir()
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(article_root))
    (article_root / "research_policy.md").write_text("# Offline test policy\n", encoding="utf-8")
    (article_root / "current_holdings.md").write_text(
        '```json\n{"as_of":"2026-10-01","currency":"CNY","approximate":true,'
        '"positions":[{"name":"test position","amount":0}]}\n```\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "offline-independent-fixture")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    root = tmp_path / "data" / "companies" / COMPANY
    (root / "wiki").mkdir(parents=True)
    (root / "company.yaml").write_text(
        json.dumps({"company_id": COMPANY, "company_name": "Synthetic review workspace"}),
        encoding="utf-8",
    )
    PolicyMemoryStore(COMPANY).seed_model_configs()

    def no_network(*args, **kwargs):
        pytest.fail("Independent review must not use a network or live model transport")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(AnthropicCompatibleModelAdapter, "_request", no_network)
    monkeypatch.setattr("policy_impact.domains.finance.acquisition.fetch_url", no_network)
    return root


def snapshot():
    return {
        "snapshot_id": "independent_synthetic_snapshot",
        "collected_at": "2020-01-03T12:00:00Z",
        "period": {"start": "2020-01-03", "end": "2020-01-03"},
        "series": [{
            "symbol": "spx", "name": "标普500", "provider": "offline-fixture",
            "unit": "点", "price_basis": "price_index",
            "source_url": "https://example.invalid/spx",
            "retrieved_at": "2020-01-03T12:00:00Z",
            "bars": [{"date": day, "open": price, "high": price,
                      "low": price, "close": price}
                     for day, price in [("2020-01-02", 100), ("2020-01-03", 102)]],
        }],
        "documents": [], "source_statuses": [], "warnings": [],
    }


@pytest.mark.parametrize("content,expected", [
    ("事实：上涨。可能解释：风险偏好。反证：仍有回落日。", True),
    ("事实：上涨。可能解释：风险偏好。待验证：利率数据。", True),
    ("事实：上涨。可能解释：风险偏好。验证：查看来源。", True),
    ("事实：上涨。可能解释：风险偏好。", False),
])
def test_finance_section_accepts_equivalent_counterevidence_labels(content, expected):
    assert workflow.has_required_analysis_parts(content) is expected


class OfflineAnalysisModel:
    def __init__(self, *, supported=True):
        self.supported = supported

    def complete(self, **kwargs):
        if kwargs["schema_name"] == "finance_verification.v1":
            output = {"supported": self.supported,
                      "issues": [] if self.supported else ["证据不支持解释"]}
        else:
            output = {
                "overview": "市场走强，原因仍需核实。",
                "sections": [{"title": "风险偏好",
                              "content": "事实是指数走强；可能解释是风险偏好变化；反证仍待验证。",
                              "evidence_refs": ["market_change_pct:spx"]}],
                "watch_next": ["验证原文与价格的时序"],
                "limitations": ["仅为合成测试资料"],
                "asset_assessments": [{"symbol": "spx", "verdict": "unidentified", "hypothesis": "原因未识别",
                    "evidence_refs": ["market_change_pct:spx"], "counterevidence": "当前只有上涨观测",
                    "missing_evidence": ["驱动变量"], "falsification_test": "需先获取同区间驱动数据，不预设因果"}],
            }
        return SimpleNamespace(payload={"output": output})


@pytest.fixture
def offline_workflow(monkeypatch, isolated_workspace):
    profile = {**default_profile(), "version": 1, "symbols": ["spx"],
               "default_session_mode": "explicit_period", "quality_policy": "strict"}
    request = {"period": snapshot()["period"], "symbols": ["spx"],
               "include_documents": False, "session_mode": "explicit_period",
               "question": "分析历史指数"}
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile", lambda cid: profile)
    monkeypatch.setattr(workflow, "parse_request", lambda query, value: {**request, "question": query})
    monkeypatch.setattr(workflow, "build_company_context_pack", lambda *a, **kw: {})
    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: snapshot())
    monkeypatch.setitem(TOOL_REGISTRY, "finance.metrics", lambda snapshot: compute_market_metrics(snapshot))
    from policy_impact.domains.finance.research_audit import audit
    monkeypatch.setitem(TOOL_REGISTRY, "finance.research_audit", audit)
    monkeypatch.setitem(TOOL_REGISTRY, "finance.publish", publish_report)
    return profile, request


def analyze(model=None):
    return workflow.run_finance_report(
        COMPANY, "分析历史指数", model_adapter=model or OfflineAnalysisModel(),
        allowed_tools=("finance.collect", "finance.metrics", "finance.research_audit"), publish_html=False,
    )


def chat(monkeypatch, actions, *, financial=True, message="分析今天美股行情"):
    monkeypatch.setattr("policy_impact.domains.finance.profile.load_profile",
                        lambda cid: default_profile() if financial else None)
    runtime = AgentRuntime(company_id=COMPANY, model_adapter=ScriptedModelAdapter(actions))
    session_id = runtime.store.create_chat_session(
        title="Independent offline review", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    result = runtime.run_turn(session_id=session_id, message=message, mode="chat",
                              model_id=DEFAULT_MODEL_ID, active_skill_id="")
    return result, runtime


def test_finance_analyze_empty_call_is_denied_and_audited(monkeypatch):
    result, runtime = chat(monkeypatch, [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Check current market data", "Report verified limits"],
        }},
        {"type": "tool_call", "tool_name": "finance.analyze", "arguments": {}},
    ])
    steps = runtime.store.list_agent_steps(result["run_id"])
    assert "tool_denied" in result["answer"]
    assert any(
        step["step_type"] == "research_failure_fallback"
        and step["output_payload"]["complete_conclusion"] is False
        for step in steps
    )


@pytest.mark.parametrize("question,expected", [
    ("分析今天行情，不生成报告", False),
    ("给我一份报告，不要用期货替代现货", True),
    ("别做报告，只分析今天美股行情", False),
    ("不要出具报告，只分析今天美股行情", False),
    ("不用出报告，只分析今天美股行情", False),
    ("不要给我报告，只分析今天美股行情", False),
], ids=["no-generate", "no-futures-is-not-no-report", "no-do", "no-issue", "no-output", "no-give"])
def test_report_intent_respects_publication_veto(question, expected):
    assert requests_report(question) is expected


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
@pytest.mark.parametrize("error_message", ["synthetic acquisition failure", ""], ids=["with-message", "empty-message"])
def test_unexpected_acquisition_failure_cannot_become_chat_success(monkeypatch, error_type, error_message):
    monkeypatch.setattr(workflow, "build_company_context_pack", lambda *a, **kw: {})
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise error_type(error_message)

    monkeypatch.setattr("policy_impact.domains.finance.acquisition.collect_finance_snapshot", fail)
    claimed = "今日美股上涨，已经核验数据，分析成功。"
    request = {"symbols": ["spx"], "start": "2020-01-03", "end": "2020-01-03"}
    result, runtime = chat(monkeypatch, [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Obtain market evidence", "Report verified limits"],
        }},
        {"type": "tool_call", "tool_name": "market.snapshot", "arguments": request},
        {"type": "tool_call", "tool_name": "market.snapshot", "arguments": request},
        {"type": "final", "output": {"answer": claimed}},
    ])
    steps = runtime.store.list_agent_steps(result["run_id"])
    assert len(calls) == 2
    assert "repeated_tool_failure" in result["answer"]
    assert claimed not in result["answer"]
    assert any(step["step_type"] == "research_failure_fallback" for step in steps)


def test_quality_failure_is_authoritative_over_model_final(monkeypatch):
    monkeypatch.setattr(workflow, "build_company_context_pack", lambda *a, **kw: {})
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise FinanceQualityError("synthetic missing core data")

    monkeypatch.setattr("policy_impact.domains.finance.acquisition.collect_finance_snapshot", fail)
    claimed = "虽然无法核实白银，但今天美股上涨且分析成功。"
    request = {"symbols": ["spx"], "start": "2020-01-03", "end": "2020-01-03"}
    result, runtime = chat(monkeypatch, [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Obtain market evidence", "Report verified limits"],
        }},
        {"type": "tool_call", "tool_name": "market.snapshot", "arguments": request},
        {"type": "tool_call", "tool_name": "market.snapshot", "arguments": request},
        {"type": "final", "output": {"answer": claimed}},
    ])
    steps = runtime.store.list_agent_steps(result["run_id"])
    assert len(calls) == 2
    assert "repeated_tool_failure" in result["answer"]
    assert claimed not in result["answer"]
    assert any(step["step_type"] == "research_failure_fallback" for step in steps)


@pytest.mark.parametrize("question", [
    "不要联网，只用刚才的数据解释今天美股行情",
    "不用刷新，沿用刚才的快照解释黄金变化",
], ids=["no-network", "reuse-no-refresh"])
def test_explicit_no_network_scope_denies_analysis_before_service(question):
    calls = []

    def analyze_spy(query):
        calls.append(query)
        raise FinanceQualityError("synthetic stop after observing unauthorized service entry")

    gateway = FinanceChatGateway(COMPANY, {"authorized_saved_id"},
                                 question=question, analyze=analyze_spy)
    try:
        gateway.call("chat_agent_loop", "finance.analyze")
    except (PermissionError, ValueError):
        pass
    assert calls == [], "Explicit no-network scope must be enforced before acquisition/model calls"


@pytest.mark.parametrize("question", [
    "禁止联网，只用现有资料解释今天黄金行情",
    "无需刷新，只用现有资料解释今天美股行情",
], ids=["forbidden-network-final", "no-refresh-needed-final"])
def test_no_network_answer_is_not_forced_to_call_forbidden_tool(question):
    gateway = FinanceChatGateway(COMPANY, set(), question=question, analyze=lambda query: None)
    issue = gateway.final_guard(question, {"answer": "本会话没有已保存的数据，无法核实；遵照要求不联网。"})
    assert issue is None, "A truthful no-data answer must not require a tool forbidden by the same request"


def test_nonfinance_workspace_cannot_call_analysis(monkeypatch):
    calls = []
    monkeypatch.setattr("policy_impact.domains.finance.runtime_adapter.execute",
                        lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ModelInvocationError, match="tool_denied"):
        chat(monkeypatch, [
            {"type": "tool_call", "tool_name": "finance.analyze", "arguments": {}},
        ], financial=False)
    assert not calls


def test_chat_json_and_saved_publication_preserve_authorized_snapshot(
    offline_workflow, isolated_workspace, monkeypatch,
):
    result = analyze()
    original = deepcopy(result["report"])
    assert result["artifacts"].keys() == {"run_artifact"}
    assert Path(result["artifacts"]["run_artifact"]).suffix == ".json"
    assert not list(isolated_workspace.rglob("*.html"))

    def forbid_acquisition(**kwargs):
        pytest.fail("Saved publication must not reacquire or reanalyze")

    monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", forbid_acquisition)
    monkeypatch.setattr(workflow, "run_finance_report", forbid_acquisition)
    published = workflow.publish_saved_analysis(
        COMPANY, original["report_id"], parent_run_id="independent_publication",
        allowed_tools=("finance.publish",),
    )
    assert Path(published["artifacts"]["html_report"]).is_file()
    assert published["report"]["report_id"] != original["report_id"]
    assert published["report"]["source_analysis_id"] == original["report_id"]
    for field in ("snapshot_id", "snapshot", "request", "analysis", "profile_snapshot"):
        assert published["report"][field] == original[field]
    assert FinanceStore(COMPANY).get_report(original["report_id"]) == original


def test_saved_publication_cannot_read_another_workspace(offline_workflow, monkeypatch):
    original = analyze()["report"]
    monkeypatch.setattr(FinanceStore, "get_report", lambda self, rid: original)
    with pytest.raises(FinanceQualityError, match="授权"):
        workflow.publish_saved_analysis("another_synthetic_company", original["report_id"],
                                        parent_run_id="test", allowed_tools=("finance.publish",))


def test_saved_publication_requires_publish_permission(offline_workflow):
    original = analyze()["report"]
    with pytest.raises(PermissionError):
        workflow.publish_saved_analysis(COMPANY, original["report_id"], parent_run_id="test",
                                        allowed_tools=())


@pytest.mark.parametrize("mutation", ["missing_core", "unsupported_analysis"])
def test_strict_chat_failure_leaves_no_deliverable(
    offline_workflow, isolated_workspace, monkeypatch, mutation,
):
    if mutation == "missing_core":
        monkeypatch.setitem(TOOL_REGISTRY, "finance.collect", lambda **kw: {**snapshot(), "series": []})
    with pytest.raises(FinanceQualityError):
        analyze(OfflineAnalysisModel(supported=mutation != "unsupported_analysis"))
    assert not list(isolated_workspace.rglob("*.html"))
    assert not list((isolated_workspace / "runs").glob("fin_*.json"))
    assert FinanceStore(COMPANY).list_reports() == []


def test_unverified_partial_chat_does_not_commit_or_crash(offline_workflow, isolated_workspace):
    profile, _ = offline_workflow
    profile["quality_policy"] = "allow_partial"
    with pytest.raises(FinanceQualityError):
        analyze(OfflineAnalysisModel(supported=False))
    assert FinanceStore(COMPANY).list_reports() == []
    assert not list((isolated_workspace / "runs").glob("fin_*.json"))


def test_current_strict_policy_cannot_publish_saved_partial_without_macro_evidence(
    offline_workflow, isolated_workspace,
):
    profile, request = offline_workflow
    profile["quality_policy"] = "allow_partial"
    request["include_documents"] = True
    result = analyze()
    assert result["report"]["data_status"] == "partial"
    assert result["report"]["documents"] == []
    profile["quality_policy"] = "strict"
    with pytest.raises(FinanceQualityError):
        workflow.publish_saved_analysis(COMPANY, result["report"]["report_id"],
                                        parent_run_id="strict_now", allowed_tools=("finance.publish",))
    assert not list(isolated_workspace.rglob("*.html"))


def test_saved_publication_does_not_relabel_partial_as_done(offline_workflow):
    profile, request = offline_workflow
    profile["quality_policy"] = "allow_partial"
    request["include_documents"] = True
    source = analyze()["report"]
    try:
        result = workflow.publish_saved_analysis(
            COMPANY, source["report_id"], parent_run_id="test", allowed_tools=("finance.publish",),
        )
    except FinanceQualityError:
        return
    assert result["metadata"]["data_status"] == result["report"]["data_status"]


def runtime_stub():
    steps = []
    return SimpleNamespace(
        company_id=COMPANY,
        store=SimpleNamespace(add_agent_step=lambda *a, **kw: steps.append(kw)),
        registry=SimpleNamespace(get_manifest=lambda name: SimpleNamespace(
            allowed_tools=("finance.collect", "finance.metrics", "finance.publish"))),
    )


def test_saved_publication_needs_conversation_authorization(monkeypatch):
    calls = []
    monkeypatch.setattr(workflow, "publish_saved_analysis", lambda *a, **kw: calls.append(a))
    result = execute(runtime_stub(), "run", "把刚才分析生成报告", DEFAULT_MODEL_ID)
    assert result["stop_reason"] == "quality_blocked"
    assert result["artifacts"] == {}
    assert not calls


@pytest.mark.parametrize("question", [
    "沿用刚才的分析，但改成最近一个月黄金，生成报告",
    "不要沿用刚才的快照，生成今天美股报告",
    "沿用刚才的数据，生成只看白银的报告",
    "把刚才的分析做成最近一个月的报告",
], ids=["changed-range-and-asset", "explicit-no-reuse", "silver-only-report", "monthly-report"])
def test_saved_publication_does_not_ignore_changed_or_negated_scope(monkeypatch, question):
    saved_calls = []
    response = {"answer": "fixture", "report": {}, "artifacts": {}, "metadata": {}}
    monkeypatch.setattr(workflow, "publish_saved_analysis",
                        lambda *a, **kw: saved_calls.append(a) or deepcopy(response))
    monkeypatch.setattr("policy_impact.domains.finance.runtime_adapter.run_finance_report",
                        lambda *a, **kw: deepcopy(response))
    execute(runtime_stub(), "run", question, DEFAULT_MODEL_ID, authorized_reports=("old_analysis",))
    assert not saved_calls, "Changed/negated scope must not blindly publish the old request"


@pytest.mark.parametrize("question", [
    "不用刷新，沿用刚才的分析生成报告",
    "不要重新获取数据，把刚才的分析生成报告",
], ids=["no-refresh-report", "no-reacquire-report"])
def test_no_refresh_report_does_not_enter_fresh_workflow(monkeypatch, question):
    fresh_calls = []
    response = {"answer": "fixture", "report": {}, "artifacts": {}, "metadata": {}}
    monkeypatch.setattr(workflow, "publish_saved_analysis", lambda *a, **kw: deepcopy(response))
    monkeypatch.setattr("policy_impact.domains.finance.runtime_adapter.run_finance_report",
                        lambda *a, **kw: fresh_calls.append(a) or deepcopy(response))
    execute(runtime_stub(), "run", question, DEFAULT_MODEL_ID, authorized_reports=("old_analysis",))
    assert not fresh_calls, "A publication request does not override an explicit no-refresh constraint"


@pytest.mark.parametrize("mutation", ["missing", "futures", "wrong_unit", "unverified_day"])
def test_hard_coverage_gate_rejects_invalid_core(mutation):
    request = {"symbols": ["xauusd"], "period": {"end": "2020-01-03"},
               "session_mode": "latest_completed"}
    series = {"symbol": "xauusd", "bars": [{}], "price_basis": "spot_price",
              "unit": "USD/troy_oz", "expected_session_date": "2020-01-02",
              "session_verified": True, "retrieved_at": "2020-01-03T00:00:00Z"}
    data = {"series": [series]}
    metrics = [{"symbol": "xauusd", "status": "ok", "change_pct": 1, "end_date": "2020-01-02"}]
    if mutation == "missing":
        data["series"] = []
    elif mutation == "futures":
        series["price_basis"] = "futures"
    elif mutation == "wrong_unit":
        series["unit"] = "CNY/g"
    else:
        series["session_verified"] = False
    with pytest.raises(FinanceQualityError):
        require_market_coverage(request, data, metrics)


def test_report_listing_filters_chat_analysis_before_limit(monkeypatch):
    from policy_impact.app.finance_api import register_finance_routes

    routes = {}

    def register(path, **_kwargs):
        def decorated(function):
            routes[path] = function
            return function
        return decorated

    app = SimpleNamespace(get=register, post=register)
    monkeypatch.setattr("policy_impact.app.finance_api.load_profile", lambda cid: default_profile())
    monkeypatch.setattr(FinanceStore, "list_reports", lambda self, limit: [
        {"report_id": "chat", "delivery_mode": "chat_analysis"},
        {"report_id": "html", "delivery_mode": "html_report"},
    ])
    register_finance_routes(app, RuntimeError)
    result = routes["/companies/{company_id}/workbench/finance/reports"](COMPANY, limit=1)
    assert result == {"reports": [{"report_id": "html", "delivery_mode": "html_report"}]}
