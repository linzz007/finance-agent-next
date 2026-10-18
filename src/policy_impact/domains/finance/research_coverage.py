"""Record why the main agent continues or stops acquiring article evidence."""

from __future__ import annotations

from typing import Any, Callable

from policy_impact.domains.finance.evidence import EvidenceLedger


class ResearchCoverage:
    def __init__(
        self, ledger: EvidenceLedger,
        on_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.on_record = on_record
        self.latest: dict[str, Any] | None = None

    def record(
        self, *, aspects: list[dict[str, Any]], decision: str,
        reason: str, next_need: str = "", sample_scope: str = "",
    ) -> dict[str, Any]:
        if decision not in {"continue", "synthesize"}:
            raise ValueError("coverage decision must be continue or synthesize")
        if not isinstance(aspects, list) or not 1 <= len(aspects) <= 12:
            raise ValueError("coverage requires 1-12 question aspects")
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
            raise ValueError("coverage reason must explain the decision")
        if not isinstance(next_need, str) or len(next_need) > 500:
            raise ValueError("next_need must be bounded text")
        if not isinstance(sample_scope, str) or len(sample_scope) > 500:
            raise ValueError("sample_scope must be bounded text")
        if decision == "continue" and not next_need.strip():
            raise ValueError("continuing research requires a specific next information need")

        normalized: list[dict[str, Any]] = []
        for item in aspects:
            if not isinstance(item, dict):
                raise ValueError("each coverage aspect must be an object")
            question = str(item.get("question") or "").strip()
            status = item.get("status")
            gap = str(item.get("gap") or "").strip()
            refs = item.get("evidence_refs", [])
            contrary = item.get("contrary_evidence_refs", [])
            if not question or len(question) > 300 or status not in {"covered", "partial", "uncovered"}:
                raise ValueError("each aspect needs a bounded question and valid status")
            if any(not isinstance(value, list) or len(value) > 64 or
                   any(not isinstance(ref, str) for ref in value) for value in (refs, contrary)):
                raise ValueError("coverage evidence refs must be bounded lists of IDs")
            self.ledger.validate([*refs, *contrary])
            if status == "covered" and not refs:
                raise ValueError("covered aspects require observed evidence")
            if status != "covered" and not gap:
                raise ValueError("partial or uncovered aspects require a gap")
            if len(gap) > 500:
                raise ValueError("coverage gap must be bounded text")
            normalized.append({
                "question": question, "status": status, "gap": gap,
                "evidence_refs": self.ledger.canonical_ids(refs),
                "contrary_evidence_refs": self.ledger.canonical_ids(contrary),
            })
        result = {
            "schema_version": "finance.research_coverage.v1",
            "aspects": normalized,
            "decision": decision,
            "reason": reason.strip(),
            "next_need": next_need.strip(),
            "sample_scope": sample_scope.strip(),
            "evidence_ids_at_record": list(self.ledger.evidence_ids),
        }
        self.latest = result
        if self.on_record:
            self.on_record(result)
        return result
