"""Typed, evidence-bound checks of selected finance article claims."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.research_artifacts import ResearchArtifacts, _horizon_end
from policy_impact.domains.finance.structured_pass import complete_structured


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    description: str = Field(min_length=4, max_length=500)


class TypedCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_type: str
    status: str
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=8)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=8)
    suggested_revision: str = Field(min_length=4, max_length=1200)
    as_of: str
    reason: str = Field(min_length=8, max_length=1200)
    follow_up_queries: list[str] = Field(default_factory=list, max_length=5)


class TypedReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[TypedCheck] = Field(min_length=1, max_length=30)
    limitations: str = Field(default="", max_length=1000)


class ResearchVerification:
    def __init__(
        self, artifacts: ResearchArtifacts, evidence: EvidenceLedger,
        complete: Callable[[list[dict[str, Any]], str], dict[str, Any]] | None,
        source_detail: Callable[[str], Any] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.evidence = evidence
        self.complete = complete
        self.source_detail = source_detail

    def call(
        self, card_ids: list[str], claim_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None, as_of: str | None = None,
    ) -> dict[str, Any]:
        if self.complete is None:
            raise RuntimeError("typed finance verifier model is unavailable")
        if (not isinstance(card_ids, list) or not 1 <= len(card_ids) <= 20
                or any(not isinstance(value, str) for value in card_ids)
                or len(set(card_ids)) != len(card_ids)):
            raise ValueError("verify requires 1-20 distinct card IDs")
        cards = [self.artifacts.call(operation="get", artifact_id=value) for value in card_ids]
        if any(card["kind"] != "article_card" for card in cards):
            raise ValueError("verify accepts article cards only")
        all_claims = {
            claim["claim_id"]: {**claim, "article_id": card["payload"]["article_id"],
                                "author": card["payload"]["author"],
                                "published_at": card["payload"]["published_at"]}
            for card in cards for claim in card["payload"]["claims"]
        }
        selected_ids = claim_ids if claim_ids is not None else list(all_claims)
        if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 30
                or any(not isinstance(value, str) or value not in all_claims for value in selected_ids)
                or len(set(selected_ids)) != len(selected_ids)):
            raise ValueError("claim_ids must select 1-30 distinct claims from the chosen cards")
        selected_external = evidence_ids or []
        if (not isinstance(selected_external, list) or len(selected_external) > 30
                or any(not isinstance(value, str) or not value.startswith("evidence_")
                       for value in selected_external)):
            raise ValueError("evidence_ids must contain at most 30 canonical EvidenceRef IDs")
        self.evidence.validate(selected_external)
        today = as_of or date.today().isoformat()
        try:
            if date.fromisoformat(today).isoformat() != today:
                raise ValueError
        except ValueError as exc:
            raise ValueError("as_of must be YYYY-MM-DD") from exc
        research_date = date.fromisoformat(today)
        claim_views = []
        allowed_statuses: dict[str, list[str]] = {}
        for value in selected_ids:
            claim = all_claims[value]
            horizon_end = _horizon_end(claim["horizon"], claim["published_at"])
            kind = claim["kind"]
            statuses = {
                "author_view": ["viewpoint_faithful", "insufficient_evidence"],
                "objective_fact": ["insufficient_evidence"],
                "prediction": ["viewpoint_faithful", "insufficient_evidence"],
                "causal_explanation": ["viewpoint_faithful", "reasoned", "insufficient_evidence"],
                "model_inference": ["reasoned", "insufficient_evidence"],
            }[kind]
            if kind == "prediction" and horizon_end and horizon_end > research_date:
                statuses.append("not_due")
            if selected_external and (kind == "objective_fact" or
                                      kind == "prediction" and horizon_end and horizon_end <= research_date):
                statuses.extend(["supported", "refuted"])
            allowed_statuses[value] = statuses
            claim_views.append({
                **claim,
                "forecast_horizon_end": horizon_end.isoformat() if horizon_end else None,
                "forecast_due_state": (
                    "not_prediction" if claim["kind"] != "prediction" else
                    "horizon_unknown" if horizon_end is None else
                    "future" if horizon_end > research_date else "due"
                ),
                "allowed_statuses": statuses,
            })
        refs = list(dict.fromkeys([
            *(all_claims[claim_id]["evidence_id"] for claim_id in selected_ids),
            *selected_external,
        ]))
        source_context = []
        for evidence_id in refs:
            item = self.evidence.get(evidence_id)
            if item is None or item.evidence_id != evidence_id:
                raise ValueError("verification requires canonical evidence IDs")
            source_context.append({
                "evidence_id": evidence_id, "source_type": item.source_type,
                "provenance": item.provenance, "published_at": item.published_at,
                "source_url": item.source_url, "article_id": item.article_id,
                "line_start": item.line_start, "line_end": item.line_end,
                "excerpt": item.excerpt, "metadata": item.metadata or {},
                "observed_detail": self.source_detail(evidence_id) if self.source_detail else None,
            })
        if len(json.dumps(source_context, ensure_ascii=False, default=str)) > 120_000:
            raise ValueError("selected evidence exceeds one verifier context; check smaller claim/source groups")
        messages = [
            {"role": "system", "content": (
                "Check selected finance claims one by one against only the supplied observed "
                "EvidenceRefs. Return JSON type=final with output.checks and output.limitations. "
                "For each claim return claim_id, claim_type, status, evidence_refs, conflicts, "
                "alternative_explanations, suggested_revision, as_of, reason and optional "
                "follow_up_queries. Choose status only from that claim's allowed_statuses; "
                "objective_fact cannot be viewpoint_faithful even if an author states it. "
                "First preserve exactly what an "
                "author said. Article text proves attribution, not external factual truth. "
                "Split objective assertions conceptually into atomic date, number, subject "
                "and period checks; support/refute only when an independent public or market "
                "source directly matches all material parts. For predictions preserve author, "
                "as_of date, horizon and conditions. For predictions use the supplied "
                "forecast_due_state: only future with a parseable forecast_horizon_end may "
                "be not_due; horizon_unknown is insufficient_evidence or viewpoint_faithful, "
                "never not_due/supported/refuted; due requires independent dated evidence "
                "before supported/refuted. For causality "
                "check event timing, author's attribution and alternative explanations; mere "
                "correlation does not establish cause. For model inference identify premises "
                "and keep it distinct from source quotes. Use insufficient_evidence when data "
                "is missing and suggest precise follow-up queries; never upgrade source "
                "agreement into proof. Proposed revisions may qualify or remove the agent's "
                "own assertion, but must not erase a faithfully attributed author viewpoint "
                "solely because external evidence disagrees. Copy the requested as_of date "
                "into every checks[].as_of; do not substitute an article publication date "
                "or claim horizon. No fabricated evidence IDs."
            )},
            {"role": "user", "content": {
                "as_of": today, "claims": claim_views,
                "evidence": source_context,
            }},
        ]
        def validate_batch(batch: TypedReviewBatch) -> None:
            returned_ids = [check.claim_id for check in batch.checks]
            if set(returned_ids) != set(selected_ids) or len(returned_ids) != len(selected_ids):
                missing = sorted(set(selected_ids) - set(returned_ids))
                unexpected = sorted(set(returned_ids) - set(selected_ids))
                raise ValueError(
                    "typed review must cover each selected claim exactly once; "
                    f"missing={missing}, unexpected={unexpected}, duplicates="
                    f"{len(returned_ids) - len(set(returned_ids))}"
                )
            if any(check.as_of != today for check in batch.checks):
                got = [(check.claim_id, check.as_of) for check in batch.checks
                       if check.as_of != today]
                raise ValueError(
                    f"every checks[].as_of must equal requested research date {today}; "
                    f"wrong_dates={got}"
                )
            invalid_statuses = [
                (check.claim_id, check.status, allowed_statuses[check.claim_id])
                for check in batch.checks
                if check.status not in allowed_statuses[check.claim_id]
            ]
            if invalid_statuses:
                raise ValueError(f"review statuses outside per-claim allowed_statuses: {invalid_statuses}")
            if any(not set(check.evidence_refs).issubset(refs) for check in batch.checks):
                raise ValueError("typed review cited evidence that was not supplied to this model pass")
            if any(any(not isinstance(query, str) or not 2 <= len(query.strip()) <= 180
                       for query in check.follow_up_queries) for check in batch.checks):
                raise ValueError("follow-up queries must be bounded search questions")
            self.artifacts._review({
                "card_ids": card_ids,
                "checks": [check.model_dump(exclude={"follow_up_queries"}) for check in batch.checks],
                "limitations": batch.limitations,
            })

        batch = complete_structured(
            self.complete, messages, "finance_typed_review.v1", TypedReviewBatch,
            validate=validate_batch,
        )
        saved = self.artifacts.call(operation="save", kind="review", payload={
            "card_ids": card_ids,
            "checks": [check.model_dump(exclude={"follow_up_queries"}) for check in batch.checks],
            "limitations": batch.limitations,
        })
        record = self.artifacts.call(operation="get", artifact_id=saved["artifact_id"])
        return {
            "review_id": saved["artifact_id"], "check_count": saved["check_count"],
            "checks": record["payload"]["checks"],
            "follow_up_queries": [
                {"claim_id": check.claim_id, "query": query}
                for check in batch.checks for query in check.follow_up_queries
            ],
            "limitations": batch.limitations,
            "review_scope": "model_pass_over_observed_evidence",
            "notice": "A separate model pass is not independent source acquisition; decisive facts need separately read evidence.",
        }


__all__ = ["ResearchVerification"]
