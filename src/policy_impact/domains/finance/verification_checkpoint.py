"""Record the main agent's verification decision without selecting its method."""

from __future__ import annotations

from typing import Any, Callable

from policy_impact.domains.finance.evidence import EvidenceLedger


class VerificationCheckpoint:
    def __init__(
        self,
        ledger: EvidenceLedger,
        on_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.on_record = on_record
        self.latest: dict[str, Any] | None = None

    def record(self, checks: list[dict[str, Any]], summary: str = "") -> dict[str, Any]:
        if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
            raise ValueError("verification checkpoint requires 1-20 claim checks")
        if not isinstance(summary, str) or len(summary) > 1000:
            raise ValueError("verification summary must be bounded text")
        normalized = []
        for item in checks:
            if not isinstance(item, dict):
                raise ValueError("each verification check must be an object")
            claim = str(item.get("claim") or "").strip()
            decision = item.get("decision")
            outcome = item.get("outcome")
            method = str(item.get("method") or "").strip()
            reason = str(item.get("reason") or "").strip()
            gap = str(item.get("gap") or "").strip()
            refs = item.get("evidence_refs", [])
            if not claim or len(claim) > 500 or not reason or len(reason) > 1000:
                raise ValueError("each check needs a bounded claim and reason")
            if decision not in {"check", "not_needed"}:
                raise ValueError("decision must be check or not_needed")
            if outcome not in {"supported", "refuted", "inconclusive", "not_applicable", "not_due"}:
                raise ValueError("invalid verification outcome")
            if not isinstance(refs, list) or len(refs) > 20 or any(not isinstance(ref, str) for ref in refs):
                raise ValueError("evidence_refs must be a bounded list of IDs")
            self.ledger.validate(refs)
            if outcome == "not_due" and decision != "check":
                raise ValueError("not_due requires a check decision")
            if decision == "not_needed" and outcome != "not_applicable":
                raise ValueError("not_needed requires not_applicable outcome")
            if decision == "check" and not method:
                raise ValueError("a check needs its method")
            # The model contract exposes both fields, but a verification
            # decision commonly explains the unresolved limitation in `reason`
            # and omits the duplicate `gap`. Preserve that decision instead of
            # retrying the same tool call until the run is stopped.
            if outcome in {"inconclusive", "not_applicable"} and not gap:
                gap = reason
            if decision == "check" and outcome == "not_applicable" and not gap:
                raise ValueError("not-applicable checks must explain why the claim cannot yet be tested")
            if outcome == "not_due" and not gap:
                gap = reason
            if outcome in {"supported", "refuted"} and not refs:
                raise ValueError("supported or refuted checks require observed evidence_refs")
            if decision == "check" and not refs and not gap:
                raise ValueError("a check without observed evidence must state its gap")
            if outcome == "inconclusive" and not gap:
                raise ValueError("inconclusive checks must state the unresolved gap")
            if any(len(value) > 1000 for value in (method, gap)):
                raise ValueError("verification method and gap must be bounded text")
            normalized.append({
                "claim": claim, "decision": decision, "outcome": outcome,
                "method": method, "reason": reason, "gap": gap,
                "evidence_refs": self.ledger.canonical_ids(refs),
            })
        result = {
            "schema_version": "finance.verification_checkpoint.v1",
            "checks": normalized,
            "summary": summary.strip(),
            "gaps": [item["gap"] for item in normalized if item["gap"]],
            "evidence_ids_at_record": list(self.ledger.evidence_ids),
        }
        if self.on_record is not None:
            self.on_record(result)
        self.latest = result
        return result

    def covers_current_evidence(self) -> bool:
        return self.latest is not None and set(self.latest["evidence_ids_at_record"]) == set(self.ledger.evidence_ids)
