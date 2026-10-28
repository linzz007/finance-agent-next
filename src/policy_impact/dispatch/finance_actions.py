"""H2 action validation and bounded, non-evidentiary correction diagnostics."""

import json
import re

from pydantic import TypeAdapter, ValidationError

from policy_impact.runtime.actions import AgentAction

CORRECTION_INPUT_POLICY = "finance.h2.isolated_correction.v1"
_XML_TAG_PREFIX = re.compile(r"^(?:\s|<[^<>]{1,512}>){1,32}$", re.DOTALL)
_VALID_JSON_ESCAPES = frozenset({'"', "\\", "/", "b", "f", "n", "r", "t"})


def normalize_public_action_text(content: str) -> str:
    text = content.strip()
    match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.IGNORECASE)
    if match:
        body = match[1].strip()
        if body.startswith("{") and "```" not in body:
            # Normalize a fence only when it contains exactly one JSON object.
            # Extra objects or transcript text inside the fence must remain
            # invalid at the public action boundary.
            normalized_body, _ = normalize_json_string_syntax(body)
            try:
                _, end = json.JSONDecoder().raw_decode(normalized_body, 0)
            except json.JSONDecodeError:
                return text
            if not normalized_body[end:].strip():
                return body
    return text


def normalize_json_string_controls(content: str) -> tuple[str, int]:
    """Escape literal controls only; strict JSON/action parsing still follows.

    This compatibility helper intentionally does not repair invalid escapes.
    The H2 action path uses ``normalize_json_string_syntax`` below so the two
    policies remain explicit.
    """
    normalized, details = normalize_json_string_syntax(content, repair_invalid_escapes=False)
    return normalized, details["json_string_controls"]


def normalize_json_string_syntax(
    content: str, *, repair_invalid_escapes: bool = True
) -> tuple[str, dict[str, int]]:
    """Apply bounded string-literal repair without repairing JSON structure.

    The returned text still has to pass the standard JSON decoder and all
    action/schema checks. Outside a JSON string, backslashes are untouched.
    """
    normalized, details, _ = _repair_json_string_literals(content, repair_invalid_escapes=repair_invalid_escapes)
    return normalized, details


def _repair_json_string_literals(
    content: str, *, repair_invalid_escapes: bool = True
) -> tuple[str, dict[str, int], list[tuple[int, int, str]]]:
    # Sparse (normalized end, cumulative growth, kind) edits map decoder offsets
    # back to the original text without building a per-character offset table.
    parts = []
    edits = []
    in_string = False
    controls = invalid_escapes = 0
    growth = 0
    index = 0
    while index < len(content):
        char = content[index]
        if not in_string:
            parts.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            parts.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            next_char = content[index + 1] if index + 1 < len(content) else None
            if next_char is not None:
                if next_char == "u":
                    digits = content[index + 2:index + 6]
                    if len(digits) == 4 and re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                        parts.append("\\u" + digits)
                        index += 6
                        continue
                if next_char in _VALID_JSON_ESCAPES:
                    parts.append("\\" + next_char)
                    index += 2
                    continue
            if repair_invalid_escapes:
                parts.append("\\\\")
                invalid_escapes += 1
                growth += 1
                edits.append((index + 1 + growth, growth, "json_string_invalid_escapes"))
            else:
                parts.append(char)
                if next_char is not None:
                    parts.append(next_char)
                    index += 1
            index += 1
            continue
        if ord(char) < 32:
            replacement = json.dumps(char)[1:-1]
            parts.append(replacement)
            controls += 1
            growth += len(replacement) - 1
            edits.append((index + 1 + growth, growth, "json_string_controls"))
        else:
            parts.append(char)
        index += 1
    normalized = "".join(parts)
    details = {
        "json_string_controls": controls,
        "json_string_invalid_escapes": invalid_escapes,
    }
    return (normalized if edits else content), details, edits


