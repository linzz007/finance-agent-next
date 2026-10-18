"""Normalize primary RSS/Atom and official policy pages into evidence snapshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from policy_impact.harness.artifacts import project_root
from policy_impact.source_connectors.http_client import domain_allowed, fetch_url
from policy_impact.source_connectors.discovery import (
    bounded_int, cache_identity, clean_candidates, deduplicate, matches_term,
    normalize_plan, publication_local_date, query_terms, query_urls,
)
from policy_impact.source_connectors.github import collect_github
from policy_impact.source_connectors.acquisition_cache import cached_acquisition
from policy_impact.source_connectors.article import extract_article


ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_LOCK = Lock()
_ARXIV_NEXT_REQUEST = 0.0


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.heading_parts: list[str] = []
        self._ignored_depth = 0
        self._in_heading = False
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            key = str(attributes.get("property") or attributes.get("name") or "").casefold()
            self.metadata[key] = str(attributes.get("content") or "")
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag in {"h1", "h2"}:
            self._in_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"h1", "h2"}:
            self._in_heading = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(unescape(data).split())
        if text:
            self.parts.append(text)
            if self._in_heading:
                self.heading_parts.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.parts)

    @property
    def heading(self) -> str:
        return " ".join(self.heading_parts).strip()


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._active: dict[str, Any] | None = None
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = str(dict(attrs).get("href") or "")
            self._active = {"href": href, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active is not None:
            title = " ".join("".join(self._active["parts"]).split())
            if self._active["href"] and title:
                self.links.append((self._active["href"], title))
            self._active = None


def source_config_path() -> Path:
    return project_root() / "config" / "sources.json"


def load_source_config() -> dict[str, Any]:
    return json.loads(source_config_path().read_text(encoding="utf-8"))


def collect_news_sources(
    *,
    discovery_plan: dict[str, Any] | None = None,
    persist: bool = True,
    force_refresh: bool = False,
    cache_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Collect one shared pool. Extra planner keys are ignored; dates are inclusive.

    Unknown publication dates are retained only without a requested date window,
    except explicitly labelled undated Atom fallback leads, not dated evidence.
    No unkeyed/other-plan news snapshot is ever read, including on source failure.
    """
    plan = normalize_plan(discovery_plan)
    config = load_source_config()
    key = cache_identity(plan, config)
    if not force_refresh:
        cached = _fresh_snapshot("news", cache_ttl_seconds, cache_key=key)
        if cached:
            return {
                **cached,
                "items": cached["items"],
                "source_statuses": cached.get("source_statuses") or [],
                "collection_succeeded": bool(cached.get("collection_succeeded")),
                "snapshot_path": cached.get("snapshot_path") or "",
                "collected_at": cached.get("captured_at"),
                "discovery_plan": plan,
                "cache_hit": True,
            }

    items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    sources = [dict(source) for group in ("news_sources", "policy_sources")
               for source in config.get(group, []) if source.get("enabled", True)]
    # Allocate before fetching so a large pool cannot exceed the candidate budget.
    quotas = [0] * len(sources)
    remaining = plan["max_candidates"]
    while remaining and any(quota < plan["per_source_limit"] for quota in quotas):
        for index in range(len(quotas)):
            if remaining and quotas[index] < plan["per_source_limit"]:
                quotas[index] += 1
                remaining -= 1
    with ThreadPoolExecutor(max_workers=max(1, min(len(sources), 8))) as executor:
        futures = {executor.submit(_collect_news_source, source, plan, quotas[index]): index
                   for index, source in enumerate(sources)}
        results: dict[int, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[index] = ([], _source_status(sources[index], "error", 0,
                                  datetime.now(timezone.utc), error=repr(exc)))
    for index, source in enumerate(sources):
        selected, status = results[index]
        items.extend(selected)
        statuses.append(status)

    items, cleaning = clean_candidates(items, plan)
    cleaning["candidate_count"] = cleaning["raw_count"]
    cleaning["provider_raw_count"] = sum(status.get("raw_count", 0) for status in statuses)
    cleaning["raw_count"] = cleaning["provider_raw_count"]
    cleaning["duplicate_count"] += sum(status.get("duplicate_count", 0) for status in statuses)
    for status in statuses:
        source_cleaning = status.get("cleaning_diagnostics", {})
        cleaning["spam_title_quarantined_count"] += source_cleaning.get("spam_title_quarantined_count", 0)
        for reason, count in source_cleaning.get("quarantine_reason_counts", {}).items():
            cleaning["quarantine_reason_counts"][reason] = cleaning["quarantine_reason_counts"].get(reason, 0) + count
    for field in ("invalid_count", "excluded_count", "outside_date_count", "unknown_date_count", "future_date_count"):
        cleaning[field] = sum(status.get("cleaning_diagnostics", {}).get(field, 0) for status in statuses)
    cleaning["dropped_count"] = max(0, cleaning["raw_count"] - cleaning["duplicate_count"] - len(items))
    cleaning["provider_dropped_count"] = sum(status.get("dropped_count", 0) for status in statuses)
    for status in statuses:
        source_id = str(status.get("source_id") or "")
        status["contributed_unique_count"] = sum(source_id in item.get("source_ids", []) for item in items)
    collected_at = datetime.now(timezone.utc).isoformat()
    succeeded = any(status["status"] in {"ok", "partial"} for status in statuses)
    result = {
        "items": items,
        "source_statuses": statuses,
        "collection_succeeded": succeeded,
        "collection_partial": any(status["status"] != "ok" for status in statuses),
        "cleaning_diagnostics": cleaning,
        "discovery_plan": plan,
        "snapshot_key": key,
        "snapshot_id": "",
        "collected_at": collected_at,
        "cache_hit": False,
    }
    snapshot_path = ""
    result["snapshot_id"] = hashlib.sha256(json.dumps(
        result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    if persist:
        snapshot_path = str(_persist_snapshot("news", items, statuses, cache_key=key, metadata=result))
    return {**result, "snapshot_path": snapshot_path}


def _collect_news_source(
    source: dict[str, Any], plan: dict[str, Any] | None = None, limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started_at = datetime.now(timezone.utc)
    plan = normalize_plan(plan)
    limit = plan["per_source_limit"] if limit is None else limit
    details: dict[str, Any] = {"raw_count": 0, "errors": [], "request_count": 0,
                               "body_fetch_count": 0, "query_urls": []}
    if not limit:
        return [], {**_source_status(source, "skipped", 0, started_at),
                    "error": "candidate budget exhausted", "raw_count": 0, "dropped_count": 0}
    try:
        kind = source.get("type")
        if kind == "github_search":
            parsed, details = collect_github(source, plan, limit, fetch=fetch_url, make_item=_news_item)
        elif kind == "rss_atom":
            parsed, details = _collect_static_source(source)
        elif kind in {"official_html_list", "official_pages"}:
            parsed, details = _collect_policy_evidence(source, plan, limit)
        else:
            url = query_urls(source, plan, limit)[0]
            details["query_urls"] = [url]
            details["request_count"] = 1
            if kind == "arxiv_search":
                response, acquisition_hit = _fetch_arxiv(url, source)
                details.update(timeout_seconds=20, max_attempts=2,
                               acquisition_cache_hit=acquisition_hit,
                               request_count=0 if acquisition_hit else 1,
                               query_term_count=min(len(query_terms(plan, source)), bounded_int(source.get("max_query_terms"), 8, 12)),
                               query_terms_omitted_count=max(0, len(query_terms(plan, source)) - bounded_int(source.get("max_query_terms"), 8, 12)))
            else:
                response = fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []),
                                     timeout_seconds=8, retries=0, max_bytes=3_000_000)
            parsed = _parse_feed(response.body, source, response.retrieved_at)
            details["raw_count"] = len(parsed)
            details["checksum"] = response.sha256
            for item in parsed:
                item.update(response_sha256=response.sha256, discovery_query_url=url)
        selected, cleaning = clean_candidates(parsed, {**plan, "max_candidates": limit})
        details["cleaning_diagnostics"] = cleaning
        details["duplicate_count"] = cleaning["duplicate_count"]
        details["unique_count"] = len(selected)
        details["dropped_count"] = max(0, details["raw_count"] - len(selected) - cleaning["duplicate_count"])
        details["candidate_limit"] = limit
        acquired = bool(parsed) or bool(details.get("successful_fetch_count"))
        state = "partial" if details["errors"] and acquired else "error" if details["errors"] else "ok"
        return selected, {**_source_status(source, state, len(selected), started_at,
                          error="; ".join(error["error"] for error in details["errors"])), **details}
    except Exception as exc:  # noqa: BLE001
        details["errors"].append({"url": str(source.get("url") or source.get("list_url") or ""),
                                  "error": f"{type(exc).__name__}: {exc}"})
        details.update(unique_count=0, duplicate_count=0, dropped_count=details["raw_count"],
                       candidate_limit=limit, cleaning_diagnostics={})
        return [], {**_source_status(source, "error", 0, started_at, error=repr(exc)), **details}


def _fetch_arxiv(url: str, source: dict[str, Any]):
    def acquire():
        global _ARXIV_NEXT_REQUEST
        with _ARXIV_LOCK:
            time.sleep(max(0, _ARXIV_NEXT_REQUEST - time.monotonic()))
            try:
                return fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []),
                                 timeout_seconds=20, retries=1, retry_delay_seconds=3, max_bytes=3_000_000)
            finally:
                _ARXIV_NEXT_REQUEST = time.monotonic() + 3

    identity = json.dumps({"url": url, "allowed_domains": source.get("allowed_domains", [])}, sort_keys=True)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return cached_acquisition(("arxiv-v1", fetch_url, key), acquire)


