"""F08/F09 acceptance for the explicit finance state-update protocol."""

from __future__ import annotations

import asyncio
import json

import pytest

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_actions import parse_root_action
from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import (
    FinanceRootContext,
    FinanceRootExecutor,
    FinanceRootState,
)
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


@pytest.mark.parametrize("payload", [
    {"type": "tool_call", "tool_name": "article.read", "arguments": {}, "state_update": "bad"},
    {"type": "delegate", "role": "analyst", "task": "read", "state_update": "bad"},
    {"type": "final", "output": [], "state_update": "bad"},
    {"type": "final", "output": {"answer": "body"}, "unexpected": True, "state_update": "bad"},
    {"type": "final", "output": {"answer": float("nan")}, "state_update": "bad"},
    "not a public JSON action",
])
def test_final_delta_recovery_does_not_relax_other_action_contracts(payload):
    with pytest.raises(ValueError):
        parse_root_action(payload)


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
    assert read.article_states[0]["material_ref"] in read.execution["material_refs"]
    assert read.article_states[0]["material_ref"] not in listed.execution["material_refs"]
    assert read.execution["observation_refs"]
    assert "observations" not in read.execution


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


@pytest.mark.parametrize("range_count", [199, 200, 201, 230])
@pytest.mark.parametrize("reverse", [False, True])
def test_segmented_read_retains_all_intervals_before_filling_gaps(range_count, reverse):
    domain = FinanceH2Domain(object())
    intervals = [[line, line] for line in range(1, range_count * 2, 2)]
    current = {"read_ranges": []}
    for start, end in reversed(intervals) if reverse else intervals:
        previous = current["read_ranges"]
        current = {"read_ranges": domain._merge_read_ranges(current, start, end)}
        assert all(pair in current["read_ranges"] for pair in previous)
    assert current["read_ranges"] == intervals
    assert domain._merge_read_ranges(current, *intervals[0]) == intervals
    last = intervals[-1][1]
    assert not domain._read_is_complete({"start_line": last, "end_line": last,
                                         "line_count": last, "has_more": False}, current)
    assert domain._read_is_complete({"start_line": 2, "end_line": last - 1,
                                    "line_count": last, "has_more": True}, current)
    assert domain._merge_read_ranges(current, 2, last - 1) == [[1, last]]


def test_large_read_index_survives_state_restore_and_actual_gap_observation():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    intervals = [[line, line] for line in range(1, 402, 2)]
    original = _state(article_states=[{
        "article_id": "article-1", "listed": True, "current_hash": "version-same",
        "read_status": "partial", "read_ranges": intervals[:-1], "use_status": "undecided",
    }])

    async def observe(state, start, end):
        return (await domain._consume_observations(FinanceRootContext(
            state=state, query=state.query, brief={}, checkpoint=None, messages=[],
            observations=[{"tasks": [{
                "task_id": f"read-{start}", "status": "succeeded", "result": {
                    "full_content": json.dumps({"schema_version": "finance.h2.tool_result.v1", "payload": {
                        "tool": "article.read", "article": {"article_id": "article-1", "current_hash": "version-same"},
                        "content": "\n".join(f"Synthetic line {line}" for line in range(start, end + 1)),
                        "start_line": start, "end_line": end,
                        "line_count": 401, "has_more": end < 401,
                    }}), "envelope": {"evidence_refs": []},
                },
            }]}],
        ), capabilities))[0]

    partial = asyncio.run(observe(original, 401, 401))
    assert partial.article_states[0]["read_ranges"] == intervals
    restored = FinanceRootState.model_validate_json(json.dumps(FinanceRootExecutor._compact_state(partial)))
    assert restored.article_states[0]["read_ranges"][0] == [1, 1]
    complete = restored
    for start, end in ((2, 101), (102, 201), (202, 301), (302, 400)):
        complete = asyncio.run(observe(complete, start, end))
        assert complete.article_states[0]["read_ranges"][0][0] == 1
        if end < 400:
            assert complete.article_states[0]["read_status"] == "partial"
        complete = FinanceRootState.model_validate_json(json.dumps(FinanceRootExecutor._compact_state(complete)))
    assert complete.article_states[0]["read_status"] == "complete"
    assert complete.article_states[0]["read_ranges"] == [[1, 401]]
    assert original.article_states[0]["read_ranges"] == intervals[:-1]


