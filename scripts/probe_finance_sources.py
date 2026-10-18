"""Reproducible live finance acceptance, without keys, caches, or old report inputs."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.domains.finance.acquisition import (  # noqa: E402
    _decode, _document, _listing_candidates, _load_config, _market_urls, _parse_market,
    collect_finance_snapshot,
)
from policy_impact.domains.finance.metrics import compute_market_metrics  # noqa: E402
from policy_impact.domains.finance.schemas import FinanceSnapshot, Period  # noqa: E402
from policy_impact.source_connectors.http_client import fetch_url  # noqa: E402


def probe(url: str, source: dict, *, purpose: str, symbol: str = "",
          candidate: dict | None = None) -> dict:
    started = time.monotonic()
    result = {"url": url, "source_id": source["source_id"], "quality": source["quality"],
              "purpose": purpose, "allowed_domains": source["allowed_domains"]}
    try:
        response = fetch_url(url, allowed_domains=source["allowed_domains"], timeout_seconds=8,
                             retries=0, max_bytes=2_000_000)
        result.update(status="ok", http_status=response.status, final_url=response.url,
                      retrieved_at=response.retrieved_at, content_type=response.content_type,
                      bytes=len(response.body), sha256=hashlib.sha256(response.body).hexdigest())
        try:
            if response.status != 200:
                raise ValueError(f"unexpected HTTP status {response.status}")
            if purpose == "market":
                bars = _parse_market(response, source, symbol)
                result.update(symbol=symbol, bar_count=len(bars),
                              first_bar=bars[0].model_dump(mode="json") if bars else None,
                              last_bar=bars[-1].model_dump(mode="json") if bars else None,
                              payload=json.loads(_decode(response)), usable=bool(bars))
            elif purpose == "listing":
                candidates = _listing_candidates(response, source)
                result.update(candidates=[{**c, "date": c["date"].isoformat() if c["date"] else None}
                                          for c in candidates], usable=bool(candidates))
            elif purpose == "article":
                document = _document(response, source, candidate)
                result.update(document=document.model_dump(mode="json"), usable=True)
            else:
                result.update(response_preview=_decode(response)[:800], usable=False,
                              validation_error="Discovery candidate only; no adapter verified.")
        except Exception as exc:
            result.update(usable=False, validation_error=f"{type(exc).__name__}: {str(exc)[:500]}")
    except Exception as exc:
        result.update(status="error", usable=False, error_type=type(exc).__name__,
                      error=str(exc)[:500])
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    print(json.dumps({k: v for k, v in result.items() if k not in {
        "payload", "document", "candidates", "response_preview"
    }}, ensure_ascii=True), flush=True)
    return result


def verify_snapshot(snapshot: dict) -> dict:
    normalized = FinanceSnapshot.model_validate(snapshot).model_dump(mode="json")
    if normalized != snapshot:
        raise AssertionError("snapshot was not JSON-native FinanceSnapshot")
    metrics = compute_market_metrics(snapshot)
    for metric in metrics:
        if metric["baseline_available"]:
            if metric["start_date"] >= snapshot["period"]["start"]:
                raise AssertionError("baseline did not precede start")
        if metric["change_pct"] is not None:
            independent = (Decimal(str(metric["end_close"])) / Decimal(str(metric["start_close"])) - 1) * 100
            if abs(independent - Decimal(str(metric["change_pct"]))) > Decimal("0.00000051"):
                raise AssertionError("deterministic return formula did not match")
    for document in snapshot["documents"]:
        if hashlib.sha256(document["text"].encode()).hexdigest() != document["content_hash"]:
            raise AssertionError("article snapshot hash mismatch")
    json.dumps(snapshot, allow_nan=False)
    return {"snapshot": snapshot, "metrics": metrics,
            "verification": {"schema_json": True, "formula_checked": True,
                             "article_hashes_checked": True, "series_count": len(snapshot["series"]),
                             "document_count": len(snapshot["documents"]),
                             "all_default_baselines_available": len(metrics) == 3 and all(
                                 m["baseline_available"] for m in metrics)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--end", default="2026-09-22")
    parser.add_argument("--historical-start", default="2024-01-02")
    parser.add_argument("--historical-end", default="2024-01-10")
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance/finance_source_probe.json")
    args = parser.parse_args()
    config = _load_config()
    for left, right in [(args.start, args.end), (args.historical_start, args.historical_end)]:
        window = Period(start=left, end=right)
        if window.start < date(1990, 1, 1) or (window.end - window.start).days + 1 > 1096:
            parser.error("probe windows must start in 1990 or later and span at most 1096 days")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    result = {"probed_at": datetime.now(timezone.utc).isoformat(), "network": "live",
              "policy": {"timeout_seconds": 8, "retries": 0, "probe_concurrency": 1,
                         "snapshot_concurrency": 4, "max_bytes": 2_000_000,
                         "snapshot_request_limit": 72, "snapshot_scheduling_budget_seconds": 90,
                         "credentials": "none", "inputs": "live public endpoints only"},
              "probes": [], "acceptance": {}, "limitations": config["limitations"]}
    for source in config["market_sources"]:
        for symbol in config["default_symbols"]:
            for url in _market_urls(source, symbol, start - timedelta(days=45), end):
                result["probes"].append(probe(url, source, purpose="market", symbol=symbol))
    for source in config["document_sources"]:
        listing = probe(source["url"], source, purpose="listing")
        result["probes"].append(listing)
        in_period = [c for c in listing.get("candidates", []) if c["date"] is not None
                     and args.start <= c["date"] <= args.end]
        if in_period:
            candidate = {**in_period[0], "date": date.fromisoformat(in_period[0]["date"])}
            result["probes"].append(probe(candidate["url"], source, purpose="article", candidate=candidate))
    result["probes"].append(probe("https://www.imf.org/en/Blogs", {
        "source_id": "imf_blogs_candidate", "allowed_domains": ["www.imf.org"],
        "quality": "First-party institution; live connectivity candidate, no content used if blocked."
    }, purpose="discovery_candidate"))
    for label, left, right in [("requested_window", args.start, args.end),
                               ("historical_window", args.historical_start, args.historical_end)]:
        print(f"Collecting {label}: {left} through {right}", flush=True)
        try:
            result["acceptance"][label] = verify_snapshot(collect_finance_snapshot(start=left, end=right))
            print(json.dumps(result["acceptance"][label]["verification"]), flush=True)
        except Exception as exc:
            result["acceptance"][label] = {"error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(str(args.output), flush=True)
    if any("error" in item for item in result["acceptance"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