def _acquisition_error(url: str, exc: Exception) -> dict[str, Any]:
    http_status = getattr(exc, "code", None)
    transient = http_status in {408, 500, 502, 503, 504} if http_status is not None else isinstance(exc, OSError)
    return {"url": url, "error": f"{type(exc).__name__}: {exc}",
            "http_status": http_status, "transient": transient}


def _collect_static_source(source: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def acquire():
        if source.get("release_api_url"):
            return _collect_release_feed(source, bounded_int(source.get("max_items"), 40, 60))
        details: dict[str, Any] = {"raw_count": 0, "errors": [], "request_count": 1,
                                  "body_fetch_count": 0, "query_urls": [str(source["url"])]}
        try:
            response = fetch_url(str(source["url"]), allowed_domains=list(source.get("allowed_domains") or []),
                                 timeout_seconds=12, retries=0, max_bytes=3_000_000)
            items = _parse_feed(response.body, source, response.retrieved_at)
            for item in items:
                item.update(response_sha256=response.sha256, discovery_query_url=str(source["url"]))
            details.update(raw_count=len(items), checksum=response.sha256, successful_fetch_count=1)
            return items, details
        except Exception as exc:  # noqa: BLE001
            details["errors"].append(_acquisition_error(str(source["url"]), exc))
            return [], details

    def acquire_with_cache_policy():
        items, details = acquire()
        errors = details.get("errors", [])
        successful = bool(items) or bool(details.get("successful_fetch_count"))
        rate_limited = any(error.get("http_status") in {403, 429} for error in errors)
        short_backoff = not successful and not rate_limited and any(error.get("transient") for error in errors)
        ttl = 30 if short_backoff else 300
        details.update(acquisition_cache_ttl_seconds=ttl,
                       acquisition_cache_policy="transient_failure_backoff" if short_backoff else
                       "successful_acquisition" if successful else "source_error_backoff",
                       acquisition_cache_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat())
        return items, details

    key = hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()
    # The fetch implementation identity isolates injected test/probe transports.
    (items, details), hit = cached_acquisition(
        ("static-v2", fetch_url, key), acquire_with_cache_policy,
        ttl_for_result=lambda result: result[1]["acquisition_cache_ttl_seconds"])
    details.update(acquisition_cache_hit=hit,
                   acquisition_request_count=details["request_count"])
    if hit:
        details["request_count"] = 0
    return items, details


def _collect_release_feed(source: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: dict[str, Any] = {"raw_count": 0, "errors": [], "request_count": 0,
                               "body_fetch_count": 0, "query_urls": []}
    api_url = str(source["release_api_url"]) + f"?per_page={limit}"
    try:
        details["request_count"] += 1
        details["query_urls"].append(api_url)
        response = fetch_url(api_url, allowed_domains=list(source.get("allowed_domains") or []),
                             timeout_seconds=8, retries=0, max_bytes=3_000_000)
        rows = json.loads(response.body)
        if not isinstance(rows, list):
            raise ValueError("GitHub releases response is not a list")
        details["raw_count"] = len(rows)
        items = []
        for row in rows[:limit]:
            if row.get("draft"):
                continue
            item = _news_item(source, str(row.get("name") or row.get("tag_name") or ""),
                              str(row.get("published_at") or ""), str(row.get("html_url") or ""),
                              str(row.get("body") or ""), response.retrieved_at)
            item.update(evidence_kind="release_notes", published_at_basis="github_release_published_at",
                        text=str(row.get("body") or "")[:24_000], discovery_query_url=api_url,
                        response_sha256=response.sha256, body_truncated=len(str(row.get("body") or "")) > 24_000)
            items.append(item)
        details["successful_fetch_count"] = 1
        return items, details
    except Exception as exc:  # noqa: BLE001
        details["errors"].append(_acquisition_error(api_url, exc))
    # Keep successful fallback acquisition separate from publication-date eligibility.
    url = str(source["url"])
    details["request_count"] += 1
    details["query_urls"].append(url)
    items = []
    try:
        response = fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []),
                             timeout_seconds=12, retries=0, max_bytes=3_000_000)
        items = _parse_feed(response.body, source, response.retrieved_at)
        details.update(raw_count=len(items), successful_fetch_count=1, fallback_used=True)
        for item in items:
            item.update(discovery_query_url=url, response_sha256=response.sha256)
            if not item.get("published_at"):
                item.update(collection_role="undated_source_lead", date_window_status="unverified",
                            limitations=["Atom provides an update time only; publication date and requested-window eligibility are unverified."])
    except Exception as exc:  # noqa: BLE001
        details["errors"].append(_acquisition_error(url, exc))
    return items, details


