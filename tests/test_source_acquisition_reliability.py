"""Mock-only regressions for SD03 acquisition and article extraction failures."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import ssl
from threading import Event
from urllib.error import HTTPError, URLError

import pytest

from policy_impact.source_connectors import acquisition_cache as cache
from policy_impact.source_connectors import ingestion as sources
from policy_impact.source_connectors.article import extract_article
from policy_impact.source_connectors.discovery import clean_candidates, normalize_plan, query_urls
from policy_impact.source_connectors.http_client import FetchResponse


@pytest.fixture(autouse=True)
def isolated_acquisition(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_CACHE", OrderedDict())
    monkeypatch.setattr(cache, "_RUNNING", {})
    monkeypatch.setattr(sources, "_ARXIV_NEXT_REQUEST", 0)
    monkeypatch.setattr(sources, "project_root", lambda: tmp_path)


def response(url, body):
    return FetchResponse(url, 200, "text/html; charset=utf-8", body.encode(),
                         "2026-09-10T07:00:00+00:00", "sha")


def release_source():
    return {"source_id": "example_releases", "name": "Example Releases", "type": "rss_atom", "level": "L2",
            "url": "https://github.com/org/repo/releases.atom", "allowed_domains": ["github.com"],
            "release_api_url": "https://api.github.com/repos/org/repo/releases"}


def test_sd03_api_403_does_not_discard_ten_atom_rows_with_date_window(monkeypatch):
    source = release_source()
    monkeypatch.setattr(sources, "load_source_config", lambda: {"news_sources": [source]})
    calls = []
    entries = "".join(f'<entry><title>v1.{i}</title><updated>2026-09-09T10:00:00Z</updated>'
                      f'<link href="https://github.com/org/repo/releases/tag/v1.{i}"/>'
                      '<content>Verified release notes supplied by the project.</content></entry>' for i in range(10))
    def fetch(url, **kwargs):
        calls.append(url)
        if "api.github.com" in url:
            raise HTTPError(url, 403, "rate limit exceeded", {}, None)
        return response(url, '<feed xmlns="http://www.w3.org/2005/Atom">' + entries + '</feed>')
    monkeypatch.setattr(sources, "fetch_url", fetch)
    result = sources.collect_news_sources(discovery_plan={"date_from": "2026-09-03", "date_to": "2026-09-10"}, persist=False)
    status = result["source_statuses"][0]
    assert len(calls) == 2 and len(result["items"]) == 10
    assert status["status"] == "partial" and status["raw_count"] == status["item_count"] == 10
    assert "403" in status["error"] and result["collection_partial"]
    assert all(item["published_at"] == "" and item["published_at_basis"] == "unknown" for item in result["items"])
    assert all(item["collection_role"] == "undated_source_lead" and item["date_window_status"] == "unverified" for item in result["items"])
    assert result["cleaning_diagnostics"]["undated_lead_count"] == 10
    second = sources.collect_news_sources(discovery_plan={"search_terms": ["another public topic"]}, persist=False, force_refresh=True)
    assert len(calls) == 2 and second["source_statuses"][0]["acquisition_cache_hit"]
    assert second["source_statuses"][0]["status"] == "partial"
    assert second["source_statuses"][0]["request_count"] == 0
    assert second["source_statuses"][0]["acquisition_request_count"] == 2


def test_both_release_endpoints_fail_retains_both_errors_and_true_status(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        raise RuntimeError("endpoint unavailable")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    rows, status = sources._collect_news_source(release_source())
    assert rows == [] and status["status"] == "error"
    assert status["request_count"] == 2 and len(status["errors"]) == 2
    _, cached = sources._collect_news_source(release_source())
    assert len(calls) == 2 and cached["status"] == "error" and len(cached["errors"]) == 2


def test_shared_static_cache_applies_each_plans_time_filter_afterward(monkeypatch):
    source = {"source_id": "shared_feed", "name": "Shared", "type": "rss_atom", "url": "https://example.com/rss", "allowed_domains": ["example.com"]}
    body = '<rss><channel>' + ''.join(f'<item><title>Article {month}</title><link>https://example.com/{month}</link><pubDate>2026-{month}-01</pubDate><description>Original evidence.</description></item>' for month in ("07", "08")) + '</channel></rss>'
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return response(url, body)
    monkeypatch.setattr(sources, "fetch_url", fetch)
    july, first = sources._collect_news_source(source, {"date_from": "2026-07-01", "date_to": "2026-07-31"})
    august, second = sources._collect_news_source(source, {"date_from": "2026-08-01", "date_to": "2026-08-31", "profile_domain": "other_wiki"})
    assert len(calls) == 1 and not first["acquisition_cache_hit"] and second["acquisition_cache_hit"]
    assert july[0]["url"].endswith("/07") and august[0]["url"].endswith("/08")
    sources._collect_news_source({**source, "url": "https://example.com/other-rss"})
    assert len(calls) == 2


def test_cache_expiry_and_copy_isolation(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    calls = []
    def load():
        calls.append(1)
        return [{"title": "Original"}]
    first, hit = cache.cached_acquisition("key", load)
    first[0]["title"] = "Caller mutation"
    now[0] = 299
    second, hit = cache.cached_acquisition("key", load)
    assert hit and second[0]["title"] == "Original" and len(calls) == 1
    now[0] = 301
    _, hit = cache.cached_acquisition("key", load)
    assert not hit and len(calls) == 2


@pytest.fixture
def acquisition_clock(monkeypatch):
    elapsed = [0.0]
    started = datetime.now(timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = started + timedelta(seconds=elapsed[0])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(cache.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(sources, "datetime", Clock)
    return elapsed


@pytest.mark.parametrize("failure", [
    ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
    URLError(ssl.SSLEOFError(8, "SSL EOF")),
    TimeoutError("timed out"), ConnectionResetError("connection reset"),
    HTTPError("https://example.com/rss", 503, "unavailable", {}, None),
], ids=["ssl_eof", "wrapped_ssl_eof", "timeout", "connection_reset", "http_503"])
def test_transient_static_failure_has_short_cooldown_then_success_cache(monkeypatch, acquisition_clock, failure):
    source = {"source_id": "news", "type": "rss_atom", "url": "https://example.com/rss",
              "allowed_domains": ["example.com"]}
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise failure
        return response(url, "<rss><channel/></rss>")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    _, first = sources._collect_news_source(source)
    assert first["status"] == "error" and first["acquisition_cache_ttl_seconds"] == 30
    assert first["errors"][0]["transient"]
    acquisition_clock[0] = 29
    _, second = sources._collect_news_source(source)
    assert second["status"] == "error" and second["request_count"] == 0 and len(calls) == 1
    assert second["acquisition_cache_expires_at"] == first["acquisition_cache_expires_at"]
    acquisition_clock[0] = 31
    _, recovered = sources._collect_news_source(source)
    assert recovered["status"] == "ok" and recovered["acquisition_cache_ttl_seconds"] == 300
    assert len(calls) == 2 and not recovered["acquisition_cache_hit"]
    acquisition_clock[0] = 330
    _, cached = sources._collect_news_source(source)
    assert cached["status"] == "ok" and cached["acquisition_cache_hit"] and len(calls) == 2
    acquisition_clock[0] = 332
    sources._collect_news_source(source)
    assert len(calls) == 3


@pytest.mark.parametrize("code", [403, 429])
def test_rate_limit_backoff_is_not_shortened_by_transient_fallback(monkeypatch, acquisition_clock, code):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if "api.github.com" in url:
            raise HTTPError(url, code, "rate limit exceeded", {}, None)
        raise ssl.SSLEOFError(8, "SSL EOF")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    _, first = sources._collect_news_source(release_source())
    assert first["status"] == "error" and first["acquisition_cache_ttl_seconds"] == 300
    acquisition_clock[0] = 299
    _, cached = sources._collect_news_source(release_source())
    assert cached["status"] == "error" and len(calls) == 2 and cached["request_count"] == 0
    acquisition_clock[0] = 301
    sources._collect_news_source(release_source())
    assert len(calls) == 4


def test_partial_atom_success_retains_long_cache_after_transient_api_error(monkeypatch, acquisition_clock):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if "api.github.com" in url:
            raise TimeoutError("timed out")
        return response(url, '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v1</title>'
                        '<link href="https://github.com/org/repo/releases/tag/v1"/>'
                        '<content>Release notes</content></entry></feed>')
    monkeypatch.setattr(sources, "fetch_url", fetch)
    rows, first = sources._collect_news_source(release_source())
    assert len(rows) == 1 and first["status"] == "partial" and first["acquisition_cache_ttl_seconds"] == 300
    acquisition_clock[0] = 299
    _, cached = sources._collect_news_source(release_source())
    assert cached["status"] == "partial" and cached["errors"] and len(calls) == 2


def test_partial_plan_snapshot_does_not_extend_transient_source_expiry(monkeypatch, acquisition_clock):
    config = {"news_sources": [
        {"source_id": name, "type": "rss_atom", "url": f"https://example.com/{name}",
         "allowed_domains": ["example.com"]} for name in ("steady", "transient")
    ]}
    calls = {"steady": 0, "transient": 0}
    def fetch(url, **kwargs):
        name = url.rsplit("/", 1)[-1]
        calls[name] += 1
        if name == "transient" and calls[name] == 1:
            raise ssl.SSLEOFError(8, "SSL EOF")
        return response(url, f'<rss><channel><item><title>{name} article</title>'
                        f'<link>https://example.com/articles/{name}</link>'
                        '<pubDate>2026-09-04</pubDate></item></channel></rss>')
    monkeypatch.setattr(sources, "fetch_url", fetch)
    monkeypatch.setattr(sources, "load_source_config", lambda: config)
    first = sources.collect_news_sources()
    assert first["collection_partial"] and len(first["items"]) == 1
    acquisition_clock[0] = 20
    assert sources.collect_news_sources()["cache_hit"]
    other_plan = {"search_terms": ["another public topic"]}
    second = sources.collect_news_sources(discovery_plan=other_plan)
    assert second["source_statuses"][1]["acquisition_cache_expires_at"] == first["source_statuses"][1]["acquisition_cache_expires_at"]
    assert calls == {"steady": 1, "transient": 1}
    acquisition_clock[0] = 31
    recovered = sources.collect_news_sources(discovery_plan=other_plan)
    assert not recovered["cache_hit"] and not recovered["collection_partial"]
    assert len(recovered["items"]) == 2 and calls == {"steady": 1, "transient": 2}
    assert sources.collect_news_sources(discovery_plan=other_plan)["cache_hit"]


def test_cache_single_flight_coalesces_concurrent_source_requests():
    entered, release = Event(), Event()
    calls = []
    def load():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"source": "public"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.cached_acquisition, "same", load)
        assert entered.wait(3)
        second = pool.submit(cache.cached_acquisition, "same", load)
        release.set()
        assert first.result(timeout=3)[0] == second.result(timeout=3)[0]
    assert len(calls) == 1


def test_article_region_excludes_chrome_and_preserves_inline_quotes():
    quote = 'The term "financial data" includes documented measurements and auditable source records.'
    html = ('<html><head><title>Page title</title></head><body><nav>Global navigation</nav>'
            '<h1>Regulation title</h1><div id="zoom"><p>The term <em>"financial data"</em> includes documented measurements and auditable source records.</p>'
            + '<p>Second official paragraph with unmodified evidence. </p>' * 6
            + '<div class="share-tools">Share on social media</div><footer>Inner footer</footer></div>'
            '<footer>Copyright and site links</footer></body></html>')
    text, selector = extract_article(html)
    assert selector == "#zoom" and quote in text
    assert text.startswith(quote) and "\nSecond official paragraph" in text
    assert all(unwanted not in text for unwanted in ("navigation", "footer", "Copyright", "Page title", "Regulation title", "Share"))


@pytest.mark.parametrize("wrapper", [
    '<div class="TRS_Editor">{body}</div>', '<article>{body}</article>',
    '<section itemprop="articleBody">{body}</section>', '<!--Content_Start-->{body}<!--Content_End-->',
])
def test_article_explicit_regions(wrapper):
    body = '<p>Original article paragraph preserving its policy statements and quoted material.</p>' * 5
    text, selector = extract_article('<nav>Site links</nav>' + wrapper.format(body=body) + '<footer>Site footer</footer>')
    assert len(text) > 200 and selector
    assert "Site" not in text


def test_chrome_only_page_fails_instead_of_becoming_evidence():
    with pytest.raises(ValueError, match="official_article_body_not_found"):
        extract_article('<html><body><nav>' + 'Long navigation. ' * 100 + '</nav><div>Unknown page structure.</div></body></html>')


def test_government_item_has_bounded_article_summary_and_original_date(monkeypatch):
    article = 'An official provision with its original quoted language and publication context. ' * 60
    html = '<meta name="PubDate" content="2026-09-04"><nav>Unrelated navigation</nav><h1>Policy</h1><div id="zoom"><p>' + article + '</p></div><footer>Footer</footer>'
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, html))
    source = {"source_id": "policy", "type": "official_pages", "name": "Government", "level": "L1",
              "allowed_domains": ["example.com"], "pages": [{"url": "https://example.com/policy", "title": "Policy"}]}
    rows, status = sources._collect_news_source(source)
    assert status["status"] == "ok" and len(rows) == 1
    item = rows[0]
    assert item["published_at"] == "2026-09-04" and item["published_at_basis"] == "html_meta:pubdate"
    assert item["article_body_selector"] == "#zoom" and item["summary_truncated"]
    assert len(item["summary"]) == 2400 and len(item["text"]) > 2400
    assert "navigation" not in item["text"] and "Footer" not in item["summary"]


def test_arxiv_bounded_timeout_retry_queries_and_shared_exact_url_cache(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return response(url, '<feed xmlns="http://www.w3.org/2005/Atom"/>')
    monkeypatch.setattr(sources, "fetch_url", fetch)
    source = {"source_id": "arxiv", "name": "arXiv", "type": "arxiv_search",
              "url": "https://export.arxiv.org/api/query", "allowed_domains": ["arxiv.org"]}
    plan = normalize_plan({"search_terms": [f"public topic {i}" for i in range(24)]})
    url = query_urls(source, plan, 40)[0]
    assert url.count("all%3A") == 8
    _, first = sources._collect_news_source(source, plan)
    _, second = sources._collect_news_source(source, plan)
    assert len(calls) == 1
    assert calls[0][1]["timeout_seconds"] == 20 and calls[0][1]["retries"] == 1
    assert calls[0][1]["retry_delay_seconds"] == 3
    assert first["query_term_count"] == 8 and first["query_terms_omitted_count"] == 16
    assert second["acquisition_cache_hit"] and second["request_count"] == 0


@pytest.mark.parametrize("title", [
    "pg电子官网网站今日正式上线全新安全认证功能，提升用户数据保护与操作体验 - cn.chinadaily.com.cn",
    "在线真人赌博娱乐平台获行业大奖，技术创新引领赛事转播新标杆 - 体坛",
    "万博体育下注手机软件发布2026年Q1安全更新：用户数据保护与隐私合规升级",
    "第三方安全监测报告：九五之尊足彩官网2026年上半年用户数据保护与合规升级分析",
    "博联官网开户通过国家信息安全等级保护三级认证",
])
def test_obvious_casino_ad_titles_are_quarantined(title):
    item = {"title": title, "url": "https://news.google.com/rss/articles/test", "source_id": "google_news",
            "published_at": "2026-09-04", "summary": "A source-provided description."}
    rows, diagnostics = clean_candidates([item], normalize_plan(None))
    assert rows == []
    assert diagnostics["spam_title_quarantined_count"] == 1
    assert diagnostics["quarantine_reason_counts"] == {"obvious_casino_ad_title": 1}
    assert diagnostics["invalid_count"] == diagnostics["excluded_count"] == 0


@pytest.mark.parametrize("title", [
    "强化学习在电子竞技与棋牌游戏中的策略泛化研究",
    "PG电子游戏中的强化学习策略研究",
    "博彩广告检测：识别pg电子官网推广垃圾的研究",
    "在线真人赌博娱乐平台的成瘾研究与监管建议",
    "Casino game benchmarks for reinforcement learning research",
    "Detecting online casino advertising spam with language models",
    "SDAIA与Rubrik签署谅解备忘录，加强数据保护与数据韧性合作",
    "云游戏平台发布安全更新与隐私认证研究",
    "证券公司官网开户流程的安全认证研究",
])
def test_normal_gaming_research_and_security_news_are_not_quarantined(title):
    item = {"title": title, "url": "https://example.com/research", "published_at": "2026-09-04"}
    rows, diagnostics = clean_candidates([item], normalize_plan(None))
    assert len(rows) == 1
    assert diagnostics["spam_title_quarantined_count"] == 0
    assert diagnostics["quarantine_reason_counts"] == {}


def test_quarantine_reason_counts_merge_once_across_sources(monkeypatch):
    config = {"news_sources": [{"source_id": name, "name": name, "type": "google_news_search",
                                "url": "https://news.google.com/rss/search", "allowed_domains": ["news.google.com"]}
                               for name in ("first", "second")]}
    body = ('<rss><channel><item><title>pg电子官网网站正式上线</title><link>https://news.google.com/spam</link>'
            '<pubDate>2026-09-04</pubDate></item><item><title>Normal gaming research</title>'
            '<link>https://example.com/research</link><pubDate>2026-09-04</pubDate></item></channel></rss>')
    monkeypatch.setattr(sources, "load_source_config", lambda: config)
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, body))
    result = sources.collect_news_sources(persist=False)
    diagnostics = result["cleaning_diagnostics"]
    assert len(result["items"]) == 1
    assert diagnostics["raw_count"] == 4 and diagnostics["duplicate_count"] == 1
    assert diagnostics["dropped_count"] == diagnostics["spam_title_quarantined_count"] == 2
    assert diagnostics["quarantine_reason_counts"] == {"obvious_casino_ad_title": 2}
    assert all(status["cleaning_diagnostics"]["spam_title_quarantined_count"] == 1 for status in result["source_statuses"])


def test_shared_news_feed_registry_uses_forty_item_ceilings():
    config = json.loads((Path(__file__).resolve().parents[1] / "config/sources.json").read_text(encoding="utf-8"))
    selected = {source["source_id"]: source for source in config["news_sources"]}
    for source_id in ("openai_news", "techcrunch_ai", "theverge_ai"):
        assert selected[source_id]["max_items"] == 40
        assert selected[source_id]["enabled"] is True


def test_cac_bodylabel_region_preserves_policy_text_not_page_chrome():
    paragraph = "A substantive official provision whose original quotation and paragraph boundaries must remain intact. "
    html = ('<div class="nav">Home and other links</div><div class="main"><h1>Policy title</h1>'
            '<div class="info">2026-09-04 18:00</div><div class="main-content"><div id="BodyLabel">'
            + '<p>' + paragraph * 6 + '</p></div></div><button>Close</button></div><footer>Copyright</footer>')
    text, selector = extract_article(html)
    assert selector == "#BodyLabel"
    assert paragraph.strip() in text and len(text) > 200
    assert all(chrome not in text for chrome in ("Home", "Policy title", "2026-09-04", "Close", "Copyright"))


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("source_type", ["official_html_list", "official_pages"])
def test_policy_article_failure_isolation_retains_other_valid_documents(monkeypatch, legacy, source_type):
    bad_url = "https://example.com/2026-09/04/bad.htm"
    good_url = "https://example.com/2026-09/04/good.htm"
    source = {"source_id": "policies", "name": "Government", "level": "L1", "type": source_type,
              "list_url": "https://example.com/list", "allowed_domains": ["example.com"],
              "pages": [{"url": bad_url, "title": "Broken policy", "published_at": "2026-09-04"},
                        {"url": good_url, "title": "Valid policy", "published_at": "2026-09-04"}]}
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("/list"):
            return response(url, f'<a href="{bad_url}">Broken policy</a><a href="{good_url}">Valid policy</a>')
        if url == bad_url:
            return response(url, '<div class="unknown">Unrecognized page with no article body.</div>')
        return response(url, '<meta name="PubDate" content="2026-09-04"><div id="BodyLabel"><p>'
                        + 'An original official provision retained after another article fails. ' * 8 + '</p></div>')
    monkeypatch.setattr(sources, "fetch_url", fetch)
    monkeypatch.setattr(sources, "load_source_config", lambda: {"policy_sources": [source]})
    if legacy:
        result = sources.collect_policy_sources(date_from="2026-09-01", date_to="2026-09-10", persist=False)
        items = result["documents"]
    else:
        result = sources.collect_news_sources(discovery_plan={"date_from": "2026-09-01", "date_to": "2026-09-10"}, persist=False)
        items = result["items"]
    assert len(items) == 1 and items[0]["source_url"] == good_url
    assert bad_url in calls and good_url in calls
    status = result["source_statuses"][0]
    assert status["status"] == "partial" and status["item_count"] == 1
    assert status["article_error_count"] == status["article_success_count"] == 1
    assert status["errors"][0]["url"] == bad_url
    assert "official_article_body_not_found" in status["errors"][0]["error"]
    assert result["collection_succeeded"] and result["collection_partial"]
