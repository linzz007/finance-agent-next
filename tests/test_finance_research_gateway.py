from pathlib import Path

import pytest

from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.research_gateway import FinanceResearchGateway, infer_source_scope


@pytest.fixture(autouse=True)
def isolated_finance_store(tmp_path, monkeypatch):
    from policy_impact.company_wiki import loader

    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(tmp_path / "articles"))


def _corpus(tmp_path: Path) -> ArticleCorpus:
    root = tmp_path / "articles"
    for author, date, body in (
        ("TT", "2026-09-28", "Gold and real yields."),
        ("培风客", "2026-09-20", "Silver supply matters."),
    ):
        folder = root / author / "articles"
        folder.mkdir(parents=True)
        (folder / f"{date}-note.md").write_text(
            f"# {author} note\n{date}\n{body}\n", encoding="utf-8"
        )
    return ArticleCorpus("finance_observer", root)


def test_source_scope_distinguishes_date_window_from_exact_article_dates():
    assert infer_source_scope(
        "只基于作者在 2026-09-12、2026-09-17、2026-10-01 这三篇本地文章，不联网"
    ) == {
        "local_only": True,
        "explicit_article_dates": ["2026-09-12", "2026-09-17", "2026-10-01"],
    }
    assert infer_source_scope(
        "概括作者从 2026-09-12 到 2026-10-01 的文章"
    )["explicit_article_dates"] == []


def test_local_only_and_exact_date_scope_enforced_by_gateway(tmp_path):
    corpus = _corpus(tmp_path)
    extra = tmp_path / "articles" / "TT" / "articles" / "2026-09-24-note.md"
    extra.write_text("# Intervening note\n2026-09-24\nDo not read this.\n", encoding="utf-8")
    gateway = FinanceResearchGateway(
        "finance_observer", set(),
        question="只基于 TT 2026-09-20、2026-09-28 这两篇本地文章，不联网",
        corpus=corpus,
    )
    listing = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert [row["published_at"] for row in listing["articles"]] == ["2026-09-28"]
    middle_id = next(row["article_id"] for row in corpus.list_articles(author="TT")
                     if row["published_at"] == "2026-09-24")
    with pytest.raises(PermissionError, match="dates explicitly selected"):
        gateway.call("chat_agent_loop", "article.read", article_id=middle_id)
    with pytest.raises(PermissionError, match="user source boundary"):
        gateway.call("chat_agent_loop", "web.search", query="gold")
    with pytest.raises(PermissionError, match="user source boundary"):
        gateway.call("chat_agent_loop", "market.snapshot", symbols=["xauusd"])
    assert not gateway.web_reads and not gateway.market_reads


def test_corpus_browses_author_folders_and_date_bounds_without_body(tmp_path):
    corpus = _corpus(tmp_path)
    corpus.sync()

    assert [item["author"] for item in corpus.list_authors()] == ["TT", "培风客"]
    rows = corpus.list_articles(author="TT", published_after="2026-09-25")
    assert len(rows) == 1
    assert "content" not in rows[0]
    assert corpus.list_articles(published_before="2026-09-25")[0]["author"] == "培风客"


def test_research_coverage_records_agent_decision_with_observed_evidence(tmp_path):
    corpus = _corpus(tmp_path)
    corpus.sync()
    events = []
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="概括 TT 的观点", corpus=corpus,
        on_coverage_record=events.append,
    )
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id)
    coverage = gateway.call("chat_agent_loop", "research.coverage", aspects=[{
        "question": "TT 关注什么变量", "status": "covered",
        "evidence_refs": [read["evidence_id"]], "contrary_evidence_refs": [], "gap": "",
    }], decision="synthesize", reason="已读正文可支持该篇的关注变量，样本范围需披露。",
       sample_scope="TT 2026-09-28，一篇文章")
    assert events == [coverage]
    assert coverage["aspects"][0]["evidence_refs"] == [read["evidence_id"]]
    with pytest.raises(ValueError, match="observed evidence|unknown|evidence"):
        gateway.call("chat_agent_loop", "research.coverage", aspects=[{
            "question": "TT 关注什么变量", "status": "covered",
            "evidence_refs": ["evidence_000000000000000000000000"], "gap": "",
        }], decision="synthesize", reason="不能使用未观察到的证据作为覆盖依据。")


