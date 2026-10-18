"""Company Wiki loader."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any

from policy_impact.harness.artifacts import project_root


wiki_workspace_lock = RLock()
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class WikiConflictError(ValueError):
    """A workspace, page, or fact identity conflicts with existing knowledge."""


def _validate_path_component(component: str) -> None:
    if (
        not component
        or len(component) > 255
        or component in {".", ".."}
        or component.endswith((".", " "))
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in component)
        or component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"invalid wiki page path component: {component!r}")


def classify_source_kind(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized.startswith(("http://", "https://")):
        return "public_disclosure"
    if normalized == "analysis_inference_from_disclosed_business":
        return "analysis_inference"
    if normalized == "user_confirmed":
        return "user_confirmed"
    if normalized == "project_design":
        return "project_design"
    return "other"


def company_dir(company_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", company_id or ""):
        raise ValueError("invalid company_id: use 1-64 ASCII letters, digits, _ or -")
    if company_id.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("invalid company_id: reserved filename")
    root = (project_root() / "data" / "companies").resolve()
    path = root / company_id
    if path.resolve() != path:
        raise ValueError("invalid company_id: workspace links are not allowed")
    return path


def resolve_wiki_page_path(company_id: str, page_path: str) -> Path:
    """Resolve a user-provided Wiki page path inside the company wiki directory."""

    normalized = str(page_path or "").replace("\\", "/").strip()
    posix_path = PurePosixPath(normalized)
    if not normalized or posix_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError(f"invalid wiki page path: {page_path}")
    if not posix_path.parts or posix_path.parts[0] != "wiki":
        raise ValueError("wiki page path must start with wiki/")
    if posix_path.suffix.lower() != ".md":
        raise ValueError("wiki page path must be a markdown file")
    for component in posix_path.parts:
        _validate_path_component(component)
    base_dir = company_dir(company_id).resolve()
    wiki_root = (base_dir / "wiki").resolve()
    candidate = base_dir / Path(*posix_path.parts)
    resolved = candidate.resolve()
    if wiki_root != base_dir / "wiki" or resolved != candidate or not resolved.is_relative_to(wiki_root):
        raise ValueError(f"wiki page path escapes company wiki: {page_path}")
    return resolved


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith(('"', "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?(?:\n|\Z)", text, re.M | re.S)
    if not match:
        return {}, text
    raw_meta, body = match.group(1), text[match.end():]
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    return meta, body.strip()


def _format_frontmatter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def dump_frontmatter(meta: dict[str, Any]) -> str:
    if not meta:
        return ""
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_format_frontmatter_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def load_company_profile(company_id: str) -> dict[str, Any]:
    path = company_dir(company_id) / "company.yaml"
    if path.resolve() != path:
        raise ValueError("company.yaml links are not allowed")
    if not path.exists():
        raise FileNotFoundError(f"company.yaml not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        profile: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line or line.startswith(" "):
                continue
            key, value = line.split(":", 1)
            profile[key.strip()] = _parse_scalar(value)
        return profile


def _extract_fact_blocks(body: str, page_meta: dict[str, Any], source_path: str) -> list[dict[str, Any]]:
    facts = []
    pattern = re.compile(
        r"^## FACT:[ \t]*(?P<fact_id>[^\r\n]*)\r?\n(?P<body>.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    for match in pattern.finditer(body):
        fact_id = match.group("fact_id").strip()
        if not fact_id:
            raise ValueError("fact_id must not be empty")
        block = match.group("body").strip()
        fact_meta: dict[str, Any] = {}
        description_lines = []
        for line in block.splitlines():
            striped = line.strip()
            if striped.startswith("- ") and ":" in striped:
                key, value = striped[2:].split(":", 1)
                fact_meta[key.strip()] = _parse_scalar(value)
            elif striped:
                description_lines.append(striped)
        try:
            importance = int(fact_meta.get("importance", page_meta.get("importance", 3)))
            confidence = float(fact_meta.get("confidence", page_meta.get("confidence", 0.5)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid importance or confidence for fact_id {fact_id!r}") from exc
        if not math.isfinite(confidence):
            raise ValueError(f"confidence must be finite for fact_id {fact_id!r}")
        facts.append(
            {
                "fact_id": fact_id,
                "source_path": source_path,
                "domain": page_meta.get("domain", ""),
                "owner": page_meta.get("owner", ""),
                "freshness": page_meta.get("freshness", ""),
                "last_updated": page_meta.get("last_updated", ""),
                "page_source": page_meta.get("source", ""),
                "importance": importance,
                "confidence": confidence,
                "source": fact_meta.get("source", page_meta.get("source", "")),
                "source_kind": classify_source_kind(
                    str(fact_meta.get("source", page_meta.get("source", "")))
                ),
                "value": fact_meta.get("value", ""),
                "policy_relevance": fact_meta.get("policy_relevance", []),
                "text": "\n".join(description_lines) or block,
            }
        )
    return facts


def load_wiki_pages(company_id: str) -> list[dict[str, Any]]:
    wiki_dir = company_dir(company_id) / "wiki"
    if wiki_dir.resolve() != wiki_dir:
        raise ValueError("wiki page path escapes company wiki: wiki directory link")
    pages = []
    for path in sorted(wiki_dir.rglob("*.md")):
        rel_path = path.relative_to(company_dir(company_id)).as_posix()
        path = resolve_wiki_page_path(company_id, rel_path)
        if path.is_dir():
            continue
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        facts = _extract_fact_blocks(body, meta, rel_path)
        pages.append({"path": rel_path, "meta": meta, "body": body, "facts": facts})
    return pages


def load_company_knowledge(company_id: str) -> dict[str, Any]:
    profile = load_company_profile(company_id)
    pages = load_wiki_pages(company_id)
    _validate_unique_facts(pages)
    facts = [fact for page in pages for fact in page["facts"]]
    return {"profile": profile, "pages": pages, "facts": facts}


def _validate_unique_facts(pages: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for page in pages:
        for fact in page["facts"]:
            fact_id = fact["fact_id"]
            if fact_id in seen:
                raise WikiConflictError(
                    f"duplicate fact_id {fact_id!r} in {seen[fact_id]} and {page['path']}"
                )
            seen[fact_id] = page["path"]


def create_company_workspace(company_id: str, name: str, profile_type: str) -> dict[str, Any]:
    path = company_dir(company_id)
    name = name.strip()
    if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise ValueError("name must contain 1-200 characters without control characters")
    if profile_type not in {"company", "personal"}:
        raise ValueError("profile_type must be company or personal")
    profile = {
        "company_id": company_id,
        "company_name": name,
        "short_name": name,
        "profile_type": profile_type,
        "business_keywords": [],
    }
    subject = "\u516c\u53f8\u80cc\u666f\u3001\u4e3b\u8425\u4e1a\u52a1\u3001\u4ea7\u54c1\u3001\u670d\u52a1\u5bf9\u8c61\u4e0e\u8d44\u6599\u6765\u6e90" if profile_type == "company" else (
        "\u4e2a\u4eba\u5b66\u4e60\u80cc\u666f\u3001\u5df2\u638c\u63e1\u7684\u6280\u80fd\u3001\u5f53\u524d\u5b66\u4e60\u9636\u6bb5\u4e0e\u53ef\u6295\u5165\u7684\u65f6\u95f4"
    )
    objective = "\u5e0c\u671b\u5206\u6790\u7684\u4e1a\u52a1\u95ee\u9898\u3001\u9884\u671f\u4ea4\u4ed8\u7269\u4e0e\u9a8c\u6536\u6807\u51c6" if profile_type == "company" else (
        "\u4eba\u5de5\u667a\u80fd\u5b66\u4e60\u76ee\u6807\u3001\u9636\u6bb5\u91cc\u7a0b\u7891\u3001\u5b9e\u8df5\u9879\u76ee\u4e0e\u53ef\u8861\u91cf\u7684\u5b66\u4e60\u6210\u679c"
    )
    templates = (
        ("00_background.md", "\u80cc\u666f", f"\u8bf7\u586b\u5199{subject}\u3002"),
        ("01_goals.md", "\u76ee\u6807", f"\u8bf7\u586b\u5199{objective}\u3002"),
        (
            "02_constraints.md", "\u7ea6\u675f",
            "\u8bf7\u586b\u5199\u9884\u7b97\u3001\u65f6\u95f4\u5b89\u6392\u3001\u9690\u79c1\u8981\u6c42\u3001\u8d44\u6599\u4f7f\u7528\u8303\u56f4\u548c\u4e0d\u5e0c\u671b\u6d89\u53ca\u7684\u5185\u5bb9\u3002"
            "\u5c1a\u672a\u786e\u8ba4\u7684\u9650\u5236\u8bf7\u4fdd\u7559\u4e3a\u7a7a\u3002",
        ),
    )
    with wiki_workspace_lock:
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WikiConflictError(f"workspace already exists: {company_id}") from exc
        # JSON is valid YAML and round-trips through the existing profile reader.
        (path / "company.yaml").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (path / "wiki").mkdir()
        for filename, title, guidance in templates:
            meta = {
                "title": title, "domain": "\u753b\u50cf", "owner": "\u7528\u6237", "source": "project_design",
            }
            # Guidance is prose, never a FACT block or a report evidence placeholder.
            body = (
                f"# {title}\n\n"
                "> \u5f85\u586b\u5199\uff1a\u4ee5\u4e0b\u4ec5\u4e3a\u586b\u5199\u63d0\u793a\uff0c\u4e0d\u4ee3\u8868\u5df2\u786e\u8ba4\u7684\u4e8b\u5b9e\u3001\u76ee\u6807\u6216\u7ea6\u675f\u3002\n\n"
                f"{guidance}\n\n"
                "\u8bf7\u53ea\u8865\u5145\u53ef\u6838\u5b9e\u6216\u672c\u4eba\u786e\u8ba4\u7684\u4fe1\u606f\uff0c\u5e76\u6ce8\u660e\u6765\u6e90\uff1b\u65e0\u6cd5\u786e\u8ba4\u7684\u5185\u5bb9\u65e0\u9700\u586b\u5199\u3002\n"
            )
            (path / "wiki" / filename).write_text(
                f"{dump_frontmatter(meta)}\n{body}", encoding="utf-8"
            )
    return profile


def update_wiki_page_body(
    company_id: str, page_path: str, body: str, *, create: bool = False
) -> dict[str, Any]:
    with wiki_workspace_lock:
        return _write_wiki_page_body(company_id, page_path, body, create=create)


def _write_wiki_page_body(
    company_id: str, page_path: str, body: str, *, create: bool
) -> dict[str, Any]:
    path = resolve_wiki_page_path(company_id, page_path)
    load_company_profile(company_id)
    if path.is_dir():
        raise ValueError(f"wiki page path is a directory: {page_path}")
    if create and path.exists():
        raise WikiConflictError(f"wiki page already exists: {page_path}")
    if not create and not path.exists():
        raise FileNotFoundError(f"wiki page not found: {page_path}")
    if create:
        meta = {"title": path.stem, "domain": "profile", "owner": "user", "source": "user_confirmed"}
    else:
        meta, _old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    normalized_body = str(body or "").strip()
    if normalized_body.startswith("---"):
        raise ValueError("body must contain Markdown content, not frontmatter")
    canonical_path = path.relative_to(company_dir(company_id)).as_posix()
    facts = _extract_fact_blocks(normalized_body + "\n", meta, canonical_path)
    for fact in facts:
        if not 1 <= fact["importance"] <= 5:
            raise ValueError(f"importance must be between 1 and 5: {fact['fact_id']}")
        if not math.isfinite(fact["confidence"]) or not 0 <= fact["confidence"] <= 1:
            raise ValueError(f"confidence must be between 0 and 1: {fact['fact_id']}")
        tags = fact["policy_relevance"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError(f"policy_relevance must be a list of strings: {fact['fact_id']}")
        if not isinstance(fact["source"], str):
            raise ValueError(f"source must be a string: {fact['fact_id']}")
    page = {"path": canonical_path, "meta": meta, "body": normalized_body, "facts": facts}
    json.dumps(page, allow_nan=False)
    pages = [
        existing for existing in load_wiki_pages(company_id)
        if resolve_wiki_page_path(company_id, existing["path"]) != path
    ]
    _validate_unique_facts([*pages, page])
    frontmatter = dump_frontmatter(meta)
    if frontmatter:
        next_text = f"{frontmatter}\n{normalized_body}\n"
    else:
        next_text = f"{normalized_body}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if create:
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(next_text)
        except FileExistsError as exc:
            raise WikiConflictError(f"wiki page already exists: {page_path}") from exc
    else:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as output:
                temporary_path = Path(output.name)
                output.write(next_text)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return page
