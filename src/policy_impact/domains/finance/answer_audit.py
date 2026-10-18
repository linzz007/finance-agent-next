"""Audit material claims in a candidate finance answer before publication."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from policy_impact.domains.finance.article_gateway import OFFICIAL_DOMAINS
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.state import answer_audit_coverage
from policy_impact.domains.finance.structured_pass import complete_structured
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.skills.schemas import FinanceReviewTarget

_EVIDENCE_ID = re.compile(r"evidence_[0-9a-f]{24}")
_TYPES = {"author_view", "objective_fact", "prediction", "causal_explanation", "model_inference", "personal_context"}
_ACTIONS = {"retain", "qualify", "revise", "delete"}


def _is_official_publisher(row: dict[str, Any]) -> bool:
    if row.get("source_type") != "public_web_page":
        return False
    host = (urlparse(str(row.get("source_url") or "")).hostname or "").casefold()
    return any(host == domain or host.endswith("." + domain)
               for domain in OFFICIAL_DOMAINS)


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    claim_type: str
    action: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=8, max_length=600)
    suggested_revision: str = Field(default="", max_length=600)


class AnswerAuditBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[AnswerClaim] = Field(default_factory=list, max_length=50)
    answer_audit_coverage: Literal["complete", "partial", "failed"] | None = None
    # Read-compatible input for older model adapters. New prompts and outputs use
    # answer_audit_coverage instead of this boolean.
    coverage_complete: bool | None = None
    limitations: str = Field(default="", max_length=1000)


class TargetAuditClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=80)
    status: Literal[
        "viewpoint_faithful",
        "supported",
        "refuted",
        "reasoned",
        "not_due",
        "insufficient_evidence",
    ]
    action: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=8, max_length=800)
    suggested_revision: str = Field(default="", max_length=800)
    answer_quote: str = Field(default="", max_length=500)


class TargetReviewCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_target_ids: list[str] = Field(default_factory=list, max_length=40)
    unchecked_target_ids: list[str] = Field(default_factory=list, max_length=40)
    checked_evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    unchecked_evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class TargetAuditBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[TargetAuditClaim] = Field(default_factory=list, max_length=40)
    answer_audit_coverage: Literal["complete", "partial", "failed"] | None = None
    coverage_complete: bool | None = None
    limitations: str = Field(default="", max_length=1200)
    coverage_detail: TargetReviewCoverage | None = None


class TargetAuditClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_audit_coverage: Literal["complete", "partial", "failed"]
    coverage_detail: TargetReviewCoverage
    limitations: str = Field(default="", max_length=1200)


class ReviewTargetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_targets: list[FinanceReviewTarget] = Field(max_length=40)
    selection_reason: str = Field(min_length=8, max_length=1200)


class CrossArticleIssue(AnswerClaim):
    answer_quote: str = Field(min_length=4, max_length=180)
    source_quote: str = Field(min_length=4, max_length=180)


class CrossArticleBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CrossArticleIssue] = Field(default_factory=list, max_length=8)


class CrossSummarySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[int] = Field(default_factory=list, max_length=30)


def _audit_payload(payload: dict[str, Any], *, default: str = "failed") -> dict[str, Any]:
    """Add the canonical field while retaining the legacy bool for old callers."""
    coverage = answer_audit_coverage(payload, default=default)  # type: ignore[arg-type]
    payload["answer_audit_coverage"] = coverage
    payload["coverage_complete"] = coverage == "complete"
    return payload


def _batch_is_complete(batch: BaseModel) -> bool:
    return answer_audit_coverage(batch.model_dump(), default="partial") == "complete"


class AuthorCoverageClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    answer_quote: str = Field(min_length=4, max_length=300)
    authors: list[str] = Field(min_length=2, max_length=12)


class AuthorCoverageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[AuthorCoverageClaim] = Field(default_factory=list, max_length=12)


class AuthorCoverageFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    source_quote: str = Field(default="", max_length=300)
    reason: str = Field(min_length=8, max_length=500)


class CrossIssueVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_index: int = Field(ge=0)
    decision: str
    reason: str = Field(min_length=8, max_length=500)


class CrossIssueReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[CrossIssueVerdict] = Field(default_factory=list, max_length=50)


class AnswerAudit:
    def __init__(
        self, evidence: EvidenceLedger,
        complete: Callable[[list[dict[str, Any]], str], dict[str, Any]],
    ) -> None:
        self.evidence = evidence
        self.complete = complete
        self._cache: dict[str, dict[str, Any]] = {}
        self._batch_cache: dict[str, AnswerAuditBatch] = {}
        self._target_cache: dict[str, TargetAuditBatch] = {}
        self._target_history: dict[str, list[dict[str, Any]]] = {}
        self._selection_cache: dict[str, dict[str, Any]] = {}

    def select_review_targets(
        self, answer: str, *, question: str,
        personal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask the main model for missing metadata, never for replacement prose."""
        observed = self.evidence.bundle().get("evidence") or []
        source_catalog = [{key: row.get(key) for key in (
            "evidence_id", "source_type", "article_id", "author", "source_ref",
            "source_url", "published_at", "line_start", "line_end",
        )} for row in observed]
        messages = [
            {"role": "system", "content": (
                "You are the main finance research Agent completing omitted review metadata "
                "for your existing candidate answer. Select the material claims or reasoning "
                "nodes that matter for the user's question. Do not split every sentence into "
                "a target. A source catalog identifies observed material; it does not prove "
                "a claim or mean every source must be used. Do not research, call tools, "
                "invent EvidenceRefs, assess correctness, or rewrite the candidate answer. "
                "Return exactly type=final with output containing only review_targets and "
                "selection_reason. If no separate semantic target needs checking, an empty "
                "review_targets list is valid with a specific reason. This selection is not "
                "itself a completed audit. Follow the supplied JSON schema exactly."
            )},
            {"role": "user", "content": {
                "user_question": question, "candidate_answer": answer,
                "observed_source_catalog": source_catalog,
                "personal_context": personal_context or {},
                "required_output_schema": ReviewTargetSelection.model_json_schema(),
            }},
        ]
        signature = _audit_signature(messages)
        if signature in self._selection_cache:
            return deepcopy(self._selection_cache[signature])

        def validate(selection: ReviewTargetSelection) -> None:
            if not selection.selection_reason.strip():
                raise ValueError("selection_reason must explain the selected or empty review scope")
            ids = [target.target_id for target in selection.review_targets]
            if len(ids) != len(set(ids)):
                raise ValueError("review target_id values must be unique")
            known = {row["evidence_id"] for row in observed}
            for target in selection.review_targets:
                if set(target.evidence_refs) - known:
                    raise ValueError("review_targets may cite only observed catalog EvidenceRefs")

        selection = complete_structured(
            self.complete, messages, "finance_review_targets.v1", ReviewTargetSelection,
            attempts=2, validate=validate,
            repair_schema_name="finance_review_targets.normalize.v1", repair_attempts=1,
            repair_instruction=(
                "Repair only the review target metadata and selection_reason. Do not return "
                "or rewrite the candidate answer, perform new research, or invent source IDs."
            ),
        )
        result = selection.model_dump(mode="json")
        self._selection_cache[signature] = deepcopy(result)
        return result

    def _call_selected_targets(
        self,
        answer: str,
        *,
        question: str,
        personal_context: dict[str, Any] | None,
        review_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Audit only material targets selected by the main Agent.

        This path deliberately does not split the answer into sentence or line
        chunks.  The target carries the semantic unit that matters for the
        current question; Runtime only validates references and the returned
        contract.
        """
        observed = self.evidence.bundle().get("evidence") or []
        known = {item["evidence_id"]: item for item in observed}
        normalized: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        input_details: list[dict[str, Any]] = []
        for index, raw in enumerate(review_targets):
            if not isinstance(raw, dict):
                issues.append({
                    "target_id": f"invalid-{index + 1}",
                    "issue_kind": "review_execution_gap",
                    "action": "revise",
                    "claim_type": "model_inference",
                    "reason": "Review target must be a structured object selected by the main Agent.",
                    "suggested_revision": "Regenerate only the review_targets structure.",
                    "span": "",
                })
                continue
            try:
                target = FinanceReviewTarget.model_validate(raw).model_dump(mode="json")
            except ValidationError as exc:
                issues.append({
                    "target_id": str(raw.get("target_id") or f"invalid-{index + 1}").strip(),
                    "issue_kind": "review_execution_gap",
                    "action": "revise",
                    "claim_type": str(raw.get("type") or "model_inference"),
                    "reason": "The main Agent selected an invalid review target: "
                    + "; ".join(
                        str(error.get("msg") or "invalid target")
                        for error in exc.errors()[:3]
                    ),
                    "suggested_revision": "Regenerate the target with valid scope, relation and evidence fields.",
                    "span": str(raw.get("claim_or_question") or "")[:500],
                })
                continue
            normalized.append(target)

        input_details.extend({
            "target_id": issue["target_id"], "execution_status": "failed",
            "checked": False, "provided_evidence_ids": [],
            "checked_evidence_ids": [], "unchecked_evidence_ids": [],
            "gap_types": ["invalid_target"],
        } for issue in issues)
        if not normalized:
            return _audit_payload({
                "status": "revise" if issues else "accept",
                "review_scope": "main_agent_selected_targets",
                "review_status": "failed" if issues else "not_requested",
                "checks": [],
                "checked_target_ids": [],
                "unchecked_target_ids": [item["target_id"] for item in input_details],
                "coverage_detail": {"targets": input_details},
                "issues": issues,
                "reviewed_claim_count": 0,
                "answer_audit_coverage": "failed" if issues else "complete",
                "coverage_complete": not issues,
                "limitations": "The main Agent did not provide a usable review target."
                if issues else "",
                "source_ids": [],
                "used_source_ids": [],
                "author_coverage": {"status": "not_applicable", "findings": []},
                "cross_review": {"status": "not_applicable", "issue_count": 0},
            }, default="failed" if issues else "complete")

        target_ids = [target["target_id"] for target in normalized]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("review target_id values must be unique")

        system = (
            "Audit one semantic review target selected by the main finance Agent. "
            "Do not split the candidate answer into sentences and do not audit claims "
            "that are outside this target. The target may represent a source-attributed "
            "author view, an objective fact, a forecast, a causal explanation, a model "
            "inference, or a cross-article synthesis. Preserve an author's view when it "
            "is faithfully supported by the supplied article, even if independent evidence "
            "contradicts it. Use refuted only for a claim-matched independent source; use "
            "insufficient_evidence when the supplied material cannot decide. A source article "
            "proves attribution, not market truth. Do not invent missing evidence. Return "
            "exactly type=final with output.checks containing one check for the supplied target. "
            "The check must contain target_id, status, action, evidence_refs, reason, "
            "suggested_revision and optional answer_quote. Allowed status values are "
            "viewpoint_faithful, supported, refuted, reasoned, not_due and "
            "insufficient_evidence. Allowed action values are retain, qualify, revise and delete. "
            "answer_audit_coverage=complete means this target was actually assessed, not that it was proven; "
            "partial means the target was only partly assessed and failed means no usable target audit was produced. "
            "limitations may explain completed inconclusive checks; nonempty limitations do not mean partial. "
            "Optional coverage_detail lists checked_target_ids, unchecked_target_ids, "
            "checked_evidence_ids and unchecked_evidence_ids for the supplied target and inputs. "
            "supported/refuted require a claim-matched independent non-author source in evidence_refs; "
            "viewpoint_faithful, reasoned, not_due and insufficient_evidence are not factual verification. "
            "Do not call tools or perform new research. Follow required_output_schema."
        )
        checks: list[dict[str, Any]] = []
        used_source_ids: list[str] = []
        coverage_complete = not issues
        limitations: list[str] = []
        cached_target_ids: list[str] = []

        for target in normalized:
            explicit_refs = list(dict.fromkeys(target["evidence_refs"]))
            unknown_refs = [value for value in explicit_refs if value not in known]
            selected_ids = [value for value in explicit_refs if value in known]
            missing_articles = []
            for article_ref in target["article_refs"]:
                def matches(row, article_ref=article_ref):
                    return article_ref in {row.get("article_id"), row.get("source_ref")}

                if not any(matches(known[value]) for value in selected_ids):
                    matching_ids = [value for value, row in known.items() if matches(row)]
                    selected_ids.extend(matching_ids)
                    if not matching_ids:
                        missing_articles.append(article_ref)
            selected_ids = list(dict.fromkeys(selected_ids))
            review_answer, answer_context = _target_answer_context(answer, target["answer_context"])
            detail: dict[str, Any] = {
                "target_id": target["target_id"], "execution_status": "failed", "checked": False,
                "requested_article_refs": target["article_refs"],
                "available_article_refs": [value for value in target["article_refs"] if value not in missing_articles],
                "provided_article_refs": [], "requested_evidence_ids": explicit_refs,
                "required_evidence_ids": selected_ids.copy(),
                "missing_article_refs": missing_articles, "missing_evidence_ids": unknown_refs,
                "provided_evidence_ids": [], "checked_evidence_ids": [],
                "unchecked_evidence_ids": selected_ids.copy(), "truncated_evidence_ids": [],
                "gap_types": [], "answer_context": answer_context, "cache_hit": False,
            }
            input_details.append(detail)
            if unknown_refs:
                detail["gap_types"].append("unobserved_evidence")
                coverage_complete = False
                issues.append({
                    **target,
                    "action": "revise",
                    "claim_type": target["type"],
                    "evidence_refs": [],
                    "reason": "The review target contains EvidenceRef values not observed in this run: "
                    + ", ".join(unknown_refs[:6]),
                    "suggested_revision": "Use only EvidenceRef values returned by the current run.",
                    "span": target["claim_or_question"],
                })
                continue
            if missing_articles:
                detail["gap_types"].append("missing_participant")
                coverage_complete = False
                limitations.append(
                    f"Target {target['target_id']} has no observed article evidence for: "
                    + ", ".join(missing_articles)
                )
                issues.append({
                    **target, "action": "qualify", "claim_type": target["type"],
                    "evidence_refs": selected_ids,
                    "reason": "Participating articles have not been observed: " + ", ".join(missing_articles),
                    "suggested_revision": "Read the missing participant or narrow the claimed comparison scope.",
                    "span": target["claim_or_question"],
                })
                continue
            if len(selected_ids) > 12:
                detail["gap_types"].append("input_limit")
                coverage_complete = False
                limitations.append(
                    f"Target {target['target_id']} selected more than 12 evidence references; "
                    "the target must be narrowed before it can be fully reviewed."
                )
                selected_ids = selected_ids[:12]
            if not selected_ids:
                detail["gap_types"].append("missing_evidence")
                coverage_complete = False
                issues.append({
                    **target,
                    "action": "qualify",
                    "claim_type": target["type"],
                    "evidence_refs": [],
                    "reason": "This material review target has no observed EvidenceRef.",
                    "suggested_revision": "Read the relevant article or independent source before presenting it as supported.",
                    "span": target["claim_or_question"],
                })
                continue

            for evidence_id in selected_ids:
                if evidence_id not in used_source_ids:
                    used_source_ids.append(evidence_id)
            source_context = [
                _audit_source(known[evidence_id], complete_observation=True)
                for evidence_id in selected_ids
            ]
            detail["provided_evidence_ids"] = selected_ids.copy()
            detail["provided_article_refs"] = [
                value for value in target["article_refs"] if any(
                    value in {known[evidence_id].get("article_id"), known[evidence_id].get("source_ref")}
                    for evidence_id in selected_ids
                )
            ]
            detail["truncated_evidence_ids"] = [
                source["evidence_id"] for source in source_context
                if source["excerpt_truncated"] or not source["excerpt"].strip()
            ]
            if detail["truncated_evidence_ids"]:
                detail["gap_types"].append("incomplete_source")
                coverage_complete = False
                limitations.append(f"Target {target['target_id']} has incomplete or invalid observed source text.")
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": {
                    "user_question": question,
                    "candidate_answer": review_answer,
                    "target": target,
                    "observed_evidence": source_context,
                    "allowed_evidence_ids": selected_ids,
                    "personal_context": personal_context or {},
                    "required_output_schema": TargetAuditBatch.model_json_schema(),
                }},
            ]

            def validate_target_batch(
                batch: TargetAuditBatch, target_id=target["target_id"], allowed_ids=tuple(selected_ids),
            ) -> None:
                if len(batch.checks) > 1 or (batch.checks and batch.checks[0].target_id != target_id):
                    raise ValueError(
                        "target audit must return exactly one check for the supplied target_id"
                    )
                if not batch.checks:
                    return
                check = batch.checks[0]
                if check.action not in _ACTIONS:
                    raise ValueError("target audit action is invalid")
                invalid = sorted(set(check.evidence_refs) - set(allowed_ids))
                if invalid:
                    raise ValueError(
                        f"target audit cited unobserved evidence IDs {invalid}"
                    )
                if check.status in {"supported", "refuted"} and not _has_independent_source(
                    check.evidence_refs, known, allowed_ids,
                ):
                    raise ValueError("supported/refuted require a bound independent non-author source")

            target_signature = _audit_signature({
                "contract": system, "schema": TargetAuditBatch.model_json_schema(),
                "target_schema": FinanceReviewTarget.model_json_schema(),
                "clarification_schema": TargetAuditClarification.model_json_schema(),
                "candidate_answer": review_answer, "question": question,
                "personal_context": personal_context or {},
                "target": {key: value for key, value in target.items()
                           if key not in {"target_id", "answer_context"}},
                "sources": [known[value] for value in selected_ids],
                "observed_evidence": source_context,
            })
            detail["input_signature"] = target_signature
            history = self._target_history.setdefault(target_signature, [])
            detail["previous_target_ids"] = list(dict.fromkeys(item["target_id"] for item in history))
            batch = self._target_cache.get(target_signature)
            try:
                if batch is not None:
                    batch = batch.model_copy(deep=True)
                    detail["reused_from_target_id"] = batch.checks[0].target_id
                    detail["cache_hit"] = True
                    _remap_target_batch(batch, target["target_id"])
                    cached_target_ids.append(target["target_id"])
                else:
                    batch = complete_structured(
                        self.complete,
                        messages,
                        "finance_target_answer_audit.v1",
                        TargetAuditBatch,
                        validate=validate_target_batch,
                        repair_schema_name="finance_review.normalize.v1",
                        repair_instruction=(
                            "Only normalize the supplied target audit into the required schema. "
                            "Keep target_id and evidence_refs within the supplied allowed values; "
                            "if the target cannot be decided, use insufficient_evidence and qualify."
                        ),
                    )
                    contradictions = _target_coverage_problems(batch, target["target_id"], selected_ids)
                    detail["metadata_clarification"] = "not_needed"
                    if contradictions:
                        detail["metadata_clarification"] = "failed"
                        clarification_messages = [
                            {"role": "system", "content": (
                                "Clarify execution and input coverage metadata for an existing review. "
                                "Return exactly type=final with output containing only answer_audit_coverage, "
                                "coverage_detail and limitations, following required_output_schema. "
                                "Do not return checks, rewrite the candidate, change verdicts, invent sources, "
                                "call tools or perform research. A missing check remains unchecked. "
                                "Complete execution does not establish factual truth."
                            )}, messages[1], {"role": "user", "content": {
                            "repair_mode": "review.coverage_metadata",
                            "previous_review": batch.model_dump(mode="json"),
                            "contradictions": contradictions,
                            "instruction": (
                                "Clarify only execution/input coverage metadata and limitations. "
                                "Do not rewrite checks, verdicts, the candidate, or sources; do not research. "
                                "A missing check cannot be manufactured by metadata clarification."
                            ),
                            "required_output_schema": TargetAuditClarification.model_json_schema(),
                        }}]
                        try:
                            clarification = complete_structured(
                                self.complete, clarification_messages,
                                "finance_review.coverage_metadata.v1", TargetAuditClarification,
                                attempts=1,
                            )
                            clarified = batch.model_copy(update={
                                **clarification.model_dump(exclude={"coverage_detail"}),
                                "coverage_detail": clarification.coverage_detail,
                                "coverage_complete": None,
                            }, deep=True)
                            if _target_coverage_problems(clarified, target["target_id"], selected_ids):
                                raise ValueError("clarified coverage still contradicts the actual review")
                            batch = clarified
                            detail["metadata_clarification"] = "complete"
                        except (ValueError, RuntimeError, TimeoutError, ConnectionError) as exc:
                            if isinstance(exc, RuntimeError) and not isinstance(exc, ModelInvocationError) and "request budget exhausted" not in str(exc):
                                raise
                            batch = batch.model_copy(update={
                                "answer_audit_coverage": "partial" if batch.checks else "failed",
                                "coverage_complete": False, "coverage_detail": None,
                            }, deep=True)
                            detail["gap_types"].append("metadata_contradiction")
                            limitations.append(f"Target {target['target_id']} coverage clarification failed.")
                    if _batch_is_complete(batch) and not detail["gap_types"]:
                        self._target_cache[target_signature] = batch.model_copy(deep=True)
            except (ValueError, RuntimeError, TimeoutError, ConnectionError) as exc:
                budget_error = isinstance(exc, RuntimeError) and "request budget exhausted" in str(exc)
                if isinstance(exc, RuntimeError) and not isinstance(exc, ModelInvocationError) and not budget_error:
                    raise
                coverage_complete = False
                failure_kind = "budget" if budget_error else "structure" if isinstance(exc, ValueError) else "model_transport"
                detail["gap_types"].append(failure_kind)
                history.append({"target_id": target["target_id"], "coverage": "failed", "gap_types": detail["gap_types"].copy()})
                detail["history_count"] = len(history)
                detail["audit_attempt_count"] = sum(not item.get("cache_hit", False) for item in history)
                limitations.append(
                    f"Target {target['target_id']} audit failed ({failure_kind}); completed targets are retained."
                )
                issues.append({
                    **target,
                    "action": "qualify",
                    "claim_type": target["type"],
                    "evidence_refs": selected_ids,
                    "reason": "The target audit did not return a valid result (" + failure_kind + ").",
                    "issue_kind": "review_execution_gap",
                    "suggested_revision": "Keep the target as an explicitly unreviewed research conclusion.",
                    "span": target["claim_or_question"],
                })
                continue

            complete_target = _batch_is_complete(batch) and not detail["gap_types"] and bool(batch.checks)
            detail["execution_status"] = "complete" if complete_target else "partial" if batch.checks else "failed"
            detail["checked"] = complete_target
            reported_inputs = batch.coverage_detail
            detail["checked_evidence_ids"] = (
                reported_inputs.checked_evidence_ids.copy() if reported_inputs else
                selected_ids.copy() if batch.checks and _batch_is_complete(batch) else []
            )
            detail["checked_evidence_ids"] = [value for value in detail["checked_evidence_ids"]
                                              if value not in detail["truncated_evidence_ids"]]
            detail["unchecked_evidence_ids"] = [value for value in detail["unchecked_evidence_ids"]
                                                if value not in detail["checked_evidence_ids"]]
            if not complete_target and not detail["gap_types"]:
                detail["gap_types"].append("incomplete_review")
            history.append({"target_id": target["target_id"], "coverage": detail["execution_status"],
                            "gap_types": detail["gap_types"].copy(), "cache_hit": target["target_id"] in cached_target_ids})
            detail["history_count"] = len(history)
            detail["audit_attempt_count"] = sum(not item.get("cache_hit", False) for item in history)
            coverage_complete = coverage_complete and complete_target
            if batch.limitations:
                limitations.append(batch.limitations)
            if not batch.checks:
                issues.append({**target, "claim_type": target["type"], "action": "qualify",
                               "issue_kind": "review_execution_gap",
                               "span": target["claim_or_question"], "reason": "No usable target check was returned.",
                               "suggested_revision": "Disclose this target as unchecked."})
                continue
            row = batch.checks[0].model_dump()
            row["fact_verified"] = complete_target and target["type"] == "objective_fact" and row["status"] == "supported"
            row["target"] = target
            row["claim_type"] = target["type"]
            row["span"] = row.get("answer_quote") or target["claim_or_question"]
            checks.append(row)
            if row["action"] != "retain":
                issues.append(row)

        result = _audit_payload({
            "status": "accept" if not issues and coverage_complete else "revise",
            "review_scope": "main_agent_selected_targets",
            "review_status": "complete" if coverage_complete else "partial" if checks else "failed",
            "checks": checks,
            "checked_target_ids": [item["target_id"] for item in input_details if item["checked"]],
            "unchecked_target_ids": [item["target_id"] for item in input_details if not item["checked"]],
            "coverage_detail": {"targets": input_details},
            "issues": issues,
            "reviewed_claim_count": len(checks),
            "answer_audit_coverage": "complete" if coverage_complete else "partial" if checks else "failed",
            "coverage_complete": coverage_complete,
            "limitations": " ".join(dict.fromkeys(limitations))[:1200],
            "source_ids": list(dict.fromkeys(used_source_ids)),
            "used_source_ids": list(dict.fromkeys(used_source_ids)),
            "review_targets": normalized,
            "cache_hit": coverage_complete and len(cached_target_ids) == len(normalized),
            "cached_target_ids": cached_target_ids,
            "author_coverage": {"status": "not_applicable", "findings": []},
            "cross_review": {"status": "not_applicable", "issue_count": 0},
        }, default="complete" if coverage_complete else "partial" if checks else "failed")
        return result

    def call(
        self, answer: str, *, question: str = "",
        personal_context: dict[str, Any] | None = None,
        review_targets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(answer, str) or not 20 <= len(answer) <= 30000:
            raise ValueError("candidate answer must be 20-30000 characters")
        if review_targets is not None:
            return self._call_selected_targets(
                answer,
                question=question,
                personal_context=personal_context,
                review_targets=review_targets,
            )
        cited_ids = list(dict.fromkeys(_EVIDENCE_ID.findall(answer)))
        observed = self.evidence.bundle().get("evidence") or []
        known = {item["evidence_id"]: item for item in observed}
        unknown = [value for value in cited_ids if value not in known]
        if unknown:
            return _audit_payload({"status": "revise", "issues": [{
                "span": value, "claim_type": "objective_fact", "action": "revise",
                "evidence_refs": [], "reason": "Citation ID was not observed in this run.",
                "suggested_revision": "Remove the unobserved citation or read the source first.",
            } for value in unknown], "reviewed_claim_count": 0}, default="failed")
        article_ids = [value for value, row in known.items()
                       if row["source_type"] == "author_article"]
        external_ids = [value for value, row in known.items()
                        if row["source_type"] != "author_article"]
        signature = hashlib.sha256(json.dumps(
            {"answer": answer, "question": question, "evidence_ids": list(known),
             "personal_context": personal_context},
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
        if signature in self._cache:
            return self._cache[signature]
        system = (
            "Audit one excerpt of a candidate finance answer, not the source articles in isolation. "
            "Return exactly {\"type\":\"final\",\"output\":{\"checks\":[{\"line_start\":1,"
            "\"line_end\":1,\"claim_type\":\"author_view\","
            "\"action\":\"retain\",\"evidence_refs\":[],"
            "\"reason\":\"why this action\",\"suggested_revision\":\"\"}],"
            "\"answer_audit_coverage\":\"complete\",\"limitations\":\"\"}}. "
            "Use an empty checks array if the excerpt has no material claims. "
            "Markdown table separator rows (for example |---|---|) are formatting, not claims. "
            "For each independent material assertion, identify its line_start and line_end in the numbered "
            "candidate_answer_lines, not the line numbers in an evidence source. "
            "Split multiple numbers, forecasts, causal explanations and inferences in the same "
            "paragraph into separate checks, which may point to the same answer line. Do not "
            "require the answer to split one attributed sentence into multiple lines unless "
            "the grouping changes its meaning or hides an important condition. "
            "Classify the assertion itself, not just its source: "
            "a forecast attributed to an author is still prediction; an author's causal account "
            "is causal_explanation. A comparison saying an unfinished current move will "
            "exceed historical episodes is a forecast, even if quoted under an author-view "
            "heading. If the answer groups it with non-predictions, require reclassification. "
            "Use author_view for views without those stronger forms, "
            "including a clearly attributed interpretive viewpoint; this does not certify "
            "its objective truth. Otherwise classify objective_fact, "
            "model_inference or personal_context. Action retain, qualify, revise or delete. "
            "Do not use claim, quote, type or classification in place of span and claim_type. "
            "Article text proves attribution, not market truth. Article author, title and "
            "publication date are source metadata established by the local article EvidenceRef; "
            "do not demand external market verification for those identity fields. One secondary webpage can "
            "support 'publisher reports X', not an unqualified high-impact fact. Check "
            "dates, subject, period, units and alternatives; correlation does not prove cause. "
            "A future forecast remains contingent. A personal claim needs supplied profile support. "
            "When observed independent evidence contradicts an attributed author view, "
            "require the answer to preserve the author's actual position and source, "
            "present the contrary evidence with its period and scope, and explain the "
            "resulting judgment. Do not demand that a faithfully attributed view disappear "
            "merely because it is contradicted. Misattributed or invented author views must "
            "instead be corrected to the actual source position. Reserve high-confidence "
            "verification language for a claim-matched independent market or primary-source check; "
            "an author article or reviewer opinion alone does not qualify. "
            "Retain an answerer's inference when it is explicitly labeled as their own, "
            "is a reasonable reading of the supplied evidence, and preserves important "
            "conditions. Do not require the source author to have stated that inference. "
            "Likewise, retain a plainly labeled conditional author forecast without "
            "requiring independent confirmation of its future outcome. Use qualify only "
            "for a material unsupported leap, misleading certainty, or omitted condition "
            "that changes the conclusion; do not demand cosmetic relabeling. "
            "Respect the user's source boundary: if they asked for local-article-only analysis, "
            "a faithfully attributed and explicitly unverified author assertion can be retained "
            "without external verification. This is one excerpt in a separate audit batch. "
            "Assess only candidate_answer_lines. Other answer sections are checked by "
            "other batches, so their absence is not a coverage limitation here. "
            "An inherited attribution label applies to subsequent list items in its "
            "section; do not require every bullet to repeat it. The label is context, "
            "not an additional answer claim to audit. "
            "answer_audit_coverage=complete means every material assertion in candidate_answer_lines was assessed; "
            "partial means one or more material assertions remain outside the review, and failed means the audit "
            "could not produce a usable review result. "
            "against claim-relevant cited source passages. A claim-matched web excerpt is "
            "not necessarily the entire webpage: if a needed table row, period or header is "
            "absent from that excerpt, say the audit excerpt is insufficient, not that the "
            "original source lacks the data. Set answer_audit_coverage to partial and explain in limitations "
            "if source text or answer assertions are missing. Leave limitations empty when "
            "coverage is complete; put ordinary source caveats in individual check reasons. "
            "It does not mean every assertion was externally proved, and it does not require "
            "the answer to mention every article that was read but not used. "
            "Copy IDs exactly from "
            "allowed_evidence_ids; do not invent evidence IDs or facts."
        )
        issues = []
        checks = []
        limitations = []
        coverage_complete = True
        cross_review = {"status": "not_applicable", "issue_count": 0}
        chunks = _answer_chunks(answer)
        audited_ids: list[str] = []
        used_evidence_ids: list[str] = []
        chunk_cursor = 0
        for chunk in chunks:
            chunk_start = answer.find(chunk, chunk_cursor)
            if chunk_start < 0:
                raise ValueError("answer audit chunk could not be located in candidate answer")
            chunk_cursor = chunk_start + len(chunk)
            local_citations = _EVIDENCE_ID.findall(chunk)
            path_citations = [value for value in article_ids
                              if _article_path_in_chunk(str(known[value].get("source_ref") or ""), chunk)]
            date_matches = [value for value in article_ids
                            if _article_date_in_chunk(str(known[value].get("published_at") or ""), chunk)]
            external_matches = [value for value in external_ids
                                if str(known[value].get("source_url") or "")
                                and str(known[value]["source_url"]) in chunk]
            broad_local_scope = len(article_ids) <= 12
            fallback = not local_citations and not path_citations and not date_matches
            external_fallback = not any(value in local_citations for value in external_ids) and not external_matches
            relevant_external_ids = [value for value in external_ids
                                     if not _audit_source(known[value], chunk)["excerpt_truncated"]]
            external_candidates = (
                relevant_external_ids[:12] if external_fallback else []
            )
            selected_ids = list(dict.fromkeys([
                *local_citations, *path_citations, *date_matches,
                *(article_ids if broad_local_scope else article_ids[:12] if fallback else []),
                *external_matches,
                *external_candidates,
            ]))
            # Explicit answer anchors and model-selected claim references define
            # what the answer used. For a small unanchored corpus, the bounded
            # candidate set is intentionally the working source set. A larger
            # unanchored corpus remains a candidate pool, not an obligation to
            # mention every read article.
            used_evidence_ids.extend(
                value for value in dict.fromkeys(
                    [*local_citations, *path_citations, *date_matches, *external_matches]
                ) if value not in used_evidence_ids
            )
            if fallback and len(article_ids) <= 12:
                used_evidence_ids.extend(
                    value for value in article_ids if value not in used_evidence_ids
                )
            if external_fallback:
                used_evidence_ids.extend(
                    value for value in external_candidates if value not in used_evidence_ids
                )
            if len(selected_ids) > 30:
                raise ValueError(
                    "one answer section cites more than 30 observed sources; split that section "
                    "and attach citations beside the individual claims"
                )
            audited_ids.extend(selected_ids)
            source_context = [_audit_source(known[value], chunk) for value in selected_ids]
            source_truncated = any(row["excerpt_truncated"] for row in source_context)
            candidate_lines = chunk.splitlines()
            line_offsets = []
            offset = 0
            for line in chunk.splitlines(keepends=True):
                line_offsets.append(offset)
                offset += len(line)
            scope_labels = {
                index: _active_attribution_label(answer, chunk_start + line_offsets[index - 1])
                for index in range(1, len(candidate_lines) + 1)
            }
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": {
                    "observed_evidence": source_context,
                    "allowed_evidence_ids": selected_ids,
                    "source_selection": (
                        "No local citation or article date in this excerpt; at most twelve "
                        "observed sources were supplied as candidates."
                        if fallback else "Sources explicitly cited in this answer excerpt."
                    ),
                    "personal_context": personal_context or {},
                    "user_question": question,
                    "candidate_answer_lines": "\n".join(
                        f"{index}: {line}" for index, line in enumerate(candidate_lines, 1)
                    ),
                    "inherited_attribution_by_line": {
                        index: label for index, label in scope_labels.items() if label
                    },
                    "final_instruction": "Return only candidate_answer_lines numbers, never source-article line numbers.",
                }},
            ]

            def validate_batch(batch: AnswerAuditBatch) -> None:
                for check in batch.checks:
                    if (check.line_end < check.line_start
                            or check.line_end > len(candidate_lines)
                            or check.line_end - check.line_start > 3):
                        raise ValueError(
                            "checks[].line_start/line_end must identify at most four "
                            f"numbered candidate-answer lines (1-{len(candidate_lines)}); "
                            f"got {check.line_start}-{check.line_end}"
                        )
                    if check.claim_type not in _TYPES or check.action not in _ACTIONS:
                        raise ValueError("answer audit claim_type or action is invalid")
                    invalid = sorted(set(check.evidence_refs) - set(selected_ids))
                    if invalid:
                        raise ValueError(
                            f"answer audit cited invalid evidence IDs {invalid}; "
                            f"allowed_evidence_ids={selected_ids}"
                        )

            batch_key = hashlib.sha256(json.dumps(
                {"chunk": chunk, "source_context": source_context,
                 "question": question, "personal_context": personal_context},
                ensure_ascii=False, sort_keys=True, default=str,
            ).encode("utf-8")).hexdigest()
            batch = self._batch_cache.get(batch_key)
            if batch is None:
                batch = complete_structured(
                    self.complete, messages, "finance_answer_audit.v1", AnswerAuditBatch,
                    validate=validate_batch,
                )
                self._batch_cache[batch_key] = batch
            coverage_complete = (coverage_complete and _batch_is_complete(batch)
                                 and not source_truncated
                                 and not batch.limitations)
            if source_truncated:
                limitations.append(
                    "At least one cited source passage exceeded the audit context limit; "
            "the answer cannot be certified from a partial passage."
                )
            if batch.limitations:
                limitations.append(batch.limitations)
            for check in batch.checks:
                row = check.model_dump()
                for evidence_id in row["evidence_refs"]:
                    if evidence_id not in used_evidence_ids:
                        used_evidence_ids.append(evidence_id)
                row["span"] = "\n".join(
                    candidate_lines[check.line_start - 1:check.line_end]
                ).strip()[:500]
                if _is_markdown_table_divider(row["span"]):
                    continue
                # A model can follow the article attribution but still put a
                # future/conditional statement in an ordinary viewpoint
                # section. Classification is part of the evidence contract,
                # so repair that boundary before deciding whether the answer
                # is publishable.
                if (row["claim_type"] != "personal_context"
                        and _forecast_language(row["span"], row["evidence_refs"], known)):
                    row["claim_type"] = "prediction"
                    answer_line_number = answer[:chunk_start].count("\n") + check.line_start
                    if (row["action"] == "retain"
                            and _active_answer_section(answer, answer_line_number) == "A"
                            and not _prediction_is_labeled(answer, answer_line_number, row["span"])):
                        row["action"] = "revise"
                    row["reason"] = (
                        "This attributed statement contains a future direction, "
                        "condition, or not-yet-realized scenario; classify it as a "
                        "prediction and preserve its horizon/conditions. "
                        + row["reason"]
                    )[:600]
                    row["suggested_revision"] = (
                        row["suggested_revision"]
                        or "Classify this statement as a prediction and retain its conditions."
                    )
                if row["action"] == "retain" and row["claim_type"] == "objective_fact":
                    refs = [known[value] for value in row["evidence_refs"]]
                    primary = any(ref["source_type"] in {
                        "official_source", "market_metric", "market_bar_series",
                    } or _is_official_publisher(ref) for ref in refs)
                    domains = {urlparse(str(ref.get("source_url") or "")).netloc.casefold()
                               for ref in refs if ref["source_type"] == "public_web_page"
                               and ref.get("source_url")}
                    attributed = bool(scope_labels.get(check.line_start)) or bool(re.search(
                        r"据|报道称|报告显示|作者(?:称|认为|文中陈述|口径|.{0,8}转述)|"
                        r"文章(?:称|认为)|according to|reported by", row["span"], re.I,
                    ))
                    source_metadata = bool(re.search(
                        r"本地.{0,100}篇|本篇|未.{0,10}(?:直接|单独).{0,15}(?:谈|讨论|展开)|"
                        r"引文|文章|原文|行号|引用|"
                        r"(?:^|[;；\s-])(?:作者|标题|发布日期|发文日期)\s*[:：]|"
                        r"source|citation|published|发表",
                        row["span"], re.I,
                    )) or bool(re.match(
                        r"^\s*#{1,6}\s*(?:\d+[.、]\s*)?20\d{2}-\d{2}-\d{2}[《\"“]",
                        row["span"],
                    ))
                    source_metadata = source_metadata or _is_article_metadata_span(
                        row["span"], row["evidence_refs"], known,
                    )
                    if not primary and len(domains) < 2 and not attributed and not source_metadata:
                        row["action"] = "qualify"
                        row["reason"] = (
                            "An unqualified public fact is supported only by one secondary "
                            "web domain or no independently observed source. " + row["reason"]
                        )[:600]
                        row["suggested_revision"] = (
                            row["suggested_revision"] or
                            "Attribute this claim to the observed publisher and disclose that it is not independently confirmed."
                        )
                if row["action"] != "retain":
                    issues.append(row)
                checks.append(row)
        atomic_review = {"status": "not_applicable", "proposed_issue_count": 0,
                         "dismissed_issue_count": 0}
        already_qualified = [
            issue for issue in issues
            if issue["action"] == "qualify" and re.search(
                r"未.{0,8}核实|未经.{0,8}核实|未.{0,8}查证|unverified|"
                r"作者(?:报告|引述|转述)", issue["span"], re.I,
            )
        ]
        if already_qualified:
            review_ids = list(dict.fromkeys(
                ref for issue in already_qualified for ref in issue["evidence_refs"]
            ))
            review_messages = [
                {"role": "system", "content": (
                    "Adjudicate proposed finance answer qualifications where the answer may "
                    "already contain the requested attribution or uncertainty. Review each "
                    "exact answer span, the proposed issue, and observed source. Dismiss an "
                    "issue if its suggested revision merely repeats a qualifier already in "
                    "the answer. Keep an issue if there is a separate material mismatch in "
                    "date, number, subject, condition, or actual-versus-forecast status. "
                    "Do not demand independent confirmation for an explicitly attributed, "
                    "explicitly unverified author report when the user asked to summarize "
                    "local articles. Return JSON type=final with output.verdicts; each item "
                    "has issue_index, decision keep/dismiss, reason. Judge every index once."
                )},
                {"role": "user", "content": {
                    "issues": [{"issue_index": index, **issue}
                               for index, issue in enumerate(already_qualified)],
                    "observed_evidence": [_audit_source(known[value]) for value in review_ids],
                }},
            ]

            def validate_atomic_verdicts(batch: CrossIssueReviewBatch) -> None:
                if sorted(item.issue_index for item in batch.verdicts) != list(range(len(already_qualified))):
                    raise ValueError("atomic issue review must cover every selected issue")
                if any(item.decision not in {"keep", "dismiss"} for item in batch.verdicts):
                    raise ValueError("atomic issue decision must be keep/dismiss")

            verdicts = complete_structured(
                self.complete, review_messages, "finance_atomic_issue_review.v1",
                CrossIssueReviewBatch, validate=validate_atomic_verdicts,
            )
            dismissed = {id(already_qualified[item.issue_index]) for item in verdicts.verdicts
                         if item.decision == "dismiss"}
            issues = [issue for issue in issues if id(issue) not in dismissed]
            atomic_review = {
                "status": "complete", "proposed_issue_count": len(already_qualified),
                "dismissed_issue_count": len(dismissed),
                "verdicts": [item.model_dump() for item in verdicts.verdicts],
            }
        # The ledger can contain reads that the final answer intentionally did
        # not use. Cross-review only the article evidence supplied to an atomic
        # batch; an unmentioned read must not become a coverage obligation.
        # Cross-article challenge is adversarial: it may inspect the bounded
        # candidate set even when the answer intentionally cites only a subset.
        # This is different from the atomic coverage obligation above; the
        # challenge must never turn an unmentioned candidate into a missing cite.
        used_article_ids = [value for value in article_ids if value in audited_ids]
        article_count = len({known[value].get("article_id") for value in used_article_ids})
        author_coverage = {"status": "not_applicable", "findings": []}
        if len({known[value].get("author") for value in used_article_ids}) >= 2:
            author_coverage = self._review_author_coverage(
                answer, question, [known[value] for value in used_article_ids],
            )
            issues.extend(author_coverage["issues"])
            if author_coverage["status"] == "incomplete":
                coverage_complete = False
                limitations.append("Some cross-author claims could not be checked against complete article passages.")
        if not issues and coverage_complete and article_count >= 2:
            cross_sources = [_audit_source(known[value]) for value in used_article_ids]
            if any(row["excerpt_truncated"] for row in cross_sources):
                coverage_complete = False
                limitations.append("Cross-article review cannot inspect a truncated source passage.")
                cross_review = {"status": "incomplete", "issue_count": 0}
            else:
                cross_system = (
                    "Check whether THIS dated article contradicts or materially undercuts a broad conclusion "
                    "in the numbered finance answer. Other articles exist but are not supplied "
                    "in this call. Never challenge a claim merely because this article alone "
                    "does not support it. Ignore cosmetic changes and issues already preserved "
                    "by the answer's qualifications. A real issue requires an exact quote from "
                    "the answer line AND an exact, material quote from this article. The source "
                    "quote must show either a logical contradiction or a condition/forecast status "
                    "whose omission changes the answer's takeaway. A conclusion saying there is "
                    "no directional view is misleading if the article gives a conditional directional "
                    "forecast; state the condition rather than erasing the direction. A forecast or "
                    "hypothetical causal mechanism must not be summarized as an observed cause. "
                    "Also flag a materially omitted observation when the answer summarizes this "
                    "article's mechanism or trend but the article explicitly reports a contrary "
                    "or diagnostic asset/market relationship that changes how confidently that "
                    "mechanism can be stated (for example, the alleged driver weakens while the "
                    "assets move independently). Do not demand a list of every detail: retain only "
                    "omissions that change the claimed explanation, direction, timing, or confidence. "
                    "First locate any explicit 'not yet happened' assertion in this source; "
                    "then check whether a selected cross-article summary treats that same "
                    "event as an explanation of an already observed move. Flag that time-status "
                    "error even if a detailed earlier answer paragraph kept the condition. "
                    "A passage saying an analysis "
                    "appears later does not contradict an answer citing that later analysis, even "
                    "if the later passage is absent from this particular evidence chunk. "
                    "Pay particular attention to claimed absence versus conditional forecasts, "
                    "claims of change versus an earlier occurrence, and omitted 'not yet' conditions. "
                    "Return JSON type=final with output.checks (at most 3); no other output "
                    "fields. Each check must contain "
                    "line_start, line_end, claim_type, action, evidence_refs, reason, "
                    "suggested_revision, answer_quote and source_quote. Use precisely those "
                    "field names and no extra keys. Quotes must be exact substrings, not paraphrases. "
                    "evidence_refs must contain only the supplied allowed_evidence_id. "
                    "Allowed claim_type: author_view, objective_fact, prediction, "
                    "causal_explanation, model_inference, personal_context. "
                    "Allowed action: qualify, revise, delete. Keep reason and suggested_revision "
                    "under 300 characters. The caller separately checks every observed article "
                    "passage, so do not report that other articles are missing in this call. "
                    "If no direct contradiction is demonstrable, return checks=[]."
                )
                answer_lines = answer.splitlines()
                selection_messages = [
                    {"role": "system", "content": (
                        "Select the numbered answer lines that make broad cross-article "
                        "conclusions or synthesize change over time. Include claims of absence, "
                        "consensus, realized causes, conditional forecasts and final summaries. "
                        "Include comparison-table rows that characterize a dated article's "
                        "causal mechanism as an explanation of an already observed move; those "
                        "rows can confuse a forecast scenario with a realized cause. Detailed "
                        "per-article quotations are checked separately. Return JSON "
                        "type=final with output.lines containing up to 30 distinct original "
                        "answer line numbers, sorted ascending. Do not rewrite the lines."
                    )},
                    {"role": "user", "content": {
                        "question": question,
                        "numbered_answer": "\n".join(
                            f"{index}: {line}" for index, line in enumerate(answer_lines, 1)
                        ),
                    }},
                ]

                def validate_selection(selection: CrossSummarySelection) -> None:
                    if (len(set(selection.lines)) != len(selection.lines)
                            or selection.lines != sorted(selection.lines)
                            or any(line < 1 or line > len(answer_lines)
                                   for line in selection.lines)):
                        raise ValueError("cross summary selection must contain distinct valid answer lines")

                selection = complete_structured(
                    self.complete, selection_messages, "finance_cross_summary_selection.v1",
                    CrossSummarySelection, validate=validate_selection,
                )
                dated_table_lines = [
                    index for index, line in enumerate(answer_lines, 1)
                    if re.match(
                        r"^\s*\|\s*(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
                        r"20\d{2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日)\s*\|",
                        line,
                    )
                ]
                dated_table_lines.extend(
                    index for index, line in enumerate(answer_lines, 1)
                    if re.match(
                        r"^\s*\|\s*20\d{2}\u5e74\d{1,2}\u6708\d{1,2}\u65e5\s*\|",
                        line,
                    )
                )
                selected_lines = sorted(set(selection.lines) | set(dated_table_lines))
                if not selected_lines:
                    selected_lines = _fallback_cross_summary_lines(answer_lines)
                if not selected_lines:
                    coverage_complete = False
                    limitations.append(
                        "Cross-article review could not identify summary or comparison lines."
                    )
                    cross_review = {
                        "status": "incomplete", "issue_count": 0,
                        "source_count": len(cross_sources), "batch_count": 0,
                        "selected_answer_lines": [],
                    }
                proposed_issues: list[dict[str, Any]] = []
                source_line_counts: dict[str, int] = {}
                cross_batch_count = 0
                for source in cross_sources if selected_lines else []:
                    source_id = source["evidence_id"]
                    published_at = str(source.get("published_at") or "")[:10]
                    short_date = published_at[5:] if len(published_at) == 10 else ""
                    normalized_answer_lines = [_normalize_date_text(line) for line in answer_lines]
                    relevant_lines = [
                        index for index in selected_lines
                        if not re.search(r"(?<!\d)\d{1,2}[-/]\d{1,2}(?!\d)",
                                         normalized_answer_lines[index - 1])
                        or short_date and short_date in normalized_answer_lines[index - 1]
                    ]
                    source_line_counts[source_id] = len(relevant_lines)
                    if not relevant_lines:
                        continue
                    for offset in range(0, len(relevant_lines), 6):
                        line_batch = relevant_lines[offset:offset + 6]
                        cross_messages = [
                            {"role": "system", "content": cross_system},
                            {"role": "user", "content": {
                                "user_question": question,
                                "observed_evidence": [source],
                                "allowed_evidence_ids": [source_id],
                                "numbered_answer": "\n".join(
                                    f"{index}: {answer_lines[index - 1]}" for index in line_batch
                                ),
                            }},
                        ]

                        def validate_cross(batch: CrossArticleBatch) -> None:
                            for check in batch.checks:
                                if (check.line_start not in line_batch
                                        or check.line_end not in line_batch
                                        or check.line_end < check.line_start
                                        or check.line_end - check.line_start > 3):
                                    raise ValueError("cross-review check must identify supplied answer lines")
                                if check.claim_type not in _TYPES or check.action not in _ACTIONS:
                                    raise ValueError("cross-review claim_type or action is invalid")
                                if set(check.evidence_refs) - {source_id}:
                                    raise ValueError("cross-review cited an unobserved article EvidenceRef")
                                answer_span = "\n".join(answer_lines[check.line_start - 1:check.line_end])
                                if check.answer_quote not in answer_span:
                                    raise ValueError("cross-review answer quote is not on the cited answer lines")
                                if check.source_quote not in source["excerpt"]:
                                    raise ValueError("cross-review source quote is not in the observed article")

                        cross = complete_structured(
                            self.complete, cross_messages, "finance_cross_answer_audit.v1",
                            CrossArticleBatch, validate=validate_cross,
                        )
                        cross_batch_count += 1
                        for check in cross.checks:
                            if check.action == "retain":
                                continue
                            row = check.model_dump()
                            row["span"] = "\n".join(
                                answer_lines[check.line_start - 1:check.line_end]
                            ).strip()[:500]
                            proposed_issues.append(row)
                dismissed_count = 0
                adjudications: list[dict[str, Any]] = []
                if proposed_issues:
                    review_messages = [
                        {"role": "system", "content": (
                            "Independently adjudicate proposed finance answer issues. For each "
                            "issue, compare the EXACT answer span, answer_quote and source_quote. "
                            "Keep only a material contradiction or a genuinely misleading omitted "
                            "condition/forecast status. Dismiss if the answer already says the "
                            "source point, if the source quote concerns a different asset or "
                            "subject, or if the concern is merely that another passage was not "
                            "provided. Judge the challenged summary sentence itself: a correct "
                            "detail elsewhere or later in the same paragraph does not excuse a "
                            "categorical summary that denies a conditional directional forecast. "
                            "A conditional forecast still has a direction, even when it is not a "
                            "standalone asset thesis. Likewise, a mechanism forecast to occur "
                            "later must not be summarized as already occurring. Do not propose "
                            "new issues. Return JSON type=final with "
                            "output.verdicts, each containing issue_index, decision "
                            "(keep/dismiss), and a concise reason. Judge every index once."
                        )},
                        {"role": "user", "content": {"issues": [
                            {"issue_index": index, **row} for index, row in enumerate(proposed_issues)
                        ]}},
                    ]

                    def validate_verdicts(batch: CrossIssueReviewBatch) -> None:
                        if sorted(item.issue_index for item in batch.verdicts) != list(range(len(proposed_issues))):
                            raise ValueError("cross issue review must cover every proposed issue")
                        if any(item.decision not in {"keep", "dismiss"} for item in batch.verdicts):
                            raise ValueError("cross issue decision must be keep/dismiss")

                    verdicts = complete_structured(
                        self.complete, review_messages, "finance_cross_issue_review.v1",
                        CrossIssueReviewBatch, validate=validate_verdicts,
                    )
                    retained_indices = {item.issue_index for item in verdicts.verdicts
                                        if item.decision == "keep"}
                    adjudications = [item.model_dump() for item in verdicts.verdicts]
                    dismissed_count = len(proposed_issues) - len(retained_indices)
                    issues.extend(row for index, row in enumerate(proposed_issues)
                                  if index in retained_indices)
                cross_review = {
                    "status": "complete" if coverage_complete else "incomplete",
                    "issue_count": len(proposed_issues) - dismissed_count,
                    "proposed_issue_count": len(proposed_issues),
                    "dismissed_issue_count": dismissed_count,
                    "source_count": len(cross_sources),
                    "batch_count": cross_batch_count,
                    "selected_answer_lines": selected_lines,
                    "source_line_counts": source_line_counts,
                    "adjudications": adjudications,
                }
        result = _audit_payload({
            "status": "accept" if not issues and coverage_complete else "revise",
            "issues": issues,
            "checks": checks,
            "reviewed_claim_count": len(checks),
            "coverage_complete": coverage_complete,
            "limitations": " ".join(limitations)[:1000],
            "section_count": len(chunks),
            "cross_review": cross_review,
            "author_coverage": {key: value for key, value in author_coverage.items() if key != "issues"},
            "source_ids": list(dict.fromkeys(audited_ids)),
            "used_source_ids": list(dict.fromkeys(used_evidence_ids)),
            "review_scope": "model_pass_over_observed_evidence",
            "atomic_issue_review": atomic_review,
        }, default="complete" if coverage_complete else "partial")
        self._cache[signature] = result
        return result

    def _review_author_coverage(
        self, answer: str, question: str, articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        answer_lines = answer.splitlines()
        authors = sorted({str(row.get("author") or "") for row in articles if row.get("author")})
        candidates = [
            index for index, line in enumerate(answer_lines, 1)
            if _may_assert_shared_author_claim(line, authors)
        ]
        if not candidates:
            return {"status": "not_applicable", "findings": [], "issues": []}
        selection_messages = [
            {"role": "system", "content": (
                "Identify only answer assertions that attribute the SAME concrete predicate to "
                "multiple named authors, including 'all/each/both authors', '三位作者', "
                "'均/都', and shared-consensus summaries. A comparison that explicitly gives "
                "different positions is not such an assertion. Select the exact quoted span "
                "and every author it covers. Return type=final with output.claims; each claim "
                "has line_number, answer_quote, authors. If there are none, return claims=[]."
            )},
            {"role": "user", "content": {
                "question": question, "observed_authors": authors,
                "candidate_answer_lines": "\n".join(
                    f"{index}: {answer_lines[index - 1]}" for index in candidates
                ),
            }},
        ]

        def validate_selection(batch: AuthorCoverageSelection) -> None:
            for claim in batch.claims:
                if (claim.line_number not in candidates
                        or claim.answer_quote not in answer_lines[claim.line_number - 1]
                        or len(set(claim.authors)) != len(claim.authors)
                        or set(claim.authors) - set(authors)):
                    raise ValueError("author coverage selection must quote a candidate line and observed authors")

        selection = complete_structured(
            self.complete, selection_messages, "finance_author_coverage_selection.v1",
            AuthorCoverageSelection, validate=validate_selection,
        )
        findings: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        incomplete = False
        for claim in selection.claims:
            for author in claim.authors:
                source_rows = [row for row in articles if row.get("author") == author]
                sources = [_audit_source(row) for row in source_rows]
                if any(source["excerpt_truncated"] for source in sources):
                    incomplete = True
                    findings.append({
                        "line_number": claim.line_number, "author": author,
                        "status": "insufficient", "evidence_refs": [],
                        "source_quote": "", "reason": "Observed article passage was truncated.",
                    })
                    continue
                allowed = {source["evidence_id"]: source for source in sources}
                messages = [
                    {"role": "system", "content": (
                        "Check whether this ONE author's observed articles support the exact "
                        "predicate attributed to them in the answer. Treat conjunctions strictly: "
                        "a source mentioning only one number or half a claim is not support for "
                        "the whole claim. Return type=final with output.status (supports, "
                        "not_mentioned, conflicts, insufficient), evidence_refs, source_quote, "
                        "reason. For supports/conflicts quote the exact supporting/conflicting "
                        "source words and supply its EvidenceRef. If the read articles do not "
                        "mention the predicate, use not_mentioned, not conflicts. Do not infer "
                        "that the author never said it outside the observed articles."
                    )},
                    {"role": "user", "content": {
                        "question": question, "answer_claim": claim.answer_quote,
                        "author": author, "observed_articles": sources,
                        "allowed_evidence_ids": list(allowed),
                    }},
                ]

                def validate_finding(finding: AuthorCoverageFinding) -> None:
                    if finding.status not in {"supports", "not_mentioned", "conflicts", "insufficient"}:
                        raise ValueError("invalid author coverage status")
                    if set(finding.evidence_refs) - set(allowed):
                        raise ValueError("author coverage cited an unobserved article")
                    if finding.status in {"supports", "conflicts"} and not (
                        finding.evidence_refs and finding.source_quote
                        and any(finding.source_quote in allowed[value]["excerpt"]
                                for value in finding.evidence_refs)
                    ):
                        raise ValueError("author coverage support/conflict requires an exact source quote")

                finding = complete_structured(
                    self.complete, messages, "finance_author_coverage_finding.v1",
                    AuthorCoverageFinding, validate=validate_finding,
                )
                required_decimals = {
                    float(value) for value in re.findall(r"(?<![\w.])\d+\.\d+", claim.answer_quote)
                    if float(value) % 1
                }
                observed_decimals = {
                    float(value) for row in source_rows for value in re.findall(
                        r"(?<![\w.])\d+\.\d+", str(row.get("excerpt") or ""),
                    )
                }
                if finding.status == "supports" and required_decimals - observed_decimals:
                    finding = AuthorCoverageFinding(
                        status="not_mentioned", evidence_refs=[], source_quote="",
                        reason="The observed articles do not contain every non-integer figure in the attributed claim.",
                    )
                entry = {"line_number": claim.line_number, "author": author, **finding.model_dump()}
                findings.append(entry)
                if finding.status == "insufficient":
                    incomplete = True
                elif finding.status != "supports":
                    reason = (
                        f"The answer attributes this entire claim to {author}, but "
                        f"the observed articles {('contradict it' if finding.status == 'conflicts' else 'do not establish it')}: "
                        f"{finding.reason}"
                    )[:600]
                    issues.append({
                        "line_start": claim.line_number, "line_end": claim.line_number,
                        "claim_type": "author_view", "action": "revise",
                        "evidence_refs": finding.evidence_refs,
                        "reason": reason,
                        "suggested_revision": (
                            "State separately which authors support each part; limit unsupported "
                            "attribution to the observed article sample."
                        ),
                        "span": answer_lines[claim.line_number - 1].strip()[:500],
                        "answer_quote": claim.answer_quote,
                        "source_quote": finding.source_quote,
                    })
        return {
            "status": "incomplete" if incomplete else "complete",
            "findings": findings, "issues": issues,
            "selected_claims": [item.model_dump() for item in selection.claims],
        }


def _is_markdown_table_divider(span: str) -> bool:
    return bool(re.fullmatch(r"\s*\|(?:\s*:?-{3,}:?\s*\|){2,}\s*", span))


def _answer_chunks(answer: str, *, maximum: int = 900, max_lines: int = 4) -> list[str]:
    chunks: list[str] = []
    current = ""
    line_count = 0
    for line in answer.splitlines(keepends=True):
        if (len(current) + len(line) > maximum or line_count >= max_lines) and current:
            chunks.append(current)
            current = ""
            line_count = 0
        while len(line) > maximum:
            chunks.append(line[:maximum])
            line = line[maximum:]
        current += line
        line_count += 1
    if current:
        chunks.append(current)
    return chunks


def _normalize_date_text(text: str) -> str:
    """Normalize full Chinese dates so source matching uses one form."""
    normalized = re.sub(
        r"(20\d{2})\u5e74(\d{1,2})\u6708(\d{1,2})\u65e5",
        lambda match: f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}",
        text,
    ).replace("/", "-")
    return re.sub(
        r"(?<!\d)(\d{1,2})\u6708(\d{1,2})\u65e5",
        lambda match: f"{int(match.group(1)):02d}-{int(match.group(2)):02d}",
        normalized,
    )


def _forecast_language(
    answer_span: str,
    evidence_refs: list[str],
    known: dict[str, dict[str, Any]],
) -> bool:
    """Return whether an attributed statement has forecast semantics.

    This is deliberately a conservative boundary check, not a semantic truth
    classifier. It catches common future, conditional, and unrealized-scenario
    markers in either the answer span or the source passage so a model cannot
    hide a forecast under an ``author_view`` heading. The model still decides
    the exact horizon and wording during revision.
    """
    answer_text = str(answer_span or "")
    # Use the answer span as the classification surface. Source passages are
    # needed to validate the claim, but future-tense words elsewhere in a
    # long article must not turn every ordinary viewpoint into a forecast.
    text = answer_text.strip()
    if re.search(
        "(?:\u6ca1\u6709|\u672a|\u5e76\u672a|\u4e0d\u66fe|\u5e76\u975e|\u4e0d\u662f|\u975e).{0,14}(?:\u9884\u6d4b|\u9884\u5224|\u672a\u6765\u8d70\u52bf|\u4e0a\u6da8\u7684\u9884\u6d4b|\u4e0b\u8dcc\u7684\u9884\u6d4b)|"
        "(?:\u9884\u6d4b|\u9884\u5224).{0,14}(?:\u5e76\u975e|\u4e0d\u662f|\u5e76\u672a|\u975e).{0,14}(?:\u7ed3\u8bba|\u89c2\u70b9|\u5224\u65ad)",
        text,
    ):
        return False
    if (
        re.search(
            "(?:\u5386\u53f2\u4e0a|\u6b64\u524d|\u8fc7\u53bb|\u5df2\u7ecf|\u5df2\u7136|\u66fe\u7ecf|\u622a\u81f3).{0,12}(?:\u8fdc\u8d85|\u8d85\u8d8a|\u4e0a\u6da8|\u4e0b\u8dcc|\u8fbe\u5230)",
            text,
        )
        and not re.search("(?:\u5c06|\u4f1a|\u82e5|\u5982\u679c|\u5927\u6982\u7387|\u6761\u4ef6\u6027|\u672a\u6765)", text)
    ):
        return False
    if re.search(
        r"(?:讨论|分析|说明|介绍|解释).{0,12}(?:预测模型|预测方法|预测能力|预测的局限)|"
        r"(?:预测模型|预测方法|预测能力|预测的局限).{0,12}(?:局限|没有|未给出|不涉及|不作)",
        text,
    ):
        return False
    if re.match(
        r"^(?:[-*]\s*)?(?:\*\*)?\s*(?:\d{1,2}[）).、]|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2})",
        text,
    ):
        # Article inventory rows and dated headings are source metadata, not
        # substantive claims, even when their titles contain "预期" or "将".
        return False
    if re.match(r"^(?:#{1,6}\s*|\*\*\s*)", text) and (
        text.rstrip().endswith("**") or text.lstrip().startswith("##")
    ):
        return False
    # A planned follow-up article is not a market forecast merely because it
    # uses future tense ("下周将拆解...").
    if re.search(
        r"(?:预告|下周|下一篇|后续文章).{0,24}(?:将|会).{0,12}(?:拆解|详聊|对照|介绍|讨论)",
        text,
    ):
        return False
    if re.search(
        r"(?:会在|将在).{0,15}(?:文章|下周|下一篇).{0,12}(?:详聊|推演|拆解|对照)",
        text,
    ):
        return False
    subject = (
        r"黄金|白银|金银|美元|美股|股市|风险资产|债务|美债|国债|收益率|利率|"
        r"流动性|危机|主升浪|修复|反转|崩|上涨|下跌|加息|降息|市场|资产|行情"
    )
    direction = r"若|如果|只要|除非|大概率|远超|超越|临近|尚未|未发生|还没有|条件性|将|会|或将|才会|不会"
    if re.search(rf"(?:{direction}).{{0,20}}(?:{subject})", text):
        return True
    if re.search(rf"(?:{subject}).{{0,20}}(?:{direction})", text):
        return True
    if re.search(
        rf"(?:预测|预判|未来|forecast|scenario).{{0,20}}(?:{subject})|"
        rf"(?:{subject}).{{0,20}}(?:预测|预判|未来|forecast|scenario)",
        text,
        re.IGNORECASE,
    ):
        return True
    market_subject = subject
    if re.search(rf"(?:将|会|或将|才会|才可能|不会).{{0,16}}(?:{market_subject})", text):
        return True
    return bool(re.search(rf"(?:{market_subject}).{{0,16}}(?:将|会|或将|才会|才可能|不会)", text))


def _prediction_is_labeled(answer: str, line_number: int, span: str) -> bool:
    """Whether the candidate already exposes the forecast boundary."""
    current = str(span or "")
    if re.search(
        r"预测|预判|前瞻|展望|情景分析|条件性(?:判断|情景|信号)|"
        r"未来情景|未来判断|路径判断|大概率|中长期|远期",
        current,
    ):
        return True
    lines = answer.splitlines()
    if not 1 <= line_number <= len(lines):
        return False
    active_section = _active_answer_section(answer, line_number)
    return active_section == "B"


def _active_answer_section(answer: str, line_number: int) -> str:
    lines = answer.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    active_section = ""
    for line in lines[:line_number]:
        match = re.match(r"^\s*\**\s*([A-C])(?:[.)、]|\s)", line, re.IGNORECASE)
        if match:
            active_section = match.group(1).upper()
    return active_section


def _fallback_cross_summary_lines(answer_lines: list[str], *, limit: int = 30) -> list[int]:
    """Select a deterministic review surface when the selector returns none."""
    if not answer_lines:
        return []
    markers = re.compile(
        r"结论|总结|一句话|变化|趋势|共识|分歧|冲突|条件|情景|预测|观点|"
        r"consensus|conflict|change|trend|conclusion|summary|forecast",
        re.IGNORECASE,
    )
    selected = [index for index, line in enumerate(answer_lines, 1) if markers.search(line)]
    selected.extend(index for index, line in enumerate(answer_lines, 1)
                   if line.lstrip().startswith(("#", "-", "|")) and index <= 4)
    selected.extend([1, len(answer_lines)])
    return sorted(set(selected))[:limit]


def _may_assert_shared_author_claim(line: str, authors: list[str]) -> bool:
    if _is_markdown_table_divider(line):
        return False
    named = sum(bool(author and author.casefold() in line.casefold()) for author in authors)
    collective = bool(re.search(
        r"(?:两|三|四|五|多|各|所有|全部|每)位?作者|各(?:博主|方)|"
        r"(?:他们|她们|三人|两人|诸位).{0,15}(?:都|均|共同)|"
        r"(?:共识|共同|一致|均|都)|\b(?:all|both|each|every|consensus)\b",
        line, re.IGNORECASE,
    ))
    return named >= 2 or collective


def _article_path_in_chunk(source_ref: str, chunk: str) -> bool:
    parts = source_ref.replace("\\", "/").casefold().split("/")
    return len(parts) >= 3 and "/".join(parts[-3:]) in chunk.casefold()


def _article_date_in_chunk(published_at: str, chunk: str) -> bool:
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published_at):
        return False
    month, day = (int(value) for value in published_at[5:].split("-"))
    return published_at in chunk or bool(re.search(
        rf"(?<!\d)0?{month}[-/]0?{day}(?!\d)", chunk,
    ))


def _active_attribution_label(answer: str, claim_position: int) -> str:
    for line in reversed(answer[:claim_position].splitlines()[-12:]):
        if re.match(r"^\s*#{2,6}\s", line):
            break
        if re.search(r"作者.{0,16}(?:转述|口径|称).{0,30}(?:未|未经|没有).{0,10}(?:核实|确认)", line):
            return line.strip()[:180]
    return ""


def _is_article_metadata_span(
    span: str, evidence_refs: list[str], known: dict[str, dict[str, Any]],
) -> bool:
    """Recognize article identity fields without treating them as market facts."""
    refs = [known[value] for value in evidence_refs if value in known]
    if not refs or any(ref.get("source_type") != "author_article" for ref in refs):
        return False
    text = str(span or "")
    normalized = _normalize_date_text(text)
    heading = bool(re.match(r"^\s*(?:#{1,6}|[-*]|\||\d+[.)])", text))
    metadata_label = bool(re.search(
        r"(?:author|title|published|publication|source|citation|article|"
        r"\u4f5c\u8005|\u6807\u9898|\u53d1\u5e03\u65e5\u671f|\u53d1\u6587\u65e5\u671f|\u6765\u6e90|\u6587\u7ae0)",
        text, re.IGNORECASE,
    ))
    for ref in refs:
        published = str(ref.get("published_at") or "")[:10]
        author = str(ref.get("author") or "").strip()
        source_ref = str(ref.get("source_ref") or "")
        if published:
            variants = {
                published,
                published.replace("-", "/"),
                f"{published[:4]}\u5e74{int(published[5:7])}\u6708{int(published[8:10])}\u65e5",
                published[5:],
            }
            if any(value and (value in normalized or value in text) for value in variants):
                if heading or metadata_label or (author and author in text):
                    return True
        filename = source_ref.replace("\\", "/").rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        if len(stem) >= 8 and stem in text:
            return True
    return False


def _has_independent_source(
    evidence_refs: list[str], known: dict[str, Any], selected_ids: tuple[str, ...],
) -> bool:
    author_locations = {
        str(known[value].get(key) or "").rstrip("/")
        for value in selected_ids if known[value]["source_type"] == "author_article"
        for key in ("source_ref", "source_url") if known[value].get(key)
    }
    for value in evidence_refs:
        row = known[value]
        if row["source_type"] not in {
            "public_web_page", "official_source", "source_document", "market_metric", "market_bar_series",
        }:
            continue
        if row.get("provenance") == "author_view" or not str(row.get("excerpt") or "").strip():
            continue
        locations = {str(row.get(key) or "").rstrip("/")
                     for key in ("source_ref", "source_url") if row.get(key)}
        if locations and not locations & author_locations:
            return True
    return False


def _remap_target_batch(batch: TargetAuditBatch, target_id: str) -> None:
    for check in batch.checks:
        check.target_id = target_id
    if batch.coverage_detail is not None:
        if batch.coverage_detail.checked_target_ids:
            batch.coverage_detail.checked_target_ids = [target_id]
        if batch.coverage_detail.unchecked_target_ids:
            batch.coverage_detail.unchecked_target_ids = [target_id]


def _target_coverage_problems(
    batch: TargetAuditBatch, target_id: str, evidence_ids: list[str],
) -> list[str]:
    problems = []
    coverage = answer_audit_coverage(batch.model_dump(), default="failed")
    if batch.answer_audit_coverage is None and batch.coverage_complete is None:
        problems.append("No explicit execution coverage was returned.")
    if (batch.coverage_complete is not None and batch.answer_audit_coverage is not None
            and batch.coverage_complete != (batch.answer_audit_coverage == "complete")):
        problems.append("Canonical and legacy coverage flags disagree.")
    if coverage == "complete" and not batch.checks:
        problems.append("Complete coverage has no target check.")
    if coverage == "failed" and batch.checks:
        problems.append("Failed coverage contains a usable target check.")
    detail = batch.coverage_detail
    if detail is not None:
        for checked, unchecked, expected in (
            (detail.checked_target_ids, detail.unchecked_target_ids, {target_id}),
            (detail.checked_evidence_ids, detail.unchecked_evidence_ids, set(evidence_ids)),
        ):
            if set(checked) & set(unchecked) or set(checked) | set(unchecked) != expected:
                problems.append("Coverage detail must partition exactly the supplied targets and inputs.")
        if detail.checked_target_ids and not batch.checks:
            problems.append("Checked target metadata has no actual check.")
        if coverage == "complete" and (
            detail.checked_target_ids != [target_id] or detail.unchecked_evidence_ids
        ):
            problems.append("Complete coverage contradicts unchecked targets or inputs.")
    return problems


def _target_answer_context(answer: str, spans: list[str]) -> tuple[str, dict[str, Any]]:
    def full(reason: str) -> tuple[str, dict[str, Any]]:
        return answer, {"scope": "full_answer", "fallback_reason": reason,
                        "text_checksum": _audit_signature(answer)}

    if not spans:
        return full("not_provided")
    locations = []
    for span in spans:
        if not span.strip() or answer.find(span) < 0:
            return full("missing_span")
        if answer.find(span) != answer.rfind(span):
            return full("ambiguous_span")
        locations.append((answer.find(span), answer.find(span) + len(span)))

    paragraphs = []
    for match in re.finditer(r"\S[\s\S]*?(?=\r?\n[ \t]*\r?\n|\Z)", answer):
        start = match.start()
        while start > 0 and answer[start - 1] in " \t":
            start -= 1
        paragraphs.append((start, match.end(), answer[start:match.end()]))
    selected: set[int] = set()
    for start, end in locations:
        containing = [index for index, (left, right, _) in enumerate(paragraphs)
                      if left < end and right > start]
        if not containing:
            return full("unresolved_paragraph")
        for index in containing:
            selected.update(range(max(0, index - 1), min(len(paragraphs), index + 2)))

    # Keep section ancestry as well as neighboring paragraphs; never extract sentences.
    headings: list[tuple[int, int]] = []
    for index, (_, _, text) in enumerate(paragraphs):
        for line in text.splitlines():
            heading = re.match(r"^ {0,3}(#{1,6})\s+", line)
            level = len(heading.group(1)) if heading else 0
            if not level and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line):
                level = 1 if "=" in line else 2
            if level:
                headings = [(depth, pos) for depth, pos in headings if depth < level]
                headings.append((level, index))
        if index in selected:
            selected.update(pos for _, pos in headings)
    text = "\n\n".join(paragraphs[index][2] for index in sorted(selected))
    return text, {"scope": "bounded_paragraphs", "fallback_reason": "",
                  "paragraph_count": len(selected), "text_checksum": _audit_signature(text)}


def _audit_signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()


def _audit_source(
    row: dict[str, Any], answer_chunk: str = "", *, complete_observation: bool = False,
) -> dict[str, Any]:
    observed_excerpt = str(row.get("excerpt") or "")
    excerpt = observed_excerpt
    limit = 20000 if row["source_type"] == "author_article" else 7500
    if complete_observation:
        limit = max(1, len(observed_excerpt))
    passage_selected = False
    if row["source_type"] == "public_web_page" and len(excerpt) > limit and answer_chunk:
        source_lines = excerpt.splitlines()
        anchors = {int(value) for value in re.findall(r"\bL(\d+)\b", answer_chunk)}
        centers: set[int] = set()
        is_sep_projection = (
            "fomcprojtabl" in str(row.get("source_url") or "").casefold()
            or "projections materials" in str((row.get("metadata") or {}).get("title") or "").casefold()
        )
        if is_sep_projection and re.search(
            r"SEP|GDP|PCE|失业|点阵|分布|中位|联邦基金|政策利率|投影表|projection|dot\s*plot",
            answer_chunk, re.I,
        ):
            # A single projection page contains several semantically distinct
            # rows with the same values (4.1, 2.0, etc.). Keep the complete
            # Table 1 and Figure 2 key rows whenever the answer uses this SEP
            # source, so the auditor can distinguish row labels and periods.
            sep_row_patterns = (
                r"Table 1\. Economic projections",
                r"\| Change in real GDP \|",
                r"\| Unemployment rate \|",
                r"\| PCE inflation \|",
                r"\| Core PCE inflation",
                r"\| Federal funds rate \|",
                r"Figure 2\. FOMC participants",
                r"\| 4\.375 \|",
                r"\| 4\.125 \|",
                r"\| 3\.875 \|",
            )
            centers.update(
                index for index, line in enumerate(source_lines)
                if any(re.search(pattern, line, re.I) for pattern in sep_row_patterns)
            )
        if anchors:
            for index, line in enumerate(source_lines):
                match = re.match(r"^(\d+):", line)
                if match and any(abs(int(match.group(1)) - anchor) <= 2 for anchor in anchors):
                    centers.add(index)
        else:
            # Score each answer line independently: a four-line answer chunk can
            # refer to four different rows of the same projections table.
            for claim_line in answer_chunk.splitlines():
                numbers = set(re.findall(
                    r"(?<![\w.])\d{2,}(?:\.\d+)?|\d+\.\d+", claim_line,
                ))
                if not numbers:
                    continue
                scored = []
                for index, line in enumerate(source_lines):
                    line_numbers = set(re.findall(
                        r"(?<![\w.])\d{2,}(?:\.\d+)?|\d+\.\d+", line,
                    ))
                    shared = numbers & line_numbers
                    score = len(shared) + 2 * sum("." in value for value in shared)
                    if score >= 2:
                        scored.append((score, index))
                centers.update(index for _, index in sorted(scored, reverse=True)[:5])
                # Numeric overlap is ambiguous in a projections page: 4.1 can
                # be an unemployment rate, a policy-rate median, or a value in
                # a different figure. Add claim-to-row semantics before the
                # bounded excerpt is assembled. This also bridges Chinese
                # answer wording to the English accessible SEP table.
                row_patterns: list[tuple[str, str]] = []
                if re.search(r"GDP|实际\s*GDP|经济增长", claim_line, re.I):
                    row_patterns.append(("GDP", r"(?:change in real GDP|GDP growth)"))
                if re.search(r"失业", claim_line, re.I):
                    row_patterns.append(("unemployment", r"unemployment rate"))
                if re.search(r"核心?\s*PCE|核心通胀", claim_line, re.I):
                    row_patterns.append(("core_pce", r"core PCE inflation"))
                elif re.search(r"PCE|通胀", claim_line, re.I):
                    row_patterns.append(("pce", r"PCE inflation"))
                if re.search(r"联邦基金|政策利率|利率中位|目标区间", claim_line, re.I):
                    row_patterns.append(("funds_rate", r"(?:federal funds rate|target range)"))
                if re.search(r"点阵|分布|dot\s*plot", claim_line, re.I):
                    row_patterns.append(("dot_plot", r"(?:figure 2|distribution of participants|midpoint of target range)"))
                for _, pattern in row_patterns:
                    centers.update(
                        index for index, line in enumerate(source_lines)
                        if re.search(pattern, line, re.I)
                    )
        if centers:
            selected_indices = set()
            for center in centers:
                selected_indices.update(range(max(0, center - 2), min(len(source_lines), center + 3)))
                # A numeric row without its table/figure title and column years
                # is ambiguous even when its numbers happen to match the answer.
                preceding = range(center - 1, max(-1, center - 19), -1)
                for pattern in (
                    r"(?:table|figure|表\s*\d|图\s*\d)",
                    r"(?:median|projection|\b20\d{2}\b)",
                ):
                    header = next((index for index in preceding
                                   if re.search(pattern, source_lines[index], re.I)), None)
                    if header is not None:
                        selected_indices.add(header)
            selected = "\n".join(source_lines[index] for index in sorted(selected_indices))
            if len(selected) <= limit:
                excerpt = selected
                passage_selected = True
    metadata = row.get("metadata") or {}
    observed_length = metadata.get("observed_excerpt_length")
    observed_hash = metadata.get("observed_excerpt_sha256")
    integrity_failed = (row["source_type"] == "author_article" and (
        observed_length != len(excerpt)
        or observed_hash != hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    ))
    return {
        "evidence_id": row["evidence_id"],
        "source_type": row["source_type"],
        "source_url": row.get("source_url"),
        "source_ref": row.get("source_ref"),
        "published_at": row.get("published_at"),
        "article_id": row.get("article_id"),
        "author": row.get("author"),
        "line_start": row.get("line_start"),
        "line_end": row.get("line_end"),
        "excerpt": excerpt[:limit],
        "excerpt_truncated": len(excerpt) > limit or integrity_failed,
        "selection_scope": "complete_observation" if complete_observation else (
            "claim_matched_passage" if passage_selected else "source_opening"
        ),
        "source_excerpt_complete": not passage_selected and len(observed_excerpt) <= limit and not integrity_failed,
        "metadata": metadata,
    }


__all__ = ["AnswerAudit"]
