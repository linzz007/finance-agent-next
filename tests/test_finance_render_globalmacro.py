"""Offline presentation tests; synthetic observations are not market acceptance data."""

from copy import deepcopy
from html.parser import HTMLParser
import json
import re

import pytest

from policy_impact.domains.finance.render import render_html


class Text(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.parts = []
        self.feed(markup)

    def handle_data(self, data):
        self.parts.append(data)

    @property
    def value(self):
        return " ".join(self.parts)


class MarkupAudit(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def market_row(markup, symbol):
    return re.search(rf'<tr id="market_change_pct:{symbol}">(.*?)</tr>', markup, re.S).group(1)


def report_fixture():
    observations = [
        ("xauusd", "国际现货黄金", "USD/troy_oz", "spot_price", "2026-09-18", 2000, "2026-09-21", 2010),
        ("xagusd", "国际现货白银", "USD/troy_oz", "spot_price", "2026-09-18", 25, "2026-09-21", 24.5),
        ("spx", "标普500", "点", "price_index", "2026-09-18", 5000, "2026-09-21", 5050),
        ("ixic", "纳斯达克综合指数", "点", "price_index", "2026-09-18", 16000, "2026-09-21", 15920),
        ("dji", "道琼斯工业平均指数", "点", "price_index", "2026-09-18", 40000, "2026-09-21", 40400),
        ("sh000001", "上证指数", "点", "price_index", "2026-09-21", 3000, "2026-09-22", 3030),
    ]
    metrics, series = [], []
    for symbol, name, unit, price_basis, start, first, end, last in observations:
        metrics.append({
            "metric_id": f"market_change_pct:{symbol}", "symbol": symbol, "name": name,
            "start_date": start, "end_date": end, "start_close": first, "end_close": last,
            "change_pct": (last / first - 1) * 100, "price_unit": unit, "unit": "%",
            "baseline_available": True, "provider": "fixture-only",
            "source_url": "https://example.com/" + symbol,
            "basis": "fixture: (end / baseline - 1) * 100; observed daily bars only",
            "points": [{"date": start, "close": first}, {"date": end, "close": last}],
        })
        spot = symbol in {"xauusd", "xagusd"}
        series.append({
            "symbol": symbol, "name": name, "unit": unit, "price_basis": price_basis,
            "timezone": "Asia/Shanghai" if symbol.startswith("sh") else "America/New_York",
            "asset_class": "precious_metal" if spot else "equity_index",
            "quote_currency": "USD" if spot else None,
            "calendar_id": "provider_daily" if spot else "XSHG" if symbol.startswith("sh") else "XNYS",
            "session_date": end, "expected_session_date": end,
            "expected_previous_session_date": start, "session_close_at": end + "T17:00:00-04:00",
            "session_date_basis": "provider_date_label" if spot else "exchange_session",
            "session_verified": not spot,
            "session_evidence": {"fixture": True, "observed_date": end},
            "daily_cutoff_basis": "provider_documented_cutoff" if spot else None,
        })
    return {
        "report_id": "fin_" + "a" * 32, "snapshot_id": "fixture-snapshot",
        "request": {"period": {"start": "2026-09-21", "end": "2026-09-22"}},
        "profile_snapshot": {"display_name": "Synthetic observer", "version": 2, "status": "draft",
                             "purpose": "Learning", "focus_topics": ["Rates"], "holdings": None},
        "analysis": {"overview": "已观测资产方向分化，因果联系仍待验证。", "sections": [{
            "title": "利率路径与风险定价", "content": "事实：政策资料描述风险。可能解释：预期或影响定价。反证/待验证：尚无资金流证据。",
            "evidence_refs": ["macro-fixture", "market_change_pct:spx"],
            "evidence_quotes": [{"evidence_ref": "macro-fixture", "quote": "Policy remains conditional."}],
        }], "watch_next": ["核对下一次政策原文。"], "limitations": ["合成数据，仅测试呈现。"]},
        "snapshot": {"collected_at": "2026-09-22T09:00:00Z", "series": series,
                     "session_mode": "latest_completed", "as_of": "2026-09-22T08:00:00Z",
                     "document_period": {"start": "2026-09-01", "end": "2026-09-22"},
                     "source_statuses": [{"source_id": "fixture-only", "status": "partial", "count": 6, "detail": "limited sample"}]},
        "metrics": metrics, "documents": [{"document_id": "macro-fixture", "kind": "policy_release",
            "url": "https://example.com/policy", "title": "Fixture policy", "publisher": "Fixture institution",
            "published_at": "2026-09-20", "retrieved_at": "2026-09-22T09:00:00Z", "text": "Policy remains conditional."}],
        "data_status": "partial", "warnings": ["未覆盖全部宏观来源。"], "wiki_fact_refs": ["profile-fixture"],
    }


def test_cross_asset_order_and_single_evidence_theme_not_per_asset_essays():
    page = render_html(report_fixture())
    headings = re.findall(r"<h2>(.*?)</h2>", page)
    assert headings == ["摘要", "跨资产行情", "宏观主线与证据", "归因核查", "后续观察", "来源附录与边界"]
    assert '<table class="market-table">' in page
    assert page.count('<th scope="row">') == 6
    themes = page.split("<h2>宏观主线与证据</h2>")[1].split("</section>")[0]
    assert themes.count("<article>") == 1
    assert "事实：" in themes and "可能解释：" in themes and "反证/待验证：" in themes
    assert 'href="#macro-fixture"' in themes
    assert "<blockquote>Policy remains conditional." in themes


def test_wiki_citations_resolve_to_immutable_relevant_background_not_empty_anchor():
    report = report_fixture()
    report["analysis"]["sections"][0]["evidence_refs"].append("profile-fixture")
    report["wiki_fact_snapshots"] = [
        {"fact_id": "profile-fixture", "value": "<用户关注黄金>", "source_path": "wiki/profile.md"},
        {"fact_id": "uncited-private-fact", "value": "should-not-display"},
    ]
    page = render_html(report)
    assert '<article id="profile-fixture">' in page
    assert 'href="#profile-fixture"' in page
    assert "&lt;用户关注黄金&gt;" in page and "Wiki 背景快照 · 非市场事实" in page
    assert "should-not-display" not in page
    report.pop("wiki_fact_snapshots")
    legacy = render_html(report)
    assert 'href="#profile-fixture"' not in legacy
    assert "旧报告未保存背景原文" in legacy


def test_small_nonzero_change_keeps_sign_without_negative_zero():
    report = report_fixture()
    report["metrics"][2]["change_pct"] = -0.000773
    page = render_html(report)
    assert "-0.0008%" in page and "-0.00%" not in page
    assert "<details><summary>本报告采用的关注主题</summary>" in page


@pytest.mark.parametrize("symbol,last,change", [("xauusd", "2,010.00", "+0.50%"), ("xagusd", "24.50", "-2.00%")])
def test_spot_price_units_cutoffs_and_no_index_or_futures_mislabel(symbol, last, change):
    row = market_row(render_html(report_fixture()), symbol)
    assert last in row and change in row
    assert "USD/troy_oz" in row and "美元/金衡盎司" in row
    assert "国际现货参考价" in row and "非交易所期货" in row and "无统一全球收盘" in row
    assert "provider_documented_cutoff" in row
    assert "价格指数" not in row and "不含分红" not in row and "收盘趋势" not in row
    assert "市场时段核验：未核验" in row
    assert f'aria-label="{report_fixture()["metrics"][0 if symbol == "xauusd" else 1]["name"]}已观测日线价格（USD/troy_oz）"' in row


@pytest.mark.parametrize("symbol,last,change", [("spx", "5,050.00", "+1.00%"), ("ixic", "15,920.00", "-0.50%"), ("dji", "40,400.00", "+1.00%")])
def test_us_indices_keep_point_units_and_deterministic_values(symbol, last, change):
    row = market_row(render_html(report_fixture()), symbol)
    assert "<td>点<small>价格指数收盘，不含分红再投资</small></td>" in row
    assert last in row and change in row
    assert "USD/troy_oz" not in row and "国际现货" not in row
    assert "市场日历：XNYS" in row


def test_latest_daily_heading_actual_dates_and_final_session_field_contract():
    report = report_fixture()
    page = render_html(report)
    assert "<h1>金融观察报告 · 最近完整日线</h1>" in page
    assert "最近完整日线，按各自市场时段分别观察" in page
    assert "不假定同步收盘" in page and "观察时点：2026-09-22T08:00:00Z" in page
    us = Text(market_row(page, "spx")).value
    china = Text(market_row(page, "sh000001")).value
    assert "2026-09-18" in us and "2026-09-21" in us and "2026-09-22" not in us
    assert "2026-09-21" in china and "2026-09-22" in china
    assert "时段日期依据：exchange_session" in us and "市场时段核验：已核验" in us
    assert "市场日历：XNYS" in us and "时段核验证据" in us
    assert '"observed_date": "2026-09-21"' in us
    assert "市场日历：XSHG" in china
    assert "资料发布日期筛选窗口：2026-09-01 至 2026-09-22；不是资料中各指标的统计期。" in page


def test_expected_session_does_not_relabel_actual_observation_date():
    report = report_fixture()
    report["snapshot"]["series"][0]["expected_session_date"] = "2026-09-22"
    row = market_row(render_html(report), "xauusd")
    assert '2,010.00<small>2026-09-21<br>' in row
    assert "预期完整时段：2026-09-22" in row
    assert "2,010.00<small>2026-09-22" not in row


def test_global_profile_heading_and_sampled_spot_feed_remain_distinct_from_official_close():
    report = report_fixture()
    report["profile_snapshot"]["markets"] = ["全球宏观", "美股"]
    report["snapshot"]["series"][0].update(timezone="UTC", session_evidence={
        "natural_day_reference": True, "market_session_verified": False,
        "interval_seconds": 14400, "last_observation_time_exact": False,
        "evidence_urls": ["https://www.bullionvault.com/gold-price-chart.do",
                          "https://www.bullionvault.com/chart/bullionvaultchart.js"],
    })
    report["metrics"][0].update(provider="BullionVault", source_url="https://chart-data.bullionvault.com/price_feed/")
    page = render_html(report)
    row = market_row(page, "xauusd")
    assert "<h1>全球宏观市场复盘 · 最近完整日线</h1>" in page
    assert "UTC 完整采样日末参考，非官方收盘价，非精确午夜成交" in row
    assert "USD/troy_oz" in row and "交易日时区：UTC" in row
    appendix = page.split("<h2>来源附录与边界</h2>")[1]
    for url in [report["metrics"][0]["source_url"], *report["snapshot"]["series"][0]["session_evidence"]["evidence_urls"]]:
        assert f'href="{url}"' in appendix
    assert "行情与口径页面" in appendix


def test_utc_gold_disclaimer_is_visible_before_main_table_and_verification_is_sampling_only():
    report = report_fixture()
    report["snapshot"]["series"][0].update(timezone="UTC", session_verified=True,
        session_date_basis="bullionvault_utc_reference_day",
        session_evidence={"natural_day_reference": True, "market_session_verified": False})
    page = render_html(report)
    main_section = page.split("<h2>跨资产行情</h2>")[1].split("</section>")[0]
    before_table = main_section.split('<div class="table-scroll"')[0]
    assert '<p class="market-reference-note">国际现货黄金' in before_table
    assert "UTC 完整采样日末参考价" in before_table
    assert "非全球官方收盘价，非精确午夜成交价" in before_table
    assert "可能沿用休市前报价" in before_table
    assert "<details" not in before_table
    row = market_row(page, "xauusd")
    assert "采样日完整性核验：已核验" in row
    assert "市场时段核验：已核验" not in row
    assert "不代表交易所交易时段或精确成交时间已核验" in row


def test_old_report_without_optional_metadata_or_quotes_still_renders():
    report = report_fixture()
    report["metrics"] = [report["metrics"][-1]]
    report["metrics"][0].pop("price_unit")
    report["snapshot"] = {"collected_at": "2026-09-22T09:00:00Z", "source_statuses": []}
    report["analysis"]["sections"][0].pop("evidence_quotes")
    page = render_html(report)
    assert "<h1>金融观察报告 · 2026-09-21 至 2026-09-22</h1>" in page
    assert "请求区间：2026-09-21 至 2026-09-22" in page
    assert "3,030.00" in page and "<td>点<small>" in page
    assert "+1.00%" in page
    assert "交易日时区：未记录" in page
    assert "市场时段核验：已核验" not in page
    assert "资料发布日期筛选窗口" not in page


def test_series_units_used_when_metric_omits_optional_unit():
    report = report_fixture()
    report["metrics"][0].pop("price_unit")
    row = market_row(render_html(report), "xauusd")
    assert "<td>USD/troy_oz<small>" in row
    assert "价格指数" not in row


def test_missing_baseline_and_unavailable_change_not_zero_or_full_period_return():
    report = report_fixture()
    metric = report["metrics"][0]
    metric.update(baseline_available=False, change_pct=None, start_close=None, points=[])
    page = render_html(report)
    row = market_row(page, "xauusd")
    assert "首个观测价格（基准缺失）" in row and "未提供" in row
    assert "暂无区间涨跌" in row and "+0.00%" not in row
    assert "不能当作完整报告期收益" in page
    assert "<svg" not in row


def test_empty_evidence_is_not_presented_as_completed_macro_analysis():
    report = report_fixture()
    report.update(analysis=None, metrics=[], documents=[], data_status="error")
    report["snapshot"]["source_statuses"] = [{"source_id": "fixture-only", "status": "error", "count": 0, "detail": "offline"}]
    page = render_html(report)
    assert "仅数据状态 / 分析未完成" in page
    assert "报告期内没有可用行情" in page and "未发布模型分析" in page
    assert "本期未取得可用宏观或机构原文" in page
    assert "获取失败" in page and "offline" in page
    assert "报告已生成" not in page and "<svg" not in page


def test_partial_sources_draft_and_unknown_holdings_stay_explicit():
    report = report_fixture()
    report["profile_snapshot"].update(holdings_history=[{"symbol": "PRIVATE_HISTORY", "quantity": 987654321}])
    page = render_html(report)
    assert "来源为部分样本" in page and "限量或部分样本" in page
    assert "未覆盖全部宏观来源" in page and "待确认草稿" in page
    assert "未推断个人持仓" in page
    assert "PRIVATE_HISTORY" not in page and "987654321" not in page


def test_optional_metadata_and_original_quotes_are_escaped_and_safe_links_only():
    report = report_fixture()
    report["snapshot"]["series"][0].update(calendar_id='<script>alert(1)</script>',
        session_evidence={"unsafe": '<img src=x onerror="alert(1)">'})
    report["metrics"][0].update(source_url="javascript:alert(1)", provider="<b>source</b>", name='Gold" onload="alert(1)')
    report["analysis"]["sections"][0]["evidence_quotes"][0]["quote"] = "<img src=x>"
    report["analysis"]["sections"][0]["content"] = "<script>alert(1)</script>"
    page = render_html(report)
    assert "<script>" not in page and "<img" not in page and 'href="javascript:' not in page
    assert "&lt;script&gt;" in page and "&lt;img" in page and "&quot; onload=&quot;" in page


def test_render_is_read_only_and_leaves_analysis_shape_and_values_unchanged():
    report = report_fixture()
    original = deepcopy(report)
    render_html(report)
    assert report == original
    assert json.dumps(report, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)


def test_null_optional_fields_do_not_claim_verified_sessions():
    report = report_fixture()
    report["snapshot"]["series"][0].update(calendar_id=None, session_date_basis=None, session_verified=None,
        session_evidence={}, daily_cutoff_basis=None, session_date=None, session_close_at=None,
        expected_session_date=None, expected_previous_session_date=None)
    row = market_row(render_html(report), "xauusd")
    assert "市场时段核验" not in row and "市场日历：" not in row and "None" not in row
    assert "2,010.00" in row and "USD/troy_oz" in row


def test_chart_has_stable_viewbox_and_ignores_nonfinite_observations():
    report = report_fixture()
    report["metrics"][0]["points"] += [{"close": float("inf")}, {"close": float("nan")}, {"close": True}]
    row = market_row(render_html(report), "xauusd")
    assert 'viewBox="0 0 520 124"' in row
    assert 'points="12.0,105.0 508.0,17.0"' in row


@pytest.mark.parametrize("path", [
    r"D:\AAAcode\private\finance.sqlite", "D:/AAAcode/private/finance.sqlite",
    r"C:\Users\Alice Smith\Documents\private.json", r"\\server\private\finance.sqlite",
    "//server/private/finance.sqlite", "file:///C:/Users/private/finance.sqlite",
    "/home/private/finance.sqlite", "/Users/Alice Smith/Documents/private.json",
])
def test_absolute_local_paths_are_hidden_in_all_html_display_surfaces_without_mutating_snapshot(path):
    report = report_fixture()
    report["profile_snapshot"].update(display_name=path, purpose=path, focus_topics=[path])
    report["analysis"].update(overview=path, watch_next=[path], limitations=[path])
    report["analysis"]["sections"][0].update(title=path, content=f"Failed to load {path}")
    report["analysis"]["sections"][0]["evidence_quotes"][0]["quote"] = path
    report["metrics"][0].update(basis=path, source_url=path, provider=path)
    report["snapshot"]["series"][0]["session_evidence"] = {"cache_file": path, "evidence_urls": [path]}
    report["snapshot"]["source_statuses"][0]["detail"] = json.dumps({"issues": [f"Failed to open {path}"]})
    report["warnings"] = [path]
    report["documents"][0].update(text=path, title=path, url=path)
    original = deepcopy(report)
    page = render_html(report)
    assert path not in page and path not in Text(page).value
    assert "private/finance.sqlite" not in page and "private\\finance.sqlite" not in page
    assert "[本地路径已隐藏]" in page
    assert report == original


def test_http_source_paths_and_units_are_not_mistaken_for_local_files():
    report = report_fixture()
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm?period=2026%2F09%2F16&lang=en"
    report["documents"][0].update(url=url, text=f"Original citation: {url}")
    page = render_html(report)
    assert url in Text(page).value
    links = [attrs["href"] for tag, attrs in MarkupAudit(page).elements if tag == "a"]
    assert url in links
    assert "USD/troy_oz" in page and "(end / baseline - 1) * 100" in page
    assert "[本地路径已隐藏]" not in page


@pytest.mark.parametrize("url", [
    "https://example.com/download?path=D%3A%5CUsers%5Cprivate%5Cfinance.json",
    "https://example.com/download?path=%2Fhome%2Fprivate%2Ffinance.json",
    "https://example.com/C:/Users/private/finance.json",
])
def test_encoded_local_path_in_source_url_is_not_exposed_as_text_or_link(url):
    report = report_fixture()
    report["snapshot"]["series"][0]["session_evidence"] = {"evidence_urls": [url]}
    page = render_html(report)
    assert url not in page and "finance.json" not in page
    assert "[本地路径已隐藏]" in page


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///C:/private/test.html",
    "//example.com/test", "https://[invalid", "https://user:password@example.com/",
    "https://:password@example.com/", "https://example.com/\nunsafe", "https://example.com/\\unsafe",
])
def test_invalid_or_executable_source_urls_fail_closed_without_breaking_report(url):
    report = report_fixture()
    report["metrics"][0]["source_url"] = url
    row = market_row(render_html(report), "xauusd")
    assert not any(tag == "a" for tag, _ in MarkupAudit(row).elements)
    assert "fixture-only" in row


