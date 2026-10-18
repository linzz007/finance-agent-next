"""Controlled model/tool loop for policy impact agents."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import sha256
from math import isfinite
from os import PathLike
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.context_offload import RunContextStore
from policy_impact.harness.model_gateway import ModelAdapter, ModelResponse
from policy_impact.harness.state import PolicyImpactState, utc_now_iso
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.actions import (
    ALLOWED_ACTION_NAMES,
    AgentAction,
    DelegateAction,
    FinalAction,
    RequestApprovalAction,
    StopReason,
    ToolCallAction,
)
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.subagent_roles import (
    SubagentRoleRegistry,
    build_default_subagent_role_registry,
)
from policy_impact.runtime.task_brief import DelegationEvidence, SubagentResult, TaskBrief
from policy_impact.skills.manifest import SkillManifest


_ACTION_ADAPTER = TypeAdapter(AgentAction)
_HIDDEN_REASONING_FIELDS = frozenset({"reasoning", "thinking", "analysis"})
_OMIT = object()


class FinalGuardBlocked(RuntimeError):
    """A final validator exhausted its repair budget; stop without publishing."""


@dataclass(init=False)
class AgentLoopConfig:
    stage_name: str
    agent_role: str
    context_manifest: ContextManifestRecord
    output_key: str
    execution_mode: ExecutionMode
    max_steps: int
    max_model_calls: int
    timeout_seconds: float
    max_calls_per_tool: int
    max_total_tool_calls: int | None
    max_repeated_failures: int
    max_delegate_denials: int
    task_prompt: str
    workspace_prompt: str
    context_pressure_tokens: int | None
    context_target_tokens: int | None
    finalization_reserve_calls: int
    recover_tool_timeouts: bool
    protocol_in_workspace_prompt: bool

    def __init__(
        self,
        stage_name: str,
        agent_role: str,
        context_manifest: ContextManifestRecord,
        output_key: str,
        max_steps: int | None = None,
        max_model_calls: int = 4,
        timeout_seconds: float = 120,
        max_calls_per_tool: int = 3,
        max_total_tool_calls: int | None = None,
        max_repeated_failures: int = 2,
        max_delegate_denials: int = 2,
        task_prompt: str = "",
        workspace_prompt: str = "",
        context_pressure_tokens: int | None = None,
        finalization_reserve_calls: int = 0,
        recover_tool_timeouts: bool = False,
        *,
        max_turns: int | None = None,
        execution_mode: ExecutionMode = ExecutionMode.AGENT_LOOP,
        context_target_tokens: int | None = None,
        protocol_in_workspace_prompt: bool = False,
    ) -> None:
        if max_steps is not None and max_turns is not None and max_steps != max_turns:
            raise ValueError("max_steps and legacy max_turns must match when both are provided")
        self.stage_name = stage_name
        self.agent_role = agent_role
        self.context_manifest = context_manifest
        self.output_key = output_key
        self.execution_mode = ExecutionMode(execution_mode)
        self.max_steps = _positive_int(
            "max_steps",
            max_steps if max_steps is not None else max_turns if max_turns is not None else 4,
        )
        self.max_model_calls = _positive_int("max_model_calls", max_model_calls)
        self.timeout_seconds = _positive_number("timeout_seconds", timeout_seconds)
        self.max_calls_per_tool = _positive_int("max_calls_per_tool", max_calls_per_tool)
        self.max_total_tool_calls = (
            None if max_total_tool_calls is None
            else _positive_int("max_total_tool_calls", max_total_tool_calls)
        )
        self.max_repeated_failures = _positive_int(
            "max_repeated_failures", max_repeated_failures
        )
        self.max_delegate_denials = _positive_int(
            "max_delegate_denials", max_delegate_denials
        )
        self.task_prompt = task_prompt
        self.workspace_prompt = workspace_prompt
        self.context_pressure_tokens = (
            None if context_pressure_tokens is None
            else _positive_int("context_pressure_tokens", context_pressure_tokens)
        )
        self.context_target_tokens = (
            None if context_target_tokens is None
            else _positive_int("context_target_tokens", context_target_tokens)
        )
        if self.context_target_tokens is not None and (
            self.context_pressure_tokens is None or self.context_target_tokens >= self.context_pressure_tokens
        ):
            raise ValueError("context_target_tokens requires a larger context_pressure_tokens")
        if type(finalization_reserve_calls) is not int or not 0 <= finalization_reserve_calls < self.max_model_calls:
            raise ValueError("finalization_reserve_calls must be below max_model_calls")
        self.finalization_reserve_calls = finalization_reserve_calls
        self.recover_tool_timeouts = bool(recover_tool_timeouts)
        self.protocol_in_workspace_prompt = bool(protocol_in_workspace_prompt)

    @property
    def max_turns(self) -> int:
        """Temporary read alias for callers migrating to ``max_steps``."""

        return self.max_steps


@dataclass(frozen=True)
class _LoopBudgets:
    max_steps: int
    max_model_calls: int
    timeout_seconds: float
    max_calls_per_tool: int
    max_total_tool_calls: int | None
    max_repeated_failures: int


@dataclass
class AgentTurn:
    turn_index: int
    model_payload: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentLoopResult:
    stop_reason: StopReason
    final_output: dict[str, Any]
    turns: list[AgentTurn] = field(default_factory=list)
    actual_execution_mode: ExecutionMode = ExecutionMode.MODEL_ONCE
    delegation_evidence: tuple[DelegationEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "final_output": _normalize_json_value(self.final_output),
            "turns": [turn.to_dict() for turn in self.turns],
            "actual_execution_mode": self.actual_execution_mode.value,
            "delegation_evidence": [
                evidence.model_dump(mode="json") for evidence in self.delegation_evidence
            ],
        }


class _InvalidJsonValue(ValueError):
    pass


class _ContextProgressCheckpoint:
    """Require a durable progress decision after restoration causes new pressure."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self.emit = emit
        self.pressure_generation = 0
        self.last_restore_generation: dict[str, int] = {}
        self.repeated_ids: set[str] = set()
        self.active = False
        self.denials = 0
        self.seen_note_ids: set[str] = set()
        self.plan_snapshot: dict[str, Any] | None = None

    def observe_offload(self, offloaded: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not offloaded:
            return None
        self.pressure_generation += 1
        if self.repeated_ids and not self.active:
            return self._activate()
        return None

    def observe_restore(self, context_id: str) -> dict[str, Any] | None:
        previous = self.last_restore_generation.get(context_id)
        checkpoint_event = None
        if previous is not None:
            self.repeated_ids.add(context_id)
            if self.pressure_generation > previous and not self.active:
                checkpoint_event = self._activate()
        self.last_restore_generation[context_id] = self.pressure_generation
        return checkpoint_event

    def _activate(self) -> dict[str, Any]:
        self.active = True
        self.denials = 0
        event = {
            "code": "context_progress_required",
            "decision": "checkpoint",
            "context_ids": sorted(self.repeated_ids),
            "pressure_generation": self.pressure_generation,
            "reason": "Repeated restoration of old observations coincided with new context-pressure offload.",
            "instruction": (
                "Pause context.open. Save a new evidence-bound research_note or validated v2 research result, update a research.plan "
                "goal with a concrete progress note, or return a truthful final answer that states "
                "what remains unfinished. Other research tools remain available."
            ),
        }
        self.emit(dict(event))
        return event

    def deny_restore(self) -> dict[str, Any]:
        self.denials += 1
        event = {
            "code": "context_progress_required",
            "decision": "deny",
            "denial_count": self.denials,
            "retryable": self.denials < 3,
            "instruction": (
                "context.open is paused until a new evidence-bound research_note, validated v2 result, or a concrete "
                "research.plan progress update succeeds. You may also return a truthful final answer."
            ),
        }
        self.emit(dict(event))
        return event

    def observe_tool(self, name: str, arguments: dict[str, Any], result: Any) -> None:
        if not isinstance(result, dict):
            return
        if name == "research.artifact" and arguments.get("operation") == "save":
            self.observe_research_result(result)
            if result.get("kind") == "research_note" and result.get("entry_count", 0) > 0:
                note_id = str(result.get("artifact_id") or "")
                if note_id and note_id not in self.seen_note_ids:
                    self.seen_note_ids.add(note_id)
                    if self.active:
                        self._release("research_note", note_id)
        if name == "research.plan":
            previous = self.plan_snapshot
            if arguments.get("operation") in {"create", "update", "get", "load"}:
                self.plan_snapshot = result
            if not self.active or arguments.get("operation") != "update" or previous is None:
                return
            goal_id = arguments.get("goal_id")
            if isinstance(goal_id, str) and re.fullmatch(r"[1-9][0-9]*", goal_id):
                goal_id = int(goal_id)
            old_goals = previous.get("goals")
            new_goals = result.get("goals")
            if (type(goal_id) is not int or not isinstance(old_goals, list)
                    or not isinstance(new_goals, list)
                    or not 1 <= goal_id <= min(len(old_goals), len(new_goals))):
                return
            old, new = old_goals[goal_id - 1], new_goals[goal_id - 1]
            note = str(new.get("note") or "").strip()
            if (new.get("status") in {"done", "blocked"}
                    and new.get("status") != old.get("status")
                    and note == str(arguments.get("note") or "").strip()
                    and len(note) >= 20):
                self._release("research_plan", str(goal_id))

    def observe_research_result(self, receipt):
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != "finance.research_artifact.v2"
                or receipt.get("persistence_status") != "saved" or receipt.get("body_validated") is not True
                or receipt.get("status") != "validated"):
            return
        ref = receipt.get("artifact_id")
        if not isinstance(ref, str) or not re.fullmatch(r"(?:understanding|synthesis|view)_[0-9a-f]{24}", ref):
            return
        if ref not in self.seen_note_ids:
            self.seen_note_ids.add(ref)
            if self.active:
                self._release("research_result", ref)

    def _release(self, kind: str, reference: str) -> None:
        self.active = False
        self.denials = 0
        self.last_restore_generation.clear()
        self.repeated_ids.clear()
        self.emit({"code": "context_progress_recorded", "decision": "release",
                   "kind": kind, "reference": reference})


