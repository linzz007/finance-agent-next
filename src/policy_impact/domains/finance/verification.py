"""Aggregate evidence checks in code, rather than trusting a contradictory verdict."""

from typing import Literal
from pydantic import Field

from policy_impact.domains.finance.schemas import FinanceModel, FinanceVerification


class EvidenceCheck(FinanceModel):
    item_id: str
    reason: str = Field(min_length=1, max_length=600)
    verdict: Literal["supported", "unsupported", "contradicted", "uncertain"]


class EvidenceChecks(FinanceModel):
    checks: list[EvidenceCheck] = Field(min_length=1, max_length=12)


def normalize_verification(payload: dict, expected_items: set[str]) -> dict:
    if "checks" not in payload:
        # Older adapters and recorded responses retain their fail-closed contract.
        return FinanceVerification.model_validate(payload).model_dump(mode="json")
    checks = EvidenceChecks.model_validate(payload).checks
    if len(checks) != len(expected_items) or {c.item_id for c in checks} != expected_items:
        raise ValueError("复核项缺失、重复或越界，不能判定整体通过")
    issues = [f"{c.item_id}: {c.reason}" for c in checks if c.verdict != "supported"]
    return {"supported": not issues, "issues": issues}
