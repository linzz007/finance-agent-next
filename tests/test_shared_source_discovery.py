"""Offline contracts for the shared source pool; no network or model calls."""

from copy import deepcopy
import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from policy_impact.source_connectors import ingestion as sources
from policy_impact.source_connectors import http_client as http
from policy_impact.source_connectors.discovery import (
    cache_identity, canonical_url, clean_candidates, normalize_plan, query_urls,
)


def response(url, body, content_type="application/xml"):
    encoded = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    return http.FetchResponse(url, 200, content_type, encoded, "2026-09-10T12:00:00+00:00", "sha")


def feed(entries):
    return '<rss><channel>' + ''.join(
        '<item><title>' + title + '</title><link>' + url.replace('&', '&amp;') + '</link>'
        '<pubDate>' + published + '</pubDate><description>' + summary + '</description></item>'
        for title, url, published, summary in entries) + '</channel></rss>'


def source(source_id="shared", **kwargs):
    return {"source_id": source_id, "name": source_id, "type": "rss_atom", "level": "L2",
            "url": "https://example.com/feed", "allowed_domains": ["example.com"], **kwargs}


def row(url="https://example.com/article", **kwargs):
    return {"title": "Agent evaluation", "summary": "A measured tool evaluation procedure.",
            "url": url, "source_id": "first", "published_at": "2026-07-01", **kwargs}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    config = {"news_sources": [source()], "policy_sources": []}
    monkeypatch.setattr(sources, "load_source_config", lambda: deepcopy(config))
    monkeypatch.setattr(sources, "project_root", lambda: tmp_path)
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, feed([
        ("Agent evaluation", "https://example.com/article", "2026-07-01", "Verified evaluation evidence.")
    ])))
    return config


def test_normalized_plan_bounds_and_ignored_extra_keys():
    plan = normalize_plan({"per_source_limit": 999, "max_candidates": 9999,
                           "search_terms": ["  agent  eval ", "agent eval"],
                           "topic_terms": {"eval": ["private planner context"]},
                           "diagnostics": {"wiki": "ignored"}})
    assert plan["per_source_limit"] == 60
    assert plan["max_candidates"] == 1000
    assert plan["search_terms"] == ["agent eval"]
    assert "topic_terms" not in plan and "diagnostics" not in plan
    assert normalize_plan(None)["per_source_limit"] == 40
    assert normalize_plan(None)["max_candidates"] == 500
    assert normalize_plan(None)["query_timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize("plan", [[], {"search_terms": "agent"}, {"focus_topics": [1]},
    {"date_from": "2026-02-30"}, {"date_to": "2026-09-10T12:00:00"},
    {"date_from": "2026-07-02", "date_to": "2026-07-01"}, {"query_timezone": "Invalid/Timezone"}])
def test_invalid_plans_fail_before_fetch(plan, offline, monkeypatch):
    monkeypatch.setattr(sources, "fetch_url", lambda *args, **kwargs: pytest.fail("must not fetch"))
    with pytest.raises(ValueError):
        sources.collect_news_sources(discovery_plan=plan)


def test_arxiv_query_uses_dynamic_terms_dates_and_bounded_batch():
    plan = normalize_plan({"search_terms": ["self-evolving agents", "金融科技"], "focus_topics": ["tool_use"],
                           "excluded_topics": ["memory"], "date_from": "2026-07-01", "date_to": "2026-07-31"})
    params = parse_qs(urlparse(query_urls(source(type="arxiv_search", url="https://export.arxiv.org/api/query"), plan, 40)[0]).query)
    assert params["max_results"] == ["40"]
    assert 'all:"self-evolving agents"' in params["search_query"][0]
    assert 'all:"金融科技"' in params["search_query"][0]
    assert 'all:"tool use"' in params["search_query"][0]
    assert 'ANDNOT all:"memory"' in params["search_query"][0]
    assert "submittedDate:[202606300000 TO 202608012359]" in params["search_query"][0]


def test_news_query_preserves_locale_and_inclusive_dates():
    plan = normalize_plan({"search_terms": ["risk analysis"], "excluded_topics": ["memory"],
                           "date_from": "2026-07-01", "date_to": "2026-07-31"})
    params = parse_qs(urlparse(query_urls(source(type="google_news_search", url="https://news.google.com/rss/search?hl=zh-CN&q=old"), plan, 40)[0]).query)
    assert params["hl"] == ["zh-CN"]
    assert params["q"] == ['("risk analysis") -"memory" after:2026-06-29 before:2026-08-02']