class AgentLoop:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_gateway: ToolGateway,
        *,
        delegate_fn: Callable[[TaskBrief], SubagentResult] | None = None,
        skill_manifest: SkillManifest | None = None,
        role_registry: SubagentRoleRegistry | None = None,
        reject_hidden_reasoning: bool = False,
        output_schema: type[BaseModel] | None = None,
        final_guard: Callable[[dict[str, Any]], str | None] | None = None,
        context_store: RunContextStore | None = None,
        on_context_offload: Callable[[dict[str, Any]], None] | None = None,
        on_context_restore: Callable[[dict[str, Any]], None] | None = None,
        on_context_checkpoint: Callable[[dict[str, Any]], None] | None = None,
        role_aliases: Mapping[str, str] | None = None,
        on_model_input: Callable[[list[dict[str, Any]], ModelResponse], None] | None = None,
    ) -> None:
        if delegate_fn is not None and not callable(delegate_fn):
            raise TypeError("delegate_fn must be callable")
        self.model_adapter = model_adapter
        self.tool_gateway = tool_gateway
        self.delegate_fn = delegate_fn
        if skill_manifest is not None and not isinstance(skill_manifest, SkillManifest):
            raise TypeError("skill_manifest must be a SkillManifest")
        self.skill_manifest = skill_manifest
        self.role_registry = role_registry or build_default_subagent_role_registry()
        if not isinstance(reject_hidden_reasoning, bool):
            raise TypeError("reject_hidden_reasoning must be a bool")
        self.reject_hidden_reasoning = reject_hidden_reasoning
        self.output_schema = output_schema
        if final_guard is not None and not callable(final_guard):
            raise TypeError("final_guard must be callable")
        self.final_guard = final_guard
        self.context_store = context_store
        self.on_context_offload = on_context_offload
        self.on_context_restore = on_context_restore
        self.on_context_checkpoint = on_context_checkpoint
        self.role_aliases = dict(role_aliases or {})
        self.on_model_input = on_model_input

    def run(
        self,
        state: PolicyImpactState,
        config: AgentLoopConfig,
        *,
        max_steps: int | None = None,
        max_model_calls: int | None = None,
        timeout_seconds: float | None = None,
        max_calls_per_tool: int | None = None,
        max_total_tool_calls: int | None = None,
        max_repeated_failures: int | None = None,
    ) -> AgentLoopResult:
        if config.execution_mode is ExecutionMode.DETERMINISTIC:
            # Deterministic executors own their output; this loop only records the mode guard.
            return AgentLoopResult(
                "deterministic_complete",
                {},
                [],
                actual_execution_mode=ExecutionMode.DETERMINISTIC,
            )
        if config.execution_mode is ExecutionMode.WORKFLOW:
            raise ValueError("ExecutionMode.WORKFLOW is not supported by AgentLoop")

        budgets = _resolve_budgets(
            config,
            max_steps=max_steps,
            max_model_calls=max_model_calls,
            timeout_seconds=timeout_seconds,
            max_calls_per_tool=max_calls_per_tool,
            max_total_tool_calls=max_total_tool_calls,
            max_repeated_failures=max_repeated_failures,
        )
        if config.execution_mode is ExecutionMode.MODEL_ONCE:
            budgets = _LoopBudgets(
                max_steps=budgets.max_steps,
                max_model_calls=1,
                timeout_seconds=budgets.timeout_seconds,
                max_calls_per_tool=budgets.max_calls_per_tool,
                max_total_tool_calls=budgets.max_total_tool_calls,
                max_repeated_failures=budgets.max_repeated_failures,
            )
        deadline = time.monotonic() + budgets.timeout_seconds
        from policy_impact.harness.research_budget import CURRENT

        research_scope = CURRENT.get()
        if research_scope:
            deadline = min(deadline, research_scope.deadline)
        messages = [
            _initial_message(config),
            {
                "role": "user",
                "content": {
                    "task": config.task_prompt
                    or f"Complete the {config.agent_role} review using only visible_context.",
                    "output_key": config.output_key,
                    "required_response": {"type": "final", "output": "structured object"},
                },
            },
        ]
        if self.output_schema is not None:
            messages[0]["content"]["output_json_schema"] = self.output_schema.model_json_schema()
            messages[0]["content"]["instructions"].append(
                "The final output must match output_json_schema exactly. Do not add undeclared fields."
            )
        turns: list[AgentTurn] = []
        model_call_count = 0
        step_count = 0
        invalid_repair_count = 0
        delegate_denial_count = 0
        nonfinal_action_count = 0
        evidence_progress_count = 0
        delegation_evidence: list[DelegationEvidence] = []
        tool_call_counts: dict[str, int] = defaultdict(int)
        failed_call_counts: dict[str, int] = defaultdict(int)
        context_restore_counts: dict[str, int] = defaultdict(int)
        context_checkpoint = (
            _ContextProgressCheckpoint(self.on_context_checkpoint)
            if self.context_store is not None and self.on_context_checkpoint is not None
            else None
        )
        last_final_gate_signature = ""
        repeated_final_gate_count = 0
        finalization_prompt_added = False
        research_boundary_added = False
        plan_recovery_state = {"gate_count": 0, "fallback_used": False}
        decision_budget = None
        reserve_calls = min(
            config.finalization_reserve_calls, budgets.max_model_calls // 5
        )

        while True:
            budget_stop = _budget_stop_reason(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            )
            if budget_stop is not None:
                return _loop_result(
                    budget_stop,
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if config.context_pressure_tokens and self.context_store:
                offloaded = self._offload_old_observations(
                    messages, config.context_pressure_tokens, config.context_target_tokens,
                )
                if context_checkpoint and offloaded:
                    checkpoint_event = context_checkpoint.observe_offload(offloaded)
                    if checkpoint_event:
                        messages.append({"role": "user", "content": {
                            "context_checkpoint": checkpoint_event,
                            "instruction": checkpoint_event["instruction"],
                        }})

            if (
                (reserve_calls and model_call_count >= budgets.max_model_calls - reserve_calls
                 or research_scope is not None and not research_scope.acquiring())
                and not research_boundary_added
            ):
                messages[0]["content"]["instructions"].append(
                    "Research acquisition is closed for this turn. Use the evidence already obtained; "
                    "update the plan, coverage and verification checkpoints as needed, then deliver "
                    "the substantive answer with explicit sample limits and unresolved gaps."
                )
                research_boundary_added = True

            # Preserve one model call for a usable final answer. Research tools
            # remain model-selected until the boundary; this guard only prevents
            # the last call from becoming another acquisition step when the loop
            # has already gathered evidence.
            if (
                model_call_count == budgets.max_model_calls - 1
                and not finalization_prompt_added
            ):
                messages[0]["content"]["instructions"].append(
                    "Budget boundary: this is the last permitted model call. Return type=final "
                    "now using the evidence already visible in the conversation. Do not call "
                    "another tool or delegate another subagent. If coverage is incomplete, "
                    "state exactly what was read and what remains unverified instead of claiming "
                    "completion."
                )
                finalization_prompt_added = True

            model_call_count += 1
            try:
                if research_scope:
                    from policy_impact.harness.research_budget import check_input
                    check_input(messages, output_tokens=getattr(self.model_adapter, "max_tokens", 16384))
                from policy_impact.harness.research_budget import decision_scope
                with decision_scope(decision_budget) as current_decision:
                    decision_budget = current_decision
                    response = self.model_adapter.complete(messages, schema_name=config.output_key)
                response = _normalize_legacy_loop_response(response, config.output_key)
                if self.on_model_input:
                    self.on_model_input(messages, response)
            except Exception as exc:  # noqa: BLE001
                error = _public_model_error_reason(exc)
                failure_payload = {
                    "type": "model_error",
                    "error": {
                        "category": "model_error",
                        "error_type": type(exc).__name__,
                        "message": error,
                    },
                }
                failure_metadata = _model_failure_metadata(
                    adapter=self.model_adapter,
                    config=config,
                    messages=messages,
                    model_call_count=model_call_count,
                    error=exc,
                )
                turn = AgentTurn(
                    turn_index=step_count + 1,
                    model_payload=failure_payload,
                    error=error,
                )
                turns.append(turn)
                persisted = _persist_model_call(
                    state,
                    config,
                    ModelResponse(
                        response_id=f"model_error_{uuid4().hex}",
                        payload=failure_payload,
                        metadata=failure_metadata,
                    ),
                    messages=messages,
                    payload=failure_payload,
                )
                if not persisted:
                    turn.error = "model failure trace persistence failed"
                    state.add_error(
                        config.stage_name,
                        "persistence_error",
                        {"source": "model_error", "error_type": type(exc).__name__},
                    )
                    return _loop_result(
                        "persistence_error",
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                state.add_error(
                    config.stage_name,
                    "model_error",
                    {"message": error, "metadata": failure_metadata},
                )
                error_metadata = getattr(exc, "metadata", {})
                preserved = (error_metadata.get("preserved_final_output", {})
                             if isinstance(error_metadata, Mapping) else {})
                return _loop_result(
                    "model_error",
                    preserved if isinstance(preserved, dict) else {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            step_count += 1
            try:
                action = _ACTION_ADAPTER.validate_python(response.payload)
                if isinstance(action, FinalAction) and self.output_schema is not None:
                    self.output_schema.model_validate(action.output)
            except ValidationError as exc:
                validation_errors = _concise_validation_errors(exc)
                audit_payload = {
                    "action_type": _audit_action_type(response.payload),
                    "validation_errors": validation_errors,
                }
                turn = AgentTurn(
                    turn_index=step_count,
                    model_payload=audit_payload,
                    created_at=response.created_at,
                )
                turns.append(turn)
                if not _persist_model_call(
                    state,
                    config,
                    response,
                    messages=messages,
                    payload=audit_payload,
                ):
                    turn.error = "persistence_error"
                    return _loop_result(
                        "persistence_error",
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                if (
                    invalid_repair_count >= 1
                    or config.execution_mode is ExecutionMode.MODEL_ONCE
                ):
                    return _loop_result(
                        "invalid_model_action",
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                invalid_repair_count += 1
                messages.append(
                    {
                        "role": "system",
                        "content": {
                            "allowed_actions": list(ALLOWED_ACTION_NAMES),
                            "validation_errors": validation_errors,
                        },
                    }
                )
                continue

            raw_payload = action.model_dump(mode="json")
            decision_budget = None
            hidden_reasoning_detected = bool(
                isinstance(action, FinalAction)
                and _contains_hidden_reasoning(raw_payload["output"])
            )
            if isinstance(action, FinalAction):
                raw_payload["output"] = _remove_hidden_reasoning(raw_payload["output"])
            payload = _normalize_json_value(raw_payload)
            audit_payload = _audit_action_payload(action, payload)
            turn = AgentTurn(
                turn_index=step_count,
                model_payload=audit_payload,
                created_at=response.created_at,
            )
            turns.append(turn)
            if not _persist_model_call(
                state,
                config,
                response,
                messages=messages,
                payload=audit_payload,
            ):
                turn.error = "persistence_error"
                return _loop_result(
                    "persistence_error",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if time.monotonic() >= deadline:
                late_output = {}
                if research_scope and isinstance(action, FinalAction) and not hidden_reasoning_detected:
                    # A complete candidate is not a successful execution. Preserve it
                    # for local provenance validation, without starting another model.
                    late_output = payload["output"]
                return _loop_result(
                    "timeout",
                    late_output,
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if hidden_reasoning_detected and self.reject_hidden_reasoning:
                turn.error = "invalid_model_action"
                turn.observation = {
                    "output_error": {"category": "hidden_reasoning_rejected"}
                }
                return _loop_result(
                    "invalid_model_action",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, FinalAction):
                try:
                    issue = self.final_guard(payload["output"]) if self.final_guard else None
                except FinalGuardBlocked as exc:
                    turn.observation = {"final_gate": {"decision": "deny", "reason": str(exc), "retryable": False}}
                    return _loop_result("gate_blocked", {}, turns, config=config,
                                        model_call_count=model_call_count,
                                        nonfinal_action_count=nonfinal_action_count,
                                        delegation_evidence=delegation_evidence)
                if issue:
                    turn.observation = {"final_gate": {"decision": "deny", "reason": issue}}
                    gate_signature = json.dumps(
                        {
                            "issue": str(issue),
                            # A cached read or failed tool call does not add
                            # evidence. Such actions cannot reset this breaker.
                            "evidence_progress_count": evidence_progress_count,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if gate_signature == last_final_gate_signature:
                        repeated_final_gate_count += 1
                    else:
                        last_final_gate_signature = gate_signature
                        repeated_final_gate_count = 1
                    if repeated_final_gate_count >= 3:
                        turn.observation["final_gate"]["retryable"] = False
                        turn.observation["final_gate"]["repeat_count"] = repeated_final_gate_count
                        return _loop_result(
                            "gate_blocked",
                            {},
                            turns,
                            config=config,
                            model_call_count=model_call_count,
                            nonfinal_action_count=nonfinal_action_count,
                            delegation_evidence=delegation_evidence,
                        )
                    messages.append({"role": "assistant", "content": payload})
                    # A user-role observation preserves its position for providers
                    # that merge system messages into one initial system prompt.
                    messages.append({"role": "user", "content": {"final_gate": issue, "instruction": "Runtime rejected the previous candidate. Resolve this issue, calling an available tool if needed, then return a new action."}})
                    continue
                return _loop_result(
                    "success",
                    payload["output"],
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            nonfinal_action_count += 1

            if config.execution_mode is ExecutionMode.MODEL_ONCE:
                turn.error = "gate_blocked"
                turn.observation = {
                    "mode_error": {
                        "action_type": action.type,
                        "category": "gate_blocked",
                        "execution_mode": config.execution_mode.value,
                    }
                }
                return _loop_result(
                    "gate_blocked",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if (
                finalization_prompt_added
                and model_call_count >= budgets.max_model_calls
                and isinstance(action, (RequestApprovalAction, DelegateAction, ToolCallAction))
            ):
                action_name = str(getattr(action, "tool_name", "") or action.type)
                turn.error = "max_model_calls"
                turn.observation = {
                    "budget_boundary": {
                        "decision": "deny",
                        "action_type": action.type,
                        "action_name": action_name,
                        "reason": (
                            "The final model-call slot is reserved for a final answer; "
                            "the requested non-final action was not executed."
                        ),
                    }
                }
                return _loop_result(
                    "max_model_calls",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, RequestApprovalAction):
                turn.observation = {
                    "approval": {
                        "status": "awaiting_approval",
                        "tool_name": action.tool_name,
                    }
                }
                return _loop_result(
                    "awaiting_approval",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, DelegateAction):
                if (
                    reserve_calls
                    and model_call_count >= budgets.max_model_calls - reserve_calls
                ):
                    _append_delegate_error(
                        turn, messages, action.role, "gate_blocked",
                        reason="Research acquisition is closed; synthesize from observed evidence and disclose gaps.",
                    )
                    continue
                delegate_stop = self._run_delegate_action(
                    action=action,
                    turn=turn,
                    messages=messages,
                    state=state,
                    config=config,
                    delegation_evidence=delegation_evidence,
                )
                if delegate_stop is None:
                    delegate_denial_count = 0
                    evidence_progress_count += 1
                    if context_checkpoint:
                        context_checkpoint.observe_research_result(
                            turn.observation.get("subagent_result", {}).get("research_receipt"))
                    if not _has_replan_budget(
                        deadline=deadline,
                        step_count=step_count,
                        model_call_count=model_call_count,
                        budgets=budgets,
                    ):
                        return _loop_result(
                            "max_model_calls",
                            {},
                            turns,
                            config=config,
                            model_call_count=model_call_count,
                            nonfinal_action_count=nonfinal_action_count,
                            delegation_evidence=delegation_evidence,
                        )
                    continue
                if delegate_denial_count >= config.max_delegate_denials - 1:
                    return _loop_result(
                        delegate_stop,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                delegate_denial_count += 1
                if not _has_replan_budget(
                    deadline=deadline,
                    step_count=step_count,
                    model_call_count=model_call_count,
                    budgets=budgets,
                ):
                    return _loop_result(
                        delegate_stop,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                continue

            if isinstance(action, ToolCallAction):
                stop_reason = self._run_tool_action(
                    action=action,
                    response=response,
                    turn=turn,
                    messages=messages,
                    config=config,
                    budgets=budgets,
                    deadline=deadline,
                    step_count=step_count,
                    model_call_count=model_call_count,
                    tool_call_counts=tool_call_counts,
                    failed_call_counts=failed_call_counts,
                    plan_recovery_state=plan_recovery_state,
                    context_restore_counts=context_restore_counts,
                    context_checkpoint=context_checkpoint,
                )
                if stop_reason is not None:
                    return _loop_result(
                        stop_reason,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                tool_result = turn.observation.get("tool_result") if turn.observation else None
                if isinstance(tool_result, dict) and not tool_result.get("cached"):
                    evidence_progress_count += 1

    def _offload_old_observations(
        self, messages: list[dict[str, Any]], threshold: int, target: int | None = None,
    ) -> list[dict[str, Any]]:
        target = threshold * 3 // 4 if target is None else target
        before = estimate_tokens(messages)
        if before <= threshold:
            return []
        assert self.context_store is not None
        offloaded = []
        # Prefer older observations, but recent large results must not prevent
        # the next decision from fitting. Exact originals remain recoverable.
        tool_indices = [index for index, item in enumerate(messages)
                        if item.get("role") == "tool" and isinstance(item.get("content"), dict)
                        and "offloaded_observation" not in item["content"]]
        candidates = [*tool_indices[:-4], *sorted(tool_indices[-4:],
                      key=lambda index: estimate_tokens(messages[index]), reverse=True)]
        for index in candidates:
            if estimate_tokens(messages) <= target:
                break
            item = messages[index]
            content = item["content"]
            tool_name = str(item.get("tool_name") or "unknown")
            notice = "Original is unchanged on disk. context.open restores small records or returns an exact JSON-text page; use next_offset for more."
            marker_size = estimate_tokens({"offloaded_observation": {
                "context_id": "context_" + "0" * 24, "checksum": "0" * 64,
                "tool_name": tool_name, "restore_tool": "context.open", "notice": notice,
            }})
            if estimate_tokens(content) <= marker_size:
                continue
            if tool_name == "context.open" and isinstance(content.get("tool_result"), dict):
                restored = dict(content["tool_result"])
                # Runtime advisory metadata is not part of the exact stored
                # observation and must not affect integrity validation.
                restored.pop("_context_restore_advisory", None)
                restored.pop("_context_checkpoint", None)
                context_id = restored.get("context_id")
                original = self.context_store.validate_view(restored)
                stored = {
                    "context_id": original["context_id"],
                    "checksum": original["checksum"],
                    "tool_name": original["tool_name"],
                }
            else:
                stored = self.context_store.save(content, tool_name)
            item["content"] = {"offloaded_observation": {
                **stored,
                "restore_tool": "context.open",
                "notice": notice,
            }}
            offloaded.append(stored)
        if offloaded and self.on_context_offload:
            self.on_context_offload({
                "reason": "context_pressure", "threshold_tokens": threshold,
                "target_tokens": target,
                "before_tokens": before, "after_tokens": estimate_tokens(messages),
                "offloaded": offloaded,
            })
        return offloaded

    def _run_delegate_action(
        self,
        *,
        action: DelegateAction,
        turn: AgentTurn,
        messages: list[dict[str, Any]],
        state: PolicyImpactState,
        config: AgentLoopConfig,
        delegation_evidence: list[DelegationEvidence],
    ) -> StopReason | None:
        role_name = self.role_aliases.get(action.role, action.role)
        if (
            self.delegate_fn is None
            or self.skill_manifest is None
            or role_name not in self.skill_manifest.allowed_subagents
        ):
            category: StopReason = "tool_denied"
            _append_delegate_error(turn, messages, action.role, category)
            return category

        try:
            from policy_impact.harness.research_budget import CURRENT
            scope = CURRENT.get()
            if scope and not scope.acquiring():
                raise ValueError("research acquisition is closed; synthesize from existing results")
            version = action.role_version or "1.0.0"
            role = self.role_registry.get(role_name, version)
            task_id = f"task_{uuid4().hex}"
            research_task = action.research_task
            if version == "2.0.0":
                prepare = getattr(self.tool_gateway, "prepare_research_delegation", None)
                if prepare is None:
                    raise ValueError("this runtime does not expose versioned research delegation")
                research_task, input_artifact_refs = prepare(research_task, task_id)
            else:
                input_artifact_refs = action.input_artifact_refs or _default_delegate_refs(
                    config.context_manifest
                )
            brief = TaskBrief(
                task_id=task_id,
                parent_run_id=state.run_id,
                parent_span_id=_parent_span_id(state.run_id, config.stage_name),
                role=role_name,
                task=research_task.objective if research_task else action.task,
                role_version=version,
                research_task=research_task,
                input_artifact_refs=input_artifact_refs,
                output_schema_name=role.output_schema_name,
                max_steps=role.max_steps,
                max_model_calls=role.max_model_calls,
                timeout_seconds=role.timeout_seconds,
            )
            raw_result = self.delegate_fn(brief)
            result = (
                raw_result
                if isinstance(raw_result, SubagentResult)
                else SubagentResult.model_validate(raw_result)
            )
            evidence = _validate_delegation_integrity(
                state=state,
                brief=brief,
                role_version=role.version,
                allowed_tools=tuple(
                    tool
                    for tool in role.allowed_tools
                    if tool in self.skill_manifest.allowed_tools
                ),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            # Domain runtimes can reject delegation before a child exists when
            # the required evidence is unavailable. Preserve that gate reason
            # so the main Agent can replan instead of reporting a child failure.
            gated_category = getattr(exc, "delegate_category", "")
            if gated_category in {"gate_blocked", "tool_denied"}:
                _append_delegate_error(
                    turn,
                    messages,
                    action.role,
                    gated_category,
                    reason=str(exc),
                )
                return gated_category
            category = "delegate_failed"
            _append_delegate_error(turn, messages, action.role, category,
                                   reason=_public_tool_error_reason(exc) if action.role_version == "2.0.0" else "",
                                   details=getattr(exc, "delegate_details", None) if action.role_version == "2.0.0" else None)
            return category

        if evidence is not None:
            delegation_evidence.append(evidence)
        turn.observation = {
            "subagent_result": {
                "role": result.role,
                "task_id": result.task_id,
                "stop_reason": result.stop_reason,
                "context_manifest_id": result.context_manifest_id,
                "output": _normalize_json_value(result.output),
                "artifact_refs": list(result.artifact_refs),
                **({"research_receipt": result.research_receipt} if result.research_receipt else {}),
                **({"review_scope": result.review_scope} if result.review_scope else {}),
                **({"observed_article_refs": list(result.observed_article_refs)}
                   if result.observed_article_refs else {}),
            }
        }
        messages.append(
            {
                "role": "tool",
                "tool_name": f"subagent.{result.role}",
                "content": turn.observation,
            }
        )
        return None

    def _run_tool_action(
        self,
        *,
        action: ToolCallAction,
        response: ModelResponse,
        turn: AgentTurn,
        messages: list[dict[str, Any]],
        config: AgentLoopConfig,
        budgets: _LoopBudgets,
        deadline: float,
        step_count: int,
        model_call_count: int,
        tool_call_counts: dict[str, int],
        failed_call_counts: dict[str, int],
        plan_recovery_state: dict[str, Any],
        context_restore_counts: dict[str, int],
        context_checkpoint: _ContextProgressCheckpoint | None,
    ) -> StopReason | None:
        arguments = _normalize_json_value(action.arguments)
        messages.append(
            _assistant_tool_call_message(
                response_id=response.response_id,
                tool_name=action.tool_name,
                arguments=arguments,
            )
        )
        if action.tool_name == "context.open" and context_checkpoint and context_checkpoint.active:
            checkpoint_event = context_checkpoint.deny_restore()
            turn.observation = {"context_checkpoint": checkpoint_event}
            messages.append({"role": "tool", "tool_name": action.tool_name,
                             "content": turn.observation})
            if checkpoint_event["retryable"] is False:
                return "gate_blocked"
            return None
        reserve_calls = min(
            config.finalization_reserve_calls, budgets.max_model_calls // 5
        )
        from policy_impact.harness.research_budget import CURRENT

        scope = CURRENT.get()
        if (
            (reserve_calls and model_call_count >= budgets.max_model_calls - reserve_calls
             or scope is not None and not scope.acquiring())
            and action.tool_name in {
                "article.list", "article.read", "article.claims", "article.study",
                "web.search", "web.open", "source.discover", "source.fetch",
                "market.catalog", "market.snapshot", "market.metrics", "market.series",
            }
        ):
            _append_tool_error(
                turn, messages, action.tool_name, "tool_denied",
                reason="Research acquisition is closed; synthesize from observed evidence and disclose gaps.",
            )
            return None
        if (
            budgets.max_total_tool_calls is not None
            and sum(tool_call_counts.values()) >= budgets.max_total_tool_calls
        ):
            quota_key = "tool_total_quota"
            failed_call_counts[quota_key] += 1
            _append_tool_error(
                turn, messages, action.tool_name, "tool_denied",
                reason="research tool budget reached; finalize from observed evidence and disclose unchecked claims",
            )
            if failed_call_counts[quota_key] >= 2 or not _has_replan_budget(
                deadline=deadline, step_count=step_count,
                model_call_count=model_call_count, budgets=budgets,
            ):
                return "tool_denied"
            return None
        if tool_call_counts[action.tool_name] >= budgets.max_calls_per_tool:
            quota_key = f"tool_quota:{action.tool_name}"
            failed_call_counts[quota_key] += 1
            _append_tool_error(
                turn,
                messages,
                action.tool_name,
                "tool_denied",
                reason=(
                    "tool call limit reached; use prior observations or state the "
                    "missing evidence instead of retrying"
                ),
            )
            if failed_call_counts[quota_key] >= 2 or not _has_replan_budget(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            ):
                return "tool_denied"
            return None
        if time.monotonic() >= deadline:
            return "timeout"

        tool_call_counts[action.tool_name] += 1
        call_hash = _tool_call_hash(action.tool_name, arguments)
        call_index = len(getattr(self.tool_gateway, "calls", ()))
        try:
            try:
                result = self.tool_gateway.call(config.stage_name, action.tool_name, **arguments)
            finally:
                # Capture before error recovery can append a different invocation.
                record = next((
                    item for item in getattr(self.tool_gateway, "calls", ())[call_index:]
                    if item.stage_name == config.stage_name and item.tool_name == action.tool_name
                ), None)
                turn.tool_call_id = record.call_id if record else None
        except PermissionError as exc:
            _append_tool_error(
                turn, messages, action.tool_name, "tool_denied",
                reason=_public_tool_error_reason(exc),
            )
            return "tool_denied"
        except TimeoutError as exc:
            failed_call_counts[call_hash] += 1
            _append_tool_error(
                turn, messages, action.tool_name, "tool_timeout",
                reason=(
                    (_public_tool_error_reason(exc) or "tool timed out")
                    + "; try another source or answer from observed evidence with the gap disclosed"
                ),
            )
            if config.recover_tool_timeouts and _has_replan_budget(
                deadline=deadline, step_count=step_count,
                model_call_count=model_call_count, budgets=budgets,
            ):
                return None
            return "tool_timeout"
        except ValueError as exc:
            if _is_research_plan_required(exc):
                failed_call_counts[call_hash] += 1
                plan_recovery_state["gate_count"] = int(
                    plan_recovery_state.get("gate_count", 0)
                ) + 1
                _append_tool_error(
                    turn,
                    messages,
                    action.tool_name,
                    "research_plan_required",
                    reason=_public_tool_error_reason(exc),
                    details={
                        "required_tool": "research.plan",
                        "blocked_tool": action.tool_name,
                        "next_action": (
                            "Call research.plan with operation=create, then retry the selected tool."
                        ),
                        "repair_attempt": plan_recovery_state["gate_count"],
                    },
                )
                if (
                    plan_recovery_state["gate_count"] >= 2
                    and not plan_recovery_state.get("fallback_used")
                ):
                    plan_recovery_state["fallback_used"] = True
                    recovery = self._recover_research_plan(
                        config=config,
                        task_prompt=config.task_prompt,
                    )
                    turn.observation["plan_recovery"] = recovery
                    if recovery.get("ok"):
                        failed_call_counts.pop(call_hash, None)
                        messages.append({
                            "role": "user",
                            "content": {
                                "runtime_nudge": {
                                    "code": "research_plan_runtime_recovery",
                                    "instruction": (
                                        "Runtime created only a minimal requirements plan after repeated "
                                        "plan-gate violations. Continue deciding the research method yourself; "
                                        "do not treat this as a prescribed workflow."
                                    ),
                                    "plan": recovery.get("plan"),
                                },
                            },
                        })
                        return None
                if plan_recovery_state["gate_count"] < 2:
                    return None
                turn.observation["tool_error"]["retryable"] = False
                return "repeated_tool_failure"
            failed_call_counts[call_hash] += 1
            _append_tool_error(
                turn,
                messages,
                action.tool_name,
                "tool_failed",
                reason=_public_tool_error_reason(exc),
            )
            if failed_call_counts[call_hash] >= budgets.max_repeated_failures:
                return "repeated_tool_failure"
            if not _has_replan_budget(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            ):
                return "tool_failed"
            return None
        except Exception as exc:  # noqa: BLE001
            failed_call_counts[call_hash] += 1
            _append_tool_error(
                turn, messages, action.tool_name, "tool_failed",
                reason=_public_tool_error_reason(exc),
            )
            if failed_call_counts[call_hash] >= budgets.max_repeated_failures:
                return "repeated_tool_failure"
            if not _has_replan_budget(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            ):
                return "tool_failed"
            return None

        try:
            normalized_result = _normalize_json_value(result)
        except _InvalidJsonValue:
            _append_tool_error(
                turn, messages, action.tool_name, "invalid_tool_output",
                reason="tool returned a non-JSON value; use structured fields from prior observations",
            )
            return "invalid_tool_output"

        failed_call_counts.pop(call_hash, None)
        if context_checkpoint:
            context_checkpoint.observe_tool(action.tool_name, arguments, normalized_result)
        context_restore_advisory = None
        if action.tool_name == "context.open" and isinstance(normalized_result, dict):
            context_id = str(normalized_result.get("context_id") or "")
            if context_id:
                restore_key = (f"{context_id}@{normalized_result['offset']}:{normalized_result['returned_chars']}"
                               if normalized_result.get("schema_version") == "run.context_view.v1" else context_id)
                context_restore_counts[restore_key] += 1
                if context_checkpoint:
                    checkpoint_event = context_checkpoint.observe_restore(restore_key)
                    if checkpoint_event:
                        normalized_result = {
                            **normalized_result, "_context_checkpoint": checkpoint_event,
                        }
                restore_count = context_restore_counts[restore_key]
                if restore_count >= 2:
                    context_restore_advisory = {
                        "code": "repeated_context_restore",
                        "context_id": context_id,
                        "restore_count": restore_count,
                        "reason": "The same old tool observation was restored repeatedly in this run.",
                        "suggested_tool": "research.artifact",
                        "instruction": (
                            "Repeated restoration plus renewed context pressure activated a "
                            "progress checkpoint. Save an evidence-bound research_note or validated "
                            "v2 understanding/synthesis/work view, update "
                            "a plan goal with concrete progress, or answer with remaining limits "
                            "before using context.open again."
                            if context_checkpoint and context_checkpoint.active else
                            "You restored the same old observation repeatedly. If you have already "
                            "extracted article viewpoints, dates, conditions or evidence IDs, "
                            "consider saving a source-bound research_note or validated v2 "
                            "understanding/synthesis/work view before restoring again. Use "
                            "current-run EvidenceRefs; context.open remains available "
                            "and is not blocked."
                        ),
                    }
                    normalized_result = {
                        **normalized_result,
                        "_context_restore_advisory": context_restore_advisory,
                    }
                    if self.on_context_restore:
                        self.on_context_restore(dict(context_restore_advisory))
        turn.observation = {"tool_result": normalized_result}
        messages.append(
            {
                "role": "tool",
                "tool_name": action.tool_name,
                "content": turn.observation,
            }
        )
        runtime_nudge = normalized_result.get("_runtime_nudge") if isinstance(normalized_result, dict) else None
        if isinstance(runtime_nudge, dict):
            # Keep the guard visible in the conversation so the model can
            # change scope or finalize.  This is a progress signal, not a
            # fixed research workflow.
            messages.append(
                {
                    "role": "user",
                    "content": {
                        "runtime_nudge": runtime_nudge,
                        "instruction": str(runtime_nudge.get("instruction") or "").strip(),
                    },
                }
            )
        # A read-only plugin may establish that the requested research scope is
        # terminal (for example, an empty Runtime-resolved date window). Keep
        # the observation in the trace, then let the domain runtime produce a
        # truthful fallback instead of spending the remaining model budget on
        # equivalent retries.
        if isinstance(normalized_result, dict) and normalized_result.get("_runtime_terminal"):
            return "deterministic_complete"
        if time.monotonic() >= deadline:
            return "timeout"
        return None

    def _recover_research_plan(
        self,
        *,
        config: AgentLoopConfig,
        task_prompt: str,
    ) -> dict[str, Any]:
        """Create one generic plan after repeated violations of the plan gate.

        This is a reliability fallback, not a finance workflow. The main Agent
        remains responsible for choosing authors, sources, tools, reading depth
        and analysis method after the plan exists.
        """
        try:
            result = self.tool_gateway.call(
                config.stage_name,
                "research.plan",
                operation="create",
                goals=[
                    "Preserve and complete the user's requested outcome and scope",
                    "Acquire and assess the evidence needed for a truthful answer",
                ],
                scope={"deliverable": str(task_prompt or "").strip()[:500]},
            )
            normalized = _normalize_json_value(result)
            return {"ok": True, "tool": "research.plan", "plan": normalized}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "tool": "research.plan",
                "error_type": type(exc).__name__,
                "error": _public_tool_error_reason(exc),
            }


def _validate_delegation_integrity(
    *,
    state: PolicyImpactState,
    brief: TaskBrief,
    role_version: str,
    allowed_tools: tuple[str, ...],
    result: SubagentResult,
) -> DelegationEvidence | None:
    if (
        (result.stop_reason != "success" and role_version != "2.0.0")
        or result.task_id != brief.task_id
        or result.parent_run_id != state.run_id
        or result.parent_run_id != brief.parent_run_id
        or result.role != brief.role
        or result.role_version != role_version
        or result.parent_span_id != brief.parent_span_id
    ):
        raise ValueError("subagent result does not match delegated task")

    expected_manifest_fields = {
        "task_id": result.task_id,
        "parent_run_id": result.parent_run_id,
        "role": result.role,
        "role_version": result.role_version,
        "parent_span_id": result.parent_span_id,
        "span_id": result.span_id,
        "output_schema_name": brief.output_schema_name,
    }
    manifest_matches = False
    for record in state.context_manifests:
        if (
            not isinstance(record, Mapping)
            or record.get("record_id") != result.context_manifest_id
            or record.get("context_type") != "subagent_context_manifest"
            or record.get("source_id") != state.run_id
        ):
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, Mapping) or not all(
            metadata.get(key) == value for key, value in expected_manifest_fields.items()
        ):
            continue
        visible_content = metadata.get("visible_content")
        if not isinstance(visible_content, Mapping):
            continue
        manifest_payload = {
            "brief": visible_content.get("task_brief"),
            "role_version": result.role_version,
            "span_id": result.span_id,
            "allowed_tools": list(allowed_tools),
            "tool_descriptors": list(metadata.get("tool_descriptors") or []),
        }
        # Older callers may construct a child manifest before plugin prompt
        # metadata existed. Keep that contract readable while enforcing the
        # extra checksum fields whenever a real plugin manifest declares them.
        if "prompt_modules" in metadata or "prompt_checksum" in metadata:
            manifest_payload.update({
                "prompt_modules": list(metadata.get("prompt_modules") or []),
                "prompt_checksum": str(metadata.get("prompt_checksum") or ""),
            })
        canonical = json.dumps(
            manifest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        checksum = sha256(canonical.encode("utf-8")).hexdigest()
        if (
            record.get("checksum") == checksum
            and record.get("record_id") == f"ctx_subagent_{checksum[:16]}"
            and metadata.get("allowed_tools") == list(allowed_tools)
            and visible_content.get("task_brief") == brief.model_dump(mode="json")
            and visible_content.get("input_artifact_refs")
            == list(brief.input_artifact_refs)
        ):
            manifest_matches = True
            break
    if not manifest_matches:
        raise ValueError("subagent context manifest evidence is missing or inconsistent")

    trace_matches = any(
        isinstance(trace, Mapping)
        and trace.get("stage") == "subagent_delegate"
        and trace.get("status") == ("ok" if result.stop_reason == "success" else "failed")
        and trace.get("stop_reason") == result.stop_reason
        and trace.get("task_id") == result.task_id
        and trace.get("parent_run_id") == result.parent_run_id
        and trace.get("role") == result.role
        and trace.get("role_version") == result.role_version
        and trace.get("parent_span_id") == result.parent_span_id
        and trace.get("span_id") == result.span_id
        and trace.get("context_manifest_id") == result.context_manifest_id
        for trace in state.stage_trace
    )
    if not trace_matches:
        raise ValueError("successful subagent child trace is missing or inconsistent")

    if result.stop_reason != "success":
        if result.artifact_refs or result.verifier_gate_audit_refs:
            raise ValueError("failed child cannot advertise successful output artifacts or verifier proof")
        return None

    evidence = result.evidence()
    persisted_evidence = []
    for raw_evidence in state.delegation_evidence:
        try:
            persisted_evidence.append(DelegationEvidence.model_validate(raw_evidence))
        except (TypeError, ValueError):
            continue
    if evidence not in persisted_evidence:
        raise ValueError("delegation evidence is missing from current state")

    for artifact_ref in result.artifact_refs:
        if artifact_ref in brief.input_artifact_refs:
            continue
        artifact_value = state.artifacts.get(artifact_ref)
        if not isinstance(artifact_value, str) or not artifact_value.strip():
            raise ValueError("delegated artifact ref is not registered")
    return evidence


def _positive_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _default_delegate_refs(context_manifest: ContextManifestRecord) -> tuple[str, ...]:
    metadata = context_manifest.metadata
    refs = metadata.get("default_delegated_artifact_refs")
    if refs is None:
        ref = metadata.get("default_delegated_artifact_ref")
        refs = [ref] if ref else []
    if not isinstance(refs, (list, tuple)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        value = str(ref or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _normalize_legacy_loop_response(response: ModelResponse, output_key: str) -> ModelResponse:
    if output_key != "general_chat_agent_loop.v1":
        return response
    payload = response.payload
    if (
        isinstance(payload, Mapping)
        and isinstance(payload.get("output"), Mapping)
        and payload.get("type") in {"general_chat", "research_report"}
    ):
        return ModelResponse(
            response_id=response.response_id,
            payload={"type": "final", "output": dict(payload["output"])},
            metadata=dict(response.metadata),
            created_at=response.created_at,
        )
    return response


def _positive_number(field_name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be positive")
    return float(value)


def _resolve_budgets(
    config: AgentLoopConfig,
    *,
    max_steps: int | None,
    max_model_calls: int | None,
    timeout_seconds: float | None,
    max_calls_per_tool: int | None,
    max_total_tool_calls: int | None,
    max_repeated_failures: int | None,
) -> _LoopBudgets:
    return _LoopBudgets(
        max_steps=_positive_int(
            "max_steps", config.max_steps if max_steps is None else max_steps
        ),
        max_model_calls=_positive_int(
            "max_model_calls",
            config.max_model_calls if max_model_calls is None else max_model_calls,
        ),
        timeout_seconds=_positive_number(
            "timeout_seconds",
            config.timeout_seconds if timeout_seconds is None else timeout_seconds,
        ),
        max_calls_per_tool=_positive_int(
            "max_calls_per_tool",
            config.max_calls_per_tool
            if max_calls_per_tool is None
            else max_calls_per_tool,
        ),
        max_total_tool_calls=(
            None if max_total_tool_calls is None and config.max_total_tool_calls is None
            else _positive_int(
                "max_total_tool_calls",
                config.max_total_tool_calls if max_total_tool_calls is None else max_total_tool_calls,
            )
        ),
        max_repeated_failures=_positive_int(
            "max_repeated_failures",
            config.max_repeated_failures
            if max_repeated_failures is None
            else max_repeated_failures,
        ),
    )


def _budget_stop_reason(
    *,
    deadline: float,
    step_count: int,
    model_call_count: int,
    budgets: _LoopBudgets,
) -> StopReason | None:
    if time.monotonic() >= deadline:
        return "timeout"
    if step_count >= budgets.max_steps:
        return "max_steps"
    if model_call_count >= budgets.max_model_calls:
        return "max_model_calls"
    return None


def _has_replan_budget(
    *,
    deadline: float,
    step_count: int,
    model_call_count: int,
    budgets: _LoopBudgets,
) -> bool:
    return (
        time.monotonic() < deadline
        and step_count < budgets.max_steps
        and model_call_count < budgets.max_model_calls
    )


def _persist_model_call(
    state: PolicyImpactState,
    config: AgentLoopConfig,
    response: ModelResponse,
    *,
    messages: list[dict[str, Any]],
    payload: dict[str, Any],
) -> bool:
    try:
        safe_messages = _normalize_json_value(messages)
        safe_payload = _normalize_json_value(payload)
        safe_metadata = _sanitize_metadata(response.metadata)
        state.add_model_call(
            {
                "response_id": response.response_id,
                "stage_name": config.stage_name,
                "agent_role": config.agent_role,
                "schema_name": config.output_key,
                "messages": safe_messages,
                "payload": safe_payload,
                "metadata": safe_metadata,
                "created_at": response.created_at,
            }
        )
    except Exception:  # noqa: BLE001
        return False
    return True


def _concise_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors(include_url=False, include_context=False, include_input=False)[:8]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "action"
        message = str(item.get("msg") or "")
        if (location.endswith("tool_call.tool_name")
                and "is an action type, not a tool name" in message):
            errors.append(f"{location}: {message[:200]}")
        else:
            errors.append(f"{location}: {item.get('type', 'invalid')}")
    return errors or ["action: invalid"]


def _audit_action_type(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
        action_type = payload["type"].strip()
        if action_type in ALLOWED_ACTION_NAMES:
            return action_type
    return "invalid"


def _sanitize_metadata(value: Any) -> dict[str, Any]:
    sanitized = _sanitize_metadata_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold() in _HIDDEN_REASONING_FIELDS:
                continue
            safe_item = _sanitize_metadata_value(item)
            if safe_item is not _OMIT:
                sanitized[key] = safe_item
        return sanitized
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            safe_item = _sanitize_metadata_value(item)
            if safe_item is not _OMIT:
                items.append(safe_item)
        return items
    try:
        return _normalize_json_value(value)
    except _InvalidJsonValue:
        return _OMIT


def _remove_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _remove_hidden_reasoning(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not _is_hidden_reasoning_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_remove_hidden_reasoning(item) for item in value]
    return value


def _contains_hidden_reasoning(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (
                isinstance(key, str)
                and _is_hidden_reasoning_key(key)
            )
            or _contains_hidden_reasoning(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_hidden_reasoning(item) for item in value)
    return False


def _is_hidden_reasoning_key(key: str) -> bool:
    normalized = "".join(character for character in key.casefold() if character.isalnum())
    return (
        normalized in {"analysis", "thinking", "chainofthought"}
        or "reasoning" in normalized
    )


def _initial_message(config: AgentLoopConfig) -> dict[str, Any]:
    metadata = config.context_manifest.metadata
    visible = dict(metadata.get("visible_content", {}))
    descriptors = list(metadata.get("tool_descriptors") or visible.get("tool_descriptions") or [])
    if descriptors:
        visible.pop("tool_descriptions", None)
    content = {
        "agent_role": config.agent_role,
        "output_key": config.output_key,
        "manifest_id": metadata.get("manifest_id", config.context_manifest.record_id),
        "visible_keys": metadata.get("visible_keys", []),
        "visible_context": visible,
        "allowed_tool_descriptors": descriptors,
        "action_examples": {
            "tool_call": {"type": "tool_call", "tool_name": "<allowed name>", "arguments": {}},
            "final": _output_contract(config.output_key)["final_shape"],
            "delegate": {"type": "delegate", "role": "<allowed role>",
                         "task": "<bounded task>", "input_artifact_refs": ["<observed reference>"]},
            "request_approval": {"type": "request_approval", "tool_name": "<allowed name>",
                                 "arguments": {}, "reason": "<reason>"},
        },
        "output_contract": _output_contract(config.output_key),
        "instructions": [
            "Use only visible_context and allowed tool observations.",
            "Do not invent evidence or hidden state.",
            "Do not include reasoning, thinking, analysis, or chain-of-thought fields.",
            "Return exactly one action: final, tool_call, delegate, or request_approval.",
            "This is a text JSON protocol, not native tool calling. Do not emit DSML/XML, tool_calls, markdown fences or prose outside the JSON object.",
            "For tool_call, put all tool parameters inside arguments, matching its input schema; never treat final/delegate as tool names.",
            "Return type=final with a structured output object when the task is complete.",
        ],
    }
    if config.workspace_prompt:
        content["workspace_prompt"] = config.workspace_prompt
    if config.protocol_in_workspace_prompt:
        content.pop("action_examples")
        content.pop("output_contract")
        content["instructions"] = []
    return {
        "role": "system",
        "content": content,
    }


def _output_contract(output_key: str) -> dict[str, Any]:
    contracts = {
        "evidence_collection.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "evidence": [
                        {
                            "source_ref": "string",
                            "summary": "string",
                            "source_url": "string",
                        }
                    ]
                },
            }
        },
        "claim_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "claims": [
                        {
                            "claim_ref": "string",
                            "statement": "string",
                            "evidence_refs": ["input_artifact_ref"],
                            "claim_confidence_label": "medium",
                            "confidence_reason": "string",
                        }
                    ]
                },
            }
        },
        "counterexample_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "counterexamples": [
                        {
                            "claim_ref": "string",
                            "issue": "string",
                            "evidence_refs": ["input_artifact_ref"],
                        }
                    ],
                    "notes": ["string"],
                },
            }
        },
        "claim_verdict_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "verdicts": [
                        {
                            "claim_ref": "string",
                            "decision": "pass",
                            "evidence_refs": ["input_artifact_ref"],
                            "reason": "string",
                        }
                    ]
                },
            }
        },
        "published_answer.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "answer": "string",
                    "verified_claim_refs": ["input_artifact_ref"],
                },
            }
        },
        "general_chat_agent_loop.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "answer": "string",
                    "citations": ["optional citation refs"],
                    "notes": ["optional uncertainty notes"],
                },
            },
            "delegate_shape": {
                "type": "delegate",
                "role": "collector|analyst|skeptic|verifier",
                "task": "bounded task brief for the selected subagent",
                "input_artifact_refs": ["main_chat_context:<run>"],
            },
        },
    }
    return contracts.get(
        output_key,
        {"final_shape": {"type": "final", "output": "structured object"}},
    )


