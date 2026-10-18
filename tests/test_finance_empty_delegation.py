"""Regression coverage for empty-evidence finance delegation gates."""

from __future__ import annotations

import json

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.answer_audit import AnswerAudit
from policy_impact.harness.agent_runtime import (
    AgentRuntime,
    _contains_no_article_notice,
    _explicit_article_authors,
)
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore


def _verification_checkpoint():
    return {"type": "tool_call", "tool_name": "research.checkpoint", "arguments": {
        "checks": [{
            "claim": "The scripted review has a bounded source scope",
            "decision": "check", "outcome": "inconclusive",
            "method": "Scripted source-scope review", "evidence_refs": [],
            "reason": "This test checks delegation and empty-source handling",
            "gap": "No independent factual verification is performed in this fixture",
        }],
    }}


def test_finance_review_roles_do_not_treat_main_analysis_as_analyst_request():
    assert AgentRuntime._finance_review_roles(
        "请分析最近一个月的美股走势，并调用subagent核验准确性"
    ) == ("verifier",)
    assert AgentRuntime._finance_review_roles(
        "请从分析和质疑两个角度判断黄金走势"
    ) == ("skeptic", "analyst")


def test_verifier_is_not_misclassified_as_conditional_if_language():
    assert not AgentRuntime._conditional_review_language(
        "Call verifier to independently verify the source."
    )
    assert not AgentRuntime._conditional_review_language(
        "Call verifier explicitly; if needed, use skeptic for a second challenge."
    )


def test_empty_article_notice_accepts_model_wording_seen_in_real_trace():
    assert _contains_no_article_notice(
        "当前时间范围没有可用的本地文章，无法总结该时间段的作者观点。"
    )
    assert _contains_no_article_notice(
        "There are no local articles available for the requested period."
    )


def test_article_scope_is_based_on_explicit_author_folders_not_comparison_words():
    authors = [{"author": "TT"}, {"author": "培风客"}]
    assert _explicit_article_authors("请总结这篇文章的主要观点", authors) == []
    assert _explicit_article_authors("TT 和培风客分别怎么说黄金", authors) == ["TT", "培风客"]


def test_relative_empty_article_window_terminates_without_budget_spin(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    article_root = tmp_path / "articles"
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(article_root))
    company_dir = tmp_path / "data" / "companies" / "finance_observer"
    (company_dir / "wiki").mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance test"}),
        encoding="utf-8",
    )
    (article_root / "TT" / "articles").mkdir(parents=True)
    (article_root / "TT" / "articles" / "1.md").write_text(
        "# Old article\n2026-07-01\nAn article outside the current relative window.\n",
        encoding="utf-8",
    )
    (article_root / "current_holdings.md").write_text(
        '```json\n{"as_of":"2026-10-01","currency":"CNY","approximate":true,"positions":[{"name":"test","amount":0}]}\n```\n',
        encoding="utf-8",
    )
    (article_root / "research_policy.md").write_text("# Research policy\n", encoding="utf-8")
    PolicyMemoryStore("finance_observer").seed_model_configs()

    runtime = AgentRuntime(
        company_id="finance_observer",
        model_adapter=ScriptedModelAdapter([
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {
                    "operation": "create",
                    "goals": ["Check the requested date and author scope", "Report the evidence result truthfully"],
                },
            },
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {"operation": "update", "goal_id": 1, "status": "done"},
            },
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {"operation": "update", "goal_id": 2, "status": "done"},
            },
            {
                "type": "tool_call",
                "tool_name": "article.list",
                "arguments": {"author": "TT", "limit": 100},
            },
            {
                "type": "tool_call",
                "tool_name": "article.list",
                "arguments": {"author": "TT", "limit": 100},
            },
        ]),
    )
    session_id = runtime.store.create_chat_session(
        title="empty relative window", mode="auto", model_id="deepseek-v4-flash", active_skill_id=""
    )
    result = runtime.run_turn(
        session_id=session_id,
        message="请总结 TT 最近一个月的文章；没有文章就明确说明，不要扩大日期范围。",
        mode="auto",
        model_id="deepseek-v4-flash",
    )

    steps = runtime.store.list_agent_steps(result["run_id"])
    assert "没有可用的本地文章" in result["answer"]
    assert sum(step["step_type"] == "model_call" for step in steps) == 5
    assert any(step["step_type"] == "research_scope_termination" for step in steps)
    assert any(
        step["step_type"] == "run_stopped"
        and step["output_payload"].get("stop_reason") == "deterministic_complete"
        for step in steps
    )


