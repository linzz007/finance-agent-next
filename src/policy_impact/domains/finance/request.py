"""Resolve observation intent; never equate a user's date with all market sessions."""

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from policy_impact.domains.finance.market_catalog import INSTRUMENTS
from policy_impact.domains.finance.schemas import Period


SYMBOL_NAMES = {symbol: metadata["name"] for symbol, metadata in INSTRUMENTS.items()}
DEFAULT_SYMBOLS = ["xauusd", "xagusd", "spx", "ixic", "dji"]
US_INDICES = ["spx", "ixic", "dji"]
CHINA_INDICES = ["sh000001", "sh000300", "sz399006"]


def _mentions(query: str, alias: str) -> bool:
    if alias.isascii():
        pattern = r"(?<![a-z0-9])" + re.escape(alias).replace(r"\ ", r"\s*") + r"(?![a-z0-9])"
        return re.search(pattern, query, re.I) is not None
    return re.sub(r"\s+", "", alias) in re.sub(r"\s+", "", query)


def _scope_parts(query: str) -> tuple[str, str]:
    positive, excluded = [], []
    for clause in re.split(r"[，,。；;]", query):
        if re.search(r"(?:只|仅)(?:作|作为)(?:宏观)?背景|仅供背景|不要求.{0,8}行情|暂无行情.*不编", clause):
            continue
        negative = re.search(r"不要|不看|不关注|不需要|不用|不再看|排除|剔除|禁止|不能|不准|不是", clause)
        if negative:
            positive.append(clause[:negative.start()])
            tail = clause[negative.start():]
            # Publication vetoes and proxy bans do not exclude the underlying asset.
            proxy_ban = re.search(r"期货|ETF|CFD|差价合约", tail, re.I) and re.search(
                r"替代|代替|充当|冒充", tail)
            if not proxy_ban and not re.search(r"(?:发布|生成|输出|编造|编)", tail):
                excluded.append(tail)
        else:
            positive.append(clause)
    return "，".join(positive), "，".join(excluded)


def _symbols(query: str, profile: dict) -> list[str]:
    positive, excluded = _scope_parts(query)
    only = re.search(r"(?:只看|只要|仅看|只分析)([^，,。；;]+)", positive)
    scope = only[1] if only else positive
    unsupported = re.search(
        r"美元指数|美汇指数|恐慌指数|波动率指数|纳斯达克\s*100|纳指\s*100|"
        r"恒生|日经|比特币|原油|美债|个股|期货|差价合约|"
        r"美元(?:走势|行情|涨跌|汇率)|(?<![A-Za-z])ETF(?![A-Za-z])|"
        r"(?<![A-Za-z0-9])(?:DXY|VIX|NDX|HSI|BTC|CFD)(?![A-Za-z0-9])", scope, re.I)
    if unsupported:
        raise ValueError(f"当前尚未接入{unsupported[0]}的可验证行情，不会用其他资产替代。")
    # XAU/USD and USD/troy_oz are quote units, not dollar-index subscriptions.
    if re.search(r"(?:^|以及|和|与|、|及)\s*美元(?!计价|价格|/|每)", scope):
        raise ValueError("美元行情尚未接入；现货金银的USD计价不代表提供美元指数。")
    unknown = set(re.findall(r"(?<![a-z0-9])(?:sh|sz)\d{6}(?!\d)", scope.lower())) - set(INSTRUMENTS)
    if unknown:
        raise ValueError("未配置的指数代码：" + "、".join(sorted(unknown)))
    selected = []
    for symbol, metadata in INSTRUMENTS.items():
        if any(_mentions(scope, alias) for alias in [symbol, metadata["name"], *metadata.get("aliases", [])]):
            selected.append(symbol)
    if not set(selected) & set(US_INDICES) and re.search(
        r"美股|美国(?:主要|三大)?(?:股市|指数)|US\s*(?:indices|stocks)", scope, re.I
    ):
        selected.extend(US_INDICES)
    if not any(symbol.startswith(("sh", "sz")) for symbol in selected) and re.search(
        r"A股|沪深股市|中国(?:主要)?(?:股市|指数)", scope, re.I
    ):
        selected.extend(CHINA_INDICES)
    if not set(selected) & {"xauusd", "xagusd"} and re.search(r"贵金属|金银", scope):
        selected.extend(["xauusd", "xagusd"])
    configured = profile.get("symbols", DEFAULT_SYMBOLS)
    if not isinstance(configured, list):
        raise ValueError("档案symbols必须是代码列表")
    symbols = selected if selected else list(configured)
    if any(not isinstance(symbol, str) or symbol not in INSTRUMENTS for symbol in symbols):
        raise ValueError("档案包含未接入的行情代码")
    if re.search(r"只看宏观|只看机构|只看新闻", query):
        symbols = []
    elif re.search(r"不要指数|不看指数", query):
        symbols = [symbol for symbol in symbols if INSTRUMENTS[symbol]["asset_class"] != "equity_index"]
    excluded_symbols = {symbol for symbol, metadata in INSTRUMENTS.items()
                        if any(_mentions(excluded, alias) for alias in
                               [symbol, metadata["name"], *metadata.get("aliases", [])])}
    if re.search(r"美股|美国(?:主要|三大)?指数", excluded):
        excluded_symbols.update(US_INDICES)
    if re.search(r"A股|中国指数", excluded):
        excluded_symbols.update(symbol for symbol in INSTRUMENTS if symbol.startswith(("sh", "sz")))
    symbols = [symbol for symbol in symbols if symbol not in excluded_symbols]
    return list(dict.fromkeys(symbols))


