"""Read-only market analysis is a loop tool, not implicit HTML publication."""

import re

from policy_impact.domains.finance.report_reader import ReportReadGateway
from policy_impact.harness.permissions import ToolPolicy
from policy_impact.harness.tool_gateway import ToolGateway


NO_ACQUISITION = re.compile(r"(?:不要|不用|无需|禁止|别|不)(?:再)?(?:重新)?(?:联网|刷新|获取|抓取|采集|查询)")


def requests_report(message: str) -> bool:
    for clause in re.split(r"[，,。；;]", message):
        veto = re.search(r"(?:不要|不用|无需|别|暂不|先不|不)(?:再|实际|立刻|现在)?(?:生成|制作|输出|发布|出具|给我|做|出).{0,8}(?:报告|HTML)", clause, re.I)
        conditional = re.search(r"如果|若|不全|不足|缺失|失败|未通过", clause[:veto.start()] if veto else "")
        if veto and not conditional:
            return False
    if re.search(r"怎么|如何|怎样|是否|能否|会不会", message) and not re.search(r"请(?:帮我)?生成|给我.{0,8}报告", message):
        return False
    return bool(re.search(r"(?:生成|制作|输出|出具|给我|做|出)[^。；;\n]{0,120}(?:报告|HTML)|(?:报告|HTML).{0,8}(?:生成|制作)", message, re.I))


def requests_saved_publication(message: str) -> bool:
    if re.search(r"(?:不要|不再|别|不用)(?:沿用|复用)|改成|改为|换成|换为|增加|新增|另加", message):
        return False
    if re.search(r"只看|仅看|最近.{0,6}(?:天|周|月)|过去.{0,6}(?:天|周|月)|\d{4}[-年]\d{1,2}", message):
        return False
    positive = NO_ACQUISITION.sub("", message)
    return bool(re.search(r"沿用|刚才|上面|上一份|那份", message)) and not bool(re.search(r"刷新|重新获取|最新数据|重新抓取", positive))


def forbids_acquisition(message: str) -> bool:
    return bool(NO_ACQUISITION.search(message) or re.search(
        r"只(?:看|依据|用)刚才|只讲概念|不查.{0,8}(?:价格|行情)", message))


def requests_fresh_market(message: str) -> bool:
    if forbids_acquisition(message):
        return False
    if re.search(r"不要(?:联网|查新|刷新)|不用(?:联网|刷新)|不查.{0,8}(?:价格|行情)|只讲概念|只(?:看|依据|用)刚才", message):
        return False
    fresh = bool(re.search(r"今天|今日|最新|行情|最近|本周|刷新|重新获取|现在", message))
    market = bool(re.search(r"市场|行情|金融|黄金|白银|金银|美股|纳指|标普|道指|指数|股市|涨跌|涨了|跌了|资产", message))
    followup = bool(re.search(r"刚才|这份报告|上面|上一份", message))
    return fresh and market and (not followup or bool(re.search(r"刷新|重新获取|最新", message)))


TOOL_DESCRIPTION = {
    "name": "finance.analyze",
    "description": "按用户本轮问题获取/校验金融数据、计算指标并分析；保存证据以供追问，不生成HTML，不交易，不修改Wiki。",
    "parameters": {"query": "用户本轮金融问题。禁止扩大资产/时间范围或自行提供URL，Runtime使用原问题。"},
}


