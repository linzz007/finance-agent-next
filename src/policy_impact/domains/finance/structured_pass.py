"""Repair malformed model JSON before evidence-bound finance records are saved."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from policy_impact.harness.research_budget import bounded_decision

T = TypeVar("T", bound=BaseModel)


@bounded_decision
def complete_structured(
    complete: Callable[[list[dict[str, Any]], str], dict[str, Any]],
    messages: list[dict[str, Any]],
    schema_name: str,
    schema: type[T],
    *,
    attempts: int = 3,
    validate: Callable[[T], None] | None = None,
    repair_schema_name: str | None = None,
    repair_attempts: int = 2,
    repair_instruction: str | None = None,
) -> T:
    """Retry structured output and optionally use a separate normalization pass.

    The first attempts ask the original model to correct its own output.  A caller
    may then opt into a second, explicitly named repair contract.  The repair
    contract is for shape recovery only; it receives the original task and the
    validation errors, and is never allowed to silently invent evidence.

    The full output schema stays in a request-local system message through all
    retries; the caller's message order and contents are left unchanged.
    """
    output_json_schema = schema.model_json_schema()
    contract = (
        'Return exactly one JSON object with type=final and output: '
        '{"type":"final","output":...}. The output_json_schema below describes '
        'only the value of output, not the outer type/output envelope. Follow all '
        'nested field names, required fields, JSON types, constraints and enum values; '
        'do not rename fields or add keys outside the schema. Preserve the original '
        'task and source constraints. Do not invent evidence IDs, quotes or claims.\n'
        + json.dumps({"output_json_schema": output_json_schema}, ensure_ascii=False)
    )
    request = deepcopy(messages)
    for message in request:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] += "\n\n" + contract
            break
    else:
        # Preserve all existing messages and their relative order.
        request.insert(0, {"role": "system", "content": contract})
    errors: list[dict[str, str]] = []
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            output = complete(request, schema_name)
        except Exception as exc:
            # The model adapter marks malformed structured output explicitly.
            # Do not turn transport or provider failures into fake normalization.
            if not getattr(exc, "structured_output", False):
                raise
            last_exc = exc
            errors = [{"field": "model_output", "error": str(exc)[:1000]}]
            if attempt == attempts - 1:
                break
            request.append({"role": "user", "content": {
                "validation_errors": errors,
                "instruction": (
                    "The previous model response did not satisfy the structured JSON "
                    "contract. Return one complete type=final object with the required "
                    "output fields. Do not invent evidence, claims, or source IDs."
                ),
                "required_output_fields": list(schema.model_fields),
            }})
            continue
        try:
            batch = schema.model_validate(output)
            if validate is not None:
                validate(batch)
            return batch
        except (ValidationError, ValueError) as exc:
            last_exc = exc
            if isinstance(exc, ValidationError):
                errors = [
                    {"field": ".".join(map(str, item["loc"])), "error": item["msg"]}
                    for item in exc.errors(include_url=False, include_input=False)[:12]
                ]
            else:
                errors = [{"field": "semantic_contract", "error": str(exc)[:1000]}]
            if attempt == attempts - 1:
                break
            request.append({"role": "user", "content": {
                "validation_errors": errors,
                "instruction": (
                    "Your previous JSON did not match the required schema. Re-read the "
                    "original source and return a complete corrected type=final output. "
                    "Do not invent evidence IDs, quotes, or claims. Arrays must remain arrays; "
                    "if a conflict has no evidence_id, state it as an alternative explanation "
                    "or limitation, not as an evidence-bound conflict."
                ),
                "required_output_fields": list(schema.model_fields),
            }})

    if repair_schema_name:
        repair_messages = list(messages)
        repair_messages.append({"role": "user", "content": {
            "repair_mode": "review.normalize",
            "validation_errors": errors,
            "instruction": repair_instruction or (
                "Normalize the existing candidate into the required JSON schema. "
                "Do not perform new research, rewrite the answer, invent evidence IDs, "
                "or add claims that are absent from the supplied context. Unknown values "
                "must be represented as insufficient evidence or a limitation."
            ),
            "required_output_fields": list(schema.model_fields),
        }})
        try:
            return complete_structured(
                complete,
                repair_messages,
                repair_schema_name,
                schema,
                attempts=repair_attempts,
                validate=validate,
            )
        except ValueError as repair_exc:
            last_exc = repair_exc

    detail = "; ".join(
        f"{item['field']}: {item['error']}" for item in errors[:8]
    ) or str(last_exc or "unknown structured output error")[:1000]
    raise ValueError(
        f"{schema_name} returned invalid structured output after {attempts} attempts"
        + (f" plus {repair_attempts} normalization attempts" if repair_schema_name else "")
        + ": " + detail
    ) from last_exc
    raise AssertionError("unreachable")


__all__ = ["complete_structured"]
