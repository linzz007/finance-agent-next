"""Archived paid Runs remain immutable while using the Workbench renderer shape."""

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from policy_impact.app.h2_archives import H2Archives
from policy_impact.app.h2_workbench_api import register_h2_workbench


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "runtime-records"
    case = root / "batch-r1" / "cases" / "SCH-02"
    artifacts = case / "records" / "runs" / "run_fixture" / "artifacts"
    artifacts.mkdir(parents=True)
    record = {
        "case": "SCH-02", "query": "Compare two sources and review the result.",
        "handle": {"run_id": "run_fixture", "task_id": "root"},
        "status": "succeeded", "model_profile": "fixture", "transport_backend": "fakeredis",
        "started_at": "2026-10-28T01:00:00+08:00",
        "scheduler_config": {"root_task_calls": 120, "child_task_calls": 60,
                             "root_calls": 600, "run_ms": 3600000},
        "metrics": {"model_calls": {"succeeded": 1, "unknown": 1}},
        "finance_delivery": {"warnings": ["Final draft was not reviewed."]},
    }
    (case / "record.json").write_text(json.dumps(record), encoding="utf-8")
    spans = [
        {"span_type": "attempt", "span_id": "attempt_root", "task_id": "root",
         "attempt_no": 1, "status": "succeeded"},
        {"span_type": "attempt", "span_id": "attempt_child", "task_id": "child",
         "attempt_no": 1, "status": "failed", "error_code": "contract_error"},
        {"span_type": "model_call", "span_id": "call_root", "task_id": "root", "status": "succeeded"},
        {"span_type": "model_call", "span_id": "call_child", "task_id": "child", "status": "unknown"},
    ]
    events = [{"event_id": str(i), "event_type": "task.waiting", "payload": {}} for i in range(1, 202)]
    (artifacts.parent / "scheduling-trace.json").write_text(
        json.dumps({"trace_id": "run_fixture", "spans": spans, "events": events}), encoding="utf-8",
    )
    with sqlite3.connect(case / "h2.sqlite") as db:
        db.executescript("""
            CREATE TABLE h2_tasks (task_id TEXT,run_id TEXT,thread_id TEXT,parent_task_id TEXT,
                budget_ref TEXT,status TEXT,objective TEXT,result_ref TEXT,created_at INTEGER);
            CREATE TABLE h2_threads (thread_id TEXT,role TEXT,role_version TEXT,depth INTEGER);
            CREATE TABLE h2_budgets (budget_id TEXT,self_call_limit INTEGER);
            CREATE TABLE h2_results (run_id TEXT,result_ref TEXT,full_ref TEXT,disposition TEXT DEFAULT 'accepted');
            CREATE TABLE h2_artifacts (run_id TEXT,artifact_ref TEXT,storage_uri TEXT,
                sha256 TEXT,byte_length INTEGER);
            INSERT INTO h2_tasks VALUES
                ('root','run_fixture','thread_root',NULL,'budget_root','succeeded','Integrate','res_root',1),
                ('child','run_fixture','thread_child','root','budget_child','failed','Analyze',NULL,2),
                ('review','run_fixture','thread_review','child','budget_child','partial','Review','res_review',3);
            INSERT INTO h2_threads VALUES ('thread_root','main','1',0),('thread_child','analyst','2',1),
                ('thread_review','verifier','1',2);
            INSERT INTO h2_budgets VALUES ('budget_root',120),('budget_child',60);
            INSERT INTO h2_results (run_id,result_ref,full_ref) VALUES ('run_fixture','res_root','art_answer'),
                ('run_fixture','res_review','art_review');
        """)
        for ref, body, run in [("art_answer", "Answer with explicit gaps." * 1000, "run_fixture"),
                               ("art_review", '{"status":"partial"}', "run_fixture"),
                               ("art_foreign", "Private foreign result", "other_run")]:
            raw = body.encode()
            (artifacts / ref).write_bytes(raw)
            db.execute("INSERT INTO h2_artifacts VALUES (?,?,?,?,?)", (
                run, ref, str((artifacts / ref).relative_to(case / "records")),
                hashlib.sha256(raw).hexdigest(), len(raw),
            ))
    reader = H2Archives(root)
    app = FastAPI()
    app.state.h2_workbench = SimpleNamespace(archives=reader)
    register_h2_workbench(app, tmp_path)
    return reader, case, TestClient(app)


