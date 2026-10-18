"""Progressive disclosure uses no provider or live financial data."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from policy_impact.runtime import capability_catalog as module
from policy_impact.runtime.capability_catalog import RunCapabilities
from policy_impact.runtime.plugin_config import read_plugin_toolkit
from policy_impact.skills.registry import SkillRegistry
from policy_impact.skills.schemas import FinanceReviewTarget


def make_catalog(*, descriptors=None):
    skill = SkillRegistry().get_manifest("general_chat")
    return RunCapabilities(
        skill.capability_catalog, skill.skill_modules,
        read_plugin_toolkit("finance_research.json") if descriptors is None else descriptors,
        schemas={"evidence_review": {"review_target": FinanceReviewTarget.model_json_schema()}},
    )


def test_catalog_is_metadata_only_until_open_and_snapshots_once(monkeypatch):
    calls = []

    def read(modules):
        calls.append(modules)
        return f"guidance snapshot {len(calls)}"

    monkeypatch.setattr(module, "read_plugin_skill_modules", read)
    catalog = make_catalog()
    assert calls == []
    assert len(catalog.entries) == 7
    first = catalog.open_skill("source_access")
    first["tool_descriptors"].clear()
    second = catalog.open_skill("source_access")
    assert calls == [["finance/source_access.md"]]
    assert first["content"] == second["content"]
    assert first["checksum"] == second["checksum"]
    assert not first["already_loaded"] and second["already_loaded"]
    assert second["tool_descriptors"]
    assert all("parameters" not in row for row in second["tool_descriptors"])
    assert make_catalog().open_skill("source_access")["content"] != first["content"]


def test_initial_parameters_are_only_bootstrap_and_schema_is_on_demand():
    catalog = make_catalog()
    initial = catalog.initial_descriptors()
    assert {item["name"] for item in initial if "parameters" in item} == catalog.BOOTSTRAP
    assert all("description" in item for item in initial)
    exact = catalog.describe_tool("article.read")
    assert exact["parameters"]["properties"]["article_id"]["type"] == "string"
    assert "schemas" not in catalog.open_skill("source_access")
    assert catalog.open_skill("evidence_review")["schemas"]["review_target"] == FinanceReviewTarget.model_json_schema()


@pytest.mark.parametrize("skill_id", ["../secrets", "/tmp/file", "C:\\secret.md", "finance/source_access.md", "unknown"])
def test_skill_lookup_accepts_only_declared_ids(skill_id):
    with pytest.raises(PermissionError):
        make_catalog().open_skill(skill_id)


def test_discovery_does_not_restore_filtered_tools_or_change_descriptors():
    allowed = [row for row in read_plugin_toolkit("finance_research.json")
               if not row["name"].startswith(("web.", "market.", "source."))]
    catalog = make_catalog(descriptors=allowed)
    with pytest.raises(PermissionError):
        catalog.describe_tool("web.open")
    tools = catalog.open_skill("source_access")["tool_descriptors"]
    assert not any(row["name"].startswith(("web.", "market.", "source.")) for row in tools)
    descriptor = catalog.describe_tool("article.read")
    descriptor["parameters"].clear()
    assert catalog.describe_tool("article.read")["parameters"]


@pytest.mark.parametrize("defect", ["duplicate_id", "duplicate_module", "unlisted", "traversal", "blank", "extra"])
def test_bad_catalog_fails_without_reading_guidance(monkeypatch, defect):
    raw = deepcopy(module.read_plugin_json("skills", "finance/catalog.json"))
    if defect == "duplicate_id":
        raw["skills"][1]["skill_id"] = raw["skills"][0]["skill_id"]
    elif defect == "duplicate_module":
        raw["skills"][1]["module"] = raw["skills"][0]["module"]
    elif defect == "unlisted":
        raw["skills"][0]["module"] = "finance/verification.md"
    elif defect == "traversal":
        raw["skills"][0]["module"] = "../secrets.md"
    elif defect == "blank":
        raw["skills"][0]["description"] = " "
    else:
        raw["skills"][0]["undeclared"] = True
    monkeypatch.setattr(module, "read_plugin_json", lambda *args: raw)
    monkeypatch.setattr(module, "read_plugin_skill_modules", lambda *args: pytest.fail("must stay lazy"))
    with pytest.raises((ValueError, ValidationError)):
        make_catalog()


def test_load_failure_is_explicit_and_does_not_cache_a_success(monkeypatch):
    catalog = make_catalog()

    def fail(*args):
        raise FileNotFoundError("injected missing module")

    original = module.read_plugin_skill_modules
    monkeypatch.setattr(module, "read_plugin_skill_modules", fail)
    with pytest.raises(FileNotFoundError):
        catalog.open_skill("source_access")
    monkeypatch.setattr(module, "read_plugin_skill_modules", original)
    assert catalog.open_skill("source_access")["already_loaded"] is False


@pytest.mark.parametrize("path", ["../outside.json", "/absolute.json", "C:\\outside.json", "finance/not-markdown.md"])
def test_manifest_rejects_arbitrary_catalog_paths(path):
    manifest = SkillRegistry().get_manifest("general_chat").model_dump()
    manifest["capability_catalog"] = path
    with pytest.raises(ValidationError):
        type(SkillRegistry().get_manifest("general_chat")).model_validate(manifest)


def test_metadata_lookups_precede_plan_but_never_bypass_business_or_role_gates(tmp_path):
    from policy_impact.domains.finance.article_corpus import ArticleCorpus
    from policy_impact.domains.finance.research_gateway import FinanceResearchGateway

    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="local sources only",
        corpus=ArticleCorpus("finance_observer", root=tmp_path), require_plan_before_tools=True,
    )
    descriptors = [row for row in read_plugin_toolkit("finance_research.json")
                   if not row["name"].startswith(("web.", "market.", "source."))]
    gateway.capabilities = make_catalog(descriptors=descriptors)
    assert gateway.call("chat_agent_loop", "skill.open", skill_id="source_access")["skill_id"] == "source_access"
    assert gateway.call("chat_agent_loop", "tool.describe", name="article.read")["parameters"]
    assert not gateway.plan.exists
    with pytest.raises(ValueError, match="research_plan_required"):
        gateway.call("chat_agent_loop", "article.list")
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "tool.describe", name="web.open")
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "skill.open", skill_id="source_access")
    assert gateway.read_refs == []
