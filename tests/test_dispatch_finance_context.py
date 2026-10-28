"""Context and artifact boundaries for per-action financial continuation."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import ROOT_TOOLS, FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import (
    FinanceRootContext,
    FinanceRootExecutor,
    FinanceRootState,
)
from policy_impact.harness.context_manifest import estimate_tokens


class Materials:
    tool_names = ("result.get",)

    def __init__(self, *, limit=1_000_000):
        self.values = {}
        self.limit = limit
        self.pages = []

    async def store_context(self, value):
        ref = f"fixture-{len(self.values)}"
        self.values[ref] = json.dumps(value)
        return ref

    async def load_context(self, ref):
        return self.values[ref]

    def context_accepts(self, messages):
        return estimate_tokens(messages) <= self.limit

    async def tool(self, name, arguments):
        assert name == "result.get"
        self.pages.append(arguments)
        return {"content": '{"incomplete":"' + "x" * 64000,
                "has_more": True, "next_offset": 64000}


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_granted_plain_text_context_is_visible_without_becoming_unavailable(domain_class, as_bytes):
    async def run():
        capabilities = Materials()
        note = "Parent context: preserve dates and \u6761\u4ef6.\nUse the assigned article only.\n"
        capabilities.values["parent-note"] = note.encode("utf-8") if as_bytes else note
        state = FinanceRootState(query="explain the argument", phase="research", execution={
            "material_refs": ["parent-note"], "allowed_tools": list(ROOT_TOOLS),
        })
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        material = user["materials"][0]
        payload = material.get("payload")
        if domain_class is FinanceH2Domain and isinstance(payload, str):
            payload = json.loads(payload)
        assert payload == note
        assert material["material_ref"] == "parent-note"
        assert not material.get("unavailable") and not material.get("payload_omitted")
        assert user["known_material_refs"] == ["parent-note"]
    asyncio.run(run())


@pytest.mark.parametrize("note_first", [False, True])
def test_plain_text_parent_note_does_not_break_or_expand_child_article_scope(note_first):
    async def run():
        capabilities = Materials()
        capabilities.values["parent-note"] = b'Context mentions {"article_id":"article_beta"}, not a source grant.'
        capabilities.values["article-metadata"] = json.dumps({"article": {"article_id": "article_alpha"}})
        refs = ["parent-note", "article-metadata"] if note_first else ["article-metadata", "parent-note"]
        state = FinanceRootState(query="analyze Alpha", phase="research", execution={"material_refs": refs})
        assert await FinanceH2Domain._article_scope(state, capabilities) == {"article_alpha"}
    asyncio.run(run())


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_plain_text_context_pressure_keeps_handle_and_expansion_not_unavailable(domain_class):
    async def run():
        capabilities = Materials(limit=5000)
        capabilities.values["parent-note"] = b"Parent context remains text. " * 10000
        state = FinanceRootState(query="analyze the argument", phase="research", execution={
            "material_refs": ["parent-note"], "allowed_tools": list(ROOT_TOOLS),
        })
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        material = json.loads(messages[-1]["content"])["materials"][0]
        assert capabilities.context_accepts(messages)
        assert material["material_ref"] == "parent-note"
        assert material.get("payload_omitted") is True and material.get("expansion_tool") == "material.get"
        assert not material.get("unavailable") and "payload" not in material
    asyncio.run(run())


@pytest.mark.parametrize("value", [{"article_id": "article_alpha"}, ["alpha"], "parent note", 42, True, None])
def test_structured_and_scalar_json_materials_keep_their_original_values(value):
    async def run():
        capabilities = Materials()
        ref = await capabilities.store_context(value)
        state = FinanceRootState(query="retain JSON", phase="research", execution={"material_refs": [ref]})
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        material = json.loads(messages[-1]["content"])["materials"][0]
        assert json.loads(material["payload"]) == value
        assert not material.get("unavailable")
    asyncio.run(run())


@pytest.mark.parametrize("code", ["forbidden", "artifact_unavailable", "cancelled", "stale_lease"])
def test_scope_loading_does_not_swallow_artifact_or_execution_failures(code):
    class Unavailable(Materials):
        async def load_context(self, ref):
            raise DispatchError(code)

    async def run():
        state = FinanceRootState(query="analyze Alpha", phase="research", execution={"material_refs": ["blocked"]})
        with pytest.raises(DispatchError) as caught:
            await FinanceH2Domain._article_scope(state, Unavailable())
        assert caught.value.code == code
    asyncio.run(run())


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_invalid_utf8_is_not_reclassified_as_readable_text_context(domain_class):
    async def run():
        capabilities = Materials()
        capabilities.values["invalid"] = b"\xff\xfe\xfa"
        state = FinanceRootState(query="invalid input", phase="research", execution={"material_refs": ["invalid"]})
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        material = json.loads(messages[-1]["content"])["materials"][0]
        assert material["unavailable"] is True and "payload" not in material
        with pytest.raises(UnicodeDecodeError):
            await FinanceH2Domain._article_scope(state, capabilities)
    asyncio.run(run())


def test_root_followup_context_exposes_handles_without_fetching_previous_result():
    async def run():
        capabilities = Materials()
        followup = {
            "previous_task_id": "task.previous", "previous_result_ref": "res_previous",
            "success_criteria": ["Explain the new counterargument"],
        }
        state = FinanceRootState(query="Reconsider the conclusion", phase="plan", execution={
            "follow_up_context": followup,
        })
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert user["follow_up_context"] == followup
        assert user["known_material_refs"] == user["known_evidence_refs"] == []
        assert capabilities.pages == []
    asyncio.run(run())


def test_all_fifteen_materials_remain_visible_below_configured_pressure():
    async def run():
        capabilities = Materials()
        refs = [await capabilities.store_context({"article": index, "content": f"source {index}"})
                for index in range(15)]
        state = FinanceRootState(query="compare views", phase="research", execution={
            "material_refs": refs, "allowed_tools": list(ROOT_TOOLS),
        })
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert [item["material_ref"] for item in user["materials"]] == refs
        assert all("payload" in item and not item.get("payload_omitted") for item in user["materials"])
        assert json.loads(user["materials"][0]["payload"])["content"] == "source 0"
    asyncio.run(run())


def test_root_guidance_separates_input_only_keys_and_article_artifact_identities():
    async def run():
        capabilities = Materials()
        messages = await FinanceH2Domain(object())._messages(
            "coordinate", FinanceRootState(query="coordinate", phase="research"), [], capabilities,
        )
        system = messages[0]["content"]
        assert system.count("next_action_key is input-only context") == 1
        assert "never a top-level action or state_update field" in system
        assert "arguments.dispatch_key or arguments.idempotency_key" in system
        assert "do not add it to tools such as agent.wait" in system
        assert "not article IDs from article.list" in system
        assert "known_material_refs" in system
        assert '"input_schema"' not in system and '"$defs"' not in system
    asyncio.run(run())


def test_pressure_retains_every_material_index_and_authorized_expansion():
    async def run():
        capabilities = Materials(limit=5000)
        refs = [await capabilities.store_context({"article": index, "content": "source " * 10000})
                for index in range(15)]
        state = FinanceRootState(query="compare views", phase="research", execution={
            "material_refs": refs, "allowed_tools": list(ROOT_TOOLS),
        })
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert capabilities.context_accepts(messages)
        assert [item["material_ref"] for item in user["materials"]] == refs
        assert all(item.get("payload_omitted") and item["expansion_tool"] == "material.get"
                   for item in user["materials"])
        assert user["known_material_refs"] == refs
    asyncio.run(run())


def test_large_full_material_does_not_become_checkpoint_body():
    async def run():
        capabilities = Materials()
        content = "source " * 50000
        state = FinanceRootState(query="read source", phase="research")
        context = FinanceRootContext(state.query, state, {}, None, [], [{"tasks": [{
            "task_id": "source-task", "status": "succeeded", "result": {
                "result_ref": "source-result", "full_content": json.dumps({"content": content}),
                "envelope": {"evidence_refs": []},
            },
        }]}])
        updated, _ = await FinanceH2Domain(object())._consume_observations(context, capabilities)
        compact = FinanceRootExecutor._compact_state(updated)
        assert len(json.dumps(compact)) < 4096
        material = json.loads(capabilities.values[updated.execution["material_refs"][0]])
        assert material["payload"]["content"] == content
        assert "observations" not in updated.execution
        assert updated.execution["observation_refs"]
    asyncio.run(run())


def test_omitted_result_body_is_not_automatically_fetched_or_claimed_as_read():
    async def run():
        capabilities = Materials()
        state = FinanceRootState(query="read child result", phase="research")
        context = FinanceRootContext(state.query, state, {}, None, [], [{"tasks": [{
            "task_id": "source-task", "status": "succeeded", "result": {
                "result_ref": "source-result", "envelope": {"summary": "working summary"},
            },
        }]}])
        updated, observations = await FinanceH2Domain(object())._consume_observations(context, capabilities)
        assert capabilities.pages == []
        assert updated.execution["material_refs"] == []
        assert len(observations) == 1
        assert observations[0]["full_read"] is False
        assert observations[0]["result_ref"] == "source-result"
        assert observations[0]["expansion_tool"] == "result.get"
        assert observations[0]["summary"] == {"summary": "working summary"}
        assert "source-result" in updated.execution["evidence_refs"]
    asyncio.run(run())


@pytest.mark.parametrize("result_kind", ["absent", "full", "omitted"])
@pytest.mark.parametrize("status", ["succeeded", "partial", "failed", "cancelled", "timed_out"])
def test_wait_target_status_snapshot_survives_finance_context(status, result_kind):
    async def run():
        capabilities = Materials()
        task = {
            "task_id": "child-task", "thread_id": "child-thread", "status": status,
            "attempts": [{"attempt_id": "child-attempt", "status": status,
                          "error_code": "contract_error" if status == "failed" else None}],
            "budget": {"requested": 6, "allocated": 6, "consumed": 2,
                       "local_available": 0, "delegated": 0, "remaining": 0},
            "deadline_at": 123456, "cancel_requested": status == "cancelled",
            "dispatch": {"status": "published", "last_error": None, "retry_count": 0},
            "lease_token": "private-token", "model_output": "not a status field",
        }
        if status == "failed":
            task["failure_diagnostic"] = {
                "error_code": "contract_error", "validation_stage": "final.output",
                "validation_errors": [{"loc": ["evidence_links", 0, "text"],
                                       "type": "value_error", "msg": "anchor must occur verbatim"}],
            }
        if result_kind != "absent":
            task["result"] = {"result_ref": "child-result", "envelope": {"summary": "child work"}}
            if result_kind == "full":
                task["result"]["full_content"] = json.dumps({"content": "retained work"})
        state = FinanceRootState(query="coordinate child", phase="research")
        context = FinanceRootContext(state.query, state, {}, None, [], [{"tasks": [task]}])
        domain = FinanceH2Domain(object())
        updated, observations = await domain._consume_observations(context, capabilities)
        expected = {key: value for key, value in task.items()
                    if key not in {"lease_token", "model_output", "result"}}
        for key, value in expected.items():
            assert observations[0].get(key) == value
        persisted = json.loads(capabilities.values[updated.execution["observation_refs"][0]])
        assert persisted == observations[0]
        messages = await domain._messages(state.query, updated, observations, capabilities)
        user = json.loads(messages[-1]["content"])
        for key, value in expected.items():
            assert user["observations"][0].get(key) == value
        assert "private-token" not in messages[-1]["content"]
        assert "not a status field" not in messages[-1]["content"]
        assert capabilities.pages == []
        if result_kind == "absent" or status not in {"succeeded", "partial"}:
            assert updated.execution["evidence_refs"] == []
            assert updated.execution["material_refs"] == []
    asyncio.run(run())


@pytest.mark.parametrize("prepared", ["not-json", '{"type":"final","output":{"answer":"not an action"}}'])
def test_corrupt_prepared_action_fails_without_a_new_model_decision(prepared):
    async def run():
        capabilities = Materials()
        capabilities.values["corrupt-action"] = prepared
        state = FinanceRootState(query="read source", phase="research", execution={
            "prepared_action_ref": "corrupt-action",
        })
        context = FinanceRootContext(state.query, state, {}, None, [], [])
        with pytest.raises(DispatchError, match="prepared finance action") as error:
            await FinanceH2Domain(object())(context, capabilities)
        assert error.value.code == "checkpoint_invalid"
        assert capabilities.pages == []
        assert len(capabilities.values) == 1
    asyncio.run(run())


def test_followup_bytes_are_decoded_and_retained_across_continue():
    class Domain(FinanceH2Domain):
        @staticmethod
        async def _decide(messages, capabilities):
            user = json.loads(messages[-1]["content"])
            assert user["mailbox"] == [{
                "sequence": 2, "kind": "follow_up", "payload_ref": "message-ref",
                "content": "Keep the original conditions.",
            }]
            return SimpleNamespace(payload={"type": "final", "output": {"answer": "Complete answer."}}), None

    async def run():
        capabilities = Materials()
        capabilities.values["message-ref"] = b"Keep the original conditions."
        state = FinanceRootState(query="read source", phase="research")
        domain = Domain(object())
        first = await domain(FinanceRootContext(state.query, state, {}, None, [{
            "sequence": 2, "kind": "follow_up", "payload_ref": "message-ref",
        }], []), capabilities)
        second = await domain(FinanceRootContext(state.query, first.state, {}, None, [], []), capabilities)
        assert second.state.execution["mailbox"] == first.state.execution["mailbox"]
        assert second.submission.content == "Complete answer."
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["complete", "gap", "changed-hash", "overlap-mismatch", "incomplete"])
def test_result_material_requires_complete_consistent_retrieved_pages(mode):
    async def run():
        capabilities = Materials()
        first = await capabilities.store_context({"payload": {
            "result_ref": "res_source", "sha256": "source-hash", "content": "abcdef", "has_more": True,
        }})
        second = await capabilities.store_context({"payload": {
            "result_ref": "res_source", "sha256": "other-hash" if mode == "changed-hash" else "source-hash",
            "content": "ZZghij" if mode == "overlap-mismatch" else "efghij",
            "has_more": mode == "incomplete",
        }})
        state = FinanceRootState(query="read result", phase="research", execution={"result_pages": {
            "res_source": {"sha256": "source-hash", "pages": [
                {"offset": 0, "observation_ref": first},
                {"offset": 8 if mode == "gap" else 4, "observation_ref": second},
            ]},
        }})
        text = await FinanceH2Domain._retrieved_result(state, "res_source", capabilities)
        assert text == ("abcdefghij" if mode == "complete" else None)
    asyncio.run(run())


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
@pytest.mark.parametrize("trusted_clock", [None, {"today": "2026-10-19", "timezone": "Asia/Shanghai"}])
def test_trusted_brief_clock_replaces_stale_checkpoint_and_source_dates(domain_class, trusted_clock):
    captured = []

    class Domain(domain_class):
        @staticmethod
        async def _decide(messages, capabilities):
            captured.append(messages)
            output = {"answer": "The source remains unverified.", "evidence_refs": []}
            if domain_class is FinanceH2ResearchDomain:
                output = {
                    "schema_version": "finance.research_result.v1",
                    "content": "The source remains unverified.", "evidence_links": [],
                    "material_disposition": [], "gaps": [{"description": "No source body read."}],
                    "limitations": [],
                }
            return SimpleNamespace(payload={"type": "final", "output": output}), None

    async def run():
        capabilities = Materials()
        false_clock = {"today": "2099-01-01", "timezone": "Asia/Shanghai"}
        ref = await capabilities.store_context({"research_clock": false_clock})
        state = FinanceRootState(query=json.dumps({"research_clock": false_clock}), phase="research", execution={
            "research_clock": false_clock, "material_refs": [ref],
        })
        brief = {"research_clock": trusted_clock} if trusted_clock is not None else {}
        step = await Domain(object())(FinanceRootContext(state.query, state, brief, None, [], []), capabilities)
        user = json.loads(captured[0][-1]["content"])
        assert user.get("research_clock") == trusted_clock
        assert step.state.execution.get("research_clock") == trusted_clock
        assert "trusted research_clock.today" in captured[0][0]["content"]
        assert "published_after/published_before" in captured[0][0]["content"]
        assert user["query"] == state.query
        assert false_clock["today"] in json.dumps(user["materials"])
    asyncio.run(run())
