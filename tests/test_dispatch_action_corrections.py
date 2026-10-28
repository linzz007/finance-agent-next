"""Bounded format recovery, latest diagnostics, immutable policy and accounting."""

import asyncio
import json

import pytest

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_rules import legacy_rules
from tests.test_dispatch_anchor_repair import RecordedCapabilities, _bad, _patch
from tests.test_dispatch_finance_research import Capabilities, _result


def _failure(number):
    return DispatchError("contract_error", retryable=True, details={
        "call_id": f"call.{number}", "output_ref": f"rejected.{number}",
        "validation_stage": "action",
        "validation_errors": [{"loc": ["output", f"field_{number}"], "type": "missing", "msg": "Field required"}],
    })


def _capabilities(responses):
    return Capabilities(responses, artifacts={
        f"rejected.{i}": ('{"PRIVATE_REJECTED_' + str(i) + '": ]').encode() for i in range(6)
    })


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("correction_number", [1, 2, 5])
def test_correction_success_stops_early_and_replaces_latest_diagnostic(research, correction_number):
    original = [{"role": "system", "content": "Trusted rules."},
                {"role": "user", "content": "Original task and actual source observations."}]
    final = {"type": "final", "output": _result() if research else {"answer": "Supported answer."}}
    caps = _capabilities([*[_failure(i) for i in range(correction_number)], final])
    domain = FinanceH2ResearchDomain if research else FinanceH2Domain
    response, ref = asyncio.run(domain._decide(original, caps))
    assert response.payload == final and len(caps.calls) == correction_number + 1
    for number, (messages, schema) in enumerate(caps.calls[1:], 1):
        assert messages[:-1] == original and len(messages) == len(original) + 1
        assert schema == "agent_action.v1"
        feedback = json.loads(messages[-1]["content"])
        assert feedback["correction_attempt"] == number
        assert feedback["correction_limit"] == 5
        assert feedback["corrections_remaining_after_this"] == 5 - number
        assert feedback["validation_errors"][0]["loc"] == ["output", f"field_{number - 1}"]
        assert "PRIVATE_REJECTED" not in json.dumps(feedback)
        assert "rejected." not in json.dumps(feedback)
    summary = caps.artifacts[ref]
    assert summary["limit"] == 5 and summary["correction_count"] == correction_number
    assert summary["status"] == "corrected" and summary["correction_attempted"]
    assert len(summary["request_refs"]) == len(summary["outcome_refs"]) == correction_number
    requests = [caps.artifacts[item] for item in summary["request_refs"]]
    assert [item["failed_output_ref"] for item in requests] == [f"rejected.{i}" for i in range(correction_number)]
    assert len(original) == 2 and not caps.tools


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_exhaustion_keeps_audit_and_cannot_reset_in_another_attempt(research, legacy):
    limit = 1 if legacy else 5
    caps = _capabilities([_failure(i) for i in range(limit + 1)])
    if legacy:
        caps.instruction_rules = legacy_rules().snapshot()
    domain = FinanceH2ResearchDomain if research else FinanceH2Domain
    with pytest.raises(DispatchError) as caught:
        asyncio.run(domain._decide([], caps))
    failure = caught.value
    assert failure.code == "contract_error" and failure.retryable is False
    assert len(caps.calls) == limit + 1 and not caps.tools
    summary = caps.artifacts[failure.details["action_repair_ref"]]
    assert summary["status"] == "failed" and summary["limit"] == summary["correction_count"] == limit
    assert len(summary["outcome_refs"]) == len(summary["request_refs"]) == limit
    assert summary["corrected_output_ref"] == f"rejected.{limit}"
    for number, ref in enumerate(summary["outcome_refs"], 1):
        outcome = caps.artifacts[ref]
        assert outcome["status"] == "failed" and outcome["error_code"] == "contract_error"
        assert outcome["output_ref"] == f"rejected.{number}"


@pytest.mark.parametrize("code", ["budget_denied", "quota_error", "forbidden", "cancelled", "timeout", "provider_error"])
def test_nonformat_failure_interrupts_remaining_corrections(code):
    caps = _capabilities([_failure(0), _failure(1), DispatchError(code, retryable=True)])
    with pytest.raises(DispatchError) as caught:
        asyncio.run(FinanceH2Domain._decide([], caps))
    assert caught.value.code == code and not caught.value.retryable
    assert len(caps.calls) == 3
    summary = caps.artifacts[caught.value.details["action_repair_ref"]]
    assert summary["correction_count"] == 2 and summary["error_code"] == code


def test_five_corrections_share_one_extra_transport_resend_at_most_seven_requests():
    final = {"type": "final", "output": {"answer": "Supported answer."}}
    caps = _capabilities([_failure(0), DispatchError("transport_error"),
                          *[_failure(i) for i in range(1, 5)], final])
    response, ref = asyncio.run(FinanceH2Domain._decide([], caps))
    assert response.payload == final and len(caps.calls) == 7
    assert caps.calls[1] == caps.calls[2]
    summary = caps.artifacts[ref]
    assert summary["correction_count"] == len(summary["request_refs"]) == 5
    assert len(summary["outcome_refs"]) == 6
    assert caps.artifacts[summary["transport_retry_ref"]]["retry_number"] == 1


def test_second_transport_failure_in_different_correction_cannot_get_another_resend():
    caps = _capabilities([_failure(0), DispatchError("transport_error", retryable=True),
                          _failure(1), DispatchError("transport_error", retryable=True)])
    with pytest.raises(DispatchError) as caught:
        asyncio.run(FinanceH2Domain._decide([], caps))
    assert len(caps.calls) == 4 and not caught.value.retryable
    assert caught.value.code == "transport_error"
    feedback = json.loads(caps.calls[-1][0][-1]["content"])
    assert feedback["correction_attempt"] == 2


def test_later_anchor_correction_keeps_original_body_and_patch_scope():
    bad = _bad()
    invalid = _patch()
    invalid["output"]["anchors"][0]["text"] = "New unsupported analysis."
    caps = RecordedCapabilities([bad, invalid, _patch()])
    response, ref = asyncio.run(FinanceH2ResearchDomain._decide([], caps))
    assert len(caps.calls) == 3
    assert response.payload["output"]["content"] == bad["output"]["content"]
    feedback = json.loads(caps.calls[-1][0][-1]["content"])
    assert feedback["correction_attempt"] == 2
    assert feedback["validation_stage"] == "final.output.anchor_patch"
    assert "New unsupported analysis" not in json.dumps(feedback)
    assert caps.artifacts[ref]["correction_count"] == 2
    assert caps.artifacts[ref]["status"] == "corrected"
