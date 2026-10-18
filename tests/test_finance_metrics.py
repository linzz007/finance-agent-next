"""Offline regressions using synthetic prices and an explicitly recorded close fixture."""

from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
import json

import pytest

from policy_impact.domains.finance.metrics import compute_market_metrics, rank_market_metrics


def bar(day, close):
    return {"date": day, "open": close, "high": close, "low": close, "close": close}


def snapshot(bars, start="2024-06-03", end="2024-06-04"):
    return {
        "schema_version": "finance.snapshot.v1", "snapshot_id": "synthetic-unit-test",
        "collected_at": "2024-06-05T01:00:00Z", "period": {"start": start, "end": end},
        "series": [{"symbol": "sh000001", "name": "Test index", "provider": "Test fixture",
                    "source_url": "https://example.com/unit-test-only", "price_basis": "price_index",
                    "retrieved_at": "2024-06-05T01:00:00Z", "bars": bars}],
    }


def test_inclusive_return_includes_first_day_and_uses_nearest_prior_close():
    data = snapshot([bar("2024-05-30", 80), bar("2024-05-31", 100),
                     bar("2024-06-03", 110), bar("2024-06-04", 121)])
    metric, = compute_market_metrics(data)
    assert metric["start_date"] == "2024-05-31"
    assert metric["change_pct"] == 21
    assert metric["latest_daily_change_pct"] == 10
    assert metric["first_trade_date"] == "2024-06-03"
    assert metric["requested_start_date"] == "2024-06-03"
    assert metric["points"] == [
        {"date": "2024-05-31", "close": 100, "is_baseline": True, "change_pct": 0},
        {"date": "2024-06-03", "close": 110, "is_baseline": False, "change_pct": 10},
        {"date": "2024-06-04", "close": 121, "is_baseline": False, "change_pct": 21},
    ]


def test_single_trading_day_uses_previous_session_not_same_day_zero():
    metric, = compute_market_metrics(snapshot([bar("2024-05-31", 100), bar("2024-06-03", 103)],
                                             end="2024-06-03"))
    assert metric["change_pct"] == metric["latest_daily_change_pct"] == 3
    assert metric["baseline_available"]


def test_missing_baseline_exposes_actual_interval_and_never_claims_full_period():
    metric, = compute_market_metrics(snapshot([bar("2024-06-03", 110), bar("2024-06-04", 121)]))
    assert metric["change_pct"] == 10
    assert metric["start_date"] == "2024-06-03"
    assert metric["status"] == "partial"
    assert not metric["baseline_available"]
    assert "NOT a full requested-period return" in metric["basis"]


def test_missing_baseline_single_point_is_unavailable_not_zero():
    metric, = compute_market_metrics(snapshot([bar("2024-06-03", 110)]))
    assert metric["change_pct"] is None
    assert metric["latest_daily_change_pct"] is None
    assert metric["status"] == "insufficient_data"
    assert metric["points"][0]["change_pct"] is None
    json.dumps(metric, allow_nan=False)


@pytest.mark.parametrize("bars", [[], [bar("2024-05-31", 100)], [bar("2024-06-05", 999)]])
def test_no_in_period_observations_produce_no_made_up_metric(bars):
    assert compute_market_metrics(snapshot(bars)) == []


def test_sort_dedupe_ignore_after_end_and_do_not_mutate_snapshot():
    bars = [bar("2024-06-04", 121), bar("2024-06-05", 999), bar("2024-05-31", 100),
            bar("2024-06-03", 110), bar("2024-06-03", 110)]
    data = snapshot(bars)
    original = deepcopy(data)
    result = compute_market_metrics(data)
    assert data == original
    assert result == compute_market_metrics(snapshot(list(reversed(bars))))
    assert result[0]["latest_trade_date"] == "2024-06-04"
    assert result[0]["point_count"] == 2


def test_conflicting_duplicates_are_rejected():
    with pytest.raises(ValueError, match="conflicting duplicate"):
        compute_market_metrics(snapshot([bar("2024-06-03", 100), bar("2024-06-03", 101)]))


