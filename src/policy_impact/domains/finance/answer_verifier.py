"""Bounded verification of financial follow-ups against immutable evidence."""

import json
import re

from policy_impact.domains.finance.metrics import compute_market_metrics, rank_market_metrics
from policy_impact.domains.finance.schemas import FinanceVerification
from policy_impact.domains.finance.verification import normalize_verification
from policy_impact.domains.finance.document_period import document_period_hint
from policy_impact.domains.finance.store import FinanceStore


def source_evidence(documents: list[dict], cited_ids: set[str]) -> list[dict]:
    """Give reviewers actual cited text, not just a catalogue of titles."""
    return [{**{k: d[k] for k in ("document_id", "title", "kind", "published_at", "url")},
             "title_period_hint": document_period_hint(d),
             "saved_body_characters": len(d.get("text", "")),
             "text_excerpt": d.get("text", "")[:6000] if d["document_id"] in cited_ids else "",
             "excerpt_truncated": len(d.get("text", "")) > 6000 if d["document_id"] in cited_ids else bool(d.get("text")),
             "notice": "正文已保存不等于已人工认证完整；原文可能包含时分，结构化时间尚待抽取核验。"}
            for d in documents]


def source_availability_issues(answer: str, reports: list[dict]) -> list[str]:
    if re.search(r"(?:报告|本次|本轮|系统)(?:只|仅)保存.{0,12}标题", answer):
        if any(d.get("saved_body_characters", 0) > 0 for r in reports for d in r.get("source_inventory", [])):
            return ["已保存来源正文，不能称报告仅保存标题/引用。未在当前observation展开不等于未保存；需要细节时用finance.report_read的source页，正文完整性另行判断。"]
    return []


def evidence_footer(reports: list[dict], answer: str) -> str:
    """Attach stored, clickable provenance without asking the model to construct URLs."""
    from urllib.parse import urlparse
    links, seen, labels = [], set(), []
    for report in reports:
        quality = report.get("research_quality") or {}
        if quality.get("answer_status") == "partial" and quality["label"] not in labels:
            labels.append(quality["label"])
        analysis = report.get("analysis") or {}
        cited = {ref for section in [*analysis.get("sections", []), *analysis.get("asset_assessments", [])] for ref in section["evidence_refs"]}
        sources = [(m["name"], m.get("source_url", "")) for m in report.get("metrics", [])]
        sources += [(d["title"], d.get("url", "")) for d in report.get("source_inventory", []) if d["document_id"] in cited]
        for title, url in sources:
            try:
                valid = urlparse(url).scheme in {"http", "https"} and urlparse(url).hostname and not re.search(r"[\s<>\\]", url)
            except ValueError:
                valid = False
            if not valid or url in seen or url in answer:
                continue
            seen.add(url)
            title = re.sub(r"[\[\]<>\r\n]", " ", title)
            links.append(f"- [{title}](<{url}>)")
    if labels:
        answer += "\n\n**研判状态：" + "；".join(labels) + "。** 行情计算可复算，单一数据源尚未独立交叉核价；语义复核不等于原因得到验证。"
    if links:
        answer += "\n\n### 本次分析的已保存来源\n" + "\n".join(links)
    return answer


def daily_value_issues(answer: str, reports: list[dict]) -> list[str]:
    """Reject explicit date + daily-return values absent from saved metrics.

    This is a narrow numeric guard, not a parser for arbitrary financial prose.
    Semantic review still checks the asset assignment and qualitative claims.
    """
    answer = answer.replace("**", "")
    claims = re.finditer(
        r"(?<!\d)(?:(?P<year>20\d{2})[-/])?(?P<month>\d{1,2})[-/月](?P<day>\d{1,2})日?"
        r"\s*(?P<daily>单日|当日)?\s*(?:涨跌幅|上涨|下跌|涨|跌)?\s*(?P<value>[+\-−]?\d+(?:\.\d+)?)\s*[%％]",
        answer)
    issues = []
    for claim in claims:
        sentence_start = max(answer.rfind(mark, 0, claim.start()) for mark in ("。", "；", "\n")) + 1
        scope = answer[sentence_start:claim.start()]
        if not claim["daily"] and (not re.search("日线|单日|当日|连续|日涨|日跌", scope) or re.search("基准|累计", scope)):
            continue
        expected = []
        for report in reports:
            for metric in report.get("metrics", []):
                for row in metric.get("daily_changes", []):
                    year, month, day = (int(part) for part in row["date"].split("-"))
                    if ((not claim["year"] or int(claim["year"]) == year)
                            and (int(claim["month"]), int(claim["day"])) == (month, day)
                            and row.get("change_pct") is not None):
                        expected.append((metric["name"], float(row["change_pct"])))
        if not expected:
            continue
        raw = claim["value"].replace("−", "-")
        value = float(raw)
        signed = raw.startswith(("+", "-"))
        precision = len(raw.split(".")[1]) if "." in raw else 0
        tolerance = 0.5 * 10 ** -precision + 1e-8
        if not any(abs(value - (number if signed else abs(number))) <= tolerance for _, number in expected):
            values = "、".join(f"{name} {number:+.6f}%" for name, number in expected)
            issues.append(f"{claim[0]}不符合该日已保存的单日指标（{values}）；不可把相对期前基准的累计涨跌当作单日涨跌。")
    return issues


