"""Model-driven financial domain steps for the opt-in Harness 2.0 path.

Public actions execute one bounded step then checkpoint/requeue the same Task.
Only research delegation creates a child Thread. SQL/Redis remain trusted
Runtime concerns, and the legacy finance AgentLoop is unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter, ValidationError

from policy_impact.runtime.actions import AgentAction, StateUpdate

from .contracts import TERMINAL, DispatchError, OutputContract, Spawn, Submission, Wait
from .execution import TOOL_CONTRACTS
from .finance_actions import correction_diagnostic, parse_root_action
from .finance_personal_sources import (
    SOURCE_SCHEMA,
    personal_source_read,
    remember_personal_source,
)
from .finance_public_sources import (
    public_source_read,
    remember_public_source,
)
from .finance_root import FinanceRootContext, FinanceRootExecutor, FinanceStep
from .finance_rules import rules_for
from .finance_tool_contracts import (
    ARTICLE_TOOLS,
    BUSINESS_TOOL_NAMES,
    MARKET_TOOLS,
    PERSONAL_TOOLS,
    PUBLIC_TOOLS,
    SCHEDULER_TOOL_NAMES,
    delegation_policy,
    describe,
    legacy_delegate_budget,
    normalize_agent_spawn,
    validate,
)
from .model_execution import public_validation_errors

ROOT_TOOLS = (*SCHEDULER_TOOL_NAMES, *(name for name in BUSINESS_TOOL_NAMES
                                      if name not in PUBLIC_TOOLS | MARKET_TOOLS | PERSONAL_TOOLS))
CACHEABLE_BUSINESS_TOOLS = frozenset({
    "article.list", "material.get", "personal.catalog", "context.open",
})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _action_key(action: Mapping[str, Any], *, revision: int, task_id: str | None = None) -> str:
    digest = hashlib.sha256(_json({"task_id": task_id, "revision": revision, "action": action}).encode()).hexdigest()[:24]
    return "finance-action-" + digest


def _business_action_cache_key(name: str, arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json({"tool_name": name, "arguments": arguments}).encode()).hexdigest()


def _as_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _material_payload(raw: Any) -> Any:
    """Materials may be UTF-8 text; actions and recovery state still require JSON."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _context_views(materials, normalized, actions):
    """Share exact payloads in this presentation; never mutate stored artifacts."""
    seen_materials = {}
    tool_materials = {}
    for material in materials:
        text = material.get("payload")
        if not isinstance(text, str):
            continue
        value = json.loads(text)
        previous = seen_materials.get(text)
        if previous is not None:
            material.pop("payload")
            material["payload_material_ref"] = previous
            continue
        ref = material["material_ref"]
        seen_materials[text] = ref
        if isinstance(value, dict) and value.get("schema_version") == "finance.h2.material.v1":
            value = value.get("payload")
        if (isinstance(value, dict)
                and value.get("schema_version") == "finance.h2.tool_result.v1"
                and isinstance(value.get("payload"), dict)):
            tool_materials.setdefault(_json(value["payload"]), ref)
    for receipt in actions:
        payload = receipt.get("payload")
        if receipt.get("status") != "succeeded" or not isinstance(payload, dict):
            continue
        ref = tool_materials.get(_json(payload))
        if ref is not None:
            receipt.pop("payload")
            receipt["payload_material_ref"] = ref
    combined = []
    for name, observations in (("normalized_observations", normalized), ("action_observations", actions)):
        for observation in observations:
            entry = dict(observation)
            copied = False
            for key in ("payload", "summary", "content", "description"):
                if key in entry:
                    entry.pop(key)
                    copied = True
            if copied:
                entry["payload_view"] = {"view": name, "observation_ref": observation["observation_ref"]}
            combined.append(entry)
    return combined


def _fit_context(system, user, capabilities):
    """Fit the final Root/child presentation using existing authorized expansion."""
    def messages():
        return [{"role": "system", "content": system}, {"role": "user", "content": _json(user)}]

    if not capabilities.context_accepts(messages()):
        for material in user.get("materials", []):
            if "payload" in material:
                material.pop("payload")
                material.update(payload_omitted=True, expansion_tool="material.get")
            if capabilities.context_accepts(messages()):
                break
        for observation in [*user.get("normalized_observations", []), *user.get("action_observations", [])]:
            if capabilities.context_accepts(messages()):
                break
            for key in ("payload", "summary", "content", "attempts", "description"):
                observation.pop(key, None)
            observation.update(payload_omitted=True, expansion_tool="material.get")
        for message in user.get("mailbox", []):
            if capabilities.context_accepts(messages()):
                break
            message.pop("content", None)
            message.update(payload_omitted=True, expansion_tool="material.get")
    if not capabilities.context_accepts(messages()):
        raise DispatchError("contract_error", "context_budget_exceeded")
    return messages()


