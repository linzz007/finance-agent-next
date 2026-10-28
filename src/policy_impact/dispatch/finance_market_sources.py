"""Opt-in legacy market tools backed by authorized immutable H2 snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json

from policy_impact.source_connectors.http_client import public_url

from .contracts import DispatchError
from .finance_tool_contracts import MARKET_TOOLS, validate


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_legacy_ids(value):
    if isinstance(value, dict):
        return {key: _without_legacy_ids(item) for key, item in value.items() if key != "evidence_id"}
    if isinstance(value, list):
        return [_without_legacy_ids(item) for item in value]
    return value


def market_source_read(value, reader_task_id):
    if not isinstance(value, dict) or value.get("tool") not in {"market.metrics", "market.series"}:
        return None
    text = value.get("text")
    if (value.get("ok") is not True or value.get("status") != "ok"
            or value.get("source_kind") != "public_market_data"
            or not isinstance(text, str) or not text.strip()
            or value.get("content_hash") != _hash(text)
            or not isinstance(value.get("snapshot_id"), str) or not value["snapshot_id"]
            or not isinstance(value.get("retrieved_at"), str) or not value["retrieved_at"]):
        return None
    metadata = {"tool", "ok", "status", "source_kind", "text", "content_hash"}
    try:
        if json.loads(text) != {key: item for key, item in value.items() if key not in metadata}:
            return None
    except ValueError:
        return None
    observation_key = "metrics" if value["tool"] == "market.metrics" else "bars"
    if not isinstance(value.get(observation_key), list) or not value[observation_key]:
        return None
    instruments = value.get("instruments")
    if not isinstance(instruments, list) or not instruments or any(
        not isinstance(item, dict) or not isinstance(item.get("source_url"), str)
        or not public_url(item["source_url"]) for item in instruments
    ):
        return None
    return {
        "source_kind": "public_market_data", "reader_task_id": reader_task_id,
        **{key: value[key] for key in (
            "snapshot_id", "source_snapshot_id", "period", "session_mode", "instruments",
            "retrieved_at", "collected_at", "content_hash",
        ) if key in value},
        "published_at": "", "line_start": 1, "line_end": len(text.splitlines()),
        "full_line_count": len(text.splitlines()), "truncated": False, "next_start_line": None,
    }


class FinanceMarketSources:
    """No mutable snapshot cache survives a single bounded tool execution."""

    async def execute(self, name, arguments, capabilities):
        if name not in MARKET_TOOLS:
            raise DispatchError("forbidden", "unsupported market tool")
        arguments = validate(name, arguments)
        from policy_impact.domains.finance.evidence import EvidenceLedger
        from policy_impact.domains.finance.market_research import MarketResearch
        from policy_impact.domains.finance.schemas import FinanceSnapshot

        market = MarketResearch(EvidenceLedger())
        if name == "market.catalog":
            return {"tool": name, "ok": True, **market.catalog()}
        if name == "market.snapshot":
            try:
                result = await asyncio.to_thread(market.snapshot, **arguments)
            except (DispatchError, ValueError):
                raise
            except Exception:  # noqa: BLE001 - provider details never enter context
                return {"tool": name, "ok": False, "status": "unavailable", "error": {
                    "code": "market_source_unavailable", "message": "No market snapshot was acquired.",
                }}
            original_id = result["snapshot_id"]
            data = market.snapshots[original_id]
            FinanceSnapshot.model_validate(data)
            snapshot_ref = await capabilities.store_context({
                "schema_version": "finance.h2.market_snapshot.v1", "snapshot": data,
                "content_hash": _hash(_json(data)),
            })
            return {**result, "tool": name, "ok": True, "snapshot_id": snapshot_ref,
                    "source_snapshot_id": original_id}

        snapshot_ref = arguments["snapshot_id"]
        raw = json.loads(await capabilities.load_context(snapshot_ref))
        data = raw.get("snapshot") if isinstance(raw, dict) else None
        if (not isinstance(data, dict) or raw.get("schema_version") != "finance.h2.market_snapshot.v1"
                or data.get("schema_version") != "finance.snapshot.v1"
                or raw.get("content_hash") != _hash(_json(data))):
            raise ValueError("snapshot_id must identify an intact authorized market snapshot")
        FinanceSnapshot.model_validate(data)
        market.snapshots[snapshot_ref] = data
        result = await asyncio.to_thread(
            market.metrics if name == "market.metrics" else market.series, **arguments,
        )
        payload = _without_legacy_ids(result)
        instruments = [{key: value for key, value in item.items() if key != "bars"} for item in data["series"]
            if name != "market.series" or item["symbol"] == arguments["symbol"]]
        payload.update(
            source_snapshot_id=data["snapshot_id"], period=data["period"],
            session_mode=data.get("session_mode", "explicit_period"), instruments=instruments,
            collected_at=data["collected_at"], source_statuses=data.get("source_statuses", []),
            warnings=data.get("warnings", []),
        )
        payload.setdefault("retrieved_at", data["collected_at"])
        nonempty = bool(payload.get("metrics") if name == "market.metrics" else payload.get("bars"))
        text = _json(payload)
        return {**payload, "tool": name, "ok": True, "status": "ok" if nonempty else "empty",
                "source_kind": "public_market_data", "text": text, "content_hash": _hash(text)}
