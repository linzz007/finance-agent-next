"""A paginated, read-only report tool scoped to this turn's authorized report IDs."""

import re
from policy_impact.domains.finance.store import FinanceStore
from policy_impact.domains.finance.metrics import compute_market_metrics, rank_market_metrics
from policy_impact.domains.finance.document_period import document_period_hint
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.tool_gateway import ToolGateway


def read(company_id: str, report_id: str, section: str = "sources", symbol: str = "",
         document_id: str = "", offset: int = 0, limit: int = 12) -> dict:
    if type(offset) is not int or not 0 <= offset <= 100_000 or type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("invalid report page bounds")
    report = FinanceStore(company_id).get_report(report_id)
    if not report or report["company_id"] != company_id:
        raise ValueError("current workspace has no such report")
    result = {"report_id": report_id, "scope": "saved_report_snapshot_not_live_market_data",
              "collected_at": report["snapshot"]["collected_at"], "period": report["request"]["period"]}
    all_metrics = compute_market_metrics(report["snapshot"])
    # Every evidence page carries the exact return endpoints, so a reader of
    # analysis/audit cannot mistake the requested start for the price baseline.
    result["return_basis"] = [{k: m.get(k) for k in
                              ("symbol", "name", "start_date", "start_close", "end_date", "end_close", "change_pct")}
                             for m in all_metrics]
    result["return_basis_notice"] = "start_date是收益实际计算基准，不是请求起日；窗口内首日不能代替期前基准。"
    metrics = all_metrics if section in {"market_series", "metrics"} else []
    if section == "source":
        document = next((d for d in report["documents"] if d["document_id"] == document_id), None)
        if not document:
            raise ValueError("source not in this report")
        end = offset + 2400
        return {**result, "source": {k: document[k] for k in ("title", "url", "document_id", "published_at", "kind")},
                "title_period_hint": document_period_hint(document),
                "text": document["text"][offset:end], "next_offset": end if end < len(document["text"]) else None,
                "full_text_length": len(document["text"])}
    if section == "sources":
        rows = [{**{k: d[k] for k in ("document_id", "title", "url", "published_at", "kind")},
                 "saved_body_characters": len(d.get("text", "")),
                 "title_period_hint": document_period_hint(d)} for d in report["documents"]]
    elif section == "market_series":
        metric = next((m for m in metrics if m["symbol"] == symbol), None)
        if not metric:
            raise ValueError("symbol not in this report")
        rows = [{**{k: v for k, v in point.items() if k != "change_pct"},
                 "cumulative_change_from_baseline_pct": point["change_pct"]}
                for point in metric["points"]]
        page_dates = {p["date"] for p in rows[offset:offset + limit]}
        result["daily_changes"] = [d for d in metric["daily_changes"] if d["date"] in page_dates]
        result["metric_definitions"] = {
            "items.cumulative_change_from_baseline_pct": "相对整个观察期期前基准的累计涨跌，不是单日涨跌",
            "daily_changes.change_pct": "相对前一个已知收盘的单日涨跌；单日数值只能引用此字段",
        }
        result.update(symbol=symbol, name=metric["name"], basis=metric["basis"], price_unit=metric.get("price_unit", "点"),
                      granularity="daily_close_only_no_intraday", intraday_available=False)
        result["computed_statistics"] = {k: v for k, v in metric.items() if k not in {"points", "daily_changes"}}
    elif section == "analysis":
        rows = (report.get("analysis") or {}).get("sections", [])
        result["overview"] = (report.get("analysis") or {}).get("overview", "")
        result["asset_assessments"] = (report.get("analysis") or {}).get("asset_assessments", [])
        result["research_quality"] = report.get("research_quality", {"label": "旧报告未保存归因核查"})
        result["provenance_notice"] = "这是已保存分析，不是历次聊天逐字记录。假设和因果未识别不能复述成已确认归因；缺少数据不是行情发生的原因。"
    elif section == "research_audit":
        audit = report.get("research_audit")
        rows = audit.get("assets", []) if audit else []
        result["research_quality"] = report.get("research_quality")
        result["limitations"] = audit.get("limitations", []) if audit else ["旧报告没有保存归因核查，不代表通过。"]
        result["missing_driver_observations"] = audit.get("missing_driver_observations", []) if audit else []
    elif section == "instruments":
        rows = [{k: v for k, v in series.items() if k != "bars"}
                for series in report["snapshot"]["series"] if not symbol or series["symbol"] == symbol]
        result["provenance_notice"] = "仅provider/source_instrument_identity/source_quality/asset_class/price_basis是适配器分类说明，不是官网逐字引文。session_evidence中的securityId=AUX/AGX、valuationSecurityId=USD、name=Galmarley_Mid来自实际feed元数据，不是适配器编造；xauusd/xagusd才是本系统代码。publication_delay_minutes=20是本系统完整性等待缓冲，不是供应商承诺的发布时刻或其实际延迟。"
        result["window_observations"] = [{"symbol": m["symbol"], "observed_session_count": m["observed_session_count"],
                                          "period_first_date": m["period_first_date"], "period_last_date": m["period_last_date"]}
                                         for m in all_metrics]
        result["count_notice"] = "研究窗口观测数使用window_observations；provider的采样元数据可能覆盖更长区间，不能把它的总数冒充窗口内观测数。"
    elif section == "profile_facts":
        rows = report.get("wiki_fact_snapshots", [])
        result["provenance_notice"] = "仅报告生成时引用的Wiki背景快照，不是市场原始数据或已确认当前持仓。"
    elif section == "metrics":
        rows = [{k: v for k, v in m.items() if k not in {"points", "daily_changes"}} for m in metrics if not symbol or m["symbol"] == symbol]
    else:
        raise ValueError("section must be sources, source, market_series, metrics, instruments, profile_facts, research_audit or analysis")
    if metrics:
        try:
            result["period_ranking"] = rank_market_metrics(metrics)
        except ValueError as exc:
            result["period_ranking"] = {"status": "unavailable", "reason": str(exc)}
        result["volume_scope"] = "原始快照可能保留成交量，但单位尚未统一校验，不用于量能或成交额结论。"
    return {**result, "items": rows[offset:offset + limit], "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None}


class ReportReadGateway(ToolGateway):
    def __init__(self, company_id: str, report_ids: set[str]):
        super().__init__(PermissionEngine([ToolPolicy("chat_agent_loop", "finance.report_read", "allow", "read scoped immutable finance evidence")]))
        self.company_id = company_id
        self.report_ids = report_ids
        self.read_sections: set[str] = set()
        self.observations: list[dict] = []

    def call(self, stage_name, tool_name, *args, **kwargs):
        if args or tool_name != "finance.report_read" or set(kwargs) - {"report_id", "section", "symbol", "document_id", "offset", "limit"}:
            raise PermissionError("only bounded report reads are available")
        if kwargs.get("report_id") not in self.report_ids:
            raise PermissionError("report was not authorized by this conversation's context")
        result = super().call(stage_name, tool_name, company_id=self.company_id, **kwargs)
        self.read_sections.add(kwargs.get("section", "sources"))
        self.observations.append(result)
        return result

    def final_guard(self, question: str, output: dict) -> str | None:
        required = set()
        if re.search("证据|缺什么|来源|原文|机构", question):
            required.add("sources")
        if re.search("逐日|每日|走势|路径|缺什么", question):
            required.add("market_series")
        if re.search("采样|截点|日界|品种|计价单位", question):
            required.add("instruments")
        if re.search("刚才|上一|报告", question) and re.search("解释|原因|反证|推翻", question):
            required.add("analysis")
        missing = required - self.read_sections
        if missing:
            return "金融证据完整性判断前，必须调用finance.report_read读取" + "、".join(sorted(missing)) + "；不能将上下文未展开等同报告未保存。日线使用已有symbol，来源先读目录。"
        answer = str(output.get("answer", ""))
        if "instruments" in self.read_sections and re.search("来源自述(?:为|是)|官网自述(?:为|是)", answer):
            return "品种observation中的分类由适配器生成，不能称为来源自述。请明确为适配器对公开feed的分类与实际元数据；20分钟是本系统采样完整性等待缓冲，不是供应商发布承诺。"
        if re.search(r"附来源|附上来源|来源链接|原文链接", question):
            def evidence_urls(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in {"url", "source_url"} and isinstance(child, str) and child.startswith(("https://", "http://")):
                            yield child
                        elif key == "evidence_urls" and isinstance(child, list):
                            yield from (url for url in child if isinstance(url, str) and url.startswith(("https://", "http://")))
                        elif isinstance(child, (dict, list)):
                            yield from evidence_urls(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from evidence_urls(child)
            urls = set(evidence_urls(self.observations))
            if urls and not any(url in answer for url in urls):
                return "用户要求可查询的来源。回答必须附上实际工具返回且支撑论点的原文URL（Markdown链接），报告ID或标题不能代替原文链接；不可编造链接。"
        if "market_series" in self.read_sections and re.search("没有.{0,8}逐日|缺少.{0,8}日线|只.{0,6}首尾", answer):
            return "已通过工具取得逐日数据，请依据实际observation描述，不能声称报告只有首尾或没有日线。"
        return None


TOOL_DESCRIPTION = {
    "name": "finance.report_read", "description": "按需展开本会话已保存金融报告的日线、原文或分析；只读旧快照，不联网。",
    "parameters": {"report_id": "授权报告ID，必填", "section": "sources|source|market_series|metrics|analysis|research_audit（归因时间边界、反向波动与缺口）|profile_facts（已引用Wiki快照）|instruments（品种、时区、原始标识与日界证据）",
                   "symbol": "market_series必填指数代码", "document_id": "source必填，先用sources查目录",
                   "offset": "分页起点默认0，source按字符其余按条", "limit": "条数1..30，默认12"},
}
