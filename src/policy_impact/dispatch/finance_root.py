"""Durable bridge from the generic Harness scheduler to finance domain steps.

This module deliberately contains no financial prompts or article logic.  A
caller supplies the domain step; this bridge only loads/saves the versioned
finance position and turns a wait decision into a scheduler checkpoint.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from .contracts import (
    CheckpointState,
    Command,
    DispatchError,
    Identifier,
    Submission,
    Text,
    Wait,
)


class FinanceRootState(Command):
    """The domain position carried across financial root Attempts.

    Large article bodies and model transcripts must remain artifacts.  These
    fields contain indexes, compact observations, and artifact references.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["finance.root_state.v1"] = "finance.root_state.v1"
    query: Text
    phase: Identifier
    completed_steps: list[Identifier] = Field(default_factory=list)
    pending_steps: list[Identifier] = Field(default_factory=list)
    research_plan: dict[str, Any] = Field(default_factory=dict)
    article_states: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ledger: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    candidate_answer: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    execution: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    last_event_id: str = Field(default="0", pattern=r"^(0|[1-9][0-9]*)$")


class FinanceStep(Command):
    """One domain decision made during one root Attempt."""

    status: Literal["continuing", "waiting", "succeeded", "partial", "failed"]
    state: FinanceRootState
    wait: Wait | None = None
    submission: Submission | None = None

    @model_validator(mode="after")
    def validate_transition(self):
        if self.status == "continuing":
            if self.wait is not None or self.submission is not None:
                raise ValueError("continuing requires no wait or submission")
            return self
        if self.status == "waiting":
            if self.wait is None or self.submission is not None:
                raise ValueError("waiting requires wait and no submission")
            return self
        if self.wait is not None or self.submission is None:
            raise ValueError("terminal step requires submission and no wait")
        if self.submission.status != self.status:
            raise ValueError("step and submission statuses must match")
        return self


@dataclass(frozen=True)
class FinanceRootContext:
    """Input exposed to the finance domain step, excluding SQL/Redis."""

    query: str
    state: FinanceRootState
    brief: dict[str, Any]
    checkpoint: dict[str, Any] | None
    messages: list[dict[str, Any]]
    observations: list[dict[str, Any]]


FinanceStepFunction = Callable[
    [FinanceRootContext, Any], FinanceStep | Awaitable[FinanceStep]
]