def _time_scopes(query: str) -> tuple[str, str]:
    """Separate document-only clauses without treating their dates as price dates."""
    market, documents = [], []
    document_words = r"宏观|新闻|资料|文章|资讯|机构|发布"
    market_words = r"行情|价格|指数|黄金|白银|金银|美股|A股|股市"
    clauses = re.split(r"[，,。；;！？!?\n]|(?:结合|参考)|(?:和|以及)(?=(?:近|最近|过去|本周|本月|\d{4}-))", query)
    for clause in clauses:
        has_instrument = any(
            _mentions(clause, alias)
            for symbol, meta in INSTRUMENTS.items()
            for alias in [symbol, meta["name"], *meta.get("aliases", [])]
        )
        if re.search(document_words, clause) and not re.search(market_words, clause) and not has_instrument:
            documents.append(clause)
        else:
            market.append(clause)
    return "，".join(market), "，".join(documents)


def _resolve_period(query: str, today: date, days: int, default_mode: str) -> tuple[Period, str, str]:
    if re.search(r"明天|后天|下周|下个月|下月|(?:最近|近|过去)\s*\d+\.\d+\s*(?:天|日|周|月)", query):
        raise ValueError("报告仅支持已发生的整数天时间窗口，请明确历史起止日期")
    if type(days) is not int or not 1 <= days <= 366:
        raise ValueError("金融观察时间窗口需为 1 至 366 天")
    origin, explicit_window = "profile", False
    daily = bool(re.search(r"今天|今日|当日|日报|日评|最新行情|\btoday\b|\bdaily\b", query, re.I))
    if re.search(r"最近一周|近一周|过去一周", query):
        days, origin, explicit_window = 7, "query", True
    elif re.search(r"本周|本星期", query):
        days, origin, explicit_window = today.weekday() + 1, "query", True
    elif "本月" in query:
        days, origin, explicit_window = today.day, "query", True
    elif re.search(r"最近一月|最近一个月|近一个月|近一月", query):
        days, origin, explicit_window = 30, "query", True
    match = re.search(r"(?:最近|近|过去)\s*(\d+)\s*(?:个)?(天|日|周|月)", query)
    if match:
        days = int(match[1]) * {"天": 1, "日": 1, "周": 7, "月": 30}[match[2]]
        origin, explicit_window = "query", True
    if not 1 <= days <= 366:
        raise ValueError("金融观察时间窗口需为 1 至 366 天")
    end, start = today, today - timedelta(days=days - 1)
    if "上周" in query:
        end = today - timedelta(days=today.weekday() + 1)
        start, origin, explicit_window = end - timedelta(days=6), "query", True
    explicit = re.findall(r"\d{4}-\d{2}-\d{2}", query)
    if len(explicit) > 2:
        raise ValueError("请用一个起止日期范围指定报告期，不要混用多段日期")
    if explicit:
        start, end = date.fromisoformat(explicit[0]), date.fromisoformat(explicit[-1])
        origin, explicit_window = "query", True
    elif re.search(r"昨天|昨日|前天", query):
        start = end = today - timedelta(days=2 if "前天" in query else 1)
        origin, explicit_window = "query", True
    if default_mode not in ("latest_completed", "explicit_period"):
        raise ValueError("invalid default_session_mode")
    session_mode = "explicit_period" if explicit_window else (
        "latest_completed" if daily or default_mode == "latest_completed" else "explicit_period")
    if session_mode == "latest_completed":
        start = end = today
        origin = "query" if daily else "profile"
    period = Period(start=start, end=end)
    if end > today or (end - start).days >= 366:
        raise ValueError("报告不能使用未来日期，且时间跨度不得超过 366 天")
    return period, session_mode, origin


