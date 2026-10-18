"""Bounded credential-free live acceptance; never reads saved reports as market evidence."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.domains.finance import acquisition as acq  # noqa: E402
from policy_impact.domains.finance import bullionvault_source as bv  # noqa: E402
from policy_impact.domains.finance.global_market_sources import market_urls  # noqa: E402
from policy_impact.domains.finance.metrics import compute_market_metrics  # noqa: E402
from policy_impact.domains.finance.schemas import FinanceSnapshot, Period  # noqa: E402
from policy_impact.source_connectors.http_client import fetch_url  # noqa: E402


def http_probe(url: str, domains: list[str], purpose: str) -> dict:
    result = {"url": url, "purpose": purpose, "admitted_as_spot_series": False}
    try:
        response = fetch_url(url, allowed_domains=domains, timeout_seconds=8, retries=0, max_bytes=2_000_000)
        text = acq._decode(response)
        result.update(http_status=response.status, final_url=response.url,
            retrieved_at=response.retrieved_at, sha256=response.sha256,
            content_type=response.content_type, bytes=len(response.body), response_preview=text[:300])
        if url.endswith("bullionvaultchart.js"):
            result["primary_source_excerpts"] = []
            for marker in (".0311034768", "priceTime", "Math.ceil", "open:null"):
                offset = text.find(marker)
                result["primary_source_excerpts"].append({"marker": marker,
                    "found": offset >= 0, "excerpt": text[max(0, offset-100):offset+250] if offset >= 0 else ""})
        if "/prices/CSV/" in url:
            result["csv_header_and_samples"] = text.splitlines()[:4]
        if "/futures/quotes/" in url:
            offset = text.find("CFD")
            result["instrument_label_evidence"] = text[max(0, offset-100):offset+120] if offset >= 0 else None
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc)[:400])
    return result


def spot_probe(symbol: str, now: datetime) -> dict:
    result = {"symbol": symbol, "source_url": bv.EVIDENCE_URLS[0 if symbol == "xauusd" else 1]}
    try:
        raw = bv.fetch_history(symbol)
        bars, evidence = bv.reference_days(raw["payload"], symbol, as_of=now,
            start=now.date()-timedelta(days=35), end=now.date())
        result.update(raw, parsed_bars=[bar.model_dump(mode="json") for bar in bars], evidence=evidence)
        most_recent_stamp = max(point["priceTime"] for point in raw["payload"]["prices"])
        result["most_recent_bucket_start_at"] = datetime.fromtimestamp(most_recent_stamp/1000, timezone.utc).isoformat()
        result["live_bucket_start_not_end_evidence"] = (
            most_recent_stamp <= now.timestamp()*1000 < most_recent_stamp + bv.INTERVAL_SECONDS*1000)
        result["last_tick_time_known"] = False
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc)[:400])
    return result


def verify(snapshot: dict) -> dict:
    assert FinanceSnapshot.model_validate(snapshot).model_dump(mode="json") == snapshot
    json.dumps(snapshot, allow_nan=False)
    for series in snapshot["series"]:
        if series["asset_class"] == "spot_metal":
            assert series["price_basis"] == "spot_price" and series["unit"] == "USD/troy_oz"
            assert all(bar["open"] is None and bar["volume"] is None for bar in series["bars"])
            assert series["source_url"].startswith("https://www.bullionvault.com/")
            for bar in series["bars"]:
                assert series["session_evidence"]["reference_days"][bar["date"]]["sample_count"] == 6
        if series["session_verified"]:
            dates = {bar["date"] for bar in series["bars"]}
            assert series["expected_session_date"] in dates and series["expected_previous_session_date"] in dates
    return {"snapshot": snapshot, "metrics": compute_market_metrics(snapshot), "schema_json_checked": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/acceptance/global_finance_source_probe.json")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    anchor = now.date().isoformat()
    config = acq._load_config()
    symbols = ["spx", "ixic", "dji", "xauusd", "xagusd"]
    output = {"probe_started_at": now.isoformat(), "scope": "fresh live public requests; no credentials or saved-report input",
              "price_basis": "spot wholesale midpoint reference, NOT official close; index price returns",
              "limitations": config["limitations"]}
    print("Collecting latest completed global snapshot", flush=True)
    output["latest_completed"] = verify(acq.collect_finance_snapshot(start=anchor, end=anchor,
        symbols=symbols, session_mode="latest_completed", as_of=now.isoformat(),
        include_documents=False, quality_policy="strict"))
    print("Preserving original spot metadata and epoch samples", flush=True)
    output["spot_original_provenance"] = [spot_probe(symbol, datetime.now(timezone.utc)) for symbol in symbols[-2:]]
    start, end = now.date()-timedelta(days=7), now.date()-timedelta(days=1)
    print("Checking explicit period and mainland compatibility", flush=True)
    output["explicit_period"] = verify(acq.collect_finance_snapshot(start=str(start), end=str(end),
        symbols=symbols, as_of=now.isoformat(), include_documents=False))
    output["mainland_compatibility"] = verify(acq.collect_finance_snapshot(start=str(start), end=str(end),
        symbols=["sh000001"], as_of=now.isoformat(), include_documents=False))
    print("Checking official Fed monetary policy feed and full HTML articles", flush=True)
    documents, status = acq._collect_documents(config["global_document_sources"][0], config,
        Period(start=now.date()-timedelta(days=30), end=now.date()), acq._Budget(config["limits"]))
    for document in documents:
        assert hashlib.sha256(document.text.encode()).hexdigest() == document.content_hash
    output["fed_monetary"] = {"status": status.model_dump(mode="json"),
                              "documents": [d.model_dump(mode="json") for d in documents]}
    jobs = [(url, ["www.bullionvault.com"], "official instrument/unit/bucket evidence") for url in bv.EVIDENCE_URLS]
    jobs += [("https://finance.sina.com.cn/futures/quotes/XAU.shtml", ["finance.sina.com.cn"], "explicit CFD exclusion"),
             ("https://data-asg.goldprice.org/dbXRates/USD", ["data-asg.goldprice.org"], "current quote candidate; not OHLC")]
    for source in config["spot_candidates"]:
        for symbol in ("xauusd", "xagusd"):
            for url in market_urls(source, symbol, start, end):
                jobs.append((url, source["allowed_domains"], source["quality"]))
    print("Auditing primary sources and excluded candidates (four workers, one try per URL)", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        output["http_probes"] = list(pool.map(lambda job: http_probe(*job), jobs))
    output["probe_finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    snapshot = output["latest_completed"]["snapshot"]
    lines = ["# \u5168\u7403\u91d1\u878d\u516c\u5f00\u6765\u6e90\u5b9e\u6d4b", "", f"\u5b9e\u6d4b\u65f6\u95f4\uff1a{now.isoformat()}", "",
        "\u6570\u636e\u6765\u81ea\u672c\u6b21\u5b9e\u65f6\u8bf7\u6c42\uff0c\u672a\u8bfb\u53d6\u65e7\u62a5\u544a\u3001\u5bc6\u94a5\u6216\u7528\u6237\u6301\u4ed3\u3002", "",
        "|\u54c1\u79cd|\u6765\u6e90|\u5b9e\u9645\u53c2\u8003/\u4ea4\u6613\u65e5|\u671f\u524d\u57fa\u51c6\u65e5|\u672b\u503c|\u5355\u4f4d|\u5b8c\u6574\u6027\u6838\u9a8c|",
        "|---|---|---|---|---:|---|---|"]
    for s in snapshot["series"]:
        lines.append(f"|{s['symbol']}|{s['provider']}|{s['session_date']}|{s['expected_previous_session_date']}|"
                     f"{s['bars'][-1]['close']}|{s['unit']}|{s['session_verified']}|")
    missing = sorted(set(symbols) - {s["symbol"] for s in snapshot["series"]})
    lines += ["", "\u672a\u53d6\u5f97\u54c1\u79cd\uff1a" + (", ".join(missing) or "\u65e0"), "",
        "\u91d1\u94f6\u4ec5\u4e3aUTC\u5b8c\u6574\u91c7\u6837\u65e5\u672b\u53c2\u8003\u4ef7\u3002\u516d\u4e2a00/04/08/12/16/20\u8d77\u59cb\u76844\u5c0f\u65f6\u6876\u5408\u5e76HLC\uff1b\u5f00\u76d8\u4ef7\u672a\u77e5\u4fdd\u6301null\u3002",
        "20\u70b9\u662f\u6700\u540e\u6876\u8d77\u70b9\uff0c\u4e0d\u662f\u672b\u503c\u786e\u5207\u6210\u4ea4\u65f6\u95f4\uff1b\u65e0\u7cbe\u786e\u5348\u591c\u62a5\u4ef7\u6216\u5b98\u65b9\u6536\u76d8\u627f\u8bfa\u3002",
        "session_verified\u53ea\u6838\u9a8c\u5b8c\u6574\u53c2\u8003\u65e5\u91c7\u6837\uff0c\u4e0d\u4ee3\u8868\u4ea4\u6613\u6240\u8ba4\u8bc1\u3002\u9759\u6b62\u62a5\u4ef7\u53ef\u80fd\u6cbf\u7528\u4f11\u5e02\u524d\u4ef7\u683c\uff0c\u4e0d\u7b97\u65b0\u4ea4\u6613\u65e5\uff1b\u7f3a\u5931\u65e5\u4e0d\u8865\u6d1e\u3002",
        "\u539f\u59cbname/securityId/valuationSecurityId/interval/batch\u3001epoch\u65f6\u95f4\u6233\u3001USD/kg\u5230USD/troy_oz\u6362\u7b97\u3001\u54c8\u5e0c\u4e0e\u5168\u6587\u89c1\u540c\u540dJSON\u3002",
        "\u91d1\u94f6\u53ea\u6709\u7ea630\u5929\u6eda\u52a8\u6837\u672c\uff1b\u66f4\u957f\u533a\u95f4\u5982\u5b9e\u6807\u7f3a\u5931\u3002\u6307\u6570\u6765\u6e90\u4e3a\u7b2c\u4e09\u65b9\u516c\u5f00\u63a5\u53e3\uff0c\u65e5\u5386\u6838\u9a8c\u4e0d\u7b49\u4e8e\u4ea4\u6613\u6240\u4ef7\u683c\u8ba4\u8bc1\u3002",
        "Yahoo\u7f3a\u5931close\u4e0d\u4ee5regularMarketPrice\u586b\u5145\uff0c\u6574\u6bb5\u56de\u9000Sina\uff1b\u4efb\u4f55\u5907\u7528\u6e90\u4e0d\u62fc\u63a5\u3002CFD\u3001\u671f\u8d27\u3001ETF\u53caLBMA\u5b9a\u76d8\u4e0d\u66ff\u4ee3spot\u3002",
        "Fed RSS\u4ec5\u6709\u754c\u6837\u672c\uff0c\u6b63\u6587\u4fdd\u7559\u4f46\u4e0d\u6293\u6295\u5f71PDF/\u56fe\u7247\u9644\u4ef6\uff1b\u5b8f\u89c2\u53d1\u5e03\u65f6\u95f4\u4e0d\u8bc1\u660e\u4ef7\u683c\u56e0\u679c\u3002", "",
        f"\u673a\u5668\u53ef\u8bfb\u5ba1\u8ba1\uff1a{args.output.name}", ""]
    args.output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "series_count": len(snapshot["series"]),
        "verified_symbols": [s["symbol"] for s in snapshot["series"] if s["session_verified"]],
        "missing_symbols": missing, "fed_documents": len(documents)}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
