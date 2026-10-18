"""Conservative, title-only month hints; no publication-date or body inference."""

from __future__ import annotations

from collections.abc import Mapping
import re


_MONTHS = {name.lower(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
), start=1)}
_MONTH_NAMES = "|".join(_MONTHS)
_CHINESE_PERIOD = re.compile(
    r"(?<![0-9])(?P<year>[0-9]{4})\s*\u5e74\s*(?P<start>[0-9]{1,2})\s*"
    r"(?:(?:\u6708(?:\u4efd)?\s*)?[-\u2014\u2013\uff0d~\uff5e\u81f3\u5230]\s*(?P<end>[0-9]{1,2})\s*)?\u6708(?:\u4efd)?"
)
_ENGLISH_PERIOD = re.compile(
    rf"\b(?:in\s+(?P<single>{_MONTH_NAMES})|"
    rf"from\s+(?P<start>{_MONTH_NAMES})\s+to\s+(?P<end>{_MONTH_NAMES}))"
    r"\s+(?P<year>[0-9]{4})\b", re.IGNORECASE,
)
_UNSUPPORTED_SUFFIX = re.compile(
    r"^\s*(?:[0-9]{1,2}\s*\u65e5|\u4e0a\u65ec|\u4e2d\u65ec|\u4e0b\u65ec|\u4e0a\u534a\u6708|\u4e0b\u534a\u6708|[-\u2014\u2013\uff0d~\uff5e\u81f3\u5230]|to\b|through\b)",
    re.IGNORECASE,
)
_SCOPE_NOTE = (
    "\u4ec5\u4f9d\u636e\u6807\u9898\u8bc6\u522b\u6307\u6807\u7edf\u8ba1\u671f\uff0c\u4e0d\u4ee3\u8868\u539f\u6587\u6240\u6709\u6570\u5b57\u91c7\u7528\u540c\u4e00\u53e3\u5f84\uff1b"
    "\u6b63\u6587\u53ef\u80fd\u540c\u65f6\u5305\u542b\u5f53\u6708\u3001\u7d2f\u8ba1\u3001\u540c\u6bd4\u6216\u73af\u6bd4\u6570\u636e\u3002"
)


def document_period_hint(document: Mapping[str, object]) -> dict:
    """Recognize explicit year/month titles for macro releases only.

    start_month/end_month are inclusive YYYY-MM strings, not publication dates.
    A month range does not establish that every statistic is cumulative. Ambiguous,
    invalid, partial-month and unsupported periods remain unknown; no year fallback.
    Only kind/title are read, so a body containing other periods cannot change the hint.
    """
    result = {
        "status": "unknown", "period_kind": "unknown",
        "start_month": None, "end_month": None, "matched_text": None,
        "hint": "\u6807\u9898\u672a\u63d0\u4f9b\u53ef\u53ef\u9760\u8bc6\u522b\u7684\u663e\u5f0f\u5e74\u4efd\u53ca\u6708\u4efd\u7edf\u8ba1\u671f\uff0c\u7edf\u8ba1\u671f\u672a\u77e5\u3002",
        "scope_note": _SCOPE_NOTE, "reason": "no_explicit_title_period",
    }
    if document.get("kind") != "macro_release":
        return {**result, "status": "not_applicable", "reason": "not_macro_release",
                "hint": "\u975e\u5b8f\u89c2\u7edf\u8ba1\u53d1\u5e03\uff0c\u4e0d\u63a8\u65ad\u7edf\u8ba1\u671f\u3002"}
    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        return result
    candidates = []
    for pattern in (_CHINESE_PERIOD, _ENGLISH_PERIOD):
        for match in pattern.finditer(title):
            if _UNSUPPORTED_SUFFIX.match(title[match.end():]):
                return {**result, "reason": "unsupported_title_period",
                        "hint": "\u6807\u9898\u5305\u542b\u4e0d\u652f\u6301\u7684\u65e5\u671f\u6216\u6708\u4efd\u533a\u95f4\u8868\u8fbe\uff0c\u7edf\u8ba1\u671f\u672a\u77e5\u3002"}
            fields = match.groupdict()
            year = int(fields["year"])
            if pattern is _CHINESE_PERIOD:
                start = int(fields["start"])
                end = int(fields["end"] or fields["start"])
            else:
                start = _MONTHS[(fields["single"] or fields["start"]).lower()]
                end = _MONTHS[(fields["single"] or fields["end"]).lower()]
            if not 1 <= year <= 9999 or not 1 <= start <= end <= 12:
                return {**result, "reason": "invalid_title_period",
                        "hint": "\u6807\u9898\u5e74\u4efd\u6216\u6708\u4efd\u533a\u95f4\u65e0\u6548\uff0c\u7edf\u8ba1\u671f\u672a\u77e5\u3002"}
            candidates.append((year, start, end, match.group()))
    if not candidates:
        return result
    if len({candidate[:3] for candidate in candidates}) != 1:
        return {**result, "reason": "ambiguous_title_period",
                "hint": "\u6807\u9898\u5305\u542b\u591a\u4e2a\u4e0d\u540c\u7edf\u8ba1\u671f\uff0c\u4e0d\u80fd\u5408\u5e76\u4e3a\u7edf\u4e00\u53e3\u5f84\u3002"}
    year, start, end, matched_text = candidates[0]
    single = start == end
    label = f"{year}\u5e74{start}\u6708\uff08\u5f53\u6708\uff09" if single else f"{year}\u5e74{start}\u81f3{end}\u6708\uff08\u533a\u95f4\uff09"
    return {
        **result, "status": "known", "period_kind": "single_month" if single else "month_range",
        "start_month": f"{year:04d}-{start:02d}", "end_month": f"{year:04d}-{end:02d}",
        "matched_text": matched_text, "hint": f"\u6807\u9898\u6307\u6807\u671f\uff1a{label}\u3002",
        "reason": "explicit_title_period",
    }