def test_research_coverage_can_record_more_than_twenty_observed_sources():
    from policy_impact.domains.finance.evidence import EvidenceLedger
    from policy_impact.domains.finance.research_coverage import ResearchCoverage

    ledger = EvidenceLedger()
    refs = [ledger.add_reference(
        source_type="author_article", provenance="article.read",
        source_ref=f"TT/articles/{index}.md",
    ).evidence_id for index in range(25)]
    recorded = ResearchCoverage(ledger).record(
        aspects=[{"question": "跨篇观点", "status": "covered", "evidence_refs": refs}],
        decision="synthesize", reason="代表性样本已覆盖各阶段，下一步综合分析。",
    )
    assert recorded["aspects"][0]["evidence_refs"] == refs


def test_research_gateway_exposes_folder_browse_and_one_article_read(tmp_path):
    corpus = _corpus(tmp_path)
    gateway = FinanceResearchGateway(
        "finance_observer",
        set(),
        question="Compare the latest author viewpoints.",
        analyze=lambda query: {},
        corpus=corpus,
        enable_analysis=False,
    )

    listing = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert listing["articles"][0]["author"] == "TT"
    article_id = listing["articles"][0]["article_id"]
    body = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=10)
    assert body["author"] == "TT"
    assert gateway.read_refs[0]["article_id"] == article_id
    assert gateway.call(
        "chat_agent_loop", "article.read", article_id=body["evidence_id"], limit=10,
    )["article_id"] == article_id
    with pytest.raises(ValueError, match="observed article EvidenceRef"):
        gateway.call(
            "chat_agent_loop", "article.read",
            article_id="evidence_000000000000000000000000",
        )
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "article.search", query="gold")
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "finance.analyze", query="latest gold")


def test_multi_author_listing_reports_remaining_author_folder(tmp_path):
    question = "TT 和培风客在已有文章中对黄金的看法有什么变化"
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question=question, corpus=_corpus(tmp_path)
    )

    first = gateway.call("chat_agent_loop", "article.list", author="TT")
    coverage = first["author_coverage"]
    assert coverage["requested_authors"] == ["TT", "培风客"]
    assert coverage["checked_author_folders"] == ["TT"]
    assert coverage["remaining_author_folders"] == ["培风客"]
    assert coverage["complete"] is False

    second = gateway.call("chat_agent_loop", "article.list", author="培风客")
    assert second["author_coverage"]["complete"] is True


def test_empty_author_scope_allows_switching_then_stops_repeated_scope(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Compare author folders", corpus=_corpus(tmp_path)
    )

    first = gateway.call("chat_agent_loop", "article.list", author="missing_author")
    assert first["articles"] == []
    assert first["evidence_id"] == gateway.article_list_history[-1]["evidence_id"]
    catalog_ref = gateway.evidence_ledger.get(first["evidence_id"])
    assert catalog_ref.source_type == "article_catalog"
    assert catalog_ref.metadata["result_count"] == 0
    assert first["scope_control"]["repeat_count"] == 1
    assert "_runtime_terminal" not in first

    # The model can still make progress by selecting a different author.
    second_author = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert second_author["articles"]
    assert "_runtime_terminal" not in second_author

    repeated = gateway.call("chat_agent_loop", "article.list", author="missing_author")
    assert repeated["articles"] == []
    assert repeated["scope_control"]["repeat_count"] == 2
    assert repeated["_runtime_nudge"]["kind"] == "repeated_empty_scope_with_unread_candidates"
    assert gateway.article_list_history[-1]["scope_key"] == "missing_author||"

    gateway.call(
        "chat_agent_loop",
        "article.read",
        article_id=second_author["articles"][0]["article_id"],
        limit=10,
    )
    exhausted = gateway.call("chat_agent_loop", "article.list", author="missing_author")
    assert exhausted["_runtime_terminal"]["kind"] == "repeated_empty_article_scope"


def test_empty_author_scope_does_not_block_another_empty_author(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Compare author folders", corpus=_corpus(tmp_path)
    )
    first = gateway.call("chat_agent_loop", "article.list", author="missing_one")
    second = gateway.call("chat_agent_loop", "article.list", author="missing_two")
    assert first["scope_control"]["repeat_count"] == 1
    assert second["scope_control"]["repeat_count"] == 1
    assert "_runtime_terminal" not in second


