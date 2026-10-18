"""Offline regressions: synthetic fixtures are never used as acquisition fallbacks."""

from copy import deepcopy
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import localcontext
import hashlib
import json

import pytest

from policy_impact.domains.finance import acquisition as acq
from policy_impact.domains.finance import bullionvault_source as bv
from policy_impact.domains.finance.global_market_sources import parse_daily
from policy_impact.domains.finance.market_catalog import INSTRUMENTS, get_symbol_metadata
from policy_impact.domains.finance.schemas import FinanceSnapshot, Period
from policy_impact.domains.finance.metrics import compute_market_metrics
from policy_impact.domains.finance.quality import FinanceQualityError, require_market_coverage
from policy_impact.source_connectors.http_client import FetchResponse


AS_OF = "2026-09-23T11:00:00+00:00"


def response(url, payload, content_type="application/json"):
    raw = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
    return FetchResponse(url, 200, content_type, raw, AS_OF, hashlib.sha256(raw).hexdigest())


@pytest.fixture
def config(monkeypatch):
    value = acq._load_config()
    value["document_sources"] = []
    value["global_document_sources"] = []
    monkeypatch.setattr(acq, "_load_config", lambda: deepcopy(value))
    monkeypatch.setattr(acq, "fetch_url", lambda *a, **k: pytest.fail("Unexpected live HTTP"))
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: pytest.fail("Unexpected live WebSocket"))
    return value


def collect(symbols=None, **kwargs):
    return acq.collect_finance_snapshot(start="2026-09-23", end="2026-09-23",
        symbols=symbols or ["spx"], session_mode="latest_completed", as_of=AS_OF,
        include_documents=False, **kwargs)


def test_latest_completed_preserves_multi_day_window_for_index_and_spot(monkeypatch):
    observed = []
    monkeypatch.setattr(acq, "_session_plan", lambda *args: {
        "expected_session_date": "2026-09-22",
        "expected_previous_session_date": "2026-09-21",
        "session_evidence": {},
        "calendar_id": "XNYS",
    })
    monkeypatch.setattr(acq, "session_dates", lambda calendar, start, end: (
        observed.append((start, end)) or []
    ))
    monkeypatch.setattr(bv, "history_interval", lambda start, end: (
        observed.append((start, end)) or 14400
    ))
    period = Period(start="2026-08-25", end="2026-09-23")
    now = datetime.fromisoformat(AS_OF)
    acq._collect_index("spx", {"global_market_sources": []}, period, now, object(), "latest_completed")
    acq._collect_spot("xauusd", {"spot_market_sources": [], "spot_candidates": []},
                      period, now, object(), "latest_completed")
    assert (date(2026, 8, 25), date(2026, 9, 22)) in observed
    assert acq._market_window_start(
        Period(start="2026-09-23", end="2026-09-23"), date(2026, 9, 22), "latest_completed"
    ) == date(2026, 9, 22)


def yahoo(close=None):
    stamps = [int(datetime(2026, 9, day, 13, 30, tzinfo=timezone.utc).timestamp()) for day in (21, 22)]
    return {"chart": {"result": [{"meta": {"symbol": "^GSPC", "instrumentType": "INDEX",
        "currency": "USD", "exchangeTimezoneName": "America/New_York", "dataGranularity": "1d",
        "regularMarketPrice": 9999}, "timestamp": stamps,
        "indicators": {"quote": [{"open": [200, 210], "high": [210, 220],
            "low": [190, 200], "close": [200, close]}]}}], "error": None}}


def sina_rows(days=(21, 22)):
    return [{"d": f"2026-09-{day:02}", "o": 100, "h": 120, "l": 90, "c": 100 + i}
            for i, day in enumerate(days)]


def payload(days=(21, 22, 23), *, constant=False):
    prices = []
    for day in days:
        for hour in range(0, 24, 4):
            value = 140000 if constant else 140000 + day * 100 + hour
            prices.append({"priceTime": int(datetime(2026, 9, day, hour, tzinfo=timezone.utc).timestamp() * 1000),
                "high": value if constant else value + 30, "low": value if constant else value - 30,
                "latestPrice": value})
    return {"name": "Galmarley_Mid", "securityId": "AUX", "valuationSecurityId": "USD",
            "interval": 14400, "batch": "full", "prices": prices}


