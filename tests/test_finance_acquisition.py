"""Offline contract, isolation, safety and parsing tests with synthetic source responses."""

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest

from policy_impact.domains.finance import acquisition as acquisition
from policy_impact.domains.finance.schemas import FinanceSnapshot, Period
from policy_impact.source_connectors.http_client import FetchResponse


def response(url, body, content_type="text/html; charset=utf-8"):
    body = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResponse(url, 200, content_type, body, "2024-06-05T09:00:00+00:00",
                         hashlib.sha256(body).hexdigest())


def tx(day, close):
    return [day, str(close), str(close), str(close), str(close), "10"]


def tencent(url, rows, symbol="sh000001"):
    return response(url, json.dumps({"code": 0, "data": {symbol: {"day": rows}}}))


@pytest.fixture
def config(monkeypatch):
    config = acquisition._load_config()
    monkeypatch.setattr(acquisition, "_load_config", lambda: deepcopy(config))
    monkeypatch.setattr(acquisition, "fetch_url", lambda *a, **k: pytest.fail("Unexpected live HTTP"))
    return config


def market_result(config):
    return acquisition._collect_market("sh000001", config,
                                       Period(start="2024-06-03", end="2024-06-04"),
                                       datetime(2024, 6, 5, 9, tzinfo=timezone.utc),
                                       acquisition._Budget(config["limits"]))


def test_default_symbols_strict_json_contract_and_nearest_baseline(config, monkeypatch):
    config["document_sources"] = []
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        param = parse_qs(urlparse(url).query)["param"][0].split(",")
        assert date.fromisoformat(param[2]) < date(2024, 6, 3)
        return tencent(url, [tx("2024-05-30", 90), tx("2024-05-31", 100),
                             tx("2024-06-03", 110), tx("2024-06-04", 121),
                             tx("2024-06-05", 999)], param[0])

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    result = acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04")
    assert [s["symbol"] for s in result["series"]] == ["sh000001", "sh000300", "sz399006"]
    assert len(calls) == 3
    for series in result["series"]:
        assert [b["date"] for b in series["bars"]] == ["2024-05-31", "2024-06-03", "2024-06-04"]
    assert FinanceSnapshot.model_validate(result).model_dump(mode="json") == result
    json.dumps(result, allow_nan=False)
    assert all(k["retries"] == 0 and 0 < k["timeout_seconds"] <= 8 and k["max_bytes"] <= 2_000_000
               and k["allowed_domains"] for _, k in calls)
    assert all("Third-party" in s["detail"] for s in result["source_statuses"])


def test_fallback_to_sina_does_not_splice_partial_tencent_prices(config, monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if "web.ifzq" in url:
            return tencent(url, [tx("2024-06-03", 200), tx("2024-06-04", 210)])
        if "qq.com" in url:
            raise TimeoutError("synthetic timeout")
        rows = [{"day": day, "open": close, "close": close, "high": close, "low": close}
                for day, close in [("2024-05-31", 100), ("2024-06-03", 110), ("2024-06-04", 121)]]
        return response(url, json.dumps(rows), "application/json; charset=gbk")

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    series, statuses, warnings = market_result(config)
    assert len(calls) == 3 and series.provider == "Sina Finance"
    assert [b.close for b in series.bars] == [100, 110, 121]
    assert [s.status for s in statuses] == ["partial", "error", "ok"]
    assert "\u672a\u62fc\u63a5" in " ".join(warnings)


def test_all_sources_fail_attempts_alternatives_returns_valid_empty_snapshot(config, monkeypatch):
    calls = []

    def fail(url, **kwargs):
        calls.append(url)
        raise TimeoutError("synthetic network outage")

    monkeypatch.setattr(acquisition, "fetch_url", fail)
    result = acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04")
    assert result["series"] == result["documents"] == []
    assert len(result["source_statuses"]) == 13
    assert all(s["status"] == "error" for s in result["source_statuses"])
    assert any("finance.sina.com.cn" in u for u in calls)
    assert any("bis.org" in u for u in calls)
    assert any("2024-speeches.htm" in u for u in calls)
    assert len(calls) < 20
    FinanceSnapshot.model_validate(result)


def test_empty_symbols_means_articles_only_and_duplicates_not_refetched(config, monkeypatch):
    config["document_sources"] = []
    assert acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04", symbols=[])["series"] == []
    calls = []
    monkeypatch.setattr(acquisition, "fetch_url", lambda url, **k: (
        calls.append(url) or tencent(url, [tx("2024-05-31", 100), tx("2024-06-04", 121)])))
    result = acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04",
                                                  symbols=["sh000001", "sh000001"])
    assert len(calls) == len(result["series"]) == 1