def test_repeated_empty_scope_does_not_end_unchecked_named_author(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer",
        set(),
        question="比较 TT 和培风客最近一个月的观点",
        corpus=_corpus(tmp_path),
    )
    gateway.article_window = ("2026-10-01", "2026-10-02")

    gateway.call("chat_agent_loop", "article.list", author="TT")
    repeated = gateway.call("chat_agent_loop", "article.list", author="TT")

    assert "_runtime_terminal" not in repeated
    assert repeated["scope_control"]["remaining_named_authors"] == ["培风客"]

    # One recovery turn is enough to move to the remaining author.  If the
    # model keeps retrying the same effective scope, stop that branch instead
    # of consuming the entire model budget.
    exhausted = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert exhausted["_runtime_terminal"]["kind"] == "repeated_empty_author_scope_with_remaining_authors"


def test_repeated_empty_scope_nudges_toward_unread_articles(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer",
        set(),
        question="比较 TT 最近一个月的观点",
        corpus=_corpus(tmp_path),
    )
    gateway.article_window = ("2026-10-01", "2026-10-02")

    listed = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert listed["articles"] == []
    gateway.article_window = ("2026-09-01", "2026-09-30")
    nonempty = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert nonempty["articles"]
    gateway.article_window = ("2026-10-01", "2026-10-02")
    gateway.call("chat_agent_loop", "article.list", author="TT")
    repeated = gateway.call("chat_agent_loop", "article.list", author="TT")

    assert "_runtime_nudge" in repeated
    assert repeated["_runtime_nudge"]["unread_article_ids"] == [
        nonempty["articles"][0]["article_id"]
    ]


def test_relative_empty_author_scope_allows_another_author_to_progress(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Compare authors from last month", corpus=_corpus(tmp_path)
    )
    first = gateway.call("chat_agent_loop", "article.list", author="missing_author")
    second = gateway.call("chat_agent_loop", "article.list", author="TT")
    assert first["articles"] == []
    assert second["articles"]
    assert "_runtime_terminal" not in second


def test_gateway_allows_public_discovery_without_making_headlines_evidence(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer",
        set(),
        question="Find public sources.",
        analyze=lambda query: {},
        enable_analysis=False,
    )
    # Discovery itself is network-dependent; permission is the contract tested
    # here, while the source connector owns transport and SSRF checks.
    assert gateway.permission_engine.decide("chat_agent_loop", "source.discover").decision == "allow"
    assert gateway.permission_engine.decide("chat_agent_loop", "source.fetch").decision == "allow"


def test_research_plan_is_run_local_and_not_available_to_subagents(tmp_path):
    gateway = FinanceResearchGateway("finance_observer", set(), question="Compare articles")
    assert gateway.call("chat_agent_loop", "research.plan", operation="get")["goals"] == []
    created = gateway.call(
        "chat_agent_loop", "research.plan", operation="create",
        goals=["Read candidate articles", "Compare changes over time"],
        scope={"authors": ["TT", "培风客"], "topics": ["gold"]},
    )
    assert created["pending_count"] == 2
    assert created["scope"] == {"authors": ["TT", "培风客"], "topics": ["gold"]}
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "research.plan", operation="get")
    with pytest.raises(ValueError, match="submit the final answer"):
        gateway.call("chat_agent_loop", "research.plan", operation="update", goal_id=3, status="done")
    with pytest.raises(ValueError):
        gateway.call("chat_agent_loop", "research.plan", operation="update", goal_id=2, status="blocked")
    gateway.call("chat_agent_loop", "research.plan", operation="update", goal_id="1", status="done")
    with pytest.raises(ValueError, match="goal_id must identify"):
        gateway.call("chat_agent_loop", "research.plan", operation="update", goal_id="1.0", status="done")
    snapshot = gateway.call(
        "chat_agent_loop", "research.plan", operation="update",
        goal_id=2, status="blocked", note="No primary market source was retrieved",
    )
    assert snapshot["pending_count"] == 0
    assert snapshot["goals"][1]["status"] == "blocked"
    assert FinanceResearchGateway("finance_observer", set(), question="New turn").plan.exists is False


