"""One input-type registry for public tool descriptions and execution checks."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from policy_impact.runtime.research_contracts import ResearchTask


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanScope(Input):
    authors: list[str] | None = Field(default=None, max_length=20)
    date_range: dict[str, str] | None = None
    topics: list[str] | None = Field(default=None, max_length=30)
    deliverable: str | None = Field(default=None, max_length=500)


class Check(Input):
    claim: str = Field(min_length=1, max_length=500)
    decision: Literal["check", "not_needed"]
    outcome: Literal["supported", "refuted", "inconclusive", "not_applicable", "not_due"]
    method: str = Field(default="", max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)
    gap: str = Field(default="", max_length=1000)


class Aspect(Input):
    question: str = Field(min_length=1, max_length=300)
    status: Literal["covered", "partial", "uncovered"]
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    contrary_evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    gap: str = Field(default="", max_length=500)


Positive = Annotated[int, Field(ge=1)]
Offset = Annotated[int, Field(ge=0)]
Page = Annotated[int, Field(ge=1, le=100)]
Text = Annotated[str, Field(min_length=1)]
Question = Annotated[str, Field(min_length=1, max_length=500)]


def optional(kind):
    return (kind | None, None)


# Cross-field decisions and resource authorization remain in domain services.
FIELDS = {
    "skill.open": {"skill_id": (Text, ...)},
    "tool.describe": {"name": (Text, ...)},
    "article.list": {"author": (Annotated[str, Field(max_length=80)], ""),
                     "published_after": (Annotated[str, Field(pattern=r"^(?:\d{4}-\d{2}-\d{2})?$")], ""),
                     "published_before": (Annotated[str, Field(pattern=r"^(?:\d{4}-\d{2}-\d{2})?$")], ""),
                     "limit": (Annotated[int, Field(ge=1, le=500)], 100)},
    "article.read": {"article_id": (Text, ...), "start_line": (Positive, 1), "limit": (Page, 60)},
    "article.claims": {"article_id": (Text, ...)},
    "article.study": {"article_id": (Text, ...), "question": (Question, ...),
                      "depth": (Literal["skim", "deep"], ...),
                      "start_line": optional(Positive), "limit": optional(Page)},
    "source.discover": {"query": (Text, ...)},
    "source.fetch": {"url": (Text, ...)},
    "web.search": {"query": (Text, ...), "limit": (Annotated[int, Field(ge=1, le=10)], 8),
                   "timelimit": optional(Literal["d", "w", "m", "y"])},
    "web.open": {"url": (Text, ...), "start_line": (Positive, 1)},
    "market.catalog": {},
    "market.snapshot": {"symbols": (Annotated[list[str], Field(min_length=1, max_length=11)], ...),
                        "start": optional(str), "end": optional(str), "latest_completed": (bool, False)},
    "market.metrics": {"snapshot_id": (Text, ...)},
    "market.series": {"snapshot_id": (Text, ...), "symbol": (Text, ...),
                      "offset": (Offset, 0), "limit": (Page, 60)},
    "research.plan": {"operation": (Literal["create", "update", "get", "load"], ...),
                      "goals": optional(Annotated[list[str], Field(min_length=2, max_length=8)]),
                      "scope": optional(PlanScope), "goal_id": optional(Positive | str),
                      "status": optional(Literal["pending", "done", "blocked"]),
                      "note": (Annotated[str, Field(max_length=240)], ""), "run_id": optional(str)},
    "research.artifact": {"operation": (Literal["save", "get", "list"], ...),
                          "schema_version": (Literal["finance.research_artifact.v1", "finance.research_artifact.v2"],
                                             "finance.research_artifact.v1"),
                          "kind": optional(Literal["article_card", "comparison", "review", "research_note",
                                                   "article_understanding", "synthesis", "working_view"]),
                          "research_task": optional(ResearchTask), "payload": optional(dict[str, Any]),
                          "artifact_id": optional(str), "cursor": optional(str)},
    "research.disposition": {"result_ref": (Text, ...),
                             "decision": (Literal["use", "qualify", "exclude", "follow_up"], ...),
                             "reason": (Text, ...)},
    "research.synthesize": {"question": (Question, ...),
                            "card_ids": (Annotated[list[str], Field(min_length=2, max_length=20)], ...)},
    "research.verify": {"card_ids": (Annotated[list[str], Field(min_length=1, max_length=20)], ...),
                        "claim_ids": optional(Annotated[list[str], Field(max_length=30)]),
                        "evidence_ids": optional(Annotated[list[str], Field(max_length=30)]), "as_of": optional(str)},
    "research.checkpoint": {"checks": (Annotated[list[Check], Field(min_length=1, max_length=20)], ...),
                            "summary": (Annotated[str, Field(max_length=1000)], "")},
    "research.coverage": {"aspects": (Annotated[list[Aspect], Field(min_length=1, max_length=12)], ...),
                          "decision": (Literal["continue", "synthesize"], ...),
                          "reason": (Annotated[str, Field(min_length=10, max_length=1000)], ...),
                          "next_need": (Annotated[str, Field(max_length=500)], ""),
                          "sample_scope": (Annotated[str, Field(max_length=500)], "")},
    "context.open": {"context_id": (Text, ...), "offset": (Offset, 0),
                     "limit": optional(Annotated[int, Field(ge=1, le=12000)])},
    "memory_search": {"query": (Text, ...), "top_k": (Annotated[int, Field(ge=1, le=20)], 5)},
    "company_wiki_search": {"query": (Text, ...), "top_k": (Annotated[int, Field(ge=1, le=20)], 5)},
    "finance.report_read": {"report_id": (Text, ...), "section": (str, "sources"),
                            "symbol": (str, ""), "document_id": (str, ""),
                            "offset": (Annotated[int, Field(ge=0, le=100000)], 0),
                            "limit": (Annotated[int, Field(ge=1, le=30)], 12)},
    "finance.analyze": {"query": optional(Annotated[str, Field(max_length=6000)])},
}

INPUTS = {name: create_model(name.replace(".", "_") + "_input", __base__=Input, **fields)
          for name, fields in FIELDS.items()}


def describe_tool(descriptor: dict) -> dict:
    name = descriptor["name"]
    if name not in INPUTS:
        return dict(descriptor)
    schema = deepcopy(INPUTS[name].model_json_schema())
    for field, description in descriptor.get("parameters", {}).items():
        if field not in schema["properties"]:
            raise ValueError(f"tool description has no input field: {name}.{field}")
        schema["properties"][field]["description"] = description
    return {**descriptor, "parameters": schema}


def validate_tool_input(name: str, arguments: dict) -> None:
    model = INPUTS.get(name)
    if model is None:
        return
    try:
        model.model_validate(arguments)
    except ValidationError as exc:
        issues = [{"path": ".".join(map(str, row["loc"])), "type": row["type"],
                   "message": row["msg"]}
                  for row in exc.errors(include_input=False, include_url=False)[:8]]
        raise ValueError(f"tool_contract_error: {name}: {issues}; correct arguments using the tool input schema") from exc


__all__ = ["INPUTS", "describe_tool", "validate_tool_input"]