def collect_policy_sources(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for source in load_source_config().get("policy_sources", []):
        if not source.get("enabled", True):
            continue
        started_at = datetime.now(timezone.utc)
        try:
            article_errors: list[dict[str, str]] = []
            source_type = str(source.get("type") or "")
            if source_type == "official_html_list":
                parsed = _collect_official_list(source, date_from=date_from, date_to=date_to, errors=article_errors)
            elif source_type == "official_pages":
                parsed = _collect_official_pages(source, date_from=date_from, date_to=date_to, errors=article_errors)
            else:
                raise ValueError(f"unsupported policy source type: {source_type}")
            documents.extend(parsed)
            state = "partial" if article_errors and parsed else "error" if article_errors else "ok"
            statuses.append({**_source_status(source, state, len(parsed), started_at,
                                             error="; ".join(item["error"] for item in article_errors)),
                             "errors": article_errors, "article_error_count": len(article_errors),
                             "article_success_count": len(parsed)})
        except Exception as exc:  # noqa: BLE001
            statuses.append(_source_status(source, "error", 0, started_at, error=repr(exc)))

    documents = _dedupe_items(documents)
    snapshot_path = ""
    if persist and documents:
        snapshot_path = str(_persist_snapshot("policies", documents, statuses))
        for document in documents:
            document["raw_text_path"] = snapshot_path
    return {
        "documents": documents,
        "source_statuses": statuses,
        "collection_succeeded": any(item["status"] in {"ok", "partial"} for item in statuses),
        "collection_partial": any(item["status"] != "ok" for item in statuses),
        "snapshot_path": snapshot_path,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
    }


def load_latest_news_snapshot(*, discovery_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = normalize_plan(discovery_plan)
    return {**_load_latest_snapshot("news", cache_key=cache_identity(plan, load_source_config())),
            "discovery_plan": plan}


def load_latest_policy_snapshot() -> dict[str, Any]:
    return _load_latest_snapshot("policies")


def _parse_feed(body: bytes, source: dict[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    entries = root.findall(f"{ATOM}entry")
    output: list[dict[str, Any]] = []
    if entries:
        for entry in entries:
            if "/api/errors" in _xml_text(entry, f"{ATOM}id"):
                raise ValueError("arXiv API error: " + _xml_text(entry, f"{ATOM}summary"))
            title = _xml_text(entry, f"{ATOM}title")
            published = _xml_text(entry, f"{ATOM}published")
            link = ""
            for node in entry.findall(f"{ATOM}link"):
                if node.attrib.get("rel", "alternate") == "alternate" and node.attrib.get("href"):
                    link = str(node.attrib["href"])
                    break
            summary = _xml_text(entry, f"{ATOM}summary") or _xml_text(entry, f"{ATOM}content")
            item = _news_item(source, title, published, link, summary, retrieved_at)
            item["updated_at"] = _normalize_datetime(_xml_text(entry, f"{ATOM}updated"))
            if source.get("type") == "arxiv_search" or urlparse(link).hostname in {"arxiv.org", "export.arxiv.org"}:
                item.update(evidence_kind="arxiv_abstract", abstract=item["summary"],
                            full_text_fetched=False,
                            limitations=["arXiv abstract only; full paper and reported results not independently verified."],
                            authors=[_xml_text(author, f"{ATOM}name") for author in entry.findall(f"{ATOM}author")],
                            categories=[node.attrib.get("term", "") for node in entry.findall(f"{ATOM}category")])
            output.append(item)
        return [item for item in output if item["title"] and item["url"]]

    channel = root.find("channel")
    if channel is None:
        return []
    for entry in channel.findall("item"):
        title = _xml_text(entry, "title")
        published = (_xml_text(entry, "pubDate") or _xml_text(entry, "date")
                     or _xml_text(entry, "{http://purl.org/dc/elements/1.1/}date"))
        link = _xml_text(entry, "link")
        summary = _xml_text(entry, "description")
        output.append(_news_item(source, title, published, link, summary, retrieved_at))
    return [item for item in output if item["title"] and item["url"]]


def _news_item(
    source: dict[str, Any],
    title: str,
    published: str,
    link: str,
    summary: str,
    retrieved_at: str,
) -> dict[str, Any]:
    clean_summary = _html_to_text(summary)[:12_000]
    clean_title = title.strip()
    if "releases" in str(source.get("source_id") or "") and re.fullmatch(
        r"v?\d[\w.+-]*", clean_title, flags=re.IGNORECASE
    ):
        clean_title = f"{source.get('name')} {clean_title}"
    published_at = _normalize_datetime(published)
    content_hash = _content_hash(clean_title, clean_summary, link)
    return {
        "news_id": f"news_{content_hash[:16]}",
        "title": clean_title,
        "source": str(source.get("name") or source.get("source_id")),
        "source_id": str(source.get("source_id") or ""),
        "source_level": str(source.get("level") or "L5"),
        "source_type": str(source.get("type") or "rss_atom"),
        "published_at": published_at,
        "published_at_basis": "source_published" if published_at else "unknown",
        "published_at_raw": published,
        "retrieved_at": retrieved_at,
        "url": link.strip(),
        "summary": clean_summary,
        "evidence_kind": "feed_summary",
        "summary_truncated": len(_html_to_text(summary)) > 12_000,
        "full_text_fetched": False,
        "topics": list(source.get("topics") or []),
        "data_mode": "live",
        "is_fixture": False,
        "content_hash": content_hash,
    }


def _collect_policy_evidence(
    source: dict[str, Any], plan: dict[str, Any], limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: dict[str, Any] = {"raw_count": 0, "errors": [], "request_count": 0,
                               "body_fetch_count": 0, "query_urls": []}
    candidates: list[dict[str, Any]] = []
    if source.get("type") == "official_html_list":
        url = str(source["list_url"])
        details["query_urls"] = [url]
        details["request_count"] += 1
        response = fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []),
                             timeout_seconds=8, retries=0, max_bytes=2_000_000)
        parser = _LinkExtractor()
        parser.feed(_decode_html(response.body, response.content_type))
        pattern = re.compile(str(source.get("article_url_regex") or ".*"))
        seen: set[str] = set()
        for href, title in parser.links:
            link = urljoin(url, href)
            if (link in seen or not domain_allowed(link, list(source.get("allowed_domains") or []))
                    or not pattern.search(urlparse(link).path)):
                continue
            seen.add(link)
            details["raw_count"] += 1
            if any(str(term).casefold() in title.casefold() for term in source.get("exclude_title_keywords", [])):
                continue
            candidates.append({"url": link, "title": title, "published_at": _date_from_url(link),
                               "published_at_basis": "url_date"})
    else:
        candidates = [dict(page) for page in source.get("pages") or []]
        details["raw_count"] = len(candidates)
    terms = query_terms(plan, source)
    candidates.sort(key=lambda page: (
        sum(matches_term(str(page.get("title") or ""), term) for term in terms),
        str(page.get("published_at") or "")), reverse=True)
    candidates = [page for page in candidates
                  if (not page.get("published_at") or _date_in_range(str(page["published_at"]),
                       plan["date_from"], plan["date_to"], query_timezone=plan["query_timezone"]))
                  and not any(matches_term(str(page.get("title") or ""), term)
                              for term in plan["excluded_topics"])]
    body_limit = min(limit, bounded_int(source.get("max_body_fetches"), 12, 20, minimum=0))
    documents = []
    for page in candidates[:body_limit]:
        details["request_count"] += 1
        details["body_fetch_count"] += 1
        try:
            document = _fetch_policy_page(source, page, bounded=True)
            item = _news_item(source, document["title"], document["published_at"],
                              document["source_url"], document["text"][:12_000], document["retrieved_at"])
            item.update(document)
            item.update(source_level="L1", evidence_kind="official_policy_text",
                        full_text_fetched=True, summary=document["text"][:2400],
                        summary_truncated=len(document["text"]) > 2400,
                        url=document["source_url"], topics=document["policy_type"],
                        limitations=["Extracted official article text; not a legal interpretation."])
            documents.append(item)
        except Exception as exc:  # noqa: BLE001
            details["errors"].append({"url": str(page.get("url") or ""), "error": f"{type(exc).__name__}: {exc}"})
    details["body_limit_skipped_count"] = max(0, len(candidates) - body_limit)
    details["article_error_count"] = len(details["errors"])
    details["article_success_count"] = len(documents)
    return documents, details


def _collect_official_list(
    source: dict[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
    errors: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    list_url = str(source["list_url"])
    response = fetch_url(list_url, allowed_domains=list(source.get("allowed_domains") or []))
    parser = _LinkExtractor()
    parser.feed(_decode_html(response.body, response.content_type))
    pattern = re.compile(str(source.get("article_url_regex") or ".*"))
    keywords = [str(item).lower() for item in source.get("keywords") or []]
    excluded = [str(item).lower() for item in source.get("exclude_title_keywords") or []]
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = urljoin(list_url, href)
        if url in seen or not domain_allowed(url, list(source.get("allowed_domains") or [])):
            continue
        if not pattern.search(urlparse(url).path):
            continue
        if keywords and not any(keyword in title.lower() for keyword in keywords):
            continue
        if excluded and any(keyword in title.lower() for keyword in excluded):
            continue
        published_at = _date_from_url(url)
        if not _date_in_range(published_at, date_from, date_to):
            continue
        seen.add(url)
        candidates.append({"url": url, "title": title, "published_at": published_at})

    documents: list[dict[str, Any]] = []
    article_errors = errors if errors is not None else []
    for candidate in candidates[: int(source.get("max_items") or 10)]:
        try:
            documents.append(_fetch_policy_page(source, candidate))
        except Exception as exc:  # noqa: BLE001
            article_errors.append({"url": str(candidate["url"]), "error": f"{type(exc).__name__}: {exc}"})
    return documents


def _collect_official_pages(
    source: dict[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
    errors: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    documents = []
    article_errors = errors if errors is not None else []
    for page in source.get("pages") or []:
        published_at = str(page.get("published_at") or "")
        if not _date_in_range(published_at, date_from, date_to):
            continue
        try:
            documents.append(_fetch_policy_page(source, page))
        except Exception as exc:  # noqa: BLE001
            article_errors.append({"url": str(page.get("url") or ""), "error": f"{type(exc).__name__}: {exc}"})
    return documents


def _fetch_policy_page(source: dict[str, Any], page: dict[str, Any], *, bounded: bool = False) -> dict[str, Any]:
    url = str(page["url"])
    options = {"timeout_seconds": 8, "retries": 0, "max_bytes": 2_000_000} if bounded else {}
    response = fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []), **options)
    extractor = _TextExtractor()
    html = _decode_html(response.body, response.content_type)
    extractor.feed(html)
    title = str(page.get("title") or extractor.heading or url)
    text, selector = extract_article(html, list(source.get("article_selectors") or []))
    content_hash = _content_hash(title, text, url)
    published_at = ""
    basis = "unknown"
    for key in ("article:published_time", "date", "pubdate", "publishdate", "pub_date", "dcterms.issued", "dc.date.issued"):
        published_at = _normalize_datetime(extractor.metadata.get(key, ""))
        if published_at:
            basis = "html_meta:" + key
            break
    if not published_at:
        published_at = _normalize_datetime(str(page.get("published_at") or ""))
        basis = str(page.get("published_at_basis") or "configured_publication_date") if published_at else "unknown"
    if not published_at:
        published_at = _date_from_url(url)
        basis = "url_date" if published_at else "unknown"
    return {
        "policy_id": f"policy_{content_hash[:16]}",
        "title": title,
        "issuer": str(source.get("issuer") or ""),
        "region_scope": list(source.get("region_scope") or ["全国"]),
        "published_at": published_at,
        "published_at_basis": basis,
        "retrieved_at": response.retrieved_at,
        "source_url": url,
        "source_id": str(source.get("source_id") or ""),
        "source_level": str(source.get("level") or "L1"),
        "source_type": str(source.get("type") or "official_page"),
        "data_mode": "live",
        "is_fixture": False,
        "policy_type": list(page.get("policy_type") or source.get("keywords") or []),
        "raw_text_path": "",
        "text": text[:120_000],
        "body_truncated": len(text) > 120_000,
        "article_body_selector": selector,
        "content_hash": content_hash,
        "response_sha256": response.sha256,
    }


def _persist_snapshot(
    kind: str, items: list[dict[str, Any]], statuses: list[dict[str, Any]], *,
    cache_key: str | None = None, metadata: dict[str, Any] | None = None,
) -> Path:
    payload = {
        **(metadata or {}),
        "schema_version": "2.0" if cache_key else "1.0",
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_statuses": statuses,
        "items": items,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    root = project_root() / "data" / kind / "snapshots"
    dated = root / date.today().isoformat()
    dated.mkdir(parents=True, exist_ok=True)
    path = dated / f"{kind}-snapshot-{digest}.json"
    _atomic_write(path, encoded)
    root.mkdir(parents=True, exist_ok=True)
    latest = root / (f"latest-{cache_key}.json" if cache_key else "latest.json")
    _atomic_write(latest, encoded)
    return path


def _atomic_write(path: Path, encoded: str) -> None:
    handle, temporary = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(encoded)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_latest_snapshot(kind: str, *, cache_key: str | None = None) -> dict[str, Any]:
    empty = {"items": [], "source_statuses": [], "snapshot_path": "", "snapshot_key": cache_key or "",
             "snapshot_id": "", "cleaning_diagnostics": {}, "collection_succeeded": False}
    # News callers must always compute a plan+config identity; never use legacy latest.json.
    if kind == "news" and not cache_key:
        return empty
    path = project_root() / "data" / kind / "snapshots" / (f"latest-{cache_key}.json" if cache_key else "latest.json")
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("invalid snapshot schema")
        if cache_key and (payload.get("snapshot_key") != cache_key or payload.get("schema_version") != "2.0"):
            return {**empty, "snapshot_error": "snapshot identity mismatch"}
    except (OSError, ValueError) as exc:
        return {**empty, "snapshot_error": f"{type(exc).__name__}: {exc}"}
    items = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            return {**empty, "snapshot_error": "invalid snapshot item"}
        copied = dict(item)
        copied["data_mode"] = "source_snapshot"
        items.append(copied)
    return {
        **payload,
        "items": items,
        "source_statuses": payload.get("source_statuses") or [],
        "captured_at": payload.get("captured_at"),
        "snapshot_path": str(path),
    }


def _fresh_snapshot(kind: str, ttl_seconds: int, *, cache_key: str | None = None) -> dict[str, Any] | None:
    snapshot = _load_latest_snapshot(kind, cache_key=cache_key)
    captured_at = str(snapshot.get("captured_at") or "")
    if not captured_at or not snapshot.get("collection_succeeded", bool(snapshot.get("items"))):
        return None
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
    # A partial batch must not extend a transient source's original cooldown.
    for status in snapshot.get("source_statuses") or []:
        if status.get("acquisition_cache_policy") != "transient_failure_backoff":
            continue
        try:
            expires = datetime.fromisoformat(status["acquisition_cache_expires_at"].replace("Z", "+00:00"))
            if expires.tzinfo is None or datetime.now(timezone.utc) >= expires:
                return None
        except (KeyError, TypeError, ValueError):
            return None
    return snapshot if 0 <= age <= max(0, ttl_seconds) else None


def _source_status(
    source: dict[str, Any],
    status: str,
    item_count: int,
    started_at: datetime,
    checksum: str = "",
    error: str = "",
) -> dict[str, Any]:
    elapsed_ms = round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000, 2)
    return {
        "source_id": source.get("source_id"),
        "name": source.get("name"),
        "level": source.get("level"),
        "type": source.get("type"),
        "url": source.get("url") or source.get("list_url") or source.get("path") or "",
        "status": status,
        "item_count": item_count,
        "elapsed_ms": elapsed_ms,
        "checksum": checksum,
        "error": error,
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deduplicate(items)


def _xml_text(node: ET.Element, path: str) -> str:
    child = node.find(path)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return " ".join(parser.parts)


def _decode_html(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _normalize_datetime(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return ""
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed.isoformat()
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.isoformat()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return ""


def _date_from_url(url: str) -> str:
    path = urlparse(url).path
    match = re.search(r"/(20\d{2})[-/](\d{2})[-/](\d{2})(?:/|_)", path)
    if not match:
        match = re.search(r"(?:/t|/)(20\d{2})(\d{2})(\d{2})(?:/|_)", path)
    try:
        return date(*map(int, match.groups())).isoformat() if match else ""
    except ValueError:
        return ""


def _date_in_range(
    value: str, date_from: str | None, date_to: str | None, *, query_timezone: str = "Asia/Shanghai",
) -> bool:
    if not value:
        return not date_from and not date_to
    normalized = _normalize_datetime(value)
    if not normalized:
        return not date_from and not date_to
    day, _, _ = publication_local_date(normalized, query_timezone)
    if not day:
        return not date_from and not date_to
    if date_from and day < date_from[:10]:
        return False
    if date_to and day > date_to[:10]:
        return False
    return True


def _content_hash(title: str, text: str, url: str) -> str:
    payload = f"{title.strip()}\n{url.strip()}\n{text.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