def test_github_queries_bound_and_use_only_public_search():
    plan = normalize_plan({"search_terms": [f"topic {i}" for i in range(20)],
                           "date_from": "2026-07-01", "date_to": "2026-07-31"})
    urls = query_urls(source(type="github_search", url="https://api.github.com/search/repositories"), plan, 60)
    assert len(urls) == 3
    assert all(parse_qs(urlparse(url).query)["per_page"] == ["60"] for url in urls)
    query = parse_qs(urlparse(urls[0]).query)["q"][0]
    assert "is:public" in query and "pushed:>=2026-06-30" in query
    assert "2026-07-31" not in query  # Repo push time is not a release publication time.


def test_profile_never_selects_a_private_pool(offline, monkeypatch):
    offline["private_sources"] = [source("private")]
    offline["persona_sources"] = {"finance": [source("secret")]}
    calls = []
    def collect(value, plan, limit):
        calls.append((value["source_id"], plan["profile_domain"]))
        return [], {"source_id": value["source_id"], "status": "ok", "raw_count": 0}
    monkeypatch.setattr(sources, "_collect_news_source", collect)
    for domain in ("finance", "personal"):
        sources.collect_news_sources(discovery_plan={"profile_domain": domain}, persist=False, force_refresh=True)
    assert calls == [("shared", "finance"), ("shared", "personal")]


def test_cache_isolated_by_plan_config_and_ignores_legacy(offline, tmp_path, monkeypatch):
    plan_a = {"search_terms": ["eval"]}
    first = sources.collect_news_sources(discovery_plan=plan_a)
    same = sources.collect_news_sources(discovery_plan=plan_a)
    assert same["cache_hit"] is True
    assert same["snapshot_key"] == first["snapshot_key"]
    assert same["snapshot_id"] == first["snapshot_id"]
    assert same["cleaning_diagnostics"] == first["cleaning_diagnostics"]
    assert same["items"][0]["data_mode"] == "source_snapshot"
    assert sources.load_latest_news_snapshot(discovery_plan={"search_terms": ["finance"]})["items"] == []
    assert sources.load_latest_news_snapshot()["items"] == []
    offline["news_sources"][0]["enabled"] = False
    assert sources.load_latest_news_snapshot(discovery_plan=plan_a)["items"] == []
    legacy = tmp_path / "data/news/snapshots/latest.json"
    legacy.write_text(json.dumps({"items": [row(title="Other persona data")]}), encoding="utf-8")
    assert sources.load_latest_news_snapshot()["items"] == []


def test_cache_normalization_extra_fields_and_config_order():
    a = cache_identity(normalize_plan({"search_terms": ["eval"]}), {"b": 1, "a": 2})
    b = cache_identity(normalize_plan({"search_terms": ["eval"], "topic_terms": {"ignored": "x"}}), {"a": 2, "b": 1})
    assert a == b


def test_cache_reuses_acquisition_without_leaking_informational_domain(offline):
    first = sources.collect_news_sources(discovery_plan={"search_terms": ["eval"], "profile_domain": "finance"})
    second = sources.collect_news_sources(discovery_plan={"search_terms": ["eval"], "profile_domain": "personal",
                                                          "diagnostics": {"response_id": "new-call"}})
    assert first["snapshot_key"] == second["snapshot_key"] and second["cache_hit"]
    assert second["discovery_plan"]["profile_domain"] == "personal"


def test_corrupt_or_mismatched_snapshot_not_used(offline, tmp_path):
    result = sources.collect_news_sources()
    path = tmp_path / "data/news/snapshots" / f"latest-{result['snapshot_key']}.json"
    path.write_text("{", encoding="utf-8")
    assert sources.load_latest_news_snapshot()["items"] == []
    assert sources.load_latest_news_snapshot()["snapshot_error"]
    path.write_text(json.dumps({"items": [row()], "snapshot_key": "other", "schema_version": "2.0"}), encoding="utf-8")
    assert sources.load_latest_news_snapshot()["snapshot_error"] == "snapshot identity mismatch"