def _assistant_tool_call_message(
    response_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "response_id": response_id,
        "content": {
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": arguments,
        },
    }


def _append_tool_error(
    turn: AgentTurn,
    messages: list[dict[str, Any]],
    tool_name: str,
    category: str,
    reason: str = "",
    details: Mapping[str, Any] | None = None,
) -> None:
    turn.error = category
    error_payload = {
        "category": category,
        "tool_name": tool_name,
    }
    if reason:
        error_payload["reason"] = reason[:500]
    if details:
        error_payload.update(_normalize_json_value(dict(details)))
    turn.observation = {
        "tool_error": error_payload
    }
    messages.append(
        {
            "role": "tool",
            "tool_name": tool_name,
            "content": turn.observation,
        }
    )


def _public_tool_error_reason(exc: BaseException) -> str:
    """Expose a repairable tool error without leaking raw implementation details."""
    if isinstance(exc, TimeoutError):
        return "tool timed out; continue with available evidence or retry once with a narrower request"
    if isinstance(exc, RuntimeError):
        return "tool execution failed (RuntimeError); inspect the tool schema and prior observations"
    if isinstance(exc, ValueError):
        reason = str(exc or "").strip()
        if not reason:
            return "invalid tool arguments; inspect the tool schema and prior observations"
    else:
        reason = str(exc or "").strip()
    if not reason:
        return "tool execution failed; inspect the tool schema and prior observations"
    reason = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer|token)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        reason,
    )
    return reason[:500]


