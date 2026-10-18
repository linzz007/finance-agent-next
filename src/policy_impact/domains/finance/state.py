"""Canonical state vocabulary for finance research and delivery."""

from __future__ import annotations

from typing import Any, Literal, Mapping


DeliveryStatus = Literal["done", "blocked"]
DeliveryMode = Literal["full", "qualified", "source_only"]
VerificationStatus = Literal["verified", "partial", "disputed", "failed"]
EvidenceCoverage = Literal["complete", "partial", "uncovered"]
AnswerAuditCoverage = Literal["complete", "partial", "failed"]

ANSWER_AUDIT_COVERAGES = {"complete", "partial", "failed"}
EVIDENCE_COVERAGES = {"complete", "partial", "uncovered"}
DELIVERY_MODES = {"full", "qualified", "source_only"}
VERIFICATION_STATUSES = {"verified", "partial", "disputed", "failed"}


def answer_audit_coverage(
    audit: Mapping[str, Any] | None,
    *,
    default: AnswerAuditCoverage = "failed",
) -> AnswerAuditCoverage:
    """Read the canonical audit coverage, accepting the old bool as input only."""
    payload = audit or {}
    value = str(payload.get("answer_audit_coverage") or "").strip().lower()
    if value in ANSWER_AUDIT_COVERAGES:
        return value  # type: ignore[return-value]
    legacy = payload.get("coverage_complete")
    if isinstance(legacy, bool):
        return "complete" if legacy else "partial"
    return default


def legacy_coverage_complete(audit: Mapping[str, Any] | None) -> bool:
    """Compatibility projection for callers that still need the former bool."""
    return answer_audit_coverage(audit) == "complete"


def normalize_delivery_mode(value: Any, default: DeliveryMode = "qualified") -> DeliveryMode:
    mode = str(value or "").strip().lower()
    if mode in DELIVERY_MODES:
        return mode  # type: ignore[return-value]
    return default


def normalize_verification_status(
    value: Any, default: VerificationStatus = "partial"
) -> VerificationStatus:
    status = str(value or "").strip().lower()
    if status == "audit_accepted":
        return "verified"
    if status in VERIFICATION_STATUSES:
        return status  # type: ignore[return-value]
    return default


def normalize_evidence_coverage(
    value: Any, default: EvidenceCoverage = "uncovered"
) -> EvidenceCoverage:
    coverage = str(value or "").strip().lower()
    if coverage in EVIDENCE_COVERAGES:
        return coverage  # type: ignore[return-value]
    return default


__all__ = [
    "AnswerAuditCoverage",
    "DeliveryMode",
    "DeliveryStatus",
    "EvidenceCoverage",
    "VerificationStatus",
    "answer_audit_coverage",
    "legacy_coverage_complete",
    "normalize_delivery_mode",
    "normalize_evidence_coverage",
    "normalize_verification_status",
]
