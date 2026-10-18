"""Role-isolated subagent execution with explicit caller capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from policy_impact.harness.agent_loop import (
    AgentLoop,
    AgentLoopConfig,
    _normalize_json_value,
)
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.harness.state import PolicyImpactState, utc_now_iso
from policy_impact.harness.tool_gateway import (
    ToolCallRecord,
    ToolGateway,
    _current_audit_log,
    summarize_tool_result,
)
from policy_impact.runtime.actions import StopReason
from policy_impact.runtime.subagent_roles import (
    SubagentRoleDefinition,
    SubagentRoleRegistry,
    build_default_subagent_role_registry,
)
from policy_impact.runtime.plugin_config import (
    read_plugin_prompt_modules,
    read_plugin_toolkit,
)
from policy_impact.runtime.task_artifacts import (
    InMemoryTaskArtifactResolver,
    TaskArtifactResolver,
)
from policy_impact.runtime.task_brief import SubagentResult, TaskBrief
from policy_impact.runtime.verifier_proof import VerifierGateProof, VerifierGateResolver
from policy_impact.skills.manifest import SkillManifest


@dataclass(frozen=True)
class SubagentDefinition:
    """Compatibility descriptor; it never grants role or tool capabilities."""

    role: str
    stage_name: str
    visible_fields: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    output_key: str
    max_turns: int = 4
    task_prompt: str = ""

    def __post_init__(self) -> None:
        for field_name in ("role", "stage_name", "output_key"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "visible_fields",
            tuple(str(value).strip() for value in self.visible_fields if str(value).strip()),
        )
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(str(value).strip() for value in self.allowed_tools if str(value).strip()),
        )
        if isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int) or self.max_turns <= 0:
            raise ValueError("max_turns must be a positive integer")
        object.__setattr__(self, "task_prompt", str(self.task_prompt or "").strip())


class SubagentRunner:
    def __init__(
        self,
        definitions: list[SubagentDefinition] | tuple[SubagentDefinition, ...] | None = None,
        model_adapter: ModelAdapter | None = None,
        tool_gateway: ToolGateway | None = None,
        *,
        skill_manifest: SkillManifest,
        role_registry: SubagentRoleRegistry | None = None,
        verifier_gate_resolver: VerifierGateResolver | None = None,
    ) -> None:
        if model_adapter is None:
            raise TypeError("model_adapter is required")
        if not isinstance(skill_manifest, SkillManifest):
            raise TypeError("skill_manifest must be a SkillManifest")
        self.model_adapter = model_adapter
        self.tool_gateway = tool_gateway or ToolGateway()
        self.skill_manifest = skill_manifest
        self.role_registry = role_registry or build_default_subagent_role_registry()
        self.verifier_gate_resolver = verifier_gate_resolver

        legacy_definitions = tuple(definitions or ())
        self.definitions = {definition.role: definition for definition in legacy_definitions}
        if len(self.definitions) != len(legacy_definitions):
            raise ValueError("duplicate legacy subagent role")
        for definition in legacy_definitions:
            # Legacy descriptors can customize the prompt only for a registered role.
            self.role_registry.get(definition.role, "1.0.0")

    def run(self, state: PolicyImpactState, role: str, **kwargs) -> SubagentResult:
        from policy_impact.harness.research_budget import CURRENT, execution_scope

        scope = CURRENT.get()
        if scope and scope.stage == "finance.main":
            brief = kwargs.get("task_brief")
            definition = self._definition_for(role, brief.role_version if brief else "1.0.0")
            with execution_scope(timeout_seconds=definition.timeout_seconds,
                                 reserve_seconds=min(scope.root.limits.child_final_seconds,
                                                     definition.timeout_seconds / 5),
                                 stage=f"{role}@{definition.version}"):
                return self._run(state, role, **kwargs)
        return self._run(state, role, **kwargs)

    def _run(
        self,
        state: PolicyImpactState,
        role: str,
        *,
        task: str | None = None,
        input_artifact_refs: tuple[str, ...] | list[str] = (),
        artifact_payloads: Mapping[str, Any] | None = None,
        artifact_resolver: TaskArtifactResolver | None = None,
        required_artifact_reads: tuple[str, ...] | list[str] = (),
        output_artifact_refs: tuple[str, ...] | list[str] = (),
        parent_span_id: str | None = None,
        task_id: str | None = None,
        final_guard: Callable[[dict[str, Any]], str | None] | None = None,
        task_brief: TaskBrief | None = None,
        on_model_input: Callable | None = None,
    ) -> SubagentResult:
        """Preload required delegated refs through the scoped tool before any model call.

        Required reads are audited, consume steps/time, and fail closed. They do
        not consume or increase the role's model-call budget. Other callers opt out.
        """
        normalized_role = str(role or "").strip()
        self._authorize_role(normalized_role)
        definition = self._definition_for(normalized_role, task_brief.role_version if task_brief else "1.0.0")
        legacy = self.definitions.get(normalized_role)
        task_text = str(
            task
            if task is not None
            else legacy.task_prompt
            if legacy is not None and legacy.task_prompt
            else f"Complete the isolated {normalized_role} task."
        ).strip()
        parent_span = str(
            parent_span_id or self._default_parent_span(state, normalized_role)
        ).strip()
        brief = task_brief or TaskBrief(
            task_id=task_id or f"task_{uuid4().hex}",
            parent_run_id=state.run_id,
            parent_span_id=parent_span,
            role=normalized_role,
            task=task_text,
            input_artifact_refs=tuple(input_artifact_refs),
            output_schema_name=definition.output_schema_name,
            max_steps=definition.max_steps,
            max_model_calls=definition.max_model_calls,
            timeout_seconds=definition.timeout_seconds,
        )
        if (brief.role != normalized_role or brief.parent_run_id != state.run_id
                or brief.output_schema_name != definition.output_schema_name
                or brief.max_steps > definition.max_steps
                or brief.max_model_calls > definition.max_model_calls
                or brief.timeout_seconds > definition.timeout_seconds):
            raise PermissionError("task brief differs from the registered execution grant")
        required_reads = tuple(required_artifact_reads)
        if (
            any(ref not in brief.input_artifact_refs for ref in required_reads)
            or len(required_reads) != len(set(required_reads))
        ):
            raise ValueError("required_artifact_reads must be unique delegated input refs")
        proofs = self._editor_proofs(definition, brief)
        task_artifacts = _resolve_task_artifacts(artifact_payloads, artifact_resolver)
        controlled_output_refs = _validate_controlled_output_refs(
            output_artifact_refs,
            brief=brief,
            resolver=task_artifacts,
        )

        span_id = f"span_{uuid4().hex}"
        allowed_tools = self._allowed_tools(definition)
        role_prompt = (
            read_plugin_prompt_modules(definition.prompt_modules)
            if definition.prompt_modules
            else ""
        )
        manifest = _build_subagent_context_manifest(
            brief=brief,
            definition=definition,
            span_id=span_id,
            allowed_tools=allowed_tools,
            prompt_checksum=_text_checksum(role_prompt),
            tool_descriptors=_tool_descriptors_for_skill(
                self.skill_manifest,
                allowed_tools=allowed_tools,
            ),
        )
        stage_name = f"subagent.{normalized_role}"
        scoped_tools = _RoleScopedToolGateway(
            normalized_role,
            allowed_tools,
            self.tool_gateway,
            brief=brief,
            artifact_resolver=task_artifacts,
        )
        deadline = time.monotonic() + brief.timeout_seconds
        from policy_impact.harness.research_budget import CURRENT

        scope = CURRENT.get()
        if scope:
            deadline = min(deadline, scope.deadline)
        observations, stop_reason = _preload_required_artifacts(
            state,
            scoped_tools,
            manifest=manifest,
            required_refs=required_reads,
            deadline=deadline,
        )
        if required_reads:
            # Keep the delegation identity stable; each observation is bound to a
            # real tool audit record and its own content checksum, not a model claim.
            manifest.metadata["required_artifact_reads"] = list(required_reads)
            manifest.metadata["visible_keys"].append("artifact_observations")
            manifest.metadata["visible_content"]["artifact_observations"] = observations
        state.add_context_manifest(manifest.to_dict())

        output: Mapping[str, Any] = {}
        remaining_seconds = deadline - time.monotonic()
        if stop_reason is None and remaining_seconds <= 0:
            stop_reason = "timeout"
        if stop_reason is None:
            task_prompt = brief.task
            if artifact_payloads:
                guidance = next(
                    (str(payload.get("review_instructions") or "")
                     for payload in artifact_payloads.values()
                     if isinstance(payload, Mapping) and payload.get("review_instructions")),
                    "",
                )
                if guidance:
                    task_prompt += (
                        "\n" + guidance
                        + '\nTo read an article, return exactly one JSON action: '
                          '{"type":"tool_call","tool_name":"article.read",'
                          '"arguments":{"article_id":"<ID from the artifact>"}}. '
                          'Do not claim a tool read unless its observation succeeded. '
                          'After reading, return the role final schema.'
                    )
            loop_result = AgentLoop(
                self.model_adapter,
                scoped_tools,
                reject_hidden_reasoning=True,
                output_schema=definition.output_schema,
                final_guard=final_guard,
                on_model_input=on_model_input,
            ).run(
                state,
                AgentLoopConfig(
                    stage_name=stage_name,
                    agent_role=normalized_role,
                    context_manifest=manifest,
                    output_key=definition.output_schema_name,
                    max_steps=brief.max_steps - len(required_reads),
                    max_model_calls=brief.max_model_calls,
                    timeout_seconds=remaining_seconds,
                    max_calls_per_tool=(
                        min(brief.max_steps - len(required_reads) - 2,
                            brief.max_model_calls - 2, 24)
                        if "article.read" in allowed_tools else 3
                    ),
                    max_total_tool_calls=(
                        10 if "article.read" in allowed_tools
                        and normalized_role in {"skeptic", "verifier"} else None
                    ),
                    task_prompt=task_prompt,
                    workspace_prompt=role_prompt,
                    finalization_reserve_calls=4 if brief.role_version == "2.0.0" else 0,
                ),
            )
            output = loop_result.final_output
            stop_reason = loop_result.stop_reason
        if stop_reason == "success" or (brief.role_version == "2.0.0" and output):
            try:
                if normalized_role == "collector":
                    _reject_collector_publishing_semantics(output)
                if brief.role_version == "1.0.0" and normalized_role in {"analyst", "skeptic", "verifier"}:
                    output = _normalize_observed_article_refs(
                        output, getattr(self.tool_gateway, "read_refs", ())
                    )
                # Do not add optional finance-only fields to legacy role
                # payloads.  New finance outputs can provide the namespaced
                # confidence fields explicitly; old scripted/adapted outputs
                # retain their original wire shape.
                output = definition.output_schema.model_validate(output).model_dump(
                    mode="python", exclude_none=True
                )
                _validate_role_output_refs(
                    definition.role,
                    output,
                    input_artifact_refs=brief.input_artifact_refs,
                    authorized_evidence_refs=(
                        _authorized_evidence_refs(artifact_payloads, brief.input_artifact_refs)
                        | scoped_tools.observed_evidence_refs
                    ),
                )
                if (
                    normalized_role in {"skeptic", "verifier"}
                    and artifact_payloads
                    and any(
                        isinstance(payload, Mapping) and payload.get("require_independent_read")
                        for payload in artifact_payloads.values()
                    )
                    and not scoped_tools.observed_evidence_refs
                ):
                    raise PermissionError("independent review requires a successful source tool read")
            except PermissionError:
                output = {}
                stop_reason = "gate_blocked"
            except ValidationError:
                output = {}
                stop_reason = "invalid_model_action"

        result = SubagentResult(
            task_id=brief.task_id,
            parent_run_id=brief.parent_run_id,
            role=normalized_role,
            role_version=definition.version,
            output=output,
            stop_reason=stop_reason,
            context_manifest_id=manifest.record_id,
            parent_span_id=brief.parent_span_id,
            span_id=span_id,
            artifact_refs=controlled_output_refs if stop_reason == "success" else (),
            verifier_gate_audit_refs=(
                tuple(proof.audit_ref for proof in proofs)
                if stop_reason == "success"
                else ()
            ),
        )
        state.add_stage_trace(
            {
                "stage": "subagent_delegate",
                "status": "ok" if result.stop_reason == "success" else "failed",
                "task_id": result.task_id,
                "parent_run_id": result.parent_run_id,
                "role": result.role,
                "role_version": result.role_version,
                "parent_span_id": result.parent_span_id,
                "span_id": result.span_id,
                "context_manifest_id": result.context_manifest_id,
                "stop_reason": result.stop_reason,
                "required_artifact_reads": list(required_reads),
                "artifact_read_audit_refs": [item["call_id"] for item in observations],
                "finished_at": utc_now_iso(),
            }
        )
        if result.stop_reason == "success":
            state.add_delegation_evidence(result.evidence().model_dump(mode="json"))
        return result

    def _definition_for(self, role: str, version: str = "1.0.0") -> SubagentRoleDefinition:
        return self.role_registry.get(role, version)

    def _authorize_role(self, role: str) -> None:
        if role not in self.skill_manifest.allowed_subagents:
            raise PermissionError(f"subagent role {role!r} is not allowed by caller skill")

    def _allowed_tools(self, definition: SubagentRoleDefinition) -> tuple[str, ...]:
        caller_tools = frozenset(self.skill_manifest.allowed_tools)
        return tuple(tool for tool in definition.allowed_tools if tool in caller_tools)

    def _editor_proofs(
        self,
        definition: SubagentRoleDefinition,
        brief: TaskBrief,
    ) -> tuple[VerifierGateProof, ...]:
        if definition.role != "editor":
            return ()
        if self.verifier_gate_resolver is None:
            raise PermissionError("editor input requires persisted verified claim proof")
        proofs = self.verifier_gate_resolver.resolve(
            brief.parent_run_id,
            brief.input_artifact_refs,
        )
        verified = {
            claim_ref
            for proof in proofs
            if proof.run_id == brief.parent_run_id
            for claim_ref in proof.verified_claim_refs
        }
        if not brief.input_artifact_refs or any(
            claim_ref not in verified for claim_ref in brief.input_artifact_refs
        ):
            raise PermissionError("editor input requires persisted verified claim proof")
        return proofs

    @staticmethod
    def _default_parent_span(state: PolicyImpactState, role: str) -> str:
        digest = hashlib.sha256(
            f"{state.run_id}:{role}:{len(state.delegation_evidence)}".encode("utf-8")
        ).hexdigest()[:16]
        return f"span_parent_{digest}"


@dataclass
class _RequiredArtifactReadRecord(ToolCallRecord):
    parent_run_id: str = ""
    task_id: str = ""
    span_id: str = ""
    context_manifest_id: str = ""
    artifact_ref: str = ""
    initiated_by: str = "harness_preload"
    result_checksum: str = ""


def _preload_required_artifacts(
    state: PolicyImpactState,
    gateway: _RoleScopedToolGateway,
    *,
    manifest: ContextManifestRecord,
    required_refs: tuple[str, ...],
    deadline: float,
) -> tuple[list[dict[str, Any]], StopReason | None]:
    observations: list[dict[str, Any]] = []
    # Every required read consumes one step and must leave room for the model.
    if len(required_refs) >= gateway.brief.max_steps:
        return observations, "max_steps"
    for artifact_ref in required_refs:
        if time.monotonic() >= deadline:
            return observations, "timeout"
        allowed = "artifact_read" in gateway.allowed_tools
        record = _RequiredArtifactReadRecord(
            call_id=f"tool_{uuid4().hex}",
            stage_name=f"subagent.{gateway.role}",
            tool_name="artifact_read",
            allowed=allowed,
            started_at=utc_now_iso(),
            argument_keys=("artifact_ref",),
            policy_decision="allow" if allowed else "deny",
            policy_reason="Caller/role tool intersection and delegated input refs",
            parent_run_id=state.run_id,
            task_id=gateway.brief.task_id,
            span_id=manifest.metadata["span_id"],
            context_manifest_id=manifest.record_id,
            artifact_ref=artifact_ref,
        )
        try:
            _current_audit_log().append(record)
            gateway.tool_gateway.calls.append(record)
        except Exception:  # noqa: BLE001
            return observations, "persistence_error"
        stop_reason: StopReason | None = None
        try:
            result = gateway.call(
                record.stage_name, "artifact_read", artifact_ref=artifact_ref
            )
            result = _normalize_json_value(result)
            canonical = json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
            record.result_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            record.result_summary = summarize_tool_result(result)
            record.status = "ok"
        except PermissionError:
            record.allowed = False
            record.policy_decision = "deny"
            record.status = "blocked"
            stop_reason = "tool_denied"
        except TimeoutError:
            record.status = "failed"
            stop_reason = "tool_timeout"
        except ValueError:
            record.status = "failed"
            stop_reason = "invalid_tool_output"
        except Exception:  # noqa: BLE001
            record.status = "failed"
            stop_reason = "tool_failed"
        record.error = stop_reason
        record.finished_at = utc_now_iso()
        try:
            state.tool_calls.append(record.to_dict())
        except Exception:  # noqa: BLE001
            return observations, "persistence_error"
        if stop_reason is not None:
            return observations, stop_reason
        observations.append({
            "tool_name": "artifact_read",
            "artifact_ref": artifact_ref,
            "call_id": record.call_id,
            "initiated_by": record.initiated_by,
            "result_checksum": record.result_checksum,
            "tool_result": result,
        })
        if time.monotonic() >= deadline:
            return observations, "timeout"
    return observations, None


class _RoleScopedToolGateway:
    def __init__(
        self,
        role: str,
        allowed_tools: tuple[str, ...],
        tool_gateway: ToolGateway,
        *,
        brief: TaskBrief,
        artifact_resolver: TaskArtifactResolver | None,
    ) -> None:
        self.role = role
        self.allowed_tools = frozenset(allowed_tools)
        self.tool_gateway = tool_gateway
        self.brief = brief
        self.artifact_resolver = artifact_resolver
        self.observed_evidence_refs: frozenset[str] = frozenset()

    @property
    def calls(self) -> list[ToolCallRecord]:
        return self.tool_gateway.calls

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        if tool_name not in self.allowed_tools:
            raise PermissionError(
                f"subagent role {self.role!r} is not allowed to use tool {tool_name!r}"
            )
        if tool_name == "artifact_read":
            return self._read_artifact(args, kwargs)
        result = self.tool_gateway.call(stage_name, tool_name, *args, **kwargs)
        self.observed_evidence_refs |= _evidence_ids_in_tool_result(result)
        return result

    def _read_artifact(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if args or set(kwargs) != {"artifact_ref"}:
            raise PermissionError("artifact_read requires exactly one artifact_ref")
        artifact_ref = kwargs.get("artifact_ref")
        if (
            not isinstance(artifact_ref, str)
            or artifact_ref not in self.brief.input_artifact_refs
            or self.artifact_resolver is None
            or not self.artifact_resolver.contains(artifact_ref)
        ):
            raise PermissionError("artifact_read ref is outside the delegated task")
        return self.artifact_resolver.read(artifact_ref)


def _evidence_ids_in_tool_result(value: Any) -> frozenset[str]:
    if isinstance(value, Mapping):
        refs = set()
        for key, item in value.items():
            if key == "evidence_id" and isinstance(item, str) and item.startswith("evidence_"):
                refs.add(item)
            elif key == "evidence_ids" and isinstance(item, (list, tuple)):
                refs.update(ref for ref in item if isinstance(ref, str) and ref.startswith("evidence_"))
            else:
                refs.update(_evidence_ids_in_tool_result(item))
        return frozenset(refs)
    if isinstance(value, (list, tuple)):
        return frozenset(ref for item in value for ref in _evidence_ids_in_tool_result(item))
    return frozenset()


def _resolve_task_artifacts(
    payloads: Mapping[str, Any] | None,
    resolver: TaskArtifactResolver | None,
) -> TaskArtifactResolver | None:
    if payloads is not None and resolver is not None:
        raise ValueError("provide artifact_payloads or artifact_resolver, not both")
    if resolver is not None:
        if not callable(getattr(resolver, "contains", None)) or not callable(
            getattr(resolver, "read", None)
        ):
            raise TypeError("artifact_resolver must implement contains/read")
        return resolver
    return InMemoryTaskArtifactResolver(payloads) if payloads is not None else None


def _validate_controlled_output_refs(
    refs: tuple[str, ...] | list[str],
    *,
    brief: TaskBrief,
    resolver: TaskArtifactResolver | None,
) -> tuple[str, ...]:
    normalized = tuple(str(ref).strip() for ref in refs)
    if any(not ref for ref in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError("output_artifact_refs must be unique nonblank strings")
    for artifact_ref in normalized:
        if artifact_ref in brief.input_artifact_refs:
            continue
        if resolver is not None and resolver.contains(artifact_ref):
            continue
        raise PermissionError(f"output artifact ref {artifact_ref!r} is not registered")
    return normalized


def _build_subagent_context_manifest(
    *,
    brief: TaskBrief,
    definition: SubagentRoleDefinition,
    span_id: str,
    allowed_tools: tuple[str, ...],
    prompt_checksum: str = "",
    tool_descriptors: list[dict[str, Any]] | None = None,
) -> ContextManifestRecord:
    visible_content = {
        "task_brief": brief.model_dump(mode="json"),
        "input_artifact_refs": list(brief.input_artifact_refs),
    }
    payload = {
        "brief": visible_content["task_brief"],
        "role_version": definition.version,
        "span_id": span_id,
        "prompt_modules": list(definition.prompt_modules),
        "prompt_checksum": prompt_checksum,
        "allowed_tools": list(allowed_tools),
        "tool_descriptors": tool_descriptors or [],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest_id = f"ctx_subagent_{checksum[:16]}"
    return ContextManifestRecord(
        record_id=manifest_id,
        context_type="subagent_context_manifest",
        source_id=brief.parent_run_id,
        checksum=checksum,
        metadata={
            "manifest_id": manifest_id,
            "stage_name": f"subagent.{brief.role}",
            "agent_role": brief.role,
            "role": brief.role,
            "role_version": definition.version,
            "prompt_template_id": definition.prompt_template_id,
            "prompt_template_version": definition.prompt_template_version,
            "prompt_modules": list(definition.prompt_modules),
            "prompt_checksum": prompt_checksum,
            "output_schema_name": definition.output_schema_name,
            "visible_keys": ["task_brief", "input_artifact_refs"],
            "visible_content": visible_content,
            "allowed_tools": list(allowed_tools),
            "tool_descriptors": tool_descriptors or [],
            "task_id": brief.task_id,
            "parent_run_id": brief.parent_run_id,
            "parent_span_id": brief.parent_span_id,
            "span_id": span_id,
        },
    )


def _text_checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tool_descriptors_for_skill(
    skill_manifest: SkillManifest,
    *,
    allowed_tools: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Expose declared plugin contracts without granting new capabilities."""

    descriptors: list[dict[str, Any]] = []
    allowed = set(allowed_tools)
    for toolkit_name in skill_manifest.toolkits:
        for descriptor in read_plugin_toolkit(toolkit_name):
            if descriptor.get("name") in allowed:
                descriptors.append(descriptor)
    return descriptors