def parse_request(query: str, profile: dict, *, today: date | None = None,
                  now: datetime | None = None) -> dict:
    """Resolve prices and document publication windows independently.

    Daily price periods are user-date anchors; acquisition resolves each market's
    completed session. Explicit historical price dates are never shifted here.
    today is the legacy deterministic midnight hook; now must be timezone-aware.
    """
    if not isinstance(query, str):
        raise ValueError("query must be text")
    original_query = query
    query = re.sub(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})[日号]",
                   lambda m: f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}", query)
    zone = ZoneInfo(profile.get("timezone", "Asia/Shanghai"))
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        cutoff = now.astimezone(zone)
        if today is not None and today != cutoff.date():
            raise ValueError("today and now disagree in the profile timezone")
    else:
        cutoff = datetime.combine(today, time.min, zone) if today is not None else datetime.now(zone)
    today = cutoff.date()
    if re.search(r"盘中|实时(?:行情|价格)|intraday|real[- ]?time", _scope_parts(query)[0], re.I):
        raise ValueError("当前提供完整交易日观察，不提供盘中或实时行情")
    market_query, document_query = _time_scopes(query)
    period, session_mode, origin = _resolve_period(
        market_query, today, profile.get("report_days", 7),
        profile.get("default_session_mode", "explicit_period"))
    document_days = profile.get("document_days", 7)
    if type(document_days) is not int or not 1 <= document_days <= 365:
        raise ValueError("document_days must be an integer between 1 and 365")
    document_period = (Period(start=today - timedelta(days=document_days - 1), end=today)
                       if session_mode == "latest_completed" else period)
    document_basis = ("recent_context_not_same_session_news" if session_mode == "latest_completed"
                      else "explicit_publication_date_window")
    if document_query:
        candidate, _, document_origin = _resolve_period(document_query, today, document_days, "explicit_period")
        if document_origin == "query":
            document_period = candidate
            document_basis = "explicit_publication_date_window"
    quality_policy = profile.get("quality_policy", "allow_partial")
    if quality_policy not in ("strict", "allow_partial"):
        raise ValueError("quality_policy must be strict or allow_partial")
    symbols = _symbols(query, profile)
    return {
        "schema_version": "finance.request.v1", "question": original_query,
        "period": period.model_dump(mode="json"), "timezone": str(zone),
        "as_of": cutoff.isoformat(), "symbols": symbols, "profile_version": profile.get("version"),
        "period_origin": origin, "session_mode": session_mode, "quality_policy": quality_policy,
        "document_period": document_period.model_dump(mode="json"),
        "document_period_basis": document_basis,
        "include_documents": not bool(re.search(r"只(?:看|要|分析).{0,12}指数|不要(?:宏观|机构|新闻|文章)", query)),
        "force_refresh": bool(re.search(r"刷新|重新获取|最新数据", query)),
    }