def history(p):
    return {"payload": p, "url": bv.EVIDENCE_URLS[0], "transport_url": "wss://chart-data.bullionvault.com/price_feed/",
            "transport": "public_websocket_stomp", "sha256": "fixture-hash", "bytes": 3000, "retrieved_at": AS_OF}


def reference(p, as_of=AS_OF):
    return bv.reference_days(p, "xauusd", as_of=datetime.fromisoformat(as_of),
                             start=date(2026, 9, 1), end=date(2026, 9, 23))


def quarterly_payload(symbol="xauusd"):
    p = payload()
    p.update(interval=43200, securityId="AUX" if symbol == "xauusd" else "AGX", prices=[])
    for i in range(32):
        day = datetime(2026, 8, 23, tzinfo=timezone.utc) + timedelta(days=i)
        for hour in (0, 12):
            value = 140000 + i * 100 + hour
            p["prices"].append({"priceTime": int((day + timedelta(hours=hour)).timestamp() * 1000),
                "high": value + 30, "low": value - 30, "latestPrice": value})
    return p


@pytest.mark.parametrize("start,end,expected", [
    (date(2026, 9, 22), date(2026, 9, 22), 14400),
    (date(2026, 9, 17), date(2026, 9, 22), 14400),
    (date(2026, 8, 26), date(2026, 9, 22), 14400),
    (date(2026, 8, 25), date(2026, 9, 22), 43200),
    (date(2026, 8, 10), date(2026, 9, 22), 43200),
])
def test_spot_window_leaves_room_for_pre_period_baseline(start, end, expected):
    assert bv.history_interval(start, end) == expected


@pytest.mark.parametrize("symbol", ["xauusd", "xagusd"])
def test_monthly_spot_uses_single_quarterly_feed_with_real_baseline(config, monkeypatch, symbol):
    calls = []
    def fetch(requested_symbol, **kwargs):
        calls.append((requested_symbol, kwargs["interval_seconds"]))
        return history(quarterly_payload(requested_symbol))
    monkeypatch.setattr(bv, "fetch_history", fetch)
    s = acq.collect_finance_snapshot(start="2026-08-25", end="2026-09-23", symbols=[symbol],
        as_of=AS_OF, quality_policy="strict", include_documents=False)
    assert calls == [(symbol, 43200)]
    series = s["series"][0]
    assert series["bars"][0]["date"] == "2026-08-24"
    assert series["bars"][-1]["date"] == "2026-09-22"
    assert len(series["bars"]) == 30 and series["session_verified"]
    evidence = series["session_evidence"]
    assert evidence["interval_seconds"] == 43200
    assert evidence["reference_days"]["2026-08-24"]["sample_count"] == 2
    assert evidence["history_window_days"] == 90
    assert "12-hour" in series["daily_cutoff_basis"]
    metrics = compute_market_metrics(s)
    assert metrics[0]["baseline_available"] and metrics[0]["start_date"] == "2026-08-24"
    assert require_market_coverage({"symbols": [symbol], "period": s["period"]}, s, metrics)["decision"] == "allow"


