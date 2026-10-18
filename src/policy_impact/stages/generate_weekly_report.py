"""Stage: generate weekly policy impact report."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from html import escape

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.gate_engine import GateEngine, claim_payload_from_assessment
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.gate_catalog import canonical_claim_snapshot_hash
from policy_impact.stages.analyze_policy_applicability import _all_company_facts


def _policy_source_url(state: PolicyImpactState, policy_id: str) -> str:
    for document in state.policy_documents:
        if str(document.get("policy_id") or "") == str(policy_id or ""):
            return str(document.get("source_url") or "")
    return ""


_APPLICABILITY_LABELS = {
    "direct": "直接适用",
    "conditional": "条件适用",
    "not_applicable": "明确不适用",
    "insufficient_evidence": "证据不足",
}

_BINDING_LABELS = {
    "mandatory": "强制性规则",
    "encouraged": "鼓励性要求",
    "guidance": "指导性意见",
    "unknown": "效力待确认",
}

_EFFECTIVE_LABELS = {
    "effective": "已施行",
    "not_yet_effective": "尚未施行",
    "unknown": "施行日期待确认",
}

_SOURCE_KIND_LABELS = {
    "public_disclosure": "公开披露",
    "analysis_inference": "分析推断",
    "project_design": "项目设计",
    "user_confirmed": "用户确认",
}


def _format_company_evidence(item: dict) -> str:
    source_kind = str(item.get("source_kind") or "other")
    source_label = _SOURCE_KIND_LABELS.get(source_kind, source_kind)
    fact_id = str(item.get("fact_id") or "unknown")
    if item.get("evidence_role") == "background":
        source_label += "; 背景证据，非生产事实"
    return f"[{source_label}; fact_id: {fact_id}] {item.get('value', '')}"


def _format_exclusion_conditions(item: dict) -> str:
    conditions = "; ".join(item.get("exclusion_conditions") or [])
    if not conditions:
        return "当前可核验来源未提取到明确排除条款，不代表不存在排除条件。"
    if item.get("applicability") != "not_applicable":
        return f"{conditions}；企业是否满足排除条件仍待核实。"
    return f"{conditions}；企业符合情况见适用理由与企业证据。"


def _assessment_groups(state: PolicyImpactState) -> dict[str, list[dict]]:
    groups = {key: [] for key in _APPLICABILITY_LABELS}
    for item in state.impact_assessments:
        groups.setdefault(str(item.get("applicability") or "conditional"), []).append(item)
    return groups


def _report_scope(state: PolicyImpactState) -> dict:
    profile = state.company_context_pack.get("profile") or {}
    facts = _all_company_facts(state)
    goals = [
        f"[{fact['fact_id']}] {fact['value']}" for fact in facts
        if any(term in fact["value"].lower() for term in ("目标", "需求", "goal", "requirement"))
    ]
    profile_goals = profile.get("current_goals") or []
    if isinstance(profile_goals, str):
        profile_goals = [profile_goals]
    goals = goals or [str(goal).strip() for goal in profile_goals if str(goal).strip()]
    request = str(state.current_user_message or "").strip()
    current_goal = "；".join([
        *([f"本轮：{request}"] if request else []),
        *([f"Wiki目标：{'；'.join(goals[:3])}"] if goals else []),
    ]) or "待确认：当前请求和 Wiki 目标均未提供，不能视为已完成用户目标。"
    boundaries = [
        "仅覆盖本轮已采集的政策快照，未接入文件不能视为不适用；不构成完整合规或上线结论。",
        *[
            f"[{fact['fact_id']}] {fact['value']}" for fact in facts
            if any(term in fact["value"] for term in ("约束", "不访问生产", "不执行交易", "不得推测", "不替"))
        ][:3],
    ]
    codes, limitations = [], []
    date_from = str(state.date_range.get("date_from") or "")
    date_to = str(state.date_range.get("date_to") or "")
    window_days = None
    try:
        elapsed = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days
        if elapsed < 0:
            raise ValueError("reversed policy date range")
        window_days = max(1, elapsed)
    except ValueError:
        codes.append("invalid_date_range")
        limitations.append("实际日期范围缺失或无效，不能确认时间覆盖。")
    if not request:
        codes.append("current_request_not_propagated")
        limitations.append("当前用户请求未传入政策阶段；当前目标来自 Wiki，不能确认本轮完整任务范围。")
    requested_window = re.search(r"(?:最近|过去|近)\s*(\d+)\s*天", request)
    if requested_window and window_days != int(requested_window.group(1)):
        codes.append("requested_window_mismatch")
        limitations.append("用户要求与实际政策日期范围不一致；仅交付下述实际范围，不能称已完成原请求。")
    skeptic_bundles = [
        bundle for bundle in state.review_bundles
        if bundle.get("role") in {"skeptic", "skeptic_rule_engine"}
    ]
    failed_skeptic = any(
        bundle.get("stop_reason") not in {"success", "model_not_configured"}
        for bundle in skeptic_bundles
    ) or any(error.get("stage") == "subagent.skeptic" for error in state.errors)
    if failed_skeptic:
        codes.append("skeptic_failed")
        limitations.append("Skeptic 独立质疑失败，未完成独立核验；仅通过确定性证据门控，报告为部分完成。")
    elif state.impact_assessments and not any(
        bundle.get("role") == "skeptic" and bundle.get("stop_reason") == "success"
        for bundle in skeptic_bundles
    ):
        codes.append("skeptic_not_completed")
        limitations.append("Skeptic 独立质疑未完成或未配置；仅有确定性证据检查，不代表独立核验通过。")
    if any(
        "source_page_without_attachment" in item.get("reason_codes", [])
        for item in state.policy_applicability
    ):
        codes.append("policy_attachment_missing")
        limitations.append("部分政策只有通知页、缺少附件全文，相应条款和企业义务仍待核实。")
    fixture_mode = any(doc.get("data_mode") == "fixture" for doc in state.policy_documents)
    title = "本地样例政策影响报告" if fixture_mode else "政策影响报告"
    if window_days is not None:
        title += f"（{window_days}天窗口）"
    return {
        "report_title": title,
        "date_range": dict(state.date_range),
        "window_days": window_days,
        "current_request": request,
        "current_goal": current_goal,
        "user_boundaries": boundaries,
        "completion_status": "partial" if codes else "completed_with_boundaries",
        "limitation_codes": codes,
        "limitations": limitations,
    }


def _scope_disclosures(scope: dict) -> list[str]:
    label = "部分完成" if scope["completion_status"] == "partial" else "限定范围内完成"
    return [
        f"交付状态：{label}；产物已生成不等于完整核验或用户目标全部完成。",
        f"实际范围：{scope['date_range'].get('date_from', '')} 至 {scope['date_range'].get('date_to', '')}",
        f"当前目标：{scope['current_goal']}",
        *[f"用户边界：{boundary}" for boundary in scope["user_boundaries"]],
        *[f"未完成项：{limitation}" for limitation in scope["limitations"]],
    ]


def _render_report(state: PolicyImpactState) -> str:
    profile = state.company_context_pack.get("profile", {})
    fixture_mode = any(doc.get("data_mode") == "fixture" for doc in state.policy_documents)
    scope = _report_scope(state)
    report_title = scope["report_title"]
    groups = _assessment_groups(state)
    lines = [
        f"# {report_title}",
        "",
        (
            "> 数据边界：本报告使用本地验收样例政策，并非真实监管文件，不可用于现实合规判断。"
            if fixture_mode
            else "> 数据边界：本报告仅覆盖已采集并保留来源信息的政策快照。"
        ),
        *[f"> {line}" for line in _scope_disclosures(scope)],
        "",
        "## 1. 企业理解摘要",
        f"- 企业：{profile.get('company_name', state.company_id)}",
        f"- 行业关键词：{', '.join(str(x) for x in profile.get('business_keywords', []))}",
        f"- 当前目标：{scope['current_goal']}",
        "",
        "## 2. 所选时间范围政策总览",
        f"- 拉取政策数量：{len(state.policy_documents)}",
        f"- 相关影响判断数量：{len(state.impact_assessments)}",
        f"- P0/P1 数量：{sum(1 for x in state.impact_assessments if x.get('impact_level') in {'P0', 'P1'})}",
        "",
        "## 3. 适用性判断",
    ]
    if state.source_manifest.get("no_updates"):
        lines.extend(
            [
                "",
                "在所选时间范围内，已配置的官方来源抓取成功，但没有发现新增政策文件。",
                "系统不会用历史文件或测试样例伪造所选时间范围内的更新。",
                "",
            ]
        )
    for relationship in ("direct", "conditional", "not_applicable", "insufficient_evidence"):
        items = groups.get(relationship) or []
        lines.extend(["", f"### {_APPLICABILITY_LABELS[relationship]}（{len(items)}）", ""])
        if not items:
            lines.append("- 无")
            continue
        for item in items:
            spans = [item.get("policy_evidence") or {}] + list(
                (item.get("policy_evidence") or {}).get("additional_spans") or []
            )
            quotes = [str(span.get("text") or "") for span in spans if str(span.get("text") or "").strip()]
            lines.extend(
                [
                    f"#### {item['impact_level']} - {item['policy_title']}",
                    f"- 规则效力：{_BINDING_LABELS.get(str(item.get('binding_effect')), item.get('binding_effect', ''))}",
                    f"- 时间效力：{_EFFECTIVE_LABELS.get(str(item.get('effective_status')), '施行日期待确认')}"
                    + (f"（{item.get('effective_date')}）" if item.get('effective_date') else ""),
                    f"- 判断分数：{item['relevance_score']}（分数不替代适用性分类）",
                    f"- 适用理由：{item.get('reasoning', [''])[1] if len(item.get('reasoning', [])) > 1 else '未记录'}",
                    f"- 触发条件：{'; '.join(item.get('trigger_conditions', [])) or '尚待核实，不能视为没有触发条件'}",
                    f"- 排除条件（政策原文）：{_format_exclusion_conditions(item)}",
                    f"- 政策原文依据：{' | '.join(quotes) or '当前来源未提供可核验条款'}",
                    f"- 政策原文链接：{_policy_source_url(state, item.get('policy_id', '')) or '未记录'}",
                    f"- 企业证据：{'; '.join(_format_company_evidence(x) for x in item.get('company_evidence', [])) or '无可绑定企业事实'}",
                    f"- 待确认事实：{'; '.join(item.get('missing_fields', [])) or '无'}",
                    f"- 建议动作：{'; '.join(item.get('recommended_actions', [])) or '无'}",
                    "",
                ]
            )
    missing_fields = sorted(
        {
            str(field)
            for item in state.impact_assessments
            for field in item.get("missing_fields", [])
            if str(field).strip()
        }
    )
    lines.extend(
        [
            "## 4. 需要用户确认/补充的信息",
            *([f"- {field}" for field in missing_fields] if missing_fields else ["- 当前没有待确认事实。"]),
            "",
            "## 5. 审计摘要",
            f"- unsupported_claim_rate：{state.review_result.get('unsupported_claim_rate', 0)}",
            f"- forced_relevance_count：{state.quality_metrics.get('forced_relevance_count', 0)}",
            f"- verified_policy_quote_count：{state.quality_metrics.get('verified_policy_quote_count', 0)}",
        ]
    )
    if state.regulatory_coverage_gaps:
        lines.extend(
            [
                "",
                "## 6. 监管覆盖边界（非适用性结论）",
                "- 下列既有框架不在本轮近期政策语料中；未单独核验前，本报告不能作为完整上线清单。",
            ]
        )
        for gap in state.regulatory_coverage_gaps:
            lines.extend(
                [
                    f"- {gap.get('framework')}：{gap.get('trigger_reason')}。",
                    f"  - 下一步：{gap.get('next_check')}",
                    f"  - 官方入口：{gap.get('official_url')}",
                ]
            )
    return "\n".join(lines)


def _render_html_report(state: PolicyImpactState) -> str:
    profile = state.company_context_pack.get("profile", {})
    fixture_mode = any(doc.get("data_mode") == "fixture" for doc in state.policy_documents)
    scope = _report_scope(state)
    report_title = scope["report_title"]
    scope_html = "".join(f"<p>{escape(line)}</p>" for line in _scope_disclosures(scope))
    boundary_text = (
        "本报告使用本地验收样例政策，并非真实监管文件，不可用于现实合规判断。"
        if fixture_mode
        else "本报告仅覆盖已采集并保留来源信息的政策快照。"
    )
    coverage_html = ""
    if state.regulatory_coverage_gaps:
        items = "".join(
            "<li>"
            f"<strong>{escape(str(gap.get('framework') or ''))}</strong>："
            f"{escape(str(gap.get('trigger_reason') or ''))}<br>"
            f"待核验：{escape(str(gap.get('next_check') or ''))}<br>"
            f"<a href='{escape(str(gap.get('official_url') or ''), quote=True)}' target='_blank' rel='noopener'>官方入口</a>"
            "</li>"
            for gap in state.regulatory_coverage_gaps
        )
        coverage_html = (
            "<section class='coverage'><h2>监管覆盖边界（非适用性结论）</h2>"
            "<p>下列既有框架不在本轮近期政策语料中；未单独核验前，本报告不能作为完整上线清单。</p>"
            f"<ul>{items}</ul></section>"
        )
    rows = []
    for item in state.impact_assessments:
        evidence = "<br>".join(
            escape(_format_company_evidence(hit)) for hit in item.get("company_evidence", [])[:4]
        )
        reasons = "<br>".join(escape(str(reason)) for reason in item.get("reasoning", [])[:5])
        reasons += f"<br><strong>排除条件（政策原文）：</strong>{escape(_format_exclusion_conditions(item))}"
        source_url = _policy_source_url(state, str(item.get("policy_id") or ""))
        source_link = (
            f"<a href='{escape(source_url, quote=True)}' target='_blank' rel='noopener'>查看原文</a>"
            if source_url
            else "未记录"
        )
        rows.append(
            "\n".join(
                [
                    "<tr>",
                    f"<td><span class='level {escape(item.get('impact_level', 'P4'))}'>{escape(item.get('impact_level', 'P4'))}</span></td>",
                    f"<td>{escape(str(item.get('policy_title', '')))}</td>",
                    f"<td>{source_link}</td>",
                    f"<td>{escape(_APPLICABILITY_LABELS.get(str(item.get('applicability')), str(item.get('applicability', ''))))}</td>",
                    f"<td>{escape(_BINDING_LABELS.get(str(item.get('binding_effect')), str(item.get('binding_effect', ''))))}</td>",
                    f"<td>{escape(_EFFECTIVE_LABELS.get(str(item.get('effective_status')), '施行日期待确认'))}"
                    f"<br>{escape(str(item.get('effective_date') or ''))}</td>",
                    f"<td><strong>{escape(str(item.get('relevance_score', '')))}</strong></td>",
                    f"<td>{reasons}</td>",
                    f"<td>{evidence}</td>",
                    f"<td>{escape('; '.join(str(x) for x in item.get('recommended_actions', [])))}</td>",
                    "</tr>",
                ]
            )
        )
    if state.source_manifest.get("no_updates"):
        rows.append(
            "<tr><td colspan='10'>官方来源抓取成功；所选时间范围内没有新增政策文件，未生成影响判断。</td></tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #68717d;
      --line: #dfe4ea;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --accent: #1f7a5c;
      --risk: #b42318;
      --warn: #b86b00;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 24px 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      border-bottom: 1px solid var(--line);
      padding-bottom: 22px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 30px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(150px, 1fr));
      gap: 12px;
      margin: 22px 0;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .metric strong {{
      font-size: 26px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 14px 12px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      font-size: 13px;
      line-height: 1.55;
    }}
    th {{
      background: #eef3f0;
      font-size: 12px;
      color: #3c4a45;
      font-weight: 700;
    }}
    .level {{
      display: inline-flex;
      min-width: 38px;
      justify-content: center;
      border-radius: 999px;
      padding: 4px 8px;
      font-weight: 700;
      color: #fff;
      background: var(--accent);
    }}
    .P0 {{ background: var(--risk); }}
    .P1 {{ background: var(--warn); }}
    .P2 {{ background: #2458a6; }}
    .P3, .P4 {{ background: #667085; }}
    .note {{
      margin-top: 20px;
      color: var(--muted);
      font-size: 13px;
    }}
    .data-boundary {{
      border: 1px solid #f0c36b;
      border-radius: 8px;
      padding: 12px 14px;
      background: #fff8e6;
      color: #754f00;
      font-size: 13px;
    }}
    .coverage {{
      margin-top: 20px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 18px 20px;
    }}
    .coverage h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .coverage p, .coverage li {{ color: var(--muted); font-size: 13px; line-height: 1.65; }}
    .coverage li + li {{ margin-top: 10px; }}
    @media (max-width: 760px) {{
      header {{ display: block; }}
      .metric-grid {{ grid-template-columns: 1fr; }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>{escape(report_title)}</h1>
      <p class="subtitle">{escape(str(profile.get("company_name", state.company_id)))} · {escape(state.date_range.get("date_from", ""))} 至 {escape(state.date_range.get("date_to", ""))}</p>
    </div>
    <p class="subtitle">Run ID: {escape(state.run_id)}</p>
  </header>
  <p class="data-boundary">{escape(boundary_text)}</p>
  <section class="data-boundary">{scope_html}</section>
  <section class="metric-grid">
    <div class="metric"><span>政策数量</span><strong>{len(state.policy_documents)}</strong></div>
    <div class="metric"><span>影响判断</span><strong>{len(state.impact_assessments)}</strong></div>
    <div class="metric"><span>P0/P1</span><strong>{sum(1 for x in state.impact_assessments if x.get("impact_level") in {"P0", "P1"})}</strong></div>
  </section>
  <table>
    <thead>
      <tr>
        <th>等级</th>
        <th>政策</th>
        <th>原文</th>
        <th>适用性</th>
        <th>规则效力</th>
        <th>时间效力</th>
        <th>分数</th>
        <th>判断理由</th>
        <th>企业证据</th>
        <th>建议动作</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  {coverage_html}
  <p class="note">所有结论均来自 Company Wiki、政策原文和本次 run_artifact；用户纠正会进入本地记忆与待确认写回流程。</p>
</main>
</body>
</html>"""