def price_basis_issues(answer: str, reports: list[dict]) -> list[str]:
    """Fail closed on narrow, recurring ambiguities of close-only statistics."""
    from policy_impact.domains.finance.metric_claims import metric_claim_issues, research_claim_issues
    issues = metric_claim_issues(answer, reports) + research_claim_issues(answer, reports)
    if not any(report.get("metrics") for report in reports):
        return issues
    for line in re.split(r"[。；;\n]", answer):
        extrema_line = re.sub(r"(?:未取得|没有|缺少|未知|未保存)(?:任何)?(?:盘中)?(?:最高价?|最低价?|最大回撤)|(?:盘中)?(?:最高价?|最低价?|最大回撤)(?:未知|未取得|未保存)", "缺失项", line)
        pieces = extrema_line.split("|") if "|" in extrema_line else [extrema_line]
        if any(re.search("最高|最低|最大回撤", part) and (re.search(r"\d", part) or "|" in extrema_line)
               and not re.search("收盘|日末|参考价", part) for part in pieces):
            issues.append("最高/最低或最大回撤的表头及句子需明确仅窗口内收盘/参考日末价；最大回撤不含期前基准，不是盘中极值。")
        if "交易所完整日线" in line and not re.search("不是|不等于|并非", line):
            issues.append("当前价格源是数据供应商，不是交易所直供认证；请写供应商指数收盘日线，区分日历完整性与价格核验。")
    if "三者方向相反" in answer:
        issues.append("三个资产不能笼统称三者方向相反；按实际符号逐组表述，例如纳指上涨、金银同跌。")
    return list(dict.fromkeys(issues))


