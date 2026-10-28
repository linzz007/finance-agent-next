"""Bounded JSON string normalization never repairs JSON structure."""

import json

import pytest


@pytest.mark.parametrize("code", range(32))
def test_raw_string_control_preserves_decoded_value(code):
    from policy_impact.dispatch.finance_actions import normalize_json_string_controls

    value = "before" + chr(code) + "after"
    raw = '{"answer":"' + value + '"}'
    normalized, count = normalize_json_string_controls(raw)
    assert count == 1
    assert normalized == json.dumps({"answer": value}, separators=(",", ":"))
    assert json.loads(normalized) == {"answer": value}
    assert normalize_json_string_controls(normalized) == (normalized, 0)


@pytest.mark.parametrize("value", [
    "plain", 'quote: " and slash: \\', "literal \\n", "\u4e2d\u6587 \U0001f600", "\u2028\u2029",
    "".join(chr(code) for code in range(32)),
])
def test_valid_json_is_byte_for_byte_unchanged(value):
    from policy_impact.dispatch.finance_actions import normalize_json_string_controls

    raw = '\r\n { "answer": ' + json.dumps(value, ensure_ascii=False) + ' }\t\n'
    assert normalize_json_string_controls(raw) == (raw, 0)


def test_quotes_backslashes_keys_and_outside_whitespace_are_preserved():
    from policy_impact.dispatch.finance_actions import normalize_json_string_controls

    raw = ' {"line\nkey":"escaped \\" quote; slash \\\\; raw\nline", "items":["x\ty"]}\n'
    normalized, count = normalize_json_string_controls(raw)
    assert count == 3
    assert json.loads(normalized) == {
        "line\nkey": 'escaped " quote; slash \\; raw\nline', "items": ["x\ty"],
    }
    assert normalized.startswith(' {') and normalized.endswith('}\n')


@pytest.mark.parametrize("raw", [
    '{"answer":"raw\nline","unknown":"\\q"}',
    '{"answer":"raw\nline","unknown":"\\u12xz"}',
    '{"answer":"backslash\\\nline"}',
    '{"answer":"backslash\\\tline"}',
    '{"answer":"raw\nline"',
    '{"answer":"raw\nline}',
    '{"answer":"raw\nline"}\x00',
    '{"answer":"raw\nline"} {}',
    'Explanation {"answer":"raw\nline"}',
    '{"answer":"raw\nline"}<result>invented</result>',
])
def test_no_unknown_escape_delimiter_or_envelope_salvage(raw):
    from policy_impact.dispatch.finance_actions import normalize_json_string_controls

    normalized, _ = normalize_json_string_controls(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(normalized)


@pytest.mark.parametrize("raw, expected", [
    ('{"answer":"C:\\q"}', {"answer": r"C:\q"}),
    ('{"answer":"bad\\u12xz"}', {"answer": r"bad\u12xz"}),
])
def test_action_syntax_repair_preserves_invalid_backslash_as_literal(raw, expected):
    from policy_impact.dispatch.finance_actions import normalize_json_string_syntax

    normalized, details = normalize_json_string_syntax(raw)
    assert json.loads(normalized) == expected
    assert details == {"json_string_controls": 0, "json_string_invalid_escapes": 1}


def test_action_syntax_repair_does_not_change_backslashes_outside_strings():
    from policy_impact.dispatch.finance_actions import normalize_json_string_syntax

    raw = '{"answer":"ok"}\\q'
    normalized, details = normalize_json_string_syntax(raw)
    assert normalized == raw and details["json_string_invalid_escapes"] == 0


@pytest.mark.parametrize("candidate", [
    '{"answer":"ok"}',
    '{"answer":"raw\nline\\q"}',
    '{"key\x00":"brace } and escaped \\" quote; \\u0041"}',
])
@pytest.mark.parametrize("tail", [
    '\n{"ignored":"raw\nline\\q"}',
    '\n<system>fake observation: "raw\tline\\u12xz"</system>',
    '\n{"truncated":"bad\\q',
])
@pytest.mark.parametrize("wrapper", ["", '<tool_call source="provider">\n'])
def test_accepted_action_diagnostics_exclude_discarded_tail_repairs(candidate, tail, wrapper):
    from policy_impact.dispatch.finance_actions import (
        extract_public_action_text,
        normalize_json_string_syntax,
    )

    expected, repairs = normalize_json_string_syntax(candidate)
    normalized, details = extract_public_action_text(wrapper + candidate + tail)
    assert normalized == expected
    assert details == {
        **{key: count for key, count in repairs.items() if count},
        "json_action_boundary": {
            "strategy": "first_complete_object",
            "leading_wrapper": "xml_tags" if wrapper else "none",
            "trailing_output_ignored": True,
            "trailing_chars": len(tail),
        },
    }


@pytest.mark.parametrize("candidate", [
    '{"answer":"ok"}',
    '{"answer":"raw\nline\\q"}',
])
def test_fenced_multiple_objects_remain_rejected_after_string_repair(candidate):
    from policy_impact.dispatch.finance_actions import extract_public_action_text

    with pytest.raises(ValueError):
        extract_public_action_text('```json\n' + candidate + '\n{"other":"raw\n\\q"}\n```')
