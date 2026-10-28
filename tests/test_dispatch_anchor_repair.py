"""Immutable anchor recovery: useful prose is never regenerated as format repair."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_anchor_repair import ResearchAnchorRepair
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.runtime.actions import AgentAction
from policy_impact.runtime.research_contracts import ResearchResult
from tests.test_dispatch_finance_research import Capabilities, _context, _result, _source


class RecordedCapabilities(Capabilities):
    async def complete(self, messages, schema_name):
        try:
            response = await super().complete(messages, schema_name)
        except DispatchError as exc:
            exc.details.setdefault("call_id", f"call.{len(self.calls)}")
            exc.details.setdefault("output_ref", f"raw.{len(self.calls)}")
            self.artifacts[exc.details["output_ref"]] = b"Rejected public action"
            raise
        ref = f"raw.{len(self.calls)}"
        self.artifacts[ref] = json.dumps(response.payload).encode()
        return SimpleNamespace(payload=response.payload, metadata={
            "call_id": f"call.{len(self.calls)}", "output_ref": ref,
        })


def _bad():
    output = _result()
    output["content"] += " Supply recovery is necessary, but uncertain."
    output["candidate_answer"] = "A conditional scenario, not a contradiction."
    output["evidence_links"] += [
        {"text": "Rejected first anchor", "evidence_refs": ["material.alpha"],
         "quotes": [{"evidence_ref": "material.alpha", "text": _result()["content"]}]},
        {"text": "Rejected second anchor", "evidence_refs": ["material.alpha"]},
    ]
    output["limitations"] = ["Not independently verified."]
    return {"type": "final", "output": output}


def _patch(indices=(1, 2)):
    texts = {1: "Supply recovery is necessary, but uncertain.",
             2: "A conditional scenario, not a contradiction."}
    return {"type": "final", "output": {"schema_version": "finance.h2.anchor_patch.v1",
            "anchors": [{"link_index": index, "text": texts[index]} for index in indices]}}


def test_anchor_feedback_exposes_only_the_whole_patch_action_schema():
    repair = ResearchAnchorRepair.prepare(_bad())
    feedback = repair.feedback()
    schema = feedback["action_json_schema"]
    assert set(schema["properties"]) == set(schema["required"]) == {"type", "output"}
    assert schema["properties"]["type"]["const"] == "final"
    assert schema["additionalProperties"] is False
    assert "oneOf" not in schema and "anyOf" not in schema
    output = schema["$defs"][schema["properties"]["output"]["$ref"].rsplit("/", 1)[1]]
    assert set(output["properties"]) == set(output["required"]) == {"schema_version", "anchors"}
    assert output["additionalProperties"] is False
    selection = schema["$defs"][output["properties"]["anchors"]["items"]["$ref"].rsplit("/", 1)[1]]
    assert set(selection["properties"]) == set(selection["required"]) == {"link_index", "text"}
    assert selection["additionalProperties"] is False
    assert feedback["final_output_schema_scope"] == "final.output"
    assert feedback["final_output_json_schema"]["properties"] == output["properties"]
    assert "action_json_schema" in feedback["instruction"]
    assert "not source quotes" in feedback["instruction"]


def test_anchor_feedback_example_is_a_complete_valid_empty_patch():
    repair = ResearchAnchorRepair.prepare(_bad())
    example = repair.feedback()["action_example"]
    assert example == _patch(())
    assert TypeAdapter(AgentAction).validate_python(example).type == "final"
    response = SimpleNamespace(payload=example, metadata={})
    assembled = repair.assemble(response)
    assert assembled.payload["output"]["content"] == _bad()["output"]["content"]
    assert assembled.metadata["research_anchor_repair"]["dropped_indices"] == [1, 2]


def test_only_anchor_correction_replaces_the_generic_action_schema():
    bad = _bad()
    caps = RecordedCapabilities([bad, _patch()])
    messages = [{"role": "system", "content": "Keep original trustworthy context."}]
    asyncio.run(FinanceH2ResearchDomain._decide(messages, caps))
    feedback = json.loads(caps.calls[1][0][-1]["content"])
    assert caps.calls[0] == (messages, "agent_action.v1")
    assert caps.calls[1][0][:-1] == messages and caps.calls[1][1] == "agent_action.v1"
    assert feedback["action_json_schema"] == ResearchAnchorRepair.prepare(bad).feedback()["action_json_schema"]
    assert feedback["action_example"] == _patch(())
    assert feedback["action_json_schema"] != TypeAdapter(AgentAction).json_schema()


def test_patch_freezes_all_other_fields_and_keeps_three_distinct_artifacts():
    bad = _bad()
    original = deepcopy(bad)
    caps = RecordedCapabilities([bad, _patch()])
    messages = [{"role": "system", "content": "Use the trusted actual observations."}]
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide(messages, caps))
    result = response.payload["output"]
    for field in original["output"]:
        if field != "evidence_links":
            assert result[field] == original["output"][field]
    for before, after in zip(original["output"]["evidence_links"], result["evidence_links"]):
        assert {k: v for k, v in before.items() if k != "text"} == {
            k: v for k, v in after.items() if k != "text"}
    assert result["evidence_links"][0] == original["output"]["evidence_links"][0]
    assert ResearchResult.model_validate(result, strict=True)
    assert bad == original and json.loads(caps.artifacts["raw.1"]) == original
    assert json.loads(caps.artifacts["raw.2"]) == _patch()
    link = caps.artifacts[ref]
    assembly = caps.artifacts[link["assembly_ref"]]
    frozen = caps.artifacts[assembly["frozen_candidate_ref"]]
    assert assembly["action"] == response.payload and frozen["action"] == original
    assert assembly["body_sha256"] == frozen["body_sha256"]
    assert assembly["repaired_indices"] == [1, 2] and assembly["dropped_indices"] == []
    request = caps.artifacts[link["request_ref"]]
    assert request["input_policy"] == "finance.h2.immutable_anchor_correction.v1"
    feedback = json.loads(caps.calls[1][0][-1]["content"])
    assert feedback["untrusted_draft"]["trust"] == "untrusted_analysis_not_observations"
    assert "Rejected first anchor" not in json.dumps(feedback) and "raw.1" not in json.dumps(feedback)
    assert "quotes" not in feedback["untrusted_draft"]
    assert caps.calls[1][0][:-1] == messages and not caps.tools


def test_all_repaired_anchors_keep_full_delivery_and_source_lineage():
    bad = _bad()
    caps = RecordedCapabilities([bad, _patch()], artifacts={"material.alpha": _source()})
    step = asyncio.run(FinanceH2ResearchDomain(None)(_context(), caps))
    saved = caps.artifacts[step.submission.full_ref]
    assert step.status == "succeeded" and len(caps.calls) == 2
    assert saved["result"]["content"] == bad["output"]["content"]
    assert saved["result"]["candidate_answer"] == bad["output"]["candidate_answer"]
    assert len(saved["result"]["evidence_links"]) == 3
    assert saved["accepted_evidence_refs"] == ["material.alpha"]
    assert step.submission.sections["finance_sources"]
    assert saved["source_reads"]["material.alpha"][0]["article"]["author"] == "Alpha"


def test_patch_rejected_by_adapter_keeps_body_and_only_original_valid_bindings():
    caps = RecordedCapabilities([_bad(), *[DispatchError("contract_error", retryable=True) for _ in range(5)]])
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert len(caps.calls) == 6 and len(response.payload["output"]["evidence_links"]) == 1
    assert response.payload["output"]["content"] == _bad()["output"]["content"]
    assert caps.artifacts[ref]["correction_call_id"] == "call.6"
    assert caps.artifacts[ref]["corrected_output_ref"] == "raw.6"


@pytest.mark.parametrize("indices", [(), (1,), (2,)])
def test_unselected_anchors_deliver_full_partial_analysis_with_known_sources(indices):
    bad = _bad()
    caps = RecordedCapabilities([bad, _patch(indices)], artifacts={"material.alpha": _source()})
    step = asyncio.run(FinanceH2ResearchDomain(None)(_context(), caps))
    saved = caps.artifacts[step.submission.full_ref]
    assert step.status == step.submission.status == "partial"
    assert saved["result"]["content"] == bad["output"]["content"]
    assert saved["result"]["candidate_answer"] == bad["output"]["candidate_answer"]
    assert len(saved["result"]["evidence_links"]) == 1 + len(indices)
    assert saved["accepted_evidence_refs"] == ["material.alpha"]
    assert saved["metadata_status"] == "incomplete_metadata"
    for index in {1, 2} - set(indices):
        assert f"research_anchor_unrepaired:{index}" in step.submission.warnings
    assert len(caps.calls) == 2 and not caps.tools


def _invalid_patch(mode):
    patch = _patch()
    if mode == "rewrite":
        patch["output"]["content"] = "The author contradicts himself."
    elif mode == "new-ref":
        patch["output"]["anchors"][0]["evidence_refs"] = ["material.invented"]
    elif mode == "new-quote":
        patch["output"]["quotes"] = [{"text": "Invented"}]
    elif mode == "state":
        patch["state_update"] = {"review": {"status": "complete"}}
    elif mode == "tool":
        patch = {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": "x"}}
    elif mode == "whole-final":
        patch = {"type": "final", "output": _result()}
    elif mode == "bare":
        patch = patch["output"]
    elif mode == "outer-extra":
        patch["reason"] = "Extra outer fields are not allowed."
    elif mode == "missing-type":
        del patch["type"]
    elif mode == "wrong-type":
        patch["type"] = "tool_call"
    elif mode == "duplicate":
        patch["output"]["anchors"] *= 2
    elif mode == "not-verbatim":
        patch["output"]["anchors"][0]["text"] = "A newly invented conclusion"
    else:
        patch["output"]["anchors"][0]["link_index"] = {
            "valid-index": 0, "unknown": 99, "negative": -1, "bool": True, "string": "1",
        }[mode]
    return patch


@pytest.mark.parametrize("mode", [
    "rewrite", "new-ref", "new-quote", "state", "tool", "whole-final", "duplicate",
    "not-verbatim", "valid-index", "unknown", "negative", "bool", "string",
    "bare", "outer-extra", "missing-type", "wrong-type",
])
def test_invalid_patch_cannot_modify_body_sources_or_execute_a_tool(mode):
    bad = _bad()
    patch = _invalid_patch(mode)
    caps = RecordedCapabilities([bad, *[deepcopy(patch) for _ in range(5)]], artifacts={"material.alpha": _source()})
    step = asyncio.run(FinanceH2ResearchDomain(None)(_context(), caps))
    saved = caps.artifacts[step.submission.full_ref]
    expected = deepcopy(bad["output"])
    expected["evidence_links"] = expected["evidence_links"][:1]
    assert saved["result"] == ResearchResult.model_validate(expected).model_dump(mode="json")
    assert step.status == "partial" and len(caps.calls) == 6 and not caps.tools
    assert json.loads(caps.artifacts["raw.2"]) == patch


@pytest.mark.parametrize("field,value", [
    ("content", 17), ("candidate_answer", []), ("gaps", "no"), ("extra", "not allowed"),
    ("supporting_artifact_refs", ["material.alpha", "material.alpha"]),
])
def test_non_anchor_field_errors_keep_isolated_correction(field, value):
    bad = _bad()
    bad["output"][field] = value
    assert ResearchAnchorRepair.prepare(bad) is None
    caps = RecordedCapabilities([bad, {"type": "final", "output": _result()}])
    response, _ = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    feedback = json.loads(caps.calls[1][0][-1]["content"])
    assert response.payload["output"] == _result() and len(caps.calls) == 2
    assert feedback["input_policy"] == "finance.h2.isolated_correction.v1"
    assert "untrusted_draft" not in feedback
    assert feedback["action_json_schema"] == TypeAdapter(AgentAction).json_schema()
    assert "action_example" not in feedback


def test_correction_transport_retransmits_exact_patch_request_once():
    caps = RecordedCapabilities([_bad(), DispatchError("transport_error", retryable=True), _patch()])
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert len(caps.calls) == 3 and caps.calls[1] == caps.calls[2]
    assert response.payload["output"]["content"] == _bad()["output"]["content"]
    retry = caps.artifacts[caps.artifacts[ref]["transport_retry_ref"]]
    assert retry["failed_call_id"] == "call.2" and retry["retry_number"] == 1
    assert caps.artifacts[ref]["correction_call_id"] == "call.3"


@pytest.mark.parametrize("code", [
    "budget_denied", "quota_error", "cancelled", "stale_lease", "provider_error", "forbidden", "timeout",
])
def test_non_contract_failures_are_not_hidden_as_success(code):
    caps = RecordedCapabilities([_bad(), DispatchError(code, retryable=True)])
    with pytest.raises(DispatchError) as caught:
        asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert caught.value.code == code and not caught.value.retryable
    assert len(caps.calls) == 2
    assert any(isinstance(v, dict) and v.get("schema_version") == "finance.h2.frozen_research_candidate.v1"
               for v in caps.artifacts.values())


def test_second_transport_error_does_not_restart_or_accept_unknown_output():
    caps = RecordedCapabilities([_bad(), DispatchError("transport_error"), DispatchError("transport_error")])
    with pytest.raises(DispatchError, match="transport_error"):
        asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert len(caps.calls) == 3


def test_capacity_fallback_has_no_correction_charge_or_false_correction_call_id():
    caps = RecordedCapabilities([_bad()], input_chars=1)
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert len(caps.calls) == 1
    assert response.payload["output"]["content"] == _bad()["output"]["content"]
    assert len(response.payload["output"]["evidence_links"]) == 1
    link = caps.artifacts[ref]
    assert link["correction_attempted"] is False and link["correction_call_id"] is None
    assert link["corrected_output_ref"] is None
    assert caps.artifacts[link["assembly_ref"]]["fallback_reason"] == "context_budget_exceeded"


@pytest.mark.parametrize("fault", ["unknown-source", "false-quote"])
def test_anchor_patch_cannot_bypass_normal_source_and_quote_validation(fault):
    bad = _bad()
    if fault == "unknown-source":
        bad["output"]["evidence_links"][1]["evidence_refs"] = ["material.foreign"]
        bad["output"]["evidence_links"][1]["quotes"] = []
    else:
        bad["output"]["evidence_links"][1]["quotes"][0]["text"] = "Invented source quote."
    caps = RecordedCapabilities([bad, _patch()], artifacts={"material.alpha": _source()})
    step = asyncio.run(FinanceH2ResearchDomain(None)(_context(), caps))
    assert step.status == "partial" and step.submission.warnings
    assert caps.artifacts[step.submission.full_ref]["result"]["content"] == bad["output"]["content"]
    if fault == "unknown-source":
        assert {"evidence_ref": "material.foreign"} not in step.submission.evidence_refs
    else:
        assert not step.submission.evidence_refs


def test_valid_result_has_no_anchor_repair_or_additional_call():
    good = {"type": "final", "output": _result()}
    caps = RecordedCapabilities([good])
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert response.payload == good and ref is None and len(caps.calls) == 1


def test_child_protocol_names_the_runtime_only_patch_exception():
    caps = RecordedCapabilities([{"type": "final", "output": _result()}],
                                artifacts={"material.alpha": _source()})
    asyncio.run(FinanceH2ResearchDomain(None)(_context(), caps))
    system = caps.calls[0][0][0]["content"]
    assert "Runtime's last correction feedback" in system
    assert "finance.h2.immutable_anchor_correction.v1" in system
    assert "without rewriting analysis or calling tools" in system
