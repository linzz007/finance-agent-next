"""Repair research anchors without letting recovery regenerate the analysis."""

from __future__ import annotations

import hashlib
import json
from copy import copy, deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError

from policy_impact.runtime.research_contracts import EvidenceLink, ResearchResult, TaskResult, Text

from .contracts import Command, DispatchError
from .finance_rules import FinanceRules, load_rules


class AnchorSelection(Command):
    link_index: Annotated[int, Field(ge=0)]
    text: Text


class AnchorPatch(Command):
    schema_version: Literal["finance.h2.anchor_patch.v1"]
    anchors: list[AnchorSelection]


class _AnchorPatchAction(Command):
    type: Literal["final"]
    output: AnchorPatch


@dataclass(frozen=True)
class ResearchAnchorRepair:
    action: dict
    invalid_indices: tuple[int, ...]
    rules: FinanceRules | None = None

    @classmethod
    def prepare(cls, action, *, rules=None):
        if not isinstance(action, dict) or action.get("type") != "final":
            return None
        output = action.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("evidence_links"), list):
            return None
        try:
            links = TypeAdapter(list[EvidenceLink]).validate_python(output["evidence_links"], strict=True)
            # Validate every other field/relationship before exposing any draft text.
            body = ResearchResult.model_validate({
                **cls._base_result(output), "evidence_links": [],
            }, strict=True)
            invalid = tuple(index for index, link in enumerate(links)
                            if link.text not in body.content and link.text not in (body.candidate_answer or ""))
            if not invalid:
                return None
        except ValidationError:
            return None
        return cls(deepcopy(action), invalid, rules)

    @staticmethod
    def _base_result(output):
        if output.get("schema_version") == "finance.task_result.v1":
            payload = TaskResult.model_validate({**output, "evidence_links": []}, strict=True).model_dump(mode="json")
            payload.update(schema_version="finance.research_result.v1", evidence_links=output.get("evidence_links", []))
            return payload
        payload = {key: value for key, value in output.items() if key not in {
            "review_targets", "review_decision", "review_decision_reason", "review_dispositions",
        }}
        if payload.get("schema_version") == "finance.analyst_result.v2":
            payload["schema_version"] = "finance.research_result.v1"
        return payload

    @property
    def body_sha256(self):
        output = self.action["output"]
        body = {key: output.get(key) for key in ("content", "candidate_answer")}
        return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def feedback(self):
        output = self.action["output"]
        return {
            "input_policy": "finance.h2.immutable_anchor_correction.v1",
            "instruction": (
                (self.rules or load_rules()).text("anchor-repair")
            ),
            "untrusted_draft": {
                "trust": "untrusted_analysis_not_observations",
                "body_sha256": self.body_sha256,
                "content": output["content"],
                "candidate_answer": output.get("candidate_answer"),
            },
            "invalid_links": [{"link_index": index, "evidence_refs": output["evidence_links"][index]["evidence_refs"]}
                              for index in self.invalid_indices],
            "action_json_schema": _AnchorPatchAction.model_json_schema(),
            "action_example": _AnchorPatchAction(
                type="final", output=AnchorPatch(schema_version="finance.h2.anchor_patch.v1", anchors=[]),
            ).model_dump(mode="json"),
            "final_output_schema_scope": "final.output",
            "final_output_json_schema": AnchorPatch.model_json_schema(),
        }

    def assemble(self, response, *, fallback_reason=None):
        selections = {}
        if fallback_reason is None:
            try:
                patch = _AnchorPatchAction.model_validate(response.payload, strict=True).output
                for item in patch.anchors:
                    if item.link_index not in self.invalid_indices or item.link_index in selections:
                        raise ValueError("anchor correction index is not an original unique invalid link")
                    output = self.action["output"]
                    if item.text not in output["content"] and item.text not in (output.get("candidate_answer") or ""):
                        raise ValueError("anchor correction must select verbatim frozen analysis")
                    selections[item.link_index] = item.text
            except (KeyError, TypeError, ValueError) as exc:
                raise DispatchError("contract_error", "invalid immutable anchor patch", details={
                    "call_id": response.metadata.get("call_id"),
                    "output_ref": response.metadata.get("output_ref"),
                    "validation_stage": "final.output.anchor_patch",
                }) from exc
        action = deepcopy(self.action)
        links = []
        dropped = []
        for index, link in enumerate(action["output"]["evidence_links"]):
            if index in self.invalid_indices:
                if index not in selections:
                    dropped.append(index)
                    continue
                link["text"] = selections[index]
            links.append(link)
        action["output"]["evidence_links"] = links
        ResearchResult.model_validate(self._base_result(action["output"]), strict=True)
        derived = copy(response)
        derived.payload = action
        derived.metadata = {**response.metadata, "research_anchor_repair": {
            "body_sha256": self.body_sha256,
            "invalid_indices": list(self.invalid_indices),
            "repaired_indices": sorted(selections),
            "dropped_indices": dropped,
            "fallback_reason": fallback_reason,
        }, "research_anchor_warnings": [f"research_anchor_unrepaired:{index}" for index in dropped]}
        return derived


__all__ = ["ResearchAnchorRepair"]