def test_failed_refresh_does_not_return_previous_evidence(offline, monkeypatch):
    sources.collect_news_sources(discovery_plan={"search_terms": ["old"]})
    def fail(*args, **kwargs):
        raise RuntimeError("source unavailable")
    monkeypatch.setattr(sources, "fetch_url", fail)
    result = sources.collect_news_sources(discovery_plan={"search_terms": ["new"]}, force_refresh=True)
    assert result["items"] == [] and result["collection_succeeded"] is False
    assert result["source_statuses"][0]["status"] == "error"
    assert "source unavailable" in result["source_statuses"][0]["error"]
    assert sources.load_latest_news_snapshot(discovery_plan={"search_terms": ["new"]})["items"] == []


def test_empty_success_can_be_cached_without_claiming_items(offline, monkeypatch):
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, "<rss><channel/></rss>"))
    first = sources.collect_news_sources()
    assert first["items"] == [] and first["collection_succeeded"]
    assert sources.collect_news_sources()["cache_hit"]


def test_candidate_allocation_and_partial_statuses(offline, monkeypatch):
    offline["news_sources"] = [source(str(i)) for i in range(4)]
    limits = []
    def collect(value, plan, limit):
        limits.append(limit)
        if value["source_id"] == "0":
            raise RuntimeError("one failed worker")
        return [row(f"https://example.com/{value['source_id']}/{i}", source_id=value["source_id"]) for i in range(limit)], {
            "source_id": value["source_id"], "status": "ok", "raw_count": limit}
    monkeypatch.setattr(sources, "_collect_news_source", collect)
    result = sources.collect_news_sources(discovery_plan={"max_candidates": 7}, persist=False)
    assert sum(limits) == 7 and max(limits) <= 40
    assert len(result["items"]) <= 7
    assert result["collection_succeeded"] and result["collection_partial"]
    assert len(result["source_statuses"]) == 4


def test_dedupe_provenance_idempotent_and_no_title_only_merge():
    items = [row("http://example.com/a?utm_source=feed#top", source_id="first"),
             row("https://example.com/a", source_id="second", summary="A conflicting assertion."),
             row("https://other.example/a", source_id="third")]
    before = deepcopy(items)
    result = sources._dedupe_items(items)
    assert len(result) == 2
    assert result[0]["source_ids"] == ["first", "second"]
    assert len(result[0]["provenance"]) == 2
    assert result[0]["summary"] == items[0]["summary"]
    assert result[0]["provenance"][1]["summary"] == "A conflicting assertion."
    assert sources._dedupe_items(result) == result
    assert items == before


def test_arxiv_canonical_identity_and_nontracking_parameters():
    assert canonical_url("http://arxiv.org/abs/2607.12345v2") == canonical_url("https://arxiv.org/pdf/2607.12345v1.pdf")
    assert canonical_url("https://example.com/a?id=1") != canonical_url("https://example.com/a?id=2")


def test_exact_substantial_syndication_identity_preserves_originals():
    body = "The evaluation measured tool execution failures across 100 documented test cases, with all inputs and observed outputs included."
    items = [row("https://example.com/original", summary=body),
             row("https://other.example/copy", source_id="syndicated", title="  Agent   evaluation  ",
                 summary=body.replace(" ", "\n")),
             row("https://other.example/conflict", source_id="conflict", summary=body + " Different conclusion.")]
    result = sources._dedupe_items(items)
    assert len(result) == 2
    assert result[0]["source_ids"] == ["first", "syndicated"]
    assert result[0]["original_urls"] == ["https://example.com/original", "https://other.example/copy"]
    assert result[0]["provenance"][1]["summary"] == items[1]["summary"]
    assert sources._dedupe_items(result) == result


def test_matching_short_summaries_do_not_prove_syndication():
    items = [row("https://example.com/a", summary="Read the full story."),
             row("https://example.com/b", summary="Read the full story.")]
    assert len(sources._dedupe_items(items)) == 2


def test_conflicting_full_text_not_merged_using_shared_summary():
    summary = "The evaluation measured tool execution failures across 100 documented test cases, with all inputs and observed outputs included."
    items = [row("https://example.com/a", summary=summary, text=summary + " Result A."),
             row("https://example.com/b", summary=summary, text=summary + " Result B.")]
    assert len(sources._dedupe_items(items)) == 2


