"""Approval belongs to the reviewer; explicit user policy survives every dispatch."""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import ROOT_TOOLS
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_rules import MANIFEST_PATH, FinanceRules, load_rules
from policy_impact.dispatch.finance_tool_contracts import normalize_agent_spawn
from policy_impact.runtime.research_contracts import ReviewReportV2
from tests.test_dispatch_finance_review_loop import (
    SOURCE_TEXT,
    ReviewCapabilities,
    _analyst_result,
    _context,
    _dispatch_and_return_report,
    _report,
)
from tests.test_dispatch_finance_rules import _claim, _create, _refresh, _repository, _service
from tests.test_dispatch_finance_rules import rule_root as shared_rule_root
from tests.test_dispatch_general_profiles import _public_spawn, _spawn


@pytest.fixture
def rule_root(tmp_path):
    return shared_rule_root.__wrapped__(tmp_path)


def _old_rules():
    snapshot = load_rules().snapshot()
    manifest = json.loads(snapshot["assets"][MANIFEST_PATH]["content"])
    manifest["schema_version"] = "finance.h2.agent_rules.v4"
    snapshot["assets"][MANIFEST_PATH]["content"] = json.dumps(manifest)
    return FinanceRules.from_snapshot(_refresh(snapshot))


def _v2_report(request, *, findings=False):
    report = _report(request, findings=findings)
    return {**report, "schema_version": "finance.review_report.v2",
            "verdict": "revision_required" if findings else "passed",
            "verdict_reason": "Specific revision needed." if findings else "All requested checks completed.",
            "blocking_finding_ids": [item["finding_id"] for item in report["findings"]]}


@pytest.mark.parametrize("options", [None, {}, {"review_options": {"max_rounds": 5}}])
def test_missing_mode_cannot_silently_create_auto_task(options):
    arguments = _public_spawn("article", "analyst")
    if options is not None:
        arguments["profile_options"] = options
    with pytest.raises(ValidationError, match="review_mode"):
        normalize_agent_spawn(arguments, dispatch_key="dispatch", task_budget={"budget": {"policy": "fixed"}},
                              rules=load_rules())
    # Historical v4 Runs retain the optional mode, rather than changing on restore.
    old = normalize_agent_spawn(arguments, dispatch_key="dispatch", task_budget={"budget": {"policy": "fixed"}},
                                rules=_old_rules())
    assert "review_mode" not in old


