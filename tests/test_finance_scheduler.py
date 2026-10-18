"""No live provider requests or persistent/background scheduler services."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest

from policy_impact.company_wiki import loader
from policy_impact.domains.finance import profile, scheduler
from policy_impact.domains.finance.store import FinanceStore


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    profile.save_profile("finance_test", {"symbols": ["sh000001", "sh000300", "sz399006"],
        "quality_policy": "allow_partial", "default_session_mode": "explicit_period"})
    clock = [datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(scheduler, "_now", lambda: clock[0])
    return tmp_path, clock


def good_snapshot(**request):
    return {
        "snapshot_id": "snapshot_" + request["end"],
        "collected_at": request["end"] + "T09:00:00+00:00",
        "period": {"start": request["start"], "end": request["end"]},
        "session_mode": request.get("session_mode", "explicit_period"),
        "quality_policy": request.get("quality_policy", "allow_partial"),
        "as_of": request.get("as_of"), "document_period": request.get("document_period"),
        "series": [{"symbol": symbol, "name": symbol, "provider": "fixture", "unit": "points",
                    "timezone": "Asia/Shanghai", "price_basis": "price_index",
                    "source_url": "https://example.org/index", "retrieved_at": request["end"] + "T09:00:00+00:00",
                    "bars": [{"date": request["end"], "open": 100, "high": 102,
                              "low": 99, "close": 101, "volume": 1000}]}
                   for symbol in request["symbols"]],
        "documents": [], "warnings": [],
        "source_statuses": [{"source_id": symbol, "status": "ok", "count": 1}
                            for symbol in request["symbols"]],
    }


@pytest.fixture
def collector(monkeypatch):
    calls = []

    def collect(**request):
        calls.append(deepcopy(request))
        data = good_snapshot(**request)
        data["snapshot_id"] += f"_{len(calls)}"
        return data

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    return calls


def test_once_uses_real_contract_and_local_calendar_window(environment, monkeypatch):
    from policy_impact.domains.finance import acquisition
    calls = []

    def collect(**request):
        calls.append(request)
        return good_snapshot(**calls[-1])

    monkeypatch.setattr(acquisition, "collect_finance_snapshot", collect)
    environment[1][0] = datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "ok"
    assert calls == [{"start": "2026-09-17", "end": "2026-09-23",
                      "symbols": ["sh000001", "sh000300", "sz399006"],
                      "session_mode": "explicit_period", "quality_policy": "allow_partial",
                      "as_of": "2026-09-23T07:30:00+08:00", "include_documents": True,
                      "document_period": {"start": "2026-09-17", "end": "2026-09-23"}}]
    assert FinanceStore("finance_test").latest_snapshot()["snapshot_id"] == result["snapshot_id"]
    status = scheduler.get_collection_status("finance_test")
    assert status["last_success_at"] == environment[1][0].isoformat()
    assert status["status"] == "ok" and not status["worker_running"]
    assert status["last_error"] is None


def test_status_only_returns_bounded_snapshot_projection(environment):
    before = scheduler.get_collection_status("finance_test")
    assert before["source_statuses"] == [] and before["warnings"] == [] and before["collected_at"] is None
    payload = good_snapshot(start="2026-09-16", end="2026-09-22", symbols=["sh000001"])
    payload["source_statuses"][0]["detail"] = "x" * 2000
    payload["warnings"] = ["w" * 2000] * 40
    FinanceStore("finance_test").save_snapshot(payload)
    status = scheduler.get_collection_status("finance_test")
    assert status["collected_at"] == payload["collected_at"]
    assert status["latest_snapshot_id"] == payload["snapshot_id"]
    assert status["source_statuses"][0]["status"] == "ok"
    assert len(status["source_statuses"][0]["detail"]) == 1200
    assert len(status["warnings"]) == 32 and len(status["warnings"][0]) == 1000
    assert status["snapshot_projection_truncated"]
    assert not {"snapshot", "series", "documents", "bars"} & status.keys()


@pytest.mark.parametrize("days_before", [1, 7, 45])
def test_bounded_preperiod_baselines_are_retained(environment, monkeypatch, days_before):
    def collect(**request):
        payload = good_snapshot(**request)
        day = (datetime.fromisoformat(request["start"]) - timedelta(days=days_before)).date().isoformat()
        for series in payload["series"]:
            series["bars"].insert(0, {**series["bars"][0], "date": day})
        return payload

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    saved = FinanceStore("finance_test").latest_snapshot()
    assert all(series["bars"][0]["date"] < saved["period"]["start"] for series in saved["series"])


def test_several_bounded_baseline_bars_are_allowed(environment, monkeypatch):
    def collect(**request):
        payload = good_snapshot(**request)
        for series in payload["series"]:
            baselines = [{**series["bars"][0], "date": (
                datetime.fromisoformat(request["start"]) - timedelta(days=days)
            ).date().isoformat()} for days in (45, 7, 1)]
            series["bars"] = baselines + series["bars"]
        return payload

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    assert len(FinanceStore("finance_test").latest_snapshot()["series"][0]["bars"]) == 4


@pytest.mark.parametrize("offset", [-46, 7])
def test_out_of_bound_baseline_or_future_bar_is_rejected(environment, monkeypatch, offset):
    def collect(**request):
        payload = good_snapshot(**request)
        day = (datetime.fromisoformat(request["start"]) + timedelta(days=offset)).date().isoformat()
        payload["series"][0]["bars"].insert(0, {**payload["series"][0]["bars"][0], "date": day})
        return payload

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "error" and "baseline lookback" in result["error"]
    assert FinanceStore("finance_test").latest_snapshot() is None


def test_baseline_only_data_is_not_a_successful_period(environment, monkeypatch):
    def collect(**request):
        payload = good_snapshot(**request)
        day = (datetime.fromisoformat(request["start"]) - timedelta(days=1)).date().isoformat()
        for series in payload["series"]:
            series["bars"][0]["date"] = day
        return payload

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "empty"
    assert scheduler.get_collection_status("finance_test")["last_success_at"] is None


def test_public_status_schedule_collect_contract(environment, collector):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from policy_impact.app.finance_api import register_finance_routes

    app = FastAPI()
    register_finance_routes(app, HTTPException)
    prefix = "/companies/finance_test/workbench/finance"
    with TestClient(app) as client:
        response = client.get(prefix + "/status")
        assert response.status_code == 200
        assert response.json()["status"]["source_statuses"] == []
        response = client.post(prefix + "/schedule", json={"enabled": False})
        assert response.status_code == 200 and not response.json()["status"]["enabled"]
        assert client.post(prefix + "/collect").json()["status"] == "paused"
        assert not collector
        client.post(prefix + "/schedule", json={"enabled": True})
        response = client.post(prefix + "/collect")
        assert response.status_code == 200 and response.json()["status"] == "ok"
        status = client.get(prefix + "/status").json()["status"]
        assert status["collected_at"] and status["source_statuses"]
        assert "series" not in status and "snapshot" not in status
        assert client.get("/companies/nonfinance/workbench/finance/profile").json() == {"profile": None}
        assert not loader.company_dir("nonfinance").exists()


def test_duplicate_calls_restart_and_new_bucket(environment, collector):
    first = scheduler.run_worker("finance_test", 60, once=True)
    second = scheduler.run_worker("finance_test", 60, once=True)
    assert first["status"] == "ok" and second["status"] == "deduplicated"
    assert first["snapshot_id"] == second["snapshot_id"] and len(collector) == 1
    environment[1][0] += timedelta(hours=1)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    assert len(collector) == 2


def test_profile_version_and_workspace_are_part_of_dedup_key(environment, collector):
    first = scheduler.collect_once("finance_test")
    profile.save_profile("finance_test", {"symbols": ["sh000300"]}, 1)
    second = scheduler.collect_once("finance_test")
    profile.save_profile("other", {"symbols": ["sh000300"], "default_session_mode": "explicit_period",
                                    "quality_policy": "allow_partial"})
    third = scheduler.collect_once("other")
    assert len({first["job_key"], second["job_key"], third["job_key"]}) == 3
    assert first["status"] == second["status"] == third["status"] == "ok"
    assert collector[1]["symbols"] == ["sh000300"] and len(collector) == 3


def test_pause_resume_is_durable_and_does_not_start_a_worker(environment, collector):
    assert scheduler.set_collection_enabled("finance_test", False)["status"] == "paused"
    assert scheduler.run_worker("finance_test", once=True)["status"] == "paused"
    assert not collector
    status = scheduler.get_collection_status("finance_test")
    assert not status["enabled"] and not status["worker_running"] and status["next_run_at"] is None
    assert scheduler.set_collection_enabled("finance_test", True)["enabled"]
    assert scheduler.collect_once("finance_test")["status"] == "ok"


@pytest.mark.parametrize("enabled", [1, 0, "false", None])
def test_enabled_requires_bool(environment, enabled):
    with pytest.raises(ValueError):
        scheduler.set_collection_enabled("finance_test", enabled)


def test_provider_error_backoff_and_recovery(environment, monkeypatch, collector):
    original = scheduler._collect_snapshot

    def fail(**request):
        raise OSError("provider unavailable")

    monkeypatch.setattr(scheduler, "_collect_snapshot", fail)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "error" and "provider unavailable" in result["error"]
    status = scheduler.get_collection_status("finance_test")
    assert status["last_success_at"] is None and status["consecutive_failures"] == 1
    assert FinanceStore("finance_test").latest_snapshot() is None
    assert scheduler.collect_once("finance_test")["status"] == "retry_wait"
    environment[1][0] += timedelta(minutes=1)
    monkeypatch.setattr(scheduler, "_collect_snapshot", original)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    with FinanceStore("finance_test").connection() as conn:
        assert conn.execute("SELECT attempt_count, status FROM finance_collection_attempts").fetchone()[:] == (2, "ok")


@pytest.mark.parametrize("mode,expected", [("partial", "partial"), ("empty", "empty"),
                                         ("error", "error"), ("missing", "partial"),
                                         ("no_statuses", "partial")])
def test_non_success_quality_is_saved_but_never_marked_success(environment, monkeypatch, mode, expected):
    def collect(**request):
        data = good_snapshot(**request)
        if mode in {"empty", "error"}:
            data["series"] = []
            data["source_statuses"] = [{"source_id": "fixture", "status": mode, "count": 0}]
        elif mode == "partial":
            data["source_statuses"][0]["status"] = "partial"
        elif mode == "missing":
            data["series"].pop()
        else:
            data["source_statuses"] = []
        return data

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == expected
    assert FinanceStore("finance_test").latest_snapshot() is not None
    status = scheduler.get_collection_status("finance_test")
    assert status["last_status"] == expected and status["last_success_at"] is None
    assert status["last_error"]


def test_failure_preserves_previous_success_timestamp(environment, monkeypatch, collector):
    scheduler.collect_once("finance_test")
    succeeded = scheduler.get_collection_status("finance_test")["last_success_at"]
    environment[1][0] += timedelta(hours=1)
    monkeypatch.setattr(scheduler, "_collect_snapshot", lambda **kw: {})
    assert scheduler.collect_once("finance_test")["status"] == "error"
    assert scheduler.get_collection_status("finance_test")["last_success_at"] == succeeded


@pytest.mark.parametrize("mutation", ["period", "symbol", "bar_date", "malformed"])
def test_invalid_snapshot_is_not_saved(environment, monkeypatch, mutation):
    def collect(**request):
        data = good_snapshot(**request)
        if mutation == "period":
            data["period"]["start"] = "2026-09-01"
        elif mutation == "symbol":
            data["series"][0]["symbol"] = "sh999999"
        elif mutation == "bar_date":
            data["series"][0]["bars"][0]["date"] = "2020-01-01"
        else:
            return {"success": True}
        return data

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "error"
    assert FinanceStore("finance_test").latest_snapshot() is None


def test_snapshot_and_success_status_commit_together(environment, monkeypatch, collector):
    original = scheduler._finish

    def fail_success(conn, **kwargs):
        if kwargs["status"] == "ok":
            raise OSError("failure between snapshot insert and attempt completion")
        return original(conn, **kwargs)

    monkeypatch.setattr(scheduler, "_finish", fail_success)
    assert scheduler.collect_once("finance_test")["status"] == "error"
    assert FinanceStore("finance_test").latest_snapshot() is None
    assert scheduler.get_collection_status("finance_test")["last_success_at"] is None


def test_concurrent_collect_and_pause_during_inflight(environment, monkeypatch):
    entered, release = Event(), Event()

    def collect(**request):
        entered.set()
        assert release.wait(10)
        return good_snapshot(**request)

    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.collect_once, "finance_test")
        try:
            assert entered.wait(10)
            assert scheduler.collect_once("finance_test")["status"] == "busy"
            assert scheduler.get_collection_status("finance_test")["collecting"]
            assert not scheduler.set_collection_enabled("finance_test", False)["enabled"]
        finally:
            release.set()
        assert future.result(timeout=10)["status"] == "ok"
    assert scheduler.collect_once("finance_test")["status"] == "paused"


def _child(environment, code):
    root = Path(__file__).resolve().parents[1]
    preamble = (
        "from pathlib import Path\nfrom datetime import datetime, timezone\n"
        "from policy_impact.company_wiki import loader\n"
        "from policy_impact.domains.finance import scheduler\n"
        "from policy_impact.domains.finance.store import exclusive_lock\n"
        f"loader.project_root = lambda: Path({str(environment[0])!r})\n"
        "scheduler._now = lambda: datetime(2026, 9, 22, 9, tzinfo=timezone.utc)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, "-u", "-c", preamble + code], cwd=root, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kill(child):
    if child.poll() is None:
        child.kill()
    child.communicate(timeout=10)


def test_worker_lock_is_cross_process_and_released_on_process_death(environment, collector):
    child = _child(environment, "import sys\nwith exclusive_lock('finance_test', 'worker') as lock:\n"
                               "    print('locked' if lock else 'failed', flush=True)\n"
                               "    sys.stdin.readline()\n")
    try:
        assert child.stdout.readline().strip() == "locked"
        assert scheduler.get_collection_status("finance_test")["worker_running"]
        assert scheduler.run_worker("finance_test", once=True)["status"] == "worker_already_running"
        assert not collector
    finally:
        _kill(child)
    assert scheduler.run_worker("finance_test", once=True)["status"] == "ok"


def test_collection_crash_leaves_recoverable_attempt_not_success(environment, collector):
    child = _child(environment, "import sys\ndef collect(**kwargs):\n"
                               "    print('collecting', flush=True)\n"
                               "    sys.stdin.readline()\n"
                               "scheduler._collect_snapshot = collect\n"
                               "scheduler.collect_once('finance_test')\n")
    try:
        assert child.stdout.readline().strip() == "collecting"
        assert scheduler.collect_once("finance_test")["status"] == "busy"
        assert scheduler.get_collection_status("finance_test")["last_success_at"] is None
    finally:
        _kill(child)
    assert scheduler.get_collection_status("finance_test")["status"] == "interrupted"
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    with FinanceStore("finance_test").connection() as conn:
        assert conn.execute("SELECT attempt_count, status FROM finance_collection_attempts").fetchone()[:] == (2, "ok")


def test_worker_loop_coalesces_and_persists_interval_without_real_wait(environment, collector):
    class StopAfterTwoTicks:
        ticks = 0

        def is_set(self):
            return self.ticks >= 2

        def wait(self, seconds):
            self.ticks += 1

    result = scheduler.run_worker("finance_test", 15, stop_event=StopAfterTwoTicks())
    assert result["status"] == "stopped" and len(collector) == 1
    status = scheduler.get_collection_status("finance_test")
    assert status["interval_minutes"] == 15 and not status["worker_running"]
    scheduler.run_worker("finance_test", 15, stop_event=StopAfterTwoTicks())
    assert len(collector) == 1
    environment[1][0] += timedelta(days=5)
    scheduler.run_worker("finance_test", 15, stop_event=StopAfterTwoTicks())
    assert len(collector) == 2


def test_no_profile_is_error_without_implicit_draft_creation(environment, collector):
    result = scheduler.collect_once("missing")
    assert result["status"] == "error" and "no finance profile" in result["error"]
    assert profile.load_profile("missing") is None and not collector


@pytest.mark.parametrize("minutes", [0, -1, True, float("nan"), float("inf"), 10081, "1"])
def test_worker_interval_validation(environment, minutes):
    with pytest.raises(ValueError):
        scheduler.run_worker("finance_test", minutes, once=True)


def _cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_finance_worker.py"
    spec = importlib.util.spec_from_file_location("finance_worker_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_once_success_and_failure_exit_codes(environment, collector, monkeypatch, capsys):
    cli = _cli()
    assert cli.main(["--company-id", "finance_test", "--interval-minutes", "30", "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    monkeypatch.setattr(cli, "run_worker", lambda *args, **kwargs: {"status": "error", "error": "offline"})
    assert cli.main(["--company-id", "finance_test", "--once"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    monkeypatch.setattr(cli, "run_worker", lambda *args, **kwargs: {"status": "deduplicated", "collection_status": "partial"})
    assert cli.main(["--company-id", "finance_test", "--once"]) == 1


def test_import_has_no_scheduler_or_workspace_side_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    _cli()
    assert not list(tmp_path.iterdir())


def daily_profile(environment, symbols=None):
    environment[1][0] = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)
    return profile.save_profile("finance_test", {
        "symbols": symbols or ["spx", "sh000001", "xauusd"],
        "default_session_mode": "latest_completed", "quality_policy": "strict",
        "report_days": 30, "document_days": 14,
    }, 1)


def completed_snapshot(**request):
    """Synthetic observations with real calendar labels, never live price fixtures."""
    from policy_impact.domains.finance.market_catalog import get_symbol_metadata
    from policy_impact.domains.finance.sessions import latest_completed_session

    stamp = datetime.fromisoformat(request["as_of"])
    data = good_snapshot(**request)
    data["snapshot_id"] = "synthetic_completed_" + hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    data["collected_at"] = request["as_of"]
    for series in data["series"]:
        metadata = get_symbol_metadata(series["symbol"])
        series.update({key: metadata[key] for key in (
            "asset_class", "quote_currency", "unit", "timezone", "price_basis")})
        series["retrieved_at"] = request["as_of"]
        if metadata["calendar"]:
            series.update(latest_completed_session(metadata["calendar"], as_of=stamp))
        else:
            day = (stamp.astimezone(timezone.utc) - timedelta(minutes=20)).date() - timedelta(days=1)
            previous = day - timedelta(days=1)
            series.update(calendar_id=None, session_date=day.isoformat(), expected_session_date=day.isoformat(),
                expected_previous_session_date=previous.isoformat(),
                session_close_at=datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc).isoformat(),
                session_verified=True, session_date_basis="bullionvault_utc_reference_day",
                daily_cutoff_basis="Synthetic complete UTC reference-day fixture, not an exchange close",
                session_evidence={"natural_day_reference": True, "market_session_verified": False,
                    "publication_delay_minutes": 20, "constant_reference_day": False,
                    "reference_days": {value.isoformat(): {"sample_count": 6, "constant_reference_day": False}
                                       for value in (previous, day)}})
        series["bars"] = [{"date": day, "open": None if metadata["calendar"] is None else close,
                           "high": close + 1, "low": close - 1, "close": close}
                          for day, close in [(series["expected_previous_session_date"], 100), (series["session_date"], 102)]]
    return data


def document(day):
    text = "Synthetic official-source text fixture. " * 20
    return {"document_id": "test_document", "title": "Synthetic policy release", "url": "https://example.org/policy",
        "source_id": "fed_monetary", "publisher": "Synthetic publisher", "kind": "policy_release",
        "published_at": day, "retrieved_at": day + "T12:00:00+00:00", "text": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest()}


def test_daily_worker_matches_parse_request_and_per_market_sessions_without_reporting(environment, monkeypatch):
    from policy_impact.domains.finance import acquisition, render, workflow
    from policy_impact.domains.finance.request import parse_request

    saved_profile = daily_profile(environment)
    expected = parse_request("", saved_profile, now=environment[1][0])
    calls, gates, metrics = [], [], []
    original_metrics, original_gate = scheduler.compute_market_metrics, scheduler.require_market_coverage

    def collect(**request):
        calls.append(request)
        data = completed_snapshot(**request)
        data["documents"] = [document("2026-09-15")]
        data["source_statuses"] += [
            {"source_id": "yahoo_chart:spx", "status": "partial", "count": 1},
            {"source_id": "fed_monetary", "status": "partial", "count": 1},
            {"source_id": "bis_speeches", "status": "error", "count": 0}]
        return data

    def compute(data):
        metrics.append(data)
        return original_metrics(data)

    def gate(*args):
        gates.append(args)
        return original_gate(*args)

    def forbidden(*args, **kwargs):
        pytest.fail("Collection must never enter causal analysis, a model, or HTML publication")

    monkeypatch.setattr(acquisition, "collect_finance_snapshot", collect)
    monkeypatch.setattr(scheduler, "compute_market_metrics", compute)
    monkeypatch.setattr(scheduler, "require_market_coverage", gate)
    monkeypatch.setattr(workflow, "run_finance_report", forbidden)
    monkeypatch.setattr(render, "render_html", forbidden)
    monkeypatch.setattr(render, "publish_report", forbidden)
    monkeypatch.setattr(FinanceStore, "save_report", forbidden)
    result = scheduler.run_worker("finance_test", once=True)
    assert result["status"] == "ok", result
    assert calls == [{**expected["period"], **{key: expected[key] for key in (
        "symbols", "session_mode", "as_of", "document_period", "include_documents", "quality_policy")}}]
    snapshot = FinanceStore("finance_test").latest_snapshot()
    assert snapshot["period"] == {"start": "2026-09-23", "end": "2026-09-23"}
    assert snapshot["document_period"] == {"start": "2026-09-10", "end": "2026-09-23"}
    assert {s["symbol"]: s["session_date"] for s in snapshot["series"]} == {
        "spx": "2026-09-22", "sh000001": "2026-09-23", "xauusd": "2026-09-22"}
    assert snapshot["session_mode"] == "latest_completed" and snapshot["quality_policy"] == "strict"
    assert len(metrics) == len(gates) == 1 and gates[0][0] == expected
    assert all(metric["change_pct"] == 2 for metric in gates[0][2])
    with FinanceStore("finance_test").connection() as conn:
        assert json.loads(conn.execute("SELECT request_json FROM finance_collection_attempts").fetchone()[0]) == expected
        assert conn.execute("SELECT COUNT(*) FROM finance_reports").fetchone()[0] == 0
    status = scheduler.get_collection_status("finance_test")
    assert status["last_success_at"] == environment[1][0].isoformat()
    assert any(source["status"] == "error" for source in status["source_statuses"])
    assert not list(environment[0].rglob("*.html"))


@pytest.mark.parametrize("state", ["partial", "empty", "error"])
def test_strict_collection_does_not_require_causal_documents(environment, monkeypatch, state):
    daily_profile(environment, ["spx"])
    def collect(**request):
        data = completed_snapshot(**request)
        data["source_statuses"].append({"source_id": "fed_monetary", "status": state, "count": 0})
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    assert FinanceStore("finance_test").latest_snapshot()["documents"] == []


@pytest.mark.parametrize("damage", ["baseline", "missing_symbol", "stale", "unverified", "constant_spot", "wrong_spot_unit"])
def test_strict_core_gaps_saved_for_audit_but_never_success(environment, monkeypatch, damage):
    daily_profile(environment, ["spx", "xauusd"])
    def collect(**request):
        data = completed_snapshot(**request)
        series, spot = data["series"]
        if damage == "baseline":
            series["bars"].pop(0)
        elif damage == "missing_symbol":
            data["series"].pop(0)
        elif damage == "stale":
            series["bars"].pop()
            series["session_date"] = series["bars"][0]["date"]
            series["session_verified"] = False
        elif damage == "unverified":
            series["session_verified"] = False
        elif damage == "constant_spot":
            spot["session_evidence"]["reference_days"][spot["expected_previous_session_date"]]["constant_reference_day"] = True
        else:
            spot["unit"] = "USD/kg"
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "partial" and "coverage failed" in result["error"], result
    assert FinanceStore("finance_test").latest_snapshot() is not None
    status = scheduler.get_collection_status("finance_test")
    assert status["last_success_at"] is None and status["consecutive_failures"] == 1


@pytest.mark.parametrize("field,value", [("session_mode", "explicit_period"), ("quality_policy", "allow_partial"),
    ("as_of", None), ("as_of", "2026-09-22T09:00:00+00:00"), ("document_period", None),
    ("document_period", {"start": "2026-09-23", "end": "2026-09-23"})])
def test_worker_rejects_silently_downgraded_acquisition_contract(environment, monkeypatch, field, value):
    daily_profile(environment, ["spx"])
    def collect(**request):
        return {**completed_snapshot(**request), field: value}
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "error"
    assert FinanceStore("finance_test").latest_snapshot() is None


@pytest.mark.parametrize("day", ["2026-09-09", "2026-09-24"])
def test_independent_documents_still_reject_dates_outside_their_window(environment, monkeypatch, day):
    daily_profile(environment, ["spx"])
    def collect(**request):
        data = completed_snapshot(**request)
        data["documents"] = [document(day)]
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "error" and "document_period" in result["error"]
    assert FinanceStore("finance_test").latest_snapshot() is None


def test_daily_dedup_excludes_clock_ticks_but_audits_original_cutoff(environment, monkeypatch):
    daily_profile(environment, ["spx"])
    calls = []
    def collect(**request):
        calls.append(request)
        return completed_snapshot(**request)
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    first = scheduler.collect_once("finance_test")
    environment[1][0] += timedelta(minutes=2)
    second = scheduler.collect_once("finance_test")
    assert first["status"] == "ok" and second["status"] == "deduplicated"
    assert first["job_key"] == second["job_key"] and len(calls) == 1
    with FinanceStore("finance_test").connection() as conn:
        request = json.loads(conn.execute("SELECT request_json FROM finance_collection_attempts").fetchone()[0])
        assert request["as_of"] == calls[0]["as_of"]


def test_retry_updates_audited_as_of_without_creating_another_bucket(environment, monkeypatch):
    daily_profile(environment, ["spx"])
    calls = []
    def collect(**request):
        calls.append(request)
        if len(calls) == 1:
            raise OSError("synthetic provider failure")
        return completed_snapshot(**request)
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    first = scheduler.collect_once("finance_test")
    environment[1][0] += timedelta(minutes=1)
    second = scheduler.collect_once("finance_test")
    assert first["status"] == "error" and second["status"] == "ok"
    assert first["job_key"] == second["job_key"]
    with FinanceStore("finance_test").connection() as conn:
        row = conn.execute("SELECT request_json, attempt_count FROM finance_collection_attempts").fetchone()
        assert row[1] == 2 and json.loads(row[0])["as_of"] == calls[-1]["as_of"] != calls[0]["as_of"]


def test_explicit_strict_week_uses_only_completed_sessions_at_worker_cutoff(environment, monkeypatch):
    from policy_impact.domains.finance.sessions import completed_session_dates, session_dates

    environment[1][0] = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)
    profile.save_profile("finance_test", {"symbols": ["spx"], "default_session_mode": "explicit_period",
                                           "report_days": 7, "quality_policy": "strict"}, 1)
    def collect(**request):
        data = completed_snapshot(**request)
        series = data["series"][0]
        first = datetime.fromisoformat(request["start"]).date()
        baseline = session_dates("XNYS", first - timedelta(days=45), first - timedelta(days=1))[-1]
        days = [baseline] + completed_session_dates("XNYS", request["start"], request["end"], as_of=request["as_of"])
        series["bars"] = [{"date": day.isoformat(), "open": 100+i, "high": 101+i, "low": 99+i, "close": 100+i}
                          for i, day in enumerate(days)]
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "ok", result
    data = FinanceStore("finance_test").latest_snapshot()
    assert data["period"]["end"] == "2026-09-23" and data["series"][0]["bars"][-1]["date"] == "2026-09-22"


def test_legacy_profile_missing_new_fields_keeps_explicit_allow_partial(environment, monkeypatch, collector):
    old = profile.load_profile("finance_test")
    for field in ("default_session_mode", "document_days", "quality_policy"):
        old.pop(field)
    monkeypatch.setattr(scheduler, "load_profile", lambda company_id: old)
    assert scheduler.collect_once("finance_test")["status"] == "ok"
    request = collector[0]
    assert request["session_mode"] == "explicit_period" and request["quality_policy"] == "allow_partial"
    assert (request["start"], request["end"]) == ("2026-09-16", "2026-09-22")
    assert request["document_period"] == {"start": request["start"], "end": request["end"]}


def test_allow_partial_legacy_does_not_fail_for_noncore_or_failed_fallback_sources(environment, monkeypatch):
    def collect(**request):
        data = good_snapshot(**request)
        data["source_statuses"] += [{"source_id": "fed_monetary", "status": "partial", "count": 0},
            {"source_id": "failed_provider:sh000001", "status": "error", "count": 0}]
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    assert scheduler.collect_once("finance_test")["status"] == "ok"


def test_daily_worker_rejects_future_market_dates(environment, monkeypatch):
    daily_profile(environment, ["spx"])
    def collect(**request):
        data = completed_snapshot(**request)
        data["series"][0]["bars"].append({"date": "2026-09-24", "high": 103, "low": 100, "close": 102})
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "error" and "baseline lookback" in result["error"]
    assert FinanceStore("finance_test").latest_snapshot() is None


def test_strict_worker_rejects_intraday_bar_even_on_the_user_anchor_date(environment, monkeypatch):
    daily_profile(environment, ["spx"])
    def collect(**request):
        data = completed_snapshot(**request)
        series = data["series"][0]
        series["bars"] = [{"date": day, "high": 103, "low": 100, "close": 102}
                          for day in ("2026-09-22", "2026-09-23")]
        series.update(session_date="2026-09-23", expected_session_date="2026-09-23",
            expected_previous_session_date="2026-09-22", session_close_at="2026-09-23T20:00:00+00:00")
        return data
    monkeypatch.setattr(scheduler, "_collect_snapshot", collect)
    result = scheduler.collect_once("finance_test")
    assert result["status"] == "partial" and "not closed/published" in result["error"]
    assert scheduler.get_collection_status("finance_test")["last_success_at"] is None
