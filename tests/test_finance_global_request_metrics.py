"""Synthetic global contract fixtures. No network, model calls, or user DB access."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from policy_impact.company_wiki import loader
from policy_impact.domains.finance import profile
from policy_impact.domains.finance.market_catalog import get_symbol_metadata
from policy_impact.domains.finance.metrics import compute_market_metrics, rank_market_metrics
from policy_impact.domains.finance.request import parse_request
from policy_impact.domains.finance.schemas import FinanceSnapshot, MarketBar, MarketSeries
from policy_impact.domains.finance.sessions import latest_completed_session


NOW = datetime.fromisoformat("2026-09-23T00:40:00+00:00")


def test_global_profile_is_strict_unknown_holdings_and_never_saves_automatically(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    draft = profile.default_profile()
    assert draft["symbols"] == ["xauusd", "xagusd", "spx", "ixic", "dji"]
    assert draft["quality_policy"] == "strict" and draft["holdings"] is None
    assert draft["default_session_mode"] == "latest_completed"
    assert profile.load_profile("existing_nonfinance") is None
    assert not list(tmp_path.iterdir())


def test_daily_user_date_news_context_and_actual_session_are_separate():
    result = parse_request("今天行情怎么样", profile.default_profile(), now=NOW)
    assert result["session_mode"] == "latest_completed"
    assert result["period"] == {"start": "2026-09-23", "end": "2026-09-23"}
    assert result["document_period"] == {"start": "2026-09-17", "end": "2026-09-23"}
    assert result["document_period_basis"] == "recent_context_not_same_session_news"
    assert result["as_of"] == "2026-09-23T08:40:00+08:00"
    assert result["quality_policy"] == "strict"
    assert latest_completed_session("XNYS", as_of=result["as_of"])["session_date"] == "2026-09-22"


def test_real_m01_today_prices_are_not_replaced_by_macro_context_week():
    query = ("分析一下今天美国三大指数的行情，有什么共同变化和分歧？"
             "结合近一周的宏观资料解释可能原因，不生成报告。")
    now = datetime.fromisoformat("2026-09-23T20:03:59.391671+08:00")
    result = parse_request(query, profile.default_profile(), now=now)
    assert result["question"] == query
    assert result["symbols"] == ["spx", "ixic", "dji"]
    assert result["period"] == {"start": "2026-09-23", "end": "2026-09-23"}
    assert result["document_period"] == {"start": "2026-09-17", "end": "2026-09-23"}
    assert result["session_mode"] == "latest_completed"
    assert result["quality_policy"] == "strict"
    assert result["include_documents"]
    snapshot = daily_snapshot(*(daily_series(symbol, now) for symbol in result["symbols"]), now=now)
    snapshot.update({key: result[key] for key in ("period", "document_period", "session_mode", "as_of", "quality_policy")})
    metrics = compute_market_metrics(snapshot)
    assert len(metrics) == 3
    assert all(metric["effective_end_date"] == "2026-09-22" and metric["status"] == "ok" for metric in metrics)


@pytest.mark.parametrize("query,price_period,news_period,mode", [
    ("今天美股，结合近30天的宏观资料", ("2026-09-23", "2026-09-23"),
     ("2026-08-25", "2026-09-23"), "latest_completed"),
    ("今天黄金和近一周宏观资料", ("2026-09-23", "2026-09-23"),
     ("2026-09-17", "2026-09-23"), "latest_completed"),
    ("2026-09-18美股行情，参考2026-09-01至2026-09-17的宏观资料", ("2026-09-18", "2026-09-18"),
     ("2026-09-01", "2026-09-17"), "explicit_period"),
    ("最近一周美股，结合近30天宏观资料", ("2026-09-17", "2026-09-23"),
     ("2026-08-25", "2026-09-23"), "explicit_period"),
])
def test_independent_market_and_document_time_scopes(query, price_period, news_period, mode):
    result = parse_request(query, profile.default_profile(), now=NOW)
    assert result["period"] == dict(zip(("start", "end"), price_period))
    assert result["document_period"] == dict(zip(("start", "end"), news_period))
    assert result["session_mode"] == mode


@pytest.mark.parametrize("query,period", [
    ("今天回顾2026-09-19黄金", {"start": "2026-09-19", "end": "2026-09-19"}),
    ("2026年9月1日至2026年9月22日美股", {"start": "2026-09-01", "end": "2026-09-22"}),
    ("最近7天黄金", {"start": "2026-09-17", "end": "2026-09-23"}),
    ("昨天美股", {"start": "2026-09-22", "end": "2026-09-22"}),
])
def test_explicit_history_is_not_shifted_for_market_holidays_or_default_daily(query, period):
    result = parse_request(query, profile.default_profile(), now=NOW)
    assert result["session_mode"] == "explicit_period"
    assert result["period"] == result["document_period"] == period


@pytest.mark.parametrize("query,expected", [
    ("今天只看黄金", {"xauusd"}), ("今天只看白银", {"xagusd"}),
    ("今天XAU/USD", {"xauusd"}), ("今天美股主要指数", {"spx", "ixic", "dji"}),
    ("只看标普500", {"spx"}), ("只看纳斯达克综合指数", {"ixic"}),
    ("只看道琼斯", {"dji"}), ("沪深300最近一周", {"sh000300"}),
    ("黄金和美股", {"xauusd", "spx", "ixic", "dji"}),
])
def test_explicit_scope_never_falls_back_to_all_profile_symbols(query, expected):
    assert set(parse_request(query, profile.default_profile(), now=NOW)["symbols"]) == expected


@pytest.mark.parametrize("query", ["今天美元指数", "今天VIX", "今天只看美元", "只看黄金和VIX",
                                  "只看黄金期货", "只看黄金ETF", "只看纳指100", "实时行情"])
def test_unavailable_positive_requests_are_not_silently_substituted(query):
    with pytest.raises(ValueError):
        parse_request(query, profile.default_profile(), now=NOW)


@pytest.mark.parametrize("query,expected", [
    ("黄金白银和美股，不要用期货或ETF替代现货", {"xauusd", "xagusd", "spx", "ixic", "dji"}),
    ("今天全球金融，美元、VIX只作背景，暂无行情不编", {"xauusd", "xagusd", "spx", "ixic", "dji"}),
    ("黄金白银和美股，核心不全就不要发布", {"xauusd", "xagusd", "spx", "ixic", "dji"}),
    ("今天只看黄金，核心不全就不要发布黄金报告", {"xauusd"}),
    ("今天只看黄金，不看美股与白银", {"xauusd"}),
    ("今天行情，不看黄金", {"xagusd", "spx", "ixic", "dji"}),
    ("黄金美元/盎司", {"xauusd"}), ("美元/盎司的黄金", {"xauusd"}),
    ("只看黄金，不要用CFD代替", {"xauusd"}),
    ("黄金白银和美股，不要用黄金期货或白银ETF替代现货", {"xauusd", "xagusd", "spx", "ixic", "dji"}),
    ("只看黄金，不要用黄金CFD冒充黄金现货", {"xauusd"}),
    ("今天黄金和美股，不看美国三大指数", {"xauusd"}),
    ("今天美股，不要盘中行情", {"spx", "ixic", "dji"}),
])
def test_negative_exclusions_and_background_mentions_do_not_become_requests(query, expected):
    result = parse_request(query, profile.default_profile(), now=NOW)
    assert set(result["symbols"]) == expected
    assert result["quality_policy"] == "strict"


@pytest.mark.parametrize("query", [
    "我想理解最近全球市场在交易什么。请看国际现货黄金、现货白银、标普500、纳斯达克综合指数和道琼斯指数，"
    "各用最近一个完整交易日的数据，在聊天里用一到三条主线讲清楚，不生成报告。",
    "/report 生成全球宏观观察报告：国际现货黄金和白银用美元每金衡盎司，另含标普500、纳斯达克综合和道琼斯指数；"
    "各取最近完整交易日，用一到三条宏观主线、证据及反证解释市场。核心数据不全就不要发布。",
    "/report 生成完整的全球宏观报告，必须覆盖现货黄金、现货白银和美国三大指数；"
    "任何一项核心证据不合格就直接告诉我缺什么，不能以部分成功交付。",
], ids=["G01", "G09", "G10"])
def test_acceptance_initial_queries_preserve_complete_core_and_strict_policy(query):
    result = parse_request(query, profile.default_profile(), now=NOW)
    assert set(result["symbols"]) == {"xauusd", "xagusd", "spx", "ixic", "dji"}
    assert result["quality_policy"] == "strict"
    assert result["session_mode"] == "latest_completed"


def test_legacy_profile_defaults_remain_explicit_and_allow_partial():
    result = parse_request("金融观察", {"symbols": ["sh000001"], "report_days": 7}, now=NOW)
    assert result["session_mode"] == "explicit_period"
    assert result["quality_policy"] == "allow_partial"
    assert result["symbols"] == ["sh000001"]


def test_clock_hook_is_deterministic_and_rejects_naive_or_conflicting_dates():
    first = parse_request("今日金融", {}, today=date(2026, 9, 23))
    assert first == parse_request("今日金融", {}, today=date(2026, 9, 23))
    assert first["as_of"] == "2026-09-23T00:00:00+08:00"
    with pytest.raises(ValueError):
        parse_request("今日金融", {}, now=datetime(2026, 9, 23))
    with pytest.raises(ValueError):
        parse_request("今日金融", {}, now=NOW, today=date(2026, 9, 24))


def row(day, close):
    return {"date": day, "high": close + 1, "low": close - 1, "close": close}


def daily_series(symbol="spx", now=NOW):
    meta = get_symbol_metadata(symbol)
    if symbol in {"xauusd", "xagusd"}:
        session = {
            "session_date": "2026-09-22", "expected_session_date": "2026-09-22",
            "expected_previous_session_date": "2026-09-21", "session_close_at": "2026-09-23T00:00:00Z",
            "calendar_id": None, "session_date_basis": "bullionvault_utc_reference_day",
            "session_verified": True, "daily_cutoff_basis": "UTC complete sampling day; not a global official close",
            "session_evidence": {"publication_delay_minutes": 20, "natural_day_reference": True,
                                 "market_session_verified": False, "fixture": "synthetic-only"},
        }
    else:
        session = latest_completed_session(meta["calendar"], as_of=now)
    return {
        "symbol": symbol, "name": meta["name"], "asset_class": meta["asset_class"],
        "provider": "Synthetic fixture", "source_url": "https://example.org/test-only",
        "unit": meta["unit"], "timezone": meta["timezone"], "price_basis": meta["price_basis"],
        "quote_currency": "USD", "retrieved_at": now.isoformat(),
        "bars": [row(session["expected_previous_session_date"], 100), row(session["session_date"], 102)],
        **session,
    }


def daily_snapshot(*series, now=NOW):
    return {"snapshot_id": "synthetic-global-test", "collected_at": now.isoformat(),
            "as_of": now.isoformat(), "period": {"start": "2026-09-23", "end": "2026-09-23"},
            "document_period": {"start": "2026-09-17", "end": "2026-09-23"},
            "session_mode": "latest_completed", "quality_policy": "strict", "series": list(series)}


def test_hlc_without_open_remains_unknown_not_previous_close():
    candle = MarketBar.model_validate(row("2026-09-22", 100))
    assert candle.open is None
    assert candle.model_dump(mode="json")["open"] is None
    assert MarketBar.model_validate({**row("2026-09-22", 100), "open": 99.5}).open == 99.5
    for invalid in ({"open": 102}, {"high": 99}, {"low": 101}, {"open": float("nan")}):
        with pytest.raises(ValueError):
            MarketBar.model_validate({**row("2026-09-22", 100), **invalid})


@pytest.mark.parametrize("symbol", ["spx", "ixic", "dji", "xauusd", "xagusd"])
def test_daily_metrics_use_actual_session_not_cst_query_date_and_propagate_metadata(symbol):
    data = daily_snapshot(daily_series(symbol))
    original = deepcopy(data)
    metric, = compute_market_metrics(data)
    assert data == original
    assert metric["change_pct"] == metric["latest_daily_change_pct"] == 2
    assert metric["requested_start_date"] == "2026-09-23"
    assert metric["effective_start_date"] == metric["session_date"] == "2026-09-22"
    assert metric["point_count"] == metric["observed_session_count"] == 1
    assert metric["session_baseline_available"] and metric["status"] == "ok"
    assert metric["quote_currency"] == "USD" and metric["fx_conversion"] is None
    assert metric["price_unit"] == data["series"][0]["unit"]
    assert metric["points"][1]["timezone"] == data["series"][0]["timezone"]
    assert metric["points"][1]["price_basis"] == data["series"][0]["price_basis"]
    assert metric["session_evidence"] == data["series"][0]["session_evidence"]
    if symbol.startswith("xa"):
        assert metric["day_basis"] == "provider_reference_day"
        assert not metric["session_continuity_verified"]
        assert "not a universal official" in metric["basis"]
    else:
        assert metric["session_continuity_verified"] and metric["day_basis"] == "exchange_session"


def test_different_markets_can_have_different_completed_dates_without_fake_cross_date_ranking():
    now = datetime.fromisoformat("2026-09-23T20:40:00+00:00")
    data = daily_snapshot(daily_series("spx", now), daily_series("xauusd", now), now=now)
    metrics = compute_market_metrics(data)
    assert [metric["session_date"] for metric in metrics] == ["2026-09-23", "2026-09-22"]
    rank = rank_market_metrics(metrics)
    assert rank["status"] == "unavailable"
    assert rank["best_symbols"] == rank["worst_symbols"] == rank["rankings"] == []


@pytest.mark.parametrize("mutation", ["unverified", "stale", "future_close", "missing_baseline", "wrong_previous"])
def test_strict_daily_rejects_partial_or_incomplete_session_evidence(mutation):
    data = daily_snapshot(daily_series())
    series = data["series"][0]
    if mutation == "unverified":
        series["session_verified"] = False
    elif mutation == "stale":
        series["expected_session_date"] = "2026-09-23"
    elif mutation == "future_close":
        series["session_close_at"] = "2026-09-24T20:00:00Z"
    elif mutation == "missing_baseline":
        series["bars"].pop(0)
    else:
        series["bars"][0]["date"] = "2026-09-18"
    with pytest.raises(ValueError, match="strict quality"):
        compute_market_metrics(data)


def test_allow_partial_is_explicit_and_does_not_invent_daily_change_across_missing_session():
    data = daily_snapshot(daily_series())
    data["quality_policy"] = "allow_partial"
    data["series"][0]["bars"][0]["date"] = "2026-09-18"
    metric, = compute_market_metrics(data)
    assert metric["status"] == "partial"
    assert metric["latest_daily_change_pct"] is None
    assert not metric["session_baseline_available"]


def weekly_snapshot(now):
    series = daily_series("spx", now)
    series["bars"] = [row(day, 100 + index) for index, day in enumerate(
        ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"])]
    data = daily_snapshot(series, now=now)
    data.update(session_mode="explicit_period", period={"start": "2026-09-17", "end": "2026-09-23"})
    return data


@pytest.mark.parametrize("as_of", ["2026-09-23T12:03:59Z", "2026-09-23T19:59:59Z", "2026-09-23T20:19:59Z"])
def test_explicit_week_coverage_requires_only_sessions_completed_and_published_by_as_of(as_of):
    data = weekly_snapshot(datetime.fromisoformat(as_of.replace("Z", "+00:00")))
    metric, = compute_market_metrics(data)
    assert metric["requested_end_date"] == "2026-09-23"
    assert metric["effective_end_date"] == metric["end_date"] == "2026-09-22"
    assert metric["expected_session_count"] == metric["observed_session_count"] == 4
    assert metric["session_continuity_verified"] and metric["status"] == "ok"
    assert metric["coverage_publication_delay_minutes"] == 20
    assert metric["missing_session_dates"] == []
    assert metric["change_pct"] == 4


def test_incomplete_current_bar_is_excluded_not_used_as_an_official_close():
    data = weekly_snapshot(NOW)
    data["series"][0]["bars"].append(row("2026-09-23", 999))
    metric, = compute_market_metrics(data)
    assert metric["end_close"] == 104 and metric["change_pct"] == 4
    assert metric["excluded_uncompleted_session_dates"] == ["2026-09-23"]
    assert all(point["date"] <= "2026-09-22" for point in metric["points"])


@pytest.mark.parametrize("missing", ["2026-09-18", "2026-09-22"])
def test_completed_coverage_clipping_never_hides_missing_past_sessions(missing):
    data = weekly_snapshot(NOW)
    data["series"][0]["bars"] = [bar for bar in data["series"][0]["bars"] if bar["date"] != missing]
    data["series"][0]["session_date"] = data["series"][0]["bars"][-1]["date"]
    with pytest.raises(ValueError, match="observed dates do not cover"):
        compute_market_metrics(data)


def test_today_session_is_required_after_actual_close_and_publication_buffer():
    data = weekly_snapshot(NOW)
    data["as_of"] = "2026-09-23T20:20:00Z"
    with pytest.raises(ValueError, match="observed dates do not cover"):
        compute_market_metrics(data)
    data["series"][0]["bars"].append(row("2026-09-23", 105))
    metric, = compute_market_metrics(data)
    assert metric["effective_end_date"] == "2026-09-23" and metric["expected_session_count"] == 5


def test_historical_price_window_is_not_extended_to_the_latest_completed_day():
    data = weekly_snapshot(NOW)
    data["period"]["end"] = "2026-09-18"
    metric, = compute_market_metrics(data)
    assert metric["requested_end_date"] == metric["effective_end_date"] == "2026-09-18"
    assert metric["expected_session_count"] == 2


def test_explicit_today_before_close_does_not_substitute_yesterday():
    data = weekly_snapshot(NOW)
    data["period"]["start"] = "2026-09-23"
    with pytest.raises(ValueError, match="no observations in the requested window"):
        compute_market_metrics(data)


@pytest.mark.parametrize("field,value", [("price_basis", "cfd_spot"), ("price_basis", "futures"),
                                       ("unit", "USD/kg"), ("quote_currency", "CNY")])
def test_spot_cannot_be_substituted_by_cfd_futures_or_wrong_unit(field, value):
    data = daily_snapshot(daily_series("xauusd"))
    data["series"][0][field] = value
    with pytest.raises(ValueError):
        compute_market_metrics(data)


def test_spot_requires_provider_cutoff_evidence_not_an_equity_calendar():
    data = daily_snapshot(daily_series("xauusd"))
    data["series"][0]["daily_cutoff_basis"] = None
    with pytest.raises(ValueError, match="provider cutoff"):
        compute_market_metrics(data)


@pytest.mark.parametrize("day", ["2026-09-21", "2026-09-22"])
def test_constant_reference_day_cannot_pass_strict_as_a_fresh_spot_session(day):
    data = daily_snapshot(daily_series("xauusd"))
    data["series"][0]["session_evidence"]["reference_days"] = {day: {"constant_reference_day": True}}
    with pytest.raises(ValueError, match="carried forward"):
        compute_market_metrics(data)


def test_recorded_fx_metadata_is_forwarded_without_applying_an_unrequested_conversion():
    data = daily_snapshot(daily_series())
    conversion = {"from_currency": "EUR", "to_currency": "USD", "rate": 1.2,
                  "as_of": "2026-09-22T20:00:00Z", "source_url": "https://example.org/fx-fixture"}
    data["series"][0]["fx_conversion"] = conversion
    metric, = compute_market_metrics(data)
    assert metric["start_close"] == 100 and metric["end_close"] == 102
    assert metric["change_pct"] == 2 and metric["fx_conversion"]["rate"] == 1.2
    assert metric["fx_conversion"]["source_url"] == conversion["source_url"]


def test_schema_requires_actual_bar_and_aware_metadata_and_preserves_legacy_snapshot():
    series = daily_series()
    with pytest.raises(ValueError, match="actual market bar"):
        MarketSeries.model_validate({**series, "session_date": "2026-09-23"})
    with pytest.raises(ValueError):
        MarketSeries.model_validate({**series, "session_close_at": "2026-09-22T20:00:00"})
    old = FinanceSnapshot.model_validate({"snapshot_id": "legacy", "collected_at": NOW,
                                         "period": {"start": "2026-09-01", "end": "2026-09-22"}})
    assert old.session_mode == "explicit_period" and old.quality_policy == "allow_partial"