@pytest.mark.parametrize("kwargs", [
    {"start": "2024-6-3", "end": "2024-06-04"},
    {"start": "2024-02-30", "end": "2024-06-04"},
    {"start": "2024-06-04", "end": "2024-06-03"},
    {"start": "1989-01-01", "end": "1989-01-02"},
    {"start": "2020-01-01", "end": "2024-06-04"},
    {"start": "2024-06-03", "end": "2024-06-04", "symbols": ["sh600519"]},
    {"start": "2024-06-03", "end": "2024-06-04", "symbols": ["http://localhost/"]},
    {"start": "2024-06-03", "end": "2024-06-04", "symbols": "sh000001"},
    {"start": "2024-06-03", "end": "2024-06-04", "symbols": [None]},
])
def test_bad_inputs_rejected_before_network(config, kwargs):
    with pytest.raises(ValueError):
        acquisition.collect_finance_snapshot(**kwargs)


@pytest.mark.parametrize("payload", [
    {"code": 1, "data": {}}, {"code": 0, "data": {"wrong": {"day": []}}},
    {"code": 0, "data": {"sh000001": {"qfqday": [tx("2024-06-03", 100)]}}},
    {"code": 0, "data": {"sh000001": {"day": [["2024-06-03"]]}}},
    {"code": 0, "data": {"sh000001": {"day": [tx("2024-06-03", 0)]}}},
    {"code": 0, "data": {"sh000001": {"day": [tx("2024-06-03", "NaN")]}}},
    {"code": 0, "data": {"sh000001": {"day": [["2024-06-03", 100, 110, 90, 95]]}}},
])
def test_malformed_or_adjusted_or_nonfinite_market_data_rejected(config, payload):
    with pytest.raises(ValueError):
        acquisition._parse_market(response("https://example.com", json.dumps(payload)),
                                  config["market_sources"][0], "sh000001")


def test_duplicate_bars_normalized_but_conflicting_date_rejected(config):
    source = config["market_sources"][0]
    rows = [tx("2024-06-04", 121), tx("2024-06-03", 110), tx("2024-06-03", 110)]
    assert len(acquisition._parse_market(tencent("https://example.com", rows), source, "sh000001")) == 2
    rows.append(tx("2024-06-03", 111))
    with pytest.raises(ValueError, match="conflicting"):
        acquisition._parse_market(tencent("https://example.com", rows), source, "sh000001")


def test_current_day_before_close_and_future_bars_are_excluded(config, monkeypatch):
    monkeypatch.setattr(acquisition, "fetch_url", lambda url, **k: tencent(
        url, [tx("2024-05-31", 100), tx("2024-06-03", 110), tx("2024-06-04", 121),
              tx("2024-06-05", 999)]))
    series, _, _ = acquisition._collect_market(
        "sh000001", config, Period(start="2024-06-03", end="2024-06-05"),
        datetime(2024, 6, 4, 6, 59, tzinfo=timezone.utc), acquisition._Budget(config["limits"]))
    assert [bar.date.isoformat() for bar in series.bars] == ["2024-05-31", "2024-06-03"]


def test_long_range_splits_primary_not_rolling_mirror(config):
    urls = acquisition._market_urls(config["market_sources"][0], "sh000001",
                                    date(2022, 1, 1), date(2024, 6, 4))
    assert len(urls) == 3
    windows = [parse_qs(urlparse(u).query)["param"][0].split(",")[2:4] for u in urls]
    assert windows[0][0] == "2022-01-01" and windows[-1][1] == "2024-06-04"
    assert len(acquisition._market_urls(config["market_sources"][1], "sh000001",
                                        date(2022, 1, 1), date(2024, 6, 4))) == 1


def test_request_budget_prevents_more_network_calls(config, monkeypatch):
    config["limits"]["max_requests"] = 1
    calls = []
    monkeypatch.setattr(acquisition, "fetch_url", lambda url, **k: calls.append(url) or response(url, "{}"))
    budget = acquisition._Budget(config["limits"])
    budget.fetch("https://example.com", config["market_sources"][0])
    with pytest.raises(TimeoutError, match="budget"):
        budget.fetch("https://example.com", config["market_sources"][0])
    assert len(calls) == 1


def test_shared_concurrency_is_bounded(config, monkeypatch):
    lock, counters = threading.Lock(), {"active": 0, "peak": 0}

    def fetch(url, **kwargs):
        with lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        time.sleep(0.02)
        with lock:
            counters["active"] -= 1
        raise TimeoutError("synthetic")

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04")
    assert 1 < counters["peak"] <= 4
    assert counters["active"] == 0


