"""Question-focused comparison of independently studied article cards."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from policy_impact.domains.finance.research_artifacts import RELATIONS, ResearchArtifacts
from policy_impact.domains.finance.structured_pass import complete_structured


class SynthesisRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    claim_ids: list[str] = Field(min_length=2, max_length=12)
    analysis: str = Field(min_length=8, max_length=1500)
    qualification: str = Field(default="", max_length=500)


class SynthesisBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relations: list[SynthesisRelation] = Field(default_factory=list, max_length=30)
    limitations: str = Field(default="", max_length=1000)


class SynthesisSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_ids: list[str] = Field(min_length=2, max_length=24)


class RelationJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_index: int = Field(ge=0)
    verdict: str
    reason: str = Field(min_length=8, max_length=1000)
    revised_analysis: str = Field(default="", max_length=1500)
    revised_type: str = ""
    qualification: str = Field(default="", max_length=500)


class RelationReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[RelationJudgment] = Field(min_length=1, max_length=30)


class ResearchSynthesis:
    def __init__(
        self, artifacts: ResearchArtifacts,
        complete: Callable[[list[dict[str, Any]], str], dict[str, Any]] | None,
    ) -> None:
        self.artifacts = artifacts
        self.complete = complete

    def call(self, question: str, card_ids: list[str]) -> dict[str, Any]:
        if self.complete is None:
            raise RuntimeError("cross-article synthesis model is unavailable")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
            raise ValueError("question must be 1-500 characters")
        if (not isinstance(card_ids, list) or not 2 <= len(card_ids) <= 20
                or any(not isinstance(value, str) for value in card_ids)
                or len(set(card_ids)) != len(card_ids)):
            raise ValueError("synthesis requires 2-20 distinct card IDs")
        cards = [self.artifacts.call(operation="get", artifact_id=card_id) for card_id in card_ids]
        if any(card["kind"] != "article_card" for card in cards):
            raise ValueError("synthesis accepts article cards only")
        if len({card["payload"]["article_id"] for card in cards}) < 2:
            raise ValueError("synthesis requires at least two distinct articles")
        views = [
            {
                "card_id": card["artifact_id"],
                "article_id": payload["article_id"], "author": payload["author"],
                "title": payload["title"], "published_at": payload["published_at"],
                "depth": payload["depth"], "limitations": payload["limitations"],
                "claims": [{
                    "claim_id": claim["claim_id"], "kind": claim["kind"],
                    "statement": claim["statement"], "quote": claim["quote"],
                    "conditions": claim["conditions"], "horizon": claim["horizon"],
                    "line_start": claim["line_start"], "line_end": claim["line_end"],
                } for claim in payload["claims"]],
            }
            for card in cards for payload in [card["payload"]]
        ]
        if len(json.dumps(views, ensure_ascii=False)) > 120_000:
            raise ValueError(
                "selected article cards exceed one synthesis context; compare bounded thematic "
                "groups first and reload source cards by ID for the final synthesis"
            )
        available = {
            claim["claim_id"]: view["article_id"]
            for view in views for claim in view["claims"]
        }
        complete_views = views
        selected_ids = list(available)
        if len(selected_ids) > 24:
            selection_messages = [
                {"role": "system", "content": (
                    "Select source-bound claim IDs most relevant to comparing these separately "
                    "dated articles for the user's question. Return JSON type=final with "
                    "output.claim_ids only. Choose 2-24 distinct IDs, at "
                    "least one from every article, retaining important contrary claims, "
                    "time conditions and uncertainty. Do not invent IDs or merge articles. "
                    "This only selects working context; "
                    "the original cards remain available."
                )},
                {"role": "user", "content": {
                    "question": question.strip(),
                    "candidates": [{
                        "article_id": view["article_id"], "published_at": view["published_at"],
                        "claims": [{key: claim[key] for key in (
                            "claim_id", "kind", "statement", "conditions", "horizon",
                        )} for claim in view["claims"]],
                    } for view in views],
                }},
            ]

            def validate_selection(selection: SynthesisSelection) -> None:
                if (len(set(selection.claim_ids)) != len(selection.claim_ids)
                        or set(selection.claim_ids) - set(available)):
                    raise ValueError("selection must contain distinct observed claim IDs")
                if {available[value] for value in selection.claim_ids} != set(available.values()):
                    raise ValueError("selection must retain claims from every selected article")

            selection = complete_structured(
                self.complete, selection_messages, "finance_synthesis_selection.v1",
                SynthesisSelection, validate=validate_selection,
            )
            selected_ids = selection.claim_ids
            selected = set(selected_ids)
            views = [{**view, "claims": [claim for claim in view["claims"]
                                        if claim["claim_id"] in selected]} for view in views]
        messages = [
            {"role": "system", "content": (
                "Compare separately studied finance articles around the user's exact question. "
                "Treat each article as a dated author's viewpoint, not an independently verified "
                "market fact. Return JSON type=final with output.relations and output.limitations. "
                "A relation must name claim_ids from at least two different articles and type "
                "agreement, disagreement, change_over_time, or complementary. Discuss conditions, "
                "dates, scope and genuine uncertainty; explain apparent contradictions instead "
                "of erasing them. Author agreement is not factual verification. Do not invent "
                "claim IDs or create a relationship when the articles do not support one. "
                "If there is no supported cross-article relationship, return relations=[] and "
                "state the reason in limitations."
            )},
            {"role": "user", "content": {
                "question": question.strip(), "article_views": views,
                "claim_to_article": {value: available[value] for value in selected_ids},
            }},
        ]
        batch = complete_structured(
            self.complete, messages, "finance_article_synthesis.v1", SynthesisBatch,
        )
        if any(claim_id not in available for relation in batch.relations
               for claim_id in relation.claim_ids):
            raise ValueError("synthesis cited claim IDs outside the selected articles")
        valid_relations = [
            relation for relation in batch.relations
            if len({available[claim_id] for claim_id in relation.claim_ids}) >= 2
        ]
        dropped_relation_count = len(batch.relations) - len(valid_relations)
        limitations = batch.limitations
        if dropped_relation_count:
            limitations = (
                f"{limitations} {dropped_relation_count} proposed relation(s) were excluded "
                "because they lacked claims from two selected articles."
            ).strip()[:1000]
        if not valid_relations:
            return {
                "status": "no_supported_relation", "card_ids": card_ids,
                "dropped_relation_count": dropped_relation_count,
                "limitations": limitations or "No cross-article relationship was supported.",
            }
        review_messages = [
            {"role": "system", "content": (
                "Independently challenge each proposed cross-article finance relation using "
                "only the supplied dated, source-quoted article claims. This is a reasoning "
                "audit, not external market verification. For EVERY relation_index return "
                "verdict retain, revise, or reject, reason, revised_analysis, qualification. "
                "Reject if the relationship is not supported; revise if a precise qualification "
                "fixes it. Preserve uncertainty, scope and chronology. A conditional signal in "
                "an earlier article is not confirmed by a later article unless ALL material "
                "conditions are observed there. Read ALL supplied card claims for explicit "
                "'not yet happened' or contrary conditions, including claims omitted from the "
                "question-focused synthesis selection. A mechanism can be compatible with a "
                "later suspected episode without having been realized there. Distinguish a "
                "reported observation from the "
                "author's inference about its cause. Two claims from one author are not "
                "independent corroboration. change_over_time requires a material change in "
                "the same proposition's stance, probability, conditions or forecast; a more "
                "specific example or suspected actor is normally complementary, not a "
                "reversal or confirmed realization. Set revised_type when the relationship "
                "category changes. "
                "Return JSON type=final with output.judgments only. Use every index exactly "
                "once; revised_analysis is required only for revise and must be source-bound."
            )},
            {"role": "user", "content": {
                "question": question.strip(), "article_views": complete_views,
                "relations": [
                    {"relation_index": index, **relation.model_dump()}
                    for index, relation in enumerate(valid_relations)
                ],
            }},
        ]

        def validate_review(review: RelationReviewBatch) -> None:
            indices = [judgment.relation_index for judgment in review.judgments]
            if sorted(indices) != list(range(len(valid_relations))):
                raise ValueError("relation review must judge every proposed relation exactly once")
            for judgment in review.judgments:
                if judgment.verdict not in {"retain", "revise", "reject"}:
                    raise ValueError("relation review verdict must be retain/revise/reject")
                if judgment.verdict == "revise" and len(judgment.revised_analysis.strip()) < 8:
                    raise ValueError("revised relation needs substantive revised_analysis")
                if judgment.revised_type and judgment.revised_type not in RELATIONS:
                    raise ValueError("revised_type must be a supported relation type")

        review = complete_structured(
            self.complete, review_messages, "finance_relation_review.v1",
            RelationReviewBatch, validate=validate_review,
        )
        judgments = {item.relation_index: item for item in review.judgments}
        audited_relations = []
        revised_count = 0
        rejected_count = 0
        for index, relation in enumerate(valid_relations):
            judgment = judgments[index]
            if judgment.verdict == "reject":
                rejected_count += 1
                continue
            if judgment.verdict == "revise":
                revised_count += 1
                relation = relation.model_copy(update={
                    "type": judgment.revised_type or relation.type,
                    "analysis": judgment.revised_analysis.strip(),
                    "qualification": judgment.qualification.strip() or judgment.reason.strip()[:500],
                })
            audited_relations.append(relation)
        if revised_count or rejected_count:
            limitations = (
                f"{limitations} Relation audit revised {revised_count} and rejected "
                f"{rejected_count} proposed relation(s)."
            ).strip()[:1000]
        if not audited_relations:
            return {
                "status": "no_supported_relation", "card_ids": card_ids,
                "dropped_relation_count": dropped_relation_count,
                "rejected_relation_count": rejected_count,
                "relation_audit": [item.model_dump() for item in review.judgments],
                "limitations": limitations or "Relation audit rejected every proposed relation.",
            }
        self.artifacts._comparison({
            "question": question.strip(), "card_ids": card_ids,
            "relations": [relation.model_dump() for relation in audited_relations],
            "limitations": limitations,
            "relation_audit": [item.model_dump() for item in review.judgments],
        })
        saved = self.artifacts.call(operation="save", kind="comparison", payload={
            "question": question.strip(), "card_ids": card_ids,
            "relations": [relation.model_dump() for relation in audited_relations],
            "limitations": limitations,
            "relation_audit": [item.model_dump() for item in review.judgments],
        })
        record = self.artifacts.call(operation="get", artifact_id=saved["artifact_id"])
        return {
            "status": "compared", "comparison_id": saved["artifact_id"],
            "card_ids": card_ids, "relation_count": saved["relation_count"],
            "dropped_relation_count": dropped_relation_count,
            "revised_relation_count": revised_count,
            "rejected_relation_count": rejected_count,
            "selected_claim_count": len(selected_ids),
            "relations": record["payload"]["relations"],
            "relation_audit": record["payload"]["relation_audit"],
            "limitations": record["payload"]["limitations"],
            "notice": "Cross-article agreement is an author-viewpoint relation, not factual verification.",
        }


__all__ = ["ResearchSynthesis"]