class FinanceH2Domain:
    """One model decision per root Attempt, with durable child handoff."""

    def __init__(self, corpus, *, public_sources=None, market_sources=None, personal_sources=None):
        self.corpus = corpus
        self.public_sources = public_sources
        self.market_sources = market_sources
        self.personal_sources = personal_sources

    async def __call__(self, context: FinanceRootContext, capabilities) -> FinanceStep:
        state, observations = await self._consume_observations(context, capabilities)
        execution = dict(state.execution)
        execution["allowed_tools"] = list(context.brief.get("allowed_tools", []))
        execution["task_budget"] = {
            key: context.brief[key] for key in ("budget", "deadline_at") if key in context.brief
        }
        execution["task_identity"] = {
            key: context.brief[key] for key in (
                "run_id", "thread_id", "task_id", "attempt_id", "parent_task_id",
                "parent_thread_id", "role", "role_version", "depth",
            ) if key in context.brief
        }
        execution.pop("research_clock", None)
        if "research_clock" in context.brief:
            execution["research_clock"] = dict(context.brief["research_clock"])
        execution["next_action_key"] = _action_key({}, revision=state.revision, task_id=context.brief.get("task_id"))
        mailbox = {str(message.get("sequence")): message for message in execution.get("mailbox", [])}
        for message in context.messages:
            if message.get("kind") != "initial":
                mailbox[str(message.get("sequence"))] = {
                    key: message.get(key) for key in ("sequence", "kind", "payload_ref")
                }
        execution["mailbox"] = list(mailbox.values())
        state = state.model_copy(update={"execution": execution})
        prepared_ref = state.execution.get("prepared_action_ref")
        if isinstance(prepared_ref, str):
            try:
                prepared = json.loads(await capabilities.load_context(prepared_ref))
                action, _ = parse_root_action(prepared)
                if action.type not in {"tool_call", "delegate"}:
                    raise ValueError("prepared action is not executable")
            except DispatchError as exc:
                if exc.code in {"cancelled", "stale_lease"}:
                    raise
                raise DispatchError("checkpoint_invalid", "prepared finance action is unavailable") from exc
            except (ValueError, TypeError) as exc:
                raise DispatchError("checkpoint_invalid", "prepared finance action is invalid") from exc
            return await self._execute_action(context, state, action, capabilities)
        messages = await self._messages(context.query, state, observations, capabilities)
        response, repair_ref = await self._decide(messages, capabilities)
        if repair_ref:
            execution = dict(state.execution)
            execution["protocol_repairs"] = [*execution.get("protocol_repairs", []), repair_ref][-40:]
            state = state.model_copy(update={"execution": execution})
        try:
            action, validation_warnings = parse_root_action(response.payload)
        except (TypeError, ValueError) as exc:
            raise DispatchError("contract_error", "finance root returned an invalid action") from exc
        if validation_warnings:
            execution = dict(state.execution)
            execution["metadata_status"] = "incomplete_metadata"
            execution["state_update_warnings"] = [
                *execution.get("state_update_warnings", []), *validation_warnings,
            ][-40:]
            state = state.model_copy(update={"execution": execution})
            state_update_warnings = validation_warnings
        else:
            state, state_update_warnings = await self._apply_state_update(
                state, action.state_update, capabilities
            )
        if action.type == "final":
            anchor_warnings = (getattr(response, "metadata", None) or {}).get("research_anchor_warnings") or []
            if anchor_warnings:
                state_update_warnings = [*(state_update_warnings or []), *anchor_warnings]
                state = state.model_copy(update={"execution": {
                    **state.execution, "metadata_status": "incomplete_metadata",
                }})
            return await self._final(
                context, state, action.output, capabilities, state_update_warnings
            )
        if action.type == "request_approval":
            return await self._approval(context, state, action, capabilities)
        if action.type in {"tool_call", "delegate"}:
            action_ref = await capabilities.store_context(action.model_dump(mode="json"))
            execution = {**state.execution, "prepared_action_ref": action_ref}
            if action.type == "delegate":
                execution["prepared_child_request_ref"] = await capabilities.store_context({
                    "schema_version": "finance.h2.child_request.v1", "query": context.query,
                    "action": action.model_dump(mode="json"),
                    "input_refs": (list(action.research_task.material_refs)
                                   if action.research_task else list(action.input_artifact_refs)),
                })
            state = state.model_copy(update={"execution": execution, "revision": state.revision + 1})
            state_ref = await capabilities.store_context(FinanceRootExecutor._compact_state(state))
            await capabilities.checkpoint(FinanceRootExecutor._checkpoint(context, state, state_ref))
            return await self._execute_action(context, state, action, capabilities)
        raise DispatchError("contract_error", "unsupported finance root action")

    async def _execute_action(self, context, state, action, capabilities):
        if action.type == "delegate":
            try:
                return await self._delegate(context, state, action, capabilities)
            except DispatchError as exc:
                if exc.retryable or exc.code in {"cancelled", "stale_lease"}:
                    raise
                execution = dict(state.execution)
                execution.pop("prepared_action_ref", None)
                execution.pop("prepared_child_request_ref", None)
                ref = await capabilities.store_context({
                    "tool_name": "delegate", "status": "failed",
                    "error": {"code": exc.code, "message": "Delegation rejected; no new research result."},
                })
                execution["action_observation_refs"] = [*execution.get("action_observation_refs", []), ref]
                return FinanceStep(status="continuing", state=state.model_copy(update={
                    "execution": execution, "revision": state.revision + 1,
                }))
        return await self._scheduler_action(context, state, action, capabilities)

    async def _scheduler_action(self, context, state, action, capabilities):
        action_key = _action_key(action.model_dump(mode="json"), revision=state.revision,
                                 task_id=context.brief.get("task_id"))
        execution = dict(state.execution)
        execution.pop("prepared_action_ref", None)
        next_state = state.model_copy(update={"execution": execution, "revision": state.revision + 1})
        try:
            if action.tool_name == "agent.wait":
                request = Wait.model_validate(action.arguments)
                # The Worker, not the model, performs the durable suspension.
                await capabilities.validate_wait(request.model_dump())
                execution["pending_action"] = {"action_key": action_key, "action": action.model_dump(mode="json")}
                return FinanceStep(status="waiting", state=next_state.model_copy(update={
                    "execution": execution, "pending_steps": [action_key],
                }), wait=request)
            if action.tool_name in BUSINESS_TOOL_NAMES:
                return await self._business_action(context, state, action, capabilities)
            arguments = action.arguments
            if action.tool_name == "agent.spawn":
                rules = rules_for(capabilities)
                if rules.compact_spawn:
                    arguments = normalize_agent_spawn(
                        arguments,
                        dispatch_key=action_key,
                        task_budget=_as_object(state.execution.get("task_budget")),
                        rules=rules,
                    )
            payload = await capabilities.tool(action.tool_name, arguments)
            status = "succeeded"
        except (ValidationError, DispatchError) as exc:
            if isinstance(exc, DispatchError) and (exc.retryable or exc.code in {"cancelled", "stale_lease"}):
                raise
            status = "failed"
            payload = {"error": {
                "code": exc.code if isinstance(exc, DispatchError) else "invalid_arguments",
                "message": "Scheduler action rejected; inspect the authorized tool description and current state.",
            }}
        observation_ref = await capabilities.store_context({
            "schema_version": "finance.h2.action_observation.v1", "action_key": action_key,
            "tool_name": action.tool_name, "status": status, "payload": payload,
        })
        execution["action_observation_refs"] = [
            *execution.get("action_observation_refs", []), observation_ref,
        ]
        if status == "succeeded" and action.tool_name == "agent.spawn":
            task = _as_object(payload.get("data"))
            execution["scheduled_children"] = [
                *execution.get("scheduled_children", []),
                {key: task.get(key) for key in ("task_id", "thread_id", "attempt_id", "status")},
            ]
        if status == "succeeded" and action.tool_name == "result.get":
            execution["evidence_refs"] = sorted(set(execution.get("evidence_refs", [])) | {
                payload["result_ref"],
            })
            article_states = [dict(item) for item in state.article_states]
            await self._inherit_source_lineage(article_states, execution, payload, capabilities)
            next_state = next_state.model_copy(update={"article_states": article_states})
            if action.arguments.get("view", "summary") == "full":
                results = dict(execution.get("result_pages") or {})
                previous = _as_object(results.get(payload["result_ref"]))
                pages = list(previous.get("pages", [])) if previous.get("sha256") == payload["sha256"] else []
                pages.append({"offset": action.arguments.get("offset", 0),
                              "observation_ref": observation_ref})
                results[payload["result_ref"]] = {"sha256": payload["sha256"], "pages": pages}
                execution["result_pages"] = results
                next_state = next_state.model_copy(update={"execution": execution})
                workflow = _as_object(execution.get("review_workflow"))
                if (workflow.get("status") == "report_pending_read"
                        and workflow.get("pending_result_ref") == payload["result_ref"]):
                    full_content = await self._retrieved_result(
                        next_state, payload["result_ref"], capabilities,
                    )
                    if full_content is not None:
                        try:
                            parsed = json.loads(full_content)
                        except json.JSONDecodeError:
                            parsed = {"content": full_content}
                        material_ref = await capabilities.store_context({
                            "schema_version": "finance.h2.material.v1",
                            "source_task_id": workflow.get("pending_task_id"),
                            "result_ref": payload["result_ref"],
                            "payload": parsed,
                        })
                        execution["material_refs"] = list(dict.fromkeys([
                            *execution.get("material_refs", []), material_ref,
                        ]))
                        execution["review_workflow"] = self._record_review_report(
                            dict(workflow), parsed, result_ref=payload["result_ref"],
                            material_ref=material_ref,
                        )
        return FinanceStep(status="continuing", state=next_state.model_copy(update={
            "execution": execution, "completed_steps": [*state.completed_steps, action_key],
        }))

    @staticmethod
    def _record_review_report(workflow, artifact, *, result_ref, material_ref):
        review_artifact = _as_object(artifact)
        report = _as_object(review_artifact.get("report"))
        if (review_artifact.get("schema_version") == "finance.h2.review_report.v1"
                and review_artifact.get("validation_status") == "complete"
                and report.get("schema_version") == "finance.review_report.v1"
                and report.get("reviewed_draft_ref") == workflow.get("pending_draft_ref")
                and report.get("reviewed_revision") == workflow.get("pending_revision")):
            workflow.pop("pending_task_id", None)
            workflow.pop("pending_draft_ref", None)
            workflow.pop("pending_revision", None)
            workflow.pop("pending_result_ref", None)
            workflow.pop("failure_code", None)
            workflow.update(
                status="report_ready", report_ref=result_ref, report_material_ref=material_ref,
                reviewed_draft_ref=report["reviewed_draft_ref"],
                reviewed_revision=report["reviewed_revision"],
                finding_ids=list(dict.fromkeys([
                    *workflow.get("finding_ids", []),
                    *(item.get("finding_id") for item in report.get("findings", [])
                      if isinstance(item, dict) and isinstance(item.get("finding_id"), str)),
                ])),
                report_refs=list(dict.fromkeys([
                    *workflow.get("report_refs", []), result_ref,
                ])),
            )
        else:
            workflow.pop("pending_task_id", None)
            workflow.pop("pending_draft_ref", None)
            workflow.pop("pending_revision", None)
            workflow.pop("pending_result_ref", None)
            workflow.update(status="report_invalid", failure_code="invalid_report_binding")
        return workflow

    @staticmethod
    async def _retrieved_result(state, result_ref, capabilities):
        result = _as_object(_as_object(state.execution.get("result_pages")).get(result_ref))
        pages = []
        for item in result.get("pages", []):
            observation = json.loads(await capabilities.load_context(item["observation_ref"]))
            payload = _as_object(observation.get("payload"))
            if payload.get("result_ref") != result_ref or payload.get("sha256") != result.get("sha256"):
                return None
            content = payload.get("content")
            if not isinstance(content, str):
                return None
            pages.append((item["offset"], content, payload.get("has_more")))
        text = ""
        complete = False
        for start, content, has_more in sorted(pages, key=lambda page: page[0]):
            if start > len(text):
                return None
            end = start + len(content)
            overlap = min(len(content), len(text) - start)
            if text[start:start + overlap] != content[:overlap]:
                return None
            if end > len(text):
                text += content[overlap:]
            if has_more is False and end == len(text):
                complete = True
        return text if complete else None

    async def _business_action(self, context, state, action, capabilities):
        name = action.tool_name
        action_key = _action_key(action.model_dump(mode="json"), revision=state.revision,
                                 task_id=context.brief.get("task_id"))
        if name not in context.brief.get("allowed_tools", []):
            raise DispatchError("forbidden")
        await capabilities.record_tool(name, action_key, "started")
        cache_key = None
        if name in CACHEABLE_BUSINESS_TOOLS:
            try:
                normalized = validate(name, action.arguments)
            except (DispatchError, TypeError, ValidationError, ValueError):
                normalized = None
            if normalized is not None:
                cache_key = _business_action_cache_key(name, normalized)
                cached_ref = _as_object(state.execution.get("business_action_cache")).get(cache_key)
                if isinstance(cached_ref, str):
                    try:
                        cached_raw = await capabilities.load_context(cached_ref)
                        cached = _as_object(_material_payload(cached_raw))
                        cached_payload = cached.get("payload")
                    except (DispatchError, TypeError, ValueError, json.JSONDecodeError):
                        cached = {}
                        cached_payload = None
                    if cached.get("schema_version") == "finance.h2.tool_result.v1" and isinstance(
                        cached_payload, Mapping,
                    ):
                        await capabilities.record_tool(name, action_key, "succeeded", cached_ref)
                        return await self._finish_business_action(
                            context, state, action, capabilities, action_key,
                            dict(cached_payload), cached_ref, failed=False,
                            cache_key=cache_key, cache_hit=True,
                        )
        failed = False
        try:
            arguments = validate(name, action.arguments)
            if name == "tool.describe":
                target = arguments["name"]
                rules = rules_for(capabilities)
                if target == "agent.spawn" and rules.compact_spawn:
                    if target not in context.brief["allowed_tools"]:
                        raise DispatchError("forbidden")
                    payload = {"tool": name, "description": describe(
                        target, state.execution.get("task_budget"), rules=rules,
                    )}
                elif target in TOOL_CONTRACTS:
                    payload = {"tool": name, "description": await capabilities.describe(target)}
                else:
                    if target != "agent_action.v1" and target not in context.brief["allowed_tools"]:
                        raise DispatchError("forbidden")
                    payload = {"tool": name, "description": describe(
                        target, state.execution.get("task_budget"), rules=rules_for(capabilities),
                    )}
            elif name == "material.get":
                text = await capabilities.load_context(arguments["material_ref"])
                if isinstance(text, bytes):
                    text = text.decode("utf-8")
                offset = arguments["offset"]
                if offset > len(text):
                    raise ValueError("offset exceeds material length")
                end = min(len(text), offset + arguments["limit"])
                payload = {"tool": name, "material_ref": arguments["material_ref"],
                           "content": text[offset:end], "has_more": end < len(text),
                           "next_offset": end if end < len(text) else None}
            elif name in PUBLIC_TOOLS:
                if self.public_sources is None:
                    raise DispatchError("forbidden", "public sources are not bound")
                payload = await asyncio.to_thread(self.public_sources.execute, name, arguments)
                failed = payload.get("ok") is False
            elif name in MARKET_TOOLS:
                if self.market_sources is None:
                    raise DispatchError("forbidden", "market sources are not bound")
                payload = await self.market_sources.execute(name, arguments, capabilities)
                failed = payload.get("ok") is False
            elif name in PERSONAL_TOOLS:
                if self.personal_sources is None:
                    raise DispatchError("forbidden", "personal sources are not bound")
                payload = await self.personal_sources.execute(
                    name, arguments, capabilities,
                    source_refs=[*state.execution.get("personal_context_refs", []),
                                 *(context.brief.get("input_refs", [])
                                   if context.brief.get("depth", 0) > 0 else [])],
                    allow_capture=context.brief.get("depth", 0) == 0,
                )
                failed = payload.get("ok") is False
            else:
                scope = None
                if context.brief.get("depth", 0) > 0:
                    scope = await self._article_scope(state, capabilities)
                payload = await asyncio.to_thread(
                    FinanceH2ChildExecutor(self.corpus)._tool,
                    {"tool_name": name, "arguments": arguments}, allowed_article_ids=scope,
                )
        except (ValueError, DispatchError) as exc:
            if isinstance(exc, DispatchError) and (exc.retryable or exc.code in {"cancelled", "stale_lease"}):
                raise
            failed = True
            issues = ([{"path": list(item["loc"]), "type": item["type"]}
                       for item in exc.errors(include_input=False, include_url=False)[:8]]
                      if isinstance(exc, ValidationError) else [])
            payload = {"tool": name, "ok": False, "error": {
                "code": exc.code if isinstance(exc, DispatchError) else "invalid_arguments",
                "message": "No successful source read. Inspect the exact tool contract and granted materials.",
                "describe_name": name,
                "issues": issues,
            }}
        ref = await capabilities.store_context({"schema_version": "finance.h2.tool_result.v1", "payload": payload})
        await capabilities.record_tool(name, action_key, "failed" if failed else "succeeded", ref)
        return await self._finish_business_action(
            context, state, action, capabilities, action_key, payload, ref,
            failed=failed, cache_key=cache_key,
        )

    async def _finish_business_action(
        self, context, state, action, capabilities, action_key, payload, ref,
        *, failed, cache_key=None, cache_hit=False,
    ):
        observation = {
            "schema_version": "finance.h2.action_observation.v1", "action_key": action_key,
            "tool_name": action.tool_name, "status": "failed" if failed else "succeeded",
            "payload": payload,
        }
        if cache_hit:
            observation.update(cache_hit=True, cache_source_ref=ref)
        observation_ref = await capabilities.store_context(observation)
        # Reuse the same source/index binding as a real child result, without creating a Thread.
        observed = {"tasks": [{"task_id": context.brief.get("task_id"),
            "status": "failed" if failed else "succeeded", "result": {
                "full_content": _json({"schema_version": "finance.h2.tool_result.v1", "payload": payload}),
                "envelope": {"evidence_refs": [{"evidence_ref": ref}] if not failed and (
                    action.tool_name == "article.read" or public_source_read(payload, context.brief.get("task_id"))
                    or personal_source_read(payload, context.brief.get("task_id"))
                ) else []},
            }}]}
        state, _ = await self._consume_observations(
            FinanceRootContext(context.query, state, context.brief, context.checkpoint,
                               context.messages, [observed]), capabilities,
        )
        execution = dict(state.execution)
        execution["action_observation_refs"] = [
            *execution.get("action_observation_refs", []), observation_ref,
        ]
        if not failed and cache_key:
            action_cache = dict(execution.get("business_action_cache") or {})
            action_cache[cache_key] = ref
            execution["business_action_cache"] = action_cache
        execution.pop("prepared_action_ref", None)
        if failed and action.tool_name == "article.read":
            article_states = [dict(item) for item in state.article_states]
            index = self._article_index(article_states).get(action.arguments.get("article_id"))
            if index is not None and article_states[index].get("read_status") != "complete":
                article_states[index]["read_status"] = "failed"
                state = state.model_copy(update={"article_states": article_states})
        return FinanceStep(status="continuing", state=state.model_copy(update={
            "execution": execution, "completed_steps": [*state.completed_steps, action_key],
            "revision": state.revision + 1,
        }))

    @staticmethod
    async def _article_scope(state, capabilities):
        ids = set()
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get("article_id"), str):
                    ids.add(value["article_id"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        for ref in state.execution.get("material_refs", []):
            collect(_material_payload(await capabilities.load_context(ref)))
        return ids

    @staticmethod
    async def _decide(messages, capabilities, *, final_output_model=None, final_repair_factory=None):
        anchor_repair = None
        original_response = None

        async def decide_once(decision_messages, *, correcting=False):
            nonlocal anchor_repair, original_response
            response = await capabilities.complete(decision_messages, "agent_action.v1")
            if correcting and anchor_repair is not None:
                return anchor_repair.assemble(response)
            if final_output_model is not None and response.payload.get("type") == "final":
                try:
                    final_output_model.model_validate(response.payload.get("output"), strict=True)
                except ValidationError as exc:
                    if not correcting and final_repair_factory is not None:
                        anchor_repair = final_repair_factory(response.payload)
                        original_response = response
                    raise DispatchError("contract_error", "invalid research final output", details={
                        "call_id": response.metadata.get("call_id"),
                        "output_ref": response.metadata.get("output_ref"),
                        "validation_stage": "final.output",
                        "validation_errors": [
                            {key: error[key] for key in ("loc", "type", "msg")}
                            for error in exc.errors(include_input=False, include_context=False)
                        ],
                    }) from exc
            return response

        async def diagnose(failure):
            failed_text = await capabilities.load_context(failure.details["output_ref"])
            if isinstance(failed_text, bytes):
                failed_text = failed_text.decode("utf-8")
            return correction_diagnostic(failed_text, {
                "validation_stage": failure.details.get("validation_stage", "action"),
                "validation_errors": public_validation_errors(failure.details.get("validation_errors")),
            })

        try:
            return await decide_once(messages), None
        except DispatchError as exc:
            original_call_id = exc.details.get("call_id")
            output_ref = exc.details.get("output_ref")
            if exc.code != "contract_error" or not isinstance(output_ref, str):
                raise
            validation_diagnostic = await diagnose(exc)

        rules = rules_for(capabilities)
        limit = rules.action_repair_limit
        frozen_candidate_ref = None
        if anchor_repair is not None:
            frozen_candidate_ref = await capabilities.store_context({
                "schema_version": "finance.h2.frozen_research_candidate.v1",
                "action": anchor_repair.action, "body_sha256": anchor_repair.body_sha256,
                "failed_output_ref": output_ref,
            })

        request_refs, outcome_refs = [], []
        transport_retry_ref = None
        correction_count = 0
        response = None

        async def summarize(status, *, assembly_ref=None, failure=None):
            metadata = response.metadata if response is not None else (failure.details if failure else {})
            return await capabilities.store_context({
                "schema_version": "finance.h2.action_repair.v1",
                "request_ref": request_refs[0], "request_refs": request_refs, "outcome_refs": outcome_refs,
                "limit": limit, "correction_count": correction_count, "status": status,
                "correction_call_id": metadata.get("call_id") if correction_count else None,
                "corrected_output_ref": metadata.get("output_ref") if correction_count else None,
                "correction_attempted": bool(correction_count),
                **({"error_code": failure.code} if failure else {}),
                **({"assembly_ref": assembly_ref} if assembly_ref else {}),
                **({"transport_retry_ref": transport_retry_ref} if transport_retry_ref else {}),
            })

        for correction_number in range(1, limit + 1):
            feedback = {
                "instruction": rules.text("action-repair"),
                "action_json_schema": TypeAdapter(AgentAction).json_schema(),
                **validation_diagnostic,
                "correction_attempt": correction_number, "correction_limit": limit,
                "corrections_remaining_after_this": limit - correction_number,
            }
            if final_output_model is not None:
                feedback.update({
                    "final_output_json_schema": final_output_model.model_json_schema(),
                    "final_output_schema_scope": "final.output",
                })
                feedback["instruction"] += " " + rules.text("research-repair")
            if anchor_repair is not None:
                feedback.update(anchor_repair.feedback())
            # Each correction replaces the previous diagnostic, not the trusted task context.
            correction_messages = [*messages, {"role": "user", "content": _json(feedback)}]
            repair_ref = await capabilities.store_context({
                "schema_version": "finance.h2.action_repair.v1",
                "original_call_id": original_call_id, "failed_output_ref": output_ref,
                "limit": limit, "correction_attempt": correction_number, "transport_retry_limit": 1,
                **validation_diagnostic, "input_policy": feedback["input_policy"],
                **({"frozen_candidate_ref": frozen_candidate_ref} if frozen_candidate_ref else {}),
            })
            request_refs.append(repair_ref)
            if anchor_repair is not None and not capabilities.context_accepts(correction_messages):
                response = anchor_repair.assemble(original_response, fallback_reason="context_budget_exceeded")
                break
            correction_count = correction_number
            while True:
                try:
                    response = await decide_once(correction_messages, correcting=True)
                    outcome_refs.append(await capabilities.store_context({
                        "schema_version": "finance.h2.action_repair_outcome.v1", "request_ref": repair_ref,
                        "correction_attempt": correction_number, "status": "corrected",
                        "call_id": response.metadata.get("call_id"), "output_ref": response.metadata.get("output_ref"),
                    }))
                    break
                except DispatchError as exc:
                    outcome_refs.append(await capabilities.store_context({
                        "schema_version": "finance.h2.action_repair_outcome.v1", "request_ref": repair_ref,
                        "correction_attempt": correction_number, "status": "failed", "error_code": exc.code,
                        "call_id": exc.details.get("call_id"), "output_ref": exc.details.get("output_ref"),
                    }))
                    if exc.code == "transport_error" and transport_retry_ref is None:
                        transport_retry_ref = await capabilities.store_context({
                            "schema_version": "finance.h2.action_repair_transport.v1",
                            "request_ref": repair_ref,
                            "failed_call_id": exc.details.get("call_id"),
                            "failed_output_ref": exc.details.get("output_ref"),
                            "error_code": exc.code,
                            "retry_number": 1,
                        })
                        continue
                    if exc.code == "contract_error":
                        latest_ref = exc.details.get("output_ref")
                        if correction_number < limit and isinstance(latest_ref, str):
                            output_ref = latest_ref
                            validation_diagnostic = await diagnose(exc)
                            break
                        if anchor_repair is not None:
                            response = anchor_repair.assemble(original_response, fallback_reason="invalid_patch")
                            response.metadata.update({key: exc.details.get(key) for key in ("call_id", "output_ref")})
                            break
                    # Terminal recovery cannot start a new Attempt and reset this decision's limit.
                    failed_ref = await summarize("failed", failure=exc)
                    raise DispatchError(exc.code, str(exc), details={
                        **exc.details, "action_repair_ref": failed_ref,
                    }) from exc
            if response is not None:
                break
        assembly_ref = None
        if anchor_repair is not None:
            assembly_ref = await capabilities.store_context({
                "schema_version": "finance.h2.anchor_repair_assembly.v1",
                "frozen_candidate_ref": frozen_candidate_ref,
                "action": response.payload, **response.metadata["research_anchor_repair"],
            })
        status = "partial" if assembly_ref and response.metadata["research_anchor_repair"]["dropped_indices"] else "corrected"
        linked_ref = await summarize(status, assembly_ref=assembly_ref)
        return response, linked_ref

    @staticmethod
    def _article_index(article_states: list[dict[str, Any]]) -> dict[str, int]:
        return {
            str(item["article_id"]): index
            for index, item in enumerate(article_states)
            if isinstance(item, Mapping) and isinstance(item.get("article_id"), str)
        }

    @staticmethod
    def _merge_read_status(previous: str, current: str) -> str:
        if previous == "complete" or current == "complete":
            return "complete"
        if current == "failed" and previous in {"unread", "partial"}:
            return "failed"
        if previous == "failed" and current == "unread":
            return previous
        if current == "partial" or previous == "partial":
            return "partial"
        return current

    @staticmethod
    def _merge_read_ranges(current: Mapping[str, Any], start: Any, end: Any) -> list[list[int]]:
        ranges = []
        for item in current.get("read_ranges") or []:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and type(item[0]) is int
                and type(item[1]) is int
                and item[0] >= 1
                and item[1] >= item[0]
            ):
                ranges.append([item[0], item[1]])
        if type(start) is int and type(end) is int and start >= 1 and end >= start:
            ranges.append([start, end])
        ranges.sort()
        merged: list[list[int]] = []
        for left, right in ranges:
            if merged and left <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        return merged

    @staticmethod
    def _read_is_complete(payload: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
        start = payload.get("start_line")
        end = payload.get("end_line")
        line_count = payload.get("line_count")
        if type(start) is int and type(end) is int and type(line_count) is int:
            ranges = FinanceH2Domain._merge_read_ranges(current, start, end)
            return bool(
                line_count >= 0
                and ranges
                and ranges[0][0] == 1
                and ranges[0][1] >= line_count
            )
        # Older recorded tool envelopes did not expose line ranges. Preserve
        # their contract, while an explicit later start remains partial.
        return payload.get("has_more") is False and start in (None, 1)

    @staticmethod
    def _evidence_refs_in(value: Any) -> set[str]:
        refs: set[str] = set()
        if isinstance(value, Mapping):
            for key, child in value.items():
                name = str(key)
                if name == "evidence_ref" and isinstance(child, str):
                    refs.add(child)
                elif name == "evidence_refs" and isinstance(child, list):
                    refs.update(item for item in child if isinstance(item, str))
                refs.update(FinanceH2Domain._evidence_refs_in(child))
        elif isinstance(value, list):
            for child in value:
                refs.update(FinanceH2Domain._evidence_refs_in(child))
        return refs

    async def _apply_state_update(self, state, update: StateUpdate | None, capabilities):
        """Merge model metadata while keeping runtime-owned facts authoritative."""
        del capabilities
        execution = dict(state.execution)
        warnings = list(execution.get("state_update_warnings") or [])
        local_start = len(warnings)
        if update is None:
            warnings.append("state_update_missing")
            execution["metadata_status"] = "incomplete_metadata"
            execution["state_update_warnings"] = warnings[-40:]
            return state.model_copy(update={"execution": execution}), []

        if not any((update.research_plan, update.article_updates, update.evidence_additions,
                    update.coverage, update.review, update.delivery)):
            warnings.append("state_update_empty")

        next_plan = state.research_plan
        if update.research_plan is not None:
            candidate_revision = update.research_plan.get("revision")
            current_revision = state.research_plan.get("revision", 0)
            if type(candidate_revision) is int and candidate_revision > current_revision:
                next_plan = dict(update.research_plan)
            else:
                warnings.append("research_plan_revision_not_increasing")

        article_states = [dict(item) for item in state.article_states]
        article_index = self._article_index(article_states)
        for item in update.article_updates:
            index = article_index.get(item.article_id)
            if index is None:
                warnings.append(f"article_update_for_unlisted:{item.article_id}")
                continue
            if item.disposition == "exclude" and not item.reason:
                warnings.append(f"excluded_article_missing_reason:{item.article_id}")
                continue
            current = article_states[index]
            for field in ("relevance", "requested_depth", "disposition", "reason"):
                value = getattr(item, field)
                if value is not None:
                    current[field] = value
            article_states[index] = current

        known_refs = {str(ref) for ref in execution.get("material_refs", []) if isinstance(ref, str)}
        known_refs.update(str(ref) for ref in execution.get("evidence_refs", []) if isinstance(ref, str))
        known_refs.update(
            str(item.get("evidence_ref"))
            for item in state.evidence_ledger
            if isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str)
        )
        for article in state.article_states:
            if not isinstance(article, Mapping):
                continue
            known_refs.update(
                ref
                for ref in (
                    article.get("material_ref"),
                    article.get("result_ref"),
                    *(article.get("material_refs") or []),
                    *(article.get("result_refs") or []),
                )
                if isinstance(ref, str)
            )
        known_refs = self._usable_refs(execution, known_refs)
        additions = [dict(item) for item in state.evidence_ledger]
        existing_keys = {
            (item.get("claim_id"), item.get("evidence_ref"), item.get("relation"))
            for item in additions
            if isinstance(item, Mapping)
        }
        for item in update.evidence_additions:
            if item.evidence_ref not in known_refs:
                warnings.append(f"evidence_ref_not_in_run:{item.evidence_ref}")
                continue
            key = (item.claim_id, item.evidence_ref, item.relation)
            if key in existing_keys:
                continue
            additions.append(item.model_dump(mode="json", exclude_none=True))
            existing_keys.add(key)

        next_coverage = state.coverage
        next_review = state.review
        next_delivery = state.delivery
        for field in ("coverage", "review", "delivery"):
            candidate = getattr(update, field)
            if candidate is None:
                continue
            if self._evidence_refs_in(candidate) - known_refs:
                warnings.append(f"{field}_contains_unknown_evidence_ref")
                continue
            merged = {**(getattr(state, field) or {}), **candidate}
            if field == "coverage":
                next_coverage = merged
            elif field == "review":
                next_review = merged
            else:
                next_delivery = merged

        execution["evidence_refs"] = sorted(known_refs)
        execution["metadata_status"] = "complete" if not warnings else "incomplete_metadata"
        execution["state_update_warnings"] = warnings[-40:]
        return state.model_copy(update={
            "research_plan": next_plan,
            "article_states": article_states,
            "evidence_ledger": additions,
            "coverage": next_coverage,
            "review": next_review,
            "delivery": next_delivery,
            "execution": execution,
        }), warnings[local_start:][-20:]

    def _merge_article_read(self, article_states, execution, payload, *, task_id,
                            material_ref, result_ref, evidence_refs):
        article = _as_object(payload.get("article"))
        article_id = article.get("article_id")
        index = self._article_index(article_states).get(article_id)
        if index is None and isinstance(article_id, str):
            index = len(article_states)
            article_states.append({"article_id": article_id, "listed": True,
                                   "read_status": "unread", "use_status": "undecided"})
        if index is None:
            return
        current = article_states[index]
        # Direct and delegated observations must share the same source-version boundary.
        if current.get("current_hash") != article.get("current_hash"):
            had_read = current.get("read_status") in {"partial", "complete"} or (
                current.get("read_status") == "failed"
                and (current.get("current_hash") is not None or bool(current.get("read_ranges")))
            )
            current["read_status"] = "unread"
            current.pop("read_ranges", None)
            if had_read:
                warning = f"article_source_version_changed:{article_id}"
                warnings = list(execution.get("state_update_warnings") or [])
                if warning not in warnings:
                    warnings.append(warning)
                execution["state_update_warnings"] = warnings[-40:]
                execution["metadata_status"] = "incomplete_metadata"
        current["current_hash"] = article.get("current_hash")
        current.update({key: article[key] for key in (
            "author", "title", "published_at",
        ) if key in article})
        read_ranges = self._merge_read_ranges(current, payload.get("start_line"), payload.get("end_line"))
        read_status = "complete" if self._read_is_complete(payload, current) else "partial"
        current["read_status"] = self._merge_read_status(str(current.get("read_status", "unread")), read_status)
        if read_ranges:
            current["read_ranges"] = read_ranges
        current.update({
            "task_id": task_id,
            "result_ref": result_ref,
            "material_ref": material_ref,
            "material_refs": list(dict.fromkeys([
                *(current.get("material_refs") or []),
                *([material_ref] if material_ref else []), *evidence_refs,
            ])),
            "result_refs": list(dict.fromkeys([
                *(current.get("result_refs") or []),
                *([result_ref] if isinstance(result_ref, str) else []),
            ])),
        })
        article_states[index] = current

    @staticmethod
    def _remember_delegated_refs(execution, result_ref, refs):
        if not isinstance(result_ref, str):
            return
        sources = dict(execution.get("delegated_source_refs") or {})
        sources[result_ref] = sorted(set(sources.get(result_ref, [])) | {
            ref for ref in refs if isinstance(ref, str)
        })
        execution["delegated_source_refs"] = sources

    @staticmethod
    def _usable_refs(execution, refs):
        sources = _as_object(execution.get("delegated_source_refs"))
        provenance = {ref for values in sources.values() if isinstance(values, list)
                      for ref in values if isinstance(ref, str)}
        direct = set(execution.get("material_refs") or []) | set(execution.get("local_evidence_refs") or [])
        return set(refs) - (provenance - direct - set(sources))

    async def _restore_reference_view(self, context, execution, capabilities):
        if execution.get("reference_view_version") == 1:
            return
        local = set(execution.get("local_evidence_refs") or [])
        # Old checkpoints already contain the original observations; do not reread sources.
        for ref in dict.fromkeys([*execution.get("observation_refs", []),
                                  *execution.get("action_observation_refs", [])]):
            observation = _as_object(json.loads(await capabilities.load_context(ref)))
            if observation.get("status") not in {"succeeded", "partial"}:
                continue
            if observation.get("tool_name") == "result.get":
                result = _as_object(observation.get("payload"))
                refs = self._evidence_refs_in({"evidence_refs": result.get("evidence_refs", [])})
                self._remember_delegated_refs(execution, result.get("result_ref"), refs)
            else:
                refs = self._evidence_refs_in({"evidence_refs": _as_object(
                    observation.get("summary")).get("evidence_refs", [])})
                if isinstance(observation.get("result_ref"), str):
                    self._remember_delegated_refs(execution, observation["result_ref"], refs)
                elif observation.get("task_id") == context.brief.get("task_id"):
                    local.update(refs)
        for article in context.state.article_states:
            if article.get("task_id") == context.brief.get("task_id"):
                local.update(article.get("material_refs") or [])
        execution["local_evidence_refs"] = sorted(local)
        execution["reference_view_version"] = 1

    async def _inherit_source_lineage(self, article_states, execution, result, capabilities, material_ref=""):
        accepted = self._evidence_refs_in({"evidence_refs": result.get("evidence_refs", [])})
        self._remember_delegated_refs(execution, result.get("result_ref"), accepted)
        lineage_ref = _as_object(result.get("section_refs")).get("finance_sources")
        if not isinstance(lineage_ref, str):
            return
        lineage = _as_object(json.loads(await capabilities.load_context(lineage_ref)))
        if lineage.get("schema_version") != "finance.h2.source_lineage.v1":
            return
        for source_ref, reads in _as_object(lineage.get("bindings")).items():
            if source_ref not in accepted or not isinstance(reads, list):
                continue
            for read in reads:
                if isinstance(read, Mapping):
                    if read.get("source_kind") == "personal_context":
                        remember_personal_source(execution, read, material_ref=material_ref,
                                                 result_ref=result.get("result_ref"))
                        continue
                    if read.get("source_kind") in {"public_web_page", "public_market_data"}:
                        remember_public_source(execution, read, material_ref=material_ref,
                                               result_ref=result.get("result_ref"))
                        continue
                    self._merge_article_read(
                        article_states, execution, read,
                        task_id=read.get("reader_task_id"), material_ref=material_ref,
                        result_ref=result.get("result_ref"), evidence_refs=[],
                    )

    async def _consume_observations(self, context, capabilities):
        state = context.state
        execution = dict(state.execution)
        await self._restore_reference_view(context, execution, capabilities)
        material_refs = list(execution.get("material_refs") or [])
        observed_payloads: list[dict[str, Any]] = []
        completed = list(state.completed_steps)
        article_states = [dict(item) for item in state.article_states]
        evidence = list(state.evidence_ledger)
        known_evidence_refs = {
            str(ref) for ref in execution.get("evidence_refs", []) if isinstance(ref, str)
        }
        pending = _as_object(execution.get("pending_action"))
        for observation in context.observations:
            for task in observation.get("tasks", []):
                task_snapshot = {
                    "task_id": task.get("task_id"), "status": task.get("status"),
                    "attempts": task.get("attempts", []),
                    **{key: task[key] for key in (
                        "thread_id", "budget", "deadline_at", "cancel_requested", "dispatch",
                        "failure_diagnostic",
                    ) if key in task},
                }
                if task.get("status") not in TERMINAL:
                    observed_payloads.append(task_snapshot)
                    continue
                result = _as_object(task.get("result"))
                successful = task.get("status") in {"succeeded", "partial"}
                if not successful:
                    pending_action = _as_object(pending.get("action"))
                    if (
                        task.get("status") in {"failed", "timed_out"}
                        and pending_action.get("tool_name") == "article.read"
                    ):
                        article_id = _as_object(pending_action.get("arguments")).get("article_id")
                        index = self._article_index(article_states).get(article_id)
                        if index is not None and article_states[index].get("read_status") != "complete":
                            article_states[index]["read_status"] = "failed"
                    workflow = dict(_as_object(execution.get("review_workflow")))
                    if workflow.get("pending_task_id") == task.get("task_id"):
                        workflow.pop("pending_task_id", None)
                        workflow.update(status="review_failed", failure_code=task.get("status", "failed"))
                        execution["review_workflow"] = workflow
                if not result:
                    workflow = dict(_as_object(execution.get("review_workflow")))
                    if workflow.get("pending_task_id") == task.get("task_id"):
                        workflow.pop("pending_task_id", None)
                        workflow.pop("pending_draft_ref", None)
                        workflow.pop("pending_revision", None)
                        failure_code = task.get("status")
                        if failure_code in {"succeeded", "partial", None}:
                            failure_code = "missing_child_result"
                        workflow.update(status="review_failed", failure_code=failure_code)
                        execution["review_workflow"] = workflow
                    observed_payloads.append(task_snapshot)
                    continue
                result_ref = result.get("result_ref")
                if successful and isinstance(result_ref, str):
                    known_evidence_refs.add(result_ref)
                full_content = result.get("full_content")
                parsed = None
                if isinstance(full_content, str):
                    try:
                        parsed = json.loads(full_content)
                    except json.JSONDecodeError:
                        parsed = {"content": full_content}
                    ref = await capabilities.store_context({
                        "schema_version": "finance.h2.material.v1",
                        "source_task_id": task.get("task_id"),
                        "result_ref": result_ref,
                        "payload": parsed,
                    })
                    if successful:
                        material_refs.append(ref)
                        known_evidence_refs.add(ref)
                else:
                    ref = ""
                workflow = dict(_as_object(execution.get("review_workflow")))
                if workflow.get("pending_task_id") == task.get("task_id"):
                    if successful and isinstance(parsed, Mapping):
                        workflow = self._record_review_report(
                            workflow, parsed, result_ref=result_ref, material_ref=ref,
                        )
                    elif successful and not isinstance(full_content, str) and isinstance(result_ref, str):
                        workflow.update(status="report_pending_read", pending_result_ref=result_ref)
                    else:
                        workflow.pop("pending_task_id", None)
                        workflow.pop("pending_draft_ref", None)
                        workflow.pop("pending_revision", None)
                        workflow.pop("pending_result_ref", None)
                        workflow.update(status="report_invalid" if successful else "review_failed",
                                        failure_code=("invalid_report_binding" if successful
                                                      else task.get("status", "failed")))
                    execution["review_workflow"] = workflow
                envelope = _as_object(result.get("envelope"))
                refs = []
                for item in envelope.get("evidence_refs", []):
                    if isinstance(item, str):
                        refs.append(item)
                    elif isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str):
                        refs.append(item["evidence_ref"])
                if not successful:
                    refs = []
                if successful and isinstance(result_ref, str):
                    self._remember_delegated_refs(execution, result_ref, refs)
                    citation_refs = [result_ref]
                else:
                    citation_refs = refs
                    execution["local_evidence_refs"] = sorted(set(execution.get("local_evidence_refs") or []) | set(refs))
                known_evidence_refs.update(citation_refs)
                if successful:
                    await self._inherit_source_lineage(
                        article_states, execution, {**result, "evidence_refs": refs}, capabilities, ref,
                    )
                existing_evidence = {
                    (item.get("evidence_ref"), item.get("task_id"))
                    for item in evidence
                    if isinstance(item, Mapping)
                }
                for item in citation_refs:
                    key = (item, task.get("task_id"))
                    if key not in existing_evidence:
                        evidence.append({"evidence_ref": item, "task_id": task.get("task_id")})
                        existing_evidence.add(key)
                tool_payload = _as_object(parsed.get("payload")) if isinstance(parsed, Mapping) else {}
                observed_tool = None
                if isinstance(parsed, Mapping):
                    observed_tool = tool_payload.get("tool") or parsed.get("tool")
                if successful and observed_tool in {"article.list", "article.search"}:
                    rows = tool_payload.get("articles")
                    if rows is None:
                        rows = tool_payload.get("result")
                    if isinstance(rows, Mapping):
                        rows = rows.get("articles") or rows.get("results")
                    for row in rows if isinstance(rows, list) else []:
                        if not isinstance(row, Mapping) or not isinstance(row.get("article_id"), str):
                            continue
                        index = self._article_index(article_states).get(row["article_id"])
                        if index is None:
                            article_states.append({
                                "article_id": row["article_id"],
                                "listed": True,
                                "read_status": "unread",
                                "use_status": "undecided",
                            })
                        else:
                            article_states[index]["listed"] = True
                if successful and ref and observed_tool == "article.read":
                    self._merge_article_read(
                        article_states, execution, tool_payload, task_id=task.get("task_id"),
                        material_ref=ref, result_ref=result_ref, evidence_refs=citation_refs,
                    )
                if successful and observed_tool == "personal.catalog":
                    execution["personal_context_refs"] = [
                        item["context_id"] for item in tool_payload.get("sources", [])
                        if isinstance(item, dict) and isinstance(item.get("context_id"), str)
                    ]
                personal_read = personal_source_read(tool_payload, task.get("task_id")) if successful else None
                if personal_read is not None:
                    remember_personal_source(execution, personal_read, material_ref=ref, result_ref=result_ref)
                public_read = public_source_read(tool_payload, task.get("task_id")) if successful else None
                if public_read is not None:
                    remember_public_source(execution, public_read, material_ref=ref, result_ref=result_ref)
                observed_payloads.append({
                    **task_snapshot,
                    "result_ref": result_ref,
                    "summary": envelope,
                    "material_ref": ref,
                    "tool_name": observed_tool,
                    **({"full_read": False, "expansion_tool": "result.get"}
                       if not isinstance(full_content, str) and isinstance(result_ref, str) else {}),
                    **({"payload": parsed} if not successful else {}),
                })
                if pending.get("task_id") == task.get("task_id"):
                    action_key = pending.get("action_key")
                    if isinstance(action_key, str) and action_key not in completed:
                        completed.append(action_key)
        execution["material_refs"] = list(dict.fromkeys(material_refs))
        execution["evidence_refs"] = sorted(self._usable_refs(execution, known_evidence_refs))
        for article in article_states:
            for field in ("material_refs", "result_refs"):
                if field in article:
                    usable = self._usable_refs(execution, article[field])
                    article[field] = [ref for ref in article[field] if ref in usable]
            for field in ("material_ref", "result_ref"):
                if field in article and not self._usable_refs(execution, [article[field]]):
                    article.pop(field)
        for index, item in enumerate(evidence):
            ref = item.get("evidence_ref")
            if isinstance(ref, str) and not self._usable_refs(execution, [ref]):
                evidence[index] = {**item, "reference_kind": "source_provenance"}
        execution["pending_action"] = {}
        observation_refs = list(execution.get("observation_refs") or [])
        for payload in observed_payloads:
            observation_refs.append(await capabilities.store_context(payload))
        execution.pop("observations", None)
        execution["observation_refs"] = observation_refs
        updated = state.model_copy(update={
            "completed_steps": completed,
            "pending_steps": [],
            "article_states": article_states,
            "evidence_ledger": evidence,
            "execution": execution,
            "revision": state.revision + (1 if context.observations else 0),
        })
        return updated, observed_payloads

    async def _messages(self, query, state, observations, capabilities):
        execution = _as_object(state.execution)
        materials = []
        for ref in execution.get("material_refs", []):
            if not isinstance(ref, str):
                continue
            try:
                raw = await capabilities.load_context(ref)
                value = _material_payload(raw)
            except DispatchError as exc:
                if exc.code in {"cancelled", "stale_lease"}:
                    raise
                materials.append({"material_ref": ref, "unavailable": True})
                continue
            except (json.JSONDecodeError, TypeError, ValueError):
                materials.append({"material_ref": ref, "unavailable": True})
                continue
            text = _json(value)
            if isinstance(value, dict) and value.get("schema_version") == SOURCE_SCHEMA:
                materials.append({"material_ref": ref, "context_id": ref, "payload_omitted": True,
                                  "expansion_tool": "context.open",
                                  **{key: value[key] for key in (
                                      "personal_kind", "source_path", "source_hash", "captured_at", "as_of",
                                  ) if key in value}})
            else:
                materials.append({"material_ref": ref, "payload": text})
        normalized_observations = []
        for ref in dict.fromkeys(execution.get("observation_refs", []) or []):
            value = json.loads(await capabilities.load_context(ref))
            normalized_observations.append({"observation_ref": ref, **value})
        action_observations = []
        for ref in dict.fromkeys(execution.get("action_observation_refs", []) or []):
            value = json.loads(await capabilities.load_context(ref))
            action_observations.append({"observation_ref": ref, **value})
        mailbox = []
        for message in execution.get("mailbox", []):
            ref = message.get("payload_ref")
            if isinstance(ref, str):
                text = await capabilities.load_context(ref)
                if isinstance(text, bytes):
                    text = text.decode("utf-8")
                mailbox.append({**message, "content": text})
        rules = rules_for(capabilities)
        system = rules.system(execution.get("task_budget"))
        if self.public_sources is not None and PUBLIC_TOOLS.intersection(execution.get("allowed_tools", [])):
            system += " " + rules.text("public-source")
        if self.market_sources is not None and MARKET_TOOLS.intersection(execution.get("allowed_tools", [])):
            system += " " + rules.text("market-source")
        if self.personal_sources is not None and PERSONAL_TOOLS.intersection(execution.get("allowed_tools", [])):
            system += " " + rules.text("personal-source")
        user = {
            "query": query,
            "delegation_policy": delegation_policy(execution.get("task_budget"), rules=rules),
            **({"research_roles": rules.catalog()} if rules.catalog() else {}),
            "instruction_rules": {"sha256": rules.sha256, "version": rules.manifest["version"]},
            "task_budget": _as_object(execution.get("task_budget")),
            "task_identity": _as_object(execution.get("task_identity")),
            **({"research_clock": execution["research_clock"]} if "research_clock" in execution else {}),
            "next_action_key": execution.get("next_action_key"),
            "phase": state.phase,
            "revision": state.revision,
            "completed_steps": state.completed_steps,
            "pending_steps": state.pending_steps,
            "research_plan": state.research_plan,
            "article_states": state.article_states,
            "evidence_ledger": state.evidence_ledger,
            "coverage": state.coverage,
            "review": state.review,
            "delivery": state.delivery,
            # Compatibility ordering/status remain; large values live in typed views.
            "observations": _context_views(materials, normalized_observations, action_observations),
            "normalized_observations": normalized_observations,
            "action_observations": action_observations,
            "scheduled_children": execution.get("scheduled_children", []),
            "mailbox": mailbox,
            "materials": materials,
            **({"public_sources": execution["public_sources"]} if execution.get("public_sources") else {}),
            **({"personal_sources": execution["personal_sources"]} if execution.get("personal_sources") else {}),
            "allowed_article_tools": sorted(ARTICLE_TOOLS.intersection(execution.get("allowed_tools", ROOT_TOOLS))),
            "tool_catalog": [
                *[{"name": name, "description": text} for name, text in rules.descriptions().items()
                  if name in execution.get("allowed_tools", ROOT_TOOLS)
                  and (name not in PUBLIC_TOOLS or self.public_sources is not None)
                  and (name not in MARKET_TOOLS or self.market_sources is not None)
                  and (name not in PERSONAL_TOOLS or self.personal_sources is not None)],
                *[{"name": name, "description": rules.descriptions(scheduler=True)[name]}
                  for name in capabilities.tool_names],
            ],
            "known_material_refs": list(_as_object(state.execution).get("material_refs") or []),
            "known_evidence_refs": list(_as_object(state.execution).get("evidence_refs") or []),
            "metadata_status": _as_object(state.execution).get("metadata_status", "incomplete_metadata"),
            "state_update_warnings": list(_as_object(state.execution).get("state_update_warnings") or [])[-10:],
        }
        if execution.get("follow_up_context"):
            user["follow_up_context"] = execution["follow_up_context"]
        return _fit_context(system, user, capabilities)

    async def _spawn_child(
        self, context, state, *, role, role_version, objective, action, input_refs, capabilities,
        task_instructions=None, review_policy=None, output_contract=None,
    ):
        action_key = _action_key(action, revision=state.revision, task_id=context.brief.get("task_id"))
        request_payload = {
            "schema_version": "finance.h2.child_request.v1",
            "query": context.query,
            "action": action,
            "input_refs": input_refs,
        }
        request_ref = state.execution.get("prepared_child_request_ref")
        if not isinstance(request_ref, str):
            request_ref = await capabilities.store_context(request_payload)
        fixed = context.brief.get("budget", {}).get("policy") == "fixed"
        request = Spawn(
            dispatch_key=action_key,
            role=role,
            role_version=role_version,
            objective=objective,
            task_instructions=task_instructions,
            success_criteria=(
                list(dict.fromkeys(action.get("success_criteria") or []))
                if action.get("success_criteria") else ["return a source-bound, public JSON result"]
            ),
            input_refs=list(dict.fromkeys([request_ref, *input_refs])),
            output_contract=output_contract or OutputContract(name="finance.child", version="1"),
            review_policy=review_policy,
            requested_budget=None if fixed else legacy_delegate_budget(role),
            wall_time_ms=legacy_delegate_budget(role).wall_time_ms if fixed else None,
        )
        receipt = await capabilities.tool("agent.spawn", request.model_dump(mode="json", exclude_none=True))
        child_id = receipt["data"]["task_id"]
        execution = dict(state.execution)
        execution.pop("prepared_action_ref", None)
        execution.pop("prepared_child_request_ref", None)
        execution["pending_action"] = {"action_key": action_key, "task_id": child_id, "action": action}
        next_state = state.model_copy(update={
            "phase": "research",
            "pending_steps": [action_key],
            "execution": execution,
            "revision": state.revision + 1,
        })
        return FinanceStep(
            status="waiting",
            state=next_state,
            wait=Wait(task_ids=[child_id], mode="all", timeout_ms=60000),
        )

    async def _delegate(self, context, state, action, capabilities):
        if action.role not in {"analyst", "verifier", "skeptic"}:
            raise DispatchError("forbidden", "finance root requested an unregistered role")
        if action.role_version == "2.0.0":
            rules = rules_for(capabilities)
            uses_profiles = rules.has_profiles
            research_task = action.research_task.model_dump(mode="json")
            review_policy = action.research_task.review_policy
            if (not rules.portable_profiles
                    and "max_rounds" not in review_policy.model_fields_set):
                review_policy = review_policy.model_copy(update={"max_rounds": 3})
                research_task["review_policy"] = review_policy.model_dump(mode="json", exclude_none=True)
            material_refs = list(research_task.get("material_refs") or [])
            known = set(_as_object(state.execution).get("material_refs") or [])
            if not set(material_refs) <= known:
                raise DispatchError("invalid_arguments", "research task references unauthorized material")
            objective = research_task["objective"]
            success_criteria = list(dict.fromkeys([
                research_task["completion_expectation"], *research_task.get("user_requirements", []),
            ]))
            payload = {
                "type": "delegate", "role": action.role, "research_task": research_task,
                "success_criteria": success_criteria,
            }
            task_parts = [
                f"Original question: {research_task['original_query']}",
                *[f"Focus: {item}" for item in research_task.get("focus", [])],
                *[f"User requirement: {item}" for item in research_task.get("user_requirements", [])],
                f"Completion expectation: {research_task['completion_expectation']}",
            ]
            return await self._spawn_child(
                context, state, role=action.role, role_version="2.0.0",
                objective=objective, action=payload, input_refs=material_refs,
                task_instructions="\n".join(task_parts)[:4096] if uses_profiles else None,
                review_policy=review_policy if uses_profiles else None,
                output_contract=OutputContract(
                    name="finance.analyst_result" if uses_profiles else "finance.research_result",
                    version="2" if uses_profiles else "1",
                ),
                capabilities=capabilities,
            )
        payload = {"type": "delegate", "role": action.role, "task": action.task}
        material_refs = list(action.input_artifact_refs)
        known = set(_as_object(state.execution).get("material_refs") or [])
        if not set(material_refs) <= known:
            raise DispatchError("invalid_arguments", "delegate references unauthorized material")
        return await self._spawn_child(
            context, state, role=action.role, role_version="1.0.0",
            objective=action.task or "perform the requested bounded finance analysis",
            action=payload, input_refs=material_refs, capabilities=capabilities,
        )

    async def _final(self, context, state, output, capabilities, state_update_warnings=None):
        body = _as_object(output)
        answer = str(body.get("answer") or body.get("content") or "").strip()
        if not answer:
            raise DispatchError("contract_error", "finance final action has no answer")
        known_refs = set(_as_object(state.execution).get("material_refs") or [])
        known_refs.update(_as_object(state.execution).get("evidence_refs") or [])
        for article in state.article_states:
            if not isinstance(article, Mapping):
                continue
            known_refs.update(
                ref
                for ref in (
                    article.get("material_ref"),
                    article.get("result_ref"),
                    *(article.get("material_refs") or []),
                    *(article.get("result_refs") or []),
                )
                if isinstance(ref, str)
            )
        known_refs = self._usable_refs(state.execution, known_refs)
        requested_refs = body.get("evidence_refs") or []
        evidence_refs = [ref for ref in requested_refs if isinstance(ref, str) and ref in known_refs]
        warnings = ["Some model evidence references were not accepted and were omitted."] if len(evidence_refs) != len(requested_refs) else []
        warnings.extend(state_update_warnings or [])
        gaps = [str(item) for item in body.get("gaps", []) if isinstance(item, str)]
        model_warnings = [str(item) for item in body.get("warnings", []) if isinstance(item, str)]
        status = "partial" if warnings or gaps or model_warnings else "succeeded"
        evidence_bindings = [{"evidence_ref": ref} for ref in evidence_refs]
        candidate_ref = await capabilities.store_context({"schema_version": "finance.h2.candidate.v1", "output": body})
        article_states = [dict(item) for item in state.article_states]
        used_refs = set(evidence_refs)
        for item in article_states:
            article_refs = {
                item.get("material_ref"),
                item.get("result_ref"),
                *(item.get("material_refs") or []),
                *(item.get("result_refs") or []),
            }
            if used_refs.intersection(article_refs):
                item["use_status"] = "used"
        execution = dict(state.execution)
        execution["pending_action"] = {}
        metadata_status = execution.get("metadata_status", "incomplete_metadata")
        if state_update_warnings:
            metadata_status = "incomplete_metadata"
        next_state = state.model_copy(update={
            "phase": "delivery",
            "pending_steps": [],
            "candidate_answer": {"artifact_ref": candidate_ref, "status": "ready"},
            "article_states": article_states,
            "delivery": {
                "status": "ready",
                "evidence_refs": evidence_refs,
                "metadata_status": metadata_status,
                "warnings": warnings[-20:],
            },
            "execution": execution,
            "revision": state.revision + 1,
        })
        return FinanceStep(
            status=status,
            state=next_state,
            submission=Submission(
                result_key="finance-final-" + str(next_state.revision),
                status=status,
                content=answer,
                summary=str(body.get("summary") or answer[:500]),
                key_points=[str(item) for item in body.get("key_points", []) if isinstance(item, str)],
                evidence_refs=evidence_bindings,
                gaps=gaps,
                warnings=[*warnings, *model_warnings],
            ),
        )

    async def _approval(self, context, state, action, capabilities):
        del capabilities
        execution = dict(state.execution)
        execution["approval_request"] = {"tool_name": action.tool_name, "reason": action.reason}
        next_state = state.model_copy(update={
            "phase": "delivery",
            "execution": execution,
            "delivery": {"status": "partial", "approval_required": True},
            "revision": state.revision + 1,
        })
        return FinanceStep(
            status="partial",
            state=next_state,
            submission=Submission(
                result_key="finance-approval-required-" + str(next_state.revision),
                status="partial",
                content="The requested finance action requires approval and was not executed.",
                summary=action.reason,
                warnings=["approval_required", f"requested_tool:{action.tool_name}"],
            ),
        )


