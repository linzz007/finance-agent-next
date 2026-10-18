"""Import public WeChat exports into the versioned finance article corpus.

The importer accepts saved Markdown/HTML or public article URLs. It never uses
cookies, login sessions, CAPTCHA bypasses, or private account APIs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import trafilatura
from lxml import html as lxml_html

from policy_impact.domains.finance.article_corpus import ArticleCorpus, article_root
from policy_impact.source_connectors.http_client import fetch_url, public_url


ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
COMPACT_DATE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
CN_DATE = re.compile(r"(20\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?")
SOURCE_URL = re.compile(r"https?://mp\.weixin\.qq\.com/[^\s)>'\"]+", re.I)
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass(frozen=True)
class ImportedSource:
    source: str
    author: str
    title: str
    published_at: str
    source_url: str
    content: str


@dataclass(frozen=True)
class ImportRecord:
    source: str
    status: str
    author: str = ""
    title: str = ""
    output: str = ""
    reason: str = ""


def _clean_title(value: str, fallback: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value).strip()
    return value[:200] or fallback[:200] or "未命名文章"


def _first_date(text: str) -> str:
    match = ISO_DATE.search(text[:3000])
    if match:
        return "-".join(match.groups())
    match = CN_DATE.search(text[:3000])
    if match:
        try:
            return datetime(*map(int, match.groups())).date().isoformat()
        except ValueError:
            return ""
    match = COMPACT_DATE.search(text[:3000])
    if match:
        try:
            return datetime(*map(int, match.groups())).date().isoformat()
        except ValueError:
            return ""
    return ""


def _frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip().strip("\"'")
    return values


def _source_url_from(text: str) -> str:
    match = SOURCE_URL.search(text)
    return match.group(0).rstrip(".,;，。") if match else ""


def _html_meta(document: Any, key: str, value: str) -> str:
    nodes = document.xpath(f"//meta[@{key}={json.dumps(value)}]/@content")
    return str(nodes[0]).strip() if nodes else ""


def _html_text(document: Any, xpath: str) -> str:
    nodes = document.xpath(xpath)
    return " ".join(str(node).strip() for node in nodes if str(node).strip())


def _html_fallback_text(node: Any) -> str:
    blocks = node.xpath(".//p | .//h1 | .//h2 | .//h3 | .//blockquote | .//li")
    lines = [" ".join(" ".join(block.itertext()).split()) for block in blocks]
    return "\n\n".join(line for line in lines if line)


def parse_markdown(text: str, *, source: str, author: str = "") -> ImportedSource:
    metadata = _frontmatter(text)
    title_match = TITLE_RE.search(text[:5000])
    title = _clean_title(metadata.get("title") or (title_match.group(1) if title_match else ""), Path(source).stem)
    published_at = (
        metadata.get("published_at")
        or metadata.get("date")
        or _first_date(text)
        or _first_date(Path(source).name)
    )
    source_url = metadata.get("source_url") or metadata.get("url") or _source_url_from(text)
    body = FRONTMATTER_RE.sub("", text, count=1).strip()
    if not TITLE_RE.match(body):
        body = f"# {title}\n\n{body}"
    return ImportedSource(source, author.strip(), title, published_at, source_url, body)


def parse_html(text: str, *, source: str, author: str = "", source_url: str = "") -> ImportedSource:
    document = lxml_html.fromstring(text)
    title = (
        _html_text(document, "//*[@id='activity-name']/text()")
        or _html_meta(document, "property", "og:title")
        or _html_text(document, "//title/text()")
        or Path(source).stem
    )
    discovered_url = source_url or _html_meta(document, "property", "og:url") or _source_url_from(text)
    date_text = (
        _html_text(document, "//*[@id='publish_time']/text()")
        or _html_meta(document, "property", "article:published_time")
        or text[:3000]
    )
    content_node = document.xpath("//*[@id='js_content']")
    fragment = lxml_html.tostring(content_node[0], encoding="unicode") if content_node else text
    body = trafilatura.extract(
        fragment,
        url=discovered_url or None,
        output_format="markdown",
        include_links=True,
        include_images=False,
        include_formatting=True,
        favor_recall=True,
    ) or ""
    if len(body.strip()) < 20 and content_node:
        body = _html_fallback_text(content_node[0])
    if len(body.strip()) < 20:
        raise ValueError("未找到足够长的公众号正文区域（需要 #js_content 或可识别正文）")
    record = parse_markdown(
        f"# {_clean_title(title, Path(source).stem)}\n\n{_first_date(date_text)}\n\n{body}",
        source=source,
        author=author,
    )
    nickname = _html_text(document, "//*[@id='profile_nickname']//text()")
    return ImportedSource(record.source, author.strip() or nickname, record.title, record.published_at, discovered_url, record.content)


def _infer_author(path: Path, input_root: Path | None) -> str:
    if input_root and path.parent != input_root:
        relative = path.relative_to(input_root)
        if len(relative.parts) > 1:
            return relative.parts[0]
    if path.parent.name.lower() == "articles":
        return path.parent.parent.name
    return "未分类"


def _slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-")
    return value[:72] or "article"


def _output_content(record: ImportedSource) -> str:
    content = record.content.strip()
    metadata: list[str] = []
    if record.published_at:
        metadata.append(f"发布日期：{record.published_at}")
    if record.source_url:
        metadata.append(f"来源：[原文]({record.source_url})")
    if metadata:
        lines = content.splitlines()
        insert_at = 1 if lines and lines[0].startswith("# ") else 0
        lines[insert_at:insert_at] = ["", *metadata]
        content = "\n".join(lines)
    return content.rstrip() + "\n"


def _iter_files(inputs: Iterable[Path]) -> Iterable[tuple[Path, Path | None]]:
    skip_dirs = {"analysis", "reports", "templates", "scripts", "images"}
    for item in inputs:
        item = item.expanduser().resolve()
        if item.is_file() and item.suffix.lower() in {".md", ".markdown", ".txt", ".html", ".htm"}:
            yield item, None
        elif item.is_dir():
            for path in sorted(item.rglob("*")):
                if (
                    path.is_file()
                    and path.suffix.lower() in {".md", ".markdown", ".txt", ".html", ".htm"}
                    and not any(part.lower() in skip_dirs for part in path.relative_to(item).parts[:-1])
                    and path.name.lower() not in {"claude.md", "current_holdings.md", "research_policy.md"}
                ):
                    yield path, item


def import_sources(
    *,
    inputs: Iterable[Path] = (),
    urls: Iterable[str] = (),
    root: Path | None = None,
    author: str = "",
    company_id: str = "finance_observer",
    dry_run: bool = False,
    sync: bool = True,
) -> dict[str, Any]:
    destination_root = (root or article_root()).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    records: list[ImportRecord] = []
    sources: list[ImportedSource] = []
    for path, input_root in _iter_files(inputs):
        try:
            raw = path.read_text(encoding="utf-8-sig")
            resolved_author = author or _infer_author(path, input_root)
            parsed = parse_html(raw, source=str(path), author=resolved_author) if path.suffix.lower() in {".html", ".htm"} else parse_markdown(raw, source=str(path), author=resolved_author)
            sources.append(parsed)
        except Exception as exc:  # noqa: BLE001
            records.append(ImportRecord(str(path), "failed", reason=str(exc)))
    for raw_url in urls:
        url = raw_url.strip()
        if not url:
            continue
        try:
            if not public_url(url) or urlparse(url).hostname.lower().rstrip(".") != "mp.weixin.qq.com":
                raise ValueError("只允许公开的 mp.weixin.qq.com 文章链接")
            response = fetch_url(url, allowed_domains=["mp.weixin.qq.com"], max_bytes=5_000_000)
            encoding = "utf-8"
            content_type = response.content_type.lower()
            if "charset=" in content_type:
                encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
            raw = response.body.decode(encoding, errors="replace")
            sources.append(parse_html(raw, source=url, author=author, source_url=response.url))
        except Exception as exc:  # noqa: BLE001
            records.append(ImportRecord(url, "failed", reason=str(exc)))

    existing_sources: set[str] = set()
    existing_hashes: set[str] = set()
    for path in destination_root.glob("*/articles/*.md"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        source_url = _source_url_from(text)
        if source_url:
            existing_sources.add(source_url)
        existing_hashes.add(hashlib.sha256(text.strip().encode("utf-8")).hexdigest())

    for item in sources:
        resolved_author = item.author.strip() or author.strip() or "未分类"
        output_text = _output_content(item)
        content_hash = hashlib.sha256(output_text.strip().encode("utf-8")).hexdigest()
        source_path = Path(item.source).resolve() if not item.source.startswith("http") else None
        if source_path and source_path.parent.name.lower() == "articles" and source_path.parent.parent == destination_root / resolved_author:
            records.append(ImportRecord(item.source, "skipped", resolved_author, item.title, reason="already_in_destination"))
            continue
        if item.source_url and item.source_url in existing_sources:
            records.append(ImportRecord(item.source, "skipped", resolved_author, item.title, reason="source_url_exists"))
            continue
        if content_hash in existing_hashes:
            records.append(ImportRecord(item.source, "skipped", resolved_author, item.title, reason="content_exists"))
            continue
        filename = f"{item.published_at + '-' if item.published_at else ''}{_slug(item.title)}-{content_hash[:10]}.md"
        output = destination_root / resolved_author / "articles" / filename
        if not dry_run:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(output_text, encoding="utf-8", newline="\n")
        existing_hashes.add(content_hash)
        if item.source_url:
            existing_sources.add(item.source_url)
        records.append(ImportRecord(item.source, "imported" if not dry_run else "would_import", resolved_author, item.title, str(output)))

    sync_result = None
    if sync and not dry_run and any(row.status == "imported" for row in records):
        sync_result = ArticleCorpus(company_id, destination_root).sync()
    return {
        "root": str(destination_root),
        "records": [asdict(row) for row in records],
        "summary": {
            "imported": sum(row.status == "imported" for row in records),
            "would_import": sum(row.status == "would_import" for row in records),
            "skipped": sum(row.status == "skipped" for row in records),
            "failed": sum(row.status == "failed" for row in records),
        },
        "sync": sync_result,
    }
