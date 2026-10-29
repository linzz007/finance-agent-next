"""Versioned instruction assets, never a source of execution permissions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .contracts import DispatchError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = "config/agents/finance.json"
ROLE_VERSIONS = {
    "analyst": ["2.0.0", "1.0.0"], "verifier": ["1.0.0"], "skeptic": ["1.0.0"],
}
LEGACY_ROLE_FIELDS = {"versions", "description", "instructions", "input_requirements", "output_contract"}
PROFILE_ROLE_FIELDS = LEGACY_ROLE_FIELDS | {
    "allowed_tools", "allowed_child_profiles", "input_contracts", "output_contracts", "context_policy",
}
CONTEXT_POLICY = {
    "history": "task_local", "materials": "explicit_refs", "parent_context": "explicit_only",
}
KNOWN_CONTRACTS = {
    "finance.research_task@1", "finance.research_result@1", "finance.analyst_result@2",
    "finance.review_request@1", "finance.review_report@1", "finance.child@1",
    "finance.task_result@1", "finance.review_report@2",
}
PROFILE_INPUT_CONTRACTS = {"finance.research_task@1", "finance.review_request@1"}
PROFILE_OUTPUT_CONTRACTS = KNOWN_CONTRACTS - PROFILE_INPUT_CONTRACTS
PROMPT_NAMES = frozenset({
    "root", "child-common", "analyst", "verifier", "skeptic", "action-context",
    "research-clock", "context-view", "public-source", "market-source", "personal-source",
    "delegation-fixed", "delegation-requested", "action-repair", "research-repair", "anchor-repair",
})
PLACEHOLDERS = {
    "root": {"delegation", "action_context", "research_clock", "context_view"},
    "child-common": {"role", "role_instruction", "delegation", "action_context", "research_clock", "context_view"},
}
TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invalid():
    return DispatchError("checkpoint_invalid", "agent rule assets are missing, invalid or changed")


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate instruction field")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=pairs)


@dataclass(frozen=True)
class FinanceRules:
    # Keep immutable serialized contents; callers receive copies, not shared mutable rules.
    _serialized: str

    @classmethod
    def from_snapshot(cls, snapshot):
        try:
            if set(snapshot) != {"schema_version", "assets", "sha256"}:
                raise ValueError("invalid snapshot fields")
            if snapshot["schema_version"] != "finance.h2.agent_rules_snapshot.v1":
                raise ValueError("unsupported snapshot")
            assets = snapshot["assets"]
            if _sha(_encode(assets)) != snapshot["sha256"]:
                raise ValueError("bundle checksum mismatch")
            for path, asset in assets.items():
                if set(asset) != {"content", "sha256"} or _sha(asset["content"]) != asset["sha256"]:
                    raise ValueError("asset checksum mismatch")
                if not isinstance(path, str) or "\\" in path or Path(path).is_absolute() or ".." in Path(path).parts:
                    raise ValueError("invalid asset path")
            manifest = _json(assets[MANIFEST_PATH]["content"])
            legacy_fields = {"schema_version", "version", "show_role_catalog", "prompts", "tool_descriptions", "roles"}
            profile_fields = legacy_fields | {"root_profile"}
            if manifest.get("schema_version") == "finance.h2.agent_rules.v1":
                if set(manifest) not in (legacy_fields, legacy_fields | {"action_repair_limit"}):
                    raise ValueError("invalid legacy manifest fields")
                schema_v2 = False
            elif manifest.get("schema_version") in {
                "finance.h2.agent_rules.v2", "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4",
                "finance.h2.agent_rules.v5",
            }:
                if set(manifest) not in (profile_fields, profile_fields | {"action_repair_limit"}):
                    raise ValueError("invalid profile manifest fields")
                schema_v2 = True
            else:
                raise ValueError("unsupported manifest schema")
            portable = manifest["schema_version"] in {
                "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4", "finance.h2.agent_rules.v5",
            }
            profile_paths = set()
            if portable:
                for name, path in manifest["roles"].items():
                    if (not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name)
                            or path != f"config/agents/profiles/{name}.json"):
                        raise ValueError("invalid profile path")
                    profile_paths.add(path)
                manifest["roles"] = {name: _json(assets[path]["content"])
                                     for name, path in manifest["roles"].items()}
            repair_limit = manifest.get("action_repair_limit", 1)
            if type(repair_limit) is not int or not 1 <= repair_limit <= 5:
                raise ValueError("invalid action repair limit")
            if (not isinstance(manifest["version"], str) or not manifest["version"].strip()
                    or type(manifest["show_role_catalog"]) is not bool
                    or (not portable and set(manifest["prompts"]) != PROMPT_NAMES)
                    or (portable and not (PROMPT_NAMES | {"general"}) <= set(manifest["prompts"]))
                    or (not portable and set(manifest["roles"]) != set(ROLE_VERSIONS))
                    or (portable and not (set(ROLE_VERSIONS) | {"general"}) <= set(manifest["roles"]))):
                raise ValueError("invalid rule manifest")
            paths = {MANIFEST_PATH, manifest["tool_descriptions"], *manifest["prompts"].values(), *profile_paths}
            if set(assets) != paths or len(paths) != len(manifest["prompts"]) + 2 + len(profile_paths):
                raise ValueError("invalid asset inventory")
            for name, path in manifest["prompts"].items():
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) or path != f"prompts/finance/{name}.md":
                    raise ValueError("unsupported prompt path")
                content = assets[path]["content"].strip()
                if not content or set(TOKEN.findall(content)) != PLACEHOLDERS.get(name, set()):
                    raise ValueError("invalid template placeholders")
            if manifest["tool_descriptions"] != "prompts/finance/tool-descriptions.json":
                raise ValueError("unsupported description path")
            tools = _json(assets[manifest["tool_descriptions"]]["content"])
            if set(tools) != {"DESCRIPTIONS", "SCHEDULER_DESCRIPTIONS"}:
                raise ValueError("invalid description groups")
            from .finance_tool_contracts import BUSINESS_TOOL_NAMES, SCHEDULER_TOOL_NAMES

            if (set(tools["DESCRIPTIONS"]) != set(BUSINESS_TOOL_NAMES)
                    or set(tools["SCHEDULER_DESCRIPTIONS"]) != set(SCHEDULER_TOOL_NAMES)):
                raise ValueError("descriptions cannot add or remove executable tools")
            for group in tools.values():
                if not isinstance(group, dict) or not group or any(
                    not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in group.items()
                ):
                    raise ValueError("invalid tool descriptions")
            for name, role in manifest["roles"].items():
                expected_role_fields = PROFILE_ROLE_FIELDS if schema_v2 else LEGACY_ROLE_FIELDS
                if (set(role) != expected_role_fields
                        or (not portable and (role["versions"] != ROLE_VERSIONS[name] or role["instructions"] != name))
                        or (portable and (not isinstance(role["versions"], list) or not role["versions"]
                            or len(set(role["versions"])) != len(role["versions"])
                            or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", v)
                                   for v in role["versions"])
                            or role["instructions"] not in manifest["prompts"]
                            or PLACEHOLDERS.get(role["instructions"])))
                        or (not schema_v2 and role["output_contract"] != "finance.research_result.v1")
                        or not isinstance(role["description"], str) or not role["description"].strip()
                        or not isinstance(role["input_requirements"], list) or not role["input_requirements"]
                        or any(not isinstance(v, str) or not v.strip() for v in role["input_requirements"])):
                    raise ValueError("invalid role definition")
                if schema_v2:
                    known_tools = set(BUSINESS_TOOL_NAMES) | set(SCHEDULER_TOOL_NAMES)
                    if (not isinstance(role["allowed_tools"], list) or not role["allowed_tools"]
                            or len(role["allowed_tools"]) != len(set(role["allowed_tools"]))
                            or set(role["allowed_tools"]) - known_tools
                            or not isinstance(role["allowed_child_profiles"], list)
                            or len(role["allowed_child_profiles"]) != len(set(role["allowed_child_profiles"]))
                            or set(role["allowed_child_profiles"]) - set(manifest["roles"])
                            or not isinstance(role["input_contracts"], list) or not role["input_contracts"]
                            or set(role["input_contracts"]) - (PROFILE_INPUT_CONTRACTS if portable else KNOWN_CONTRACTS)
                            or not isinstance(role["output_contracts"], list) or not role["output_contracts"]
                            or set(role["output_contracts"]) - (PROFILE_OUTPUT_CONTRACTS if portable else KNOWN_CONTRACTS)
                            or role["context_policy"] != CONTEXT_POLICY):
                        raise ValueError("invalid agent profile")
                    if portable and (
                        ("finance.analyst_result@2" in role["output_contracts"] and name != "analyst")
                        or (set(role["output_contracts"]) & {"finance.review_report@1", "finance.review_report@2"}
                            and name != "verifier")
                    ):
                        raise ValueError("specialist result contract requires its registered workflow")
            if schema_v2:
                root_profile = manifest["root_profile"]
                if (set(root_profile) != {"allowed_child_profiles"}
                        or not isinstance(root_profile["allowed_child_profiles"], list)
                        or len(root_profile["allowed_child_profiles"]) != len(set(root_profile["allowed_child_profiles"]))
                        or set(root_profile["allowed_child_profiles"]) - set(manifest["roles"])):
                    raise ValueError("invalid root profile")
            return cls(_encode(snapshot))
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise _invalid() from exc

    def snapshot(self):
        return json.loads(self._serialized)

    @property
    def sha256(self):
        return self.snapshot()["sha256"]

    @property
    def manifest(self):
        assets = self.snapshot()["assets"]
        manifest = json.loads(assets[MANIFEST_PATH]["content"])
        if manifest["schema_version"] in {
            "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4", "finance.h2.agent_rules.v5",
        }:
            manifest["roles"] = {name: json.loads(assets[path]["content"])
                                 for name, path in manifest["roles"].items()}
        return manifest

    @property
    def portable_profiles(self):
        return self.manifest["schema_version"] in {
            "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4", "finance.h2.agent_rules.v5",
        }

    @property
    def compact_spawn(self):
        return self.manifest["schema_version"] in {"finance.h2.agent_rules.v4", "finance.h2.agent_rules.v5"}

    @property
    def review_verdicts(self):
        return self.manifest["schema_version"] == "finance.h2.agent_rules.v5"

    @property
    def action_repair_limit(self):
        return self.manifest.get("action_repair_limit", 1)

    @property
    def has_profiles(self):
        return self.manifest["schema_version"] in {
            "finance.h2.agent_rules.v2", "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4",
            "finance.h2.agent_rules.v5",
        }

    def role_profile(self, role: str, role_version: str | None = None):
        manifest = self.manifest
        if role not in manifest["roles"]:
            raise DispatchError("contract_error", "unknown research role")
        profile = manifest["roles"][role]
        version = role_version or profile["versions"][0]
        if version not in profile["versions"]:
            raise DispatchError("contract_error", "unsupported research role version")
        if not self.has_profiles:
            from .finance_tool_contracts import BUSINESS_TOOL_NAMES, SCHEDULER_TOOL_NAMES

            return {
                **profile,
                "allowed_tools": [*BUSINESS_TOOL_NAMES, *SCHEDULER_TOOL_NAMES],
                "allowed_child_profiles": list(manifest["roles"]),
                "input_contracts": sorted(KNOWN_CONTRACTS),
                "output_contracts": sorted(KNOWN_CONTRACTS),
                "context_policy": dict(CONTEXT_POLICY),
            }
        return profile

    def allowed_root_children(self):
        if not self.has_profiles:
            return list(self.manifest["roles"])
        return list(self.manifest["root_profile"]["allowed_child_profiles"])

    def text(self, name: str, **values):
        path = self.manifest["prompts"][name]
        text = self.snapshot()["assets"][path]["content"].strip()
        if set(values) != PLACEHOLDERS.get(name, set()):
            raise _invalid()
        return TOKEN.sub(lambda match: values[match[1]], text)

    def guidance(self, task_budget):
        fixed = (task_budget or {}).get("budget", {}).get("policy") == "fixed"
        return self.text("delegation-fixed" if fixed else "delegation-requested") + " "

    def system(self, task_budget, *, role=None):
        values = {
            "delegation": self.guidance(task_budget),
            "action_context": " " + self.text("action-context"),
            "research_clock": " " + self.text("research-clock"),
            "context_view": " " + self.text("context-view"),
        }
        if role is not None:
            if role not in self.manifest["roles"]:
                raise DispatchError("contract_error", "unknown research role")
            values.update(role=role, role_instruction=self.text(self.manifest["roles"][role]["instructions"]))
        return self.text("child-common" if role is not None else "root", **values)

    def descriptions(self, *, scheduler=False):
        manifest = self.manifest
        raw = self.snapshot()["assets"][manifest["tool_descriptions"]]["content"]
        return json.loads(raw)["SCHEDULER_DESCRIPTIONS" if scheduler else "DESCRIPTIONS"]

    def catalog(self):
        if not self.manifest["show_role_catalog"]:
            return []
        result = []
        for name, role in self.manifest["roles"].items():
            if self.compact_spawn:
                item = {
                    "profile": name,
                    "description": role["description"],
                    "input_requirements": role["input_requirements"],
                    "output_contracts": role["output_contracts"],
                    "can_delegate_to": role["allowed_child_profiles"],
                }
            else:
                item = {"role": name, "description": role["description"],
                        "versions": role["versions"], "input_requirements": role["input_requirements"]}
            if self.has_profiles and not self.compact_spawn:
                item.update({
                    "allowed_child_profiles": role["allowed_child_profiles"],
                    "input_contracts": role["input_contracts"],
                    "output_contracts": role["output_contracts"],
                    "available_tools": role["allowed_tools"],
                })
            else:
                item["output_contract"] = role["output_contract"]
            result.append(item)
        return result


def load_rules(root: Path = PROJECT_ROOT) -> FinanceRules:
    try:
        root = root.resolve(strict=True)

        def read(relative):
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
            if not path.is_file() or path.stat().st_size > 256 * 1024:
                raise ValueError("invalid instruction file")
            content = path.read_bytes().decode("utf-8")
            return {"content": content, "sha256": _sha(content)}

        manifest_asset = read(MANIFEST_PATH)
        manifest = _json(manifest_asset["content"])
        assets = {MANIFEST_PATH: manifest_asset}
        profile_paths = (list(manifest["roles"].values())
                         if manifest["schema_version"] in {
                             "finance.h2.agent_rules.v3", "finance.h2.agent_rules.v4", "finance.h2.agent_rules.v5",
                         } else [])
        for path in [*manifest["prompts"].values(), manifest["tool_descriptions"], *profile_paths]:
            if (not isinstance(path, str) or not path.startswith(("prompts/finance/", "config/agents/profiles/"))
                    or ".." in Path(path).parts):
                raise ValueError("unsupported rule asset")
            assets[path] = read(path)
        return FinanceRules.from_snapshot({"schema_version": "finance.h2.agent_rules_snapshot.v1",
                                           "assets": assets, "sha256": _sha(_encode(assets))})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _invalid() from exc


def rules_for(capabilities) -> FinanceRules:
    if not hasattr(capabilities, "instruction_rules"):
        return load_rules()  # Direct/offline adapters have no durable Run.
    snapshot = capabilities.instruction_rules
    if snapshot is not None:
        return FinanceRules.from_snapshot(snapshot)
    return legacy_rules()


def legacy_rules() -> FinanceRules:
    try:
        raw = (PROJECT_ROOT / "prompts/finance/legacy-pre-101.json").read_text(encoding="utf-8")
        snapshot = json.loads(raw)
        if snapshot["sha256"] != LEGACY_SHA256:
            raise ValueError("changed legacy compatibility bundle")
        return FinanceRules.from_snapshot(snapshot)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _invalid() from exc


# Filled from the frozen pre-externalization instruction bundle, never from current files.
LEGACY_SHA256 = "7e7e0f22b40f603753594c4c53aca2cfa311194db07d3fc98539a712941daca3"
