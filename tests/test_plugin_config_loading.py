import json

import pytest

from policy_impact.mcp_server.registry import TOOLS
from policy_impact.runtime.plugin_config import plugin_path
from policy_impact.runtime.subagent_manifests import build_default_subagent_manifest_registry
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.skills.registry import SkillRegistry


def test_default_skill_manifests_are_loaded_from_plugin_file():
    payload = json.loads(plugin_path("skills", "manifest.json").read_text(encoding="utf-8"))
    registry = SkillRegistry()

    assert [item["id"] for item in payload] == [skill.id for skill in registry.list_manifests()]
    assert registry.get_manifest("general_chat").context_policy_id == "context.general_chat.v1"
    assert "news.load_items" in registry.get_manifest("external_impact_report").allowed_tools


def test_subagent_roles_and_roster_are_loaded_from_plugin_files():
    roles_payload = json.loads(plugin_path("subagents", "roles.json").read_text(encoding="utf-8"))
    manifests_payload = json.loads(
        plugin_path("subagents", "manifests.json").read_text(encoding="utf-8")
    )
    role_registry = build_default_subagent_role_registry()
    manifest_registry = build_default_subagent_manifest_registry(role_registry)

    assert [(item["role"], item["version"]) for item in roles_payload] == [
        ("collector", "1.0.0"),
        ("analyst", "1.0.0"),
        ("analyst", "2.0.0"),
        ("skeptic", "1.0.0"),
        ("verifier", "1.0.0"),
        ("editor", "1.0.0"),
    ]
    assert [item["role"] for item in manifests_payload] == [
        "collector",
        "analyst",
        "skeptic",
        "verifier",
    ]
    assert role_registry.get("collector").output_schema_name == "evidence_collection.v1"
    assert role_registry.get("analyst").output_schema_name == "claim_set.v1"
    assert role_registry.get("analyst", "2.0.0").output_schema_name == "finance.research_result.v1"
    assert manifest_registry.get("skeptic").context_policy_id == "context.subagent.skeptic.v1"
    assert role_registry.get("collector").prompt_modules == ("subagents/collector.md",)
    assert role_registry.get("verifier").prompt_modules == ("subagents/verifier.md",)
    from policy_impact.runtime.plugin_config import read_plugin_prompt_modules
    assert "Collector role" in read_plugin_prompt_modules(
        role_registry.get("collector").prompt_modules
    )
    assert "Verifier role" in read_plugin_prompt_modules(
        role_registry.get("verifier").prompt_modules
    )


def test_tool_registry_is_loaded_from_plugin_file():
    payload = json.loads(plugin_path("tools", "registry.json").read_text(encoding="utf-8"))

    assert {item["name"] for item in payload} == set(TOOLS)
    assert callable(TOOLS["company_wiki_search"])
    assert callable(TOOLS["report_workflow.run"])


def test_finance_prompt_modules_and_toolkit_are_loaded_from_declared_plugins():
    skill = SkillRegistry().get_manifest("general_chat")
    from policy_impact.runtime.plugin_config import (
        read_plugin_prompt_modules,
        read_plugin_skill_modules,
        read_plugin_toolkit,
    )

    prompt = read_plugin_prompt_modules(skill.prompt_modules)
    skills = read_plugin_skill_modules(skill.skill_modules)
    toolkit = read_plugin_toolkit(skill.toolkits[0])
    assert "Finance Research Agent: core system prompt" in prompt
    assert "article.read" in skills
    assert "analyst" in skills
    assert "验证阶段" in skills
    assert len(skill.prompt_modules) == 1
    assert len(skill.skill_modules) >= 3
    assert {item["name"] for item in toolkit} >= {
        "article.list", "article.read", "web.open", "market.snapshot", "research.plan",
    }


def test_finance_system_prompt_keeps_policy_in_the_plugin_skills():
    skill = SkillRegistry().get_manifest("general_chat")
    from policy_impact.runtime.plugin_config import (
        read_plugin_prompt_modules,
        read_plugin_skill_modules,
    )

    system = read_plugin_prompt_modules(skill.prompt_modules)
    skills = read_plugin_skill_modules(skill.skill_modules)

    # The system prompt defines the workspace contract; capability-specific
    # tool and delegation guidance belongs to independently loadable skills.
    assert "article.list" not in system
    assert "market.snapshot" not in system
    assert "article.list" in skills
    assert "Subagent 是可选插件" in skills
    assert "验证阶段本身是每轮实质分析后的必经阶段" in skills
    assert "JSON 动作协议" in system
    assert "request_approval" in system
    assert "general_chat/runtime_execution.md" not in skill.skill_modules
    assert "finance/runtime_execution.md" not in skill.skill_modules
    assert "finance/verification.md" not in skill.skill_modules
    assert skill.capability_catalog == "finance/catalog.json"


def test_plugin_prompt_and_toolkit_paths_cannot_escape_plugin_root():
    from policy_impact.runtime.plugin_config import (
        read_plugin_prompt_modules,
        read_plugin_skill_modules,
        read_plugin_toolkit,
    )

    with pytest.raises(ValueError):
        read_plugin_prompt_modules(["../secrets.md"])
    with pytest.raises(ValueError):
        read_plugin_skill_modules(["../secrets.md"])
    with pytest.raises(ValueError):
        read_plugin_toolkit("../secrets.json")


def test_research_plan_persists_and_loads_only_within_the_same_session(tmp_path):
    from policy_impact.domains.finance.research_plan import ResearchPlan

    old_run = "run_12345678"
    old_path = tmp_path / "runs" / old_run / "research_plan.json"
    plan = ResearchPlan(session_id="session_a", run_id=old_run, state_path=old_path)
    plan.call("create", goals=["Read sources", "Compare evidence"])
    plan.call("update", goal_id=1, status="done")
    assert old_path.is_file()

    resumed = ResearchPlan(
        session_id="session_a",
        run_id="run_87654321",
        state_path=tmp_path / "runs" / "run_87654321" / "research_plan.json",
    )
    loaded = resumed.call("load", run_id=old_run)
    assert loaded["goals"][0]["status"] == "done"
    assert loaded["session_id"] == "session_a"

    foreign = ResearchPlan(
        session_id="session_b",
        run_id="run_87654321",
        state_path=tmp_path / "runs" / "run_87654321" / "research_plan.json",
    )
    with pytest.raises(PermissionError):
        foreign.call("load", run_id=old_run)