def test_cross_source_and_local_duplicate_cleaning_counts(offline, monkeypatch):
    offline["news_sources"] += [source("second")]
    body = feed([("Agent", "https://example.com/a", "2026-07-01", "evaluation")]*2)
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, body))
    result = sources.collect_news_sources(persist=False)
    counts = result["cleaning_diagnostics"]
    assert counts["raw_count"] == 4 and counts["unique_count"] == 1
    assert counts["duplicate_count"] == 3 and counts["dropped_count"] == 0
    assert result["items"][0]["source_ids"] == ["shared", "second"]
    assert all(status["duplicate_count"] == 1 for status in result["source_statuses"])


def test_cleaning_dates_exclusions_invalid_and_never_retrieval_dates():
    plan = normalize_plan({"date_from": "2026-07-01", "date_to": "2026-07-31", "excluded_topics": ["memory"]})
    items = [row(), row("https://example.com/b", published_at="2026-06-30"),
             row("https://example.com/c", published_at="", retrieved_at="2026-07-10"),
             row("https://example.com/d", summary="New memory design"),
             row("http://127.0.0.1/internal"), row("https://example.com/e", published_at="2099-01-01")]
    result, counts = clean_candidates(items, plan)
    assert len(result) == 1 and result[0]["url"] == items[0]["url"]
    assert counts["outside_date_count"] == counts["unknown_date_count"] == counts["excluded_count"] == 1
    assert counts["invalid_count"] == counts["future_date_count"] == 1
    assert clean_candidates([items[2]], normalize_plan(None))[0][0]["published_at"] == ""


@pytest.mark.parametrize(("published", "zone", "keep", "local_day"), [
    ("2026-06-30T16:00:00Z", "Asia/Shanghai", True, "2026-07-01"),
    ("2026-06-30T15:59:59Z", "Asia/Shanghai", False, ""),
    ("2026-07-01T15:59:59+00:00", "Asia/Shanghai", True, "2026-07-01"),
    ("2026-07-01T16:00:00+00:00", "Asia/Shanghai", False, ""),
    ("2026-07-02T03:59:59Z", "America/New_York", True, "2026-07-01"),
    ("2026-07-02T04:00:00Z", "America/New_York", False, ""),
    ("2026-07-01", "America/New_York", True, "2026-07-01"),
    ("2026-07-01T00:01:00", "Asia/Shanghai", True, "2026-07-01"),
])
def test_date_windows_use_local_publication_day(published, zone, keep, local_day):
    plan = normalize_plan({"date_from": "2026-07-01", "date_to": "2026-07-01", "query_timezone": zone})
    original = row(published_at=published)
    selected, counts = clean_candidates([original], plan)
    assert bool(selected) is keep
    assert counts["outside_date_count"] == (0 if keep else 1)
    if keep:
        assert selected[0]["published_at"] == published
        assert selected[0]["published_at_query_timezone"].startswith(local_day)
        assert selected[0]["publication_query_timezone"] == zone
    assert "published_at_query_timezone" not in original


def test_timezone_is_part_of_cache_identity_and_default_is_equivalent(offline):
    implicit = sources.collect_news_sources()
    explicit = sources.collect_news_sources(discovery_plan={"query_timezone": "Asia/Shanghai"})
    assert implicit["snapshot_key"] == explicit["snapshot_key"] and explicit["cache_hit"]
    assert sources.load_latest_news_snapshot(discovery_plan={"query_timezone": "UTC"})["items"] == []


def test_naive_dates_preserve_precision_and_disclose_assumption():
    assert sources._normalize_datetime("2026-07-01") == "2026-07-01"
    assert sources._normalize_datetime("2026-07-01 00:30:00") == "2026-07-01T00:30:00"
    rows, _ = clean_candidates([row(published_at="2026-07-01T00:30:00")], normalize_plan(None))
    assert rows[0]["published_at_timezone_assumption"] == "query_timezone_for_naive_datetime"