def article(title="Official release", published="2024/06/03 10:00", text=None):
    text = text or ("Public macroeconomic release with figures and definitions. " * 20 + "FINAL_MARKER")
    return (f'<html><head><meta name="ArticleTitle" content="{title}">'
            f'<meta name="PubDate" content="{published}"></head><body><nav>Navigation</nav>'
            f'<div class="TRS_UEDITOR"><p>{text}</p><script>DO_NOT_KEEP</script>'
            '<table><tr><td>GDP</td><td>5.0%</td></tr></table></div></body></html>')


def test_official_article_full_text_hash_date_and_status(config, monkeypatch):
    source = config["document_sources"][0]
    url = source["url"] + "202406/t20240603_1.html"

    def fetch(request_url, **kwargs):
        assert kwargs["allowed_domains"] == ["www.stats.gov.cn"]
        if request_url == source["url"]:
            return response(request_url, '<a href="./202406/t20240603_1.html">Official release</a>')
        assert request_url == url
        return response(url, article())

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, status = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-04"),
                                                  acquisition._Budget(config["limits"]))
    doc, = docs
    assert doc.published_at == date(2024, 6, 3)
    assert "FINAL_MARKER" in doc.text and "5.0%" in doc.text
    assert "DO_NOT_KEEP" not in doc.text and "Navigation" not in doc.text
    assert doc.content_hash == hashlib.sha256(doc.text.encode()).hexdigest()
    assert status.status == "partial" and status.count == 1
    assert "First-party" in status.detail and "bounded" in status.detail


def test_article_needs_real_publication_date_not_url_hint(config):
    source = config["document_sources"][0]
    with pytest.raises(ValueError, match="publication date"):
        acquisition._document(response(source["url"], article(published="unknown")), source,
                              {"title": "hint", "date": date(2024, 6, 3), "date_verified": False})


def test_listing_links_outside_allowlist_and_duplicate_fragments_rejected(config):
    source = config["document_sources"][0]
    listing = ('<a href="http://127.0.0.1/t20240603_1.html">Bad</a>'
               '<a href="https://www.stats.gov.cn.evil.com/t20240603_2.html">Bad</a>'
               '<a href="./202406/t20240603_1.html">Good</a>'
               '<a href="./202406/t20240603_1.html#anchor">Good duplicate</a>')
    candidates = acquisition._listing_candidates(response(source["url"], listing), source)
    assert len(candidates) == 1 and candidates[0]["url"].startswith(source["url"])


def test_rdf_and_rss_dates_without_xml_declaration(config):
    fed = config["document_sources"][2]
    xml = ('<rss><channel><item><title>Speech</title><link>https://www.federalreserve.gov/newsevents/speech/x20240603a.htm</link>'
           '<pubDate>Mon, 03 Jun 2024 10:00:00 -0400</pubDate></item></channel></rss>')
    candidates = acquisition._listing_candidates(response(fed["url"], xml, "text/xml"), fed)
    assert candidates[0]["date"] == date(2024, 6, 3) and candidates[0]["date_verified"]
    bis = config["document_sources"][3]
    xml = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
           'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<item><title>Speech</title><link>https://www.bis.org/speeches/test</link>'
           '<dc:date>2024-06-03T10:00:00Z</dc:date></item></rdf:RDF>')
    candidates = acquisition._listing_candidates(response(bis["url"], xml, "text/xml"), bis)
    assert candidates[0]["date"] == date(2024, 6, 3)


def test_bis_pdf_excerpt_not_mislabeled_full_text_even_when_long(config):
    source = config["document_sources"][3]
    html = ('<article><h1>Speech</h1><p>' + "Long but incomplete PDF excerpt. " * 150
            + '</p><a href="/speeches/test.pdf">PDF (7 pages)</a></article>')
    with pytest.raises(ValueError, match="completeness unverified"):
        acquisition._document(response("https://www.bis.org/speeches/test", html), source,
                              {"title": "Speech", "date": date(2024, 6, 3), "date_verified": True})


def test_invalid_encoding_is_not_replaced_with_garbled_text(config):
    with pytest.raises(ValueError, match="data loss"):
        acquisition._decode(response("https://example.com", b"\xff\xff"))
    text = "\u56fd\u5bb6\u7edf\u8ba1\u5c40"
    assert acquisition._decode(response("https://example.com", text.encode("gbk"),
                                        "text/html; charset=gbk")) == text


def test_article_failure_preserved_and_next_candidate_tried(config, monkeypatch):
    source = config["document_sources"][0]

    def fetch(url, **kwargs):
        if url == source["url"]:
            return response(url, '<a href="./202406/t20240604_1.html">A</a>'
                                 '<a href="./202406/t20240603_1.html">B</a>')
        if "t20240604" in url:
            raise TimeoutError("synthetic article failure")
        return response(url, article())

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, status = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-04"),
                                                  acquisition._Budget(config["limits"]))
    assert len(docs) == 1 and status.status == "partial"
    assert "synthetic article failure" in status.detail