def test_multiple_providers_for_same_symbol_are_rejected():
    data = snapshot([bar("2024-06-03", 100)])
    data["series"].append(deepcopy(data["series"][0]))
    data["series"][1]["provider"] = "Other test provider"
    with pytest.raises(ValueError, match="cannot mix providers"):
        compute_market_metrics(data)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_invalid_prices_are_not_silently_removed(price):
    with pytest.raises(ValueError):
        compute_market_metrics(snapshot([bar("2024-05-31", price), bar("2024-06-03", 100)]))


def test_inconsistent_ohlc_is_rejected():
    row = {**bar("2024-06-03", 100), "high": 99}
    with pytest.raises(ValueError):
        compute_market_metrics(snapshot([row]))


def test_weekend_start_and_end_report_real_sessions():
    metric, = compute_market_metrics(snapshot([bar("2024-05-31", 100), bar("2024-06-03", 102),
                                              bar("2024-06-07", 104)],
                                             start="2024-06-01", end="2024-06-09"))
    assert metric["change_pct"] == 4
    assert metric["end_date"] == "2024-06-07"
    assert metric["end_gap_calendar_days"] == 2
    assert "continuity is unverified" in metric["basis"]


def test_long_holiday_nearest_known_baseline_and_daily_basis_are_explicit():
    metric, = compute_market_metrics(snapshot([bar("2024-02-08", 100), bar("2024-02-19", 101)],
                                             start="2024-02-15", end="2024-02-19"))
    assert metric["change_pct"] == 1
    assert metric["latest_daily_baseline_date"] == "2024-02-08"
    assert "previous_known_close" in metric["latest_daily_change_basis"]


def test_decimal_rounding_deterministic_and_independent_of_global_context():
    data = snapshot([bar("2024-05-31", 3), bar("2024-06-03", 4)])
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = True
        first = compute_market_metrics(data)
    with localcontext() as ctx:
        ctx.prec = 60
        second = compute_market_metrics(data)
    assert first == second
    assert first[0]["change_pct"] == 33.333333
    assert Decimal(str(first[0]["change_pct"])).is_finite()


def test_overflowing_percentage_cannot_escape_as_json_infinity():
    with pytest.raises(ValueError, match="finite JSON"):
        compute_market_metrics(snapshot([bar("2024-05-31", 1e-300), bar("2024-06-03", 1e300)]))


def test_invalid_period_extra_fields_and_wrong_price_basis_rejected():
    data = snapshot([])
    data["period"]["end"] = "2024-01-01"
    with pytest.raises(ValueError):
        compute_market_metrics(data)
    data = snapshot([])
    data["unrecognized"] = True
    with pytest.raises(ValueError):
        compute_market_metrics(data)
    data = snapshot([])
    data["series"][0]["price_basis"] = "total_return"
    with pytest.raises(ValueError, match="price_index"):
        compute_market_metrics(data)


def test_required_output_fields_and_negative_return():
    metric, = compute_market_metrics(snapshot([bar("2024-05-31", 100), bar("2024-06-03", 90)]))
    assert metric["change_pct"] == -10
    assert set("metric_id symbol name start_date end_date start_close end_close change_pct unit "
               "source_url provider basis points latest_daily_change_pct latest_trade_date".split()) <= metric.keys()