def test_repeated_empty_author_scope_stops_runtime_without_budget_spin(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    article_root = tmp_path / "articles"
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(article_root))
    company_dir = tmp_path / "data" / "companies" / "finance_observer"
    (company_dir / "wiki").mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance test"}),
        encoding="utf-8",
    )
    (article_root / "known" / "articles").mkdir(parents=True)
    (article_root / "known" / "articles" / "2026-09-30.md").write_text(
        "# Known article\n2026-09-30\nA source outside the requested author scope.\n",
        encoding="utf-8",
    )
    (article_root / "current_holdings.md").write_text(
        '```json\n{"as_of":"2026-10-01","currency":"CNY","approximate":true,'
        '"positions":[{"name":"cash","amount":0}]}\n```\n',
        encoding="utf-8",
    )
    (article_root / "research_policy.md").write_text("# Research policy\n", encoding="utf-8")
    PolicyMemoryStore("finance_observer").seed_model_configs()

    adapter = ScriptedModelAdapter([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Check the requested author", "Report the evidence boundary"],
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "missing_author"}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "missing_author"}},
    ])
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)
    session_id = runtime.store.create_chat_session(
        title="repeated empty author", mode="auto", model_id="deepseek-v4-flash", active_skill_id=""
    )
    result = runtime.run_turn(
        session_id=session_id,
        message="Summarize articles by missing_author; do not broaden the date range.",
        mode="auto",
        model_id="deepseek-v4-flash",
    )

    steps = runtime.store.list_agent_steps(result["run_id"])
    assert "repeated" in result["answer"] or "两次" in result["answer"]
    assert sum(step["step_type"] == "model_call" for step in steps) == 3
    termination = next(
        step for step in steps
        if step["step_type"] == "research_scope_termination"
    )
    assert termination["output_payload"]["reason"] == "repeated_empty_article_scope"
    assert termination["output_payload"]["article_reads"] == 0


