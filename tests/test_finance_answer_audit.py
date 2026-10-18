import pytest
from pydantic import ValidationError

from policy_impact.domains.finance.answer_audit import (
    AnswerAudit,
    AnswerClaim,
    TargetAuditClaim,
    _answer_chunks,
    _audit_source,
    _is_official_publisher,
)
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.skills.schemas import FinanceReviewTarget, GeneralChatOutput


def _target(**overrides):
    payload = {
        "target_id": "target-1",
        "type": "model_inference",
        "scope": "cross_article",
        "relation_type": "agreement",
        "claim_or_question": "Do the two articles support a bounded common view?",
        "article_refs": ["article-1", "article-2"],
        "evidence_refs": [],
        "review_question": "Are both article positions actually comparable?",
        "external_check_needed": "undecided",
        "importance": "core",
    }
    payload.update(overrides)
    return payload


def test_review_target_scope_and_relation_fields_are_semantically_bound():
    target = FinanceReviewTarget.model_validate(_target())
    assert target.scope == "cross_article"
    assert target.relation_type == "agreement"
    with pytest.raises(ValidationError, match="relation_type is only valid"):
        FinanceReviewTarget.model_validate(_target(scope="article"))
    with pytest.raises(ValidationError, match="cross_article targets require article_refs"):
        FinanceReviewTarget.model_validate(_target(article_refs=[]))
    with pytest.raises(ValidationError, match="personal scope"):
        FinanceReviewTarget.model_validate(_target(scope="personal", relation_type=None))
    with pytest.raises(ValidationError, match="personal_context targets"):
        FinanceReviewTarget.model_validate(_target(type="personal_context", scope="article", relation_type=None))


def _evidence():
    ledger = EvidenceLedger()
    first = ledger.add_reference(
        source_type="public_web_page", provenance="web.open",
        source_ref="https://example.com/story", source_url="https://example.com/story",
        excerpt="A publisher reported a 15 billion loss.",
    )
    second = ledger.add_reference(
        source_type="public_web_page", provenance="web.open",
        source_ref="https://other.example/story", source_url="https://other.example/story",
        excerpt="A second publisher independently reported a 15 billion loss.",
    )
    return ledger, first.evidence_id, second.evidence_id


def test_audit_source_selects_cited_lines_from_long_web_page():
    ledger = EvidenceLedger()
    excerpt = "\n".join(f"{number}: Projection row {number} " + "x" * 80
                        for number in range(1, 100))
    source = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/projections.htm",
        source_url="https://www.federalreserve.gov/projections.htm",
        line_start=1, line_end=99, excerpt=excerpt,
    )
    selected = _audit_source(source.to_dict(), "Official Figure 2 L75-L79 shows the distribution.")
    assert selected["selection_scope"] == "claim_matched_passage"
    assert "75: Projection row 75" in selected["excerpt"]
    assert "1: Projection row 1" not in selected["excerpt"]
    assert not selected["excerpt_truncated"]


def test_selected_review_targets_audit_semantic_units_without_chunking_answer():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-gold-1", "author": "TT",
        "path": "C:/articles/TT/articles/gold.md",
        "published_at": "2026-09-30", "line_start": 1, "line_end": 3,
        "content_hash": "target-hash",
    }, excerpt="1: TT argues that falling real yields could support gold.\n2: The condition is not yet confirmed.")
    calls = []

    def complete(messages, schema_name):
        calls.append((schema_name, messages[1]["content"]))
        assert schema_name == "finance_target_answer_audit.v1"
        content = messages[1]["content"]
        assert "candidate_answer_lines" not in content
        assert content["candidate_answer"].startswith("A long answer")
        assert content["target"]["target_id"] == "view-1"
        return {"checks": [{
            "target_id": "view-1", "status": "viewpoint_faithful", "action": "retain",
            "evidence_refs": [ref.evidence_id],
            "reason": "The article states this conditional author viewpoint.",
            "suggested_revision": "", "answer_quote": "falling real yields could support gold",
        }], "coverage_complete": True}

    answer = "A long answer with several paragraphs and a conclusion."
    result = AnswerAudit(ledger, complete).call(
        answer,
        question="What is TT's view?",
        review_targets=[{
            "target_id": "view-1", "type": "author_view",
            "claim_or_question": "TT argues that falling real yields could support gold.",
            "article_refs": ["tt-gold-1"], "evidence_refs": [ref.evidence_id],
            "review_question": "Did TT actually express this conditional view?",
            "external_check_needed": "no", "importance": "core",
        }],
    )
    assert result["status"] == "accept"
    assert result["review_scope"] == "main_agent_selected_targets"
    assert result["reviewed_claim_count"] == 1
    assert len(calls) == 1


def test_selected_review_target_uses_normalize_contract_after_original_retries():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-gold-2", "author": "TT",
        "path": "C:/articles/TT/articles/gold-2.md", "published_at": "2026-10-01",
        "line_start": 1, "line_end": 1, "content_hash": "normalize-hash",
    }, excerpt="1: TT says real yields are an important condition for gold.")
    schemas = []

    def complete(messages, schema_name):
        schemas.append(schema_name)
        if schema_name == "finance_review.normalize.v1":
            return {"checks": [{
                "target_id": "view-2", "status": "viewpoint_faithful", "action": "retain",
                "evidence_refs": [ref.evidence_id],
                "reason": "The source directly attributes the condition to TT.",
                "suggested_revision": "", "answer_quote": "real yields are an important condition",
            }], "coverage_complete": True}
        return {"malformed": True}

    result = AnswerAudit(ledger, complete).call(
        "A complete answer that remains visible after format recovery.",
        review_targets=[{
            "target_id": "view-2", "type": "author_view",
            "claim_or_question": "TT says real yields are an important condition for gold.",
            "article_refs": ["tt-gold-2"], "evidence_refs": [ref.evidence_id],
            "review_question": "Does the article support the attribution?",
            "external_check_needed": "no", "importance": "core",
        }],
    )
    assert result["status"] == "accept"
    assert schemas[:3] == ["finance_target_answer_audit.v1"] * 3
    assert schemas[3] == "finance_review.normalize.v1"


def test_selected_review_target_normalizes_model_contract_failure():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-gold-3", "author": "TT",
        "path": "C:/articles/TT/articles/gold-3.md", "published_at": "2026-10-02",
        "line_start": 1, "line_end": 1, "content_hash": "normalize-error-hash",
    }, excerpt="1: TT says real yields are an important condition for gold.")
    schemas = []

    def complete(messages, schema_name):
        schemas.append(schema_name)
        if schema_name == "finance_review.normalize.v1":
            return {"checks": [{
                "target_id": "view-3", "status": "viewpoint_faithful", "action": "retain",
                "evidence_refs": [ref.evidence_id],
                "reason": "The source directly attributes the condition to TT.",
                "suggested_revision": "", "answer_quote": "real yields are an important condition",
            }], "coverage_complete": True}
        raise ModelInvocationError("model response missing JSON object output", structured_output=True)

    result = AnswerAudit(ledger, complete).call(
        "A complete answer that remains visible after model contract recovery.",
        review_targets=[{
            "target_id": "view-3", "type": "author_view",
            "claim_or_question": "TT says real yields are an important condition for gold.",
            "article_refs": ["tt-gold-3"], "evidence_refs": [ref.evidence_id],
            "review_question": "Does the article support the attribution?",
            "external_check_needed": "no", "importance": "core",
        }],
    )
    assert result["status"] == "accept"
    assert schemas[:3] == ["finance_target_answer_audit.v1"] * 3
    assert schemas[3] == "finance_review.normalize.v1"


def test_selected_review_target_rejects_unobserved_evidence_without_model_guessing():
    ledger = EvidenceLedger()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        raise AssertionError("invalid EvidenceRef must not be sent to the model")

    result = AnswerAudit(ledger, complete).call(
        "The answer remains available but its evidence is missing.",
        review_targets=[{
            "target_id": "missing-1", "type": "objective_fact",
            "claim_or_question": "The market fact is supported by a missing source.",
            "article_refs": [], "evidence_refs": ["evidence_aaaaaaaaaaaaaaaaaaaaaaaa"],
            "review_question": "Can the fact be checked from the observed evidence?",
            "external_check_needed": "yes", "importance": "core",
        }],
    )
    assert result["status"] == "revise"
    assert result["coverage_complete"] is False
    assert "issue_kind" not in result["issues"][0]
    assert calls == []


def _article_evidence(ledger, article_id, *, start=1, end=3, excerpt="1: A conditional view."):
    return ledger.add_article({
        "article_id": article_id, "author": article_id,
        "path": f"C:/articles/{article_id}.md", "published_at": "2026-10-01",
        "line_start": start, "line_end": end, "content_hash": "source-version-1",
    }, excerpt=excerpt)


def _retained_target(payload):
    return {"checks": [{
        "target_id": payload["target"]["target_id"],
        "status": "reasoned", "action": "retain",
        "evidence_refs": payload["allowed_evidence_ids"],
        "reason": "The supplied evidence supports this bounded interpretation.",
    }], "answer_audit_coverage": "complete"}


