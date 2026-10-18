"""Failure replay for public answer checkpoints; no private corpus or model calls."""

import json

import pytest

from policy_impact.domains.finance.answer_audit import AnswerAudit
from policy_impact.domains.finance.answer_candidates import AnswerCandidates
from policy_impact.domains.finance.answer_delivery import (
    audit_delivery_state,
    bounded_audit_answer,
    research_citation_issue,
)
from policy_impact.domains.finance.evidence import EvidenceLedger


def _journal(tmp_path):
    events = []
    return AnswerCandidates(tmp_path / "answers", lambda event, payload: events.append((event, payload))), events


def _bundle(excerpt="An observed conditional author view."):
    return {"evidence": [{"evidence_id": "evidence_one", "source_type": "author_article",
                          "excerpt": excerpt, "content_hash": "version-one"}]}


def test_checkpoint_is_immutable_and_does_not_accept_empty_revision(tmp_path):
    journal, events = _journal(tmp_path)
    original = {"answer": "A complete conditional analysis.", "review_targets": []}
    first = journal.capture(original, _bundle())
    original["answer"] = "Mutated caller body"
    assert journal.capture({"answer": ""}, _bundle()) is None
    second = journal.capture({"answer": "A complete revised analysis."}, _bundle())
    assert second["parent_version"] == first["candidate_version"]
    saved = json.loads((journal.directory / "candidate-0001.json").read_text(encoding="utf-8"))
    assert saved["output"]["answer"] == "A complete conditional analysis."
    assert first["output"]["answer"] == saved["output"]["answer"]
    assert len(events) == 2


def test_delivery_choice_does_not_create_a_fake_new_candidate(tmp_path):
    journal, _ = _journal(tmp_path)
    first = journal.capture({"answer": "Full draft stays unchanged."}, _bundle())
    repeated = journal.capture({"answer": "Full draft stays unchanged.",
                                "review_disposition": "deliver_with_limits",
                                "review_resolution_reason": "Disclose the unresolved review."}, _bundle())
    assert repeated["candidate_version"] == first["candidate_version"]


def test_write_failure_keeps_inline_analysis_without_advertising_saved_path(tmp_path, monkeypatch):
    journal, events = _journal(tmp_path)

    def fail_replace(*args):
        raise OSError("fixture disk failure")

    monkeypatch.setattr("policy_impact.domains.finance.answer_candidates.os.replace", fail_replace)
    candidate = journal.capture({"answer": "Useful complete analysis despite failed disk write."}, _bundle())
    assert candidate["persistence_status"] == "failed"
    assert "checkpoint_path" not in candidate
    output, _, selected = journal.select(_bundle(), lambda output: None)
    assert selected is candidate
    assert output["answer"].startswith("Useful complete analysis")
    assert events[0][1]["persistence_status"] == "failed"
    assert not list(journal.directory.glob("*.tmp"))


def test_invalid_newer_draft_does_not_destroy_eligible_older_candidate(tmp_path):
    journal, events = _journal(tmp_path)
    journal.capture({"answer": "Original safe draft."}, _bundle())
    journal.capture({"answer": "New draft contains a known deterministic math error."}, _bundle())
    output, _, selected = journal.select(
        _bundle(), lambda output: "known math error" if "math error" in output["answer"] else None,
    )
    assert output["answer"] == "Original safe draft."
    assert selected["candidate_version"] == 1
    assert events[-2][1]["selected"] is False
    assert events[-1][1]["selected"] is True


def test_source_change_invalidates_checkpoint_even_when_evidence_id_is_reused(tmp_path):
    journal, _ = _journal(tmp_path)
    journal.capture({"answer": "Analysis bound to the original source."}, _bundle())
    output, audit, selected = journal.select(_bundle("Corrected source text."), lambda output: None)
    assert (output, audit, selected) == ({}, None, None)


def test_refreshing_retrieval_time_alone_does_not_invalidate_source(tmp_path):
    journal, _ = _journal(tmp_path)
    bundle = _bundle()
    journal.capture({"answer": "Analysis bound to unchanged evidence."}, bundle)
    bundle["evidence"][0]["retrieved_at"] = "2026-10-17"
    assert journal.select(bundle, lambda output: None)[2] is not None


