"""Public BullionVault spot-midpoint HLC, aggregated into explicit UTC reference days.

This is not an exchange close, LBMA fixing, CFD, or a fabricated open price.
The provider's own chart uses epoch milliseconds and open:null. See EVIDENCE_URLS.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
import uuid

from policy_impact.source_connectors.http_client import _validate_destination

from .schemas import MarketBar

HOST = "chart-data.bullionvault.com"
SOURCE_URL = f"https://{HOST}/price_feed/"
INTERVAL_SECONDS = 14400
HISTORY_WINDOWS = {14400: 30, 43200: 90}
KG_PER_TROY_OUNCE = "0.0311034768"
EVIDENCE_URLS = ["https://www.bullionvault.com/gold-price-chart.do",
                 "https://www.bullionvault.com/silver-price-chart.do",
                 "https://www.bullionvault.com/chart/bullionvaultchart.js"]
CUTOFF_BASIS = (
    "UTC\u5b8c\u6574\u91c7\u6837\u65e5\u672b\u53c2\u8003\u4ef7\uff0c\u975e\u5b98\u65b9\u6536\u76d8\u6216\u7cbe\u786e\u5348\u591c\u6210\u4ea4\u4ef7\uff1a\u6c47\u603b00/04/08/12/16/20\u70b9\u8d77\u59cb\u7684\u516d\u4e2a4\u5c0f\u65f6\u6876\uff0c"
    "\u672b\u503c\u53d620\u70b9\u6876latestPrice\uff0c\u786e\u5207\u6700\u540e\u62a5\u4ef7\u65f6\u523b\u672a\u77e5\uff1b\u6b21\u65e500:00\u540e\u7b49\u5f8520\u5206\u949f\u3002"
    "\u6279\u53d1\u5e02\u573a\u73b0\u8d27\u4e2d\u95f4\u53c2\u8003\u4ef7\uff0c\u4e0d\u662f\u53ef\u6267\u884c\u6210\u4ea4\u4ef7\uff1b\u65e0\u5f00\u76d8\u4ef7\uff0c\u4f11\u5e02\u53ef\u80fd\u6cbf\u7528\u62a5\u4ef7\uff0c\u65e0\u4ea4\u6613\u6240\u65e5\u5386\u542b\u4e49\u3002"
)


def history_interval(start: date, end: date) -> int:
    # A rolling month needs a pre-period baseline plus a possibly incomplete first day.
    return 43200 if (end - start).days + 2 >= HISTORY_WINDOWS[14400] else 14400


def cutoff_basis(interval_seconds: int) -> str:
    if interval_seconds == INTERVAL_SECONDS:
        return CUTOFF_BASIS
    return (
        "UTC complete reference day: two 12-hour buckets starting 00/12 UTC; "
        "day-end reference is the 12 UTC bucket latestPrice, exact last tick unknown. "
        "The system waits 20 minutes after next 00 UTC (not a provider publication SLA). "
        "Wholesale spot midpoint, not an official close or executable quote; "
        "no open price; quotes may be carried through closures."
    )


async def _receive_payload(metal: str, timeout: float, max_bytes: int, interval_seconds: int) -> dict:
    try:
        from websockets.asyncio.client import ClientConnection, connect
    except ImportError as exc:
        raise ValueError("websockets>=15 is required for public BullionVault history") from exc

    class NoRedirectConnection(connect):
        def process_redirect(self, exc):
            return exc

    # HTTP helper cannot perform STOMP/WebSocket. Reuse its DNS/public-host checks;
    # the protocol destination is fixed, redirects/proxies/auth are never allowed.
    _validate_destination(SOURCE_URL, [HOST])
    url = f"wss://{HOST}/price_feed/000/{uuid.uuid4().hex[:12]}/websocket"
    async with NoRedirectConnection(
        url, origin="https://www.bullionvault.com", proxy=None,
        open_timeout=timeout, close_timeout=0.2, max_size=max_bytes, max_queue=4,
        ping_interval=None, create_connection=ClientConnection,
    ) as socket:
        if await socket.recv() != "o":
            raise ValueError("unexpected public chart opening frame")
        await socket.send(json.dumps(["CONNECT\naccept-version:1.2\nheart-beat:0,0\n\n\x00"]))
        greeting = await socket.recv()
        if not greeting.startswith("a") or not json.loads(greeting[1:])[0].startswith("CONNECTED\n"):
            raise ValueError("public chart did not acknowledge STOMP")
        await socket.send(json.dumps([
            f"SUBSCRIBE\nid:finance-public-history\ndestination:/t/{metal}/USD/{interval_seconds}\nack:auto\n\n\x00"
        ]))
        for _ in range(4):
            frame = await socket.recv()
            if frame == "h":
                continue
            if not frame.startswith("a"):
                raise ValueError("unexpected public chart data frame")
            messages = json.loads(frame[1:])
            if len(messages) != 1 or not messages[0].startswith("MESSAGE\n"):
                raise ValueError("unexpected STOMP message type")
            header, body = messages[0].split("\n\n", 1)
            if f"destination:/t/{metal}/USD/{interval_seconds}" not in header:
                raise ValueError("chart destination does not match requested instrument")
            return json.loads(body.rstrip("\x00"))
    raise ValueError("no historical chart batch in bounded receive")


def fetch_history(symbol: str, *, timeout_seconds: float = 8,
                  max_bytes: int = 2_000_000, interval_seconds: int = INTERVAL_SECONDS) -> dict:
    """One bounded, credential-free subscription; caller counts it in the shared budget."""
    if symbol not in {"xauusd", "xagusd"}:
        raise ValueError("only spot gold/silver chart symbols are accepted")
    if interval_seconds not in HISTORY_WINDOWS:
        raise ValueError("unsupported public chart interval")
    metal = "AUX" if symbol == "xauusd" else "AGX"
    timeout = min(8.0, max(0.1, timeout_seconds))
    payload = asyncio.run(asyncio.wait_for(
        _receive_payload(metal, timeout, min(max_bytes, 2_000_000), interval_seconds), timeout))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    chart = EVIDENCE_URLS[0 if symbol == "xauusd" else 1]
    return {"payload": payload, "url": chart, "transport": "public_websocket_stomp",
            "transport_url": f"wss://{HOST}/price_feed/ (ephemeral SockJS transport)",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _per_ounce(price) -> float:
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError("invalid positive finite BullionVault price")
    with localcontext(Context(prec=700, rounding=ROUND_HALF_UP)):
        return float((Decimal(str(price)) * Decimal(KG_PER_TROY_OUNCE)).quantize(Decimal("0.000001")))


def reference_days(payload: dict, symbol: str, *, as_of: datetime,
                   start: date, end: date, interval_seconds: int = INTERVAL_SECONDS) -> tuple[list[MarketBar], dict]:
    """Require every bucket in a UTC day; never mix resolutions or invent samples."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be aware")
    if interval_seconds not in HISTORY_WINDOWS:
        raise ValueError("unsupported public chart interval")
    metal = {"xauusd": "AUX", "xagusd": "AGX"}[symbol]
    if (payload.get("securityId") != metal or payload.get("valuationSecurityId") != "USD"
            or payload.get("name") != "Galmarley_Mid" or payload.get("interval") != interval_seconds
            or payload.get("batch") != "full" or not isinstance(payload.get("prices"), list)):
        raise ValueError("BullionVault instrument/currency/interval/batch identity mismatch")
    by_stamp = {}
    for point in payload["prices"]:
        stamp = point.get("priceTime")
        if type(stamp) is not int or stamp % (interval_seconds * 1000):
            raise ValueError("unaligned or invalid provider epoch timestamp")
        if stamp in by_stamp and by_stamp[stamp] != point:
            raise ValueError("conflicting duplicate provider timestamp")
        by_stamp[stamp] = point
    grouped = {}
    for stamp in sorted(by_stamp):
        dt = datetime.fromtimestamp(stamp / 1000, timezone.utc)
        if start <= dt.date() <= end:
            grouped.setdefault(dt.date(), []).append((dt, by_stamp[stamp]))
    bars, issues, evidence_days = [], [], {}
    for day, samples in sorted(grouped.items()):
        closes_at = datetime.combine(day + timedelta(days=1), time(), timezone.utc)
        if closes_at + timedelta(minutes=20) > as_of:
            issues.append({"date": day.isoformat(), "reason": "utc_reference_day_not_completed"})
            continue
        expected_hours = list(range(0, 24, interval_seconds // 3600))
        if len(samples) != len(expected_hours) or [stamp.hour for stamp, _ in samples] != expected_hours:
            issues.append({"date": day.isoformat(), "reason": "missing_four_hour_buckets" if interval_seconds == 14400
                           else "missing_twelve_hour_buckets", "count": len(samples)})
            continue
        if any(point.get(key) is None for _, point in samples for key in ("high", "low", "latestPrice")):
            issues.append({"date": day.isoformat(), "reason": "missing_hlc"})
            continue
        converted = []
        for _, point in samples:
            high, low, close = (_per_ounce(point[key]) for key in ("high", "low", "latestPrice"))
            if not low <= close <= high:
                raise ValueError("inconsistent provider HLC")
            converted.append((high, low, close))
        bars.append(MarketBar(date=day, open=None, high=max(v[0] for v in converted),
                              low=min(v[1] for v in converted), close=converted[-1][2], volume=None))
        evidence_days[day.isoformat()] = {
            "sample_count": len(expected_hours), "bucket_start_timestamps_ms": [point["priceTime"] for _, point in samples],
            "last_bucket_start_at": samples[-1][0].isoformat(),
            "reference_day_end_at": closes_at.isoformat(),
            "last_observation_time_exact": False,
            "constant_reference_day": all(v[0] == v[1] == converted[0][2] for v in converted),
        }
    return bars, {
        "natural_day_reference": True, "market_session_verified": False,
        "provider_may_carry_quotes_through_closures": True,
        "daily_cutoff_basis": cutoff_basis(interval_seconds), "interval_seconds": interval_seconds,
        "expected_samples_per_day": 86400 // interval_seconds,
        "history_window_days": HISTORY_WINDOWS[interval_seconds],
        "raw_price_unit": "USD/kg", "kg_per_troy_ounce": KG_PER_TROY_OUNCE,
        "unit_conversion": "price_per_kg * 0.0311034768; no currency conversion",
        "timezone_evidence": "provider epoch milliseconds; UTC-aligned interval starts; not CSV local-time guessing",
        "evidence_urls": EVIDENCE_URLS, "raw_count": len(payload["prices"]),
        "reference_days": evidence_days, "dropped_days": issues,
        "limitation": f"rolling approximately {HISTORY_WINDOWS[interval_seconds]}-day "
                      f"{interval_seconds // 3600}-hour sample; unknown intrabucket final tick time; "
                      "provider may carry quotes through closures; no official exchange/LBMA close",
    }