def test_feed_pipeline_and_policy_prefilter_keep_local_boundary(offline, monkeypatch):
    body = feed([("Boundary event", "https://example.com/a", "2026-06-30T16:30:00Z", "Verified source evidence.")])
    monkeypatch.setattr(sources, "fetch_url", lambda url, **kwargs: response(url, body))
    plan = {"date_from": "2026-07-01", "date_to": "2026-07-01"}
    result = sources.collect_news_sources(discovery_plan=plan, persist=False)
    assert len(result["items"]) == 1
    assert result["items"][0]["published_at_query_timezone"] == "2026-07-01T00:30:00+08:00"
    assert sources._date_in_range("2026-06-30T16:30:00Z", "2026-07-01", "2026-07-01")
    assert not sources._date_in_range("2026-07-01T16:30:00Z", "2026-07-01", "2026-07-01")


def test_feed_updated_is_not_published_and_arxiv_is_explicit_abstract():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Paper</title><updated>2026-07-02T01:00:00Z</updated><link href="https://arxiv.org/abs/2607.12345"/><summary>An abstract, not a full paper.</summary></entry></feed>'
    item = sources._parse_feed(xml.encode(), source(type="arxiv_search"), "2026-07-03T00:00:00Z")[0]
    assert item["published_at"] == "" and item["updated_at"].startswith("2026-07-02")
    assert item["evidence_kind"] == "arxiv_abstract" and item["full_text_fetched"] is False
    assert item["abstract"] == item["summary"] and item["limitations"]
    assert sources._normalize_datetime("nonsense") == ""
    assert sources._date_from_url("https://example.com/2026-02/30/a") == ""


def test_arxiv_error_entry_is_not_silent_empty_success():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id><summary>Invalid query</summary></entry></feed>'
    with pytest.raises(ValueError, match="arXiv API error"):
        sources._parse_feed(xml.encode(), source(type="arxiv_search"), "2026-07-03")


def test_policy_evidence_partial_failure_and_bounded_bodies(offline, monkeypatch):
    policy = source("policy", type="official_pages", level="L1", max_body_fetches=2,
                    pages=[{"url": f"https://example.com/{i}", "title": f"Policy {i}"} for i in range(4)])
    offline["news_sources"] = []
    offline["policy_sources"] = [policy]
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/1"):
            raise RuntimeError("article unavailable")
        return response(url, '<meta name="PubDate" content="2026-07-01"><h1>Policy</h1><p>' + 'Official body. '*30 + '</p>', "text/html")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    result = sources.collect_news_sources(discovery_plan={"date_from": "2026-07-01", "date_to": "2026-07-31"}, persist=False)
    assert len(calls) == 2
    assert all(options["timeout_seconds"] == 8 and options["retries"] == 0 for _, options in calls)
    assert result["collection_succeeded"] and result["collection_partial"]
    assert result["source_statuses"][0]["status"] == "partial"
    item = result["items"][0]
    assert item["url"] == item["source_url"] and item["summary"] and item["title"]
    assert item["source_level"] == "L1" and item["published_at"].startswith("2026-07-01")
    assert item["published_at_basis"] == "html_meta:pubdate"
    assert item["evidence_kind"] == "official_policy_text" and item["provenance"]


def test_dynamic_policy_list_not_restricted_to_static_persona_keywords(offline, monkeypatch):
    policy = source("policy", type="official_html_list", list_url="https://example.com/list", level="L1",
                    keywords=["artificial intelligence"], article_url_regex=r"/2026-07/01/.*")
    offline["news_sources"] = []
    offline["policy_sources"] = [policy]
    def fetch(url, **kwargs):
        if url.endswith("/list"):
            return response(url, '<a href="/2026-07/01/risk">Financial risk regulation</a><a href="http://127.0.0.1/2026-07/01/x">Risk</a>', "text/html")
        return response(url, '<h1>Risk</h1><p>' + 'A financial regulatory provision. '*20 + '</p>', "text/html")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    result = sources.collect_news_sources(discovery_plan={"search_terms": ["financial risk"]}, persist=False)
    assert len(result["items"]) == 1
    assert result["items"][0]["published_at_basis"] == "url_date"