class FinanceAnswerVerifier:
    def __init__(self, gateway, complete, *, max_reviews=2, conversation_context=None):
        self.gateway = gateway
        self.complete = complete
        self.max_reviews = max_reviews
        self.reviews = 0
        self.results = []
        self.conversation_context = conversation_context or []

    def check(self, question: str, output: dict) -> str | None:
        prerequisite = self.gateway.final_guard(question, output)
        if prerequisite:
            return prerequisite
        if self.reviews >= self.max_reviews:
            return "金融事实复核预算耗尽，不能将未通过复核的回答发布为已验证结论。"
        try:
            reports = self._load_evidence()
        except Exception as exc:
            self.results.append({"supported": False, "error_type": type(exc).__name__, "stage": "evidence_load"})
            return "金融报告证据缺失或无效，不能进行事实复核：" + str(exc)[:200]
        fresh_report = (getattr(self.gateway, "analysis_result", None) or {}).get("report")
        if fresh_report:
            from policy_impact.domains.finance.render import render_chat_analysis
            try:
                output["answer"] = evidence_footer(reports, render_chat_analysis(fresh_report))
            except ValueError as exc:
                return str(exc)
            output["delivery_mode"] = "validated_finance_projection"
            self.results.append({"supported": True, "stage": "validated_analysis_projection",
                                 "report_id": fresh_report["report_id"],
                                 "reason": "沿用工作流已校验分析和代码计算指标，确定性生成答复，不执行第二次自由改写。"})
            return None
        numeric_issues = (daily_value_issues(str(output.get("answer", "")), reports)
                          + price_basis_issues(str(output.get("answer", "")), reports)
                          + source_availability_issues(str(output.get("answer", "")), reports))
        if (re.search("期后|晚于.{0,12}(?:行情|截止)", question)
                and re.search(r"(?:^|\n|[。？?])\s*(?:\*\*)?不能(?:\*\*)?\s*[。！!]", str(output.get("answer", "")))):
            numeric_issues.append("期后文献是否有解释价值不能直接回答‘不能’：可以事后回顾此前事件，但不能仅凭期后发布日期证明它在当时已知或构成催化。请区分研究用途和时间因果。")
        if numeric_issues:
            self.results.append({"supported": False, "issues": numeric_issues, "stage": "daily_numeric_check"})
            return "金融数值复核未通过：" + "；".join(numeric_issues)
        self.reviews += 1
        issue = self._review(question, output, reports)
        if issue is None:
            output["answer"] = evidence_footer(reports, str(output.get("answer", "")))
        return issue

    def _load_evidence(self):
        if not self.gateway.report_ids:
            raise ValueError("本轮没有授权的报告")
        reports = []
        for report_id in sorted(self.gateway.report_ids):
            report = FinanceStore(self.gateway.company_id).get_report(report_id)
            if not report or report.get("company_id") != self.gateway.company_id:
                raise ValueError("授权报告不存在或不属于当前工作空间")
            # Recompute exact statistics for old saved reports as well. Never fetch
            # fresh prices while validating an answer about an immutable report.
            metrics = compute_market_metrics(report["snapshot"])
            try:
                ranking = rank_market_metrics(metrics)
            except ValueError as exc:
                ranking = {"status": "unavailable", "reason": str(exc)}
            analysis = report.get("analysis") or {}
            cited_ids = {ref for row in [*analysis.get("sections", []), *analysis.get("asset_assessments", [])]
                         for ref in row.get("evidence_refs", [])}
            reports.append({
                "report_id": report_id, "period": report["request"]["period"],
                "metrics": [{k: v for k, v in m.items() if k not in {"points", "session_evidence"}} for m in metrics],
                "period_ranking": ranking,
                "source_inventory": source_evidence(report["documents"], cited_ids),
                "analysis": report.get("analysis"),
                "research_audit": report.get("research_audit"), "research_quality": report.get("research_quality"),
                "saved_data": {"daily_closes": any(s["bars"] for s in report["snapshot"]["series"]),
                    "raw_volume_present": any(b.get("volume") is not None for s in report["snapshot"]["series"] for b in s["bars"]),
                    "volume_unit_verified": False, "source_full_text_saved": any(d.get("text") for d in report["documents"])},
            })
        return reports

    def _review(self, question, output, reports):
        try:
            response = self.complete(messages=[
                {"role": "system", "content": (
                    "你是金融追问事实复核员。仅依据提供的不可变报告、确定性指标和实际工具observation核对answer。"
                    "重点核对排名、连续上涨/下跌、区间涨跌与最大回撤口径、缺失数据是否实际已保存。"
                    "优先逐资产绑定start_date/start_close/end_date/end_close/change_pct整组，不能从请求起始日期推测基准；观测数使用metrics.observed_session_count，不用供应商跨窗口原始条数。"
                    "窗口内最高点只能是period_peak_date/period_peak_close，不能把先前局部高点说成整个窗口高点；文中自称最终又创新高不豁免先前误述。"
                    "metrics.daily_changes提供完整观察期逐日收盘、方向和单日涨跌。单日涨跌必须核对该字段，不得用market_series.items中的cumulative_change_from_baseline_pct替代（旧记录名为change_pct）；逐日核查连续回落/上涨，中间任何反向日都会中断。"
                    "连续上涨要求相邻观测日每天都上涨；列出的日期中隔着下跌日，即使括号承认回落，仍不能称连续。"
                    "固定基准日和期末价格的区间收益与中途路径无关；中途反弹不可能改变期前起点或压缩首尾差，即使写可能也是数学错误，必须拦截。"
                    "最大回撤由区间内历史峰值到后续谷值计算，不等于相对期前基准最低跌幅。"
                    "period_peak_close/period_trough_close仅是窗口内收盘或参考日末价极值，不是盘中最高最低。凡展示极值必须明确收盘/日末价口径；最大回撤也仅窗口内日末价，不含期前基准。"
                    "research_audit.timeline中after_price_cutoff属于期后材料，不能用作此前走势的催化证据；同日无时分只能作背景。"
                    "期后文章可以回顾历史事件，但未核实首次公开时间前不能说当时市场已经知道；不能绝对化为期后资料不可能解释历史。"
                    "候选机制与检验必须对应：增长盈利假设不能只用实际利率验证；未来行情不能倒证此前历史原因。"
                    "若research_quality为partial，不能把原因分析称为已验证；实际反向波动需与单一叙事相对照，但不单独证明宏观机制错误。"
                    "如果回答明确说相对期初/期前基准一度下跌多少，应核对min_change_from_pre_period_baseline_pct，而不是max_drawdown_pct；两值不同恰恰是口径不同，不能将正确区分二者的回答误判成混淆。"
                    "单边下行/一路下跌与连续下跌一样需要逐日证据；中间有上涨只能说整体或震荡下行。"
                    "逐份检查saved_data.raw_volume_present：true表示原始volume已保存但单位未校验，只能说量能口径不可靠；false表示此快照确实没有原始volume，允许说未保存或缺失量能数据。不得把false误读成true。"
                    "本轮未请求某品种，只能说此快照未包含，不能据此判系统尚未接入该品种。"
                    "逐个指标检查统计期：单月工业增加值与年内累计投资/房地产可能同时发布，不能将整批宏观数据统称累计；这属于实质口径错误。title_period_hint仅指标题指标，正文可同时含其他期数。"
                    "市场因果解释必须是待验证假设。原文未展开不等于未保存。仅目录标题不能支持完整讲话解读。"
                    "source_inventory.text_excerpt是已引用原文，核查如投票一致性时要读它，不能只看analysis的引用句。saved_body_characters>0表示已保存正文，不能声称仅保存标题；excerpt_truncated为true或未展开不代表存储中没有正文。未人工认证完整也不等于已确认不完整。"
                    "published_at字段只保留日期，不等于原文没有时分；应说系统尚未结构化抽取核验。禁止将政策利率预测（联邦基金利率）说成实际收益率路径。"
                    "正向和否定因果都需证据：指数窄幅或不同向不能证明当天没有单一宏观冲击；可说无法识别或排除共同驱动。"
                    "逐句核查，不允许结尾的因果未识别免责声明豁免前文的确定性归因或排除。无成分/板块/资金流证据不得确认风格轮动已经发生。"
                    "instruments的provenance_notice明确适配器分类不是来源逐字自述。securityId AUX/AGX、valuationSecurityId USD、name Galmarley_Mid是原始feed元数据；source_quality/source_instrument_identity是适配器说明，不可反过来。20分钟是系统采样完整性缓冲，不是供应商发布延迟；即使另加免责声明也需纠正归属。"
                    "本快照金银采样分辨率以daily_cutoff_basis和instruments.session_evidence.interval_seconds为准，不能套用source_quality中罗列的其他可选分辨率。月度窗口可能使用两个12小时桶，不是必然六个4小时桶。"
                    "conversation_context只用于核对本会话之前说过什么，不是外部市场事实证据。检查‘上一轮认为/归因’是否真实对应已存analysis及对话；不能把因果未知、候选假设改述成已确认归因。缺失的历史不能编造。"
                    "缺少硬数据或只能取得讲话是信息覆盖限制，绝不能成为指数分化的原因。也不能把先前拒绝归因复述为只能归因于讲话；这属于sources/causality实质错误。"
                    "market_series只有逐日收盘，不能据此判断全天持续、尾盘形成或盘中事件时点；此类验证需要未接入的分钟/逐笔数据。多个变量同向且时点吻合只构成线索，不能据此断言可识别的共同驱动已出现。"
                    "只拦截实质错误，不因同义转述或少解释一个非必需来源判失败；不添加外部知识。"
                    "逐项核查numbers、sources、gaps、causality四项，每项必须且只出现一次。"
                    "没有相关主张或没有实质错误则supported；有直接冲突则contradicted，无依据的事实则unsupported，无法核实则uncertain。"
                    "每项先写不超过80字reason（具体主张与证据对照），再写verdict。"
                    "JSON: {\"type\":\"final\",\"output\":{\"checks\":[{\"item_id\":\"numbers\",\"reason\":\"数值与指标一致\",\"verdict\":\"supported\"},...]}}。"
                    "不要返回全局supported或issues；不要发现主张其实成立却依然判错。"
                )},
                {"role": "user", "content": json.dumps({"question": question, "answer": output.get("answer", ""),
                    "reports": reports, "tool_observations": self.gateway.observations,
                    "conversation_context": self.conversation_context}, ensure_ascii=False)},
            ], schema_name="finance_followup_verification.v1")
            review = normalize_verification(response.payload.get("output", response.payload), {"numbers", "sources", "gaps", "causality"})
            self.results.append(review)
            if review["supported"] and not review["issues"]:
                return None
            return "金融事实复核未通过，请修正以下具体错误，不引入新主张：" + "；".join(review["issues"])
        except Exception as exc:
            self.results.append({"supported": False, "error_type": type(exc).__name__})
            return "金融事实复核失败，不能声称已核验。" + str(exc)[:200]
