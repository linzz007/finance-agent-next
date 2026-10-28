"""On-demand H2 finance descriptions backed by the execution input models."""

from typing import Annotated

from pydantic import Field, TypeAdapter, create_model

from policy_impact.domains.finance.tool_contracts import INPUTS, Input, Page
from policy_impact.runtime.actions import AgentAction

from .contracts import AgentSpawnInput, BudgetRequest, DispatchError, OutputContract, Spawn
from .finance_rules import load_rules

ARTICLE_TOOLS = frozenset({"article.list", "article.search", "article.read", "article.claims"})
PUBLIC_TOOLS = frozenset({"web.search", "web.open"})
MARKET_TOOLS = frozenset({"market.catalog", "market.snapshot", "market.metrics", "market.series"})
PERSONAL_TOOLS = frozenset({"personal.catalog", "context.open", "company_wiki_search"})
INPUT_MODELS = {
    name: INPUTS[name] for name in ("article.list", "article.claims", "tool.describe", "web.search", "web.open",
                                   "context.open", "company_wiki_search", *sorted(MARKET_TOOLS))
}
INPUT_MODELS["personal.catalog"] = create_model("h2_personal_catalog_input", __base__=Input)
INPUT_MODELS["article.read"] = create_model(
    "h2_article_read_input", __base__=INPUTS["article.read"], limit=(Page, 100),
)
INPUT_MODELS["article.search"] = create_model(
    "h2_article_search_input", __base__=Input,
    query=(Annotated[str, Field(min_length=1, max_length=1000)], ...),
    authors=(list[str] | None, None),
    limit=(Annotated[int, Field(ge=1, le=12)], 6),
)
INPUT_MODELS["material.get"] = create_model(
    "h2_material_get_input", __base__=Input,
    material_ref=(Annotated[str, Field(min_length=1, max_length=160)], ...),
    offset=(Annotated[int, Field(ge=0)], 0),
    limit=(Annotated[int, Field(ge=1, le=64000)], 16000),
)
BUSINESS_TOOL_NAMES = (
    "article.list", "article.search", "article.read", "article.claims", "tool.describe", "material.get",
    "web.search", "web.open", "market.catalog", "market.snapshot", "market.metrics", "market.series",
    "personal.catalog", "context.open", "company_wiki_search",
)
SCHEDULER_TOOL_NAMES = (
    "agent.spawn", "agent.wait", "agent.status", "agent.send", "agent.cancel", "agent.resume", "result.get",
)


def legacy_delegate_budget(role: str) -> BudgetRequest:
    if role == "analyst":
        return BudgetRequest(model_calls=4, wall_time_ms=600000)
    if role in {"verifier", "skeptic"}:
        return BudgetRequest(model_calls=1, wall_time_ms=120000)
    raise ValueError("unsupported legacy delegate role")


def delegation_policy(task_budget=None, *, rules=None) -> dict:
    roles = (rules or load_rules()).manifest["roles"]
    policy = {
        "preferred_tool": "agent.spawn",
        "budget_parameter": "requested_budget",
        "research_role_versions": {
            name: role["versions"] for name, role in roles.items()
        },
        "legacy_delegate": {
            "compatibility_only": True,
            "waits_implicitly": True,
            "requested_budget_by_role": {
                role: legacy_delegate_budget(role).model_dump(mode="json")
                for role in ("analyst", "verifier", "skeptic")
            },
        },
    }
    budget = (task_budget or {}).get("budget", {})
    if budget.get("policy") == "fixed":
        policy.pop("budget_parameter")
        policy.update(budget_policy="fixed", child_task_calls=budget["child_task_calls"],
                      call_counts_model_selected=False, run_budget=budget["run_budget"])
        for value in policy["legacy_delegate"]["requested_budget_by_role"].values():
            value["model_calls"] = budget["child_task_calls"]
    return policy


def delegation_guidance(task_budget):
    return load_rules().guidance(task_budget)