def test_research_plan_rejects_invalid_scope_without_mutating_goals(tmp_path):
    gateway = FinanceResearchGateway("finance_observer", set(), question="Compare articles")
    with pytest.raises(ValueError, match="scope.date_range.*dict_type"):
        gateway.call(
            "chat_agent_loop", "research.plan", operation="create",
            goals=["Find articles", "Compare views"],
            scope={"date_range": "recent"},
        )
    assert gateway.call("chat_agent_loop", "research.plan", operation="get")["goals"] == []
    created = gateway.call(
        "chat_agent_loop", "research.plan", operation="create",
        goals=["Find articles", "Compare views"],
        scope={"date_range": {"published_after": "2026-09-01"}},
    )
    with pytest.raises(ValueError, match="scope.date_range.*dict_type"):
        gateway.call(
            "chat_agent_loop", "research.plan", operation="update", goal_id=1,
            status="done", scope={"date_range": "recent"},
        )
    assert gateway.call("chat_agent_loop", "research.plan", operation="get") == created


@pytest.mark.parametrize("delivered", [False, True])
def test_plan_terminal_defaults_do_not_certify_verification_and_persist(tmp_path, delivered):
    from policy_impact.domains.finance.research_plan import ResearchPlan

    run_id = "run_terminal123"
    path = tmp_path / "runs" / run_id / "research_plan.json"
    plan = ResearchPlan(session_id="session", run_id=run_id, state_path=path)
    plan.call(operation="create", goals=["Read sources", "Compare views", "Verify argument"])
    plan.call(operation="update", goal_id=1, status="done", note="Sources read")
    plan.call(operation="update", goal_id=2, status="blocked", note="Missing source")
    progress = "Some checks already performed".ljust(240, ".")
    plan.call(operation="update", goal_id=3, status="pending", note=progress)
    snapshot = plan.finish_delivery(delivered=delivered, reason="model_error")
    assert snapshot["delivery_mode"] == ("qualified" if delivered else "source_only")
    assert snapshot["verification_status"] == ("partial" if delivered else "failed")
    assert snapshot["answer_audit_coverage"] == ("partial" if delivered else "failed")
    assert snapshot["evidence_coverage"] == ("partial" if delivered else "uncovered")
    assert snapshot["goals"][0]["status"] == "done"
    assert snapshot["goals"][0]["note"] == "Sources read"
    assert snapshot["goals"][1]["note"] == "Missing source"
    unfinished = snapshot["goals"][2]
    assert unfinished["status"] == "blocked"
    assert "Some checks already performed" in unfinished["note"]
    assert "model_error" in unfinished["note"]
    assert unfinished["interruption"] == {"reason": "model_error", "previous_note": progress}
    assert len(unfinished["note"]) <= 240
    assert snapshot["research_pending_count"] == 0
    restored = ResearchPlan(session_id="session", run_id=run_id, state_path=path)
    assert restored.call(operation="load", run_id=run_id) == snapshot


@pytest.mark.parametrize("delivered", [False, True])
@pytest.mark.parametrize("mode", ["full", "qualified", "source_only"])
def test_every_terminal_delivery_closes_unrecorded_goals_without_certifying_completion(
    tmp_path, delivered, mode,
):
    from policy_impact.domains.finance.research_plan import ResearchPlan

    run_id = "run_close_goals"
    path = tmp_path / "runs" / run_id / "research_plan.json"
    plan = ResearchPlan(session_id="session", run_id=run_id, state_path=path)
    plan.call(operation="create", goals=["Read sources", "Check reasoning"])
    plan.call(operation="update", goal_id=1, status="done", note="Read full source")
    plan.call(operation="update", goal_id=2, status="pending", note="Some checks remain")

    snapshot = plan.finish_delivery(delivered=delivered, delivery_mode=mode)

    assert snapshot["delivery_status"] == ("done" if delivered else "blocked")
    assert snapshot["delivery_mode"] == mode
    assert snapshot["goals"][0]["status"] == "done"
    assert snapshot["goals"][0]["note"] == "Read full source"
    assert snapshot["goals"][1]["status"] == "blocked"
    assert snapshot["goals"][1]["interruption"]["previous_note"] == "Some checks remain"
    assert snapshot["goals"][1]["interruption"]["reason"]
    assert snapshot["research_pending_count"] == 0
    assert snapshot["pending_count"] == 0
    assert all(goal["status"] != "pending" for goal in snapshot["goals"])
    restored = ResearchPlan(session_id="session", run_id=run_id, state_path=path)
    assert restored.call(operation="load", run_id=run_id) == snapshot


