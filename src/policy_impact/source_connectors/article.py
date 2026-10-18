"""Conservative stdlib extraction of explicit article regions, never whole pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORED = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "iframe", "button"}
BLOCKS = {"p", "div", "section", "article", "main", "blockquote", "li", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "br", "hr"}
DEFAULT_SELECTORS = [".TRS_Editor", "#zoom", "#zoom2", "#BodyLabel", "#ucapcontent", "#con_con", ".article-content", ".article_content", ".pages_content"]
BOILERPLATE = re.compile(r"(?:^|[\s_-])(nav|footer|header|breadcrumb|share|tools?|print|related|copyright)(?:$|[\s_-])", re.I)


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.nodes: list[Node] = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _text(node: Node) -> str:
    pieces: list[str] = []

    def visit(current: Node | str):
        if isinstance(current, str):
            pieces.append(current)
            return
        identity = str(current.attrs.get("id") or "") + " " + str(current.attrs.get("class") or "")
        if (current.tag in IGNORED or BOILERPLATE.search(identity)
                or current.attrs.get("aria-hidden") == "true" or "hidden" in current.attrs):
            return
        if current.tag in BLOCKS:
            pieces.append("\n")
        for child in current.children:
            visit(child)
        if current.tag in BLOCKS:
            pieces.append("\n")

    visit(node)
    return "\n".join(line for raw in "".join(pieces).splitlines() if (line := " ".join(raw.split())))


def extract_article(html: str, selectors: list[str] | None = None) -> tuple[str, str]:
    parser = ArticleParser()
    parser.feed(html)
    parser.close()
    selectors = list(dict.fromkeys((selectors or []) + DEFAULT_SELECTORS))
    for selector in selectors:
        if not selector or selector[0] not in {"#", "."}:
            continue
        for node in parser.nodes:
            matched = (node.attrs.get("id") == selector[1:] if selector.startswith("#")
                       else selector[1:] in str(node.attrs.get("class") or "").split())
            if matched:
                text = _text(node)
                if len(text) >= 200:
                    return text, selector
    for node in parser.nodes:
        if node.attrs.get("itemprop") == "articleBody" or node.tag in {"article", "main"} or node.attrs.get("role") == "main":
            text = _text(node)
            if len(text) >= 200:
                return text, node.tag if not node.attrs.get("itemprop") else "[itemprop=articleBody]"
    # Publisher templates sometimes delimit the正文 rather than assigning an element ID.
    marker = re.search(r"<!--\s*(?:Content_Start|正文开始)\s*-->(.*?)<!--\s*(?:Content_End|正文结束)\s*-->", html, re.S | re.I)
    if marker:
        region = ArticleParser()
        region.feed(marker.group(1))
        text = _text(region.root)
        if len(text) >= 200:
            return text, "content_comment_markers"
    # Minimal standalone documents have no site chrome and a single article heading.
    standalone_tags = {"html", "head", "body", "meta", "title", "h1", "h2", "p", "br", "span", "b", "i", "strong", "em", "a"}
    if (sum(node.tag == "h1" for node in parser.nodes) == 1
            and all(node.tag in standalone_tags for node in parser.nodes)):
        text = "\n".join(_text(node) for node in parser.nodes if node.tag == "p")
        if len(text) >= 200:
            return text, "standalone_article_paragraphs"
    raise ValueError("official_article_body_not_found: no substantial explicit article region")
