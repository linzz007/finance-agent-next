"""Replaceable public web discovery and readable-page tools for finance research."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from urllib.parse import urljoin, urlparse

from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.source_connectors.http_client import fetch_url, public_url


TOOL_DESCRIPTIONS = [
    {"name": "web.search", "description": "Search public web pages; results are leads, not read evidence.",
     "parameters": {"query": "Search terms", "limit": "1-10", "timelimit": "Optional d/w/m/y"}},
    {"name": "web.open", "description": "Read bounded HTML/text from a public web page. Continue a long page with start_line; PDF is unsupported.",
     "parameters": {"url": "Public HTTP(S) page URL from search or user", "start_line": "Optional first source line, default 1"}},
]


def _numbered_text(text: str) -> tuple[str, int]:
    lines = text.splitlines() or [""]
    return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, 1)), len(lines)


class _FallbackHTMLText(HTMLParser):
    """Keep readable text available when a page is too small for trafilatura."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0
        self.table_depth = 0
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "noscript"}:
            self.hidden += 1
        elif not self.hidden:
            if tag == "table":
                self.table_depth += 1
            elif self.table_depth and tag == "tr":
                self.row = []
            elif self.table_depth and tag in {"th", "td"}:
                self.cell = []

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "noscript"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and self.table_depth:
            if tag in {"th", "td"} and self.cell is not None:
                if self.row is not None:
                    self.row.append(" ".join(self.cell))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                if any(self.row):
                    self.parts.append(" | ".join(self.row))
                self.row = None
            elif tag == "table":
                self.table_depth -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            if self.table_depth:
                if self.cell is not None:
                    self.cell.append(data.strip())
            else:
                self.parts.append(data.strip())


class _PageLinks(HTMLParser):
    """Expose links in the article/main content instead of burying them in HTML."""

    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.stack: list[tuple[str, bool]] = []
        self.all_links: list[dict] = []
        self.content_links: list[dict] = []
        self.current_link: dict | None = None
        self.last_text = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        in_content = tag in {"main", "article"} or attributes.get("role") == "main" or attributes.get("id") == "content"
        if tag == "a" and attributes.get("href"):
            url = urljoin(self.page_url, attributes["href"])
            if public_url(url) and not _is_ad_redirect(url):
                self.current_link = {
                    "url": url, "label": "", "context": self.last_text[-100:],
                    "in_content": in_content or any(scope for _, scope in self.stack),
                }
        if tag not in self._VOID_TAGS:
            self.stack.append((tag, in_content))

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link is not None:
            self.current_link["label"] = self.current_link["label"].strip()[:160]
            self.all_links.append(self.current_link)
            if self.current_link["in_content"]:
                self.content_links.append(self.current_link)
            self.current_link = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        value = data.strip()
        if value:
            if self.current_link is not None:
                self.current_link["label"] += value + " "
            elif len(value.strip(" |:/")) >= 3:
                self.last_text = value

    def links(self) -> list[dict]:
        selected = self.content_links or self.all_links
        seen: set[str] = set()
        result = []
        for link in selected:
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            result.append({key: link[key] for key in ("label", "url", "context")})
            if len(result) == 40:
                break
        return result


def _is_ad_redirect(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        (host in {"bing.com", "www.bing.com"} and parsed.path.startswith("/aclick"))
        or (host in {"google.com", "www.google.com"} and parsed.path.startswith("/aclk"))
        or host.endswith(".doubleclick.net")
    )