class PublicationGateBlocked(RuntimeError):
    pass


def _assert_publication_allowed(
    state: PolicyImpactState,
    gate_engine: GateEngine,
) -> None:
    if not state.impact_assessments:
        if (
            state.source_manifest.get("no_updates")
            and state.source_manifest.get("collection_succeeded")
        ):
            source_hash = hashlib.sha256(
                json.dumps(
                    state.source_manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            decision = gate_engine.evaluate_publication(
                {
                    "publication_kind": "no_updates",
                    "run_id": state.run_id,
                    "claim_id": "",
                    "review_audit_ref": "",
                    "claim_snapshot_hash": "",
                    "evidence_refs": [f"source:{source_hash}"],
                    "no_updates": True,
                    "collection_succeeded": True,
                },
                span_id="generate_weekly_report:no_updates",
                attempt=state.stage_retry_counts.get("generate_weekly_report", 0) + 1,
            )
            if not decision.passed:
                raise PublicationGateBlocked(
                    "; ".join(decision.reasons) or "no-updates publication gate blocked"
                )
            return
        raise PublicationGateBlocked("publication requires reviewed claims or a successful no-updates collection")

    review_refs = state.review_result.get("claim_review_audit_refs") or {}
    attempt = state.stage_retry_counts.get("generate_weekly_report", 0) + 1
    for assessment in state.impact_assessments:
        claim_payload = claim_payload_from_assessment(assessment)
        claim_id = str(claim_payload.get("claim_id") or "unknown")
        evidence_refs = list(
            dict.fromkeys(
                [
                    *claim_payload.get("policy_evidence_refs", []),
                    *claim_payload.get("company_evidence_refs", []),
                ]
            )
        )
        decision = gate_engine.evaluate_publication(
            {
                "run_id": state.run_id,
                "claim_id": claim_id,
                "review_audit_ref": str(review_refs.get(claim_id) or ""),
                "claim_snapshot_hash": canonical_claim_snapshot_hash(claim_payload),
                "evidence_refs": evidence_refs,
            },
            span_id=f"generate_weekly_report:{claim_id}",
            attempt=attempt,
        )
        if not decision.passed:
            raise PublicationGateBlocked(
                f"{claim_id}: {'; '.join(decision.reasons) or 'publication gate blocked'}"
            )


def run(
    state: PolicyImpactState,
    gateway: ToolGateway | None = None,
    *,
    gate_engine: GateEngine,
) -> PolicyImpactState:
    state.report_paths = {}
    _assert_publication_allowed(state, gate_engine)
    scope = _report_scope(state)
    state.quality_metrics["policy_report_scope"] = scope
    if scope["completion_status"] == "partial":
        state.add_warning("generate_weekly_report", "政策报告部分完成，必须披露范围与核验限制", scope)
    gateway = gateway or ToolGateway()
    report = _render_report(state)
    run_suffix = state.run_id[:8]
    filename = f"{state.date_range['date_to']}-weekly-policy-impact-{run_suffix}.md"
    result = gateway.call("generate_weekly_report", "report_write", company_id=state.company_id, filename=filename, content=report)
    state.report_paths["report"] = result["path"]
    html_filename = f"{state.date_range['date_to']}-weekly-policy-impact-{run_suffix}.html"
    html_result = gateway.call(
        "generate_weekly_report",
        "report_write",
        company_id=state.company_id,
        filename=html_filename,
        content=_render_html_report(state),
    )
    state.report_paths["html_report"] = html_result["path"]
    state.report_session_id = f"report_{state.run_id}"
    store = PolicyMemoryStore(state.company_id)
    store.save_report_session(
        report_id=state.report_session_id,
        run_id=state.run_id,
        report_path=result["path"],
        metadata={
            "date_range": state.date_range,
            "assessment_count": len(state.impact_assessments),
            "html_report_path": html_result["path"],
        },
    )

    artifact_path = write_json_artifact(
        state,
        "run_artifact",
        state.to_dict(),
        f"data/companies/{state.company_id}/runs",
        f"{state.date_range['date_to']}-run_artifact.json",
    )
    state.report_paths["run_artifact"] = str(artifact_path)
    return state
