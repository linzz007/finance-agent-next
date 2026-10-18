"""Publish a readable report and its exact, workspace-scoped evidence snapshot."""

from __future__ import annotations

from html import escape
import json
import math
import os
import re
import tempfile
from urllib.parse import parse_qsl, unquote, urlparse

from policy_impact.company_wiki.loader import company_dir
from policy_impact.domains.finance.store import FinanceStore, FinanceConflictError
from policy_impact.domains.finance.document_period import document_period_hint


_WEB_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_LOCAL_PATH = re.compile(
    r"(?:file:/+|(?<!\w)[A-Za-z]:[\\/]|\\\\|(?<![\w:/])//)[^\r\n<>\"'|，；。！？]+"
    r"|(?<![\w:/<])/(?:[^\s/<>\"'|，；。！？]+/)+[^\r\n<>\"'|，；。！？]*",
    re.IGNORECASE,
)


def _redact_local_paths(text: str) -> str:
    # Public HTTP paths are citations, not filesystem paths; inspect query values separately.
    parts, offset = [], 0
    for match in _WEB_URL.finditer(text):
        parts.append(_LOCAL_PATH.sub("[本地路径已隐藏]", text[offset:match.start()]))
        url = match.group()
        try:
            parsed = urlparse(url)
            local_query = any(_LOCAL_PATH.search(unquote(value)) for _, value in parse_qsl(parsed.query))
            local_drive = re.search(r"[A-Za-z]:[\\/]", unquote(parsed.path))
            parts.append("[本地路径已隐藏]" if local_query or local_drive else url)
        except ValueError:
            parts.append("[无效来源链接]")
        offset = match.end()
    parts.append(_LOCAL_PATH.sub("[本地路径已隐藏]", text[offset:]))
    return "".join(parts)


def _display_projection(value):
    if isinstance(value, dict):
        return {_redact_local_paths(key) if isinstance(key, str) else key: _display_projection(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_display_projection(item) for item in value]
    return _redact_local_paths(value) if isinstance(value, str) else value


def _link(url: str, title: str) -> str:
    try:
        parsed = urlparse(str(url))
        safe = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None
                and parsed.password is None and not re.search(r"[\s\x00-\x1f\x7f\\]", str(url)))
    except ValueError:
        safe = False
    if not safe:
        return escape(title)
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>'


def _price(value) -> str:
    return f"{value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else "未提供"


