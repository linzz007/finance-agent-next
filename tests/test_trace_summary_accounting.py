"""Offline accounting regressions for audited provider and shared-source traces."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from policy_impact.app.chat_workbench_service import ChatWorkbenchService


def test_provider_metadata_locations_and_runtime_precedence():
    steps = [
        {"step_type": "model_call", "metadata": {"adapter": "AnthropicCompatibleModelAdapter"},
         "output_payload": {"metadata": {"provider": "anthropic-compatible", "attempts": 2,
                                          "total_latency_ms": 120, "latency_ms": 20}}},
        {"step_type": "model_call", "metadata": {"adapter": "AnthropicCompatibleModelAdapter",
                                                  "attempts": 1, "latency_ms": 30}},
        {"step_type": "model_call", "metadata": {"provider": "test", "deterministic_action": False},
         "output_payload": {"metadata": {"deterministic_action": True, "attempts": 100}}},
        {"step_type": "runtime_action", "metadata": {"provider": "inherited", "attempts": 100}},
        {"step_type": "model_call", "metadata": {"adapter": "ScriptedModelAdapter"}},
        {"step_type": "model_call", "output_payload": {"response_id": "opaque-id", "usage": {"tokens": 2}}},
    ]
    result = ChatWorkbenchService._summarize_model_calls(steps)

    assert result["call_count"] == 5
    assert result["provider_call_count"] == 2
    assert result["runtime_action_count"] == 2
    assert result["unclassified_call_count"] == 2
    assert result["provider_total_attempts"] == result["total_attempts"] == 3
    assert result["provider_attempts_complete"] is True
    assert result["retried_call_count"] == 1
    assert result["total_latency_ms"] == 150


@pytest.mark.parametrize("attempts", [None, True, -1, "2", 1.5])
def test_missing_or_invalid_attempts_are_not_inferred(attempts):
    result = ChatWorkbenchService._summarize_model_calls([
        {"step_type": "model_call", "metadata": {"provider": "test", "attempts": attempts}},
        {"step_type": "model_call", "metadata": {"provider": "test", "attempts": 0,
                                                  "total_latency_ms": 0, "latency_ms": 50}},
    ])

    assert result["provider_call_count"] == 2
    assert result["provider_total_attempts"] == 0
    assert result["provider_attempts_recorded_call_count"] == 1
    assert result["provider_attempts_complete"] is False
    assert result["total_latency_ms"] == 0


def test_shared_sources_are_not_double_counted_across_stage_and_summary():
    manifest = {"source_statuses": [{"source_id": "a", "status": "ok"},
                                    {"source_id": "b", "status": "error"}],
                "snapshot_path": "saved/shared.json", "collected_at": "2026-09-10T01:00:00Z"}
    result = ChatWorkbenchService._summarize_sources([
        {"step_type": "workflow_stage", "output_payload": {"source_snapshot": {
            "manifest": manifest, "items": [{}, {}, {}],
        }}},
        {"step_type": "workflow_summary", "output_payload": {
            "source_manifest": manifest, "news_source_count": 2, "event_count": 3,
            "report_request": {"source_pool": ["a", "b"]},
        }},
    ])

    assert result["recorded"] is True
    assert result["source_count"] == result["planned_source_count"] == 2
    assert result["successful_source_count"] == result["failed_source_count"] == 1
    assert result["snapshot_item_count"] == 3
    assert result["snapshot_path"] == "saved/shared.json"
    assert result["snapshot_time"] == "2026-09-10T01:00:00Z"
    assert result["news_source_count"] is None
    assert result["policy_source_count"] is None
    assert result["enabled_news_source_count"] is None


def test_source_snapshot_stage_is_available_before_summary():
    result = ChatWorkbenchService._summarize_sources([
        {"step_type": "workflow_stage", "output_payload": {"source_snapshot": {
            "manifest": {"source_statuses": []}, "items": [],
        }}},
    ])

    assert result["recorded"] is True
    assert result["source_count"] == result["snapshot_item_count"] == 0


def test_route_only_is_not_source_evidence_and_missing_is_not_zero():
    steps = [{"step_type": "workflow_stage", "output_payload": {"internal_route": "news"}}]
    view = ChatWorkbenchService._build_observability_view({}, steps)

    assert view["trace_summary"]["has_source_summary"] is False
    assert view["source_summary"]["source_count"] is None
    assert view["source_summary"]["snapshot_item_count"] is None
    assert view["source_summary"]["news_source_count"] is None


def test_legacy_inventory_preserves_explicit_zero_and_missing_enabled_count():
    result = ChatWorkbenchService._summarize_sources([
        {"step_type": "tool_call", "output_payload": {
            "news_source_count": 4, "policy_source_count": 0,
            "snapshots": {"news": {"item_count": 0, "snapshot_path": "empty.json"}},
        }},
    ])

    assert result["recorded"] is True
    assert result["news_source_count"] == 4
    assert result["policy_source_count"] == result["snapshot_news_count"] == 0
    assert result["enabled_news_source_count"] is None


def test_tool_summary_counts_failed_tool_results_even_when_call_record_is_ok():
    view = ChatWorkbenchService._build_observability_view({}, [
        {
            "step_type": "tool_result",
            "status": "done",
            "tool_calls": [{
                "call_id": "tool-ok",
                "tool_name": "article.list",
                "status": "ok",
                "allowed": True,
                "policy_decision": "allow",
            }],
        },
        {
            "step_type": "tool_result",
            "status": "failed",
            "tool_calls": [{
                "call_id": "tool-failed",
                "tool_name": "article.read",
                "status": "ok",
                "allowed": True,
                "policy_decision": "allow",
            }],
            "metadata": {"tool_call_id": "tool-failed"},
            "output_payload": {"observation": {
                "category": "tool_failed",
                "tool_name": "article.read",
            }},
        },
    ])

    assert view["tool_summary"]["total_calls"] == 2
    assert view["tool_summary"]["failed_calls"] == 1


@pytest.mark.parametrize("case,logical,attempts,events", [
    ("SD03", 4, 4, 66), ("SD04", 6, 7, 57),
])
def test_saved_initial_trace_reconciles_offline(case, logical, attempts, events):
    path = Path(__file__).resolve().parents[1] / "data/acceptance/shared_discovery_initial_20260910/cases" / case / "trace.json"
    if not path.exists():
        pytest.skip("Local saved acceptance trace is not part of the source checkout")
    trace = json.loads(path.read_text(encoding="utf-8"))
    before = deepcopy(trace)
    view = ChatWorkbenchService._build_observability_view(trace, trace["steps"])
    model = view["model_summary"]
    source = view["source_summary"]

    assert model["provider_call_count"] == logical
    assert model["provider_total_attempts"] == attempts
    assert model["provider_attempts_complete"] is True
    assert model["runtime_action_count"] == 1
    assert model["unclassified_call_count"] == 0
    assert model["retried_call_count"] == (1 if case == "SD04" else 0)
    assert source["source_count"] == source["planned_source_count"] == 17
    assert source["successful_source_count"] + source["failed_source_count"] == 17
    assert source["snapshot_item_count"] == events
    assert source["snapshot_path"] and source["snapshot_time"]
    assert view["trace_summary"]["has_source_summary"] is True
    assert trace == before
