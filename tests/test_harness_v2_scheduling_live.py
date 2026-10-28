"""Keep live acceptance facts separate from structural and user-export success."""

import json
import sqlite3

from scripts.harness_v2_scheduling_live import differences, source_inventory, trace_facts


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
    assert differences(hashes, dict(hashes)) == {}
    changed = {**hashes, "prompts/finance/analyst.md": "changed"}
    assert differences(hashes, changed) == {
        "prompts/finance/analyst.md": {"before": hashes["prompts/finance/analyst.md"], "after": "changed"},
    }
