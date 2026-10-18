"""Context-local budgets shared by a financial run and its child executions."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
import os
import time

from policy_impact.harness.context_manifest import estimate_tokens


MAX_CONTEXT_TOKENS = 1_000_000


@dataclass(frozen=True)
class ResearchLimits:
    run_seconds: int = 3600
    final_seconds: int = 600
    child_seconds: int = 1200
    child_final_seconds: int = 120
    root_requests: int = 600
    root_final_requests: int = 24
    decision_requests: int = 5
    max_delegates: int = 40
    input_tokens: int = MAX_CONTEXT_TOKENS
    context_tokens: int = MAX_CONTEXT_TOKENS
    output_tokens: int = 32768
    safety_tokens: int = 4096
    context_pressure_percent: int = 70
    context_target_percent: int = 60

    def __post_init__(self):
        if not 0 < self.context_target_percent < self.context_pressure_percent <= 100:
            raise ValueError("context percentages must satisfy 0 < target < pressure <= 100")

    @classmethod
    def from_env(cls):
        values = {}
        for key, default in asdict(cls()).items():
            value = int(os.getenv("FINANCE_RESEARCH_" + key.upper(), str(default)))
            if value < (0 if key == "context_tokens" else 1):
                raise ValueError(f"invalid finance research budget: {key}")
            values[key] = value
        if values["final_seconds"] >= values["run_seconds"]:
            raise ValueError("final_seconds must be below run_seconds")
        if values["child_final_seconds"] >= values["child_seconds"]:
            raise ValueError("child_final_seconds must be below child_seconds")
        if values["root_final_requests"] >= values["root_requests"]:
            raise ValueError("root_final_requests must be below root_requests")
        if values["decision_requests"] > 5:
            raise ValueError("decision_requests must not exceed 5")
        return cls(**values)

    def input_limit(self, output_tokens=16384):
        # An unknown provider window is not unlimited; keep the project ceiling.
        ceiling = min(self.context_tokens or MAX_CONTEXT_TOKENS, MAX_CONTEXT_TOKENS)
        limit = min(self.input_tokens, MAX_CONTEXT_TOKENS, ceiling - output_tokens)
        return max(0, limit - self.safety_tokens)

    def context_pressure_limits(self, output_tokens=16384):
        capacity = min(self.input_tokens, self.context_tokens or MAX_CONTEXT_TOKENS, MAX_CONTEXT_TOKENS)
        trigger = min(self.input_limit(output_tokens), capacity * self.context_pressure_percent // 100)
        if trigger < 2:
            return 0, 0
        # Preserve hysteresis if the output reserve clamps a small window.
        target = min(capacity * self.context_target_percent // 100,
                     trigger * self.context_target_percent // self.context_pressure_percent,
                     trigger - 1)
        return trigger, max(1, target)

    def describe(self):
        trigger, target = self.context_pressure_limits(self.output_tokens)
        return {**asdict(self), "capacity_status": "configured" if self.context_tokens else "provisional",
                "effective_context_ceiling_tokens": min(self.context_tokens or MAX_CONTEXT_TOKENS,
                                                        MAX_CONTEXT_TOKENS),
                "effective_input_tokens": self.input_limit(self.output_tokens),
                "context_pressure_tokens": trigger, "context_target_tokens": target,
                "capacity_source": "configuration", "estimator": "mixed_cjk_json_v1",
                "capacity_verified": False}


@dataclass
class RunBudget:
    limits: ResearchLimits
    deadline: float
    requests: int = 0
    attempts: list[dict] = field(default_factory=list)


@dataclass
class BudgetScope:
    root: RunBudget
    deadline: float
    reserve_seconds: float
    stage: str

    def remaining(self):
        return max(0.0, min(self.deadline, self.root.deadline) - time.monotonic())

    def acquiring(self):
        return (self.remaining() > self.reserve_seconds and
                self.root.requests < self.root.limits.root_requests - self.root.limits.root_final_requests)


CURRENT: ContextVar[BudgetScope | None] = ContextVar("finance_research_budget", default=None)


@dataclass
class DecisionBudget:
    requests: int = 0


DECISION: ContextVar[DecisionBudget | None] = ContextVar("finance_model_decision", default=None)


@contextmanager
def decision_scope(existing=None):
    budget = DECISION.get() or existing or DecisionBudget()
    token = DECISION.set(budget)
    try:
        yield budget
    finally:
        DECISION.reset(token)


def bounded_decision(function):
    @wraps(function)
    def run(*args, **kwargs):
        with decision_scope():
            return function(*args, **kwargs)
    return run


@contextmanager
def execution_scope(*, limits=None, timeout_seconds=None, reserve_seconds=0, stage="research"):
    parent = CURRENT.get()
    limits = limits or (parent.root.limits if parent else ResearchLimits.from_env())
    now = time.monotonic()
    timeout = limits.run_seconds if timeout_seconds is None else timeout_seconds
    parent_deadline = parent.deadline - parent.reserve_seconds if parent else now + timeout
    deadline = min(now + timeout, parent_deadline)
    if deadline <= now:
        raise TimeoutError("no allocatable parent research budget remains")
    root = parent.root if parent else RunBudget(limits, deadline)
    scope = BudgetScope(root, deadline, min(reserve_seconds, max(0, deadline - now)), stage)
    token = CURRENT.set(scope)
    try:
        yield scope
    finally:
        CURRENT.reset(token)


def check_input(messages, *, output_tokens=16384):
    scope = CURRENT.get()
    if scope is None:
        return
    estimated = estimate_tokens(messages)
    limit = scope.root.limits.input_limit(output_tokens)
    if estimated > limit:
        raise ValueError(f"context_budget_exceeded: estimated={estimated}, input_limit={limit}; "
                         "restore/select scoped materials or let the main Agent coordinate segments")


def source_timeout(timeout_seconds):
    scope = CURRENT.get()
    if scope is None:
        return timeout_seconds
    if not scope.acquiring():
        raise TimeoutError("research acquisition boundary reached; preserve finalization reserve")
    return min(timeout_seconds, scope.remaining() - scope.reserve_seconds)


def tighten_response_timeout(response, idle_seconds, *, remaining_seconds=None):
    scope = CURRENT.get()
    remaining = scope.remaining() if scope else idle_seconds
    if remaining_seconds is not None:
        remaining = min(remaining, remaining_seconds)
    if remaining <= 0:
        raise TimeoutError("model response execution deadline exhausted")
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(min(idle_seconds, remaining))


def read_response_bytes(response, idle_seconds, *, max_bytes=8 * 1024 * 1024):
    scope = CURRENT.get()
    if scope is None:
        return response.read()
    if not hasattr(response, "read1"):
        tighten_response_timeout(response, idle_seconds)
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("model response size limit exceeded")
        return body
    chunks, size = [], 0
    while True:
        tighten_response_timeout(response, idle_seconds)
        chunk = response.read1(min(65536, max_bytes + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("model response size limit exceeded")


def consume_request(*, attempt, schema_name):
    scope = CURRENT.get()
    if scope is None:
        return
    decision = DECISION.get()
    if decision and decision.requests >= scope.root.limits.decision_requests:
        raise RuntimeError("shared logical model decision request budget exhausted")
    if scope.remaining() <= 0:
        raise TimeoutError("research execution deadline exhausted")
    if scope.root.requests >= scope.root.limits.root_requests:
        raise RuntimeError("research model request budget exhausted")
    if (scope.stage != "finance.main" and scope.root.requests >=
            scope.root.limits.root_requests - scope.root.limits.root_final_requests):
        raise RuntimeError("parent finalization request reserve reached")
    scope.root.requests += 1
    if decision:
        decision.requests += 1
    scope.root.attempts.append({"request_index": scope.root.requests, "stage": scope.stage,
                               "attempt": attempt, "schema_name": schema_name})


def finance_execution(function):
    @wraps(function)
    def run(runtime, *args, **kwargs):
        limits = ResearchLimits.from_env()
        with execution_scope(limits=limits, reserve_seconds=limits.final_seconds, stage="finance.main") as scope:
            try:
                return function(runtime, *args, **kwargs)
            finally:
                runtime.store.add_agent_step(
                    kwargs["run_id"], step_type="research_budget", title="金融研究共享预算总账",
                    status="done", output_payload={"limits": limits.describe(),
                                                   "physical_requests": scope.root.requests,
                                                   "attempts": scope.root.attempts,
                                                   "remaining_seconds": scope.remaining()},
                    metadata={"loop_phase": "stop"})
    return run