def test_rollback_cannot_erase_newer_review_issue_or_empty_targets(tmp_path):
    journal, _ = _journal(tmp_path)
    old = journal.capture({"answer": "Both authors guarantee gold gains."}, _bundle())
    newer = journal.capture({"answer": "Both authors guarantee gold gains. Invalid source citation."}, _bundle())
    journal.reviewed(newer, {"answer_audit_coverage": "complete", "issues": [{
        "span": "Both authors guarantee gold gains.", "reason": "The original author statement was conditional.",
        "action": "revise",
    }]})
    output, audit, selected = journal.select(
        _bundle(), lambda output: "invalid citation" if "Invalid" in output["answer"] else None,
    )
    assert selected is old
    assert len(audit["issues"]) == 1
    delivered, resolution = bounded_audit_answer(output["answer"], audit, _bundle(), preserve_candidate=True)
    assert output["answer"] in delivered
    assert "conditional" in delivered
    assert resolution == "disputed_full_draft"
    replacement = journal.capture({"answer": output["answer"], "review_targets": []}, _bundle())
    assert journal.unresolved(replacement)


def test_reassessed_same_semantic_target_can_resolve_issue_only_for_its_own_version(tmp_path):
    journal, _ = _journal(tmp_path)
    target = {"target_id": "one", "claim_or_question": "Is this condition preserved?"}
    old = journal.capture({"answer": "Gold will rise."}, _bundle())
    journal.reviewed(old, {"issues": [{"target": target, "reason": "Condition missing.", "action": "revise"}]})
    new = journal.capture({"answer": "Gold could rise if real yields fall."}, _bundle())
    journal.reviewed(new, {"answer_audit_coverage": "complete",
                          "checks": [{"target": {**target, "target_id": "renamed"}, "action": "retain"}]})
    assert journal.unresolved(new) == []
    assert journal.unresolved(old)


def test_completed_review_with_limitations_preserves_entire_table_without_false_incomplete_label():
    draft = "| Author | View |\n|---|---|\n| TT | Conditional gold view |"
    audit = {"answer_audit_coverage": "complete", "issues": [], "limitations": "Author attribution only."}
    delivered, resolution = bounded_audit_answer(draft, audit, {}, preserve_candidate=True)
    assert draft in delivered
    assert resolution == "review_complete"
    assert "审核范围未完成" not in delivered
    assert "Author attribution only" in delivered


def test_reviewed_but_inconclusive_is_not_fact_verified():
    audit = {"answer_audit_coverage": "complete", "issues": [], "checks": [{
        "status": "insufficient_evidence", "action": "retain", "target": {"type": "objective_fact"},
    }]}
    status = audit_delivery_state(audit)
    assert status["semantic_review"] == "complete"
    assert status["verification_status"] == "partial"


def test_attribution_and_reasoned_inference_are_not_independent_fact_verification():
    for result in ("viewpoint_faithful", "reasoned", "not_due"):
        assert audit_delivery_state({"answer_audit_coverage": "complete", "checks": [{
            "status": result, "action": "retain", "target": {"type": "author_view"},
        }]})["verification_status"] == "partial"


def test_structured_evidence_refs_field_name_is_not_a_fabricated_citation():
    ledger = EvidenceLedger()
    output = {"answer": "A complete source-limited answer.", "review_targets": [{"evidence_refs": []}]}
    assert research_citation_issue(output, [], ledger) is None
    output["review_targets"][0]["evidence_refs"] = ["evidence_fabricated"]
    assert "evidence_fabricated" in research_citation_issue(output, [], ledger)


