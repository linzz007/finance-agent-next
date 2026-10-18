"""Verification decisions are traceable without requiring a fixed research path."""

import pytest

from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.verification_checkpoint import VerificationCheckpoint


def _check(**overrides):
    return {
        "claim": "The author's outlook is conditional",
        "decision": "check",
        "outcome": "inconclusive",
        "method": "Compare the observed article passage",
        "evidence_refs": [],
        "reason": "The scripted ledger has no source text",
        "gap": "Read the article before making a factual claim",
        **overrides,
    }


def test_checkpoint_records_decision_result_and_gap_without_forcing_a_tool():
    records = []
    checkpoint = VerificationCheckpoint(EvidenceLedger(), records.append)
    result = checkpoint.record([_check()])
    assert result["checks"][0]["outcome"] == "inconclusive"
    assert result["gaps"] == ["Read the article before making a factual claim"]
    assert records == [result]
    assert checkpoint.covers_current_evidence()


def test_checkpoint_rejects_unknown_evidence_and_unsupported_certainty():
    checkpoint = VerificationCheckpoint(EvidenceLedger())
    with pytest.raises(ValueError, match="unknown evidence"):
        checkpoint.record([_check(evidence_refs=["evidence_not_observed"])])
    with pytest.raises(ValueError, match="observed evidence_refs"):
        checkpoint.record([_check(outcome="supported", gap="")])
    with pytest.raises(ValueError, match="not_needed"):
        checkpoint.record([_check(decision="not_needed", outcome="supported")])
    assert checkpoint.latest is None


def test_new_evidence_requires_a_fresh_checkpoint():
    ledger = EvidenceLedger()
    checkpoint = VerificationCheckpoint(ledger)
    checkpoint.record([_check()])
    ledger.add_reference(source_type="public_page", provenance="web.open", source_ref="https://example.org/source")
    assert not checkpoint.covers_current_evidence()
    checkpoint.record([_check()])
    assert checkpoint.covers_current_evidence()


def test_not_yet_due_forecast_can_be_checked_without_pretending_it_was_verified():
    checkpoint = VerificationCheckpoint(EvidenceLedger())
    result = checkpoint.record([_check(
        claim="The October forecast has not reached its horizon",
        outcome="not_applicable", method="Check the forecast horizon",
        gap="The forecast period has not ended",
    )])
    assert result["checks"][0]["outcome"] == "not_applicable"
    due = checkpoint.record([_check(
        outcome="not_due", reason="The forecast horizon is still in the future", gap="",
    )])
    assert due["checks"][0]["outcome"] == "not_due"
    assert due["gaps"] == ["The forecast horizon is still in the future"]
    implicit_gap = checkpoint.record([_check(outcome="not_applicable", gap="")])
    assert implicit_gap["gaps"] == ["The scripted ledger has no source text"]
    with pytest.raises(ValueError, match="bounded claim and reason"):
        checkpoint.record([_check(outcome="not_applicable", reason="", gap="")])
    with pytest.raises(ValueError, match="check decision"):
        checkpoint.record([_check(decision="not_needed", outcome="not_due")])
