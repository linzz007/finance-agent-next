"""Offline integration tests: no live models, article roots or user preferences."""

import copy
import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter, ValidationError

from policy_impact.company_wiki import loader
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.research_gateway import FinanceResearchGateway
from policy_impact.domains.finance.research_records import SCHEMA, checksum
from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.model_gateway import ModelResponse
from policy_impact.harness.research_budget import CURRENT, ResearchLimits, execution_scope
from policy_impact.harness.state import PolicyImpactState
from policy_impact.runtime.actions import AgentAction
from policy_impact.runtime.research_contracts import ResearchTask, ResearchResult
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.runtime.task_brief import TaskBrief
from policy_impact.skills.registry import SkillRegistry


@pytest.fixture
def research(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "articles"
    for author, day, view in (("Alpha", "2026-10-01", "Gold may rise if real yields fall."),
                               ("Beta", "2026-10-02", "Gold may fall if real yields rise.")):
        directory = root / author / "articles"
        directory.mkdir(parents=True)
        (directory / (day + ".md")).write_text(
            f"# {author}\n{day}\n{view}\nThe condition matters; this is not a verified forecast.\n",
            encoding="utf-8")
    corpus = ArticleCorpus("finance_observer", root)
    gateway = FinanceResearchGateway("finance_observer", set(), question="Explain these views",
                                     corpus=corpus, session_id="session-test", run_id="run_12345678")
    listing = gateway.call("chat_agent_loop", "article.list", limit=10)
    ids = [row["article_id"] for row in listing["articles"]]
    return gateway, ids


def task(ref, *, kind="article_understanding", depth="deep", **reading):
    return {"schema_version": "finance.research_task.v1", "original_query": "Explain the argument",
            "objective": "Explain the argument and its condition", "material_refs": [ref],
            "requested_result_kind": kind, "completion_expectation": "Preserve scope and evidence",
            **({"reading_request": {"material_ref": ref, "depth": depth, **reading}}
               if kind == "article_understanding" else {})}


def result_for(prepared, content="The author offers a conditional view."):
    refs = sorted({ref for call in prepared.input_calls for ref in call["evidence_ids"]})
    return {"schema_version": "finance.research_result.v1", "content": content,
            "evidence_links": [{"text": content, "evidence_refs": refs}] if refs else [],
            "material_disposition": [{"material_ref": ref, "decision": "considered", "reason": "Relevant input"}
                                     for ref in sorted(prepared.visible)],
            "gaps": [], "limitations": []}


class ReaderModel:
    def __init__(self, service, task_id, *, advance=None):
        self.service, self.task_id, self.advance = service, task_id, advance
        self.messages = []

    def complete(self, messages, schema_name):
        self.messages.append(copy.deepcopy(messages))
        prepared = self.service.prepared[self.task_id]
        # The fixture reads the actual input; only Runtime may record visibility.
        from policy_impact.domains.finance.research_execution import _walk
        supplied = [prepared.input_versions.get(checksum(row)) for row in _walk(messages)]
        supplied = [row for row in supplied if row and row["has_body"]]
        refs = sorted({ref for row in supplied for ref in row["evidence_ids"]})
        body = result_for(prepared)
        body["evidence_links"] = [{"text": body["content"], "evidence_refs": refs}] if refs else []
        body["material_disposition"] = [{"material_ref": row["material_ref"], "decision": "considered",
                                          "reason": "Input supplied"} for row in supplied]
        if self.advance:
            self.advance()
        return ModelResponse(response_id="fixture-response", payload={"type": "final", "output": body})


def run_child(gateway, descriptor, *, task_id="task_fixture", model=None):
    normalized, refs = gateway.prepare_research_delegation(ResearchTask.model_validate(descriptor), task_id)
    registry = build_default_subagent_role_registry()
    role = registry.get("analyst", "2.0.0")
    brief = TaskBrief(task_id=task_id, parent_run_id=gateway.artifacts.run_id,
                      parent_span_id="span_parent", role="analyst", role_version="2.0.0",
                      task=normalized.objective, research_task=normalized, input_artifact_refs=refs,
                      output_schema_name=role.output_schema_name, max_steps=role.max_steps,
                      max_model_calls=role.max_model_calls, timeout_seconds=role.timeout_seconds)
    state = PolicyImpactState(company_id="finance_observer", run_id=gateway.artifacts.run_id)
    model = model or ReaderModel(gateway.research_execution, task_id)
    output = gateway.research_execution.delegate(brief, state, model_adapter=model,
                                               skill_manifest=SkillRegistry().get_manifest("general_chat"),
                                               role_registry=registry)
    return output, state, model


def test_versioned_action_is_opt_in_and_strict(research):
    _, ids = research
    adapter = TypeAdapter(AgentAction)
    old = adapter.validate_python({"type": "delegate", "role": "analyst", "task": "legacy"})
    assert "role_version" not in old.model_dump(mode="json")
    new = adapter.validate_python({"type": "delegate", "role": "analyst", "role_version": "2.0.0",
                                   "research_task": task(ids[0])})
    assert "task" not in new.model_dump(mode="json")
    for change in ({"role_version": "3.0.0"}, {"task": "duplicate"}, {"role": "verifier"}):
        with pytest.raises(ValidationError):
            adapter.validate_python({**new.model_dump(mode="json"), **change})
    with pytest.raises(ValidationError):
        ResearchTask.model_validate({**task(ids[0]), "focus": [" "]})


def test_full_article_delivered_in_one_analysis_and_restorable(research):
    gateway, ids = research
    output, state, model = run_child(gateway, task(ids[0]))
    assert output.stop_reason == "success"
    assert len(model.messages) == 1
    assert "The condition matters" in json.dumps(model.messages[0])
    receipt = output.research_receipt
    assert receipt["persistence_status"] == "saved"
    record = gateway.artifacts.call(operation="get", artifact_id=receipt["artifact_id"])
    assert record["payload"]["content"] == output.output["content"]
    assert record["processing"]["analysis_input_calls"]
    assert record["source_bindings"][0]["line_start"] == 1
    assert record["source_bindings"][0]["line_end"] == 4
    assert record["producer"]["role_version"] == "2.0.0"
    assert state.delegation_evidence[0]["role_version"] == "2.0.0"
    assert gateway.research_execution.pending_dispositions() == [receipt["artifact_id"]]
    decision = gateway.call("chat_agent_loop", "research.disposition", result_ref=receipt["artifact_id"],
                            decision="qualify", reason="Author view, not independently verified")
    assert decision["decision"] == "qualify"
    assert gateway.research_execution.pending_dispositions() == []


def test_reread_restores_real_body_not_empty_cached_receipt(research):
    gateway, ids = research
    gateway.read_article(article_id=ids[0])
    assert gateway.read_article(article_id=ids[0])["text"] == ""
    output, _, model = run_child(gateway, task(ids[0]))
    assert output.stop_reason == "success"
    assert "Gold may" in json.dumps(model.messages)
    observations = [event for event in gateway.research_execution.events
                    if event["event"] == "research_material_observation"]
    assert observations[-1]["restored"] is True
    assert len(gateway.read_refs) == 1


def test_skim_receives_only_authorized_range_and_no_other_article(research):
    gateway, ids = research
    output, _, model = run_child(gateway, task(ids[0], depth="skim", ranges=[{"start_line": 3, "end_line": 3}]))
    prepared = gateway.research_execution.prepared["task_fixture"]
    assert output.stop_reason == "success"
    assert "The condition matters" not in json.dumps(model.messages)
    with pytest.raises(PermissionError):
        prepared.read_article(ids[0], start_line=1, limit=4)
    with pytest.raises(PermissionError):
        prepared.read_article(ids[1])
    with pytest.raises(PermissionError):
        prepared.read("D:/private/secret.md")


def test_select_ranges_grant_is_metadata_only_until_actual_read(research):
    gateway, ids = research
    normalized, _ = gateway.prepare_research_delegation(
        ResearchTask.model_validate(task(ids[0], depth="skim", select_ranges=True)), "task_select")
    prepared = gateway.research_execution.prepared["task_select"]
    assert prepared.sources == {}
    assert prepared.task == normalized
    payload = prepared.read_article(normalized.material_refs[0], start_line=3, limit=1)
    assert payload["observations"][0]["line_start"] == 3
    assert prepared.visible == set()


def test_two_understandings_can_be_synthesized_without_transitive_reads(research):
    gateway, ids = research
    first, _, _ = run_child(gateway, task(ids[0]), task_id="task_alpha")
    second, _, _ = run_child(gateway, task(ids[1]), task_id="task_beta")
    refs = [first.research_receipt["artifact_id"], second.research_receipt["artifact_id"]]
    descriptor = {**task(refs[0], kind="synthesis"), "material_refs": refs}
    combined, _, model = run_child(gateway, descriptor, task_id="task_synthesis")
    assert combined.stop_reason == "success"
    prepared = gateway.research_execution.prepared["task_synthesis"]
    assert len(prepared.dependencies) == 2
    assert all(row["origin"] == "inherited" for row in prepared.sources.values())
    assert len(combined.output["evidence_links"][0]["evidence_refs"]) == 2
    with pytest.raises(PermissionError):
        prepared.read_article(ids[0])
    assert len(model.messages) == 1
    assert "The author offers a conditional view" in json.dumps(model.messages)


def test_unknown_material_and_fake_evidence_do_not_gain_authority(research):
    gateway, ids = research
    with pytest.raises(PermissionError):
        gateway.prepare_research_delegation(ResearchTask.model_validate(task("article_missing")), "task_bad")
    gateway.prepare_research_delegation(ResearchTask.model_validate(task(ids[0])), "task_provenance")
    prepared = gateway.research_execution.prepared["task_provenance"]
    body = result_for(prepared)
    body["material_disposition"] = [{"material_ref": prepared.task.material_refs[0],
                                      "decision": "considered", "reason": "Just claimed"}]
    with pytest.raises(ValueError, match="actual model input"):
        gateway.artifacts.research.validate(prepared.task, body, material_bindings=prepared.bindings,
                                           source_bindings=list(prepared.sources.values()), processing=prepared.processing())
    body["material_disposition"] = []
    body["evidence_links"] = [{"text": body["content"], "evidence_refs": ["evidence_fake"]}]
    with pytest.raises(PermissionError):
        gateway.artifacts.research.validate(prepared.task, body, material_bindings=prepared.bindings,
                                           source_bindings=list(prepared.sources.values()), processing=prepared.processing())


def test_storage_failure_keeps_valid_inline_result_without_fake_artifact(research, monkeypatch):
    gateway, ids = research
    monkeypatch.setattr(gateway.artifacts.research, "persist", lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    output, state, _ = run_child(gateway, task(ids[0]))
    assert output.stop_reason == "success"
    assert output.output["content"]
    assert output.research_receipt["persistence_status"] == "failed"
    assert "artifact_id" not in output.research_receipt
    assert output.research_receipt["result_checksum"] in gateway.research_execution.receipts
    assert state.delegation_evidence


def test_late_complete_analysis_preserves_timeout_without_success_proof(research, monkeypatch):
    gateway, ids = research
    import policy_impact.harness.research_budget as budget
    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    model = ReaderModel(gateway.research_execution, "task_late", advance=lambda: clock.__setitem__(0, 1500.0))
    with execution_scope(timeout_seconds=3600, reserve_seconds=600):
        output, state, _ = run_child(gateway, task(ids[0]), task_id="task_late", model=model)
    assert output.stop_reason == "timeout"
    assert output.output["content"]
    assert output.research_receipt["persistence_status"] == "saved"
    assert not state.delegation_evidence
    with pytest.raises(ValueError):
        output.evidence()


def test_overflow_is_parent_coordination_not_silent_skim(research):
    gateway, ids = research
    with execution_scope(limits=replace(ResearchLimits(), input_tokens=4500)):
        with pytest.raises(ValueError, match="main Agent must coordinate"):
            gateway.prepare_research_delegation(ResearchTask.model_validate(task(ids[0])), "task_overflow")
    prepared = gateway.research_execution.prepared["task_overflow"]
    assert prepared.task.reading_request.depth == "deep"
    assert prepared.input_calls == []


def test_main_save_requires_actual_visible_sources_and_exact_quote(research):
    gateway, ids = research
    observed = gateway.read_article(article_id=ids[0])
    descriptor = task(ids[0])
    body = {"schema_version": "finance.research_result.v1", "content": "A conditional view.",
            "evidence_links": [{"text": "A conditional view.", "evidence_refs": [observed["evidence_id"]]}],
            "material_disposition": [{"material_ref": ids[0], "decision": "considered", "reason": "Read source"}],
            "gaps": [], "limitations": []}
    with pytest.raises(ValueError):
        gateway.research_artifact(operation="save", schema_version=SCHEMA, kind="article_understanding",
                                  research_task=descriptor, payload=body)
    gateway.research_execution.observe_main_input([{"role": "tool", "content": observed}],
                                                  ModelResponse(response_id="main-1", payload={}))
    receipt = gateway.research_artifact(operation="save", schema_version=SCHEMA, kind="article_understanding",
                                        research_task=descriptor, payload=body)
    assert receipt["persistence_status"] == "saved"
    body["evidence_links"][0]["quotes"] = [{"evidence_ref": observed["evidence_id"], "text": "invented quote"}]
    with pytest.raises(ValueError, match="verbatim"):
        gateway.research_artifact(operation="save", schema_version=SCHEMA, kind="article_understanding",
                                  research_task=descriptor, payload=body)


def test_list_more_than_100_and_stale_cursor(research):
    gateway, ids = research
    run_child(gateway, task(ids[0]))
    prepared = gateway.research_execution.prepared["task_fixture"]
    for index in range(105):
        gateway.research_execution.ingest(prepared, result_for(prepared, f"View {index}."),
                                          producer={"role": "analyst"}, stop_reason="success")
    first = gateway.research_artifact(operation="list", schema_version=SCHEMA)
    count = first["count"]
    cursor = first["next_cursor"]
    while cursor:
        page = gateway.research_artifact(operation="list", schema_version=SCHEMA, cursor=cursor)
        count += page["count"]
        cursor = page["next_cursor"]
    assert count == 106
    assert page["listing_complete"] is True
    gateway.research_execution.ingest(prepared, result_for(prepared, "New view."),
                                      producer={"role": "analyst"}, stop_reason="success")
    with pytest.raises(ValueError, match="stale_cursor"):
        gateway.research_artifact(operation="list", schema_version=SCHEMA, cursor=first["next_cursor"])
    with pytest.raises(ValueError):
        gateway.research_artifact(operation="list", schema_version=SCHEMA, cursor="broken")


def test_tampering_and_changed_upstream_are_not_restored(research):
    gateway, ids = research
    output, _, _ = run_child(gateway, task(ids[0]))
    ref = output.research_receipt["artifact_id"]
    path = gateway.artifacts.root / (ref + ".json")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["content"] = "Tampered"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        gateway.research_artifact(operation="get", artifact_id=ref)


@pytest.mark.parametrize("child_failure", [False, True])
def test_parent_loop_accepts_versioned_result_with_integrity_proof(research, child_failure):
    gateway, ids = research
    state = PolicyImpactState(company_id="finance_observer", run_id=gateway.artifacts.run_id)
    skill = SkillRegistry().get_manifest("general_chat")
    registry = build_default_subagent_role_registry()

    class Main:
        calls = 0

        def complete(self, messages, schema_name):
            self.calls += 1
            if self.calls == 1:
                payload = {"type": "delegate", "role": "analyst", "role_version": "2.0.0", "research_task": task(ids[0])}
            else:
                receipt = messages[-1]["content"]["subagent_result"]["research_receipt"]
                assert receipt["status"] == ("no_valid_result" if child_failure else "validated")
                payload = {"type": "final", "output": {"answer": "A conditional view."}}
            return ModelResponse(response_id=f"main-{self.calls}", payload=payload)

    def delegate(brief):
        from policy_impact.harness.model_gateway import ModelInvocationError

        class Failed:
            def complete(self, messages, schema_name):
                raise ModelInvocationError("HTTP 503 temporarily unavailable")

        return gateway.research_execution.delegate(
            brief, state, model_adapter=Failed() if child_failure else ReaderModel(gateway.research_execution, brief.task_id),
            skill_manifest=skill, role_registry=registry)

    manifest = ContextManifestRecord(record_id="ctx_main", context_type="test", source_id=state.run_id,
                                      checksum="test", metadata={"visible_content": {}})
    with execution_scope(reserve_seconds=600):
        output = AgentLoop(Main(), gateway, delegate_fn=delegate, skill_manifest=skill,
                            role_registry=registry).run(state, AgentLoopConfig(
                                stage_name="chat_agent_loop", agent_role="main", context_manifest=manifest,
                                output_key="general_chat_agent_loop.v1", max_steps=4, max_model_calls=4))
    assert output.stop_reason == "success"
    assert len(output.delegation_evidence) == (0 if child_failure else 1)
    if not child_failure:
        assert output.delegation_evidence[0].role_version == "2.0.0"


def test_failed_child_retains_diagnostics_without_success_proof(research):
    from policy_impact.harness.model_gateway import ModelInvocationError

    gateway, ids = research

    class Failed:
        def complete(self, messages, schema_name):
            raise ModelInvocationError("HTTP 400 context_budget_exceeded: select fewer granted materials")

    output, state, _ = run_child(gateway, task(ids[0]), model=Failed())
    assert output.stop_reason == "model_error"
    assert output.output == {}
    assert output.research_receipt["status"] == "no_valid_result"
    assert "context_budget_exceeded" in json.dumps(output.research_receipt["diagnostics"])
    assert not state.delegation_evidence
    assert not gateway.research_execution.receipts


def test_ingestion_rejection_cannot_keep_runner_success_proof(research, monkeypatch):
    gateway, ids = research
    monkeypatch.setattr(gateway.research_execution, "ingest",
                        lambda *_, **__: (_ for _ in ()).throw(ValueError("stale source version")))
    output, state, _ = run_child(gateway, task(ids[0]))
    assert output.stop_reason == "gate_blocked"
    assert output.research_receipt["runner_stop_reason"] == "success"
    assert output.output == {}
    assert not output.artifact_refs
    assert not state.delegation_evidence
    assert state.stage_trace[-1]["status"] == "failed"


def test_working_view_restores_full_result_and_invalidates_with_source(research):
    gateway, ids = research
    first, _, _ = run_child(gateway, task(ids[0]), task_id="task_source")
    ref = first.research_receipt["artifact_id"]
    view, _, model = run_child(gateway, task(ref, kind="working_view"), task_id="task_view")
    view_id = view.research_receipt["artifact_id"]
    record = gateway.research_artifact(operation="get", artifact_id=view_id)
    assert record["kind"] == "working_view"
    assert record["dependencies"][0]["artifact_id"] == ref
    assert first.output["content"] in json.dumps(model.messages)
    prepared = gateway.research_execution.prepared["task_view"]
    assert prepared.read(prepared.task.material_refs[0])["record"]["payload"] == dict(first.output)
    article = gateway.corpus.get_article(ids[0])
    path = gateway.corpus.root / article["rel_path"]
    path.write_text(path.read_text(encoding="utf-8") + "New condition.\n", encoding="utf-8")
    gateway.corpus.sync()
    for stale in (ref, view_id):
        with pytest.raises(ValueError, match="stale source version"):
            gateway.research_artifact(operation="get", artifact_id=stale)


def test_current_run_artifact_id_cannot_grant_other_run_access(research):
    gateway, ids = research
    output, _, _ = run_child(gateway, task(ids[0]))
    ref = output.research_receipt["artifact_id"]
    other = FinanceResearchGateway("finance_observer", set(), question="Other run", corpus=gateway.corpus,
                                    session_id="session-other", run_id="run_87654321")
    with pytest.raises((FileNotFoundError, PermissionError, ValueError)):
        other.research_artifact(operation="get", artifact_id=ref)
    with pytest.raises((FileNotFoundError, PermissionError, ValueError)):
        other.prepare_research_delegation(ResearchTask.model_validate(task(ref, kind="synthesis")), "task_other")


def test_main_save_disk_failure_retains_inline_result_without_recovery_id(research, monkeypatch):
    gateway, ids = research
    observed = gateway.read_article(article_id=ids[0])
    gateway.research_execution.observe_main_input([{"role": "tool", "content": observed}],
                                                  ModelResponse(response_id="main-source", payload={}))
    body = {"schema_version": "finance.research_result.v1", "content": "The author's conditional view.",
            "evidence_links": [{"text": "The author's conditional view.", "evidence_refs": [observed["evidence_id"]]}],
            "material_disposition": [{"material_ref": ids[0], "decision": "considered", "reason": "Observed source"}],
            "gaps": [], "limitations": []}
    monkeypatch.setattr(gateway.artifacts.research, "persist", lambda **_: (_ for _ in ()).throw(OSError("disk full")))
    receipt = gateway.research_artifact(operation="save", schema_version=SCHEMA, kind="article_understanding",
                                        research_task=task(ids[0]), payload=body)
    assert receipt["persistence_status"] == "failed"
    assert "artifact_id" not in receipt
    assert gateway.research_execution.validated_bodies[receipt["result_checksum"]]["content"] == body["content"]


def test_context_offload_restores_complete_versioned_result_and_provenance(research):
    from policy_impact.harness.context_offload import RunContextStore

    gateway, ids = research
    output, _, _ = run_child(gateway, task(ids[0]))
    observation = {"subagent_result": output.model_dump(mode="json")}
    messages = [{"role": "tool", "tool_name": "subagent.analyst", "content": observation},
                *[{"role": "tool", "tool_name": "fixture", "content": {"index": index}} for index in range(4)]]
    store = RunContextStore(gateway.artifacts.root.parent)
    loop = AgentLoop(ReaderModel(gateway.research_execution, "unused"), gateway, context_store=store)
    offloaded = loop._offload_old_observations(messages, 100)
    assert len(offloaded) == 1
    assert "offloaded_observation" in messages[0]["content"]
    restored = store.open(offloaded[0]["context_id"])
    assert restored["content"] == observation
    gateway.research_execution.observe_main_input([{"role": "tool", "content": {"tool_result": restored}}],
                                                  ModelResponse(response_id="restored-input", payload={}))
    assert output.research_receipt["artifact_id"] in gateway.research_execution.main_visible_artifacts
    assert gateway.research_execution.main_visible_evidence


def test_final_use_does_not_include_every_read_or_considered_article(research):
    gateway, ids = research
    first, _, _ = run_child(gateway, task(ids[0]), task_id="task_used")
    other, _, _ = run_child(gateway, task(ids[1]), task_id="task_excluded")
    for output, decision in ((first, "use"), (other, "exclude")):
        gateway.research_execution.disposition(output.research_receipt["artifact_id"], decision, "Parent relevance decision")
    refs = first.output["evidence_links"][0]["evidence_refs"]
    audit = gateway.research_execution.final_audit(first.output["content"] + " " + " ".join(refs))
    assert audit["used_articles"] == [ids[0]]
    assert len(gateway.read_refs) == 2


def test_context_progress_accepts_only_new_committed_valid_v2_results(research):
    from policy_impact.harness.agent_loop import _ContextProgressCheckpoint

    gateway, ids = research
    output, _, _ = run_child(gateway, task(ids[0]))
    events = []
    checkpoint = _ContextProgressCheckpoint(events.append)
    checkpoint._activate()
    checkpoint.observe_research_result({**output.research_receipt, "persistence_status": "failed"})
    assert checkpoint.active
    checkpoint.observe_tool("research.artifact", {"operation": "save"}, output.research_receipt)
    assert not checkpoint.active
    assert events[-1]["kind"] == "research_result"
    checkpoint._activate()
    checkpoint.observe_research_result(output.research_receipt)
    assert checkpoint.active
    assert len([event for event in events if event["decision"] == "release"]) == 1


def test_overflow_details_reach_parent_for_coordinated_range_tasks(research):
    gateway, ids = research
    article = gateway.corpus.get_article(ids[0])
    (gateway.corpus.root / article["rel_path"]).write_text(
        "# Source\n2026-10-01\n" + "A detailed source condition. " * 1200, encoding="utf-8")
    gateway.corpus.sync()

    class Main:
        calls = 0

        def complete(self, messages, schema_name):
            self.calls += 1
            if self.calls == 1:
                payload = {"type": "delegate", "role": "analyst", "role_version": "2.0.0", "research_task": task(ids[0])}
            else:
                details = messages[-1]["content"]["delegate_error"]["details"]
                assert details["code"] == "research_input_overflow"
                assert details["estimated_tokens"] > details["input_limit"]
                assert details["materials"][0]["content_hash"]
                assert details["materials"][0]["line_count"] == 3
                payload = {"type": "final", "output": {"answer": "The full input did not fit; coordinated range tasks are needed."}}
            return ModelResponse(response_id=f"main-{self.calls}", payload=payload)

    state = PolicyImpactState(company_id="finance_observer", run_id=gateway.artifacts.run_id)
    manifest = ContextManifestRecord(record_id="ctx_overflow", context_type="test", source_id=state.run_id,
                                      checksum="test", metadata={"visible_content": {}})
    with execution_scope(limits=replace(ResearchLimits(), input_tokens=10000)):
        output = AgentLoop(Main(), gateway, delegate_fn=lambda _: pytest.fail("no oversized child may start"),
                            skill_manifest=SkillRegistry().get_manifest("general_chat")).run(
            state, AgentLoopConfig(stage_name="chat_agent_loop", agent_role="main", context_manifest=manifest,
                                   output_key="general_chat_agent_loop.v1", max_steps=4, max_model_calls=4))
    assert output.stop_reason == "success"
    assert not output.delegation_evidence