def extract_public_action_text(content: str) -> tuple[str, dict]:
    """Extract one bounded action object without executing surrounding model text.

    Some text-only gateways append a fabricated tool transcript or a second JSON
    object after a valid action. The first complete object is the only candidate
    that can be executed; all surrounding text remains in the raw audit artifact.
    A leading sequence of XML-like tags is accepted only as a narrow provider
    wrapper compatibility path. Natural-language prefixes are still rejected.
    """
    text = normalize_public_action_text(content)
    start = len(text) - len(text.lstrip())
    prefix_kind = "none"
    if start >= len(text) or text[start] != "{":
        opening = text.find("{", start)
        prefix = text[:opening] if opening >= 0 else ""
        if (
            opening < 0
            or opening - start > 4096
            or not prefix.startswith(text[:start])
            or not _XML_TAG_PREFIX.fullmatch(prefix)
        ):
            raise ValueError("public action must start with a JSON object")
        start = opening
        prefix_kind = "xml_tags"

    # Normalize only the candidate region. XML-like wrappers may contain quote
    # characters, which must not affect JSON string-boundary tracking.
    candidate_text, _, edits = _repair_json_string_literals(text[start:])
    try:
        _, end = json.JSONDecoder().raw_decode(candidate_text, 0)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid public action JSON: {exc.msg}") from exc

    candidate = candidate_text[:end]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid public action JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("public action must be a JSON object")  # noqa: TRY004 - protocol rejection uses the contract error path

    details = {}
    original_end = end
    for edit_end, growth, kind in edits:
        if edit_end > end:
            break
        details[kind] = details.get(kind, 0) + 1
        original_end = end - growth
    trailing = text[start + original_end:]
    if prefix_kind != "none" or trailing.strip():
        details["json_action_boundary"] = {
            "strategy": "first_complete_object",
            "leading_wrapper": prefix_kind,
            "trailing_output_ignored": bool(trailing.strip()),
            "trailing_chars": len(trailing),
        }
    return candidate, details


def correction_diagnostic(content: str, details: dict) -> dict:
    """Locate errors without replaying rejected prose, values or tool markup."""
    diagnostic = {
        "input_policy": CORRECTION_INPUT_POLICY,
        "validation_stage": details.get("validation_stage", "action"),
        "validation_errors": details.get("validation_errors", []),
    }
    if diagnostic["validation_errors"]:
        return diagnostic
    text = normalize_public_action_text(content)
    offset_base = content.find(text)
    # Invalid fenced JSON is intentionally rejected by the public parser, but
    # its correction diagnostic still points into the original artifact.
    stripped = content.strip()
    fenced = re.fullmatch(
        r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", stripped, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        raw_body = fenced[1]
        text = raw_body.strip()
        offset_base = (
            len(content) - len(content.lstrip())
            + fenced.start(1)
            + len(raw_body)
            - len(raw_body.lstrip())
        )
    offset_base = max(offset_base, 0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        offset = offset_base + exc.pos
        syntax = {
            "type": "json_syntax", "msg": exc.msg, "offset": offset,
            "line": content.count("\n", 0, offset) + 1,
            "column": offset - content.rfind("\n", 0, offset),
        }
        # Never return a full body, even a short one; values cannot become evidence.
        width = min(160, len(content) - 1)
        if width > 0:
            start = min(max(0, offset - width // 2), len(content) - width)
            end = start + width
            syntax["excerpt"] = {
                "start": start, "end": end, "trust": "untrusted_syntax_only",
                "text": re.sub(r'[^{}\[\]:,"`<>/=\s]', "?", content[start:end]),
            }
        diagnostic["syntax_error"] = syntax
    except ValueError:
        diagnostic["structure_error"] = "json_value_rejected"
    else:
        if not isinstance(payload, dict):
            diagnostic["structure_error"] = "top_level_object_required"
    return diagnostic


def parse_root_action(payload: object) -> tuple[AgentAction, list[str]]:
    try:
        return TypeAdapter(AgentAction).validate_python(payload), []
    except ValidationError:
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "final"
            or "state_update" not in payload
        ):
            raise
        # Only the optional delta may be discarded; the rest must validate in full.
        envelope = {key: value for key, value in payload.items() if key != "state_update"}
        action = TypeAdapter(AgentAction).validate_python(envelope)
        return action, ["state_update_invalid"]