def test_period_close_extrema_and_drawdown_exclude_baseline_intraday_and_volume():
    rows = [bar("2024-05-31", 200), bar("2024-06-03", 100), bar("2024-06-04", 120),
            bar("2024-06-05", 90), bar("2024-06-06", 110)]
    rows[1].update(high=999, low=1, volume=123456789)
    metric, = compute_market_metrics(snapshot(rows, end="2024-06-06"))
    assert (metric["start_date"], metric["start_close"]) == ("2024-05-31", 200)
    assert (metric["period_first_date"], metric["period_first_close"]) == ("2024-06-03", 100)
    assert (metric["period_last_date"], metric["period_last_close"]) == ("2024-06-06", 110)
    assert (metric["period_peak_date"], metric["period_peak_close"]) == ("2024-06-04", 120)
    assert (metric["period_trough_date"], metric["period_trough_close"]) == ("2024-06-05", 90)
    assert metric["max_drawdown_pct"] == 25
    assert (metric["max_drawdown_peak_date"], metric["max_drawdown_peak_close"]) == ("2024-06-04", 120)
    assert (metric["max_drawdown_trough_date"], metric["max_drawdown_trough_close"]) == ("2024-06-05", 90)
    assert metric["min_change_from_pre_period_baseline_pct"] == -55
    assert metric["min_change_from_pre_period_baseline_date"] == "2024-06-05"
    assert metric["min_change_from_pre_period_baseline_close"] == 90
    assert metric["pre_period_baseline_date"] == "2024-05-31"
    assert metric["pre_period_baseline_close"] == 200
    assert "nonnegative" in metric["max_drawdown_basis"]
    assert "NOT drawdown" in metric["min_change_from_pre_period_baseline_basis"]
    assert metric["price_unit"] == snapshot(rows)["series"][0].get("unit", "\u70b9")
    assert metric["provider"] == "Test fixture"
    assert metric["source_url"] == "https://example.com/unit-test-only"
    assert "volume" not in json.dumps(metric)


@pytest.mark.parametrize("closes, expected_loss, peak_day, trough_day", [
    ([50, 100], 0, None, None),
    ([100], 0, None, None),
    ([100, 100, 100], 0, None, None),
    ([100, 90, 80], 20, 3, 5),
    ([100, 80, 120, 100], 20, 3, 4),
    ([100, 100, 80, 100, 80], 20, 3, 5),
])
def test_drawdown_obeys_chronology_and_earliest_ties(closes, expected_loss, peak_day, trough_day):
    rows = [bar(f"2024-06-{day:02d}", close) for day, close in enumerate(closes, start=3)]
    metric, = compute_market_metrics(snapshot(rows, end="2024-06-09"))
    assert metric["max_drawdown_pct"] == expected_loss
    assert metric["max_drawdown_peak_date"] == (f"2024-06-{peak_day:02d}" if peak_day else None)
    assert metric["max_drawdown_trough_date"] == (f"2024-06-{trough_day:02d}" if trough_day else None)
    assert metric["period_peak_date"] == rows[closes.index(max(closes))]["date"]
    assert metric["period_trough_date"] == rows[closes.index(min(closes))]["date"]


def test_drawdown_compares_unrounded_ratios_before_selecting_dates():
    metric, = compute_market_metrics(snapshot([
        bar("2024-06-03", 100), bar("2024-06-04", 99.9999999),
        bar("2024-06-05", 100), bar("2024-06-06", 99.9999998),
    ], end="2024-06-06"))
    assert metric["max_drawdown_pct"] == 0  # Both losses round to six-decimal zero.
    assert metric["max_drawdown_peak_date"] == "2024-06-03"
    assert metric["max_drawdown_trough_date"] == "2024-06-06"


def test_relative_baseline_minimum_can_be_positive_and_is_not_drawdown():
    metric, = compute_market_metrics(snapshot([
        bar("2024-05-31", 50), bar("2024-06-03", 110), bar("2024-06-04", 100),
    ]))
    assert metric["min_change_from_pre_period_baseline_pct"] == 100
    assert metric["max_drawdown_pct"] == 9.090909