def normalize_agent_spawn(arguments: dict, *, dispatch_key: str, task_budget: dict, rules) -> dict:
    """Translate the compact v4 model request into the existing durable Spawn contract."""
    if not rules.compact_spawn:
        raise DispatchError("invalid_arguments", "compact agent.spawn is unavailable for this frozen Run")
    request = AgentSpawnInput.model_validate(arguments, strict=True)
    profile_name = request.profile
    profile = rules.role_profile(profile_name)
    role_version = profile["versions"][0]
    options = request.profile_options
    if options is not None and profile_name != "analyst":
        raise DispatchError("invalid_arguments", "profile_options is available only for the Analyst Profile")
    if options is not None and role_version != "2.0.0":
        raise DispatchError("invalid_arguments", "Analyst profile options require Analyst 2.0.0")
    if request.success_criteria is None:
        if profile_name != "general":
            raise DispatchError("invalid_arguments", "success_criteria is required for a specialist Profile")
        success_criteria = ["Complete the bounded task and disclose material evidence gaps or limitations."]
    else:
        success_criteria = request.success_criteria

    output_contract = request.output_contract or _profile_output_contract(profile_name, role_version, profile)
    allowed_contracts = set(profile["output_contracts"])
    contract_key = f"{output_contract.name}@{output_contract.version}"
    if contract_key not in allowed_contracts:
        raise DispatchError("invalid_arguments", "output_contract is not registered for the selected Profile")

    fixed_budget = task_budget.get("budget", {}).get("policy") == "fixed"
    if fixed_budget and request.requested_budget is not None:
        raise DispatchError("invalid_arguments", "requested_budget is unavailable under the fixed-budget policy")
    if not fixed_budget and request.requested_budget is None:
        raise DispatchError("invalid_arguments", "requested_budget is required for this historical Run")

    task_instructions = request.task_instructions
    label = f"Task label: {request.task_name}"
    task_instructions = f"{label}\n\n{task_instructions}" if task_instructions else label
    return Spawn(
        dispatch_key=dispatch_key,
        role=profile_name,
        role_version=role_version,
        objective=request.message,
        task_instructions=task_instructions,
        success_criteria=success_criteria,
        input_refs=request.input_refs,
        parent_context_refs=request.parent_context_refs,
        output_contract=output_contract,
        review_mode=options.review_mode if options else None,
        review_options=options.review_options if options else None,
        requested_budget=request.requested_budget,
        wall_time_ms=request.wall_time_ms,
        depends_on_task_ids=request.depends_on_task_ids,
        priority=request.priority,
        model_profile=request.model_profile,
    ).model_dump(mode="json", exclude_none=True)


def _profile_output_contract(profile_name: str, role_version: str, profile: dict) -> OutputContract:
    if profile_name == "analyst" and role_version == "2.0.0":
        return OutputContract(name="finance.analyst_result", version="2")
    value = profile["output_contract"]
    name, separator, version = value.rpartition(".v")
    if not separator:
        raise DispatchError("checkpoint_invalid", "Profile default output contract is invalid")
    return OutputContract(name=name, version=version)


def describe(name: str, task_budget=None, *, rules=None) -> dict:
    rules = rules or load_rules()
    if name == "agent_action.v1":
        return {"name": name, "parameters": TypeAdapter(AgentAction).json_schema(),
                "delegation_policy": delegation_policy(task_budget, rules=rules)}
    if name == "agent.spawn" and rules.compact_spawn:
        schema = AgentSpawnInput.model_json_schema()
        if (task_budget or {}).get("budget", {}).get("policy") == "fixed":
            schema.get("properties", {}).pop("requested_budget", None)
        return {
            "name": name,
            "description": (
                "Create a child Task. Required: task_name and message. profile defaults to general. "
                "Use success_criteria for specialist Profiles; output_contract defaults from the Profile. "
                "profile_options is Analyst-only. Runtime supplies dispatch_key and resolves the Profile version."
            ),
            "parameters": schema,
            "profiles": rules.catalog(),
            "runtime_filled_fields": ["dispatch_key", "role_version", "default_output_contract"],
        }
    if name == "agent.spawn":
        from .execution import tool_contracts

        budget = (task_budget or {}).get("budget", {})
        return {
            "name": name,
            "description": "Historical Spawn contract for this frozen Run.",
            "parameters": tool_contracts(budget)[name].model_json_schema(),
        }
    if name not in INPUT_MODELS:
        raise ValueError("description is not available in this finance adapter")
    return {"name": name, "description": rules.descriptions()[name],
            "parameters": INPUT_MODELS[name].model_json_schema()}


def validate(name: str, arguments: dict) -> dict:
    if name not in INPUT_MODELS:
        raise ValueError("tool is not available in this finance adapter")
    return INPUT_MODELS[name].model_validate(arguments).model_dump(mode="json")