@pytest.mark.parametrize("previous_status,previous_ranges", [
    ("partial", [[1, 5]]),
    ("complete", [[1, 10]]),
    ("failed", [[1, 5]]),
])
@pytest.mark.parametrize("old_hash,new_hash", [
    ("version-old", "version-new"),
    (None, "version-new"),
    ("version-old", None),
])
def test_article_read_progress_cannot_cross_source_versions(
    previous_status, previous_ranges, old_hash, new_hash,
):
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state(article_states=[{
        "article_id": "article-1", "listed": True,
        "current_hash": old_hash, "read_status": previous_status,
        "read_ranges": previous_ranges, "use_status": "undecided",
        "material_ref": "old-material", "material_refs": ["old-material"],
        "result_refs": ["old-result"],
    }])
    context = FinanceRootContext(
        query=state.query, state=state, brief={}, checkpoint=None, messages=[],
        observations=[{"tasks": [{
            "task_id": "read-task", "status": "succeeded", "result": {
                "result_ref": "new-result",
                "full_content": json.dumps({
                    "schema_version": "finance.h2.tool_result.v1",
                    "payload": {
                        "tool": "article.read",
                        "article": {"article_id": "article-1", "current_hash": new_hash},
                        "content": "new version tail", "start_line": 6, "end_line": 10,
                        "line_count": 10, "has_more": False,
                    },
                }),
                "envelope": {"evidence_refs": ["new-result"]},
            },
        }]}],
    )
    updated, _ = asyncio.run(domain._consume_observations(context, capabilities))
    article = updated.article_states[0]
    assert article["current_hash"] == new_hash
    assert article["read_status"] == "partial"
    assert article["read_ranges"] == [[6, 10]]
    assert "old-material" in article["material_refs"]
    assert "old-result" in article["result_refs"]
    assert "new-result" in article["result_refs"]
    assert updated.execution["metadata_status"] == "incomplete_metadata"
    assert "article_source_version_changed:article-1" in updated.execution["state_update_warnings"]
    assert state.article_states[0]["read_ranges"] == previous_ranges


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
@pytest.mark.parametrize("empty_progress", [{}, {"current_hash": None, "read_ranges": []}])
def test_first_successful_read_after_failure_is_not_a_version_change(domain_class, empty_progress):
    domain = domain_class(object())
    original_article = {
        "article_id": "article-1", "listed": True,
        "read_status": "failed", "use_status": "undecided", **empty_progress,
    }
    state = _state(article_states=[original_article])
    state.execution["state_update_warnings"] = ["previous_read_invalid_arguments"]
    context = FinanceRootContext(
        query=state.query, state=state, brief={}, checkpoint=None, messages=[],
        observations=[{"tasks": [{
            "task_id": "first-valid-read", "status": "succeeded", "result": {
                "result_ref": "first-result",
                "full_content": json.dumps({
                    "schema_version": "finance.h2.tool_result.v1",
                    "payload": {
                        "tool": "article.read",
                        "article": {"article_id": "article-1", "current_hash": "first-hash"},
                        "content": "first observed source", "start_line": 1, "end_line": 5,
                        "line_count": 10, "has_more": True,
                    },
                }),
                "envelope": {"evidence_refs": ["first-result"]},
            },
        }]}],
    )
    updated, _ = asyncio.run(domain._consume_observations(context, ContextCapabilities()))
    article = updated.article_states[0]
    assert article["current_hash"] == "first-hash"
    assert article["read_status"] == "partial"
    assert article["read_ranges"] == [[1, 5]]
    assert "first-result" in article["result_refs"]
    assert updated.execution["state_update_warnings"] == ["previous_read_invalid_arguments"]
    assert state.article_states == [original_article]
    assert state.execution["state_update_warnings"] == ["previous_read_invalid_arguments"]


