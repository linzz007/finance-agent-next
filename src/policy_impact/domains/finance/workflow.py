"""A bounded financial report workflow, hosted as a tool by the main AgentLoop."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from uuid import uuid4

from policy_impact.company_wiki.indexer import build_company_context_pack
from policy_impact.domains.finance.request import parse_request
from policy_impact.domains.finance.schemas import FinanceAnalysis, FinanceSnapshot, FinanceVerification
from policy_impact.domains.finance.verification import normalize_verification
from policy_impact.domains.finance.document_period import document_period_hint
from policy_impact.domains.finance.quality import FinanceQualityError, require_market_coverage
from policy_impact.domains.finance.research_audit import validate_assessments, summarize
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.report_stage_graph import END, ReportStageError, ReportStageGraph, StageSpec
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.plugin_config import read_plugin_text


STAGES = {
    "profile": "读取金融档案并解析本轮观察范围",
    "acquisition": "获取并保存金融来源快照",
    "validation": "校验交易日、价格与资料来源",
    "metrics": "计算跨资产行情与区间变化",
    "coverage": "校验行情覆盖度与宏观资料窗口",
    "research_audit": "核查归因时间边界与逐资产证据缺口",
    "analysis": "结合个人关注清单分析金融变化",
    "publication": "校验引用并发布金融报告",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_source_quote(quote: str, source: str) -> str | None:
    """Match formatting only, then retain the exact original source span."""
    needle = "".join(quote.split()).casefold()
    if not needle:
        return None
    folded, positions = [], []
    for index, character in enumerate(source):
        if not character.isspace():
            normalized = character.casefold()
            folded.append(normalized)
            positions.extend([index] * len(normalized))
    start = "".join(folded).find(needle)
    if start < 0:
        return None
    return source[positions[start]:positions[start + len(needle) - 1] + 1]


def has_required_analysis_parts(content: str) -> bool:
    return "事实" in content and "可能" in content and any(
        marker in content for marker in ("反证", "待验证", "验证")
    )


def validate_analysis(payload: dict, evidence_ids: set[str], evidence: dict | None = None,
                      technical_context: list[str] | None = None) -> dict:
    value = FinanceAnalysis.model_validate(payload).model_dump(mode="json")
    citation_errors = []
    for section in value["sections"]:
        if not section["evidence_refs"] or not set(section["evidence_refs"]) <= evidence_ids:
            raise ValueError("每个分析段落必须引用本次已获取的证据 ID")
        if evidence is not None:
            quotes = {q["evidence_ref"]: q["quote"] for q in section["evidence_quotes"]}
            for ref in section["evidence_refs"]:
                source = evidence[ref]
                if "text" in source:
                    quote = quotes.get(ref, "")
                    canonical = canonical_source_quote(quote, source["text"])
                    if canonical is None:
                        citation_errors.append(f"段落引用{ref}必须提供对应原文中的真实摘录 evidence_quotes")
                    else:
                        for item in section["evidence_quotes"]:
                            if item["evidence_ref"] == ref:
                                item["quote"] = canonical
    # Give the bounded repair attempt all independent errors, rather than
    # spending its only retry fixing citations before discovering bad numbers.
    errors = list(citation_errors)
    # Financial figures belong to deterministic tables. This also prevents a
    # generated percentage or price from silently bypassing numeric validation.
    prose = " ".join([value["overview"], *[s[k] for s in value["sections"] for k in ("title", "content")],
                      *value["watch_next"], *value["limitations"],
                      *[s[k] for s in value["asset_assessments"] for k in ("hypothesis", "counterevidence", "falsification_test")],
                      *[item for s in value["asset_assessments"] for item in s["missing_evidence"]]])
    numeric_prose = re.sub(r"沪深\s*300|上证\s*50|科创\s*50|标普\s*500|纳斯达克\s*100|S&P\s*500|(?:sh|sz)\d{6}", "指数", prose, flags=re.I)
    numeric_prose = re.sub(r"(?<!\d)(?:19|20)\d{2}(?:年)?\s*[—–至-]\s*(?:19|20)\d{2}年", "年份区间", numeric_prose)
    numeric_prose = re.sub(r"00/04/08/12/16/20\s*(?:点|时)|(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", "采样时刻", numeric_prose)
    numeric_prose = re.sub(r"\d{4}-\d{2}-\d{2}|\d{4}年|(?:[1-9]|1[0-2])\s*[—–至-]\s*(?:[1-9]|1[0-2])月|\d{1,2}月(?:\d{1,2}[—–至-]\d{1,2}日|\d{1,2}日)?|(?<=至)\d{2}-\d{2}|(?:HTTP\s*|返回\s*|状态码\s*)[1-5]\d{2}", "日期或HTTP状态", numeric_prose)
    numeric_prose = re.sub(r"(?<!\d)(?:[1-9]|[12]\d|3[01])日", "日期", numeric_prose)
    technical_text = re.sub(r"\s+", "", " ".join(technical_context or []))
    numeric_prose = re.sub(r"\d+个\d+小时(?:采样)?桶|\d+\s*(?:自然日|天|小时|分钟)",
                           lambda m: "已知采样限制" if re.sub(r"\s+", "", m[0]) in technical_text else m[0], numeric_prose)
    valid_counts = {str(v[key]) for v in (evidence or {}).values()
                    for key in ("point_count", "latest_consecutive_up_days") if key in v}
    numeric_prose = re.sub(r"(\d+)\s*个?\s*(?:交易日|观测日)",
                           lambda m: "观测天数" if m[1] in valid_counts else m[0], numeric_prose)
    invalid = re.search(r"\d|百分之|千分之|万分之|[零〇一二三四五六七八九十百千万亿两]{2,}(?:点|元|基点|美元)", numeric_prose)
    if invalid:
        excerpt = numeric_prose[max(0, invalid.start()-12):invalid.end()+18]
        errors.append("正文不得自行生成金融数值；请引用系统指标表并作定性解释。需修改片段：" + excerpt)
    if not re.search(r"[\u4e00-\u9fff]", prose):
        errors.append("金融报告正文必须使用中文")
    if errors:
        raise ValueError("；".join(dict.fromkeys(errors)))
    return value


def run_finance_report(company_id: str, query: str, *, model_adapter, allowed_tools: tuple[str, ...],
                       on_stage=None, parent_run_id: str = "", publish_html: bool = True) -> dict:
    from policy_impact.domains.finance.profile import load_profile

    stages = []
    graph = ReportStageGraph("financial_report", parent_run_id or uuid4().hex, (
        StageSpec("profile", "acquisition", required_outputs=("profile", "request", "wiki_fact_ids")),
        StageSpec("acquisition", "validation", required_outputs=("snapshot_id", "source_statuses")),
        StageSpec("validation", "metrics", required_outputs=("series_count", "document_count", "warnings")),
        StageSpec("metrics", "coverage", required_outputs=("metrics", "formula"),
                  check=lambda output: None if isinstance(output["metrics"], list) else "invalid metric list"),
        StageSpec("coverage", "research_audit", next_on_ask="research_audit",
                  required_outputs=("market_gate", "warnings")),
        StageSpec("research_audit", "analysis", required_outputs=("document_selection",)),
        StageSpec("analysis", "publication" if publish_html else END,
                  next_on_ask="publication" if publish_html else END,
                  required_outputs=("analysis", "errors", "verification")),
        *([StageSpec("publication", END, required_outputs=("artifacts", "data_status"))] if publish_html else []),
    ))
    tools = {"acquisition": "finance.collect", "metrics": "finance.metrics",
             "research_audit": "finance.research_audit", "publication": "finance.publish"}
    gateway = ToolGateway(PermissionEngine([
        ToolPolicy("finance." + stage, tool, "allow", "activated /report skill permits this financial stage")
        for stage, tool in tools.items() if tool in allowed_tools
    ]))

    def emit(stage, status, *, inputs=None, outputs=None, gate=None, calls=None, values=None):
        transition = None
        if status == "started":
            graph.start(stage)
        elif status == "completed":
            try:
                transition = graph.finish(stage, outputs or {}, gate, values=values)
            except ReportStageError as exc:
                failure = {"stage": stage, "title": STAGES[stage], "status": "failed", "at": _now(),
                           "input": inputs or {}, "output": {"reason": str(exc)},
                           "gate": {"decision": "deny", "reason": str(exc)}, "tool_calls": calls or [],
                           "transition": graph.state.transitions[-1] if graph.state.transitions else None}
                stages.append(failure)
                if on_stage:
                    on_stage(failure)
                raise
        elif status == "failed":
            transition = graph.fail(stage, str((gate or {}).get("reason") or "stage failed"))
        record = {"stage": stage, "title": STAGES[stage], "status": status, "at": _now(),
                  "input": inputs or {}, "output": outputs or {}, "gate": gate, "tool_calls": calls or [],
                  "transition": transition}
        stages.append(record)
        if on_stage:
            on_stage(record)

    emit("profile", "started", inputs={"question": query})
    profile = load_profile(company_id)
    if not profile:
        emit("profile", "failed", gate={"decision": "deny", "reason": "当前工作空间尚未建立金融观察档案"})
        raise ValueError("当前工作空间尚未建立金融观察档案")
    request = parse_request(query, profile)
    strict = request.get("quality_policy", profile.get("quality_policy", "allow_partial")) == "strict"
    context = build_company_context_pack(company_id, task="financial_report")
    facts = [f for f in context.get("all_company_facts", context.get("retrieved_company_facts", []))]
    emit("profile", "completed", inputs={"question": query}, outputs={"profile": profile, "request": request,
         "wiki_fact_ids": [f.get("fact_id") for f in facts]}, gate={"decision": "allow", "reason": "时间范围与画像已固定"},
         values={"wiki_facts": facts})
    profile = graph.state.require("profile", "profile")
    request = graph.state.require("profile", "request")
    facts = graph.state.require_value("wiki_facts")
    emit("acquisition", "started", inputs=request)
    raw = gateway.call("finance.acquisition", "finance.collect", company_id=company_id, request=request)
    snapshot = FinanceSnapshot.model_validate(raw).model_dump(mode="json")
    emit("acquisition", "completed", outputs={"snapshot_id": snapshot["snapshot_id"],
          "collected_at": snapshot["collected_at"], "source_statuses": snapshot["source_statuses"]},
          calls=[gateway.calls[-1].to_dict()], values={"snapshot": snapshot})
    snapshot = graph.state.require_value("snapshot")
    if graph.state.require("acquisition", "snapshot_id") != snapshot["snapshot_id"]:
        raise ValueError("snapshot state mismatch")
    emit("validation", "started", inputs={"snapshot_id": snapshot["snapshot_id"]})
    try:
        if snapshot["period"] != request["period"]:
            raise ValueError("采集快照与本轮报告期不一致")
        expected = set(request["symbols"])
        if not {s["symbol"] for s in snapshot["series"]} <= expected:
            raise ValueError("快照含本次请求范围外的指数")
    except ValueError as exc:
        emit("validation", "failed", outputs={"reason": str(exc), "snapshot_id": snapshot["snapshot_id"]},
             gate={"decision": "deny", "reason": str(exc)})
        raise
    document_period = request.get("document_period", request["period"])
    documents = [d for d in snapshot["documents"]
                 if document_period["start"] <= d["published_at"] <= document_period["end"]] if request["include_documents"] else []
    documents = [{**d, "title_period_hint": document_period_hint(d)} for d in documents]
    warnings = list(snapshot["warnings"])
    missing = expected - {s["symbol"] for s in snapshot["series"] if s["bars"]}
    if missing:
        warnings.append("未取得部分指数数据：" + "、".join(sorted(missing)))
    if request["include_documents"] and not documents:
        warnings.append("本次报告期内未取得可用宏观发布或机构原文，不以历史文章补齐。")
    unhealthy = [s["source_id"] for s in snapshot["source_statuses"] if s["status"] in {"error", "partial"}]
    if unhealthy:
        warnings.append("部分来源未完整获取：" + "、".join(unhealthy))
    emit("validation", "completed", outputs={"series_count": len(snapshot["series"]), "document_count": len(documents),
          "warnings": warnings}, gate={"decision": "allow", "reason": "有效记录通过 schema；缺失来源单独披露"},
          values={"documents": documents})
    documents = graph.state.require_value("documents")
    warnings = list(graph.state.require("validation", "warnings"))
    emit("metrics", "started")
    metrics = gateway.call("finance.metrics", "finance.metrics", snapshot=snapshot)
    for metric in metrics:
        if metric.get("status") != "ok":
            warnings.append(f"{metric['name']}缺少报告期前基准，不能视为完整区间涨跌。")
        if metric.get("end_gap_calendar_days", 0) > 5:
            warnings.append(f"{metric['name']}最近可得交易日为{metric['end_date']}，距报告截止日较远；未验证交易日历。")
    emit("metrics", "completed", outputs={"metrics": metrics, "formula": "(end_close / start_close - 1) * 100"},
          calls=[gateway.calls[-1].to_dict()])
    metrics = graph.state.require("metrics", "metrics")
    emit("coverage", "started", inputs={"snapshot_id": snapshot["snapshot_id"], "strict": strict})
    if strict:
        try:
            quality_gate = require_market_coverage(request, snapshot, metrics)
            if request["include_documents"] and not documents:
                raise FinanceQualityError("未取得新闻窗口内可核对的宏观原文，无法交付带证据的原因分析。")
        except FinanceQualityError as exc:
            emit("coverage", "failed", outputs={"reason": str(exc), "snapshot_id": snapshot["snapshot_id"]},
                  gate={"decision": "deny", "reason": str(exc)})
            raise
    else:
        quality_gate = {"decision": "ask" if warnings else "allow",
                        "reason": "存在缺失或时效限制，后续分析必须披露" if warnings else "行情与资料覆盖可用于分析"}
    emit("coverage", "completed", outputs={"market_gate": quality_gate, "warnings": warnings}, gate=quality_gate)
    warnings = graph.state.require("coverage", "warnings")
    emit("research_audit", "started", inputs={"snapshot_id": snapshot["snapshot_id"]})
    research_audit = gateway.call("finance.research_audit", "finance.research_audit",
                                  request=request, metrics=metrics, documents=documents)
    emit("research_audit", "completed", outputs=research_audit, calls=[gateway.calls[-1].to_dict()],
          gate={"decision": "allow", "reason": "仅允许证据约束的观察与候选解释，不代表因果已核实"})
    research_audit = dict(graph.state.outputs["research_audit"])
    selection = research_audit["document_selection"]["selected_ids"]
    by_id = {d["document_id"]: d for d in documents}
    selected_documents = [by_id[ref] for ref in selection]
    evidence_ledger = EvidenceLedger()
    evidence: dict[str, dict] = {}
    metric_evidence: dict[str, str] = {}
    for metric in metrics:
        item = evidence_ledger.add_market_metric(metric, snapshot_id=snapshot["snapshot_id"])
        metric["evidence_id"] = item.evidence_id
        metric_evidence[str(metric["metric_id"])] = item.evidence_id
        evidence[str(metric["metric_id"])] = {
            **{k: v for k, v in metric.items() if k not in {"points", "daily_changes"}},
            "evidence_id": item.evidence_id,
            "legacy_ref": str(metric["metric_id"]),
            "evidence_type": "market_metric",
        }
    document_evidence: dict[str, str] = {}
    for document in documents:
        item = evidence_ledger.add_source_document(document)
        document["evidence_id"] = item.evidence_id
        document_evidence[str(document["document_id"])] = item.evidence_id
    for document in selected_documents:
        evidence[str(document["document_id"])] = {
            **document,
            "text": document["text"][:3800],
            "evidence_id": document["evidence_id"],
            "legacy_ref": str(document["document_id"]),
            "evidence_type": "source_document",
        }
    fact_evidence: dict[str, str] = {}
    for fact in facts:
        if not fact.get("fact_id"):
            continue
        item = evidence_ledger.add_wiki_fact(fact)
        fact_evidence[str(fact["fact_id"])] = item.evidence_id
        evidence[str(fact["fact_id"])] = {
            "type": "profile_fact", "value": fact.get("value"),
            "source": fact.get("source"), "source_path": fact.get("source_path"),
            "status": "background_only_not_current_holdings", "metadata": fact.get("metadata", {}),
            "evidence_id": item.evidence_id, "legacy_ref": str(fact["fact_id"]),
            "evidence_type": "wiki_fact",
        }
    analysis = None
    analysis_errors = []
    verification = None
    emit("analysis", "started", inputs={"evidence_ids": list(evidence), "profile_version": profile["version"]})
    if evidence:
        messages = [
            {"role": "system", "content": read_plugin_text("prompts", "finance_report_system.md")},
            {"role": "user", "content": json.dumps({"request": request, "profile": profile,
                "wiki_facts": facts[:12], "evidence": evidence, "quality_warnings": warnings,
                "research_audit": research_audit,
                "output_schema": FinanceAnalysis.model_json_schema()}, ensure_ascii=False)},
        ]
        for attempt in range(2):
            try:
                response = model_adapter.complete(messages=messages, schema_name="finance_analysis.v1")
                candidate = validate_analysis(response.payload.get("output", response.payload), set(evidence), evidence,
                                              technical_context=warnings)
                validate_assessments(candidate, research_audit, evidence)
                from policy_impact.domains.finance.answer_verifier import price_basis_issues
                basis_errors = price_basis_issues("\n".join([candidate["overview"], *[s["content"] for s in candidate["sections"]]]), [{"metrics": metrics}])
                if basis_errors:
                    raise ValueError("；".join(basis_errors))
                if strict:
                    if len(candidate["sections"]) > 4:
                        raise ValueError("请收敛为一至三条宏观主线，可另加一个市场概览，不按每个资产分段")
                    for section in candidate["sections"]:
                        if not has_required_analysis_parts(section["content"]):
                            raise ValueError("每段须包含事实、可能解释、反证/待验证三部分，不能只有泛泛结论")
                    doc_ids = {d["document_id"] for d in documents}
                    if selected_documents and request["include_documents"] and not any(doc_ids & set(s["evidence_refs"]) for s in candidate["sections"]):
                        raise ValueError("没有宏观原文用于分析，仅复述行情不能交付所要求的宏观研判")
                expected_checks = {"overview", *[f"section_{i}" for i in range(len(candidate["sections"]))],
                                   *[f"asset_{a['symbol']}" for a in candidate["asset_assessments"]]}
                review = model_adapter.complete(schema_name="finance_verification.v1", messages=[
                    {"role": "system", "content": read_plugin_text("prompts", "finance_verification_system.md")},
                    {"role": "user", "content": json.dumps({"expected_items": sorted(expected_checks), "profile": profile,
                        "request": request, "observed_symbols": [m["symbol"] for m in metrics],
                        "research_audit": research_audit,
                        "asset_assessments": [{"item_id": f"asset_{a['symbol']}", "assessment": a,
                            "cited_evidence": {ref: evidence[ref] for ref in a["evidence_refs"]}} for a in candidate["asset_assessments"]],
                        "scope_rule": "profile中的支持/关注不等于本轮取得；仅observed_symbols表示实际取得。未请求的标的不能判为缺少核心数据，也不能因profile已接入而否定本轮未包含。",
                        "source_inventory": [{"id": d["document_id"], "publisher": d["publisher"], "kind": d["kind"]} for d in documents], "overview": candidate["overview"], "sections": [
                        {"item_id": f"section_{i}", "section": s, "cited_evidence": {ref: evidence[ref] for ref in s["evidence_refs"]}} for i, s in enumerate(candidate["sections"])]}, ensure_ascii=False)},
                ])
                verification = normalize_verification(review.payload.get("output", review.payload), expected_checks)
                if not verification["supported"] or verification["issues"]:
                    raise ValueError("引用语义复核未通过：" + "；".join(verification["issues"]))
                analysis = candidate
                break
            except Exception as exc:
                analysis_errors.append(str(exc)[:400])
                if attempt == 0:
                    if 'response' in locals():
                        messages.append({"role": "assistant", "content": json.dumps(response.payload, ensure_ascii=False)})
                    messages.append({"role": "user", "content": "上面的候选未通过校验。根据下面具体问题修改或删除不被支持的主张，保持正确的部分；重新返回完整 JSON：" + str(exc)[:2000]})
    if not analysis:
        warnings.append("分析未完成：" + (analysis_errors[-1] if analysis_errors else "没有符合报告期的可用证据"))
        if strict or not publish_html:
            reason = warnings[-1]
            emit("analysis", "failed", outputs={"errors": analysis_errors, "verification": verification},
                 gate={"decision": "deny", "reason": reason})
            raise FinanceQualityError(reason + "；未发布不合格报告。")
    emit("analysis", "completed", outputs={"analysis": analysis, "errors": analysis_errors, "verification": verification},
          gate={"decision": "allow" if analysis else "ask", "reason": "中文 schema 与证据引用校验" if analysis else "分析不可用，仅发布数据情况"})
    analysis = graph.state.require("analysis", "analysis")
    report_id = "fin_" + uuid4().hex
    research_quality = summarize(analysis, research_audit)
    status = "done" if analysis and (metrics or not expected) and (strict or not warnings) else "partial"
    cited_fact_ids = {ref for section in (analysis or {}).get("sections", []) for ref in section["evidence_refs"]}
    evidence_bundle = evidence_ledger.bundle()
    report = {
        "schema_version": "finance.report.v1", "skill": "financial_report", "company_id": company_id,
        "report_id": report_id, "parent_run_id": parent_run_id, "created_at": _now(), "request": request,
        "profile_snapshot": profile, "wiki_fact_refs": [f.get("fact_id") for f in facts],
        "wiki_fact_snapshots": [{key: f.get(key) for key in ("fact_id", "value", "source", "source_path", "status", "confidence")}
                                for f in facts if f.get("fact_id") in cited_fact_ids],
        "snapshot_id": snapshot["snapshot_id"], "snapshot": snapshot, "metrics": metrics,
        "documents": documents, "analysis": analysis, "analysis_status": "done" if analysis else "partial",
        "analysis_verification": verification,
        "research_audit": research_audit, "research_quality": research_quality,
        "data_status": status, "warnings": list(dict.fromkeys(warnings)), "stages": stages,
        # Canonical IDs are the source of truth. Legacy IDs remain usable in
        # analysis_refs and old follow-up code through this explicit alias map.
        "evidence_ids": list(evidence_ledger.evidence_ids),
        "legacy_evidence_ids": list(evidence),
        "evidence_aliases": evidence_ledger.aliases,
        "evidence_bundle": evidence_bundle,
        "evidence_ref_schema": "finance.evidence_ref.v1",
        "evidence_maps": {
            "metrics": metric_evidence,
            "documents": document_evidence,
            "wiki_facts": fact_evidence,
        },
        "delivery_mode": "html_report" if publish_html else "chat_analysis",
    }
    if not publish_html:
        from pathlib import Path
        from policy_impact.company_wiki.loader import company_dir
        from policy_impact.domains.finance.store import FinanceStore

        # Keep the exact same evidence authorization mechanism as report follow-ups,
        # without publishing an HTML page or relying on model-maintained Memory.
        report["workflow_graph"] = graph.state.summary()
        FinanceStore(company_id).save_report(report)
        evidence_path = Path(company_dir(company_id)) / "runs" / f"{report_id}.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with evidence_path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return {"answer": analysis["overview"], "report": report,
                "workflow_graph": graph.state.summary(),
                "artifacts": {"run_artifact": str(evidence_path)},
                "citations": ([{"type": "financial_source", "title": m["name"], "url": m["source_url"]} for m in metrics]
                              + [{"type": "financial_source", "title": d["title"], "url": d["url"]} for d in selected_documents]),
                "metadata": {"report_id": report_id, "analysis_status": "done", "data_status": status,
                             "research_quality": research_quality}}
    emit("publication", "started", inputs={"report_id": report_id})
    artifacts = gateway.call("finance.publication", "finance.publish", company_id=company_id, report=report)
    emit("publication", "completed", outputs={"artifacts": artifacts, "data_status": status},
          gate={"decision": "allow", "reason": "报告保留来源、观察时点、画像版本和不完整状态"},
          calls=[gateway.calls[-1].to_dict()])
    report["workflow_graph"] = graph.state.summary()
    title = ("全球宏观市场复盘 · 最近完整日线" if request.get("session_mode") == "latest_completed"
             else f"金融观察报告 · {request['period']['start']} 至 {request['period']['end']}")
    answer = f"## {title}\n\n" + (analysis["overview"] if analysis else "本次未完成金融分析，请查看数据可用性说明。")
    answer += "\n\n### 市场概览\n"
    for m in metrics:
        change = (f"{m['change_pct']:+.4f}%" if 0 < abs(m['change_pct']) < 0.005 else f"{m['change_pct']:+.2f}%") if m['change_pct'] is not None else "区间涨跌不可计算"
        answer += f"\n- **{m['name']}**：{change}，计算区间 {m['start_date']} 至 {m['end_date']}。"
    if warnings:
        answer += "\n\n### 数据边界\n- 使用已完成日线，不是盘中实时行情；各品种的基准日和截止口径见报告。"
        if expected & {"xauusd", "xagusd"}:
            answer += "\n- 金银为国际现货 UTC 采样参考价，不是统一官方收盘价或可执行报价。"
        answer += "\n- 未将宏观事件与涨跌的共现当作已证实因果；来源可用性、备用源与其他限制已保留在报告附录。"
    answer += f"\n\n已生成 HTML 报告，包含 {len(metrics)} 组行情指标和 {len(documents)} 篇原始资料。可以继续追问计算方式、来源或不同解释。\n\n报告编号：`{report_id}`"
    answer += "\n\n**研判边界：" + research_quality["label"] + "。** " + research_quality["reason"]
    return {"answer": answer, "artifacts": artifacts,
            "workflow_graph": graph.state.summary(),
            "citations": ([{"type": "financial_source", "title": m["name"], "url": m["source_url"]} for m in metrics]
                          + [{"type": "financial_source", "title": d["title"], "url": d["url"]} for d in documents]),
            "metadata": {"report_id": report_id, "analysis_status": report["analysis_status"], "data_status": status,
                         "research_quality": research_quality},
            "internal_route": "finance", "report": report}


def publish_saved_analysis(company_id: str, report_id: str, *, parent_run_id: str, allowed_tools: tuple[str, ...], on_stage=None) -> dict:
    """Publish only an explicitly authorized saved result; do not fetch or reanalyze."""
    from copy import deepcopy
    from policy_impact.domains.finance.store import FinanceStore

    source = FinanceStore(company_id).get_report(report_id)
    if not source or source.get("company_id") != company_id:
        raise FinanceQualityError("本会话没有授权的金融分析快照")
    if not source.get("analysis") or source.get("analysis_status") != "done" or source.get("data_status") != "done":
        raise FinanceQualityError("已有分析未通过，不能转为报告")
    review = source.get("analysis_verification") or {}
    if not review.get("supported") or review.get("issues"):
        raise FinanceQualityError("已有分析缺少有效复核结果")
    require_market_coverage(source["request"], source["snapshot"], source["metrics"])
    from policy_impact.domains.finance.answer_verifier import price_basis_issues
    prose = "\n".join([source["analysis"]["overview"], *[s["content"] for s in source["analysis"]["sections"]]])
    current_errors = price_basis_issues(prose, [source])
    if current_errors:
        raise FinanceQualityError("保存正文未通过现行发布口径检查，未重新采集也未发布：" + "；".join(current_errors))
    if source["request"].get("include_documents") and not source.get("documents"):
        raise FinanceQualityError("已有分析缺少要求的宏观原文，不能转为报告")
    report = deepcopy(source)
    report.update(report_id="fin_" + uuid4().hex, parent_run_id=parent_run_id, created_at=_now(),
                  delivery_mode="html_report", source_analysis_id=report_id)
    report.pop("artifacts", None)
    gateway = ToolGateway(PermissionEngine([
        ToolPolicy("finance.publication", "finance.publish", "allow", "explicit report request reuses authorized verified snapshot")
    ] if "finance.publish" in allowed_tools else []))
    artifacts = gateway.call("finance.publication", "finance.publish", company_id=company_id, report=report)
    if on_stage:
        on_stage({"stage": "publication", "title": "将已核验分析生成 HTML（未重新采集）", "status": "completed",
                  "input": {"source_analysis_id": report_id}, "output": {"artifacts": artifacts, "fresh_fetch": False},
                  "gate": {"decision": "allow", "reason": "复用本会话合格证据"}, "tool_calls": [gateway.calls[-1].to_dict()]})
    return {"answer": "已将刚才通过校验的分析生成 HTML 报告，沿用原始快照，没有重新联网。\n\n" + report["analysis"]["overview"],
            "report": report, "artifacts": artifacts, "citations": [], "internal_route": "finance",
            "metadata": {"report_id": report["report_id"], "source_analysis_id": report_id,
                         "analysis_status": report["analysis_status"], "data_status": report["data_status"]}}
