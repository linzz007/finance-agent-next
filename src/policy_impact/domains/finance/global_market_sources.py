"""Parsers for live-verified public daily endpoints; never substitute instruments."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import re
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from .schemas import MarketBar


def market_urls(source: dict, symbol: str, start: date, end: date) -> list[str]:
    provider_symbol = source["symbols"][symbol]
    if source["adapter"] == "yahoo":
        local = ZoneInfo("America/New_York")
        left = int(datetime.combine(start, time(), local).timestamp())
        right = int(datetime.combine(end + timedelta(days=1), time(), local).timestamp())
        return [source["url"] + "/" + quote(provider_symbol, safe="") + "?" + urlencode({
            "period1": left, "period2": right, "interval": "1d", "events": "history",
        })]
    if source["adapter"] == "stooq_candidate":
        return [source["url"] + "?" + urlencode({"s": provider_symbol, "i": "d",
                "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d")})]
    if source["adapter"] == "yahoo_spot_candidate":
        return [source["url"] + "/" + quote(provider_symbol, safe="") + "?range=1mo&interval=1d"]
    if source["adapter"] == "bullionvault_candidate":
        return [source["url"] + f"/{provider_symbol}/USD/14400/Full"]
    return [source["url"] + "?" + urlencode({"symbol": provider_symbol})]


def parse_daily(text: str, source: dict, symbol: str, start: date,
                end: date) -> tuple[list[MarketBar], dict]:
    """Only validate/retain requested dates; report null rows, never fill them from quotes."""
    adapter = source["adapter"]
    if adapter == "sina_spot_candidate":
        # The provider wraps JSON in a fixed assignment; no JavaScript evaluation.
        match = re.fullmatch(r"\s*(?:/\*.*?\*/\s*)?var _data=\((\[.*\])\);?\s*", text, re.S)
        if not match:
            raise ValueError("unexpected Sina candidate JSONP wrapper")
        payload = json.loads(match.group(1))
    else:
        payload = json.loads(text)
    issues, bars = [], []
    evidence = {"provider_symbol": source["symbols"][symbol], "adapter": adapter}
    if adapter in {"sina_us", "sina_spot_candidate"}:
        if not isinstance(payload, list):
            raise ValueError("Sina response is not a daily-bar array")
        evidence["raw_count"] = len(payload)
        for row in payload:
            day = date.fromisoformat(row["d" if adapter == "sina_us" else "date"])
            if not start <= day <= end:
                continue
            keys = ("o", "h", "l", "c") if adapter == "sina_us" else ("open", "high", "low", "close")
            values = [row.get(key) for key in keys]
            if any(value is None for value in values):
                issues.append({"date": day.isoformat(), "reason": "null_ohlc"})
                continue
            bars.append(MarketBar(date=day, open=values[0], high=values[1], low=values[2],
                                  close=values[3], volume=row.get("v") if adapter == "sina_us" else None))
    elif adapter == "yahoo":
        chart = payload.get("chart", {})
        results = chart.get("result")
        if chart.get("error") or not isinstance(results, list) or len(results) != 1:
            raise ValueError("Yahoo response has no single successful chart")
        result = results[0]
        meta = result.get("meta", {})
        if (meta.get("symbol") != source["symbols"][symbol] or meta.get("instrumentType") != "INDEX"
                or meta.get("currency") != "USD" or meta.get("dataGranularity") != "1d"
                or meta.get("exchangeTimezoneName") != "America/New_York"):
            raise ValueError("Yahoo symbol/instrument/currency/daily/timezone identity mismatch")
        stamps = result.get("timestamp", [])
        quotes = result.get("indicators", {}).get("quote", [])
        if not isinstance(stamps, list) or len(quotes) != 1:
            raise ValueError("Yahoo timestamps/quote block malformed")
        values = quotes[0]
        if any(not isinstance(values.get(key), list) or len(values[key]) != len(stamps)
               for key in ("open", "high", "low", "close")):
            raise ValueError("Yahoo OHLC arrays do not align with timestamps")
        evidence.update(raw_count=len(stamps), provider_metadata={key: meta.get(key) for key in (
            "symbol", "instrumentType", "currency", "exchangeTimezoneName", "dataGranularity",
            "regularMarketTime", "currentTradingPeriod",
        )})
        for offset, stamp in enumerate(stamps):
            day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
            if not start <= day <= end:
                continue
            ohlc = {key: values[key][offset] for key in ("open", "high", "low", "close")}
            if any(value is None for value in ohlc.values()):
                issues.append({"date": day.isoformat(), "reason": "null_ohlc"})
                continue
            volumes = values.get("volume", [])
            volume = volumes[offset] if len(volumes) == len(stamps) else None
            bars.append(MarketBar(date=day, **ohlc, volume=volume))
    else:
        raise ValueError("unverified global source adapter; data not admitted")
    evidence["dropped_rows"] = issues
    return bars, evidence
