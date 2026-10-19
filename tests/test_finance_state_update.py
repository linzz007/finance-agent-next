"""F08/F09 acceptance for the explicit finance state-update protocol."""

from __future__ import annotations

import asyncio
import json

import pytest

from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_root import FinanceRootContext, FinanceRootState
from policy_impact.runtime.actions import EvidenceAddition, StateUpdate


class ContextCapabilities:
    def __init__(self):
        self.counter = 0

    async def store_context(self, _payload):
        self.counter += 1
        return f"context-{self.counter}"


def _state(**updates):
    return FinanceRootState(
        query="compare the article viewpoints",
        phase="research",
        execution={"material_refs": ["article-material"]},
        **updates,
    )


def test_observed_list_and_read_results_build_one_article_state():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state()
    context = FinanceRootContext(
        query=state.query,
        state=state,
        brief={},
        checkpoint=None,
        messages=[],
        observations=[{
            "tasks": [{
                "task_id": "list-task",
                "status": "succeeded",
                "result": {
                    "result_ref": "result-list",
                    "full_content": json.dumps({
                        "schema_version": "finance.h2.tool_result.v1",
                        "payload": {
                            "tool": "article.list",
                            "articles": [{"article_id": "article-1", "title": "A"}],
                        },
                    }),
                    "envelope": {"evidence_refs": []},
                },
            }]
        }],
    )
    listed, _ = asyncio.run(domain._consume_observations(context, capabilities))
    assert listed.article_states == [{
        "article_id": "article-1",
        "listed": True,
        "read_status": "unread",
        "use_status": "undecided",
    }]

    read_context = FinanceRootContext(
        query=state.query,
        state=listed,
        brief={},
        checkpoint=None,
        messages=[],
        observations=[{
            "tasks": [{
                "task_id": "read-task",
                "status": "succeeded",
                "result": {
                    "result_ref": "result-read",
                    "full_content": json.dumps({
                        "schema_version": "finance.h2.tool_result.v1",
                        "payload": {
                            "tool": "article.read",
                            "article": {"article_id": "article-1"},
                            "content": "conditional view",
                            "has_more": False,
                        },
                    }),
                    "envelope": {"evidence_refs": ["result-read"]},
                },
            }]
        }],
    )
    read, _ = asyncio.run(domain._consume_observations(read_context, capabilities))
    assert len(read.article_states) == 1
    assert read.article_states[0]["read_status"] == "complete"
    assert read.article_states[0]["material_ref"] == "context-2"


def test_segmented_read_requires_contiguous_full_article_range():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state(article_states=[{
        "article_id": "article-1",
        "listed": True,
        "read_status": "unread",
        "use_status": "undecided",
    }])

    def observation(result_ref, start_line, end_line, line_count, has_more):
        return [{
            "tasks": [{
                "task_id": f"read-{start_line}",
                "status": "succeeded",
                "result": {
                    "result_ref": result_ref,
                    "full_content": json.dumps({
                        "schema_version": "finance.h2.tool_result.v1",
                        "payload": {
                            "tool": "article.read",
                            "article": {"article_id": "article-1"},
                            "content": f"lines {start_line}-{end_line}",
                            "start_line": start_line,
                            "end_line": end_line,
                            "line_count": line_count,
                            "has_more": has_more,
                        },
                    }),
                    "envelope": {"evidence_refs": [result_ref]},
                },
            }]
        }]

    partial_context = FinanceRootContext(
        state=state,
        query=state.query,
        brief={},
        checkpoint=None,
        messages=[],
        observations=observation("result-tail", 6, 10, 10, False),
    )
    partial, _ = asyncio.run(domain._consume_observations(partial_context, capabilities))
    assert partial.article_states[0]["read_status"] == "partial"
    assert partial.article_states[0]["read_ranges"] == [[6, 10]]

    complete_context = FinanceRootContext(
        state=partial,
        query=partial.query,
        brief={},
        checkpoint=None,
        messages=[],
        observations=observation("result-head", 1, 5, 10, True),
    )
    complete, _ = asyncio.run(domain._consume_observations(complete_context, capabilities))
    assert complete.article_states[0]["read_status"] == "complete"
    assert complete.article_states[0]["read_ranges"] == [[1, 10]]