def test_target_audit_includes_missing_participant_without_expanding_explicit_article_range():
    ledger = EvidenceLedger()
    first = _article_evidence(ledger, "article-1")
    _article_evidence(ledger, "article-1", start=10, end=12, excerpt="10: Other discussion.")
    second = _article_evidence(ledger, "article-2")
    calls = []

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        calls.append(payload)
        return _retained_target(payload)

    result = AnswerAudit(ledger, complete).call(
        "Both authors present a conditional view, within these observed passages.",
        review_targets=[_target(evidence_refs=[first.evidence_id])],
    )
    assert result["status"] == "accept"
    assert calls[0]["allowed_evidence_ids"] == [first.evidence_id, second.evidence_id]


def test_target_audit_missing_participant_is_not_silently_accepted():
    ledger = EvidenceLedger()
    first = _article_evidence(ledger, "article-1")

    def complete(*args):
        raise AssertionError("Do not ask the reviewer to invent an unread participant")

    result = AnswerAudit(ledger, complete).call(
        "The two authors agree in the articles supplied for comparison.",
        review_targets=[_target(evidence_refs=[first.evidence_id])],
    )
    assert result["review_status"] == "failed"
    assert result["answer_audit_coverage"] == "failed"
    assert result["coverage_complete"] is False
    assert "article-2" in result["limitations"]


def test_target_audit_preserves_complete_observed_excerpt_beyond_legacy_cutoff():
    ledger = EvidenceLedger()
    excerpt = "1: Opening.\n" + "2: Background.\n" * 2200 + "3: Only if real yields fall."
    ref = _article_evidence(ledger, "article-1", excerpt=excerpt)
    calls = []

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        calls.append(payload)
        assert payload["observed_evidence"][0]["excerpt"] == excerpt
        assert payload["observed_evidence"][0]["source_excerpt_complete"] is True
        return _retained_target(payload)

    result = AnswerAudit(ledger, complete).call(
        "This article offers a conditional view, not an unconditional forecast.",
        review_targets=[_target(scope="article", relation_type=None,
                               article_refs=["article-1"], evidence_refs=[ref.evidence_id])],
    )
    assert result["status"] == "accept"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["answer", "question", "target", "personal_context", "same_id_source"])
def test_selected_target_cache_invalidates_on_actual_input_changes(change):
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        return _retained_target(messages[1]["content"])

    auditor = AnswerAudit(ledger, complete)
    answer = "The observed report describes a loss, subject to its source scope."
    kwargs = {"question": "What happened?", "personal_context": {"currency": "CNY"},
              "review_targets": [_target(scope="external", relation_type=None,
                                         article_refs=[], evidence_refs=[first])]}
    original = auditor.call(answer, **kwargs)
    original["checks"][0]["reason"] = "Caller mutation must not leak into the cache."
    repeat = auditor.call(answer, **kwargs)
    assert len(calls) == 1
    assert repeat["cache_hit"] is True
    assert "Caller mutation" not in repeat["checks"][0]["reason"]
    if change == "answer":
        answer += " This scope matters."
    elif change == "question":
        kwargs["question"] = "What is the reported loss?"
    elif change == "target":
        kwargs["review_targets"][0]["review_question"] = "Is this a reported number rather than a verified fact?"
    elif change == "personal_context":
        kwargs["personal_context"] = {"currency": "USD"}
    else:
        updated = ledger.add_reference(
            source_type="public_web_page", provenance="web.open",
            source_ref="https://example.com/story", source_url="https://example.com/story",
            excerpt="The same evidence ID now has a corrected observed excerpt.",
        )
        assert updated.evidence_id == first
    assert auditor.call(answer, **kwargs)["cache_hit"] is False
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["transport", "structure", "budget"])
def test_partial_target_audit_retains_success_and_only_retries_failed_target(failure):
    ledger, first, second = _evidence()
    calls = []
    recovered = False

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        target_id = payload["target"]["target_id"]
        calls.append((target_id, schema_name))
        if target_id == "target-2" and not recovered:
            if failure == "transport":
                raise ModelInvocationError("Remote connection ended before content")
            if failure == "budget":
                raise RuntimeError("shared logical model decision request budget exhausted")
            return {"wrong_shape": True}
        return _retained_target(payload)

    targets = [_target(target_id=f"target-{number}", scope="external", relation_type=None,
                       article_refs=[], evidence_refs=[ref])
               for number, ref in enumerate([first, second], 1)]
    auditor = AnswerAudit(ledger, complete)
    answer = "The complete analysis must remain available if a selected check fails."
    partial = auditor.call(answer, review_targets=targets)
    assert partial["answer_audit_coverage"] == "partial"
    assert [row["target_id"] for row in partial["checks"]] == ["target-1"]
    assert partial["review_status"] == "partial"
    assert partial["issues"][0]["issue_kind"] == "review_execution_gap"
    assert "issue_kind" not in partial["checks"][0]
    if failure != "structure":
        assert calls == [("target-1", "finance_target_answer_audit.v1"),
                         ("target-2", "finance_target_answer_audit.v1")]
    recovered = True
    final = auditor.call(answer, review_targets=targets)
    assert final["status"] == "accept"
    assert final["cached_target_ids"] == ["target-1"]
    assert [item[0] for item in calls].count("target-1") == 1
    assert calls[-1] == ("target-2", "finance_target_answer_audit.v1")


def test_invalid_target_cannot_be_marked_complete_when_another_target_passes():
    ledger, first, _ = _evidence()
    auditor = AnswerAudit(ledger, lambda messages, schema_name: _retained_target(messages[1]["content"]))
    result = auditor.call(
        "One selected target is valid but the other target has an invalid shape.",
        review_targets=[_target(scope="external", relation_type=None, article_refs=[], evidence_refs=[first]),
                        {"target_id": "bad"}],
    )
    assert result["status"] == "revise"
    assert result["answer_audit_coverage"] == "partial"
    assert result["coverage_complete"] is False


def test_invalid_only_target_does_not_claim_partial_audit_execution():
    def complete(*args):
        raise AssertionError("Invalid metadata must not become a semantic audit")

    result = AnswerAudit(EvidenceLedger(), complete).call(
        "The candidate is retained but no valid review target was supplied.",
        review_targets=[{"target_id": "invalid"}],
    )
    assert result["review_status"] == result["answer_audit_coverage"] == "failed"
    assert result["reviewed_claim_count"] == 0
    assert result["issues"][0]["issue_kind"] == "review_execution_gap"


def test_all_target_transport_failures_are_failed_and_not_cached():
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        raise ModelInvocationError("Remote connection ended before content")

    auditor = AnswerAudit(ledger, complete)
    target = _target(scope="external", relation_type=None, article_refs=[], evidence_refs=[first])
    for _ in range(2):
        result = auditor.call("The full analysis is retained even though this audit could not run.",
                              review_targets=[target])
        assert result["review_status"] == "failed"
        assert result["answer_audit_coverage"] == "failed"
        assert result["checks"] == []
        assert result["issues"][0]["issue_kind"] == "review_execution_gap"
    assert calls == ["finance_target_answer_audit.v1"] * 2


@pytest.mark.parametrize("failure", ["transport", "structure", "budget", "empty_check"])
def test_runtime_review_failure_is_an_execution_gap_not_a_semantic_verdict(failure):
    from policy_impact.domains.finance.answer_delivery import (
        audit_delivery_state,
        bounded_audit_answer,
    )

    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        if failure == "transport":
            raise ModelInvocationError("Offline fixture transport interruption")
        if failure == "budget":
            raise RuntimeError("shared logical model decision request budget exhausted")
        if failure == "structure":
            return {"wrong_shape": True}
        return {"checks": [], "answer_audit_coverage": "failed"}

    target = _target(scope="external", relation_type=None, article_refs=[], evidence_refs=[first])
    candidate = "Complete comparison.\n\n| Case | View |\n|---|---|\n| A | Conditional |"
    result = AnswerAudit(ledger, complete).call(candidate, review_targets=[target])
    assert result["checks"] == []
    assert result["answer_audit_coverage"] == result["review_status"] == "failed"
    issue = result["issues"][0]
    assert issue["issue_kind"] == "review_execution_gap"
    assert issue["target_id"] == target["target_id"]
    assert "status" not in issue
    if failure != "structure":
        assert calls == ["finance_target_answer_audit.v1"]
    answer, resolution = bounded_audit_answer(candidate, result, ledger.bundle(), preserve_candidate=True)
    assert resolution == "review_failed_full_draft"
    assert candidate in answer
    assert "审核未完成" in answer
    assert "争议" not in answer
    state = audit_delivery_state(result)
    assert state["answer_audit_coverage"] == state["semantic_review"] == state["verification_status"] == "failed"
    assert issue["reason"] in state["open_gaps"]


@pytest.mark.parametrize(("status", "action"), [
    ("refuted", "revise"), ("insufficient_evidence", "qualify"), ("reasoned", "revise"),
])
def test_valid_semantic_objection_is_never_tagged_as_an_execution_gap(status, action):
    from policy_impact.domains.finance.answer_delivery import (
        audit_delivery_state,
        bounded_audit_answer,
    )

    ledger, first, _ = _evidence()

    def complete(messages, schema_name):
        result = _retained_target(messages[1]["content"])
        result["checks"][0].update(status=status, action=action)
        return result

    candidate = "A complete comparison that still needs a substantive correction."
    audit = AnswerAudit(ledger, complete).call(candidate, review_targets=[_target(
        scope="external", relation_type=None, article_refs=[], evidence_refs=[first],
    )])
    assert audit["answer_audit_coverage"] == "complete"
    assert "issue_kind" not in audit["issues"][0]
    answer, resolution = bounded_audit_answer(candidate, audit, ledger.bundle(), preserve_candidate=True)
    assert resolution == "disputed_full_draft"
    assert "争议 1" in answer
    assert candidate in answer
    state = audit_delivery_state(audit)
    assert state["semantic_review"] == "complete"
    assert state["verification_status"] == "disputed"