def _render_chat_facts_only(report: dict) -> str:
    period = report["request"]["period"]
    lines = [
        "## 行情事实（原因研判暂缓）",
        "子智能体发现原因分析存在未解决的证据或表述冲突，因此本轮不展示该分析。"
        "以下仅保留已计算的行情数据；这不是独立价格交叉核验或投资建议。",
        f"请求窗口：{period['start']} 至 {period['end']}；各资产以实际可用的基准日和期末日为准。",
        "",
        "|资产|基准日期与价格|期末日期与价格|区间涨跌|",
        "|---|---|---|---|",
    ]
    for metric in report["metrics"]:
        change = metric.get("change_pct")
        change_text = f"{change:+.2f}%" if change is not None else "无法计算"
        unit = metric.get("price_unit") or "点"
        lines.append(
            f"|{metric['name']}|{metric['start_date']} · {_price(metric['start_close'])} {unit}|"
            f"{metric['end_date']} · {_price(metric['end_close'])} {unit}|{change_text}|"
        )
    lines.extend(["", "涨跌按（期末价 / 基准价 − 1）×100 计算，不是持仓盈亏；"
                  "金银为 UTC 采样参考日末价，不是官方收盘或可执行报价。", "", "### 行情来源"])
    seen = set()
    for metric in report["metrics"]:
        url = str(metric.get("source_url") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or re.search(r"[\s<>\\]", url) or url in seen:
            continue
        seen.add(url)
        label = re.sub(r"[\[\]<>\r\n]", " ", str(metric["name"]))
        lines.append(f"- [{label}](<{url}>)")
    lines.append("原因分析未通过子智能体复核；具体争议和证据缺口见本轮运行过程。")
    return "\n".join(lines)


def render_chat_analysis(report: dict, *, facts_only: bool = False) -> str:
    """Project verified finance facts once; never let a second LLM rewrite them."""
    if (report.get("data_status") != "done" or report.get("analysis_status") != "done"
            or not (report.get("analysis_verification") or {}).get("supported")
            or (report.get("analysis_verification") or {}).get("issues")):
        raise ValueError("only a validated financial analysis can be projected")
    report = _display_projection(report)
    if facts_only:
        return _render_chat_facts_only(report)
    analysis = report["analysis"]
    period = report["request"]["period"]
    lines = ["## 行情事实", f"请求窗口：{period['start']} 至 {period['end']}。以下使用各资产实际可用的收益基准与期末，非实时行情。",
             "|资产|收益基准日期与价格|期末日期与价格|区间涨跌|",
             "|---|---|---|---|"]
    for metric in report["metrics"]:
        change = metric.get("change_pct")
        value = f"{change:+.2f}%" if change is not None else "无法计算"
        unit = metric.get("price_unit") or "点"
        lines.append(f"|{metric['name']}|{metric['start_date']} · {_price(metric['start_close'])} {unit}|"
                     f"{metric['end_date']} · {_price(metric['end_close'])} {unit}|{value}|")
    lines += ["", "收益按（期末价 / 基准价 − 1）×100 计算，期前基准不等于窗口首日；不是持仓盈亏。",
              "纳指等指数为第三方供应商日线；金银为 USD/金衡盎司的 UTC 采样参考日末价，不是官方收盘或可执行报价。", "", "### 窗口内价格路径"]
    for metric in report["metrics"]:
        lines.append(f"- **{metric['name']}**：窗口内收盘/参考日末价最高 {_price(metric['period_peak_close'])}（{metric['period_peak_date']}），"
                     f"最低 {_price(metric['period_trough_close'])}（{metric['period_trough_date']}）；"
                     f"窗口内收盘/参考日末价最大回撤 {metric['max_drawdown_pct']:.2f}%；"
                     f"上涨 {metric['up_days']}、下跌 {metric['down_days']}、不变 {metric['unchanged_days']} 个观测日。")
    lines += ["以上极值与回撤只含窗口内收盘/参考日末价，不含期前基准，也不是盘中极值。", "", "## 分析与证据边界", analysis["overview"]]
    for section in analysis["sections"]:
        lines += ["", "### " + section["title"], section["content"]]
    names = {m["symbol"]: m["name"] for m in report["metrics"]}
    for row in analysis.get("asset_assessments", []):
        label = "原因未识别" if row["verdict"] == "unidentified" else "候选解释，未证实"
        lines += ["", f"### {names[row['symbol']]}：{label}", row["hypothesis"],
                  "- 反向观察：" + row["counterevidence"],
                  "- 尚缺证据：" + "；".join(row["missing_evidence"]),
                  "- 待执行的检验：" + row["falsification_test"]]
    lines += ["", "## 下一步核查", *["- " + item for item in analysis.get("watch_next", [])],
              "", "原文保存、行情计算与因果识别是不同的验证层；候选解释不是已查明的涨跌主因。这是聊天分析，尚未发布 HTML 报告。"]
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def _chart(metric: dict, price_unit: str = "") -> str:
    points = metric.get("points") or []
    values = [p["close"] for p in points if isinstance(p, dict) and isinstance(p.get("close"), (int, float))
              and not isinstance(p["close"], bool) and math.isfinite(p["close"])]
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    coords = " ".join(f"{12 + i * 496 / (len(values)-1):.1f},{105 - (v-lo)*88/span:.1f}" for i, v in enumerate(values))
    label = escape(f'{metric["name"]}已观测日线价格（{price_unit or "单位未记录"}）')
    return f'<svg viewBox="0 0 520 124" role="img" aria-label="{label}"><title>{label}</title><polyline fill="none" stroke="#187b70" stroke-width="2.5" points="{coords}"/></svg>'


def _source_status(status: dict) -> str:
    labels = {"ok": "已获取", "partial": "限量或部分样本", "empty": "本期无可用记录", "error": "获取失败", "disabled": "未启用"}
    detail = status.get("detail", "")
    try:
        parsed = json.loads(detail)
        brief = f"候选 {parsed.get('discovered_count', '-')}，已保留 {status['count']} 条；问题 {len(parsed.get('issues', []))} 项。"
    except (ValueError, TypeError):
        brief = detail[:200]
    return f'<tr><td>{escape(status["source_id"])}</td><td>{labels.get(status["status"], escape(status["status"]))}</td><td>{escape(str(status["count"]))}</td><td>{escape(brief)}<details><summary>技术详情</summary><p>{escape(detail)}</p></details></td></tr>'


def _research_section(report: dict) -> str:
    audit = report.get("research_audit")
    if not audit:
        return '<section><h2>归因核查</h2><p>旧报告未保存逐资产归因核查，不能据此判定原因已验证。</p></section>'
    quality = report.get("research_quality") or {}
    documents = {d["document_id"]: d for d in report.get("documents", [])}
    assessments = {a["symbol"]: a for a in (report.get("analysis") or {}).get("asset_assessments", [])}
    items = []
    for asset in audit["assets"]:
        row = assessments.get(asset["symbol"], {})
        verdict = {"hypothesis_only": "候选解释，未证实", "unidentified": "原因未识别"}.get(row.get("verdict"), "仅行情观察")
        detail = ''.join(f'<p><strong>{label}：</strong>{escape(str(row.get(key, "未记录")))}</p>' for key, label in (
            ("hypothesis", "可能机制"), ("counterevidence", "反向观察 / 替代解释"), ("falsification_test", "如何检验"))) if row else ''
        if row:
            detail += '<p><strong>尚缺证据：</strong>' + escape('；'.join(row.get('missing_evidence', []))) + '</p>'
            test = row.get("historical_test")
            if test:
                detail += '<p><strong>历史检验计划（尚未完成）：</strong>' + escape(
                    f'{test["window"]["start"]} 至 {test["window"]["end"]}；变量：' + '、'.join(test['variables']) + '；' + test['weakening_condition']) + '</p>'
            refs = []
            for ref in row.get("evidence_refs", []):
                if ref == asset["metric_ref"]:
                    refs.append(_link(asset["source_url"], asset["name"] + "行情"))
                elif ref in documents:
                    refs.append(_link(documents[ref]["url"], documents[ref]["title"]))
            detail += '<p class="refs">本项依据：' + ' · '.join(refs) + '</p>'
        late = [d for d in asset['timeline'] if d['relation'] == 'after_price_cutoff']
        same = [d for d in asset['timeline'] if d['relation'] == 'same_day_order_unknown']
        dates = ''.join(f'<li>{_link(d["url"], d["document_id"])} · {escape(d["published_at"])} · 可用于事后回顾，但不能仅据此认证当时已知的催化因素</li>' for d in late)
        dates += ''.join(f'<li>{_link(d["url"], d["document_id"])} · {escape(d["published_at"])} · 同日发布，时分与收盘先后未经核验</li>' for d in same)
        items.append(f'<article><h3>{escape(asset["name"])} · {verdict}</h3><p class="muted">价格截止 {escape(asset["price_cutoff"])}；单源价格，未独立交叉核验。</p>{detail}'
                     + (f'<details><summary>不能用作已确认催化因素的资料</summary><ul>{dates}</ul></details>' if dates else '') + '</article>')
    limitations = ''.join(f'<li>{escape(item)}</li>' for item in audit['limitations'])
    return f'<section><h2>归因核查</h2><p><strong>{escape(quality.get("label", "未核查"))}</strong></p><p>{escape(quality.get("reason", ""))}</p>{"".join(items)}<details><summary>核查能力边界</summary><ul>{limitations}</ul></details></section>'


def render_html(report: dict) -> str:
    report = _display_projection(report)
    p = report["request"]["period"]
    profile = report["profile_snapshot"]
    analysis = report.get("analysis") or {}
    cited_ids = {ref for section in analysis.get("sections", []) for ref in section["evidence_refs"]}
    wiki_snapshots = {f["fact_id"]: f for f in report.get("wiki_fact_snapshots", []) if f.get("fact_id") in cited_ids}
    reference_ids = ({d["document_id"] for d in report["documents"]}
                     | {m["metric_id"] for m in report["metrics"]} | set(wiki_snapshots))
    sections = []
    for section in analysis.get("sections", []):
        refs = " ".join(f'<a href="#{escape(ref, quote=True)}">[{escape(ref)}]</a>' if ref in reference_ids
                        else f'<span>[{escape(ref)}：旧报告未保存背景原文]</span>' for ref in section["evidence_refs"])
        quotes = "".join(f'<blockquote>{escape(q["quote"])} <a href="#{escape(q["evidence_ref"], quote=True)}">原文</a></blockquote>' for q in section.get("evidence_quotes", []))
        sections.append(f'<article><h3>{escape(section["title"])}</h3><p>{escape(section["content"])}</p><p class="refs">{refs}</p>{quotes}</article>')
    markets = []
    market_sources = []
    reference_symbols = []
    saved_series = {s["symbol"]: s for s in report["snapshot"].get("series", [])}
    for m in report["metrics"]:
        symbol = m.get("symbol", "")
        series = saved_series.get(symbol, {})
        spot = symbol in {"xauusd", "xagusd"}
        legacy_index = symbol in {"sh000001", "sh000300", "sz399006", "sz399001", "sh000016", "sh000688", "spx", "ixic", "dji"}
        change = (f"{m['change_pct']:+.4f}%" if 0 < abs(m['change_pct']) < 0.005 else f"{m['change_pct']:+.2f}%") if m['change_pct'] is not None else "暂无区间涨跌"
        price_unit = m.get("price_unit") or series.get("unit") or ("USD/troy_oz" if spot else "点" if legacy_index else "单位未记录")
        price_basis = m.get("price_basis") or series.get("price_basis") or ("spot_price" if spot else "price_index" if legacy_index else "")
        basis_label = {"price_index": "价格指数收盘，不含分红再投资", "spot_price": "国际现货参考价（美元/金衡盎司），非交易所期货；无统一全球收盘"}.get(price_basis, price_basis or "报价口径未记录")
        timezone = m.get("timezone") or series.get("timezone") or "未记录"
        session = {**series, **{key: value for key, value in m.items() if value is not None}}
        session_evidence = session.get("session_evidence") or {}
        utc_reference = spot and session_evidence.get("natural_day_reference") is True and timezone == "UTC"
        if utc_reference:
            basis_label += "；UTC 完整采样日末参考，非官方收盘价，非精确午夜成交"
            reference_symbols.append(m["name"])
        session_notes = "".join(f'<p>{label}：{escape(str(session[key]))}</p>' for key, label in (
            ("asset_class", "资产类别"), ("quote_currency", "报价币种"), ("calendar_id", "市场日历"),
            ("session_date", "市场时段日期"), ("expected_session_date", "预期完整时段"),
            ("expected_previous_session_date", "预期前一时段"), ("session_close_at", "时段截止时间"),
            ("session_date_basis", "时段日期依据"), ("daily_cutoff_basis", "日线截点依据"),
        ) if session.get(key))
        if session.get("session_verified") is not None:
            verification_label = "采样日完整性核验" if utc_reference else "市场时段核验"
            session_notes += f'<p>{verification_label}：' + ("已核验" if session["session_verified"] is True else "未核验") + '</p>'
        if utc_reference:
            session_notes += '<p>采样日完整不代表交易所交易时段或精确成交时间已核验。</p>'
        if session.get("session_evidence"):
            session_notes += '<details><summary>时段核验证据</summary><p>' + escape(json.dumps(session["session_evidence"], ensure_ascii=False)) + '</p></details>'
        evidence_urls = session_evidence.get("evidence_urls", [])
        evidence_links = " · ".join(_link(url, url) for url in evidence_urls if isinstance(url, str)) if isinstance(evidence_urls, list) else ""
        market_sources.append(f'<li><strong>{escape(m["name"])}</strong> · 数据 feed：{_link(m["source_url"], m["provider"])}'
                              + (f'<p>行情与口径页面：{evidence_links}</p>' if evidence_links else "") + '</li>')
        baseline_label = "期前基准" if m.get("baseline_available") else "首个观测价格（基准缺失）"
        markets.append(f'''<tr id="{escape(m['metric_id'], quote=True)}"><th scope="row">{escape(m['name'])}<small>{escape(symbol)}</small></th>
<td>{escape(price_unit)}<small>{escape(basis_label)}</small></td>
<td class="number">{_price(m['start_close'])}<small>{escape(m['start_date'])}<br>{baseline_label}</small></td>
<td class="number">{_price(m['end_close'])}<small>{escape(m['end_date'])}<br>交易日时区：{escape(timezone)}</small></td>
<td class="number">{change}</td><td>{_link(m['source_url'], m['provider'])}
<details><summary>口径与日线</summary><p>{escape(m['basis'])}</p>{session_notes}{_chart(m, price_unit)}</details></td></tr>''')
    documents = []
    for d in report["documents"]:
        kind = {"macro_release": "宏观发布", "institution_view": "机构观点", "policy_release": "政策原文"}.get(d["kind"], d["kind"])
        hint = document_period_hint(d)
        period_note = f'<p class="muted">{escape(hint["hint"])}{escape(hint["scope_note"])}</p>' if hint["status"] == "known" else ""
        documents.append(f'<article id="{escape(d["document_id"])}"><h3>{_link(d["url"], d["title"])}</h3><p class="muted">{escape(kind)} · {escape(d["publisher"])} · 发布于 {escape(d["published_at"])}</p>{period_note}<details><summary>查看原文摘录与获取时间</summary><p>{escape(d["text"][:2000])}</p><p class="muted">{escape(d["retrieved_at"])} · {escape(d["document_id"])}</p></details></article>')
    statuses = "".join(_source_status(s) for s in report["snapshot"]["source_statuses"])
    warnings = "".join(f'<li>{escape(w)}</li>' for w in report["warnings"])
    watch = "".join(f'<li>{escape(w)}</li>' for w in analysis.get("watch_next", []))
    limits = "".join(f'<li>{escape(w)}</li>' for w in analysis.get("limitations", []))
    label = "分析已生成 / 来源为部分样本" if report["data_status"] == "partial" and analysis else "报告已生成" if analysis else "仅数据状态 / 分析未完成"
    report_name = "全球宏观市场复盘" if "全球宏观" in profile.get("markets", []) else "金融观察报告"
    title = f"{report_name} · 最近完整日线" if report["snapshot"].get("session_mode") == "latest_completed" else f"{report_name} · {p['start']} 至 {p['end']}"
    session_scope = "最近完整日线，按各自市场时段分别观察" if report["snapshot"].get("session_mode") == "latest_completed" else f"请求区间：{p['start']} 至 {p['end']}"
    as_of = f'；观察时点：{escape(str(report["snapshot"]["as_of"]))}' if report["snapshot"].get("as_of") else ""
    document_period = report["snapshot"].get("document_period")
    document_scope = f'<p class="muted">资料发布日期筛选窗口：{escape(document_period["start"])} 至 {escape(document_period["end"])}；不是资料中各指标的统计期。</p>' if document_period else ""
    reference_note = ('<p class="market-reference-note">' + '、'.join(escape(name) for name in reference_symbols)
                      + '：USD/troy_oz，UTC 完整采样日末参考价；非全球官方收盘价，非精确午夜成交价。采样完整不代表新的交易日成交，供应方可能沿用休市前报价。</p>') if reference_symbols else ""
    wiki_appendix = "".join(f'<article id="{escape(ref, quote=True)}"><h3>{escape(ref)}</h3>'
                           f'<p>{escape(json.dumps(fact.get("value"), ensure_ascii=False))}</p>'
                           f'<p class="muted">Wiki 背景快照 · 非市场事实 · {escape(str(fact.get("source_path") or "来源未记录"))}</p></article>'
                           for ref, fact in wiki_snapshots.items())
    focus_details = '<details><summary>本报告采用的关注主题</summary><p>' + '、'.join(escape(x) for x in profile['focus_topics']) + '</p></details>'
    research_section = _research_section(report)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f5f7f8;color:#202a31;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1180px;margin:auto;padding:40px 28px}}header{{border-bottom:2px solid #187b70;padding-bottom:24px}}
h1{{font-size:29px;line-height:1.4;margin:12px 0;overflow-wrap:anywhere}}h2{{font-size:22px;margin:0 0 20px}}h3{{font-size:18px;margin:0 0 8px;overflow-wrap:anywhere}}
p{{margin:8px 0;overflow-wrap:anywhere}}section{{padding:28px 0;border-bottom:1px solid #d8e0e4}}article{{margin:18px 0}}
a{{color:#126c91;overflow-wrap:anywhere}}.muted,.refs,small{{font-size:13px;color:#626d75}}.tag{{color:#187b70;font-weight:650}}
svg{{width:100%;min-width:0;display:block;aspect-ratio:520/124}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #d8e0e4;text-align:left;padding:10px;vertical-align:top;overflow-wrap:anywhere}}
caption{{text-align:left;color:#626d75;padding-bottom:12px}}.market-table{{min-width:920px;table-layout:fixed}}
.market-table th[scope="row"]{{font-size:14px}}.market-table small{{display:block;font-weight:400;font-size:12px}}
.number{{font-variant-numeric:tabular-nums}}.table-scroll{{max-width:100%;overflow:auto}}summary{{cursor:pointer;color:#126c91}}
blockquote{{margin:12px 0;padding-left:16px;border-left:3px solid #d8e0e4;overflow-wrap:anywhere}}li{{margin:8px 0}}
footer{{padding:22px 0;font-size:12px;color:#626d75}}
@media(max-width:600px){{main{{padding:24px 16px}}h1{{font-size:24px}}table{{min-width:560px}}.market-table{{min-width:920px}}}}
</style></head><body><main>
<header><span class="tag">FINANCIAL OBSERVER · {label}</span><h1>{escape(title)}</h1><p>{escape(profile['display_name'])} · 档案 v{escape(str(profile['version']))}（{'待确认草稿' if profile['status'] == 'draft' else '已确认'}）</p><p>{escape(profile['purpose'])}</p>{focus_details}<p class="muted">数据采集时间：{escape(report['snapshot']['collected_at'])}；仅已保存日线，不是实时行情。各标的交易日、单位和基准分别列示，宏观统计期以原文为准。</p>{document_scope}</header>
<section><h2>摘要</h2><p>{escape(analysis.get('overview') or '可用证据或模型输出未通过校验，不能形成可靠分析。')}</p></section>
<section><h2>跨资产行情</h2>{reference_note}<div class="table-scroll" role="region" aria-label="跨资产行情表" tabindex="0"><table class="market-table"><caption>{escape(session_scope)}{as_of}；各标的按实际观测日计算，不假定同步收盘。</caption><thead><tr><th scope="col">标的</th><th scope="col">单位 / 报价基准</th><th scope="col">基准观测 / 日期</th><th scope="col">期末观测 / 交易日</th><th scope="col">区间变化</th><th scope="col">来源 / 口径</th></tr></thead><tbody>{''.join(markets) or '<tr><td colspan="6">报告期内没有可用行情，未填充虚构价格。</td></tr>'}</tbody></table></div><p class="muted">区间变化 = (期末观测价格 / 基准观测价格 − 1) × 100%；基准缺失时只代表实际可计算区间，不能当作完整报告期收益。交易日连续性与时段依据以各标的记录为准；非收益承诺。</p></section>
<section><h2>宏观主线与证据</h2>{''.join(sections) or '<p>未发布模型分析；没有足够证据形成可验证的宏观主线。</p>'}</section>
{research_section}
<section><h2>后续观察</h2><ul>{watch or '<li>先补足有效数据再作判断。</li>'}</ul></section>
<section><h2>来源附录与边界</h2><ul>{warnings}{limits}</ul><div class="table-scroll"><table><thead><tr><th>来源</th><th>状态</th><th>记录数</th><th>说明</th></tr></thead><tbody>{statuses}</tbody></table></div><ul>{''.join(market_sources)}</ul>{''.join(documents) or '<p>本期未取得可用宏观或机构原文。</p>'}{wiki_appendix}</section>
<footer>报告编号：{escape(report['report_id'])}<br>快照：{escape(report['snapshot_id'])}<br>本报告用于信息观察和金融学习，不构成投资建议；机构观点不等同于事实，未推断个人持仓。</footer></main></body></html>'''


def publish_report(company_id: str, report: dict) -> dict:
    if report["company_id"] != company_id or not re.fullmatch(r"fin_[a-f0-9]{32}", report["report_id"]):
        raise ValueError("report identity is outside the current workspace")
    folder = company_dir(company_id) / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    prefix = folder / (report["request"]["period"]["end"] + "-" + report["report_id"])
    artifacts = {"html_report": str(prefix.with_suffix(".html")), "report": str(prefix.with_suffix(".md")),
                 "run_artifact": str(prefix.with_suffix(".json"))}
    frozen = json.loads(json.dumps({**report, "artifacts": artifacts}, ensure_ascii=False))
    store = FinanceStore(company_id)
    existing = store.get_report(report["report_id"])
    if existing is not None and existing != frozen:
        raise FinanceConflictError("published report IDs are immutable")
    analysis = frozen.get("analysis") or {}
    markdown = f"# 金融观察报告\n\n报告期：{report['request']['period']['start']} 至 {report['request']['period']['end']}\n\n" + analysis.get("overview", "分析未完成")
    for section in analysis.get("sections", []):
        markdown += f"\n\n## {section['title']}\n\n{section['content']}\n\n证据：" + ", ".join(section["evidence_refs"])
    markdown += "\n\n## 数据边界\n\n" + "\n".join("- " + w for w in report["warnings"])
    quality = report.get("research_quality") or {}
    if quality:
        markdown += "\n\n## 归因核查\n\n" + quality["label"] + "。" + quality["reason"]
    for item in analysis.get("asset_assessments", []):
        markdown += f"\n\n### {item['symbol']}\n\n候选解释：{item['hypothesis']}\n\n反向观察：{item['counterevidence']}\n\n检验条件：{item['falsification_test']}"
    contents = {"html_report": render_html(frozen), "report": markdown,
                "run_artifact": json.dumps(frozen, ensure_ascii=False, indent=2)}
    for key, content in contents.items():
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as temp:
            temporary = temp.name
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            os.replace(temporary, artifacts[key])
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    # Only register a published report once all user-facing files are durable.
    # A process crash before this commit may leave unregistered files, not a false success.
    store.save_report(frozen)
    return artifacts
