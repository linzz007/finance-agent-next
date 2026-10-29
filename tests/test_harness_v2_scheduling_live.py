"""Keep live acceptance facts separate from structural and user-export success."""

import hashlib
import json
import sqlite3

import pytest

from scripts.harness_v2_scheduling_live import (
    differences,
    review_trace_facts,
    source_inventory,
    trace_facts,
)


def test_root_delivery_does_not_hide_failed_child_or_summary_only_read(tmp_path):
    trace_path = tmp_path / "scheduling-trace.json"
    trace_path.write_text(json.dumps({
        "spans": [],
        "events": [
            {"event_type": "result.summary_viewed", "payload": {
                "result_ref": "result.a", "reader_task_id": "root", "reader_kind": "agent",
            }},
            {"event_type": "result.full_viewed", "payload": {
                "result_ref": "result.a", "reader_task_id": "root", "reader_kind": "user",
            }},
        ],
    }), encoding="utf-8")
    with sqlite3.connect(tmp_path / "h2.sqlite") as connection:
        connection.executescript("""
            CREATE TABLE h2_tasks (task_id TEXT, parent_task_id TEXT, status TEXT, result_ref TEXT);
            CREATE TABLE h2_model_calls (
                call_id TEXT, task_id TEXT, started_at INTEGER, finished_at INTEGER, status TEXT);
            INSERT INTO h2_tasks VALUES ('root', NULL, 'succeeded', 'root.result');
            INSERT INTO h2_tasks VALUES ('a', 'root', 'succeeded', 'result.a');
            INSERT INTO h2_tasks VALUES ('b', 'root', 'failed', NULL);
            INSERT INTO h2_model_calls VALUES ('a1', 'a', 100, 200, 'succeeded');
            INSERT INTO h2_model_calls VALUES ('b1', 'b', 150, 210, 'unknown');
        """)
    record = {
        "case": "example", "query": "Compare two independent analyses.",
        "status": "succeeded", "delivered": True, "duration_seconds": 2,
        "exports": {"files": {"scheduling_trace": str(trace_path)}},
    }
    facts = trace_facts(tmp_path, record)
    assert facts["delivered"]
    assert not facts["all_children_succeeded"]
    assert not facts["all_child_results_fully_read_by_parent"]
    assert facts["overlapping_sibling_requests"] == [["a1", "b1"]]
    assert facts["physical_requests"] == 2
    assert facts["semantic_acceptance"] == "manual_review_required"


def test_live_inventory_freezes_rule_assets_and_catalog_with_same_comparison_scope():
    hashes = source_inventory()
    assert "prompts/finance/root.md" in hashes
    assert "prompts/finance/analyst.md" in hashes
    assert "config/agents/profiles/analyst.json" in hashes
    assert "tests/fixtures/h2_agent_scheduling_acceptance.json" in hashes
    assert "scripts/harness_v2_scheduling_live.py" in hashes
    assert not any(name.startswith("docs/acceptance/") for name in hashes)
    assert not any(name.startswith("docs/acceptance/") for name in hashes)
    assert differences(hashes, dict(hashes)) == {}
    changed = {**hashes, "prompts/finance/analyst.md": "changed"}
    assert differences(hashes, changed) == {
        "prompts/finance/analyst.md": {"before": hashes["prompts/finance/analyst.md"], "after": "changed"},
    }


def test_review_trace_preserves_actual_verdict_and_detects_artifact_tampering(tmp_path):
    records = tmp_path / "records"
    records.mkdir()
    artifacts = {
        "brief": {"review_policy": {"mode": "required"}},
        "result": {"review": {"status": "issues_open", "verdict": "revision_required"}},
        "correction": {"tool_name": "final", "status": "failed", "correction_number": 1,
                       "error": {"code": "contract_error", "validation_stage": "review.evidence"}},
    }
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE h2_artifacts (artifact_ref TEXT, storage_uri TEXT, byte_length INTEGER, sha256 TEXT);
            CREATE TABLE h2_tasks (task_id TEXT, status TEXT, brief_ref TEXT, thread_id TEXT, result_ref TEXT);
            CREATE TABLE h2_results (result_ref TEXT, full_ref TEXT);
            CREATE TABLE h2_threads (thread_id TEXT, role TEXT);
            INSERT INTO h2_threads VALUES ('thread', 'analyst');
            INSERT INTO h2_tasks VALUES ('task', 'partial', 'brief', 'thread', 'res');
            INSERT INTO h2_results VALUES ('res', 'result');
        """)
        for ref, value in artifacts.items():
            content = json.dumps(value).encode()
            (records / ref).write_bytes(content)
            connection.execute("INSERT INTO h2_artifacts VALUES (?,?,?,?)", (
                ref, ref, len(content), hashlib.sha256(content).hexdigest(),
            ))
        facts = review_trace_facts(connection, tmp_path)
        assert facts["review_states"][0]["review"]["verdict"] == "revision_required"
        assert facts["review_states"][0]["status"] == "partial"
        assert facts["corrective_observations"][0]["correction_number"] == 1
        (records / "result").write_text("{}")
        with pytest.raises(ValueError, match="integrity"):
            review_trace_facts(connection, tmp_path)