def test_quarterly_feed_rejects_incomplete_days_and_resolution_mismatch(config, monkeypatch):
    p = quarterly_payload()
    stamp = int(datetime(2026, 9, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
    p["prices"] = [point for point in p["prices"] if point["priceTime"] != stamp]
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: history(p))
    s = acq.collect_finance_snapshot(start="2026-08-25", end="2026-09-23", symbols=["xauusd"],
        as_of=AS_OF, quality_policy="strict", include_documents=False)
    assert not s["series"][0]["session_verified"]
    assert "2026-09-01" in s["series"][0]["session_evidence"]["missing_reference_dates"]
    with pytest.raises(FinanceQualityError, match="2026-09-01"):
        require_market_coverage({"symbols": ["xauusd"], "period": s["period"]}, s, compute_market_metrics(s))
    with pytest.raises(ValueError, match="identity"):
        reference(p)


def test_unsupported_spot_resolution_is_rejected_before_network():
    with pytest.raises(ValueError, match="unsupported"):
        bv.fetch_history("xauusd", interval_seconds=86400)


@pytest.mark.parametrize("missing_baseline", [False, True])
def test_strict_cache_reuses_only_complete_market_snapshots(config, monkeypatch, missing_baseline):
    from policy_impact.domains.finance import store, tools
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: history(quarterly_payload()))
    complete = acq.collect_finance_snapshot(start="2026-08-25", end="2026-09-23", symbols=["xauusd"],
        as_of=AS_OF, quality_policy="strict", include_documents=False)
    cached = deepcopy(complete)
    if missing_baseline:
        cached["series"][0]["bars"].pop(0)
    saved, calls = [], []
    class Store:
        def latest_snapshot(self):
            return cached
        def save_snapshot(self, value):
            saved.append(value)
    monkeypatch.setattr(store, "FinanceStore", lambda company_id: Store())
    monkeypatch.setattr(store, "exclusive_lock", lambda *args: nullcontext(True))
    def acquire(**kwargs):
        calls.append(kwargs)
        return complete
    monkeypatch.setattr(acq, "collect_finance_snapshot", acquire)
    request = {"symbols": ["xauusd"], "period": complete["period"], "as_of": AS_OF,
               "session_mode": "explicit_period", "quality_policy": "strict", "include_documents": False}
    result = tools.collect("test_company", request)
    assert result["series"][0]["bars"][0]["date"] == "2026-08-24"
    assert len(calls) == len(saved) == int(missing_baseline)


def test_catalog_identity_and_defensive_copy():
    assert set(("spx", "ixic", "dji", "xauusd", "xagusd")) <= INSTRUMENTS.keys()
    gold = get_symbol_metadata("xauusd")
    assert gold["unit"] == "USD/troy_oz" and gold["price_basis"] == "spot_price"
    assert gold["calendar"] is None and gold["enabled"]
    gold["aliases"].append("not shared")
    assert "not shared" not in get_symbol_metadata("xauusd")["aliases"]
    with pytest.raises(ValueError):
        get_symbol_metadata("GC=F")


def test_yahoo_null_close_is_missing_not_regular_quote(config):
    bars, evidence = parse_daily(json.dumps(yahoo()), config["global_market_sources"][0],
                                "spx", date(2026, 9, 21), date(2026, 9, 22))
    assert [b.close for b in bars] == [200]
    assert evidence["dropped_rows"] == [{"date": "2026-09-22", "reason": "null_ohlc"}]


@pytest.mark.parametrize("key,value", [("symbol", "GLD"), ("instrumentType", "ETF"),
    ("currency", "EUR"), ("dataGranularity", "1h"), ("exchangeTimezoneName", "UTC")])
def test_yahoo_wrong_identity_rejected(config, key, value):
    p = yahoo(210)
    p["chart"]["result"][0]["meta"][key] = value
    with pytest.raises(ValueError, match="identity"):
        parse_daily(json.dumps(p), config["global_market_sources"][0], "spx", date(2026, 9, 21), date(2026, 9, 22))