def test_partial_audit_keeps_real_refutation_beside_another_targets_execution_failure():
    from policy_impact.domains.finance.answer_delivery import (
        audit_delivery_state,
        bounded_audit_answer,
    )

    ledger, first, second = _evidence()

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        if payload["target"]["target_id"] == "interrupted":
            raise ConnectionError("Offline fixture connection closed")
        result = _retained_target(payload)
        result["checks"][0].update(status="refuted", action="revise",
                                   reason="The observed independent source contradicts the conclusion.")
        return result

    targets = [_target(target_id=target_id, scope="external", relation_type=None,
                       article_refs=[], evidence_refs=[ref])
               for target_id, ref in [("refuted", first), ("interrupted", second)]]
    candidate = "A complete comparison with two material conclusions."
    audit = AnswerAudit(ledger, complete).call(candidate, review_targets=targets)
    assert audit["answer_audit_coverage"] == "partial"
    assert len(audit["checks"]) == 1 and audit["checks"][0]["status"] == "refuted"
    assert "issue_kind" not in audit["issues"][0]
    assert audit["issues"][1]["issue_kind"] == "review_execution_gap"
    answer, resolution = bounded_audit_answer(candidate, audit, ledger.bundle(), preserve_candidate=True)
    assert answer.endswith(candidate)
    assert resolution == "disputed_full_draft"
    assert "未完成目标 interrupted" in answer
    assert "争议 1" in answer and "争议 2" not in answer
    assert all(issue["reason"] in answer for issue in audit["issues"])
    state = audit_delivery_state(audit)
    assert state["answer_audit_coverage"] == state["semantic_review"] == "partial"
    assert state["verification_status"] == "disputed"


@pytest.mark.parametrize("model", [AnswerClaim, TargetAuditClaim])
def test_model_financial_checks_cannot_supply_runtime_execution_classification(model):
    check = {"action": "qualify", "reason": "A substantive financial objection remains."}
    if model is AnswerClaim:
        check.update(line_start=1, line_end=1, claim_type="objective_fact")
    else:
        check.update(target_id="target-1", status="insufficient_evidence")
    model.model_validate(check)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate({**check, "issue_kind": "review_execution_gap"})
    assert "issue_kind" not in model.model_json_schema()["properties"]


@pytest.mark.parametrize("empty", [False, True])
def test_missing_target_metadata_selection_is_bounded_and_does_not_return_prose(empty):
    ledger, first, _ = _evidence()
    answer = "Preserve this complete candidate answer exactly while selecting review targets."
    calls = []
    targets = [] if empty else [_target(scope="external", relation_type=None,
                                       article_refs=[], evidence_refs=[first])]

    def complete(messages, schema_name):
        calls.append(schema_name)
        assert messages[1]["content"]["candidate_answer"] == answer
        assert "observed_source_catalog" in messages[1]["content"]
        assert "excerpt" not in messages[1]["content"]["observed_source_catalog"][0]
        if schema_name == "finance_review_targets.normalize.v1":
            return {"review_targets": targets,
                    "selection_reason": "Metadata only; the supplied answer body is unchanged."}
        return {"review_targets": targets, "selection_reason": "Select material claims only.",
                "answer": "This forbidden replacement must never be published."}

    auditor = AnswerAudit(ledger, complete)
    result = auditor.select_review_targets(answer, question="Explain this report")
    assert result["review_targets"] == [FinanceReviewTarget.model_validate(row).model_dump(mode="json")
                                        for row in targets]
    assert "answer" not in result
    assert calls == ["finance_review_targets.v1"] * 2 + ["finance_review_targets.normalize.v1"]
    result["selection_reason"] = "Caller mutation"
    assert auditor.select_review_targets(answer, question="Explain this report")["selection_reason"] != "Caller mutation"
    assert len(calls) == 3


def test_target_metadata_transport_failure_never_becomes_format_repair():
    ledger, _, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        raise ModelInvocationError("Transport unavailable before any complete response")

    with pytest.raises(ModelInvocationError):
        AnswerAudit(ledger, complete).select_review_targets(
            "The complete candidate analysis must be preserved.", question="Explain the report",
        )
    assert calls == ["finance_review_targets.v1"]


def _external_target(ref, **overrides):
    return _target(scope="external", relation_type=None, article_refs=[],
                   evidence_refs=[ref], **overrides)


def _coverage_metadata(payload, *, checked=True):
    return {
        "checked_target_ids": [payload["target"]["target_id"]] if checked else [],
        "unchecked_target_ids": [] if checked else [payload["target"]["target_id"]],
        "checked_evidence_ids": payload["allowed_evidence_ids"] if checked else [],
        "unchecked_evidence_ids": [] if checked else payload["allowed_evidence_ids"],
    }


def test_review_disposition_is_optional_and_preserves_legacy_output():
    original = GeneralChatOutput(answer="A complete candidate answer.")
    assert original.review_disposition == "continue"
    assert original.review_resolution_reason == ""
    result = GeneralChatOutput(answer=original.answer, review_disposition="deliver_with_limits",
                               review_resolution_reason="Available inputs cannot resolve this fact.")
    assert result.review_disposition == "deliver_with_limits"
    with pytest.raises(ValidationError):
        GeneralChatOutput(answer=original.answer, review_disposition="skip_audit")
    with pytest.raises(ValidationError):
        FinanceReviewTarget.model_validate(_external_target("ref", answer_context="not a list"))


@pytest.mark.parametrize("status", ["viewpoint_faithful", "reasoned", "not_due", "insufficient_evidence"])
def test_completed_limited_verdicts_are_cached_without_becoming_fact_verified(status):
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        payload = messages[1]["content"]
        result = _retained_target(payload)
        result["checks"][0]["status"] = status
        result["limitations"] = "Only the supplied sources were checked; market truth remains unresolved."
        result["coverage_detail"] = _coverage_metadata(payload)
        return result

    auditor = AnswerAudit(ledger, complete)
    target = _external_target(first)
    answer = "This attributed analysis preserves conditions without asserting market truth."
    original = auditor.call(answer, review_targets=[target])
    assert original["status"] == "accept"
    assert original["answer_audit_coverage"] == "complete"
    assert original["issues"] == []
    assert original["checks"][0]["fact_verified"] is False
    assert original["checked_target_ids"] == ["target-1"]
    assert original["unchecked_target_ids"] == []
    renamed = auditor.call(answer, review_targets=[{**target, "target_id": "renamed"}])
    assert renamed["cache_hit"] is True
    assert renamed["checks"][0]["target_id"] == "renamed"
    assert renamed["checks"][0]["target"]["target_id"] == "renamed"
    detail = renamed["coverage_detail"]["targets"][0]
    assert detail["previous_target_ids"] == ["target-1"]
    assert detail["history_count"] == 2
    assert detail["audit_attempt_count"] == 1
    assert detail["reused_from_target_id"] == "target-1"
    assert detail["checked_evidence_ids"] == [first]
    assert len(calls) == 1


def test_new_target_reuses_prior_completed_inconclusive_issue():
    ledger, first, second = _evidence()
    calls = []

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        calls.append(payload["target"]["target_id"])
        result = _retained_target(payload)
        result["checks"][0].update(status="insufficient_evidence", action="qualify")
        result["limitations"] = "No independent resolution is available in these observed inputs."
        return result

    auditor = AnswerAudit(ledger, complete)
    answer = "Available evidence does not resolve these two material questions."
    target = _external_target(first)
    auditor.call(answer, review_targets=[target])
    result = auditor.call(answer, review_targets=[{**target, "target_id": "renamed"},
                                                 _external_target(second, target_id="new")])
    assert calls == ["target-1", "new"]
    assert result["cached_target_ids"] == ["renamed"]
    assert result["answer_audit_coverage"] == "complete"
    assert [item["target_id"] for item in result["issues"]] == ["renamed", "new"]
    assert all(item["status"] == "insufficient_evidence" for item in result["checks"])


def _context_candidate():
    paragraphs = [
        "# Analysis", "Introductory scope.", "## Conditional view", "Author and date: TT, October.",
        "The author expects gains only if yields fall. This is an attributed forecast, not a fact.",
        "The outcome has not yet occurred.", "## Separate topic", "Unrelated background.",
        "This distant discussion may be revised independently.",
    ]
    return "\n\n".join(paragraphs)


@pytest.mark.parametrize("change", ["distant", "condition", "adjacent", "heading"])
def test_bounded_answer_context_reuses_only_unchanged_semantic_inputs(change):
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        payload = messages[1]["content"]
        calls.append(payload["candidate_answer"])
        assert "# Analysis" in payload["candidate_answer"]
        assert "Author and date" in payload["candidate_answer"]
        assert "The outcome has not yet occurred" in payload["candidate_answer"]
        assert "This is an attributed forecast, not a fact." in payload["candidate_answer"]
        assert "This distant discussion" not in payload["candidate_answer"]
        return _retained_target(payload)

    auditor = AnswerAudit(ledger, complete)
    target = _external_target(first, answer_context=["The author expects gains"])
    answer = _context_candidate()
    auditor.call(answer, review_targets=[target])
    old, new = {
        "distant": ("may be revised independently", "has been reorganized"),
        "condition": ("only if yields fall", "even if yields rise"),
        "adjacent": ("October.", "November."),
        "heading": ("Conditional view", "Unconditional view"),
    }[change]
    result = auditor.call(answer.replace(old, new), review_targets=[target])
    assert result["cache_hit"] is (change == "distant")
    assert len(calls) == (1 if change == "distant" else 2)
    assert result["coverage_detail"]["targets"][0]["answer_context"]["scope"] == "bounded_paragraphs"


