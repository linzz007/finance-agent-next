from datetime import date

import pytest

from policy_impact.domains.finance.request import parse_request
from policy_impact.domains.finance.schemas import MarketBar, Period


def test_request_obeys_user_period_and_preserves_profile():
    profile = {"report_days": 30, "symbols": ["sh000001"], "version": 3}
    result = parse_request("最近7天，只看沪深300指数", profile, today=date(2026, 9, 22))
    assert result["period"] == {"start": "2026-09-16", "end": "2026-09-22"}
    assert result["symbols"] == ["sh000300"]
    assert not result["include_documents"]
    assert profile["symbols"] == ["sh000001"]
    assert result["profile_version"] == 3


def test_current_day_and_explicit_window_are_distinct():
    assert parse_request("今日日报", {}, today=date(2026, 9, 22))["period"] == {
        "start": "2026-09-22", "end": "2026-09-22"}
    assert parse_request("2026-08-01至2026-08-31", {}, today=date(2026, 9, 22))["period"]["end"] == "2026-08-31"
    for query in ("最近0天", "最近400天", "2026-09-01至2027-01-01", "2026-09-21至2026-08-21"):
        with pytest.raises(ValueError):
            parse_request(query, {}, today=date(2026, 9, 22))


def test_market_data_contract_rejects_invalid_numbers_and_ohlc():
    for close in (float("nan"), float("inf"), -1, 103):
        with pytest.raises(ValueError):
            MarketBar(date="2026-09-21", open=100, high=102, low=99, close=close)
    with pytest.raises(ValueError):
        Period(start="2026-09-22", end="2026-09-21")


def test_one_date_calendar_week_and_unsupported_index_do_not_silently_default():
    today = date(2026, 9, 22)
    assert parse_request("2026-09-01 金融报告", {}, today=today)["period"] == {"start": "2026-09-01", "end": "2026-09-01"}
    assert parse_request("本周报告", {}, today=today)["period"]["start"] == "2026-09-21"
    assert parse_request("本月报告", {}, today=today)["period"]["start"] == "2026-09-01"
    assert parse_request("只看宏观不看指数", {}, today=today)["symbols"] == []
    assert parse_request("只看深证成指", {}, today=today)["symbols"] == ["sz399001"]
    assert parse_request("只看纳斯达克涨跌", {}, today=today)["symbols"] == ["ixic"]
    for query in ("只看纳斯达克100涨跌", "sh600000最近一周", "2026-09-01 2026-09-12 2026-09-21"):
        with pytest.raises(ValueError):
            parse_request(query, {}, today=today)