def test_realistic_null_yahoo_fallback_uses_whole_sina_series(config, monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs["retries"] == 0 and kwargs["timeout_seconds"] <= 8
        return response(url, yahoo() if "yahoo" in url else sina_rows())
    monkeypatch.setattr(acq, "fetch_url", fetch)
    snapshot = collect(quality_policy="strict")
    series = snapshot["series"][0]
    assert series["provider"] == "Sina Finance US" and [b["close"] for b in series["bars"]] == [100, 101]
    assert series["session_verified"] and series["session_date"] == "2026-09-22"
    assert series["expected_previous_session_date"] == "2026-09-21"
    assert series["timezone"] == "America/New_York" and series["calendar_id"] == "XNYS"
    assert snapshot["period"]["start"] == "2026-09-23"
    assert [s["status"] for s in snapshot["source_statuses"]] == ["partial", "ok"]
    assert len(calls) == 2
    assert FinanceSnapshot.model_validate(snapshot).model_dump(mode="json") == snapshot


def test_all_indices_fail_bounded_alternatives_no_fabrication(config, monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        raise TimeoutError("offline fixture")
    monkeypatch.setattr(acq, "fetch_url", fetch)
    snapshot = collect()
    assert snapshot["series"] == [] and len(calls) == 5
    assert [s["status"] for s in snapshot["source_statuses"]] == ["error"] * 3


def test_calendar_unavailable_does_not_guess_weekdays(config, monkeypatch):
    def fail(*a, **k):
        raise acq.SessionCalendarUnavailable("fixture unavailable")
    monkeypatch.setattr(acq, "latest_completed_session", fail)
    result = collect()
    assert not result["series"] and result["source_statuses"][0]["source_id"] == "calendar:spx"


def test_missing_baseline_and_expected_session_never_verified(config, monkeypatch):
    config["global_market_sources"] = [config["global_market_sources"][1]]
    monkeypatch.setattr(acq, "fetch_url", lambda url, **kw: response(url, sina_rows((18, 21))))
    result = collect()
    s = result["series"][0]
    assert s["session_date"] == "2026-09-21" and s["expected_session_date"] == "2026-09-22"
    assert s["session_close_at"] is None and not s["session_verified"]


def test_non_session_provider_rows_not_admitted_as_valid_index_days(config, monkeypatch):
    config["global_market_sources"] = [config["global_market_sources"][1]]
    monkeypatch.setattr(acq, "fetch_url", lambda url, **kw: response(url, sina_rows((20, 21, 22))))
    s = collect()["series"][0]
    assert [b["date"] for b in s["bars"]] == ["2026-09-21", "2026-09-22"]
    assert not s["session_verified"]
    assert s["session_evidence"]["unexpected_session_dates"] == ["2026-09-20"]


@pytest.mark.parametrize("as_of,expected", [
    ("2026-09-08T20:19:59+00:00", "2026-09-04"),
    ("2026-09-08T20:20:00+00:00", "2026-09-08"),
    ("2026-11-27T18:19:59+00:00", "2026-11-25"),
    ("2026-11-27T18:20:00+00:00", "2026-11-27"),
])
def test_index_calendar_holiday_and_early_close_not_weekday_guess(as_of, expected):
    plan = acq._session_plan(get_symbol_metadata("spx"), Period(start="2026-01-01", end="2026-12-31"),
                             datetime.fromisoformat(as_of), "latest_completed")
    assert str(plan["expected_session_date"]) == expected


def test_six_buckets_hlc_conversion_and_no_open_current_day_excluded():
    bars, e = reference(payload())
    assert [b.date.isoformat() for b in bars] == ["2026-09-21", "2026-09-22"]
    assert all(b.open is None and b.volume is None for b in bars)
    assert bars[-1].close == bv._per_ounce(142220)
    assert e["dropped_days"] == [{"date": "2026-09-23", "reason": "utc_reference_day_not_completed"}]
    d = e["reference_days"]["2026-09-22"]
    assert d["sample_count"] == 6 and d["last_bucket_start_at"] == "2026-09-22T20:00:00+00:00"
    assert d["reference_day_end_at"] == "2026-09-23T00:00:00+00:00"
    assert d["last_observation_time_exact"] is False
    assert e["market_session_verified"] is False
    with localcontext() as ctx:
        ctx.prec = 2
        assert bv._per_ounce(142220) == bars[-1].close


@pytest.mark.parametrize("as_of,days", [("2026-09-23T00:19:59+00:00", [21]),
                                      ("2026-09-23T00:20:00+00:00", [21, 22])])
def test_spot_publication_buffer(as_of, days):
    assert [b.date.day for b in reference(payload(), as_of)[0]] == days


@pytest.mark.parametrize("damage", ["missing", "null"])
def test_missing_bucket_or_hlc_drops_entire_day(damage):
    p = payload((21, 22))
    if damage == "missing":
        del p["prices"][-1]
    else:
        p["prices"][-1]["latestPrice"] = None
    assert [b.date.day for b in reference(p)[0]] == [21]


@pytest.mark.parametrize("key,value", [("securityId", "GC"), ("valuationSecurityId", "EUR"),
    ("name", "CFD"), ("interval", 86400), ("batch", "update")])
def test_spot_wrong_metadata_rejected(key, value):
    p = payload()
    p[key] = value
    with pytest.raises(ValueError, match="identity"):
        reference(p)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, True])
def test_spot_invalid_values_rejected(value):
    p = payload()
    p["prices"][0]["high"] = value
    with pytest.raises(ValueError):
        reference(p)