class FinanceChatGateway(ReportReadGateway):
    def __init__(self, company_id, report_ids, *, question, analyze):
        super().__init__(company_id, report_ids)
        from policy_impact.harness.permissions import PermissionEngine

        self.permission_engine = PermissionEngine([
            ToolPolicy("chat_agent_loop", "finance.report_read", "allow", "conversation-scoped financial evidence"),
            ToolPolicy("chat_agent_loop", "finance.analyze",
                       "deny" if forbids_acquisition(question) else "allow",
                       "user forbids acquisition; read the authorized saved snapshot" if forbids_acquisition(question)
                       else "read-only financial analysis; no publication or trading"),
        ])
        self.question = question
        self.analysis_result = None
        self.analysis_error = None
        self.analysis_attempted = False
        self.local_tools["finance.analyze"] = self._analyze
        self._run_analysis = analyze

    def _analyze(self, query=None):
        if forbids_acquisition(self.question):
            raise PermissionError("用户要求沿用快照或不联网，本轮不允许重新采集")
        if query is not None and (not isinstance(query, str) or len(query) > 6000):
            raise ValueError("query must be bounded text")
        if self.analysis_attempted:
            raise ValueError("每轮只允许一次数据分析，避免重复采集和重复模型费用")
        self.analysis_attempted = True
        try:
            # The model cannot expand the user's authorization by rewriting scope.
            result = self._run_analysis(self.question)
            report = result["report"]
            self.analysis_result = result
            self.report_ids.clear()
            self.report_ids.add(report["report_id"])
            self.read_sections.update({"sources", "metrics"})
            observation = {
                "status": "done", "report_id": report["report_id"], "scope": "validated_finance_snapshot",
                "collected_at": report["snapshot"]["collected_at"], "request": report["request"],
                "analysis": report["analysis"], "warnings": report["warnings"],
                "research_audit": report.get("research_audit"), "research_quality": report.get("research_quality"),
                "delivery_instruction": "若research_quality.answer_status为partial，明确走势可用但原因尚未证实；逐资产呈现asset_assessments，不能将候选机制说成结论。",
                "metrics": [{k: v for k, v in m.items() if k not in {"points", "daily_changes"}} for m in report["metrics"]],
                "sources": [{**{k: d[k] for k in ("document_id", "title", "url", "published_at")},
                             "saved_body_characters": len(d.get("text", "")),
                             "read_instruction": "正文已存储，可用report_read source按需展开；不是只存标题，不代表全文完整性已认证。"}
                             for d in report["documents"]
                             if d.get("evidence_id") in report.get("evidence_ids", [])
                             or d["document_id"] in report.get("legacy_evidence_ids", report.get("evidence_ids", []))],
                "source_inventory_count": len(report["documents"]),
                "html_published": False,
            }
        except Exception as exc:
            self.analysis_error = str(exc)[:1200] or type(exc).__name__
            observation = {"status": "blocked", "reason": str(exc), "html_published": False,
                           "instruction": "明确说明本次数据/分析未通过校验，不用旧报告填补，不编造行情。"}
        self.observations.append(observation)
        return observation

    def call(self, stage_name, tool_name, *args, **kwargs):
        if tool_name == "finance.analyze":
            if stage_name != "chat_agent_loop" or args or set(kwargs) - {"query"}:
                raise PermissionError("financial analysis accepts only a bounded user query in the main chat loop")
            return ToolGateway.call(self, stage_name, tool_name, **kwargs)
        return super().call(stage_name, tool_name, *args, **kwargs)

    def final_guard(self, question, output):
        if requests_fresh_market(question) and not self.analysis_attempted:
            return "当前问题需要本轮金融数据，请先调用finance.analyze，不能用已有报告或模型知识冒充今天行情。"
        if self.analysis_error:
            answer = str(output.get("answer", ""))
            if not any(word in answer for word in ("未通过", "无法", "失败", "缺少", "未取得", "未能")):
                return "本轮金融数据或分析未通过，最终回答必须明确失败，不能给出市场结论。"
            return None
        report = (self.analysis_result or {}).get("report") or {}
        verification = report.get("analysis_verification") or {}
        if (report.get("data_status") == "done" and report.get("analysis_status") == "done"
                and verification.get("supported") and not verification.get("issues")):
            # The answer verifier projects these verified facts, rather than
            # requesting redundant evidence pages for a second free-form summary.
            return None
        return super().final_guard(question, output) if self.report_ids else None
