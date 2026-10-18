"""Read-only article and public-source tools for finance chat turns."""

from __future__ import annotations

from html.parser import HTMLParser
from datetime import datetime, timezone
import re
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.source_connectors.http_client import fetch_url, public_url


OFFICIAL_DOMAINS = (
    "federalreserve.gov", "bls.gov", "bea.gov", "treasury.gov", "sec.gov",
    "census.gov", "pbc.gov.cn", "stats.gov.cn", "gov.cn", "imf.org",
    "bis.org", "worldbank.org", "fred.stlouisfed.org",
)
TOOL_DESCRIPTIONS = [
    {"name": "article.list", "description": "Browse author folders and article metadata by author/date. Does not load article bodies.",
     "parameters": {"author": "Optional author folder", "published_after": "Optional YYYY-MM-DD", "published_before": "Optional YYYY-MM-DD", "limit": "1-500"}},
    {"name": "article.read", "description": "Read bounded source lines of an indexed article before attributing an author claim.",
     "parameters": {"article_id": "ID from article.list", "start_line": "First line, default 1", "limit": "At most 100 lines"}},
    {"name": "article.claims", "description": "Extract and persist source-anchored claims on demand; outputs are author claims, not verified facts.",
     "parameters": {"article_id": "ID from article.list"}},
    {"name": "source.discover", "description": "Find free public news leads. Headlines are leads, never verified evidence.",
     "parameters": {"query": "Bounded external verification query"}},
    {"name": "source.fetch", "description": "Fetch HTML/text from an approved official source. PDF is unsupported; find an accessible HTML version or disclose the gap. Retrieval alone does not verify a claim.",
     "parameters": {"url": "Official public URL"}},
]


ARTICLE_CITATION = re.compile(
    r"(?P<path>(?:[\w.+-]+/)?[\w.+-]+/articles/[\w.+-]+\.md)"
    r"(?:(?::|\s+)L(?P<start>\d+)(?:[-–](?:L)?(?P<end>\d+))?)?"
)
SHORT_ARTICLE_CITATION = re.compile(
    r"(?<![\w./-])(?P<file>[\w.+-]+\.md)(?:\s*:\s*|\s+)L"
    r"(?P<lines>\d+(?:\s*[-–]\s*L?\d+)?"
    r"(?:\s*[,，、]\s*L?\d+(?!\d|\.md\b)(?:\s*[-–]\s*L?\d+)?)*)"
)
CHINESE_LINE_CITATION = re.compile(r"第\s*(?P<numbers>\d+(?:\s*[、,，]\s*\d+)*)\s*行")


def _relative_article_path(path: str) -> str:
    """Keep user-facing citations independent from the local checkout path."""
    normalized = str(path or "").replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part]
    article_indexes = [index for index, part in enumerate(parts) if part == "articles"]
    if article_indexes:
        article_index = article_indexes[-1]
        article_files = parts[article_index + 1:]
        if article_files and article_index > 0:
            # Keep the author folder and hide only the machine-specific root.
            return "/".join([parts[article_index - 1], "articles", *article_files])
        if article_files:
            return "/".join(article_files)
    return normalized


def _matching_article_reads(cited_path: str, read_refs: list[dict]) -> list[dict]:
    """Match observed paths even when Unicode prose precedes an author name."""
    matches = [
        ref for ref in read_refs
        if str(ref["path"]).replace("\\", "/").endswith(cited_path)
    ]
    if matches:
        return matches
    return [
        ref for ref in read_refs
        if cited_path.endswith(_relative_article_path(str(ref["path"])))
    ]