@pytest.mark.parametrize("spans,reason", [([], "not_provided"), (["missing"], "missing_span"),
                                        (["Repeated"], "ambiguous_span"), (["  "], "missing_span")])
def test_answer_context_missing_or_ambiguous_spans_fall_back_to_full_answer(spans, reason):
    ledger, first, _ = _evidence()
    answer = "Repeated attribution.\n\nOther conditions.\n\nRepeated attribution."

    def complete(messages, schema_name):
        assert messages[1]["content"]["candidate_answer"] == answer
        return _retained_target(messages[1]["content"])

    result = AnswerAudit(ledger, complete).call(answer, review_targets=[_external_target(first, answer_context=spans)])
    assert result["coverage_detail"]["targets"][0]["answer_context"]["fallback_reason"] == reason


def test_answer_context_preserves_multi_paragraph_span_tables_and_crlf():
    ledger, first, _ = _evidence()
    answer = "# Comparison\r\n\r\nBefore.\r\n\r\n| Author | View |\r\n|---|---|\r\n| TT | Conditional |\r\n\r\nAfter.\r\n\r\nTail."

    def complete(messages, schema_name):
        reviewed = messages[1]["content"]["candidate_answer"]
        assert "| Author | View |\r\n|---|---|\r\n| TT | Conditional |" in reviewed
        assert "# Comparison" in reviewed
        assert "Before." in reviewed and "After." in reviewed
        return _retained_target(messages[1]["content"])

    target = _external_target(first, answer_context=["| TT | Conditional |\r\n\r\nAfter."])
    result = AnswerAudit(ledger, complete).call(answer, review_targets=[target])
    assert result["answer_audit_coverage"] == "complete"


@pytest.mark.parametrize("contradiction", ["flags", "detail", "missing_flag"])
def test_metadata_contradiction_gets_one_metadata_only_clarification(contradiction):
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        payload = messages[1]["content"]
        if schema_name == "finance_review.coverage_metadata.v1":
            request = messages[-1]["content"]
            assert "Do not return checks" in messages[0]["content"]
            assert "output.checks" not in messages[0]["content"]
            assert request["repair_mode"] == "review.coverage_metadata"
            assert "checks" not in request["required_output_schema"]["properties"]
            return {"answer_audit_coverage": "complete", "coverage_detail": _coverage_metadata(payload),
                    "limitations": "Execution completed, but this is not proof of market truth."}
        result = _retained_target(payload)
        if contradiction == "flags":
            result["coverage_complete"] = False
        elif contradiction == "detail":
            result["coverage_detail"] = _coverage_metadata(payload, checked=False)
        else:
            result.pop("answer_audit_coverage")
        return result

    result = AnswerAudit(ledger, complete).call(
        "Retain this complete candidate without rewriting its financial argument.",
        review_targets=[_external_target(first)],
    )
    assert calls == ["finance_target_answer_audit.v1", "finance_review.coverage_metadata.v1"]
    assert result["status"] == "accept"
    assert result["checks"][0]["status"] == "reasoned"
    assert result["coverage_detail"]["targets"][0]["metadata_clarification"] == "complete"


@pytest.mark.parametrize("failure", ["contradictory", "malformed", "transport", "budget", "rewrite"])
def test_metadata_clarification_failure_is_bounded_and_preserves_valid_check(failure):
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        payload = messages[1]["content"]
        if schema_name == "finance_target_answer_audit.v1":
            return {**_retained_target(payload), "coverage_complete": False}
        if failure == "transport":
            raise ModelInvocationError("offline transport failure")
        if failure == "budget":
            raise RuntimeError("shared logical model decision request budget exhausted")
        if failure == "malformed":
            return {"wrong_shape": True}
        result = {"answer_audit_coverage": "complete",
                  "coverage_detail": _coverage_metadata(payload, checked=failure != "contradictory")}
        if failure == "rewrite":
            result["checks"] = []
        return result

    result = AnswerAudit(ledger, complete).call(
        "This valid analysis remains available despite incomplete review metadata.",
        review_targets=[_external_target(first)],
    )
    assert len(calls) == 2
    assert result["answer_audit_coverage"] == "partial"
    assert result["checks"][0]["status"] == "reasoned"
    assert result["unchecked_target_ids"] == ["target-1"]
    assert result["coverage_detail"]["targets"][0]["gap_types"] == ["metadata_contradiction"]


def test_complete_flag_without_check_cannot_be_repaired_into_a_passing_audit():
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        if schema_name == "finance_target_answer_audit.v1":
            return {"checks": [], "answer_audit_coverage": "complete"}
        return {"answer_audit_coverage": "complete",
                "coverage_detail": _coverage_metadata(messages[1]["content"])}

    result = AnswerAudit(ledger, complete).call(
        "An empty check list must never certify this complete candidate.", review_targets=[_external_target(first)],
    )
    assert len(calls) == 2
    assert result["answer_audit_coverage"] == "failed"
    assert result["checks"] == []


def test_real_missing_participant_has_structured_unchecked_coverage():
    ledger = EvidenceLedger()
    first = _article_evidence(ledger, "article-1")
    result = AnswerAudit(ledger, lambda *_: pytest.fail("Missing input must not trigger research")).call(
        "The authors appear to agree, but the second article was not observed.",
        review_targets=[_target(evidence_refs=[first.evidence_id])],
    )
    detail = result["coverage_detail"]["targets"][0]
    assert detail["missing_article_refs"] == ["article-2"]
    assert detail["unchecked_evidence_ids"] == [first.evidence_id]
    assert detail["provided_evidence_ids"] == []
    assert detail["provided_article_refs"] == []
    assert detail["available_article_refs"] == ["article-1"]
    assert detail["gap_types"] == ["missing_participant"]
    assert result["unchecked_target_ids"] == ["target-1"]


def test_invalid_observed_source_text_stays_partial_and_is_never_cached():
    ledger = EvidenceLedger()
    ref = ledger.add_reference(source_type="author_article", provenance="author_view",
                               source_ref="article-1", excerpt="1: A truncated source without integrity metadata.")
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        return _retained_target(messages[1]["content"])

    auditor = AnswerAudit(ledger, complete)
    for target_id in ["original", "renamed"]:
        result = auditor.call("This answer cannot be fully checked against an invalid source excerpt.",
                              review_targets=[_external_target(ref.evidence_id, target_id=target_id)])
        assert result["answer_audit_coverage"] == "partial"
        assert result["coverage_detail"]["targets"][0]["truncated_evidence_ids"] == [ref.evidence_id]
        assert result["coverage_detail"]["targets"][0]["checked_evidence_ids"] == []
        assert result["cache_hit"] is False
    assert len(calls) == 2
    assert result["coverage_detail"]["targets"][0]["previous_target_ids"] == ["original"]


def test_partial_input_metadata_retains_checked_inputs_without_claiming_full_coverage():
    ledger, first, second = _evidence()

    def complete(messages, schema_name):
        assert schema_name == "finance_target_answer_audit.v1"
        result = _retained_target(messages[1]["content"])
        result.update(answer_audit_coverage="partial", coverage_detail={
            "checked_target_ids": ["target-1"], "unchecked_target_ids": [],
            "checked_evidence_ids": [first], "unchecked_evidence_ids": [second],
        })
        return result

    target = _external_target(first)
    target["evidence_refs"].append(second)
    result = AnswerAudit(ledger, complete).call(
        "The second input was supplied but was not assessed by the reviewer.", review_targets=[target],
    )
    assert result["answer_audit_coverage"] == "partial"
    assert result["unchecked_target_ids"] == ["target-1"]
    detail = result["coverage_detail"]["targets"][0]
    assert detail["checked_evidence_ids"] == [first]
    assert detail["unchecked_evidence_ids"] == [second]
    assert detail["metadata_clarification"] == "not_needed"


def test_over_limit_participants_are_reported_as_unsupplied_not_checked_or_cached():
    ledger = EvidenceLedger()
    refs = [_article_evidence(ledger, f"article-{index}").evidence_id for index in range(13)]
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        return _retained_target(messages[1]["content"])

    target = _target(article_refs=[f"article-{index}" for index in range(13)])
    auditor = AnswerAudit(ledger, complete)
    for _ in range(2):
        result = auditor.call("The selected comparison has more inputs than one review can cover.",
                              review_targets=[target])
        assert result["answer_audit_coverage"] == "partial"
        detail = result["coverage_detail"]["targets"][0]
        assert detail["required_evidence_ids"] == refs
        assert detail["provided_evidence_ids"] == refs[:12]
        assert detail["unchecked_evidence_ids"] == refs[12:]
        assert "article-12" not in detail["provided_article_refs"]
        assert detail["gap_types"] == ["input_limit"]
    assert len(calls) == 2