def test_archive_uses_full_answer_and_real_child_failures_without_writing(archive):
    reader, case, client = archive
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob("*") if p.is_file()}
    rows = client.get("/api/h2-workbench/archives").json()["archives"]
    assert len(rows) == 1
    identity = rows[0]["archive_id"]
    response = client.get(f"/api/h2-workbench/archives/{identity}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["readonly"]
    assert len(data["run"]["answer"]) > 16000
    view = data["view"]
    assert [task["status"] for task in view["status"]["tasks"]] == ["succeeded", "failed", "partial"]
    assert view["status"]["tasks"][1]["attempts"][0]["error_code"] == "contract_error"
    assert view["status"]["tasks"][1]["budget"] == {"consumed": 1, "remaining": 59}
    assert view["metrics"]["model_calls"]["unknown"] == 1
    assert view["budget"]["root"] == 120
    assert len(view["trace"]["events"]) == 200 and view["status"]["has_more"]
    page = reader.view(identity, after_event_id=view["status"]["next_event_id"])
    assert [event["event_id"] for event in page["view"]["trace"]["events"]] == ["201"]
    assert not page["view"]["status"]["has_more"]
    assert reader.artifact(identity, "art_answer", offset=10, limit=5)["content"] == data["run"]["answer"][10:15]
    assert client.get(f"/api/h2-workbench/archives/{identity}/artifacts/art_review").status_code == 200
    assert client.post(f"/api/h2-workbench/archives/{identity}/cancel").status_code == 404
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob("*") if p.is_file()}
    assert after == before


def test_archive_rejects_foreign_refs_bad_hashes_and_unknown_identity(archive):
    reader, case, client = archive
    identity = reader.catalog()[0]["archive_id"]
    prefix = f"/api/h2-workbench/archives/{identity}"
    assert client.get("/api/h2-workbench/archives/not-registered").status_code == 404
    assert client.get(prefix + "/artifacts/art_foreign").status_code == 404
    assert client.get(prefix + "/artifacts/art_answer?offset=-1").status_code == 422
    (case / "records/runs/run_fixture/artifacts/art_answer").write_text("tampered", encoding="utf-8")
    assert client.get(prefix + "/artifacts/art_answer").status_code == 503
    assert client.get(prefix).status_code == 503


def test_archive_cannot_follow_database_paths_outside_run(archive):
    reader, case, client = archive
    identity = reader.catalog()[0]["archive_id"]
    with sqlite3.connect(case / "h2.sqlite") as db:
        db.execute("UPDATE h2_artifacts SET storage_uri='../private' WHERE artifact_ref='art_review'")
    assert client.get(f"/api/h2-workbench/archives/{identity}/artifacts/art_review").status_code == 503
    record = json.loads((case / "record.json").read_text())
    record["handle"]["run_id"] = "../../private"
    (case / "record.json").write_text(json.dumps(record), encoding="utf-8")
    assert reader.catalog() == []


def test_archive_adds_runtime_review_without_rewriting_historical_snapshot(archive):
    from tests.test_dispatch_review_presentation import result_wrapper
    reader, case, client = archive
    wrapper = result_wrapper()
    raw = json.dumps(wrapper, ensure_ascii=False).encode()
    path = case / "records" / "runs" / "run_fixture" / "artifacts" / "art_analysis"
    path.write_bytes(raw)
    with sqlite3.connect(case / "h2.sqlite") as db:
        db.execute("INSERT INTO h2_results VALUES (?,?,?,?)", (
            "run_fixture", "res_analysis", "art_analysis", "accepted"))
        db.execute("UPDATE h2_tasks SET result_ref='res_analysis',status='succeeded' WHERE task_id='child'")
        db.execute("INSERT INTO h2_artifacts VALUES (?,?,?,?,?)", (
            "run_fixture", "art_analysis", str(path.relative_to(case / "records")),
            hashlib.sha256(raw).hexdigest(), len(raw)))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob("*") if p.is_file()}
    identity = reader.catalog()[0]["archive_id"]
    data = client.get(f"/api/h2-workbench/archives/{identity}").json()
    status = data["run"]["metadata"]["review_statuses"][0]
    assert status["label"] == "核验通过"
    assert status == data["view"]["review_statuses"][0]
    assert status == data["view"]["status"]["tasks"][1]["review_status"]
    page = client.get(f"/api/h2-workbench/archives/{identity}/artifacts/art_analysis?limit=1").json()
    assert page["has_more"] and page["content"] == raw.decode()[:1]
    assert page["review_status"]["status"] == "passed"
    assert "快照" in page["snapshot_notice"]
    assert reader.artifact(identity, "art_analysis")["content"] == raw.decode()
    assert path.read_bytes() == raw
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob("*") if p.is_file()}
    assert after == before


def test_report_text_cannot_impersonate_an_accepted_analyst_review(archive):
    from tests.test_dispatch_review_presentation import result_wrapper
    reader, case, client = archive
    raw = json.dumps(result_wrapper(), ensure_ascii=False).encode()
    path = case / "records/runs/run_fixture/artifacts/art_answer"
    path.write_bytes(raw)
    with sqlite3.connect(case / "h2.sqlite") as db:
        db.execute("UPDATE h2_artifacts SET sha256=?,byte_length=? WHERE artifact_ref='art_answer'",
                   (hashlib.sha256(raw).hexdigest(), len(raw)))
    identity = reader.catalog()[0]["archive_id"]
    view = reader.view(identity)
    assert view["run"]["answer"] == raw.decode()
    assert view["view"]["review_statuses"] == []
    page = client.get(f"/api/h2-workbench/archives/{identity}/artifacts/art_answer").json()
    assert page["content"] == raw.decode() and "review_status" not in page
