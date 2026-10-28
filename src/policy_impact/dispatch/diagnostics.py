"""Bounded public diagnostics, separate from rejected output and evidence."""

import re

from .observability import redact


def safe_text(value, secret=""):
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)


def public_output_normalization(value):
    """Allow only bounded parser metadata into audit metadata."""
    result = {}
    count = value.get("json_string_controls") if isinstance(value, dict) else None
    if type(count) is int and 1 <= count <= 1_000_000:
        result["json_string_controls"] = count
    count = value.get("json_string_invalid_escapes") if isinstance(value, dict) else None
    if type(count) is int and 1 <= count <= 1_000_000:
        result["json_string_invalid_escapes"] = count
    boundary = value.get("json_action_boundary") if isinstance(value, dict) else None
    if isinstance(boundary, dict):
        strategy = boundary.get("strategy")
        wrapper = boundary.get("leading_wrapper")
        ignored = boundary.get("trailing_output_ignored")
        trailing_chars = boundary.get("trailing_chars")
        if (
            strategy == "first_complete_object"
            and wrapper in {"none", "xml_tags"}
            and type(ignored) is bool
            and type(trailing_chars) is int
            and 0 <= trailing_chars <= 1_000_000
        ):
            result["json_action_boundary"] = {
                "strategy": strategy,
                "leading_wrapper": wrapper,
                "trailing_output_ignored": ignored,
                "trailing_chars": trailing_chars,
            }
    return result


def public_validation_errors(errors, *, secret="", mask_urls=False):
    """Bound field diagnostics without exposing rejected values or error context."""
    if not isinstance(errors, list):
        return []

    def text(value):
        if mask_urls and not isinstance(value, str):
            return ""
        value = safe_text(value, secret)
        if mask_urls:
            value = redact(re.sub(r"https?://\S+", "[redacted]", value, flags=re.IGNORECASE))
        return value

    result = []
    for error in errors[:12]:
        if not isinstance(error, dict):
            continue
        loc = error.get("loc", [])
        if not isinstance(loc, (list, tuple)):
            continue
        result.append({
            "loc": [part if type(part) is int else text(part)[:160]
                    for part in loc[:12] if type(part) in {str, int}],
            "type": text(error.get("type", ""))[:80],
            "msg": text(error.get("msg", ""))[:320],
        })
    return result


_FAILURE_MESSAGES = {
    "transport_error": "Model transport failed.",
    "provider_error": "Model provider reported a failure.",
    "contract_error": "Agent output did not satisfy its contract.",
    "quota_error": "Model quota was unavailable.",
    "timeout": "Execution timed out.",
    "outcome_unknown": "Execution outcome is unknown.",
    "checkpoint_invalid": "Checkpoint validation failed.",
    "artifact_unavailable": "Required artifact was unavailable.",
    "budget_denied": "Execution budget was unavailable.",
    "forbidden": "Execution permission was denied.",
    "internal_error": "Execution failed with an internal error.",
}


def failure_diagnostic(error_code, details=None):
    result = {"error_code": error_code, "message": _FAILURE_MESSAGES[error_code]}
    details = details if isinstance(details, dict) else {}
    stage = details.get("validation_stage")
    if error_code == "contract_error" and isinstance(stage, str) and stage in {
        "action", "final.output",
    }:
        result["validation_stage"] = stage
        result["validation_errors"] = public_validation_errors(
            details.get("validation_errors"), mask_urls=True
        )
    return result
