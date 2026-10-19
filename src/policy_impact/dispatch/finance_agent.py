"""Model-driven financial domain steps for the opt-in Harness 2.0 path.

The domain adapter translates public model actions into durable child Tasks.
It does not expose SQL/Redis to the model and it does not modify the legacy
finance AgentLoop. Source access is deliberately performed by child tasks so a
root Attempt can checkpoint after each bounded acquisition step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from policy_impact.runtime.actions import AgentAction, StateUpdate
from policy_impact.runtime.research_contracts import ResearchResult

from .contracts import BudgetRequest, DispatchError, OutputContract, Spawn, Submission, Wait
from .finance_root import FinanceRootContext, FinanceStep

ROOT_TOOLS = ("agent.spawn", "agent.wait", "agent.status", "result.get")
ARTICLE_TOOLS = frozenset({"article.list", "article.search", "article.read", "article.claims"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _action_key(action: Mapping[str, Any], *, revision: int) -> str:
    digest = hashlib.sha256(_json({"revision": revision, "action": action}).encode()).hexdigest()[:24]
    return "finance-action-" + digest


def _as_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class FinanceH2Domain:
    """One model decision per root Attempt, with durable child handoff."""

    def __init__(self, corpus, *, max_material_chars: int = 120_000):
        self.corpus = corpus
        self.max_material_chars = max_material_chars

    async def __call__(self, context: FinanceRootContext, capabilities) -> FinanceStep:
        state, observations = await self._consume_observations(context, capabilities)
        messages = await self._messages(context.query, state, observations, capabilities)
        response = await capabilities.complete(messages, "agent_action.v1")
        try:
            action = TypeAdapter(AgentAction).validate_python(response.payload)
        except (TypeError, ValueError) as exc:
            raise DispatchError("contract_error", "finance root returned an invalid action") from exc
        state, state_update_warnings = await self._apply_state_update(
            state, action.state_update, capabilities
        )
        if action.type == "final":
            return await self._final(
                context, state, action.output, capabilities, state_update_warnings
            )
        if action.type == "request_approval":
            return await self._approval(context, state, action, capabilities)
        if action.type == "tool_call":
            if action.tool_name not in ARTICLE_TOOLS:
                raise DispatchError("forbidden", "finance root requested an unavailable article tool")
            return await self._spawn_child(
                context,
                state,
                role="finance_tool",
                role_version="1.0.0",
                objective=f"Execute the bounded finance tool {action.tool_name} for the root question.",
                action={"type": "tool_call", "tool_name": action.tool_name, "arguments": action.arguments},
                input_refs=[],
                capabilities=capabilities,
            )
        if action.type == "delegate":
            return await self._delegate(context, state, action, capabilities)
        raise DispatchError("contract_error", "unsupported finance root action")

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
        return merged[-200:]

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
            "evidence_ledger": additions[-200:],
            "coverage": next_coverage,
            "review": next_review,
            "delivery": next_delivery,
            "execution": execution,
        }), warnings[local_start:][-20:]

    async def _consume_observations(self, context, capabilities):
        state = context.state
        execution = dict(state.execution)
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
                result = _as_object(task.get("result"))
                if not result:
                    pending_action = _as_object(pending.get("action"))
                    if (
                        task.get("status") in {"failed", "timed_out"}
                        and pending_action.get("tool_name") == "article.read"
                    ):
                        article_id = _as_object(pending_action.get("arguments")).get("article_id")
                        index = self._article_index(article_states).get(article_id)
                        if index is not None and article_states[index].get("read_status") != "complete":
                            article_states[index]["read_status"] = "failed"
                    observed_payloads.append({"task_id": task.get("task_id"), "status": task.get("status")})
                    continue
                result_ref = result.get("result_ref")
                if isinstance(result_ref, str):
                    known_evidence_refs.add(result_ref)
                full_content = result.get("full_content")
                if not isinstance(full_content, str) and isinstance(result_ref, str):
                    page = await capabilities.tool(
                        "result.get", {"result_ref": result_ref, "view": "full", "offset": 0, "limit": 64000}
                    )
                    full_content = page.get("content") if isinstance(page, dict) else None
                parsed = None
                if isinstance(full_content, str):
                    try:
                        parsed = json.loads(full_content)
                    except json.JSONDecodeError:
                        parsed = {"content": full_content[: self.max_material_chars]}
                    ref = await capabilities.store_context({
                        "schema_version": "finance.h2.material.v1",
                        "source_task_id": task.get("task_id"),
                        "result_ref": result_ref,
                        "payload": parsed,
                    })
                    material_refs.append(ref)
                    known_evidence_refs.add(ref)
                else:
                    ref = ""
                envelope = _as_object(result.get("envelope"))
                refs = []
                for item in envelope.get("evidence_refs", []):
                    if isinstance(item, str):
                        refs.append(item)
                    elif isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str):
                        refs.append(item["evidence_ref"])
                known_evidence_refs.update(refs)
                existing_evidence = {
                    (item.get("evidence_ref"), item.get("task_id"))
                    for item in evidence
                    if isinstance(item, Mapping)
                }
                for item in refs:
                    key = (item, task.get("task_id"))
                    if key not in existing_evidence:
                        evidence.append({"evidence_ref": item, "task_id": task.get("task_id")})
                        existing_evidence.add(key)
                tool_payload = _as_object(parsed.get("payload")) if isinstance(parsed, Mapping) else {}
                observed_tool = None
                if isinstance(parsed, Mapping):
                    observed_tool = tool_payload.get("tool") or parsed.get("tool")
                if observed_tool in {"article.list", "article.search"}:
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
                if ref and observed_tool == "article.read":
                    article = _as_object(tool_payload.get("article"))
                    article_id = article.get("article_id")
                    index = self._article_index(article_states).get(article_id)
                    if index is not None:
                        current = article_states[index]
                        read_ranges = self._merge_read_ranges(
                            current,
                            tool_payload.get("start_line"),
                            tool_payload.get("end_line"),
                        )
                        read_status = (
                            "complete"
                            if self._read_is_complete(tool_payload, current)
                            else "partial"
                        )
                        current["read_status"] = self._merge_read_status(
                            str(current.get("read_status", "unread")), read_status
                        )
                        if read_ranges:
                            current["read_ranges"] = read_ranges
                        current.update({
                            "task_id": task.get("task_id"),
                            "result_ref": result_ref,
                            "material_ref": ref,
                            "material_refs": list(dict.fromkeys([
                                *(current.get("material_refs") or []),
                                ref,
                            ])),
                            "result_refs": list(dict.fromkeys([
                                *(current.get("result_refs") or []),
                                *([result_ref] if isinstance(result_ref, str) else []),
                            ])),
                        })
                        article_states[index] = current
                observed_payloads.append({
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                    "result_ref": result_ref,
                    "summary": envelope,
                    "payload": parsed,
                })
                if pending.get("task_id") == task.get("task_id"):
                    action_key = pending.get("action_key")
                    if isinstance(action_key, str) and action_key not in completed:
                        completed.append(action_key)
        execution["material_refs"] = list(dict.fromkeys(material_refs))
        execution["evidence_refs"] = sorted(known_evidence_refs)
        execution["pending_action"] = {}
        execution["observations"] = observed_payloads[-20:]
        updated = state.model_copy(update={
            "completed_steps": completed[-100:],
            "pending_steps": [],
            "article_states": article_states[-100:],
            "evidence_ledger": evidence[-200:],
            "execution": execution,
            "revision": state.revision + (1 if context.observations else 0),
        })
        return updated, observed_payloads

    async def _messages(self, query, state, observations, capabilities):
        execution = _as_object(state.execution)
        materials = []
        for ref in execution.get("material_refs", [])[-12:]:
            if not isinstance(ref, str):
                continue
            try:
                raw = await capabilities.load_context(ref)
                value = json.loads(raw)
            except (DispatchError, json.JSONDecodeError, TypeError, ValueError):
                continue
            text = _json(value)
            materials.append({"material_ref": ref, "payload": text[: self.max_material_chars]})
        system = (
            "You are the root financial research agent in a durable scheduler. "
            "First understand the user's query and make a plan. Decide the next bounded action yourself. "
            "Use article.list to discover metadata, article.read to read source text, and delegate for a "
            "source-bound article analysis or review. Each child result is evidence, not an automatic final source. "
            "Do not invent article IDs, evidence references, authors, dates or facts. "
            "Use type=tool_call with tool_name and arguments, type=delegate for a child, or type=final. "
            "A final answer must preserve uncertainty, author viewpoints, contrary evidence and gaps. "
            "Author agreement is not external factual verification. Every action may include an optional "
            "compact state_update: research_plan with a new integer revision, article_updates only for "
            "listed articles, evidence_additions only for known EvidenceRefs, and coverage/review/delivery "
            "snapshots. Never set read_status or use_status; Runtime derives them from tool results and final "
            "evidence. If no metadata update is justified, omit it and Runtime will mark metadata incomplete. "
            "Return exactly one public JSON action."
        )
        user = {
            "query": query,
            "phase": state.phase,
            "revision": state.revision,
            "completed_steps": state.completed_steps[-30:],
            "pending_steps": state.pending_steps,
            "research_plan": state.research_plan,
            "article_states": state.article_states[-30:],
            "evidence_ledger": state.evidence_ledger[-80:],
            "coverage": state.coverage,
            "observations": observations[-12:],
            "materials": materials,
            "allowed_article_tools": sorted(ARTICLE_TOOLS),
            "known_material_refs": list(_as_object(state.execution).get("material_refs") or []),
            "known_evidence_refs": list(_as_object(state.execution).get("evidence_refs") or []),
            "metadata_status": _as_object(state.execution).get("metadata_status", "incomplete_metadata"),
            "state_update_warnings": list(_as_object(state.execution).get("state_update_warnings") or [])[-10:],
        }
        return [{"role": "system", "content": system}, {"role": "user", "content": _json(user)}]

    async def _spawn_child(self, context, state, *, role, role_version, objective, action, input_refs, capabilities):
        action_key = _action_key(action, revision=state.revision)
        request_payload = {
            "schema_version": "finance.h2.child_request.v1",
            "query": context.query,
            "action": action,
            "input_refs": input_refs,
        }
        request_ref = await capabilities.store_context(request_payload)
        request = Spawn(
            dispatch_key=action_key,
            role=role,
            role_version=role_version,
            objective=objective,
            success_criteria=["return a source-bound, public JSON result"],
            input_refs=list(dict.fromkeys([request_ref, *input_refs])),
            output_contract=OutputContract(name="finance.child", version="1"),
            requested_budget=BudgetRequest(model_calls=4 if role == "analyst" else 1, wall_time_ms=600000 if role == "analyst" else 120000),
        )
        receipt = await capabilities.tool("agent.spawn", request.model_dump(mode="json"))
        child_id = receipt["data"]["task_id"]
        execution = dict(state.execution)
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
            research_task = action.research_task.model_dump(mode="json")
            material_refs = list(research_task.get("material_refs") or [])
            known = set(_as_object(state.execution).get("material_refs") or [])
            if not set(material_refs) <= known:
                raise DispatchError("invalid_arguments", "research task references unauthorized material")
            objective = research_task["objective"]
            payload = {"type": "delegate", "role": action.role, "research_task": research_task}
            return await self._spawn_child(
                context, state, role=action.role, role_version="2.0.0",
                objective=objective, action=payload, input_refs=material_refs,
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
        requested_refs = body.get("evidence_refs") or []
        evidence_refs = [ref for ref in requested_refs if isinstance(ref, str) and ref in known_refs]
        warnings = ["Some model evidence references were not accepted and were omitted."] if len(evidence_refs) != len(requested_refs) else []
        warnings.extend(state_update_warnings or [])
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
            status="succeeded" if not warnings else "partial",
            state=next_state,
            submission=Submission(
                result_key="finance-final-" + str(next_state.revision),
                status="succeeded" if not warnings else "partial",
                content=answer,
                summary=str(body.get("summary") or answer[:500]),
                key_points=[str(item) for item in body.get("key_points", []) if isinstance(item, str)][:20],
                evidence_refs=evidence_bindings,
                gaps=[str(item) for item in body.get("gaps", []) if isinstance(item, str)][:20],
                warnings=[*warnings, *[str(item) for item in body.get("warnings", []) if isinstance(item, str)][:20]],
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

    def __init__(self, corpus):
        self.corpus = corpus

    async def __call__(self, work, capabilities):
        refs = list(work.brief.get("input_refs") or [])
        if not refs:
            raise DispatchError("contract_error", "finance child has no input request")
        raw = await capabilities.load_context(refs[0])
        request = json.loads(raw)
        action = _as_object(request.get("action"))
        if work.brief.get("role") == "finance_tool":
            payload = self._tool(action)
            ref = await capabilities.store_context({"schema_version": "finance.h2.tool_result.v1", "payload": payload})
            evidence_refs = [{"evidence_ref": ref}] if action.get("tool_name") == "article.read" else []
            return Submission(
                result_key="finance-tool-" + str(work.task_id),
                full_ref=ref,
                summary=f"{action.get('tool_name', 'article tool')} completed",
                evidence_refs=evidence_refs,
            )
        if work.brief.get("role") in {"analyst", "verifier", "skeptic"}:
            material_refs = list(request.get("input_refs") or refs[1:])
            materials = []
            for material_ref in material_refs:
                value = json.loads(await capabilities.load_context(material_ref))
                materials.append(value)
            messages = [
                {"role": "system", "content": (
                    "Analyze the supplied financial source materials for the exact task. "
                    "Return a source-bound finance.research_result.v1 JSON result. "
                    "Separate author viewpoint from fact, preserve conditions and gaps, "
                    "and cite only the supplied material artifact refs."
                )},
                {"role": "user", "content": _json({
                    "objective": work.brief.get("objective"),
                    "materials": materials,
                    "material_refs": material_refs,
                })},
            ]
            response = await capabilities.complete(messages, "finance.research_result.v1")
            result = ResearchResult.model_validate(response.payload.get("output"))
            ref = await capabilities.store_context({
                "schema_version": "finance.h2.analyst_result.v1",
                "result": result.model_dump(mode="json"),
            })
            return Submission(
                result_key="finance-analyst-" + str(work.task_id),
                full_ref=ref,
                summary=result.content[:500],
                evidence_refs=[{"evidence_ref": ref} for ref in material_refs],
                gaps=[item.description for item in result.gaps],
                warnings=list(result.limitations),
            )
        raise DispatchError("forbidden", "unregistered finance child role")

    def _tool(self, action: dict[str, Any]) -> dict[str, Any]:
        name = action.get("tool_name")
        arguments = _as_object(action.get("arguments"))
        if name == "article.list":
            return {"tool": name, "articles": self.corpus.list_articles(**arguments)}
        if name == "article.search":
            return {"tool": name, "result": self.corpus.search(**arguments)}
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
