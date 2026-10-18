"""Bounded, source-preserving delivery when final claim review cannot converge."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from policy_impact.domains.finance.answer_audit import _is_markdown_table_divider
from policy_impact.domains.finance.article_gateway import ARTICLE_CITATION, _relative_article_path
from policy_impact.domains.finance.state import answer_audit_coverage


def independent_review_claimed(answer: str, *, full_coverage: bool = False) -> bool:
    """Detect explicit completed-review declarations, not missing disclosures."""
    clauses = re.split(r"[。！？!?;；\n]|\.\s+|\bbut\b|但是|但|不过", answer, flags=re.IGNORECASE)
    for clause in clauses:
        if full_coverage and not re.search(r"\b(?:all|every|fully|complete(?:ly)?)\b|全部|全面|所有", clause, re.IGNORECASE):
            continue
        for match in re.finditer(
            r"independently\s+(?:read|verified|reviewed|checked|confirmed)"
            r"|independent\s+(?:review|verification|check)\s+(?:(?:has|have)\s+been\s+|was\s+|is\s+)?completed"
            r"|独立(?:地)?(?:阅读|读取|核实|核验|复核|验证|查证)", clause, re.IGNORECASE,
        ):
            prefix, suffix = clause[:match.start()], clause[match.end():]
            if re.search(
                r"\b(?:not|never|without|cannot|can't|couldn't|didn't|wasn't|haven't|hasn't|"
                r"can|could|will|would|should|must|may|to)\s+(?:(?:yet|been|actually|successfully|fully)\s+)*$"
                r"|(?:未|没有|无法|未能|未经|尚无|并无)(?:完成|进行|实际|成功|了|的)*$", prefix, re.IGNORECASE,
            ):
                continue
            if match.group().startswith("独立"):
                if re.match(r"(?:尚未|未|没有|失败|不完整)", suffix):
                    continue
                if not re.search(r"(?:已|已经|完成|通过|成功)", prefix) and not suffix.startswith("了"):
                    continue
            return True
    return False


def research_citation_issue(output, read_refs, evidence_ledger):
    """Local provenance checks only; this does not perform semantic review."""
    from policy_impact.domains.finance.article_gateway import article_citation_issue

    answer = output.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return "no complete public answer"
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    refs = {ref for value in strings(output) for ref in re.findall(r"evidence_[A-Za-z0-9_]+", value)}
    unknown = sorted(ref for ref in refs
                     if evidence_ledger.get(ref) is None)
    if unknown:
        return "unobserved EvidenceRefs: " + ", ".join(unknown)
    citations = output.get("citations")
    issue = article_citation_issue(answer, read_refs, citations if isinstance(citations, list) else [])
    if issue and not issue.startswith("Cite at least one"):
        return issue
    return None


def _article_quote(issue: Mapping[str, Any], known: Mapping[str, dict[str, Any]]) -> str:
    source_quote = str(issue.get("source_quote") or "").strip()
    span = str(issue.get("span") or "")
    citations = list(ARTICLE_CITATION.finditer(span.replace("\\", "/")))
    for evidence_id in issue.get("evidence_refs") or []:
        source = known.get(str(evidence_id))
        if not source or source.get("source_type") != "author_article":
            continue
        excerpt = str(source.get("excerpt") or "")
        path = _relative_article_path(str(source.get("source_ref") or ""))
        if source_quote and source_quote in excerpt:
            line = next((int(match.group(1)) for match in re.finditer(
                r"(?m)^(\d+):\s*(.*)$", excerpt
            ) if source_quote in match.group(2)), source.get("line_start") or 1)
            return f"{source_quote} (`{path}:L{line}`)"
        for citation in citations:
            if not citation.group("start") or not path.endswith(citation.group("path")):
                continue
            line = int(citation.group("start"))
            if not int(source.get("line_start") or 1) <= line <= int(source.get("line_end") or 0):
                continue
            match = re.search(rf"(?m)^{line}:\s*(.+)$", excerpt)
            if match:
                return f"{match.group(1).strip()[:350]} (`{path}:L{line}`)"
    return ""


def observed_evidence_answer(bundle: Mapping[str, Any], *, reason: str) -> str:
    """Never turn unchecked model prose into a financial conclusion."""
    sources = bundle.get("evidence") or []
    market = []
    articles = []
    seen_articles = set()
    for item in sources:
        if item.get("source_type") == "market_metric" and len(market) < 8:
            try:
                metric = json.loads(str(item.get("excerpt") or "{}"))
            except json.JSONDecodeError:
                continue
            market.append(
                f"- {metric.get('symbol', '行情')}：{metric.get('start_date', '?')} 至 "
                f"{metric.get('end_date', '?')}，{metric.get('start_close', '?')} → "
                f"{metric.get('end_close', '?')}，区间变化 {metric.get('change_pct', '?')}" 
                f"（{metric.get('unit', '')}；来源：{item.get('source_url') or '本轮行情工具'}）。"
            )
        if item.get("source_type") == "author_article" and len(articles) < 8:
            article_id = str(item.get("article_id") or item.get("source_ref") or "")
            if article_id in seen_articles:
                continue
            seen_articles.add(article_id)
            lines = [entry for entry in str(item.get("excerpt") or "").splitlines()
                     if re.match(r"^\d+:\s*\S", entry)]
            candidates = []
            body_start = next((int(entry.split(":", 1)[0]) + 1 for entry in lines
                               if entry.split(":", 1)[1].strip() == "---"
                               and int(entry.split(":", 1)[0]) <= 15), 0)
            for entry in lines:
                number, content = entry.split(":", 1)
                content = content.strip()
                if re.match(r"^(?:#|!\[|---|(?:作者|发布日期|日期|来源|原文链接)\s*[:：])", content):
                    continue
                candidates.append((number, content))
            selected = next(((number, content) for number, content in candidates
                             if body_start and int(number) >= body_start and len(content) >= 35),
                            candidates[0] if candidates else None)
            if selected:
                number, content = selected
                path = _relative_article_path(str(item.get("source_ref") or ""))
                articles.append(
                    f"- {item.get('author') or '作者'}原文片段：{content[:250]} "
                    f"(`{path}:L{number}`)"
                )
    parts = [
        "## 本轮能够确认的材料",
        f"最终综合或完整审核尚未完成：{reason}。以下仅呈现本轮实际读取的材料，"
        "不把作者观点或价格同向变化当作因果证明。",
    ]
    if market:
        parts.extend(["### 行情观察", "\n".join(market)])
    if articles:
        parts.extend(["### 作者原文", "\n".join(articles)])
    parts.append("核心解释仍需逐条核对原文、时间条件及独立证据；本轮不能给出高可信度的因果结论。")
    return "\n\n".join(parts)


def bounded_audit_answer(
    candidate: str, audit: Mapping[str, Any] | None,
    bundle: Mapping[str, Any],
    *,
    preserve_candidate: bool = False,
) -> tuple[str, str]:
    """Deliver a source-preserving answer when final review cannot converge.

    The legacy bounded mode keeps only safely located material.  Finance chat now
    uses ``preserve_candidate=True``: the complete research draft remains visible,
    while the unresolved audit state is attached explicitly instead of silently
    replacing the analysis with a short source inventory.
    """
    if not audit:
        reason = str((audit or {}).get("limitations") or "审核未完成")[:300]
        if preserve_candidate and candidate.strip():
            return (
                "**审核状态：终稿审核未完成。** 以下保留主 Agent 的完整研究稿；"
                "其中未完成审核的内容不得视为已验证事实。"
                f" 审核缺口：{reason}\n\n{candidate.rstrip()}",
                "review_failed_full_draft",
            )
        answer = observed_evidence_answer(bundle, reason=reason)
        issues = list((audit or {}).get("issues") or [])
        if issues:
            known = {str(item.get("evidence_id")): item for item in bundle.get("evidence") or []}
            disputes = []
            for issue in issues[:6]:
                quote = _article_quote(issue, known)
                if quote:
                    disputes.append(
                        f"- 作者原文：{quote}\n  审核提出的反驳或保留理由（尚未独立裁定）："
                        f"{str(issue.get('reason') or '证据不足')[:600]}"
                    )
            if disputes:
                answer += "\n\n### 已定位的争议\n\n" + "\n\n".join(disputes)
        return answer, "source_only"
    audit_coverage = answer_audit_coverage(audit)
    coverage_complete = audit_coverage == "complete"
    issues = [issue for issue in audit.get("issues") or []
              if not _is_markdown_table_divider(str(issue.get("span") or ""))]
    execution_gaps = []
    if preserve_candidate:
        # Only Runtime-tagged diagnostics are execution gaps; unknown issues stay semantic.
        execution_gaps = [issue for issue in issues
                          if issue.get("issue_kind") == "review_execution_gap"]
        issues = [issue for issue in issues
                  if issue.get("issue_kind") != "review_execution_gap"]
    execution_note = ""
    if execution_gaps:
        notes = []
        for issue in execution_gaps:
            target = issue.get("target") or issue
            target_id = str(issue.get("target_id") or target.get("target_id") or "未指定目标")
            description = str(target.get("claim_or_question") or issue.get("span") or "")[:500]
            reason = str(issue.get("reason") or "未取得有效审核结果")[:600]
            notes.append(f"- 未完成目标 {target_id}：{description}\n  原因：{reason}")
        limitations = str(audit.get("limitations") or "").strip()
        execution_note = (
            "**审核未完成：以下目标未取得完整有效审核结果，相关内容不能视为已验证事实。**\n\n"
            + "\n".join(notes)
            + ("\n\n审核范围说明：" + limitations[:500] if limitations else "")
            + "\n\n"
        )
        if not issues:
            return (
                execution_note + candidate.rstrip(),
                "review_failed_full_draft" if audit_coverage == "failed" else "review_incomplete_full_draft",
            )
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for issue in issues:
        groups.setdefault(str(issue.get("span") or "").strip(), []).append(issue)
    if not issues and not coverage_complete:
        if preserve_candidate and candidate.strip():
            return (
                "**审核范围未完成：以下为完整研究稿，不是已全面核实的结论。** "
                f"审核缺口：{str(audit.get('limitations') or '未完成全部主张复核')[:500]}\n\n"
                + candidate.rstrip(),
                "review_incomplete_full_draft",
            )
        return observed_evidence_answer(
            bundle, reason=str(audit.get("limitations") or "Review did not cover all claims")[:300]
        ), "source_only"
    if not issues:
        if preserve_candidate:
            if coverage_complete:
                note = str(audit.get("limitations") or "").strip()
                return (candidate.rstrip() + ("\n\n**审核说明：** " + note if note else ""),
                        "review_complete")
            return (
                candidate.rstrip() + "\n\n**审核状态：部分审核结果未能形成完整覆盖，但原始研究稿已保留。**",
                "review_incomplete_full_draft",
            )
        return (candidate + "\n\n**审核保留：**审查器仅将表格分隔行误判为主张；该行已保留，"
                "不据此认定其他结论已获独立验证。"), "disputed_partial"
    safe = candidate
    if preserve_candidate:
        disputes = []
        for index, (span, related) in enumerate(groups.items(), 1):
            reason = "；".join(
                str(issue.get("reason") or "证据或归因不足")[:600]
                for issue in related
            )
            disputes.append(
                f"- 争议 {index}：{span[:500]}\n  审核理由（尚未独立裁定）：{reason}"
            )
        return (
            execution_note
            + "**审核状态：部分结论存在争议或证据不足。** 以下保留主 Agent 的完整研究稿，"
            "并列出审核提出的问题；作者观点不会因审核意见而被删除，审核意见本身也不等于独立事实裁决。\n\n"
            + "\n".join(disputes)
            + "\n\n"
            + candidate.rstrip(),
            "disputed_full_draft",
        )
    known = {str(item.get("evidence_id")): item for item in bundle.get("evidence") or []}
    if any(candidate.count(span) != 1 for span in groups if span):
        return observed_evidence_answer(bundle, reason="争议段落无法唯一定位"), "source_only"
    replacements: list[tuple[int, int, str]] = []
    table_notes: dict[int, list[str]] = {}
    for index, (span, related) in enumerate(groups.items(), 1):
        if not span or len(span) >= 500 or span not in safe:
            return observed_evidence_answer(bundle, reason="争议段落无法可靠定位"), "source_only"
        dispute = []
        seen_quotes = set()
        for issue in related:
            quote = _article_quote(issue, known)
            if quote and quote not in seen_quotes:
                dispute.append(f"**作者原文**：{quote}")
                seen_quotes.add(quote)
            reason = str(issue.get("reason") or "证据或归因不足")[:600]
            if reason not in seen_quotes:
                dispute.append(f"**审核提出的反驳或保留理由（尚未独立裁定）**：{reason}")
                seen_quotes.add(reason)
        detail = "；".join(dispute)
        start = candidate.index(span)
        end = start + len(span)
        if "\n" not in span and re.fullmatch(r"\s*\|.*\|\s*", span):
            cells = span.split("|")
            first = next((i for i in range(1, len(cells) - 1) if cells[i].strip()), None)
            if first is None:
                return observed_evidence_answer(bundle, reason="争议表格行无法定位主张"), "source_only"
            cells[first] = " 待核实的归纳：" + cells[first].strip() + f"（争议 {index}） "
            replacements.append((start, end, "|".join(cells)))
            table_end = candidate.find("\n", end)
            table_end = len(candidate) if table_end < 0 else table_end + 1
            while table_end < len(candidate):
                next_end = candidate.find("\n", table_end)
                next_end = len(candidate) if next_end < 0 else next_end + 1
                if not re.fullmatch(r"\s*\|.*\|\s*", candidate[table_end:next_end].strip()):
                    break
                table_end = next_end
            table_notes.setdefault(table_end, []).append(
                f"**争议 {index}（尚未裁定）**：表内归纳仅供对照，不是已核实事实。{detail}"
            )
        else:
            original = span.strip()
            if re.match(r"^\d+[.)、]\s", original):
                marker, original = re.match(r"^(\d+[.)、])\s*(.*)$", original).groups()
                lead = f"{marker} "
            elif re.match(r"^[-*]\s", original):
                lead, original = original[:2], original[2:]
            else:
                lead = ""
            replacements.append((start, end,
                f"{lead}**争议 {index}（尚未裁定）**：原稿归纳“{original}”尚不能作为已证实结论。"
                f"{detail}"
            ))
    for offset, notes in table_notes.items():
        replacements.append((offset, offset, "\n" + "\n\n".join(notes) + "\n"))
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        safe = safe[:start] + replacement + safe[end:]
    answer = (
        "**审核状态：部分结论仍有争议。** 争议已在相关位置标注，作者原文与审核理由并列；"
        "审核意见本身也可能有误，不代表独立事实裁决。\n\n"
        + safe.rstrip()
    )
    if not coverage_complete:
        answer = (
            "**审核范围未完成：以下为带争议的研究稿，不是已全面核实的结论。** "
            "已定位的争议见对应段落；其他未被审核覆盖的主张也不能据此视为已验证。"
            "审核缺口：" + str(audit.get("limitations") or "未完成全部主张复核")[:500]
            + "\n\n" + answer
        )
    return answer, "disputed_partial"


def audit_delivery_state(audit: Mapping[str, Any] | None) -> dict[str, Any]:
    """Review completion is not a certificate of external financial truth."""
    coverage = answer_audit_coverage(audit, default="failed")
    checks = list((audit or {}).get("checks") or [])
    issues = list((audit or {}).get("issues") or [])
    semantic_issues = [row for row in issues if row.get("issue_kind") != "review_execution_gap"]
    not_requested = (audit or {}).get("review_status") == "not_requested"
    semantic = "partial" if not_requested else (
        "complete" if coverage == "complete" else "partial" if checks else "failed"
    )
    verified = bool(checks) and coverage == "complete" and not issues and all(
        row.get("fact_verified") is True and row.get("status") == "supported"
        and (row.get("target") or {}).get("type") == "objective_fact"
        for row in checks
    )
    verification = "verified" if verified else (
        "disputed" if any(row.get("status") == "refuted" for row in checks) or semantic_issues
        else "failed" if semantic == "failed" else "partial"
    )
    return {
        "answer_audit_coverage": coverage, "semantic_review": semantic,
        "verification_status": verification,
        "review_status": "not_requested" if not_requested else semantic,
        "open_gaps": list(dict.fromkeys([
            *([(audit or {}).get("limitations")] if (audit or {}).get("limitations") else []),
            *[str(row.get("reason") or "Unresolved target review") for row in issues],
        ])),
    }