def _is_research_plan_required(exc: BaseException) -> bool:
    return "research_plan_required" in str(exc or "")


def _public_model_error_reason(exc: BaseException) -> str:
    """Return a bounded model failure description safe for trace/UI output."""
    reason = str(exc or "").strip()
    if not reason:
        reason = "model call failed without a public error message"
    reason = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer|token)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        reason,
    )
    return reason[:600]


def _model_failure_metadata(
    *,
    adapter: ModelAdapter,
    config: AgentLoopConfig,
    messages: list[dict[str, Any]],
    model_call_count: int,
    error: BaseException,
) -> dict[str, Any]:
    adapter_metadata = getattr(error, "metadata", {})
    if not isinstance(adapter_metadata, Mapping):
        adapter_metadata = {}
    max_attempts = getattr(adapter, "max_attempts", None)
    attempts = adapter_metadata.get("attempts")
    if attempts is None:
        attempts = getattr(error, "attempts", None)
    return {
        "adapter": type(adapter).__name__,
        "provider": str(getattr(adapter, "provider_name", "") or ""),
        "model_id": str(getattr(adapter, "model_id", "") or ""),
        "schema_name": config.output_key,
        "attempts": attempts,
        "max_attempts": max_attempts,
        "retry_errors": [
            _public_model_error_reason(RuntimeError(str(item)))
            for item in list(adapter_metadata.get("retry_errors") or [])[:8]
        ],
        "transport_attempts": list(adapter_metadata.get("transport_attempts") or []),
        "failed_public_responses": _sanitize_metadata(adapter_metadata).get("failed_public_responses", []),
        "failure_kind": adapter_metadata.get("failure_kind") or (
            "context_budget" if "context_budget_exceeded" in str(error) else "unknown"),
        "error_type": type(error).__name__,
        "error_message": _public_model_error_reason(error),
        "loop_phase": "model_call",
        "model_call_number": model_call_count,
        "context_summary": {
            "message_count": len(messages),
            "estimated_tokens": estimate_tokens(messages),
            "last_role": str(messages[-1].get("role") or "") if messages else "",
        },
    }