def test_audit_contract_change_invalidates_semantic_cache(monkeypatch):
    from policy_impact.domains.finance.answer_audit import TargetAuditBatch

    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        return _retained_target(messages[1]["content"])

    auditor = AnswerAudit(ledger, complete)
    target = _external_target(first)
    answer = "This bounded interpretation must be rechecked under a changed review contract."
    auditor.call(answer, review_targets=[target])
    schema = TargetAuditBatch.model_json_schema()
    schema["description"] = "Updated execution coverage semantics."
    monkeypatch.setattr(TargetAuditBatch, "model_json_schema", staticmethod(lambda: schema))
    assert auditor.call(answer, review_targets=[target])["cache_hit"] is False
    assert len(calls) == 2


def test_unrelated_new_observations_do_not_invalidate_completed_target():
    ledger, first, _ = _evidence()
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        return _retained_target(messages[1]["content"])

    auditor = AnswerAudit(ledger, complete)
    target = _external_target(first)
    answer = "This answer depends on only the selected observed source, not the whole ledger."
    auditor.call(answer, review_targets=[target])
    _article_evidence(ledger, "unrelated")
    assert auditor.call(answer, review_targets=[target])["cache_hit"] is True
    assert len(calls) == 1


def test_retrieved_copy_of_author_article_is_not_independent_fact_evidence():
    ledger, first, _ = _evidence()
    article = ledger.add_article({
        "article_id": "article-1", "path": "https://example.com/story", "content_hash": "version-1",
    }, excerpt="1: The author reports this alleged loss.")

    def complete(messages, schema_name):
        result = _retained_target(messages[1]["content"])
        result["checks"][0].update(status="supported", evidence_refs=[first])
        return result

    target = _external_target(first, type="objective_fact")
    target["evidence_refs"].append(article.evidence_id)
    result = AnswerAudit(ledger, complete).call(
        "Reading the same article through another tool must not establish independence.", review_targets=[target],
    )
    assert result["answer_audit_coverage"] == "failed"
    assert result["checks"] == []


@pytest.mark.parametrize("verdict", ["supported", "refuted"])
@pytest.mark.parametrize("use_independent", [False, True])
def test_fact_verdicts_require_bound_non_author_evidence(verdict, use_independent):
    ledger, first, _ = _evidence()
    article = _article_evidence(ledger, "article-1")
    calls = []

    def complete(messages, schema_name):
        calls.append(schema_name)
        result = _retained_target(messages[1]["content"])
        result["checks"][0].update(status=verdict, evidence_refs=[first] if use_independent else [article.evidence_id])
        return result

    target = _external_target(first, type="objective_fact")
    target["evidence_refs"].append(article.evidence_id)
    result = AnswerAudit(ledger, complete).call(
        "The objective claim must bind its verdict to the independent observed source.", review_targets=[target],
    )
    assert result["answer_audit_coverage"] == ("complete" if use_independent else "failed")
    if use_independent:
        assert result["checks"][0]["fact_verified"] is (verdict == "supported")
        assert len(calls) == 1
    else:
        assert result["checks"] == []
        assert len(calls) == 5


def test_audit_source_finds_numeric_table_rows_without_answer_line_numbers():
    ledger = EvidenceLedger()
    rows = [f"{number}: Unrelated opening " + "x" * 80 for number in range(1, 100)]
    rows[74] = "75: | 4.375 | 4 | 8 |"
    rows[76] = "77: | 4.125 | 12 | 6 |"
    source = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/projections.htm",
        source_url="https://www.federalreserve.gov/projections.htm",
        line_start=1, line_end=99, excerpt="\n".join(rows),
    )
    selected = _audit_source(source.to_dict(), "SEP Figure 2: 12 at 4.125 and 4 at 4.375.")
    assert "75: | 4.375 | 4 | 8 |" in selected["excerpt"]
    assert "77: | 4.125 | 12 | 6 |" in selected["excerpt"]
    assert not selected["excerpt_truncated"]


def test_audit_source_keeps_each_claims_table_row_and_nearby_header():
    ledger = EvidenceLedger()
    rows = [f"{number}: Unrelated page line " + "x" * 100 for number in range(1, 140)]
    rows[55] = "56: Table 1. Economic projections, September 2026"
    rows[56] = "57: Variable | 2026 | 2027 | 2028 | 2029 | Longer run"
    rows[59] = "60: Unemployment rate median | 4.1 | 4.1 | 4.1 | 4.1 | 4.2"
    rows[83] = "84: Figure 2. Distribution of projected policy rates, September 2026"
    rows[85] = "86: Policy rate 4.375 | 4 participants"
    rows[86] = "87: Policy rate 4.125 | 12 participants"
    source = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/projections.htm",
        source_url="https://www.federalreserve.gov/projections.htm",
        line_start=1, line_end=139, excerpt="\n".join(rows),
    )
    selected = _audit_source(source.to_dict(),
        "Unemployment median for 2026-2029 was 4.1 and longer-run was 4.2.\n"
        "The dot plot showed 12 at 4.125 and 4 at 4.375.")
    assert "Unemployment rate median | 4.1" in selected["excerpt"]
    assert "Distribution of projected policy rates" in selected["excerpt"]
    assert "Policy rate 4.375 | 4 participants" in selected["excerpt"]
    assert "Policy rate 4.125 | 12 participants" in selected["excerpt"]
    assert selected["selection_scope"] == "claim_matched_passage"
    assert selected["source_excerpt_complete"] is False
    assert not selected["excerpt_truncated"]


def test_audit_source_maps_chinese_sep_claims_to_english_rows():
    ledger = EvidenceLedger()
    rows = [f"{number}: unrelated " + "x" * 90 for number in range(1, 120)]
    rows[6] = "7: Table 1. Economic projections, September 2026"
    rows[10] = "11: |  | 2026 | 2027 | 2028 | 2029 | Longer run |"
    rows[11] = "12: | Change in real GDP | 2.3 | 2.4 | 2.2 | 2.1 | 2.0 |"
    rows[13] = "14: | Unemployment rate | 4.1 | 4.1 | 4.1 | 4.1 | 4.2 |"
    rows[15] = "16: | PCE inflation | 3.7 | 2.3 | 2.1 | 2.0 | 2.0 |"
    rows[17] = "18: | Core PCE inflation | 3.4 | 2.5 | 2.2 | 2.0 |"
    rows[20] = "21: | Federal funds rate | 4.1 | 4.1 | 3.9 | 3.6 | 3.2 |"
    rows[69] = "70: Figure 2. FOMC participants' assessments of appropriate monetary policy"
    rows[74] = "75: | 4.375 | 4 participants |"
    source = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/projections.htm",
        source_url="https://www.federalreserve.gov/projections.htm",
        line_start=1, line_end=119, excerpt="\n".join(rows),
    )
    selected = _audit_source(source.to_dict(),
        "其他中位数：实际GDP增速2.3%、失业率4.1%、PCE通胀3.7%、核心PCE通胀3.4%。\n"
        "联邦基金利率中位数4.1%，点阵图分布有4位在4.375%。")
    for marker in (
        "12: | Change in real GDP", "14: | Unemployment rate",
        "16: | PCE inflation", "18: | Core PCE inflation",
        "21: | Federal funds rate", "70: Figure 2", "75: | 4.375",
    ):
        assert marker in selected["excerpt"], marker
    assert selected["selection_scope"] == "claim_matched_passage"


def test_author_coverage_runs_despite_atomic_disputes_and_checks_each_author():
    ledger = EvidenceLedger()
    passages = {
        "TT": "1: We described a 25bp hike.",
        "培风客": "1: We discussed the first hike and its market impact.",
        "Spread Trading": "1: The Fed raised 25bp to 3.75%-4.00%.",
    }
    refs = {}
    for author, passage in passages.items():
        ref = ledger.add_article({
            "article_id": f"{author}-fomc", "author": author,
            "path": f"C:/articles/{author}/articles/fomc.md",
            "published_at": "2026-09-20", "line_start": 1,
            "line_end": 1, "content_hash": f"hash-{author}",
        }, excerpt=passage)
        refs[author] = ref.evidence_id

    def complete(messages, schema_name):
        if schema_name == "finance_answer_audit.v1":
            return {"checks": [{
                "line_start": 1, "line_end": 1, "claim_type": "objective_fact",
                "action": "qualify", "evidence_refs": [],
                "reason": "This unrelated official claim is not verified.",
                "suggested_revision": "State the verification limit.",
            }], "coverage_complete": False, "limitations": "Other claim not fully checked."}
        if schema_name == "finance_author_coverage_selection.v1":
            return {"claims": [{
                "line_number": 2,
                "answer_quote": "All three authors described the 25bp hike and 3.75%-4.00% target range",
                "authors": list(passages),
            }]}
        if schema_name == "finance_author_coverage_finding.v1":
            author = messages[1]["content"]["author"]
            return {
                "status": "supports", "evidence_refs": [refs[author]],
                "source_quote": passages[author].split(": ", 1)[1],
                "reason": "The article explicitly states the attributed numbers.",
            }
        raise AssertionError(schema_name)

    result = AnswerAudit(ledger, complete).call(
        "The official data need separate review.\n"
        "All three authors described the 25bp hike and 3.75%-4.00% target range."
    )
    assert result["status"] == "revise"
    assert result["cross_review"]["status"] == "not_applicable"
    assert result["author_coverage"]["status"] == "complete"
    assert {row["author"]: row["status"] for row in result["author_coverage"]["findings"]} == {
        "TT": "not_mentioned", "培风客": "not_mentioned", "Spread Trading": "supports",
    }
    assert {"TT", "培风客"} == {
        author for author in passages if any(
            author in issue["reason"] for issue in result["issues"] if issue["span"].startswith("All three")
        )
    }


