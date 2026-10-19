"""Validated scheduling commands; trusted execution identity is never model input."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
Positive = Annotated[int, Field(gt=0)]

TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "timed_out"})
LIVE_ATTEMPTS = frozenset({"queued", "leased", "running"})


class DispatchError(ValueError):
    def __init__(self, code: str, message: str = "", *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message or code)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BudgetRequest(Command):
    model_calls: Positive
    wall_time_ms: Positive


class OutputContract(Command):
    name: Identifier
    version: Identifier


class Spawn(Command):
    dispatch_key: Identifier
    role: Identifier
    role_version: Identifier
    objective: Text
    success_criteria: list[Text] = Field(min_length=1)
    input_refs: list[Identifier]
    output_contract: OutputContract
    requested_budget: BudgetRequest
    parent_context_refs: list[Identifier] = Field(default_factory=list)
    depends_on_task_ids: list[Identifier] = Field(default_factory=list)
    priority: int = 0
    model_profile: Identifier | None = None

    @model_validator(mode="after")
    def unique_refs(self):
        for values in (self.input_refs, self.parent_context_refs, self.depends_on_task_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate references")
        return self


class FollowUp(Command):
    requested_budget: BudgetRequest
    output_contract: OutputContract
    success_criteria: list[Text] = Field(min_length=1)


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
    root_reserve_calls: int = 24
    run_ms: int = 3600000
    run_reserve_ms: int = 600000
    child_reserve_ms: int = 120000
    outbox_high_watermark: int = 1000
    outbox_low_watermark: int = 500
    aging_step_ms: int = 30000
    aging_priority_step: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for k, v in vars(self).items() if k != "stream_name"):
            raise ValueError("scheduler limits must be positive integers")
        if self.root_reserve_calls >= self.root_calls or self.run_reserve_ms >= self.run_ms:
            raise ValueError("reserve must leave research capacity")
        if self.outbox_low_watermark >= self.outbox_high_watermark:
            raise ValueError("outbox low watermark must be below high watermark")
        if not self.stream_name or any(x.isspace() for x in self.stream_name):
            raise ValueError("invalid stream name")