def _reject_collector_publishing_semantics(output: Mapping[str, Any]) -> None:
    forbidden = {
        "conclusion",
        "decision",
        "finaldecision",
        "publicationdecision",
        "publishdecision",
        "recommendation",
        "recommendations",
        "verdict",
        "verdicts",
    }

    def visit(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = "".join(
                    character
                    for character in str(key).strip().casefold()
                    if character.isalnum()
                )
                if normalized in forbidden or normalized.endswith("decision"):
                    return True
                if visit(item):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(visit(item) for item in value)
        return False

    if visit(output):
        raise PermissionError("collector output contains publishing semantics")


def _validate_role_output_refs(
    role: str,
    output: Mapping[str, Any],
    *,
    input_artifact_refs: tuple[str, ...],
    authorized_evidence_refs: frozenset[str] = frozenset(),
) -> None:
    allowed = frozenset(input_artifact_refs) | authorized_evidence_refs
    referenced: list[str] = []
    if role in {"analyst", "skeptic"}:
        collection_key = "claims" if role == "analyst" else "counterexamples"
        for item in output.get(collection_key, ()):
            if isinstance(item, Mapping):
                referenced.extend(str(ref) for ref in item.get("evidence_refs", ()))
    elif role == "verifier":
        for item in output.get("verdicts", ()):
            if isinstance(item, Mapping):
                referenced.extend(str(ref) for ref in item.get("evidence_refs", ()))
    elif role == "editor":
        referenced.extend(str(ref) for ref in output.get("verified_claim_refs", ()))
    if any(ref not in allowed for ref in referenced):
        raise PermissionError("subagent output references evidence outside the delegated task")


def _normalize_observed_article_refs(
    output: Mapping[str, Any], read_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(output)
    for key in ("claims", "counterexamples", "verdicts"):
        items = output.get(key)
        if not isinstance(items, (list, tuple)):
            continue
        rewritten = []
        for item in items:
            if not isinstance(item, Mapping):
                rewritten.append(item)
                continue
            entry = dict(item)
            refs = []
            for raw_ref in item.get("evidence_refs", ()):
                ref = str(raw_ref).strip()
                match = re.fullmatch(r"(.+\.md):L(\d+)(?:[-–]L?(\d+))?", ref)
                if match:
                    path, start_text, end_text = match.groups()
                    start, end = int(start_text), int(end_text or start_text)
                    observed = next((
                        row for row in read_refs
                        if str(row.get("path") or "").replace("\\", "/").endswith(path.replace("\\", "/"))
                        and int(row.get("line_start") or 0) <= start <= end <= int(row.get("line_end") or 0)
                        and str(row.get("evidence_id") or "").startswith("evidence_")
                    ), None)
                    if observed:
                        ref = observed["evidence_id"]
                refs.append(ref)
            entry["evidence_refs"] = refs
            rewritten.append(entry)
        normalized[key] = rewritten
    return normalized


def _authorized_evidence_refs(
    payloads: Mapping[str, Any] | None,
    input_artifact_refs: tuple[str, ...],
) -> frozenset[str]:
    """Expose only evidence IDs embedded in the registered task artifact."""
    if payloads is None:
        return frozenset()
    refs: set[str] = set()
    for artifact_ref in input_artifact_refs:
        payload = payloads.get(artifact_ref)
        if not isinstance(payload, Mapping):
            continue
        bundle = payload.get("evidence_bundle")
        if isinstance(bundle, Mapping):
            refs.update(str(ref) for ref in bundle.get("evidence_ids") or () if str(ref).strip())
        refs.update(str(ref) for ref in payload.get("evidence_ids") or () if str(ref).strip())
    return frozenset(refs)


__all__ = ["SubagentDefinition", "SubagentResult", "SubagentRunner"]
