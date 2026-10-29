"""Versioned public research contracts, separate from legacy claims-only outputs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


Text = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
ResultKind = Literal["article_understanding", "synthesis", "working_view"]
ReviewMode = Literal["required", "auto", "off"]
ReviewScope = Literal[
    "source_fidelity", "reasoning_conditions", "evidence_support", "external_facts",
    "temporal_conditions", "counterevidence",
]


class ResearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("text must not be blank")
        return value


class ReviewPolicy(ResearchContract):
    mode: ReviewMode = "auto"
    scope: list[ReviewScope] = Field(
        default_factory=lambda: ["source_fidelity", "reasoning_conditions"], min_length=1, max_length=6,
    )
    max_rounds: Annotated[StrictInt, Field(ge=1, le=5)] = 5
    instructions: Annotated[StrictStr, Field(min_length=1, max_length=4096)] | None = None

    @model_validator(mode="after")
    def unique_scope(self):
        if len(self.scope) != len(set(self.scope)):
            raise ValueError("review scope values must be unique")
        return self


class ReviewTarget(ResearchContract):
    target_id: Annotated[StrictStr, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
    kind: Literal["author_view", "objective_fact", "prediction", "causal_explanation", "model_inference"]
    statement: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    evidence_refs: list[Text] = Field(default_factory=list, max_length=32)
    conditions: list[Text] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_evidence(self):
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("review target evidence refs must be unique")
        return self


class ReviewRequest(ResearchContract):
    schema_version: Literal["finance.review_request.v1"]
    draft_ref: Text
    draft_revision: Annotated[StrictInt, Field(ge=1)]
    round_number: Annotated[StrictInt, Field(ge=1, le=5)]
    scope: list[ReviewScope] = Field(min_length=1)
    targets: list[ReviewTarget] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [target.target_id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("review target ids must be unique")
        if len(self.scope) != len(set(self.scope)):
            raise ValueError("review request scope values must be unique")
        return self


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
    review_policy: ReviewPolicy = Field(default_factory=ReviewPolicy)

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


class ReviewFinding(ResearchContract):
    finding_id: Annotated[StrictStr, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
    target_id: Text | None = None
    category: Literal[
        "unsupported", "misattribution", "condition_loss", "contradiction", "reasoning_gap",
        "external_fact_unverified", "other",
    ]
    severity: Literal["material", "minor"]
    assessment: Literal["supported", "partly_supported", "unsupported", "contradicted", "inconclusive"]
    explanation: Text
    evidence_refs: list[Text] = Field(default_factory=list, max_length=32)
    quotes: list[SourceQuote] = Field(default_factory=list, max_length=32)
    suggested_change: Text | None = None
    unchecked_scope: list[Text] = Field(default_factory=list, max_length=32)
    limitations: list[Text] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def quote_refs(self):
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("finding evidence refs must be unique")
        if any(quote.evidence_ref not in self.evidence_refs for quote in self.quotes):
            raise ValueError("finding quotes must reference listed evidence")
        if self.target_id is None and not self.unchecked_scope:
            raise ValueError("unlinked finding requires an unchecked-scope locator")
        return self


class ReviewReport(ResearchContract):
    schema_version: Literal["finance.review_report.v1"]
    reviewed_draft_ref: Text
    reviewed_revision: Annotated[StrictInt, Field(ge=1)]
    checked_target_ids: list[Text] = Field(min_length=1, max_length=32)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    checked_scope: list[ReviewScope] = Field(min_length=1)
    unchecked_scope: list[Text] = Field(default_factory=list, max_length=64)
    limitations: list[Text] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def unique_findings_and_scopes(self):
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("review finding ids must be unique")
        if len(self.checked_target_ids) != len(set(self.checked_target_ids)):
            raise ValueError("checked target ids must be unique")
        if len(self.checked_scope) != len(set(self.checked_scope)):
            raise ValueError("checked scope values must be unique")
        return self


class ReviewReportV2(ReviewReport):
    schema_version: Literal["finance.review_report.v2"]
    verdict: Literal["passed", "revision_required", "inconclusive"]
    verdict_reason: Text
    blocking_finding_ids: list[Text] = Field(max_length=100)

    @model_validator(mode="after")
    def consistent_verdict(self):
        blockers = set(self.blocking_finding_ids)
        findings = {item.finding_id: item for item in self.findings}
        if len(blockers) != len(self.blocking_finding_ids) or blockers - findings.keys():
            raise ValueError("blocking finding ids must be unique ids from this report")
        required = {item.finding_id for item in self.findings
                    if item.severity == "material" and item.assessment != "supported"}
        if not required <= blockers:
            raise ValueError("material adverse or inconclusive findings must block approval")
        if self.verdict == "passed" and (blockers or self.unchecked_scope
                                        or any(item.unchecked_scope for item in self.findings)):
            raise ValueError("passed requires no blocking findings or unfinished requested checks")
        if self.verdict == "revision_required" and not blockers:
            raise ValueError("revision_required needs at least one blocking finding")
        return self


class ReviewDisposition(ResearchContract):
    finding_id: Annotated[StrictStr, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
    disposition: Literal["accepted", "rejected_with_reason", "unresolved"]
    rationale: Text | None = None

    @model_validator(mode="after")
    def explain_nontrivial_disposition(self):
        if self.disposition in {"rejected_with_reason", "unresolved"} and not self.rationale:
            raise ValueError("rejected or unresolved findings require a rationale")
        return self


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


class TaskResult(ResearchResult):
    """General tasks keep an open text body with optional research metadata."""

    schema_version: Literal["finance.task_result.v1"]
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    material_disposition: list[MaterialDisposition] = Field(default_factory=list)
    gaps: list[ResearchGap] = Field(default_factory=list)
    limitations: list[Text] = Field(default_factory=list)


class AnalystResearchResult(ResearchResult):
    schema_version: Literal["finance.analyst_result.v2"]
    review_targets: list[ReviewTarget] = Field(default_factory=list, max_length=32)
    review_decision: Literal["request", "skip"]
    review_decision_reason: Text | None = None
    review_dispositions: list[ReviewDisposition] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def review_fields(self):
        target_ids = [target.target_id for target in self.review_targets]
        finding_ids = [item.finding_id for item in self.review_dispositions]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("review target ids must be unique")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("review dispositions must be unique")
        if self.review_decision == "request" and not self.review_targets:
            raise ValueError("review request requires at least one target")
        if self.review_decision == "skip" and not self.review_decision_reason:
            raise ValueError("skipping review requires a reason")
        return self