@pytest.mark.parametrize("closes", [[110], [110, 121]])
def test_missing_baseline_does_not_fabricate_relative_minimum_or_first_day_direction(closes):
    rows = [bar(f"2024-06-{day:02d}", close) for day, close in enumerate(closes, start=3)]
    metric, = compute_market_metrics(snapshot(rows))
    for suffix in ("pct", "date", "close"):
        assert metric[f"min_change_from_pre_period_baseline_{suffix}"] is None
    assert metric["pre_period_baseline_date"] is None
    assert metric["pre_period_baseline_close"] is None
    assert metric["unknown_change_days"] == 1
    assert metric["up_days"] == metric["latest_consecutive_up_days"] == len(closes) - 1
    assert metric["down_days"] == metric["unchanged_days"] == 0
    assert metric["daily_changes"][0] == {
        "date": "2024-06-03", "close": 110, "previous_date": None,
        "previous_close": None, "change_pct": None, "direction": "unknown",
    }


@pytest.mark.parametrize("closes, directions, counts, streak", [
    ([110, 121], ["up", "up"], (2, 0, 0), 2),
    ([110, 100], ["up", "down"], (1, 1, 0), 0),
    ([100, 100], ["unchanged", "unchanged"], (0, 0, 2), 0),
    ([90, 100, 100], ["down", "up", "unchanged"], (1, 1, 1), 0),
    ([110, 100, 101, 102], ["up", "down", "up", "up"], (3, 1, 0), 2),
    ([100, 101, 102], ["unchanged", "up", "up"], (2, 0, 1), 2),
    ([100.0000001], ["up"], (1, 0, 0), 1),
    ([99.9999999], ["down"], (0, 1, 0), 0),
])
def test_daily_counts_use_unrounded_closes_and_streak_resets(closes, directions, counts, streak):
    rows = [bar("2024-05-30", 99), bar("2024-05-31", 100)]
    rows += [bar(f"2024-06-{day:02d}", close) for day, close in enumerate(closes, start=3)]
    metric, = compute_market_metrics(snapshot(rows, end="2024-06-09"))
    assert [point["direction"] for point in metric["daily_changes"]] == directions
    assert tuple(metric[f"{direction}_days"] for direction in ("up", "down", "unchanged")) == counts
    assert metric["unknown_change_days"] == 0
    assert sum(counts) == metric["point_count"]
    assert metric["daily_changes"][0]["previous_date"] == "2024-05-31"
    assert metric["daily_changes"][-1]["change_pct"] == metric["latest_daily_change_pct"]
    assert metric["latest_consecutive_up_days"] == streak
    assert metric["latest_consecutive_up_start_date"] == (rows[-streak]["date"] if streak else None)
    assert metric["latest_consecutive_up_end_date"] == (rows[-1]["date"] if streak else None)


def test_missing_sessions_are_not_invented_or_presented_as_verified_continuity():
    metric, = compute_market_metrics(snapshot([
        bar("2024-05-31", 100), bar("2024-06-03", 101), bar("2024-06-07", 102),
    ], end="2024-06-09"))
    assert metric["latest_consecutive_up_days"] == metric["up_days"] == 2
    assert metric["session_continuity_verified"] is False
    assert "continuity unverified" in metric["daily_change_basis"]
    assert "missing sessions" in metric["latest_consecutive_up_days_basis"]
    assert metric["daily_changes"][1]["previous_date"] == "2024-06-03"


def ranking_fixture(prices):
    data = snapshot([])
    template = data["series"].pop()
    for symbol, baseline, last in prices:
        data["series"].append({**deepcopy(template), "symbol": symbol,
                               "bars": [bar("2024-05-31", baseline), bar("2024-06-03", last)]})
    return compute_market_metrics(data)


def test_ranking_is_return_based_with_deterministic_ties_and_no_mutation():
    metrics = ranking_fixture([("b", 100, 110), ("c", 10000, 10100), ("a", 50, 55)])
    original = deepcopy(metrics)
    result = rank_market_metrics(metrics)
    assert metrics == original
    assert result == rank_market_metrics(list(reversed(metrics)))
    assert [(row["symbol"], row["rank"]) for row in result["rankings"]] == [("a", 1), ("b", 1), ("c", 3)]
    assert result["best_symbols"] == ["a", "b"]
    assert result["worst_symbols"] == ["c"]
    assert result["status"] == "ok"
    assert result["comparison_window"] == {
        "requested_start_date": "2024-06-03", "requested_end_date": "2024-06-04",
        "start_date": "2024-05-31", "first_trade_date": "2024-06-03", "end_date": "2024-06-03",
    }
    assert result["rankings"][0]["provider"] == "Test fixture"
    assert result["rankings"][0]["unit"] == "%"
    assert result["rankings"][0]["source_url"] == "https://example.com/unit-test-only"
    json.dumps(result, allow_nan=False)


