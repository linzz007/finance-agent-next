"""Credential-free, bounded acquisition of public price indices and official articles."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, time as daytime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
import time
from urllib.parse import urldefrag, urlencode, urljoin, urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from policy_impact.source_connectors.article import ArticleParser, _text, extract_article
from policy_impact.source_connectors.http_client import FetchResponse, domain_allowed, fetch_url
from policy_impact.harness.research_budget import CURRENT

from .schemas import FinanceSnapshot, MarketBar, MarketSeries, Period, SourceDocument, SourceStatus
from .market_catalog import get_symbol_metadata
from .global_market_sources import market_urls, parse_daily
from . import bullionvault_source
from .sessions import latest_completed_session, session_dates, SessionCalendarUnavailable

CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "finance_sources.json"
SHANGHAI = timezone(timedelta(hours=8))


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class _Budget:
    def __init__(self, limits: dict):
        self.timeout = min(8.0, max(0.1, float(limits["timeout_seconds"])))
        self.max_bytes = min(2_000_000, max(1024, int(limits["max_bytes"])))
        self.remaining = min(72, max(1, int(limits["max_requests"])))
        self.deadline = time.monotonic() + min(90, max(1, limits["collection_budget_seconds"]))
        scope = CURRENT.get()
        if scope is not None:
            self.deadline = min(self.deadline, scope.deadline - scope.reserve_seconds)
        self.lock = Lock()

    def reserve(self) -> float:
        with self.lock:
            remaining_time = self.deadline - time.monotonic()
            if self.remaining <= 0 or remaining_time <= 0:
                raise TimeoutError("collection request/time budget exhausted")
            self.remaining -= 1
        return min(self.timeout, remaining_time)

    def fetch(self, url: str, source: dict) -> FetchResponse:
        timeout = self.reserve()
        response = fetch_url(url, allowed_domains=source["allowed_domains"],
                             timeout_seconds=timeout, retries=0,
                             max_bytes=self.max_bytes)
        if response.status != 200:
            raise ValueError(f"unexpected HTTP status {response.status}")
        return response


def _decode(response: FetchResponse) -> str:
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", response.content_type, re.I)
    encodings = [match.group(1)] if match else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = response.body.decode(encoding)
            if "\ufffd" in text:
                raise ValueError("response contains replacement characters")
            return text
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError("response cannot be decoded without data loss")


def _dedupe_bars(bars: list[MarketBar]) -> list[MarketBar]:
    by_date: dict[date, MarketBar] = {}
    for bar in bars:
        if bar.date in by_date and by_date[bar.date] != bar:
            raise ValueError(f"conflicting duplicate bar for {bar.date}")
        by_date[bar.date] = bar
    return [by_date[day] for day in sorted(by_date)]


def _parse_market(response: FetchResponse, source: dict, symbol: str) -> list[MarketBar]:
    payload = json.loads(_decode(response))
    if source["adapter"] == "tencent":
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("Tencent response is not a successful JSON payload")
        data = payload.get("data", {}).get(symbol)
        if not isinstance(data, dict):
            raise ValueError("Tencent response has no requested symbol")
        # Adjusted qfqday/hfqday must not be passed off as unadjusted index prices.
        rows = data.get("day")
        if rows is None:
            if data.get("qfqday") or data.get("hfqday"):
                raise ValueError("adjusted bars returned for an unadjusted index request")
            rows = []
        if not isinstance(rows, list):
            raise ValueError("Tencent day field is not a list")
        bars = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                raise ValueError("malformed Tencent OHLC row")
            bars.append(MarketBar(date=row[0], open=row[1], close=row[2], high=row[3],
                                  low=row[4], volume=row[5] if len(row) > 5 else None))
    elif source["adapter"] == "sina":
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise ValueError("Sina response is not a daily-bar array")
        bars = [MarketBar(date=row["day"], open=row["open"], close=row["close"],
                          high=row["high"], low=row["low"], volume=row.get("volume"))
                for row in payload]
    else:
        raise ValueError("unknown market adapter")
    return _dedupe_bars(bars)


def _market_urls(source: dict, symbol: str, start: date, end: date) -> list[str]:
    if source["adapter"] == "sina":
        return [source["url"] + "?" + urlencode({"symbol": symbol, "scale": 240,
                                                   "ma": "no", "datalen": 1023})]
    if not source["date_range"]:
        ranges = [(start, end)]
    else:
        ranges = []
        cursor = start
        while cursor <= end:
            stop = min(end, cursor + timedelta(days=365))
            ranges.append((cursor, stop))
            if stop == end:
                break
            cursor = stop + timedelta(days=1)
    return [source["url"] + "?" + urlencode({
        "param": f"{symbol},day,{left},{right},640,"
    }) for left, right in ranges]


def _collect_market(symbol: str, config: dict, period: Period, now: datetime,
                    budget: _Budget) -> tuple[MarketSeries | None, list[SourceStatus], list[str]]:
    lookback = min(45, max(1, config["limits"]["baseline_lookback_days"]))
    query_start = max(date(1990, 1, 1), period.start - timedelta(days=lookback))
    local_now = now.astimezone(SHANGHAI)
    closed_through = local_now.date()
    if local_now.time() < daytime(15, 10):
        closed_through -= timedelta(days=1)
    query_end = min(period.end, closed_through)
    statuses: list[SourceStatus] = []
    warnings: list[str] = []
    best: MarketSeries | None = None
    best_rank = (-1, -1, date.min, -1)
    if query_end < query_start:
        return None, [SourceStatus(source_id=f"market:{symbol}", status="empty", count=0,
                                   detail="Requested window has no finalized historical dates.")], []
    for source in config["market_sources"]:
        started = time.monotonic()
        rows: list[MarketBar] = []
        errors: list[str] = []
        requests = []
        retrieved_at = now
        source_url = ""
        for url in _market_urls(source, symbol, query_start, query_end):
            try:
                response = budget.fetch(url, source)
                batch = _parse_market(response, source, symbol)
                rows.extend(bar for bar in batch if query_start <= bar.date <= query_end)
                retrieved_at = datetime.fromisoformat(response.retrieved_at)
                source_url = source_url or response.url
                requests.append({"url": response.url, "sha256": response.sha256,
                                 "raw_count": len(batch)})
            except Exception as exc:  # source isolation; validation errors are never synthetic data
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:240]}")
        try:
            rows = _dedupe_bars(rows)
        except ValueError as exc:
            errors.append(str(exc))
            rows = []
        before = [bar for bar in rows if bar.date < period.start]
        within = [bar for bar in rows if period.start <= bar.date <= period.end]
        retained = before[-1:] + within
        stale = bool(within and (query_end - within[-1].date).days > 7)
        problems = list(errors)
        if not before:
            problems.append(f"No pre-period close in the bounded {lookback}-day lookup.")
        if stale:
            problems.append("Latest close is over seven calendar days behind requested end.")
        state = ("partial" if problems else "ok") if within else ("error" if errors else "empty")
        statuses.append(SourceStatus(
            source_id=f"{source['source_id']}:{symbol}", status=state, count=len(retained),
            latest_date=retained[-1].date.isoformat() if retained else "",
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            detail=json.dumps({"quality": source["quality"], "requests": requests,
                               "issues": problems, "coverage": "exchange calendar not verified"},
                              ensure_ascii=False),
        ))
        rank = (bool(within), bool(before), within[-1].date if within else date.min, len(within))
        if retained and rank > best_rank:
            best_rank = rank
            best = MarketSeries(symbol=symbol, name=config["symbols"][symbol],
                                provider=source["provider"], unit="\u70b9", price_basis="price_index",
                                source_url=source_url, retrieved_at=retrieved_at, bars=retained)
        if within and before and not problems:
            break
    if best is None or not any(bar.date >= period.start for bar in best.bars):
        warnings.append(f"{symbol}\uff1a\u5df2\u5c1d\u8bd5\u6240\u6709\u914d\u7f6e\u884c\u60c5\u6765\u6e90\uff0c\u4ecd\u672a\u53d6\u5f97\u8bf7\u6c42\u533a\u95f4\u5185\u7684\u6709\u6548\u65e5\u7ebf\u3002")
    if best and not any(bar.date < period.start for bar in best.bars):
        warnings.append(f"{symbol}\uff1a\u7f3a\u5c11\u533a\u95f4\u5f00\u59cb\u65e5\u524d\u7684\u6536\u76d8\u57fa\u51c6\uff0c\u4e0d\u80fd\u8ba1\u7b97\u5b8c\u6574\u8bf7\u6c42\u533a\u95f4\u7684\u6da8\u8dcc\u5e45\u3002")
    if len(statuses) > 1:
        warnings.append(f"{symbol}\uff1a\u5df2\u5c1d\u8bd5\u5907\u7528\u884c\u60c5\u6765\u6e90\uff1b\u4e0d\u540c\u4f9b\u5e94\u5546\u7684\u4ef7\u683c\u672a\u62fc\u63a5\u4f7f\u7528\u3002")
    return best, statuses, warnings


def _session_plan(metadata: dict, period: Period, now: datetime, mode: str) -> dict:
    cutoff = now
    if mode == "explicit_period":
        period_cutoff = datetime.combine(period.end + timedelta(days=1), daytime(),
                                         ZoneInfo(metadata["timezone"]))
        cutoff = min(cutoff, period_cutoff)
    plan = latest_completed_session(metadata["calendar"], as_of=cutoff)
    plan["session_evidence"]["calendar_scope"] = (
        "XNYS regular-session calendar shared by SPX/DJI/Nasdaq Composite; not index publisher certification"
        if metadata["calendar"] == "XNYS" else "XSHG regular-session calendar")
    return plan


def _market_window_start(period: Period, expected: date, mode: str) -> date:
    # A single-date latest request means the last completed session; a
    # multi-date request retains its requested lookback window.
    return expected if mode == "latest_completed" and period.start >= period.end else period.start


def _collect_index(symbol: str, config: dict, period: Period, now: datetime,
                   budget: _Budget, mode: str):
    metadata = get_symbol_metadata(symbol)
    try:
        plan = _session_plan(metadata, period, now, mode)
    except SessionCalendarUnavailable as exc:
        return None, [SourceStatus(source_id=f"calendar:{symbol}", status="error", count=0,
                                  detail=str(exc))], [f"{symbol}\uff1a\u4ea4\u6613\u65e5\u5386\u4e0d\u53ef\u7528\uff0c\u672a\u7528\u5de5\u4f5c\u65e5\u4ee3\u66ff\u4ea4\u6613\u65e5\u3002"]
    expected = date.fromisoformat(str(plan["expected_session_date"]))
    previous = date.fromisoformat(str(plan["expected_previous_session_date"]))
    left = _market_window_start(period, expected, mode)
    query_start = max(date(1990, 1, 1), left - timedelta(days=45))
    if expected < left:
        return None, [SourceStatus(source_id=f"calendar:{symbol}", status="empty", count=0,
                                  detail="No completed market session in requested period")], []
    try:
        required = session_dates(metadata["calendar"], left, expected)
        pre_dates = session_dates(metadata["calendar"], query_start, left - timedelta(days=1))
    except SessionCalendarUnavailable as exc:
        return None, [SourceStatus(source_id=f"calendar:{symbol}", status="error", count=0,
                                  detail=str(exc))], [f"{symbol}\uff1a\u8bf7\u6c42\u8303\u56f4\u8d85\u51fa\u5df2\u9a8c\u8bc1\u4ea4\u6613\u65e5\u5386\uff0c\u672a\u63a8\u6d4b\u7f3a\u5931\u65e5\u671f\u3002"]
    baseline_day = pre_dates[-1] if pre_dates else None
    mainland = symbol.startswith(("sh", "sz"))
    sources = config["market_sources" if mainland else "global_market_sources"]
    best, best_rank, statuses = None, (-1, -1, date.min, -1), []
    for source in sources:
        started = time.monotonic()
        rows, requests, errors, provenance = [], [], [], []
        retrieved_at, source_url = now, source["url"]
        urls = (_market_urls if mainland else market_urls)(source, symbol, query_start, expected)
        for url in urls:
            for attempt in range(min(2, max(1, source.get("max_attempts", 1)))):
                try:
                    response = budget.fetch(url, source)
                    if mainland:
                        batch, evidence = _parse_market(response, source, symbol), {}
                    else:
                        batch, evidence = parse_daily(_decode(response), source, symbol, query_start, expected)
                    rows.extend(bar for bar in batch if query_start <= bar.date <= expected)
                    requests.append({"url": response.url, "sha256": response.sha256,
                                     "attempt": attempt + 1, "raw_count": len(batch)})
                    provenance.append(evidence)
                    retrieved_at, source_url = response.retrieved_at, response.url
                    break
                except Exception as exc:
                    errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {str(exc)[:240]}")
        try:
            rows = _dedupe_bars(rows)
        except ValueError as exc:
            errors.append(str(exc))
            rows = []
        allowed_dates = set(pre_dates) | set(required)
        unexpected_dates = sorted({b.date for b in rows} - allowed_dates)
        rows = [bar for bar in rows if bar.date in allowed_dates]
        before, within = [b for b in rows if b.date < left], [b for b in rows if left <= b.date <= expected]
        retained = before[-1:] + within
        dates = {b.date for b in retained}
        missing = sorted(set(required) - dates)
        issues = []
        if unexpected_dates:
            issues.append("Provider bars on non-session dates: " + ",".join(map(str, unexpected_dates)))
        if missing:
            issues.append("Missing calendar sessions: " + ",".join(map(str, missing)))
        if baseline_day not in dates:
            issues.append("Expected pre-period baseline missing")
        if previous not in dates:
            issues.append("Expected previous completed session missing")
        if len(requests) < len(urls):
            issues.append("One or more requested history chunks failed")
        verified = bool(within and not issues)
        evidence = {**plan["session_evidence"], "source_quality": source["quality"],
                    "provider_provenance": provenance, "requests": requests,
                    "unexpected_session_dates": list(map(str, unexpected_dates)),
                    "missing_session_dates": list(map(str, missing)),
                    "expected_baseline_date": str(baseline_day) if baseline_day else None}
        actual = within[-1].date if within else (retained[-1].date if retained else None)
        state = "ok" if verified else ("partial" if retained else ("error" if errors else "empty"))
        statuses.append(SourceStatus(source_id=f"{source['source_id']}:{symbol}", status=state,
                        count=len(retained), latest_date=str(actual or ""),
                        elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                        detail=json.dumps({"quality": source["quality"], "requests": requests,
                            "attempt_errors": errors, "issues": issues, "timezone": metadata["timezone"],
                            "session_verified": verified, "expected_session_date": str(expected),
                            "session_evidence": evidence}, ensure_ascii=False)))
        rank = (verified, bool(within), actual or date.min, len(retained))
        if retained and rank > best_rank:
            best_rank = rank
            best = MarketSeries(symbol=symbol, name=metadata["name"], provider=source["provider"],
                unit=metadata["unit"], timezone=metadata["timezone"], price_basis=metadata["price_basis"],
                asset_class=metadata["asset_class"], quote_currency=metadata["quote_currency"],
                source_url=source_url, retrieved_at=retrieved_at, bars=retained,
                calendar_id=plan["calendar_id"], session_date=actual, expected_session_date=expected,
                expected_previous_session_date=previous,
                session_close_at=plan["session_close_at"] if actual == expected else None,
                session_date_basis=plan["session_date_basis"], session_verified=verified,
                session_evidence=evidence, daily_cutoff_basis="Exchange regular session close plus 20-minute publication buffer")
        if verified:
            break
    warnings = []
    if not best or not best.session_verified:
        warnings.append(f"{symbol}\uff1a\u672a\u53d6\u5f97\u5b8c\u6574\u4e14\u7ecf\u65e5\u5386\u6838\u9a8c\u7684\u76ee\u6807\u65e5\u7ebf\u4e0e\u57fa\u51c6\uff1b\u7f3a\u5931\u6570\u636e\u672a\u8865\u9020\u3002")
    if len(statuses) > 1:
        warnings.append(f"{symbol}\uff1a\u5df2\u5c1d\u8bd5\u5907\u7528\u6765\u6e90\uff0c\u6574\u6bb5\u9009\u53d6\u540c\u4e00\u4f9b\u5e94\u5546\uff0c\u672a\u62fc\u63a5\u4ef7\u683c\u3002")
    return best, statuses, warnings


def _collect_spot(symbol: str, config: dict, period: Period, now: datetime,
                  budget: _Budget, mode: str):
    metadata = get_symbol_metadata(symbol)
    latest = (now.astimezone(timezone.utc) - timedelta(minutes=20)).date() - timedelta(days=1)
    expected = latest if mode == "latest_completed" else min(period.end, latest)
    previous = expected - timedelta(days=1)
    left = _market_window_start(period, expected, mode)
    start = left - timedelta(days=45)
    interval_seconds = bullionvault_source.history_interval(left, latest)
    statuses, best = [], None
    for source in config.get("spot_market_sources", []):
        started = time.monotonic()
        errors, result = [], None
        for attempt in range(min(2, max(1, source.get("max_attempts", 1)))):
            try:
                result = bullionvault_source.fetch_history(symbol, timeout_seconds=budget.reserve(),
                                                           max_bytes=budget.max_bytes,
                                                           interval_seconds=interval_seconds)
                rows, evidence = bullionvault_source.reference_days(result["payload"], symbol,
                                                    as_of=now, start=start, end=expected,
                                                    interval_seconds=interval_seconds)
                break
            except Exception as exc:
                result = None
                errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {str(exc)[:240]}")
        if result is None:
            statuses.append(SourceStatus(source_id=f"{source['source_id']}:{symbol}", status="error", count=0,
                detail=json.dumps({"quality": source["quality"], "attempt_errors": errors})))
            continue
        before, within = [b for b in rows if b.date < left], [b for b in rows if left <= b.date <= expected]
        retained = before[-1:] + within
        dates = {b.date for b in retained}
        required = {left - timedelta(days=1)} | {
            left + timedelta(days=i) for i in range(max(0, (expected - left).days + 1))}
        missing = sorted(required - dates)
        actual = within[-1].date if within else (retained[-1].date if retained else None)
        actual_evidence = evidence["reference_days"].get(str(actual), {})
        constant_days = [day for day, value in evidence["reference_days"].items()
                         if date.fromisoformat(day) in dates and value["constant_reference_day"]]
        verified = bool(within and not missing and expected in dates and previous in dates)
        evidence.update(publication_delay_minutes=20, provider_metadata={key: result["payload"].get(key)
                        for key in ("name", "securityId", "valuationSecurityId", "interval", "batch")},
                        source_quality=source["quality"], payload_sha256=result["sha256"],
                        constant_reference_day=actual_evidence.get("constant_reference_day", False),
                        quote_carry_forward_possible=bool(constant_days),
                        constant_reference_dates=constant_days, missing_reference_dates=list(map(str, missing)),
                        expected_date_rule="previous completed UTC natural day, NOT an inferred trading day; no weekend rollback",
                        **{key: actual_evidence[key] for key in ("last_bucket_start_at", "last_observation_time_exact")
                           if key in actual_evidence})
        issues = (["Missing complete UTC reference days: " + ",".join(map(str, missing))] if missing else [])
        if constant_days:
            issues.append("Constant reference day: quote carry-forward possible; not proof of a new trading session")
        state = "ok" if verified and not issues else ("partial" if retained else "empty")
        statuses.append(SourceStatus(source_id=f"{source['source_id']}:{symbol}", status=state,
            count=len(retained), latest_date=str(actual or ""),
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            detail=json.dumps({"quality": source["quality"], "attempt_errors": errors, "issues": issues,
                "transport": result["transport"], "transport_url": result["transport_url"],
                "sha256": result["sha256"], "bytes": result["bytes"], "session_evidence": evidence,
                "timezone": "UTC", "expected_session_date": str(expected), "session_verified": verified}, ensure_ascii=False)))
        if retained:
            best = MarketSeries(symbol=symbol, name=metadata["name"], provider=source["provider"],
                unit="USD/troy_oz", timezone="UTC", price_basis="spot_price", asset_class="spot_metal",
                quote_currency="USD", source_url=result["url"], retrieved_at=result["retrieved_at"], bars=retained,
                calendar_id=None, session_date=actual, expected_session_date=expected,
                expected_previous_session_date=previous,
                session_close_at=datetime.combine(expected + timedelta(days=1), daytime(), timezone.utc)
                    if actual == expected else None,
                session_date_basis="bullionvault_utc_reference_day", session_verified=verified,
                session_evidence=evidence, daily_cutoff_basis=evidence["daily_cutoff_basis"])
            break
    # Uncertified candidates are auditable but NEVER admitted as an alternative instrument.
    if not best:
        for source in config.get("spot_candidates", []):
            errors, requests = [], []
            for url in market_urls(source, symbol, start, expected):
                try:
                    response = budget.fetch(url, source)
                    requests.append({"url": response.url, "sha256": response.sha256,
                                     "content_type": response.content_type})
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {str(exc)[:240]}")
            statuses.append(SourceStatus(source_id=f"{source['source_id']}:{symbol}",
                status="error" if errors else "disabled", count=0,
                detail=json.dumps({"quality": source["quality"], "requests": requests, "issues": errors,
                                   "admitted": False, "reason": "uncertified requested spot/daily identity"})))
    warnings = [f"{symbol}\uff1aUTC\u5b8c\u6574\u91c7\u6837\u65e5\u672b\u53c2\u8003\u4ef7\uff0c\u975e\u5b98\u65b9\u6536\u76d8\u6216\u7cbe\u786e\u5348\u591c\u6210\u4ea4\u4ef7\uff1b\u65e0\u5f00\u76d8\u4ef7\uff0c{86400 // interval_seconds}\u4e2a{interval_seconds // 3600}\u5c0f\u65f6\u91c7\u6837\u6876\u5fc5\u987b\u5b8c\u6574\u3002"]
    if not best or not best.session_verified:
        warnings.append(f"{symbol}\uff1a\u5b8c\u6574\u53c2\u8003\u65e5\u6216\u671f\u524d\u57fa\u51c6\u7f3a\u5931\uff0c\u672a\u7528\u671f\u8d27\u3001ETF\u3001CFD\u6216\u65e7\u503c\u8865\u9f50\u3002")
    if best and best.session_evidence["quote_carry_forward_possible"]:
        warnings.append(f"{symbol}\uff1a\u5b58\u5728\u5168\u5929\u9759\u6b62\u62a5\u4ef7\uff0c\u53ef\u80fd\u6cbf\u7528\u4f11\u5e02\u524d\u62a5\u4ef7\uff0c\u4e0d\u80fd\u4f5c\u4e3a\u65b0\u4ea4\u6613\u65e5\u6da8\u8dcc\u8bc1\u636e\u3002")
    return best, statuses, warnings


def _collect_instrument(symbol, config, period, now, budget, mode):
    if symbol in {"xauusd", "xagusd"}:
        return _collect_spot(symbol, config, period, now, budget, mode)
    if symbol in {"spx", "ixic", "dji"} or mode == "latest_completed":
        return _collect_index(symbol, config, period, now, budget, mode)
    series, statuses, warnings = _collect_market(symbol, config, period, now, budget)
    if series:
        metadata = get_symbol_metadata(symbol)
        series.asset_class, series.quote_currency = metadata["asset_class"], metadata["quote_currency"]
        series.calendar_id = metadata["calendar"]
        series.session_date = series.bars[-1].date
        series.session_evidence = {"calendar_verified": False, "scope": "legacy explicit-period acquisition"}
    return series, statuses, warnings


def _parse_date(value: str) -> date | None:
    value = value.strip()
    match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})(?:$|[T\s])", value)
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in ("%B %d, %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _metadata(parser: ArticleParser) -> dict[str, str]:
    return {str(n.attrs.get("name") or n.attrs.get("property") or "").lower():
            str(n.attrs.get("content") or "") for n in parser.nodes if n.tag == "meta"}


def _listing_candidates(response: FetchResponse, source: dict) -> list[dict]:
    text = _decode(response)
    candidates = []
    if source["adapter"] in {"fed", "bis"} and (
        "xml" in response.content_type or urlparse(response.url).path.endswith((".xml", ".rss"))
    ):
        root = ET.fromstring(text)
        for item in root.findall(".//{*}item"):
            fields = {child.tag.split("}")[-1].lower(): "".join(child.itertext()).strip()
                      for child in item}
            candidates.append({"url": urljoin(response.url, fields.get("link", "")),
                               "title": fields.get("title", ""),
                               "date": _parse_date(fields.get("pubdate", fields.get("date", ""))),
                               "date_verified": True})
    else:
        parser = ArticleParser()
        parser.feed(text)
        for node in parser.nodes:
            href = node.attrs.get("href") if node.tag == "a" else None
            if not href:
                continue
            url = urljoin(response.url, href)
            path = urlparse(url).path
            pattern = (r"/t(\d{8})_\d+\.html$" if source["adapter"] == "stats"
                       else r"/newsevents/speech/[a-z]+(\d{8})[a-z]\.htm$")
            match = re.search(pattern, path)
            if match:
                try:
                    day = datetime.strptime(match.group(1), "%Y%m%d").date()
                except ValueError:
                    continue
                candidates.append({"url": url, "title": node.attrs.get("title") or _text(node),
                                   "date": day, "date_verified": False})
    unique = {}
    for item in candidates:
        url = urldefrag(item["url"])[0]
        if domain_allowed(url, source["allowed_domains"]):
            unique.setdefault(url, {**item, "url": url})
    return sorted(unique.values(), key=lambda item: item["date"] or date.min, reverse=True)


def _document(response: FetchResponse, source: dict, candidate: dict) -> SourceDocument:
    html = _decode(response)
    parser = ArticleParser()
    parser.feed(html)
    metadata = _metadata(parser)
    title = next((metadata[key] for key in ("articletitle", "citation_title", "og:title")
                  if metadata.get(key)), candidate["title"])
    published = next((day for key in ("pubdate", "citation_publication_date", "article:published_time")
                      if (day := _parse_date(metadata.get(key, "")))), None)
    if published is None:
        for node in parser.nodes:
            if node.tag == "time" or "article__time" in str(node.attrs.get("class", "")):
                published = _parse_date(str(node.attrs.get("datetime") or _text(node)))
                if published:
                    break
    if published is None and candidate["date_verified"]:
        published = candidate["date"]
    if published is None:
        raise ValueError("no explicit publication date in the article or official feed")
    text, selector = extract_article(html, source["selectors"])
    if not title.strip() or len(text) < 400:
        raise ValueError("article is missing its title or substantial full text")
    # Generic role=main may be a link/summary page, not an article snapshot.
    if source["adapter"] == "bis":
        has_publication_pdf = any(
            n.tag == "a" and urlparse(str(n.attrs.get("href", ""))).path.endswith(".pdf")
            and "/speeches/" in str(n.attrs.get("href", "")) for n in parser.nodes
        )
        if has_publication_pdf or selector not in {"article", ".field--name-body"} or len(text) < 1800:
            raise ValueError("BIS HTML completeness unverified (PDF-linked/short page); not ingested")
    return SourceDocument(
        document_id="finance_doc_" + hashlib.sha256(response.url.encode()).hexdigest()[:20],
        title=title.strip(), url=response.url, source_id=source["source_id"],
        publisher=source["publisher"], kind=source["kind"], published_at=published,
        retrieved_at=response.retrieved_at, text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _collect_documents(source: dict, config: dict, period: Period,
                       budget: _Budget) -> tuple[list[SourceDocument], SourceStatus]:
    started = time.monotonic()
    errors, candidates, documents, requests = [], [], [], []
    limit = min(4, max(1, config["limits"]["max_documents_per_source"]))
    attempts = min(6, max(limit, config["limits"]["max_article_attempts_per_source"]))
    listing_ok = False

    def discover(url: str) -> None:
        nonlocal listing_ok
        try:
            response = budget.fetch(url, source)
            candidates.extend(_listing_candidates(response, source))
            requests.append({"url": response.url, "sha256": response.sha256})
            listing_ok = True
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:240]}")

    discover(source["url"])
    if source["adapter"] == "fed" and source.get("archive_url") and not any(
        c["date"] and period.start <= c["date"] <= period.end for c in candidates
    ):
        for year in range(period.end.year, period.start.year - 1, -1):
            discover(source["archive_url"].format(year=year))
    # URL dates are discovery hints only. The article/feed supplies the final publication date.
    upper_hint = date.fromordinal(min(date.max.toordinal(), period.end.toordinal() + 1))
    selected = [c for c in candidates if c["date"] is None
                or period.start - timedelta(days=1) <= c["date"] <= upper_hint]
    unique = {c["url"]: c for c in selected}
    selected = sorted(unique.values(), key=lambda c: c["date"] or date.min, reverse=True)
    for candidate in selected[:attempts]:
        if len(documents) >= limit:
            break
        try:
            response = budget.fetch(candidate["url"], source)
            document = _document(response, source, candidate)
            requests.append({"url": response.url, "sha256": response.sha256,
                             "text_sha256": document.content_hash, "text_chars": len(document.text)})
            if period.start <= document.published_at <= period.end:
                documents.append(document)
            else:
                errors.append(f"{candidate['url']}: actual publication date is outside request")
        except Exception as exc:
            errors.append(f"{candidate['url']}: {type(exc).__name__}: {str(exc)[:240]}")
    # Even successful bounded samples are partial, never an exhaustive literature search.
    state = "partial" if documents else ("error" if errors else "empty")
    detail = {"quality": source["quality"], "discovered_count": len(candidates),
              "in_period_candidates": len(selected), "article_limit": limit,
              "listing_ok": listing_ok, "requests": requests, "issues": errors,
              "coverage": "bounded official-source sample; full extracted HTML text, no PDF/image OCR"}
    return documents, SourceStatus(
        source_id=source["source_id"], status=state, count=len(documents),
        detail=json.dumps(detail, ensure_ascii=False),
        latest_date=max((d.published_at.isoformat() for d in documents), default=""),
        elapsed_ms=round((time.monotonic() - started) * 1000, 2),
    )


def collect_finance_snapshot(*, start: str, end: str, symbols: list[str] | None = None,
                             session_mode: str = "explicit_period", as_of: str | None = None,
                             document_period: dict | None = None, include_documents: bool = True,
                             quality_policy: str = "allow_partial") -> dict:
    """Return a JSON-native FinanceSnapshot; no credentials, disk caches or fabricated fallback.

    Dates are inclusive. Latest mode preserves the user anchor but selects each market's
    own completed date. Metals are UTC reference days, not exchange trading sessions.
    """
    if not all(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
               for value in (start, end)):
        raise ValueError("start and end must be YYYY-MM-DD strings")
    period = Period(start=start, end=end)
    config = _load_config()
    if period.start < date(1990, 1, 1) or (period.end - period.start).days + 1 > min(
        1096, config["limits"]["max_period_days"]
    ):
        raise ValueError("supported period starts in 1990 or later and spans at most 1096 days")
    if symbols is not None and (not isinstance(symbols, list)
                                or any(not isinstance(symbol, str) for symbol in symbols)):
        raise ValueError("symbols must be a list of configured index codes")
    symbols = list(dict.fromkeys(config["default_symbols"] if symbols is None else symbols))
    if len(symbols) > 11 or any(symbol not in config["symbols"] for symbol in symbols):
        raise ValueError("only configured market symbols are supported (maximum eleven)")
    now = datetime.now(timezone.utc)
    cutoff = datetime.fromisoformat(as_of) if as_of is not None else now
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("as_of must contain an explicit UTC offset")
    if type(include_documents) is not bool:
        raise ValueError("include_documents must be a boolean")
    documents_period = Period.model_validate(document_period) if document_period is not None else period
    if documents_period.start < date(1990, 1, 1) or (documents_period.end - documents_period.start).days >= 1096:
        raise ValueError("document_period must start in 1990 or later and span at most 1096 days")
    budget = _Budget(config["limits"])
    snapshot = FinanceSnapshot(snapshot_id="pending", collected_at=now, period=period,
                               session_mode=session_mode, as_of=cutoff, document_period=documents_period,
                               quality_policy=quality_policy,
                               warnings=list(config.get("limitations", [])))
    if period.end >= now.astimezone(SHANGHAI).date():
        snapshot.warnings.append("\u622a\u6b62\u65e5\u671f\u5305\u542b\u4eca\u5929\u6216\u672a\u6765\uff0c\u4ec5\u4fdd\u7559\u76ee\u524d\u5df2\u77e5\u7684\u6536\u76d8\u65e5\u7ebf\uff1b\u76d8\u4e2d\u548c\u672a\u6765\u65e5\u671f\u6570\u636e\u4e0d\u8ba1\u5165\u3002")
    with ThreadPoolExecutor(max_workers=min(4, max(1, config["limits"]["max_workers"]))) as pool:
        market_jobs = [pool.submit(copy_context().run, _collect_instrument, symbol, config, period, cutoff, budget, session_mode)
                       for symbol in symbols]
        document_sources = list(config["document_sources"])
        if any(symbol in {"spx", "ixic", "dji", "xauusd", "xagusd"} for symbol in symbols):
            document_sources += config.get("global_document_sources", [])
        document_jobs = [pool.submit(copy_context().run, _collect_documents, source, config, documents_period, budget)
                         for source in document_sources] if include_documents else []
        for job in market_jobs:
            series, statuses, warnings = job.result()
            if series:
                snapshot.series.append(series)
            snapshot.source_statuses.extend(statuses)
            snapshot.warnings.extend(warnings)
        for job in document_jobs:
            documents, status = job.result()
            snapshot.documents.extend(documents)
            snapshot.source_statuses.append(status)
    snapshot.documents = sorted({doc.url: doc for doc in snapshot.documents}.values(),
                                key=lambda doc: (doc.published_at, doc.url), reverse=True)
    if include_documents and not snapshot.documents:
        snapshot.warnings.append("\u672c\u6b21\u672a\u53d6\u5f97\u53d1\u5e03\u65e5\u671f\u843d\u5728\u8bf7\u6c42\u533a\u95f4\u5185\u7684\u6709\u6548\u5b98\u65b9\u6587\u7ae0\uff0c\u4e0d\u7528\u533a\u95f4\u5916\u65e7\u6587\u8865\u9f50\u3002")
    data = snapshot.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False).encode()).hexdigest()
    data["snapshot_id"] = "finance_" + digest[:24]
    return FinanceSnapshot.model_validate(data).model_dump(mode="json")
