"""Typed input and output schemas for built-in skills."""

from threading import RLock
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillInput(BaseModel):
    message: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SkillOutput(BaseModel):
    artifact_types: ClassVar[tuple[str, ...]] = ()

    answer: str
    citations: list[dict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = dict(super().model_json_schema(*args, **kwargs))
        schema["artifact_types"] = list(cls.artifact_types)
        return schema


class FinanceReviewTarget(BaseModel):
    """A material claim or reasoning node selected by the main finance Agent."""

    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=80)
    type: Literal[
        "author_view",
        "objective_fact",
        "prediction",
        "causal_explanation",
        "model_inference",
        "personal_context",
    ]
    scope: Literal["article", "cross_article", "external", "personal"] = "article"
    relation_type: Literal[
        "agreement", "disagreement", "change_over_time", "complementary"
    ] | None = None
    claim_or_question: str = Field(min_length=8, max_length=1000)
    article_refs: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    review_question: str = Field(min_length=8, max_length=600)
    external_check_needed: Literal["yes", "no", "undecided"] = "undecided"
    importance: Literal["core", "supporting", "context"] = "supporting"
    answer_context: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_scope_semantics(self) -> "FinanceReviewTarget":
        if self.relation_type is not None and self.scope != "cross_article":
            raise ValueError("relation_type is only valid for cross_article targets")
        if self.scope == "cross_article" and not self.article_refs:
            raise ValueError("cross_article targets require article_refs")
        if self.scope == "personal" and self.type != "personal_context":
            raise ValueError("personal scope requires a personal_context target")
        if self.type == "personal_context" and self.scope != "personal":
            raise ValueError("personal_context targets require personal scope")
        return self


class GeneralChatOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("message",)

    review_targets: list[FinanceReviewTarget] = Field(default_factory=list, max_length=40)
    review_status: Literal[
        "not_requested", "planned", "partial", "complete", "failed"
    ] = "not_requested"
    open_gaps: list[str] = Field(default_factory=list, max_length=30)
    review_disposition: Literal["continue", "deliver_with_limits"] = "continue"
    review_resolution_reason: str = Field(default="", max_length=1200)


class PolicyImpactOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = (
        "markdown_report",
        "html_report",
        "run_artifact",
    )


class ExternalImpactOutput(PolicyImpactOutput):
    pass


class NewsReportOutput(PolicyImpactOutput):
    pass


class ResearchReportOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("markdown_report",)


class WikiBlueprintOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("json_blueprint",)


_SCHEMA_MODELS_BY_REF: dict[str, type[BaseModel]] = {}
_SCHEMA_MODELS_LOCK = RLock()


def schema_model_ref(model: type[BaseModel]) -> str:
    """Return the stable process-local reference for an already-loaded model."""
    return f"{model.__module__}:{model.__qualname__}"


def register_schema_model(model: type[BaseModel]) -> type[BaseModel]:
    """Register a loaded Pydantic model without importing or replacing code."""
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise ValueError("schema model must be a loaded Pydantic model class")
    reference = schema_model_ref(model)
    with _SCHEMA_MODELS_LOCK:
        registered = _SCHEMA_MODELS_BY_REF.get(reference)
        if registered is not None and registered is not model:
            raise ValueError(f"conflicting schema reference: {reference}")
        _SCHEMA_MODELS_BY_REF[reference] = model
    return model


def resolve_schema_model(value: object) -> type[BaseModel]:
    """Resolve only loaded classes and references already known to this process."""
    if isinstance(value, type) and issubclass(value, BaseModel):
        return register_schema_model(value)
    if isinstance(value, str):
        with _SCHEMA_MODELS_LOCK:
            registered = _SCHEMA_MODELS_BY_REF.get(value)
        if registered is None:
            raise ValueError(f"unknown schema reference: {value}")
        return registered
    raise ValueError("schema value must be a Pydantic model class or registered reference")


for _built_in_schema in (
    SkillInput,
    SkillOutput,
    FinanceReviewTarget,
    GeneralChatOutput,
    ExternalImpactOutput,
    PolicyImpactOutput,
    NewsReportOutput,
    ResearchReportOutput,
    WikiBlueprintOutput,
):
    register_schema_model(_built_in_schema)

del _built_in_schema
