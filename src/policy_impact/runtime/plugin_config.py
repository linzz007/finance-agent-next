"""File-backed plugin configuration helpers.

Runtime code owns execution semantics; plugin files own descriptors, prompts,
tool bindings, and policy-facing metadata.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = PROJECT_ROOT / "plugins"


def _safe_plugin_path(*parts: str, root: Path = PLUGIN_ROOT) -> Path:
    """Resolve a plugin-relative path without allowing directory traversal."""
    if not parts or any(not isinstance(part, str) or not part for part in parts):
        raise ValueError("plugin path parts must be non-empty strings")
    normalized = "/".join(parts).replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError(f"plugin path must stay inside the plugin directory: {parts!r}")
    path = (root / Path(*posix_path.parts)).resolve()
    root_resolved = root.resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise ValueError(f"plugin path escapes the plugin directory: {parts!r}")
    return path


def plugin_path(*parts: str) -> Path:
    return _safe_plugin_path(*parts)


def read_plugin_json(*parts: str) -> Any:
    path = plugin_path(*parts)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"plugin config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"plugin config is not valid JSON: {path}: {exc}") from exc


def read_plugin_text(*parts: str) -> str:
    path = _safe_plugin_path(*parts)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"plugin text not found: {path}") from exc


def read_plugin_prompt_modules(modules: tuple[str, ...] | list[str]) -> str:
    """Load ordered Markdown modules below ``plugins/prompts``.

    The caller supplies only relative module names declared by a skill manifest;
    absolute paths and parent traversal are rejected by the same path guard used
    for the other plugin files.
    """
    if not isinstance(modules, (tuple, list)) or not modules:
        raise ValueError("prompt modules must be a non-empty list")
    chunks: list[str] = []
    for module in modules:
        if not isinstance(module, str) or not module.strip():
            raise ValueError("prompt module names must be non-empty strings")
        relative = module.strip().replace("\\", "/")
        if not relative.lower().endswith(".md"):
            raise ValueError(f"prompt module must be Markdown: {module!r}")
        path = _safe_plugin_path("prompts", *PurePosixPath(relative).parts)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"prompt module not found: {path}") from exc
        if not text:
            raise ValueError(f"prompt module is empty: {module!r}")
        chunks.append(text)
    return "\n\n".join(chunks)


def read_plugin_skill_modules(modules: tuple[str, ...] | list[str]) -> str:
    """Load optional capability skills from ``plugins/skills``.

    Skill files describe capabilities and judgment boundaries.  They are
    intentionally separate from the single workspace system prompt so a
    workspace can add or remove a capability without changing Runtime code.
    Paths are still resolved through the plugin sandbox; a skill cannot read
    arbitrary files merely because it is named in a manifest.
    """
    if not isinstance(modules, (tuple, list)) or not modules:
        return ""
    chunks: list[str] = []
    for module in modules:
        if not isinstance(module, str) or not module.strip():
            raise ValueError("skill module names must be non-empty strings")
        relative = module.strip().replace("\\", "/")
        if not relative.lower().endswith(".md"):
            raise ValueError(f"skill module must be Markdown: {module!r}")
        path = _safe_plugin_path("skills", *PurePosixPath(relative).parts)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"skill module not found: {path}") from exc
        if not text:
            raise ValueError(f"skill module is empty: {module!r}")
        chunks.append(text)
    return "\n\n".join(chunks)


def read_plugin_toolkit(name: str) -> list[dict[str, Any]]:
    """Read a tool descriptor list from ``plugins/tools`` with basic validation."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("toolkit name is required")
    relative = name.strip().replace("\\", "/")
    if not relative.lower().endswith(".json"):
        raise ValueError("toolkit must be a JSON file")
    payload = read_plugin_json("tools", *PurePosixPath(relative).parts)
    if not isinstance(payload, list):
        raise ValueError(f"toolkit must contain a list: {name}")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"invalid toolkit descriptor in {name}: {item!r}")
        tool_name = item["name"].strip()
        if not tool_name or tool_name in names:
            raise ValueError(f"duplicate or blank toolkit tool name in {name}: {tool_name!r}")
        names.add(tool_name)
        if relative == "finance_research.json":
            from policy_impact.domains.finance.tool_contracts import describe_tool
            result.append(describe_tool(item))
        else:
            result.append(dict(item))
    return result


def import_ref(reference: str) -> Any:
    module_name, separator, object_path = str(reference or "").partition(":")
    if not separator or not module_name or not object_path:
        raise ValueError(f"plugin import reference must use module:object syntax: {reference!r}")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in object_path.split("."):
        if not part:
            raise ValueError(f"invalid plugin import reference: {reference!r}")
        value = getattr(value, part)
    return value


__all__ = [
    "PLUGIN_ROOT",
    "PROJECT_ROOT",
    "import_ref",
    "plugin_path",
    "read_plugin_prompt_modules",
    "read_plugin_skill_modules",
    "read_plugin_json",
    "read_plugin_text",
    "read_plugin_toolkit",
]
