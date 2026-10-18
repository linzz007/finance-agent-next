"""Exchange sessions from exchange_calendars, never from a weekday approximation.

The dependency's regular-session schedule includes DST, holidays and early
closes. It is a maintained calendar, not a promise of perfect future knowledge.
Missing dependencies, unknown calendars and out-of-coverage requests fail
explicitly. OTC spot metals have no universal exchange close: acquisition must
supply its provider's documented daily-cutoff evidence instead of using this
module to relabel an equity/futures calendar as a spot calendar.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version


class SessionCalendarUnavailable(ValueError):
    """A complete exchange-session answer cannot be established."""


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ValueError("session date must be a date, not a timestamp")
    return date.fromisoformat(value) if isinstance(value, str) else value


def _aware(value: datetime | str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware ISO timestamp")
    return stamp.astimezone(timezone.utc)


def _calendar(calendar_name: str):
    if not isinstance(calendar_name, str) or not calendar_name.strip():
        raise SessionCalendarUnavailable("an exchange calendar ID is required")
    # Do not let generic 24/5 or futures schedules certify an OTC spot close.
    if calendar_name in {"24/5", "24/7"} or "spot" in calendar_name.lower():
        raise SessionCalendarUnavailable("spot metals require provider daily-cutoff evidence")
    try:
        import exchange_calendars
        return exchange_calendars.get_calendar(calendar_name)
    except Exception as exc:
        raise SessionCalendarUnavailable(f"calendar {calendar_name} unavailable: {exc}") from exc


def _check_bounds(calendar, start: date, end: date) -> None:
    if start > end:
        raise ValueError("session range end precedes start")
    if start < calendar.first_session.date() or end > calendar.last_session.date():
        raise SessionCalendarUnavailable(
            f"calendar {calendar.name} covers {calendar.first_session.date()} through "
            f"{calendar.last_session.date()}, not the requested range"
        )


def session_dates(calendar_name: str, start: date | str, end: date | str) -> list[date]:
    """Regular-session labels within the inclusive range; no live-clock reads."""
    start, end = _date(start), _date(end)
    calendar = _calendar(calendar_name)
    _check_bounds(calendar, start, end)
    try:
        return [stamp.date() for stamp in calendar.sessions_in_range(start.isoformat(), end.isoformat())]
    except Exception as exc:
        raise SessionCalendarUnavailable(f"calendar session lookup failed: {exc}") from exc


def completed_session_dates(calendar_name: str, start: date | str, end: date | str, *,
                            as_of: datetime | str, publication_delay_minutes: int = 20) -> list[date]:
    """Requested session labels whose scheduled close plus publication lag has passed.

    The caller keeps the original request boundaries. Missing bars within this
    independently resolved set remain missing; provider data never sets coverage.
    """
    if type(publication_delay_minutes) is not int or not 0 <= publication_delay_minutes <= 1440:
        raise ValueError("publication_delay_minutes must be an integer in [0, 1440]")
    start, end = _date(start), _date(end)
    cutoff = _aware(as_of)
    calendar = _calendar(calendar_name)
    _check_bounds(calendar, start, end)
    lag = timedelta(minutes=publication_delay_minutes)
    try:
        labels = calendar.sessions_in_range(start.isoformat(), end.isoformat())
        return [label.date() for label in labels
                if calendar.session_close(label).to_pydatetime() + lag <= cutoff]
    except Exception as exc:
        raise SessionCalendarUnavailable(f"completed-session coverage lookup failed: {exc}") from exc


def latest_completed_session(calendar_name: str, *, as_of: datetime | str,
                             publication_delay_minutes: int = 20) -> dict:
    """Resolve the latest scheduled regular close plus a provider publication lag.

    Returns metadata only, never prices and never a claim that a provider has
    published a bar. The caller must verify the actual data has this session and
    its immediately preceding session. Values match MarketSeries field names.
    """
    if type(publication_delay_minutes) is not int or not 0 <= publication_delay_minutes <= 1440:
        raise ValueError("publication_delay_minutes must be an integer in [0, 1440]")
    cutoff = _aware(as_of)
    calendar = _calendar(calendar_name)
    local_day = cutoff.astimezone(calendar.tz).date()
    _check_bounds(calendar, local_day, local_day)
    start = max(calendar.first_session.date(), local_day - timedelta(days=60))
    lag = timedelta(minutes=publication_delay_minutes)
    try:
        labels = calendar.sessions_in_range(start.isoformat(), local_day.isoformat())
        for label in reversed(labels):
            close = calendar.session_close(label).to_pydatetime()
            if close + lag > cutoff:
                continue
            previous = calendar.previous_session(label).date()
            session_date = label.date().isoformat()
            library_version = version("exchange-calendars")
            return {
                "session_date": session_date, "expected_session_date": session_date,
                "expected_previous_session_date": previous.isoformat(),
                "session_close_at": close.isoformat(), "calendar_id": calendar_name,
                "timezone": str(calendar.tz), "session_verified": True,
                "session_date_basis": f"exchange_calendars:{calendar_name}:regular_session",
                "session_evidence": {
                    "library": "exchange_calendars", "library_version": library_version,
                    "as_of": cutoff.isoformat(), "publication_delay_minutes": publication_delay_minutes,
                    "calendar_first_session": calendar.first_session.date().isoformat(),
                    "calendar_last_session": calendar.last_session.date().isoformat(),
                    "limitation": "maintained scheduled calendar; unexpected exchange closures may require updates",
                },
            }
    except Exception as exc:
        raise SessionCalendarUnavailable(f"completed-session lookup failed: {exc}") from exc
    raise SessionCalendarUnavailable("no completed session and preceding baseline in the bounded lookup")
