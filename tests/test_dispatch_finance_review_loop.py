"""Offline end-to-end contracts for version-bound Analyst/Verifier reviews."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootContext, FinanceRootState
from policy_impact.dispatch.finance_rules import legacy_rules, load_rules
from policy_impact.runtime.actions import AgentAction
from policy_impact.runtime.research_contracts import AnalystResearchResult, ReviewReport

SOURCE_TEXT = "The author keeps the view conditional."
TARGET = {
    "target_id": "claim-conditional-view",
    "kind": "author_view",
    "statement": SOURCE_TEXT,
    "evidence_refs": ["material.alpha"],
    "conditions": ["Funding remains available."],
}
SCOPE = ["source_fidelity", "reasoning_conditions"]


def _source():
    return {
        "schema_version": "finance.h2.tool_result.v1",
        "payload": {
            "tool": "article.read",
            "article": {"article_id": "article.alpha", "author": "Alpha", "current_hash": "hash.alpha"},
            "content": SOURCE_TEXT,
            "start_line": 1,
            "end_line": 1,
            "line_count": 1,
            "has_more": False,
        },
    }


class ReviewCapabilities:
    tool_names = ("agent.spawn", "agent.wait", "agent.status", "result.get")

    def __init__(self, *, instruction_rules=None):
        self.instruction_rules = instruction_rules or load_rules().snapshot()
        self.artifacts = {"material.alpha": _source()}
        self.spawned = []
        self.spawn_task_ids = ["task.verifier.1", "task.verifier.2"]
        self.full_results = {}
        self.result_get_calls = []

    def context_accepts(self, _messages):
        return True

    async def load_context(self, ref):
        if ref not in self.artifacts:
            raise DispatchError("not_found")
        return json.dumps(self.artifacts[ref], ensure_ascii=False).encode("utf-8")

    async def store_context(self, payload):
        ref = f"artifact.review.{len(self.artifacts)}"
        self.artifacts[ref] = json.loads(json.dumps(payload, ensure_ascii=False))
        return ref

    async def tool(self, name, arguments):
        if name == "agent.spawn":
            self.spawned.append(arguments)
            return {"data": {"task_id": self.spawn_task_ids.pop(0), "status": "queued"}}
        assert name == "result.get"
        result_ref = arguments["result_ref"]
        content = self.full_results[result_ref]
        offset = arguments.get("offset", 0)
        end = min(offset + arguments.get("limit", 16000), len(content))
        response = {
            "result_ref": result_ref,
            "status": "partial",
            "metadata_status": "complete",
            "content": content[offset:end],
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "byte_length": len(content.encode("utf-8")),
            "has_more": end < len(content),
            "next_offset": end if end < len(content) else None,
            "evidence_refs": [],
            "warnings": [],
            "section_refs": {},
        }
        self.result_get_calls.append(response)
        return response


@pytest.mark.parametrize(("user", "expected_model"), [
    (
        {
            "role": "analyst",
            "role_version": "2.0.0",
            "output_contract": {"name": "finance.analyst_result", "version": "2"},
        },
        AnalystResearchResult,
    ),
    (
        {
            "role": "verifier",
            "output_contract": {"name": "finance.review_report", "version": "1"},
            "review_request": {"schema_version": "finance.review_request.v1"},
        },
        ReviewReport,
    ),
])
def test_decide_selects_final_contract_from_assembled_top_level_brief(monkeypatch, user, expected_model):
    selected = {}

    async def capture(_messages, _capabilities, **kwargs):
        selected.update(kwargs)
        return None, None

    monkeypatch.setattr(FinanceH2Domain, "_decide", staticmethod(capture))
    messages = [{"role": "user", "content": json.dumps(user)}]

    asyncio.run(FinanceH2ResearchDomain._decide(messages, ReviewCapabilities()))

    assert selected["final_output_model"] is expected_model


def _context(*, policy=None, state=None):
    policy = policy or {"mode": "required", "scope": SCOPE, "max_rounds": 2}
    brief = {
        "task_id": "task.analyst",
        "thread_id": "thread.analyst",
        "role": "analyst",
        "role_version": "2.0.0",
        "depth": 1,
        "objective": "Reconstruct the author's reasoning.",
        "success_criteria": ["Preserve conditions and bind claims to the article."],
        "output_contract": {"name": "finance.analyst_result", "version": "2"},
        "review_policy": policy,
        "input_refs": ["material.alpha"],
        "budget": {"policy": "fixed"},
    }
    state = state or FinanceRootState(
        query=brief["objective"],
        phase="research",
        execution={
            "material_refs": ["material.alpha"],
            "evidence_refs": ["material.alpha"],
            "source_material_refs": ["material.alpha"],
            "research_brief": {
                key: brief[key] for key in (
                    "task_id", "role", "role_version", "depth", "objective", "success_criteria",
                    "output_contract", "review_policy", "input_refs",
                )
            },
        },
    )
    return FinanceRootContext(brief["objective"], state, brief, None, [], [])


def _analyst_result(*, content=SOURCE_TEXT, decision="request", reason=None, dispositions=()):
    return {
        "schema_version": "finance.analyst_result.v2",
        "content": content,
        "evidence_links": [{"text": SOURCE_TEXT, "evidence_refs": ["material.alpha"]}],
        "material_disposition": [{
            "material_ref": "material.alpha", "decision": "considered", "reason": "The assigned article was read.",
        }],
        "gaps": [],
        "limitations": [],
        "review_targets": [TARGET],
        "review_decision": decision,
        "review_decision_reason": reason,
        "review_dispositions": list(dispositions),
    }


def _root_context():
    state = FinanceRootState(
        query="Explain Alpha's article.", phase="research",
        execution={"material_refs": ["material.alpha"], "evidence_refs": ["material.alpha"]},
    )
    return FinanceRootContext(
        state.query, state, {"task_id": "task.root", "budget": {"policy": "fixed"}}, None, [], [],
    )


def _root_analyst_action():
    return TypeAdapter(AgentAction).validate_python({
        "type": "delegate",
        "role": "analyst",
        "role_version": "2.0.0",
        "research_task": {
            "schema_version": "finance.research_task.v1",
            "original_query": "Explain Alpha's article.",
            "objective": "Reconstruct the author's reasoning about funding.",
            "material_refs": ["material.alpha"],
            "requested_result_kind": "article_understanding",
            "completion_expectation": "Preserve the author's conditions and uncertainty.",
            "reading_request": {"material_ref": "material.alpha", "depth": "deep"},
            "focus": ["The link between funding and the conclusion."],
            "user_requirements": ["Identify any reasoning gap."],
            "review_policy": {"mode": "required", "scope": SCOPE, "max_rounds": 2},
        },
    }, strict=True)


def _report(request, *, findings=True, checked_target_ids=None):
    targets = request["targets"]
    report = {
        "schema_version": "finance.review_report.v1",
        "reviewed_draft_ref": request["draft_ref"],
        "reviewed_revision": request["draft_revision"],
        "checked_target_ids": checked_target_ids or [item["target_id"] for item in targets],
        "findings": ([{
            "finding_id": "r1-condition-loss" if request["round_number"] == 1 else "r2-no-new-finding",
            "target_id": targets[0]["target_id"],
            "category": "condition_loss",
            "severity": "material",
            "assessment": "partly_supported",
            "explanation": "The statement is supported only under its stated funding condition.",
            "evidence_refs": ["material.alpha"],
            "quotes": [{"evidence_ref": "material.alpha", "text": SOURCE_TEXT}],
            "suggested_change": "Keep the funding condition in the summary.",
            "unchecked_scope": [],
            "limitations": [],
        }] if findings else []),
        "checked_scope": request["scope"],
        "unchecked_scope": [],
        "limitations": [],
    }
    return ReviewReport.model_validate(report, strict=True).model_dump(mode="json")


async def _dispatch_and_return_report(domain, context, step, capabilities, *, findings):
    waiting = await domain._dispatch_review(context, step.state, capabilities)
    assert waiting.status == "waiting"
    task_id = waiting.wait.task_ids[0]
    request = capabilities.spawned[-1]["review_request"]
    wrapper = {
        "schema_version": "finance.h2.review_report.v1",
        "report": _report(request, findings=findings),
        "validation_status": "complete",
        "validation_warnings": [],
        "accepted_evidence_refs": ["material.alpha"] if findings else [],
    }
    observation = {"tasks": [{
        "task_id": task_id,
        "status": "succeeded",
        "result": {
            "result_ref": f"res.{task_id}",
            "full_content": json.dumps(wrapper, ensure_ascii=False),
            "envelope": {"evidence_refs": []},
        },
    }]}
    restored = FinanceRootState.model_validate(waiting.state.model_dump(mode="json"), strict=True)
    resumed = replace(context, state=restored, observations=[observation])
    state, _ = await domain._consume_observations(resumed, capabilities)
    return state, wrapper


def test_required_review_round_trip_carries_report_and_accumulates_finding_dispositions():
    domain = FinanceH2ResearchDomain(None)
    capabilities = ReviewCapabilities()
    context = _context()

    first = asyncio.run(domain._final(context, context.state, _analyst_result(), capabilities))
    assert first.status == "continuing"
    assert first.state.execution["review_workflow"]["status"] == "dispatch_pending"
    first_request = first.state.execution["review_workflow"]["prepared_spawn"]["review_request"]
    assert first_request["draft_revision"] == first_request["round_number"] == 1
    state_after_first_report, first_wrapper = asyncio.run(
        _dispatch_and_return_report(domain, context, first, capabilities, findings=True)
    )
    workflow = state_after_first_report.execution["review_workflow"]
    assert workflow["status"] == "report_ready"
    assert workflow["finding_ids"] == ["r1-condition-loss"]

    resumed_context = replace(context, state=state_after_first_report)
    messages = asyncio.run(domain._messages(context.query, state_after_first_report, [], capabilities))
    user = json.loads(messages[-1]["content"])
    assert user["review_workflow"]["status"] == "report_ready"
    assert any("finance.review_report.v1" in json.dumps(item) for item in user["materials"])

    accepted = [{"finding_id": "r1-condition-loss", "disposition": "accepted", "rationale": None}]
    revised = "The author keeps the view conditional. Funding remains available."
    second = asyncio.run(domain._final(
        resumed_context, state_after_first_report,
        _analyst_result(content=revised, decision="request", dispositions=accepted), capabilities,
    ))
    second_workflow = second.state.execution["review_workflow"]
    second_request = second_workflow["prepared_spawn"]["review_request"]
    assert second.status == "continuing"
    assert second_request["draft_revision"] == second_request["round_number"] == 2
    state_after_second_report, _ = asyncio.run(
        _dispatch_and_return_report(domain, resumed_context, second, capabilities, findings=False)
    )
    assert state_after_second_report.execution["review_workflow"]["finding_ids"] == ["r1-condition-loss"]

    final_context = replace(resumed_context, state=state_after_second_report)
    final = asyncio.run(domain._final(
        final_context, state_after_second_report,
        _analyst_result(content=revised, decision="skip", reason="The revised draft has completed review.",
                        dispositions=accepted),
        capabilities,
    ))
    saved = capabilities.artifacts[final.submission.full_ref]
    assert final.status == final.submission.status == "succeeded"
    assert saved["review"]["status"] == "reviewed_with_resolutions"
    assert saved["review"]["rounds"] == 2
    assert len(saved["review"]["report_refs"]) == 2
    assert saved["review"]["dispositions"] == accepted
    assert first_wrapper["report"]["checked_target_ids"] == [TARGET["target_id"]]


@pytest.mark.parametrize("task_status", [
    "pending_dispatch", "queued", "running", "waiting", "backpressured", "blocked",
])
def test_wait_timeout_preserves_inflight_review_until_its_result_arrives(task_status):
    async def run():
        domain = FinanceH2ResearchDomain(None)
        capabilities = ReviewCapabilities()
        context = _context()
        first = await domain._final(context, context.state, _analyst_result(), capabilities)
        waiting = await domain._dispatch_review(context, first.state, capabilities)
        task_id = waiting.wait.task_ids[0]
        original_workflow = dict(waiting.state.execution["review_workflow"])
        state = waiting.state
        for _ in range(2):
            resumed = replace(context, state=state, observations=[{
                "reason": "timeout", "tasks": [{"task_id": task_id, "status": task_status}],
            }])
            state, observed = await domain._consume_observations(resumed, capabilities)
            assert state.execution["review_workflow"] == original_workflow
            assert observed[-1]["status"] == task_status
            assert len(capabilities.spawned) == 1
            premature_final = await domain._final(
                replace(context, state=state), state, _analyst_result(), capabilities,
            )
            assert premature_final.status == "waiting"
            assert premature_final.wait.task_ids == [task_id]
            assert premature_final.state.execution["review_workflow"] == original_workflow
            assert len(capabilities.spawned) == 1

        request = capabilities.spawned[0]["review_request"]
        wrapper = {
            "schema_version": "finance.h2.review_report.v1",
            "report": _report(request, findings=True),
            "validation_status": "complete",
            "validation_warnings": [],
            "accepted_evidence_refs": ["material.alpha"],
        }
        resumed = replace(context, state=state, observations=[{"tasks": [{
            "task_id": task_id, "status": "succeeded", "result": {
                "result_ref": f"res.{task_id}",
                "full_content": json.dumps(wrapper), "envelope": {"evidence_refs": []},
            },
        }]}])
        state, _ = await domain._consume_observations(resumed, capabilities)
        workflow = state.execution["review_workflow"]
        assert workflow["status"] == "report_ready"
        assert workflow["reviewed_draft_ref"] == request["draft_ref"]
        assert workflow["reviewed_revision"] == request["draft_revision"]
        assert workflow["rounds"] == 1
        assert "failure_code" not in workflow
        assert len(capabilities.spawned) == 1

    asyncio.run(run())


def test_oversized_review_result_waits_for_paged_read_before_binding_to_draft():
    async def run():
        domain = FinanceH2ResearchDomain(None)
        capabilities = ReviewCapabilities()
        context = _context()
        first = await domain._final(context, context.state, _analyst_result(), capabilities)
        workflow = dict(first.state.execution["review_workflow"])
        workflow["failure_code"] = "invalid_report_binding"
        first_state = first.state.model_copy(update={"execution": {
            **first.state.execution, "review_workflow": workflow,
        }})
        first = first.model_copy(update={"state": first_state})
        waiting = await domain._dispatch_review(context, first.state, capabilities)
        task_id = waiting.wait.task_ids[0]
        result_ref = f"res.{task_id}"
        request = capabilities.spawned[-1]["review_request"]
        report = _report(request, findings=True)
        report["findings"][0]["explanation"] = "Expanded independent finding. " * 1000
        wrapper = {
            "schema_version": "finance.h2.review_report.v1",
            "report": report,
            "validation_status": "complete",
            "validation_warnings": [],
            "accepted_evidence_refs": ["material.alpha"],
        }
        full_content = json.dumps(wrapper, ensure_ascii=False)
        assert len(full_content.encode("utf-8")) > 16 * 1024
        capabilities.full_results[result_ref] = full_content
        observation = {"tasks": [{
            "task_id": task_id,
            "status": "partial",
            "result": {"result_ref": result_ref, "envelope": {"evidence_refs": []}},
        }]}
        resumed = replace(context, state=waiting.state, observations=[observation])
        state, _ = await domain._consume_observations(resumed, capabilities)
        pending = state.execution["review_workflow"]
        assert pending["status"] == "report_pending_read"
        assert pending["pending_task_id"] == task_id
        assert pending["pending_result_ref"] == result_ref
        assert pending["pending_draft_ref"] == request["draft_ref"]
        assert pending["pending_revision"] == request["draft_revision"]

        premature_final = await domain._final(
            replace(context, state=state), state, _analyst_result(), capabilities,
        )
        assert premature_final.status == "continuing"
        assert premature_final.state.execution["review_workflow"] == pending
        diagnostic_ref = premature_final.state.execution["action_observation_refs"][-1]
        diagnostic = capabilities.artifacts[diagnostic_ref]
        assert diagnostic["error"]["code"] == "review_report_unread"
        assert diagnostic["result_ref"] == result_ref
        assert len(capabilities.spawned) == 1
        state = premature_final.state

        offset = 0
        for _ in range(10):
            action = TypeAdapter(AgentAction).validate_python({
                "type": "tool_call",
                "tool_name": "result.get",
                "arguments": {"result_ref": result_ref, "view": "full", "offset": offset, "limit": 5000},
            }, strict=True)
            step = await domain._scheduler_action(context, state, action, capabilities)
            state = step.state
            page = capabilities.result_get_calls[-1]
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        else:
            pytest.fail("result.get did not finish reading the review report")

        bound = state.execution["review_workflow"]
        assert bound["status"] == "report_ready"
        assert bound["report_ref"] == result_ref
        assert bound["reviewed_draft_ref"] == request["draft_ref"]
        assert bound["reviewed_revision"] == request["draft_revision"]
        assert bound["report_refs"] == [result_ref]
        assert bound["finding_ids"] == ["r1-condition-loss"]
        assert "failure_code" not in bound
        assert bound["report_material_ref"] in state.execution["material_refs"]
        material = json.loads(await capabilities.load_context(bound["report_material_ref"]))
        assert material["payload"]["report"]["reviewed_revision"] == request["draft_revision"]
        assert len(capabilities.result_get_calls) > 1
    asyncio.run(run())


def test_required_policy_forces_review_even_when_analyst_says_skip():
    context = _context()
    output = _analyst_result(decision="skip", reason="I think self-review is enough.")
    step = asyncio.run(FinanceH2ResearchDomain(None)._final(
        context, context.state, output, ReviewCapabilities(),
    ))
    assert step.state.execution["review_workflow"]["status"] == "dispatch_pending"


def test_required_review_without_any_target_is_rejected_before_delivery():
    context = _context()
    output = _analyst_result(decision="skip", reason="No target was supplied.")
    output["review_targets"] = []
    with pytest.raises(DispatchError, match="needs at least one target"):
        asyncio.run(FinanceH2ResearchDomain(None)._final(
            context, context.state, output, ReviewCapabilities(),
        ))


def test_root_turns_research_task_into_profile_brief_and_legacy_runs_keep_old_contract():
    action = _root_analyst_action()
    context = _root_context()
    state = context.state
    domain = FinanceH2ResearchDomain(None)

    current = ReviewCapabilities()
    step = asyncio.run(domain._delegate(context, state, action, current))
    brief = current.spawned[-1]
    assert step.status == "waiting"
    assert brief["objective"] == action.research_task.objective
    assert "Original question: Explain Alpha's article." in brief["task_instructions"]
    assert "Focus: The link between funding and the conclusion." in brief["task_instructions"]
    assert brief["success_criteria"] == [
        "Preserve the author's conditions and uncertainty.", "Identify any reasoning gap.",
    ]
    assert brief["review_policy"] == action.research_task.review_policy.model_dump(mode="json", exclude_none=True)
    assert brief["output_contract"] == {"name": "finance.analyst_result", "version": "2"}
    assert "material.alpha" in brief["input_refs"]

    legacy = ReviewCapabilities(instruction_rules=legacy_rules().snapshot())
    asyncio.run(domain._delegate(context, state, action, legacy))
    old_brief = legacy.spawned[-1]
    assert old_brief["output_contract"] == {"name": "finance.research_result", "version": "1"}
    assert "review_policy" not in old_brief and "task_instructions" not in old_brief


@pytest.mark.parametrize("mode, expected_status, expected_step", [
    ("auto", "skipped_with_reason", "succeeded"),
    ("off", "not_requested", "succeeded"),
])
def test_auto_skip_and_off_policy_do_not_spawn_verifier(mode, expected_status, expected_step):
    policy = {"mode": mode, "scope": SCOPE, "max_rounds": 3}
    context = _context(policy=policy)
    capabilities = ReviewCapabilities()
    step = asyncio.run(FinanceH2ResearchDomain(None)._final(
        context, context.state,
        _analyst_result(decision="skip", reason="No material review risk was identified."), capabilities,
    ))
    saved = capabilities.artifacts[step.submission.full_ref]
    assert step.status == expected_step
    assert saved["review"]["status"] == expected_status
    assert capabilities.spawned == []


def test_review_report_with_incomplete_target_coverage_is_not_accepted_as_complete():
    context = _context()
    capabilities = ReviewCapabilities()
    request = {
        "schema_version": "finance.review_request.v1",
        "draft_ref": "draft.one",
        "draft_revision": 1,
        "round_number": 1,
        "scope": SCOPE,
        "targets": [TARGET, {**TARGET, "target_id": "claim-second", "statement": "A second claim."}],
    }
    report = _report(request, findings=False, checked_target_ids=[TARGET["target_id"]])
    state = context.state.model_copy(update={"execution": {
        **context.state.execution,
        "research_brief": {"role": "verifier", "review_request": request},
        "material_refs": ["material.alpha", "draft.one"],
    }})
    context = replace(context, state=state)
    capabilities.artifacts["draft.one"] = {"schema_version": "finance.h2.review_draft.v1"}
    step = asyncio.run(FinanceH2ResearchDomain(None)._final_review(context, state, report, capabilities))
    saved = capabilities.artifacts[step.submission.full_ref]
    assert step.status == "partial"
    assert saved["validation_status"] == "incomplete"
    assert saved["validation_warnings"] == ["review_target_coverage_mismatch"]


def test_review_report_cannot_bind_to_another_draft_revision():
    context = _context()
    request = {
        "schema_version": "finance.review_request.v1",
        "draft_ref": "draft.one",
        "draft_revision": 1,
        "round_number": 1,
        "scope": SCOPE,
        "targets": [TARGET],
    }
    report = _report(request, findings=False)
    report["reviewed_revision"] = 2
    state = context.state.model_copy(update={"execution": {
        **context.state.execution,
        "research_brief": {"role": "verifier", "review_request": request},
    }})
    with pytest.raises(DispatchError, match="draft binding"):
        asyncio.run(FinanceH2ResearchDomain(None)._final_review(
            replace(context, state=state), state, report, ReviewCapabilities(),
        ))


def test_verifier_failure_preserves_analyst_draft_and_is_disclosed():
    domain = FinanceH2ResearchDomain(None)
    capabilities = ReviewCapabilities()
    context = _context(policy={"mode": "auto", "scope": SCOPE, "max_rounds": 2})
    first = asyncio.run(domain._final(context, context.state, _analyst_result(), capabilities))
    waiting = asyncio.run(domain._dispatch_review(context, first.state, capabilities))
    task_id = waiting.wait.task_ids[0]
    failed_observation = {"tasks": [{"task_id": task_id, "status": "failed", "result": {}}]}
    resumed = replace(context, state=waiting.state, observations=[failed_observation])
    failed_state, _ = asyncio.run(domain._consume_observations(resumed, capabilities))
    assert failed_state.execution["review_workflow"]["status"] == "review_failed"
    assert failed_state.execution["review_workflow"]["failure_code"] == "failed"

    final = asyncio.run(domain._final(
        replace(context, state=failed_state), failed_state,
        _analyst_result(decision="skip", reason="Verifier failed; retain the source-bound analysis."),
        capabilities,
    ))
    saved = capabilities.artifacts[final.submission.full_ref]
    assert final.status == final.submission.status == "partial"
    assert saved["result"]["content"] == SOURCE_TEXT
    assert saved["review"]["status"] == "review_failed"
    assert saved["review"]["failure_code"] == "failed"


def test_review_round_limit_delivers_changed_draft_as_partial_without_another_spawn():
    domain = FinanceH2ResearchDomain(None)
    capabilities = ReviewCapabilities()
    context = _context(policy={"mode": "required", "scope": SCOPE, "max_rounds": 1})
    first = asyncio.run(domain._final(context, context.state, _analyst_result(), capabilities))
    state_after_report, _ = asyncio.run(
        _dispatch_and_return_report(domain, context, first, capabilities, findings=True)
    )
    dispositions = [{"finding_id": "r1-condition-loss", "disposition": "accepted", "rationale": None}]
    changed = "The author keeps the view conditional. Funding remains available."
    final = asyncio.run(domain._final(
        replace(context, state=state_after_report), state_after_report,
        _analyst_result(content=changed, decision="request", dispositions=dispositions), capabilities,
    ))
    saved = capabilities.artifacts[final.submission.full_ref]
    assert final.status == final.submission.status == "partial"
    assert saved["result"]["content"] == changed
    assert saved["review"]["status"] == "round_limit_reached"
    assert saved["review"]["rounds"] == 1
    assert len(capabilities.spawned) == 1
