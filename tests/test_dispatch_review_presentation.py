"""Runtime labels must stay outside the immutable analysis and survive paging."""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_review_status import (
    artifact_review_presentation,
    build_review_status,
)
from policy_impact.runtime.actions import AgentAction
from tests.test_dispatch_finance_review_loop import (
    SOURCE_TEXT,
    ReviewCapabilities,
    _analyst_result,
    _context,
    _dispatch_and_return_report,
    _root_context,
)


def result_wrapper(status="passed", **changes):
    review = {
        "status": status, "policy": {"mode": "required", "scope": ["source_fidelity"], "max_rounds": 5},
        "verdict": "passed", "rounds": 2, "draft_ref": "art_draft", "draft_revision": 2,
        "report_ref": "res_report", "blocking_finding_ids": [], **changes,
    }
    return {"schema_version": "finance.h2.analyst_result.v1", "result": {
        "content": "Snapshot still awaiting review.", "limitations": ["External facts remain unverified."]},
        "review": review, "review_status": {"label": "Model invented approval"}}


@pytest.mark.parametrize(("status", "label"), [
    ("passed", "核验通过"), ("not_requested", "未请求核验"),
    ("round_limit_reached", "核验达轮数上限，未通过"),
    ("review_failed", "核验未完成"), ("issues_open", "核验问题待解决"),
    ("reviewed_with_resolutions", "已审阅，旧版未记录通过判定"),
])
def test_status_labels_come_from_runtime_review_fields(status, label):
    wrapper = result_wrapper(status, verdict=None if status != "passed" else "passed")
    before = json.dumps(wrapper)
    projection = build_review_status(wrapper)
    assert projection["label"] == label
    assert projection["authority"] == "runtime"
    assert projection["scope_labels"] == ["原文忠实度"]
    assert "外部事实" in projection["scope_notice"]
    assert "快照" in projection["snapshot_notice"]
    assert json.dumps(wrapper) == before


@pytest.mark.parametrize("change", [
    {"verdict": None}, {"report_ref": None}, {"draft_ref": None}, {"draft_revision": 0},
    {"blocking_finding_ids": ["unresolved"]},
])
def test_malformed_pass_is_never_presented_as_approval(change):
    status = build_review_status(result_wrapper(**change))
    assert status["status"] == "invalid"
    assert status["label"] == "核验状态不可确认"


def test_raw_model_or_source_text_does_not_establish_a_review_decision():
    raw = json.dumps(result_wrapper())
    assert artifact_review_presentation(raw) == {}
    assert artifact_review_presentation(raw, trusted_result=True)["review_status"]["status"] == "passed"


def test_passed_snapshot_retains_exact_text_with_separate_runtime_status():
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        output = _analyst_result(content=SOURCE_TEXT + " This draft is awaiting review.")
        output["limitations"] = ["This draft is awaiting review.", "External facts remain unverified."]
        first = await domain._final(context, context.state, output, caps)
        draft_ref = first.state.execution["review_workflow"]["draft_ref"]
        draft_before = json.dumps(caps.artifacts[draft_ref], sort_keys=True)
        state, _ = await _dispatch_and_return_report(domain, context, first, caps, findings=False)
        final = await domain._final(replace(context, state=state), state, output, caps)
        assert final.status == "succeeded"
        saved = caps.artifacts[final.submission.full_ref]
        assert saved["result"] == caps.artifacts[draft_ref]["result"]
        assert saved["review_status"]["status"] == "passed"
        assert saved["review_status"]["draft_ref"] == draft_ref
        assert "awaiting review" in saved["result"]["content"]
        assert "External facts remain unverified." in final.submission.warnings
        assert draft_before == json.dumps(caps.artifacts[draft_ref], sort_keys=True)

        # Removing only process text still changes the reviewed body and must re-review.
        output["limitations"] = ["External facts remain unverified."]
        changed = await domain._final(replace(context, state=state), state, output, caps)
        assert changed.status == "continuing" and changed.submission is None
        assert changed.state.execution["review_workflow"]["status"] == "dispatch_pending"
    asyncio.run(run())


def test_parent_gets_status_only_after_complete_child_result_read_and_checkpoint_restore():
    async def run():
        domain, caps, context = FinanceH2Domain(None), ReviewCapabilities(), _root_context()
        caps.full_results["res_child"] = json.dumps(result_wrapper(), ensure_ascii=False)
        state, offset = context.state, 0
        for _ in range(30):
            action = TypeAdapter(AgentAction).validate_python({
                "type": "tool_call", "tool_name": "result.get", "arguments": {
                    "result_ref": "res_child", "view": "full", "offset": offset, "limit": 160}}, strict=True)
            step = await domain._scheduler_action(replace(context, state=state), state, action, caps)
            state = type(state).model_validate(step.state.model_dump(mode="json"), strict=True)
            page = caps.result_get_calls[-1]
            if not page["has_more"]:
                break
            assert "child_review_statuses" not in state.execution
            offset = page["next_offset"]
        else:
            pytest.fail("child result paging did not finish")
        assert len(caps.result_get_calls) > 1
        status = state.execution["child_review_statuses"]["res_child"]
        assert status["label"] == "核验通过"
        messages = await domain._messages(context.query, state, [], caps)
        user = json.loads(messages[1]["content"])
        assert user["child_review_statuses"] == [status]
        assert status["draft_ref"] == "art_draft"
    asyncio.run(run())


def test_wait_delivered_full_result_also_populates_parent_status():
    async def run():
        domain, caps, context = FinanceH2Domain(None), ReviewCapabilities(), _root_context()
        observation = {"tasks": [{"task_id": "child", "status": "succeeded", "result": {
            "result_ref": "res_child", "full_content": json.dumps(result_wrapper()),
            "envelope": {"evidence_refs": []}}}]}
        state, _ = await domain._consume_observations(replace(context, observations=[observation]), caps)
        assert state.execution["child_review_statuses"]["res_child"]["status"] == "passed"
    asyncio.run(run())