def _complete(refs, *, line_start=1, action="retain"):
    return lambda messages, schema_name: {
        "checks": [{
            "line_start": line_start, "line_end": line_start,
            "claim_type": "objective_fact", "action": action,
            "evidence_refs": refs, "reason": "The cited report describes this loss.",
            "suggested_revision": "Attribute the reported figure to the source.",
        }],
        "coverage_complete": True,
    }


def test_answer_audit_downgrades_one_secondary_source():
    ledger, first, _ = _evidence()
    span = "The firm lost 15 billion in July."
    answer = f"{span} ({first})"
    audit = AnswerAudit(ledger, _complete([first])).call(answer)
    assert audit["status"] == "revise"
    assert audit["issues"][0]["action"] == "qualify"
    assert audit["reviewed_claim_count"] == 1


def test_answer_audit_accepts_claim_matched_official_publisher_page():
    ledger = EvidenceLedger()
    ref = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/monetarypolicy/statement.htm",
        source_url="https://www.federalreserve.gov/monetarypolicy/statement.htm",
        excerpt="The federal funds rate target range is 3.75 to 4.00 percent.",
    )
    answer = f"The target range is 3.75 to 4.00 percent. ({ref.evidence_id})"
    assert AnswerAudit(ledger, _complete([ref.evidence_id])).call(answer)["status"] == "accept"
    assert not _is_official_publisher({
        "source_type": "public_web_page",
        "source_url": "https://federalreserve.gov.example.com/statement",
    })


def test_unrelated_long_web_page_does_not_make_short_official_claim_incomplete():
    ledger = EvidenceLedger()
    statement = ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/statement.htm",
        source_url="https://www.federalreserve.gov/statement.htm",
        excerpt="The Committee raised the target range to 3.75 to 4 percent.",
    )
    ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://www.federalreserve.gov/projections.htm",
        source_url="https://www.federalreserve.gov/projections.htm",
        excerpt="\n".join(f"{number}: unrelated data " + "x" * 100
                          for number in range(1, 100)),
    )
    observed = []

    def complete(messages, schema_name):
        observed.extend(messages[1]["content"]["observed_evidence"])
        return {"checks": [{
            "line_start": 1, "line_end": 1, "claim_type": "objective_fact",
            "action": "retain", "evidence_refs": [statement.evidence_id],
            "reason": "The official statement supports the target range.",
            "suggested_revision": "",
        }], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call("The Fed raised the range to 3.75 to 4 percent.")
    assert result["status"] == "accept"
    assert [row["evidence_id"] for row in observed] == [statement.evidence_id]


def test_answer_audit_sees_observed_market_values_without_inline_evidence_id():
    ledger = EvidenceLedger()
    metric = ledger.add_market_metric({
        "metric_id": "market_change_pct:xauusd", "name": "Gold", "symbol": "xauusd",
        "start_date": "2026-10-07", "end_date": "2026-10-08",
        "start_close": 4108.0, "end_close": 4144.59, "change_pct": 0.89,
        "unit": "%", "price_unit": "USD/troy_oz",
        "source_url": "https://example.com/gold",
    }, snapshot_id="snapshot-1")
    observed = []

    def complete(messages, schema_name):
        observed.extend(messages[1]["content"]["observed_evidence"])
        return {"checks": [{
            "line_start": 1, "line_end": 1, "claim_type": "objective_fact",
            "action": "retain", "evidence_refs": [metric.evidence_id],
            "reason": "The observed market metric contains this close and date.",
            "suggested_revision": "",
        }], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call("Gold closed at 4144.59 on 2026-10-08.")
    assert result["status"] == "accept"
    assert observed[0]["evidence_id"] == metric.evidence_id
    assert "4144.59" in observed[0]["excerpt"]


def test_atomic_issue_review_dismisses_redundant_unverified_qualification():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-09-30", "author": "TT",
        "path": "C:/articles/TT/articles/2026-09-30.md",
        "published_at": "2026-09-30", "line_start": 1,
        "line_end": 1, "content_hash": "hash-30",
    }, excerpt="1: TT reports an unverified market number.")
    answer = "作者报告该市场数字，未经独立核实，不能视为已证实的行情事实。"

    def complete(messages, schema_name):
        if schema_name == "finance_atomic_issue_review.v1":
            return {"verdicts": [{
                "issue_index": 0, "decision": "dismiss",
                "reason": "The answer already attributes the figure and labels it unverified.",
            }]}
        return {"checks": [{
            "line_start": 1, "line_end": 1, "claim_type": "objective_fact",
            "action": "qualify", "evidence_refs": [ref.evidence_id],
            "reason": "The number is only author-reported and not independently verified.",
            "suggested_revision": "Attribute the number and mark it unverified.",
        }], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(answer)
    assert result["status"] == "accept"
    assert result["atomic_issue_review"]["dismissed_issue_count"] == 1


def test_answer_audit_accepts_two_domains_and_rejects_unknown_refs():
    ledger, first, second = _evidence()
    span = "The firm lost 15 billion in July."
    answer = f"{span} ({first}; {second})"
    audit = AnswerAudit(ledger, _complete([first, second])).call(answer)
    assert audit["status"] == "accept"
    unknown = AnswerAudit(ledger, lambda *_: pytest.fail("model should not run")).call(
        "The firm lost 15 billion in July. (evidence_aaaaaaaaaaaaaaaaaaaaaaaa)"
    )
    assert unknown["status"] == "revise"


@pytest.mark.parametrize("span", [
    "作者称白银当日跌幅超过 4%，本轮未经独立核实。",
    "本篇文章未直接讨论美股。",
    "本地 TT 目录 2026-09-01 至 2026-09-30 共 7 篇。",
    "### 3. 2026-09-13《加息降息皆是表象》",
    "作者转述盘面：美联储加息 25bp，未经本轮外部核实。",
    "- 作者：TT；标题：《金银又遇假期砸盘》；发布日期：2026-09-30。",
    "- 标题：《金银又遇假期砸盘》",
    "- 发布日期：2026-09-30。",
    "09-30 本篇未直接谈美股。",
])
def test_answer_audit_does_not_downgrade_attribution_or_source_metadata(span):
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-09-30", "author": "TT",
        "path": "C:/articles/TT/articles/2026-09-30.md",
        "published_at": "2026-09-30", "line_start": 1,
        "line_end": 1, "content_hash": "source-hash",
    }, excerpt="1: source text")
    result = AnswerAudit(ledger, _complete([ref.evidence_id])).call(
        f"{span} ({ref.evidence_id})"
    )
    assert result["status"] == "accept"


def test_article_identity_heading_is_not_external_market_fact():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-10-01", "author": "TT",
        "path": "C:/articles/TT/articles/2026-10-01.md",
        "published_at": "2026-10-01", "line_start": 1,
        "line_end": 3, "content_hash": "metadata-hash",
    }, excerpt="1: article source")
    answer = f"### TT - 2026-10-01 - Gold review\n({ref.evidence_id})"
    result = AnswerAudit(ledger, _complete([ref.evidence_id])).call(answer)
    assert result["status"] == "accept"


def test_answer_audit_rejects_unsupported_model_refs_and_invalid_lines():
    ledger, first, _ = _evidence()
    answer = f"The firm lost 15 billion in July. ({first})"
    with pytest.raises(ValueError, match="invalid evidence IDs"):
        AnswerAudit(ledger, _complete(["evidence_aaaaaaaaaaaaaaaaaaaaaaaa"])).call(answer)
    with pytest.raises(ValueError, match="numbered candidate-answer lines"):
        AnswerAudit(ledger, _complete([first], line_start=100)).call(answer)


def test_answer_audit_receives_the_full_observed_article_passage():
    ledger = EvidenceLedger()
    passage = "1: opening\n" + "x" * 3000 + "\n63: crucial later qualification"
    ref = ledger.add_article({
        "article_id": "tt-2026-09-30", "author": "TT",
        "path": "C:/articles/TT/2026-09-30.md", "line_start": 1,
        "line_end": 63, "content_hash": "source-hash",
    }, excerpt=passage)

    def complete(messages, schema_name):
        observed = messages[1]["content"]["observed_evidence"]
        assert "63: crucial later qualification" in observed[0]["excerpt"]
        assert observed[0]["excerpt_truncated"] is False
        return {
            "checks": [{"line_start": 1, "line_end": 1,
                        "claim_type": "author_view", "action": "retain",
                        "evidence_refs": [ref.evidence_id],
                        "reason": "The complete observed article contains this qualification."}],
            "coverage_complete": True,
        }

    result = AnswerAudit(ledger, complete).call(
        f"TT made this qualification. ({ref.evidence_id})"
    )
    assert result["status"] == "accept"