class FinanceRootExecutor:
    """Run exactly one durable finance domain step per Worker invocation.

    The executor is opt-in.  ``Worker`` still owns the lease and all
    persistence boundaries; the domain callback can only use the capabilities
    supplied by that Worker.
    """

    def __init__(self, step: FinanceStepFunction, *, research: bool = False):
        self._step = step
        self._research = research

    async def __call__(self, work, capabilities):
        from .finance_rules import rules_for

        is_research = (
            self._research and work.brief.get("depth") in {1, 2}
            and work.brief.get("role") in rules_for(capabilities).manifest["roles"]
        )
        if not is_research and (work.brief.get("depth") != 0 or work.brief.get("role") != "main"):
            raise DispatchError("contract_error", "finance root executor requires root task")

        state = await self._load_state(work, capabilities)
        context = FinanceRootContext(
            query=state.query,
            state=state,
            brief=work.brief,
            checkpoint=work.checkpoint,
            messages=work.messages,
            observations=work.observations,
        )
        result = self._step(context, capabilities)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, FinanceStep):
            raise DispatchError("contract_error", "finance step returned an invalid result")
        # Every durable domain step must publish a newer state version.  The
        # first invocation has no checkpoint, but it still creates a durable
        # state artifact and must not be allowed to publish revision 1 again.
        if result.state.revision <= state.revision:
            raise DispatchError("contract_error", "finance state revision did not advance")

        state_ref = await capabilities.store_context(self._compact_state(result.state))
        if result.status == "continuing":
            checkpoint = self._checkpoint(work, result.state, state_ref)
            return await capabilities.continue_task(checkpoint)
        if result.status == "waiting":
            checkpoint = self._checkpoint(work, result.state, state_ref)
            return await capabilities.wait(result.wait.model_dump(), checkpoint)

        sections = dict(result.submission.sections)
        if "finance_state" in sections and sections["finance_state"] != state_ref:
            raise DispatchError("contract_error", "finance_state section is reserved")
        sections["finance_state"] = state_ref
        return result.submission.model_copy(update={"sections": sections})

    @staticmethod
    def _compact_state(state: FinanceRootState) -> dict[str, Any]:
        """Keep the checkpoint an index, never a second article/transcript store."""
        payload = state.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise DispatchError("contract_error", "finance root state exceeds the compact limit")
        forbidden_keys = {
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
                    if str(key).casefold() in forbidden_keys:
                        raise DispatchError(
                            "contract_error",
                            f"finance root state contains non-compact field: {key}",
                        )
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)
        return payload

    async def _load_state(self, work, capabilities) -> FinanceRootState:
        if work.checkpoint is not None:
            ref = work.checkpoint.get("context_ref")
            if not isinstance(ref, str) or not ref:
                raise DispatchError("checkpoint_invalid", "finance checkpoint has no context")
            try:
                raw = await capabilities.load_context(ref)
                payload = json.loads(raw)
                return FinanceRootState.model_validate(payload)
            except DispatchError as exc:
                if exc.code in {"stale_lease", "cancelled"}:
                    raise
                raise DispatchError("checkpoint_invalid", "finance state is unavailable") from exc
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise DispatchError("checkpoint_invalid", "invalid finance root state") from exc

        follow_up_query = next(
            (
                message["content"]
                for message in reversed(work.messages)
                if message.get("kind") == "follow_up"
                and isinstance(message.get("content"), str)
            ),
            None,
        )
        execution = {}
        is_follow_up = work.brief.get("previous_task_id") is not None
        if is_follow_up:
            if not follow_up_query:
                raise DispatchError("contract_error", "follow-up task has no research query")
            execution["follow_up_context"] = {
                "previous_task_id": work.brief["previous_task_id"],
                "previous_result_ref": work.brief.get("previous_result_ref"),
                "success_criteria": list(work.brief.get("success_criteria") or []),
            }

        if self._research:
            refs = list(work.brief.get("input_refs") or [])
            assignment = None
            if refs:
                try:
                    first = json.loads(await capabilities.load_context(refs[0]))
                except json.JSONDecodeError:
                    first = None
                if isinstance(first, dict) and first.get("schema_version") == "finance.h2.child_request.v1":
                    assignment = first.get("action") if not is_follow_up else None
                    # Only the accepted TaskBrief defines input grants, not the envelope.
                    refs = refs[1:]
            return FinanceRootState(
                query=follow_up_query if is_follow_up else work.brief["objective"], phase="research",
                execution={"material_refs": refs, "evidence_refs": refs,
                           "research_assignment": assignment, **execution},
            )

        query = next(
            (
                message.get("content")
                for message in work.messages
                if message.get("kind") == "initial" and isinstance(message.get("content"), str)
            ),
            None,
        )
        if query is None:
            # A terminal Thread follow-up intentionally starts a new Task and
            # therefore has only a follow_up mailbox message. It is a new
            # research request, not a replay of the previous Task's state.
            query = follow_up_query
        if not query:
            raise DispatchError("contract_error", "root mailbox has no research query")
        return FinanceRootState(query=query, phase="plan", execution=execution)

    @staticmethod
    def _checkpoint(work, state: FinanceRootState, state_ref: str) -> CheckpointState:
        sequences = [
            int(message["sequence"])
            for message in work.messages
            if str(message.get("sequence", "")).isdigit()
        ]
        previous_cursor = 0
        if isinstance(work.checkpoint, dict):
            previous_cursor = int(work.checkpoint.get("consumed_message_seq", 0) or 0)
        return CheckpointState(
            current_phase=state.phase,
            completed_steps=state.completed_steps,
            pending_steps=state.pending_steps,
            input_refs=list(work.brief.get("input_refs", [])),
            context_ref=state_ref,
            consumed_message_seq=max(sequences, default=previous_cursor),
            last_event_id=state.last_event_id,
        )


__all__ = ["FinanceRootContext", "FinanceRootExecutor", "FinanceRootState", "FinanceStep"]