@pytest.mark.parametrize("source_type", ["source_document", "market_bar_series"])
def test_observed_public_document_or_market_series_is_eligible_fact_evidence(source_type):
    ledger = EvidenceLedger()
    source = ledger.add_reference(
        source_type=source_type, provenance="retrieved_public_source",
        source_ref="https://example.com/data", source_url="https://example.com/data",
        excerpt="A dated public observation that supports the selected target.",
    )

    def complete(messages, schema_name):
        return {"checks": [{
            "target_id": "fact", "status": "supported", "action": "retain",
            "evidence_refs": [source.evidence_id], "reason": "The dated observation supports this fact.",
        }], "answer_audit_coverage": "complete"}

    audit = AnswerAudit(ledger, complete).call(
        "The dated observation supports this factual claim.", review_targets=[{
            "target_id": "fact", "type": "objective_fact", "scope": "external",
            "claim_or_question": "Does this public observation support the fact?",
            "review_question": "Check the date and metric scope against the observation.",
            "evidence_refs": [source.evidence_id],
        }],
    )
    assert audit["checks"][0]["fact_verified"] is True
    assert audit_delivery_state(audit)["verification_status"] == "verified"


def test_failed_review_cannot_clear_an_existing_semantic_objection(tmp_path):
    journal, _ = _journal(tmp_path)
    target = {"target_id": "one", "claim_or_question": "Is this condition preserved?"}
    original = journal.capture({"answer": "A conditional claim with unresolved scope."}, _bundle())
    journal.reviewed(original, {"issues": [{"target": target, "action": "qualify", "reason": "Missing condition."}]})
    journal.reviewed(original, {"answer_audit_coverage": "partial", "checks": [{"target": target, "action": "retain"}]})
    assert journal.unresolved(original)


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("historical_review_on_newer", [False, True])
def test_execution_failure_preserves_historical_dispute_and_classification_in_fallback(
    tmp_path, rollback, historical_review_on_newer,
):
    journal, _ = _journal(tmp_path)
    target = {"target_id": "comparison", "claim_or_question": "Are the author conditions preserved?"}
    draft = "Complete comparison.\n\n| Author | View |\n|---|---|\n| A | Conditional |"
    old = journal.capture({"answer": draft}, _bundle())
    newer = journal.capture({"answer": draft + "\nNewer revision."}, _bundle())
    reviewed = newer if historical_review_on_newer else old
    objection = {"target": target, "span": "The original disputed claim.", "action": "revise",
                 "status": "refuted", "reason": "The original article contradicts the unconditional conclusion."}
    journal.reviewed(reviewed, {"answer_audit_coverage": "complete", "issues": [objection],
                               "checks": [objection]})
    gap = {**target, "issue_kind": "review_execution_gap", "action": "qualify",
           "reason": "The later target audit did not return a valid result (model_transport)."}
    journal.reviewed(old, {"answer_audit_coverage": "failed", "review_status": "failed",
                           "issues": [gap], "checks": []})
    journal.reviewed(newer, {"answer_audit_coverage": "failed", "review_status": "failed",
                             "issues": [gap], "checks": []})
    if rollback:
        empty_selection = journal.capture({"answer": draft, "review_targets": []}, _bundle())
        journal.reviewed(empty_selection, {"answer_audit_coverage": "complete", "review_status": "not_requested",
                                           "review_targets": [], "checks": [], "issues": []})
    output, audit, selected = journal.select(
        _bundle(), lambda output: "fixture rejected revision" if rollback and output != old["output"] else None,
    )
    assert selected is (old if rollback else newer)
    assert objection in audit["issues"] and gap in audit["issues"]
    saved = json.loads((journal.directory / "review-0002.json").read_text(encoding="utf-8"))
    assert saved["audit"]["issues"][0]["issue_kind"] == "review_execution_gap"
    answer, resolution = bounded_audit_answer(output["answer"], audit, _bundle(), preserve_candidate=True)
    assert answer.endswith(output["answer"])
    assert resolution == "disputed_full_draft"
    assert "未完成目标 comparison" in answer
    assert "争议 1：The original disputed claim." in answer
    assert "争议 2" not in answer
    state = audit_delivery_state(audit)
    assert state["answer_audit_coverage"] == state["semantic_review"] == "failed"
    assert state["verification_status"] == "disputed"


