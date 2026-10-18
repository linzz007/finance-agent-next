"""Runtime-owned grants, visible research inputs and parent result ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from uuid import uuid4

from policy_impact.domains.finance.article_gateway import IMAGE_LINE
from policy_impact.domains.finance.research_records import SCHEMA, ID_RE, checksum
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.research_budget import CURRENT, ResearchLimits, execution_scope
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.plugin_config import read_plugin_prompt_modules
from policy_impact.runtime.research_contracts import ResearchTask


class ResearchInputOverflow(ValueError):
    def __init__(self, estimated_tokens, input_limit, materials):
        super().__init__("context_budget_exceeded: full input does not fit; main Agent must coordinate "
                         "explicit range tasks and article-level integration")
        self.delegate_details = {"code": "research_input_overflow", "estimated_tokens": estimated_tokens,
                                 "input_limit": input_limit, "materials": materials}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk(item)


def _source_binding(fragment, origin):
    return {**{key: getattr(fragment, key) for key in (
        "evidence_id", "source_type", "source_ref", "article_id", "author", "published_at",
        "content_hash", "line_start", "line_end", "source_url",
    )}, "origin": origin}


def _input_batches(messages, response):
    return response.metadata.get("actual_model_inputs") or [
        {"response_id": response.response_id, "messages": messages, "input_kind": "generation"}]


@dataclass
class PreparedResearch:
    service: "ResearchExecution"
    task_id: str
    task: ResearchTask
    bindings: list[dict]
    payloads: dict
    dependencies: list[dict]
    sources: dict = field(default_factory=dict)
    input_versions: dict = field(default_factory=dict)
    visible: set = field(default_factory=set)
    input_calls: list[dict] = field(default_factory=list)

    def remember(self, ref, payload, evidence_ids=(), *, has_body=False):
        self.payloads[ref] = payload
        self.input_versions[checksum(payload)] = {
            "material_ref": ref, "evidence_ids": list(evidence_ids), "has_body": has_body,
        }

    def contains(self, ref):
        return ref in self.payloads

    def read(self, ref):
        if ref not in self.payloads:
            raise PermissionError("material is outside the delegated task")
        self.service.check_versions(self)
        return self.payloads[ref]

    def read_article(self, article_id, start_line=1, limit=100):
        binding = next((row for row in self.bindings if row["kind"] == "article"
                        and article_id in {row["material_ref"], row["lookup_ref"]}), None)
        if binding is None:
            raise PermissionError("article read needs an explicitly granted original article")
        if type(start_line) is not int or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("article.read requires positive integer bounds and limit <=100")
        end = min(binding["line_count"], start_line + limit - 1)
        request = self.task.reading_request
        if request and request.material_ref == binding["material_ref"] and request.depth == "skim" and request.ranges:
            if not any(span.start_line <= start_line <= end <= span.end_line for span in request.ranges):
                raise PermissionError("article range is outside the granted skim ranges")
        observations = self.service.acquire(self, binding, [(start_line, end)], "child_read")
        payload = {"material_ref": binding["material_ref"], "observations": observations}
        self.remember(binding["material_ref"], payload,
                      [row["evidence_id"] for row in observations], has_body=True)
        return payload

    def observe_input(self, messages, response):
        for batch in _input_batches(messages, response):
            matched = {}
            for row in _walk(batch["messages"]):
                observation = self.input_versions.get(checksum(row))
                if observation and observation["has_body"]:
                    matched[observation["material_ref"]] = observation
            if not matched:
                continue
            self.visible.update(matched)
            evidence_ids = sorted({ref for item in matched.values() for ref in item["evidence_ids"]})
            self.input_calls.append({"response_id": batch["response_id"], "input_kind": batch["input_kind"],
                                     "material_refs": sorted(matched), "evidence_ids": evidence_ids,
                                     "ranges": [self.sources[ref] for ref in evidence_ids if ref in self.sources]})

    def processing(self):
        return {"visible_material_refs": sorted(self.visible),
                "analysis_input_calls": self.input_calls,
                "acquired_source_bindings": list(self.sources.values())}


class ResearchExecution:
    def __init__(self, gateway):
        self.gateway = gateway
        self.prepared = {}
        self.handles = {}
        self.receipts = {}
        self.validated_bodies = {}
        self.dispositions = {}
        self.events = []
        self.on_event = None
        self.main_visible_evidence = set()
        self.main_visible_original_evidence = set()
        self.main_visible_artifacts = set()
        self.main_calls = []
        self.main_producer = {"role": "main", "role_version": "2.0.0"}
        gateway.artifacts.research.context_provider = self.main_context

    def emit(self, event, **payload):
        record = {"event": event, "run_id": self.gateway.artifacts.run_id,
                  "session_id": self.gateway.artifacts.session_id, **payload}
        self.events.append(record)
        if self.on_event:
            self.on_event(record)
        return record

    def _bind(self, lookup, task_id):
        original = self.handles.get(lookup)
        lookup = original["lookup_ref"] if original else lookup
        known_articles = {ref for listing in self.gateway.article_list_history
                          for ref in listing.get("article_ids", [])}
        known_articles.update(row["article_id"] for row in self.gateway.read_refs)
        if lookup in known_articles:
            self.gateway._check_article_scope(lookup)
            if self.gateway.allowed_article_ids is not None and lookup not in self.gateway.allowed_article_ids:
                raise PermissionError("article is outside the parent grant")
            article = self.gateway.corpus.get_article(lookup)
            if article is None:
                raise ValueError("missing article material")
            binding = {"lookup_ref": lookup, "kind": "article", "content_hash": article["current_hash"],
                       "article_id": lookup, "author": article["author"], "title": article["title"],
                       "published_at": article["published_at"], "line_count": len(article["content"].splitlines())}
        elif re.fullmatch(r"(?:understanding|synthesis|view|card|comparison|review|note)_[0-9a-f]{24}", lookup):
            record = self.gateway.artifacts.call(operation="get", artifact_id=lookup)
            binding = {"lookup_ref": lookup, "kind": "artifact",
                       "content_hash": self.gateway.artifacts.research.record_checksum(record)}
        else:
            raise PermissionError("unknown material: use a listed article or current-run artifact, never a path")
        if original and original["content_hash"] != binding["content_hash"]:
            raise ValueError("stale material handle; explicitly select a new source version")
        binding["material_ref"] = "material_" + checksum({"task_id": task_id, **binding})[:24]
        self.handles[binding["material_ref"]] = binding
        return binding

    def prepare(self, task, task_id, *, acquire=True):
        task = ResearchTask.model_validate(task)
        bindings = [self._bind(ref, task_id) for ref in task.material_refs]
        aliases = dict(zip(task.material_refs, [row["material_ref"] for row in bindings]))
        if len(set(aliases.values())) != len(bindings):
            raise ValueError("equivalent material lookups must not be granted twice")
        body = task.model_dump(mode="json", exclude_none=True)
        body["material_refs"] = list(aliases.values())
        if task.reading_request:
            body["reading_request"]["material_ref"] = aliases[task.reading_request.material_ref]
        normalized = ResearchTask.model_validate(body)
        prepared = PreparedResearch(self, task_id, normalized, bindings, {}, [])
        snapshot_ref = "task_snapshot_" + checksum({"task_id": task_id, "task": body})[:24]
        prepared.remember(snapshot_ref, {"research_task": body})
        for binding in bindings:
            ref = binding["material_ref"]
            if binding["kind"] == "artifact":
                record = self.gateway.artifacts.call(operation="get", artifact_id=binding["lookup_ref"])
                prepared.dependencies.append({"artifact_id": binding["lookup_ref"], "checksum": binding["content_hash"]})
                evidence_ids = self._artifact_evidence(record)
                for evidence_id in evidence_ids:
                    fragment = self.gateway.evidence_ledger.get(evidence_id)
                    if fragment is None:
                        raise ValueError("upstream evidence is unavailable in the current run")
                    prepared.sources[evidence_id] = _source_binding(fragment, "inherited")
                prepared.remember(ref, {"material_ref": ref, "record": record,
                                        "evidence_ids": evidence_ids}, evidence_ids, has_body=True)
            else:
                payload = {"material_ref": ref, "metadata": binding, "observations": []}
                request = normalized.reading_request
                if acquire and request and request.material_ref == ref:
                    spans = ([(1, binding["line_count"])] if request.depth == "deep" else
                             [(span.start_line, span.end_line) for span in request.ranges])
                    payload["observations"] = self.acquire(prepared, binding, spans, "runtime_preload")
                prepared.remember(ref, payload, [row["evidence_id"] for row in payload["observations"]],
                                  has_body=bool(payload["observations"]))
        self.prepared[task_id] = prepared
        return prepared

    @staticmethod
    def _artifact_evidence(record):
        if record.get("schema_version") == SCHEMA:
            processed = {ref for call in record["processing"].get("analysis_input_calls", [])
                         for ref in call.get("evidence_ids", [])}
            return sorted({row["evidence_id"] for row in record["source_bindings"]
                           if row["evidence_id"] in processed})
        refs = set()
        for row in _walk(record.get("payload", {})):
            if isinstance(row.get("evidence_id"), str):
                refs.add(row["evidence_id"])
            refs.update(ref for ref in row.get("evidence_refs", []) if isinstance(ref, str))
        return sorted(refs)

    def acquire(self, prepared, binding, spans, origin):
        from policy_impact.domains.finance.article_gateway import _range_observed

        self.check_versions(prepared)
        article = self.gateway.corpus.get_article(binding["article_id"])
        lines = article["content"].splitlines()
        observations = []
        for start, end in spans:
            if not 1 <= start <= end <= len(lines):
                raise ValueError("reading range exceeds the granted article version")
            for chunk in range(start, end + 1, 100):
                last = min(end, chunk + 99)
                scope = CURRENT.get()
                if scope and not scope.acquiring():
                    raise TimeoutError("research acquisition closed; preserve observed material and finalize")
                restored = _range_observed(chunk, last, self.gateway.read_refs_for_article(binding["article_id"]))
                while not _range_observed(chunk, last, self.gateway.read_refs_for_article(binding["article_id"])):
                    self.gateway.read_article(article_id=binding["article_id"], start_line=chunk, limit=last - chunk + 1)
                text = "\n".join(f"{number}: {IMAGE_LINE.sub('[image not verified]', lines[number - 1])}"
                                 for number in range(chunk, last + 1))
                reference = {"article_id": binding["article_id"], "author": binding["author"],
                             "title": binding["title"], "published_at": binding["published_at"],
                             "path": str(self.gateway.corpus.root / article["rel_path"]),
                             "content_hash": binding["content_hash"], "line_start": chunk, "line_end": last,
                             "source_url": article["source_url"]}
                fragment = self.gateway.evidence_ledger.add_article(reference, excerpt=text)
                prepared.sources[fragment.evidence_id] = _source_binding(fragment, origin)
                observation = {**reference, "evidence_id": fragment.evidence_id, "text": text,
                               "restored": restored, "independent_verification": False}
                observations.append(observation)
                self.emit("research_material_observation", task_id=prepared.task_id,
                          material_ref=binding["material_ref"], evidence_id=fragment.evidence_id,
                          content_hash=binding["content_hash"], line_start=chunk, line_end=last,
                          origin=origin, restored=restored, observation_checksum=checksum(observation))
        return observations

    def check_versions(self, prepared):
        for row in prepared.bindings:
            if row["kind"] == "article":
                article = self.gateway.corpus.get_article(row["article_id"])
                if article is None or article["current_hash"] != row["content_hash"]:
                    raise ValueError("stale source version; create an explicit new task")
            else:
                record = self.gateway.artifacts.call(operation="get", artifact_id=row["lookup_ref"])
                if self.gateway.artifacts.research.record_checksum(record) != row["content_hash"]:
                    raise ValueError("stale upstream artifact")

    def prepare_delegate(self, task, task_id):
        self.gateway._ensure_research_open()
        if self.gateway.require_plan_before_tools and not self.gateway.plan.exists:
            raise ValueError("research_plan_required: call research.plan before delegating")
        limits = CURRENT.get().root.limits if CURRENT.get() else ResearchLimits.from_env()
        if len(self.prepared) >= limits.max_delegates:
            raise ValueError("research delegation budget reached; synthesize and disclose gaps")
        prepared = self.prepare(task, task_id)
        # Include role/schema/tool overhead as well as materials. AgentLoop also
        # checks its exact request before each real call.
        from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
        definition = build_default_subagent_role_registry().get("analyst", "2.0.0")
        prompt = read_plugin_prompt_modules(definition.prompt_modules)
        estimated = estimate_tokens({"task": prepared.task.model_dump(mode="json"),
                                     "materials": prepared.payloads, "prompt": prompt,
                                     "output_schema": definition.output_schema.model_json_schema()})
        if estimated > limits.input_limit():
            self.emit("research_input_overflow", task_id=task_id, estimated_tokens=estimated,
                      input_limit=limits.input_limit(), materials=prepared.bindings)
            raise ResearchInputOverflow(estimated, limits.input_limit(), prepared.bindings)
        return prepared.task, tuple(prepared.payloads)

    def delegate(self, brief, state, *, model_adapter, skill_manifest, role_registry):
        prepared = self.prepared[brief.task_id]
        tools = ToolGateway(PermissionEngine([
            ToolPolicy("subagent.analyst", "article.read", "allow", "task-scoped original material"),
        ]), local_tools={"article.read": prepared.read_article})
        runner = SubagentRunner(model_adapter=model_adapter, tool_gateway=tools,
                                skill_manifest=skill_manifest, role_registry=role_registry)
        scope = CURRENT.get()
        limits = scope.root.limits if scope else ResearchLimits.from_env()

        def guard(payload):
            try:
                self.check_versions(prepared)
                self.gateway.artifacts.research.validate(
                    prepared.task, payload, material_bindings=prepared.bindings,
                    source_bindings=list(prepared.sources.values()), processing=prepared.processing())
            except (ValueError, PermissionError) as exc:
                return str(exc)
            return None

        with execution_scope(limits=limits, timeout_seconds=min(brief.timeout_seconds, limits.child_seconds),
                             reserve_seconds=limits.child_final_seconds, stage="analyst@2.0.0"):
            result = runner.run(state, brief.role, task_brief=brief, artifact_resolver=prepared,
                                required_artifact_reads=brief.input_artifact_refs,
                                on_model_input=prepared.observe_input, final_guard=guard)
        for tool in tools.calls:
            self.emit("research_child_tool", task_id=brief.task_id, tool=tool.to_dict())
        for call in prepared.input_calls:
            self.emit("research_analysis_input", task_id=brief.task_id, **call)
        if not result.output:
            receipt = {"status": "no_valid_result", "task_id": brief.task_id,
                       "stop_reason": result.stop_reason, "processing": prepared.processing(),
                       "diagnostics": [{"stage": row.get("stage"), "message": row.get("message"),
                                        "detail": row.get("detail")} for row in state.errors[-3:]]}
            self.emit("research_result_ingest", **receipt)
            return result.model_copy(update={"research_receipt": receipt})
        from policy_impact.runtime.task_brief import deep_thaw_json
        payload = deep_thaw_json(result.output)
        manifest = next(row for row in state.context_manifests if row["record_id"] == result.context_manifest_id)
        try:
            receipt = self.ingest(prepared, payload, producer={
                "role": brief.role, "role_version": brief.role_version, "parent_span_id": brief.parent_span_id,
                "span_id": result.span_id, "context_manifest_id": result.context_manifest_id,
                "context_checksum": manifest["checksum"],
                "prompt_checksum": manifest["metadata"]["prompt_checksum"],
                "capability_modules": list(role_registry.get(brief.role, brief.role_version).prompt_modules),
            }, stop_reason=result.stop_reason)
        except (ValueError, PermissionError) as exc:
            receipt = {"status": "rejected", "task_id": brief.task_id, "error": str(exc),
                       "stop_reason": "gate_blocked", "runner_stop_reason": result.stop_reason,
                       "processing": prepared.processing()}
            self.emit("research_result_ingest", **receipt)
            state.delegation_evidence[:] = [row for row in state.delegation_evidence if row["task_id"] != brief.task_id]
            state.add_stage_trace({"stage": "subagent_delegate", "status": "failed", "task_id": brief.task_id,
                                   "parent_run_id": brief.parent_run_id, "role": brief.role,
                                   "role_version": brief.role_version, "parent_span_id": brief.parent_span_id,
                                   "span_id": result.span_id, "context_manifest_id": result.context_manifest_id,
                                   "stop_reason": "gate_blocked", "runner_stop_reason": result.stop_reason})
            return result.model_copy(update={"output": {}, "stop_reason": "gate_blocked", "artifact_refs": (),
                                             "research_receipt": receipt})
        key = receipt.get("artifact_id") or receipt["result_checksum"]
        normalized = self.validated_bodies[key]
        return result.model_copy(update={"research_receipt": receipt, "output": normalized})

    def ingest(self, prepared, payload, *, producer, stop_reason):
        self.check_versions(prepared)
        body = self.gateway.artifacts.research.validate(
            prepared.task, payload, material_bindings=prepared.bindings,
            source_bindings=list(prepared.sources.values()), processing=prepared.processing())
        result_checksum = checksum(body)
        try:
            receipt = self.gateway.artifacts.research.persist(
                task=prepared.task, payload=body, task_id=prepared.task_id, producer=producer,
                material_bindings=prepared.bindings, source_bindings=list(prepared.sources.values()),
                dependencies=prepared.dependencies, processing=prepared.processing(),
                execution={"stop_reason": stop_reason})
        except OSError as exc:
            receipt = {"persistence_status": "failed", "error_type": type(exc).__name__}
        receipt = {**receipt, "status": "validated", "result_checksum": result_checksum,
                   "task_id": prepared.task_id, "stop_reason": stop_reason,
                   "processing": prepared.processing(), "body_validated": True}
        key = receipt.get("artifact_id") or result_checksum
        self.receipts[key] = receipt
        self.validated_bodies[key] = body
        self.emit("research_result_ingest", **receipt,
                  material_disposition=body["material_disposition"],
                  evidence_import={"identity_mapping": {ref: ref for ref in prepared.sources},
                                   "independent_verification": False})
        return receipt

    def observe_main_input(self, messages, response):
        for batch in _input_batches(messages, response):
            self._observe_main_batch(batch)

    def _observe_main_batch(self, batch):
        evidence_ids, artifact_ids = set(), set()
        for row in _walk(batch["messages"]):
            ref = row.get("evidence_id")
            fragment = self.gateway.evidence_ledger.get(ref) if isinstance(ref, str) else None
            if fragment and row.get("text") == fragment.excerpt and fragment.excerpt:
                evidence_ids.add(ref)
                self.main_visible_original_evidence.add(ref)
            if row.get("schema_version") == SCHEMA and row.get("artifact_id"):
                try:
                    record = self.gateway.artifacts.call(operation="get", artifact_id=row["artifact_id"])
                    if checksum(record) == checksum(row):
                        artifact_ids.add(row["artifact_id"])
                        evidence_ids.update(self._artifact_evidence(record))
                except (OSError, ValueError, PermissionError):
                    pass
            receipt = row.get("research_receipt")
            if isinstance(receipt, dict) and isinstance(row.get("output"), dict):
                if checksum(row["output"]) == receipt.get("result_checksum"):
                    key = receipt.get("artifact_id") or receipt.get("result_checksum")
                    if key in self.receipts:
                        if receipt.get("artifact_id"):
                            artifact_ids.add(receipt["artifact_id"])
                        evidence_ids.update(ref for item in row["output"].get("evidence_links", [])
                                            for ref in item["evidence_refs"])
        self.main_visible_evidence.update(evidence_ids)
        self.main_visible_artifacts.update(artifact_ids)
        if evidence_ids or artifact_ids:
            self.main_calls.append({"response_id": batch["response_id"], "input_kind": batch["input_kind"],
                                    "evidence_ids": sorted(evidence_ids), "artifact_ids": sorted(artifact_ids)})

    def main_context(self, task, payload):
        prepared = self.prepare(task, "task_main_" + uuid4().hex, acquire=False)
        for binding in prepared.bindings:
            if binding["kind"] == "article":
                refs = [self.gateway.evidence_ledger.get(ref) for ref in self.main_visible_original_evidence]
                refs = [ref for ref in refs if ref and ref.article_id == binding["article_id"]
                        and ref.content_hash == binding["content_hash"]]
                if refs:
                    prepared.visible.add(binding["material_ref"])
                for ref in refs:
                    prepared.sources[ref.evidence_id] = _source_binding(ref, "main_observation")
            elif binding["lookup_ref"] in self.main_visible_artifacts:
                prepared.visible.add(binding["material_ref"])
            else:
                prepared.sources = {key: value for key, value in prepared.sources.items()
                                    if key in self.main_visible_evidence}
        prepared.input_calls = list(self.main_calls)

        def after_save(receipt):
            body = self.gateway.artifacts.research.validate(
                prepared.task, payload, material_bindings=prepared.bindings,
                source_bindings=list(prepared.sources.values()), processing=prepared.processing())
            receipt = {**receipt, "result_checksum": checksum(body), "task_id": prepared.task_id,
                       "status": "validated", "stop_reason": "in_progress", "body_validated": True}
            key = receipt.get("artifact_id") or receipt["result_checksum"]
            self.receipts[key] = receipt
            self.validated_bodies[key] = body
            self.emit("research_result_ingest", **receipt,
                      material_disposition=body["material_disposition"])
            return receipt

        return {"task": prepared.task, "after_save": after_save,
                "task_id": prepared.task_id, "producer": self.main_producer,
                "material_bindings": prepared.bindings, "source_bindings": list(prepared.sources.values()),
                "dependencies": prepared.dependencies, "processing": prepared.processing(),
                "execution": {"stop_reason": "in_progress"}}

    def disposition(self, result_ref, decision, reason):
        if result_ref not in self.receipts:
            raise PermissionError("disposition needs an ingested current-run result or inline checksum")
        if decision not in {"use", "qualify", "exclude", "follow_up"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError("disposition needs use|qualify|exclude|follow_up and a nonblank reason")
        record = self.emit("research_result_disposition", result_ref=result_ref,
                           result_checksum=self.receipts[result_ref]["result_checksum"],
                           decision=decision, reason=reason)
        self.dispositions[result_ref] = record
        return record

    def pending_dispositions(self):
        return sorted(set(self.receipts) - set(self.dispositions))

    def final_audit(self, answer):
        from policy_impact.domains.finance.article_gateway import article_cited_ids

        cited = set(re.findall(r"evidence_[A-Za-z0-9_]+", answer))
        cited_articles = article_cited_ids(answer, self.gateway.read_refs)
        return {"research_results": list(self.receipts.values()),
                "dispositions": list(self.dispositions.values()),
                "pending_dispositions": self.pending_dispositions(),
                "answer_evidence_refs": sorted(cited),
                "used_articles": sorted({ref.article_id for item in cited
                                         if (ref := self.gateway.evidence_ledger.get(item)) and ref.article_id}
                                        | set(cited_articles)),
                "use_basis": "actual final-answer EvidenceRefs/inline source locators, not considered or read state"}
