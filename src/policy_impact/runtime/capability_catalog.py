"""Run-scoped disclosure of trusted guidance, separate from tool authorization."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from policy_impact.runtime.plugin_config import (
    plugin_path,
    read_plugin_json,
    read_plugin_skill_modules,
)


class SkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=240)
    module: str = Field(min_length=1)
    tools: list[str]


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["finance.skill_catalog.v1"]
    skills: list[SkillEntry] = Field(min_length=1, max_length=32)


class RunCapabilities:
    BOOTSTRAP = frozenset({"research.plan", "skill.open", "tool.describe"})

    def __init__(
        self, catalog_name: str, modules: tuple[str, ...], descriptors: list[dict], *,
        schemas: dict[str, dict] | None = None, on_event: Callable | None = None,
    ) -> None:
        raw = read_plugin_json("skills", catalog_name)
        catalog = Catalog.model_validate(raw)
        entries = {entry.skill_id: entry for entry in catalog.skills}
        if len(entries) != len(catalog.skills):
            raise ValueError("duplicate skill ID in capability catalog")
        catalog_modules = [entry.module for entry in catalog.skills]
        if len(set(catalog_modules)) != len(catalog_modules) or set(catalog_modules) != set(modules):
            raise ValueError("catalog modules must exactly match the declared skill modules")
        for entry in catalog.skills:
            if not entry.description.strip() or not entry.module.endswith(".md"):
                raise ValueError("skill description and Markdown module are required")
            if not plugin_path("skills", entry.module).is_file():
                raise ValueError(f"skill module not found: {entry.skill_id}")
            if len(set(entry.tools)) != len(entry.tools):
                raise ValueError("duplicate tool in skill catalog")
        self.entries = entries
        self.descriptors = {item["name"]: deepcopy(item) for item in descriptors}
        if len(self.descriptors) != len(descriptors):
            raise ValueError("duplicate tool descriptor")
        self.schemas = deepcopy(schemas or {})
        self.on_event = on_event
        self._loaded: dict[str, tuple[str, str]] = {}
        self.checksum = sha256(catalog.model_dump_json().encode("utf-8")).hexdigest()

    def prompt_catalog(self) -> str:
        lines = ["# 可按需读取的 Skills（目录，不是必经步骤）"]
        lines.extend(f"- `{item.skill_id}`：{item.description}" for item in self.entries.values())
        return "\n".join(lines)

    def initial_descriptors(self) -> list[dict]:
        return [deepcopy(item) if name in self.BOOTSTRAP else {
            "name": name, "description": item["description"],
        } for name, item in self.descriptors.items()]

    def open_skill(self, skill_id: str) -> dict:
        if skill_id not in self.entries:
            raise PermissionError("unknown or unauthorized skill ID; use the supplied catalog")
        entry = self.entries[skill_id]
        already_loaded = skill_id in self._loaded
        if not already_loaded:
            content = read_plugin_skill_modules([entry.module])
            self._loaded[skill_id] = (content, sha256(content.encode("utf-8")).hexdigest())
        content, checksum = self._loaded[skill_id]
        result = {
            "skill_id": skill_id, "module": entry.module, "content": content,
            "checksum": checksum, "already_loaded": already_loaded,
            "tool_descriptors": [{"name": name, "description": self.descriptors[name]["description"]}
                                 for name in entry.tools if name in self.descriptors],
        }
        if skill_id in self.schemas:
            result["schemas"] = deepcopy(self.schemas[skill_id])
        self._emit("skill_load", {
            "skill_id": skill_id, "module": entry.module, "checksum": checksum,
            "already_loaded": already_loaded, "characters": len(content),
            "tool_names": [item["name"] for item in result["tool_descriptors"]],
        })
        return result

    def describe_tool(self, tool_name: str) -> dict:
        if tool_name not in self.descriptors:
            raise PermissionError("tool is not available in this run")
        descriptor = deepcopy(self.descriptors[tool_name])
        self._emit("tool_describe", {"tool_name": tool_name})
        return descriptor

    def _emit(self, event: str, payload: dict) -> None:
        if self.on_event:
            self.on_event(event, payload)