def test_empty_target_selection_cannot_turn_historical_execution_gap_into_pass(tmp_path):
    journal, _ = _journal(tmp_path)
    draft = "Complete preserved draft.\n\n| A | B |\n|---|---|\n| X | Y |"
    old = journal.capture({"answer": draft}, _bundle())
    gap = {"target_id": "interrupted", "claim_or_question": "Compare the selected views.",
           "issue_kind": "review_execution_gap", "action": "qualify", "reason": "Review execution failed."}
    journal.reviewed(old, {"answer_audit_coverage": "failed", "checks": [], "issues": [gap]})
    new = journal.capture({"answer": draft, "review_targets": []}, _bundle())
    journal.reviewed(new, {"answer_audit_coverage": "complete", "review_status": "not_requested",
                          "checks": [], "issues": []})
    output, audit, selected = journal.select(_bundle(), lambda _: None)
    assert selected is new and audit["issues"] == [gap]
    answer, resolution = bounded_audit_answer(output["answer"], audit, _bundle(), preserve_candidate=True)
    assert answer.endswith(draft)
    assert resolution == "review_incomplete_full_draft"
    assert "未完成目标 interrupted" in answer and "争议" not in answer
    state = audit_delivery_state(audit)
    assert state["answer_audit_coverage"] == "complete"
    assert state["semantic_review"] == state["verification_status"] == "partial"
    assert gap["reason"] in state["open_gaps"]


def test_switching_answer_context_cannot_clear_unchanged_disputed_passage(tmp_path):
    journal, _ = _journal(tmp_path)
    disputed = "Gold is guaranteed to rise."
    background = "Gold could rise if real yields decline."
    answer = (f"{disputed}\n\nFirst unrelated paragraph.\n\nSecond unrelated paragraph."
              f"\n\nThird unrelated paragraph.\n\n{background}")
    target = {"target_id": "one", "claim_or_question": "Is the gold conclusion conditional?",
              "answer_context": [disputed]}
    old = journal.capture({"answer": answer, "review_targets": [target]}, _bundle())
    journal.reviewed(old, {"issues": [{"target": target, "span": disputed,
                                      "reason": "Missing condition.", "action": "revise"}]})
    narrowed = {**target, "answer_context": [background]}
    new = journal.capture({"answer": answer, "review_targets": [narrowed]}, _bundle())
    journal.reviewed(new, {"answer_audit_coverage": "complete",
                          "checks": [{"target": narrowed, "action": "retain"}]})
    assert journal.unresolved(new)


@pytest.mark.parametrize("failure", ["missing_article", "model_transport"])
def test_restored_inputs_and_successful_review_clear_flattened_operational_gap(tmp_path, failure):
    ledger = EvidenceLedger()
    first = ledger.add_article({"article_id": "first", "author": "First", "path": "first.md",
                                "line_start": 1, "line_end": 1, "content_hash": "first-version"},
                               excerpt="The first conditional view.")
    target = {"target_id": "compare", "type": "author_view", "scope": "cross_article",
              "claim_or_question": "Are both author views preserved?",
              "review_question": "Check both authors and their conditions.",
              "article_refs": ["first.md", "second.md"], "evidence_refs": [first.evidence_id]}
    answer = "The authors describe conditional views, not externally verified market outcomes."
    journal, _ = _journal(tmp_path)
    old = journal.capture({"answer": answer, "review_targets": [target]}, ledger.bundle())

    def add_second():
        return ledger.add_article({"article_id": "second", "author": "Second", "path": "second.md",
                                   "line_start": 1, "line_end": 1, "content_hash": "second-version"},
                                  excerpt="The second conditional view.")

    if failure == "model_transport":
        second = add_second()

    def interrupted(*args):
        raise ConnectionError("fixture review connection closed")

    failed = AnswerAudit(ledger, interrupted).call(answer, review_targets=[target])
    assert failed["issues"] and "target" not in failed["issues"][0]
    expected_gap = "missing_participant" if failure == "missing_article" else "model_transport"
    assert expected_gap in failed["coverage_detail"]["targets"][0]["gap_types"]
    journal.reviewed(old, failed)
    if failure == "missing_article":
        second = add_second()
    repaired = {**target, "evidence_refs": [first.evidence_id, second.evidence_id]}

    def complete(messages, schema_name):
        return {"checks": [{"target_id": "compare", "status": "viewpoint_faithful", "action": "retain",
                            "evidence_refs": repaired["evidence_refs"], "reason": "Both conditions are preserved."}],
                "answer_audit_coverage": "complete"}

    new = journal.capture({"answer": answer, "review_targets": [repaired]}, ledger.bundle())
    audit = AnswerAudit(ledger, complete).call(answer, review_targets=[repaired])
    assert audit["answer_audit_coverage"] == "complete", json.dumps(audit, ensure_ascii=False)
    journal.reviewed(new, audit)
    assert journal.unresolved(new) == []
    assert journal.unresolved(old)