@pytest.mark.parametrize("source_hash", ["version-same", None])
def test_article_read_progress_same_version_retains_contiguous_ranges(source_hash):
    domain = FinanceH2Domain(object())
    state = _state(article_states=[{
        "article_id": "article-1", "listed": True,
        "current_hash": source_hash, "read_status": "partial",
        "read_ranges": [[1, 5]], "use_status": "undecided",
    }])
    context = FinanceRootContext(
        query=state.query, state=state, brief={}, checkpoint=None, messages=[],
        observations=[{"tasks": [{
            "task_id": "read-task", "status": "succeeded", "result": {
                "full_content": json.dumps({
                    "schema_version": "finance.h2.tool_result.v1",
                    "payload": {
                        "tool": "article.read",
                        "article": {"article_id": "article-1", "current_hash": source_hash},
                        "content": "same version tail", "start_line": 6, "end_line": 10,
                        "line_count": 10, "has_more": False,
                    },
                }),
                "envelope": {"evidence_refs": []},
            },
        }]}],
    )
    updated, _ = asyncio.run(domain._consume_observations(context, ContextCapabilities()))
    assert updated.article_states[0]["read_status"] == "complete"
    assert updated.article_states[0]["read_ranges"] == [[1, 10]]
    assert "article_source_version_changed:article-1" not in updated.execution.get("state_update_warnings", [])


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


@pytest.mark.parametrize("domain_type", [FinanceH2Domain, FinanceH2ResearchDomain])
@pytest.mark.parametrize("change", ["contrary", "metadata", "foreign"])
def test_large_evidence_ledger_retains_history_and_opposing_bindings(domain_type, change):
    ledger = [EvidenceAddition(
        claim_id=f"claim-{index}", evidence_ref="article-material", relation="supports",
        source_kind="article", scope="synthetic source", certainty="conditional",
    ).model_dump(mode="json", exclude_none=True) for index in range(200 if change == "contrary" else 201)]
    state = _state(evidence_ledger=ledger)
    opposing = EvidenceAddition(
        claim_id="claim-0", evidence_ref="article-material", relation="contradicts",
        source_kind="article", scope="synthetic contrary condition", certainty="conditional",
    )
    if change == "contrary":
        update = StateUpdate(evidence_additions=[opposing, opposing])
    elif change == "metadata":
        update = StateUpdate(coverage={"gaps": ["External truth remains unverified."]})
    else:
        update = StateUpdate(evidence_additions=[opposing.model_copy(update={"evidence_ref": "foreign-ref"})])
    domain = domain_type(object())
    updated, warnings = asyncio.run(domain._apply_state_update(state, update, object()))
    expected = [*ledger, opposing.model_dump(mode="json", exclude_none=True)] if change == "contrary" else ledger
    assert updated.evidence_ledger == expected
    assert state.evidence_ledger == ledger
    assert warnings == (["evidence_ref_not_in_run:foreign-ref"] if change == "foreign" else [])
    assert FinanceRootExecutor._compact_state(updated)["evidence_ledger"] == expected
    repeated, _ = asyncio.run(domain._apply_state_update(updated, update, object()))
    assert repeated.evidence_ledger == expected


def test_large_evidence_ledger_keeps_explicit_compact_state_limit():
    ledger = [{
        "claim_id": f"claim-{index}", "evidence_ref": "article-material", "relation": "supports",
        "source_kind": "article", "scope": "synthetic source", "certainty": "conditional",
    } for index in range(1000)]
    domain = FinanceH2Domain(object())
    updated, _ = asyncio.run(domain._apply_state_update(
        _state(evidence_ledger=ledger), StateUpdate(review={"status": "unverified"}), object(),
    ))
    assert updated.evidence_ledger == ledger
    with pytest.raises(DispatchError, match="compact limit"):
        FinanceRootExecutor._compact_state(updated)


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


def test_final_submission_retains_complete_model_supplied_envelope():
    domain = FinanceH2Domain(object())
    capabilities = ContextCapabilities()
    state = _state()
    metadata = {name: [f"{name}-{index}" for index in range(31)]
                for name in ("key_points", "gaps", "warnings")}
    result = asyncio.run(domain._final(
        FinanceRootContext(state=state, query=state.query, brief={}, checkpoint=None,
                           messages=[], observations=[]),
        state, {"answer": "Complete synthetic answer.", "evidence_refs": [],
                **{key: [*values, None, 12, {}] for key, values in metadata.items()}},
        capabilities, [],
    ))
    assert result.submission.content == "Complete synthetic answer."
    for field, entries in metadata.items():
        assert getattr(result.submission, field) == entries


def test_missing_state_update_is_visible_without_blocking_delivery():
    domain = FinanceH2Domain(object())
    state, warnings = asyncio.run(domain._apply_state_update(_state(), None, object()))
    assert warnings == []
    assert state.execution["metadata_status"] == "incomplete_metadata"
    assert "state_update_missing" in state.execution["state_update_warnings"]