def test_answer_audit_cannot_certify_truncated_source_or_incomplete_review():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "long-article", "author": "TT",
        "path": "C:/articles/TT/long.md", "line_start": 1,
        "line_end": 100, "content_hash": "source-hash",
    }, excerpt="x" * 20001)
    answer = f"The author made a long argument. ({ref.evidence_id})"
    result = AnswerAudit(ledger, lambda *_: {
        "checks": [], "coverage_complete": True,
    }).call(answer)
    assert result["status"] == "revise"
    assert result["coverage_complete"] is False
    assert "partial passage" in result["limitations"]

    ledger, first, _ = _evidence()
    result = AnswerAudit(ledger, lambda *_: {
        "checks": [], "coverage_complete": True,
        "limitations": "One answer paragraph was not assessed.",
    }).call(f"The firm lost 15 billion in July. ({first})")
    assert result["status"] == "revise"


def test_answer_audit_selects_dated_article_and_all_small_corpus_fallback():
    ledger = EvidenceLedger()
    refs = []
    for day in range(1, 8):
        row = ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}",
            "line_start": 1, "line_end": 2,
            "content_hash": f"hash-{day}",
        }, excerpt=f"1: article {day} viewpoint")
        refs.append(row.evidence_id)
    seen = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [2]}
        supplied = messages[1]["content"]["allowed_evidence_ids"]
        seen.append(supplied)
        assert "full_answer_context" not in messages[1]["content"]
        return {"checks": []} if schema_name == "finance_cross_answer_audit.v1" else {
            "checks": [], "coverage_complete": True,
        }

    result = AnswerAudit(ledger, complete).call(
        "09-07: TT explained his revised view.\n"
        "Across September the conditions changed substantially."
    )
    assert result["status"] == "accept"
    assert refs[-1] in seen[0]
    assert set(result["source_ids"]) == set(refs)


def test_answer_audit_chunks_never_exceed_four_candidate_lines():
    answer = "".join(f"Claim {index} has a separate source.\n" for index in range(20))
    chunks = _answer_chunks(answer)
    assert len(chunks) >= 5
    assert all(len(chunk.splitlines()) <= 4 for chunk in chunks)


def test_answer_audit_inherits_attribution_across_batches_but_not_sections():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-09-17", "author": "TT",
        "path": "C:/articles/TT/articles/2026-09-17.md",
        "line_start": 1, "line_end": 20, "content_hash": "hash",
    }, excerpt="1: author reports the market snapshot")
    observed_scopes = []

    def complete(messages, schema_name):
        content = messages[1]["content"]
        observed_scopes.append(content["inherited_attribution_by_line"])
        rows = content["candidate_answer_lines"].splitlines()
        return {
            "checks": [
                {"line_start": int(row.split(":", 1)[0]),
                 "line_end": int(row.split(":", 1)[0]),
                 "claim_type": "objective_fact", "action": "retain",
                 "evidence_refs": [ref.evidence_id],
                 "reason": "This figure is described in the observed article."}
                for row in rows if row.split(":", 1)[1].strip().startswith("-")
            ],
            "coverage_complete": True,
        }

    answer = (
        "**作者转述的盘面（未经独立核实）**\n"
        + "".join(f"- Market observation {index}.\n" for index in range(7))
    )
    result = AnswerAudit(ledger, complete).call(answer)
    assert result["status"] == "accept"
    assert len(observed_scopes) == 2
    assert observed_scopes[1]
    observed_scopes.clear()
    result = AnswerAudit(ledger, complete).call(
        answer + "### Separate analysis\n- Unattributed market observation.\n"
    )
    assert result["status"] == "revise"
    assert 2 not in observed_scopes[-1]


def test_answer_audit_reuses_only_unchanged_sections():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-article", "author": "TT",
        "path": "C:/articles/TT/articles/article.md",
        "line_start": 1, "line_end": 10, "content_hash": "hash",
    }, excerpt="1: the author's view")
    calls = []

    def complete(messages, schema_name):
        calls.append(messages[1]["content"]["candidate_answer_lines"])
        return {"checks": [], "coverage_complete": True}

    audit = AnswerAudit(ledger, complete)
    prefix = f"Source {ref.evidence_id}\nScope\nContext\nConclusion\n"
    audit.call(prefix + "First revision.")
    audit.call(prefix + "Second revision.")
    assert len(calls) == 3
    audit.call(prefix + "Second revision.", question="different question")
    assert len(calls) == 5


def test_answer_audit_rejects_cross_article_conclusion_despite_atomic_passes():
    ledger = EvidenceLedger()
    refs = []
    for day, passage in ((3, "No stock view stated."),
                         (13, "US equities may fall first if tightening intensifies.")):
        ref = ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}", "line_start": 1,
            "line_end": 1, "content_hash": f"hash-{day}",
        }, excerpt="1: " + passage)
        refs.append(ref.evidence_id)

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [1]}
        if schema_name == "finance_cross_issue_review.v1":
            return {"verdicts": [{
                "issue_index": 0, "decision": "dismiss",
                "reason": "The other article alone says nothing about stocks and cannot negate this summary.",
            }, {
                "issue_index": 1, "decision": "keep",
                "reason": "The conditional forecast contradicts the broad absence claim.",
            }]}
        if schema_name == "finance_cross_answer_audit.v1":
            assert len(messages[1]["content"]["observed_evidence"]) == 1
            supplied_id = messages[1]["content"]["allowed_evidence_ids"][0]
            if supplied_id != refs[-1]:
                return {"checks": [{
                    "line_start": 1, "line_end": 1,
                    "claim_type": "model_inference", "action": "qualify",
                    "evidence_refs": [supplied_id],
                    "reason": "This single article does not discuss stocks.",
                    "suggested_revision": "Remove the general conclusion.",
                    "answer_quote": "never gave a directional US-equity forecast",
                    "source_quote": "No stock view stated.",
                }]}
            return {"checks": [{
                "line_start": 1, "line_end": 1,
                "claim_type": "model_inference", "action": "qualify",
                "evidence_refs": [supplied_id],
                "reason": "The later article contains a conditional bearish forecast.",
                "suggested_revision": "Acknowledge the conditional forecast.",
                "answer_quote": "never gave a directional US-equity forecast",
                "source_quote": "US equities may fall first if tightening intensifies",
            }]}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "TT never gave a directional US-equity forecast."
    )
    assert result["status"] == "revise"
    assert {key: value for key, value in result["cross_review"].items()
            if key not in {"adjudications", "source_line_counts"}} == {
        "status": "complete", "issue_count": 1, "proposed_issue_count": 2,
        "dismissed_issue_count": 1, "source_count": 2,
        "batch_count": 2,
        "selected_answer_lines": [1],
    }
    assert [row["decision"] for row in result["cross_review"]["adjudications"]] == [
        "dismiss", "keep",
    ]
    assert sorted(result["cross_review"]["source_line_counts"].values()) == [1, 1]


def test_cross_article_review_always_inspects_dated_comparison_rows():
    ledger = EvidenceLedger()
    for day in (3, 26):
        ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}", "line_start": 1,
            "line_end": 1, "content_hash": f"hash-{day}",
        }, excerpt="1: The shortage has not happened yet.")
    inspected = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            inspected.append(messages[1]["content"]["numbered_answer"])
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    audit = AnswerAudit(ledger, complete).call(
        "| 09-26 | The shortage caused the selloff | Confirmed |"
    )
    assert audit["cross_review"]["selected_answer_lines"] == [1]
    assert len(inspected) == 1
    assert "09-26" in inspected[0]


def test_answer_audit_reclassifies_forecast_hidden_under_author_view():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-09-03", "author": "TT",
        "path": "C:/articles/TT/articles/2026-09-03.md",
        "published_at": "2026-09-03", "line_start": 1,
        "line_end": 20, "content_hash": "forecast-hash",
    }, excerpt="15: 本轮主升浪将远超历史高点，这是尚未兑现的长期判断")
    answer = f"**A. 作者观点**\n- 本轮主升浪级别远超历史高点。（{ref.evidence_id}）"

    result = AnswerAudit(ledger, lambda *_: {
        "checks": [{
            "line_start": 2, "line_end": 2, "claim_type": "author_view",
            "action": "retain", "evidence_refs": [ref.evidence_id],
            "reason": "The article expresses this view.",
        }],
        "coverage_complete": True,
    }).call(answer)

    assert result["status"] == "revise"
    assert result["issues"][0]["claim_type"] == "prediction"
    assert "prediction" in result["issues"][0]["reason"]


def test_answer_audit_keeps_a_forecast_already_labeled_as_prediction():
    ledger = EvidenceLedger()
    ref = ledger.add_article({
        "article_id": "tt-2026-09-26", "author": "TT",
        "path": "C:/articles/TT/articles/2026-09-26.md",
        "published_at": "2026-09-26", "line_start": 1,
        "line_end": 20, "content_hash": "conditional-hash",
    }, excerpt="10: 若美元走弱而金银续跌，才预示流动性危机临近")
    answer = f"- **关键条件性预测**：若美元走弱而金银续跌，才预示流动性危机临近。（{ref.evidence_id}）"

    result = AnswerAudit(ledger, lambda *_: {
        "checks": [{
            "line_start": 1, "line_end": 1, "claim_type": "author_view",
            "action": "retain", "evidence_refs": [ref.evidence_id],
            "reason": "The article expresses this conditional view.",
        }],
        "coverage_complete": True,
    }).call(answer)

    assert result["status"] == "accept"
    assert result["checks"][0]["claim_type"] == "prediction"


