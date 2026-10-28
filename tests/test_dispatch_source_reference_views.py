"""Delegated provenance must never silently become a parent's source grant."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootState
from policy_impact.runtime.actions import StateUpdate
from policy_impact.runtime.research_contracts import ResearchResult
from tests.test_dispatch_finance_research import Capabilities, _context, _result


def _old_state(*, explicit=False):
    return FinanceRootState(query="Explain the delegated source.", phase="research", execution={
        "material_refs": ["parent.wrapper", *(["source.private"] if explicit else [])],
        "evidence_refs": ["parent.wrapper", "res_child", "source.private"],
        "observation_refs": ["observation.old"],
    }, article_states=[{
        "article_id": "article.alpha", "task_id": "task.grandchild", "listed": True,
        "read_status": "complete", "use_status": "undecided", "material_ref": "parent.wrapper",
        "material_refs": ["parent.wrapper", "source.private"], "result_ref": "res_child",
        "result_refs": ["res_child"],
    }], evidence_ledger=[{"evidence_ref": "source.private", "task_id": "task.grandchild"}])


def _caps():
    return Capabilities(artifacts={"observation.old": {
        "task_id": "task.child", "status": "succeeded", "result_ref": "res_child",
        "summary": {"evidence_refs": [{"evidence_ref": "source.private"}]},
    }})


@pytest.mark.parametrize("explicit", [False, True])
def test_old_checkpoint_recovers_provenance_without_loading_private_sources(explicit):
    caps = _caps()
    state = _old_state(explicit=explicit)
    recovered, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(_context(state=state), caps))
    assert ("source.private" in recovered.execution["evidence_refs"]) is explicit
    assert ("source.private" in recovered.article_states[0]["material_refs"]) is explicit
    assert recovered.execution["delegated_source_refs"] == {"res_child": ["source.private"]}
    assert recovered.evidence_ledger[0]["evidence_ref"] == "source.private"
    assert recovered.evidence_ledger[0].get("reference_kind") == (None if explicit else "source_provenance")
    assert caps.loads == ["observation.old"] and not caps.calls and not caps.tools
    assert state.execution["evidence_refs"] == ["parent.wrapper", "res_child", "source.private"]
    caps.loads.clear()
    twice, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(_context(state=recovered), caps))
    assert twice.execution == recovered.execution and caps.loads == []


def test_state_update_cannot_restore_legacy_provenance_as_an_active_evidence_binding():
    caps = _caps()
    domain = FinanceH2Domain(None)
    state, _ = asyncio.run(domain._consume_observations(_context(state=_old_state()), caps))
    update = StateUpdate(evidence_additions=[{
        "claim_id": "claim.one", "evidence_ref": "source.private", "relation": "supports",
        "source_kind": "article", "scope": "delegated analysis", "certainty": "conditional",
    }], coverage={"evidence_refs": ["source.private"]})
    updated, warnings = asyncio.run(domain._apply_state_update(state, update, caps))
    assert "source.private" not in updated.execution["evidence_refs"]
    assert len(updated.evidence_ledger) == 1 and updated.evidence_ledger[0]["reference_kind"] == "source_provenance"
    assert "evidence_ref_not_in_run:source.private" in warnings
    assert "coverage_contains_unknown_evidence_ref" in warnings


def test_new_result_envelope_keeps_private_source_ids_out_of_callable_references():
    caps = Capabilities()
    state = FinanceRootState(query="Explain the delegated view.", phase="research")
    context = replace(_context(state=state), observations=[{"tasks": [{
        "task_id": "task.child", "status": "succeeded", "result": {
            "result_ref": "res_child", "full_content": json.dumps({"content": "Child analysis."}),
            "envelope": {"evidence_refs": [{"evidence_ref": "source.private"}]},
        },
    }]}])
    observed, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(context, caps))
    assert "source.private" not in observed.execution["evidence_refs"]
    assert "res_child" in observed.execution["evidence_refs"]
    assert observed.execution["delegated_source_refs"] == {"res_child": ["source.private"]}
    assert all(item["evidence_ref"] != "source.private" for item in observed.evidence_ledger)
    assert not caps.calls and not caps.tools


def test_old_result_get_checkpoint_restores_source_index_without_reading_source_or_result_body():
    state = _old_state()
    state = state.model_copy(update={"execution": {
        **state.execution, "observation_refs": [], "action_observation_refs": ["observation.get"],
    }})
    caps = Capabilities(artifacts={"observation.get": {
        "tool_name": "result.get", "status": "succeeded", "payload": {
            "result_ref": "res_child", "evidence_refs": [{"evidence_ref": "source.private"}],
        },
    }})
    recovered, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(_context(state=state), caps))
    assert recovered.execution["delegated_source_refs"] == {"res_child": ["source.private"]}
    assert "source.private" not in recovered.execution["evidence_refs"]
    assert recovered.article_states[0]["material_refs"] == ["parent.wrapper"]
    assert caps.loads == ["observation.get"] and not caps.calls and not caps.tools


def test_actual_local_observation_is_not_removed_when_also_present_in_delegated_provenance():
    state = _old_state()
    state = state.model_copy(update={"execution": {
        **state.execution, "observation_refs": ["observation.old", "observation.local"],
    }})
    caps = _caps()
    caps.artifacts["observation.local"] = {
        "task_id": "task.research", "status": "succeeded", "result_ref": None,
        "summary": {"evidence_refs": [{"evidence_ref": "source.private"}]},
    }
    recovered, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(_context(state=state), caps))
    assert recovered.execution["local_evidence_refs"] == ["source.private"]
    assert "source.private" in recovered.execution["evidence_refs"]
    assert "source.private" in recovered.article_states[0]["material_refs"]
    assert recovered.evidence_ledger[0].get("reference_kind") is None
    assert caps.loads == ["observation.old", "observation.local"]


@pytest.mark.parametrize("research", [False, True])
def test_final_cannot_accept_old_private_provenance_even_if_article_and_ledger_still_contain_it(research):
    state = _old_state()
    state = state.model_copy(update={"execution": {
        **state.execution, "reference_view_version": 1,
        "delegated_source_refs": {"res_child": ["source.private"]},
    }})
    output = _result("source.private") if research else {
        "answer": "Preserve the full conditional analysis.", "evidence_refs": ["source.private"],
    }
    domain = FinanceH2ResearchDomain(None) if research else FinanceH2Domain(None)
    caps = Capabilities()
    step = asyncio.run(domain._final(_context(state=state), state, output, caps))
    assert step.submission.evidence_refs == []
    assert step.submission.warnings and step.submission.status == "partial"
    assert step.state.article_states[0]["use_status"] == "undecided"
    assert caps.loads == [] and caps.calls == [] and caps.tools == []
    original = ResearchResult.model_validate(output, strict=True).model_dump(mode="json") if research else output
    assert any(item.get("result") == original or item.get("output") == original for item in caps.artifacts.values())