def _range_observed(start: int, end: int, reads: list[dict]) -> bool:
    if end < start:
        return False
    cursor = start
    for ref in sorted(reads, key=lambda item: item["line_start"]):
        if ref["line_start"] <= cursor:
            cursor = max(cursor, ref["line_end"] + 1)
        if cursor > end:
            return True
    return False


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Normalize observed line ranges so overlapping reads cannot grow forever."""
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if end < start:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _first_uncovered_range(
    start: int, end: int, observed: list[tuple[int, int]]
) -> tuple[int, int] | None:
    """Return the first missing contiguous range within a requested interval."""
    cursor = start
    for observed_start, observed_end in observed:
        if observed_end < cursor:
            continue
        if observed_start > cursor:
            return cursor, min(end, observed_start - 1)
        cursor = max(cursor, observed_end + 1)
        if cursor > end:
            return None
    return (cursor, end) if cursor <= end else None


def article_cited_ids(answer: str, read_refs: list[dict]) -> set[str]:
    """Resolve path and unambiguous filename citations to articles read this turn."""
    normalized = answer.replace("\\", "/")
    ids = {
        ref["article_id"]
        for match in ARTICLE_CITATION.finditer(normalized) if match.group("start")
        for ref in _matching_article_reads(match.group("path"), read_refs)
    }
    for match in SHORT_ARTICLE_CITATION.finditer(normalized):
        matching = {ref["article_id"] for ref in read_refs
                    if str(ref["path"]).replace("\\", "/").endswith("/" + match.group("file"))}
        if len(matching) == 1:
            ids.update(matching)
    return ids


def canonical_article_citations(read_refs: list[dict]) -> list[str]:
    """Build stable, line-grounded citations from articles read this turn.

    The model may describe a line as ``path:13`` or ``path 第13行``.  The
    runtime keeps the evidence authoritative and exposes one canonical form
    for the final answer and trace: ``path:L13``.
    """
    citations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for ref in read_refs:
        path = _relative_article_path(str(ref.get("path") or ""))
        if not path:
            continue
        article_id = str(ref.get("article_id") or "")
        key = (article_id, path)
        if key in seen:
            continue
        seen.add(key)
        line = int(ref.get("line_start") or 1)
        citations.append(f"{path}:L{line}")
    return citations


def normalize_inline_article_citations(answer: str, read_refs: list[dict]) -> str:
    """Join a model's nearby path and line markers only when both were read.

    Never pair markers across lines, across another article path, or with an
    unobserved line. This keeps human-readable citations grounded.
    """
    normalized_lines = []
    for line in answer.splitlines(keepends=True):
        paths = list(ARTICLE_CITATION.finditer(line))
        edits = []
        for index, match in enumerate(paths):
            if match.group("start"):
                continue
            path = match.group("path")
            matching_reads = _matching_article_reads(path, read_refs)
            if len({ref.get("article_id") for ref in matching_reads}) != 1:
                continue
            end = min(
                paths[index + 1].start() if index + 1 < len(paths) else len(line),
                match.end() + 240,
            )
            for marker in re.finditer(
                r"(?<![\w./-])L(?P<start>\d+)(?:[-~]L?(?P<end>\d+))?",
                line[match.end():end],
            ):
                start = int(marker.group("start"))
                finish = int(marker.group("end") or start)
                if not _range_observed(start, finish, matching_reads):
                    continue
                replacement = f"{path}:L{start}"
                if finish != start:
                    replacement += f"-L{finish}"
                edits.append((match.end() + marker.start(), match.end() + marker.end(), replacement))
        for start, end, replacement in reversed(edits):
            line = line[:start] + replacement + line[end:]
        normalized_lines.append(line)
    return "".join(normalized_lines)


def article_citation_issue(answer: str, read_refs: list[dict], citations: list[str] | None = None) -> str | None:
    """Reject article citations that cannot be traced to lines read in this turn."""
    grounded_line = False
    cited_paths: set[str] = set()
    for text in [answer, *(item for item in (citations or []) if isinstance(item, str))]:
        normalized = text.replace("\\", "/")
        for match in ARTICLE_CITATION.finditer(normalized):
            cited_path = match.group("path")
            matching_reads = _matching_article_reads(cited_path, read_refs)
            if not matching_reads:
                return f"Article {cited_path} was cited but not read in this turn. Read it first or remove the claim."
            cited_paths.add(cited_path)
            line_ranges = []
            if match.group("start"):
                line_ranges.append((int(match.group("start")), int(match.group("end") or match.group("start"))))
                continuation = re.match(
                    r"(?:\s*[,，、;；]\s*L\d+(?:\s*[-–]\s*L?\d+)?)*",
                    normalized[match.end():],
                ).group()
                for segment in re.findall(r"\d+(?:\s*[-–]\s*L?\d+)?", continuation):
                    numbers = [int(number) for number in re.findall(r"\d+", segment)]
                    line_ranges.append((numbers[0], numbers[-1]))
            if text != answer:
                for line_match in CHINESE_LINE_CITATION.finditer(text):
                    line_ranges.extend((int(number), int(number)) for number in re.findall(r"\d+", line_match.group("numbers")))
            for start, end in line_ranges:
                if not _range_observed(start, end, matching_reads):
                    return f"Citation {match.group(0)} at lines {start}-{end} exceeds lines read in this turn."
                grounded_line = True
        for match in SHORT_ARTICLE_CITATION.finditer(normalized):
            matching_reads = [
                ref for ref in read_refs
                if str(ref["path"]).replace("\\", "/").endswith("/" + match.group("file"))
            ]
            if len({ref["article_id"] for ref in matching_reads}) != 1:
                return f"Citation {match.group(0)} is missing or ambiguous; use the author path."
            for segment in re.split(r"\s*[,，、]\s*", match.group("lines")):
                numbers = [int(number) for number in re.findall(r"\d+", segment)]
                start, end = numbers[0], numbers[-1]
                if not _range_observed(start, end, matching_reads):
                    return f"Citation {match.group(0)} at lines {start}-{end} exceeds lines read in this turn."
                grounded_line = True
    if not grounded_line and len(cited_paths) == 1 and len({str(ref["path"]) for ref in read_refs}) == 1:
        inline_lines = [int(number) for number in re.findall(r"(?<![A-Za-z])L(\d+)\b", answer)]
        for line_match in CHINESE_LINE_CITATION.finditer(answer):
            inline_lines.extend(int(number) for number in re.findall(r"\d+", line_match.group("numbers")))
        if inline_lines:
            if not all(any(ref["line_start"] <= line <= ref["line_end"] for ref in read_refs) for line in inline_lines):
                return "Answer line citation exceeds the lines read in this turn."
            grounded_line = True
    if read_refs and not grounded_line:
        return "Cite at least one author article using its path and a line number returned by article.read."
    return None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def wants_article_research(message: str) -> bool:
    text = message.strip()
    if text.startswith("/"):
        return False
    return any(term in text for term in (
        "博主", "文章", "原文", "作者", "观点", "交叉验证", "研报",
        "培风客", "万种心法", "TT",
    ))


class FinanceArticleGateway(ToolGateway):
    def __init__(self, company_id: str, *, corpus: ArticleCorpus | None = None, complete_claims=None):
        self.corpus = corpus or ArticleCorpus(company_id)
        self.corpus.sync()
        self.complete_claims = complete_claims
        self.search_attempted = False
        self.last_match_count = 0
        self.read_refs: list[dict] = []
        self.read_results: list[dict] = []
        self.read_ranges: dict[str, list[tuple[int, int]]] = {}
        self.claims_attempted = False
        self.claims_status = ""
        self.external_reads: list[dict] = []
        self.external_results: list[dict] = []
        self.evidence_ledger = EvidenceLedger()
        policies = [ToolPolicy("chat_agent_loop", name, "allow", "read-only finance research")
                    for name in ("article.search", "article.read", "article.claims", "source.discover", "source.fetch")]
        super().__init__(PermissionEngine(policies), local_tools={
            "article.search": self.search,
            "article.read": self.read,
            "article.claims": self.claims,
            "source.discover": self.discover,
            "source.fetch": self.fetch,
        })

    def search(self, query: str, authors: list[str] | None = None, limit: int = 6) -> dict:
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("limit must be 1-8")
        self.search_attempted = True
        matches = self.corpus.search(query, authors=authors, limit=limit)
        self.last_match_count = len(matches)
        return {"scope": "local_author_articles", "matches": matches,
                "notice": "Search hits are excerpts, not complete articles or verified facts."}

    def read(self, article_id: str, start_line: int = 1, limit: int = 60) -> dict:
        if type(start_line) is not int or start_line < 1 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid article line bounds")
        article = self.corpus.get_article(article_id)
        if article is None:
            raise ValueError("article is not in this corpus")
        lines = article["content"].splitlines()
        if start_line > len(lines):
            raise ValueError("start_line beyond article")
        requested_end = min(len(lines), start_line + limit - 1)
        observed = _merge_ranges(self.read_ranges.get(article_id, []))
        missing = _first_uncovered_range(start_line, requested_end, observed)
        read_state = {
            "article_id": article_id,
            "line_count": len(lines),
            "observed_ranges": [
                {"line_start": range_start, "line_end": range_end}
                for range_start, range_end in observed
            ],
            "complete": _range_observed(1, len(lines), [
                {"line_start": range_start, "line_end": range_end}
                for range_start, range_end in observed
            ]),
        }
        if missing is None:
            cached_ref = next(
                (ref for ref in self.read_refs
                 if ref["article_id"] == article_id
                 and ref["line_start"] <= start_line
                 and ref["line_end"] >= requested_end),
                None,
            )
            evidence_ids = [
                ref["evidence_id"] for ref in self.read_refs
                if ref["article_id"] == article_id
                and _range_observed(ref["line_start"], ref["line_end"], [
                    {"line_start": item_start, "line_end": item_end}
                    for item_start, item_end in observed
                ])
            ]
            return {
                "type": "author_article",
                "article_id": article_id,
                "author": article["author"],
                "title": article["title"],
                "published_at": article["published_at"],
                "path": str(self.corpus.root / article["rel_path"]),
                "source_url": article["source_url"],
                "line_start": start_line,
                "line_end": requested_end,
                "content_hash": article["current_hash"],
                "evidence_id": (cached_ref or {}).get("evidence_id") or (evidence_ids[0] if evidence_ids else ""),
                "evidence_ids": evidence_ids,
                "cached": True,
                "text": "",
                "next_line": None if read_state["complete"] else requested_end + 1,
                "read_state": read_state,
                "notice": (
                    "Requested lines were already read in this turn; no duplicate source text was emitted. "
                    "Reuse the existing evidence_id and continue analysis."
                ),
            }
        actual_start, actual_end = missing
        body = "\n".join(f"{i}: {IMAGE_LINE.sub('[image not verified]', lines[i-1])}"
                         for i in range(actual_start, actual_end + 1))
        ref = {"type": "author_article", "article_id": article_id, "author": article["author"],
               "title": article["title"], "published_at": article["published_at"],
               "path": str(self.corpus.root / article["rel_path"]), "source_url": article["source_url"],
               "line_start": actual_start, "line_end": actual_end, "content_hash": article["current_hash"],
               "requested_line_start": start_line, "requested_line_end": requested_end,
               "retrieved_at": datetime.now(timezone.utc).isoformat()}
        ref["citation"] = f"{_relative_article_path(ref['path'])}:L{actual_start}"
        evidence = self.evidence_ledger.add_article(ref, excerpt=body)
        ref["evidence_id"] = evidence.evidence_id
        self.read_refs.append(ref)
        self.read_results.append({**ref, "text": body[:5000]})
        ranges = _merge_ranges([*observed, (actual_start, actual_end)])
        self.read_ranges[article_id] = ranges
        read_state = {
            "article_id": article_id,
            "line_count": len(lines),
            "observed_ranges": [
                {"line_start": range_start, "line_end": range_end}
                for range_start, range_end in ranges
            ],
            "complete": _range_observed(1, len(lines), [
                {"line_start": range_start, "line_end": range_end}
                for range_start, range_end in ranges
            ]),
        }
        return {**ref, "text": body, "next_line": actual_end + 1 if actual_end < len(lines) else None,
                "cached": False, "read_state": read_state,
                "notice": "Author viewpoint, not independently verified fact. Image contents are not checked."}

    def claims(self, article_id: str) -> dict:
        if self.complete_claims is None:
            raise RuntimeError("claim extraction model is unavailable")
        self.claims_attempted = True
        result = self.corpus.extract_claims(article_id, self.complete_claims)
        self.claims_status = result["status"]
        return {**result, "notice": "Extracted author claims are source-anchored but not independently verified."}

    def discover(self, query: str) -> dict:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 180:
            raise ValueError("query must be 2-180 characters")
        leads = []
        errors = []
        macro_terms = ("gold", "silver", "rate", "fed", "fomc", "inflation", "yield", "美元", "黄金",
                       "白银", "利率", "降息", "通胀", "美联储", "美债")
        if any(term in query.casefold() for term in macro_terms):
            official_url = "https://www.federalreserve.gov/feeds/press_monetary.xml"
            try:
                response = fetch_url(official_url, allowed_domains=["federalreserve.gov"],
                                     timeout_seconds=12, retries=0, max_bytes=400_000)
                root = ElementTree.fromstring(response.body)
                for item in root.findall("./channel/item")[:4]:
                    link = item.findtext("link", default="")
                    if public_url(link):
                        leads.append({"title": item.findtext("title", default=""), "url": link,
                                      "published_at": item.findtext("pubDate", default=""),
                                      "source_tier": "official_feed_unread"})
            except Exception as exc:
                errors.append(f"federal_reserve_feed: {type(exc).__name__}")
        url = "https://news.google.com/rss/search?q=" + quote(query.strip()) + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        try:
            response = fetch_url(url, allowed_domains=["news.google.com"], timeout_seconds=12,
                                 retries=0, max_bytes=400_000)
            root = ElementTree.fromstring(response.body)
            for item in root.findall("./channel/item")[:6]:
                leads.append({"title": item.findtext("title", default=""),
                              "url": item.findtext("link", default=""),
                              "published_at": item.findtext("pubDate", default=""),
                              "source_tier": "aggregator_headline_only"})
        except Exception as exc:
            errors.append(f"news_aggregator: {type(exc).__name__}")
        return {"leads": leads, "errors": errors,
                "notice": "Feed entries and headlines are discovery leads, not verified evidence. Fetch the relevant official page text."}

    def fetch(self, url: str) -> dict:
        if not isinstance(url, str) or not public_url(url) or len(url) > 1200:
            raise ValueError("invalid public URL")
        host = (urlparse(url).hostname or "").lower()
        if not any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS):
            raise PermissionError("only approved public primary-source domains may be fetched")
        if urlparse(url).path.casefold().endswith(".pdf"):
            return {
                "status": "unsupported_format", "url": url,
                "reason": "source.fetch cannot extract PDF content; this URL is not read evidence",
                "next_step": "Find the publisher's HTML/text version or disclose that the PDF was not checked.",
            }
        response = fetch_url(url, allowed_domains=list(OFFICIAL_DOMAINS), timeout_seconds=15,
                             retries=2, retry_delay_seconds=0.5, max_bytes=800_000)
        if not any(kind in response.content_type for kind in ("html", "text", "json", "xml")):
            return {
                "status": "unsupported_format", "url": response.url,
                "reason": "source.fetch cannot extract this content type; it is not read evidence",
                "next_step": "Find the publisher's HTML/text version or disclose that the file was not checked.",
            }
        if "html" in response.content_type:
            parser = _Text()
            parser.feed(response.body.decode("utf-8", errors="replace"))
            text = "\n".join(parser.parts)
        else:
            text = response.body.decode("utf-8", errors="replace")
        text = re.sub(r"\n{3,}", "\n\n", text)
        bounded_text = text[:14000]
        numbered_text = "\n".join(
            f"{index}: {line}" for index, line in enumerate(bounded_text.splitlines() or [""], 1)
        )
        returned_line_end = len(bounded_text.splitlines()) or 1
        ref = {"type": "official_source", "url": response.url, "retrieved_at": response.retrieved_at,
               "sha256": response.sha256, "content_type": response.content_type,
               "line_start": 1, "line_end": returned_line_end,
               "anchor": f"{response.url}#L1-L{returned_line_end}"}
        evidence = self.evidence_ledger.add_public_source(ref, excerpt=bounded_text)
        ref["evidence_id"] = evidence.evidence_id
        self.external_reads.append(ref)
        self.external_results.append({**ref, "text": numbered_text[:4000]})
        return {**ref, "text": numbered_text, "truncated": len(text) > 14000,
                "notice": "Retrieved official text; any particular claim still requires a matching passage and date."}


IMAGE_LINE = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