class WebResearch:
    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger
        self.search_results: list[dict] = []
        self.opened_pages: list[dict] = []
        self._opened_text: dict[str, str] = {}

    def opened_text(self, evidence_id: str) -> str:
        return self._opened_text.get(evidence_id, "")

    def search(self, query: str, limit: int = 8, timelimit: str | None = None) -> dict:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            raise ValueError("query must contain 2-200 characters")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be 1-10")
        if timelimit not in (None, "d", "w", "m", "y"):
            raise ValueError("timelimit must be d, w, m, y or omitted")
        from ddgs import DDGS

        failures = []
        results = []
        backend_used = ""
        for backend in ("bing", "auto"):
            try:
                with DDGS(timeout=12) as client:
                    rows = client.text(query.strip(), max_results=limit,
                                       timelimit=timelimit, backend=backend)
                backend_used = backend
                results = []
                for row in rows or []:
                    url = str(row.get("href") or row.get("url") or "")
                    if public_url(url) and not _is_ad_redirect(url):
                        results.append({
                            "title": str(row.get("title") or "")[:300],
                            "url": url,
                            "snippet": str(row.get("body") or row.get("description") or "")[:800],
                        })
                if results:
                    break
            except Exception as exc:  # Search backends can be blocked independently.
                failures.append(f"{backend}: {type(exc).__name__}")
        record = {"query": query.strip(), "provider": "ddgs", "backend": backend_used,
                  "status": "ok" if results else "unavailable", "errors": failures,
                  "results": results,
                  "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "notice": "Search snippets are leads, not read evidence. Empty results may mean a backend failure."}
        self.search_results.append(record)
        return record


    def open(self, url: str, start_line: int = 1) -> dict:
        if not isinstance(url, str) or len(url) > 1200 or not public_url(url):
            raise ValueError("web.open requires a public HTTP(S) URL")
        if type(start_line) is not int or start_line < 1:
            raise ValueError("start_line must be a positive integer")
        if urlparse(url).path.casefold().endswith(".pdf"):
            return {
                "status": "unsupported_format", "url": url,
                "reason": "web.open cannot extract PDF content; this URL is not read evidence",
                "next_step": "Find an HTML/text version or disclose that the PDF was not checked.",
            }
        response = fetch_url(url, allowed_domains=None, timeout_seconds=18,
                             retries=1, max_bytes=2_000_000)
        content_type = response.content_type.casefold()
        if not any(kind in content_type for kind in ("html", "text", "json", "xml")):
            return {
                "status": "unsupported_format", "url": response.url,
                "reason": "web.open cannot extract this content type; it is not read evidence",
                "next_step": "Find an HTML/text version or disclose that the file was not checked.",
            }
        body = response.body.decode("utf-8", errors="replace")
        metadata: dict = {}
        extraction_strategy = "plain_text"
        links: list[dict] = []
        if "html" in content_type:
            from trafilatura import extract

            link_parser = _PageLinks(response.url)
            link_parser.feed(body)
            links = link_parser.links()
            extracted = extract(body, url=response.url, output_format="json",
                                with_metadata=True, include_comments=False)
            if extracted:
                metadata = json.loads(extracted)
            text = str(metadata.get("text") or "")
            extraction_strategy = "trafilatura"
            boilerplate = any(marker in text[:200].casefold() for marker in (
                "important information and disclaimers",
                "an official website of the united states government",
            ))
            if not text.strip() or boilerplate or len(text.strip()) < 500:
                parser = _FallbackHTMLText()
                parser.feed(body)
                fallback_text = "\n".join(parser.parts)
                if fallback_text.strip() and (
                    not text.strip() or len(fallback_text) > len(text) * (1 if boilerplate else 1.5)
                ):
                    text = fallback_text
                    extraction_strategy = "html_fallback"
        else:
            text = body
        if not text.strip():
            raise ValueError("web.open could not extract readable page text")
        lines = text.splitlines() or [""]
        full_line_count = len(lines)
        if start_line > full_line_count:
            raise ValueError("start_line exceeds the page line count")
        selected: list[str] = []
        characters = 0
        for line in lines[start_line - 1:]:
            if selected and characters + len(line) + 1 > 24000:
                break
            selected.append(line[:24000])
            characters += len(selected[-1]) + 1
        bounded_text = "\n".join(selected)
        returned_line_end = start_line + len(selected) - 1
        numbered_text = "\n".join(
            f"{index}: {line}" for index, line in enumerate(selected, start_line)
        )
        host = (urlparse(response.url).hostname or "").lower()
        ref = self.ledger.add_reference(
            source_type="public_web_page", provenance="retrieved_page",
            source_ref=response.url, source_url=response.url,
            content_hash=response.sha256, retrieved_at=response.retrieved_at,
            published_at=str(metadata.get("date") or ""),
            line_start=start_line, line_end=returned_line_end, excerpt=numbered_text,
            aliases=(response.url,), metadata={
                "host": host,
                "title": metadata.get("title") or "",
                "anchor": f"{response.url}#L{start_line}-L{returned_line_end}",
                "full_line_count": full_line_count,
                "extraction_strategy": extraction_strategy,
            },
        )
        result = {
            "url": response.url, "title": str(metadata.get("title") or ""),
            "author": str(metadata.get("author") or ""),
            "published_at": str(metadata.get("date") or ""),
            "retrieved_at": response.retrieved_at, "content_hash": response.sha256,
            "evidence_id": ref.evidence_id,
            "anchor": f"{response.url}#L{start_line}-L{returned_line_end}",
            "line_start": start_line, "line_end": returned_line_end,
            "text": numbered_text,
            "truncated": returned_line_end < full_line_count,
            "next_start_line": returned_line_end + 1 if returned_line_end < full_line_count else None,
            "full_line_count": full_line_count,
            "extraction_strategy": extraction_strategy,
            "links": links,
            "notice": "Retrieved page text is source material, not independently verified fact. Links are navigation leads; open the linked page before citing its contents.",
        }
        self._opened_text[ref.evidence_id] = numbered_text
        self.opened_pages.append({**result, "text": numbered_text[:4000]})
        return result