def test_ranking_does_not_create_ties_from_display_rounding_or_decimal_context():
    metrics = ranking_fixture([("a", 100, 100.0000001), ("b", 100, 100.0000002)])
    assert metrics[0]["change_pct"] == metrics[1]["change_pct"] == 0
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = True
        result = rank_market_metrics(metrics)
    assert result == rank_market_metrics(metrics)
    assert result["best_symbols"] == ["b"]
    assert result["worst_symbols"] == ["a"]
    assert [row["rank"] for row in result["rankings"]] == [1, 2]


def test_all_tied_or_single_index_are_both_best_and_worst():
    for prices in [[("b", 10, 9), ("a", 100, 90)], [("a", 100, 90)]]:
        result = rank_market_metrics(ranking_fixture(prices))
        assert result["best_symbols"] == result["worst_symbols"] == sorted(row[0] for row in prices)
        assert all(row["rank"] == 1 for row in result["rankings"])


def test_ranking_excludes_partial_returns_and_handles_no_eligible_series():
    partial, = compute_market_metrics(snapshot([bar("2024-06-03", 100), bar("2024-06-04", 200)]))
    full = ranking_fixture([("full", 100, 101)])
    result = rank_market_metrics([partial, *full])
    assert result["status"] == "partial"
    assert result["best_symbols"] == result["worst_symbols"] == ["full"]
    assert result["excluded"] == [{"symbol": "sh000001", "reason": "missing_pre_period_baseline_or_return"}]
    for metrics in ([], [partial]):
        result = rank_market_metrics(metrics)
        assert result["status"] == "unavailable"
        assert result["best_symbols"] == result["worst_symbols"] == result["rankings"] == []
        assert result["comparison_window"] is None


@pytest.mark.parametrize("field, value", [
    ("requested_start_date", "2024-06-01"), ("requested_end_date", "2024-06-05"),
    ("start_date", "2024-05-30"), ("end_date", "2024-06-04"),
    ("first_trade_date", "2024-06-04"),
])
def test_ranking_rejects_mismatched_windows(field, value):
    metrics = ranking_fixture([("a", 100, 110), ("b", 100, 120)])
    metrics[1][field] = value
    with pytest.raises(ValueError, match="different actual or requested"):
        rank_market_metrics(metrics)


@pytest.mark.parametrize("field, value, error", [
    ("symbol", "a", "duplicate"), ("unit", "points", "percentage"),
    ("start_close", 0, "finite positive"), ("end_close", float("inf"), "finite positive"),
    ("change_pct", 50, "does not match"), ("change_pct", float("nan"), "does not match"),
])
def test_ranking_rejects_duplicate_or_inconsistent_metrics(field, value, error):
    metrics = ranking_fixture([("a", 100, 110), ("b", 100, 120)])
    metrics[1][field] = value
    with pytest.raises(ValueError, match=error):
        rank_market_metrics(metrics)


