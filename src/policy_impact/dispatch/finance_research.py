"""Source-bound research roles on the shared durable finance action loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace

from pydantic import ValidationError

from policy_impact.runtime.research_contracts import (
    AnalystResearchResult,
    ResearchResult,
    ReviewPolicy,
    ReviewReport,
    ReviewReportV2,
    ReviewRequest,
    TaskResult,
)

from .contracts import DispatchError, OutputContract, Spawn, Submission, Wait
from .finance_agent import (
    FinanceH2Domain,
    _as_object,
    _fit_context,
)
from .finance_anchor_repair import ResearchAnchorRepair
from .finance_personal_sources import personal_source_read
from .finance_public_sources import public_source_read
from .finance_review_status import build_review_status
from .finance_root import FinanceStep
from .finance_rules import rules_for
from .finance_tool_contracts import MARKET_TOOLS, PERSONAL_TOOLS


def _has_contract(brief, name, version):
    return _as_object(brief.get("output_contract")) == {"name": name, "version": version}


class FinanceH2ResearchDomain(FinanceH2Domain):
    """Specialize research context and delivery, not scheduling or permissions."""

    async def __call__(self, context, capabilities):
        execution = dict(context.state.execution)
        execution["allowed_tools"] = list(context.brief.get("allowed_tools") or [])
        execution.setdefault("source_material_refs", list(execution.get("material_refs") or []))
        assignment = _as_object(execution.get("research_assignment"))
        assigned_task = _as_object(assignment.get("research_task"))
        review_policy = context.brief.get("review_policy") or assigned_task.get("review_policy")
        rules = rules_for(capabilities)
        uses_profiles = rules.has_profiles
        if (uses_profiles and context.brief.get("role") == "analyst"
                and context.brief.get("role_version") == "2.0.0"
                and _has_contract(context.brief, "finance.analyst_result", "2")):
            defaults = {"max_rounds": 5 if rules.portable_profiles else 3}
            review_policy = ReviewPolicy.model_validate(review_policy or defaults, strict=True).model_dump(
                mode="json", exclude_none=True,
            )
        execution["research_brief"] = {
            key: context.brief[key]
            for key in ("objective", "role", "role_version", "depth", "success_criteria", "output_contract",
                        "task_instructions", "review_request", "review_mode", "review_options")
            if key in context.brief
        }
        if review_policy is not None:
            execution["research_brief"]["review_policy"] = review_policy
        if execution.get("follow_up_context"):
            execution["research_brief"]["objective"] = context.query
        context = replace(context, state=context.state.model_copy(update={"execution": execution}))
        workflow = _as_object(execution.get("review_workflow"))
        if workflow.get("status") == "dispatch_pending":
            return await self._dispatch_review(context, context.state, capabilities)
        return await super().__call__(context, capabilities)

    @staticmethod
    async def _decide(messages, capabilities):
        if not messages:
            return await FinanceH2Domain._decide(
                messages, capabilities, final_output_model=ResearchResult,
                final_repair_factory=lambda action: ResearchAnchorRepair.prepare(
                    action, rules=rules_for(capabilities),
                ),
            )
        try:
            user = _as_object(json.loads(messages[-1]["content"]))
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            user = {}
        brief = _as_object(user.get("research_brief"))
        role = brief.get("role", user.get("role"))
        role_version = brief.get("role_version", user.get("role_version"))
        output_contract = brief.get("output_contract", user.get("output_contract"))
        review_request = brief.get("review_request", user.get("review_request"))
        uses_profiles = rules_for(capabilities).has_profiles
        if (uses_profiles and role == "verifier" and review_request
                and output_contract in ({"name": "finance.review_report", "version": "1"},
                                        {"name": "finance.review_report", "version": "2"})):
            model = ReviewReportV2 if output_contract["version"] == "2" else ReviewReport
            return await FinanceH2Domain._decide(messages, capabilities, final_output_model=model)
        if (uses_profiles and role == "analyst" and role_version == "2.0.0"
                and output_contract == {"name": "finance.analyst_result", "version": "2"}):
            return await FinanceH2Domain._decide(
                messages, capabilities, final_output_model=AnalystResearchResult,
                final_repair_factory=lambda action: ResearchAnchorRepair.prepare(
                    action, rules=rules_for(capabilities),
                ),
            )
        if output_contract == {"name": "finance.task_result", "version": "1"}:
            return await FinanceH2Domain._decide(
                messages, capabilities, final_output_model=TaskResult,
                final_repair_factory=lambda action: ResearchAnchorRepair.prepare(
                    action, rules=rules_for(capabilities),
                ),
            )
        return await FinanceH2Domain._decide(
            messages, capabilities, final_output_model=ResearchResult,
            final_repair_factory=lambda action: ResearchAnchorRepair.prepare(action, rules=rules_for(capabilities)),
        )

    async def _messages(self, query, state, observations, capabilities):
        messages = await super()._messages(query, state, observations, capabilities)
        user = json.loads(messages[-1]["content"])
        execution = _as_object(state.execution)
        brief = _as_object(execution.get("research_brief"))
        assignment = _as_object(execution.get("research_assignment"))
        role = brief.get("role", "analyst")
        rules = rules_for(capabilities)
        analyst_v2 = (rules.has_profiles and role == "analyst" and brief.get("role_version") == "2.0.0"
                      and _has_contract(brief, "finance.analyst_result", "2"))
        messages[0] = {"role": "system", "content": rules.system(user.get("task_budget"), role=role)}
        if self.public_sources is not None and any(
            item.get("name") in {"web.search", "web.open"} for item in user.get("tool_catalog", [])
        ):
            messages[0]["content"] += " " + rules.text("public-source")
        if self.market_sources is not None and any(
            item.get("name") in MARKET_TOOLS for item in user.get("tool_catalog", [])
        ):
            messages[0]["content"] += " " + rules.text("market-source")
        if self.personal_sources is not None and any(
            item.get("name") in PERSONAL_TOOLS for item in user.get("tool_catalog", [])
        ):
            messages[0]["content"] += " " + rules.text("personal-source")
        # Keep each trusted handle beside its sole payload, including omitted sources.
        materials = user.get("materials", [])
        for material in materials:
            payload = material.get("payload")
            if isinstance(payload, str):
                try:
                    material["payload"] = json.loads(payload)
                except json.JSONDecodeError:
                    material["expansion_tool"] = "material.get"
            elif material.get("unavailable"):
                material.update(payload_omitted=True, expansion_tool="material.get")
        user.update({
            "objective": brief.get("objective", query),
            "role": role,
            "role_version": brief.get("role_version"),
            "depth": brief.get("depth"),
            "task": assignment.get("research_task") or assignment.get("task") or query,
            "task_instructions": brief.get("task_instructions"),
            "success_criteria": brief.get("success_criteria", []),
            "output_contract": brief.get("output_contract"),
            "review_policy": brief.get("review_policy"),
            "review_mode": brief.get("review_mode"),
            "review_options": brief.get("review_options"),
            "review_request": brief.get("review_request"),
            "review_workflow": _as_object(state.execution.get("review_workflow")),
            "output_json_schema_scope": "final.output",
            "output_json_schema": (
                (ReviewReportV2 if _has_contract(brief, "finance.review_report", "2")
                 else ReviewReport).model_json_schema()
                if (rules.has_profiles and role == "verifier" and brief.get("review_request")
                    and (_has_contract(brief, "finance.review_report", "1")
                         or _has_contract(brief, "finance.review_report", "2")))
                else AnalystResearchResult.model_json_schema()
                if analyst_v2
                else TaskResult.model_json_schema()
                if _has_contract(brief, "finance.task_result", "1")
                else ResearchResult.model_json_schema()
            ),
            "material_refs": list(execution.get("material_refs") or []),
            "materials": materials,
        })
        return _fit_context(messages[0]["content"], user, capabilities)

    @staticmethod
    def _source_text(value):
        if not isinstance(value, dict) or value.get("ok") is False:
            return None
        schema = value.get("schema_version")
        if schema in {"finance.h2.material.v1", "finance.h2.tool_result.v1"}:
            return FinanceH2ResearchDomain._source_text(value.get("payload"))
        if schema == "finance.h2.analyst_result.v1":
            if value.get("validation_status") == "pending" or value.get("accepted_evidence_refs") == []:
                return None
            return FinanceH2ResearchDomain._source_text(value.get("result"))
        if schema in {"finance.research_result.v1", "finance.task_result.v1"}:
            try:
                result = (TaskResult if schema == "finance.task_result.v1" else ResearchResult).model_validate(
                    value, strict=True,
                )
            except ValidationError:
                return None
            if result.evidence_links:
                return result.content + "\n" + (result.candidate_answer or "")
            return None
        if public_source_read(value, None) is not None or personal_source_read(value, None) is not None:
            return value["text"]
        article = _as_object(value.get("article")) or value
        content = value.get("content")
        if (isinstance(article.get("article_id"), str) and isinstance(content, str)
                and content.strip() and value.get("tool") in (None, "article.read")):
            return content
        return None

    @staticmethod
    def _source_reads(value, reader_task_id):
        if not isinstance(value, dict) or value.get("ok") is False:
            return []
        schema = value.get("schema_version")
        if schema in {"finance.h2.material.v1", "finance.h2.tool_result.v1"}:
            return FinanceH2ResearchDomain._source_reads(
                value.get("payload"), value.get("source_task_id") or reader_task_id,
            )
        if schema == "finance.h2.analyst_result.v1":
            if value.get("validation_status") != "complete":
                return []
            accepted = set(value.get("accepted_evidence_refs") or [])
            return [read for ref, reads in _as_object(value.get("source_reads")).items()
                    if ref in accepted for read in reads if isinstance(read, dict)]
        public_read = public_source_read(value, reader_task_id)
        if public_read is not None:
            return [public_read]
        personal_read = personal_source_read(value, reader_task_id)
        if personal_read is not None:
            return [personal_read]
        article = _as_object(value.get("article"))
        if (value.get("tool") != "article.read" or not isinstance(article.get("article_id"), str)
                or not isinstance(value.get("content"), str) or not value["content"].strip()):
            return []
        return [{
            "reader_task_id": reader_task_id,
            "article": {key: article[key] for key in (
                "article_id", "author", "title", "published_at", "current_hash",
            ) if key in article},
            **{key: value[key] for key in (
                "start_line", "end_line", "line_count", "has_more",
            ) if key in value},
        }]

    async def _dispatch_review(self, context, state, capabilities):
        execution = dict(state.execution)
        workflow = dict(_as_object(execution.get("review_workflow")))
        spawn_payload = _as_object(workflow.get("prepared_spawn"))
        if not spawn_payload:
            raise DispatchError("checkpoint_invalid", "prepared review dispatch is missing")
        try:
            receipt = await capabilities.tool("agent.spawn", spawn_payload)
        except DispatchError as exc:
            if exc.retryable or exc.code in {"cancelled", "stale_lease"}:
                raise
            workflow.pop("prepared_spawn", None)
            workflow.update(status="review_failed", failure_code=exc.code)
            execution["review_workflow"] = workflow
            return FinanceStep(status="continuing", state=state.model_copy(update={
                "execution": execution, "revision": state.revision + 1,
            }))
        task = _as_object(receipt.get("data"))
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            workflow.pop("prepared_spawn", None)
            workflow.update(status="review_failed", failure_code="invalid_spawn_receipt")
            execution["review_workflow"] = workflow
            return FinanceStep(status="continuing", state=state.model_copy(update={
                "execution": execution, "revision": state.revision + 1,
            }))
        request = _as_object(spawn_payload.get("review_request"))
        workflow.pop("prepared_spawn", None)
        workflow.update(
            status="waiting", pending_task_id=task_id,
            rounds=request.get("round_number", workflow.get("rounds", 0)),
            pending_draft_ref=request.get("draft_ref"),
            pending_revision=request.get("draft_revision"),
        )
        execution["review_workflow"] = workflow
        next_state = state.model_copy(update={
            "phase": "review", "pending_steps": ["review-" + task_id],
            "execution": execution, "revision": state.revision + 1,
        })
        return FinanceStep(status="waiting", state=next_state,
                           wait=Wait(task_ids=[task_id], mode="all", timeout_ms=60000))

    @staticmethod
    def _review_task_instructions(request: ReviewRequest) -> str:
        scopes = ", ".join(request.scope)
        return (f"Review immutable draft revision {request.draft_revision}; round {request.round_number}. "
                f"Check only the registered scope: {scopes}. Use the supplied targets and authorized sources.")

    async def _prepare_review(self, context, state, *, result, candidate, candidate_ref, known_refs,
                              evidence_refs, capabilities, policy, workflow):
        targets = [target.model_dump(mode="json") for target in candidate.review_targets]
        target_refs = {ref for target in candidate.review_targets for ref in target.evidence_refs}
        if not target_refs <= known_refs:
            raise DispatchError("contract_error", "review target contains an unauthorized evidence ref")
        source_refs = list(dict.fromkeys([
            *target_refs,
            *evidence_refs,
            *(ref for ref in state.execution.get("source_material_refs", []) if ref in known_refs),
        ]))
        if len(source_refs) > 64:
            raise DispatchError("contract_error", "review task has too many source references")
        draft_body = {"result": result.model_dump(mode="json"), "review_targets": targets}
        digest = hashlib.sha256(json.dumps(
            draft_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        previous_ref = workflow.get("draft_ref")
        if digest == workflow.get("draft_sha256") and isinstance(previous_ref, str):
            draft_ref = previous_ref
            revision = int(workflow.get("draft_revision", 1))
        else:
            revision = int(workflow.get("draft_revision", 0)) + 1
            draft_ref = await capabilities.store_context({
                "schema_version": "finance.h2.review_draft.v1",
                "draft_revision": revision,
                **draft_body,
            })
        round_number = int(workflow.get("rounds", 0)) + 1
        request = ReviewRequest(
            schema_version="finance.review_request.v1", draft_ref=draft_ref,
            draft_revision=revision, round_number=round_number,
            scope=list(policy.scope), targets=candidate.review_targets,
        )
        parent_task_id = str(context.brief.get("task_id") or "unknown")
        dispatch_key = "review-" + hashlib.sha256(
            f"{parent_task_id}:{draft_ref}:{round_number}".encode()
        ).hexdigest()[:32]
        request_budget = None
        if context.brief.get("budget", {}).get("policy") != "fixed":
            from .finance_tool_contracts import legacy_delegate_budget
            request_budget = legacy_delegate_budget("verifier")
        spawn = Spawn(
            dispatch_key=dispatch_key,
            role="verifier", role_version="1.0.0",
            objective=f"Independently review Analyst draft revision {revision} against authorized evidence.",
            task_instructions=policy.instructions or self._review_task_instructions(request),
            success_criteria=[
                "Return a version-bound ReviewReport for every assigned target.",
                "Bind evidence to explicitly authorized source references and disclose unchecked scope.",
            ],
            input_refs=list(dict.fromkeys([draft_ref, *source_refs])),
            output_contract=OutputContract(name="finance.review_report",
                                           version="2" if rules_for(capabilities).review_verdicts else "1"),
            review_request=request,
            requested_budget=request_budget,
        )
        next_workflow = {
            **workflow,
            "status": "dispatch_pending",
            "rounds": int(workflow.get("rounds", 0)),
            "draft_ref": draft_ref,
            "draft_revision": revision,
            "draft_sha256": digest,
            "review_scope": list(policy.scope),
            "review_targets": targets,
            "candidate_ref": candidate_ref,
            "prepared_spawn": spawn.model_dump(mode="json", exclude_none=True),
            "report_refs": list(workflow.get("report_refs") or []),
        }
        execution = dict(state.execution)
        execution["review_workflow"] = next_workflow
        execution["material_refs"] = list(dict.fromkeys([
            *execution.get("material_refs", []), draft_ref,
        ]))
        next_state = state.model_copy(update={
            "phase": "review", "pending_steps": [],
            "candidate_answer": {"artifact_ref": draft_ref, "candidate_ref": candidate_ref,
                                 "status": "awaiting_review"},
            "execution": execution, "revision": state.revision + 1,
        })
        return FinanceStep(status="continuing", state=next_state)

    async def _final_review(self, context, state, output, capabilities):
        brief = _as_object(state.execution.get("research_brief"))
        model = ReviewReportV2 if _has_contract(brief, "finance.review_report", "2") else ReviewReport
        try:
            report = model.model_validate(output, strict=True)
        except (TypeError, ValidationError) as exc:
            raise DispatchError("contract_error", "Verifier requires a valid ReviewReport") from exc
        request = ReviewRequest.model_validate(brief.get("review_request"), strict=True)
        if (report.reviewed_draft_ref != request.draft_ref
                or report.reviewed_revision != request.draft_revision):
            raise DispatchError("contract_error", "ReviewReport draft binding does not match its task", details={
                "validation_stage": "review.binding", "validation_warnings": ["copy_review_request_draft_ref_and_revision"],
            })
        allowed_targets = {target.target_id for target in request.targets}
        warnings = []
        if set(report.checked_target_ids) != allowed_targets:
            warnings.append("review_target_coverage_mismatch")
        if any(finding.target_id is not None and finding.target_id not in allowed_targets
               for finding in report.findings):
            raise DispatchError("contract_error", "ReviewReport references an unknown target", details={
                "validation_stage": "review.targets", "validation_warnings": ["use_only_review_request_target_ids"],
            })
        if set(report.checked_scope) - set(request.scope):
            raise DispatchError("contract_error", "ReviewReport checked outside its authorized scope", details={
                "validation_stage": "review.scope", "validation_warnings": ["use_only_review_request_scope"],
            })
        if isinstance(report, ReviewReportV2) and set(report.checked_scope) != set(request.scope):
            warnings.append("review_scope_coverage_mismatch")

        execution = dict(state.execution)
        known_refs = {
            ref for key in ("material_refs", "evidence_refs")
            for ref in execution.get(key, []) if isinstance(ref, str)
        }
        known_refs.update(context.brief.get("input_refs", []))
        known_refs = self._usable_refs(execution, known_refs)
        report_evidence = {ref for finding in report.findings for ref in finding.evidence_refs}
        if report.reviewed_draft_ref not in known_refs:
            warnings.append("review_draft_not_authorized")
        for ref in sorted(report_evidence - known_refs):
            warnings.append(f"review_unknown_evidence_ref:{ref}")
        source_texts = {}
        for ref in sorted(report_evidence & known_refs):
            if ref == request.draft_ref:
                warnings.append("review_draft_cannot_be_evidence")
                continue
            try:
                raw = await capabilities.load_context(ref)
                source = json.loads(raw)
            except (DispatchError, TypeError, ValueError, json.JSONDecodeError):
                warnings.append(f"review_source_unavailable:{ref}")
                continue
            text = self._source_text(source)
            if text is None:
                warnings.append(f"review_source_unbound:{ref}")
            else:
                source_texts[ref] = text
        invalid_quotes = set()
        for finding in report.findings:
            for quote in finding.quotes:
                if quote.evidence_ref not in source_texts or quote.text not in source_texts[quote.evidence_ref]:
                    invalid_quotes.add(quote.evidence_ref)
                    warnings.append(f"review_quote_mismatch:{quote.evidence_ref}")
        accepted_refs = sorted(report_evidence.intersection(source_texts).difference(invalid_quotes))
        warnings = list(dict.fromkeys(warnings))
        if warnings and isinstance(report, ReviewReportV2):
            raise DispatchError("contract_error", "ReviewReport failed source, quote or coverage validation", details={
                "validation_stage": "review.evidence", "validation_warnings": warnings[:12],
            })
        validation_status = "complete" if not warnings else "incomplete"
        report_ref = await capabilities.store_context({
            "schema_version": "finance.h2.review_report.v1",
            "report": report.model_dump(mode="json"),
            "validation_status": validation_status,
            "validation_warnings": warnings,
            "accepted_evidence_refs": accepted_refs,
        })
        status = "partial" if warnings or report.unchecked_scope else "succeeded"
        return FinanceStep(status=status, state=state.model_copy(update={
            "phase": "review_delivery", "revision": state.revision + 1,
            "candidate_answer": {"artifact_ref": report_ref, "status": "ready"},
            "delivery": {"status": "ready", "review_validation": validation_status,
                         "warnings": warnings},
        }), submission=Submission(
            result_key="finance-review-" + str(context.brief.get("task_id")),
            status=status, full_ref=report_ref, summary=f"Review of draft revision {report.reviewed_revision}",
            evidence_refs=[{"evidence_ref": ref} for ref in accepted_refs],
            gaps=[*report.unchecked_scope, *report.limitations], warnings=warnings,
        ))

    async def _final(self, context, state, output, capabilities, state_update_warnings=None):
        try:
            return await self._final_impl(context, state, output, capabilities, state_update_warnings)
        except DispatchError as exc:
            if (not rules_for(capabilities).review_verdicts or exc.code != "contract_error"
                    or not exc.details):
                raise
            execution = dict(state.execution)
            count = int(execution.get("domain_contract_corrections", 0))
            if count >= 5:
                raise DispatchError("contract_error", "domain correction limit reached", details=exc.details) from exc
            from .diagnostics import safe_text

            details = {key: [safe_text(item)[:320] for item in value[:12]] for key, value in exc.details.items()
                       if key in {"missing_finding_ids", "unknown_finding_ids", "validation_warnings"}
                       and isinstance(value, list)}
            observation = {
                "schema_version": "finance.h2.action_observation.v1", "tool_name": "final", "status": "failed",
                "error": {"code": "contract_error", "validation_stage": exc.details.get("validation_stage", "review.dispositions"),
                          "message": safe_text(str(exc))[:500], **details},
                "instruction": "Correct the listed fields using trusted task state and authorized sources. "
                               "Only review_workflow.finding_ids belong in formal dispositions. "
                               "Copy source quotes exactly. No research result has been accepted by this failed submission.",
                "correction_number": count + 1, "correction_limit": 5,
            }
            ref = await capabilities.store_context(observation)
            execution["domain_contract_corrections"] = count + 1
            execution["action_observation_refs"] = [*execution.get("action_observation_refs", []), ref]
            return FinanceStep(status="continuing", state=state.model_copy(update={
                "execution": execution, "revision": state.revision + 1,
            }))

    async def _final_impl(self, context, state, output, capabilities, state_update_warnings=None):
        brief = _as_object(state.execution.get("research_brief"))
        if (rules_for(capabilities).has_profiles and brief.get("role") == "verifier"
                and brief.get("review_request") and (_has_contract(brief, "finance.review_report", "1")
                                                     or _has_contract(brief, "finance.review_report", "2"))):
            return await self._final_review(context, state, output, capabilities)
        analyst_v2 = (rules_for(capabilities).has_profiles and brief.get("role") == "analyst"
                      and brief.get("role_version") == "2.0.0"
                      and _has_contract(brief, "finance.analyst_result", "2"))
        analyst_output = None
        general_output = None
        if analyst_v2:
            try:
                analyst_output = AnalystResearchResult.model_validate(output, strict=True)
                base_output = analyst_output.model_dump(mode="json", exclude={
                    "review_targets", "review_decision", "review_decision_reason", "review_dispositions",
                })
                base_output["schema_version"] = "finance.research_result.v1"
                result = ResearchResult.model_validate(base_output, strict=True)
            except (TypeError, ValidationError) as exc:
                raise DispatchError("contract_error", "Analyst v2 requires a valid versioned research result") from exc
        elif _has_contract(brief, "finance.task_result", "1"):
            try:
                general_output = TaskResult.model_validate(output, strict=True)
                base_output = general_output.model_dump(mode="json")
                base_output["schema_version"] = "finance.research_result.v1"
                result = ResearchResult.model_validate(base_output, strict=True)
            except (TypeError, ValidationError) as exc:
                raise DispatchError("contract_error", "general tasks require a valid TaskResult") from exc
        else:
            try:
                result = ResearchResult.model_validate(output, strict=True)
            except (TypeError, ValidationError) as exc:
                raise DispatchError("contract_error", "finance research requires a valid ResearchResult") from exc

        # Persist the complete candidate before inspecting any source bindings.
        original = result.model_dump(mode="json")
        if general_output is not None:
            original = general_output.model_dump(mode="json")
        candidate_ref = await capabilities.store_context({
            "schema_version": "finance.h2.analyst_candidate.v2" if analyst_v2 else "finance.h2.analyst_result.v1",
            "result": original,
            **({
                "review_targets": [target.model_dump(mode="json") for target in analyst_output.review_targets],
                "review_decision": analyst_output.review_decision,
                "review_decision_reason": analyst_output.review_decision_reason,
                "review_dispositions": [item.model_dump(mode="json")
                                        for item in analyst_output.review_dispositions],
            } if analyst_v2 else {}),
            "validation_status": "pending", "validation_warnings": [],
        })
        execution = dict(state.execution)
        known_refs = {
            ref for key in ("material_refs", "evidence_refs")
            for ref in execution.get(key, []) if isinstance(ref, str)
        }
        for article in state.article_states:
            article = _as_object(article)
            known_refs.update(
                ref for ref in (
                    article.get("material_ref"), article.get("result_ref"),
                    *(article.get("material_refs") or []), *(article.get("result_refs") or []),
                ) if isinstance(ref, str)
            )
        known_refs = self._usable_refs(execution, known_refs)
        evidence_refs = list(dict.fromkeys(
            ref for link in result.evidence_links for ref in link.evidence_refs
        ))
        validation_warnings = []
        for field, refs in (
            ("evidence_refs", evidence_refs),
            ("material_disposition", [item.material_ref for item in result.material_disposition]),
            ("supporting_artifact_refs", result.supporting_artifact_refs),
            ("gaps", [ref for gap in result.gaps for ref in gap.material_refs]),
        ):
            validation_warnings.extend(
                f"research_{field}_unknown_ref:{ref}" for ref in sorted(set(refs) - known_refs)
            )

        source_texts = {}
        source_reads = {}
        for evidence_ref in evidence_refs:
            if evidence_ref not in known_refs:
                continue
            try:
                if evidence_ref.startswith("res_"):
                    raw = await self._retrieved_result(state, evidence_ref, capabilities)
                    if raw is None:
                        validation_warnings.append(
                            f"research_source_inconclusive:{evidence_ref}:full_source_not_verified"
                        )
                        continue
                else:
                    raw = await capabilities.load_context(evidence_ref)
            except DispatchError as exc:
                if exc.retryable or exc.code not in {"not_found", "forbidden"}:
                    raise
                validation_warnings.append(f"research_source_unavailable:{evidence_ref}:{exc.code}")
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                validation_warnings.append(f"research_source_inconclusive:{evidence_ref}:unreadable_content")
                continue
            text = self._source_text(payload)
            if text is None:
                validation_warnings.append(f"research_source_unbound:{evidence_ref}")
                continue
            source_texts[evidence_ref] = text
            source_reads[evidence_ref] = self._source_reads(payload, context.brief.get("task_id"))
        invalid_quotes = set()
        for link in result.evidence_links:
            for quote in link.quotes:
                text = source_texts.get(quote.evidence_ref)
                if text is not None and quote.text not in text:
                    invalid_quotes.add(quote.evidence_ref)
                    validation_warnings.append(f"research_quote_mismatch:{quote.evidence_ref}")
        evidence_refs = [ref for ref in evidence_refs if ref in source_texts and ref not in invalid_quotes]
        source_reads = {ref: source_reads[ref] for ref in evidence_refs if source_reads.get(ref)}
        validation_warnings = list(dict.fromkeys(validation_warnings))
        incomplete = bool(validation_warnings or state_update_warnings)
        metadata_status = (
            "incomplete_metadata" if incomplete
            else execution.get("metadata_status", "incomplete_metadata")
        )

        review_summary = None
        review_incomplete = False
        review_warnings = []
        if analyst_v2:
            policy = ReviewPolicy.model_validate(brief.get("review_policy") or {}, strict=True)
            workflow = dict(_as_object(execution.get("review_workflow")))
            if workflow.get("status") == "waiting" and workflow.get("pending_task_id"):
                return FinanceStep(status="waiting", state=state, wait=Wait(
                    task_ids=[workflow["pending_task_id"]], mode="all", timeout_ms=60000,
                ))
            if workflow.get("status") == "report_pending_read":
                observation_ref = await capabilities.store_context({
                    "tool_name": "final", "status": "blocked",
                    "error": {
                        "code": "review_report_unread",
                        "message": "Read pending_result_ref with result.get(view=full), following every page, "
                                   "before revising or delivering the reviewed draft.",
                    },
                    "result_ref": workflow.get("pending_result_ref"),
                })
                execution["action_observation_refs"] = [
                    *execution.get("action_observation_refs", []), observation_ref,
                ]
                return FinanceStep(status="continuing", state=state.model_copy(update={
                    "execution": execution, "revision": state.revision + 1,
                }))
            rounds = int(workflow.get("rounds", 0))
            report_ready = workflow.get("status") == "report_ready"
            finding_ids = set(workflow.get("finding_ids") or [])
            dispositions = {item.finding_id: item for item in analyst_output.review_dispositions}
            if finding_ids != set(dispositions):
                raise DispatchError("contract_error", "every received ReviewReport finding needs one disposition",
                                    details={"missing_finding_ids": sorted(finding_ids - set(dispositions)),
                                             "unknown_finding_ids": sorted(set(dispositions) - finding_ids)})
            draft_body = {
                "result": result.model_dump(mode="json"),
                "review_targets": [target.model_dump(mode="json") for target in analyst_output.review_targets],
            }
            draft_digest = hashlib.sha256(json.dumps(
                draft_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            same_reviewed_draft = report_ready and draft_digest == workflow.get("draft_sha256")
            decision = analyst_output.review_decision
            strict_review = rules_for(capabilities).review_verdicts
            unresolved = [item.finding_id for item in dispositions.values() if item.disposition == "unresolved"]
            raw_approvals = workflow.get("approved_drafts")
            approved_drafts = ([item for item in raw_approvals if isinstance(item, Mapping)]
                               if isinstance(raw_approvals, list) else [])
            approval = next((item for item in reversed(approved_drafts)
                             if item.get("draft_sha256") == draft_digest
                             and item.get("review_scope") == list(policy.scope)
                             and item.get("verdict") == "passed"
                             and not item.get("blocking_finding_ids")), None) if strict_review else None
            if same_reviewed_draft and workflow.get("verdict") != "passed":
                approval = None
            if (strict_review and approval is None and same_reviewed_draft
                    and workflow.get("verdict") == "passed"):
                approval = {
                    "draft_sha256": workflow.get("draft_sha256"),
                    "draft_ref": workflow.get("reviewed_draft_ref") or workflow.get("draft_ref"),
                    "draft_revision": workflow.get("reviewed_revision") or workflow.get("draft_revision"),
                    "report_ref": workflow.get("report_ref"),
                    "report_material_ref": workflow.get("report_material_ref"),
                    "review_scope": list(policy.scope),
                    "verdict": "passed",
                    "verdict_reason": workflow.get("verdict_reason"),
                    "blocking_finding_ids": workflow.get("blocking_finding_ids", []),
                }
            approved = strict_review and approval is not None and not unresolved
            must_request = policy.mode == "required" and (
                not approved if strict_review else not report_ready
            )
            should_request = policy.mode != "off" and (
                must_request or (decision == "request" and not (strict_review and approved))
            )
            depth_exhausted = context.brief.get("depth", 1) >= 2
            if should_request and not depth_exhausted and rounds < min(policy.max_rounds, 5):
                if not analyst_output.review_targets:
                    raise DispatchError("contract_error", "required article review needs at least one target", details={
                        "validation_stage": "review.targets", "validation_warnings": ["provide_at_least_one_review_target"],
                    })
                return await self._prepare_review(
                    context, state, result=result, candidate=analyst_output,
                    candidate_ref=candidate_ref, known_refs=known_refs,
                    evidence_refs=evidence_refs, capabilities=capabilities,
                    policy=policy, workflow=workflow,
                )
            if should_request and depth_exhausted:
                review_status = "review_failed"
                review_incomplete = True
                review_warnings.append("review_depth_limit_reached")
            elif should_request:
                review_status = "round_limit_reached"
                review_incomplete = True
                review_warnings.append("review_round_limit_reached")
            elif policy.mode == "off":
                review_status = "not_requested"
                if decision == "request":
                    review_incomplete = True
                    review_warnings.append("review_request_blocked_by_task_policy")
            elif strict_review and approved:
                review_status = "passed"
            elif report_ready:
                if strict_review:
                    review_status = "issues_open"
                    review_incomplete = True
                    review_warnings.append("final_review_not_passed")
                elif same_reviewed_draft:
                    unresolved = [item.finding_id for item in dispositions.values()
                                  if item.disposition == "unresolved"]
                    review_status = "issues_open" if unresolved else "reviewed_with_resolutions"
                    if unresolved:
                        review_incomplete = True
                        review_warnings.append("review_findings_unresolved")
                else:
                    if policy.mode == "required":
                        review_status = "round_limit_reached" if rounds >= min(policy.max_rounds, 5) else "review_failed"
                        review_incomplete = True
                    else:
                        review_status = "skipped_with_reason"
                        review_incomplete = True
                    review_warnings.append("final_draft_differs_from_last_review")
            elif workflow.get("status") in {"review_failed", "report_invalid"}:
                review_status = "review_failed"
                review_incomplete = True
                review_warnings.append("independent_review_failed")
            elif decision == "skip" and policy.mode == "auto":
                review_status = "skipped_with_reason"
            else:
                review_status = "review_failed"
                review_incomplete = True
                review_warnings.append("required_review_not_completed")
            review_binding = approval if strict_review and approved else workflow
            review_summary = {
                "policy": policy.model_dump(mode="json"),
                "status": review_status,
                "rounds": rounds,
                "draft_ref": review_binding.get("draft_ref", workflow.get("draft_ref")),
                "draft_revision": review_binding.get("draft_revision", workflow.get("draft_revision")),
                "report_ref": review_binding.get("report_ref"),
                "reused_approval": bool(approved and not same_reviewed_draft),
                "report_refs": list(workflow.get("report_refs") or []),
                "failure_code": workflow.get("failure_code"),
                "decision_reason": analyst_output.review_decision_reason,
                "dispositions": [item.model_dump(mode="json")
                                 for item in analyst_output.review_dispositions],
                **({"verdict": review_binding.get("verdict", workflow.get("verdict")),
                    "verdict_reason": review_binding.get("verdict_reason", workflow.get("verdict_reason")),
                    "blocking_finding_ids": review_binding.get(
                        "blocking_finding_ids", workflow.get("blocking_finding_ids", []),
                    )} if strict_review else {}),
            }
            workflow["status"] = review_status
            if strict_review and approved and approval is not None:
                workflow.update(
                    draft_ref=approval.get("draft_ref"),
                    draft_revision=approval.get("draft_revision"),
                    draft_sha256=draft_digest,
                    reviewed_draft_ref=approval.get("draft_ref"),
                    reviewed_revision=approval.get("draft_revision"),
                    report_ref=approval.get("report_ref"),
                    report_material_ref=approval.get("report_material_ref"),
                    verdict="passed",
                    verdict_reason=approval.get("verdict_reason"),
                    blocking_finding_ids=[],
                )
                workflow.pop("failure_code", None)
            execution["review_workflow"] = workflow

        delivery_artifact = {
            "schema_version": "finance.h2.analyst_result.v1",
            "result": original, "candidate_ref": candidate_ref,
            "validation_status": "complete", "validation_warnings": validation_warnings,
            "accepted_evidence_refs": evidence_refs, "metadata_status": metadata_status,
            "source_reads": source_reads,
            **({"review": review_summary,
                "review_targets": [target.model_dump(mode="json") for target in analyst_output.review_targets]}
               if analyst_v2 else {}),
        }
        if analyst_v2:
            delivery_artifact["review_status"] = build_review_status(delivery_artifact)
        ref = await capabilities.store_context(delivery_artifact)
        source_ref = await capabilities.store_context({
            "schema_version": "finance.h2.source_lineage.v1", "bindings": source_reads,
        }) if source_reads else None
        warnings = list(dict.fromkeys([
            *result.limitations, *(state_update_warnings or []), *validation_warnings, *review_warnings,
        ]))
        execution.pop("pending_action", None)
        execution.pop("prepared_action_ref", None)
        execution["metadata_status"] = metadata_status
        execution["validation_warnings"] = validation_warnings[-40:]
        article_states = [dict(item) for item in state.article_states]
        for article in article_states:
            article_refs = {
                article.get("material_ref"), article.get("result_ref"),
                *(article.get("material_refs") or []), *(article.get("result_refs") or []),
            }
            if article_refs.intersection(evidence_refs):
                article["use_status"] = "used"
        status = "partial" if incomplete or review_incomplete else "succeeded"
        next_state = state.model_copy(update={
            "phase": "delivery", "pending_steps": [],
            "candidate_answer": {"artifact_ref": ref, "candidate_ref": candidate_ref, "status": "ready"},
            "article_states": article_states, "execution": execution,
            "delivery": {
                "status": "ready", "evidence_refs": evidence_refs,
                "metadata_status": metadata_status,
                **({"review": review_summary} if analyst_v2 else {}),
                "warnings": warnings[-20:],
            },
            "revision": state.revision + 1,
        })
        return FinanceStep(status=status, state=next_state, submission=Submission(
            result_key="finance-analyst-" + str(next_state.revision), status=status, full_ref=ref,
            summary=result.content[:500],
            evidence_refs=[{"evidence_ref": item} for item in evidence_refs],
            sections={**({"finance_sources": source_ref} if source_ref else {}),
                      **({"finance_review": workflow["report_material_ref"]}
                         if analyst_v2 and workflow.get("report_material_ref") else {})},
            gaps=[item.description for item in result.gaps], warnings=warnings,
        ))


__all__ = ["FinanceH2ResearchDomain"]