class FinanceH2ChildExecutor:
    """Execute bounded article acquisition or versioned analyst work."""

    def __init__(self, corpus, *, public_sources=None, market_sources=None, personal_sources=None):
        self.corpus = corpus
        self.public_sources = public_sources
        self.market_sources = market_sources
        self.personal_sources = personal_sources

    async def __call__(self, work, capabilities):
        if work.brief.get("role") in rules_for(capabilities).manifest["roles"]:
            from .finance_research import FinanceH2ResearchDomain

            return await FinanceRootExecutor(FinanceH2ResearchDomain(
                self.corpus, public_sources=self.public_sources, market_sources=self.market_sources,
                personal_sources=self.personal_sources,
            ), research=True)(work, capabilities)
        refs = list(work.brief.get("input_refs") or [])
        if not refs:
            raise DispatchError("contract_error", "finance child has no input request")
        raw = await capabilities.load_context(refs[0])
        request = json.loads(raw)
        action = _as_object(request.get("action"))
        if work.brief.get("role") == "finance_tool":
            failed = False
            try:
                payload = self._tool(action, rules=rules_for(capabilities))
            except DispatchError:
                raise
            except ValueError as exc:
                failed = True
                issues = []
                if isinstance(exc, ValidationError):
                    issues = [{"path": list(item["loc"]), "type": item["type"]}
                              for item in exc.errors(include_input=False, include_url=False)[:8]]
                payload = {"tool": action.get("tool_name"), "ok": False, "error": {
                    "code": "invalid_arguments",
                    "message": "Tool request rejected; use tool.describe for the exact input Schema. No source read succeeded.",
                    "issues": issues,
                    "describe_name": action.get("tool_name"),
                }}
            ref = await capabilities.store_context({"schema_version": "finance.h2.tool_result.v1", "payload": payload})
            evidence_refs = [{"evidence_ref": ref}] if not failed and action.get("tool_name") == "article.read" else []
            return Submission(
                result_key="finance-tool-" + str(work.task_id),
                status="failed" if failed else "succeeded",
                full_ref=ref,
                summary=f"{action.get('tool_name', 'article tool')} {'rejected: invalid_arguments' if failed else 'completed'}",
                evidence_refs=evidence_refs,
                warnings=["tool_contract_error"] if failed else [],
            )
        raise DispatchError("forbidden", "unregistered finance child role")

    def _tool(self, action: dict[str, Any], *, allowed_article_ids: set[str] | None = None, rules=None) -> dict[str, Any]:
        name = action.get("tool_name")
        arguments = validate(name, action.get("arguments"))
        if (allowed_article_ids is not None and name in {"article.read", "article.claims"}
                and arguments.get("article_id") not in allowed_article_ids):
            raise DispatchError("forbidden", "article is outside the granted material scope")
        if name == "tool.describe":
            return {"tool": name, "description": describe(arguments["name"], rules=rules)}
        if name == "article.list":
            rows = self.corpus.list_articles(**arguments)
            if allowed_article_ids is not None:
                rows = [row for row in rows if row.get("article_id") in allowed_article_ids]
            if allowed_article_ids is None and callable(getattr(self.corpus, "list_authors", None)):
                authors = self.corpus.list_authors()
                authors_scope = "corpus_all_active"
            else:
                grouped = {}
                for row in rows:
                    author = row["author"]
                    entry = grouped.setdefault(author, {
                        "author": author, "article_count": 0, "latest_published_at": "",
                    })
                    entry["article_count"] += 1
                    entry["latest_published_at"] = max(
                        entry["latest_published_at"], row.get("published_at") or "",
                    )
                authors = [grouped[author] for author in sorted(grouped)]
                authors_scope = ("returned_authorized_articles" if allowed_article_ids is not None
                                 else "returned_articles")
            return {"tool": name, "articles": rows, "authors": authors,
                    "authors_scope": authors_scope}
        if name == "article.search":
            rows = self.corpus.search(**arguments)
            if allowed_article_ids is not None:
                rows = [row for row in rows if row.get("article_id") in allowed_article_ids]
            return {"tool": name, "result": rows}
        if name == "article.read":
            article_id = arguments.get("article_id")
            article = self.corpus.get_article(article_id)
            if not article:
                raise ValueError("article is not in the current corpus")
            start_line = arguments.get("start_line", 1)
            limit = arguments.get("limit", 100)
            if type(start_line) is not int or start_line < 1:
                raise ValueError("article.read start_line must be a positive integer")
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("article.read limit must be an integer from 1 to 100")
            lines = str(article.get("content") or "").splitlines()
            end_line = min(len(lines), start_line + limit - 1)
            return {
                "tool": name,
                "article": {
                    key: article.get(key)
                    for key in ("article_id", "author", "title", "published_at", "source_url", "current_hash")
                },
                "content": "\n".join(lines[start_line - 1:end_line]),
                "start_line": start_line,
                "end_line": end_line,
                "line_count": len(lines),
                "has_more": end_line < len(lines),
            }
        if name == "article.claims":
            return {"tool": name, "claims": self.corpus.claims(arguments.get("article_id"))}
        raise DispatchError("forbidden", "unsupported finance article tool")


__all__ = ["ARTICLE_TOOLS", "ROOT_TOOLS", "FinanceH2ChildExecutor", "FinanceH2Domain"]
