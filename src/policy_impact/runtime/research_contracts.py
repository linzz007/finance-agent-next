"""Versioned public research contracts, separate from legacy claims-only outputs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


Text = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
ResultKind = Literal["article_understanding", "synthesis", "working_view"]


class ResearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("text must not be blank")
        return value


class SourceRange(ResearchContract):
    start_line: Annotated[StrictInt, Field(ge=1)]
    end_line: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def ordered(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self


class ReadingRequest(ResearchContract):
    material_ref: Text
    depth: Literal["skim", "deep"]
    ranges: list[SourceRange] = Field(default_factory=list)
    select_ranges: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def scope(self):
        if self.depth == "deep" and (self.ranges or self.select_ranges):
            raise ValueError("deep requests the whole focal version; omit ranges/select_ranges")
        if self.depth == "skim" and bool(self.ranges) == self.select_ranges:
            raise ValueError("skim requires either ranges or select_ranges=true")
        previous = 0
        for span in sorted(self.ranges, key=lambda item: item.start_line):
            if span.start_line <= previous:
                raise ValueError("reading ranges must not overlap")
            previous = span.end_line
        return self


class ResearchTask(ResearchContract):
    schema_version: Literal["finance.research_task.v1"]
    original_query: Text
    objective: Text
    material_refs: list[Text]
    requested_result_kind: ResultKind
    completion_expectation: Text
    reading_request: ReadingRequest | None = None
    focus: list[Text] = Field(default_factory=list)
    user_requirements: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def materials(self):
        if len(self.material_refs) != len(set(self.material_refs)):
            raise ValueError("material_refs must be unique")
        if self.requested_result_kind == "article_understanding" and self.reading_request is None:
            raise ValueError("article_understanding requires reading_request")
        if self.reading_request and self.reading_request.material_ref not in self.material_refs:
            raise ValueError("focal material must be explicitly granted in material_refs")
        return self


class SourceQuote(ResearchContract):
    evidence_ref: Text
    text: Text


class EvidenceLink(ResearchContract):
    text: Text
    evidence_refs: list[Text] = Field(min_length=1)
    quotes: list[SourceQuote] = Field(default_factory=list)

    @model_validator(mode="after")
    def sources(self):
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        if any(item.evidence_ref not in self.evidence_refs for item in self.quotes):
            raise ValueError("quote evidence must also occur in the link evidence_refs")
        return self


class MaterialDisposition(ResearchContract):
    material_ref: Text
    decision: Literal["considered", "excluded", "unprocessed"]
    reason: Text


class ResearchGap(ResearchContract):
    description: Text
    material_refs: list[Text] = Field(default_factory=list)
    next_need: Text | None = None


class ResearchResult(ResearchContract):
    schema_version: Literal["finance.research_result.v1"]
    content: Text
    evidence_links: list[EvidenceLink]
    material_disposition: list[MaterialDisposition]
    gaps: list[ResearchGap]
    limitations: list[Text]
    supporting_artifact_refs: list[Text] = Field(default_factory=list)
    candidate_answer: Text | None = None

    @model_validator(mode="after")
    def anchors(self):
        for item in self.evidence_links:
            if item.text not in self.content and item.text not in (self.candidate_answer or ""):
                raise ValueError("evidence link text must occur verbatim in content/candidate_answer")
        refs = [item.material_ref for item in self.material_disposition]
        if len(refs) != len(set(refs)):
            raise ValueError("one material disposition is allowed per handle")
        if len(self.supporting_artifact_refs) != len(set(self.supporting_artifact_refs)):
            raise ValueError("supporting_artifact_refs must be unique")
        return self