def test_finance_main_loop_requires_plan_before_any_research_tool(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Which TT articles were published?",
        require_plan_before_tools=True,
    )
    with pytest.raises(ValueError, match="research_plan_required"):
        gateway.call("chat_agent_loop", "article.list", author="TT")
    assert gateway.article_list_attempted is False
    gateway.call(
        "chat_agent_loop", "research.plan", operation="create",
        goals=["Find TT articles", "Report dates and titles"],
    )
    assert gateway.call("chat_agent_loop", "article.list", author="TT")["articles"] == []


def test_memory_search_is_a_real_workspace_scoped_gateway_tool(tmp_path):
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Recall my gold focus", child_roles=("analyst",)
    )
    gateway.memory_store.save_memory(
        "finance_observer", "preference", "User focuses on gold and rates.", importance=0.9
    )

    results = gateway.call("chat_agent_loop", "memory_search", query="gold", top_k=5)

    assert results and results[0]["content"] == "User focuses on gold and rates."
    assert gateway.permission_engine.decide("chat_agent_loop", "memory_search").decision == "allow"
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "memory_search", query="gold", top_k=5)
    assert gateway.calls[-1].status == "blocked"
    assert gateway.calls[-1].policy_decision == "deny"


def test_article_filters_and_file_boundary_are_enforced(tmp_path):
    corpus = _corpus(tmp_path)
    outside = tmp_path / "private.md"
    outside.write_text("private material", encoding="utf-8")
    (corpus.root / "TT" / "articles" / "nested").mkdir()
    (corpus.root / "TT" / "articles" / "nested" / "hidden.md").write_text(
        "# Hidden\n2026-09-29\nprivate material", encoding="utf-8"
    )
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Compare authors", corpus=corpus
    )

    selected = gateway.call(
        "chat_agent_loop", "article.list", author="TT",
        published_after="2026-09-28", published_before="2026-09-28",
    )["articles"]
    assert len(selected) == 1
    assert selected[0]["author"] == "TT"
    assert selected[0]["published_at"] == "2026-09-28"
    assert not gateway.call(
        "chat_agent_loop", "article.list", author="TT",
        published_after="2026-09-29",
    )["articles"]
    assert not gateway.call("chat_agent_loop", "article.list", author="../")['articles']
    with pytest.raises(ValueError):
        gateway.call("chat_agent_loop", "article.list", published_after="2026/09/28")
    with pytest.raises(ValueError):
        gateway.call("chat_agent_loop", "article.list", published_after="2026-02-30")
    with pytest.raises(ValueError):
        gateway.call(
            "chat_agent_loop", "article.list",
            published_after="2026-09-29", published_before="2026-09-28",
        )
    with pytest.raises(ValueError):
        gateway.call("chat_agent_loop", "article.read", article_id=str(outside))
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "file.read", path=str(outside))
    with pytest.raises(PermissionError):
        gateway.call("chat_agent_loop", "artifact_read", artifact_ref=str(outside))
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "file.read", path=str(outside))
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "report_write", path=str(outside))
    assert "private material" not in str(gateway.call("chat_agent_loop", "article.list"))


def test_delegated_article_batch_cannot_read_other_indexed_article(tmp_path):
    corpus = _corpus(tmp_path)
    corpus.sync()
    allowed = corpus.list_articles(author="TT")[0]["article_id"]
    other = corpus.list_articles()[1]["article_id"]
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Read TT", corpus=corpus,
        child_roles=("analyst",), allowed_article_ids={allowed},
    )
    listed = gateway.call("subagent.analyst", "article.list")["articles"]
    assert [row["article_id"] for row in listed] == [allowed]
    observed = gateway.call("subagent.analyst", "article.read", article_id=allowed)
    assert observed["article_id"] == allowed
    assert gateway.call(
        "subagent.analyst", "article.read", article_id=observed["evidence_id"],
    )["article_id"] == allowed
    with pytest.raises(PermissionError, match="outside the delegated batch"):
        gateway.call("subagent.analyst", "article.read", article_id=other)
