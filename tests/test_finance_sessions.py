"""Deterministic library-backed sessions, including official 2026 NYSE closures."""

from datetime import datetime
import sys

import pytest

from policy_impact.domains.finance.sessions import (
    SessionCalendarUnavailable, completed_session_dates, latest_completed_session, session_dates,
)


@pytest.mark.parametrize("as_of,expected", [
    ("2026-09-23T00:05:00+08:00", "2026-09-21"),
    ("2026-09-23T05:00:00+08:00", "2026-09-22"),
    ("2026-09-27T12:00:00Z", "2026-09-25"),
    ("2026-09-07T23:00:00Z", "2026-09-04"),
    ("2026-11-27T18:19:00Z", "2026-11-25"),
    ("2026-11-27T18:20:00Z", "2026-11-27"),
])
def test_latest_completed_accounts_for_cross_date_weekend_holiday_and_early_close(as_of, expected):
    resolved = latest_completed_session("XNYS", as_of=as_of)
    assert resolved["session_date"] == resolved["expected_session_date"] == expected
    assert resolved["session_verified"]
    assert resolved["calendar_id"] == "XNYS"
    assert resolved["session_evidence"]["library_version"] == "4.13.2"
    assert "unannounced" not in resolved["session_date_basis"]


def test_half_day_close_is_not_assumed_to_be_4pm():
    resolved = latest_completed_session("XNYS", as_of="2026-11-27T18:00:00Z", publication_delay_minutes=0)
    assert resolved["session_date"] == "2026-11-27"
    assert resolved["session_close_at"] == "2026-11-27T18:00:00+00:00"
    assert resolved["expected_previous_session_date"] == "2026-11-25"


@pytest.mark.parametrize("as_of,close", [
    ("2026-03-06T22:00:00Z", "2026-03-06T21:00:00+00:00"),
    ("2026-03-09T22:00:00Z", "2026-03-09T20:00:00+00:00"),
    ("2026-11-02T22:00:00Z", "2026-11-02T21:00:00+00:00"),
])
def test_dst_uses_exchange_calendar_not_fixed_utc_close(as_of, close):
    assert latest_completed_session("XNYS", as_of=as_of)["session_close_at"] == close


def test_real_session_range_excludes_thanksgiving_without_handwritten_holidays():
    assert [str(day) for day in session_dates("XNYS", "2026-11-25", "2026-11-27")] == [
        "2026-11-25", "2026-11-27"]


@pytest.mark.parametrize("as_of,expected", [
    ("2026-11-27T18:19:59Z", ["2026-11-25"]),
    ("2026-11-27T18:20:00Z", ["2026-11-25", "2026-11-27"]),
])
def test_completed_coverage_uses_real_early_close_and_publication_buffer(as_of, expected):
    actual = completed_session_dates("XNYS", "2026-11-25", "2026-11-27", as_of=as_of)
    assert list(map(str, actual)) == expected


def test_completed_coverage_returns_no_dates_for_unfinished_explicit_single_day():
    assert completed_session_dates("XNYS", "2026-09-23", "2026-09-23",
                                   as_of="2026-09-23T12:00:00Z") == []


@pytest.mark.parametrize("calendar", ["spot", "24/5", "24/7", "NOT_AN_EXCHANGE", ""])
def test_unsupported_calendar_cannot_certify_spot_or_unknown_sessions(calendar):
    with pytest.raises(SessionCalendarUnavailable):
        latest_completed_session(calendar, as_of="2026-09-23T00:30:00Z")


def test_calendar_dependency_missing_fails_instead_of_weekday_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "exchange_calendars", None)
    with pytest.raises(SessionCalendarUnavailable, match="unavailable"):
        latest_completed_session("XNYS", as_of="2026-09-23T00:30:00Z")


def test_out_of_future_and_historical_coverage_is_explicit():
    for start, end in [("2099-09-01", "2099-09-02"), ("1900-01-01", "1900-01-02")]:
        with pytest.raises(SessionCalendarUnavailable, match="covers"):
            session_dates("XNYS", start, end)
    with pytest.raises(SessionCalendarUnavailable, match="covers"):
        latest_completed_session("XNYS", as_of="2099-09-23T00:30:00Z")


@pytest.mark.parametrize("delay", [-1, 1441, True, 1.5])
def test_delay_requires_bounded_integer(delay):
    with pytest.raises(ValueError):
        latest_completed_session("XNYS", as_of="2026-09-23T00:30:00Z", publication_delay_minutes=delay)


def test_naive_cutoff_is_not_guessed():
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_completed_session("XNYS", as_of=datetime(2026, 9, 23))
