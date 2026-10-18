"""Offline title-period contracts; no inference from publication dates or full text."""

from copy import deepcopy
import json

import pytest

from policy_impact.domains.finance.document_period import document_period_hint


def document(title, **extra):
    return {"kind": "macro_release", "title": title, "published_at": "2026-09-15", **extra}


@pytest.mark.parametrize("title, start, end, kind", [
    ("2026\u5e748\u6708\u4efd\u89c4\u6a21\u4ee5\u4e0a\u5de5\u4e1a\u589e\u52a0\u503c\u589e\u957f\u60c5\u51b5", "2026-08", "2026-08", "single_month"),
    ("2026\u5e741\u20148\u6708\u4efd\u5168\u56fd\u56fa\u5b9a\u8d44\u4ea7\u6295\u8d44\u60c5\u51b5", "2026-01", "2026-08", "month_range"),
    ("2026\u5e741\u20148\u6708\u4efd\u5168\u56fd\u623f\u5730\u4ea7\u5f00\u53d1\u548c\u9500\u552e\u60c5\u51b5", "2026-01", "2026-08", "month_range"),
    ("Industrial Production Operation in August 2026", "2026-08", "2026-08", "single_month"),
    ("Investment in Fixed Assets from January to August 2026", "2026-01", "2026-08", "month_range"),
    ("Real Estate Development from January to August 2026", "2026-01", "2026-08", "month_range"),
    ("2026 \u5e74 8 \u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1", "2026-08", "2026-08", "single_month"),
    ("2026\u5e742\u6708\u81f38\u6708\u5de5\u4e1a\u7edf\u8ba1", "2026-02", "2026-08", "month_range"),
    ("Output IN AUGUST 2026", "2026-08", "2026-08", "single_month"),
    ("Output from January to December 2026", "2026-01", "2026-12", "month_range"),
    ("2025\u5e7412\u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1", "2025-12", "2025-12", "single_month"),
    ("2026\u5e748\u6708\u4efd / in August 2026", "2026-08", "2026-08", "single_month"),
])
def test_explicit_title_periods(title, start, end, kind):
    result = document_period_hint(document(title))
    assert result["status"] == "known"
    assert result["period_kind"] == kind
    assert (result["start_month"], result["end_month"]) == (start, end)
    assert result["matched_text"] in title
    assert ("\u5f53\u6708" if kind == "single_month" else "\u533a\u95f4") in result["hint"]
    assert result["reason"] == "explicit_title_period"
    assert "\u4ec5\u4f9d\u636e\u6807\u9898" in result["scope_note"]
    assert "\u4e0d\u4ee3\u8868\u539f\u6587\u6240\u6709\u6570\u5b57" in result["scope_note"]
    assert "\u5f53\u6708\u3001\u7d2f\u8ba1" in result["scope_note"]
    assert "\u5168\u90e8\u7d2f\u8ba1" not in result["hint"]
    json.dumps(result, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("separator", ["\u2014", "\u2013", "-", "\uff0d", "\u81f3", "\u5230", "~", "\uff5e"])
def test_chinese_range_separator_variants(separator):
    result = document_period_hint(document(f"2026\u5e741{separator}8\u6708\u4efd\u6295\u8d44\u60c5\u51b5"))
    assert result["status"] == "known"
    assert (result["start_month"], result["end_month"]) == ("2026-01", "2026-08")


@pytest.mark.parametrize("title", [
    "8\u6708\u4efd\u89c4\u6a21\u4ee5\u4e0a\u5de5\u4e1a\u589e\u52a0\u503c", "1\u20148\u6708\u4efd\u5168\u56fd\u56fa\u5b9a\u8d44\u4ea7\u6295\u8d44\u60c5\u51b5",
    "Industrial Production in August", "Investment from January to August",
    "2026\u5e74\u4e0a\u534a\u5e74\u5de5\u4e1a\u7edf\u8ba1", "Industrial Production in Q3 2026",
    "2026\u5e74\u5de5\u4e1a\u7edf\u8ba1", "\u7ecf\u6d4e\u89c2\u5bdf", "", None, 202608,
    "Output from December 2025 to January 2026",
])
def test_unknown_periods_do_not_borrow_publication_year_or_body(title):
    result = document_period_hint(document(title, text="2026\u5e741\u20148\u6708\u4efd\u7d2f\u8ba1\u589e\u957f5%\uff0c8\u6708\u4efd\u589e\u957f4%\u3002"))
    assert result["status"] == "unknown"
    assert result["period_kind"] == "unknown"
    assert result["start_month"] is result["end_month"] is result["matched_text"] is None
    assert "\u672a\u77e5" in result["hint"]


@pytest.mark.parametrize("title", [
    "2026\u5e740\u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1", "2026\u5e7413\u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1", "2026\u5e741\u201413\u6708\u4efd\u6295\u8d44\u60c5\u51b5",
    "2026\u5e748\u20141\u6708\u4efd\u6295\u8d44\u60c5\u51b5", "0000\u5e748\u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1",
    "Output from August to January 2026", "Output in August 0000",
])
def test_invalid_months_years_and_reversed_ranges_are_unknown(title):
    result = document_period_hint(document(title))
    assert result["status"] == "unknown"
    assert result["reason"] == "invalid_title_period"
    assert result["start_month"] is result["end_month"] is None


@pytest.mark.parametrize("title", [
    "2026\u5e748\u670816\u65e5\u7ecf\u6d4e\u65b0\u95fb\u53d1\u5e03\u4f1a", "2026\u5e748\u6708\u4e0a\u65ec\u751f\u4ea7\u60c5\u51b5",
    "2026\u5e748\u6708\u4e0b\u534a\u6708\u751f\u4ea7\u60c5\u51b5", "2026\u5e748\u6708\u20142027\u5e741\u6708\u7ecf\u6d4e\u7edf\u8ba1",
    "Output in August 2026 to January 2027",
])
def test_dated_events_partial_months_and_cross_year_ranges_are_not_truncated(title):
    result = document_period_hint(document(title))
    assert result["status"] == "unknown"
    assert result["reason"] == "unsupported_title_period"


@pytest.mark.parametrize("title", [
    "2026\u5e748\u6708\u4efd\u5de5\u4e1a\u589e\u52a0\u503c\u53ca2026\u5e741\u20148\u6708\u4efd\u6295\u8d44\u60c5\u51b5",
    "Industrial Production in August 2026 and Investment from January to August 2026",
    "2026\u5e748\u6708\u4efd\u4e0e2025\u5e748\u6708\u4efd\u5de5\u4e1a\u7edf\u8ba1\u6bd4\u8f83",
])
def test_multiple_distinct_explicit_periods_are_not_combined(title):
    result = document_period_hint(document(title))
    assert result["status"] == "unknown"
    assert result["reason"] == "ambiguous_title_period"
    assert result["start_month"] is result["end_month"] is None


@pytest.mark.parametrize("kind", ["institution_view", "policy_release", None, "unknown"])
def test_non_macro_documents_do_not_infer_period(kind):
    result = document_period_hint(document("2026\u5e741\u20148\u6708\u4efd\u7ecf\u6d4e\u89c2\u5bdf", kind=kind))
    assert result["status"] == "not_applicable"
    assert result["reason"] == "not_macro_release"
    assert result["period_kind"] == "unknown"
    assert result["start_month"] is result["end_month"] is result["matched_text"] is None


def test_industry_month_is_not_overwritten_by_cumulative_body_or_other_documents():
    industry = document("2026\u5e748\u6708\u4efd\u89c4\u6a21\u4ee5\u4e0a\u5de5\u4e1a\u589e\u52a0\u503c\u589e\u957f\u60c5\u51b5",
                        text="8\u6708\u4efd\u540c\u6bd4\u589e\u957f5%\u30021\u20148\u6708\u4efd\u7d2f\u8ba1\u540c\u6bd4\u589e\u957f6%\u3002")
    original = deepcopy(industry)
    result = document_period_hint(industry)
    for title in ("2026\u5e741\u20148\u6708\u4efd\u5168\u56fd\u56fa\u5b9a\u8d44\u4ea7\u6295\u8d44\u60c5\u51b5", "2026\u5e741\u20148\u6708\u4efd\u5168\u56fd\u623f\u5730\u4ea7\u5f00\u53d1\u548c\u9500\u552e\u60c5\u51b5"):
        other = document_period_hint(document(title))
        assert other["period_kind"] == "month_range"
        assert other["start_month"] == "2026-01"
    assert result == document_period_hint(industry)
    assert industry == original
    assert result["period_kind"] == "single_month"
    assert result["start_month"] == result["end_month"] == "2026-08"
    assert "\u4e0d\u4ee3\u8868\u539f\u6587\u6240\u6709\u6570\u5b57" in result["scope_note"]


def test_only_kind_and_title_are_read_and_input_is_not_mutated():
    class TitleOnlyDocument(dict):
        def get(self, key, default=None):
            assert key in {"kind", "title"}
            return super().get(key, default)

    data = TitleOnlyDocument(document("Output in August 2026", published_at="2030-01-01"))
    original = deepcopy(data)
    result = document_period_hint(data)
    assert result["start_month"] == "2026-08"
    assert data == original
    assert document_period_hint({})["status"] == "not_applicable"
    assert document_period_hint({"kind": "macro_release"})["status"] == "unknown"
