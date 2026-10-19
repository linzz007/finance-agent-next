"""Contracts for independently callable market and public web research tools."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.market_research import MarketResearch
from policy_impact.domains.finance.web_research import WebResearch
from policy_impact.source_connectors.http_client import FetchResponse


def test_market_tools_keep_acquisition_and_calculation_separate(monkeypatch):
    from policy_impact.domains.finance import acquisition, metrics

    calls = []
    snapshot = {
        "snapshot_id": "finance_test", "collected_at": "2026-10-01T00:00:00+00:00",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "session_mode": "explicit_period", "warnings": [], "source_statuses": [],
        "series": [{"symbol": "spx", "provider": "test", "unit": "points",
                    "price_basis": "price_index", "source_url": "https://example.com/spx",
                    "retrieved_at": "2026-10-01T00:00:00+00:00", "session_date": "2026-09-30",
                    "session_verified": True, "bars": [{"date": "2026-09-30", "close": 100}]}],
    }
    monkeypatch.setattr(acquisition, "collect_finance_snapshot", lambda **kwargs: calls.append(kwargs) or snapshot)
    monkeypatch.setattr(metrics, "compute_market_metrics", lambda data: [{
        "metric_id": "metric_spx", "symbol": "spx", "name": "S&P 500", "change_pct": 2.5,
        "daily_changes": [{"date": "2026-09-30"}],
    }])
    tool = MarketResearch(EvidenceLedger())
    result = tool.snapshot(["spx"], "2026-09-01", "2026-09-30")
    assert calls[0]["include_documents"] is False
    assert result["observed_symbols"] == ["spx"]
    assert "metrics" not in result
    metric = tool.metrics(result["snapshot_id"])["metrics"][0]
    assert metric["change_pct"] == 2.5 and metric["evidence_id"].startswith("evidence_")
    assert "daily_changes" not in metric
    series = tool.series(result["snapshot_id"], "spx")
    assert series["bars"][0]["close"] == 100
    assert series["evidence_id"].startswith("evidence_")
    assert tool.ledger.get(series["evidence_id"]).source_type == "market_bar_series"
    evidence_excerpt = tool.ledger.get(series["evidence_id"]).excerpt
    assert '"close": 100' in evidence_excerpt
    assert '"bars"' in evidence_excerpt
    assert "evidence_id" not in tool.series(result["snapshot_id"], "spx", offset=1)
    with pytest.raises(ValueError):
        tool.metrics("finance_other_turn")


def test_public_web_search_and_open_keep_leads_separate_from_evidence(monkeypatch):
    from policy_impact.domains.finance import web_research

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            return [{"title": "Fed source", "href": "https://example.com/article",
                     "body": "An unverified search snippet"}]

    monkeypatch.setattr("ddgs.DDGS", Searcher)
    monkeypatch.setattr(web_research, "fetch_url", lambda *args, **kwargs: FetchResponse(
        url="https://example.com/article", status=200, content_type="text/html",
        body=b"<html><head><title>Fed source</title></head><body><article><p>Test evidence about rates and prices.</p></article></body></html>",
        retrieved_at=datetime.now(timezone.utc).isoformat(), sha256="abc123",
    ))
    ledger = EvidenceLedger()
    web = WebResearch(ledger)
    lead = web.search("Fed rates", limit=1)
    assert lead["status"] == "ok" and ledger.evidence_ids == ()
    page = web.open(lead["results"][0]["url"])
    assert "rates and prices" in page["text"]
    assert page["evidence_id"] in ledger.evidence_ids
    assert ledger.bundle()["evidence"][0]["provenance"] == "retrieved_page"


def test_public_web_open_falls_back_when_extractor_only_returns_disclaimer(monkeypatch):
    from policy_impact.domains.finance import web_research
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: json.dumps({
        "title": "Gold Demand Trends", "text": "Important information and disclaimers",
        "date": "2026-07-30",
    }))
    monkeypatch.setattr(web_research, "fetch_url", lambda *args, **kwargs: FetchResponse(
        url="https://www.gold.org/research/q2", status=200, content_type="text/html",
        body=(b"<html><body><article><p>Central banks bought 289 tonnes in Q2, "
              b"up 62 percent year-on-year.</p></article>"
              b"<table><tr><th>Q2'25</th><th>Q2'26</th><th>q/q %<br>change</th>"
              b"<th>y/y %<br>change</th></tr>"
              b"<tr><td>177.9</td><td>288.9</td><td>411</td><td>62</td></tr></table>"
              b"<footer>Disclaimer</footer></body></html>"),
        retrieved_at="2026-10-02T00:00:00+00:00", sha256="abc123",
    ))
    ledger = EvidenceLedger()
    result = WebResearch(ledger).open("https://www.gold.org/research/q2")
    assert result["extraction_strategy"] == "html_fallback"
    assert "289 tonnes" in result["text"] and "62 percent" in result["text"]
    assert "Q2'25 | Q2'26 | q/q % change | y/y % change" in result["text"]
    assert "177.9 | 288.9 | 411 | 62" in result["text"]
    assert ledger.bundle()["evidence"][0]["metadata"]["extraction_strategy"] == "html_fallback"


def test_public_web_open_keeps_normal_extraction(monkeypatch):
    from policy_impact.domains.finance import web_research
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: json.dumps({
        "text": "Official release: Q2 central bank demand was 289 tonnes.",
    }))
    monkeypatch.setattr(web_research, "fetch_url", lambda *args, **kwargs: FetchResponse(
        url="https://www.gold.org/research/q2", status=200, content_type="text/html",
        body=b"<html><body><p>Short HTML content.</p></body></html>",
        retrieved_at="2026-10-02T00:00:00+00:00", sha256="abc123",
    ))
    result = WebResearch(EvidenceLedger()).open("https://www.gold.org/research/q2")
    assert result["extraction_strategy"] == "trafilatura"
    assert "Official release" in result["text"]


def test_public_web_open_recovers_content_links_from_navigation_page(monkeypatch):
    from policy_impact.domains.finance import web_research
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: json.dumps({
        "text": "An official website of the United States Government",
    }))
    monkeypatch.setattr(web_research, "fetch_url", lambda *args, **kwargs: FetchResponse(
        url="https://www.federalreserve.gov/monetarypolicy/meeting.htm",
        status=200, content_type="text/html",
        body=(b'<html><body><nav><a href="/index.htm">Home</a></nav>'
              b'<div id="content"><h1>Meeting materials</h1><p>FOMC statement: '
              b'<a href="/newsevents/pressreleases/statement.htm">HTML</a></p>'
              b'<p>Projections: <a href="/monetarypolicy/projections.htm">HTML</a></p>'
              b'</div></body></html>'),
        retrieved_at="2026-10-12T00:00:00+00:00", sha256="abc123",
    ))
    result = WebResearch(EvidenceLedger()).open(
        "https://www.federalreserve.gov/monetarypolicy/meeting.htm"
    )
    assert result["extraction_strategy"] == "html_fallback"
    assert "Meeting materials" in result["text"]
    assert [link["url"] for link in result["links"]] == [
        "https://www.federalreserve.gov/newsevents/pressreleases/statement.htm",
        "https://www.federalreserve.gov/monetarypolicy/projections.htm",
    ]
    assert result["links"][0]["context"] == "FOMC statement:"


def test_public_web_open_can_continue_long_page_with_stable_source_lines(monkeypatch):
    from policy_impact.domains.finance import web_research

    page = "\n".join(f"Official projection line {number} " + "x" * 900
                     for number in range(1, 61)).encode()
    monkeypatch.setattr(web_research, "fetch_url", lambda *args, **kwargs: FetchResponse(
        url="https://www.federalreserve.gov/projections.txt", status=200,
        content_type="text/plain", body=page,
        retrieved_at="2026-10-12T00:00:00+00:00", sha256="abc123",
    ))
    ledger = EvidenceLedger()
    web = WebResearch(ledger)
    first = web.open("https://www.federalreserve.gov/projections.txt")
    assert first["truncated"] and first["next_start_line"] > 1
    second = web.open(first["url"], start_line=first["next_start_line"])
    assert second["line_start"] == first["line_end"] + 1
    assert second["text"].startswith(f"{second['line_start']}: Official projection line")
    assert second["evidence_id"] != first["evidence_id"]
    with pytest.raises(ValueError, match="line count"):
        web.open(first["url"], start_line=61)


def test_public_web_search_skips_ad_redirects_and_tries_next_backend(monkeypatch):
    calls = []

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            calls.append(kwargs["backend"])
            if kwargs["backend"] == "bing":
                return [{"title": "Ad", "href": "https://www.bing.com/aclick?u=ad"}]
            return [{"title": "Official page", "href": "https://www.federalreserve.gov/monetarypolicy/fomc.htm"}]

    monkeypatch.setattr("ddgs.DDGS", Searcher)
    result = WebResearch(EvidenceLedger()).search("Federal Reserve", limit=2)
    assert calls == ["bing", "auto"]
    assert result["status"] == "ok"
    assert len(result["results"]) == 1
    assert result["results"][0]["url"].startswith("https://www.federalreserve.gov/")


def test_public_web_open_rejects_local_targets():
    web = WebResearch(EvidenceLedger())
    with pytest.raises(ValueError):
        web.open("http://127.0.0.1/secrets")


def test_public_web_pdf_returns_recoverable_non_evidence(monkeypatch):
    from policy_impact.domains.finance import web_research

    monkeypatch.setattr(web_research, "fetch_url", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("PDF should not be fetched by web.open")
    ))
    ledger = EvidenceLedger()
    result = WebResearch(ledger).open(
        "https://www.federalreserve.gov/monetarypolicy/files/monetary20260916a1.pdf"
    )
    assert result["status"] == "unsupported_format"
    assert not ledger.bundle()["evidence"]
