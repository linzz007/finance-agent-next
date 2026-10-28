"""Exact input deduplication preserves full evidence and durable observations."""

import asyncio
import json

import pytest

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import ROOT_TOOLS, FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootState
from policy_impact.harness.context_manifest import estimate_tokens


class Context:
    tool_names = ("result.get",)

    def __init__(self, values, *, limit=1_000_000):
        self.values = {key: json.dumps(value) for key, value in values.items()}
        self.limit = limit
        self.loads = []

    async def load_context(self, ref):
        self.loads.append(ref)
        return self.values[ref]

    def context_accepts(self, messages):
        return estimate_tokens(messages) <= self.limit


def source(content="UNIQUE_SOURCE_BODY", *, version="hash-1", reader="reader-1"):
    return {
        "schema_version": "finance.h2.material.v1", "source_task_id": reader,
        "result_ref": None, "payload": {
            "schema_version": "finance.h2.tool_result.v1", "payload": {
                "tool": "article.read",
                "article": {"article_id": "article-1", "current_hash": version},
                "content": content, "start_line": 1, "end_line": 1,
                "line_count": 1, "has_more": False,
            },
        },
    }


def fixture(*, content="UNIQUE_SOURCE_BODY", duplicate=True, failed=False):
    material = source(content)
    receipt = {
        "schema_version": "finance.h2.action_observation.v1",
        "action_key": "action-1", "tool_name": "article.read",
        "status": "failed" if failed else "succeeded",
        "payload": material["payload"]["payload"],
    }
    values = {"material-1": material, "receipt-1": receipt,
              "normalized-1": {"task_id": "reader-1", "status": "succeeded",
                               "material_ref": "material-1", "summary": {"summary": "scope retained"}}}
    refs = ["material-1"]
    if duplicate:
        values["material-2"] = material
        refs.append("material-2")
    state = FinanceRootState(query="Explain the assigned source", phase="research", execution={
        "material_refs": refs, "observation_refs": ["normalized-1"],
        "action_observation_refs": ["receipt-1"], "allowed_tools": list(ROOT_TOOLS),
    })
    return values, state


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_exact_material_and_success_receipt_body_is_present_once(domain_class):
    async def run():
        values, state = fixture()
        capabilities = Context(values)
        stored = dict(capabilities.values)
        original_state = state.model_dump()
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert messages[-1]["content"].count("UNIQUE_SOURCE_BODY") == 1
        assert [item["material_ref"] for item in user["materials"]] == ["material-1", "material-2"]
        assert user["materials"][1]["payload_material_ref"] == "material-1"
        assert not user["materials"][1].get("payload_omitted")
        assert user["action_observations"][0]["payload_material_ref"] == "material-1"
        assert user["action_observations"][0]["action_key"] == "action-1"
        assert user["normalized_observations"][0]["summary"] == {"summary": "scope retained"}
        assert user["observations"][0]["payload_view"] == {
            "view": "normalized_observations", "observation_ref": "normalized-1",
        }
        assert "payload_material_ref" in messages[0]["content"]
        assert capabilities.values == stored and state.model_dump() == original_state
        assert user["known_material_refs"] == ["material-1", "material-2"]
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("source_task_id", "other-reader"), ("version", "hash-2")])
def test_different_source_identity_or_version_is_never_collapsed(field, value):
    async def run():
        first = source()
        second = source(reader=value) if field == "source_task_id" else source(version=value)
        capabilities = Context({"a": first, "b": second})
        state = FinanceRootState(query="Compare versions", phase="research", execution={"material_refs": ["a", "b"]})
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert messages[-1]["content"].count("UNIQUE_SOURCE_BODY") == 2
        assert all("payload" in item for item in user["materials"])
    asyncio.run(run())


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_failure_diagnostic_remains_complete_once_and_never_becomes_success(domain_class):
    async def run():
        diagnostic = {"tool": "article.read", "ok": False, "error": {
            "code": "forbidden", "message": "UNIQUE_FAILURE_DIAGNOSTIC",
        }}
        receipt = {"schema_version": "finance.h2.action_observation.v1",
                   "tool_name": "article.read", "status": "failed", "payload": diagnostic}
        capabilities = Context({"failed-ref": receipt})
        state = FinanceRootState(query="Disclose the gap", phase="research", execution={
            "action_observation_refs": ["failed-ref"],
        })
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert messages[-1]["content"].count("UNIQUE_FAILURE_DIAGNOSTIC") == 1
        assert user["action_observations"][0]["payload"] == diagnostic
        assert user["observations"][0]["status"] == "failed"
        assert user["observations"][0]["payload_view"]["observation_ref"] == "failed-ref"
        assert not user["known_evidence_refs"] and not user["materials"]
    asyncio.run(run())


def test_scheduler_payload_without_typed_material_remains_visible_once():
    async def run():
        receipt = {"schema_version": "finance.h2.action_observation.v1",
                   "tool_name": "result.get", "status": "succeeded",
                   "payload": {"result_ref": "res-child", "content": "UNIQUE_RESULT_PAGE",
                               "has_more": True, "next_offset": 30}}
        capabilities = Context({"page": receipt})
        state = FinanceRootState(query="Read the child result", phase="research", execution={
            "action_observation_refs": ["page"],
        })
        messages = await FinanceH2Domain(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert messages[-1]["content"].count("UNIQUE_RESULT_PAGE") == 1
        assert user["action_observations"][0]["payload"] == receipt["payload"]
        assert user["observations"][0]["payload_view"]["view"] == "action_observations"
    asyncio.run(run())


@pytest.mark.parametrize("domain_class", [FinanceH2Domain, FinanceH2ResearchDomain])
def test_pressure_retains_all_handles_without_editing_durable_records(domain_class):
    async def run():
        values, state = fixture(content="Long source text. " * 20000)
        capabilities = Context(values, limit=14000)
        stored = dict(capabilities.values)
        messages = await domain_class(object())._messages(state.query, state, [], capabilities)
        user = json.loads(messages[-1]["content"])
        assert capabilities.context_accepts(messages)
        assert user["materials"][0]["payload_omitted"] is True
        assert user["materials"][0]["expansion_tool"] == "material.get"
        assert user["known_material_refs"] == ["material-1", "material-2"]
        assert len(user["normalized_observations"]) == len(user["action_observations"]) == 1
        assert len(user["observations"]) == 2
        assert capabilities.values == stored
    asyncio.run(run())


def test_unfittable_root_fails_before_a_model_request_instead_of_returning_oversize():
    async def run():
        values, state = fixture()
        with pytest.raises(DispatchError) as error:
            await FinanceH2Domain(object())._messages(state.query, state, [], Context(values, limit=1))
        assert error.value.code == "contract_error"
    asyncio.run(run())
