"""Structured action contracts for the controlled agent loop."""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from policy_impact.runtime.research_contracts import ResearchTask

ALLOWED_ACTION_NAMES = ("final", "tool_call", "delegate", "request_approval")


def _validate_json_value(value: Any, *, path: str = "value") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} must use string keys")
        for key, item in value.items():
            _validate_json_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} must contain JSON-compatible values")


class _ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_serializer(mode="wrap")
    def omit_empty_optional_fields(self, handler):
        """Keep the public action envelope compact on the wire."""
        result = handler(self)
        return {key: value for key, value in result.items() if value is not None}


class ArticleStateUpdate(_ActionModel):
    """Model suggestions for a discovered article; runtime owns read/use facts."""

    article_id: str
    relevance: Literal["primary", "secondary", "background"] | None = None
    requested_depth: Literal["shallow", "deep"] | None = None
    disposition: Literal["considered", "exclude", "follow_up"] | None = None
    reason: str | None = None

    @field_validator("article_id", "reason", mode="before")
    @classmethod
    def validate_text(cls, value: object, info: Any) -> object:
        if value is None and info.field_name == "reason":
            return value
        return _nonblank_text(value, field_name=info.field_name)


class EvidenceAddition(_ActionModel):
    claim_id: str
    evidence_ref: str
    relation: Literal["supports", "contradicts", "contextualizes"]
    source_kind: Literal["article", "external", "model_inference"]
    scope: str
    certainty: Literal["direct", "conditional", "inconclusive"]
    note: str | None = None

    @field_validator("claim_id", "evidence_ref", "scope", "note", mode="before")
    @classmethod
    def validate_text(cls, value: object, info: Any) -> object:
        if value is None and info.field_name == "note":
            return value
        return _nonblank_text(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_certainty(self):
        if self.source_kind == "model_inference" and self.certainty == "direct":
            raise ValueError("model_inference cannot be marked direct")
        return self


class StateUpdate(_ActionModel):
    """Optional compact finance Root delta carried by an H2 model action."""

    research_plan: dict[str, Any] | None = None
    article_updates: list[ArticleStateUpdate] = Field(default_factory=list)
    evidence_additions: list[EvidenceAddition] = Field(default_factory=list)
    coverage: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None

    @field_validator("research_plan", "coverage", "review", "delivery")
    @classmethod
    def validate_json_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            _validate_json_value(value)
        return value

    @model_validator(mode="after")
    def reject_non_compact_fields(self):
        forbidden = {
            "article_body",
            "article_content",
            "body",
            "full_text",
            "model_output",
            "prompt",
            "raw_content",
            "transcript",
        }

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).casefold() in forbidden:
                        raise ValueError(f"state_update contains non-compact field: {key}")
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.model_dump(mode="python"))
        return self


class FinalAction(_ActionModel):
    type: Literal["final"]
    output: dict[str, Any]
    state_update: StateUpdate | None = None

    @field_validator("output")
    @classmethod
    def validate_output(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="output")
        return value


class ToolCallAction(_ActionModel):
    type: Literal["tool_call"]
    tool_name: str
    arguments: dict[str, Any]
    state_update: StateUpdate | None = None

    @field_validator("tool_name", mode="before")
    @classmethod
    def validate_tool_name(cls, value: object) -> object:
        name = _nonblank_text(value, field_name="tool_name")
        if name in ALLOWED_ACTION_NAMES:
            raise ValueError(
                f"{name!r} is an action type, not a tool name; use type={name!r}"
            )
        return name

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="arguments")
        return value


class DelegateAction(_ActionModel):
    type: Literal["delegate"]
    role: str
    task: str | None = None
    input_artifact_refs: tuple[str, ...] = ()
    role_version: str | None = None
    research_task: ResearchTask | None = None
    state_update: StateUpdate | None = None

    @model_validator(mode="after")
    def versioned_profile(self):
        if self.role_version == "2.0.0":
            if self.role != "analyst" or self.research_task is None:
                raise ValueError("analyst@2.0.0 requires research_task")
            if self.task is not None or self.input_artifact_refs:
                raise ValueError("v2 uses research_task once; omit task/input_artifact_refs")
        elif self.role_version in {None, "1.0.0"}:
            if self.task is None or self.research_task is not None:
                raise ValueError("legacy delegation requires task and no research_task")
        else:
            raise ValueError("unsupported role_version; no implicit fallback is allowed")
        return self

    @model_serializer(mode="wrap")
    def wire_profile(self, handler):
        result = handler(self)
        for key in ("role_version", "research_task", "task"):
            if result.get(key) is None:
                result.pop(key, None)
        if self.research_task is not None:
            result.pop("input_artifact_refs", None)
        return result

    @field_validator("role", "task", mode="before")
    @classmethod
    def validate_required_text(cls, value: object, info: Any) -> object:
        return _nonblank_text(value, field_name=info.field_name)

    @field_validator("input_artifact_refs", mode="after")
    @classmethod
    def validate_artifact_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            _nonblank_text(value, field_name="input_artifact_refs") for value in values
        )


class RequestApprovalAction(_ActionModel):
    type: Literal["request_approval"]
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    state_update: StateUpdate | None = None

    @field_validator("tool_name", "reason", mode="before")
    @classmethod
    def validate_required_text(cls, value: object, info: Any) -> object:
        return _nonblank_text(value, field_name=info.field_name)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="arguments")
        return value


def _nonblank_text(value: object, *, field_name: str) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


AgentAction = Annotated[
    FinalAction | ToolCallAction | DelegateAction | RequestApprovalAction,
    Field(discriminator="type"),
]

StopReason = Literal[
    "success",
    "deterministic_complete",
    "max_steps",
    "max_model_calls",
    "timeout",
    "model_error",
    "invalid_model_action",
    "tool_denied",
    "tool_failed",
    "tool_timeout",
    "unknown_outcome",
    "invalid_tool_output",
    "repeated_tool_failure",
    "context_budget_exceeded",
    "integrity_error",
    "persistence_error",
    "awaiting_approval",
    "gate_blocked",
    "cancelled",
    "delegate_failed",
]


__all__ = [
    "ALLOWED_ACTION_NAMES",
    "AgentAction",
    "ArticleStateUpdate",
    "DelegateAction",
    "EvidenceAddition",
    "FinalAction",
    "RequestApprovalAction",
    "StateUpdate",
    "StopReason",
    "ToolCallAction",
]