def test_bounded_reassessment_can_close_objection_when_it_includes_disputed_passage(tmp_path):
    journal, _ = _journal(tmp_path)
    disputed = "The author expects gold to rise under certain conditions."
    answer = (f"## Author outlook\n\n{disputed}\n\nThe condition is lower real yields."
              "\n\n## Background\n\nUnrelated financial history.")
    target = {"target_id": "one", "claim_or_question": "Is the gold view properly qualified?",
              "answer_context": [disputed]}
    candidate = journal.capture({"answer": answer}, _bundle())
    journal.reviewed(candidate, {"issues": [{"target": target, "span": disputed, "action": "qualify",
                                            "reason": "Check the condition in adjacent context."}]})
    journal.reviewed(candidate, {"answer_audit_coverage": "complete",
                                "checks": [{"target": target, "action": "retain"}]})
    assert journal.unresolved(candidate) == []


def test_rewritten_objection_requires_full_context_when_original_passage_cannot_be_matched(tmp_path):
    journal, _ = _journal(tmp_path)
    target = {"target_id": "one", "claim_or_question": "Is the gold conclusion conditional?"}
    old = journal.capture({"answer": "Gold is guaranteed to rise."}, _bundle())
    journal.reviewed(old, {"issues": [{"target": target, "span": old["output"]["answer"],
                                      "reason": "Missing condition.", "action": "revise"}]})
    repaired_text = "Gold could rise if real yields decline."
    new = journal.capture({"answer": f"{repaired_text}\n\nOther discussion.\n\nFurther background."}, _bundle())
    journal.reviewed(new, {"answer_audit_coverage": "complete", "checks": [{
        "target": {**target, "answer_context": [repaired_text]}, "action": "retain",
    }]})
    assert journal.unresolved(new)
    journal.reviewed(new, {"answer_audit_coverage": "complete", "checks": [{"target": target, "action": "retain"}]})
    assert journal.unresolved(new) == []


def test_partial_batch_can_close_only_its_completed_targets_objections(tmp_path):
    journal, _ = _journal(tmp_path)
    candidate = journal.capture({"answer": "A bounded comparison of conditional author views."}, _bundle())
    first = {"target_id": "first", "claim_or_question": "Is the first author view preserved?"}
    second = {"target_id": "second", "claim_or_question": "Is the second author view preserved?"}
    journal.reviewed(candidate, {"issues": [
        {"target": target, "reason": "Target review failed.", "action": "qualify"}
        for target in (first, second)
    ]})
    journal.reviewed(candidate, {"answer_audit_coverage": "partial", "coverage_detail": {"targets": [
        {"target_id": "first", "checked": True, "gap_types": []},
        {"target_id": "second", "checked": False, "gap_types": ["model_transport"]},
    ]}, "checks": [{"target": target, "action": "retain"} for target in (first, second)]})
    remaining = journal.unresolved(candidate)
    assert len(remaining) == 1
    assert remaining[0]["target"]["target_id"] == "second"


def test_narrowing_participating_articles_does_not_clear_missing_participant_warning(tmp_path):
    journal, _ = _journal(tmp_path)
    target = {"target_id": "compare", "claim_or_question": "Are both author views preserved?",
              "article_refs": ["first.md", "second.md"]}
    old = journal.capture({"answer": "Both authors share this conditional view."}, _bundle())
    journal.reviewed(old, {"issues": [{**target, "reason": "Second article was not observed.", "action": "qualify"}]})
    new = journal.capture({"answer": old["output"]["answer"], "review_targets": []}, _bundle())
    journal.reviewed(new, {"answer_audit_coverage": "complete", "checks": [{
        "target": {**target, "article_refs": ["first.md"]}, "action": "retain",
    }]})
    assert journal.unresolved(new)
