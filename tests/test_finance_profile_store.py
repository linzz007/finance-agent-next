"""Profile/store tests use temporary workspaces, never real user data."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from policy_impact.company_wiki import loader
from policy_impact.domains.finance import profile
from policy_impact.domains.finance.store import FinanceConflictError, FinanceStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    loader.create_company_workspace("finance_test", "Test", "personal")
    return loader.company_dir("finance_test")


def snapshot(snapshot_id="snapshot_1"):
    return {"snapshot_id": snapshot_id, "collected_at": "2026-09-22T09:00:00+00:00",
            "period": {"start": "2026-09-16", "end": "2026-09-22"},
            "series": [], "documents": [], "source_statuses": [], "warnings": []}


def test_default_is_fresh_draft_and_missing_profile_has_no_side_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    assert profile.load_profile("no_finance") is None
    assert not (tmp_path / "data").exists()
    draft = profile.default_profile()
    assert draft["domain"] == "finance" and draft["status"] == "draft"
    assert draft["holdings"] is None and draft["report_days"] == 7
    assert draft["timezone"] == "Asia/Shanghai"
    assert draft["symbols"] == ["xauusd", "xagusd", "spx", "ixic", "dji"]
    assert draft["quality_policy"] == "strict"
    assert draft["default_session_mode"] == "latest_completed"
    assert not {"capital", "risk_preference", "experience"} & draft.keys()
    draft["symbols"].clear()
    assert len(profile.default_profile()["symbols"]) == 5


def test_profile_versions_wiki_facts_and_workspace_isolation(workspace):
    original = {path: path.read_bytes() for path in workspace.rglob("*.md")}
    saved = profile.save_profile("finance_test", profile.default_profile(), expected_version=0)
    assert saved["version"] == 1 and saved["workspace_id"] == "finance_test"
    assert profile.load_profile("finance_test") == saved
    assert profile.load_profile("another_workspace") is None
    updated = profile.save_profile("finance_test", {"status": "confirmed", "report_days": 14}, 1)
    assert updated["version"] == 2 and updated["holdings"] is None
    assert {path: path.read_bytes() for path in original} == original
    wiki = (workspace / profile.PAGE_PATH).read_text(encoding="utf-8")
    assert "CONFIRMED" in wiki and "profile_version: 2" in wiki
    with sqlite3.connect(workspace / "db" / "finance.sqlite") as conn:
        versions = conn.execute("SELECT payload_json FROM finance_profiles ORDER BY version").fetchall()
        assert [json.loads(row[0])["report_days"] for row in versions] == [7, 14]
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        value, source = conn.execute("SELECT value, source FROM company_facts "
                                     "WHERE fact_id='finance.profile.report_days'").fetchone()
        assert json.loads(value) == {"status": "confirmed", "value": 14}
        assert source == "user_confirmed"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='memory_items'").fetchone() is None


def test_draft_index_is_not_confirmed(workspace):
    profile.save_profile("finance_test", {})
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        rows = conn.execute("SELECT value, source, confidence FROM company_facts").fetchall()
    assert rows
    assert all(json.loads(value)["status"] == "draft" and source == "project_design" and confidence < .7
               for value, source, confidence in rows)


@pytest.mark.parametrize("has_finance_db", [False, True])
def test_nonfinance_workspace_probe_is_strictly_read_only(workspace, has_finance_db):
    if has_finance_db:
        FinanceStore("finance_test")
    before = {path.relative_to(workspace): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in workspace.rglob("*") if path.is_file()}
    for _ in range(3):
        assert profile.load_profile("finance_test") is None
    after = {path.relative_to(workspace): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in workspace.rglob("*") if path.is_file()}
    assert after == before


def test_synced_profile_read_does_not_initialize_or_rewrite_files(workspace, monkeypatch):
    saved = profile.save_profile("finance_test", {})
    monkeypatch.setattr(profile, "FinanceStore", lambda *args: pytest.fail("read initialized store"))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in workspace.rglob("*") if path.is_file()}
    assert profile.load_profile("finance_test") == saved
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before


def test_profile_uses_shared_instrument_registry(workspace, monkeypatch):
    monkeypatch.setattr(profile, "INSTRUMENTS", {"sh000016": {"name": "SSE50"}})
    assert profile.save_profile("finance_test", {"symbols": ["sh000016"]})["symbols"] == ["sh000016"]
    with pytest.raises(ValueError, match="unsupported"):
        profile.save_profile("finance_test", {"symbols": ["sh000001"]})


@pytest.mark.parametrize("patch", [
    {"symbols": "sh000001"}, {"symbols": []}, {"symbols": ["sh999999"]},
    {"symbols": ["sh000001", "sh000001"]}, {"symbols": [123]},
    {"report_days": 0}, {"report_days": 366}, {"report_days": True},
    {"report_days": 1.5}, {"report_days": "7"}, {"timezone": "Mars/Olympus"},
    {"timezone": "../Shanghai"}, {"markets": "CN"}, {"markets": []},
    {"focus_topics": [None]}, {"constraints": {}}, {"holdings": "unknown"},
    {"domain": "other"}, {"status": "inferred"}, {"version": True},
    {"purpose": " "}, {"display_name": "x\n## FACT: injection"},
    {"workspace_id": "other"}, {"extension": float("nan")},
    {"quality_policy": "best_effort"}, {"quality_policy": True},
    {"default_session_mode": "intraday"}, {"document_days": 0}, {"document_days": True},
])
def test_invalid_profile_does_not_commit(workspace, patch):
    with pytest.raises(ValueError):
        profile.save_profile("finance_test", patch)
    assert profile.load_profile("finance_test") is None
    assert not (workspace / profile.PAGE_PATH).exists()


def test_optimistic_version_conflict_and_concurrent_saves(workspace):
    profile.save_profile("finance_test", {}, 0)
    with pytest.raises(profile.ProfileVersionConflict):
        profile.save_profile("finance_test", {"report_days": 99}, 0)

    def save(days):
        try:
            return profile.save_profile("finance_test", {"report_days": days}, 1)["version"]
        except profile.ProfileVersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, (8, 9)))
    assert sorted(outcomes, key=str) == [2, "conflict"]
    assert profile.load_profile("finance_test")["version"] == 2


@pytest.mark.parametrize("managed", [False, True])
def test_existing_or_edited_finance_page_is_not_overwritten(workspace, managed):
    if managed:
        profile.save_profile("finance_test", {})
    path = workspace / profile.PAGE_PATH
    path.write_text("# My existing finance notes\n", encoding="utf-8")
    with pytest.raises(loader.WikiConflictError):
        profile.save_profile("finance_test", {"report_days": 30})
    assert path.read_text(encoding="utf-8") == "# My existing finance notes\n"
    loaded = profile.load_profile("finance_test")
    assert (loaded["version"] if loaded else 0) == int(managed)


def test_foreign_facts_and_wiki_are_preserved(workspace):
    (workspace / "wiki" / "existing.md").write_text(
        "## FACT: unrelated\n- value: preserve\n", encoding="utf-8")
    profile.save_profile("finance_test", {})
    path = workspace / "db" / "company_memory.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO company_facts (fact_id, source_path, value, updated_at) "
                     "VALUES ('unrelated', 'wiki/existing.md', 'preserve', 'today')")
    profile.save_profile("finance_test", {"symbols": ["sh000300"]})
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM company_facts WHERE fact_id='unrelated'").fetchone()[0] == "preserve"


def test_conflicting_fact_identity_does_not_commit(workspace):
    path = workspace / "wiki" / "existing.md"
    path.write_text("## FACT: finance.profile.purpose\n- value: mine\n", encoding="utf-8")
    with pytest.raises(loader.WikiConflictError):
        profile.save_profile("finance_test", {})
    assert profile.load_profile("finance_test") is None


@pytest.mark.parametrize("failure_point", ["_write_markdown", "_sync_facts"])
def test_projection_failure_commits_db_then_load_repairs_without_new_version(
    workspace, monkeypatch, failure_point
):
    profile.save_profile("finance_test", {}, 0)
    original = getattr(profile, failure_point)

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(profile, failure_point, fail)
    with pytest.raises(profile.ProfileSyncError) as error:
        profile.save_profile("finance_test", {"report_days": 14}, 1)
    assert error.value.committed_version == 2
    with sqlite3.connect(workspace / "db" / "finance.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM finance_profiles").fetchone()[0] == 2
        assert conn.execute("SELECT pending FROM finance_profile_projection").fetchone()[0] == 1
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        value = conn.execute("SELECT value FROM company_facts WHERE fact_id='finance.profile.report_days'").fetchone()[0]
        assert json.loads(value)["value"] == 7
    monkeypatch.setattr(profile, failure_point, original)
    assert profile.load_profile("finance_test")["version"] == 2
    with sqlite3.connect(workspace / "db" / "finance.sqlite") as conn:
        assert conn.execute("SELECT pending FROM finance_profile_projection").fetchone()[0] == 0
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        value = conn.execute("SELECT value FROM company_facts WHERE fact_id='finance.profile.report_days'").fetchone()[0]
        assert json.loads(value)["value"] == 14


def test_pending_projection_never_overwrites_subsequent_manual_edit(workspace, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr(profile, "_sync_facts", lambda *args: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(profile.ProfileSyncError):
            profile.save_profile("finance_test", {})
    path = workspace / profile.PAGE_PATH
    path.write_text("Manual recovery notes", encoding="utf-8")
    with pytest.raises(profile.ProfileSyncError):
        profile.load_profile("finance_test")
    assert path.read_text(encoding="utf-8") == "Manual recovery notes"


def test_projection_replay_after_facts_commit_before_ack_is_idempotent(workspace, monkeypatch):
    original = profile._sync_facts

    def commit_then_fail(*args):
        original(*args)
        raise OSError("interrupted after the facts transaction committed")

    with monkeypatch.context() as scoped:
        scoped.setattr(profile, "_sync_facts", commit_then_fail)
        with pytest.raises(profile.ProfileSyncError):
            profile.save_profile("finance_test", {})
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        before = conn.execute("SELECT * FROM company_facts ORDER BY fact_id").fetchall()
    assert before and profile.load_profile("finance_test")["version"] == 1
    with sqlite3.connect(workspace / "db" / "company_memory.sqlite") as conn:
        assert conn.execute("SELECT * FROM company_facts ORDER BY fact_id").fetchall() == before


def test_snapshots_and_reports_are_immutable_idempotent_and_scoped(workspace):
    store = FinanceStore("finance_test")
    assert store.latest_snapshot() is None and store.list_reports() == []
    first = snapshot()
    assert store.save_snapshot(first) == "snapshot_1"
    assert store.save_snapshot(dict(reversed(list(first.items())))) == "snapshot_1"
    assert store.latest_snapshot() == first
    with pytest.raises(FinanceConflictError):
        store.save_snapshot({**first, "warnings": ["changed"]})
    report = {"report_id": "report_1", "profile_version": 1, "snapshot_refs": ["snapshot_1"],
              "analysis": {"overview": "test"}}
    assert store.save_report(report) == "report_1"
    assert store.save_report(report) == "report_1"
    with pytest.raises(FinanceConflictError):
        store.save_report({**report, "profile_version": 2})
    store.save_report({"report_id": "report_2"})
    assert store.list_reports(1) == [{"report_id": "report_2"}]
    assert store.get_report("report_1") == report
    assert store.get_report("missing") is None
    other = FinanceStore("other")
    assert other.get_report("report_1") is None and other.latest_snapshot() is None
    other.save_snapshot({**first, "warnings": ["isolated"]})
    assert store.latest_snapshot() == first
    assert not (workspace / "db" / "company_memory.sqlite").exists()


def test_content_ids_and_insert_transaction_rollback(workspace):
    store = FinanceStore("finance_test")
    report = {"summary": "stable", "data": [1, 2]}
    report_id = store.save_report(report)
    assert store.save_report(report) == report_id
    assert store.get_report(report_id) == {**report, "report_id": report_id}
    with pytest.raises(RuntimeError), store.connection(immediate=True) as conn:
        store._insert(conn, snapshot(), snapshot=True)
        raise RuntimeError("failure before commit")
    assert store.latest_snapshot() is None
    with pytest.raises(sqlite3.IntegrityError), store.connection(immediate=True) as conn:
        conn.execute("DELETE FROM finance_reports")
    assert store.get_report(report_id) is not None


@pytest.mark.parametrize("iteration", range(10))
def test_concurrent_immutable_insert(workspace, iteration):
    def save(_):
        return FinanceStore("finance_test").save_snapshot(snapshot())

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(save, range(4))) == ["snapshot_1"] * 4
    with FinanceStore("finance_test").connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM finance_snapshots").fetchone()[0] == 1


@pytest.mark.parametrize("payload", [{"v": float("inf")}, {1: "key"}, {"v": (1, 2)},
                                     {"workspace_id": "another"}, {"report_id": " "}])
def test_store_rejects_non_json_or_foreign_payload(workspace, payload):
    with pytest.raises(ValueError):
        FinanceStore("finance_test").save_report(payload)


def test_store_validates_snapshot_contract(workspace):
    with pytest.raises(ValueError):
        FinanceStore("finance_test").save_snapshot({**snapshot(), "period": {"start": "2026-10-01", "end": "2026-09-01"}})


@pytest.mark.parametrize("company_id", ["../other", "C:\\outside", "a/b", "NUL"])
def test_unsafe_workspace_rejected(company_id):
    with pytest.raises(ValueError):
        FinanceStore(company_id)


def test_redirected_database_path_is_rejected(workspace, monkeypatch):
    from pathlib import Path

    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path.name == "finance.sqlite":
            return workspace.parent / "outside.sqlite"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="links"):
        FinanceStore("finance_test")


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 1001])
def test_report_limit_is_validated(workspace, limit):
    with pytest.raises(ValueError):
        FinanceStore("finance_test").list_reports(limit)