def test_github_release_readme_discovery_and_partial_error(offline, monkeypatch):
    offline["news_sources"] = [source("github", type="github_search", url="https://api.github.com/search/repositories",
                                      allowed_domains=["github.com"], max_queries=1, max_repo_details=1)]
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if "/search/" in url:
            return response(url, {"items": [{"full_name": "org/repo", "html_url": "https://github.com/org/repo", "private": False,
                                               "description": "Agent evaluation", "created_at": "2020-01-01", "pushed_at": "2026-07-01"},
                                              {"full_name": "org/private", "html_url": "https://github.com/org/private", "private": True}]}, "application/json")
        if url.endswith("/readme"):
            return response(url, {"encoding": "base64", "content": base64.b64encode(b"# Agent evaluation README").decode(), "sha": "readme-sha"}, "application/json")
        return response(url, [{"name": "v1", "published_at": "2026-07-01T12:00:00Z", "body": "An evaluated agent tool release.",
                               "html_url": "https://github.com/org/repo/releases/tag/v1"}], "application/json")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    result = sources.collect_news_sources(persist=False)
    assert len(calls) == 3
    assert [item["evidence_kind"] for item in result["items"]] == ["release_notes", "repository_readme"]
    assert result["items"][1]["published_at"] == "" and result["items"][1]["readme_sha"] == "readme-sha"
    ranged = sources.collect_news_sources(discovery_plan={"date_from": "2026-07-01", "date_to": "2026-07-31"}, persist=False)
    assert len(ranged["items"]) == 1 and ranged["items"][0]["evidence_kind"] == "release_notes"


def test_static_release_api_uses_actual_published_date(offline, monkeypatch):
    offline["news_sources"] = [source("repo_releases", url="https://github.com/org/repo/releases.atom",
                                      release_api_url="https://api.github.com/repos/org/repo/releases", allowed_domains=["github.com"])]
    def fetch(url, **kwargs):
        assert "per_page=40" in url
        return response(url, [{"name": "v1.0", "published_at": "2026-07-01T01:00:00Z", "created_at": "2025-01-01",
                               "body": "Actual release notes", "html_url": "https://github.com/org/repo/releases/tag/v1"}], "application/json")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    item = sources.collect_news_sources(persist=False)["items"][0]
    assert item["published_at"].startswith("2026-07-01")
    assert item["published_at_basis"] == "github_release_published_at"


def test_github_enrichment_failure_keeps_metadata_visible(offline, monkeypatch):
    offline["news_sources"] = [source("github", type="github_search", url="https://api.github.com/search/repositories",
                                      allowed_domains=["github.com"], max_queries=1, max_repo_details=1)]
    def fetch(url, **kwargs):
        if "/search/" in url:
            return response(url, {"items": [{"full_name": "org/repo", "html_url": "https://github.com/org/repo"}]})
        raise RuntimeError("rate limited")
    monkeypatch.setattr(sources, "fetch_url", fetch)
    result = sources.collect_news_sources(persist=False)
    assert len(result["items"]) == 1 and result["items"][0]["published_at"] == ""
    assert result["source_statuses"][0]["status"] == "partial"
    assert len(result["source_statuses"][0]["errors"]) == 2


def test_no_sources_returns_bounded_empty_result(offline):
    offline["news_sources"] = []
    result = sources.collect_news_sources(persist=False)
    assert result["items"] == [] and result["collection_succeeded"] is False
    assert result["cleaning_diagnostics"]["raw_count"] == 0


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "https://[::1]/", "http://169.254.169.254/", "https://user:pass@example.com/",
    "file:///etc/passwd", "http://localhost/", "https://example.com:8080/", "https://example.com\\@attacker.com/",
    "https://example.com\n/", "http://10.0.0.1/"])
def test_public_url_boundary_rejects_unsafe_urls(url):
    assert not http.public_url(url)
    assert not http.domain_allowed(url, ["example.com", "127.0.0.1", "localhost"])


def test_dns_and_redirect_are_checked_before_request(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="non-public"):
        http.fetch_url("https://example.com/", allowed_domains=["example.com"], retries=0)
    handler = http._AllowedRedirects(["example.com"])
    with pytest.raises(ValueError, match="allowlist"):
        handler.redirect_request(None, None, 302, "", {}, "https://other.example/")


def test_response_size_bound(monkeypatch):
    monkeypatch.setattr(http, "_validate_destination", lambda *args: None)
    class Response:
        headers = {}
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return "https://example.com/"
        def read(self, count): return b"x" * count
    class Opener:
        def open(self, *args, **kwargs): return Response()
    monkeypatch.setattr(http, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="exceeds"):
        http.fetch_url("https://example.com/", allowed_domains=["example.com"], max_bytes=10, retries=0)
