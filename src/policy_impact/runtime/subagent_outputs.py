"""Strict public output contracts for the default delegated roles."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, ValidationInfo, field_validator, model_validator


Confidence = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class _StrictFrozenOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def reject_blank_strings(cls, value, info: ValidationInfo):
        if isinstance(value, str) and not value.strip():
            if info.field_name in {"source_url", "supporting_quote"}:
                return ""
            raise ValueError("string fields must not be blank")
        return value.strip() if isinstance(value, str) else value


class EvidenceItem(_StrictFrozenOutput):
    source_ref: StrictStr
    summary: StrictStr
    source_url: StrictStr = ""


class CollectorOutput(_StrictFrozenOutput):
    evidence: tuple[EvidenceItem, ...]


class ClaimItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    statement: StrictStr
    evidence_refs: tuple[StrictStr, ...]
    claim_confidence_label: Literal["high", "medium", "low"] | None = None
    confidence_reason: StrictStr | None = None
    # Input-only compatibility for older role prompts and saved scripted runs.
    # New role outputs should use claim_confidence_label and confidence_reason.
    confidence: Confidence | None = None


class AnalystOutput(_StrictFrozenOutput):
    claims: tuple[ClaimItem, ...]


class CounterexampleItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    issue: StrictStr
    evidence_refs: tuple[StrictStr, ...]


class SkepticOutput(_StrictFrozenOutput):
    counterexamples: tuple[CounterexampleItem, ...]
    notes: tuple[StrictStr, ...]


class VerdictItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    decision: Literal["pass", "fail", "unverified"]
    evidence_refs: tuple[StrictStr, ...]
    reason: StrictStr
    supporting_quote: StrictStr = ""
    supporting_quotes: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def require_evidence_for_decisive_verdict(self):
        if self.decision in {"pass", "fail"} and not self.evidence_refs:
            raise ValueError("pass/fail verdicts require a source evidence reference")
        return self


class VerifierOutput(_StrictFrozenOutput):
    verdicts: tuple[VerdictItem, ...]


class EditorOutput(_StrictFrozenOutput):
    answer: StrictStr
    verified_claim_refs: tuple[StrictStr, ...]


__all__ = [
    "AnalystOutput",
    "ClaimItem",
    "CollectorOutput",
    "CounterexampleItem",
    "EditorOutput",
    "EvidenceItem",
    "SkepticOutput",
    "VerdictItem",
    "VerifierOutput",
]
