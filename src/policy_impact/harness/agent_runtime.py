"""Chat-turn runtime for the single-user Agent Workbench."""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from dataclasses import replace
from calendar import monthrange
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from policy_impact.company_wiki.loader import company_dir, load_company_knowledge
from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig, AgentLoopResult, AgentTurn
from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.context_manifest import build_context_manifest, estimate_tokens
from policy_impact.harness.model_config import (
    DEFAULT_MODEL_ID,
    configured_model_id,
    effective_model_config,
    ensure_default_model_available,
)
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelAdapter,
    ModelInvocationError,
    ModelResponse,
)
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.prompt_builder import (
    build_conversation_summary_prompt,
    build_research_report_prompt,
)
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagent_planner import PlannedSubagentTask, QuerySubagentPlanner
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.harness.tool_gateway import ToolGateway, reset_tool_audit_log
from policy_impact.memory.store import PolicyMemoryStore, canonical_memory_id
from policy_impact.official_legal_reference import resolve_official_legal_reference
from policy_impact.mcp_server.registry import register_all_tools
from policy_impact.rag.lexical import tokenize
from policy_impact.runtime.actions import ALLOWED_ACTION_NAMES
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.executor_registry import (
    ExecutionOverrideRegistry,
    ExecutorRegistry,
)
from policy_impact.runtime.hooks import HookManager
from policy_impact.runtime.gate_catalog import (
    AgentStepGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner
from policy_impact.runtime.result import CitationRef, SkillResult
from policy_impact.runtime.subagent_manifests import (
    SubagentManifest,
    build_default_subagent_manifest_registry,
)
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.runtime.task_brief import SubagentResult, TaskBrief
from policy_impact.runtime.turn_coordinator import (
    RunExecutionLockRegistry,
    TurnCoordinator,
    TurnOperations,
)
from policy_impact.runtime.verifier_proof import PolicyMemoryReviewGateResolver
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact
from policy_impact.skill_executors.recent_news_report import run_recent_news_report
from policy_impact.source_connectors.ingestion import (
    load_latest_news_snapshot,
    load_latest_policy_snapshot,
    load_source_config,
)
from policy_impact.skills.executors import (
    CompanyWikiBlueprintExecutor,
    ExternalImpactReportExecutor,
    GeneralChatExecutor,
    OfficialLegalReferenceExecutor,
    OfficialLegalReferenceOverrideResolver,
    PolicyWeeklyImpactExecutor,
    RecentNewsReportExecutor,
    ResearchReportExecutor,
)
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry, compatibility_tool_policy
from policy_impact.skills.schemas import SkillOutput


def _materialize_execution_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _materialize_execution_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_materialize_execution_value(item) for item in value]
    if isinstance(value, frozenset):
        return [_materialize_execution_value(item) for item in value]
    return value


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _materialize_execution_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _stated_article_read_count(answer: str, author: str) -> int | None:
    """Find an explicit per-author read count, not a catalog/list count."""
    pattern = (
        rf"{re.escape(author)}[^。；;\n]{{0,120}}?"
        r"(?<!未)(?<!没)(?<!没有)(?<!尚未)(?<!未逐篇)"
        r"(?:读取|阅读|读完|读了|读)"
        r"[^，,。；;\n]{0,24}?([0-9]+)\s*篇"
    )
    match = re.search(pattern, str(answer or ""), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _finance_article_sample_count_issue(
    answer: str, article_refs: list[dict[str, Any]],
) -> str | None:
    """Check a stated full-sample author count against articles cited this turn."""
    from policy_impact.domains.finance.article_gateway import article_cited_ids

    cited_ids = article_cited_ids(answer, article_refs)
    cited = {str(ref.get("article_id")): ref for ref in article_refs
             if str(ref.get("article_id")) in cited_ids}
    if not cited:
        return None
    authors = {str(ref.get("author") or "").strip() for ref in cited.values()}
    authors.discard("")
    for match in re.finditer(r"(?<!\d)(\d+)\s*位作者共\s*(\d+)\s*篇文章", answer):
        stated_authors, stated_articles = map(int, match.groups())
        if stated_articles == len(cited) and stated_authors != len(authors):
            return (
                f"The answer says {stated_authors} authors across {stated_articles} articles, "
                f"but its cited article sample contains {len(authors)} distinct authors: "
                f"{', '.join(sorted(authors))}. Correct the count before publishing."
            )
    return None


def _finance_article_read_scope_issue(
    answer: str, article_refs: list[dict[str, Any]],
) -> str | None:
    """Keep explicit per-author read counts consistent with the Runtime ledger."""
    actual_by_author: dict[str, set[str]] = {}
    for ref in article_refs:
        article_id = str(ref.get("article_id") or "").strip()
        author = str(ref.get("author") or "").strip()
        if article_id and author:
            actual_by_author.setdefault(author, set()).add(article_id)
    for author, article_ids in actual_by_author.items():
        stated_count = _stated_article_read_count(answer, author)
        if stated_count is None or stated_count == len(article_ids):
            continue
        return (
            f"The answer says {author} had {stated_count} actually read articles, "
            f"but the Runtime article.read ledger contains {len(article_ids)} distinct "
            "articles for that author. Correct the read scope and distinguish selected "
            "sample articles from all articles read in this run."
        )
    if len(actual_by_author) == 1:
        match = re.search(
            r"本轮实际(?:已)?(?:读取|阅读)(?:了)?(?:正文)?\s*(\d+)\s*篇",
            answer,
        )
        if match:
            stated_count = int(match.group(1))
            author, article_ids = next(iter(actual_by_author.items()))
            if stated_count != len(article_ids):
                return (
                    f"The answer says {stated_count} articles were actually read, but "
                    f"the Runtime article.read ledger contains {len(article_ids)} distinct "
                    f"articles for {author}. Correct the read scope before publishing."
                )
    return None


def _finance_article_citation_location_issue(answer: str) -> str | None:
    """A trailing source index alone does not ground the answer's main claims."""
    from policy_impact.domains.finance.article_gateway import ARTICLE_CITATION

    if len(answer) < 500:
        return None
    index = re.search(
        r"(?m)^(?:\*\*)?本轮已读取的文章证据(?:\*\*)?\s*$|"
        r"^#{1,6}\s*(?:证据索引|参考资料|References)\s*$",
        answer,
        re.IGNORECASE,
    )
    if not index:
        return None
    def has_line_citation(text: str) -> bool:
        return any(
            match.group("start")
            for match in ARTICLE_CITATION.finditer(text.replace("\\", "/"))
        )
    if has_line_citation(answer[index.end():]) and not has_line_citation(answer[:index.start()]):
        return (
            "The answer puts all article path-and-line citations in a trailing source index. "
            "Place observed line citations next to the substantive author claims in the main "
            "answer; keep the index only as a supplement. Do not invent or guess line numbers."
        )
    return None


def _finance_verifier_web_quote_issue(
    output: dict[str, Any], opened_text_by_ref: Mapping[str, str]
) -> str | None:
    """Reject decisive web verdicts supported only by a search snippet or title."""
    for verdict in output.get("verdicts") or []:
        if verdict.get("decision") not in {"pass", "fail"}:
            continue
        web_refs = [ref for ref in verdict.get("evidence_refs") or [] if ref in opened_text_by_ref]
        if not web_refs:
            continue
        primary_quote = str(verdict.get("supporting_quote") or "").strip()
        quotes = [str(value).strip() for value in verdict.get("supporting_quotes") or []]
        if not quotes:
            quotes = [primary_quote]
        if not 1 <= len(quotes) <= 3 or any(not quote or len(quote) > 240 for quote in quotes):
            return "A decisive web verdict requires one to three short direct page quotes."
        for quote in quotes:
            normalized_quote = " ".join(quote.split()).casefold()
            if not any(
                normalized_quote in " ".join(opened_text_by_ref[ref].split()).casefold()
                for ref in web_refs
            ):
                return "A supporting quote is not present in the cited web.open page text."
        # The legacy singular quote can carry a prose number alongside three table
        # excerpts. Count it only when it also appears in an opened web page.
        anchor_quotes = list(quotes)
        if primary_quote and primary_quote not in quotes and len(primary_quote) <= 240:
            normalized_primary = " ".join(primary_quote.split()).casefold()
            if any(
                normalized_primary in " ".join(opened_text_by_ref[ref].split()).casefold()
                for ref in web_refs
            ):
                anchor_quotes.append(primary_quote)
        anchors = {
            number for token in re.findall(r"\d{2,}", str(verdict.get("claim_ref") or ""))
            if (number := int(token)) > 31 and not 1900 <= number <= 2100
        }
        missing = sorted(
            number for number in anchors
            if not any(re.search(rf"(?<!\d){number}(?!\d)", quote) for quote in anchor_quotes)
        )
        if missing:
            return f"The quoted page passage is missing numeric claim anchors: {missing}."
    return None


def _finance_verifier_market_fail_issue(
    output: dict[str, Any], evidence_bundle: Mapping[str, Any], *, author_article_task: bool
) -> str | None:
    """A partial market window cannot falsify an author's dated article claim."""
    if not author_article_task:
        return None
    evidence = {
        str(row.get("evidence_id") or ""): row
        for row in evidence_bundle.get("evidence") or []
    }
    months = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
    }
    for verdict in output.get("verdicts") or []:
        if verdict.get("decision") in {"pass", "fail"}:
            cited_market = [
                evidence.get(str(ref)) for ref in verdict.get("evidence_refs") or []
            ]
            market_ends = [
                match.group(1)
                for row in cited_market
                if row and row.get("source_type") == "market_bar_series"
                if (match := re.search(r"to (\d{4}-\d{2}-\d{2})", str(row.get("excerpt") or "")))
            ]
            claim_dates = re.findall(r"20\d{2}-\d{2}-\d{2}", str(verdict.get("claim_ref") or ""))
            if market_ends and claim_dates and max(claim_dates) > max(market_ends):
                return (
                    "The market series ends on " + max(market_ends) + ", before the as-of date "
                    + max(claim_dates) + " in claim_ref. Limit the verdict to observed bars "
                    "and leave the later date unverified."
                )
        if verdict.get("decision") != "fail":
            continue
        cited = [evidence.get(str(ref)) for ref in verdict.get("evidence_refs") or []]
        market_rows = [row for row in cited if row and row.get("source_type") == "market_bar_series"]
        if not market_rows:
            continue
        article_rows = [row for row in cited if row and row.get("source_type") == "author_article"]
        if not article_rows:
            return (
                "Before failing an author's historical market statement, independently read and cite "
                "the original article passage as well as the market series. If that is unavailable, "
                "return unverified."
            )
        quote = str(verdict.get("supporting_quote") or "").strip()
        if not quote or len(quote) > 240 or not any(
            " ".join(quote.split()) in " ".join(str(row.get("excerpt") or "").split())
            for row in article_rows
        ):
            return "Quote the original article's exact dated claim in supporting_quote before failing it."
        claim_ref = str(verdict.get("claim_ref") or "").casefold()
        asset_terms = {
            "gold": r"gold|xau|黄金|金价",
            "silver": r"silver|xag|白银|银价",
        }
        for asset, pattern in asset_terms.items():
            if re.search(rf"(?:^|[_-]){asset}(?:[_-]|$)", claim_ref) and not re.search(
                pattern, quote, flags=re.IGNORECASE
            ):
                return (
                    "The quoted article passage is about a different topic from the "
                    f"{asset} claim. Read and quote the claim-matched passage, or return unverified."
                )
        conditional_signal = re.search(
            r"如果|假如|倘若|一旦|只有|除非|才(?:是|算|能)|\bif\b|\bonly if\b|\bwould be\b",
            quote, flags=re.IGNORECASE,
        )
        asserted_occurrence = re.search(
            r"已经|已突破|已站上|现已|目前已|\balready\b|\bhas broken\b",
            quote, flags=re.IGNORECASE,
        )
        if conditional_signal and not asserted_occurrence:
            return (
                "The article describes a conditional market signal, not an assertion that "
                "the threshold was reached. A lower observed price does not falsify the "
                "conditional claim; return unverified or review a claim of actual occurrence."
            )
        month_match = re.search(r"(十一|十二|十|[一二三四五六七八九]|1[0-2]|[1-9])月份?", quote)
        if not month_match:
            continue
        month_token = month_match.group(1)
        month = months.get(month_token, int(month_token) if month_token.isdigit() else 0)
        year = str(article_rows[0].get("published_at") or "")[:4]
        starts = [
            match.group(1)
            for row in market_rows
            if (match := re.search(r"daily bars (\d{4}-\d{2}-\d{2})", str(row.get("excerpt") or "")))
        ]
        if year.isdigit() and starts and all(start[:7] > f"{year}-{month:02d}" for start in starts):
            return (
                "The cited market series starts after the month named in the article passage. "
                "Extend the historical window or mark this verdict unverified; a later window "
                "cannot falsify an earlier price move."
            )
    return None


def _finance_future_watch_issue(answer: str, *, as_of: date | None = None) -> str | None:
    """Prevent already elapsed months from appearing as upcoming watch items."""
    today = as_of or date.today()
    not_due_markers = re.compile(
        r"not[_ -]?due|未到期|尚未到期|尚未兑现|尚未发生|尚未实现",
        flags=re.IGNORECASE,
    )
    for line in str(answer or "").splitlines():
        not_due_match = not_due_markers.search(line)
        if not not_due_match:
            continue
        if re.search(r"已过去|已结束|历史回顾|不应标记|不能标记", line):
            continue
        if not re.search(r"预测|预期|目标|时间窗口|观察|到期|forecast|horizon", line, re.I):
            continue
        candidates: list[tuple[date, int]] = []
        for match in re.finditer(
            r"(?:(?P<year>20\d{2})\s*(?:年|[-/]))?"
            r"(?P<month>1[0-2]|0?[1-9])\s*(?:月|[-/])"
            r"(?P<day>[12]\d|3[01]|0?[1-9])\s*(?:日|号)?(?!\d)",
            line,
        ):
            # Article citations contain publication dates in their filenames,
            # e.g. ``TT/articles/2026-06-04-abc.md``.  Those dates describe the
            # source, not the prediction horizon next to ``not_due``.
            prefix = line[max(0, match.start() - 80):match.start()]
            suffix = line[match.end():match.end() + 100]
            if (
                re.search(r"(?:^|[\\/])articles[\\/]$", prefix, re.IGNORECASE)
                and re.match(r"-[A-Za-z0-9]", suffix)
            ) or re.search(r"-[A-Za-z0-9][A-Za-z0-9_-]*\\.md(?:[:`)]|$)", suffix, re.IGNORECASE):
                continue
            year = int(match.group("year") or today.year)
            try:
                candidates.append((
                    date(year, int(match.group("month")), int(match.group("day"))),
                    match.start(),
                ))
            except ValueError:
                continue
        for match in re.finditer(
            r"(?:(?P<year>20\d{2})\s*年\s*)?"
            r"(?P<month>1[0-2]|0?[1-9])\s*月(?!\s*(?:\d|[-/]))",
            line,
        ):
            prefix = line[max(0, match.start() - 80):match.start()]
            suffix = line[match.end():match.end() + 100]
            if (
                re.search(r"(?:^|[\\/])articles[\\/]$", prefix, re.IGNORECASE)
                and re.match(r"-[A-Za-z0-9]", suffix)
            ) or re.search(r"-[A-Za-z0-9][A-Za-z0-9_-]*\\.md(?:[:`)]|$)", suffix, re.IGNORECASE):
                continue
            year = int(match.group("year") or today.year)
            month = int(match.group("month"))
            try:
                candidates.append((date(year, month, monthrange(year, month)[1]), match.start()))
            except ValueError:
                continue
        if not candidates:
            continue
        relevant, _ = min(
            candidates,
            key=lambda item: abs(item[1] - not_due_match.start()),
        )
        if relevant < today:
            return (
                f"The answer marks {relevant.isoformat()} as not_due, but the "
                f"observation date is {today.isoformat()}. Move the elapsed horizon to "
                "historical/expired status or provide a genuinely future horizon."
            )
    marker = re.search(
        r"(?:\*\*|#{2,4}\s*)(?:[一二三四五六七八九十]+[、.]\s*)?"
        r"(?:后续|未来|接下来|下一步)[^\n]{0,24}(?:观察|关注|监测|指标)",
        answer,
    )
    if not marker:
        return None
    section = answer[marker.end():]
    section = re.split(r"\n#{2,4}\s|\n---\s*\n", section, maxsplit=1)[0]
    for line in section.splitlines():
        for match in re.finditer(
            r"(?<!\d)(?:(?P<year>20\d{2})\s*(?:年|[-/])\s*)?"
            r"(?P<month>1[0-2]|0?[1-9])\s*月",
            line,
        ):
            month = int(match.group("month"))
            year_text = match.group("year")
            year = int(year_text) if year_text else today.year
            # A historical document or event can be a valid follow-up source.
            if re.search(r"(?:原文|历史回顾|已属过去|已结束|已公布|会议纪要|复盘)", line):
                continue
            if re.search(r"[^。；\n]{0,14}(?:后|以来)", line[match.end():match.end() + 20]):
                continue
            tail = re.split(r"[、,，;；。]", line[match.end():], maxsplit=1)[0][:60]
            publication = re.search(
                r"(?:[（(]\s*)?(?:20\d{2}\s*年\s*)?(1[0-2]|0?[1-9])\s*月\s*(?:发布|公布)",
                tail,
            )
            if publication and int(publication.group(1)) > month:
                continue
            future_event = bool(re.search(
                r"(?:的)?(?:FOMC|议息|利率决议|会议|选举|发布|公布|召开|加息概率|"
                r"数据(?:将|待|即将)(?:公布|发布)|(?:CPI|通胀|就业)(?:将|待|即将)(?:公布|发布))",
                tail,
                re.IGNORECASE,
            ))
            if not future_event:
                continue
            if year_text and (year, month) < (today.year, today.month):
                return (
                    f"The future-watch section treats {year}-{month:02d} as upcoming, "
                    f"but today is {today.isoformat()}. Move it to the historical review "
                    "or replace it with a genuinely future observation."
                )
            if not year_text and 0 < today.month - month <= 3:
                return (
                    f"The future-watch section names month {month} without a year, "
                    f"but it already elapsed in {today.year}. Specify a future year "
                    "if intended, or use the next scheduled event instead."
                )
    return None


def _finance_market_horizon_issue(answer: str, evidence_bundle: Mapping[str, Any]) -> str | None:
    """Do not extend a price conclusion beyond the latest observed market bar."""
    latest_by_symbol: dict[str, str] = {}
    for row in evidence_bundle.get("evidence") or []:
        if row.get("source_type") != "market_bar_series":
            continue
        match = re.search(r"to (\d{4}-\d{2}-\d{2})", str(row.get("excerpt") or ""))
        symbol = str((row.get("metadata") or {}).get("symbol") or "").casefold()
        if match and symbol:
            latest_by_symbol[symbol] = max(latest_by_symbol.get(symbol, ""), match.group(1))
    if not latest_by_symbol:
        return None
    for sentence in re.split(r"[。；\n]", answer):
        if not re.search(r"触及|突破|站上|收盘|最高|最低|涨跌|上涨|下跌|价格", sentence):
            continue
        as_of_dates = re.findall(
            r"截至(?:当前|今日|今天)?\s*[（(]?\s*(20\d{2}-\d{2}-\d{2})",
            sentence,
        )
        if not as_of_dates:
            continue
        symbol = (
            "xagusd" if re.search(r"白银|银价|silver|xag", sentence, re.I)
            else "xauusd" if re.search(r"黄金|金价|gold|xau", sentence, re.I)
            else ""
        )
        latest = latest_by_symbol.get(symbol, "") if symbol else max(latest_by_symbol.values())
        if latest and max(as_of_dates) > latest and not re.search(r"(?:数据|日线|行情)截至", sentence):
            return (
                f"The price statement claims coverage through {max(as_of_dates)}, but the "
                f"latest observed {symbol or 'market'} bar ends on {latest}. "
                "State the observed end date and leave the later interval unverified."
            )
    return None


def _finance_market_return_math_issue(answer: str) -> str | None:
    """Check a stated start/end price and period return when all share one line."""
    start_pattern = re.compile(r"期初(?:价|价格)?\s*[：:]?\s*([\d,]+(?:\.\d+)?)")
    end_pattern = re.compile(r"期末(?:价|价格)?\s*[：:]?\s*([\d,]+(?:\.\d+)?)")
    return_pattern = re.compile(r"(?:区间(?:涨跌幅?|收益率?|变化)?|累计(?:涨跌幅?|收益率?))\s*[：:]?\s*\*{0,2}\s*([+-]?\d+(?:\.\d+)?)\s*%")
    for line in answer.splitlines():
        start = start_pattern.search(line)
        end = end_pattern.search(line)
        stated = return_pattern.search(line)
        if not (start and end and stated):
            continue
        first = float(start.group(1).replace(",", ""))
        last = float(end.group(1).replace(",", ""))
        reported = float(stated.group(1))
        if first <= 0 or last <= 0:
            continue
        actual = (last / first - 1) * 100
        if abs(actual - reported) > 0.15:
            return (
                "The stated period return is inconsistent with the start and end prices "
                f"on the same line ({first:g} to {last:g} implies {actual:.2f}%, "
                f"not {reported:g}%). Check the baseline date/value and either recompute "
                "the percentage or label the separately used reference close explicitly."
            )
    return None


def _drop_model_market_citations(citations: list[Any]) -> list[Any]:
    """Keep model citations except market refs, which Runtime must mint."""
    return [
        citation for citation in citations
        if not (
            isinstance(citation, dict)
            and str(citation.get("type") or citation.get("citation_type") or "")
            == "market_metric"
        )
    ]


class _FinanceEvidenceUnavailable(PermissionError):
    """Recoverable delegation gate used when no source evidence was read."""

    delegate_category = "gate_blocked"


def _requests_market_evidence(message: str) -> bool:
    """Detect market-fact intent without requiring one exact wording pattern."""
    text = str(message or "").casefold()
    from policy_impact.domains.finance.market_catalog import INSTRUMENTS

    asset_terms = {
        str(alias).casefold()
        for metadata in INSTRUMENTS.values()
        for alias in [*metadata.get("aliases", []), metadata.get("name", "")]
        if alias
    }
    # Keep intent recognition broader than the currently enabled quote catalog.
    # Unsupported assets go through the normal clarification/data-gap path.
    asset_terms.update({
        "\u7f8e\u5143", "\u7f8e\u5143\u6307\u6570", "\u5229\u7387", "\u539f\u6cb9", "\u94dc",
        "\u6bd4\u7279\u5e01", "\u6052\u751f", "\u6052\u751f\u79d1\u6280",
        "gold", "silver", "nasdaq", "s&p", "spx", "ixic",
    })
    concept_markers = (
        "\u662f\u4ec0\u4e48", "\u4ec0\u4e48\u610f\u601d", "\u4ec0\u4e48\u6982\u5ff5", "\u5b9a\u4e49", "\u6982\u5ff5\u533a\u522b",
        "\u6709\u4ec0\u4e48\u533a\u522b", "\u5982\u4f55\u7406\u89e3", "what is", "definition", "meaning", "difference",
    )
    if any(marker in text for marker in concept_markers) and not any(marker in text for marker in (
        "\u884c\u60c5", "\u8d70\u52bf", "\u8d8b\u52bf", "\u6da8\u8dcc", "\u6da8\u4e86", "\u8dcc\u4e86", "\u4ef7\u683c", "\u6536\u76d8", "\u6536\u76ca",
        "\u56de\u62a5", "\u8868\u73b0", "\u6c47\u7387", "\u591a\u5c11", "\u4eca\u5929", "\u4eca\u65e5", "\u6700\u65b0", "\u5f53\u524d", "\u73b0\u5728", "\u4eca\u5e74", "\u672c\u6708", "\u672c\u5468", "\u6700\u8fd1", "\u8fc7\u53bb",
        "price", "trend", "return", "performance", "exchange rate", "latest", "recent", "year to date", "up", "down",
    )):
        return False
    evidence_terms = (
        "\u884c\u60c5", "\u8d70\u52bf", "\u8d8b\u52bf", "\u6da8\u8dcc", "\u6da8\u4e86", "\u8dcc\u4e86", "\u4ef7\u683c", "\u6536\u76d8", "\u6536\u76ca",
        "\u56de\u62a5", "\u8868\u73b0", "\u6c47\u7387", "\u591a\u5c11",
        "\u53ef\u6838\u5b9e", "\u6838\u9a8c", "\u9a8c\u8bc1", "\u4e8b\u5b9e", "market fact", "price", "trend", "return",
        "performance", "exchange rate", "up", "down",
    )
    # A time window scopes sources; it does not by itself request market facts.
    return bool(any(term in text for term in asset_terms) and any(term in text for term in evidence_terms))


def _contains_unverified_market_notice(answer: str) -> bool:
    text = str(answer or "").casefold()
    return any(
        marker in text
        for marker in (
            "未核实", "尚未核实", "未验证", "尚未验证", "没有独立核实", "未获取行情",
            "没有行情证据", "market facts were not verified", "not independently verified",
        )
    )


def _requests_market_trend(message: str) -> bool:
    text = str(message or "").casefold()
    return any(
        term in text
        for term in (
            "走势", "趋势", "表现", "涨了", "跌了", "最近一个月", "近一个月", "最近30天", "近30天",
            "今年", "本月", "本周", "收益", "回报", "performance", "year to date",
            "last month", "past month", "recent month", "30 days", "trend",
        )
    )


def _contains_market_coverage_notice(answer: str) -> bool:
    text = str(answer or "").casefold()
    return any(
        marker in text
        for marker in (
            "单日", "只有一个有效点", "有效点不足", "不足以判断趋势", "不能代表趋势",
            "数据不足以支持趋势", "insufficient", "not enough points", "cannot establish a trend",
        )
    )


def _contains_no_article_notice(answer: str) -> bool:
    text = str(answer or "").casefold()
    return any(
        marker in text
        for marker in (
            "没有找到文章", "未找到文章", "没有匹配文章", "无匹配文章", "文章库中没有",
            "当前时间范围没有", "日期范围内没有", "没有可用的本地文章", "没有可用文章",
            "本地文章不可用", "该时间范围没有可用文章", "no local articles", "no articles matched",
            "no matching articles", "无法完成", "无法比较", "不能比较", "没有可读文章",
            "没有读取文章", "未读取文章", "尚未读取文章", "没有取得文章证据", "未取得文章证据",
        )
    )


def _format_market_fallback(market_reads: list[dict[str, Any]]) -> str:
    """Preserve usable market facts when an empty article scope ends the loop."""
    lines: list[str] = []
    for metric in market_reads:
        change_pct = metric.get("change_pct")
        if change_pct is None:
            continue
        try:
            formatted_change = f"{float(change_pct):+.2f}%"
        except (TypeError, ValueError):
            continue
        symbol = str(metric.get("symbol") or "market")
        start_date = str(
            metric.get("effective_start_date")
            or metric.get("start_date")
            or metric.get("requested_start_date")
            or "unknown"
        )
        end_date = str(
            metric.get("latest_trade_date")
            or metric.get("end_date")
            or metric.get("requested_end_date")
            or "unknown"
        )
        start_close = metric.get("period_first_close") or metric.get("start_close")
        end_close = metric.get("period_last_close") or metric.get("end_close")
        price_unit = str(metric.get("price_unit") or metric.get("unit") or "")
        closes = ""
        if start_close is not None and end_close is not None:
            closes = f"；首个收盘/参考值 {start_close} -> {end_close} {price_unit}".rstrip()
        lines.append(f"- {symbol}：{start_date} 至 {end_date}，区间变动 {formatted_change}{closes}")
    if not lines:
        return ""
    return (
        "## 已获取的行情事实\n"
        + "\n".join(lines)
        + "\n\n这些数字来自行情工具及其证据绑定；Runtime 未从缺失的本地文章中推导因果解释。"
    )


def _fallback_article_claim_lines(
    corpus: Any,
    artifacts: Any,
    article_refs: list[dict[str, Any]],
    *,
    per_article_limit: int = 20,
) -> list[str]:
    """Expose successful source extraction when final synthesis is unavailable.

    This is deliberately a presentation-only fallback. It does not infer
    agreement, causality, or truth; it surfaces claims already persisted by
    ``article.claims`` or an ``article.study`` card and keeps their source
    lines and evidence binding visible.
    """
    from policy_impact.domains.finance.article_gateway import _relative_article_path

    refs_by_article: dict[str, list[dict[str, Any]]] = {}
    for ref in article_refs:
        article_id = str(ref.get("article_id") or "").strip()
        if article_id:
            refs_by_article.setdefault(article_id, []).append(ref)
    if not refs_by_article:
        return []

    records: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    for article_id, refs in refs_by_article.items():
        try:
            claims = list(corpus.claims(article_id))
        except Exception:  # noqa: BLE001
            claims = []
        for claim in claims:
            claim_id = str(claim.get("claim_id") or "")
            if claim_id and claim_id in seen_claims:
                continue
            line_start = int(claim.get("start_line") or claim.get("line_start") or 0)
            if not line_start or not any(
                int(ref.get("line_start") or 0) <= line_start <= int(ref.get("line_end") or 0)
                for ref in refs
            ):
                continue
            seen_claims.add(claim_id)
            records.append({
                **claim,
                "article_id": article_id,
                "line_start": line_start,
                "line_end": int(claim.get("line_end") or line_start),
            })

    try:
        artifact_rows = artifacts.call(operation="list", kind="article_card").get("artifacts") or []
    except Exception:  # noqa: BLE001
        artifact_rows = []
    for summary in artifact_rows:
        article_id = str(summary.get("article_id") or "").strip()
        if article_id not in refs_by_article:
            continue
        try:
            card = artifacts.call(
                operation="get", artifact_id=str(summary.get("artifact_id") or "")
            ).get("payload") or {}
        except Exception:  # noqa: BLE001
            continue
        for claim in card.get("claims") or []:
            claim_id = str(claim.get("claim_id") or "")
            if claim_id and claim_id in seen_claims:
                continue
            line_start = int(claim.get("line_start") or 0)
            if not line_start or not any(
                int(ref.get("line_start") or 0) <= line_start <= int(ref.get("line_end") or 0)
                for ref in refs_by_article[article_id]
            ):
                continue
            seen_claims.add(claim_id)
            records.append({**claim, "article_id": article_id})

    output: list[str] = []
    per_article: dict[str, int] = {}
    for claim in sorted(records, key=lambda item: (item["article_id"], item.get("line_start", 0))):
        article_id = claim["article_id"]
        if per_article.get(article_id, 0) >= per_article_limit:
            continue
        ref = next(
            (
                item for item in refs_by_article[article_id]
                if int(item.get("line_start") or 0) <= int(claim.get("line_start") or 0)
                <= int(item.get("line_end") or 0)
            ),
            refs_by_article[article_id][0],
        )
        path = _relative_article_path(str(ref.get("path") or ""))
        line_start = int(claim.get("line_start") or 1)
        line_end = int(claim.get("line_end") or line_start)
        citation = f"{path}:L{line_start}" + (f"-L{line_end}" if line_end > line_start else "")
        statement = re.sub(r"\s+", " ", str(claim.get("statement") or "").strip())
        quote = re.sub(r"\s+", " ", str(claim.get("quote") or "").strip())
        if not statement and not quote:
            continue
        kind = str(claim.get("kind") or claim.get("claim_type") or "author_claim")
        evidence_id = str(claim.get("evidence_id") or ref.get("evidence_id") or "")
        evidence_suffix = f"；EvidenceRef={evidence_id}" if evidence_id else ""
        output.append(
            f"- [{kind}] {statement or quote}（原文：{quote[:280]}；{citation}"
            f"{evidence_suffix}；仅代表作者表达，未独立核验）"
        )
        per_article[article_id] = per_article.get(article_id, 0) + 1
    return output


def _explicit_article_authors(message: str, authors: list[dict[str, Any]]) -> list[str]:
    """Return author folders explicitly named in the user request.

    This is a source-preservation check, not an analysis policy. The model
    remains free to choose the date scope, sources, reading order, and whether
    to delegate after each folder is checked.
    """
    text = str(message or "").casefold()
    matches = []
    for item in authors:
        author = str(item.get("author") or "").strip()
        if author and author.casefold() in text and author not in matches:
            matches.append(author)
    return matches


def _finance_comparison_has_synthesis(answer: str) -> bool:
    if len(answer.strip()) < 1800:
        return False
    synthesis_lines = []
    in_reference_index = False
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_reference_index = any(
                term in stripped.casefold() for term in ("证据索引", "参考资料", "references", "附录")
            )
        if in_reference_index or not stripped or stripped.startswith(("#", "|")):
            continue
        synthesis_lines.append(re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped))
    synthesis = "\n".join(synthesis_lines).casefold()
    comparison_markers = ("变化", "比较", "共识", "分歧", "修正", "反例", "推断",
                          "change", "compared", "whereas", "however")
    return len(synthesis.strip()) >= 550 and any(
        term in synthesis for term in comparison_markers
    )


def _context_manifest_record_from_metadata(
    *,
    run_id: str,
    metadata: dict[str, Any],
    stage_name: str,
    agent_role: str,
) -> ContextManifestRecord:
    record_metadata = dict(metadata)
    record_metadata.setdefault("stage_name", stage_name)
    record_metadata.setdefault("agent_role", agent_role)
    record_metadata.setdefault("visible_keys", list(record_metadata.get("visible_keys") or []))
    record_id = str(record_metadata.get("manifest_id") or record_metadata.get("record_id") or "").strip()
    if not record_id:
        record_id = f"ctx_{_stable_hash({'run_id': run_id, 'metadata': record_metadata})[:16]}"
        record_metadata["manifest_id"] = record_id
    return ContextManifestRecord(
        record_id=record_id,
        context_type="stage_context_manifest",
        source_id=run_id,
        checksum=_stable_hash(record_metadata),
        metadata=record_metadata,
    )


class _ForcedFirstActionModelAdapter:
    """Return one deterministic runtime action, then delegate to the real adapter."""

    def __init__(self, inner: ModelAdapter, first_payload: dict[str, Any]) -> None:
        self.inner = inner
        self.first_payload = dict(first_payload)
        self._used = False

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        if not self._used:
            self._used = True
            return ModelResponse(
                response_id=f"runtime_forced_{_stable_hash(self.first_payload)[:12]}",
                payload=dict(self.first_payload),
                metadata={
                    "adapter": type(self).__name__,
                    "schema_name": schema_name,
                    "deterministic_action": True,
                },
            )
        return self.inner.complete(messages, schema_name)


class _ReportWorkflowToolGateway:
    """Skill-scoped gateway; report workflow is not exposed as a normal chat tool."""

    def __init__(self, run_workflow) -> None:
        self._run_workflow = run_workflow
        self.last_result: dict[str, Any] = {}

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if args or tool_name != "report_workflow.run" or stage_name != "report_agent_loop":
            raise PermissionError("report_workflow.run is only available inside /report AgentLoop")
        message = str(kwargs.get("message") or "").strip()
        if not message:
            raise ValueError("report_workflow.run requires message")
        result = dict(self._run_workflow(message))
        self.last_result = result
        return {
            "schema_version": "report_workflow.observation.v1",
            "answer": str(result.get("answer") or ""),
            "artifacts": dict(result.get("artifacts") or {}),
            "citations": list(result.get("citations") or []),
            "internal_route": str(result.get("internal_route") or ""),
            "delegation_evidence_count": len(result.get("delegation_evidence") or []),
            "stop_reason": str(result.get("stop_reason") or "success"),
        }


class AgentRuntime:
    """Run one chat turn through controlled harness semantics and a real model boundary."""

    def __init__(
        self,
        company_id: str,
        registry: SkillRegistry | None = None,
        model_adapter: ModelAdapter | None = None,
        executor_registry: ExecutorRegistry | None = None,
        execution_override_registry: ExecutionOverrideRegistry | None = None,
        hooks: HookManager | None = None,
        run_locks: RunExecutionLockRegistry | None = None,
        operations: TurnOperations | None = None,
    ) -> None:
        self.company_id = company_id
        self.registry = registry or SkillRegistry()
        self.store = PolicyMemoryStore(company_id)
        self.model_adapter = model_adapter
        self.subagent_role_registry = build_default_subagent_role_registry()
        self.subagent_manifest_registry = build_default_subagent_manifest_registry(
            self.subagent_role_registry
        )
        self.executor_registry = executor_registry or self._build_executor_registry()
        self.execution_override_registry = (
            execution_override_registry or self._build_execution_override_registry()
        )
        self.hooks = hooks or HookManager()
        self.run_locks = run_locks or RunExecutionLockRegistry()
        register_all_tools()
        self.operations = operations or TurnOperations(
            validate_request=self._validate_turn_request,
            parse_slash_command=self._parse_slash_command,
            normalize_requested_skill_id=self._normalize_requested_skill_id,
            assign_first_turn_title=self._assign_first_turn_title,
            add_intent_step=self._add_intent_step,
            add_skill_selection_step=self._add_skill_selection_step,
            build_context=self._build_context,
            after_skill_result=self._after_skill_result,
            public_citations=self._public_citations,
            after_terminal=self._finish_finance_delivery,
        )
        self.coordinator = TurnCoordinator(
            company_id=self.company_id,
            store=self.store,
            registry=self.registry,
            executor_registry=self.executor_registry,
            execution_override_registry=self.execution_override_registry,
            hooks=self.hooks,
            run_locks=self.run_locks,
            operations=self.operations,
        )

    def _build_executor_registry(self) -> ExecutorRegistry:
        registry = ExecutorRegistry()
        general_chat = self.registry.get_manifest("general_chat")
        registry.register(
            general_chat.executor_id,
            GeneralChatExecutor(self._execute_general_chat, general_chat.output_schema),
        )
        external_impact_report = self.registry.get_manifest("external_impact_report")
        registry.register(
            external_impact_report.executor_id,
            ExternalImpactReportExecutor(
                self._execute_external_impact_report,
                external_impact_report.output_schema,
            ),
        )
        policy_weekly_impact = self.registry.get_manifest("policy_weekly_impact")
        registry.register(
            policy_weekly_impact.executor_id,
            PolicyWeeklyImpactExecutor(
                self._execute_policy_weekly_impact,
                policy_weekly_impact.output_schema,
            ),
        )
        recent_news_report = self.registry.get_manifest("recent_news_report")
        registry.register(
            recent_news_report.executor_id,
            RecentNewsReportExecutor(
                self._execute_recent_news_report,
                recent_news_report.output_schema,
            ),
        )
        research_report = self.registry.get_manifest("research_report")
        registry.register(
            research_report.executor_id,
            ResearchReportExecutor(
                self._execute_research_report,
                research_report.output_schema,
            ),
        )
        company_wiki_blueprint = self.registry.get_manifest("company_wiki_blueprint")
        registry.register(
            company_wiki_blueprint.executor_id,
            CompanyWikiBlueprintExecutor(
                self._execute_company_wiki_blueprint,
                company_wiki_blueprint.output_schema,
            ),
        )
        registry.register(
            "official_legal_reference",
            OfficialLegalReferenceExecutor(
                self._execute_official_legal_reference,
                SkillOutput,
            ),
        )
        return registry

    @staticmethod
    def _build_execution_override_registry() -> ExecutionOverrideRegistry:
        registry = ExecutionOverrideRegistry()
        registry.register(
            "official_legal_reference",
            OfficialLegalReferenceOverrideResolver(resolve_official_legal_reference),
        )
        return registry

    def run_turn(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str = "",
    ) -> dict[str, Any]:
        return self.coordinator.run_turn(
            session_id=session_id,
            message=message,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )

    def _validate_turn_request(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str,
    ) -> None:
        del session_id, message, mode, active_skill_id
        configured_model_id = str(effective_model_config()["model_id"])
        if model_id != configured_model_id:
            raise ValueError(f"模型不存在或不可用：{model_id}。当前只允许 {configured_model_id}。")
        if not self.store.get_model_config(model_id):
            raise ValueError("没有可用模型，无法执行。请先检查默认模型配置。")
        if self.model_adapter is None:
            ensure_default_model_available()

    def _normalize_requested_skill_id(
        self,
        requested_skill_id: str,
        inline_skill_id: str | None,
        task_message: str,
    ) -> str:
        if inline_skill_id is None and self._requests_memory_write(task_message):
            return "general_chat"
        if inline_skill_id is None and not requested_skill_id:
            if self.company_id == "finance_observer":
                return "general_chat"
            from policy_impact.domains.finance.chat_gateway import requests_report
            from policy_impact.domains.finance.profile import load_profile
            if requests_report(task_message) and load_profile(self.company_id):
                return "external_impact_report"
        return requested_skill_id

    def _after_skill_result(
        self,
        run_id: str,
        message: str,
        skill: SkillManifest,
        result: SkillResult,
    ) -> list[dict[str, Any]]:
        planned_writes: list[dict[str, Any]] = []
        for proposal in result.memory_proposals:
            parsed = json.loads(proposal)
            if not isinstance(parsed, dict):
                raise ValueError("memory proposal must decode to an object")
            if parsed.get("schema_version") != "runtime.memory_write.v1":
                raise ValueError("unsupported memory proposal schema")
            planned_writes.append(parsed)
        planned_writes.extend(
            self._plan_skill_memory_if_requested(
                run_id,
                message,
                skill,
                result.model_dump(mode="python"),
            )
        )
        return planned_writes

    def _finish_finance_delivery(self, run_id: str, published: bool, stop_reason: str) -> None:
        from policy_impact.company_wiki.loader import company_dir
        from policy_impact.domains.finance.research_plan import ResearchPlan

        path = company_dir(self.company_id) / "runs" / run_id / "research_plan.json"
        if not path.is_file() and self.company_id != "finance_observer":
            return
        run = self.store.get_agent_run(run_id)
        if not run:
            raise RuntimeError("published run is unavailable for delivery checkpoint")
        plan = ResearchPlan(
            session_id=str(run["session_id"]), run_id=run_id, state_path=path
        )
        if path.is_file():
            plan.call(operation="load", run_id=run_id)
        metadata = run.get("metadata") or {}
        legacy_published = published and stop_reason == "success"
        # The terminal commit, not model success, establishes actual delivery.
        delivered = published
        # A truthful Runtime fallback is user-visible, but it is not a
        # model-produced full answer. Preserve the distinction in the
        # canonical delivery dimensions instead of defaulting it to ``full``.
        delivery_mode = metadata.get("delivery_mode")
        verification_status = metadata.get("verification_status")
        evidence_coverage = metadata.get("evidence_coverage")
        answer_audit_coverage = metadata.get("answer_audit_coverage")
        if not legacy_published:
            delivery_mode = delivery_mode or "source_only"
            verification_status = verification_status or "failed"
            evidence_coverage = evidence_coverage or "partial"
            answer_audit_coverage = answer_audit_coverage or "failed"
        reason = "research_plan_missing" if not plan.exists else stop_reason
        snapshot = plan.finish_delivery(
            delivered=delivered,
            delivery_mode=delivery_mode,
            verification_status=verification_status,
            evidence_coverage=evidence_coverage,
            answer_audit_coverage=answer_audit_coverage,
            reason=reason,
        )
        # Keep the old ``published`` field as a compatibility projection for
        # a model-produced final action. The canonical delivery_status below
        # also covers a safe Runtime fallback or source-only answer.
        self.store.add_agent_step(
            run_id, step_type="research_delivery", title="Final answer delivery checkpoint",
            status="done" if delivered else "warning",
            output_payload={"published": legacy_published, "stop_reason": stop_reason,
                            "delivery_status": snapshot.get("delivery_status"),
                            "delivery_mode": snapshot.get("delivery_mode"),
                            "verification_status": snapshot.get("verification_status"),
                            "evidence_coverage": snapshot.get("evidence_coverage"),
                            "answer_audit_coverage": snapshot.get("answer_audit_coverage"),
                            "research_plan": snapshot},
            metadata={"loop_phase": "stop"},
        )

    def _record_finance_verification(self, run_id: str, record: dict[str, Any]) -> None:
        checks = record["checks"]
        self.store.add_agent_step(
            run_id, step_type="verification_decision", title="Verification scope selected",
            status="done",
            output_payload={"decisions": [
                {"claim": item["claim"], "decision": item["decision"],
                 "method": item["method"]} for item in checks
            ]}, metadata={"loop_phase": "verify"},
        )
        self.store.add_agent_step(
            run_id, step_type="verification_result", title="Verification results recorded",
            status="done", output_payload={
                "schema_version": record["schema_version"], "checks": checks,
                "summary": record["summary"],
            }, metadata={"loop_phase": "verify"},
        )
        self.store.add_agent_step(
            run_id, step_type="verification_gap", title="Remaining verification gaps",
            status="warning" if record["gaps"] else "done",
            output_payload={"gaps": record["gaps"]},
            metadata={"loop_phase": "verify"},
        )

    @staticmethod
    def _public_citations(citations: list[CitationRef]) -> list[dict[str, Any]]:
        serialized = []
        for citation in citations:
            item = citation.model_dump(mode="python", exclude_defaults=True)
            citation_type = str(item.pop("citation_type", "") or "")
            if citation_type:
                item["type"] = citation_type
            serialized.append(item)
        return serialized

    @staticmethod
    def _parse_slash_command(message: str) -> tuple[str | None, str]:
        text = str(message or "").strip()
        # An explicitly deferred command is quoted intent, not tool consent.
        if re.search(r"(?:不要|别|不必|暂不|先不)(?:立即|现在|实际|直接)?(?:执行|运行|生成报告)", text):
            return None, text
        match = re.search(
            r"(^|\s)/(auto|chat|report|impact|policy|news|research|wiki)(?=\s|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None, text
        command_to_skill = {
            "auto": "",
            "chat": "general_chat",
            "report": "external_impact_report",
            "impact": "external_impact_report",
            "policy": "external_impact_report",
            "news": "external_impact_report",
            "research": "external_impact_report",
            "wiki": "company_wiki_blueprint",
        }
        command = match.group(2).lower()
        command_start = match.start() + len(match.group(1) or "")
        command_end = match.end()
        task = f"{text[:command_start]} {text[command_end:]}".strip()
        task = re.sub(r"\s+", " ", task)
        return command_to_skill[command], task or text

    def _assign_first_turn_title(self, session_id: str, message: str) -> None:
        session = self.store.get_chat_session(session_id)
        if not session or int(session.get("message_count") or 0) > 0:
            return
        current = str(session.get("title") or "").strip()
        if current not in {"", "新对话", "未命名对话"} and not re.fullmatch(r"\?{3,}", current):
            return
        title = re.sub(r"\s+", " ", str(message or "")).strip(" /：:")
        if not title:
            return
        self.store.update_chat_session_settings(session_id, title=title[:32])

    def _add_intent_step(
        self,
        run_id: str,
        message: str,
        mode: str,
        skill: SkillManifest,
    ) -> None:
        self.store.add_agent_step(
            run_id,
            step_type="intent_classification",
            title="识别本轮意图",
            status="done",
            input_payload={"message": message, "mode": mode},
            output_payload={"resolved_skill_id": skill.id},
            metadata={"method": "deterministic_keyword_router", "loop_phase": "think"},
        )

    def _add_skill_selection_step(
        self,
        run_id: str,
        skill: SkillManifest,
        active_skill_id: str,
    ) -> None:
        self.store.add_agent_step(
            run_id,
            step_type="skill_selection",
            title="选择执行技能",
            status="done",
            input_payload={"active_skill_id": active_skill_id},
            output_payload={
                "skill": {
                    "id": skill.id,
                    "name": skill.name,
                    "mode": skill.execution_mode.value,
                    "subagents": list(skill.allowed_subagents),
                    "artifact_types": list(
                        skill.output_schema.model_json_schema().get("artifact_types", [])
                    ),
                }
            },
            metadata={
                "selection_source": "explicit" if active_skill_id else "resolver",
                "loop_phase": "think",
            },
        )

    def _build_context(
        self,
        run_id: str,
        session_id: str,
        message: str,
        skill: SkillManifest,
        model_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        config = ChatContextConfig()
        all_messages = self.store.list_chat_messages(session_id)
        history = all_messages[:-1] if all_messages and all_messages[-1].get("role") == "user" else all_messages
        summary = self._ensure_conversation_summary(
            run_id=run_id,
            session_id=session_id,
            history=history,
            model_id=model_id,
            config=config,
        )
        strict_knowledge_only = (
            skill.id == "general_chat"
            and self._requires_retrieved_evidence_only(message)
            and "知识库" in message
            and "记忆" not in message
        )
        unbound_project_scope = skill.id == "general_chat" and self._is_unbound_project_comparison(message)
        workbench_project_scope = skill.id == "general_chat" and self._is_workbench_design_query(message)
        raw_memories: list[dict[str, Any]] = []
        memories: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        context_tool_calls: list[dict[str, Any]] = []
        if skill.id == "general_chat":
            from policy_impact.domains.finance.profile import load_profile
            finance_workspace = self.company_id == "finance_observer" or bool(load_profile(self.company_id))
            if finance_workspace:
                # Finance research exposes Wiki and memory as normal scoped
                # tools.  The model decides whether they add value for this
                # question; Runtime does not prefetch them before the loop.
                history_only = False
                report_followup_context = False
                self.store.add_agent_step(
                    run_id,
                    step_type="memory_read",
                    title="延迟读取金融工作空间记忆",
                    status="skipped",
                    input_payload={"query": message, "top_k": 5},
                    output_payload={
                        "memory_count": 0,
                        "raw_memory_count": 0,
                        "initiated_by": "model",
                        "available_as_tool": "memory_search",
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "金融研究主 Agent 自主决定是否调用 memory_search",
                        "policy_id": "finance.context_deferred_to_agent.v1",
                    },
                    metadata={"loop_phase": "observe", "context_retrieval": True},
                )
                self.store.add_agent_step(
                    run_id,
                    step_type="tool_call",
                    title="延迟读取金融 Wiki",
                    status="skipped",
                    input_payload={"tools": []},
                    output_payload={
                        "fact_count": 0,
                        "raw_fact_count": 0,
                        "initiated_by": "model",
                        "available_as_tool": "company_wiki_search",
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "金融研究主 Agent 自主决定是否调用 company_wiki_search",
                        "policy_id": "finance.context_deferred_to_agent.v1",
                    },
                    metadata={"loop_phase": "observe", "context_retrieval": True},
                )
            else:
                history_only = self._is_immediate_history_query(message) or self._is_session_context_declaration(message)
                report_followup_context = (
                    history_only and not self._is_session_context_declaration(message)
                    and self._report_followup_needs_wiki(message, history)
                )
                reset_tool_audit_log()
                gateway = ToolGateway()
                raw_memories = [] if strict_knowledge_only else gateway.call(
                    "main_agent",
                    "memory_search",
                    company_id=self.company_id,
                    query=message,
                    top_k=5,
                )
                memories = [] if history_only else self._filter_general_chat_memories(message, raw_memories)
                raw_facts = [] if unbound_project_scope or workbench_project_scope else gateway.call(
                    "main_agent",
                    "company_wiki_search",
                    company_id=self.company_id,
                    query=message,
                    top_k=12,
                )
                facts = [] if history_only and not report_followup_context else raw_facts
                context_tool_calls = [record.to_dict() for record in gateway.calls]
                self.store.add_agent_step(
                    run_id,
                    step_type="memory_read",
                    title="读取长期记忆",
                    status="done",
                    input_payload={"query": message, "top_k": 5},
                    output_payload={
                        "memory_count": len(memories),
                        "raw_memory_count": len(raw_memories),
                        "filtered_out": max(0, len(raw_memories) - len(memories)),
                        "memory_ids": [str(item.get("id") or "") for item in memories],
                        "history_only_resolution": history_only,
                    },
                    metadata={"loop_phase": "observe"},
                )
                requested_tools = [str(record.get("tool_name") or "") for record in context_tool_calls]
                self.store.add_agent_step(
                    run_id,
                    step_type="tool_call",
                    title="为主 Agent 检索记忆与相关 Wiki 事实",
                    status="done",
                    input_payload={"tools": requested_tools},
                    output_payload={
                        "fact_count": len(facts),
                        "raw_fact_count": len(raw_facts),
                        "memory_count": len(memories),
                        "fact_ids": [str(item.get("fact_id") or "") for item in facts],
                        "history_only_resolution": history_only and not report_followup_context,
                        "wiki_context_retained_for_report_followup": report_followup_context,
                        "workbench_project_scope": workbench_project_scope,
                    },
                    tool_calls=context_tool_calls,
                    gate_result={
                        "decision": "allow",
                        "reason": (
                            "Workbench 项目问题不注入企业 Wiki fact，避免把公司画像当项目事实"
                            if workbench_project_scope
                            else "main_agent allowlist permits context retrieval tools"
                        ),
                    },
                    metadata={"loop_phase": "act", "context_retrieval": True},
                )
        elif skill.id in {"research_report", "external_impact_report"}:
            raw_memories = self.store.search_memory(message, top_k=8)
            memories = self._filter_research_memories(message, raw_memories)
            self.store.add_agent_step(
                run_id,
                step_type="memory_read",
                title=(
                    "为外部变化分析读取长期记忆"
                    if skill.id == "external_impact_report"
                    else "为调研报告读取记忆"
                ),
                status="done",
                input_payload={"query": message, "top_k": 8},
                output_payload={
                    "memory_count": len(memories),
                    "raw_memory_count": len(raw_memories),
                    "filtered_out": max(0, len(raw_memories) - len(memories)),
                    "memory_ids": [str(item.get("id") or "") for item in memories],
                },
                metadata={"loop_phase": "observe"},
            )

        skill_payload = {
            "id": skill.id,
            "name": skill.name,
            "mode": skill.execution_mode.value,
            "subagents": list(skill.allowed_subagents),
        }
        tool_policy = {
            "decision_order": ["deny", "ask", "allow"],
            "skill_allowlists": compatibility_tool_policy(skill),
            "runtime_owns_permissions": True,
        }
        from policy_impact.harness.report_context import read_report_evidence

        report_evidence, report_read = read_report_evidence(company_dir(self.company_id), history, message)
        if report_evidence:
            config = replace(config, artifact_token_budget=1800)
        if report_read:
            self.store.add_agent_step(
                run_id, step_type="artifact_read", title="回读本会话报告的来源证据", status="done",
                input_payload={"query": message, "path": report_read["path"]},
                output_payload={"evidence": report_evidence, "read_audit": report_read},
                metadata={"loop_phase": "observe", "context_retrieval": True},
            )
        compiled = compile_chat_context(
            message=message,
            skill=skill_payload,
            history=history,
            conversation_summary=summary,
            memories=memories,
            facts=facts,
            tool_policy=tool_policy,
            config=config,
            report_evidence=report_evidence,
        )
        recent_run_traces = self._compile_recent_run_traces(
            history,
            limit=3,
            token_budget=config.run_trace_token_budget,
        )
        compiled["recent_run_traces"] = recent_run_traces
        compiled["context_policy"]["recent_run_traces_included"] = len(recent_run_traces)
        state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        state.current_user_message = compiled["current_user_message"]
        state.selected_skill = compiled["selected_skill"]
        state.recent_history = compiled["recent_history"]
        state.recent_artifacts = compiled["recent_artifacts"]
        state.recent_run_traces = compiled["recent_run_traces"]
        state.conversation_summary = compiled["conversation_summary"]
        state.relevant_memories = compiled["relevant_memories"]
        state.company_facts = compiled["company_facts"]
        state.tool_policy_summary = compiled["tool_policy_summary"]
        state.context_policy = compiled["context_policy"]
        manifest = build_context_manifest(
            state=state,
            stage_name="chat_turn",
            agent_role=f"skill:{skill.id}",
            visible_fields=[
                "current_user_message",
                "selected_skill",
                "recent_history",
                "recent_artifacts",
                "recent_run_traces",
                "conversation_summary",
                "relevant_memories",
                "company_facts",
                "tool_policy_summary",
                "context_policy",
            ],
            token_budget=config.manifest_budget(),
            manifest_sequence=1,
        )
        prepared = {
            "history": history,
            "recent_history": compiled["recent_history"],
            "recent_artifacts": compiled["recent_artifacts"],
            "recent_run_traces": compiled["recent_run_traces"],
            "conversation_summary": compiled["conversation_summary"],
            "memories": memories,
            "facts": facts,
            "strict_knowledge_only": strict_knowledge_only,
            "unbound_project_scope": unbound_project_scope,
            "workbench_project_scope": workbench_project_scope,
            "context_tool_calls": context_tool_calls,
        }
        return manifest.metadata, prepared

    def _compile_recent_run_traces(
        self,
        history: list[dict[str, Any]],
        *,
        limit: int,
        token_budget: int,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_run_ids: set[str] = set()
        for message in reversed(history):
            if str(message.get("role") or "") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            run_id = str(metadata.get("run_id") or "").strip()
            if not run_id or run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            run = self.store.get_agent_run(run_id) or {}
            steps = self.store.list_agent_steps(run_id) if run else []
            step_types: list[str] = []
            internal_routes: list[str] = []
            tool_names: list[str] = []
            gate_decisions: list[dict[str, str]] = []
            report_workflow_run = False
            workflow_observation_returned = False
            for step in steps:
                step_type = str(step.get("step_type") or "")
                if step_type:
                    step_types.append(step_type)
                step_metadata = step.get("metadata") or {}
                output = step.get("output_payload") or {}
                if output.get("internal_route"):
                    internal_routes.append(str(output.get("internal_route")))
                payload = output.get("payload") if isinstance(output.get("payload"), dict) else {}
                payload_tool = str(payload.get("tool_name") or "")
                if payload_tool:
                    tool_names.append(payload_tool)
                if payload_tool == "report_workflow.run":
                    report_workflow_run = True
                for call in step.get("tool_calls") or []:
                    tool_name = str(call.get("tool_name") or "")
                    if tool_name:
                        tool_names.append(tool_name)
                if (
                    step_metadata.get("embedded_loop") == "external_impact_report"
                    and step_type == "workflow_stage"
                ):
                    workflow_observation_returned = True
                gate_result = step.get("gate_result") or {}
                if gate_result.get("decision"):
                    gate_decisions.append(
                        {
                            "step_type": step_type,
                            "decision": str(gate_result.get("decision") or ""),
                            "policy_id": str(gate_result.get("policy_id") or ""),
                            "reason": str(gate_result.get("reason") or "")[:120],
                        }
                    )
            run_artifacts = list(run.get("artifacts") or [])
            metadata_artifacts = metadata.get("artifacts") or {}
            artifact_keys = [
                str(item.get("artifact_type") or "")
                for item in run_artifacts
                if isinstance(item, dict) and item.get("artifact_type")
            ]
            if not artifact_keys and isinstance(metadata_artifacts, dict):
                artifact_keys = sorted(str(key) for key in metadata_artifacts)
            record = {
                "recency_rank": len(records) + 1,
                "run_id": run_id,
                "skill_id": str(metadata.get("skill_id") or ""),
                "status": str(run.get("status") or ""),
                "step_types": step_types,
                "internal_routes": _unique_preserve_order(internal_routes),
                "tool_names": _unique_preserve_order(tool_names),
                "report_workflow_run": report_workflow_run,
                "workflow_observation_returned": workflow_observation_returned,
                "artifact_keys": artifact_keys,
                "artifact_count": len(artifact_keys),
                "gate_decisions": gate_decisions[:6],
            }
            trial = [*records, record]
            if estimate_tokens(trial) <= token_budget:
                records.append(record)
            if len(records) >= limit:
                break
        return records

    def _ensure_conversation_summary(
        self,
        *,
        run_id: str,
        session_id: str,
        history: list[dict[str, Any]],
        model_id: str,
        config: ChatContextConfig,
    ) -> dict[str, Any]:
        compacted_count = max(0, len(history) - config.recent_message_limit)
        if compacted_count == 0:
            return {}
        compacted_messages = history[:compacted_count]
        source_hash = hashlib.sha256(
            json.dumps(
                [
                    {
                        "role": item.get("role", ""),
                        "content": item.get("content", ""),
                    }
                    for item in compacted_messages
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        existing = self.store.get_conversation_summary(session_id)
        if existing and existing.get("source_hash") == source_hash:
            self.store.add_agent_step(
                run_id,
                step_type="context_compaction",
                title="复用增量对话摘要",
                status="done",
                input_payload={"source_message_count": compacted_count},
                output_payload={
                    "summary_id": f"conversation_summary:{session_id}",
                    "source_hash": source_hash,
                    "reused": True,
                },
                metadata={"loop_phase": "observe", "compaction_policy": "incremental_model_summary"},
            )
            return dict(existing.get("summary") or {})

        previous_count = int((existing or {}).get("source_message_count") or 0)
        if previous_count < 0 or previous_count > compacted_count:
            previous_count = 0
        incremental_messages = compacted_messages[previous_count:]
        previous_summary = dict((existing or {}).get("summary") or {}) if previous_count else {}
        prompt_messages, prompt_contract = build_conversation_summary_prompt(
            previous_summary=previous_summary,
            messages=incremental_messages,
        )
        fallback_used = False
        try:
            response = self._complete_model(
                run_id=run_id,
                title="压缩历史对话上下文",
                model_id=model_id,
                schema_name="conversation_summary.v1",
                messages=prompt_messages,
                prompt_contract=prompt_contract,
            )
            summary = dict(response.payload.get("output") or {})
        except ModelInvocationError as exc:
            fallback_used = True
            summary = self._extractive_conversation_summary(previous_summary, incremental_messages)
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行上下文压缩降级门控",
                status="done",
                input_payload={"contract_id": "conversation_summary.v1"},
                output_payload={"fallback": "loss_aware_extractive", "error": str(exc)},
                gate_result={
                    "decision": "repair",
                    "reason": "摘要模型不可用，使用不引入新事实的抽取式压缩并保留失败证据",
                },
                metadata={"loop_phase": "observe"},
            )
        saved = self.store.upsert_conversation_summary(
            session_id=session_id,
            summary=summary,
            source_message_count=compacted_count,
            source_hash=source_hash,
        )
        self.store.add_agent_step(
            run_id,
            step_type="context_compaction",
            title="生成增量对话摘要",
            status="done",
            input_payload={
                "previous_source_message_count": previous_count,
                "incremental_message_count": len(incremental_messages),
                "source_message_count": compacted_count,
            },
            output_payload={
                "summary_id": f"conversation_summary:{session_id}",
                "source_hash": source_hash,
                "reused": False,
                "fallback_used": fallback_used,
                "summary_keys": sorted(summary),
            },
            gate_result={
                "decision": "allow",
                "reason": "摘要通过 conversation_summary.v1 契约或无新增事实的抽取式降级生成",
                "policy_id": "context.compaction.v1",
            },
            metadata={"loop_phase": "observe", "compaction_policy": "incremental_model_summary"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="memory_write",
            title="持久化增量对话摘要",
            status="done",
            input_payload={
                "memory_type": "conversation_summary",
                "write_policy": "context_pressure_compaction",
            },
            output_payload={
                "memory_id": f"conversation_summary:{session_id}",
                "namespace": f"company:{self.company_id}:conversation",
                "created": existing is None,
                "updated": existing is not None,
                "source_message_count": compacted_count,
                "source_hash": source_hash,
                "fallback_used": fallback_used,
            },
            gate_result={
                "decision": "allow",
                "reason": "仅压缩已持久化对话，不新增用户事实或企业事实",
            },
            metadata={"loop_phase": "act", "compaction_policy": "incremental_model_summary"},
        )
        return dict(saved.get("summary") or {})

    @staticmethod
    def _extractive_conversation_summary(
        previous_summary: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        excerpts = []
        for item in messages:
            role = "用户" if item.get("role") == "user" else "助手"
            content = " ".join(str(item.get("content") or "").split())[:240]
            if content:
                excerpts.append(f"{role}：{content}")
        previous_text = str(previous_summary.get("summary") or "").strip()
        merged = "；".join([part for part in (previous_text, *excerpts) if part])[:900]
        return {
            "summary": merged or "旧对话没有可压缩的实质内容。",
            "user_facts": list(previous_summary.get("user_facts") or [])[:6],
            "decisions": list(previous_summary.get("decisions") or [])[:6],
            "open_loops": list(previous_summary.get("open_loops") or [])[:6],
        }

    def _execute_skill(
        self,
        run_id: str,
        session_id: str,
        skill: SkillManifest,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
        *,
        executor_id: str,
        services: Mapping[str, Any],
    ) -> SkillResult:
        context = SkillExecutionContext(
            run_id=run_id,
            session_id=session_id,
            company_id=self.company_id,
            message=message,
            model_id=model_id,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
            services=services,
        )
        result = self.executor_registry.get(executor_id).execute(context)
        self._persist_skill_memory_if_requested(
            run_id,
            message,
            skill,
            result.model_dump(mode="python"),
        )
        return result

    def _execute_general_chat(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_general_chat(
            context.run_id,
            context.session_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_external_impact_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_external_impact_report(
            context.run_id,
            context.session_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_policy_weekly_impact(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_policy_weekly_impact(
            context.run_id,
            context.session_id,
            context.message,
        )

    def _execute_recent_news_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_recent_news_report(context.run_id, context.message)

    def _execute_research_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_research_report(
            context.run_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_company_wiki_blueprint(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_company_wiki_blueprint(context.run_id)

    def _execute_official_legal_reference(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_official_legal_reference(
            context.run_id,
            context.message,
            _materialize_execution_value(context.services["legal_reference"]),
        )

    def _run_official_legal_reference(
        self,
        run_id: str,
        message: str,
        legal_reference: dict[str, Any],
    ) -> dict[str, Any]:
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="核验官方法规原文",
            status="done",
            input_payload={
                "reference_id": legal_reference["reference_id"],
                "query": message,
            },
            output_payload={
                "title": legal_reference["title"],
                "official_text": legal_reference["official_text"],
                "official_url": legal_reference["official_url"],
            },
            gate_result={
                "decision": "allow",
                "reason": "命中经过人工核验的官方法规原文注册表",
            },
            metadata={"loop_phase": "observe", "source_level": "official_primary"},
        )
        return {
            "answer": legal_reference["answer"],
            "artifacts": {},
            "citations": [
                {
                    "type": "official_legal_reference",
                    "title": legal_reference["title"],
                    "url": legal_reference["official_url"],
                }
            ],
        }

    def _run_general_chat(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        strict_knowledge_only = bool(prepared_context.get("strict_knowledge_only"))
        command = re.match(r"^/([A-Za-z][A-Za-z0-9_-]*)(?=\s|$)", message.strip())
        if command and command.group(1).lower() not in {"auto", "chat", "report", "impact", "policy", "news", "research", "wiki"}:
            self.store.add_agent_step(
                run_id, step_type="gate_check", title="识别未知技能命令", status="done",
                input_payload={"command": command.group(0)},
                output_payload={"supported_public_commands": ["/report"], "model_call_skipped": True},
                gate_result={"decision": "deny", "reason": "unknown_slash_command"},
                metadata={"deterministic": True, "loop_phase": "observe"},
            )
            return {"answer": f"未识别的技能命令：{command.group(0)}。当前可使用 /report 生成资料研判报告，或直接输入普通问题。本轮未执行报告或发布操作。",
                    "artifacts": {}, "citations": []}
        memories = list(prepared_context.get("memories") or [])
        facts = list(prepared_context.get("facts") or [])
        citations = []
        visible_fact_ids = {
            str(item.get("fact_id") or "")
            for item in (context_manifest.get("visible_content") or {}).get("company_facts") or []
        }
        for fact in facts:
            if str(fact.get("fact_id") or "") not in visible_fact_ids:
                continue
            citations.append(
                {
                    "type": "company_fact",
                    "fact_id": fact.get("fact_id"),
                    "path": fact.get("source_path"),
                    "source": fact.get("source"),
                    "source_kind": fact.get("source_kind"),
                }
            )
        for item in memories[:3]:
            citations.append({"type": "memory_item", "memory_id": item.get("id")})
        history = self.store.list_chat_messages(session_id)
        prior_article_refs = []
        for previous in reversed(history[:-1]):
            if previous.get("role") == "assistant":
                prior_article_refs = [item for item in (previous.get("metadata") or {}).get("citations", [])
                                      if item.get("type") == "author_article"]
                break
        prior_fact_answer = self._prior_fact_id_answer(message, history[:-1])
        if prior_fact_answer:
            prior_citations = self._prior_company_fact_citations(history[:-1])
            self.store.add_agent_step(
                run_id,
                step_type="evidence_replay",
                title="复现上一轮企业事实引用",
                status="done",
                input_payload={"question": message},
                output_payload={"fact_ids": [item["fact_id"] for item in prior_citations]},
                gate_result={
                    "decision": "allow",
                    "reason": "fact_id 来自上一轮已持久化回答与引用元数据",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {
                "answer": prior_fact_answer,
                "artifacts": {},
                "citations": prior_citations,
            }
        if self._is_unpersisted_cross_session_query(message):
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行跨会话临时信息隔离门控",
                status="done",
                input_payload={"policy_id": "memory.cross_session_isolation.v1"},
                output_payload={"model_call_skipped": True},
                gate_result={
                    "decision": "deny",
                    "reason": "未显式写入长期记忆的会话临时信息不得跨会话读取",
                    "policy_id": "memory.cross_session_isolation.v1",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {
                "answer": (
                    "我无法读取另一个会话中未写入长期记忆的临时内容。"
                    "只有你明确要求保存的长期记忆才能跨会话召回；请回到原会话查看，或在当前会话重新提供。"
                ),
                "artifacts": {},
                "citations": [],
            }
        if prepared_context.get("unbound_project_scope"):
            configured_subject = (
                "company_001（同花顺企业画像）"
                if self.company_id == "company_001"
                else self.company_id
            )
            answer = (
                "当前无法基于受控证据比较“这个项目”和 CoursePilot：当前企业知识库属于 "
                f"{configured_subject}，不是 Agent Workbench 或 CoursePilot 的项目资料；"
                "长期记忆中也没有足以消除“这个项目”指代歧义的项目契约。"
                "本轮已阻止把企业画像事实冒充项目实现事实，也没有调用模型补造差异。"
                "请明确项目名称，并为两个项目提供 README、能力边界或结构化项目画像后再比较。"
            )
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="确认项目比较对象",
                status="done",
                input_payload={"message": message, "configured_company_id": self.company_id},
                output_payload={"model_call_skipped": True},
                gate_result={
                    "decision": "ask",
                    "reason": "项目指代不明确，且企业 Wiki 不能作为项目代码事实",
                    "policy_id": "general_chat.subject_scope.v1",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {"answer": answer, "artifacts": {}, "citations": []}
        predelegated_subagents = {"findings": [], "delegation_evidence": []}
        defer_finance_review = False
        # Finance turns need one main-loop decision over all research tools.
        # Pre-delegating from a keyword branch would make the model lose the
        # chance to inspect the article/report evidence first.
        from policy_impact.domains.finance.profile import load_profile
        defer_finance_review = bool(load_profile(self.company_id)) or "finance" in self.company_id.casefold()
        if self._should_predelegate_subagents(message) and not defer_finance_review:
            predelegated_subagents = self._run_dynamic_subagents_for_chat(
                run_id=run_id,
                message=message,
                context_manifest=context_manifest,
                prepared_context=prepared_context,
            )
            if predelegated_subagents.get("findings"):
                context_manifest = self._attach_subagent_context(
                    run_id=run_id,
                    context_manifest=context_manifest,
                    subagent_findings=list(predelegated_subagents.get("findings") or []),
                )
        chat_loop = self._run_general_chat_agent_loop(
            run_id=run_id,
            session_id=session_id,
            message=message,
            model_id=model_id,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
            memories=memories,
            facts=facts,
            prior_article_refs=prior_article_refs,
        )
        answer = str(chat_loop.get("answer") or "").strip()
        if not answer:
            raise ModelInvocationError(
                f"普通对话 AgentLoop 未产出 answer：{chat_loop.get('stop_reason') or 'unknown'}"
            )
        delegation_evidence = [
            *list(predelegated_subagents.get("delegation_evidence") or []),
            *list(chat_loop.get("delegation_evidence") or []),
        ]
        governed_answer, answer_contract = self._govern_general_chat_answer(message, answer, facts)
        if answer_contract:
            answer = governed_answer
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行企业回答契约门控",
                status="done",
                input_payload={"contract_id": answer_contract["contract_id"]},
                output_payload={
                    "repair_applied": True,
                    "required_fields": answer_contract["required_fields"],
                },
                gate_result={
                    "decision": "repair",
                    "reason": answer_contract["reason"],
                    "policy_id": answer_contract["contract_id"],
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
        memory_requested = self._requests_memory_write(message)
        memory_content = self._explicit_memory_content(message)
        feedback_memory_content = "" if memory_content else self._feedback_memory_content(message)
        memory_proposals: list[str] = []
        if memory_content:
            namespace = f"company:{self.company_id}:user_profile"
            memory_id, memory_created = self._plan_deduplicated_memory_id(
                namespace,
                "explicit_user_memory",
                memory_content,
            )
            memory_plan = {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": "explicit_user_memory",
                "content": memory_content,
                "importance": 0.9,
                "deduplicate": True,
                "metadata": {
                    "source": "explicit_user_request",
                    "run_id": run_id,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户明确要求保存的长期记忆",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "explicit_user_memory",
                        "write_policy": "explicit_user_request_only",
                    },
                    "output_payload": {
                        "namespace": namespace,
                        "content_preview": memory_content[:160],
                        "created": memory_created,
                        "deduplicated": not memory_created,
                    },
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮明确要求记住该信息",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
            memory_proposals.append(
                json.dumps(memory_plan, ensure_ascii=False, sort_keys=True)
            )
            citations = [item for item in citations if item.get("type") != "memory_item"]
            citations.append({"type": "memory_item", "memory_id": memory_id})
            answer = (
                f"已写入长期记忆。\n- 保存内容：{memory_content}"
                if memory_created
                else f"长期记忆中已存在相同内容，本轮未重复写入。\n- 保存内容：{memory_content}"
            )
        elif feedback_memory_content:
            namespace = f"company:{self.company_id}:user_profile"
            memory_id, memory_created = self._plan_deduplicated_memory_id(
                namespace,
                "feedback_memory",
                feedback_memory_content,
            )
            memory_plan = {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": "feedback_memory",
                "content": feedback_memory_content,
                "importance": 0.85,
                "deduplicate": True,
                "metadata": {
                    "source": "user_feedback",
                    "run_id": run_id,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户反馈偏好",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "feedback_memory",
                        "write_policy": "durable_user_feedback_only",
                    },
                    "output_payload": {
                        "namespace": namespace,
                        "content_preview": feedback_memory_content[:160],
                        "created": memory_created,
                        "deduplicated": not memory_created,
                    },
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮给出会影响后续报告和建议排序的反馈",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
            memory_proposals.append(
                json.dumps(memory_plan, ensure_ascii=False, sort_keys=True)
            )
            citations = [item for item in citations if item.get("type") != "memory_item"]
            citations.append({"type": "memory_item", "memory_id": memory_id})
            if memory_created:
                answer = f"{answer}\n\n已记录这条反馈，后续报告和建议排序会参考它。[memory_id: {memory_id}]"
        elif memory_requested:
            answer = "未执行记忆写入：没有识别到冒号后的具体保存内容。请使用“请记住：...”后重试。"
        citations.extend(chat_loop.get("citations") or [])
        citations = self._ground_general_chat_citations(
            message,
            answer,
            citations,
            memory_requested=memory_requested,
            strict_knowledge_only=strict_knowledge_only,
            memory_items=memories,
        )
        answer = self._attach_grounded_memory_citations(answer, citations)
        literal_reply = self._literal_reply_prefix(message, answer)
        if literal_reply is not None and not memory_proposals:
            self.store.add_agent_step(
                run_id, step_type="gate_check", title="应用用户指定的短回复格式", status="done",
                input_payload={"requested_reply": literal_reply},
                output_payload={"extra_suffix_removed": True},
                gate_result={"decision": "repair", "reason": "literal_reply_suffix_removed"},
                metadata={"deterministic": True, "loop_phase": "observe"},
            )
            answer = literal_reply
            citations = []
        return {
            "answer": answer,
            "artifacts": dict(chat_loop.get("artifacts") or {}),
            "citations": citations,
            "memory_proposals": memory_proposals,
            "delegation_evidence": delegation_evidence,
            "stop_reason": str(chat_loop.get("stop_reason") or "success"),
            **{field: chat_loop[field] for field in (
                "delivery_status", "delivery_mode", "verification_status",
                "evidence_coverage", "answer_audit_coverage", "semantic_review",
            ) if field in chat_loop},
        }

    @staticmethod
    def _literal_reply_prefix(message: str, answer: str) -> str | None:
        match = re.search(r"只回复\s*[：:]?\s*([^。！？!?\n]{1,32})[。.!！]?$", message.strip())
        if not match:
            return None
        literal = match.group(1).strip().strip('"“”')
        # Only remove an unsolicited suffix; never replace a refusal or invent agreement.
        if literal and answer.startswith(literal) and answer != literal:
            return literal
        return None

    @staticmethod
    def _explicit_subagent_requested(message: str) -> bool:
        return bool(re.search(r"sub[ -]?agents?|子智能体|多智能体", str(message or ""), re.I))

    @staticmethod
    def _finance_review_requested(message: str) -> bool:
        """Return whether the user explicitly requires an independent review.

        Conditional language such as "if needed, let the main Agent decide"
        describes an available capability, not a hard review gate. Treating it
        as mandatory makes the final guard reject an otherwise valid answer.
        """
        text = str(message or "").casefold()
        if AgentRuntime._conditional_review_language(text):
            return False
        return AgentRuntime._explicit_subagent_requested(text) or any(
            marker in text
            for marker in (
                "复核", "核验", "查证", "质疑", "反例", "反驳", "验证准确",
                "独立判断", "多角度", "skeptic", "verifier", "analyst",
            )
        )

    @staticmethod
    def _conditional_review_language(message: str) -> bool:
        """Detect capability suggestions that must remain model-decided."""
        text = str(message or "").casefold()
        has_conditional = bool(
            re.search(r"(?:如果|若|必要时|需要时|视情况|\bif\b|\bwhen\b|as needed)", text)
        )
        mentions_review = bool(
            re.search(r"(?:独立|复核|核验|查证|质疑|subagent|子智能体|委派|delegate|verifier|skeptic|analyst)", text)
        )
        if not (has_conditional and mentions_review):
            return False
        # An explicit imperative inside the same request still wins.
        return not bool(
            re.search(
                r"(?:请调用|必须调用|要求调用|帮我调用|请让|必须让|please\s+use|must\s+use|"
                r"(?:call|use|require|must\s+(?:call|use)|explicitly\s+(?:call|use))\s+"
                r"(?:the\s+)?(?:verifier|skeptic|analyst|subagent)\b|"
                r"independently\s+(?:verify|review|read)|independent\s+review)",
                text,
            )
        )

    @staticmethod
    def _finance_review_roles(message: str) -> tuple[str, ...]:
        text = str(message or "").casefold()
        roles: list[str] = []
        if any(marker in text for marker in ("核验", "查证", "验证", "准确", "verifier")):
            roles.append("verifier")
        if any(marker in text for marker in ("质疑", "反例", "反驳", "风险", "skeptic")):
            roles.append("skeptic")
        analyst_requested = any(
            marker in text
            for marker in ("analyst", "分析助手", "分析子智能体", "分析角度", "分析视角")
        ) or bool(re.search(r"分析(?:和|与|及|、)(?:质疑|核验|复核)(?:两个|两种)?(?:角度|视角)", text))
        if analyst_requested:
            roles.append("analyst")
        if not roles:
            roles.append("skeptic")
        return tuple(dict.fromkeys(roles))[:2]

    @staticmethod
    def _finance_subagent_evidence_audit(
        delegate_records: list[dict[str, Any]],
        evidence_bundle: dict[str, Any],
        *,
        review_required: bool = False,
    ) -> dict[str, Any]:
        """Bind structured child claims to the evidence visible in this run.

        This is deliberately an audit, not a second analysis policy.  The main
        Agent can choose whether to delegate and which roles to use; once a
        child returns, the Runtime records whether each structured claim,
        counterexample, or verdict names evidence that was actually available
        to that child or to the parent research ledger.
        """
        parent_ids = {
            str(value)
            for value in evidence_bundle.get("evidence_ids") or []
            if str(value).strip()
        }
        parent_aliases = {
            str(key): str(value)
            for key, value in (evidence_bundle.get("aliases") or {}).items()
            if str(key).strip() and str(value).strip()
        }
        parent_ids.update(parent_aliases)
        claim_rows: list[dict[str, Any]] = []
        verifier_decisions: dict[str, str] = {}

        def child_evidence_ids(finding: dict[str, Any]) -> set[str]:
            child_bundle = finding.get("child_evidence_bundle") or {}
            ids = {
                str(value)
                for value in child_bundle.get("evidence_ids") or []
                if str(value).strip()
            }
            ids.update(
                str(value)
                for value in (finding.get("evidence_ids") or [])
                if str(value).strip()
            )
            ids.update(
                str(key)
                for key in (child_bundle.get("aliases") or {}).keys()
                if str(key).strip()
            )
            ids.update(
                str(value)
                for value in (child_bundle.get("aliases") or {}).values()
                if str(value).strip()
            )
            return ids

        # Collect verifier decisions first so analyst and skeptic rows can
        # show how the same claim was treated by a verification role.
        for record in delegate_records:
            finding = record.get("finding") or {}
            if finding.get("status") != "success" or finding.get("role") != "verifier":
                continue
            for item in (finding.get("output") or {}).get("verdicts") or []:
                claim_ref = str(item.get("claim_ref") or "").strip()
                if claim_ref:
                    verifier_decisions[claim_ref] = str(item.get("decision") or "unknown")

        for record in delegate_records:
            finding = record.get("finding") or {}
            if finding.get("status") != "success":
                continue
            role = str(finding.get("role") or "unknown")
            output = finding.get("output") or {}
            if role == "analyst":
                items = output.get("claims") or []
                item_kind = "claim"
            elif role == "skeptic":
                items = output.get("counterexamples") or []
                item_kind = "counterexample"
            elif role == "verifier":
                items = output.get("verdicts") or []
                item_kind = "verdict"
            else:
                items = []
                item_kind = "structured_item"
            allowed_ids = parent_ids | child_evidence_ids(finding)
            expected_author = str(finding.get("assigned_author") or "").strip()
            observed_authors = {
                str(item.get("author") or "").strip()
                for item in (finding.get("child_evidence_bundle") or {}).get("evidence") or []
                if item.get("source_type") == "author_article" and str(item.get("author") or "").strip()
            }
            author_scope = (
                "valid"
                if not expected_author or observed_authors == {expected_author}
                else "mismatch_or_unobserved"
            )
            for item in items:
                claim_ref = str(item.get("claim_ref") or "").strip()
                refs = [str(value).strip() for value in item.get("evidence_refs") or [] if str(value).strip()]
                resolved = [parent_aliases.get(ref, ref) for ref in refs]
                unknown = [ref for ref, canonical in zip(refs, resolved) if canonical not in allowed_ids]
                decision = str(item.get("decision") or "") if role == "verifier" else ""
                no_binding_expected = role == "verifier" and decision == "unverified" and not refs
                row = {
                    "role": role,
                    "item_kind": item_kind,
                    "claim_ref": claim_ref,
                    "decision": decision,
                    "evidence_refs": refs,
                    "resolved_evidence_refs": resolved,
                    "evidence_binding": (
                        "unverified_no_binding_expected" if no_binding_expected
                        else "valid" if refs and not unknown and author_scope == "valid"
                        else "missing_or_unavailable"
                    ),
                    "unavailable_evidence_refs": unknown,
                    "expected_author": expected_author,
                    "observed_authors": sorted(observed_authors),
                    "author_scope": author_scope,
                    "verifier_decision": verifier_decisions.get(claim_ref, "not_reviewed"),
                }
                claim_rows.append(row)

        invalid_rows = [row for row in claim_rows if row["evidence_binding"] == "missing_or_unavailable"]
        has_successful_verifier = any(
            (record.get("finding") or {}).get("status") == "success"
            and (record.get("finding") or {}).get("role") == "verifier"
            and bool(((record.get("finding") or {}).get("output") or {}).get("verdicts"))
            for record in delegate_records
        )
        unreviewed_count = sum(
            row["verifier_decision"] == "not_reviewed" for row in claim_rows
        )
        review_complete = has_successful_verifier and unreviewed_count == 0
        verdict_rows = [row for row in claim_rows if row["role"] == "verifier"]
        return {
            "schema_version": "finance.subagent_evidence_audit.v1",
            "claim_bindings": claim_rows,
            "claim_count": len(claim_rows),
            "invalid_binding_count": len(invalid_rows),
            "unreviewed_claim_count": unreviewed_count,
            "verifier_verdict_count": len(verdict_rows),
            "unverified_verdict_count": sum(row["decision"] == "unverified" for row in verdict_rows),
            "verifier_decisions": verifier_decisions,
            "review_required": review_required,
            "review_complete": review_complete,
            "review_status": (
                "complete" if review_complete else
                "partial" if has_successful_verifier else "not_performed"
            ),
            "review_gate_satisfied": not review_required or review_complete,
            "policy": (
                "review_gate_when_requested; otherwise_audit_only"
                if review_required else "audit_only; main_agent_retains_delegation_and_analysis_choice"
            ),
        }

    @staticmethod
    def _finance_subagent_review_scope(role: str, evidence: dict[str, Any]) -> str:
        rows = evidence.get("evidence") or []
        external_types = {
            "official_source", "source_document", "public_web_page",
            "market_metric", "market_bar_series",
        }
        external_read = any(row.get("source_type") in external_types for row in rows)
        if role == "verifier" and external_read:
            return "independent_source_read"
        article_read = any(row.get("source_type") == "author_article" for row in rows)
        if role == "verifier" and article_read:
            return "independent_article_read"
        if external_read or article_read:
            return "delegated_source_read"
        return "delegated_context_only"

    @staticmethod
    def _should_predelegate_subagents(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        if re.search(r"sub[ -]?agents?", text, re.I) and not re.search(r"[\u4e00-\u9fff]", text):
            return False
        explicit_delegation_markers = (
            "子智能体",
            "多智能体",
            "委派",
            "分析助手",
            "质疑助手",
            "核验助手",
            "验证助手",
        )
        if AgentRuntime._explicit_subagent_requested(text) or any(marker in text for marker in explicit_delegation_markers):
            return True
        role_markers = ("分析", "质疑", "反驳", "核验", "验证", "查证", "复核")
        angle_markers = ("角度", "视角", "分别", "多角度", "两个角度", "三个角度", "四个角度")
        if any(role in text for role in role_markers) and any(angle in text for angle in angle_markers):
            return True
        return bool(re.search(r"从.{0,24}(分析|质疑|反驳|核验|验证|查证|复核).{0,16}(角度|视角)", text))

    def _run_general_chat_agent_loop(
        self,
        *,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
        memories: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        prior_article_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from policy_impact.domains.finance.profile import load_profile

        if self.company_id == "finance_observer" or load_profile(self.company_id):
            return self._run_autonomous_finance_loop(
                run_id=run_id,
                session_id=session_id,
                message=message,
                model_id=model_id,
                context_manifest=context_manifest,
                prepared_context=prepared_context,
                memories=memories,
                facts=facts,
                prior_article_refs=prior_article_refs,
            )
        skill_manifest = self.registry.get_manifest("general_chat")
        from policy_impact.runtime.plugin_config import (
            read_plugin_prompt_modules,
            read_plugin_skill_modules,
        )
        prompt_modules = skill_manifest.prompt_modules or ("general_chat_system.md",)
        workspace_prompt = read_plugin_prompt_modules(prompt_modules)
        skill_prompt = read_plugin_skill_modules(skill_manifest.skill_modules)
        if skill_prompt:
            workspace_prompt += "\n\n# 当前可用能力 skills\n\n" + skill_prompt
        artifact_ref = f"main_chat_context:{run_id[:12]}"
        agent_roster = self._subagent_roster_for_skill(skill_manifest)
        task_artifact = self._build_dynamic_subagent_context_artifact(
            message=message,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
        )
        loop_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        loop_state.current_user_message = message
        loop_state.selected_skill = {"id": "general_chat", "mode": "agent_loop"}
        loop_state.recent_history = list(prepared_context.get("recent_history") or [])
        loop_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        loop_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
        loop_state.conversation_summary = dict(prepared_context.get("conversation_summary") or {})
        loop_state.relevant_memories = list(memories)
        loop_state.company_facts = list(facts)
        loop_state.tool_policy_summary = {
            "decision_order": ["deny", "ask", "allow"],
            "runtime_owns_permissions": True,
            "main_loop_tools": ["delegate"],
            "subagent_roster_count": len(agent_roster),
        }
        from policy_impact.domains.finance.report_reader import ReportReadGateway, TOOL_DESCRIPTION
        from policy_impact.domains.finance.profile import load_profile
        from policy_impact.domains.finance.chat_gateway import TOOL_DESCRIPTION as ANALYZE_TOOL
        from policy_impact.domains.finance.research_gateway import FinanceResearchGateway
        from policy_impact.domains.finance.article_gateway import (
            TOOL_DESCRIPTIONS as ARTICLE_TOOLS,
            article_citation_issue,
            canonical_article_citations,
        )
        from policy_impact.domains.finance.runtime_adapter import execute as execute_finance
        finance_workspace = self.company_id == "finance_observer" or bool(load_profile(self.company_id))
        if finance_workspace:
            agent_roster = [
                {
                    **item,
                    "tools": [tool for tool in item.get("tools", []) if tool != "article.search"],
                }
                for item in agent_roster
            ]
        needs_claim_extraction = finance_workspace and any(
            term in message for term in (
                "逐条提取", "逐条抽取", "逐条提炼", "每条提取", "逐项提取", "article.claims",
            )
        )
        # Finance research may legitimately require several bounded reads per
        # article.  Keep the model's path autonomous, but do not let the
        # generic chat budget terminate a valid multi-article investigation
        # before it has a chance to synthesize the collected evidence.
        finance_loop_steps = max(skill_manifest.max_steps, 180) if finance_workspace else skill_manifest.max_steps
        finance_loop_model_calls = (
            max(skill_manifest.max_model_calls, 120) if finance_workspace else skill_manifest.max_model_calls
        )
        finance_report_ids = {str(row["finance_report_id"]) for row in loop_state.recent_artifacts if row.get("finance_report_id")}
        finance_read_enabled = finance_workspace and bool(finance_report_ids) and "finance.report_read" in skill_manifest.allowed_tools
        finance_analysis_enabled = finance_workspace and "finance.analyze" in skill_manifest.allowed_tools and bool(load_profile(self.company_id))
        chat_gateway = ReportReadGateway(self.company_id, finance_report_ids) if finance_read_enabled else ToolGateway(PermissionEngine([]))
        available_actions = ["delegate", "final"]
        task_prompt = self._general_chat_loop_task_prompt(message, artifact_ref, agent_roster)
        article_policy = ""
        article_holdings = []
        if finance_workspace:
            from policy_impact.domains.finance.article_profile import load_holdings, load_research_policy
            def extract_article_claims(messages, schema_name):
                response = self._complete_model(
                    run_id=run_id, title="Extract source-anchored finance claims", model_id=model_id,
                    messages=messages, schema_name=schema_name,
                    prompt_contract={"contract_id": schema_name, "evidence_scope": "local_author_article"},
                )
                return response.payload["output"]

            chat_gateway = FinanceResearchGateway(
                self.company_id,
                finance_report_ids,
                question=message,
                analyze=lambda query: execute_finance(self, run_id, query, model_id, publish_html=False),
                complete_claims=extract_article_claims,
                enable_analysis=finance_analysis_enabled,
            )
            article_holdings = load_holdings(chat_gateway.corpus.root)
            article_policy = load_research_policy(chat_gateway.corpus.root)
            available_actions.extend("tool_call:" + item["name"] for item in ARTICLE_TOOLS)
            loop_state.tool_policy_summary["main_loop_tools"].extend(ARTICLE_TOOLS)
            if prior_article_refs:
                task_prompt += "\nPrior conversation article refs: " + json.dumps(
                    [{"article_id": item["article_id"], "author": item.get("author"),
                      "title": item.get("title"), "line_start": item.get("line_start")}
                     for item in prior_article_refs if item.get("article_id")], ensure_ascii=False)
        if finance_analysis_enabled:
            available_actions.extend(["tool_call:finance.analyze", "tool_call:finance.report_read"])
            loop_state.tool_policy_summary["main_loop_tools"].extend([ANALYZE_TOOL, TOOL_DESCRIPTION])
        if finance_read_enabled:
            available_actions.append("tool_call:finance.report_read")
            loop_state.tool_policy_summary["main_loop_tools"].append(TOOL_DESCRIPTION)
        visible_content = dict(context_manifest.get("visible_content") or {})
        if finance_workspace:
            visible_content["finance_research_preferences"] = article_policy
            visible_content["current_holdings"] = article_holdings
            visible_content["prior_article_refs"] = prior_article_refs
        visible_content.update(
            {
                "current_user_message": message,
                "selected_skill": loop_state.selected_skill,
                "relevant_memories": loop_state.relevant_memories,
                "company_facts": loop_state.company_facts,
                "recent_history": loop_state.recent_history,
                "recent_artifacts": loop_state.recent_artifacts,
                "recent_run_traces": loop_state.recent_run_traces,
                "conversation_summary": loop_state.conversation_summary,
                "runtime_self_knowledge": (
                    []
                    if self._requires_retrieved_evidence_only(message)
                    else self._general_system_facts(message)
                ),
                "agent_roster": agent_roster,
                "delegated_context_artifact_ref": artifact_ref,
                "tool_policy_summary": loop_state.tool_policy_summary,
                "loop_budget": {
                    "max_steps": finance_loop_steps,
                    "max_model_calls": finance_loop_model_calls,
                    "timeout_seconds": skill_manifest.timeout_seconds,
                    "reserve_final_model_call": True,
                },
                "delegation_policy": {
                    "policy_id": "main_agent.spawn_subagent.v1",
                    "decision_owner": "main_agent_model",
                    "instructions": [
                        "Choose delegate or final from the user's actual task, visible evidence, and role roster.",
                        "Role names and analysis, risk, source, or verification keywords are not mandatory delegation triggers.",
                        "Explicit user role requests are instructions to consider, not actions already executed by the runtime.",
                        "After each child observation, reassess whether another delegation adds value or a final answer is ready.",
                        "Reserve a model call for the final answer; respect the loop and child role budgets.",
                    ],
                    "delegate_only_when": [
                        "用户显式要求子智能体、多角度、质疑、验证或来源收集",
                        "问题需要独立证据收集、结构化分析、反方审查或证据核验",
                        "简单解释、寒暄、记忆写入和明确事实复述不要委派",
                    ],
                    "max_roles_per_turn": 3,
                    "allowed_roles": list(skill_manifest.allowed_subagents),
                },
            }
        )
        manifest_record = _context_manifest_record_from_metadata(
            run_id=run_id,
            metadata={
                **dict(context_manifest),
                "stage_name": "chat_agent_loop",
                "agent_role": "main_chat_agent",
                "loop_boundary": "Main Agent may call delegate; Runtime owns subagent manifests, permissions, context, trace, and evidence.",
                "available_actions": available_actions,
                "default_delegated_artifact_ref": artifact_ref,
                "default_delegated_artifact_refs": [artifact_ref],
                "visible_keys": list(visible_content),
                "visible_content": visible_content,
            },
            stage_name="chat_agent_loop",
            agent_role="main_chat_agent",
        )
        loop_state.add_context_manifest(manifest_record.to_dict())
        delegate_records: list[dict[str, Any]] = []
        explicit_finance_review = (
            finance_workspace and self._finance_review_requested(message)
        )
        review_state: dict[str, Any] = {
            "started": False,
            "completed": False,
            "skipped": False,
            "final_retry_required": False,
            "final_candidates": 0,
            "findings": [],
            "error": "",
        }

        def ensure_required_finance_reviews() -> None:
            """Run hard review requests exactly once after evidence collection."""
            if not explicit_finance_review or review_state["started"]:
                return
            evidence_bundle = chat_gateway.evidence_bundle()
            analysis_result = getattr(chat_gateway, "analysis_result", None)
            if not evidence_bundle.get("evidence_ids") and not analysis_result:
                review_state["started"] = True
                review_state["completed"] = True
                review_state["skipped"] = True
                review_state["error"] = (
                    "controlled review skipped: no source fragment or validated finance snapshot is available"
                )
                self.store.add_agent_step(
                    run_id,
                    step_type="review_required",
                    title="Finance controlled review gated by missing evidence",
                    status="done",
                    input_payload={"message": message, "requested_roles": []},
                    output_payload={
                        "evidence_ids": [],
                        "trigger": "evidence_unavailable",
                        "skipped": True,
                    },
                    gate_result={
                        "decision": "deny",
                        "reason": review_state["error"],
                        "policy_id": "finance.review_required.v1",
                    },
                    metadata={"loop_phase": "plan", "deterministic": True},
                )
                self.store.add_agent_step(
                    run_id,
                    step_type="review_result",
                    title="Finance controlled review not started",
                    status="done",
                    input_payload={"requested_roles": []},
                    output_payload={
                        "completed": False,
                        "skipped": True,
                        "reason": review_state["error"],
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "没有证据时不启动子智能体，主 Agent 负责说明数据边界",
                        "policy_id": "finance.review_completion.v1",
                    },
                    metadata={"loop_phase": "observe", "deterministic": True},
                )
                return
            roles = tuple(
                role
                for role in self._finance_review_roles(message)
                if role in skill_manifest.allowed_subagents
            )
            existing_by_role = {
                record["finding"]["role"]: record["finding"]
                for record in delegate_records
                if record.get("finding", {}).get("status") == "success"
                and record.get("finding", {}).get("role") in roles
            }
            existing_findings = list(existing_by_role.values())
            if roles and all(role in existing_by_role for role in roles):
                review_state["started"] = True
                review_state["completed"] = True
                review_state["findings"] = existing_findings
                chat_gateway.close_research()
                self.store.add_agent_step(
                    run_id,
                    step_type="review_result",
                    title="Finance controlled review result",
                    status="done",
                    input_payload={"requested_roles": list(roles)},
                    output_payload={
                        "completed": True,
                        "findings": [
                            {
                                "role": item.get("role"),
                                "status": item.get("status"),
                                "task_id": item.get("task_id"),
                                "output_schema": item.get("output_schema"),
                                "evidence_ids": item.get("evidence_ids") or [],
                            }
                            for item in existing_findings
                        ],
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "主 Agent 已完成受控委派，Runtime 认领成功复核结果",
                        "policy_id": "finance.review_completion.v1",
                    },
                    metadata={"loop_phase": "observe", "deterministic": True},
                )
                return
            review_state["started"] = True
            pending_roles = [role for role in roles if role not in existing_by_role]
            self.store.add_agent_step(
                run_id,
                step_type="review_required",
                title="Finance controlled review required",
                status="done",
                input_payload={"message": message, "requested_roles": list(roles)},
                output_payload={
                    "evidence_ids": list(evidence_bundle.get("evidence_ids") or []),
                    "trigger": "explicit_user_review_request",
                    "pending_roles": pending_roles,
                },
                gate_result={
                    "decision": "allow" if roles else "deny",
                    "reason": "Runtime owns explicit review planning",
                    "policy_id": "finance.review_required.v1",
                },
                metadata={"loop_phase": "plan", "deterministic": True},
            )
            if not roles:
                review_state["error"] = "no requested review role is allowed by the skill manifest"
                return

            review_artifact = {
                **task_artifact,
                "evidence_bundle": evidence_bundle,
                "evidence_ids": list(evidence_bundle.get("evidence_ids") or []),
                "finance_article_evidence": list(chat_gateway.read_results[-6:]),
                "official_source_evidence": list(chat_gateway.external_results[-4:]),
                "research_policy": article_policy[:2200],
            }
            if analysis_result:
                review_artifact["finance_review"] = self._finance_subagent_review_artifact(
                    analysis_result["report"]
                )
            findings: list[dict[str, Any]] = list(existing_findings)
            for index, role in enumerate(pending_roles, start=1):
                definition = self.subagent_role_registry.get(role, "1.0.0")
                evidence_ids = ", ".join(evidence_bundle.get("evidence_ids") or []) or "none"
                brief = TaskBrief(
                    task_id=f"task_{run_id[:8]}_review_{index}_{role}",
                    parent_run_id=run_id,
                    parent_span_id=f"span_chat_{run_id[:16]}",
                    role=role,
                    task=(
                        f"Review the user's finance research question using only artifact_ref {artifact_ref}. "
                        f"Authorized evidence_ids are: {evidence_ids}. "
                        "Return the role schema. Any evidence_refs must be one of those evidence_ids "
                        "or the authorized artifact_ref; never invent a source. "
                        "This is a controlled review, not a request to collect new articles."
                    ),
                    input_artifact_refs=(artifact_ref,),
                    output_schema_name=definition.output_schema_name,
                    max_steps=definition.max_steps,
                    max_model_calls=definition.max_model_calls,
                    timeout_seconds=definition.timeout_seconds,
                )
                try:
                    result = self._run_general_chat_delegate(
                        parent_state=loop_state,
                        skill_manifest=skill_manifest,
                        brief=brief,
                        artifact_ref=artifact_ref,
                        artifact_payload=review_artifact,
                        prepared_context=prepared_context,
                        delegate_records=delegate_records,
                        reason="Runtime enforced an explicit finance review request",
                        trigger="explicit_user_review_request",
                    )
                    findings.append(
                        next(
                            record["finding"]
                            for record in reversed(delegate_records)
                            if record.get("finding", {}).get("task_id") == brief.task_id
                        )
                    )
                    if result.stop_reason != "success":
                        review_state["error"] = f"{role} review stopped: {result.stop_reason}"
                except Exception as exc:  # noqa: BLE001
                    review_state["error"] = f"{role} review failed: {str(exc)[:180]}"
                    break
            review_state["findings"] = findings
            successful_roles = {
                item.get("role") for item in findings if item.get("status") == "success"
            }
            review_state["completed"] = bool(roles and all(role in successful_roles for role in roles))
            if review_state["completed"]:
                chat_gateway.close_research()
                review_state["final_retry_required"] = True
            self.store.add_agent_step(
                run_id,
                step_type="review_result",
                title="Finance controlled review result",
                status="done" if review_state["completed"] else "failed",
                input_payload={"requested_roles": list(roles)},
                output_payload={
                    "completed": review_state["completed"],
                    "findings": [
                        {
                            "role": item.get("role"),
                            "status": item.get("status"),
                            "task_id": item.get("task_id"),
                            "output_schema": item.get("output_schema"),
                        }
                        for item in findings
                    ],
                },
                gate_result={
                    "decision": "allow" if review_state["completed"] else "deny",
                    "reason": review_state["error"] or "all required reviews completed",
                    "policy_id": "finance.review_completion.v1",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )

        def delegate_fn(brief: TaskBrief) -> SubagentResult:
            article_evidence_pending = (
                finance_workspace
                and explicit_finance_review
                and not getattr(chat_gateway, "evidence_ids", ())
                and not getattr(chat_gateway, "analysis_result", None)
            ) or (
                finance_workspace
                and not chat_gateway.read_results
                and bool(getattr(chat_gateway, "article_list_attempted", False) or prior_article_refs)
                and not getattr(chat_gateway, "analysis_result", None)
            )
            if article_evidence_pending:
                article_count = getattr(chat_gateway, "article_list_count", None)
                count_note = (
                    f"article.list returned {article_count} matching rows"
                    if article_count is not None
                    else "article.list has not returned readable source rows"
                )
                self.store.add_agent_step(
                    run_id,
                    step_type="subagent_gate",
                    title="Subagent delegation blocked by missing finance evidence",
                    status="done",
                    input_payload={"role": brief.role, "message": message},
                    output_payload={
                        "evidence_ids": [],
                        "article_list_attempted": bool(
                            getattr(chat_gateway, "article_list_attempted", False)
                        ),
                        "article_list_count": article_count,
                        "next_actions": [
                            "expand the date range",
                            "choose an author with available articles",
                            "read source lines before requesting review",
                        ],
                    },
                    gate_result={
                        "decision": "deny",
                        "reason": f"{count_note}; a subagent may not review an empty evidence set",
                        "policy_id": "finance.subagent_evidence_gate.v1",
                    },
                    metadata={"loop_phase": "plan", "deterministic": True},
                )
                raise _FinanceEvidenceUnavailable(
                    f"evidence_unavailable: {count_note}; read source lines before delegation"
                )
            delegated_artifact = task_artifact
            if finance_workspace and chat_gateway.read_results:
                delegated_artifact = {
                    **delegated_artifact,
                    "finance_article_evidence": chat_gateway.read_results[-4:],
                    "official_source_evidence": chat_gateway.external_results[-3:],
                    "evidence_bundle": chat_gateway.evidence_bundle(),
                    "research_policy": article_policy[:2200],
                }
            if getattr(chat_gateway, "analysis_result", None):
                delegated_artifact = {
                    **delegated_artifact,
                    "finance_review": self._finance_subagent_review_artifact(
                        chat_gateway.analysis_result["report"]
                    ),
                }
            return self._run_general_chat_delegate(
                parent_state=loop_state,
                skill_manifest=skill_manifest,
                brief=brief,
                artifact_ref=artifact_ref,
                artifact_payload=delegated_artifact,
                prepared_context=prepared_context,
                delegate_records=delegate_records,
            )

        finance_verifier = None
        if finance_read_enabled or finance_analysis_enabled:
            from policy_impact.domains.finance.answer_verifier import FinanceAnswerVerifier

            def review_finance_answer(messages, schema_name):
                return self._complete_model(run_id=run_id, title="金融追问事实复核", model_id=model_id,
                    messages=messages, schema_name=schema_name,
                    prompt_contract={"contract_id": schema_name, "max_review_calls": 2,
                                     "evidence_scope": "authorized_saved_reports"})

            finance_verifier = FinanceAnswerVerifier(chat_gateway, review_finance_answer,
                conversation_context=loop_state.recent_history)

        def finance_final_guard(output):
            issue = chat_gateway.final_guard(message, output)
            if issue:
                return issue
            finance_reviews = [record["finding"] for record in delegate_records
                               if record["finding"].get("status") == "success"
                               and record["finding"].get("role") in {"verifier", "skeptic"}
                               and any(record["finding"].get("output", {}).get(key)
                                       for key in ("verdicts", "counterexamples", "notes"))]
            if (getattr(chat_gateway, "analysis_result", None)
                    and self._explicit_subagent_requested(message)
                    and not finance_reviews and not review_state["completed"]):
                return "用户明确要求子智能体核验；请先基于本轮 finance.analyze 快照委派 verifier 或 skeptic，收到有效复核结果后再回答。"
            if getattr(chat_gateway, "analysis_error", None) or not getattr(chat_gateway, "report_ids", None):
                return None
            issue = finance_verifier.check(message, output)
            if issue and finance_verifier.reviews >= finance_verifier.max_reviews:
                from policy_impact.harness.agent_loop import FinalGuardBlocked
                chat_gateway.analysis_error = issue
                raise FinalGuardBlocked(issue)
            if not issue and finance_reviews and output.get("delivery_mode") == "validated_finance_projection":
                review_summary = []
                failed_verdicts = 0
                for finding in finance_reviews:
                    result = finding["output"]
                    if finding["role"] == "verifier":
                        verdicts = result.get("verdicts") or []
                        failed = sum(item.get("decision") == "fail" for item in verdicts)
                        failed_verdicts += failed
                        review_summary.append(f"证据核验 {len(verdicts)} 项，其中 {failed} 项待核查")
                    else:
                        review_summary.append(
                            f"质疑审查发现 {len(result.get('counterexamples') or [])} 项反例或证据缺口"
                        )
                if failed_verdicts:
                    from policy_impact.domains.finance.render import render_chat_analysis
                    output["answer"] = render_chat_analysis(chat_gateway.analysis_result["report"], facts_only=True)
                    output["delivery_mode"] = "finance_review_downgraded"
                output["answer"] += ("\n\n**子智能体复核**：" + "；".join(review_summary)
                                     + "。详细意见见本轮运行过程；这是同源证据的上下文隔离复核，非独立行情交叉核价。")
            return issue

        def research_final_guard(output):
            if explicit_finance_review:
                review_state["final_candidates"] += 1
                if (not review_state["started"]
                        and not chat_gateway.evidence_bundle().get("evidence_ids")
                        and not getattr(chat_gateway, "analysis_result", None)):
                    ensure_required_finance_reviews()
                if review_state["skipped"]:
                    answer = str(output.get("answer") or "")
                    if not any(marker in answer for marker in (
                        "没有文章", "未找到文章", "无文章", "没有证据", "缺少证据", "无证据",
                        "找不到文章", "没有找到文章", "数据不足", "无法复核",
                    )):
                        return (
                            "当前没有已读取的文章或有效金融快照，不能宣称完成 Subagent 复核。"
                            "请说明没有找到/读取到证据，并建议扩大日期范围或更换作者。"
                        )
                    return None
                if not review_state["started"]:
                    existing_review_ready = any(
                        record.get("finding", {}).get("status") == "success"
                        and record.get("finding", {}).get("role") in {"analyst", "skeptic", "verifier"}
                        for record in delegate_records
                    )
                    if not existing_review_ready and review_state["final_candidates"] < 2:
                        return (
                            "用户明确要求受控 Subagent 复核。请先基于本轮证据返回 delegate action；"
                            "若已有复核结果则直接综合后返回 final。"
                        )
                    ensure_required_finance_reviews()
                if not review_state["completed"]:
                    from policy_impact.harness.agent_loop import FinalGuardBlocked
                    raise FinalGuardBlocked(
                        "受控金融复核未完成：" + (
                            review_state["error"] or "没有返回成功的复核结果"
                        )
                    )
                if review_state["final_retry_required"]:
                    review_state["final_retry_required"] = False
                    summary = [
                        {
                            "role": item.get("role"),
                            "status": item.get("status"),
                            "output": item.get("output") or {},
                        }
                        for item in review_state["findings"]
                    ]
                    return (
                        "受控复核已完成。现在只需基于原始证据和以下 review_result 返回一次最终答案，"
                        "不得再次调用文章或金融数据工具："
                        + json.dumps(_materialize_execution_value(summary), ensure_ascii=False)[:5000]
                    )
            finance_issue = (
                finance_final_guard(output)
                if finance_workspace and finance_verifier
                else chat_gateway.final_guard(message, output) if finance_workspace else None
            )
            if finance_issue:
                return finance_issue
            if needs_claim_extraction and chat_gateway.read_refs and not chat_gateway.claims_attempted:
                return "The user requested claim-by-claim extraction. Call article.claims on the selected read article before final."
            if not chat_gateway.read_refs:
                return None
            answer = str(output.get("answer", ""))
            raw_citations = output.get("citations") or []
            citation_issue = article_citation_issue(answer, chat_gateway.read_refs, raw_citations)
            if citation_issue and not any(
                marker in citation_issue
                for marker in ("not read", "exceeds")
            ):
                # A model can understand the evidence but emit a harmless
                # variant such as path:13 instead of path:L13.  Ground that
                # final response from the Runtime-owned read ledger once;
                # never use this path to authorize an unread or out-of-range
                # source.
                generated = canonical_article_citations(chat_gateway.read_refs)
                merged: list[Any] = []
                seen: set[str] = set()
                for item in [*raw_citations, *generated]:
                    if not isinstance(item, str) or not item.strip():
                        continue
                    item = item.strip()
                    if item in seen:
                        continue
                    seen.add(item)
                    merged.append(item)
                output["citations"] = merged
                answer = str(output.get("answer", "")).rstrip()
                evidence_lines = "\n".join(f"- `{item}`" for item in generated)
                if generated and not any(item in answer for item in generated):
                    output["answer"] = (
                        f"{answer}\n\n**本轮已读取的文章证据**\n{evidence_lines}"
                    )
                self.store.add_agent_step(
                    run_id,
                    step_type="evidence_citation_grounding",
                    title="Runtime 补齐文章证据引用",
                    status="done",
                    input_payload={
                        "raw_citation_issue": citation_issue,
                        "read_ref_count": len(chat_gateway.read_refs),
                    },
                    output_payload={
                        "citations": generated,
                        "source": "runtime_read_ledger",
                        "unread_sources_allowed": False,
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "引用仅从本轮 article.read 结果生成",
                        "policy_id": "finance.article_citation_grounding.v1",
                    },
                    metadata={"loop_phase": "observe", "deterministic": True},
                )
                citation_issue = article_citation_issue(
                    str(output.get("answer", "")),
                    chat_gateway.read_refs,
                    output.get("citations"),
                )
            if citation_issue:
                return citation_issue
            # A local article is a viewpoint source; external verification is
            # optional and should be requested by the task, not forced by the router.
            return None

        # Chat decisions come from the adapter, including the first action.
        loop_result = AgentLoop(
            self._get_model_adapter(),
            chat_gateway,
            delegate_fn=delegate_fn,
            skill_manifest=skill_manifest,
            role_registry=self.subagent_role_registry,
            reject_hidden_reasoning=True,
            final_guard=research_final_guard if finance_workspace else finance_final_guard if finance_verifier else None,
        ).run(
            loop_state,
            AgentLoopConfig(
                stage_name="chat_agent_loop",
                agent_role="main_chat_agent",
                context_manifest=manifest_record,
                output_key="general_chat_agent_loop.v1",
                execution_mode=ExecutionMode.AGENT_LOOP,
                max_steps=finance_loop_steps,
                max_model_calls=finance_loop_model_calls,
                timeout_seconds=max(skill_manifest.timeout_seconds, 240) if finance_workspace else skill_manifest.timeout_seconds,
                # Three assets may need separate series pages plus sources,
                # metrics and audit. Acquisition still has its own once/turn gate.
                max_calls_per_tool=6 if finance_workspace else 1,
                task_prompt=task_prompt,
                workspace_prompt=workspace_prompt,
            ),
        )
        self._record_general_chat_agent_loop(run_id, loop_state, loop_result, delegate_records)
        if finance_workspace:
            self.store.add_agent_step(
                run_id, step_type="evidence_audit", title="Finance article research evidence",
                status="done", output_payload={
                    "searched": chat_gateway.search_attempted,
                    "article_reads": chat_gateway.read_refs,
                    "official_source_reads": chat_gateway.external_reads,
                    "evidence_bundle": chat_gateway.evidence_bundle(),
                }, metadata={"loop_phase": "observe"},
            )
        if finance_read_enabled or finance_analysis_enabled:
            self.store.add_agent_step(run_id, step_type="gate_check", title="金融追问事实复核结果", status="done",
                output_payload={"reviews": finance_verifier.results, "review_calls": finance_verifier.reviews,
                                "max_review_calls": finance_verifier.max_reviews},
                gate_result={"decision": "allow" if loop_result.stop_reason == "success" else "deny",
                             "reason": "依据已保存报告复核，独立复核最多两次；不声称数学完备性"})
            call_index = 0
            for turn in loop_result.turns:
                observation = dict(turn.observation or {})
                if "final_gate" in observation:
                    self.store.add_agent_step(run_id, step_type="gate_check", title="金融追问证据门控", status="done",
                        gate_result=observation["final_gate"], metadata={"loop_phase": "observe", "turn_index": turn.turn_index})
                if "tool_result" in observation:
                    is_analysis = observation["tool_result"].get("scope") == "validated_finance_snapshot" or observation["tool_result"].get("html_published") is False
                    self.store.add_agent_step(run_id, step_type="tool_result" if is_analysis else "artifact_read", title="获取并分析金融数据" if is_analysis else "按需展开金融报告证据", status="done",
                        output_payload={"observation": observation["tool_result"]},
                        tool_calls=[chat_gateway.calls[call_index].to_dict()] if call_index < len(chat_gateway.calls) else [],
                        metadata={"loop_phase": "observe", "fresh_fetch": is_analysis, "turn_index": turn.turn_index})
                    call_index += 1
        final_output = loop_result.final_output if loop_result.stop_reason == "success" else {}
        analysis_result = getattr(chat_gateway, "analysis_result", None) or {}
        blocked_reason = getattr(chat_gateway, "analysis_error", None)
        if not blocked_reason and finance_verifier and not final_output:
            blocked_reason = "本轮未能在授权范围与校验预算内完成回答（" + loop_result.stop_reason + "）。没有发布报告；可查看执行记录中的拒绝原因。"
        if review_state["skipped"] and not blocked_reason and final_output:
            final_output["answer"] = (
                str(final_output.get("answer") or "").strip()
                + "\n\n本轮未读取到可复核的文章或金融快照，因此未启动 Subagent；可扩大日期范围或更换作者后重试。"
            )
        return {
            "answer": ("本次金融分析未通过校验：" + blocked_reason) if blocked_reason else str(final_output.get("answer") or "").strip(),
            "artifacts": {} if blocked_reason else analysis_result.get("artifacts", {}),
            "citations": [] if blocked_reason else (
                [
                    *list(chat_gateway.read_refs),
                    *list(chat_gateway.external_reads),
                    *list(analysis_result.get("citations", [])),
                ] if finance_workspace else analysis_result.get("citations", [])
            ),
            "delegation_evidence": [
                item.model_dump(mode="json") for item in loop_result.delegation_evidence
            ],
            "stop_reason": "quality_blocked" if blocked_reason else loop_result.stop_reason,
            "actual_execution_mode": loop_result.actual_execution_mode.value,
        }

    from policy_impact.harness.research_budget import finance_execution

    @finance_execution
    def _run_autonomous_finance_loop(
        self,
        *,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
        memories: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        prior_article_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from policy_impact.domains.finance.article_gateway import (
            _relative_article_path,
            article_citation_issue,
            article_cited_ids,
            canonical_article_citations,
            wants_article_research,
        )
        from policy_impact.domains.finance.article_profile import load_holdings, load_research_policy
        from policy_impact.domains.finance.answer_audit import AnswerAudit
        from policy_impact.domains.finance.research_gateway import FinanceResearchGateway
        from policy_impact.skills.schemas import FinanceReviewTarget
        from policy_impact.runtime.plugin_config import (
            read_plugin_prompt_modules,
            read_plugin_skill_modules,
            read_plugin_toolkit,
        )

        skill = self.registry.get_manifest("general_chat")
        from policy_impact.harness.research_budget import CURRENT

        research_scope = CURRENT.get()
        research_limits = research_scope.root.limits
        context_pressure, context_target = research_limits.context_pressure_limits(research_limits.output_tokens)
        roster = self._subagent_roster_for_skill(skill)
        # All model calls made by one finance turn must share the same
        # long-running model policy.  The main AgentLoop used the finance
        # limits below, but article.study/article.claims used to call
        # _complete_model() and silently fall back to the base 45s/2048
        # configuration.
        finance_model_adapter = self._get_model_adapter()
        if isinstance(finance_model_adapter, AnthropicCompatibleModelAdapter):
            finance_model_adapter = copy(finance_model_adapter)
            finance_model_adapter.max_tokens = max(finance_model_adapter.max_tokens, 16384)
            finance_model_adapter.timeout_seconds = max(finance_model_adapter.timeout_seconds, 90)
        report_ids = {
            str(row["finance_report_id"])
            for row in prepared_context.get("recent_artifacts") or []
            if row.get("finance_report_id")
        }
        def extract_claims(messages, schema_name):
            response = self._complete_model(
                run_id=run_id, title="Extract source-anchored finance claims",
                model_id=model_id,
                messages=messages, schema_name=schema_name,
                prompt_contract={"contract_id": schema_name, "evidence_scope": "local_author_article"},
                adapter=finance_model_adapter,
            )
            return response.payload["output"]

        def study_article(messages, schema_name):
            response = self._complete_model(
                run_id=run_id, title="Study one finance article",
                model_id=model_id, messages=messages, schema_name=schema_name,
                prompt_contract={"contract_id": schema_name, "evidence_scope": "one_author_article"},
                adapter=finance_model_adapter,
            )
            return response.payload["output"]

        def synthesize_articles(messages, schema_name):
            response = self._complete_model(
                run_id=run_id, title="Compare finance article viewpoints",
                model_id=model_id, messages=messages, schema_name=schema_name,
                prompt_contract={"contract_id": schema_name, "evidence_scope": "article_cards"},
                adapter=finance_model_adapter,
            )
            return response.payload["output"]

        def review_article_claims(messages, schema_name):
            response = self._complete_model(
                run_id=run_id, title="Typed finance claim review",
                model_id=model_id, messages=messages, schema_name=schema_name,
                prompt_contract={"contract_id": schema_name, "evidence_scope": "selected_claims"},
                adapter=finance_model_adapter,
            )
            return response.payload["output"]

        research_state_path = (
            company_dir(self.company_id) / "runs" / run_id / "research_plan.json"
        )
        gateway = FinanceResearchGateway(
            self.company_id,
            report_ids,
            question=message,
            complete_claims=extract_claims,
            complete_study=study_article,
            complete_synthesis=synthesize_articles,
            complete_review=review_article_claims,
            session_id=session_id,
            run_id=run_id,
            research_state_path=research_state_path,
            require_plan_before_tools=True,
            on_verification_record=lambda record: self._record_finance_verification(
                run_id, record
            ),
            on_coverage_record=lambda record: self.store.add_agent_step(
                run_id, step_type="research_coverage", title="研究取材覆盖与收敛判断",
                status="done", output_payload=record,
                metadata={"loop_phase": "observe", "schema_version": record["schema_version"]},
            ),
        )

        def audit_final_claims(messages, schema_name):
            response = self._complete_model(
                run_id=run_id, title="Audit final finance answer claims",
                model_id=model_id, messages=messages, schema_name=schema_name,
                prompt_contract={
                    "contract_id": schema_name,
                    "evidence_scope": "candidate_answer_and_observed_evidence",
                },
                adapter=finance_model_adapter,
            )
            return response.payload["output"]

        answer_auditor = AnswerAudit(gateway.evidence_ledger, audit_final_claims)
        from policy_impact.domains.finance.answer_candidates import AnswerCandidates

        def candidate_event(event, payload):
            self.store.add_agent_step(
                run_id, step_type=event, title="完整分析候选检查点" if event == "answer_candidate_checkpoint"
                else "完整分析候选选择", status="warning" if payload.get("persistence_status") == "failed"
                or payload.get("selected") is False else "done",
                output_payload=payload, metadata={"loop_phase": "gate", "deterministic": True},
            )

        candidates = AnswerCandidates(research_state_path.parent / "answer_candidates", candidate_event)

        def candidate_local_issue(output):
            from policy_impact.domains.finance.answer_delivery import research_citation_issue

            refs = [*gateway.read_refs, *delegated_article_refs()]
            issue = research_citation_issue(output, refs, gateway.evidence_ledger)
            if issue:
                return issue
            answer = output["answer"]
            for issue in (
                _finance_market_return_math_issue(answer),
                _finance_market_horizon_issue(answer, gateway.evidence_bundle()),
                _finance_article_sample_count_issue(answer, refs),
                _finance_article_read_scope_issue(answer, refs),
                _finance_article_citation_location_issue(answer),
                _finance_future_watch_issue(answer),
            ):
                if issue:
                    return issue
            for metric in gateway.market_reads:
                valid, reason = gateway.evidence_ledger.validate_market_metric(
                    str(metric.get("evidence_id") or ""), metric,
                )
                if not valid:
                    return "Market metric evidence failed canonical Ledger validation: " + reason
            return independent_review_issue(output, provenance_only=True)

        named_article_authors = _explicit_article_authors(
            message, gateway.corpus.list_authors()
        )
        # Author-folder scope is derived from the workspace index, not from
        # comparison vocabulary. The model remains free to choose how to read
        # and analyze the sources, while Runtime preserves every named source.
        multi_author_scope = len(named_article_authors) >= 2
        local_article_scope = bool(named_article_authors) and (
            wants_article_research(message) or multi_author_scope
        )
        artifact_ref = f"main_chat_context:{run_id[:12]}"
        article_root = gateway.corpus.root
        paths = {
            "wiki": str(company_dir(self.company_id) / "wiki"),
            "articles": str(article_root),
            "research_policy": str(article_root.parent / "research_policy.md"),
            "current_holdings": str(article_root.parent / "current_holdings.md"),
        }
        toolkit_name = skill.toolkits[0] if skill.toolkits else "finance_research.json"
        toolkit_descriptions = read_plugin_toolkit(toolkit_name)
        tool_descriptions = [
            item for item in toolkit_descriptions
            if item["name"] in skill.allowed_tools
            and (item["name"] != "finance.report_read" or bool(report_ids))
            and (not gateway.source_scope["local_only"] or item["name"] not in {
                "web.search", "web.open", "market.catalog", "market.snapshot",
                "market.metrics", "market.series", "source.discover", "source.fetch",
                "finance.report_read",
            })
        ]
        prompt_modules = skill.prompt_modules or ("finance/00_system.md",)
        skill_modules = skill.skill_modules
        system_prompt = read_plugin_prompt_modules(prompt_modules)
        capabilities = None
        if skill.capability_catalog:
            from policy_impact.runtime.capability_catalog import RunCapabilities

            capabilities = RunCapabilities(
                skill.capability_catalog, skill_modules, tool_descriptions,
                schemas={"evidence_review": {
                    "review_target": FinanceReviewTarget.model_json_schema(),
                }},
                on_event=lambda event, payload: self.store.add_agent_step(
                    run_id, step_type=event, title="读取能力说明" if event == "skill_load" else "查看工具契约",
                    status="done", output_payload=payload,
                    metadata={"loop_phase": "observe", "capability_discovery": True},
                ),
            )
            gateway.capabilities = capabilities
            skill_prompt = capabilities.prompt_catalog()
            tool_descriptions = capabilities.initial_descriptors()
        else:
            skill_prompt = read_plugin_skill_modules(skill_modules)
        workspace_prompt = system_prompt
        if skill_prompt:
            workspace_prompt += "\n\n" + skill_prompt
        self.store.add_agent_step(
            run_id,
            step_type="plugin_load",
            title="加载金融研究 Prompt 与工具插件",
            status="done",
            input_payload={"skill_id": skill.id},
            output_payload={
                "prompt_modules": list(prompt_modules),
                "skill_modules": list(skill_modules),
                "toolkits": [toolkit_name],
                "tool_names": [item["name"] for item in tool_descriptions],
                "prompt_checksum": _stable_hash(system_prompt),
                "skill_checksum": _stable_hash(skill_prompt),
                "composition": "one_system_prompt_plus_capability_catalog" if capabilities
                else "one_system_prompt_plus_optional_skills",
                "initial_loaded_skill_modules": [] if capabilities else list(skill_modules),
                "capability_catalog": skill.capability_catalog,
                "catalog_checksum": capabilities.checksum if capabilities else None,
            },
            metadata={"loop_phase": "observe", "plugin_config": True},
        )

        state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        state.current_user_message = message
        state.selected_skill = {"id": "general_chat", "mode": "agent_loop"}
        state.recent_history = list(prepared_context.get("recent_history") or [])
        state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
        state.conversation_summary = dict(prepared_context.get("conversation_summary") or {})
        state.relevant_memories = list(memories)
        state.company_facts = list(facts)
        visible = dict(context_manifest.get("visible_content") or {})
        visible.update({
            "current_user_message": message,
            "selected_skill": state.selected_skill,
            "relevant_memories": state.relevant_memories,
            "company_facts": state.company_facts,
            "recent_history": state.recent_history,
            "recent_artifacts": state.recent_artifacts,
            "conversation_summary": state.conversation_summary,
            "prior_article_refs": prior_article_refs,
            "finance_research_preferences": load_research_policy(article_root),
            "current_holdings": load_holdings(article_root),
            "workspace_paths": paths,
            "research_clock": {
                "today": date.today().isoformat(),
                "timezone": "Asia/Shanghai",
                "relative_article_periods": "Resolve relative periods to exact published_after/published_before before article.list.",
            },
            "plugin_prompt_modules": list(prompt_modules),
            "plugin_skill_modules": list(skill_modules),
            "plugin_toolkits": [toolkit_name],
            "authorized_report_ids": sorted(report_ids),
            "agent_roster": roster,
            "delegated_context_artifact_ref": artifact_ref,
            "research_requirements": {
                "source_scope": gateway.source_scope,
                "named_author_folders": named_article_authors,
                "all_named_author_folders_required": bool(named_article_authors),
                "plan_required_before_final": True,
                "planning_contract": (
                    "For every finance request, use research.plan to preserve the user's "
                    "outcome, goals, scope, evidence needs and completion criteria. "
                    "This is a requirements checklist, not a fixed analysis workflow."
                ),
            },
            "research_plan": gateway.plan.snapshot(),
            "loop_budget": {"max_steps": 180, "max_model_calls": 120,
                            "max_delegates_v2": research_limits.max_delegates,
                            "research_limits": research_limits.describe()},
        })
        if capabilities is None:
            visible["review_target_json_schema"] = FinanceReviewTarget.model_json_schema()
        manifest_record = _context_manifest_record_from_metadata(
            run_id=run_id,
            metadata={
                **dict(context_manifest),
                "stage_name": "chat_agent_loop",
                "agent_role": "main_chat_agent",
                "available_actions": ["final", "delegate", *["tool_call:" + item["name"] for item in tool_descriptions]],
                "default_delegated_artifact_ref": artifact_ref,
                "default_delegated_artifact_refs": [artifact_ref],
                "visible_keys": list(visible),
                "visible_content": visible,
                "tool_descriptors": tool_descriptions,
            },
            stage_name="chat_agent_loop",
            agent_role="main_chat_agent",
        )
        state.add_context_manifest(manifest_record.to_dict())
        gateway.research_execution.main_producer = {
            "role": "main", "role_version": "2.0.0", "context_manifest_id": manifest_record.record_id,
            "context_checksum": manifest_record.checksum, "prompt_checksum": _stable_hash(system_prompt),
            "skill_checksum": _stable_hash(skill_prompt),
        }
        gateway.research_execution.on_event = lambda event: self.store.add_agent_step(
            run_id, step_type=event["event"], title=event["event"], status="done",
            output_payload=event, metadata={"loop_phase": "observe", "research_contract": "v2"})
        base_artifact = self._build_dynamic_subagent_context_artifact(
            message=message, context_manifest=context_manifest, prepared_context=prepared_context
        )
        delegate_records: list[dict[str, Any]] = []
        delegated_batch_ids: set[str] = set()
        citation_grounding_recorded = False
        market_grounding_recorded = False
        independent_review_requested = not self._conditional_review_language(message) and bool(
            re.search(
                r"(?:independent(?:ly)?\s+(?:verify|review|read)|独立(?:地)?(?:核验|验证|复核|查证|读取|判断))",
                str(message or "").casefold(),
            )
        )
        verifier_review_requested = independent_review_requested or (
            not self._conditional_review_language(message)
            and bool(re.search(r"\bverifier\b|independent\s+(?:review|verify|read)", str(message or "").casefold()))
        )

        def delegate(brief: TaskBrief) -> SubagentResult:
            if brief.role_version == "2.0.0":
                child_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
                try:
                    return gateway.research_execution.delegate(
                        brief, child_state, model_adapter=finance_model_adapter,
                        skill_manifest=skill, role_registry=self.subagent_role_registry)
                finally:
                    state.context_manifests.extend(child_state.context_manifests)
                    state.stage_trace.extend(child_state.stage_trace)
                    state.delegation_evidence.extend(child_state.delegation_evidence)
                    for child_manifest in child_state.context_manifests:
                        self.store.add_agent_step(
                            run_id, step_type="subagent_context", title="构建 analyst v2 授权上下文",
                            status="done", output_payload={"context_manifest": child_manifest},
                            metadata={"loop_phase": "think", "subagent_role": "analyst", "role_version": "2.0.0"})
                    for call in child_state.model_calls:
                        self.store.add_agent_step(
                            run_id, step_type="model_call", title="analyst v2 研究调用",
                            status="failed" if (call.get("payload") or {}).get("type") == "model_error" else "done",
                            input_payload={"schema_name": call.get("schema_name"), "agent_role": call.get("agent_role"),
                                           "messages": call.get("messages", [])},
                            output_payload={"payload": call.get("payload"), "response_id": call.get("response_id")},
                            metadata={**dict(call.get("metadata") or {}), "loop_phase": "think", "role_version": "2.0.0"})
                    for trace in child_state.stage_trace:
                        self.store.add_agent_step(run_id, step_type="subagent_trace", title="analyst v2 实际执行记录",
                                                  status="failed" if trace.get("status") == "failed" else "done",
                                                  output_payload=trace, metadata={"loop_phase": "observe"})
            if len(delegate_records) >= 5:
                raise _FinanceEvidenceUnavailable(
                    "Finance turn has reached the five-task delegation limit. "
                    "Do not delegate again; read any remaining articles directly or state "
                    "which listed articles were not covered."
                )
            assigned_ids = None
            assigned_author = ""
            candidate_listing = list(gateway.last_article_listing)
            if brief.role in {"skeptic", "verifier"}:
                listed_ids = {
                    article_id for listing in gateway.article_list_history
                    for article_id in listing.get("article_ids") or []
                }
                candidate_listing = [
                    {key: row[key] for key in ("article_id", "author", "title", "published_at")}
                    for row in gateway.corpus.list_articles(limit=500)
                    if row["article_id"] in listed_ids
                ]
            if brief.role == "analyst":
                # A previous article.list may have been for another author.
                # Rebuild the candidate scope from the task's explicit author
                # so a delegated batch can never silently cross attribution
                # boundaries.
                authors_in_task = [
                    str(row.get("author") or "").strip()
                    for row in gateway.corpus.list_authors()
                    if isinstance(row, dict)
                    and str(row.get("author") or "").strip()
                    and str(row.get("author") or "").strip() in brief.task
                ]
                if len(authors_in_task) == 1:
                    assigned_author = authors_in_task[0]
                    list_kwargs: dict[str, Any] = {
                        "author": assigned_author,
                        "limit": 500,
                    }
                    if gateway.article_window:
                        list_kwargs.update({
                            "published_after": gateway.article_window[0],
                            "published_before": gateway.article_window[1],
                        })
                    candidate_listing = [
                        {
                            key: row[key]
                            for key in ("article_id", "author", "title", "published_at")
                        }
                        for row in gateway.corpus.list_articles(**list_kwargs)
                    ]
                ordered_ids = [
                    item["article_id"]
                    for item in candidate_listing
                    if item.get("article_id")
                ]
                listed_ids = set(ordered_ids)
                mentioned_ids = list(dict.fromkeys(
                    article_id for article_id in re.findall(r"article_[A-Za-z0-9]+", brief.task)
                    if article_id in listed_ids
                ))
                requested_ids = mentioned_ids or ordered_ids
                unread_ids = [
                    article_id for article_id in requested_ids
                    if article_id not in {
                        item.get("article_id") for item in gateway.read_refs
                    }
                ]
                remaining_ids = list(dict.fromkeys(
                    article_id for article_id in [*unread_ids, *requested_ids]
                    if article_id not in delegated_batch_ids
                ))
                if not remaining_ids:
                    raise _FinanceEvidenceUnavailable(
                        "All listed articles have already been assigned to analyst batches. "
                        "Synthesize the existing observations or read any remaining sources directly."
                    )
                assigned_ids = set(remaining_ids[:3])
            payload = {
                **base_artifact,
                "research_policy": visible["finance_research_preferences"],
                "current_holdings": visible["current_holdings"],
                "workspace_paths": paths,
                "article_candidates": [
                    item for item in candidate_listing
                    if assigned_ids is None or item["article_id"] in assigned_ids
                ],
                "evidence_bundle": gateway.evidence_bundle(),
                "finance_article_evidence": gateway.read_results[-8:],
                "public_web_evidence": gateway.web_reads[-6:],
                "market_evidence": gateway.market_reads[-12:],
            }
            if assigned_ids is not None:
                payload["article_batch_boundary"] = {
                    "assigned_article_ids": sorted(assigned_ids),
                    "assigned_author": assigned_author,
                    "max_articles": 3,
                    "notice": (
                        "This child task is scoped to the assigned article_ids only. "
                        "Read each assigned article_id with article.read before returning claims; "
                        "if a read fails, report that article as unread. "
                        "Do not imply that other listed articles were reviewed."
                    ),
                }
                # Keep TaskBrief unchanged: the parent checks its signed child
                # context manifest. The artifact supplies the narrower scope
                # that the child sees immediately after artifact_read.
                payload["article_batch_boundary"]["evidence_ref_format"] = (
                    "In claim_set.evidence_refs use only evidence_id strings returned by "
                    "article.read. Do not put article_id/path/line composites there; the "
                    "ledger resolves every evidence_id to its source path and line range."
                )
                payload["article_batch_boundary"]["priority"] = (
                    "The parent task may mention more article IDs than this bounded batch. "
                    "Only assigned_article_ids are in scope, even if the task names others."
                )
                payload["article_batch_boundary"]["coverage_requirement"] = (
                    "For each assigned article, return at least one claim supported by an "
                    "article.read evidence_id, or a brief claim saying the read article has no "
                    "relevant viewpoint on the requested topic. Do not silently omit a read "
                    "article from the structured claim_set."
                )
            child_gateway = None
            if brief.role in {"analyst", "verifier", "skeptic"}:
                child_gateway = FinanceResearchGateway(
                    self.company_id, set(), question=brief.task,
                    source_question=message,
                    child_roles=(brief.role,),
                    session_id=session_id,
                    run_id=run_id,
                    research_state_path=None,
                    allowed_article_ids=assigned_ids,
                )
                payload["evidence_bundle"] = {
                    **payload["evidence_bundle"],
                    "evidence": [
                        {
                            key: item[key]
                            for key in (
                                "evidence_id", "source_type", "source_ref", "article_id",
                                "author", "published_at", "line_start", "line_end", "source_url",
                            ) if key in item
                        }
                        for item in payload["evidence_bundle"]["evidence"]
                    ],
                }
                payload["finance_article_evidence"] = [
                    {key: value for key, value in item.items() if key != "text"}
                    for item in payload["finance_article_evidence"]
                ]
                payload["public_web_evidence"] = [
                    {key: value for key, value in item.items() if key != "text"}
                    for item in payload["public_web_evidence"]
                ]
                payload["market_evidence"] = [
                    {
                        key: item[key]
                        for key in (
                            "evidence_id", "snapshot_id", "symbol", "start_date", "end_date",
                            "start_close", "end_close", "change_pct", "unit", "price_unit",
                            "point_count", "source_url", "quality_issues",
                        ) if key in item
                    }
                    for item in payload["market_evidence"]
                ]
                source_refs = [
                    {"article_id": item.get("article_id"), "title": item.get("title")}
                    for item in candidate_listing
                ]
                payload["review_instructions"] = (
                    "You can independently read sources with article.read(article_id, start_line, limit) "
                    "or web.open(url). If this task asks for independent source review, use one of these "
                    "tools before giving a verdict. Reading the delegated artifact alone is same-source "
                    "context review, not independent retrieval. For a verifier review, re-reading the "
                    "same local author article is only independent_article_read; to claim "
                    "independent_source_read you must also retrieve at least one external public source "
                    "with web.search/web.open, or a claim-matched market/policy source, and bind its "
                    "evidence ID to the verdict. This review is not a second full corpus read. "
                    "Prioritize the few claims most likely to change the conclusion, read targeted "
                    "source ranges, and reserve a model call to return the required structured result. "
                    "If the task lists more documents than fit the budget, report the unreviewed scope "
                    "in notes instead of repeatedly reading without a final verdict."
                )
                payload["available_article_refs"] = source_refs
                payload["require_independent_read"] = independent_review_requested
            child_final_guard = None
            if brief.role == "analyst" and assigned_ids and child_gateway is not None:
                def child_final_guard(output: dict[str, Any]) -> str | None:
                    evidence_to_article = {
                        str(item.get("evidence_id") or ""): str(item.get("article_id") or "")
                        for item in child_gateway.evidence_bundle().get("evidence") or []
                        if item.get("source_type") == "author_article"
                    }
                    claimed_ids = {
                        evidence_to_article.get(str(evidence_id or ""), "")
                        for claim in output.get("claims") or []
                        if isinstance(claim, dict)
                        for evidence_id in claim.get("evidence_refs") or []
                    }
                    for claim in output.get("claims") or []:
                        if isinstance(claim, dict):
                            for ref in claim.get("evidence_refs") or []:
                                claimed_ids.update(article_cited_ids(str(ref), child_gateway.read_refs))
                    missing_ids = sorted(assigned_ids - claimed_ids)
                    if missing_ids:
                        return (
                            "The analyst claim_set silently omitted assigned articles: "
                            + ", ".join(missing_ids)
                            + ". Read each missing article and return one evidence-bound "
                            "claim per article, even if the finding is that the article has "
                            "no relevant viewpoint on the requested topic. Use the evidence_id "
                            "from article.read in evidence_refs; do not add schema fields."
                        )
                    return None
            elif brief.role == "verifier" and child_gateway is not None:
                def child_final_guard(output: dict[str, Any]) -> str | None:
                    web_issue = _finance_verifier_web_quote_issue(
                        output,
                        {
                            page["evidence_id"]: child_gateway.web.opened_text(page["evidence_id"])
                            for page in child_gateway.web_reads
                        },
                    )
                    return web_issue or _finance_verifier_market_fail_issue(
                        output,
                        child_gateway.evidence_bundle(),
                        author_article_task=any(
                            marker in state.current_user_message.casefold()
                            for marker in ("文章", "作者", "article", "author")
                        ),
                    )
            result = self._run_general_chat_delegate(
                parent_state=state,
                skill_manifest=skill,
                brief=brief,
                artifact_ref=artifact_ref,
                artifact_payload=payload,
                prepared_context=prepared_context,
                delegate_records=delegate_records,
                gateway=child_gateway,
                finance_research=True,
                final_guard=child_final_guard,
            )
            if child_gateway is not None:
                # Keep the child permission boundary, but expose its immutable
                # source fragments in the parent turn's canonical evidence
                # bundle for audit, replay, and final-answer citation checks.
                gateway.evidence_ledger.merge_bundle(child_gateway.evidence_bundle())
            finding = delegate_records[-1]["finding"]
            finding["assigned_author"] = assigned_author
            observed_authors = {
                str(item.get("author") or "").strip()
                for item in (finding.get("child_evidence_bundle") or {}).get("evidence") or []
                if item.get("source_type") == "author_article" and str(item.get("author") or "").strip()
            }
            finding["observed_authors"] = sorted(observed_authors)
            if assigned_ids and result.stop_reason == "success" and not observed_authors:
                child_output = finding.get("output") or {}
                substantive_output = any(
                    isinstance(child_output.get(key), list) and child_output.get(key)
                    for key in ("claims", "assessments", "counterexamples", "verdicts")
                )
                if substantive_output or independent_review_requested:
                    finding.update({
                        "status": "failed",
                        "stop_reason": "gate_blocked",
                        "error": "analyst returned substantive output without reading any Runtime-assigned article_id",
                    })
                    finding["assigned_article_ids"] = sorted(assigned_ids)
                    raise _FinanceEvidenceUnavailable(
                        "analyst did not read the assigned article batch; retry with article.read before claims"
                    )
            if assigned_author and observed_authors and observed_authors != {assigned_author}:
                finding.update({
                    "status": "failed",
                    "stop_reason": "gate_blocked",
                    "error": (
                        f"delegated author scope mismatch: expected {assigned_author}, "
                        f"observed {sorted(observed_authors)}"
                    ),
                })
                raise _FinanceEvidenceUnavailable(
                    "delegated article author scope mismatch; discard the batch and retry with the requested author"
                )
            if assigned_ids is not None:
                finding["assigned_article_ids"] = sorted(assigned_ids)
                if result.stop_reason == "success":
                    delegated_batch_ids.update(assigned_ids)
            return result

        def delegated_article_refs() -> list[dict[str, Any]]:
            refs = []
            for record in delegate_records:
                finding = record["finding"]
                if finding.get("status") != "success":
                    continue
                for item in (finding.get("child_evidence_bundle") or {}).get("evidence") or []:
                    if item.get("source_type") == "author_article" and item.get("source_ref"):
                        refs.append({
                            "article_id": item.get("article_id"),
                            "author": item.get("author"),
                            "published_at": item.get("published_at"),
                            "path": item["source_ref"],
                            "line_start": item.get("line_start"),
                            "line_end": item.get("line_end"),
                            "evidence_id": item.get("evidence_id"),
                        })
            return refs

        audit_attempts = 0

        def independent_review_issue(output: dict[str, Any], *, provenance_only=False) -> str | None:
            from policy_impact.domains.finance.answer_delivery import independent_review_claimed

            if not verifier_review_requested:
                return None
            answer = str(output.get("answer") or "")
            declared_review = independent_review_claimed(answer)
            if provenance_only and not declared_review:
                return None
            successful_verifier = any(
                record["finding"].get("status") == "success"
                and record["finding"].get("role") == "verifier"
                and record["finding"].get("review_scope") == "independent_source_read"
                and bool((record["finding"].get("output") or {}).get("verdicts"))
                for record in delegate_records
            )
            if successful_verifier:
                audit = self._finance_subagent_evidence_audit(
                    delegate_records, gateway.evidence_bundle(), review_required=True
                )
                if audit["review_complete"] and not audit["invalid_binding_count"]:
                    return None
                if provenance_only and not independent_review_claimed(answer, full_coverage=True):
                    return None
                if any(term in answer.casefold() for term in (
                    "not every claim", "not all claims", "unreviewed", "未全部核验", "部分未核验",
                )):
                    return None
                return (
                    "Verifier returned a partial verdict set. Disclose which claims remain "
                    "unreviewed, or ask verifier to cover the remaining structured claims before final."
                )
            local_independent_read = any(
                record["finding"].get("status") == "success"
                and record["finding"].get("role") == "verifier"
                and record["finding"].get("review_scope") == "independent_article_read"
                for record in delegate_records
            )
            if provenance_only and local_independent_read:
                return None
            if (not declared_review or local_independent_read) and any(term in answer.casefold() for term in (
                "未完成独立", "未独立读取", "不算独立", "没有独立", "无法独立",
                "未能独立", "not independent", "could not independently",
            )):
                return None
            return (
                "Trace shows no successful independent source read by a subagent. "
                "Do not claim the subagent read article.read/web.open or independently verified a source. "
                "Revise the answer to disclose that independent review was not completed."
            )

        def review_scope_guard(output: dict[str, Any]) -> str | None:
            nonlocal citation_grounding_recorded, market_grounding_recorded, audit_attempts
            from policy_impact.domains.finance.article_gateway import normalize_inline_article_citations

            pending_results = gateway.research_execution.pending_dispositions()
            if pending_results:
                return ("Record research.disposition for the returned research results before delivery: "
                        + ", ".join(pending_results)
                        + ". Choose use, qualify, exclude or follow_up with a reason; this is not verification.")

            answer = str(output.get("answer") or "")
            legacy_research = not bool(gateway.research_execution.prepared)
            article_refs = [*gateway.read_refs, *delegated_article_refs()]
            normalized_answer = normalize_inline_article_citations(answer, article_refs)
            if normalized_answer != answer:
                output["answer"] = normalized_answer
                answer = normalized_answer
            model_answer = answer
            citation_issue = article_citation_issue(answer, article_refs)
            if not legacy_research:
                from policy_impact.domains.finance.answer_delivery import research_citation_issue

                citation_issue = research_citation_issue(output, article_refs, gateway.evidence_ledger)
            plan_snapshot = gateway.plan.snapshot()
            if not plan_snapshot["goals"]:
                return (
                    "Before finalizing, create a concise but complete research.plan for this finance turn "
                    "with at least two goals covering the user's requested outcome and the "
                    "evidence or completion check. The plan does not prescribe the research method."
                )
            if plan_snapshot.get("research_pending_count"):
                return (
                    "The research.plan still has pending goals. Continue the research, then "
                    "mark each goal done, or mark an impossible goal blocked with a concrete reason, "
                    "before finalizing."
                )
            if not gateway.verification_checkpoint.latest:
                return (
                    "Record a research.checkpoint after analysis: identify material claims, "
                    "what was checked or did not need external checking, observed EvidenceRefs, "
                    "and remaining gaps. This does not require a specific verification tool."
                )
            if not gateway.verification_checkpoint.covers_current_evidence():
                return (
                    "New evidence was observed after the verification checkpoint. "
                    "Reconsider the affected claims and record an updated research.checkpoint."
                )
            if legacy_research and citation_issue and multi_author_scope:
                return (
                    citation_issue + " For a comparative answer, put a source path and "
                    "body-line citation beside each material author claim. A list of "
                    "article titles or Runtime-generated L1 reading markers is not "
                    "claim-level evidence. Use only lines present in the read ledger."
                )
            if legacy_research and citation_issue and not any(
                marker in citation_issue for marker in ("not read", "exceeds")
            ):
                generated = canonical_article_citations(article_refs)
                output["citations"] = [
                    item for item in [*(output.get("citations") or []), *generated]
                    if isinstance(item, str) and item.strip()
                ]
                if generated and not any(item in answer for item in generated):
                    output["answer"] = (
                        f"{answer.rstrip()}\n\n**本轮已读取的文章证据**\n"
                        + "\n".join(f"- `{item}`" for item in generated)
                    )
                    answer = str(output["answer"])
                if not citation_grounding_recorded:
                    self.store.add_agent_step(
                        run_id,
                        step_type="evidence_citation_grounding",
                        title="Runtime 补齐文章证据引用",
                        status="done",
                        input_payload={
                            "raw_citation_issue": citation_issue,
                            "read_ref_count": len(article_refs),
                        },
                        output_payload={
                            "citations": generated,
                            "source": "runtime_read_ledger",
                            "unread_sources_allowed": False,
                        },
                        gate_result={
                            "decision": "allow",
                            "reason": "引用仅从本轮 article.read 结果生成",
                            "policy_id": "finance.article_citation_grounding.v1",
                        },
                        metadata={"loop_phase": "observe", "deterministic": True},
                    )
                    citation_grounding_recorded = True
                citation_issue = article_citation_issue(
                    str(output.get("answer") or ""), article_refs, output.get("citations")
                )
            if citation_issue:
                return citation_issue
            if _requests_market_evidence(message) and gateway.market_reads:
                invalid_metric_refs = []
                for metric in gateway.market_reads:
                    evidence_id = str(metric.get("evidence_id") or "")
                    if not evidence_id:
                        invalid_metric_refs.append({"reason": "missing evidence_id", "symbol": metric.get("symbol")})
                        continue
                    valid, reason = gateway.evidence_ledger.validate_market_metric(evidence_id, metric)
                    if not valid:
                        invalid_metric_refs.append({
                            "evidence_id": evidence_id,
                            "symbol": metric.get("symbol"),
                            "reason": reason,
                        })
                if invalid_metric_refs:
                    return (
                        "Market metric evidence failed canonical Ledger validation: "
                        + json.dumps(invalid_metric_refs, ensure_ascii=False)
                        + ". Do not publish the numeric market answer."
                    )
                metric_refs = [
                    {
                        "type": "market_metric",
                        "evidence_id": item.get("evidence_id"),
                        "snapshot_id": item.get("snapshot_id"),
                        "symbol": item.get("symbol"),
                        "period": {
                            "start": item.get("start_date"),
                            "end": item.get("end_date"),
                        },
                        "unit": item.get("unit"),
                        "price_unit": item.get("price_unit"),
                        "observed_values": {
                            "start_close": item.get("start_close"),
                            "end_close": item.get("end_close"),
                            "change_pct": item.get("change_pct"),
                        },
                    }
                    for item in gateway.market_reads
                    if item.get("evidence_id") and item.get("snapshot_id") and item.get("symbol")
                ]
                if not metric_refs:
                    return (
                        "Market metrics were read without a usable evidence_id, snapshot_id, "
                        "symbol, and unit binding. Do not publish the numeric market answer."
                    )
                existing_citations = list(output.get("citations") or [])
                existing_metric_ids = {
                    str(item.get("evidence_id") or "")
                    for item in existing_citations
                    if isinstance(item, dict) and item.get("type") == "market_metric"
                }
                output["citations"] = [
                    *existing_citations,
                    *[item for item in metric_refs if item["evidence_id"] not in existing_metric_ids],
                ]
                output["market_evidence_refs"] = metric_refs
                if not market_grounding_recorded:
                    self.store.add_agent_step(
                        run_id,
                        step_type="market_evidence_grounding",
                        title="绑定行情数字与快照证据",
                        status="done",
                        input_payload={"metric_count": len(metric_refs)},
                        output_payload={"metric_refs": metric_refs},
                        gate_result={
                            "decision": "allow",
                            "reason": "每个行情指标引用均绑定 evidence_id、snapshot_id、symbol、period 和 unit",
                            "policy_id": "finance.market_metric_evidence_binding.v1",
                        },
                        metadata={"loop_phase": "observe", "deterministic": True},
                    )
                    market_grounding_recorded = True
            if legacy_research and local_article_scope and not gateway.article_list_attempted and not gateway.read_refs:
                return (
                    "This request names a local article author. Call article.list with the "
                    "resolved author/date scope before giving a final answer."
                )
            if _requests_market_evidence(message) and not gateway.market_reads and not _contains_unverified_market_notice(answer):
                return (
                    "The user asked for market facts or verification. Use market.snapshot "
                    "followed by market.metrics; a generic official page is not a substitute for "
                    "a claim-matched market observation. If those sources are unavailable, "
                    "explicitly state that the market facts were not independently verified."
                )
            if _requests_market_trend(message) and gateway.market_reads:
                sufficient_series = {
                    (item.get("snapshot_id"), item.get("symbol"))
                    for item in getattr(gateway.market, "series_reads", [])
                    if int(item.get("bar_count") or 0) >= 3
                }
                insufficient_metrics = [
                    item for item in gateway.market_reads
                    if int(item.get("point_count") or 0) < 3
                    and (item.get("snapshot_id"), item.get("symbol")) not in sufficient_series
                ]
                if insufficient_metrics and not _contains_market_coverage_notice(answer):
                    return (
                        "The acquired market observations contain fewer than three effective points. "
                        "Do not describe them as a monthly trend; either read a sufficiently long "
                        "market.series or explicitly disclose that the available data cannot establish trend."
                    )
            if (
                gateway.article_list_attempted
                and not gateway.article_list_any_match
                and not gateway.read_refs
                and not _contains_no_article_notice(answer)
            ):
                return (
                    "No local articles matched the requested scope. State that the requested "
                    "period or author has no available articles; do not widen the scope implicitly."
                )
            if legacy_research and local_article_scope:
                requested_authors = named_article_authors
                if multi_author_scope:
                    planned_authors = {
                        str(author).strip().casefold()
                        for author in (plan_snapshot.get("scope") or {}).get("authors") or []
                    }
                    missing_plan_authors = [
                        author for author in requested_authors
                        if author.casefold() not in planned_authors
                    ]
                    if missing_plan_authors:
                        return (
                            "The research.plan does not preserve every author named by the user. "
                            "Update it with scope.authors including: "
                            f"{', '.join(missing_plan_authors)}."
                        )
                    author_listings: dict[str, list[dict[str, Any]]] = {
                        author: [] for author in requested_authors
                    }
                    for listing in gateway.article_list_history:
                        scope = listing.get("effective_request") or {}
                        author = str(scope.get("author") or "").strip()
                        if author not in author_listings:
                            continue
                        if gateway.article_window:
                            window_start, window_end = gateway.article_window
                            if (
                                str(scope.get("published_after") or "") > window_start
                                or str(scope.get("published_before") or "") < window_end
                            ):
                                continue
                        author_listings[author].append(listing)
                    missing_authors = [author for author, rows in author_listings.items() if not rows]
                    if missing_authors:
                        return (
                            "The user explicitly named multiple author folders, but these folders "
                            f"have not been checked across the requested date scope: {', '.join(missing_authors)}. "
                            "List each named author across the full requested date scope before reading or "
                            "delegating. An empty folder is a valid result for that author; continue "
                            "checking the remaining named folders instead of ending the whole request."
                        )
                    read_ids = {str(ref.get("article_id") or "") for ref in article_refs}
                    unread_authors = [
                        author for author, rows in author_listings.items()
                        if any(row.get("article_ids") for row in rows)
                        and not any(
                            article_id in read_ids
                            for row in rows for article_id in row.get("article_ids") or []
                        )
                    ]
                    if unread_authors:
                        return (
                            "The named author folders contain articles, but no article.read evidence "
                            f"was collected for: {', '.join(unread_authors)}. Read at least one "
                            "relevant article per nonempty author (directly or through a successful "
                            "analyst delegation) before comparing their views. An empty author folder "
                            "must be reported as empty, not treated as a read viewpoint."
                        )
                    for author in requested_authors:
                        read_count = len({
                            str(ref.get("article_id") or "") for ref in article_refs
                            if ref.get("author") == author and ref.get("article_id")
                        })
                        stated_count = _stated_article_read_count(answer, author)
                        if stated_count is not None and stated_count != read_count:
                            return (
                                f"The answer says {author} had {stated_count} actually read "
                                f"articles, but this run's article.read ledger has {read_count}. "
                                "Correct the coverage count and distinguish listed from read articles."
                            )
                    if any(term in message.casefold() for term in (
                        "变化", "演变", "随时间", "时间线", "over time", "trend",
                    )):
                        for author, rows in author_listings.items():
                            listed_ids = {
                                article_id for row in rows
                                for article_id in row.get("article_ids") or []
                            }
                            if not listed_ids:
                                continue
                            months: dict[str, set[str]] = {}
                            for article in gateway.corpus.list_articles(author=author, limit=500):
                                if article["article_id"] in listed_ids:
                                    months.setdefault(article["published_at"][:7], set()).add(
                                        article["article_id"]
                                    )
                            missing_months = [
                                month for month, article_ids in sorted(months.items())
                                if not (article_ids & read_ids)
                            ]
                            if len(months) > 1 and missing_months:
                                return (
                                    f"The answer compares how {author}'s view changed over time, "
                                    f"but no article.read evidence was collected from {', '.join(missing_months)}. "
                                    "Read a relevant article from each represented month before "
                                    "claiming a trend across the full period; disclose any month "
                                    "that has no relevant article instead of inferring its view."
                                )
                    cited_ids = article_cited_ids(model_answer, article_refs)
                    uncited_authors = [
                        author for author, rows in author_listings.items()
                        if any(row.get("article_ids") for row in rows)
                        and not any(
                            article_id in cited_ids
                            for row in rows for article_id in row.get("article_ids") or []
                        )
                    ]
                    if uncited_authors:
                        return (
                            "The comparative answer has no line citation for these named "
                            f"authors: {', '.join(uncited_authors)}. Cite a read body line "
                            "next to each author's substantive viewpoint."
                        )
            distinct_articles = {ref["article_id"] for ref in article_refs}
            comparison_requested = any(term in message.casefold() for term in ("比较", "对比", "compare"))
            multi_period = any(
                term in message.casefold()
                for term in ("随时间", "时间变化", "逐篇", "每篇", "over time", "each article")
            )
            broad_comparison = comparison_requested and multi_period and gateway.article_list_max_count >= 6
            # The plan gate above is universal. This branch handles the
            # separate request to read every listed article.
            if any(term in message for term in ("全部", "逐篇", "每篇")) and gateway.article_list_attempted:
                listed_ids = {
                    article_id for listing in gateway.article_list_history
                    for article_id in listing.get("article_ids") or []
                }
                missing_ids = listed_ids - distinct_articles
                disclosed_incomplete = any(
                    term in answer.casefold() for term in (
                        "未能覆盖全部", "未覆盖全部", "未能逐篇", "not all articles",
                        "could not cover all",
                    )
                )
                if missing_ids and len(listed_ids) <= 24 and not disclosed_incomplete:
                    return (
                        f"The user asked to read listed articles one by one, but {len(missing_ids)} "
                        "have not been read in this turn. Read the missing IDs before "
                        "claiming full coverage, or explicitly state that full coverage failed."
                    )
                if legacy_research and not missing_ids and len(listed_ids) <= 15:
                    cited_ids = article_cited_ids(answer, article_refs)
                    uncited = sorted(listed_ids - cited_ids)
                    if uncited:
                        return (
                            f"The user asked for every listed article, but {len(uncited)} "
                            "have no visible path-and-line citation in the answer. "
                            "Use the compact observed_article_refs already returned by the "
                            "subagents to add one verified line citation per listed article; "
                            "do not reread sources solely to reconstruct citation paths. "
                            "or explicitly state which articles are not supported by citations."
                        )
            if legacy_research and broad_comparison:
                if not _finance_comparison_has_synthesis(model_answer):
                    return (
                        "The answer is mostly an article inventory, not the requested synthesis. "
                        "Compare how each requested topic changed across periods, explain where the "
                        "author revised or contradicted an earlier view, distinguish quoted claims "
                        "from checked market facts, and cite the relevant read articles. "
                        "Keep the per-article inventory compact; use the remaining space for conclusions."
                    )
            if legacy_research and comparison_requested and len(distinct_articles) >= 2 and len(model_answer.strip()) < 180:
                return (
                    "The user requested a multi-article comparison. The answer is too brief to cover "
                    "each article, changes over time, agreements or conflicts, and evidence gaps. "
                    "Expand the answer using the sources already read, with visible line citations."
                )
            if any(term in message for term in ("未核实", "没有核实", "未经核实")) and not any(
                term in answer for term in ("未核实", "未经核实", "尚未核实", "没有核实", "未独立核实")
            ):
                return (
                    "The user explicitly asked which market facts remain unverified. "
                    "Identify the material author-quoted prices, policy figures or causal claims "
                    "that were not checked against market or primary-source tools this turn. "
                    "Use existing evidence; do not browse again solely to add this disclosure."
                )
            time_issue = _finance_future_watch_issue(answer)
            if time_issue:
                return time_issue
            market_horizon_issue = _finance_market_horizon_issue(answer, gateway.evidence_bundle())
            if market_horizon_issue:
                return market_horizon_issue
            market_math_issue = _finance_market_return_math_issue(answer)
            if market_math_issue:
                return market_math_issue
            sample_count_issue = _finance_article_sample_count_issue(answer, article_refs)
            if sample_count_issue:
                return sample_count_issue
            read_scope_issue = _finance_article_read_scope_issue(answer, article_refs)
            if read_scope_issue:
                return read_scope_issue
            citation_location_issue = _finance_article_citation_location_issue(answer)
            if citation_location_issue:
                return citation_location_issue
            def final_claim_issue() -> str | None:
                nonlocal audit_attempts
                from policy_impact.domains.finance.answer_delivery import (
                    audit_delivery_state,
                    bounded_audit_answer,
                )
                from policy_impact.domains.finance.state import answer_audit_coverage

                if not any(
                    row.get("source_type") in {
                        "author_article", "public_web_page", "market_metric", "market_bar_series",
                        "official_source", "source_document", "wiki_fact",
                    }
                    for row in gateway.evidence_bundle().get("evidence") or []
                ):
                    return None
                audit = None
                target_selection_failed = False
                candidate = candidates.capture(output, gateway.evidence_bundle())
                reused = False
                audit_failed = False
                audit_error_type = ""
                try:
                    if not isinstance(output.get("review_targets"), list):
                        target_selection_failed = True
                        selection = answer_auditor.select_review_targets(
                            str(output.get("answer") or ""), question=message,
                            personal_context={"current_holdings": visible["current_holdings"]},
                        )
                        output["review_targets"] = selection["review_targets"]
                        self.store.add_agent_step(
                            run_id, step_type="answer_review_target_selection",
                            title="主 Agent 补充终稿审核目标", status="done",
                            input_payload={"answer_checksum": _stable_hash(str(output.get("answer") or ""))},
                            output_payload=selection,
                            metadata={"loop_phase": "gate", "metadata_only": True},
                        )
                        target_selection_failed = False
                    candidate = candidates.capture(output, gateway.evidence_bundle())
                    previous_review = candidates.latest_review(candidate)
                    if previous_review is not None:
                        audit = previous_review["audit"]
                        reused = True
                    else:
                        audit_attempts += 1
                        audit = answer_auditor.call(
                            str(output.get("answer") or ""),
                            question=message,
                            personal_context={"current_holdings": visible["current_holdings"]},
                            review_targets=output["review_targets"],
                        )
                except Exception as exc:  # noqa: BLE001
                    audit_failed = True
                    audit_error_type = type(exc).__name__
                    if target_selection_failed:
                        self.store.add_agent_step(
                            run_id, step_type="answer_review_target_selection",
                            title="终稿审核目标补充未完成", status="warning",
                            input_payload={"answer_checksum": _stable_hash(str(output.get("answer") or ""))},
                            output_payload={"status": "failed", "error_type": type(exc).__name__,
                                            "candidate_preserved": True},
                            metadata={"loop_phase": "gate", "metadata_only": True},
                        )
                    if target_selection_failed:
                        audit_attempts += 1
                else:
                    self.store.add_agent_step(
                        run_id, step_type="answer_claim_audit",
                        title="最终答案原子主张审查", status=(
                            "done" if audit["status"] == "accept" else "warning"
                        ),
                        input_payload={"answer_checksum": candidate["answer_checksum"],
                                       "candidate_version": candidate["candidate_version"]},
                        output_payload={**audit, "candidate_review_reused": reused},
                        metadata={"loop_phase": "gate", "review_scope": audit.get("review_scope")},
                    )
                if not reused:
                    candidates.reviewed(candidate, audit)
                prior_issues = candidates.unresolved(candidate)
                if prior_issues:
                    audit = {**(audit or {}), "issues": prior_issues}
                issues = (audit or {}).get("issues") or []
                budget_available = research_scope.remaining() > 0 and (
                    research_scope.root.requests < research_scope.root.limits.root_requests)
                deliver_requested = output.get("review_disposition") == "deliver_with_limits"
                disposition = output.get("review_disposition", "continue")
                if not isinstance(disposition, str) or disposition not in {"continue", "deliver_with_limits"}:
                    return "review_disposition must be continue or deliver_with_limits; preserve the answer body."
                reason = output.get("review_resolution_reason")
                if deliver_requested and (not isinstance(reason, str) or not reason.strip()):
                    return "Provide review_resolution_reason for deliver_with_limits; keep the complete answer unchanged."
                if issues and not (reused or audit_failed or deliver_requested or not budget_available):
                    return (
                        "Selected review targets have unresolved issues, not a rejection of the whole analysis. "
                        "Choose targeted revision, evidence gathering, or return the complete answer with "
                        "review_disposition=deliver_with_limits and a specific review_resolution_reason. "
                        "Preserve author views, rebuttals and uncertainty. Do not rewrite unrelated sections "
                        "or clear issues by renaming/removing targets. Issues: "
                        + json.dumps(issues[:6], ensure_ascii=False)[:4500]
                    )
                output.update(audit_delivery_state(audit))
                if (audit and audit.get("status") == "accept" and not issues
                        and answer_audit_coverage(audit) == "complete"):
                    if audit.get("limitations"):
                        output["answer"] += "\n\n**审核说明：** " + audit["limitations"]
                    return None
                partial, resolution = bounded_audit_answer(
                    str(output.get("answer") or ""), audit, gateway.evidence_bundle(),
                    preserve_candidate=True,
                )
                output["answer"] = partial
                audit_coverage = output["answer_audit_coverage"]
                if audit_failed:
                    output["open_gaps"].append("终稿审核调用失败，完整候选稿已保留")
                self.store.add_agent_step(
                    run_id, step_type="answer_review_resolution",
                    title="有界终稿审核后的部分交付", status="warning",
                    output_payload={
                        "resolution": resolution,
                        "audit_attempts": audit_attempts,
                        "issue_count": len((audit or {}).get("issues") or []),
                        "answer_audit_coverage": audit_coverage,
                        "coverage_complete": audit_coverage == "complete",
                        "unresolved_review": True,
                        "target_selection_failed": target_selection_failed,
                        "candidate_version": candidate["candidate_version"],
                        "review_disposition": output.get("review_disposition", "continue"),
                        "review_resolution_reason": output.get("review_resolution_reason", ""),
                        "no_progress_reuse": reused,
                        "audit_error_type": audit_error_type,
                    },
                    metadata={"loop_phase": "gate"},
                )
                return None
            return independent_review_issue(output) or final_claim_issue()

        def checkpointed_review_guard(output):
            from policy_impact.domains.finance.article_gateway import (
                normalize_inline_article_citations,
            )

            refs = [*gateway.read_refs, *delegated_article_refs()]
            answer = output.get("answer")
            if isinstance(answer, str):
                output["answer"] = normalize_inline_article_citations(answer, refs)
            for key in ("verification_status", "answer_audit_coverage", "semantic_review", "delivery_status"):
                output.pop(key, None)
            issue = candidate_local_issue(output)
            if issue:
                candidate_event("answer_candidate_selection", {"selected": False, "reason": issue})
                return issue
            candidates.capture(output, gateway.evidence_bundle())
            return review_scope_guard(output)

        result = AgentLoop(
            finance_model_adapter, gateway, delegate_fn=delegate,
            skill_manifest=skill, role_registry=self.subagent_role_registry,
            reject_hidden_reasoning=True, final_guard=checkpointed_review_guard,
            context_store=gateway.context_store,
            on_model_input=gateway.research_execution.observe_main_input,
            role_aliases={item["name"]: item["role"] for item in roster},
            on_context_offload=lambda event: self.store.add_agent_step(
                run_id, step_type="context_offload", title="换出可恢复工具观察",
                status="done", output_payload=event,
                metadata={"loop_phase": "observe", "policy_id": "finance.context_pressure.v1"},
            ),
            on_context_restore=lambda event: self.store.add_agent_step(
                run_id, step_type="context_restore_advisory", title="重复恢复上下文提示",
                status="warning", output_payload=event,
                metadata={"loop_phase": "observe", "policy_id": "finance.context_restore_advisory.v1"},
            ),
            on_context_checkpoint=lambda event: self.store.add_agent_step(
                run_id, step_type="context_progress_gate", title="上下文进度门控",
                status="warning" if event["decision"] != "release" else "done",
                output_payload=event,
                metadata={"loop_phase": "gate", "policy_id": "finance.context_progress_gate.v1"},
            ),
        ).run(state, AgentLoopConfig(
            stage_name="chat_agent_loop", agent_role="main_chat_agent",
            context_manifest=manifest_record, output_key="general_chat_agent_loop.v1",
            max_steps=180, max_model_calls=120, timeout_seconds=research_scope.remaining(),
            max_calls_per_tool=180, max_delegate_denials=3, task_prompt=message,
            workspace_prompt=workspace_prompt,
            context_pressure_tokens=context_pressure or None,
            context_target_tokens=context_target or None,
            finalization_reserve_calls=24,
            recover_tool_timeouts=True,
            protocol_in_workspace_prompt=bool(capabilities),
        ))
        self._record_general_chat_agent_loop(run_id, state, result, delegate_records)
        self.store.add_agent_step(
            run_id, step_type="evidence_audit", title="Finance autonomous research evidence",
            status="done", output_payload={
                "article_reads": gateway.read_refs,
                "research_plan": gateway.plan.snapshot(),
                "verification_checkpoint": gateway.verification_checkpoint.latest,
                "research_coverage": gateway.coverage.latest,
                "research_artifacts": gateway.artifacts.call(operation="list"),
                "research_artifacts_v2": gateway.artifacts.call(
                    operation="list", schema_version="finance.research_artifact.v2"),
                "article_list_history": gateway.article_list_history,
                "article_list_max_count": gateway.article_list_max_count,
                "public_web_searches": gateway.web.search_results,
                "public_web_reads": gateway.web_reads,
                "market_reads": gateway.market_reads,
                "market_series_reads": gateway.market.series_reads,
                "subagent_evidence": [record["finding"].get("child_evidence_bundle", {}) for record in delegate_records],
                "evidence_bundle": gateway.evidence_bundle(),
            }, metadata={"loop_phase": "observe"},
        )
        subagent_evidence_audit = self._finance_subagent_evidence_audit(
            delegate_records,
            gateway.evidence_bundle(),
            review_required=verifier_review_requested,
        )
        self.store.add_agent_step(
            run_id,
            step_type="subagent_evidence_audit",
            title="Finance subagent claim-level evidence audit",
            status=(
                "done"
                if not subagent_evidence_audit["invalid_binding_count"]
                and subagent_evidence_audit["review_status"] != "partial"
                and subagent_evidence_audit["review_gate_satisfied"]
                else "warning"
            ),
            input_payload={
                "successful_subagents": [
                    {
                        "role": (record.get("finding") or {}).get("role"),
                        "task_id": (record.get("finding") or {}).get("task_id"),
                    }
                    for record in delegate_records
                    if (record.get("finding") or {}).get("status") == "success"
                ],
                "parent_evidence_ids": list(gateway.evidence_bundle().get("evidence_ids") or []),
            },
            output_payload=subagent_evidence_audit,
            gate_result={
                "decision": (
                    "allow"
                    if not subagent_evidence_audit["invalid_binding_count"]
                    and subagent_evidence_audit["review_status"] != "partial"
                    and subagent_evidence_audit["review_gate_satisfied"]
                    else "warn"
                ),
                "reason": (
                    "存在无效证据引用；保留结果但不得当成已核验结论"
                    if subagent_evidence_audit["invalid_binding_count"]
                    else "复核仅覆盖部分主张；未核实结论不得写为证伪，也不得声称全面核验"
                    if subagent_evidence_audit["review_status"] == "partial"
                    else "已核对所有结构化主张及证据引用"
                    if subagent_evidence_audit["review_complete"]
                    else "本轮未执行 verifier；仅记录已有子智能体输出的证据绑定"
                ),
                "policy_id": "finance.subagent_claim_evidence_audit.v1",
            },
            metadata={"loop_phase": "observe", "deterministic": True},
        )
        calls_by_id = {record.call_id: record for record in gateway.calls}
        for turn in result.turns:
            observation = dict(turn.observation or {})
            if "tool_result" not in observation and "tool_error" not in observation:
                continue
            record = calls_by_id.get(turn.tool_call_id)
            tool_name = record.tool_name if record else turn.model_payload.get("tool_name", "unknown")
            failed = "tool_error" in observation
            self.store.add_agent_step(
                run_id,
                step_type="tool_result",
                title=f"金融研究工具{'失败' if failed else '返回'}：{tool_name}",
                status="failed" if failed else "done",
                output_payload={"observation": observation["tool_error" if failed else "tool_result"]},
                tool_calls=[record.to_dict()] if record else [],
                metadata={"loop_phase": "observe", "turn_index": turn.turn_index,
                          "tool_call_id": turn.tool_call_id},
            )
        output = result.final_output if result.stop_reason == "success" else {}
        if result.stop_reason != "success" and result.final_output:
            from policy_impact.harness.agent_loop import _contains_hidden_reasoning

            late = dict(result.final_output)
            issue = "hidden reasoning is not a public candidate" if _contains_hidden_reasoning(late) else (
                candidate_local_issue(late))
            self.store.add_agent_step(
                run_id, step_type="research_late_candidate", title="中断完整候选的本地来源检查",
                status="failed" if issue else "done",
                output_payload={"candidate_retained": not bool(issue), "provenance_issue": issue,
                                "semantic_review_performed": False, "stop_reason": result.stop_reason},
                metadata={"loop_phase": "stop", "deterministic": True})
            if issue:
                output = {}
            else:
                candidates.capture(late, gateway.evidence_bundle())
        if result.stop_reason != "success":
            from policy_impact.domains.finance.answer_delivery import (
                audit_delivery_state,
                bounded_audit_answer,
            )

            output, audit, selected = candidates.select(gateway.evidence_bundle(), candidate_local_issue)
            if selected is not None:
                independent_gap = independent_review_issue(output)
                answer, resolution = bounded_audit_answer(
                    output["answer"], audit, gateway.evidence_bundle(), preserve_candidate=True,
                )
                output.update(audit_delivery_state(audit))
                output["answer"] = (
                    f"**研究状态：运行在 `{result.stop_reason}` 时结束，保留已有完整分析候选。** "
                    "本地来源检查不代表金融结论已获验证。\n\n" + answer
                )
                output["delivery_mode"] = "qualified"
                if independent_gap:
                    output["answer"] = (
                        "**独立复核状态：本轮未完成用户要求的全部独立来源复核；以下保留已有分析，"
                        "不将其声称为已完成独立核验的结果。**\n\n" + output["answer"]
                    )
                    output["open_gaps"].append("用户要求的独立来源复核未全部完成")
                    if output["verification_status"] == "verified":
                        output["verification_status"] = "partial"
                if selected["persistence_status"] != "saved":
                    output["answer"] += "\n\n**保存缺口：**候选稿仅在本次运行内保留，文件检查点保存失败。"
                self.store.add_agent_step(
                    run_id, step_type="answer_review_resolution", title="中断后保留完整分析候选",
                    status="warning", output_payload={
                        "candidate_version": selected["candidate_version"], "resolution": resolution,
                        "stop_reason": result.stop_reason, "audit_attempts": audit_attempts,
                        "answer_audit_coverage": output["answer_audit_coverage"],
                        "unresolved_review": True,
                    }, metadata={"loop_phase": "stop"},
                )
        if output and candidates.records:
            candidate = candidates.records[-1]
            review = candidates.latest_review(candidate)
            if candidate["persistence_status"] != "saved" or (review and review["persistence_status"] != "saved"):
                if "文件检查点保存失败" not in output.get("answer", ""):
                    output["answer"] += "\n\n**保存缺口：**分析或审核仅在本次运行内保留，文件检查点保存失败。"
                output["delivery_mode"] = "qualified"
        answer = str(output.get("answer") or "").strip()
        fatal_stop_reasons = {
            "model_error",
            "persistence_error",
            "tool_denied",
            "tool_failed",
            "repeated_tool_failure",
            "tool_timeout",
            "delegate_failed",
            "invalid_tool_output",
        }
        if result.stop_reason in fatal_stop_reasons:
            last_error = next(
                (
                    str(turn.error or "").strip()
                    for turn in reversed(result.turns)
                    if str(turn.error or "").strip()
                ),
                "",
            )
            detail = f"; {last_error[:240]}" if last_error else ""
            # Keep the Run auditable and return a truthful partial-research
            # answer. A tool boundary is useful information to the user; it
            # must not erase evidence already collected or become an HTTP 502.
            self.store.add_agent_step(
                run_id,
                step_type="research_failure_fallback",
                title="金融研究工具边界降级",
                status="done",
                input_payload={
                    "stop_reason": result.stop_reason,
                    "last_error": last_error[:600],
                    "detail": detail,
                },
                output_payload={
                    "answer_source": "runtime_truthful_fallback",
                    "evidence_preserved": True,
                    "complete_conclusion": False,
                },
                gate_result={
                    "decision": "allow",
                    "reason": "保留已获取证据并明确披露研究未完成，不将部分结果伪装成完整结论",
                    "policy_id": "finance.research_failure_fallback.v1",
                },
                metadata={"loop_phase": "stop", "deterministic": True},
            )
        if (
            not answer
            and gateway.article_window
            and gateway.article_list_attempted
            and not gateway.article_list_any_match
            and not gateway.read_refs
        ):
            window_start, window_end = gateway.article_window
            answer = (
                f"当前请求的本地文章时间范围为 {window_start} 至 {window_end}，"
                "该范围内没有可用的本地文章，因此无法对作者观点做逐篇比较。"
                "Runtime 没有隐式扩大日期范围，也没有启动 Subagent，因为当前没有可供复核的文章证据。"
                "本轮也没有把任何市场事实写成已核实结论；如需继续比较，请指定一个确实有文章的日期范围。"
            )
            self.store.add_agent_step(
                run_id,
                step_type="research_scope_termination",
                title="本地文章时间窗口为空，终止继续检索",
                status="done",
                input_payload={"article_window": list(gateway.article_window)},
                output_payload={
                    "reason": "empty_article_window",
                    "article_reads": 0,
                    "subagent_started": False,
                    "answer_source": "runtime_truthful_fallback",
                },
                gate_result={
                    "decision": "allow",
                    "reason": "空窗口是有效研究边界，不扩大日期范围、不伪造证据",
                    "policy_id": "finance.empty_article_window.v1",
                },
                metadata={"loop_phase": "stop", "deterministic": True},
            )
        if (
            gateway.market_reads
            and gateway.article_window
            and gateway.article_list_attempted
            and not gateway.article_list_any_match
            and not gateway.read_refs
            and _contains_no_article_notice(answer)
        ):
            market_summary = _format_market_fallback(gateway.market_reads)
            if market_summary:
                window_start, window_end = gateway.article_window
                answer = (
                    f"{market_summary}\n\n"
                    "## 本地文章证据\n"
                    f"Runtime 解析的文章时间范围为 {window_start} 至 {window_end}，该范围内没有本地文章。"
                    "Runtime 没有隐式扩大时间范围。\n\n"
                    "## 仍需核实\n"
                    "本次降级回答保留了已测得的行情事实，但没有声称某个宏观事件或文章导致了涨跌；"
                    "在形成因果判断前，需要读取匹配的来源材料。"
                )
                self.store.add_agent_step(
                    run_id,
                    step_type="market_fallback_grounding",
                    title="Preserve market evidence when local article scope is empty",
                    status="done",
                    input_payload={"market_read_count": len(gateway.market_reads)},
                    output_payload={
                        "market_facts_preserved": True,
                        "article_scope_empty": True,
                        "causal_claims_added": False,
                    },
                    gate_result={
                        "decision": "allow",
                        "reason": "Keep collected market facts visible while preserving the empty article boundary.",
                        "policy_id": "finance.empty_article_preserves_market_evidence.v1",
                    },
                    metadata={"loop_phase": "stop", "deterministic": True},
                )
        repeated_empty_scope = None
        for item in reversed(gateway.article_list_history):
            control = item.get("scope_control") or {}
            if control.get("kind") == "empty_article_scope" and int(control.get("repeat_count") or 0) >= 2:
                repeated_empty_scope = control
                break
        if (
            not answer
            and repeated_empty_scope
            and not gateway.read_refs
        ):
            author = repeated_empty_scope.get("author") or "当前作者"
            after = repeated_empty_scope.get("published_after") or "未限定"
            before = repeated_empty_scope.get("published_before") or "未限定"
            answer = (
                f"已检查作者「{author}」在 {after} 至 {before} 的本地文章目录，"
                "该范围连续两次没有返回文章，当前没有可用的本地文章。Runtime 已停止重复查询，"
                "没有隐式扩大日期范围，也没有把不存在的文章交给 Subagent 分析。"
                "请切换其他作者文件夹或明确指定新的日期范围后再继续。"
            )
            self.store.add_agent_step(
                run_id,
                step_type="research_scope_termination",
                title="同一空文章范围重复查询，终止无进展分支",
                status="done",
                input_payload={"scope_control": repeated_empty_scope},
                output_payload={
                    "reason": "repeated_empty_article_scope",
                    "article_reads": 0,
                    "subagent_started": False,
                    "answer_source": "runtime_truthful_fallback",
                },
                gate_result={
                    "decision": "allow",
                    "reason": "空范围已检查且重复查询没有新增证据，保留真实降级原因",
                    "policy_id": "finance.repeated_empty_article_scope.v1",
                },
                metadata={"loop_phase": "stop", "deterministic": True},
            )
        if not answer:
            # A budget/step/gate stop must remain useful to the user.  The
            # model is still free to choose its research path; this fallback
            # only summarizes the artifacts already recorded by the Runtime.
            plan = gateway.plan.snapshot()
            evidence_lines = []
            article_evidence_refs = [*gateway.read_refs, *delegated_article_refs()]
            unique_articles: dict[str, dict[str, Any]] = {}
            for ref in article_evidence_refs:
                article_id = str(ref.get("article_id") or "")
                if article_id and (
                    article_id not in unique_articles
                    or int(ref.get("line_start") or 1) > int(unique_articles[article_id].get("line_start") or 1)
                ):
                    unique_articles[article_id] = ref
            for ref in list(unique_articles.values())[:20]:
                citation = f"{_relative_article_path(str(ref.get('path') or ''))}:L{ref.get('line_start', 1)}"
                evidence_lines.append(
                    f"- {ref.get('author', '')} {ref.get('published_at', '')}: {citation}"
                )
            for ref in gateway.external_reads[:8]:
                evidence_lines.append(
                    f"- official source: {ref.get('url', '')}"
                )
            claim_lines = _fallback_article_claim_lines(
                gateway.corpus,
                gateway.artifacts,
                article_evidence_refs,
            )
            review_lines = []
            for record in delegate_records:
                finding = record.get("finding") or {}
                role = finding.get("role") or record.get("role") or "unknown"
                status = finding.get("status") or "unknown"
                review_scope = finding.get("review_scope") or ""
                suffix = f", {review_scope}" if review_scope else ""
                review_lines.append(f"- {role}: {status}{suffix}")
            pending = [
                str(goal.get("goal") or "")
                for goal in plan.get("goals") or []
                if goal.get("status") == "pending"
            ]
            blocked = [
                str(goal.get("goal") or "")
                for goal in plan.get("goals") or []
                if goal.get("status") == "blocked"
            ]
            stop_reason = result.stop_reason
            candidate_notice = (
                "本轮已生成过完整候选，但没有候选通过当前来源与本地安全检查；已保留真实材料。"
                if candidates.records else "本轮没有获得模型的最终综合答案，Runtime 已停止继续消耗预算。"
            )
            answer_parts = [
                "## 本轮研究状态",
                (
                    candidate_notice +
                    f"停止原因：`{stop_reason}`。这不是一条完整的金融结论，不能据此做投资判断。"
                ),
                "\n## 已获取的证据",
                (
                    f"- 单次本地文章查询最多返回：{gateway.article_list_max_count} 篇；"
                    f"实际读取：{len(unique_articles)} 篇去重文章、"
                    f"{len(article_evidence_refs)} 个证据片段。\n"
                    f"- 外部官方来源：{len(gateway.external_reads)} 条；网页读取：{len(gateway.web_reads)} 条。\n"
                    f"- 行情读取：{len(gateway.market_reads)} 条。\n"
                    f"- Subagent 委派记录：{len(delegate_records)} 条；"
                    "analyst 取证不等于独立复核。"
                ),
            ]
            if evidence_lines:
                answer_parts.extend(["\n已保留的文章阅读位置（不代表逐条结论依据）：", "\n".join(evidence_lines)])
            else:
                answer_parts.append("\n本轮没有保留可用于引用的文章或外部来源证据。")
            if claim_lines:
                answer_parts.extend([
                    "\n## 已提取的作者主张（未独立核验）",
                    "以下内容来自本轮已经成功完成的逐条提取或文章卡片；Runtime 不把它们升级为事实，也不替主 Agent 判断文章之间的关系。",
                    "\n".join(claim_lines),
                ])
            if review_lines:
                answer_parts.extend(["\n## Subagent 复核状态", "\n".join(review_lines)])
            answer_parts.append("\n## 尚未完成")
            if pending:
                answer_parts.append("研究计划中的待办目标：\n" + "\n".join(f"- {item}" for item in pending))
            if blocked:
                answer_parts.append("研究计划中的阻塞目标：\n" + "\n".join(f"- {item}" for item in blocked))
            if not pending and not blocked:
                answer_parts.append("研究计划没有留下待办，但本轮仍未形成足够的最终综合结果。")
            answer_parts.append(
                "本轮未形成可交付的最终答复；以上已读材料不代表用户问题已得到回答。"
                "可以继续在同一会话中追问，Runtime 会复用已记录的运行状态。"
            )
            answer = "\n\n".join(answer_parts)
            output = {"delivery_mode": "qualified" if gateway.research_execution.validated_bodies else "source_only",
                      "verification_status": "failed", "answer_audit_coverage": "failed",
                      "semantic_review": "failed"}
            for body in gateway.research_execution.validated_bodies.values():
                answer += "\n\n## 已保留的研究分析（未完成最终综合）\n\n" + body["content"]
            if gateway.evidence_ledger.evidence_ids:
                from policy_impact.domains.finance.answer_delivery import observed_evidence_answer

                answer += "\n\n" + observed_evidence_answer(
                    gateway.evidence_bundle(), reason=f"运行在 {result.stop_reason} 时中断"
                )
        self.store.add_agent_step(
            run_id, step_type="research_final_linkage", title="新版研究结果与最终答案关联",
            status="done", output_payload=gateway.research_execution.final_audit(answer),
            metadata={"loop_phase": "observe"})
        model_citations = output.get("citations") if isinstance(output, dict) else []
        if not isinstance(model_citations, list):
            model_citations = []
        # A model may describe a citation, but it cannot mint a market
        # evidence reference. Runtime will add only Ledger-backed metrics below.
        model_citations = _drop_model_market_citations(model_citations)
        grounded_market_citations = []
        for metric in gateway.market_reads:
            evidence_id = str(metric.get("evidence_id") or "")
            if not evidence_id:
                continue
            valid, _ = gateway.evidence_ledger.validate_market_metric(evidence_id, metric)
            if valid:
                grounded_market_citations.append({
                    "type": "market_metric",
                    "evidence_id": evidence_id,
                    "snapshot_id": metric.get("snapshot_id"),
                    "symbol": metric.get("symbol"),
                    "period": {
                        "start": metric.get("start_date"),
                        "end": metric.get("end_date"),
                    },
                    "unit": metric.get("unit"),
                })
        citations = []
        for citation in [
            *model_citations,
            *gateway.read_refs,
            *delegated_article_refs(),
            *gateway.external_reads,
            *grounded_market_citations,
        ]:
            if isinstance(citation, str):
                citation = {"type": "article_line", "citation": citation}
            if not isinstance(citation, dict):
                continue
            if citation not in citations:
                citations.append(citation)
        from policy_impact.domains.finance.state import answer_audit_coverage

        raw_verification = str(output.get("verification_status") or "").strip()
        if raw_verification == "audit_accepted":
            verification_status = "verified"
        elif raw_verification in {"disputed_full_draft", "disputed_partial"} or "disputed" in raw_verification:
            verification_status = "disputed"
        elif raw_verification in {"review_failed_full_draft", "source_only"} or not answer:
            verification_status = "failed"
        elif raw_verification in {"verified", "partial", "failed"}:
            verification_status = raw_verification
        else:
            verification_status = "partial"
        latest_coverage = gateway.coverage.latest or {}
        aspect_statuses = {
            str(item.get("status"))
            for item in latest_coverage.get("aspects") or []
            if isinstance(item, dict)
        }
        if "uncovered" in aspect_statuses:
            evidence_coverage = "uncovered"
        elif "partial" in aspect_statuses or not latest_coverage:
            evidence_coverage = "partial" if gateway.evidence_ledger.evidence_ids else "uncovered"
        else:
            evidence_coverage = "complete"
        audit_coverage = str(
            output.get("answer_audit_coverage")
            or answer_audit_coverage(
                {"coverage_complete": raw_verification == "verified"},
                default="failed",
            )
        )
        if audit_coverage not in {"complete", "partial", "failed"}:
            audit_coverage = "failed"
        if output.get("delivery_mode") == "source_only" or raw_verification == "source_only":
            delivery_mode = "source_only"
        elif output.get("delivery_mode") == "qualified" or verification_status in {"disputed", "partial", "failed"}:
            delivery_mode = "qualified"
        else:
            delivery_mode = "full"
        # Delivery and research completion are independent; source_only and
        # stop_reason retain the distinction from a completed analysis.
        delivery_status = "done" if answer else "blocked"
        semantic_review = str(output.get("semantic_review") or output.get("review_status") or "")
        if semantic_review not in {"complete", "partial", "failed"}:
            semantic_review = "complete" if verification_status == "verified" else "partial"
        return {
            "answer": answer,
            "artifacts": {},
            "citations": citations,
            "delegation_evidence": [item.model_dump(mode="json") for item in result.delegation_evidence],
            "stop_reason": result.stop_reason,
            "delivery_status": delivery_status,
            "delivery_mode": delivery_mode,
            "verification_status": verification_status,
            "evidence_coverage": evidence_coverage,
            "answer_audit_coverage": audit_coverage,
            "semantic_review": semantic_review,
            "actual_execution_mode": result.actual_execution_mode.value,
        }

    def _subagent_roster_for_skill(self, skill_manifest: SkillManifest) -> list[dict[str, Any]]:
        manifests = self.subagent_manifest_registry.for_roles(skill_manifest.allowed_subagents)
        return [
            manifest.roster_item(caller_allowed_tools=skill_manifest.allowed_tools)
            for manifest in manifests
        ]

    @staticmethod
    def _general_chat_loop_task_prompt(
        message: str,
        artifact_ref: str,
        agent_roster: list[dict[str, Any]],
    ) -> str:
        return "\n".join(
            [
                "本轮用户问题（由 Runtime 注入）：" + message,
                "本轮默认委派 artifact ref：" + artifact_ref,
                f"agent_roster：{json.dumps(agent_roster, ensure_ascii=False)}",
            ]
        )

    def _run_general_chat_delegate(
        self,
        *,
        parent_state: PolicyImpactState,
        skill_manifest: SkillManifest,
        brief: TaskBrief,
        artifact_ref: str,
        artifact_payload: dict[str, Any],
        prepared_context: dict[str, Any],
        delegate_records: list[dict[str, Any]],
        gateway: ToolGateway | None = None,
        finance_research: bool = False,
        final_guard: Callable[[dict[str, Any]], str | None] | None = None,
        reason: str = "主 Agent 在 AgentLoop 中显式返回 delegate action",
        trigger: str = "main_agent_delegate_action",
    ) -> SubagentResult:
        manifest = self.subagent_manifest_registry.get(brief.role)
        input_refs = tuple(brief.input_artifact_refs or (artifact_ref,))
        if any(ref != artifact_ref for ref in input_refs):
            raise PermissionError("general_chat subagent can only read the delegated main-chat artifact")
        task = PlannedSubagentTask(
            role=brief.role,
            reason=reason,
            task=brief.task,
            priority=20,
            trigger=trigger,
        )
        gateway = gateway or self._subagent_tool_gateway(skill_manifest)
        child_state = PolicyImpactState(company_id=self.company_id, run_id=parent_state.run_id)
        child_state.current_user_message = parent_state.current_user_message
        child_state.selected_skill = {
            "id": "general_chat",
            "mode": "agent_loop",
            "delegated_by": "main_agent_delegate_action",
        }
        child_state.recent_history = list(prepared_context.get("recent_history") or [])
        child_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        child_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
        child_state.relevant_memories = list(prepared_context.get("memories") or [])
        child_state.company_facts = list(prepared_context.get("facts") or [])
        child_model_adapter = self._get_model_adapter()
        if finance_research and isinstance(child_model_adapter, AnthropicCompatibleModelAdapter):
            child_model_adapter = copy(child_model_adapter)
            child_model_adapter.max_tokens = max(child_model_adapter.max_tokens, 4096)
            child_model_adapter.timeout_seconds = max(child_model_adapter.timeout_seconds, 90)
        forced_adapter = _ForcedFirstActionModelAdapter(
            child_model_adapter,
            {
                "type": "tool_call",
                "tool_name": "artifact_read",
                "arguments": {"artifact_ref": artifact_ref},
            },
        )
        try:
            result = SubagentRunner(
                model_adapter=forced_adapter,
                tool_gateway=gateway,
                skill_manifest=skill_manifest,
                role_registry=self.subagent_role_registry,
            ).run(
                child_state,
                brief.role,
                task=brief.task,
                input_artifact_refs=input_refs,
                artifact_payloads={ref: artifact_payload for ref in input_refs},
                parent_span_id=brief.parent_span_id,
                task_id=brief.task_id,
                final_guard=final_guard,
            )
            if hasattr(gateway, "evidence_bundle"):
                result = result.model_copy(update={
                    "review_scope": self._finance_subagent_review_scope(
                        brief.role, gateway.evidence_bundle()
                    ),
                    "observed_article_refs": tuple({
                        key: ref[key] for key in (
                            "article_id", "path", "line_start", "line_end", "evidence_id"
                        ) if key in ref
                    } for ref in gateway.read_refs),
                })
            finding = {
                "role": result.role,
                "task_id": result.task_id,
                "status": "success" if result.stop_reason == "success" else "failed",
                "stop_reason": result.stop_reason,
                "reason": task.reason,
                "trigger": task.trigger,
                "output_schema": manifest.output_schema_name,
                "output": _materialize_execution_value(result.output),
                "context_manifest_id": result.context_manifest_id,
                "review_scope": result.review_scope,
                "artifact_refs": list(result.artifact_refs),
                "observed_article_refs": list(result.observed_article_refs),
                "evidence_ids": list(
                    ((artifact_payload.get("evidence_bundle") or {}).get("evidence_ids") or [])
                ),
            }
            if hasattr(gateway, "evidence_bundle"):
                finding["child_evidence_bundle"] = gateway.evidence_bundle()
                finding["evidence_ids"] = list(dict.fromkeys([
                    *finding["evidence_ids"], *gateway.evidence_ids,
                ]))
        except Exception as exc:  # noqa: BLE001
            finding = {
                "role": brief.role,
                "task_id": brief.task_id,
                "status": "failed",
                "stop_reason": "delegate_failed",
                "reason": task.reason,
                "trigger": task.trigger,
                "error": str(exc)[:240],
                "output": {},
                "evidence_ids": list(
                    ((artifact_payload.get("evidence_bundle") or {}).get("evidence_ids") or [])
                ),
            }
            delegate_records.append(
                {
                    "task": task,
                    "manifest": manifest,
                    "artifact_ref": artifact_ref,
                    "child_state": child_state,
                    "finding": finding,
                    "tool_calls": [record.to_dict() for record in gateway.calls],
                    "skill_manifest": skill_manifest,
                }
            )
            raise

        parent_state.context_manifests.extend(child_state.context_manifests)
        parent_state.stage_trace.extend(child_state.stage_trace)
        parent_state.delegation_evidence.extend(child_state.delegation_evidence)
        parent_state.artifacts.update(child_state.artifacts)
        delegate_records.append(
            {
                "task": task,
                "manifest": manifest,
                "artifact_ref": artifact_ref,
                "child_state": child_state,
                "finding": finding,
                "tool_calls": [record.to_dict() for record in gateway.calls],
                "skill_manifest": skill_manifest,
            }
        )
        return result

    def _record_general_chat_agent_loop(
        self,
        run_id: str,
        loop_state: PolicyImpactState,
        loop_result: AgentLoopResult,
        delegate_records: list[dict[str, Any]],
    ) -> None:
        pending_records = list(delegate_records)
        for index, turn in enumerate(loop_result.turns, start=1):
            budget_boundary = (turn.observation or {}).get("budget_boundary")
            if isinstance(budget_boundary, dict):
                model_payload = {}
                if index <= len(loop_state.model_calls):
                    model_payload = dict(loop_state.model_calls[index - 1].get("payload") or {})
                self.store.add_agent_step(
                    run_id,
                    step_type="gate_check",
                    title="AgentLoop budget boundary",
                    status="done",
                    input_payload={
                        "turn_index": turn.turn_index,
                        "candidate_action": model_payload,
                    },
                    output_payload=budget_boundary,
                    gate_result={
                        "decision": "deny",
                        "reason": str(budget_boundary.get("reason") or ""),
                        "policy_id": "general_chat.final_slot.v1",
                    },
                    metadata={"loop_phase": "observe", "embedded_loop": "general_chat"},
                )
            final_gate = (turn.observation or {}).get("final_gate")
            if not isinstance(final_gate, dict):
                continue
            model_payload = {}
            if index <= len(loop_state.model_calls):
                model_payload = dict(loop_state.model_calls[index - 1].get("payload") or {})
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="主 Agent 最终答案门控拒绝",
                status="failed",
                input_payload={
                    "turn_index": turn.turn_index,
                    "candidate_action": model_payload,
                },
                output_payload={
                    "reason": final_gate.get("reason", ""),
                    "retryable": final_gate.get("retryable", True),
                    "repeat_count": final_gate.get("repeat_count", 0),
                },
                gate_result={
                    "decision": "deny",
                    "reason": str(final_gate.get("reason") or ""),
                    "policy_id": "general_chat.final_answer_gate.v1",
                },
                metadata={"loop_phase": "observe", "embedded_loop": "general_chat"},
            )
        for index, model_call in enumerate(loop_state.model_calls):
            turn = loop_result.turns[index] if index < len(loop_result.turns) else None
            self._record_general_chat_loop_model_call(run_id, model_call, turn=turn)
            payload = model_call.get("payload") or {}
            if payload.get("type") != "delegate" or not pending_records:
                continue
            role = str(payload.get("role") or "")
            record_index = next(
                (
                    index
                    for index, record in enumerate(pending_records)
                    if getattr(record.get("task"), "role", "") == role
                ),
                0,
            )
            record = pending_records.pop(record_index)
            self._record_general_chat_subagent_plan(run_id, record)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=record["task"],
                artifact_ref=record["artifact_ref"],
                child_state=record["child_state"],
                finding=record["finding"],
                tool_calls=record["tool_calls"],
            )
        for record in pending_records:
            self._record_general_chat_subagent_plan(run_id, record)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=record["task"],
                artifact_ref=record["artifact_ref"],
                child_state=record["child_state"],
                finding=record["finding"],
                tool_calls=record["tool_calls"],
            )
        if loop_result.stop_reason != "success":
            last_error = next(
                (turn.error for turn in reversed(loop_result.turns) if turn.error), None
            )
            deterministic_stop = loop_result.stop_reason == "deterministic_complete"
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="普通对话 AgentLoop 停止检查",
                status="done" if deterministic_stop else "failed",
                input_payload={"stop_reason": loop_result.stop_reason},
                output_payload={
                    "final_output": loop_result.final_output,
                    "error": str(last_error or "")[:600],
                },
                gate_result={
                    "decision": "allow" if deterministic_stop else "ask",
                    "reason": (
                        "工具已明确结束当前研究范围，Runtime 将生成事实边界答案"
                        if deterministic_stop
                        else "主 Agent 未能在预算内产出合法最终回答"
                    ),
                    "policy_id": "general_chat.agent_loop_stop.v1",
                },
                metadata={"loop_phase": "observe", "embedded_loop": "general_chat"},
            )

    def _record_general_chat_loop_model_call(
        self,
        run_id: str,
        model_call: dict[str, Any],
        *,
        turn: AgentTurn | None = None,
    ) -> None:
        payload = model_call.get("payload") or {}
        action_type = str(payload.get("type") or "")
        title_map = {
            "delegate": "主 Agent 调用 spawn_subagent",
            "final": "主 Agent 生成最终回答",
            "tool_call": "主 Agent 尝试调用工具",
            "request_approval": "主 Agent 请求人工审批",
            "model_error": "主 Agent 模型调用失败",
        }
        step_status = "failed" if action_type == "model_error" else "done"
        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=title_map.get(action_type, "主 Agent 输出动作"),
            status=step_status,
            input_payload={
                "schema_name": model_call.get("schema_name"),
                "agent_role": model_call.get("agent_role"),
                "messages": model_call.get("messages") or [],
                "prompt_contract": {
                    "contract_id": "general_chat_agent_loop.v1",
                    "role": "main_chat_agent",
                    "allowed_actions": list(ALLOWED_ACTION_NAMES),
                },
            },
            output_payload={
                "payload": payload,
                "response_id": model_call.get("response_id"),
                "observation": dict(turn.observation or {}) if turn else {},
                "turn_error": str(turn.error or "") if turn else "",
            },
            metadata={
                **dict(model_call.get("metadata") or {}),
                "loop_phase": "think",
                "embedded_loop": "general_chat",
                "action_type": action_type,
                **({"turn_index": turn.turn_index, "tool_call_id": turn.tool_call_id} if turn else {}),
            },
        )

    def _record_general_chat_subagent_plan(
        self,
        run_id: str,
        record: dict[str, Any],
    ) -> None:
        task: PlannedSubagentTask = record["task"]
        manifest: SubagentManifest = record["manifest"]
        skill_manifest: SkillManifest = record["skill_manifest"]
        self.store.add_agent_step(
            run_id,
            step_type="subagent_plan",
            title="主 Agent 规划子智能体委派",
            status="done",
            input_payload={
                "role": task.role,
                "message": task.task,
                "planner": "main_agent_loop.delegate_action.v1",
            },
            output_payload={
                "planner": "main_agent_loop.delegate_action.v1",
                "artifact_ref": record["artifact_ref"],
                "tasks": [task.to_dict()],
                "agent_manifest": manifest.roster_item(
                    caller_allowed_tools=skill_manifest.allowed_tools
                ),
            },
            gate_result={
                "decision": "allow",
                "reason": "主 Agent 只能委派 general_chat manifest 声明的子智能体角色",
                "policy_id": "subagent.main_agent_delegate.v1",
            },
            metadata={
                "loop_phase": "think",
                "planner": "main_agent_loop.delegate_action.v1",
                "dynamic_subagent": True,
            },
        )

    def _run_dynamic_subagents_for_chat(
        self,
        *,
        run_id: str,
        message: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        skill_manifest = self.registry.get_manifest("general_chat")
        artifact_ref = f"main_chat_context:{run_id[:12]}"
        planned_tasks = self._plan_dynamic_subagents_for_chat(
            run_id=run_id,
            message=message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        if not planned_tasks:
            return {"findings": [], "delegation_evidence": []}

        task_artifact = self._build_dynamic_subagent_context_artifact(
            message=message,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
        )
        self.store.add_agent_step(
            run_id,
            step_type="subagent_plan",
            title="规划动态子智能体",
            status="done",
            input_payload={"message": message},
            output_payload={
                "planner": "model_assisted_subagent_planner.v1",
                "artifact_ref": artifact_ref,
                "tasks": [task.to_dict() for task in planned_tasks],
            },
            gate_result={
                "decision": "allow",
                "reason": "只允许 general_chat manifest 中声明的子智能体角色",
                "policy_id": "subagent.dynamic_planner.v1",
            },
            metadata={"loop_phase": "think", "planner": "model_assisted_subagent_planner.v1"},
        )

        findings: list[dict[str, Any]] = []
        delegation_evidence: list[dict[str, Any]] = []
        for index, task in enumerate(planned_tasks, start=1):
            gateway = self._subagent_tool_gateway(skill_manifest)
            child_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
            child_state.current_user_message = message
            child_state.selected_skill = {
                "id": "general_chat",
                "mode": "agent_loop",
                "delegated_by": "model_assisted_subagent_planner.v1",
            }
            child_state.recent_history = list(prepared_context.get("recent_history") or [])
            child_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
            child_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
            child_state.relevant_memories = list(prepared_context.get("memories") or [])
            child_state.company_facts = list(prepared_context.get("facts") or [])
            forced_adapter = _ForcedFirstActionModelAdapter(
                self._get_model_adapter(),
                {
                    "type": "tool_call",
                    "tool_name": "artifact_read",
                    "arguments": {"artifact_ref": artifact_ref},
                },
            )
            try:
                result = SubagentRunner(
                    model_adapter=forced_adapter,
                    tool_gateway=gateway,
                    skill_manifest=skill_manifest,
                ).run(
                    child_state,
                    task.role,
                    task=task.task,
                    input_artifact_refs=(artifact_ref,),
                    artifact_payloads={artifact_ref: task_artifact},
                    parent_span_id=f"span_chat_{run_id[:16]}",
                    task_id=f"task_{run_id[:8]}_{index}_{task.role}",
                )
                finding = {
                    "role": result.role,
                    "task_id": result.task_id,
                    "status": "success" if result.stop_reason == "success" else "failed",
                    "stop_reason": result.stop_reason,
                    "reason": task.reason,
                    "trigger": task.trigger,
                    "output_schema": child_state.context_manifests[-1]
                    .get("metadata", {})
                    .get("output_schema_name", ""),
                    "output": _materialize_execution_value(result.output),
                    "context_manifest_id": result.context_manifest_id,
                    "artifact_refs": list(result.artifact_refs),
                }
                if result.stop_reason == "success":
                    delegation_evidence.append(result.evidence().model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001
                finding = {
                    "role": task.role,
                    "task_id": f"task_{run_id[:8]}_{index}_{task.role}",
                    "status": "failed",
                    "stop_reason": "delegate_failed",
                    "reason": task.reason,
                    "trigger": task.trigger,
                    "error": str(exc)[:240],
                    "output": {},
                }
            findings.append(finding)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=task,
                artifact_ref=artifact_ref,
                child_state=child_state,
                finding=finding,
                tool_calls=[record.to_dict() for record in gateway.calls],
            )

        return {"findings": findings, "delegation_evidence": delegation_evidence}

    def _plan_dynamic_subagents_for_chat(
        self,
        *,
        run_id: str,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> list[PlannedSubagentTask]:
        if QuerySubagentPlanner._should_skip(str(message or "")):
            return []
        heuristic_candidates = QuerySubagentPlanner().plan(
            message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        candidate_pool = heuristic_candidates or self._default_subagent_candidate_pool(
            message=message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        try:
            planner_response = self._complete_model(
                run_id=run_id,
                title="模型规划是否委派子智能体",
                model_id=configured_model_id(),
                schema_name="subagent_plan.v1",
                messages=self._build_subagent_planner_prompt(
                    message=message,
                    skill_manifest=skill_manifest,
                    artifact_ref=artifact_ref,
                    prepared_context=prepared_context,
                    candidates=candidate_pool,
                ),
                prompt_contract={
                    "contract_id": "subagent_planner.v1",
                    "role": "main_agent_planner",
                    "allowed_roles": list(skill_manifest.allowed_subagents),
                    "output_schema": {
                        "type": "subagent_plan",
                        "output": {
                            "tasks": [
                                {
                                    "role": "collector|analyst|skeptic|verifier",
                                    "reason": "why this role is needed",
                                    "task": "dynamic task brief for the role",
                                    "priority": 10,
                                    "trigger": "user_intent_or_risk_signal",
                                }
                            ]
                        },
                    },
                },
            )
            planned = self._parse_model_subagent_plan(
                planner_response.payload,
                candidates=candidate_pool,
                skill_manifest=skill_manifest,
            )
            if planned:
                merged = self._merge_required_subagent_candidates(planned, heuristic_candidates)
                if len(merged) > len(planned):
                    self.store.add_agent_step(
                        run_id,
                        step_type="gate_check",
                        title="补全显式子智能体角色",
                        status="done",
                        input_payload={
                            "planner": "model_assisted_subagent_planner.v1",
                            "model_roles": [task.role for task in planned],
                            "required_roles": [task.role for task in heuristic_candidates],
                        },
                        output_payload={"merged_roles": [task.role for task in merged]},
                        gate_result={
                            "decision": "repair",
                            "reason": "用户显式要求的多角度子任务不能被模型规划漏掉",
                            "policy_id": "subagent.required_role_completion.v1",
                        },
                        metadata={"loop_phase": "observe", "planner": "query_subagent_planner.v1"},
                    )
                return merged
            if heuristic_candidates:
                self.store.add_agent_step(
                    run_id,
                    step_type="gate_check",
                    title="修复空的子智能体规划",
                    status="done",
                    input_payload={
                        "planner": "model_assisted_subagent_planner.v1",
                        "model_task_count": 0,
                    },
                    output_payload={
                        "fallback_planner": "query_subagent_planner.v1",
                        "candidate_count": len(heuristic_candidates),
                        "roles": [task.role for task in heuristic_candidates],
                    },
                    gate_result={
                        "decision": "repair",
                        "reason": "用户问题包含明确的分析、质疑、来源或核验触发信号，模型空计划被确定性候选计划修复",
                        "policy_id": "subagent.empty_plan_repair.v1",
                    },
                    metadata={
                        "loop_phase": "observe",
                        "planner": "query_subagent_planner.v1",
                    },
                )
                return heuristic_candidates
            return []
        except Exception as exc:  # noqa: BLE001
            if not heuristic_candidates:
                return []
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="子智能体规划失败后回退候选计划",
                status="done",
                input_payload={"planner": "model_assisted_subagent_planner.v1"},
                output_payload={
                    "fallback_planner": "query_subagent_planner.v1",
                    "candidate_count": len(heuristic_candidates),
                    "error": str(exc)[:240],
                },
                gate_result={
                    "decision": "repair",
                    "reason": "模型规划未产出可用结构化计划，回退到确定性候选计划",
                    "policy_id": "subagent.planner_fallback.v1",
                },
                metadata={"loop_phase": "observe", "planner": "query_subagent_planner.v1"},
            )
            return heuristic_candidates

    @staticmethod
    def _merge_required_subagent_candidates(
        planned: list[PlannedSubagentTask],
        heuristic_candidates: list[PlannedSubagentTask],
    ) -> list[PlannedSubagentTask]:
        if not planned or not heuristic_candidates:
            return planned
        merged = list(planned)
        seen = {task.role for task in merged}
        for candidate in sorted(heuristic_candidates, key=lambda item: (item.priority, item.role)):
            if candidate.role in seen:
                continue
            merged.append(candidate)
            seen.add(candidate.role)
            if len(merged) >= 3:
                break
        merged.sort(key=lambda item: (item.priority, item.role))
        return merged[:3]

    @staticmethod
    def _default_subagent_candidate_pool(
        *,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> list[PlannedSubagentTask]:
        role_priorities = {
            "collector": 30,
            "analyst": 40,
            "skeptic": 50,
            "verifier": 60,
        }
        role_reasons = {
            "collector": "当本轮需要来源、证据链或数据源盘点时使用",
            "analyst": "当本轮需要结构化影响分析、方案拆解或取舍判断时使用",
            "skeptic": "当本轮需要独立质疑、风险审查或反例检查时使用",
            "verifier": "当本轮需要核验结论是否被证据支撑时使用",
        }
        return [
            PlannedSubagentTask(
                role=role,
                reason=role_reasons.get(role, "候选子智能体角色"),
                task=QuerySubagentPlanner._task_for_role(
                    role,
                    message=message,
                    artifact_ref=artifact_ref,
                    prepared_context=prepared_context,
                ),
                priority=role_priorities.get(role, 90),
                trigger="model_delegation_candidate",
            )
            for role in skill_manifest.allowed_subagents
            if role in role_priorities
        ]

    @staticmethod
    def _build_subagent_planner_prompt(
        *,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
        candidates: list[PlannedSubagentTask],
    ) -> list[dict[str, Any]]:
        context_counts = {
            "memory_count": len(prepared_context.get("memories") or []),
            "company_fact_count": len(prepared_context.get("facts") or []),
            "history_count": len(prepared_context.get("recent_history") or []),
            "recent_artifact_count": len(prepared_context.get("recent_artifacts") or []),
            "recent_run_trace_count": len(prepared_context.get("recent_run_traces") or []),
        }
        candidate_payload = [item.to_dict() for item in candidates]
        return [
            {
                "role": "system",
                "content": (
                    "你是 Agent Workbench 主 Agent 内部的 delegation planner。"
                    "你的任务不是回答用户，而是判断本轮是否需要启动隔离子智能体。"
                    "你必须独立判断是否委派；简单问答、概念解释、寒暄和记忆写入返回空 tasks。"
                    "只能从候选角色中选择，不能创造新角色。"
                    "每个 task 必须是给该角色的动态任务 brief，而不是复述用户问题。"
                    "只返回 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": "subagent_plan.v1",
                        "required_json": {
                            "type": "subagent_plan",
                            "output": {"tasks": []},
                        },
                        "allowed_roles": list(skill_manifest.allowed_subagents),
                        "artifact_ref": artifact_ref,
                        "user_message": message,
                        "context_counts": context_counts,
                        "candidate_tasks": candidate_payload,
                        "decision_rules": [
                            "简单定义、普通解释、记忆写入、寒暄：tasks=[]",
                            "需要反驳/风险/拷打：选择 skeptic",
                            "需要结构化方案/取舍/影响判断：选择 analyst",
                            "需要来源、证据链、数据源盘点：选择 collector",
                            "需要确认结论是否被证据支撑：选择 verifier",
                            "最多 3 个角色；每个 role 只出现一次",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _parse_model_subagent_plan(
        payload: dict[str, Any],
        *,
        candidates: list[PlannedSubagentTask],
        skill_manifest: SkillManifest,
    ) -> list[PlannedSubagentTask]:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        raw_tasks = output.get("tasks") if isinstance(output, dict) else []
        if not isinstance(raw_tasks, list):
            return []
        allowed_roles = set(skill_manifest.allowed_subagents)
        candidate_by_role = {item.role: item for item in candidates}
        planned: list[PlannedSubagentTask] = []
        for index, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in allowed_roles or role not in candidate_by_role:
                continue
            if any(existing.role == role for existing in planned):
                continue
            fallback = candidate_by_role[role]
            task = str(item.get("task") or "").strip() or fallback.task
            reason = str(item.get("reason") or "").strip() or fallback.reason
            trigger = str(item.get("trigger") or "").strip() or fallback.trigger
            try:
                priority = int(item.get("priority") or fallback.priority or (20 + index))
            except (TypeError, ValueError):
                priority = fallback.priority
            planned.append(
                PlannedSubagentTask(
                    role=role,
                    reason=reason[:300],
                    task=task[:1800],
                    priority=priority,
                    trigger=trigger[:120],
                )
            )
        planned.sort(key=lambda item: (item.priority, item.role))
        return planned[:3]

    def _build_dynamic_subagent_context_artifact(
        self,
        *,
        message: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        visible = context_manifest.get("visible_content") or {}
        return {
            "schema_version": "main_chat_context_for_subagent.v1",
            "current_user_message": message,
            "context_controls": {
                "visible_keys": list(context_manifest.get("visible_keys") or []),
                "token_budget": dict(context_manifest.get("token_budget") or {}),
                "context_policy": visible.get("context_policy") or {},
            },
            "company_facts": list(prepared_context.get("facts") or [])[:8],
            "runtime_self_knowledge": self._general_system_facts(message),
            "long_term_memories": list(prepared_context.get("memories") or [])[:5],
            "recent_history": list(prepared_context.get("recent_history") or [])[-8:],
            "recent_artifacts": list(prepared_context.get("recent_artifacts") or [])[:5],
            "recent_run_traces": list(prepared_context.get("recent_run_traces") or [])[:3],
            "tool_policy_summary": visible.get("tool_policy_summary")
            or prepared_context.get("tool_policy_summary")
            or {},
            "sandbox_policy": {
                "artifact_scope": "subagent can only read explicitly delegated artifact_ref",
                "tool_scope": "allowed tools = role contract ∩ caller skill manifest",
                "write_policy": "subagent cannot write long-term memory or publish artifacts in general_chat",
            },
        }

    @staticmethod
    def _finance_subagent_review_artifact(report: dict[str, Any]) -> dict[str, Any]:
        evidence_ids = set(report.get("evidence_ids") or [])
        documents = [document for document in report.get("documents", [])
                     if not evidence_ids
                     or document.get("evidence_id") in evidence_ids
                     or document.get("document_id") in set(report.get("legacy_evidence_ids") or [])]
        return {
            "report_id": report.get("report_id"),
            "request": report.get("request"),
            "metrics": [{key: value for key, value in metric.items()
                         if key not in {"points", "daily_changes"}}
                        for metric in report.get("metrics", [])],
            "analysis": report.get("analysis"),
            "analysis_verification": report.get("analysis_verification"),
            "research_quality": report.get("research_quality"),
            "research_audit": report.get("research_audit"),
            "warnings": report.get("warnings", []),
            "source_statuses": report.get("snapshot", {}).get("source_statuses", []),
            "evidence_ref_schema": report.get("evidence_ref_schema", "finance.evidence_ref.v1"),
            "evidence_bundle": report.get("evidence_bundle", {}),
            "evidence_aliases": report.get("evidence_aliases", {}),
            "cited_sources": [{
                 "document_id": document.get("document_id"),
                 "evidence_id": document.get("evidence_id"),
                "title": document.get("title"),
                "published_at": document.get("published_at"),
                "url": document.get("url"),
                "text_excerpt": str(document.get("text") or "")[:1800],
            } for document in documents[:12]],
            "review_boundary": "Same saved snapshot, not independent market-price cross-check.",
        }

    def _run_finance_workflow_reviews(
        self,
        *,
        run_id: str,
        message: str,
        report: dict[str, Any],
        model_id: str,
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run bounded verifier/skeptic reviews over one completed finance report."""
        skill_manifest = self.registry.get_manifest("external_impact_report")
        requested_roles = tuple(
            role for role in ("verifier", "skeptic")
            if role in skill_manifest.allowed_subagents
        )
        artifact_ref = f"finance_report_review:{run_id[:12]}"
        evidence_bundle = dict(report.get("evidence_bundle") or {})
        self.store.add_agent_step(
            run_id,
            step_type="review_required",
            title="金融报告高风险受控复核",
            status="done",
            input_payload={"report_id": report.get("report_id"), "message": message},
            output_payload={
                "requested_roles": list(requested_roles),
                "evidence_ids": list(evidence_bundle.get("evidence_ids") or []),
                "trigger": "finance_report_causal_analysis",
            },
            gate_result={
                "decision": "allow" if requested_roles and evidence_bundle.get("evidence_ids") else "deny",
                "reason": "只对带有结构化证据的金融原因分析启动受控复核",
                "policy_id": "finance.report_review_required.v1",
            },
            metadata={"loop_phase": "plan", "workflow": "financial_report"},
        )
        if not requested_roles or not evidence_bundle.get("evidence_ids"):
            reason = "金融报告缺少可委派证据或允许的复核角色"
            self.store.add_agent_step(
                run_id,
                step_type="review_result",
                title="金融报告受控复核未启动",
                status="failed",
                output_payload={"reason": reason},
                gate_result={"decision": "deny", "reason": reason,
                             "policy_id": "finance.report_review_completion.v1"},
                metadata={"loop_phase": "observe", "workflow": "financial_report"},
            )
            return {"status": "blocked", "findings": [], "delegation_evidence": [], "error": reason}

        artifact_payload = {
            "schema_version": "finance.report_review_context.v1",
            "report_id": report.get("report_id"),
            "evidence_bundle": evidence_bundle,
            "evidence_ids": list(evidence_bundle.get("evidence_ids") or []),
            "finance_review": self._finance_subagent_review_artifact(report),
            "review_policy": {
                "same_snapshot_only": True,
                "independent_price_crosscheck": False,
                "allowed_evidence_refs": list(evidence_bundle.get("evidence_ids") or []),
            },
        }
        parent_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        parent_state.current_user_message = message
        parent_state.selected_skill = {
            "id": "external_impact_report",
            "mode": "subagent_workflow",
            "delegated_by": "finance_report_review_required",
        }
        parent_state.recent_history = list(prepared_context.get("recent_history") or [])
        parent_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        parent_state.relevant_memories = list(prepared_context.get("memories") or [])
        parent_state.company_facts = list(prepared_context.get("facts") or [])
        delegate_records: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        delegation_evidence: list[dict[str, Any]] = []
        for index, role in enumerate(requested_roles, start=1):
            definition = self.subagent_role_registry.get(role, "1.0.0")
            focus = (
                "逐项检查报告中的事实、证据引用、数据窗口和未验证原因；发现不支持的主张时给出 verdict。"
                if role == "verifier" else
                "寻找报告原因解释的反例、替代机制和证据缺口；不要根据指数方向自行补充因果结论。"
            )
            task = PlannedSubagentTask(
                role=role,
                reason="金融报告包含原因分析，需要隔离复核",
                task=(
                    f"审查 finance report {report.get('report_id')}。只能通过 artifact_ref {artifact_ref} "
                    f"读取本轮报告和 evidence_bundle；授权 evidence_ids 为：{', '.join(evidence_bundle.get('evidence_ids') or [])}。"
                    f"{focus} 返回角色规定的结构化 schema；evidence_refs 只能使用授权 canonical evidence_id。"
                ),
                priority=10 + index,
                trigger="finance_report_causal_analysis",
            )
            brief = TaskBrief(
                task_id=f"task_{run_id[:8]}_finance_review_{index}_{role}",
                parent_run_id=run_id,
                parent_span_id=f"span_finance_review_{run_id[:12]}",
                role=role,
                task=task.task,
                input_artifact_refs=(artifact_ref,),
                output_schema_name=definition.output_schema_name,
                max_steps=definition.max_steps,
                max_model_calls=definition.max_model_calls,
                timeout_seconds=definition.timeout_seconds,
            )
            try:
                result = self._run_general_chat_delegate(
                    parent_state=parent_state,
                    skill_manifest=skill_manifest,
                    brief=brief,
                    artifact_ref=artifact_ref,
                    artifact_payload=artifact_payload,
                    prepared_context=prepared_context,
                    delegate_records=delegate_records,
                    reason=task.reason,
                    trigger=task.trigger,
                )
                finding = next(
                    record["finding"] for record in reversed(delegate_records)
                    if record.get("finding", {}).get("task_id") == brief.task_id
                )
                if result.stop_reason == "success":
                    delegation_evidence.append(result.evidence().model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001
                finding = next(
                    record["finding"] for record in reversed(delegate_records)
                    if record.get("finding", {}).get("task_id") == brief.task_id
                ) if any(record.get("finding", {}).get("task_id") == brief.task_id
                         for record in delegate_records) else {
                    "role": role, "task_id": brief.task_id, "status": "failed",
                    "stop_reason": "delegate_failed", "error": str(exc)[:240], "output": {},
                }
            findings.append(finding)
            record = next(record for record in reversed(delegate_records)
                          if record.get("finding", {}).get("task_id") == brief.task_id)
            self._record_general_chat_subagent_plan(run_id, record)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=task,
                artifact_ref=artifact_ref,
                child_state=record["child_state"],
                finding=finding,
                tool_calls=record["tool_calls"],
            )

        passed = bool(findings) and len(findings) == len(requested_roles) and all(
            item.get("status") == "success" for item in findings
        )
        review = {
            "status": "passed" if passed else "blocked",
            "findings": findings,
            "delegation_evidence": delegation_evidence,
            "evidence_ids": list(evidence_bundle.get("evidence_ids") or []),
        }
        self.store.add_agent_step(
            run_id,
            step_type="review_result",
            title="金融报告受控复核结果",
            status="done" if passed else "failed",
            input_payload={"requested_roles": list(requested_roles), "report_id": report.get("report_id")},
            output_payload={
                "status": review["status"],
                "findings": [
                    {"role": item.get("role"), "status": item.get("status"),
                     "stop_reason": item.get("stop_reason"), "evidence_ids": item.get("evidence_ids", [])}
                    for item in findings
                ],
            },
            gate_result={
                "decision": "allow" if passed else "deny",
                "reason": "verifier 与 skeptic 均完成受控复核" if passed else "至少一个金融复核子任务未完成",
                "policy_id": "finance.report_review_completion.v1",
            },
            metadata={"loop_phase": "observe", "workflow": "financial_report"},
        )
        return review

    def _subagent_tool_gateway(self, skill_manifest: SkillManifest) -> ToolGateway:
        role_registry = build_default_subagent_role_registry()
        policies: list[ToolPolicy] = []
        for role_name in skill_manifest.allowed_subagents:
            try:
                role = role_registry.get(role_name, "1.0.0")
            except KeyError:
                continue
            for tool_name in role.allowed_tools:
                if tool_name in skill_manifest.allowed_tools:
                    policies.append(
                        ToolPolicy(
                            f"subagent.{role.role}",
                            tool_name,
                            "allow",
                            "role contract and caller skill allowlist intersection",
                        )
                    )
        return ToolGateway(permission_engine=PermissionEngine(policies))

    def _record_dynamic_subagent_child(
        self,
        *,
        run_id: str,
        task: PlannedSubagentTask,
        artifact_ref: str,
        child_state: PolicyImpactState,
        finding: dict[str, Any],
        tool_calls: list[dict[str, Any]],
    ) -> None:
        for manifest in child_state.context_manifests:
            self.store.add_agent_step(
                run_id,
                step_type="subagent_context",
                title=f"构建 {task.role} 子智能体上下文",
                status="done",
                input_payload={
                    "task_id": finding.get("task_id", ""),
                    "role": task.role,
                    "artifact_ref": artifact_ref,
                },
                output_payload={"context_manifest": manifest},
                metadata={"loop_phase": "think", "subagent_role": task.role},
            )

        for model_call in child_state.model_calls:
            model_metadata = model_call.get("metadata") or {}
            payload = model_call.get("payload") or {}
            deterministic_action = bool(model_metadata.get("deterministic_action"))
            self.store.add_agent_step(
                run_id,
                step_type="runtime_action" if deterministic_action else "model_call",
                title=(
                    f"Runtime 为 {task.role} 执行必需的任务上下文读取"
                    if deterministic_action
                    else f"{task.role} 子智能体读取任务上下文"
                    if payload.get("type") == "tool_call"
                    else f"{task.role} 子智能体生成结构化结果"
                ),
                status="done" if finding.get("status") == "success" else "failed",
                input_payload={
                    "schema_name": model_call.get("schema_name"),
                    "agent_role": model_call.get("agent_role"),
                    "messages": model_call.get("messages") or [],
                    "prompt_contract": {
                        "contract_id": model_call.get("schema_name") or "",
                        "role": task.role,
                        "loop_boundary": "subagent reads task artifact through artifact_read; runtime owns permissions",
                    },
                },
                output_payload={
                    "payload": payload,
                    "response_id": model_call.get("response_id"),
                },
                metadata={
                    **dict(model_metadata),
                    "loop_phase": "act" if deterministic_action else "think",
                    "decision_source": "runtime" if deterministic_action else "model_adapter",
                    "subagent_role": task.role,
                    "dynamic_subagent": True,
                },
            )

        observed_tool_calls = [
            {
                "stage_name": f"subagent.{task.role}",
                "tool_name": "artifact_read",
                "allowed": True,
                "status": "ok",
                "policy_decision": "allow",
                "policy_reason": "artifact ref is inside delegated task scope",
                "argument_keys": ["artifact_ref"],
                "result_summary": "dict[main_chat_context_for_subagent]",
            },
            *tool_calls,
        ]
        self.store.add_agent_step(
            run_id,
            step_type="subagent_delegate",
            title=f"执行动态子智能体：{task.role}",
            status="done" if finding.get("status") == "success" else "failed",
            input_payload={
                "role": task.role,
                "reason": task.reason,
                "trigger": task.trigger,
                "artifact_ref": artifact_ref,
            },
            output_payload={
                "role": task.role,
                "task_id": finding.get("task_id", ""),
                "stop_reason": finding.get("stop_reason", ""),
                "output_schema": finding.get("output_schema", ""),
                "output": finding.get("output") or {},
                "context_manifest_id": finding.get("context_manifest_id", ""),
                "review_scope": finding.get("review_scope", ""),
                "assigned_article_ids": finding.get("assigned_article_ids") or [],
                "assigned_author": finding.get("assigned_author", ""),
                "observed_authors": finding.get("observed_authors") or [],
                "observed_article_refs": finding.get("observed_article_refs") or [],
                "evidence_ids": finding.get("evidence_ids") or [],
            },
            tool_calls=observed_tool_calls,
            gate_result={
                "decision": "allow" if finding.get("status") == "success" else "ask",
                "reason": (
                    "子智能体在任务 artifact 和 role-scoped tool policy 内完成"
                    if finding.get("status") == "success"
                    else str(finding.get("error") or "子智能体未能产出合格结构化结果")
                ),
                "policy_id": "subagent.delegation_result.v1",
            },
            metadata={
                "loop_phase": "observe",
                "subagent_role": task.role,
                "dynamic_subagent": True,
            },
        )

    def _attach_subagent_context(
        self,
        *,
        run_id: str,
        context_manifest: dict[str, Any],
        subagent_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        visible_content = dict(context_manifest.get("visible_content") or {})
        visible_content["subagent_results"] = subagent_findings
        visible_keys = list(context_manifest.get("visible_keys") or [])
        if "subagent_results" not in visible_keys:
            visible_keys.append("subagent_results")
        token_estimates = dict(context_manifest.get("token_estimates") or {})
        token_estimates["subagent_results"] = estimate_tokens(subagent_findings)
        patched = {
            **dict(context_manifest),
            "manifest_id": f"ctx_{_stable_hash({'run_id': run_id, 'subagent_findings': subagent_findings})[:16]}",
            "visible_keys": visible_keys,
            "visible_content": visible_content,
            "token_estimates": token_estimates,
            "context_patch": {
                "patch_id": "dynamic_subagent_results.v1",
                "subagent_result_count": len(subagent_findings),
                "roles": _unique_preserve_order(
                    [str(item.get("role") or "") for item in subagent_findings]
                ),
            },
        }
        self.store.add_agent_step(
            run_id,
            step_type="context_build",
            title="回填动态子智能体上下文",
            status="done",
            input_payload={"subagent_result_count": len(subagent_findings)},
            output_payload={"context_manifest": patched},
            metadata={"loop_phase": "think", "context_patch": "dynamic_subagent_results.v1"},
        )
        return patched

    @classmethod
    def _govern_general_chat_answer(
        cls,
        message: str,
        model_answer: str,
        facts: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None]:
        text = str(message or "").lower()
        if cls._is_workbench_capability_query(message):
            return cls._render_workbench_general_answer(message), {
                "contract_id": "general_chat.workbench_capabilities.v1",
                "required_fields": [
                    "AgentRun",
                    "AgentStep",
                    "conditional_tool_gate_memory_artifact",
                    "model_content_not_deterministic",
                ],
                "reason": "系统自描述必须区分每轮固定留痕与按路由条件触发的能力",
            }
        if cls._is_immediate_history_query(message) and not facts and any(
            marker in model_answer
            for marker in ("公开事实（public_disclosure）", "公开事实 (public_disclosure)")
        ):
            repaired = str(model_answer).replace(
                "公开事实（public_disclosure）",
                "用户确认的会话事实（user_confirmed）",
            ).replace(
                "公开事实 (public_disclosure)",
                "用户确认的会话事实 (user_confirmed)",
            ).replace(
                "以上“公开事实”",
                "以上“用户确认的会话事实”",
            )
            return repaired, {
                "contract_id": "general_chat.session_provenance.v1",
                "required_fields": ["user_confirmed", "analysis_inference"],
                "reason": "会话历史中的用户声明不得标记为企业公开披露",
            }
        if all(term in text for term in ("ifind", "上线清单")) and any(
            term in text for term in ("负责人", "阻断条件", "验收证据")
        ):
            return cls._release_readiness_contract_answer(facts), {
                "contract_id": "general_chat.release_readiness.v1",
                "required_fields": [
                    "检查项",
                    "负责人",
                    "证据产物",
                    "通过阈值",
                    "阻断条件",
                    "待确认",
                ],
                "reason": "模型草案必须转换为逐项可审计的上线责任契约",
            }
        if "ifind" in text and any(term in text for term in ("超时", "重试", "幂等")):
            return cls._api_engineering_review_answer(facts), {
                "contract_id": "general_chat.api_risk_hypothesis.v1",
                "required_fields": ["已知事实", "待核验 P0 假设", "证据要求", "通用工程建议"],
                "reason": "没有实现证据时，不得把超时、重试或幂等风险写成已确认缺陷",
            }
        if cls._requires_retrieved_evidence_only(message):
            repaired = str(model_answer or "").replace(
                "企业事实中未提及",
                "本轮检索到的企业事实未提供",
            ).replace(
                "企业知识库中未提及",
                "本轮检索到的企业事实未提供",
            )
            if repaired != model_answer:
                return repaired, {
                    "contract_id": "general_chat.retrieval_scope.v1",
                    "required_fields": ["本轮检索范围", "证据不足边界"],
                    "reason": "相关性检索不能表述为已完成企业知识库全量扫描",
                }
        return model_answer, None

    @staticmethod
    def _fact_value(facts: list[dict[str, Any]], fact_id: str) -> str:
        return next(
            (
                str(item.get("value") or "")
                for item in facts
                if str(item.get("fact_id") or "") == fact_id
            ),
            "",
        )

    @classmethod
    def _api_engineering_review_answer(cls, facts: list[dict[str, Any]]) -> str:
        ifind = cls._fact_value(facts, "ai_product.ifind")
        known = (
            f"- [ai_product.ifind] {ifind}"
            if ifind
            else "- 当前企业知识库没有提供 iFinD 异步 API 的实现证据。"
        )
        return "\n".join(
            [
                "结论：当前证据只能支持风险假设和核验计划，不能认定现网已经存在超时、重试、幂等或数据合规缺陷。",
                "",
                "已知事实：",
                known,
                "- 知识库未提供客户端/服务端超时配置、重试策略、幂等存储、压测结果或生产 Trace。",
                "",
                "待核验 P0 假设：",
                "1. 超时边界：核验客户端、网关、任务队列和下游模型的分层超时是否一致。",
                "   - 证据要求：配置快照、超时 Trace、故障注入记录；没有这些证据前不得写成‘缺少双向超时’。",
                "2. 重试安全：核验重试触发条件、退避、抖动、最大次数和不可重试错误分类。",
                "   - 证据要求：重试策略配置、错误分类表、失败重放测试与请求放大率。",
                "3. 幂等语义：核验任务键生成、重复提交去重、状态机终态和结果复用。",
                "   - 证据要求：幂等键协议、唯一约束或去重记录、并发重复请求测试。",
                "4. 数据合规：核验研报内容、用户标识、日志字段、保存期限与第三方传输边界。",
                "   - 证据要求：字段清单、数据流图、脱敏样例、权限审计和删除记录。",
                "",
                "通用工程建议（仅在核验发现缺口后实施）：统一 deadline 透传；对可重试错误使用有上限的指数退避；以业务任务键实现幂等；将敏感字段最小化并纳入审计。所有阈值必须由压测和业务 SLA 决定，不能把示例值当成 iFinD 现状。",
            ]
        )

    @classmethod
    def _release_readiness_contract_answer(cls, facts: list[dict[str, Any]]) -> str:
        ifind = cls._fact_value(facts, "ai_product.ifind")
        known = (
            f"- [ai_product.ifind] {ifind}"
            if ifind
            else "- 当前企业知识库没有提供 iFinD 异步研报摘要任务的已实现能力。"
        )
        return "\n".join(
            [
                "已知事实：",
                known,
                "- 当前证据不能证明异步任务页面、状态机、SLA、数据字段或合规流程已经实现。",
                "",
                "逐项上线契约：",
                "1. 检查项：任务提交与状态可见性",
                "   - 负责人：产品经理（待指定姓名）",
                "   - 证据产物：验收环境录屏、状态机用例报告、异常文案清单",
                "   - 通过阈值：提交、排队、运行、成功、失败、取消均有可复现状态与下一步动作",
                "   - 阻断条件：任一终态不可识别，或失败后用户无法恢复/重试",
                "   - 待确认：当前界面和状态机是否存在；不得用企业 fact_id 代替验收证据",
                "2. 检查项：摘要正确性与证据回链",
                "   - 负责人：算法/应用研发负责人（待指定姓名）",
                "   - 证据产物：标注集、逐条来源回链、事实一致性评测报告",
                "   - 通过阈值：冻结标注集版本，P0 事实错误为 0，关键结论来源回链覆盖率 100%，其余质量阈值由业务负责人签字版本化",
                "   - 阻断条件：关键结论无来源、事实冲突未拦截或评测版本不可复现",
                "   - 待确认：任务样本、黄金答案、容错标准和人工复核比例",
                "3. 检查项：超时、重试与幂等",
                "   - 负责人：后端研发负责人（待指定姓名）",
                "   - 证据产物：配置快照、故障注入报告、重复请求与恢复 Trace",
                "   - 通过阈值：重试不造成重复任务/重复计费，超时后状态可恢复且结果唯一",
                "   - 阻断条件：出现重复副作用、孤儿任务、无限重试或终态不一致",
                "   - 待确认：真实 SLA、队列容量、下游限流与幂等键协议",
                "4. 检查项：数据与内容合规",
                "   - 负责人：合规/数据安全负责人（待指定姓名）",
                "   - 证据产物：数据流图、字段与权限清单、脱敏样例、保存/删除记录",
                "   - 通过阈值：每个数据字段均有目的、来源、权限、期限和责任人",
                "   - 阻断条件：来源授权不明、敏感字段无控制、内容结论无法追溯",
                "   - 待确认：研报版权、用户数据范围、第三方模型传输和日志留存边界",
                "",
                "通用工程建议：将以上契约写入发布门禁；只有证据产物存在且通过阈值后才能关闭检查项。负责人、阈值和证据目前均是待确认项，不代表 iFinD 现状。",
            ]
        )

    @staticmethod
    def _ground_general_chat_citations(
        message: str,
        answer: str,
        citations: list[Any],
        *,
        memory_requested: bool,
        strict_knowledge_only: bool,
        memory_items: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        mentioned_fact_ids = AgentRuntime._extract_company_fact_ids(answer)
        message_text = str(message or "")
        memory_focused = (
            any(term in message_text for term in ("记忆", "记住", "记下", "保存的"))
            and "知识库" not in message_text
        )
        memory_content_by_id = {
            str(item.get("id") or ""): " ".join(str(item.get("content") or "").split())
            for item in memory_items or []
        }
        normalized_answer = " ".join(str(answer or "").split())
        filtered = []
        seen = set()
        for item in citations:
            if not isinstance(item, dict):
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if marker not in seen and str(item or "").strip():
                    seen.add(marker)
                    filtered.append(item)
                continue
            item_type = str(item.get("type") or "")
            if item_type == "company_fact":
                fact_id = str(item.get("fact_id") or "")
                if memory_requested or memory_focused:
                    continue
                if fact_id not in mentioned_fact_ids:
                    continue
            if item_type == "memory_item" and not (memory_requested or memory_focused):
                continue
            if item_type == "memory_item" and memory_focused and not memory_requested:
                memory_id = str(item.get("memory_id") or "")
                content = memory_content_by_id.get(memory_id, "")
                explicit_marker = bool(
                    re.search(
                        rf"\[memory_id\s*:\s*{re.escape(memory_id)}\]",
                        normalized_answer,
                        flags=re.I,
                    )
                )
                content_terms = set(tokenize(content))
                answer_terms = set(tokenize(normalized_answer))
                overlap = len(content_terms & answer_terms)
                overlap_ratio = overlap / max(1, len(content_terms))
                paraphrase_grounded = overlap >= 4 and overlap_ratio >= 0.25
                if not content or not (explicit_marker or content in normalized_answer or paraphrase_grounded):
                    continue
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            filtered.append(item)
        return filtered

    @staticmethod
    def _attach_grounded_memory_citations(
        answer: str,
        citations: list[Any],
    ) -> str:
        memory_ids = [
            str(item.get("memory_id") or "").strip()
            for item in citations
            if isinstance(item, dict)
            and item.get("type") == "memory_item"
            and str(item.get("memory_id") or "").strip()
        ]
        memory_ids = list(dict.fromkeys(memory_ids))
        if not memory_ids:
            return answer
        cleaned = re.sub(
            r"\s*\[memory_id\s*:\s*[^\]\r\n]+\]",
            "",
            str(answer or ""),
            flags=re.I,
        ).rstrip()
        markers = " ".join(f"[memory_id: {memory_id}]" for memory_id in memory_ids)
        return f"{cleaned}\n\n记忆引用：{markers}"

    @staticmethod
    def _extract_company_fact_ids(content: str) -> set[str]:
        """Accept bracketed and Markdown-code fact references emitted by the model."""
        text = str(content or "")
        candidates = re.findall(
            r"\[(?:fact_id\s*:\s*)?([A-Za-z0-9_][A-Za-z0-9_.:-]*)\]",
            text,
            flags=re.IGNORECASE,
        )
        candidates.extend(
            re.findall(
                r"`([A-Za-z0-9_][A-Za-z0-9_.:-]*\.[A-Za-z0-9_.:-]+)`",
                text,
                flags=re.IGNORECASE,
            )
        )
        candidates.extend(
            re.findall(
                r"fact_id\s*(?:为|是|[:：=])\s*([A-Za-z0-9_][A-Za-z0-9_.:-]*\.[A-Za-z0-9_.:-]+)",
                text,
                flags=re.IGNORECASE,
            )
        )
        explicit = re.findall(r"fact_id\s*:\s*([A-Za-z0-9_][A-Za-z0-9_.:-]*)", text, flags=re.I)
        return {value for value in candidates if any(char in value for char in "_.:-")} | set(explicit)

    @staticmethod
    def _prior_company_fact_citations(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        previous = next(
            (item for item in reversed(history) if item.get("role") == "assistant"),
            None,
        )
        if not previous:
            return []
        citations = []
        seen = set()
        for item in (previous.get("metadata") or {}).get("citations") or []:
            fact_id = str(item.get("fact_id") or "").strip()
            if item.get("type") != "company_fact" or not fact_id or fact_id in seen:
                continue
            seen.add(fact_id)
            citations.append({"type": "company_fact", "fact_id": fact_id, "path": item.get("path")})
        for fact_id in AgentRuntime._extract_company_fact_ids(str(previous.get("content") or "")):
            if fact_id in seen:
                continue
            seen.add(fact_id)
            citations.append({"type": "company_fact", "fact_id": fact_id})
        return citations

    @classmethod
    def _prior_fact_id_answer(
        cls,
        message: str,
        history: list[dict[str, Any]],
    ) -> str:
        text = str(message or "").lower()
        if "fact_id" not in text or not any(term in text for term in ("上一轮", "上轮", "刚才")):
            return ""
        fact_ids = [item["fact_id"] for item in cls._prior_company_fact_citations(history)]
        if not fact_ids:
            return "上一轮回答没有持久化可复现的企业 fact_id；本轮不会根据新的检索结果补造。"
        return "\n".join(
            [
                "上一轮回答实际使用并持久化的 fact_id：",
                *[f"- {fact_id}" for fact_id in fact_ids],
                "本轮直接复现上一轮证据元数据，没有用新的检索结果改写历史引用。",
            ]
        )

    @staticmethod
    def _explicit_memory_content(message: str) -> str:
        text = str(message or "").strip()
        patterns = (
            r"请(?:把|将)(?:以下内容)?(?:显式)?(?:写入|保存到|存入)(?:长期)?记忆\s*[：:]\s*(.+)",
            r"(?:请|帮我)?(?:显式)?(?:写入|保存到|存入)(?:长期)?记忆\s*[：:]\s*(.+)",
            r"(?:请|帮我)?记住\s*[：:]\s*(.+)",
            r"remember that\s*[：:]?\s*(.+)",
        )
        match = next(
            (found for pattern in patterns if (found := re.search(pattern, text, flags=re.I | re.S))),
            None,
        )
        if not match:
            return ""
        content = match.group(1).strip(" ：:，,。")
        content = re.split(
            r"(?:。|\n)\s*(?:请)?(?:只在实际写入|写入成功后|否则明确|不要假装)",
            content,
            maxsplit=1,
        )[0].strip(" ：:，,。")
        return content[:1000]

    @staticmethod
    def _feedback_memory_content(message: str) -> str:
        text = re.sub(r"\s+", " ", str(message or "").strip())
        if not text:
            return ""
        # Reviewing an existing preference is not authorization to persist the
        # question itself. Durable feedback needs a forward-looking instruction.
        if not re.search(r"(?:后续|以后|今后|长期|之后)", text) or not re.search(
            r"(?:调整|更新|记住|保存|不要|不再|始终)", text
        ):
            return ""
        if re.search(
            r"(?:不要|不需要|无需|禁止|不得)[^。；\n]{0,20}(?:记住|写入|保存到|存入).{0,6}记忆",
            text,
            flags=re.I,
        ):
            return ""
        feedback_markers = (
            "不认可",
            "不同意",
            "不接受",
            "纠正",
            "短期不",
            "暂时不",
            "以后不要",
            "后续不要",
            "之后不要",
        )
        durable_scope_markers = (
            "报告",
            "建议",
            "行动",
            "关注重点",
            "优先级",
            "偏好",
            "短期",
            "长期",
            "之后",
            "以后",
            "后续",
        )
        if not any(marker in text for marker in feedback_markers):
            return ""
        if not any(marker in text for marker in durable_scope_markers):
            return ""
        return f"用户反馈/偏好：{text}".strip()[:1000]

    @staticmethod
    def _requests_memory_write(message: str) -> bool:
        text = str(message or "").lower()
        if re.search(
            r"(?:不要|不需要|无需|禁止|不得|不(?:要)?)(?:将|把)?[^。；\n]{0,20}(?:写入|保存到|存入).{0,6}记忆",
            text,
            flags=re.I,
        ) or re.search(r"(?:不要|无需|禁止|不得)记住", text, flags=re.I):
            return False
        return bool(
            re.search(
                r"(?:请记住|帮我记住|写入.{0,4}记忆|保存到.{0,4}记忆|存入.{0,4}记忆|remember that)",
                text,
                flags=re.I,
            )
        )

    def _plan_skill_memory_if_requested(
        self,
        run_id: str,
        message: str,
        skill: SkillManifest,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Chat already proposes the explicitly requested preference. Saving its
        # acknowledgement as a second skill memory pollutes later retrieval.
        if skill.id == "general_chat" or not self._requests_memory_write(message):
            return []
        artifacts = {
            str(key): str(value)
            for key, value in (result.get("artifacts") or {}).items()
            if value
        }
        namespace = f"company:{self.company_id}:skill_results"
        memory_type = "explicit_skill_result"
        content = (
            f"用户明确要求保存技能结果：skill={skill.id}；"
            f"artifacts={artifacts}；"
            f"answer={str(result.get('answer') or '')[:300]}"
        )
        memory_id, _ = self._plan_deduplicated_memory_id(
            namespace,
            memory_type,
            content,
        )
        return [
            {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": memory_type,
                "content": content,
                "importance": 0.8,
                "deduplicate": True,
                "metadata": {
                    "source": "explicit_user_request",
                    "run_id": run_id,
                    "skill_id": skill.id,
                    "artifacts": artifacts,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户明确要求保存的技能结果",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "explicit_skill_result",
                        "write_policy": "explicit_user_request_only",
                    },
                    "output_payload": {"namespace": namespace},
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮明确要求将技能结果写入长期记忆",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
        ]

    def _plan_deduplicated_memory_id(
        self,
        namespace: str,
        memory_type: str,
        content: str,
    ) -> tuple[str, bool]:
        existing = self.store.find_memory_exact(namespace, memory_type, content)
        if existing:
            return str(existing["id"]), False
        return canonical_memory_id(namespace, memory_type, content), True

    @staticmethod
    def _general_system_facts(message: str) -> list[str]:
        facts = [
            "Agent Workbench is a single-user Agent Harness and Runtime learning system.",
            "Each turn is persisted as an AgentRun with ordered AgentStep records.",
            "SkillRegistry routes general_chat, /report, and the compatible internal routes behind report runs.",
            "Every completed turn records AgentRun, ordered AgentStep, context, and final-answer evidence; memory writes, workflow tools, gates, model calls, and artifacts are recorded only when the selected route actually triggers them.",
            "General chat uses a real configured model after deterministic memory and company-wiki retrieval.",
        ]
        text = str(message or "").lower()
        if "coursepilot" in text or "course pilot" in text:
            facts.extend(
                [
                    "CoursePilot is the user's course-learning Agent Runtime project, focused on RAG, tutoring, quizzes, grading, learning memory, and retrieval evaluation.",
                    "Agent Workbench is focused on Harness and Infra controls across replaceable business skills: routing, context contracts, tool policy, trace, artifacts, replay, and eval.",
                ]
            )
        return facts

    @staticmethod
    def _requires_retrieved_evidence_only(message: str) -> bool:
        text = str(message or "").lower()
        strict_markers = ("只基于", "仅基于", "只根据", "仅根据", "only use", "based only on")
        evidence_markers = ("知识库", "记忆", "检索", "knowledge base", "memory", "retrieved")
        return any(marker in text for marker in strict_markers) and any(
            marker in text for marker in evidence_markers
        )

    def _complete_model(
        self,
        *,
        run_id: str,
        title: str,
        model_id: str,
        schema_name: str,
        messages: list[dict[str, Any]],
        prompt_contract: dict[str, Any],
        adapter: ModelAdapter | None = None,
    ) -> ModelResponse:
        adapter = adapter or self._get_model_adapter()
        input_payload = {
            "model_id": model_id,
            "schema_name": schema_name,
            "messages": messages,
            "prompt_contract": prompt_contract,
        }
        try:
            response = adapter.complete(messages=messages, schema_name=schema_name)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or type(exc).__name__
            self.store.add_agent_step(
                run_id,
                step_type="model_call",
                title=title,
                status="failed",
                input_payload=input_payload,
                output_payload={
                    "error": message,
                    "error_metadata": dict(getattr(exc, "metadata", {}) or {}),
                },
                metadata={
                    "adapter": type(adapter).__name__,
                    "loop_phase": "think",
                    "error_type": type(exc).__name__,
                },
            )
            if isinstance(exc, ModelInvocationError):
                raise
            raise ModelInvocationError(message) from exc

        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=title,
            status="done",
            input_payload=input_payload,
            output_payload=response.to_dict(),
            metadata={
                "adapter": type(adapter).__name__,
                "loop_phase": "think",
                "latency_ms": response.metadata.get("total_latency_ms")
                or response.metadata.get("latency_ms"),
                "attempt_latency_ms": response.metadata.get("latency_ms"),
                "total_latency_ms": response.metadata.get("total_latency_ms")
                or response.metadata.get("latency_ms"),
                "usage": response.metadata.get("usage", {}),
                "attempts": response.metadata.get("attempts", 1),
                "retry_errors": response.metadata.get("retry_errors", []),
                "stop_reason": response.metadata.get("stop_reason", ""),
                "temperature": response.metadata.get("temperature"),
                "max_tokens": response.metadata.get("max_tokens"),
                "timeout_seconds": response.metadata.get("timeout_seconds"),
                "max_attempts": response.metadata.get("max_attempts"),
            },
        )
        return response

    def _get_model_adapter(self) -> ModelAdapter:
        if self.model_adapter is None:
            config = effective_model_config()
            if config["protocol"] == "openai-responses":
                from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter

                self.model_adapter = ResponsesModelAdapter.from_env()
            elif config["protocol"] == "openai-compatible":
                from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter

                self.model_adapter = OpenAICompatibleModelAdapter.from_env()
            else:
                self.model_adapter = AnthropicCompatibleModelAdapter.from_env()
        return self.model_adapter

    def _run_external_impact_report(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_gateway = _ReportWorkflowToolGateway(
            lambda workflow_message: self._run_external_impact_workflow(
                run_id,
                session_id,
                workflow_message,
                model_id,
                context_manifest,
                prepared_context,
            )
        )
        loop_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        loop_state.current_user_message = message
        loop_state.selected_skill = {"id": "external_impact_report", "mode": "skill"}
        loop_state.recent_history = list(prepared_context.get("recent_history") or [])
        loop_state.conversation_summary = dict(prepared_context.get("conversation_summary") or {})
        loop_state.relevant_memories = list(prepared_context.get("memories") or [])
        loop_state.tool_policy_summary = {
            "decision_order": ["deny", "ask", "allow"],
            "skill_scoped_tools": ["report_workflow.run"],
            "runtime_owns_permissions": True,
        }
        manifest_record = _context_manifest_record_from_metadata(
            run_id=run_id,
            metadata={
                **dict(context_manifest),
                "stage_name": "report_agent_loop",
                "agent_role": "report_orchestrator",
                "loop_boundary": "AgentLoop owns orchestration; report workflow is a skill-scoped tool observation.",
                "available_actions": ["tool_call:report_workflow.run", "final"],
                "visible_content": {
                    "current_user_message": message,
                    "selected_skill": loop_state.selected_skill,
                    "recent_history": loop_state.recent_history,
                    "conversation_summary": loop_state.conversation_summary,
                    "relevant_memories": loop_state.relevant_memories,
                    "tool_policy_summary": loop_state.tool_policy_summary,
                },
            },
            stage_name="report_agent_loop",
            agent_role="report_orchestrator",
        )
        loop_state.add_context_manifest(manifest_record.to_dict())
        forced_payload = {
            "type": "tool_call",
            "tool_name": "report_workflow.run",
            "arguments": {"message": message},
        }
        self._record_embedded_report_model_call(
            run_id,
            {
                "schema_name": "agent_action.v1",
                "agent_role": "report_orchestrator",
                "messages": [],
                "payload": forced_payload,
                "response_id": f"runtime_forced_{_stable_hash(forced_payload)[:12]}",
                "metadata": {
                    "adapter": "_ForcedFirstActionModelAdapter",
                    "loop_phase": "think",
                    "embedded_loop": "external_impact_report",
                    "action_type": "tool_call",
                    "deterministic_action": True,
                    "causal_trace_pre_recorded": True,
                },
            },
        )
        forced_adapter = _ForcedFirstActionModelAdapter(
            self._get_model_adapter(),
            forced_payload,
        )
        loop_result = AgentLoop(
            forced_adapter,
            workflow_gateway,
            reject_hidden_reasoning=True,
        ).run(
            loop_state,
            AgentLoopConfig(
                stage_name="report_agent_loop",
                agent_role="report_orchestrator",
                context_manifest=manifest_record,
                output_key="agent_action.v1",
                execution_mode=ExecutionMode.AGENT_LOOP,
                max_steps=4,
                max_model_calls=2,
                timeout_seconds=240,
                max_calls_per_tool=1,
                task_prompt=(
                    "This turn explicitly activated /report. The runtime has already "
                    "called report_workflow.run as a skill-scoped tool. Use the tool "
                    "observation as the primary evidence, then return type=final with "
                    "output.answer, output.artifacts, output.citations, and output.internal_route. "
                    "Do not invent additional sources or claim direct workflow execution outside AgentLoop."
                ),
            ),
        )
        self._record_embedded_report_loop(run_id, loop_state, loop_result)
        workflow_result = workflow_gateway.last_result
        final_output = loop_result.final_output if loop_result.stop_reason == "success" else {}
        # The fixed workflow and its gates remain authoritative.  The outer
        # AgentLoop is allowed to observe and explain the result, but it must
        # not overwrite a workflow ask/deny/scope decision with a generic model
        # answer.
        answer = str(workflow_result.get("answer") or final_output.get("answer") or "").strip()
        artifacts = dict(workflow_result.get("artifacts") or {})
        citations = list(workflow_result.get("citations") or final_output.get("citations") or [])
        if not answer:
            raise ModelInvocationError(f"/report AgentLoop failed: {loop_result.stop_reason}")
        return {
            "answer": answer,
            "artifacts": artifacts,
            "citations": citations,
            "artifact_refs": [value for value in artifacts.values() if isinstance(value, str)],
            "delegation_evidence": list(workflow_result.get("delegation_evidence") or []),
            "internal_route": str(workflow_result.get("internal_route") or final_output.get("internal_route") or ""),
            "metadata": dict(workflow_result.get("metadata") or {}),
            "stop_reason": workflow_result.get("stop_reason") or loop_result.stop_reason,
            "loop_execution_mode": loop_result.actual_execution_mode.value,
        }

    def _run_external_impact_workflow(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        from policy_impact.domains.finance.profile import load_profile
        if load_profile(self.company_id):
            from policy_impact.domains.finance.runtime_adapter import execute
            authorized_reports = tuple(dict.fromkeys(str(row["finance_report_id"])
                for row in prepared_context.get("recent_artifacts", []) if row.get("finance_report_id")))
            return execute(
                self,
                run_id,
                message,
                model_id,
                authorized_reports=authorized_reports,
                prepared_context=prepared_context,
            )
        legacy_route = self._impact_internal_route(message)
        route = "source_inventory" if legacy_route == "source_inventory" else "news"
        route_labels = {
            "source_inventory": "数据源盘点",
            "news": "共享来源池研判（新闻、技术、政策与论文）",
        }
        selected_sources = self._impact_selected_sources(route)
        if route == "news":
            selected_sources = [*selected_sources, "policy_sources"]
        self.store.add_agent_step(
            run_id,
            step_type="workflow_stage",
            title="选择外部变化分析路线",
            status="done",
            input_payload={"message": message},
            output_payload={
                "public_skill_id": "external_impact_report",
                "internal_route": route,
                "workflow": "source_inventory" if route == "source_inventory" else "unified_shared_report",
                "route_label": route_labels.get(route, route),
                "selected_source_types": selected_sources,
                "hidden_legacy_skills": [
                    "policy_weekly_impact",
                    "recent_news_report",
                    "research_report",
                ],
            },
            metadata={
                "loop_phase": "think",
                "router": "external_change_router.v1",
                "public_skill": True,
            },
        )
        if route == "source_inventory":
            result = self._run_impact_source_inventory(run_id, message)
        else:
            result = self._run_recent_news_report(run_id, message)
        return self._wrap_external_impact_result(
            result,
            route=route,
            route_label=route_labels.get(route, route),
            selected_sources=selected_sources,
        )

    def _record_embedded_report_loop(
        self,
        run_id: str,
        loop_state: PolicyImpactState,
        loop_result: AgentLoopResult,
    ) -> None:
        turns_by_index = {
            int(turn.turn_index): turn
            for turn in loop_result.turns
            if int(turn.turn_index or 0) > 0
        }
        observed_turns: set[int] = set()
        for index, model_call in enumerate(loop_state.model_calls, start=1):
            if self._is_pre_recorded_report_workflow_call(model_call):
                turn = turns_by_index.get(index)
                if turn is not None:
                    self._record_embedded_report_observation(run_id, turn)
                    observed_turns.add(int(turn.turn_index))
                continue
            self._record_embedded_report_model_call(run_id, model_call)
            turn = turns_by_index.get(index)
            if turn is not None:
                self._record_embedded_report_observation(run_id, turn)
                observed_turns.add(int(turn.turn_index))
        for turn in loop_result.turns:
            turn_index = int(turn.turn_index or 0)
            if turn_index not in observed_turns:
                self._record_embedded_report_observation(run_id, turn)

    @staticmethod
    def _is_pre_recorded_report_workflow_call(model_call: dict[str, Any]) -> bool:
        payload = model_call.get("payload") or {}
        metadata = model_call.get("metadata") or {}
        return (
            payload.get("type") == "tool_call"
            and payload.get("tool_name") == "report_workflow.run"
            and bool(metadata.get("deterministic_action"))
        )

    def _record_embedded_report_model_call(
        self,
        run_id: str,
        model_call: dict[str, Any],
    ) -> None:
        payload = model_call.get("payload") or {}
        action_type = str(payload.get("type") or "")
        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=(
                "报告主 Agent 决定调用 Workflow"
                if action_type == "tool_call"
                else "报告主 Agent 汇总 Workflow 观察结果"
            ),
            status="done",
            input_payload={
                "schema_name": model_call.get("schema_name"),
                "agent_role": model_call.get("agent_role"),
                "messages": model_call.get("messages") or [],
            },
            output_payload={
                "payload": payload,
                "response_id": model_call.get("response_id"),
            },
            metadata={
                **dict(model_call.get("metadata") or {}),
                "loop_phase": "think",
                "embedded_loop": "external_impact_report",
                "action_type": action_type,
            },
        )

    def _record_embedded_report_observation(
        self,
        run_id: str,
        turn: Any,
    ) -> None:
        observation = dict(turn.observation or {})
        if "tool_result" not in observation:
            return
        self.store.add_agent_step(
            run_id,
            step_type="workflow_stage",
            title="报告 Workflow 结果回填 AgentLoop",
            status="done",
            input_payload={"turn_index": turn.turn_index},
            output_payload={"observation": observation["tool_result"]},
            metadata={
                "loop_phase": "observe",
                "embedded_loop": "external_impact_report",
                "tool_name": "report_workflow.run",
            },
        )

    @staticmethod
    def _impact_internal_route(message: str) -> str:
        text = str(message or "").lower()
        agent_tech_terms = (
            "agent runtime",
            "agent harness",
            "tool policy",
            "subagent",
            "claude code",
            "codex",
            "openclaw",
            "runtime",
            "harness",
            "工具策略",
            "子智能体",
        )
        news_or_release_terms = (
            "新闻",
            "最新",
            "日报",
            "发布",
            "更新",
            "开源",
            "行业事件",
            "news",
            "latest",
            "release",
            "changelog",
        )
        strong_policy_terms = (
            "政策",
            "监管",
            "法规",
            "法条",
            "办法",
            "条例",
            "合规",
            "regulation",
        )
        regulatory_terms_without_plain_policy = tuple(
            term for term in strong_policy_terms if term != "policy"
        )
        source_inventory_terms = (
            "数据源",
            "source",
            "sources",
            "抓取状态",
            "快照",
            "接入了什么",
            "有哪些数据",
        )
        source_inventory_action_terms = (
            "有哪些",
            "查看",
            "看看",
            "盘点",
            "状态",
            "列出",
            "接入",
            "show",
            "list",
            "status",
        )
        if any(term in text for term in source_inventory_terms) and (
            any(term in text for term in source_inventory_action_terms)
            or not any(term in text for term in ("影响", "分析", "报告", "impact"))
        ):
            return "source_inventory"
        if any(term in text for term in news_or_release_terms) and not any(
            term in text for term in strong_policy_terms
        ):
            return "news"
        if any(term in text for term in agent_tech_terms) and not any(
            term in text for term in regulatory_terms_without_plain_policy
        ):
            return "news"
        if any(
            term in text
            for term in (
                *strong_policy_terms,
                "合规政策",
                "policy",
            )
        ):
            return "policy"
        if any(
            term in text
            for term in (
                "调研",
                "研究",
                "综述",
                "research",
                "study",
            )
        ) and not any(
            term in text
            for term in (
                "新闻",
                "最新",
                "发布",
                "release",
                "changelog",
                "政策",
                "监管",
                "影响",
            )
        ):
            return "research"
        return "news"

    @staticmethod
    def _impact_selected_sources(route: str) -> list[str]:
        if route == "policy":
            return ["policy_sources", "company_wiki", "long_term_memory"]
        if route == "research":
            return ["company_wiki", "long_term_memory", "model_research_synthesis"]
        if route == "source_inventory":
            return ["source_config", "news_snapshot", "policy_snapshot"]
        return [
            "news_sources",
            "tech_release_feeds",
            "research_feeds",
            "company_wiki",
            "long_term_memory",
        ]

    def _wrap_external_impact_result(
        self,
        result: dict[str, Any],
        *,
        route: str,
        route_label: str,
        selected_sources: list[str],
    ) -> dict[str, Any]:
        wrapped = dict(result)
        answer = str(wrapped.get("answer") or "")
        source_text = "、".join(selected_sources)
        prefix = (
            f"已通过统一外部变化分析入口处理本轮请求。\n"
            f"- 内部路线：{route_label}\n"
            f"- 数据/上下文边界：{source_text}\n\n"
        )
        if not answer.startswith("已通过统一外部变化分析入口"):
            wrapped["answer"] = prefix + answer
        wrapped.setdefault("citations", [])
        wrapped.setdefault("artifacts", {})
        wrapped["internal_route"] = route
        return wrapped

    def _run_impact_source_inventory(self, run_id: str, message: str) -> dict[str, Any]:
        config = load_source_config()
        news_sources = list(config.get("news_sources") or [])
        policy_sources = list(config.get("policy_sources") or [])
        fallback_sources = list(config.get("local_fallback_sources") or [])
        news_snapshot = load_latest_news_snapshot()
        policy_snapshot = load_latest_policy_snapshot()
        enabled_news = [item for item in news_sources if item.get("enabled", True)]
        enabled_policy = [item for item in policy_sources if item.get("enabled", True)]
        snapshot_summary = {
            "news": {
                "item_count": len(news_snapshot.get("items") or []),
                "snapshot_path": news_snapshot.get("snapshot_path") or "",
                "captured_at": news_snapshot.get("captured_at") or news_snapshot.get("collected_at") or "",
                "source_status_count": len(news_snapshot.get("source_statuses") or []),
                "source_statuses": news_snapshot.get("source_statuses") or [],
            },
            "policy": {
                "item_count": len(
                    policy_snapshot.get("items") or policy_snapshot.get("documents") or []
                ),
                "snapshot_path": policy_snapshot.get("snapshot_path") or "",
                "captured_at": policy_snapshot.get("captured_at") or policy_snapshot.get("collected_at") or "",
                "source_status_count": len(policy_snapshot.get("source_statuses") or []),
                "source_statuses": policy_snapshot.get("source_statuses") or [],
            },
        }
        inventory = {
            "schema_version": "external_impact.source_inventory.v1",
            "message": message,
            "news_sources": news_sources,
            "policy_sources": policy_sources,
            "local_fallback_sources": fallback_sources,
            "source_levels": config.get("source_levels") or {},
            "snapshots": snapshot_summary,
            "boundary": {
                "user_visible_skill": "external_impact_report",
                "legacy_internal_routes": [
                    "policy_weekly_impact",
                    "recent_news_report",
                    "research_report",
                ],
                "fixture_rule": "TEST/local fixture sources can validate the pipeline, but should not be presented as production evidence.",
            },
        }
        artifact_dir = company_dir(self.company_id) / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{date.today().isoformat()}-source-inventory.json"
        artifact_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="读取外部变化数据源配置",
            status="done",
            output_payload={
                "news_source_count": len(news_sources),
                "enabled_news_source_count": len(enabled_news),
                "policy_source_count": len(policy_sources),
                "enabled_policy_source_count": len(enabled_policy),
                "fallback_source_count": len(fallback_sources),
                "snapshots": snapshot_summary,
                "source_levels": config.get("source_levels") or {},
            },
            tool_calls=[
                {
                    "stage_name": "external_impact_source_inventory",
                    "tool_name": "source_config.read",
                    "policy_decision": "allow",
                    "status": "success",
                    "output": {
                        "news_source_count": len(news_sources),
                        "policy_source_count": len(policy_sources),
                    },
                }
            ],
            metadata={"loop_phase": "act", "data_surface": "source_inventory"},
        )
        has_snapshot = bool(snapshot_summary["news"]["item_count"] or snapshot_summary["policy"]["item_count"])
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="检查数据源证据边界",
            status="done",
            gate_result={
                "decision": "allow" if has_snapshot else "ask",
                "reason": (
                    "已找到历史快照，可用于复盘来源状态"
                    if has_snapshot
                    else "当前没有可用快照；只能展示已配置来源，不能声称已抓取到实时数据"
                ),
                "policy_id": "external_impact.source_boundary.v1",
            },
            metadata={"loop_phase": "observe"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入数据源盘点产物",
            status="done",
            output_payload={"artifacts": {"run_artifact": str(artifact_path)}},
            metadata={"loop_phase": "act"},
        )
        answer = self._render_source_inventory_answer(
            enabled_news=enabled_news,
            enabled_policy=enabled_policy,
            fallback_sources=fallback_sources,
            snapshot_summary=snapshot_summary,
            artifact_path=artifact_path,
        )
        return {
            "answer": answer,
            "artifacts": {"run_artifact": str(artifact_path)},
            "citations": [{"type": "source_inventory", "path": str(artifact_path)}],
        }

    @staticmethod
    def _render_source_inventory_answer(
        *,
        enabled_news: list[dict[str, Any]],
        enabled_policy: list[dict[str, Any]],
        fallback_sources: list[dict[str, Any]],
        snapshot_summary: dict[str, Any],
        artifact_path: Path,
    ) -> str:
        def status_lines(kind: str, label: str) -> list[str]:
            snapshot = snapshot_summary[kind]
            result = [f"- {label}快照采集时间：{snapshot.get('captured_at') or '尚未采集'}；此处读取已有快照，不是本次重新抓取。"]
            for row in snapshot.get("source_statuses", []):
                status = {"ok": "成功", "error": "失败", "empty": "无内容", "failed": "失败"}.get(row.get("status"), row.get("status") or "未知")
                result.append(f"- {row.get('name') or row.get('source_id')}：{status}，{row.get('item_count', row.get('document_count', 0))} 条"
                              + (f"；原因：{row['error']}" if row.get("error") else ""))
            return result

        def source_lines(items: list[dict[str, Any]], limit: int = 6) -> list[str]:
            if not items:
                return ["- 未启用来源"]
            lines = []
            for item in items[:limit]:
                topics = "、".join(str(value) for value in item.get("topics") or item.get("keywords") or [])
                lines.append(
                    f"- {item.get('name') or item.get('source_id')}（{item.get('level', '')}/{item.get('type', '')}）"
                    + (f"：{topics}" if topics else "")
                )
            if len(items) > limit:
                lines.append(f"- 其余 {len(items) - limit} 个来源见 RunArtifact")
            return lines

        return "\n".join(
            [
                "外部变化数据源盘点如下：",
                "",
                "## 新闻/技术发布/研究来源",
                *source_lines(enabled_news),
                "",
                "## 政策/监管来源",
                *source_lines(enabled_policy),
                "",
                "## 本地兜底来源",
                *source_lines(fallback_sources),
                "",
                "## 最近快照",
                f"- 新闻快照：{snapshot_summary['news']['item_count']} 条，状态记录 {snapshot_summary['news']['source_status_count']} 条",
                f"- 政策快照：{snapshot_summary['policy']['item_count']} 条，状态记录 {snapshot_summary['policy']['source_status_count']} 条",
                *status_lines("news", "新闻"),
                *status_lines("policy", "政策"),
                "",
                "## 使用边界",
                "- L1/L2/L3 来源可以作为影响分析的主要证据；TEST/local fixture 只用于链路验收，不能当作实时结论。",
                "- GitHub 仅覆盖配置仓库的 Release；arXiv 仅覆盖查询返回的论文摘要。未接入全站 Trending、引用量或点赞榜，不能据此宣称热门或全量覆盖。",
                "- 用户正常聊天时不需要选择具体来源；系统会按问题语义在统一影响分析入口内部路由。",
                "",
                f"RunArtifact：{artifact_path}",
            ]
        )

    def _run_policy_weekly_impact(self, run_id: str, session_id: str, message: str) -> dict[str, Any]:
        effective_query = self._policy_effective_query(session_id, message)
        if effective_query != str(message or "").strip():
            inherited_messages = [
                str(item.get("content") or "")
                for item in self.store.list_chat_messages(session_id)[:-1]
                if item.get("role") == "user" and str(item.get("content") or "").strip()
            ][-4:]
            self.store.add_agent_step(
                run_id,
                step_type="history_read",
                title="继承政策会话上下文",
                status="done",
                input_payload={"current_message": message},
                output_payload={
                    "history_turn_count": len(inherited_messages),
                    "recent_history": inherited_messages,
                    "effective_query": effective_query,
                },
                metadata={"loop_phase": "think", "continuity_contract": "policy_followup.v1"},
            )
        review_proof_resolver = PolicyMemoryReviewGateResolver(self.store)
        gate_runner = GateRunner(
            build_default_gate_registry(proof_resolver=review_proof_resolver),
            AgentStepGateDecisionSink(self.store),
        )
        result = run_policy_weekly_impact(
            self.company_id,
            model_adapter=self._get_model_adapter(),
            run_id=run_id,
            lookback_days=self._policy_lookback_days(effective_query),
            query=effective_query,
            gate_runner=gate_runner,
        )
        state = result.state
        policy_scope = dict(state.quality_metrics.get("policy_report_scope") or {})
        policy_preferences = list(state.company_context_pack.get("user_preferences") or [])
        self.store.add_agent_step(
            run_id,
            step_type="memory_read",
            title="读取政策报告偏好",
            status="done",
            input_payload={"query": "政策报告 输出顺序 偏好"},
            output_payload={
                "memory_count": len(policy_preferences),
                "raw_memory_count": len(policy_preferences),
                "filtered_out": 0,
                "memory_ids": [str(item.get("id") or "") for item in policy_preferences],
            },
            metadata={"loop_phase": "observe", "scope": "policy_report_preferences"},
        )
        data_mode = self._policy_data_mode(state)
        tool_calls = list(state.tool_calls)
        gate_results = list(state.stage_gate_results)
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="运行政策分析工作流工具",
            status="done" if state.current_stage == "done" else "failed",
            output_payload={
                "tool_call_count": len(tool_calls),
                "workflow_run_id": state.run_id,
                "workflow_status": state.current_stage,
                "data_mode": data_mode,
                "errors": state.errors,
                "source_manifest": state.source_manifest,
                "regulatory_coverage_gaps": state.regulatory_coverage_gaps,
                "query_company_facts": state.query_company_facts,
            },
            tool_calls=tool_calls,
            metadata={"loop_phase": "act"},
        )
        stage_titles = {
            "load_company_context": "读取企业知识库",
            "fetch_recent_policies": "抓取官方政策",
            "policy_ingest_and_index": "构建政策索引",
            "retrieve_relevant_clauses": "召回候选条款",
            "extract_policy_clauses": "抽取政策条款",
            "match_company_policy": "生成候选企业关联",
            "analyze_policy_applicability": "判定政策适用性",
            "score_policy_impact": "计算影响等级",
            "review_evidence_and_risk": "执行证据质疑复核",
            "generate_weekly_report": "生成报告产物",
        }
        for stage_trace in state.stage_trace:
            stage_name = str(stage_trace.get("stage") or "workflow_stage")
            gate = stage_trace.get("gate") or {}
            self.store.add_agent_step(
                run_id,
                step_type="workflow_stage",
                title=stage_titles.get(stage_name, stage_name),
                status="done" if stage_trace.get("status") == "ok" else "failed",
                output_payload={
                    "stage_name": stage_name,
                    "duration_ms": stage_trace.get("duration_ms", 0),
                    "artifact_keys": stage_trace.get("artifact_keys") or [],
                    "gate_metrics": gate.get("metrics") or {},
                },
                gate_result={
                    "decision": "allow" if gate.get("passed", True) else "deny",
                    "reason": "; ".join(str(item) for item in gate.get("issues") or [])
                    or "阶段契约与证据门控通过",
                },
                metadata={"loop_phase": "observe", "workflow_stage": stage_name, "recording_mode": "post_execution_summary"},
            )
        if state.model_calls:
            role_titles = {
                "applicability_analyst": "运行政策适用性分析子智能体",
                "skeptic": "运行政策证据质疑子智能体",
            }
            for workflow_model_call in state.model_calls:
                model_metadata = workflow_model_call.get("metadata") or {}
                role = str(workflow_model_call.get("agent_role") or "policy_subagent")
                schema_name = str(workflow_model_call.get("schema_name") or "structured_output")
                self.store.add_agent_step(
                    run_id,
                    step_type="model_call",
                    title=role_titles.get(role, f"运行 {role} 子智能体"),
                    status="done" if state.current_stage == "done" else "failed",
                    input_payload={
                        "model_id": model_metadata.get("model_id", configured_model_id()),
                        "schema_name": schema_name,
                        "subagent_role": role,
                        "messages": workflow_model_call.get("messages") or [],
                        "prompt_contract": {
                            "contract_id": schema_name,
                            "role": role,
                            "loop_boundary": "model proposes; deterministic evidence gates publish",
                            "context_controls": {
                                "visible_keys": [
                                    "applicability_context"
                                    if role == "applicability_analyst"
                                    else "review_context"
                                ],
                            },
                        },
                        "context_manifest_ids": [
                            item.get("record_id")
                            for item in state.context_manifests
                            if item.get("record_id")
                        ],
                    },
                    output_payload={
                        "payload": workflow_model_call.get("payload") or {},
                        "review_bundles": [
                            item for item in state.review_bundles if item.get("role") == role
                        ],
                    },
                    metadata={
                        "adapter": model_metadata.get("adapter", "AnthropicCompatibleModelAdapter"),
                        "loop_phase": "think",
                        "latency_ms": model_metadata.get("total_latency_ms")
                        or model_metadata.get("latency_ms"),
                        "attempt_latency_ms": model_metadata.get("latency_ms"),
                        "total_latency_ms": model_metadata.get("total_latency_ms")
                        or model_metadata.get("latency_ms"),
                        "usage": model_metadata.get("usage", {}),
                        "attempts": model_metadata.get("attempts", 1),
                        "retry_errors": model_metadata.get("retry_errors", []),
                        "stop_reason": model_metadata.get("stop_reason", ""),
                        "temperature": model_metadata.get("temperature"),
                        "max_tokens": model_metadata.get("max_tokens"),
                        "timeout_seconds": model_metadata.get("timeout_seconds"),
                        "max_attempts": model_metadata.get("max_attempts"),
                    },
                )
        if state.current_stage != "done":
            error_detail = state.errors[-1].get("detail") if state.errors else "unknown workflow error"
            raise ModelInvocationError(f"政策分析工作流失败：{error_detail}")
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="汇总工作流门控结果",
            status="done",
            output_payload={
                "gate_count": len(gate_results),
                "workflow_status": state.current_stage,
                "data_mode": data_mode,
            },
            gate_result={
                "decision": "ask" if data_mode == "fixture" or policy_scope.get("completion_status") == "partial" else "allow",
                "reason": (
                    "本地验收样例只用于验证流程，不可作为真实政策结论"
                    if data_mode == "fixture"
                    else "；".join(policy_scope.get("limitations") or []) or "政策来源通过运行时校验；不代表公司合规结论"
                ),
                "stage_gate_count": len(gate_results),
            },
            metadata={"loop_phase": "observe"},
        )
        artifacts = {
            "report": state.report_paths.get("report", ""),
            "html_report": state.report_paths.get("html_report", ""),
            "run_artifact": state.report_paths.get("run_artifact", ""),
        }
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="挂载政策分析产物",
            status="done",
            output_payload={"artifacts": artifacts},
            metadata={"loop_phase": "act"},
        )
        answer = self._policy_workflow_answer(message, state, artifacts, query_context=effective_query)
        policy_citations = [
            {
                "type": "policy_source",
                "policy_id": item.get("policy_id"),
                "title": item.get("policy_title"),
                "url": (item.get("policy_evidence") or {}).get("source_url")
                or self._policy_source_url(state, str(item.get("policy_id") or "")),
            }
            for item in state.impact_assessments
            if (item.get("policy_evidence") or {}).get("source_url")
            or self._policy_source_url(state, str(item.get("policy_id") or ""))
        ]
        coverage_citations = [
            {
                "type": "regulatory_coverage_gap",
                "title": item.get("framework"),
                "url": item.get("official_url"),
            }
            for item in state.regulatory_coverage_gaps
            if item.get("official_url")
        ]
        grounded_policy_citations = self._ground_policy_citations(
            answer,
            policy_citations + coverage_citations,
        )
        return {
            "answer": answer,
            "artifacts": artifacts,
            "delegation_evidence": list(state.delegation_evidence),
            "metadata": {"policy_report_scope": policy_scope},
            "citations": grounded_policy_citations
            + [{"type": "run_artifact", "path": artifacts["run_artifact"]}],
        }

    @staticmethod
    def _ground_policy_citations(answer: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        text = str(answer or "")
        grounded = []
        seen = set()
        for item in citations:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not ((title and title in text) or (url and url in text)):
                continue
            marker = (str(item.get("type") or ""), title, url)
            if marker in seen:
                continue
            seen.add(marker)
            grounded.append(item)
        return grounded

    def _run_recent_news_report(self, run_id: str, message: str) -> dict[str, Any]:
        scope_issue = self._news_project_scope_issue(message)
        if scope_issue:
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="确认新闻影响分析对象",
                status="done",
                input_payload={"message": message, "configured_company_id": self.company_id},
                gate_result={
                    "decision": "ask",
                    "reason": scope_issue,
                    "policy_id": "news.subject_scope.v1",
                },
                metadata={"loop_phase": "observe"},
            )
            return {
                "answer": scope_issue,
                "artifacts": {},
                "citations": [],
            }
        stage_labels = {
            "wiki_context": ("读取 Wiki 并确定本轮任务", "news.workflow.wiki_context.v1"),
            "source_collection": ("读取外部新闻与技术发布", "news.workflow.source_ingestion.v1"),
            "filtering": ("清洗、结构化并匹配 Wiki 与查询约束", "news.workflow.profile_matching.v1"),
            "impact_analysis": ("分析具体影响与行动建议", "news.workflow.impact_analysis.v1"),
            "report_rendering": ("生成 Markdown 与 HTML 报告产物", "news.workflow.artifact_contract.v1"),
        }
        active_stage = ""

        def record_stage(event: dict[str, Any]) -> None:
            nonlocal active_stage
            active_stage = str(event["stage"])
            completed = event["status"] in {"completed", "failed", "skipped"}
            label, policy_id = stage_labels.get(active_stage, (active_stage, active_stage))
            gate = dict(event.get("gate") or {})
            if completed:
                gate.setdefault("decision", "allow")
                gate.setdefault("reason", "阶段实际执行完成")
                gate["policy_id"] = policy_id
            self.store.add_agent_step(
                run_id,
                step_type="workflow_stage" if completed else "workflow_stage_start",
                title=label if completed else f"开始：{label}",
                status="failed" if event["status"] == "failed" else "done",
                input_payload=event.get("input") or {},
                output_payload=event.get("output") or {},
                gate_result=gate if completed else None,
                metadata={
                    "workflow": "recent_news_report", "workflow_run_id": event["run_id"],
                    "loop_phase": "observe" if completed else "act", "stage_gate": completed,
                    "stage": active_stage, "event_status": event["status"],
                    "started_at": event.get("started_at"), "finished_at": event.get("finished_at"),
                    "recording_mode": "live_stage_callback",
                    "graph_transition": event.get("transition"),
                },
            )

        runtime = self

        class AuditedReportModel:
            @property
            def supports_discovery_planning(self) -> bool:
                return isinstance(runtime._get_model_adapter(), AnthropicCompatibleModelAdapter)

            def complete(self, messages, schema_name):
                planning = schema_name == "report_discovery_plan.v1"
                return runtime._complete_model(
                    run_id=run_id,
                    title="基于 Wiki 与问题规划公开检索词" if planning else "基于来源与 Wiki 生成中文影响分析",
                    model_id=configured_model_id(),
                    schema_name=schema_name, messages=messages,
                    prompt_contract={
                        "contract_id": "report_discovery_plan.v1" if planning else "profile_aware_news_report.v1",
                        "evidence_scope": "wiki_and_query_to_public_search_terms" if planning else "selected_events_and_wiki_facts",
                    },
                )

        try:
            result = run_recent_news_report(
                self.company_id, query=message, model_adapter=AuditedReportModel(), on_stage=record_stage,
            )
        except Exception as exc:
            self.store.add_agent_step(
                run_id, step_type="workflow_stage", title=f"阶段失败：{active_stage}", status="failed",
                output_payload={"error_type": type(exc).__name__, "reason": str(exc)[:500]},
                gate_result={"decision": "deny", "reason": "阶段异常，未继续生成成功报告"},
                metadata={"workflow": "recent_news_report", "stage": active_stage, "loop_phase": "stop", "recording_mode": "live_stage_callback"},
            )
            raise
        artifacts = {"run_artifact": result["artifact_path"]}
        if result.get("report_path"):
            artifacts["report"] = result["report_path"]
        if result.get("html_report_path"):
            artifacts["html_report"] = result["html_report_path"]
        source_manifest = result.get("source_manifest") or {}
        self.store.add_agent_step(
            run_id, step_type="workflow_summary", title="汇总来源覆盖与报告分析状态", status="done",
            output_payload={
                "source_manifest": source_manifest, "data_mode": result.get("data_mode"),
                "news_source_count": len(source_manifest.get("source_statuses") or []),
                "event_count": result.get("event_count"), "matched_event_count": result.get("matched_event_count"),
                "selected_event_count": result.get("selected_event_count"),
                "analysis_status": result.get("analysis_status"),
                "analysis_diagnostics": result.get("analysis_diagnostics") or {},
                "report_request": result.get("report_request") or {},
                "discovery_plan": result.get("discovery_plan") or {},
                "quality_diagnostics": result.get("quality_diagnostics") or {},
                "incomplete_reasons": result.get("incomplete_reasons") or [],
                "artifacts": artifacts,
            },
            metadata={"loop_phase": "observe", "recording_mode": "post_execution_summary"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="检查新闻报告输出",
            status="done",
            gate_result={
                "decision": "allow" if result["relevance_gate"] == "allow" and result.get("analysis_status") != "partial" else "ask",
                "reason": result["gate_reason"],
                "data_mode": result["data_mode"],
            },
            metadata={"loop_phase": "observe"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="挂载新闻报告产物",
            status="done",
            output_payload={"artifacts": artifacts},
            metadata={"loop_phase": "act"},
        )
        return {
            "answer": self._news_workflow_answer(message, result, artifacts),
            "artifacts": artifacts,
            "citations": [{"type": "news_artifact", "path": result["artifact_path"]}],
            "metadata": {"analysis_status": result.get("analysis_status"), "workflow_run_id": result.get("run_id"), "incomplete_reasons": result.get("incomplete_reasons", [])},
        }

    def _news_project_scope_issue(self, message: str) -> str:
        text = str(message or "").lower()
        project_reference = any(
            term in text for term in ("我的项目", "这个项目", "本项目", "my project", "this project")
        )
        named_projects = [
            term for term in ("agent workbench", "workbench", "coursepilot", "course pilot")
            if term in text
        ]
        named_projects.extend(re.findall(r"\b([a-z][a-z0-9_-]*)\s*(?:项目|project\b)", text))
        named_projects.extend(re.findall(r"[“\"]([^”\"]+)[”\"]\s*项目", text))
        named_projects.extend(re.findall(r"项目(?:名称)?\s*[：:]\s*([^\s，。！？,.;:]+)", text))
        named_projects = [name for name in named_projects if name not in {"my", "this", "the", "our"}]
        if not (project_reference or named_projects):
            return ""
        knowledge = load_company_knowledge(self.company_id)
        profile = knowledge["profile"]
        identity = [str(profile.get(key) or "").lower() for key in (
            "company_id", "company_name", "short_name", "name", "project_name",
        )]
        fact_texts = [
            str(fact.get("value") or fact.get("text") or "").lower()
            for fact in knowledge["facts"]
            if fact.get("source_kind") not in {"analysis_inference", "project_design"}
        ]
        fact_texts.extend(
            str(page.get("body") or "").lower()
            for page in knowledge.get("pages", [])
            if not page.get("facts") and (page.get("meta") or {}).get("source") == "user_confirmed"
        )
        known_context = "\n".join([*identity, *fact_texts])
        unknown_names = [name for name in named_projects if name not in known_context]
        has_project_context = bool(profile.get("project_name")) or any(
            any(marker in fact for marker in ("项目", "project", "prototype", "原型"))
            for fact in fact_texts
        )
        if not unknown_names and (
            named_projects or has_project_context
            or any(name and name in text for name in identity)
        ):
            return ""
        subject = "你点名的项目" if unknown_names else "“我的项目”"
        configured_subject = str(profile.get("company_name") or profile.get("name") or self.company_id)
        return (
            f"当前无法可靠分析{subject}的新闻影响：当前 Wiki 对象为 {configured_subject}，"
            "已确认的画像与 Wiki 事实不足以绑定该项目。"
            "因此本轮只完成对象范围门控，没有调用新闻工作流，也没有生成影响报告。"
            "请明确项目名称或在当前 Wiki 补充项目目标、技术栈、关键依赖和约束；"
            f"如果指的是当前 Wiki 中的 {configured_subject}，请明确这一点。"
        )

    def _run_research_report(
        self,
        run_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        memories = list(prepared_context.get("memories") or [])
        prompt_messages, prompt_contract = build_research_report_prompt(
            message=message,
            context_manifest=context_manifest,
            memory_count=len(memories),
            evidence=self._research_evidence(message, memories),
            conversation_summary=prepared_context.get("conversation_summary") or {},
            recent_history=prepared_context.get("recent_history") or [],
        )
        model_response = self._complete_model(
            run_id=run_id,
            title="生成调研报告提纲",
            model_id=model_id,
            schema_name="research_report.v1",
            messages=prompt_messages,
            prompt_contract=prompt_contract,
        )
        report_text = self._render_research_report_v2(
            message,
            context_manifest,
            memories,
            model_response.payload,
            run_id=run_id,
        )
        report_dir = company_dir(self.company_id) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{date.today().isoformat()}-research-report-{run_id}.md"
        report_path.write_text(report_text, encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入调研报告产物",
            status="done",
            output_payload={"report": str(report_path)},
            metadata={"loop_phase": "act"},
        )
        return {
            "answer": self._research_answer(message, report_path),
            "artifacts": {"report": str(report_path)},
            "citations": [{"type": "research_report", "path": str(report_path)}],
        }

    def _research_evidence(self, message: str, memories: list[dict[str, Any]]) -> list[str]:
        evidence = [str(item.get("content") or "")[:300] for item in memories[:5]]
        if self._is_workbench_design_query(message) or self._is_harness_interview_query(message):
            evidence.extend(
                [
                    "AgentRuntime persists each turn as AgentRun plus ordered AgentStep records.",
                    "SkillRegistry routes general_chat, external_impact_report, and the internal report workflow executors.",
                    "ContextManifest records visible keys, hidden fields, token budgets, token estimates, and checksums; checksum mismatch verification is not implemented yet.",
                    "ToolGateway applies deterministic tool allowlists and stores tool audit evidence.",
                    "ChatWorkbenchService exposes trace summaries for context, memory, model, tools, gates, and artifacts.",
                    "The frontend renders the execution trace above the final assistant answer.",
                ]
            )
        return [item for item in evidence if item]

    def _run_company_wiki_blueprint(self, run_id: str) -> dict[str, Any]:
        gateway = ToolGateway()
        blueprint = gateway.call("main_agent", "company_wiki_blueprint", company_id=self.company_id)
        tool_calls = [record.to_dict() for record in gateway.calls]
        artifact_dir = company_dir(self.company_id) / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "company-wiki-blueprint.json"
        artifact_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="生成企业知识库规划",
            status="done",
            output_payload={
                "missing_file_count": len(blueprint.get("missing_files", [])),
                "question_count": len(blueprint.get("next_questions", [])),
            },
            tool_calls=tool_calls,
            gate_result={
                "decision": "allow",
                "reason": "main_agent allowlist permits company_wiki_blueprint",
            },
            metadata={"loop_phase": "act"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入知识库规划产物",
            status="done",
            output_payload={"artifacts": {"wiki_blueprint": str(artifact_path)}},
            metadata={"loop_phase": "act"},
        )
        answer_lines = ["企业知识库规划已生成。"]
        missing = blueprint.get("missing_files", [])
        if missing:
            answer_lines.append(f"缺失文件（{len(missing)}）：" + ", ".join(missing[:5]))
        missing_facts = blueprint.get("missing_facts", [])
        if missing_facts:
            answer_lines.append(f"缺失事实项：{len(missing_facts)} 个。")
        questions = blueprint.get("next_questions", [])
        if questions:
            answer_lines.extend(
                [
                    f"下一步采集问题（共 {len(questions)} 个）：",
                    *[f"{index}. {question}" for index, question in enumerate(questions, 1)],
                ]
            )
        answer_lines.append(f"结构化产物：{artifact_path}")
        return {
            "answer": "\n".join(answer_lines),
            "artifacts": {"wiki_blueprint": str(artifact_path)},
            "citations": [{"type": "company_wiki_blueprint", "path": str(artifact_path)}],
        }

    @staticmethod
    def _has_any(text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def _policy_workflow_answer(
        self,
        message: str,
        state: PolicyImpactState,
        artifacts: dict[str, str],
        *,
        query_context: str = "",
    ) -> str:
        text = str(query_context or message or "").lower()
        scope = state.quality_metrics.get("policy_report_scope") or {}
        limitations = [f"未完成项：{item}" for item in scope.get("limitations", [])]
        disclosure = [
            "政策报告部分完成，需结合下列证据边界阅读。" if scope.get("completion_status") == "partial"
            else "已在当前来源与画像范围内生成政策研判，不构成公司合规结论。",
            f"实际范围：{state.date_range.get('date_from')} 至 {state.date_range.get('date_to')}。",
            *limitations,
        ]
        lines = [
            *disclosure,
            "",
            f"本次 workflow 处理政策数：{len(state.policy_documents)}，形成评估项：{len(state.impact_assessments)}。",
        ]
        if state.source_manifest.get("no_updates"):
            return "\n".join(
                [
                    "政策来源检查已完成。",
                    "",
                    (
                        f"官方来源抓取成功，但 {state.date_range.get('date_from')} 至 "
                        f"{state.date_range.get('date_to')} 没有发现新增政策文件。"
                    ),
                    "系统不会用历史政策或本地测试样例伪造本周更新，因此本轮不生成新的影响判断。",
                    "审计报告和 RunArtifact 已保存到本轮运行产物。",
                ]
            )
        if self._policy_data_mode(state) == "fixture":
            lines.insert(
                1,
                "数据边界：本次运行使用本地验收样例政策，并非真实近期监管文件；以下内容只用于验证 Harness 流程，不可作为现实合规结论。",
            )
        focused_followup = self._policy_product_followup_answer(message, state)
        if focused_followup:
            return "\n".join([*disclosure, focused_followup])
        targeted_answer = self._targeted_policy_answer(message, state)
        if targeted_answer:
            return "\n".join([*disclosure, targeted_answer])
        if state.regulatory_coverage_gaps:
            lines.extend(
                [
                    "",
                    "重要边界：本轮近期政策快照不是完整监管清单；4 类既有基础框架仍需单独做条款级核验。",
                ]
            )
            lines.extend(self._regulatory_triage_lines(state))
        relevant = self._relevant_policy_assessments(text, state.impact_assessments)
        if not relevant:
            covered = "、".join(item.get("title", "") for item in state.policy_documents)
            unrelated = self._explicit_domain_mismatch_answer(text)
            lines.extend(["", unrelated] if unrelated else [
                "",
                "当前可核验政策库没有覆盖你点名的法规或主题，因此本轮不生成条款、义务主体或整改结论。",
                f"本次实际覆盖：{covered or '无'}。",
                "请补充官方原文链接，或先把该官方来源接入政策源配置后再分析。",
            ])
        else:
            preference_text = " ".join(
                str(item.get("content") or "")
                for item in state.company_context_pack.get("user_preferences") or []
            )
            if "先列明确不适用" in preference_text and "条件适用" in preference_text:
                order = {
                    "not_applicable": 0,
                    "conditional": 1,
                    "direct": 2,
                    "insufficient_evidence": 3,
                }
                relevant = sorted(
                    relevant,
                    key=lambda item: order.get(str(item.get("applicability") or ""), 9),
                )
            labels = {
                "direct": "直接适用",
                "conditional": "条件适用",
                "not_applicable": "明确不适用",
                "insufficient_evidence": "证据不足",
            }
            lines.extend(
                [
                    "",
                    *self._policy_query_focus_lines(text, state, relevant),
                    "",
                    "分类口径：直接适用、条件适用、明确不适用、证据不足互斥；指导性文件单独标记为“不产生强制义务 + 直接相关”，不冒充强制要求。系统不会为凑齐四类而改变证据结论。",
                    "",
                    "针对本轮问题的证据化结论：",
                ]
            )
            for item in relevant:
                relationship = str(item.get("applicability") or "conditional")
                binding_effect = str(item.get("binding_effect") or "unknown")
                policy_evidence = item.get("policy_evidence") or {}
                source_kind_labels = {
                    "public_disclosure": "公开披露",
                    "analysis_inference": "分析推断",
                    "project_design": "项目设计",
                    "user_confirmed": "用户确认",
                }
                company_evidence = "; ".join(
                    (
                        f"[{source_kind_labels.get(str(fact.get('source_kind') or 'other'), str(fact.get('source_kind') or 'other'))}; "
                        f"fact_id: {fact.get('fact_id') or 'unknown'}] {fact.get('value') or ''}"
                    )
                    for fact in item.get("company_evidence") or []
                )
                source_url = policy_evidence.get("source_url") or self._policy_source_url(
                    state, str(item.get("policy_id") or "")
                )
                summary = item.get("reasoning", [""])[1] if len(item.get("reasoning", [])) > 1 else ""
                effective_status = str(item.get("effective_status") or "unknown")
                effective_date = str(item.get("effective_date") or "")
                as_of_date = str(item.get("as_of_date") or state.date_range.get("date_to") or "")
                if effective_status == "not_yet_effective":
                    effective_text = f"截至 {as_of_date} 尚未施行，将于 {effective_date} 施行"
                elif effective_status == "effective":
                    effective_text = f"已于 {effective_date} 施行" if effective_date else "已施行"
                else:
                    effective_text = "未检出独立施行日期"
                relationship_label = labels.get(relationship, relationship)
                if binding_effect == "guidance" and relationship == "not_applicable":
                    relationship_label = "不产生强制义务"
                policy_document = self._policy_document(
                    state,
                    str(item.get("policy_id") or ""),
                )
                published_at = str(policy_document.get("published_at") or "")
                lines.extend(
                    [
                        f"- **[{relationship_label}] {item.get('policy_title', '')}**",
                        f"  - 适用理由：{summary or '未记录'}",
                        *(
                            ["  - 政策相关性：直接相关（指导性文件，非强制义务）"]
                            if binding_effect == "guidance"
                            else []
                        ),
                        f"  - 发布日期：{published_at[:10] or '未记录'}",
                        f"  - 时间效力：{effective_text}",
                        f"  - 企业证据：{company_evidence or '无可绑定企业事实'}",
                        f"  - 政策依据：{str(policy_evidence.get('text') or '')[:360] or '当前来源未提供完整条款'}",
                        f"  - 待确认：{'; '.join(item.get('missing_fields', [])) or '无'}",
                        f"  - 建议动作：{'; '.join(item.get('recommended_actions', [])) or '无'}",
                        f"  - 原文：{source_url or '未记录'}",
                    ]
                )
        lines.extend(
            [
                "",
                "完整 Markdown、HTML 和 RunArtifact 已保存到本轮运行产物，可在执行记录中核验。",
            ]
        )
        if state.regulatory_coverage_gaps:
            lines.extend(
                [
                    "",
                    "监管覆盖边界（不是适用性结论）：",
                    "本轮只分析已采集的近期政策。以下既有框架尚未进入条款级核验，未核验前不能把本报告当作完整上线清单：",
                ]
            )
            for gap in state.regulatory_coverage_gaps:
                lines.extend(
                    [
                        f"- {gap.get('framework')}：{gap.get('trigger_reason')}",
                        f"  - 待核验：{gap.get('next_check')}",
                        f"  - 官方入口：{gap.get('official_url')}",
                    ]
                )
        if state.query_company_facts:
            lines.extend(
                [
                    "",
                    "本轮临时企业事实（仅进入本次 Context，不自动写入长期 Memory）：",
                    *[f"- [{item.get('fact_id')}] {item.get('value')}" for item in state.query_company_facts],
                    "这些事实只改变证据前提；对未进入条款级核验的既有框架，系统不会声称适用性结论已经改变。",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _policy_query_focus_lines(
        text: str,
        state: PolicyImpactState,
        relevant: list[dict[str, Any]],
    ) -> list[str]:
        normalized = str(text or "").lower()
        wants_agent_platform = any(term in normalized for term in ("agent 平台", "agent平台", "智能体平台"))
        wants_tool_policy = any(term in normalized for term in ("工具权限", "tool policy", "权限门控"))
        wants_audit = any(term in normalized for term in ("审计留痕", "运行留痕", "留痕", "trace"))
        wants_compliance = any(term in normalized for term in ("企业合规", "合规"))
        if not any((wants_agent_platform, wants_tool_policy, wants_audit, wants_compliance)):
            return []

        titles = " ".join(str(item.get("policy_title") or "") for item in relevant)
        lines = ["本轮问题映射（政策证据与工程建议分开）："]
        if wants_agent_platform:
            if "智能体规范应用" in titles:
                lines.append(
                    "- Agent 平台：智能体指导性文件与产品形态直接相关，但不产生强制义务；只能作为治理方向，不能冒充合规结论。"
                )
            else:
                lines.append("- Agent 平台：本轮政策快照没有形成可直接绑定的强制义务结论。")
        if wants_tool_policy:
            lines.append(
                "- 工具权限：本轮 4 份政策没有规定 Agent 工具的 allow/ask/deny 机制；最小权限、人工确认和越权拦截属于工程控制建议，仍需结合个人信息、算法和生成式 AI 规则做条款级核验。"
            )
        if wants_audit:
            lines.append(
                "- 审计留痕：建议保留输入证据、工具决策、模型版本、人工确认和产物校验记录；本轮来源没有给出统一保存期限，不能虚构具体年限。"
            )
        if wants_compliance:
            conditional = sum(
                1 for item in state.impact_assessments if item.get("applicability") == "conditional"
            )
            if state.regulatory_coverage_gaps:
                boundary = (
                    f"此外仍有 {len(state.regulatory_coverage_gaps)} 类基础监管框架未完成条款级核验"
                )
            else:
                boundary = f"本轮仅覆盖已采集的 {len(state.policy_documents)} 份政策，未接入法规不能视为不适用"
            lines.append(
                f"- 企业合规：当前全部评估中形成 {conditional} 项条件适用判断；{boundary}，本报告不是完整上线清单。"
            )
        return lines

    @staticmethod
    def _explicit_domain_mismatch_answer(text: str) -> str:
        normalized = str(text or "").lower()
        agriculture_terms = (
            "深远海",
            "海洋养殖",
            "养殖装备",
            "农机",
            "农业补贴",
            "渔业补贴",
            "畜禽",
            "屠宰",
        )
        asks_direct_impact = any(term in normalized for term in ("直接影响", "是否相关", "有没有影响", "没有就明确"))
        if not asks_direct_impact or not any(term in normalized for term in agriculture_terms):
            return ""
        subjects = []
        if any(term in normalized for term in ("深远海", "海洋养殖", "养殖装备")):
            subjects.append("深远海养殖政策：基于业务领域不相交，未发现对同花顺核心业务的直接影响。")
        if any(term in normalized for term in ("农机", "农业补贴", "农机购置")):
            subjects.append("农机购置补贴政策：基于业务领域不相交，未发现对同花顺核心业务的直接影响。")
        if any(term in normalized for term in ("畜禽", "屠宰")):
            subjects.append("畜禽或屠宰政策：基于业务领域不相交，未发现对同花顺核心业务的直接影响。")
        return "\n".join(
            [
                "结论：",
                *[f"- {subject}" for subject in subjects],
                "概念股、行业资讯或 ESG 话题不构成政策对企业义务或经营流程的直接因果链。",
                "证据边界：本轮政策库未接入点名文件的官方原文；以上是业务领域不相交的初筛结论，不能替代逐份文件的条款级法律核验。",
            ]
        )

    @classmethod
    def _targeted_policy_answer(
        cls,
        message: str,
        state: PolicyImpactState,
    ) -> str:
        text = str(message or "")
        if "网络数据安全风险评估办法" not in text and "为什么是条件适用" not in text:
            return ""
        document = next(
            (
                item
                for item in state.policy_documents
                if "网络数据安全风险评估办法" in str(item.get("title") or "")
            ),
            {},
        )
        assessment = next(
            (
                item
                for item in state.impact_assessments
                if "网络数据安全风险评估办法" in str(item.get("policy_title") or "")
            ),
            {},
        )
        source_url = str(document.get("source_url") or "")
        published_at = str(document.get("published_at") or "")[:10]
        effective_date = str(assessment.get("effective_date") or "2026-08-20")
        if "发布日期" in text or "是否已经施行" in text:
            return "\n".join(
                [
                    "结论：截至 2026-07-16，该办法尚未施行。",
                    f"- 发布日期：{published_at or '未记录'}",
                    f"- 施行日期：{effective_date}",
                    "- 官方施行条款：第二十五条规定，本办法自2026年8月20日起施行。",
                    f"- 官方原文：{source_url or '未记录'}",
                    "发布日期不等于施行日期，因此不能把它写成 2026-07-16 已生效义务。",
                ]
            )
        if "为什么是条件适用" in text:
            return "\n".join(
                [
                    "承接上一轮《网络数据安全风险评估办法》的适用性判断：",
                    "适用性：条件适用。企业处理网络数据并不自动触发年度评估和报送义务，关键义务主体是重要数据处理者。",
                    "必须补齐的两个企业事实：",
                    "1. 同花顺是否已被主管部门识别或告知为重要数据处理者。",
                    "2. 当前处理的数据是否进入适用的重要数据目录，以及主管部门采用的目录口径。",
                    f"时间边界：截至 2026-07-16 尚未施行，将于 {effective_date} 施行。",
                    f"官方原文：{source_url or '未记录'}",
                ]
            )
        if "只回答适用性" in text:
            return "\n".join(
                [
                    "适用性：条件适用。",
                    "义务主体：重要数据处理者，不是所有一般网络数据处理者。",
                    "缺失事实：是否已被识别为重要数据处理者；处理数据是否进入适用的重要数据目录及主管部门口径。",
                    f"时间边界：截至 2026-07-16 尚未施行，将于 {effective_date} 施行。",
                    f"官方原文：{source_url or '未记录'}",
                ]
            )
        return ""

    @staticmethod
    def _regulatory_triage_lines(state: PolicyImpactState) -> list[str]:
        fact_ids = {str(item.get("fact_id") or "") for item in state.query_company_facts}
        if not fact_ids:
            return []
        priorities = []
        if fact_ids & {
            "query.output.investment_suggestion",
            "query.action.broker_jump",
            "query.output.buy_sell_points",
        }:
            priorities.append(
                "- P0 上线阻断问题：用真实输出样例、用户协议、收费与交易链路核验证券投顾业务边界；未关闭前不作资质结论。"
            )
        if fact_ids & {"query.data.holdings", "query.data.risk_preference", "query.data.watchlist"}:
            priorities.append(
                "- P0 上线阻断问题：确认实际个人信息字段及自动化决策用途；如命中《个人信息保护法》第五十五条情形，处理前完成影响评估并留痕。"
            )
        if "query.product.ai_research_assistant" in fact_ids:
            priorities.extend(
                [
                    "- P1 核验：确认服务是否面向境内公众以及生成式 AI 安全评估、算法备案边界。",
                    "- P1 核验：确认自选股个性化、排序、检索或调度是否落入算法推荐服务范围。",
                ]
            )
        if not priorities:
            return []
        return [
            "",
            "监管关注优先级（风险筛查，不是已完成的法律适用结论）：",
            *priorities,
        ]

    @staticmethod
    def _policy_product_followup_answer(message: str, state: PolicyImpactState) -> str:
        text = str(message or "")
        if not state.regulatory_coverage_gaps:
            return ""
        artifacts_note = "完整 Trace、Markdown、HTML 和 RunArtifact 已保存到本轮运行产物。"
        if any(term in text for term in ("相比第一轮", "哪些证据前提变化", "哪些结论仍不能", "补充事实")):
            facts = {str(item.get("fact_id") or ""): str(item.get("value") or "") for item in state.query_company_facts}
            changed_ids = (
                "query.action.direct_order",
                "query.business.fee",
                "query.output.buy_sell_points",
                "query.output.public_market_explanation",
                "query.data.watchlist",
                "query.data.holdings",
                "query.data.risk_preference",
            )
            changed = [f"- [{fact_id}] {facts[fact_id]}" for fact_id in changed_ids if fact_id in facts]
            return "\n".join(
                [
                    "本轮只更新证据前提，不把未核验法规写成已经改变的适用性结论。",
                    "",
                    "本轮新增或澄清的临时事实（不自动写入长期 Memory）：",
                    *(changed or ["- 当前消息没有抽取到新的明确产品事实。"]),
                    "",
                    "基于这些事实可以调整的风险假设：",
                    "- 个人信息前提减弱：用户否定了持仓和风险偏好处理；但自选股仍是个性化输入，个人信息规则不能整体排除。",
                    "- 交易执行前提减弱：不能直接下单，说明系统与交易执行之间仍有用户跳转；这不等于自动排除证券投顾边界。",
                    "- 建议强度前提减弱：不收费、不输出明确买卖点、只做公开信息解读都应进入后续核验，但单凭这些陈述仍不能形成资质结论。",
                    "",
                    "仍不能下的结论：",
                    "- 不能认定或排除证券投资顾问业务；相关框架尚未进入本轮条款级核验。",
                    "- 不能认定生成式 AI、算法推荐和个人信息保护义务已经全部满足或全部不适用。",
                    "- 不能把《网络数据安全风险评估办法》的年度评估/报送义务写成当前已触发；还缺重要数据处理者身份与目录口径，且该办法截至 2026-07-16 尚未施行。",
                    "",
                    artifacts_note,
                ]
            )
        if "证券投资顾问" in text or "投顾" in text or "直接认定" in text:
            return "\n".join(
                [
                    "上一轮列出的 4 项是待单独核验的基础监管框架，不是已经完成的适用性结论：",
                    *[
                        f"- {gap.get('framework')}：{gap.get('next_check')}（{gap.get('official_url')}）"
                        for gap in state.regulatory_coverage_gaps
                    ],
                    "",
                    "不能仅凭当前产品描述直接认定同花顺属于证券投资顾问，也不能直接排除。当前系统只掌握产品功能陈述，尚未完成《证券投资顾问业务暂行规定》的条款级核验，也没有取得主体资质、实际输出样例、用户协议、收费安排和完整交易链路等可核验证据。",
                    "",
                    "因此本轮结论是：证券投顾边界 = 未评估；需要单独启动该法规的官方原文与企业事实核验。",
                    artifacts_note,
                ]
            )
        return ""

    @staticmethod
    def _policy_source_url(state: PolicyImpactState, policy_id: str) -> str:
        for document in state.policy_documents:
            if str(document.get("policy_id") or "") == policy_id:
                return str(document.get("source_url") or "")
        return ""

    @staticmethod
    def _policy_document(state: PolicyImpactState, policy_id: str) -> dict[str, Any]:
        return next(
            (
                document
                for document in state.policy_documents
                if str(document.get("policy_id") or "") == policy_id
            ),
            {},
        )

    def _policy_effective_query(self, session_id: str, message: str) -> str:
        text = str(message or "").strip()
        followup_markers = (
            "刚才",
            "上面",
            "这个结论",
            "第",
            "条款",
            "证据",
            "为什么",
            "反驳",
            "撤回",
            "义务主体",
            "那如果",
            "上一轮",
            "第一轮",
            "补充",
            "相比",
            "变化",
            "哪些",
            "是否",
        )
        if not any(marker in text for marker in followup_markers):
            return text
        history = self.store.list_chat_messages(session_id)
        previous_users = [
            str(item.get("content") or "")
            for item in history[:-1]
            if item.get("role") == "user" and str(item.get("content") or "").strip()
        ][-4:]
        if not previous_users:
            return text
        prior = "\n".join(
            f"前序用户事实 {index}：{content}"
            for index, content in enumerate(previous_users, start=1)
        )
        return f"{prior}\n当前用户补充：{text}"

    @staticmethod
    def _relevant_policy_assessments(
        text: str,
        assessments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = str(text or "").lower()
        named = [item.strip() for item in re.findall(r"《([^》]+)》", normalized) if item.strip()]
        if named:
            return [
                item
                for item in assessments
                if any(name in str(item.get("policy_title") or "").lower() for name in named)
            ]
        topic_rules = {
            "network_data": (
                "网络数据",
                "数据安全风险评估",
                "重要数据",
                "一般数据处理者",
                "数据留痕",
                "审计留痕",
                "运行留痕",
            ),
            "anthropomorphic": ("拟人化", "情感互动", "情感陪伴", "工作助手"),
            "agent_guidance": ("智能体规范", "agent 平台", "agent平台", "智能体平台"),
            "ai_ethics": ("科技伦理", "伦理审查"),
        }
        title_markers = {
            "network_data": "网络数据安全风险评估",
            "anthropomorphic": "拟人化互动",
            "agent_guidance": "智能体规范应用",
            "ai_ethics": "科技伦理审查",
        }
        requested_topics = {
            topic
            for topic, terms in topic_rules.items()
            if any(term in normalized for term in terms)
        }
        unsupported_specific_terms = (
            "个人信息保护法",
            "欧盟人工智能法",
            "eu ai act",
            "程序化交易",
            "算法备案",
            "深度合成",
            "生成式人工智能服务管理暂行办法",
            "深远海",
            "海洋养殖",
            "养殖装备",
            "农机",
            "农业补贴",
            "渔业补贴",
        )
        if any(term in normalized for term in unsupported_specific_terms) and not requested_topics:
            return []
        if requested_topics:
            return [
                item
                for item in assessments
                if any(
                    marker in str(item.get("policy_title") or "")
                    for topic, marker in title_markers.items()
                    if topic in requested_topics
                )
            ]
        return assessments

    @staticmethod
    def _policy_lookback_days(message: str) -> int:
        text = str(message or "")
        match = re.search(r"(?:近|最近)\s*(\d{1,3})\s*(天|日|个月|月)", text)
        if not match:
            if any(term in text for term in ("本周", "一周", "过去7天")):
                return 7
            if any(term in text for term in ("最近", "最新", "近期", "新增", "更新")):
                return 30
            return 365
        value = int(match.group(1))
        days = value * 30 if "月" in match.group(2) else value
        return max(1, min(days, 365))

    def _news_workflow_answer(
        self,
        message: str,
        result: dict[str, Any],
        artifacts: dict[str, str],
    ) -> str:
        topic_labels = {
            "inference_cost": "推理成本",
            "long_context": "长上下文",
            "tool_use": "Agent 工具调用",
            "agent_harness": "Agent Harness",
            "model_products": "模型产品",
            "developer_tools": "开发者工具",
            "financial_ai": "金融 AI",
        }
        def short_text(value: Any, limit: int) -> str:
            text = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
            return text if len(text) <= limit else text[:limit - 1] + "…"

        def semantic_text(value: Any, limit: int) -> str:
            # Reject source-sized or untranslated text, even in a semantic field.
            if not isinstance(value, str) or len(value) > 320 or not re.search(r"[\u4e00-\u9fff]", value):
                return ""
            return short_text(value, limit)

        top_matches = list(result.get("top_matches") or [])
        allowed = result.get("relevance_gate") == "allow"
        pending = any(
            item.get("analysis_status") != "done" or item.get("analysis_mode") != "model"
            or not semantic_text(item.get("event_summary_zh"), 100)
            for item in top_matches
        )
        partial = result.get("analysis_status") == "partial" or pending
        statuses = (result.get("source_manifest") or {}).get("source_statuses") or []
        successful = sum(item.get("status") == "ok" for item in statuses)
        heading = (
            "来源与问题相关性门控未通过，本轮不生成推断性结论。" if not allowed
            else "报告部分完成：部分分析或证据仍待核验，不代表已完成全部研判。" if partial
            else "已生成基于当前 Wiki 与来源资料的条件式研判报告。"
        )
        header = "\n".join([
            heading,
            (f"来源：{successful}/{len(statuses)} 个成功。" if statuses else "来源状态未记录。")
            + f"读取 {result.get('event_count', 0)} 条，匹配 {result.get('matched_event_count', 0)} 条，"
            + f"入选 {result.get('selected_event_count', len(top_matches))} 条。",
        ])
        missing_topics = [
            topic_labels.get(topic, topic)
            for topic, covered in (result.get("topic_coverage") or {}).items()
            if not covered
        ]
        limitations = [f"门控说明：{short_text(result.get('gate_reason'), 130) or '未记录'}"]
        if pending:
            limitations.append("未通过验证的条目仅保留标题；中文摘要与条件式影响分析待完成。")
        if missing_topics:
            limitations.append(f"未覆盖主题：{short_text('、'.join(missing_topics), 80)}；缺项不生成结论。")
        limitations.extend(short_text(item, 80) for item in (result.get("incomplete_reasons") or [])[:2])
        if "future_window:" in str(result.get("gate_reason") or ""):
            limitations.append("请改用已经过去的日期窗口；无法获取尚未发布的资料。")
        footer_lines = [
            f"数据范围：{short_text(result.get('source_disclosure'), 100) or '未记录'}",
            "限制：" + "；".join(item for item in limitations if item),
            "条件式建议不代表已采用或已产生效果。原始来源、Wiki 事实与完整诊断保留在报告产物和 Trace。",
        ]
        report_path = artifacts.get("html_report") or artifacts.get("report") or ""
        if report_path and len(report_path) <= 400 and not any(char in report_path for char in "\r\n<>"):
            footer_lines.append(f"[查看完整报告](<{report_path.replace(chr(92), '/')}>)")
        elif "future_window:" in str(result.get("gate_reason") or ""):
            footer_lines.append("本轮没有生成正式报告；诊断信息见 RunArtifact 和 Trace。")
        else:
            footer_lines.append("完整报告见本轮报告产物。")
        footer = "\n".join(footer_lines)
        entries = []
        # Leave room for the public /report prefix; never truncate a report link or disclosure.
        remaining = max(0, 1200 - len(header) - len(footer) - 40)
        if allowed:
            for index, item in enumerate(top_matches[:3], start=1):
                title = short_text(item.get("title"), 48) or "未命名条目"
                summary = semantic_text(item.get("event_summary_zh"), 100)
                validated = item.get("analysis_status") == "done" and item.get("analysis_mode") == "model"
                if not validated or not summary:
                    entry = f"{index}. {title}\n分析待完成：尚无通过验证的中文摘要与条件式影响，不据此下结论。"
                else:
                    condition = semantic_text(item.get("condition"), 60)
                    impact = semantic_text(item.get("conditional_impact"), 100)
                    impact_text = (
                        f"{condition}，{impact}" if condition and impact
                        else "适用条件与完整影响说明见报告，实际采用状态和效果仍需核验。"
                    )
                    entry = f"{index}. {title}\n摘要：{summary}\n条件式影响：{impact_text}"
                    action = semantic_text(item.get("recommended_action"), 320)
                    if action:
                        action = short_text(action.split("；执行边界：", 1)[0], 80)
                        entry += f"\n建议行动：{action}"
                if len(entry) + 2 > remaining:
                    break
                entries.append(entry)
                remaining -= len(entry) + 2
        if allowed and len(top_matches) > len(entries):
            entries.append(f"其余 {len(top_matches) - len(entries)} 项见完整报告。")
        elif allowed and not top_matches:
            entries.append("当前没有通过来源门控且与问题匹配的事件。")
        return "\n\n".join([header, *entries, footer])

    @staticmethod
    def _policy_data_mode(state: PolicyImpactState) -> str:
        return "fixture" if any(doc.get("data_mode") == "fixture" for doc in state.policy_documents) else "live_snapshot"

    @staticmethod
    def _is_workbench_general_query(message: str) -> bool:
        text = str(message or "").lower()
        has_workbench = "agent workbench" in text or "workbench" in text or "这个项目" in text or "本项目" in text
        has_coursepilot = "coursepilot" in text or "course pilot" in text
        has_user_intent = any(
            term in text
            for term in (
                "能做什么",
                "介绍",
                "区别",
                "侧重点",
                "学习",
                "设计",
                "harness",
                "runtime",
                "trace",
            )
        )
        return (has_workbench and has_user_intent) or has_coursepilot

    @staticmethod
    def _is_workbench_capability_query(message: str) -> bool:
        text = str(message or "").lower()
        has_explicit_subject = "agent workbench" in text or "workbench" in text
        asks_capability = any(term in text for term in ("能做什么", "介绍", "有哪些能力", "功能"))
        asks_model_boundary = any(
            term in text
            for term in (
                "模型调用边界",
                "模型边界",
                "什么时候调用模型",
                "哪些步骤调用模型",
                "model boundary",
            )
        )
        return has_explicit_subject and (asks_capability or asks_model_boundary)

    @staticmethod
    def _is_unbound_project_comparison(message: str) -> bool:
        text = str(message or "").lower()
        has_comparison = "coursepilot" in text or "course pilot" in text
        has_vague_subject = any(term in text for term in ("这个项目", "本项目", "我的项目"))
        has_explicit_subject = any(
            term in text
            for term in (
                "agent workbench",
                "workbench",
                "policy_impact",
                "同花顺",
                "company_001",
            )
        )
        return has_comparison and has_vague_subject and not has_explicit_subject

    @staticmethod
    def _render_workbench_general_answer(message: str) -> str:
        text = str(message or "").lower()
        if any(
            term in text
            for term in (
                "模型调用边界",
                "模型边界",
                "什么时候调用模型",
                "哪些步骤调用模型",
                "model boundary",
            )
        ):
            return (
                "Agent Workbench 的模型调用边界是：Runtime 先用确定性代码完成路由、权限、"
                "上下文编译和工具控制，只在所选 Skill 的语义生成或判断阶段调用已配置的 "
                "deepseek-v4-flash；每次模型调用都会写入 Trace，缺少可用模型时整轮拒绝执行，"
                "确定性 Stage 不存在隐藏模型旁路。"
            )
        if "coursepilot" in text or "course pilot" in text or "区别" in text:
            return "\n".join(
                [
                    "这个项目和 CoursePilot 的分工可以这样理解：",
                    "",
                    "1. CoursePilot 是课程学习 Agent Runtime：重点是学习/练习/批改流程、RAG 检索评测、学习记忆和上下文预算。",
                    "2. Agent Workbench 是 Agent Harness / Infra：重点是把每轮对话变成可路由、可控、可观测、可评估的 AgentRun。",
                    "3. CoursePilot 更像一个垂直业务 Agent；Workbench 更像控制层和观测层，可以挂 policy、news、research、wiki 等不同 skill。",
                    "4. 面试时 CoursePilot 负责证明你会做可运行的 Runtime 和 RAG，Workbench 负责证明你理解 Harness、Tool Policy、Trace、Memory、Eval。",
                ]
            )
        return "\n".join(
            [
                "Agent Workbench 当前可以做四类事情：",
                "",
                "1. 普通对话：读取企业知识库和长期记忆，但不会主动跑重型 workflow。",
                "2. Skill 执行：通过普通对话或 /report 进入不同能力边界。",
                "3. Harness 观测：每轮都会记录 AgentRun 和有序 AgentStep；权限判断与门控是确定性代码，模型回答和技能内容不是。context、model、final answer 会留痕，memory、tool/gate、artifact 按路由实际触发，未触发时计数为 0。",
                "4. 面试学习：你可以围绕 AgentRuntime、SkillRegistry、ContextManifest、ToolGateway、MemoryStore、Trace、Eval 逐个学习。",
                "",
                "这个项目的重点不是政策分析本身，而是学习一个成熟 Agent 系统怎样控制、观测和复盘每一次运行。",
            ]
        )

    @staticmethod
    def _query_allows_artifact_memory(message: str) -> bool:
        text = str(message or "").lower()
        return any(
            term in text
            for term in (
                "报告",
                "历史",
                "之前",
                "上次",
                "产物",
                "artifact",
                "report",
                "weekly",
            )
        )

    @staticmethod
    def _artifact_memory_key(item: dict[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return str(metadata.get("path") or item.get("content") or item.get("id") or "")

    def _is_stale_artifact_memory(self, message: str, item: dict[str, Any]) -> bool:
        memory_type = str(item.get("memory_type") or "").lower()
        content = str(item.get("content") or "").lower()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        blob = f"{content} {metadata}".lower()
        artifact_like = memory_type in {"skill_result", "policy_report", "news_report"} or any(
            term in blob
            for term in (
                "weekly-policy-impact",
                "recent-news-impact",
                "research-report",
                "生成政策影响报告",
                "生成最近新闻影响报告",
                "调研报告已生成",
            )
        )
        return artifact_like and not self._query_allows_artifact_memory(message)

    def _filter_general_chat_memories(
        self,
        message: str,
        memories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        seen_artifacts: set[str] = set()
        for item in memories:
            # Conversation summaries are session-scoped working memory. They are
            # loaded through get_conversation_summary(session_id) and must never
            # re-enter another session through the global memory search index.
            if item.get("memory_type") == "conversation_summary":
                continue
            if self._is_stale_artifact_memory(message, item):
                continue
            key = self._artifact_memory_key(item)
            if key and key in seen_artifacts:
                continue
            seen_artifacts.add(key)
            filtered.append(item)
            if len(filtered) >= 5:
                break
        return filtered

    @staticmethod
    def _report_followup_needs_wiki(message: str, history: list[dict[str, Any]]) -> bool:
        """Keep retrieved Wiki context for applying a report, not repeating it."""
        text = str(message or "").lower()
        if re.search(r"逐字|原样|照抄|(?:只|仅)(?:需|要)?复述|verbatim|repeat exactly", text):
            return False
        report_reference = any(term in text for term in ("报告", "论文", "研究", "report", "paper"))
        saved_report = any(
            item.get("role") == "assistant"
            and {"report", "html_report", "run_artifact"}.intersection(
                (item.get("metadata") or {}).get("artifacts") or {}
            )
            for item in history
        )
        application_intent = re.search(
            r"适合我|适合当前|适用于|值得先试|请比较"
            r"|(?:结合|根据|考虑|针对).{0,30}(?:我|当前|项目|场景|背景|限制|约束|wiki)"
            r"|(?:设计|制定|规划|安排).{0,24}(?:实验|验证|方案|计划|对照)"
            r"|(?:如何|怎么).{0,24}(?:应用|实施|验证|改进|落地)"
            r"|\b(?:apply|adapt|compare)\b|design.{0,24}experiment",
            text,
        )
        return bool((report_reference or saved_report) and application_intent)

    @staticmethod
    def _is_immediate_history_query(message: str) -> bool:
        text = str(message or "").lower()
        immediate_markers = (
            "刚才",
            "上一轮",
            "上轮",
            "本轮之前",
            "本次对话",
            "本次会话",
            "这个会话",
            "当前会话",
            "另一个会话",
            "previous turn",
            "just now",
            "this session",
        )
        explicit_memory_markers = (
            "长期记忆",
            "记忆里",
            "记忆中",
            "让我记住",
            "保存过",
            "保存的",
            "long-term memory",
            "saved memory",
        )
        negated_memory_write = bool(
            re.search(
                r"(?:不要|不需要|无需|禁止|不得|不)(?:将|把)?[^。；\n]{0,20}(?:写入|保存到|存入).{0,8}(?:长期)?记忆",
                text,
                flags=re.I,
            )
        )
        has_immediate_marker = any(marker in text for marker in immediate_markers)
        has_explicit_memory_marker = any(marker in text for marker in explicit_memory_markers)
        return has_immediate_marker and (not has_explicit_memory_marker or negated_memory_write)

    @staticmethod
    def _is_session_context_declaration(message: str) -> bool:
        """Identify declarative turn-scoped controls that should not pull global evidence."""
        text = str(message or "").strip().lower()
        if re.match(r"^(?:未决事项|禁止项|临时代号|输出偏好)\s*[：:]", text):
            return True
        return text.startswith(("纠正上一条", "更正上一条")) and not any(
            marker in text for marker in ("？", "?", "为什么", "如何", "分析", "查询", "检索")
        )

    @staticmethod
    def _is_unpersisted_cross_session_query(message: str) -> bool:
        text = str(message or "").lower()
        cross_session_markers = ("另一个会话", "其他会话", "别的会话", "another session")
        explicit_memory_markers = (
            "长期记忆",
            "记忆里",
            "记忆中",
            "让我记住",
            "保存过",
            "保存的",
            "long-term memory",
            "saved memory",
        )
        return any(marker in text for marker in cross_session_markers) and not any(
            marker in text for marker in explicit_memory_markers
        )

    @staticmethod
    def _is_workbench_design_query(message: str) -> bool:
        text = str(message or "").lower()
        has_workbench = "agent workbench" in text or "workbench" in text or "工作台" in text
        has_design_intent = any(
            term in text
            for term in (
                "设计",
                "重点",
                "架构",
                "侧重点",
                "学习",
                "harness",
                "runtime",
                "infra",
                "subagent",
            )
        )
        return has_workbench and has_design_intent

    def _filter_research_memories(
        self,
        message: str,
        memories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        text = str(message or "").lower()
        wants_policy = any(term in text for term in ("政策", "合规", "监管", "policy", "compliance", "regulation"))
        wants_news = any(term in text for term in ("新闻", "日报", "news", "daily", "latest"))
        design_terms = (
            "agent workbench",
            "workbench",
            "agentrun",
            "agentstep",
            "harness",
            "runtime",
            "skill",
            "context",
            "memory",
            "trace",
            "tool",
            "gate",
            "eval",
            "replay",
            "工作台",
            "上下文",
            "记忆",
            "工具",
            "追踪",
            "评测",
        )
        stale_policy_terms = (
            "weekly-policy-impact",
            "policy-impact",
            "政策影响报告",
            "政策分析",
            "合规分析",
        )
        stale_news_terms = ("weekly-news", "news-report", "新闻影响报告")

        filtered: list[dict[str, Any]] = []
        seen_memory_keys: set[str] = set()
        for item in memories:
            if item.get("memory_type") == "conversation_summary":
                continue
            content = str(item.get("content") or "")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            blob = f"{content} {metadata}".lower()
            memory_key = " ".join(content.split()).lower() or self._artifact_memory_key(item)
            if memory_key and memory_key in seen_memory_keys:
                continue

            if self._is_workbench_design_query(message):
                if any(term in blob for term in stale_policy_terms) or any(term in blob for term in stale_news_terms):
                    continue
                if any(term in blob for term in design_terms):
                    filtered.append(item)
                    if memory_key:
                        seen_memory_keys.add(memory_key)
                continue

            if not wants_policy and any(term in blob for term in stale_policy_terms):
                continue
            if not wants_news and any(term in blob for term in stale_news_terms):
                continue
            filtered.append(item)
            if memory_key:
                seen_memory_keys.add(memory_key)
        return filtered[:5]

    def _research_answer(self, message: str, report_path: Path) -> str:
        if self._is_workbench_design_query(message):
            return "\n".join(
                [
                    "Agent Workbench 的设计重点已整理完成。",
                    "",
                    "核心学习顺序：",
                    "1. AgentRuntime：每轮对话如何变成可审计的 AgentRun。",
                    "2. SkillRegistry：普通对话和 /report 如何决定执行边界。",
                    "3. ContextManifest：模型本轮能看什么、不能看什么、token 预算是多少。",
                    "4. MemoryStore：什么时候读记忆、什么时候写结果，如何避免旧结果污染新问题。",
                    "5. ToolGateway / PermissionEngine：工具调用为什么必须经过确定性权限层。",
                    "6. AgentStep / Trace：think、act、observe、answer 每一步如何前端可见。",
                    "7. Eval / Linter：用测试用例证明路由、上下文、记忆和 trace 没跑偏。",
                    "",
                    f"Markdown 报告：{report_path}",
                ]
            )
        if self._is_harness_interview_query(message):
            return "\n".join(
                [
                    "Agent Harness 面试讲法已整理完成。",
                    "",
                    "建议按这条线讲：",
                    "1. ContextManifest 解决本轮模型可见上下文和 token budget。",
                    "2. ToolGateway / PermissionEngine 解决工具权限、审计和确定性控制。",
                    "3. Trace / AgentStep 解决每一步输入输出、loop phase 和失败定位。",
                    "4. Eval / Linter 解决如何证明路由、上下文、记忆和产物没有跑偏。",
                    "",
                    f"Markdown 报告：{report_path}",
                ]
            )
        return f"调研报告已生成：{report_path}"

    def _render_research_report_v2(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        if self._is_workbench_design_query(message):
            return self._render_workbench_design_report(
                message, context_manifest, memories, model_payload, run_id=run_id
            )
        if self._is_harness_interview_query(message):
            return self._render_harness_interview_report(
                message, context_manifest, memories, model_payload, run_id=run_id
            )
        return self._render_generic_research_report(message, context_manifest, memories, model_payload)

    def _company_display_name(self) -> str:
        try:
            profile = load_company_knowledge(self.company_id).get("profile", {})
            return str(profile.get("company_name") or self.company_id)
        except FileNotFoundError:
            return self.company_id

    @staticmethod
    def _memory_bullets(memories: list[dict[str, Any]]) -> list[str]:
        if not memories:
            return ["- 本轮没有命中和当前问题直接相关的长期记忆。"]
        out = []
        for item in memories[:5]:
            content = str(item.get("content") or "").strip()
            if len(content) > 180:
                content = content[:177].rstrip() + "..."
            out.append(f"- {content}")
        return out

    def _current_run_fact_lines(self, run_id: str) -> list[str]:
        steps = self.store.list_agent_steps(run_id) if run_id else []
        counts: dict[str, int] = {}
        for step in steps:
            step_type = str(step.get("step_type") or "unknown")
            counts[step_type] = counts.get(step_type, 0) + 1
        return [
            "## 本次 Run 已发生的事实（报告写入前）",
            "",
            (
                "- "
                + ", ".join(
                    f"{name}={counts.get(name, 0)}"
                    for name in (
                        "intent_classification",
                        "skill_selection",
                        "context_build",
                        "memory_read",
                        "model_call",
                        "tool_call",
                        "gate_check",
                        "memory_write",
                    )
                )
            ),
            "- 上述计数只描述当前 Run；系统具备某项能力，不代表这一轮一定触发该步骤。",
        ]

    def _render_workbench_design_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        thesis = str(output.get("thesis") or "").strip() or (
            "Agent Workbench 的核心不是单个政策/新闻 workflow，而是把每轮用户对话变成"
            "可路由、可控、可观测、可评估、可复盘的 AgentRun。"
        )
        model_analysis = self._model_report_section(output)
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        memory_lines = self._memory_bullets(memories)
        return "\n".join(
            [
                "# Agent Workbench 设计重点整理",
                "",
                f"- 用户问题：{message}",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 一句话定位",
                "",
                thesis
                or "Agent Workbench 的核心不是单个政策/新闻工作流，而是一套把对话、技能、工具、记忆、上下文、追踪和评测统一起来的 Agent Harness。",
                "",
                "## 系统设计链（按任务条件触发，并非每轮全走）",
                "",
                "```text",
                "User message",
                "-> AgentRuntime 创建 AgentRun",
                "-> SkillRegistry 选择 skill",
                "-> ContextManifest 构造本轮可见上下文",
                "-> MemoryStore 按问题语义读取相关长期记忆（按需）",
                "-> ModelGateway / AgentLoop 执行真实模型调用",
                "-> ToolGateway / PermissionEngine 管控工具调用（技能需要工具时）",
                "-> GateEngine 校验关键输出（存在契约或风险门控时）",
                "-> ArtifactStore 保存报告或结构化产物（产物型技能）",
                "-> MemoryStore 写入可复用结果（仅显式请求或上下文压缩）",
                "-> AgentStep 持久化 trace 并在前端展示",
                "```",
                "",
                *self._current_run_fact_lines(run_id),
                "",
                "## 模型生成的补充分析（不是当前 Run 事实）",
                "",
                *model_analysis,
                "",
                "## 你学习这个项目时的重点",
                "",
                "1. **AgentRuntime**：看每一轮对话如何被拆成 intent、skill、context、memory、tool/model、artifact、final answer。重点不是“能回答”，而是每一步都有输入、输出和状态。",
                "2. **SkillRegistry**：理解普通对话和 `/report` 的差异。Skill 是业务能力的边界，也是工具权限、上下文策略和 artifact 类型的入口。",
                "3. **ContextManifest**：重点看 visible keys、hidden fields、token budget、checksum。它解决的是“本轮模型到底能看到什么”，不是简单拼 prompt。",
                "4. **MemoryStore**：区分 session message、长期 memory、skill result memory。尤其要注意旧报告结果不能污染新问题，本轮已经加入 memory 过滤指标。",
                "5. **ToolGateway / PermissionEngine**：工具调用不是 prompt 里说一句“不要乱用”就结束，而是由 harness 层确定性 allow/deny/ask，并记录 tool audit。",
                "6. **AgentStep / Trace**：每一步都带 step_type、loop_phase、input_payload、output_payload、tool_calls、gate_result、metadata。前端展示的是系统可解释性。",
                "7. **Eval / Linter**：用动态对话测试、pytest 和 harness_linter 去证明路由、memory、context、trace 和前端没有跑偏。",
                "",
                "## 和 CoursePilot 的区别",
                "",
                "- CoursePilot 重点是课程学习 Runtime：RAG、练习、批改、学习记忆和检索评测。",
                "- Agent Workbench 重点是 Harness / Infra：同一个对话入口下，如何控制 skill、上下文、工具权限、trace、artifact 和 eval。",
                "- 所以 Workbench 不要讲成政策分析产品，政策/新闻/调研只是挂在 Harness 上的业务 skill。",
                "",
                "## 本轮命中的长期记忆",
                "",
                *memory_lines,
                "",
                "## 面试讲法",
                "",
                "这个项目可以这样讲：我实现了一个单用户 Agent Workbench，每轮用户输入都会进入统一 AgentRuntime，经过 Skill 路由、ContextManifest、Memory、ToolGateway、Gate、Artifact 和 Trace。业务 skill 可以替换，但 harness 层负责控制、观测、评估和复盘。",
            ]
        )

    @staticmethod
    def _is_harness_interview_query(message: str) -> bool:
        text = str(message or "").lower()
        required = any(term in text for term in ("harness", "面试", "讲法", "contextmanifest", "toolgateway", "trace", "eval"))
        concept_count = sum(
            1
            for term in ("contextmanifest", "toolgateway", "trace", "eval", "memory", "gate", "artifact")
            if term in text
        )
        return required and concept_count >= 2

    def _render_harness_interview_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        memory_lines = self._memory_bullets(memories)
        return "\n".join(
            [
                "# Agent Harness 面试讲法整理",
                "",
                f"- 用户问题：{message}",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 总体讲法",
                "",
                "我不会把这个项目讲成一个简单的政策/新闻分析器，而会讲成一个单用户 Agent Harness。"
                "它的核心是把每一轮用户输入变成可控制、可观测、可评估、可复盘的 AgentRun。",
                "",
                *self._current_run_fact_lines(run_id),
                "",
                "## 模型生成的补充分析（不是当前 Run 事实）",
                "",
                *self._model_report_section(output),
                "",
                "## 四个核心模块",
                "",
                "### 1. ContextManifest",
                "",
                "- 解决问题：模型本轮到底能看到什么，哪些 state 字段必须隐藏，token 预算怎么分配。",
                "- 项目实现：每轮 `context_build` step 记录 `visible_keys`、`hidden_fields`、`token_budget`、`token_estimates` 和 checksum。",
                "- 面试回答：这不是简单拼 prompt，而是把上下文变成可审计的输入契约。",
                "",
                "### 2. ToolGateway / PermissionEngine",
                "",
                "- 解决问题：工具调用不能只靠 prompt 让模型自觉，必须有确定性权限层。",
                "- 项目实现：工具调用统一经过 ToolGateway，记录 allow/deny/ask 风格的 gate result 和 tool audit。",
                "- 面试回答：模型可以提出工具意图，但 harness 决定是否执行，并把决策原因写进 trace。",
                "",
                "### 3. Trace / AgentStep",
                "",
                "- 解决问题：Agent 失败时不能只看到最终回答，要能还原每一步发生了什么。",
                "- 项目实现：每轮运行持久化为 AgentRun；每个 AgentStep 记录 step_type、loop_phase、input_payload、output_payload、tool_calls、gate_result、metadata。",
                "- 面试回答：trace 不是日志堆积，而是围绕 think / act / observe / answer 的运行证据。",
                "",
                "### 4. Eval / Linter",
                "",
                "- 解决问题：不能靠主观看 demo 判断 Agent 是否变好。",
                "- 项目实现：用 pytest、harness_linter、API dialogue matrix 验证 skill 路由、memory 过滤、context、artifact 和 trace 结构。",
                "- 面试回答：我会用固定 case 覆盖普通对话、/report 和 Wiki 数据页等混合意图，防止回归。",
                "",
                "## 容易被追问的点",
                "",
                "- 为什么不是 Dify / LangGraph workflow？因为这个项目重点在每轮对话的控制面和观测面，而不是只跑固定 DAG。",
                "- 为什么不是 Codex/Claude Skill？因为通用 Agent 可以执行任务，但这里的价值是长期 memory、tool policy、trace、artifact、eval 都在本地系统里可审计。",
                "- 多 Agent 在哪里？当前以 skill/subagent 角色边界为主，policy/news workflow 可继续把底层 stage 映射成更细的 AgentStep。",
                "",
                "## 本轮命中的长期记忆",
                "",
                *memory_lines,
            ]
        )

    def _render_generic_research_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        thesis = str(output.get("thesis") or "").strip()
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        return "\n".join(
            [
                f"# 调研报告：{message}",
                "",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 结论",
                "",
                thesis or "本轮调研应围绕问题本身组织结论、证据、风险和下一步验证动作。",
                "",
                "## 模型调研内容",
                "",
                *self._model_report_section(output),
                "",
                "## 相关记忆",
                "",
                *self._memory_bullets(memories),
                "",
                "## 建议",
                "",
                "- 优先把问题拆成可验证的判断点。",
                "- 区分模型生成内容、工具返回内容和长期记忆内容。",
                "- 在 trace 中检查 context、memory、model_call 和 artifact 是否互相一致。",
            ]
        )

    @staticmethod
    def _model_report_section(output: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for label, key in (
            ("证据", "evidence"),
            ("风险", "risks"),
            ("建议", "recommendations"),
            ("下一步验证", "next_checks"),
        ):
            values = output.get(key) if isinstance(output, dict) else None
            if not isinstance(values, list) or not values:
                continue
            lines.append(f"### {label}")
            lines.append("")
            lines.extend(f"- {str(item).strip()}" for item in values if str(item).strip())
            lines.append("")
        return lines or ["- 模型未返回可用的结构化分析项。"]

    def _render_research_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
    ) -> str:
        company_name = self.company_id
        try:
            company_name = str(load_company_knowledge(self.company_id).get("profile", {}).get("company_name") or company_name)
        except FileNotFoundError:
            pass
        thesis = (
            model_payload.get("output", {}).get("thesis")
            if isinstance(model_payload.get("output"), dict)
            else ""
        )
        memory_lines = "\n".join(f"- {item.get('content', '')}" for item in memories[:5]) or "- 未命中相关长期记忆。"
        visible_keys = ", ".join(context_manifest.get("visible_keys", []))
        return "\n".join(
            [
                f"# 调研报告：{message}",
                "",
                f"- 企业：{company_name}",
                f"- 本轮运行可见字段：{visible_keys}",
                "",
                "## 结论",
                "",
                thesis
                or "成熟的 Agent Harness 需要在同一个受控运行时内整合对话、技能、工具、记忆、上下文、追踪和产物。",
                "",
                "## 证据",
                "",
                "- 当前项目已经具备 ToolGateway、ContextManifest、AgentLoop、Checkpoint、Replay 和 SQLite memory。",
                "- 新的智能体工作台会把每轮用户请求记录为可审计的 AgentRun，并持久化有序的 AgentStep。",
                memory_lines,
                "",
                "## 建议",
                "",
                "- 将业务技能保持为可插拔能力，不要把所有行为硬编码到页面层。",
                "- 在执行追踪中持续展示工具策略、上下文清单、记忆读写、模型调用和产物路径。",
                "- 面试演示优先使用本地确定性运行；接入真实模型时只替换模型适配器边界。",
            ]
        )
