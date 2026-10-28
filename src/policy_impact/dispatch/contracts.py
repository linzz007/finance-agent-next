"""Validated scheduling commands; trusted execution identity is never model input."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from policy_impact.runtime.research_contracts import (
    ReviewMode,
    ReviewPolicy,
    ReviewRequest,
    ReviewScope,
)

Text = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
TaskName = Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
Positive = Annotated[int, Field(gt=0)]

TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "timed_out"})
LIVE_ATTEMPTS = frozenset({"queued", "leased", "running"})


class DispatchError(ValueError):
    def __init__(self, code: str, message: str = "", *, retryable: bool = False, details=None):
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(message or code)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResearchClock(Command):
    today: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    timezone: Literal["Asia/Shanghai"]

    @field_validator("today")
    @classmethod
    def valid_calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class BudgetRequest(Command):
    model_calls: Positive
    wall_time_ms: Positive


class OutputContract(Command):
    name: Identifier
    version: Identifier


class ReviewOptions(Command):
    scope: list[ReviewScope] | None = Field(default=None, min_length=1, max_length=6)
    instructions: Text | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=5)

    @field_validator("scope")
    @classmethod
    def unique_scope(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("duplicate review scope")
        return value


class AgentSpawnProfileOptions(Command):
    """Specialist-only options exposed by the Analyst Profile."""

    review_mode: ReviewMode | None = Field(
        default=None, description="Analyst only: required, auto, or off; defaults to auto.",
    )
    review_options: ReviewOptions | None = Field(
        default=None, description="Analyst only: optional review scope, instructions, or smaller round cap.",
    )


class AgentSpawnInput(Command):
    """Compact model-facing delegation request; Runtime supplies internal identity fields."""

    task_name: TaskName = Field(description="Short human-readable label for this delegated task.")
    message: Text = Field(description="The task goal and the information the child needs to do it.")
    profile: Identifier = Field(default="general", description="Registered Profile; defaults to general.")
    task_instructions: Text | None = Field(
        default=None, description="Optional method, scope, or delivery details for this task.",
    )
    success_criteria: list[Text] | None = Field(
        default=None,
        description="Task-specific acceptance criteria. Required for specialist Profiles; general has a default.",
        min_length=1,
    )
    input_refs: list[Identifier] = Field(
        default_factory=list, description="Explicitly authorized material references needed by the child.",
    )
    parent_context_refs: list[Identifier] = Field(
        default_factory=list, description="Optional explicit parent-context artifact references.",
    )
    output_contract: OutputContract | None = Field(
        default=None, description="Optional registered output contract; otherwise the Profile default is used.",
    )
    profile_options: AgentSpawnProfileOptions | None = Field(
        default=None, description="Profile-specific options. Currently supported only by Analyst.",
    )
    requested_budget: BudgetRequest | None = Field(
        default=None, description="Required only by historical Runs using requested-budget scheduling.",
    )
    wall_time_ms: Positive | None = Field(
        default=None, description="Optional shorter duration request under the fixed-budget policy.",
    )
    depends_on_task_ids: list[Identifier] = Field(default_factory=list)
    priority: int = 0
    model_profile: Identifier | None = None

    @model_validator(mode="after")
    def unique_refs(self):
        for values in (self.input_refs, self.parent_context_refs, self.depends_on_task_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate references")
        if self.requested_budget is not None and self.wall_time_ms is not None:
            raise ValueError("specify only one duration source")
        return self


class Spawn(Command):
    dispatch_key: Identifier
    role: Identifier
    role_version: Identifier
    objective: Text
    task_instructions: Text | None = None
    success_criteria: list[Text] = Field(min_length=1)
    input_refs: list[Identifier]
    output_contract: OutputContract
    review_policy: ReviewPolicy | None = None
    review_mode: ReviewMode | None = Field(
        default=None, description="Analyst v2 only: required, auto (default), or off for independent review.",
    )
    review_options: ReviewOptions | None = Field(
        default=None,
        description="Optional Analyst v2 review scope, special instructions, or smaller round cap; default cap is five.",
    )
    review_request: ReviewRequest | None = None
    requested_budget: BudgetRequest | None = None
    wall_time_ms: Positive | None = None
    parent_context_refs: list[Identifier] = Field(default_factory=list)
    depends_on_task_ids: list[Identifier] = Field(default_factory=list)
    priority: int = 0
    model_profile: Identifier | None = None

    @model_validator(mode="after")
    def unique_refs(self):
        if self.review_policy is not None and (self.review_mode is not None or self.review_options is not None):
            raise ValueError("use review_mode/review_options or legacy review_policy, not both")
        if self.requested_budget is not None and self.wall_time_ms is not None:
            raise ValueError("specify only one duration source")
        for values in (self.input_refs, self.parent_context_refs, self.depends_on_task_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate references")
        return self


class FollowUp(Command):
    requested_budget: BudgetRequest | None = None
    wall_time_ms: Positive | None = None
    output_contract: OutputContract
    success_criteria: list[Text] = Field(min_length=1)

    @model_validator(mode="after")
    def one_duration(self):
        if self.requested_budget is not None and self.wall_time_ms is not None:
            raise ValueError("specify only one duration source")
        return self


class Send(Command):
    thread_id: Identifier
    idempotency_key: Identifier
    content: Text | None = None
    content_ref: Identifier | None = None
    follow_up: FollowUp | None = None

    @model_validator(mode="after")
    def one_content(self):
        if (self.content is None) == (self.content_ref is None):
            raise ValueError("exactly one of content and content_ref is required")
        return self


class Wait(Command):
    task_ids: list[Identifier] = Field(min_length=1, max_length=200)
    mode: Literal["any", "all", "specific"] = "all"
    after_event_id: str = Field(default="0", pattern=r"^(0|[1-9][0-9]*)$")
    timeout_ms: int = Field(default=30000, ge=0, le=60000)
    max_events: int = Field(default=100, gt=0, le=200)

    @model_validator(mode="after")
    def valid_targets(self):
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("duplicate targets")
        if self.mode == "specific" and len(self.task_ids) != 1:
            raise ValueError("specific requires one target")
        return self


class Cancel(Command):
    task_id: Identifier
    idempotency_key: Identifier
    reason: Text = "cancelled by caller"


class Resume(Command):
    task_id: Identifier
    idempotency_key: Identifier
    checkpoint_ref: Identifier | None = None
    model_profile: Identifier | None = None


class ModelPolicySet(Command):
    idempotency_key: Identifier
    expected_policy_ref: Identifier
    selected_profile: Identifier
    allowed_profiles: list[Identifier] = Field(min_length=1)
    auto_fallback: Literal[False] = False

    @model_validator(mode="after")
    def valid_profiles(self):
        if (
            len(set(self.allowed_profiles)) != len(self.allowed_profiles)
            or self.selected_profile not in self.allowed_profiles
        ):
            raise ValueError("invalid allowed profiles")
        return self


class CheckpointState(Command):
    current_phase: Identifier
    completed_steps: list[Identifier]
    pending_steps: list[Identifier]
    input_refs: list[Identifier]
    partial_result_ref: Identifier | None = None
    context_ref: Identifier
    consumed_message_seq: int = Field(ge=0)
    last_event_id: str = Field(pattern=r"^(0|[1-9][0-9]*)$")


class Submission(Command):
    result_key: Identifier
    status: Literal["succeeded", "partial", "failed", "cancelled"] = "succeeded"
    content: str | None = None
    full_ref: Identifier | None = None
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    sections: dict[str, Identifier] = Field(default_factory=dict)
    evidence_refs: list[dict] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_body(self):
        if (self.content is None) == (self.full_ref is None):
            raise ValueError("exactly one body is required")
        return self


@dataclass(frozen=True)
class CallerContext:
    run_id: str
    thread_id: str
    task_id: str
    attempt_id: str | None = None
    lease_token: str | None = field(default=None, repr=False)
    trusted_user: bool = False


@dataclass(frozen=True)
class Lease:
    run_id: str
    thread_id: str
    task_id: str
    attempt_id: str
    worker_id: str
    token: str = field(repr=False)

    def caller(self) -> CallerContext:
        return CallerContext(self.run_id, self.thread_id, self.task_id, self.attempt_id, self.token)


@dataclass(frozen=True)
class SchedulerConfig:
    budget_policy: Literal["fixed", "hierarchical"] = "fixed"
    stream_name: str = "finance-agent-next:local:h2:dispatch"
    lease_ms: int = 60000
    publisher_lease_ms: int = 30000
    republish_ms: int = 60000
    publish_attempts: int = 8
    max_attempts: int = 5
    run_concurrency: int = 8
    worker_concurrency: int = 4
    max_children: int = 40
    root_calls: int = 600
    root_task_calls: int = 120
    child_task_calls: int = 60
    child_ms: int = 1200000
    root_reserve_calls: int = 24
    run_ms: int = 3600000
    run_reserve_ms: int = 600000
    child_reserve_ms: int = 120000
    outbox_high_watermark: int = 1000
    outbox_low_watermark: int = 500
    aging_step_ms: int = 30000
    aging_priority_step: int = 1

    def __post_init__(self):
        if self.budget_policy not in {"fixed", "hierarchical"}:
            raise ValueError("invalid budget policy")
        if any(type(v) is not int or v <= 0 for k, v in vars(self).items()
               if k not in {"stream_name", "budget_policy"}):
            raise ValueError("scheduler limits must be positive integers")
        if self.root_reserve_calls >= self.root_calls or self.run_reserve_ms >= self.run_ms:
            raise ValueError("reserve must leave research capacity")
        if self.outbox_low_watermark >= self.outbox_high_watermark:
            raise ValueError("outbox low watermark must be below high watermark")
        if not self.stream_name or any(x.isspace() for x in self.stream_name):
            raise ValueError("invalid stream name")