def _append_delegate_error(
    turn: AgentTurn,
    messages: list[dict[str, Any]],
    role: str,
    category: StopReason,
    reason: str = "",
    details: Mapping[str, Any] | None = None,
) -> None:
    turn.error = category
    error_payload = {
        "category": category,
        "role": role,
    }
    if reason:
        error_payload["reason"] = reason[:500]
    if details:
        error_payload["details"] = _normalize_json_value(details)
    turn.observation = {
        "delegate_error": error_payload
    }
    messages.append({"role": "system", "content": turn.observation})


def _audit_action_payload(action: AgentAction, payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, DelegateAction):
        return {
            "type": "delegate",
            "role": action.role,
            "input_artifact_ref_count": len(action.input_artifact_refs),
            **({"role_version": action.role_version,
                "research_task_checksum": sha256(action.research_task.model_dump_json().encode()).hexdigest(),
                "material_refs": list(action.research_task.material_refs)}
               if action.research_task else {}),
        }
    return payload


def _parent_span_id(run_id: str, stage_name: str) -> str:
    digest = sha256(f"{run_id}:{stage_name}".encode("utf-8")).hexdigest()[:16]
    return f"span_parent_{digest}"


def _loop_result(
    stop_reason: StopReason,
    final_output: dict[str, Any],
    turns: list[AgentTurn],
    *,
    config: AgentLoopConfig,
    model_call_count: int,
    nonfinal_action_count: int,
    delegation_evidence: list[DelegationEvidence],
) -> AgentLoopResult:
    if delegation_evidence:
        actual_mode = ExecutionMode.SUBAGENT_WORKFLOW
    elif model_call_count == 1 and nonfinal_action_count == 0:
        actual_mode = ExecutionMode.MODEL_ONCE
    elif model_call_count == 0 and config.execution_mode is ExecutionMode.MODEL_ONCE:
        actual_mode = ExecutionMode.MODEL_ONCE
    else:
        actual_mode = ExecutionMode.AGENT_LOOP
    return AgentLoopResult(
        stop_reason=stop_reason,
        final_output=final_output,
        turns=turns,
        actual_execution_mode=actual_mode,
        delegation_evidence=tuple(delegation_evidence),
    )


def _tool_call_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"arguments": arguments, "tool_name": tool_name},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise _InvalidJsonValue("non-finite number")
        return value
    if isinstance(value, PathLike):
        return str(value)
    if is_dataclass(value):
        return _normalize_json_value(asdict(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _InvalidJsonValue("mapping keys must be strings")
        return {
            key: _normalize_json_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize_json_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    raise _InvalidJsonValue(f"unsupported JSON value type: {type(value).__name__}")


__all__ = [
    "AgentLoop",
    "AgentLoopConfig",
    "AgentLoopResult",
    "AgentTurn",
    "StopReason",
]