@pytest.fixture
def recorded_september_snapshot():
    # Close-only extract from finance_source_probe.json, collected 2026-09-22T15:58:53Z.
    # Frozen regression evidence, NOT a live fetch. OHLC fields are synthetic close copies.
    dates = ["2026-08-31"] + [f"2026-09-{day:02d}" for day in
                             (1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21, 22)]
    closes = {
        "sh000001": [3986.30, 3979.89, 3941.39, 3942.09, 3930.12, 3932.70, 3940.55,
                     3951.51, 3934.40, 3888.11, 3885.33, 3864.28, 3891.60, 3875.60,
                     3911.87, 3949.91, 3952.13],
        "sh000300": [4625.09, 4611.44, 4547.96, 4552.58, 4548.05, 4575.02, 4558.74,
                     4572.60, 4548.39, 4510.16, 4480.08, 4450.04, 4480.27, 4460.16,
                     4507.39, 4539.57, 4544.59],
        "sz399006": [3438.68, 3393.43, 3312.24, 3312.54, 3286.55, 3398.68, 3359.72,
                     3354.97, 3338.42, 3322.04, 3285.58, 3247.92, 3311.47, 3298.31,
                     3372.68, 3399.59, 3399.93],
    }
    data = snapshot([], start="2026-09-01", end="2026-09-22")
    data.update(snapshot_id="recorded-close-regression-20260922", collected_at="2026-09-22T15:58:53Z")
    template = data["series"].pop()
    for symbol, values in closes.items():
        data["series"].append({
            **deepcopy(template), "symbol": symbol, "name": symbol,
            "provider": "Tencent Finance (recorded closes)",
            "retrieved_at": "2026-09-22T15:58:53Z",
            "source_url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
                          f"{symbol}%2Cday%2C2026-07-18%2C2026-09-22%2C640%2C",
            "bars": [bar(day, close) for day, close in zip(dates, values)],
        })
    return data


def test_recorded_september_17_decline_breaks_claimed_five_day_streak(recorded_september_snapshot):
    recorded_september_snapshot["period"]["start"] = "2026-09-16"
    metrics = compute_market_metrics(recorded_september_snapshot)
    assert len(metrics) == 3
    for metric in metrics:
        assert metric["point_count"] == 5
        assert (metric["up_days"], metric["down_days"], metric["unchanged_days"]) == (4, 1, 0)
        assert metric["unknown_change_days"] == 0
        assert [point["direction"] for point in metric["daily_changes"]] == ["up", "down", "up", "up", "up"]
        september_17 = metric["daily_changes"][1]
        assert september_17["date"] == "2026-09-17"
        assert september_17["previous_date"] == "2026-09-16"
        assert september_17["change_pct"] < 0
        assert metric["latest_consecutive_up_days"] == 3
        assert metric["latest_consecutive_up_start_date"] == "2026-09-18"
        assert metric["latest_consecutive_up_end_date"] == "2026-09-22"
        assert metric["max_drawdown_peak_date"] == "2026-09-16"
        assert metric["max_drawdown_trough_date"] == "2026-09-17"


def test_recorded_full_period_shanghai_is_best_not_chinext(recorded_september_snapshot):
    metrics = compute_market_metrics(recorded_september_snapshot)
    assert {metric["symbol"]: metric["change_pct"] for metric in metrics} == {
        "sh000001": -0.857186, "sh000300": -1.740507, "sz399006": -1.126886,
    }
    assert [(m["up_days"], m["down_days"], m["unchanged_days"]) for m in metrics] == [
        (8, 8, 0), (7, 9, 0), (6, 10, 0),
    ]
    result = rank_market_metrics(metrics)
    assert result["best_symbols"] == ["sh000001"]
    assert result["worst_symbols"] == ["sh000300"]
    assert [row["symbol"] for row in result["rankings"]] == ["sh000001", "sz399006", "sh000300"]
    assert result["comparison_window"]["start_date"] == "2026-08-31"
    assert all(metric["latest_consecutive_up_days"] == 3 for metric in metrics)


def test_recorded_ranking_is_window_specific_not_a_hardcoded_winner(recorded_september_snapshot):
    recorded_september_snapshot["period"]["start"] = "2026-09-16"
    result = rank_market_metrics(compute_market_metrics(recorded_september_snapshot))
    assert result["best_symbols"] == ["sz399006"]
    assert result["worst_symbols"] == ["sh000300"]
    assert result["comparison_window"]["start_date"] == "2026-09-15"