def test_publication_metadata_overrides_url_date_without_boundary_loss(config, monkeypatch):
    source = config["document_sources"][0]

    def fetch(url, **kwargs):
        if url == source["url"]:
            return response(url, '<a href="./202406/t20240604_1.html">Dated one day later in URL</a>')
        return response(url, article(published="2024/06/03 10:00"))

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, _ = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-03"),
                                             acquisition._Budget(config["limits"]))
    assert len(docs) == 1 and docs[0].published_at == date(2024, 6, 3)


def test_out_of_period_article_not_used_as_current_evidence(config, monkeypatch):
    source = config["document_sources"][0]

    def fetch(url, **kwargs):
        if url == source["url"]:
            return response(url, '<a href="./202406/t20240603_1.html">Misleading URL</a>')
        return response(url, article(published="2023/06/03 10:00"))

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, status = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-04"),
                                                  acquisition._Budget(config["limits"]))
    assert docs == [] and "actual publication date is outside" in status.detail


def test_document_cap_bounds_requests_and_preserves_listing_order_on_ties(config, monkeypatch):
    source, calls = config["document_sources"][0], []

    def fetch(url, **kwargs):
        calls.append(url)
        if url == source["url"]:
            return response(url, "".join(f'<a href="./202406/t20240603_{i}.html">Article {i}</a>'
                                         for i in range(30)))
        return response(url, article())

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, status = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-04"),
                                                  acquisition._Budget(config["limits"]))
    assert len(docs) == 4 and len(calls) == 5
    assert docs[0].url.endswith("_0.html")
    assert json.loads(status.detail)["in_period_candidates"] == 30


def test_expired_budget_starts_no_request(config):
    budget = acquisition._Budget(config["limits"])
    budget.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError, match="budget"):
        budget.fetch(config["market_sources"][0]["url"], config["market_sources"][0])


def test_sina_null_is_empty_and_long_holiday_baseline_is_retained(config, monkeypatch):
    assert acquisition._parse_market(response("https://example.com", "null"),
                                      config["market_sources"][2], "sh000001") == []
    monkeypatch.setattr(acquisition, "fetch_url", lambda url, **k: tencent(
        url, [tx("2024-02-08", 100), tx("2024-02-19", 110)]))
    series, _, _ = acquisition._collect_market(
        "sh000001", config, Period(start="2024-02-15", end="2024-02-19"),
        datetime(2024, 2, 20, 9, tzinfo=timezone.utc), acquisition._Budget(config["limits"]))
    assert series.bars[0].date == date(2024, 2, 8)


def test_partial_fallback_is_kept_when_all_alternatives_fail(config, monkeypatch):
    def fetch(url, **kwargs):
        if "web.ifzq" in url:
            return tencent(url, [tx("2024-06-03", 100), tx("2024-06-04", 110)])
        raise TimeoutError("synthetic fallback failure")

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    series, statuses, warnings = market_result(config)
    assert series.provider == "Tencent Finance"
    assert [s.status for s in statuses] == ["partial", "error", "error"]
    assert "\u7f3a\u5c11\u533a\u95f4\u5f00\u59cb\u65e5\u524d\u7684\u6536\u76d8\u57fa\u51c6" in " ".join(warnings)


def test_fed_annual_archive_can_supply_history_when_current_feed_has_no_match(config, monkeypatch):
    source, calls = config["document_sources"][2], []

    def fetch(url, **kwargs):
        calls.append(url)
        if url.endswith(".xml"):
            return response(url, "<rss><channel/></rss>", "application/xml")
        if url.endswith("2024-speeches.htm"):
            return response(url, '<a href="/newsevents/speech/powell20240603a.htm">Historical speech</a>')
        return response(url, '<p class="article__time">June 03, 2024</p><div id="article">'
                             + "Historical speech body, not a present-day substitute. " * 30 + "</div>")

    monkeypatch.setattr(acquisition, "fetch_url", fetch)
    docs, status = acquisition._collect_documents(source, config, Period(start="2024-06-03", end="2024-06-04"),
                                                  acquisition._Budget(config["limits"]))
    assert len(docs) == 1 and docs[0].published_at == date(2024, 6, 3)
    assert len(calls) == 3 and status.status == "partial"


def test_all_user_facing_warnings_remain_chinese_on_total_failure(config, monkeypatch):
    def fail(url, **kwargs):
        raise TimeoutError("synthetic outage")

    monkeypatch.setattr(acquisition, "fetch_url", fail)
    result = acquisition.collect_finance_snapshot(start="2024-06-03", end="2024-06-04")
    assert result["warnings"]
    assert all(re.search(r"[\u4e00-\u9fff]", warning) for warning in result["warnings"])