def test_spot_conflicting_timestamp_rejected():
    p = payload()
    p["prices"].append({**p["prices"][0], "latestPrice": 1})
    with pytest.raises(ValueError, match="conflicting"):
        reference(p)


def test_spot_metadata_original_units_and_citable_source(config, monkeypatch):
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: history(payload()))
    result = collect(["xauusd"], quality_policy="strict")
    s = result["series"][0]
    assert s["source_url"] == bv.EVIDENCE_URLS[0] and s["session_verified"]
    assert s["unit"] == "USD/troy_oz" and s["fx_conversion"] is None and s["calendar_id"] is None
    e = s["session_evidence"]
    assert e["provider_metadata"]["securityId"] == "AUX" and e["raw_price_unit"] == "USD/kg"
    assert e["publication_delay_minutes"] == 20 and not e["constant_reference_day"]
    assert e["last_observation_time_exact"] is False
    assert "wss://" in result["source_statuses"][0]["detail"]


def test_constant_reference_is_not_rolled_back_to_guessed_trading_day(config, monkeypatch):
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: history(payload(constant=True)))
    s = collect(["xauusd"])["series"][0]
    assert s["session_date"] == s["expected_session_date"] == "2026-09-22"
    assert s["session_evidence"]["constant_reference_day"]
    assert s["session_evidence"]["quote_carry_forward_possible"]
    assert s["session_evidence"]["market_session_verified"] is False


def test_entire_missing_reference_day_not_skipped_or_filled(config, monkeypatch):
    monkeypatch.setattr(bv, "fetch_history", lambda *a, **k: history(payload((19, 20, 23))))
    s = collect(["xauusd"])["series"][0]
    assert s["session_date"] == "2026-09-20" and s["expected_session_date"] == "2026-09-22"
    assert not s["session_verified"] and s["session_close_at"] is None
    assert s["session_evidence"]["missing_reference_dates"] == ["2026-09-21", "2026-09-22"]


def test_spot_failure_probes_candidates_without_admitting_cfd(config, monkeypatch):
    calls = []
    def fail(*a, **k):
        raise TimeoutError("fixture")
    monkeypatch.setattr(bv, "fetch_history", fail)
    monkeypatch.setattr(acq, "fetch_url", lambda url, **k: calls.append(url) or response(url, sina_rows()))
    result = collect(["xauusd"], quality_policy="allow_partial")
    assert result["series"] == [] and len(calls) == 4
    assert all(s["count"] == 0 for s in result["source_statuses"])
    assert any("sina_cfd" in s["source_id"] and s["status"] == "disabled" for s in result["source_statuses"])


@pytest.mark.parametrize("kwargs", [{"as_of": "2026-09-23T11:00:00"}, {"session_mode": "period"},
    {"quality_policy": "unknown"}, {"include_documents": "false"},
    {"document_period": {"start": "2026-09-23", "end": "2026-09-22"}}])
def test_bad_contract_rejected_before_network(config, kwargs):
    with pytest.raises((ValueError, TypeError)):
        acq.collect_finance_snapshot(start="2026-09-23", end="2026-09-23", symbols=["spx"], **kwargs)


def test_documents_use_independent_period_and_fed_monetary_selector(config, monkeypatch):
    official = {"source_id": "fed_monetary", "publisher": "Federal Reserve Board", "adapter": "fed",
        "kind": "policy_release", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "allowed_domains": ["www.federalreserve.gov"], "selectors": ["#article"], "quality": "first party"}
    config["document_sources"] = [official]
    article_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
    text = "Official monetary policy statement. " * 30
    def fetch(url, **k):
        if url.endswith(".xml"):
            return response(url, f"<rss><channel><item><title>FOMC statement</title><link>{article_url}</link>"
                "<pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>", "application/xml")
        return response(url, f'<html><div id="article">{text}</div></html>', "text/html")
    monkeypatch.setattr(acq, "fetch_url", fetch)
    s = acq.collect_finance_snapshot(start="2026-09-23", end="2026-09-23", symbols=[],
        document_period={"start": "2026-09-15", "end": "2026-09-17"})
    assert len(s["documents"]) == 1 and s["documents"][0]["published_at"] == "2026-09-16"
    assert s["documents"][0]["text"].strip() == text.strip()
    assert s["documents"][0]["kind"] == "policy_release"
