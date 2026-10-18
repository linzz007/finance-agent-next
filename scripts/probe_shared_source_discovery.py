"""Bounded HTTP-only smoke probe of every active shared provider, without keys/LLMs."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Lock
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from policy_impact.source_connectors import ingestion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--per-source-limit", type=int, default=4)
    parser.add_argument("--request-budget", type=int, default=48)
    parser.add_argument("--timeout", type=float, default=6)
    args = parser.parse_args()
    config = deepcopy(ingestion.load_source_config())
    for group in ("news_sources", "policy_sources"):
        for source in config.get(group, []):
            source["max_repo_details"] = min(int(source.get("max_repo_details", 1)), 1)
            source["max_queries"] = 1
            source["max_body_fetches"] = min(int(source.get("max_body_fetches", 2)), 2)
    original_fetch = ingestion.fetch_url
    lock = Lock()
    requests = []
    started = time.monotonic()
    budget = max(1, min(args.request_budget, 64))

    def fetch(url, **kwargs):
        with lock:
            if len(requests) >= budget:
                raise RuntimeError("probe HTTP request budget exhausted")
            requests.append(url)
        kwargs.update(timeout_seconds=max(1, min(args.timeout, 8)), retries=0)
        return original_fetch(url, **kwargs)

    ingestion.load_source_config = lambda: config
    ingestion.fetch_url = fetch
    result = ingestion.collect_news_sources(
        discovery_plan={"search_terms": args.term or ["agent evaluation", "人工智能"],
                        "date_from": args.date_from, "date_to": args.date_to,
                        "per_source_limit": max(1, min(args.per_source_limit, 8)), "max_candidates": 120},
        persist=False, force_refresh=True,
    )
    records = []
    for status in result["source_statuses"]:
        records.append({key: status.get(key) for key in (
            "source_id", "status", "raw_count", "unique_count", "item_count", "duplicate_count",
            "dropped_count", "body_fetch_count", "request_count", "errors", "error")})
    print(json.dumps({
        "scope": "All active shared sources; unauthenticated HTTP only; no LLM, environment keys, or snapshots.",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "http_request_count": len(requests), "request_budget": budget,
        "collection_succeeded": result["collection_succeeded"],
        "collection_partial": result["collection_partial"],
        "cleaning_diagnostics": result["cleaning_diagnostics"], "sources": records,
        "sample": [{key: item.get(key) for key in ("source_id", "title", "url", "published_at",
                                                   "published_at_basis", "evidence_kind")}
                   for item in result["items"][:6]],
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