def _article_ledger(count=2):
    ledger = EvidenceLedger()
    refs = []
    for index in range(count):
        day = index + 1
        row = ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}", "line_start": 1,
            "line_end": 2, "content_hash": f"hash-{day}",
        }, excerpt=f"1: article {day} source passage")
        refs.append(row.evidence_id)
    return ledger, refs


def test_large_corpus_unreferenced_heading_does_not_block_source_review():
    ledger, refs = _article_ledger(13)

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [5]}
        if schema_name == "finance_cross_answer_audit.v1":
            return {"checks": []}
        content = messages[1]["content"]
        if content["candidate_answer_lines"].startswith("1: ## Conclusion"):
            return {"checks": [], "coverage_complete": True}
        return {"checks": [{
            "line_start": 1, "line_end": 1, "claim_type": "author_view",
            "action": "retain", "evidence_refs": [refs[0]],
            "reason": "The first article supports the dated viewpoint.",
        }], "coverage_complete": True}

    answer = f"## Conclusion\n\n---\n\n2026-09-01: TT describes a change. ({refs[0]})"
    result = AnswerAudit(ledger, complete).call(answer)
    assert result["status"] == "accept"
    assert result["cross_review"]["source_count"] == 12
    assert result["used_source_ids"] == [refs[0]]


def test_answer_audit_reclassifies_prediction_mislabelled_as_objective_fact():
    ledger, refs = _article_ledger()
    answer = f"**A. 作者观点**\n- 黄金将上涨。（{refs[0]}）"

    result = AnswerAudit(ledger, lambda *_: {
        "checks": [{
            "line_start": 2, "line_end": 2, "claim_type": "objective_fact",
            "action": "retain", "evidence_refs": [refs[0]],
            "reason": "The source mentions this direction.",
        }],
        "coverage_complete": True,
    }).call(answer)

    assert result["status"] == "revise"
    assert result["issues"][0]["claim_type"] == "prediction"


def test_forecast_method_discussion_is_not_itself_a_market_prediction():
    ledger, refs = _article_ledger()
    answer = f"**A. 作者观点**\n- 作者讨论预测模型的局限，但没有给出黄金方向。（{refs[0]}）"

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [2]}
        if schema_name == "finance_cross_answer_audit.v1":
            return {"checks": []}
        return {"checks": [{
            "line_start": 2, "line_end": 2, "claim_type": "author_view",
            "action": "retain", "evidence_refs": [refs[0]],
            "reason": "This is a discussion of method rather than a market forecast.",
        }], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(answer)

    assert result["status"] == "accept"
    assert result["checks"][0]["claim_type"] == "author_view"


@pytest.mark.parametrize("statement", [
    "作者没有预测市场未来走势。",
    "黄金上涨的预测并非本文结论。",
    "历史上黄金价格远超此前高点。",
])
def test_forecast_boundary_ignores_negation_and_historical_comparison(statement):
    ledger, refs = _article_ledger()
    answer = f"**A. 作者观点**\n- {statement}（{refs[0]}）"

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [2]}
        if schema_name == "finance_cross_answer_audit.v1":
            return {"checks": []}
        return {"checks": [{
            "line_start": 2, "line_end": 2, "claim_type": "author_view",
            "action": "retain", "evidence_refs": [refs[0]],
            "reason": "This is not an unfulfilled market forecast.",
        }], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(answer)
    assert result["status"] == "accept"
    assert result["checks"][0]["claim_type"] == "author_view"


def test_cross_review_empty_selection_uses_deterministic_fallback():
    ledger, refs = _article_ledger()
    seen = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            seen.append(messages[1]["content"]["numbered_answer"])
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "09-01 的作者观点与 09-02 的作者观点存在变化。"
    )
    assert result["status"] == "accept"
    assert result["cross_review"]["selected_answer_lines"]
    assert seen


def test_cross_review_handles_more_than_twelve_articles_in_source_batches():
    ledger, refs = _article_ledger(13)
    cross_calls = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [1]}
        if schema_name == "finance_cross_answer_audit.v1":
            cross_calls.append(messages[1]["content"]["allowed_evidence_ids"][0])
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "跨 13 篇文章总结观点变化。 " + " ".join(refs)
    )
    assert result["status"] == "accept"
    assert result["cross_review"]["status"] == "complete"
    assert result["cross_review"]["source_count"] == 13
    assert result["cross_review"]["batch_count"] == 13
    assert set(cross_calls) == set(refs)


def test_large_corpus_does_not_require_unmentioned_reads():
    ledger, refs = _article_ledger(13)

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": [1]}
        if schema_name == "finance_cross_answer_audit.v1":
            return {"checks": []}
        return {
            "checks": [{
                "line_start": 1, "line_end": 1,
                "claim_type": "author_view", "action": "retain",
                "evidence_refs": [refs[0]],
                "reason": "The supplied source supports one part of the claim.",
            }],
            "coverage_complete": True,
        }

    result = AnswerAudit(ledger, complete).call(
        "Cross-author conclusion without source anchors."
    )
    assert result["status"] == "accept"
    assert result["coverage_complete"] is True
    assert "observed sources" not in result["limitations"]
    assert result["used_source_ids"] == [refs[0]]


def test_cross_review_includes_slash_date_table_rows():
    ledger, refs = _article_ledger()
    inspected = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            inspected.append(messages[1]["content"]["numbered_answer"])
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "| 2026/09/01 | 观点变化 |\n| 2026/09/02 | 条件发生变化 |"
    )
    assert result["status"] == "accept"
    assert result["cross_review"]["selected_answer_lines"] == [1, 2]
    assert inspected


def test_cross_review_includes_full_chinese_date_table_rows():
    ledger, refs = _article_ledger()
    inspected = []

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            inspected.append(messages[1]["content"]["numbered_answer"])
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "| 2026年09月01日 | 观点变化 |\n| 2026年09月02日 | 条件发生变化 |"
    )
    assert result["status"] == "accept"
    assert result["cross_review"]["selected_answer_lines"] == [1, 2]
    assert inspected


def test_cross_review_routes_full_chinese_date_rows_to_matching_sources():
    ledger = EvidenceLedger()
    refs_by_date = {}
    for day in (1, 2):
        ref = ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}", "line_start": 1,
            "line_end": 1, "content_hash": f"hash-{day}",
        }, excerpt=f"1: article {day} viewpoint")
        refs_by_date[day] = ref.evidence_id
    inspected_by_source = {}

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            content = messages[1]["content"]
            inspected_by_source[content["allowed_evidence_ids"][0]] = content["numbered_answer"]
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "| 2026\u5e749\u67081\u65e5 | first viewpoint |\n"
        "| 2026\u5e749\u67082\u65e5 | second viewpoint |"
    )

    assert result["cross_review"]["source_line_counts"] == {
        refs_by_date[1]: 1,
        refs_by_date[2]: 1,
    }
    assert inspected_by_source[refs_by_date[1]].startswith("1: | ")
    assert "first viewpoint" in inspected_by_source[refs_by_date[1]]
    assert "second viewpoint" not in inspected_by_source[refs_by_date[1]]
    assert inspected_by_source[refs_by_date[2]].startswith("2: | ")
    assert "second viewpoint" in inspected_by_source[refs_by_date[2]]
    assert "first viewpoint" not in inspected_by_source[refs_by_date[2]]


def test_cross_review_routes_short_chinese_date_rows_to_matching_sources():
    ledger = EvidenceLedger()
    refs_by_date = {}
    for day in (1, 2):
        ref = ledger.add_article({
            "article_id": f"tt-2026-09-{day:02d}", "author": "TT",
            "path": f"C:/articles/TT/articles/2026-09-{day:02d}.md",
            "published_at": f"2026-09-{day:02d}", "line_start": 1,
            "line_end": 1, "content_hash": f"short-hash-{day}",
        }, excerpt=f"1: article {day} viewpoint")
        refs_by_date[day] = ref.evidence_id
    inspected_by_source = {}

    def complete(messages, schema_name):
        if schema_name == "finance_cross_summary_selection.v1":
            return {"lines": []}
        if schema_name == "finance_cross_answer_audit.v1":
            content = messages[1]["content"]
            inspected_by_source[content["allowed_evidence_ids"][0]] = content["numbered_answer"]
            return {"checks": []}
        return {"checks": [], "coverage_complete": True}

    result = AnswerAudit(ledger, complete).call(
        "| 9\u67081\u65e5 | first viewpoint |\n"
        "| 9\u67082\u65e5 | second viewpoint |"
    )

    assert result["cross_review"]["source_line_counts"] == {
        refs_by_date[1]: 1,
        refs_by_date[2]: 1,
    }
    assert "first viewpoint" in inspected_by_source[refs_by_date[1]]
    assert "second viewpoint" not in inspected_by_source[refs_by_date[1]]
    assert "second viewpoint" in inspected_by_source[refs_by_date[2]]
    assert "first viewpoint" not in inspected_by_source[refs_by_date[2]]
from policy_impact.domains.finance.answer_audit import _article_date_in_chunk


def test_answer_audit_matches_unpadded_dates_in_multi_article_table():
    chunk = "| 成交量定行情性质 | 8/12、9/23、09-29 |"
    assert _article_date_in_chunk("2026-08-12", chunk)
    assert _article_date_in_chunk("2026-09-23", chunk)
    assert _article_date_in_chunk("2026-09-29", chunk)
    assert not _article_date_in_chunk("2026-08-13", chunk)