def test_untrusted_text_including_numeric_slots_cannot_create_executable_html():
    report = report_fixture()
    attack = '\"><img src=x onerror="alert(1)"><script>alert(1)</script><iframe srcdoc="bad"></iframe>'
    report["profile_snapshot"].update(version=attack, display_name=attack, purpose=attack)
    report["snapshot"]["source_statuses"][0].update(count=attack, detail=json.dumps({"issues": [attack]}))
    report["analysis"]["sections"][0].update(title=attack, content=attack, evidence_refs=[attack])
    report["analysis"]["sections"][0]["evidence_quotes"][0].update(quote=attack, evidence_ref=attack)
    report["metrics"][0].update(metric_id=attack, provider=attack, name=attack)
    report["documents"][0].update(title=attack, document_id=attack, publisher=attack, text=attack)
    report["wiki_fact_refs"] = [attack]
    page = render_html(report)
    elements = MarkupAudit(page).elements
    assert not any(tag in {"script", "img", "iframe", "object", "embed", "base"} for tag, _ in elements)
    assert not any(key.lower().startswith("on") or key == "srcdoc" for _, attrs in elements for key in attrs)
    assert "&lt;script&gt;" in page and "&lt;img" in page
    policies = [attrs["content"] for tag, attrs in elements if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"]
    assert policies and "default-src 'none'" in policies[0] and "base-uri 'none'" in policies[0]