def test_frozen_run_off_rejects_conflicting_second_dispatch_and_replay_change(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    service = _service(repo, rule_root)
    request = {"run_id": "policy-run", "session_ref": "policy", "query": "Analyze both without review.",
               "selected_profile": "fixture", "allowed_profiles": ["fixture"], "tools": list(ROOT_TOOLS),
               "dispatch_constraints": {"analyst_review_mode": "off"}}
    root = service.create_run(**request)
    lease = _claim(repo, root.task_id)
    first = repo.spawn(lease.caller(), _spawn("first", "analyst", review_mode="off"))["data"]
    with pytest.raises(DispatchError, match="conflicts"):
        repo.spawn(lease.caller(), _spawn("second", "analyst", review_mode="auto"))
    with pytest.raises(DispatchError, match="explicit"):
        repo.spawn(lease.caller(), _spawn("second", "analyst", review_mode=None))
    second = repo.spawn(lease.caller(), _spawn("second", "analyst", review_mode="off"))["data"]
    assert first["task_id"] != second["task_id"]
    restored_repo = _repository(tmp_path)
    child_lease = _claim(restored_repo, first["task_id"])
    child_input = restored_repo.execution_input(child_lease)
    assert child_input["brief"]["dispatch_constraints"] == {"analyst_review_mode": "off"}
    with pytest.raises(DispatchError, match="conflicts"):
        restored_repo.spawn(child_lease.caller(), _spawn("grandchild", "analyst", review_mode="required"))
    with pytest.raises(DispatchError):
        service.create_run(**{**request, "dispatch_constraints": {"analyst_review_mode": "required"}})
    with repo.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM h2_tasks").fetchone()[0] == 3


def test_required_review_gets_extended_window_and_short_default_is_rejected(tmp_path, rule_root):
    repo = _repository(tmp_path)
    repo.migrate()
    service = _service(repo, rule_root)
    root = _create(service, "review-windows")
    lease = _claim(repo, root.task_id)

    required = repo.spawn(lease.caller(), _spawn("required", "analyst", review_mode="required"))["data"]
    assert 45 * 60 * 1000 - 1000 <= required["allocated_budget"]["wall_time_ms"] <= 45 * 60 * 1000
    child_input = repo.execution_input(_claim(repo, required["task_id"]))
    assert child_input["brief"]["budget"]["required_review_child_window_ms"] == 45 * 60 * 1000

    with repo.db.connection(write=True) as conn:
        row = conn.execute("SELECT budget_ref FROM h2_runs WHERE run_id=?", (root.run_id,)).fetchone()
        conn.execute(
            "UPDATE h2_budgets SET deadline_at=deadline_at-2700000 WHERE budget_id=?", (row[0],),
        )
    before = repo.status(lease.caller(), [root.task_id])["tasks"]
    with pytest.raises(DispatchError, match="minimum default") as denied:
        repo.spawn(lease.caller(), _spawn("too-late", "analyst", review_mode="off"))
    assert denied.value.details["scope"] == "time"
    assert denied.value.details["required_ms"] == 10 * 60 * 1000
    assert 4 * 60 * 1000 < denied.value.details["available_ms"] <= 5 * 60 * 1000
    assert denied.value.details["window_kind"] == "default"
    assert repo.status(lease.caller(), [root.task_id])["tasks"] == before

    shortened = repo.spawn(lease.caller(), _spawn(
        "bounded", "analyst", review_mode="off", wall_time_ms=4 * 60 * 1000,
    ))["data"]
    assert 4 * 60 * 1000 - 10000 <= shortened["allocated_budget"]["wall_time_ms"] <= 4 * 60 * 1000


@pytest.mark.parametrize("change", ["false_approval", "missing_blocker", "unfinished_scope", "invented_blocker"])
def test_report_cannot_claim_passed_with_blocking_or_unfinished_checks(change):
    context = _context()
    caps = ReviewCapabilities()
    draft = asyncio.run(FinanceH2ResearchDomain(None)._final(context, context.state, _analyst_result(), caps))
    request = draft.state.execution["review_workflow"]["prepared_spawn"]["review_request"]
    report = _v2_report(request, findings=True)
    if change == "false_approval":
        report["verdict"] = "passed"
    elif change == "missing_blocker":
        report["blocking_finding_ids"] = []
    elif change == "unfinished_scope":
        report = _v2_report(request)
        report["unchecked_scope"] = ["Requested condition could not be checked."]
    else:
        report["blocking_finding_ids"].append("unknown")
    with pytest.raises(ValidationError):
        ReviewReportV2.model_validate(report, strict=True)


@pytest.mark.parametrize("change", ["reviewer_rejected", "changed_after_pass", "inconclusive"])
def test_required_analyst_skip_cannot_bypass_reviewer_gate(change):
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        first = await domain._final(context, context.state, _analyst_result(), caps)
        state, _ = await _dispatch_and_return_report(domain, context, first, caps,
                                                    findings=change == "reviewer_rejected")
        dispositions = ([{"finding_id": "r1-condition-loss", "disposition": "rejected_with_reason",
                          "rationale": "The analyst disagrees; the reviewer has not accepted that rationale."}]
                        if change == "reviewer_rejected" else [])
        if change == "inconclusive":
            state = state.model_copy(update={"execution": {**state.execution, "review_workflow": {
                **state.execution["review_workflow"], "verdict": "inconclusive"}}})
        content = SOURCE_TEXT + " Changed." if change == "changed_after_pass" else SOURCE_TEXT
        final = await domain._final(replace(context, state=state), state,
                                    _analyst_result(content=content, decision="skip", reason="Ready.",
                                                    dispositions=dispositions), caps)
        assert final.status == "continuing" and final.submission is None
        assert final.state.execution["review_workflow"]["prepared_spawn"]["review_request"]["round_number"] == 2
    asyncio.run(run())


def test_required_review_stops_when_the_same_draft_has_passed():
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        first = await domain._final(context, context.state, _analyst_result(), caps)
        state, _ = await _dispatch_and_return_report(domain, context, first, caps, findings=False)
        assert state.execution["review_workflow"]["verdict"] == "passed"

        final = await domain._final(
            replace(context, state=state), state,
            _analyst_result(decision="request", reason="Run the review once more."), caps,
        )

        assert final.status == "succeeded"
        assert final.submission is not None
        assert final.state.delivery["review"]["status"] == "passed"
        assert len(caps.spawned) == 1

    asyncio.run(run())


def test_required_review_reuses_a_prior_pass_for_an_identical_draft_after_intervening_revision():
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        first = await domain._final(context, context.state,
                                    _analyst_result(decision="request", reason="Initial draft."), caps)
        state_a, _ = await _dispatch_and_return_report(domain, context, first, caps, findings=False)
        approved_a = state_a.execution["review_workflow"]["approved_drafts"][0]

        revision_b = await domain._final(
            replace(context, state=state_a), state_a,
            _analyst_result(content=SOURCE_TEXT + " Funding remains available.",
                            decision="request", reason="Revise the wording."), caps,
        )
        state_b, _ = await _dispatch_and_return_report(domain, context, revision_b, caps, findings=False)

        final = await domain._final(
            replace(context, state=state_b), state_b,
            _analyst_result(decision="request", reason="Return to the previously reviewed draft."), caps,
        )

        assert final.status == "succeeded"
        review = final.state.delivery["review"]
        assert review["status"] == "passed"
        assert review["reused_approval"] is True
        assert review["draft_ref"] == approved_a["draft_ref"]
        assert review["draft_revision"] == approved_a["draft_revision"]
        assert review["report_ref"] == approved_a["report_ref"]
        assert len(caps.spawned) == 2

    asyncio.run(run())


@pytest.mark.parametrize("verdict", ["revision_required", "inconclusive"])
def test_new_adverse_report_revokes_a_cached_pass_for_the_same_draft(verdict):
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        first = await domain._final(context, context.state, _analyst_result(), caps)
        state, _ = await _dispatch_and_return_report(domain, context, first, caps, findings=False)
        workflow = json.loads(json.dumps(state.execution["review_workflow"]))
        assert workflow["approved_drafts"]

        request = first.state.execution["review_workflow"]["prepared_spawn"]["review_request"]
        report = _v2_report(request, findings=True)
        report["verdict"] = verdict
        ReviewReportV2.model_validate(report, strict=True)
        workflow.update(pending_draft_ref=request["draft_ref"], pending_revision=request["draft_revision"])
        workflow = domain._record_review_report(
            workflow, {"schema_version": "finance.h2.review_report.v1", "report": report,
                       "validation_status": "complete"},
            result_ref="res.new-adverse-report", material_ref="material.new-adverse-report",
        )
        assert workflow["approved_drafts"] == []
        state = state.model_copy(update={"execution": {**state.execution, "review_workflow": workflow}})
        final = await domain._final(
            replace(context, state=state), state,
            _analyst_result(decision="skip", reason="The old report was passed.", dispositions=[{
                "finding_id": report["findings"][0]["finding_id"], "disposition": "accepted",
            }]), caps,
        )
        assert final.status == "continuing" and final.submission is None
        assert final.state.execution["review_workflow"]["status"] == "dispatch_pending"
    asyncio.run(run())


def test_bad_finding_ids_are_feedback_then_correctable_with_a_five_correction_limit():
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        bad = _analyst_result(dispositions=[{"finding_id": "not-formally-accepted", "disposition": "accepted"}])
        state = context.state
        for count in range(1, 6):
            step = await domain._final(replace(context, state=state), state, bad, caps)
            assert step.status == "continuing" and step.submission is None
            state = step.state
            observation = caps.artifacts[state.execution["action_observation_refs"][-1]]
            assert observation["error"]["unknown_finding_ids"] == ["not-formally-accepted"]
            assert observation["correction_number"] == count
        with pytest.raises(DispatchError, match="correction limit"):
            await domain._final(replace(context, state=state), state, bad, caps)
        good = await domain._final(replace(context, state=state), state, _analyst_result(), caps)
        assert good.status == "continuing"
        assert good.state.execution["review_workflow"]["status"] == "dispatch_pending"
        # Existing v4 semantics remain terminal on the same domain error.
        with pytest.raises(DispatchError, match="every received"):
            await domain._final(context, context.state, bad, ReviewCapabilities(instruction_rules=_old_rules().snapshot()))
    asyncio.run(run())


def test_invalid_quote_is_returned_to_verifier_for_correction_before_report_acceptance():
    async def run():
        domain, caps, context = FinanceH2ResearchDomain(None), ReviewCapabilities(), _context()
        first = await domain._final(context, context.state, _analyst_result(), caps)
        request = first.state.execution["review_workflow"]["prepared_spawn"]["review_request"]
        state = context.state.model_copy(update={"execution": {**context.state.execution,
            "research_brief": {"role": "verifier", "review_request": request,
                               "output_contract": {"name": "finance.review_report", "version": "2"}},
            "material_refs": ["material.alpha", request["draft_ref"]]}})
        report = _v2_report(request, findings=True)
        report["findings"][0]["quotes"][0]["text"] = "This quotation does not occur in the article."
        failed = await domain._final(replace(context, state=state), state, report, caps)
        assert failed.status == "continuing" and failed.submission is None
        observation = caps.artifacts[failed.state.execution["action_observation_refs"][-1]]
        assert observation["error"]["validation_stage"] == "review.evidence"
        report["findings"][0]["quotes"][0]["text"] = SOURCE_TEXT
        good = await domain._final(replace(context, state=failed.state), failed.state, report, caps)
        assert good.status == "succeeded"
        saved = caps.artifacts[good.submission.full_ref]
        assert saved["report"]["verdict"] == "revision_required"
        assert saved["validation_status"] == "complete"
    asyncio.run(run())
