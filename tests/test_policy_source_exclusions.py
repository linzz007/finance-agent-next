"""Offline regressions for source exclusions, independent of company eligibility."""

from copy import deepcopy
from html import escape
import json
from pathlib import Path

import pytest

from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.stages import analyze_policy_applicability as stage
from policy_impact.stages import generate_weekly_report as report_stage
from policy_impact.stages.score_policy_impact import _score_applicability


TITLE = "人工智能拟人化互动服务管理暂行办法"
EXCLUSION = (
    "提供智能客服、知识问答、工作助手、学习教育、科学研究等服务，"
    "不涉及持续性的情感互动的，不适用本办法。"
)
SOURCE_URL = "https://official.example/source-exclusions"
ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "data/acceptance/persona_20260910_release/cases/H09/run_artifact.json"
)


@pytest.fixture(autouse=True)
def no_artifact_writes(monkeypatch):
    monkeypatch.setattr(stage, "write_json_artifact", lambda *args, **kwargs: None)


def _state(*, value="测试需求：公开金融资料问答和只读报表Agent原型", text=None):
    state = PolicyImpactState(company_id="offline_exclusions", run_id="offline_exclusions")
    state.date_range = {"date_from": "2026-03-14", "date_to": "2026-09-10"}
    state.current_user_message = "整理最近180天官方政策，保留测试画像边界"
    state.company_context_pack = {"pinned_facts": [{
        "fact_id": "custom.service",
        "value": value,
        "source_kind": "public_disclosure",
        "source_path": "wiki/custom.md",
        "importance": 5,
        "confidence": 1.0,
    }]}
    state.policy_documents = [{
        "policy_id": "source_policy",
        "title": TITLE,
        "text": text if text is not None else (
            "第二条\n向境内公众提供持续性情感互动服务，适用本办法。\n" + EXCLUSION
        ),
        "source_url": SOURCE_URL,
        "source_level": "L1",
    }]
    state.policy_matches = [{
        "policy_id": "source_policy",
        "company_evidence": deepcopy(state.company_context_pack["pinned_facts"]),
    }]
    return state


def _model(state, *, exclusion_conditions=None):
    return ScriptedModelAdapter([{"type": "final", "output": {"assessments": [{
        "policy_id": doc["policy_id"],
        "applicability": "not_applicable",
        "scope_summary": "企业已经满足排除条件",
        "exclusion_conditions": exclusion_conditions or [],
        "matched_company_fact_ids": ["custom.service", "ths_goal"],
        "policy_evidence_span_ids": [f"{doc['policy_id']}:scope:2"],
    } for doc in state.policy_documents]}}])


def _score_for_render(state):
    state.impact_assessments = [
        _score_applicability(state, item, f"impact_{index}")
        for index, item in enumerate(state.policy_applicability)
    ]


def _assert_source_bound(state, item, clause=EXCLUSION):
    assert clause in item["exclusion_conditions"]
    document = next(doc for doc in state.policy_documents if doc["policy_id"] == item["policy_id"])
    assert clause in document["text"]
    assert any(
        clause in span["text"]
        and span["verified"]
        and span["source_url"] == document["source_url"]
        and span["span_id"].startswith(f"{item['policy_id']}:scope:")
        for span in item["policy_evidence_spans"]
    )


@pytest.mark.parametrize("with_model", [False, True])
@pytest.mark.parametrize("value", [
    "测试需求：知识问答和工作助手原型，不提供持续性情感互动。",
    "已上线服务提供知识问答和工作助手。",
])
def test_source_exclusion_survives_without_proving_company_exclusion(with_model, value):
    state = _state(value=value)
    stage.run(state, model_adapter=_model(state) if with_model else None)
    item = state.policy_applicability[0]
    _assert_source_bound(state, item)
    assert item["applicability"] == "conditional"
    assert item["missing_company_facts"]
    assert "company_exclusion_confirmed" not in item["reason_codes"]
    assert "企业已经满足" not in item["scope_summary"]


def test_no_company_facts_does_not_erase_policy_exclusion():
    state = _state()
    state.company_context_pack = {}
    state.policy_matches = []
    stage.run(state)
    item = state.policy_applicability[0]
    _assert_source_bound(state, item)
    assert item["applicability"] == "insufficient_evidence"
    assert item["matched_company_fact_ids"] == []
    assert item["missing_company_facts"]


def test_confirmed_operational_exclusion_still_requires_real_business_evidence():
    state = _state(value="已上线服务仅提供知识问答和工作助手，不提供持续性情感互动。")
    stage.run(state)
    item = state.policy_applicability[0]
    _assert_source_bound(state, item)
    assert item["applicability"] == "not_applicable"
    assert "company_exclusion_confirmed" in item["reason_codes"]


def test_fallback_itself_retains_source_exclusion():
    state = _state()
    context = stage._build_context(state)[0]
    item = stage._fallback_assessment(state, context)
    assert EXCLUSION in item["exclusion_conditions"]
    assert item["applicability"] == "conditional"