def test_empty_article_result_can_delegate_but_review_cannot_invent_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(tmp_path / "articles"))
    company_dir = tmp_path / "data" / "companies" / "finance_observer"
    (company_dir / "wiki").mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance test"}),
        encoding="utf-8",
    )
    (tmp_path / "articles").mkdir()
    (tmp_path / "articles" / "current_holdings.md").write_text(
        '```json\n{"as_of":"2026-10-01","currency":"CNY","approximate":true,'
        '"positions":[{"name":"test position","amount":0}]}\n```\n',
        encoding="utf-8",
    )
    (tmp_path / "articles" / "research_policy.md").write_text(
        "# Finance research test policy\n", encoding="utf-8"
    )
    PolicyMemoryStore("finance_observer").seed_model_configs()

    runtime = AgentRuntime(
        company_id="finance_observer",
        model_adapter=ScriptedModelAdapter([
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {
                    "operation": "create",
                    "goals": ["Check the requested date and author scope", "Report the evidence result truthfully"],
                },
            },
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {"operation": "update", "goal_id": 1, "status": "done"},
            },
            {
                "type": "tool_call",
                "tool_name": "research.plan",
                "arguments": {"operation": "update", "goal_id": 2, "status": "done"},
            },
            {
                "type": "tool_call",
                "tool_name": "article.list",
                "arguments": {
                    "author": "未来作者",
                    "published_after": "2026-12-01",
                    "published_before": "2026-12-31",
                    "limit": 200,
                },
            },
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Review the unavailable article evidence.",
            },
            {
                "type": "final",
                "output": {"counterexamples": [], "notes": ["No article was available to review."]},
            },
            _verification_checkpoint(),
            {
                "type": "final",
                "output": {"answer": "没有找到文章；skeptic 也未取得证据，不能核实作者观点。"},
            },
        ]),
    )
    session_id = runtime.store.create_chat_session(
        title="empty evidence", mode="auto", model_id="deepseek-v4-flash", active_skill_id=""
    )
    result = runtime.run_turn(
        session_id=session_id,
        message=(
            "查找未来作者在2026年12月的文章，并调用 skeptic Subagent 复核；"
            "没有文章就明确说明，不要编造。"
        ),
        mode="auto",
        model_id="deepseek-v4-flash",
    )

    steps = runtime.store.list_agent_steps(result["run_id"])
    assert result["answer"].startswith("没有找到文章")
    assert any(step["step_type"] == "subagent_delegate" for step in steps)
    assert not any("delegate_failed" in json.dumps(step, ensure_ascii=False) for step in steps)
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    evidence = audit["output_payload"]["evidence_bundle"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["source_type"] == "article_catalog"
    assert evidence[0]["metadata"]["result_count"] == 0


def test_requested_verifier_and_skeptic_require_two_model_delegates(tmp_path, monkeypatch):
    monkeypatch.setattr(AnswerAudit, "call", lambda self, answer, **kwargs: {
        "status": "accept", "issues": [], "checks": [],
        "reviewed_claim_count": 0, "coverage_complete": True,
        "review_scope": "scripted_test_stub",
    })
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    article_root = tmp_path / "articles"
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(article_root))
    company_dir = tmp_path / "data" / "companies" / "finance_observer"
    (company_dir / "wiki").mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance test"}),
        encoding="utf-8",
    )
    (article_root / "TT" / "articles").mkdir(parents=True)
    (article_root / "TT" / "articles" / "1.md").write_text(
        chr(10).join(["# Gold view", "2026-07-12", "The author expects gold to rise.", ""]),
        encoding="utf-8",
    )
    (article_root / "current_holdings.md").write_text(
        chr(10).join([
            "```json",
            '{"as_of":"2026-10-01","currency":"CNY","approximate":true,'
            '"positions":[{"name":"test position","amount":0}]}',
            "```", "",
        ]),
        encoding="utf-8",
    )
    (article_root / "research_policy.md").write_text("# Test policy" + chr(10), encoding="utf-8")
    corpus = ArticleCorpus("finance_observer")
    corpus.sync()
    article_id = corpus.list_articles(author="TT")[0]["article_id"]
    PolicyMemoryStore("finance_observer").seed_model_configs()
    citation = "TT/articles/1.md:L3"
    runtime = AgentRuntime(
        company_id="finance_observer",
        model_adapter=ScriptedModelAdapter([
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "create",
                "goals": ["Read the requested article", "Complete verifier and skeptic review"],
                "scope": {"authors": ["TT"]},
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 1, "status": "done",
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 2, "status": "done",
            }},
            {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
            {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": article_id}},
            {"type": "delegate", "role": "verifier", "task": "Check the sourced author view."},
            {"type": "final", "output": {"verdicts": []}},
            {"type": "delegate", "role": "skeptic", "task": "Challenge the sourced author view."},
            {"type": "final", "output": {"counterexamples": [], "notes": ["No contradiction in the read lines."]}},
            _verification_checkpoint(),
            {"type": "final", "output": {"answer": f"Author view: {citation}; not independently verified."}},
        ]),
    )
    session_id = runtime.store.create_chat_session(
        title="role coverage", mode="auto", model_id="deepseek-v4-flash", active_skill_id=""
    )
    result = runtime.run_turn(
        session_id=session_id,
        message="Read TT's article and ask verifier and skeptic to review the source.",
        mode="auto",
        model_id="deepseek-v4-flash",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    roles = [step["output_payload"]["role"] for step in steps if step["step_type"] == "subagent_delegate"]
    assert roles == ["verifier", "skeptic"]
    assert citation in result["answer"]
