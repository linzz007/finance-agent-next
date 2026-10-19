"""Composable market observations; no model analysis or report publication."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.market_catalog import INSTRUMENTS, get_symbol_metadata


TOOL_DESCRIPTIONS = [
    {"name": "market.catalog", "description": "List supported market symbols and quote units.", "parameters": {}},
    {"name": "market.snapshot", "description": "Fetch public daily market data for chosen symbols and dates; no analysis.",
     "parameters": {"symbols": "List of symbols from market.catalog", "start": "Optional YYYY-MM-DD",
                    "end": "Optional YYYY-MM-DD", "latest_completed": "True for each market's last completed session"}},
    {"name": "market.metrics", "description": "Calculate deterministic changes for one acquired snapshot.",
     "parameters": {"snapshot_id": "ID returned by market.snapshot"}},
    {"name": "market.series", "description": "Read bounded daily bars from one acquired snapshot.",
     "parameters": {"snapshot_id": "ID returned by market.snapshot", "symbol": "Market symbol",
                    "offset": "First bar index", "limit": "1-100 bars"}},
]


class MarketResearch:
    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger
        self.snapshots: dict[str, dict] = {}
        self.metric_reads: list[dict] = []
        self.series_reads: list[dict] = []

    @staticmethod
    def catalog() -> dict:
        return {"symbols": {symbol: get_symbol_metadata(symbol) for symbol in INSTRUMENTS}}

    def snapshot(self, symbols: list[str], start: str | None = None,
                 end: str | None = None, latest_completed: bool = False) -> dict:
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 11 or any(
            not isinstance(symbol, str) or symbol not in INSTRUMENTS for symbol in symbols
        ):
            raise ValueError("symbols must be 1-11 configured market symbols")
        if type(latest_completed) is not bool:
            raise ValueError("latest_completed must be boolean")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        start = start or today
        end = end or today
        from policy_impact.domains.finance.acquisition import collect_finance_snapshot

        data = collect_finance_snapshot(
            start=start, end=end, symbols=symbols,
            session_mode="latest_completed" if latest_completed else "explicit_period",
            include_documents=False, quality_policy="allow_partial",
        )
        snapshot_id = data["snapshot_id"]
        self.snapshots[snapshot_id] = data
        observed = [series["symbol"] for series in data["series"]]
        return {
            "snapshot_id": snapshot_id, "period": data["period"],
            "session_mode": data["session_mode"], "collected_at": data["collected_at"],
            "requested_symbols": symbols, "observed_symbols": observed,
            "missing_symbols": [symbol for symbol in symbols if symbol not in observed],
            "series": [{
                "symbol": item["symbol"], "provider": item["provider"],
                "unit": item["unit"], "price_basis": item["price_basis"],
                "source_url": item["source_url"], "retrieved_at": item["retrieved_at"],
                "session_date": item.get("session_date"), "session_verified": item.get("session_verified"),
                "bar_count": len(item["bars"]),
            } for item in data["series"]],
            "source_statuses": data["source_statuses"], "warnings": data["warnings"],
        }

    def metrics(self, snapshot_id: str) -> dict:
        data = self._get(snapshot_id)
        from policy_impact.domains.finance.metrics import compute_market_metrics

        results = compute_market_metrics(data)
        compact = []
        for item in results:
            evidence = self.ledger.add_market_metric(item, snapshot_id=snapshot_id)
            compact.append({**{key: value for key, value in item.items()
                               if key not in {"daily_changes", "points"}},
                            "snapshot_id": snapshot_id,
                            "evidence_id": evidence.evidence_id})
        self.metric_reads.extend(compact)
        return {"snapshot_id": snapshot_id, "metrics": compact}

    def series(self, snapshot_id: str, symbol: str, offset: int = 0, limit: int = 60) -> dict:
        data = self._get(snapshot_id)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative and limit must be 1-100")
        match = next((item for item in data["series"] if item["symbol"] == symbol), None)
        if match is None:
            raise ValueError("symbol is not present in this snapshot")
        bars = match["bars"]
        result = {"snapshot_id": snapshot_id, "symbol": symbol, "unit": match["unit"],
                "source_url": match["source_url"], "retrieved_at": match["retrieved_at"],
                "offset": offset, "bars": bars[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(bars) else None}
        if result["bars"]:
            content_hash = hashlib.sha256(json.dumps(
                result["bars"], ensure_ascii=False, sort_keys=True, default=str,
            ).encode("utf-8")).hexdigest()
            evidence_excerpt = json.dumps(
                {
                    "symbol": symbol,
                    "unit": match["unit"],
                    "price_basis": match["price_basis"],
                    "provider": match["provider"],
                    "source_url": match["source_url"],
                    "offset": offset,
                    "bars": result["bars"],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            evidence = self.ledger.add_reference(
                source_type="market_bar_series",
                provenance="retrieved_public_market_data",
                source_ref=f"{snapshot_id}:{symbol}:bars:{offset}:{offset + len(result['bars'])}",
                content_hash=content_hash,
                retrieved_at=str(match["retrieved_at"]),
                published_at=str(result["bars"][-1].get("date") or ""),
                source_url=str(match["source_url"]),
                excerpt=evidence_excerpt,
                metadata={
                    "snapshot_id": snapshot_id, "symbol": symbol, "offset": offset,
                    "bar_count": len(result["bars"]), "unit": match["unit"],
                    "price_basis": match["price_basis"],
                    "provider": match["provider"],
                },
            )
            result["evidence_id"] = evidence.evidence_id
        self.series_reads.append({
            "snapshot_id": snapshot_id,
            "symbol": symbol,
            "offset": offset,
            "bar_count": len(result["bars"]),
            "evidence_id": result.get("evidence_id", ""),
        })
        return result

    def _get(self, snapshot_id: str) -> dict:
        if not isinstance(snapshot_id, str) or snapshot_id not in self.snapshots:
            raise ValueError("snapshot_id must belong to the current research turn")
        return self.snapshots[snapshot_id]