def test_state_update_plan_revision_is_resumable_and_must_increase():
    domain = FinanceH2Domain(object())
    state = _state(research_plan={"revision": 1, "goals": ["compare"]})
    update = StateUpdate(research_plan={"revision": 2, "goals": ["compare", "verify"]})
    next_state, warnings = asyncio.run(domain._apply_state_update(state, update, object()))
    assert next_state.research_plan["revision"] == 2
    assert warnings == []

    stale, warnings = asyncio.run(domain._apply_state_update(next_state, update, object()))
    assert stale.research_plan["revision"] == 2
    assert "research_plan_revision_not_increasing" in warnings


def test_evidence_additions_are_run_bound_and_idempotent():
    domain = FinanceH2Domain(object())
    state = _state(evidence_ledger=[])
    addition = EvidenceAddition(
        claim_id="claim-1",
        evidence_ref="article-material",
        relation="supports",
        source_kind="article",
        scope="article lines 1-3",
        certainty="conditional",
    )
    update = StateUpdate(evidence_additions=[addition, addition])
    next_state, warnings = asyncio.run(domain._apply_state_update(state, update, object()))
    assert warnings == []
    assert len(next_state.evidence_ledger) == 1
    assert next_state.evidence_ledger[0]["relation"] == "supports"

    bad = update.model_copy(update={
        "evidence_additions": [addition.model_copy(update={"evidence_ref": "other-run-ref"})]
    })
    rejected, warnings = asyncio.run(domain._apply_state_update(next_state, bad, object()))
    assert len(rejected.evidence_ledger) == 1
    assert "evidence_ref_not_in_run:other-run-ref" in warnings


def test_model_inference_cannot_be_upgraded_to_direct_fact():
    with pytest.raises(ValueError, match="model_inference cannot be marked direct"):
        EvidenceAddition(
            claim_id="claim-1",
            evidence_ref="article-material",
            relation="supports",
            source_kind="model_inference",
            scope="derived conclusion",
            certainty="direct",
        )


def test_invalid_final_metadata_keeps_complete_answer_and_marks_metadata():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state(article_states=[{
        "article_id": "article-1",
        "listed": True,
        "read_status": "unread",
        "use_status": "undecided",
    }])
    update = StateUpdate(article_updates=[{
        "article_id": "not-listed",
        "relevance": "primary",
    }])
    patched, warnings = asyncio.run(domain._apply_state_update(state, update, capabilities))
    result = asyncio.run(domain._final(
        FinanceRootContext(state=patched, query=patched.query, brief={}, checkpoint=None,
                           messages=[], observations=[]),
        patched,
        {"answer": "完整答案仍然交付", "evidence_refs": []},
        capabilities,
        warnings,
    ))
    assert result.submission.content == "完整答案仍然交付"
    assert result.submission.status == "partial"
    assert result.state.delivery["metadata_status"] == "incomplete_metadata"
    assert result.state.article_states[0]["read_status"] == "unread"


def test_final_evidence_can_mark_only_read_article_as_used():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state(article_states=[
        {
            "article_id": "article-1",
            "listed": True,
            "read_status": "complete",
            "use_status": "undecided",
            "material_ref": "article-material",
        },
        {
            "article_id": "article-2",
            "listed": True,
            "read_status": "unread",
            "use_status": "undecided",
        },
    ])
    result = asyncio.run(domain._final(
        FinanceRootContext(state=state, query=state.query, brief={}, checkpoint=None,
                           messages=[], observations=[]),
        state,
        {"answer": "source-bound answer", "evidence_refs": ["article-material"]},
        capabilities,
        [],
    ))
    assert result.state.article_states[0]["use_status"] == "used"
    assert result.state.article_states[1]["use_status"] == "undecided"


def test_missing_state_update_is_visible_without_blocking_delivery():
    domain = FinanceH2Domain(object())
    state, warnings = asyncio.run(domain._apply_state_update(_state(), None, object()))
    assert warnings == []
    assert state.execution["metadata_status"] == "incomplete_metadata"
    assert "state_update_missing" in state.execution["state_update_warnings"]