def test_source_exclusion_and_span_survive_an_omitting_generic_model():
    state = _state(value="已上线服务提供知识问答和工作助手。")
    state.policy_documents[0]["title"] = "通用服务管理办法"
    context = stage._build_context(state)[0]
    context["policy_spans"] = [
        {"span_id": f"source_policy:scope:{index}", "text": f"第{index}条\n服务提供者应当建立管理制度。", "source_url": SOURCE_URL}
        for index in range(1, 4)
    ] + [{"span_id": "source_policy:scope:4", "text": EXCLUSION, "source_url": SOURCE_URL}]
    item = stage._normalize(state, context, {
        "applicability": "conditional",
        "exclusion_conditions": [],
        "policy_evidence_span_ids": ["source_policy:scope:1", "source_policy:scope:2"],
    })
    _assert_source_bound(state, item)
    assert item["applicability"] == "conditional"


def test_source_supplement_does_not_authorize_an_incomplete_model_exclusion_claim():
    state = _state(value="已上线服务提供知识问答和工作助手。")
    state.policy_documents[0]["title"] = "通用服务管理办法"
    context = stage._build_context(state)[0]
    item = stage._normalize(state, context, {
        "applicability": "not_applicable",
        "matched_company_fact_ids": ["custom.service"],
        "policy_evidence_span_ids": [span["span_id"] for span in context["policy_spans"]],
    })
    _assert_source_bound(state, item)
    assert item["applicability"] == "conditional"


def test_long_source_exclusion_is_not_truncated_or_duplicated():
    clause = "提供知识问答、工作助手等服务，" + "并满足原文所列服务边界，" * 18 + "不涉及持续性的情感互动的，不适用本办法。"
    assert len(clause) > 180
    state = _state(text="第二条\n" + clause + "\n" + clause)
    stage.run(state)
    item = state.policy_applicability[0]
    _assert_source_bound(state, item, clause)
    assert item["exclusion_conditions"].count(clause) == 1


@pytest.mark.parametrize("text,title", [
    ("第二条\n向境内公众提供持续性情感互动服务，适用本办法。", TITLE),
    ("现将办法印发给你们，请结合实际贯彻执行。附件全文请下载查看。", "关于印发人工智能科技伦理审查办法的通知"),
])
def test_absent_source_exclusion_is_not_invented(text, title):
    state = _state(text=text)
    state.policy_documents[0]["title"] = title
    stage.run(state, model_adapter=_model(state, exclusion_conditions=[EXCLUSION]))
    item = state.policy_applicability[0]
    assert item["exclusion_conditions"] == []
    assert item["applicability"] in {"conditional", "insufficient_evidence"}
    if "通知" in title:
        assert item["applicability"] == "insufficient_evidence"
        assert "source_page_without_attachment" in item["reason_codes"]
        assert any("附件全文" in fact for fact in item["missing_company_facts"])
        _score_for_render(state)
        assert "policy_attachment_missing" in report_stage._report_scope(state)["limitation_codes"]


@pytest.mark.parametrize("renderer", [report_stage._render_report, report_stage._render_html_report])
def test_report_displays_source_exclusion_separately_from_company_eligibility(renderer):
    state = _state()
    stage.run(state)
    _score_for_render(state)
    rendered = renderer(state)
    assert EXCLUSION in rendered or escape(EXCLUSION) in rendered
    assert "排除条件（政策原文）" in rendered
    assert "企业是否满足排除条件仍待核实" in rendered
    assert "- 排除条件：未记录" not in rendered
    assert "条件适用" in rendered
    assert SOURCE_URL in rendered


@pytest.mark.skipif(not ARCHIVE.exists(), reason="Local release H09 archive is unavailable")
@pytest.mark.parametrize("with_model", [False, True])
def test_release_h09_offline_replay_preserves_exclusion_and_missing_attachment(with_model):
    artifact = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    state = PolicyImpactState(company_id=artifact["company_id"], run_id="offline_h09_exclusions")
    for key in ("company_context_pack", "policy_documents", "policy_matches", "date_range", "current_user_message"):
        setattr(state, key, deepcopy(artifact[key]))
    stage.run(state, model_adapter=_model(state) if with_model else None)
    item = next(item for item in state.policy_applicability if item["policy_title"] == TITLE)
    _assert_source_bound(state, item)
    assert item["applicability"] == "conditional"
    assert "background_evidence_only" in item["reason_codes"]
    assert item["missing_company_facts"]
    notice = next(item for item in state.policy_applicability if "通知" in item["policy_title"])
    assert notice["exclusion_conditions"] == []
    assert notice["applicability"] == "insufficient_evidence"
    assert "source_page_without_attachment" in notice["reason_codes"]
    _score_for_render(state)
    for renderer in (report_stage._render_report, report_stage._render_html_report):
        rendered = renderer(state)
        assert EXCLUSION in rendered
        assert "企业是否满足排除条件仍待核实" in rendered
        assert "部分完成" in rendered
        assert "缺少附件全文" in rendered
