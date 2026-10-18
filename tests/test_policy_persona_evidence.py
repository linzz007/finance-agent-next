"""Offline regression tests for policy evidence from user-created Wikis."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.runtime.gate_catalog import StateGateDecisionSink, build_default_gate_registry
from policy_impact.runtime.gates import GateRunner
from policy_impact.stages import analyze_policy_applicability as stage
from policy_impact.stages import score_policy_impact
from policy_impact.stages import generate_weekly_report as report_stage
from policy_impact.stages import review_evidence_and_risk as review_stage


BASELINE = (
    Path(__file__).resolve().parents[1]
    / "data/acceptance/persona_20260909_baseline/cases/H09"
)
PERSONA_IDS = {
    "ths_identity", "ths_goal", "ths_stack", "ths_constraints", "ths_watchlist",
}
TITLES = [
    "人工智能拟人化互动服务管理暂行办法",
    "智能体规范应用与创新发展实施意见",
    "网络数据安全风险评估办法",
    "人工智能服务管理规定",
]
SCOPE = (
    "第二条\n向境内公众提供持续性情感互动服务，适用本办法。\n"
    "提供知识问答、工作助手等服务，不涉及持续性情感互动的，不适用本办法。\n"
    "第三条\n重要数据处理者应当开展风险评估。鼓励智能体研发应用。"
)


@pytest.fixture(autouse=True)
def no_artifact_writes(monkeypatch):
    monkeypatch.setattr(stage, "write_json_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(score_policy_impact, "write_json_artifact", lambda *args, **kwargs: None)


def _fact(fact_id="custom.identity", value="用户身份：AI研发人员", **kwargs):
    return {
        "fact_id": fact_id,
        "value": value,
        "domain": "user_defined",
        "source_path": "wiki/custom.md",
        "source_kind": "user_confirmed",
        "confidence": 1.0,
        "importance": 5,
        **kwargs,
    }


def _state(title=TITLES[0], facts=(), *, bucket="pinned_facts", matches=True):
    state = PolicyImpactState(company_id="offline_persona", run_id="offline_persona")
    state.company_context_pack = {bucket: deepcopy(list(facts))}
    state.policy_documents = [{
        "policy_id": "offline_policy",
        "title": title,
        "text": SCOPE,
        "source_url": "https://official.example/offline-policy",
        "source_level": "L1",
    }]
    if matches:
        state.policy_matches = [{
            "policy_id": "offline_policy",
            "company_evidence": deepcopy(list(facts)),
        }]
    return state


def _score(state):
    sink = StateGateDecisionSink(state)
    gate = GateEngine(
        runner=GateRunner(build_default_gate_registry(proof_resolver=sink), sink),
        run_id=state.run_id,
    )
    score_policy_impact.run(state, gate_engine=gate)


@pytest.mark.skipif(
    not all((BASELINE / name).exists() for name in ("wiki_before.json", "run_artifact.json")),
    reason="Local H09 acceptance archive is not present; synthetic persona regressions still run",
)
def test_h09_archived_wiki_background_survives_applicability_and_claim_gates():
    wiki = json.loads((BASELINE / "wiki_before.json").read_text(encoding="utf-8"))
    artifact = json.loads((BASELINE / "run_artifact.json").read_text(encoding="utf-8"))
    state = PolicyImpactState(company_id=wiki["company_id"], run_id="offline_h09")
    state.company_context_pack = deepcopy(artifact["company_context_pack"])
    state.policy_documents = deepcopy(artifact["policy_documents"])
    state.policy_matches = deepcopy(artifact["policy_matches"])
    state.date_range = deepcopy(artifact["date_range"])
    assert PERSONA_IDS <= {fact["fact_id"] for fact in wiki["facts"]}

    stage.run(state)
    _score(state)

    for context, assessment in zip(state.applicability_context, state.policy_applicability):
        assert PERSONA_IDS <= {fact["fact_id"] for fact in context["company_facts"]}
        assert all(fact["evidence_role"] == "background" for fact in context["company_facts"])
        assert PERSONA_IDS <= set(assessment["matched_company_fact_ids"])
        assert assessment["applicability"] in {"conditional", "insufficient_evidence"}
        assert assessment["missing_company_facts"]
        assert "company_product_is_work_assistant" not in assessment["reason_codes"]
        assert "agent_product_confirmed" not in assessment["reason_codes"]
        assert "投研" not in assessment["scope_summary"]
        assert "背景" in assessment["scope_summary"]
    for item in state.impact_assessments:
        assert PERSONA_IDS <= {fact["fact_id"] for fact in item["company_evidence"]}
        assert all(fact["evidence_role"] == "background" for fact in item["company_evidence"])
        assert item["impact_level"] not in {"P0", "P1"}
    decisions = [
        item for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]
    assert len(decisions) == 6
    assert all(item["decision"] == "pass" for item in decisions)


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("bucket", ["pinned_facts", "retrieved_company_facts", "all_company_facts"])
def test_user_background_does_not_depend_on_fact_id_domain_or_retrieval(title, bucket):
    facts = [
        _fact("arbitrary-1", "用户身份：AI研发人员"),
        _fact("arbitrary-2", "用户希望评估只读资料问答Agent测试原型"),
        _fact("arbitrary-3", "约束：不访问生产系统，不推测企业采用状态"),
    ]
    state = _state(title, facts, bucket=bucket, matches=False)

    stage.run(state)

    context = state.applicability_context[0]
    assert {fact["fact_id"] for fact in context["company_facts"]} == {
        fact["fact_id"] for fact in facts
    }
    assert all(fact["source_kind"] == "user_confirmed" for fact in context["company_facts"])
    assert all(fact["confidence"] == 1.0 for fact in context["company_facts"])
    assert state.policy_applicability[0]["applicability"] in {"conditional", "insufficient_evidence"}


@pytest.mark.parametrize("title", TITLES)
def test_empty_company_evidence_never_proves_exclusion_or_data_processing(title):
    state = _state(title)
    stage.run(state)
    item = state.policy_applicability[0]
    assert item["applicability"] == "insufficient_evidence"
    assert item["matched_company_fact_ids"] == []
    assert item["missing_company_facts"]
    assert "network_data_processing_confirmed" not in item["reason_codes"]
    assert not any("附件" in action for action in item["recommended_actions"])


@pytest.mark.parametrize("proposed_status", ["direct", "not_applicable"])
@pytest.mark.parametrize("fact_ids", [[], ["invented.product"]])
def test_model_decisive_claim_requires_resolved_company_facts(proposed_status, fact_ids):
    state = _state(TITLES[-1])
    context = stage._build_context(state)[0]
    item = stage._normalize(state, context, {
        "applicability": proposed_status,
        "matched_company_fact_ids": fact_ids,
        "scope_summary": "已经确认企业产品满足或排除适用范围",
        "reason_codes": ["company_product_confirmed"],
    })
    assert item["applicability"] == "insufficient_evidence"
    assert item["missing_company_facts"]
    assert "已经确认" not in item["scope_summary"]
    assert "company_product_confirmed" not in item["reason_codes"]


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("source_kind", ["user_confirmed", "project_design", "analysis_inference"])
def test_test_requirements_cannot_be_promoted_to_production_facts(title, source_kind):
    state = _state(title, [_fact(
        "ai_product.wencai",
        "测试需求：评估投研问答Agent原型，不代表公司生产产品，不提供持续性情感互动。",
        domain="ai_products", source_kind=source_kind,
    )])
    context = stage._build_context(state)[0]
    item = stage._normalize(state, context, {
        "applicability": "direct",
        "matched_company_fact_ids": ["ai_product.wencai"],
        "scope_summary": "企业已上线智能体产品",
    })
    assert item["matched_company_fact_ids"] == ["ai_product.wencai"]
    assert item["applicability"] in {"conditional", "insufficient_evidence"}
    assert "背景" in item["scope_summary"]
    assert "企业已上线" not in item["scope_summary"]
    assert item["missing_company_facts"]


def test_explicit_operational_exclusion_works_with_custom_fact_id():
    state = _state(facts=[_fact(
        "custom.live_service",
        "已上线服务仅提供知识问答和工作助手，不提供持续性情感互动服务。",
        source_kind="public_disclosure",
    )])
    stage.run(state)
    item = state.policy_applicability[0]
    assert item["applicability"] == "not_applicable"
    assert item["matched_company_fact_ids"] == ["custom.live_service"]
    assert "explicit_scope_exclusion" in item["reason_codes"]


def test_work_assistant_label_alone_does_not_prove_no_emotional_interaction():
    state = _state(facts=[_fact(
        "custom.live_service", "已上线服务提供知识问答和工作助手。",
        source_kind="public_disclosure",
    )])
    stage.run(state)
    assert state.policy_applicability[0]["applicability"] == "conditional"
    assert state.policy_applicability[0]["missing_company_facts"]


def test_placeholder_is_not_evidence_and_source_metadata_survives():
    state = _state(TITLES[-1], [
        _fact("placeholder", "Pending confirmation: record company business", confidence=0.0),
        _fact("custom.goal", "目标：评估Agent原型", source_kind="", source="user_confirmed"),
    ])
    stage.run(state)
    facts = state.applicability_context[0]["company_facts"]
    assert [fact["fact_id"] for fact in facts] == ["custom.goal"]
    assert facts[0]["source_kind"] == "user_confirmed"
    assert facts[0]["evidence_role"] == "background"


@pytest.mark.parametrize("title", TITLES)
def test_five_field_persona_survives_a_scripted_decisive_model_proposal(title):
    facts = [
        _fact("ths_identity", "验收角色：测试画像，不代表企业内部披露"),
        _fact("ths_goal", "测试需求：公开金融资料问答和只读报表Agent原型"),
        _fact("ths_stack", "测试原型使用Python、SQLite和LLM API，不是生产架构事实"),
        _fact("ths_constraints", "只读分析，不执行交易、不访问生产系统"),
        _fact("ths_watchlist", "关注Agent Runtime、工具调用、检索、Trace、Eval"),
    ]
    state = _state(title, facts)
    model = ScriptedModelAdapter([{"type": "final", "output": {"assessments": [{
        "policy_id": "offline_policy",
        "applicability": "not_applicable",
        "scope_summary": "企业已上线产品不适用本办法",
        "matched_company_fact_ids": ["ths_goal"],
    }]}}])
    stage.run(state, model_adapter=model)
    _score(state)

    item = state.policy_applicability[0]
    assert item["applicability"] == "conditional"
    assert set(item["matched_company_fact_ids"]) == PERSONA_IDS
    assert "背景" in item["scope_summary"]
    assert state.model_calls
    payload = json.loads(state.model_calls[0]["messages"][1]["content"])
    assert "evidence_boundary" in payload
    assert all(fact["evidence_role"] == "background" for fact in payload["policies"][0]["company_facts"])
    assert all(fact["evidence_role"] == "background" for fact in state.impact_assessments[0]["company_evidence"])


def test_user_wiki_named_preferences_still_provides_background():
    state = _state(facts=[_fact(
        "preference.custom_goal", "目标：评估Agent原型", source_path="wiki/preferences.md",
    )])
    stage.run(state)
    assert state.policy_applicability[0]["matched_company_fact_ids"] == ["preference.custom_goal"]


@pytest.mark.parametrize("extra", [{"confidence": 0.1}, {"evidence_role": "background"}])
def test_weak_or_explicit_background_evidence_cannot_prove_exclusion(extra):
    state = _state(facts=[_fact(
        "custom.service", "已上线服务仅提供知识问答，不提供持续性情感互动服务。",
        source_kind="public_disclosure", **extra,
    )])
    stage.run(state)
    assert state.policy_applicability[0]["applicability"] == "conditional"


def test_qualifier_beyond_prompt_excerpt_still_controls_evidence_role():
    state = _state(facts=[_fact(
        "custom.service",
        "知识问答和工作助手，不提供持续性情感互动服务。" + "功能说明。" * 100 + "以上仅为测试原型。",
        source_kind="public_disclosure",
    )])
    stage.run(state)
    assert state.applicability_context[0]["company_facts"][0]["evidence_role"] == "background"
    assert state.policy_applicability[0]["applicability"] == "conditional"


def test_exclusion_requires_a_policy_exclusion_span_and_no_conflicting_business_fact():
    fact = _fact(
        "custom.qa", "已上线服务仅提供知识问答，不提供持续性情感互动服务。",
        source_kind="public_disclosure",
    )
    state = _state(facts=[fact])
    state.policy_documents[0]["text"] = "第二条\n向境内公众提供持续性情感互动服务，适用本办法。"
    stage.run(state)
    assert state.policy_applicability[0]["applicability"] == "conditional"

    state = _state(facts=[fact, _fact(
        "custom.emotional", "另一项已上线服务向公众提供持续性情感互动服务。",
        source_kind="public_disclosure",
    )])
    stage.run(state)
    assert state.policy_applicability[0]["applicability"] == "conditional"


def _report_state():
    state = _state(facts=[
        _fact("custom.goal", "测试需求：评估公开金融资料问答和只读报表Agent原型"),
        _fact("custom.limit", "约束：不访问生产系统，不替企业下合规结论"),
    ])
    state.date_range = {"date_from": "2026-03-13", "date_to": "2026-09-09"}
    state.current_user_message = "整理最近180天官方政策，结合测试画像列待确认问题，不替公司下合规结论"
    stage.run(state)
    _score(state)
    return state


@pytest.mark.parametrize("renderer", [report_stage._render_report, report_stage._render_html_report])
def test_policy_report_uses_real_window_wiki_goal_and_discloses_failed_skeptic(renderer):
    state = _report_state()
    state.review_bundles.append({"role": "skeptic_rule_engine", "stop_reason": "model_error"})
    rendered = renderer(state)
    assert "本周" not in rendered
    assert "180天" in rendered
    assert "2026-03-13" in rendered and "2026-09-09" in rendered
    assert "当前目标" in rendered and "只读报表Agent原型" in rendered
    assert "部分完成" in rendered and "Skeptic" in rendered
    assert "确定性" in rendered and "未完成" in rendered
    assert "不访问生产系统" in rendered
    assert "背景证据" in rendered


def test_policy_report_scope_does_not_hide_missing_request_or_window_mismatch():
    state = _report_state()
    state.current_user_message = ""
    scope = report_stage._report_scope(state)
    assert scope["completion_status"] == "partial"
    assert "current_request_not_propagated" in scope["limitation_codes"]
    assert "只读报表Agent原型" in scope["current_goal"]

    state.current_user_message = "整理最近7天官方政策"
    scope = report_stage._report_scope(state)
    assert scope["completion_status"] == "partial"
    assert "requested_window_mismatch" in scope["limitation_codes"]
    assert "范围不一致" in report_stage._render_report(state)


def test_partial_scope_is_persisted_without_bypassing_publication_gate(monkeypatch):
    state = _report_state()
    sink = StateGateDecisionSink(state)
    gate = GateEngine(
        runner=GateRunner(build_default_gate_registry(proof_resolver=sink), sink),
        run_id=state.run_id,
    )
    monkeypatch.setattr(review_stage, "write_json_artifact", Mock())
    review_stage.run(state, gate_engine=gate)
    monkeypatch.setattr(report_stage, "PolicyMemoryStore", Mock())
    writer = Mock(return_value="offline-run-artifact.json")
    monkeypatch.setattr(report_stage, "write_json_artifact", writer)
    gateway = Mock()
    gateway.call.return_value = {"path": "offline-report"}

    report_stage.run(state, gateway=gateway, gate_engine=gate)

    artifact = writer.call_args.args[2]
    scope = artifact["quality_metrics"]["policy_report_scope"]
    assert scope["completion_status"] == "partial"
    assert scope["window_days"] == 180
    assert "skeptic_not_completed" in scope["limitation_codes"]
    assert scope["current_request"] == state.current_user_message
    assert scope["user_boundaries"]
    assert len(gateway.call.call_args_list) == 2
    assert all("部分完成" in call.kwargs["content"] for call in gateway.call.call_args_list)
    decisions = [
        item for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]
    assert [item["gate_id"] for item in decisions] == [
        "claim_schema", "evidence_binding", "review_verdict", "publication",
    ]
    assert all(item["decision"] == "pass" for item in decisions)
