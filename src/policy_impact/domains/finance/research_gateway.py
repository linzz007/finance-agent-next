"""One bounded tool surface for autonomous finance research turns.

The local corpus is navigated by author folder and date.  Article bodies are
still read one at a time, while market snapshots and saved reports remain
separate tools.  The gateway owns the single audit stream for the turn.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.article_gateway import FinanceArticleGateway
from policy_impact.domains.finance.article_study import ArticleStudy
from policy_impact.domains.finance.chat_gateway import FinanceChatGateway
from policy_impact.domains.finance.market_research import MarketResearch
from policy_impact.domains.finance.report_reader import read as read_report
from policy_impact.domains.finance.research_plan import ResearchPlan
from policy_impact.domains.finance.research_artifacts import ResearchArtifacts
from policy_impact.domains.finance.research_coverage import ResearchCoverage
from policy_impact.domains.finance.research_synthesis import ResearchSynthesis
from policy_impact.domains.finance.research_verification import ResearchVerification
from policy_impact.domains.finance.verification_checkpoint import VerificationCheckpoint
from policy_impact.domains.finance.web_research import WebResearch
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.context_offload import RunContextStore
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.memory.store import PolicyMemoryStore


def infer_article_window(question: str, *, today: date | None = None) -> tuple[str, str] | None:
    """Resolve explicit relative article periods without constraining other research."""
    text = str(question or "").casefold()
    if not text:
        return None
    days: int | None = None
    if re.search(
        r"(?:最近|近|过去|过去的)\s*(?:一|1个?|1)\s*个月|"
        r"last\s+month|30\s*(?:天|日|days?)",
        text,
    ):
        days = 30
    elif re.search(
        r"(?:最近|近|过去|过去的)\s*(?:一|1个?|1)\s*(?:周|星期)|"
        r"last\s+week|7\s*(?:天|日|days?)",
        text,
    ):
        days = 7
    elif re.search(
        r"(?:最近|近|过去|过去的)\s*(?:三|3个?|3)\s*个月|"
        r"last\s+3\s+months|90\s*(?:天|日|days?)",
        text,
    ):
        days = 90
    if days is None:
        return None
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def infer_source_scope(question: str) -> dict[str, Any]:
    """Honor explicit user source boundaries without selecting an analysis workflow."""
    text = str(question or "")
    lower = text.casefold()
    local_only = bool(re.search(
        r"(?<!不)(?:只|仅)(?:根据|基于|使用|看).{0,150}(?:本地|文章|资料)|"
        r"(?:不|不要|不需|无需|禁止|别)(?:联网|上网|搜索网页|查外部|调用行情)|"
        r"(?:without|no)\s+(?:web|internet|external)|local\s+(?:articles?|sources?)\s+only",
        lower,
    ))
    dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
    explicit_dates: list[str] = []
    if len(set(dates)) >= 2 and re.search(r"文章|篇|articles?", lower):
        between = re.search(
            r"20\d{2}-\d{2}-\d{2}\s*(?:至|到|~|～|to|through|until)\s*"
            r"20\d{2}-\d{2}-\d{2}",
            lower,
        )
        if not between:
            explicit_dates = list(dict.fromkeys(dates))
    return {"local_only": local_only, "explicit_article_dates": explicit_dates}


class FinanceResearchGateway(ToolGateway):
    """Compose finance tools without making the user question choose a mode."""

    def __init__(
        self,
        company_id: str,
        report_ids: set[str],
        *,
        question: str,
        analyze: Callable[[str], dict[str, Any]] | None = None,
        complete_claims: Callable | None = None,
        complete_study: Callable | None = None,
        complete_synthesis: Callable | None = None,
        complete_review: Callable | None = None,
        corpus: ArticleCorpus | None = None,
        enable_analysis: bool = False,
        child_roles: tuple[str, ...] = (),
        allowed_article_ids: set[str] | None = None,
        session_id: str = "",
        run_id: str = "",
        research_state_path: Path | None = None,
        source_question: str | None = None,
        require_plan_before_tools: bool = False,
        on_verification_record: Callable[[dict[str, Any]], None] | None = None,
        on_coverage_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.company_id = company_id
        self.question = str(question or "")
        self.source_scope = infer_source_scope(source_question if source_question is not None else question)
        self.report_ids = set(report_ids)
        self.memory_store = PolicyMemoryStore(company_id)
        self.article_gateway = FinanceArticleGateway(
            company_id, corpus=corpus, complete_claims=complete_claims
        )
        if analyze is None:
            def analyze(query: str) -> dict[str, Any]:
                raise RuntimeError("finance.analyze is not available in autonomous research")
        self.chat_gateway = FinanceChatGateway(
            company_id,
            self.report_ids,
            question=question,
            analyze=analyze,
        )
        self.web = WebResearch(self.article_gateway.evidence_ledger)
        self.market = MarketResearch(self.article_gateway.evidence_ledger)
        if research_state_path is not None:
            if not session_id or not run_id or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", run_id):
                raise ValueError("persisted research plans require generated session and run identifiers")
            expected = (Path(research_state_path).resolve())
            from policy_impact.company_wiki.loader import company_dir

            allowed = (company_dir(company_id) / "runs" / run_id / "research_plan.json").resolve()
            if expected != allowed:
                raise ValueError("research plan state must be the Runtime-generated run file")
        self.plan = ResearchPlan(
            session_id=session_id,
            run_id=run_id,
            state_path=research_state_path,
        )
        self.require_plan_before_tools = require_plan_before_tools
        self.artifacts = ResearchArtifacts(
            company_id, run_id, session_id, self.article_gateway.corpus,
            self.article_gateway.evidence_ledger, self.article_gateway.read_refs,
        )
        self.context_store = None
        if session_id and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", run_id):
            from policy_impact.company_wiki.loader import company_dir

            self.context_store = RunContextStore(company_dir(company_id) / "runs" / run_id)
        self.article_study = ArticleStudy(self.article_gateway, self.artifacts, complete_study)
        self.synthesis = ResearchSynthesis(self.artifacts, complete_synthesis)
        self.verification = ResearchVerification(
            self.artifacts, self.article_gateway.evidence_ledger,
            complete_review, self._verification_source_detail,
        )
        self.verification_checkpoint = VerificationCheckpoint(
            self.article_gateway.evidence_ledger, on_verification_record
        )
        self.coverage = ResearchCoverage(self.article_gateway.evidence_ledger, on_coverage_record)
        self.enable_analysis = enable_analysis
        self.article_window = infer_article_window(question)
        self.article_list_attempted = False
        self.article_list_count = 0
        self.article_list_max_count = 0
        self.article_list_any_match = False
        self.article_list_history: list[dict[str, Any]] = []
        self.last_article_listing: list[dict[str, Any]] = []
        # An empty author/date scope is useful once: it lets the model move to
        # another folder. Repeating the exact empty scope is no longer useful
        # evidence acquisition, so the plugin can terminate that branch.
        self._empty_article_scope_counts: dict[str, int] = {}
        self.allowed_article_ids = frozenset(allowed_article_ids) if allowed_article_ids is not None else None
        self.research_locked = False
        self.capabilities = None
        policies = [
            ToolPolicy("chat_agent_loop", "skill.open", "allow", "configured capability guidance only"),
            ToolPolicy("chat_agent_loop", "tool.describe", "allow", "current-run tool descriptors only"),
            ToolPolicy("chat_agent_loop", "company_wiki_search", "allow", "workspace-scoped wiki search"),
            ToolPolicy("chat_agent_loop", "memory_search", "allow", "workspace-scoped long-term memory search"),
            ToolPolicy("chat_agent_loop", "research.plan", "allow", "run-local research goals"),
            ToolPolicy("chat_agent_loop", "research.artifact", "allow", "run-local evidence-bound research records"),
            ToolPolicy("chat_agent_loop", "research.synthesize", "allow", "question-focused cross-article analysis"),
            ToolPolicy("chat_agent_loop", "research.verify", "allow", "typed claim review over observed evidence"),
            ToolPolicy("chat_agent_loop", "research.checkpoint", "allow", "record verification decisions and gaps"),
            ToolPolicy("chat_agent_loop", "research.coverage", "allow", "record evidence coverage and acquisition decision"),
            ToolPolicy("chat_agent_loop", "context.open", "allow", "restore run-local tool observation"),
            ToolPolicy("chat_agent_loop", "article.list", "allow", "read-only article metadata"),
            ToolPolicy("chat_agent_loop", "article.read", "allow", "read one bounded article"),
            ToolPolicy("chat_agent_loop", "article.claims", "allow", "on-demand source extraction"),
            ToolPolicy("chat_agent_loop", "article.study", "allow", "one-article source-bound study"),
            ToolPolicy("chat_agent_loop", "source.discover", "deny" if self.source_scope["local_only"] else "allow", "user source boundary"),
            ToolPolicy("chat_agent_loop", "source.fetch", "deny" if self.source_scope["local_only"] else "allow", "user source boundary"),
            ToolPolicy("chat_agent_loop", "finance.report_read", "allow", "conversation-scoped saved report"),
            *[ToolPolicy("chat_agent_loop", name,
                         "deny" if self.source_scope["local_only"] else "allow",
                         "user source boundary" if self.source_scope["local_only"] else "read-only finance research")
              for name in ("web.search", "web.open", "market.catalog", "market.snapshot",
                           "market.metrics", "market.series")],
            ToolPolicy(
                "chat_agent_loop",
                "finance.analyze",
                "allow" if enable_analysis else "deny",
                "read-only market snapshot" if enable_analysis else "finance profile unavailable",
            ),
        ]
        for role in child_roles:
            policies.extend(ToolPolicy(f"subagent.{role}", name,
                                       "deny" if self.source_scope["local_only"] and name.startswith(("web.", "market.")) else "allow",
                                       "user source boundary" if self.source_scope["local_only"] else "finance review read-only tool")
                            for name in ("article.list", "article.read", "web.search", "web.open",
                                         "market.catalog", "market.snapshot", "market.metrics", "market.series",
                                         "company_wiki_search"))
        super().__init__(PermissionEngine(policies), local_tools={
            "skill.open": self.open_skill,
            "tool.describe": self.describe_tool,
            "company_wiki_search": self.wiki_search,
            "memory_search": self.search_memory,
            "research.plan": self.plan.call,
            "research.artifact": self.research_artifact,
            "research.synthesize": self.synthesize,
            "research.verify": self.verify,
            "research.checkpoint": self.verification_checkpoint.record,
            "research.coverage": self.coverage.record,
            "context.open": self.open_context,
            "article.list": self.list_articles,
            "article.read": self.read_article,
            "article.claims": self.claims,
            "article.study": self.study,
            "source.discover": self.discover,
            "source.fetch": self.fetch,
            "web.search": self.web.search,
            "web.open": self.web.open,
            "market.catalog": self.market.catalog,
            "market.snapshot": self.market.snapshot,
            "market.metrics": self.market.metrics,
            "market.series": self.market.series,
            "finance.report_read": self.read_report,
            "finance.analyze": self.analyze_market,
        })
        from policy_impact.domains.finance.research_execution import ResearchExecution

        self.research_execution = ResearchExecution(self)
        self.local_tools["research.disposition"] = self.research_execution.disposition
        self.permission_engine = PermissionEngine([
            *policies, ToolPolicy("chat_agent_loop", "research.disposition", "allow", "parent result decision"),
        ])

    def prepare_research_delegation(self, task, task_id):
        return self.research_execution.prepare_delegate(task, task_id)

    def read_refs_for_article(self, article_id):
        return [row for row in self.read_refs if row["article_id"] == article_id]

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        from policy_impact.domains.finance.tool_contracts import validate_tool_input

        if args:
            raise ValueError("tool_contract_error: financial tools require named JSON arguments")
        validate_tool_input(tool_name, kwargs)
        if (self.require_plan_before_tools and stage_name == "chat_agent_loop"
                and tool_name not in {"research.plan", "skill.open", "tool.describe"}
                and not self.plan.exists):
            raise ValueError(
                "research_plan_required: call research.plan first, then retry the selected tool. "
                "Use operation=create with goals as a 2-8 item string array and optional scope "
                "such as {authors:[], date_range:{start:YYYY-MM-DD, end:YYYY-MM-DD}, topics:[], "
                "deliverable:string}. The plan records requirements only; it does not prescribe a workflow. "
                "Do not repeat the blocked finance tool call."
            )
        return super().call(stage_name, tool_name, *args, **kwargs)

    def open_skill(self, skill_id: str) -> dict:
        if self.capabilities is None:
            raise PermissionError("capability discovery is not configured")
        return self.capabilities.open_skill(skill_id)

    def describe_tool(self, name: str) -> dict:
        if self.capabilities is None:
            raise PermissionError("capability discovery is not configured")
        return self.capabilities.describe_tool(name)

    @property
    def corpus(self):
        return self.article_gateway.corpus

    def open_context(self, context_id: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        if self.context_store is None:
            raise PermissionError("context restoration requires a Runtime-bound finance run")
        from policy_impact.harness.research_budget import CURRENT
        scope = CURRENT.get()
        view_budget = scope.root.limits.input_limit() // 4 if scope else 6000
        return self.context_store.view(context_id, offset=offset, limit=limit, max_tokens=view_budget)

    @property
    def search_attempted(self):
        return self.article_gateway.search_attempted

    @property
    def last_match_count(self):
        return self.article_gateway.last_match_count

    @property
    def read_refs(self):
        return self.article_gateway.read_refs

    @property
    def read_results(self):
        return self.article_gateway.read_results

    @property
    def claims_attempted(self):
        return self.article_gateway.claims_attempted

    @property
    def external_reads(self):
        return self.article_gateway.external_reads

    @property
    def external_results(self):
        return self.article_gateway.external_results

    @property
    def web_reads(self):
        return self.web.opened_pages

    @property
    def market_reads(self):
        return self.market.metric_reads

    @property
    def evidence_ledger(self):
        return self.article_gateway.evidence_ledger

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return self.article_gateway.evidence_ledger.evidence_ids

    def evidence_bundle(self) -> dict[str, Any]:
        return self.article_gateway.evidence_ledger.bundle()

    @property
    def analysis_result(self):
        return self.chat_gateway.analysis_result

    @property
    def analysis_error(self):
        return self.chat_gateway.analysis_error

    @property
    def analysis_attempted(self):
        return self.chat_gateway.analysis_attempted

    @property
    def read_sections(self):
        return self.chat_gateway.read_sections

    @property
    def observations(self):
        return self.chat_gateway.observations

    def list_articles(self, **kwargs):
        self._ensure_research_open()
        if set(kwargs) - {"limit", "author", "published_after", "published_before"}:
            raise ValueError("article.list accepts limit, author and date bounds only")
        self.article_list_attempted = True
        model_requested = dict(kwargs)
        requested = dict(kwargs)
        had_prior_match = self.article_list_any_match
        empty_intersection = False
        if self.article_window:
            window_start, window_end = self.article_window
            explicit_start = str(requested.get("published_after") or "")
            explicit_end = str(requested.get("published_before") or "")
            requested["published_after"] = max(explicit_start, window_start) if explicit_start else window_start
            requested["published_before"] = min(explicit_end, window_end) if explicit_end else window_end
            empty_intersection = requested["published_after"] > requested["published_before"]
        articles = [] if empty_intersection else self.corpus.list_articles(**requested)
        if self.allowed_article_ids is not None:
            articles = [
                row for row in ([] if empty_intersection else self.corpus.list_articles(**{**requested, "limit": 500}))
                if row["article_id"] in self.allowed_article_ids
            ][:requested.get("limit", 100)]
        if self.source_scope["explicit_article_dates"]:
            articles = [row for row in articles
                        if row["published_at"] in self.source_scope["explicit_article_dates"]]
        result = {
            "scope": "local_author_folders_metadata",
            "authors": self.corpus.list_authors(),
            "articles": articles,
            "notice": "Metadata only. Read each selected article separately before using its viewpoint.",
        }
        if len(articles) > 6:
            result["adaptive_research_hint"] = {
                "kind": "large_article_scope",
                "candidate_count": len(articles),
                "recommendation": (
                    "The candidate set is large. Consider delegating bounded analyst batches "
                    "before reading many full articles in the main context; this is an adaptive "
                    "context-preservation hint, not a mandatory workflow."
                ),
            }
        if self.article_window:
            result["date_policy"] = {
                "mode": "runtime_inferred_relative_window",
                "model_requested": {
                    key: model_requested[key]
                    for key in ("author", "published_after", "published_before", "limit")
                    if key in model_requested
                },
                "effective_request": {
                    key: requested[key]
                    for key in ("author", "published_after", "published_before", "limit")
                    if key in requested
                },
                "published_after": requested["published_after"],
                "published_before": requested["published_before"],
                "reason": "Relative article period was resolved against the Runtime date.",
            }
            if empty_intersection or not articles:
                result["date_policy"]["no_matches"] = True
                if empty_intersection:
                    result["date_policy"]["empty_intersection"] = True
                result["notice"] = (
                    "No articles matched the Runtime-resolved date window. "
                    "Do not widen the window implicitly; ask the user for a different period."
                )
                # With an explicit author, an empty relative window only
                # proves that this folder is empty. Keep the loop open so the
                # model can inspect another author; the repeated-scope guard
                # handles an unproductive retry. An unscoped empty window is
                # terminal because there is no narrower branch to explore.
                if not had_prior_match and not requested.get("author"):
                    result["_runtime_terminal"] = {
                        "kind": "empty_article_window",
                        "reason": "No local articles are available in the Runtime-resolved date window.",
                    }
        scope_key = self._article_scope_key(requested)
        scope_control: dict[str, Any] | None = None
        if not articles:
            repeat_count = self._empty_article_scope_counts.get(scope_key, 0) + 1
            self._empty_article_scope_counts[scope_key] = repeat_count
            scope_control = {
                "kind": "empty_article_scope",
                "scope_key": scope_key,
                "checked": True,
                "repeat_count": repeat_count,
                "author": requested.get("author") or None,
                "published_after": requested.get("published_after") or None,
                "published_before": requested.get("published_before") or None,
                "recommendation": (
                    "Switch to another author folder or ask the user to change the date range. "
                    "Do not repeat this empty scope and do not widen the date range implicitly."
                ),
            }
            result["scope_control"] = scope_control
            # A first empty scope is intentionally non-terminal: the model may
            # inspect another author. Only the same empty scope repeated in the
            # same turn is an unproductive loop.
            if repeat_count >= 2:
                remaining_authors = self._remaining_named_authors(
                    current_author=str(requested.get("author") or "").strip()
                )
                if remaining_authors:
                    scope_control["remaining_named_authors"] = remaining_authors
                    scope_control["recommendation"] = (
                        "This empty scope is exhausted, but other explicitly named author "
                        "folders remain unchecked. Check each remaining folder with article.list "
                        "before ending the multi-author request."
                    )
                    # A model can acknowledge the recommendation but still keep
                    # retrying the same effective scope with different date
                    # arguments.  Give it one recovery turn, then stop this
                    # branch so the whole run cannot burn its budget without
                    # reading a single article.
                    if repeat_count == 2:
                        result["_runtime_nudge"] = {
                            "kind": "repeated_empty_author_scope",
                            "reason": "The effective author/date scope is already exhausted.",
                            "remaining_named_authors": remaining_authors,
                            "instruction": (
                                "Do not call article.list for this author/date scope again. "
                                "Check one of remaining_named_authors, or return a truthful "
                                "partial answer if no further evidence is needed."
                            ),
                        }
                    else:
                        result["_runtime_terminal"] = {
                            "kind": "repeated_empty_author_scope_with_remaining_authors",
                            "reason": (
                                "The same effective empty author/date scope was retried "
                                "after Runtime already instructed the model to move on."
                            ),
                            "scope_control": scope_control,
                        }
                else:
                    unread_article_ids = self._unread_listed_article_ids()
                    if unread_article_ids:
                        scope_control["unread_article_ids"] = unread_article_ids[:24]
                        result["_runtime_nudge"] = {
                            "kind": "repeated_empty_scope_with_unread_candidates",
                            "reason": (
                                "The empty author/date scope is exhausted, but earlier "
                                "article.list calls returned unread candidates."
                            ),
                            "unread_article_ids": unread_article_ids[:24],
                            "instruction": (
                                "Do not call article.list for this empty scope again. "
                                "Read the listed article IDs one at a time, or delegate "
                                "bounded batches, then finalize with the evidence collected."
                            ),
                        }
                    else:
                        result["_runtime_terminal"] = {
                            "kind": "repeated_empty_article_scope",
                            "reason": "The same author/date article scope returned no rows twice.",
                            "scope_control": scope_control,
                        }
        elif scope_key in self._empty_article_scope_counts:
            scope_control = {
                "kind": "article_scope_progress",
                "scope_key": scope_key,
                "checked": True,
                "repeat_count": self._empty_article_scope_counts[scope_key],
                "result_count": len(articles),
                "recommendation": "Continue with the returned article IDs; read each selected article separately.",
            }
            result["scope_control"] = scope_control
        self.article_list_count = len(result["articles"])
        self.article_list_max_count = max(self.article_list_max_count, self.article_list_count)
        self.article_list_any_match = self.article_list_any_match or bool(result["articles"])
        self.last_article_listing = [
            {key: row[key] for key in ("article_id", "author", "title", "published_at")}
            for row in result["articles"][:50]
        ]
        self.article_list_history.append({
            "model_requested": model_requested,
            "effective_request": {
                key: requested[key]
                for key in ("author", "published_after", "published_before", "limit")
                if key in requested
            },
            "count": self.article_list_count,
            "article_ids": [row["article_id"] for row in self.last_article_listing],
            "scope_key": scope_key,
            "scope_control": scope_control,
        })
        requested_authors = self._named_authors_in_question()
        if len(requested_authors) >= 2:
            checked_authors = {
                str(item.get("effective_request", {}).get("author") or "").strip().casefold()
                for item in self.article_list_history
                if str(item.get("effective_request", {}).get("author") or "").strip()
            }
            remaining_authors = [
                author for author in requested_authors
                if author.casefold() not in checked_authors
            ]
            author_coverage = {
                "requested_authors": requested_authors,
                "checked_author_folders": [
                    author for author in requested_authors
                    if author.casefold() in checked_authors
                ],
                "remaining_author_folders": remaining_authors,
                "complete": not remaining_authors,
                "instruction": (
                    "Check every explicitly named author folder before comparing their views."
                    if remaining_authors
                    else "All explicitly named author folders have been checked."
                ),
            }
            result["author_coverage"] = author_coverage
            self.article_list_history[-1]["author_coverage"] = author_coverage
        catalog_hash = hashlib.sha256(json.dumps(
            [{"article_id": row["article_id"], "published_at": row["published_at"]}
             for row in self.last_article_listing],
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        catalog_ref = self.evidence_ledger.add_reference(
            source_type="article_catalog",
            provenance="observed_local_index",
            source_ref=f"article_catalog:{self.company_id}:{scope_key}",
            content_hash=catalog_hash,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            excerpt=f"Author/date scope returned {self.article_list_count} indexed articles; metadata only.",
            metadata={
                "author": requested.get("author") or "",
                "published_after": requested.get("published_after") or "",
                "published_before": requested.get("published_before") or "",
                "result_count": self.article_list_count,
            },
        )
        result["evidence_id"] = catalog_ref.evidence_id
        self.article_list_history[-1]["evidence_id"] = catalog_ref.evidence_id
        return result

    def _remaining_named_authors(self, *, current_author: str) -> list[str]:
        """Keep an empty folder from terminating an unchecked multi-author task."""
        named = self._named_authors_in_question()
        checked = {
            str((item.get("effective_request") or {}).get("author") or "").strip().casefold()
            for item in self.article_list_history
        }
        checked.add(current_author.casefold())
        return [author for author in named if author.casefold() not in checked]

    def _named_authors_in_question(self) -> list[str]:
        """Return corpus author folders explicitly named in the current query."""
        question = self.question.casefold()
        return [
            author for item in self.corpus.list_authors()
            if (author := str(item.get("author") or "").strip())
            and author.casefold() in question
        ]

    def _unread_listed_article_ids(self) -> list[str]:
        """Return article IDs listed in this turn that were not read yet."""
        read_ids = {
            str(ref.get("article_id") or "").strip()
            for ref in self.read_refs
            if str(ref.get("article_id") or "").strip()
        }
        candidates: list[str] = []
        seen: set[str] = set()
        for listing in self.article_list_history:
            for article_id in listing.get("article_ids") or []:
                normalized = str(article_id or "").strip()
                if normalized and normalized not in read_ids and normalized not in seen:
                    seen.add(normalized)
                    candidates.append(normalized)
        return candidates

    @staticmethod
    def _article_scope_key(requested: dict[str, Any]) -> str:
        """Return a stable key for the evidence scope, excluding page size."""
        return "|".join(
            str(requested.get(key) or "").strip()
            for key in ("author", "published_after", "published_before")
        )

    def wiki_search(self, query: str, top_k: int = 5):
        from policy_impact.mcp_server.tools_company import company_wiki_search

        return company_wiki_search(self.company_id, query, top_k=top_k)

    def search_memory(self, query: str, top_k: int = 5):
        """Search only this workspace's persisted memory; never accept a path."""
        self._ensure_research_open()
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("memory_search query must be non-empty text up to 2000 characters")
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("memory_search top_k must be an integer from 1 to 20")
        return self.memory_store.search_memory(query=query, top_k=top_k)

    def research_artifact(self, **kwargs):
        if (kwargs.get("operation") not in {"get", "list"}
                and kwargs.get("schema_version") != "finance.research_artifact.v2"):
            self._ensure_research_open()
        return self.artifacts.call(**kwargs)

    def synthesize(self, **kwargs):
        self._ensure_research_open()
        return self.synthesis.call(**kwargs)

    def verify(self, **kwargs):
        self._ensure_research_open()
        return self.verification.call(**kwargs)

    def _verification_source_detail(self, evidence_id: str):
        web_text = self.web.opened_text(evidence_id)
        if web_text:
            return {"text": web_text[:14000]}
        metric = next((item for item in self.market.metric_reads
                       if item.get("evidence_id") == evidence_id), None)
        if metric is not None:
            return metric
        official = next((item for item in self.article_gateway.external_results
                         if item.get("evidence_id") == evidence_id), None)
        return {"text": official["text"]} if official else None

    def read_article(self, **kwargs):
        self._ensure_research_open()
        article_id = str(kwargs.get("article_id") or "")
        if article_id.startswith("evidence_"):
            observed = next((
                row for row in self.evidence_bundle().get("evidence") or []
                if row.get("evidence_id") == article_id
                and row.get("source_type") == "author_article"
                and row.get("article_id")
            ), None)
            if observed is None:
                raise ValueError("article.read needs an article ID or an observed article EvidenceRef")
            kwargs["article_id"] = observed["article_id"]
        self._check_article_scope(kwargs.get("article_id"))
        if self.allowed_article_ids is not None and kwargs.get("article_id") not in self.allowed_article_ids:
            raise PermissionError("article is outside the delegated batch")
        return self.article_gateway.read(**kwargs)

    def claims(self, **kwargs):
        self._ensure_research_open()
        self._check_article_scope(kwargs.get("article_id"))
        if self.allowed_article_ids is not None and kwargs.get("article_id") not in self.allowed_article_ids:
            raise PermissionError("article is outside the delegated batch")
        return self.article_gateway.claims(**kwargs)

    def study(self, **kwargs):
        self._ensure_research_open()
        self._check_article_scope(kwargs.get("article_id"))
        if self.allowed_article_ids is not None and kwargs.get("article_id") not in self.allowed_article_ids:
            raise PermissionError("article is outside the delegated batch")
        return self.article_study.call(**kwargs)

    def _check_article_scope(self, article_id: Any) -> None:
        allowed_dates = self.source_scope["explicit_article_dates"]
        if not allowed_dates:
            return
        article = self.corpus.get_article(str(article_id or ""))
        if article is not None and article["published_at"] not in allowed_dates:
            raise PermissionError(
                "article is outside the dates explicitly selected by the user: "
                + ", ".join(allowed_dates)
            )

    def discover(self, **kwargs):
        self._ensure_research_open()
        return self.article_gateway.discover(**kwargs)

    def fetch(self, **kwargs):
        self._ensure_research_open()
        return self.article_gateway.fetch(**kwargs)

    def read_report(self, **kwargs):
        allowed = {"report_id", "section", "symbol", "document_id", "offset", "limit"}
        if set(kwargs) - allowed or kwargs.get("report_id") not in self.report_ids:
            raise PermissionError("report was not authorized by this conversation's context")
        result = read_report(self.company_id, **kwargs)
        self.chat_gateway.read_sections.add(kwargs.get("section", "sources"))
        self.chat_gateway.observations.append(result)
        return result

    def analyze_market(self, query: str | None = None):
        self._ensure_research_open()
        return self.chat_gateway._analyze(query)

    def close_research(self) -> None:
        """Prevent post-review browsing from consuming the final-answer budget."""
        self.research_locked = True

    def _ensure_research_open(self) -> None:
        if self.research_locked:
            raise PermissionError(
                "finance evidence collection is closed after controlled review; return final"
            )

    def final_guard(self, question: str, output: dict) -> str | None:
        return self.chat_gateway.final_guard(question, output)
