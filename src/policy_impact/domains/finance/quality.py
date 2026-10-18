"""Hard publication prerequisites independent of model wording."""

from datetime import date, datetime, timezone


class FinanceQualityError(ValueError):
    """The requested evidence did not meet the financial delivery contract."""


def require_market_coverage(request: dict, snapshot: dict, metrics: list[dict]) -> dict:
    expected = set(request.get("symbols", []))
    series = {item["symbol"]: item for item in snapshot["series"]}
    indexed = {item["symbol"]: item for item in metrics}
    issues = []
    for symbol in sorted(expected):
        data, metric = series.get(symbol), indexed.get(symbol)
        if not data or not data.get("bars") or not metric:
            issues.append(f"{symbol} 缺少行情或可计算指标")
            continue
        if metric.get("status") != "ok" or metric.get("change_pct") is None:
            issues.append(f"{symbol} 缺少完整基准，无法计算涨跌")
        if symbol in {"xauusd", "xagusd"}:
            missing_days = data.get("session_evidence", {}).get("missing_reference_dates", [])
            if missing_days:
                issues.append(f"{symbol} 缺少完整UTC参考日或期前基准：" + "、".join(missing_days[:5]))
            if data.get("price_basis") != "spot_price":
                issues.append(f"{symbol} 不是已验证的国际现货口径")
            if data.get("unit") != "USD/troy_oz":
                issues.append(f"{symbol} 计价单位不是美元/金衡盎司")
            if data.get("session_evidence", {}).get("constant_reference_day"):
                issues.append(f"{symbol} 为可能沿用旧价的平价参考日，不能证明是新交易日行情")
        expected_day = data.get("expected_session_date")
        latest_day = metric.get("end_date")
        if expected_day and latest_day != expected_day:
            issues.append(f"{symbol} 应有 {expected_day} 的完整行情，实际仅到 {latest_day}")
        if request.get("session_mode") == "latest_completed" and not expected_day:
            issues.append(f"{symbol} 未能核实最近完整日线日期")
        if request.get("session_mode") == "latest_completed" and data.get("session_verified") is False:
            issues.append(f"{symbol} 日线时段未通过验证")
        if request.get("session_mode") != "latest_completed" and metric.get("end_gap_calendar_days", 0) > 5:
            issues.append(f"{symbol} 行情距离请求截止日过远")
        retrieved = datetime.fromisoformat(data["retrieved_at"].replace("Z", "+00:00"))
        if retrieved.tzinfo is None or retrieved > datetime.now(timezone.utc):
            issues.append(f"{symbol} 采集时间无效")
        if latest_day and date.fromisoformat(latest_day) > date.fromisoformat(request["period"]["end"]):
            issues.append(f"{symbol} 使用了请求截止日之后的行情")
    if issues:
        raise FinanceQualityError("核心数据校验失败，未发布报告：" + "；".join(issues))
    return {"decision": "allow", "required_symbols": sorted(expected),
            "reason": "请求范围内行情、基准和日线口径通过确定性校验"}
