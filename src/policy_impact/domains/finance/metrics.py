"""Deterministic price-index/spot returns. No network, models, or clock reads."""

from __future__ import annotations

from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from datetime import timedelta
from fractions import Fraction
import math

from .schemas import FinanceSnapshot, MarketBar, MarketSeries
from .sessions import (SessionCalendarUnavailable, completed_session_dates,
                       latest_completed_session, session_dates)


def _change(close: float, baseline: float) -> float:
    with localcontext(Context(prec=700, rounding=ROUND_HALF_UP)):
        result = (Decimal(str(close)) / Decimal(str(baseline)) - 1) * 100
        number = float(result.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
        if not math.isfinite(number):
            raise ValueError("percentage exceeds finite JSON number range")
        return number


def _period_statistics(within: list[MarketBar], baseline: MarketBar | None) -> dict:
    first, last = within[0], within[-1]
    peak = max(within, key=lambda bar: bar.close)
    trough = min(within, key=lambda bar: bar.close)
    running_peak = first
    drawdown_peak = drawdown_trough = None
    lowest_ratio = Fraction(1)
    daily_changes = []
    counts = dict(up=0, down=0, unchanged=0, unknown=0)
    previous = baseline
    consecutive_up = 0
    for bar in within:
        if bar.close > running_peak.close:
            running_peak = bar
        # Compare exact ratios before rounding; equal extrema retain the earliest dates.
        ratio = Fraction(str(bar.close)) / Fraction(str(running_peak.close))
        if ratio < lowest_ratio:
            lowest_ratio = ratio
            drawdown_peak, drawdown_trough = running_peak, bar
        direction = ("unknown" if previous is None else "up" if bar.close > previous.close
                     else "down" if bar.close < previous.close else "unchanged")
        counts[direction] += 1
        consecutive_up = consecutive_up + 1 if direction == "up" else 0
        daily_changes.append({
            "date": bar.date.isoformat(), "close": bar.close,
            "previous_date": previous.date.isoformat() if previous else None,
            "previous_close": previous.close if previous else None,
            "change_pct": _change(bar.close, previous.close) if previous else None,
            "direction": direction,
        })
        previous = bar
    return {
        "period_first_date": first.date.isoformat(), "period_first_close": first.close,
        "period_last_date": last.date.isoformat(), "period_last_close": last.close,
        "period_peak_date": peak.date.isoformat(), "period_peak_close": peak.close,
        "period_trough_date": trough.date.isoformat(), "period_trough_close": trough.close,
        "period_close_basis": "in-window observed closes only; excludes pre-period baseline "
                              "and intraday OHLC extremes; equal extrema use earliest date",
        "max_drawdown_pct": abs(_change(drawdown_trough.close, drawdown_peak.close))
                            if drawdown_peak else 0.0,
        "max_drawdown_peak_date": drawdown_peak.date.isoformat() if drawdown_peak else None,
        "max_drawdown_peak_close": drawdown_peak.close if drawdown_peak else None,
        "max_drawdown_trough_date": drawdown_trough.date.isoformat() if drawdown_trough else None,
        "max_drawdown_trough_close": drawdown_trough.close if drawdown_trough else None,
        "max_drawdown_basis": "max(1 - later close / earlier running peak close) * 100; "
                              "nonnegative loss magnitude; in-window closes only, baseline excluded; "
                              "equal losses use earliest trough then earliest peak; "
                              "zero with null endpoints when no observed decline",
        "pre_period_baseline_date": baseline.date.isoformat() if baseline else None,
        "pre_period_baseline_close": baseline.close if baseline else None,
        "min_change_from_pre_period_baseline_pct": _change(trough.close, baseline.close)
                                                   if baseline else None,
        "min_change_from_pre_period_baseline_date": trough.date.isoformat() if baseline else None,
        "min_change_from_pre_period_baseline_close": trough.close if baseline else None,
        "min_change_from_pre_period_baseline_basis": "min(in-window close / pre-period close - 1) "
                                                     "* 100; signed, may be positive; NOT drawdown; "
                                                     "unavailable without pre-period baseline",
        "up_days": counts["up"], "down_days": counts["down"],
        "unchanged_days": counts["unchanged"], "unknown_change_days": counts["unknown"],
        "daily_changes": daily_changes,
        "daily_change_basis": "in-window observations versus previous_known_close; first day uses "
                              "pre-period baseline when available, otherwise unknown; direction "
                              "uses unrounded closes; exchange-calendar continuity unverified",
        "latest_consecutive_up_days": consecutive_up,
        "latest_consecutive_up_start_date": within[-consecutive_up].date.isoformat()
                                            if consecutive_up else None,
        "latest_consecutive_up_end_date": last.date.isoformat() if consecutive_up else None,
        "latest_consecutive_up_days_basis": "trailing up observations within requested window only; "
                                            "down/unchanged/unknown breaks streak; no inference "
                                            "about earlier or missing sessions",
        "session_continuity_verified": False,
    }


def _price_basis(series: MarketSeries) -> bool:
    spot = series.price_basis in {"spot_price", "spot"}
    if not spot and series.price_basis != "price_index":
        raise ValueError("metrics require unadjusted price_index or spot_price, not CFD/futures/total return")
    if series.symbol in {"xauusd", "xagusd"} and not spot:
        raise ValueError("international spot metals cannot be replaced by a price index")
    if spot and (series.unit != "USD/troy_oz" or series.quote_currency not in {None, "USD"}):
        raise ValueError("international spot metal prices must be USD/troy_oz")
    if series.fx_conversion and series.quote_currency != series.fx_conversion.to_currency:
        raise ValueError("FX conversion target must match the recorded quote currency")
    return spot


def _daily_issues(snapshot: FinanceSnapshot, series: MarketSeries, baseline: MarketBar | None) -> list[str]:
    issues = []
    if not series.session_verified:
        issues.append("session is not verified")
    if series.session_date != series.expected_session_date or series.expected_session_date is None:
        issues.append("actual session differs from the expected completed session")
    if (series.expected_previous_session_date is None or baseline is None
            or baseline.date != series.expected_previous_session_date):
        issues.append("immediately preceding completed session baseline is missing")
    cutoff = snapshot.as_of or snapshot.collected_at
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        issues.append("daily as_of must be timezone-aware")
        return issues
    lag = series.session_evidence.get("publication_delay_minutes", 0)
    if type(lag) is not int or not 0 <= lag <= 1440:
        issues.append("invalid provider publication delay")
        return issues
    if series.session_close_at is None or series.session_close_at + timedelta(minutes=lag) > cutoff:
        issues.append("session is not closed/published by the cutoff")
    if series.calendar_id:
        try:
            expected = latest_completed_session(series.calendar_id, as_of=cutoff,
                                                publication_delay_minutes=lag)
            if expected["expected_session_date"] != str(series.session_date):
                issues.append("session does not match the exchange calendar at the cutoff")
            if expected["expected_previous_session_date"] != str(series.expected_previous_session_date):
                issues.append("previous session does not match the exchange calendar")
        except SessionCalendarUnavailable as exc:
            issues.append(str(exc))
    elif series.price_basis in {"spot", "spot_price"}:
        if not series.daily_cutoff_basis or not series.session_evidence:
            issues.append("OTC spot daily candle lacks provider cutoff evidence")
        reference_days = series.session_evidence.get("reference_days", {})
        reference_flags = [series.session_evidence]
        if isinstance(reference_days, dict):
            reference_flags.extend(reference_days.get(str(day), {}) for day in
                                   (series.session_date, series.expected_previous_session_date))
        if any(isinstance(flags, dict) and (flags.get("constant_reference_day") is True
               or flags.get("quote_carry_forward_possible") is True) for flags in reference_flags):
            issues.append("spot reference-day quotes may be carried forward; not verified fresh prices")
    else:
        issues.append("daily equity index has no calendar identity")
    return issues


def _coverage(series: MarketSeries, start, end, within: list[MarketBar]) -> dict:
    result = {"session_continuity_verified": False, "missing_session_dates": [],
              "unexpected_session_dates": [], "expected_session_count": None,
              "observed_session_count": len(within),
              "session_calendar_limitation": "no complete exchange calendar supplied; observed dates only"}
    if not series.calendar_id:
        return result
    try:
        expected = set(session_dates(series.calendar_id, start, end))
    except SessionCalendarUnavailable as exc:
        result["session_calendar_limitation"] = str(exc)
        return result
    observed = {bar.date for bar in within}
    result.update(session_continuity_verified=expected == observed,
                  missing_session_dates=sorted(str(day) for day in expected - observed),
                  unexpected_session_dates=sorted(str(day) for day in observed - expected),
                  expected_session_count=len(expected),
                  session_calendar_limitation="checked against a maintained schedule, not a guarantee against unannounced closures")
    return result


def compute_market_metrics(snapshot: dict) -> list[dict]:
    """Calculate inclusive-window returns from the closest known close BEFORE start.

    Without that close, report the actual first-to-last observation interval explicitly.
    A single observation without a baseline is unavailable, never a fabricated 0% return.
    start_date/end_date are the actual close dates used, not the requested boundaries.
    """
    validated = FinanceSnapshot.model_validate(snapshot)
    metrics, symbols = [], set()
    for series in validated.series:
        spot = _price_basis(series)
        completion_issues = []
        cutoff = validated.as_of or validated.collected_at
        lag = series.session_evidence.get("publication_delay_minutes", 20)
        if validated.session_mode == "latest_completed":
            if series.session_date is None:
                raise ValueError("latest_completed metrics require the actual per-market session_date")
            start = end = series.session_date
        else:
            start, end = validated.period.start, validated.period.end
            if series.calendar_id:
                try:
                    completed = completed_session_dates(
                        series.calendar_id, start, end, as_of=cutoff,
                        publication_delay_minutes=lag)
                    end = completed[-1] if completed else start - timedelta(days=1)
                except (SessionCalendarUnavailable, ValueError) as exc:
                    completion_issues.append(f"completed exchange coverage cannot be verified: {exc}")
        if series.symbol in symbols:
            raise ValueError(f"duplicate market series for {series.symbol}; cannot mix providers")
        symbols.add(series.symbol)
        by_date: dict = {}
        for bar in series.bars:
            if bar.date in by_date and by_date[bar.date] != bar:
                raise ValueError(f"conflicting duplicate bar for {series.symbol} on {bar.date}")
            by_date[bar.date] = bar
        bars: list[MarketBar] = [by_date[day] for day in sorted(by_date) if day <= end]
        before = [bar for bar in bars if bar.date < start]
        within = [bar for bar in bars if start <= bar.date <= end]
        if not within:
            if validated.quality_policy == "strict":
                raise ValueError(f"strict quality: {series.symbol} has no observations in the requested window")
            continue
        has_baseline = bool(before)
        baseline, last = before[-1] if before else within[0], within[-1]
        observations = ([baseline] if has_baseline else []) + within
        previous = observations[-2] if len(observations) > 1 else None
        calculable = baseline.date < last.date
        coverage = _coverage(series, start, end, within)
        issues = completion_issues + ([] if has_baseline else ["pre-period baseline is missing"])
        if series.calendar_id and coverage["expected_session_count"] is None:
            issues.append("exchange session coverage cannot be verified")
        if coverage["missing_session_dates"] or coverage["unexpected_session_dates"]:
            issues.append("observed dates do not cover the exchange sessions exactly")
        if validated.session_mode == "latest_completed":
            issues.extend(_daily_issues(validated, series, before[-1] if has_baseline else None))
        if validated.quality_policy == "strict" and issues:
            raise ValueError(f"strict quality: {series.symbol}: {'; '.join(issues)}")
        price_label = "spot_price_close" if spot else "price_index_close"
        basis = (
            f"{price_label}: ({last.date} close / {baseline.date} close - 1) * 100; "
            + ("baseline is the nearest known close strictly before requested start; "
               "includes the first in-period trading day's move. " if has_baseline else
               "MISSING pre-period baseline: actual first-observed-close to last-observed-close "
               "interval only; the first in-period day's change is unavailable, NOT a full "
               "requested-period return. ")
            + (f"Observed session labels checked against {series.calendar_id}. "
               if coverage["session_continuity_verified"] else
               "Observed sessions only; exchange-calendar continuity is unverified. ")
            + ("Spot price return in the recorded quote unit; no implied FX conversion or trading P&L. "
               if spot else "Price-index return, not total return; ")
            + "No annualization."
        )
        if not calculable:
            basis += " Only one close and no earlier baseline; change is unavailable."
        if spot:
            basis = basis.replace("trading day's", "provider reference day's")
            basis += " Provider reference-day prices, not a universal official spot-market close."
        points = [{"date": bar.date.isoformat(), "close": bar.close,
                   "is_baseline": has_baseline and bar.date == baseline.date,
                   "change_pct": _change(bar.close, baseline.close) if calculable else None}
                  for bar in observations]
        if series.asset_class or series.session_date is not None:
            for point in points:
                point.update(price_unit=series.unit, price_basis=series.price_basis,
                             quote_currency=series.quote_currency, timezone=series.timezone)
        metadata = series.model_dump(mode="json", include={
            "asset_class", "quote_currency", "timezone", "price_basis", "calendar_id",
            "session_date", "expected_session_date", "expected_previous_session_date",
            "session_close_at", "session_date_basis", "session_verified", "session_evidence",
            "daily_cutoff_basis", "fx_conversion",
        })
        metric = {
            "metric_id": f"market_change_pct:{series.symbol}",
            "symbol": series.symbol, "name": series.name,
            "start_date": baseline.date.isoformat(), "end_date": last.date.isoformat(),
            "start_close": baseline.close, "end_close": last.close,
            "change_pct": _change(last.close, baseline.close) if calculable else None,
            "unit": "%", "price_unit": series.unit,
            "source_url": series.source_url, "provider": series.provider, "basis": basis,
            "points": points,
            "latest_daily_change_pct": _change(last.close, previous.close) if previous else None,
            "latest_trade_date": last.date.isoformat(),
            "latest_daily_baseline_date": previous.date.isoformat() if previous else None,
            "latest_daily_change_basis": "previous_known_close; session continuity unverified",
            "requested_start_date": validated.period.start.isoformat(),
            "requested_end_date": validated.period.end.isoformat(),
            "effective_start_date": start.isoformat(), "effective_end_date": end.isoformat(),
            "coverage_as_of": cutoff.isoformat(),
            "coverage_end_basis": ("exchange_close_plus_publication_delay" if series.calendar_id
                                   else "provider_reference_day"),
            "coverage_publication_delay_minutes": lag,
            "excluded_uncompleted_session_dates": sorted(
                str(day) for day in by_date if end < day <= validated.period.end),
            "first_trade_date": within[0].date.isoformat(),
            "baseline_available": has_baseline,
            "coverage": "pre_period_baseline_available" if has_baseline else "missing_pre_period_baseline",
            "status": "ok" if not issues else ("partial" if calculable else "insufficient_data"),
            "quality_issues": list(dict.fromkeys(issues)),
            "end_gap_calendar_days": (end - last.date).days,
            "point_count": len(within),
            **_period_statistics(within, before[-1] if has_baseline else None),
            **coverage, **metadata, "session_mode": validated.session_mode,
            "day_basis": "provider_reference_day" if spot else "exchange_session",
        }
        if coverage["session_continuity_verified"]:
            metric["daily_change_basis"] = metric["daily_change_basis"].replace(
                "exchange-calendar continuity unverified", f"session labels checked against {series.calendar_id}")
        if validated.session_mode == "latest_completed":
            metric["session_baseline_available"] = bool(
                has_baseline and baseline.date == series.expected_previous_session_date)
            if not metric["session_baseline_available"]:
                metric["latest_daily_change_pct"] = None
                metric["latest_daily_change_basis"] = "unavailable: preceding completed session is missing"
        metrics.append(metric)
    return metrics


def rank_market_metrics(metrics: list[dict]) -> dict:
    """Rank computed metrics by exact close ratio, descending, for a common window.

    Missing pre-period baselines are explicitly excluded, never ranked by their partial
    returns. Different actual/requested windows raise ValueError instead of comparing
    unlike periods. Ties share competition rank (1, 1, 3), ordered by symbol; best/worst
    include ALL tied symbols. Winners apply only to the supplied eligible series.
    """
    window_keys = ("requested_start_date", "requested_end_date", "start_date",
                   "first_trade_date", "end_date")
    if (len(metrics) > 1 and all(metric.get("session_mode") == "latest_completed" for metric in metrics)
            and len({tuple(metric[key] for key in window_keys) for metric in metrics}) > 1):
        return {
            "status": "unavailable", "rankings": [], "best_symbols": [], "worst_symbols": [],
            "excluded": [{"symbol": metric["symbol"], "reason": "different_completed_session_windows"}
                         for metric in sorted(metrics, key=lambda metric: metric["symbol"])],
            "comparison_window": None,
            "basis": "Each market has its own completed session/reference day; no cross-date winner is inferred.",
        }
    eligible, excluded, symbols = [], [], set()
    window = None
    for metric in metrics:
        symbol = metric["symbol"]
        if symbol in symbols:
            raise ValueError(f"duplicate market metric for {symbol}")
        symbols.add(symbol)
        if not metric["baseline_available"] or metric["change_pct"] is None:
            excluded.append({"symbol": symbol, "reason": "missing_pre_period_baseline_or_return"})
            continue
        prices = (metric["start_close"], metric["end_close"])
        if metric["unit"] != "%" or any(not math.isfinite(p) or p <= 0 for p in prices):
            raise ValueError("ranking requires finite positive closes and percentage returns")
        current_window = tuple(metric[key] for key in window_keys)
        if window is not None and current_window != window:
            raise ValueError("cannot rank different actual or requested observation windows")
        window = current_window
        change = _change(metric["end_close"], metric["start_close"])
        if change != metric["change_pct"]:
            raise ValueError(f"return does not match close prices for {symbol}")
        ratio = Fraction(str(metric["end_close"])) / Fraction(str(metric["start_close"]))
        eligible.append((ratio, metric))
    eligible.sort(key=lambda row: (-row[0], row[1]["symbol"]))
    rankings = []
    previous_ratio, rank = None, 0
    for position, (ratio, metric) in enumerate(eligible, start=1):
        if ratio != previous_ratio:
            rank = position
        rankings.append({
            "rank": rank, **{key: metric[key] for key in
                             ("symbol", "name", "change_pct", "unit", "provider", "source_url")},
        })
        previous_ratio = ratio
    return {
        "status": ("partial" if excluded else "ok") if eligible else "unavailable",
        "rankings": rankings,
        "best_symbols": [m["symbol"] for ratio, m in eligible if ratio == eligible[0][0]],
        "worst_symbols": [m["symbol"] for ratio, m in eligible if ratio == eligible[-1][0]],
        "excluded": sorted(excluded, key=lambda item: item["symbol"]),
        "comparison_window": dict(zip(window_keys, window)) if window else None,
        "basis": "price-index percentage return from pre-period baseline to last in-window close; "
                 "exact unrounded close ratios, descending; competition ranks; tied symbols sorted; "
                 "best/worst apply only to supplied eligible series; not a market-wide ranking; "
                 "exchange-calendar continuity unverified",
    }
