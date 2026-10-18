"""End-to-end contract for tool-led financial chat and independent review."""

from __future__ import annotations

import json
from datetime import date

import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.domains.finance.answer_audit import AnswerAudit
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.harness import agent_runtime as runtime_module
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore

_REAL_ANSWER_AUDIT_CALL = AnswerAudit.call
_REAL_REVIEW_TARGET_SELECTION = AnswerAudit.select_review_targets


def test_failed_finance_synthesis_fallback_surfaces_only_observed_article_claims():
    class Corpus:
        def claims(self, article_id):
            assert article_id == "article-1"
            return [{
                "claim_id": "claim-1",
                "claim_type": "forecast",
                "statement": "作者预计事件前市场叙事仍然有限。",
                "quote": "事件前不会有太明确的叙事和机会。",
                "start_line": 7,
            }]

    class Artifacts:
        def call(self, *, operation, kind=None, artifact_id=None):
            assert operation == "list"
            return {"artifacts": []}

    lines = runtime_module._fallback_article_claim_lines(
        Corpus(), Artifacts(), [{
            "article_id": "article-1",
            "author": "TT",
            "path": r"D:\\AAAcode\\金融\\文章数据\\TT\\articles\\one.md",
            "line_start": 1,
            "line_end": 20,
            "evidence_id": "evidence-1",
        }],
    )

    assert len(lines) == 1
    assert "[forecast]" in lines[0]
    assert "TT/articles/one.md:L7" in lines[0]
    assert "EvidenceRef=evidence-1" in lines[0]
    assert "未独立核验" in lines[0]


@pytest.fixture(autouse=True)
def stub_answer_audit_for_scripted_action_tests(monkeypatch):
    # These tests script main-Agent actions; claim-audit validation has its own
    # focused tests and live-model acceptance runs.
    monkeypatch.setattr(AnswerAudit, "call", lambda self, answer, **kwargs: {
        "status": "accept", "issues": [], "checks": [],
        "reviewed_claim_count": 0, "coverage_complete": True,
        "review_scope": "scripted_test_stub",
    })
    monkeypatch.setattr(AnswerAudit, "select_review_targets", lambda self, answer, **kwargs: {
        "review_targets": [], "selection_reason": "Scripted fixture; metadata completion is tested separately.",
    })


@pytest.mark.parametrize(
    ("answer", "author", "expected"),
    [
        ("培风客：实际读取了10篇文章。", "培风客", 10),
        ("培风客已逐篇读取其中12篇；TT实际读取了3篇。", "培风客", 12),
        ("培风客已逐篇读取其中12篇；TT实际读取了3篇。", "TT", 3),
        ("培风客在窗口内共16篇，本报告实际读取并结构化提炼了9篇，其余7篇未读。", "培风客", 9),
        ("培风客未读的7篇可能影响结论。", "培风客", None),
        ("培风客目录列出16篇，尚待逐篇核查。", "培风客", None),
    ],
)
def test_stated_article_read_count_only_matches_explicit_reads(answer, author, expected):
    assert runtime_module._stated_article_read_count(answer, author) == expected


def test_finance_article_sample_count_checks_distinct_cited_authors():
    refs = [
        {"article_id": "tt-1", "author": "TT", "path": "D:/corpus/TT/articles/one.md"},
        {"article_id": "tt-2", "author": "TT", "path": "D:/corpus/TT/articles/two.md"},
        {"article_id": "pf-1", "author": "培风客", "path": "D:/corpus/培风客/articles/one.md"},
    ]
    cited = "`TT/articles/one.md:L1` `TT/articles/two.md:L1` `培风客/articles/one.md:L1`"
    assert "2 distinct authors" in runtime_module._finance_article_sample_count_issue(
        "样本来自3位作者共3篇文章。 " + cited, refs
    )
    assert runtime_module._finance_article_sample_count_issue(
        "样本来自2位作者共3篇文章。 " + cited, refs
    ) is None
    assert runtime_module._finance_article_sample_count_issue(
        "另外一组来自3位作者共8篇文章。 " + cited, refs
    ) is None


def test_finance_article_read_scope_matches_all_runtime_reads_not_only_citations():
    refs = [
        {"article_id": "tt-1", "author": "TT"},
        {"article_id": "tt-2", "author": "TT"},
        {"article_id": "tt-3", "author": "TT"},
    ]
    issue = runtime_module._finance_article_read_scope_issue(
        "TT 实际读取正文 2 篇。", refs
    )
    assert issue and "article.read ledger contains 3 distinct" in issue
    assert runtime_module._finance_article_read_scope_issue(
        "TT 实际读取正文 3 篇。", refs
    ) is None
    assert runtime_module._finance_article_read_scope_issue(
        "TT 目录共 66 篇。本轮实际读取正文 2 篇。", refs
    )
    assert runtime_module._finance_article_read_scope_issue(
        "TT 目录共 66 篇。本轮实际读取正文 3 篇。", refs
    ) is None


def test_finance_article_claims_need_a_line_citation_before_source_index():
    index = "**本轮已读取的文章证据**\n- `TT/articles/one.md:L12`"
    long_summary = "TT 反复提到美元。" * 60
    assert runtime_module._finance_article_citation_location_issue(
        long_summary + " (`TT/articles/one.md`)\n\n" + index
    )
    assert runtime_module._finance_article_citation_location_issue(
        long_summary + " (`TT/articles/one.md:L12`)\n\n" + index
    ) is None
    assert runtime_module._finance_article_citation_location_issue(
        "TT 反复提到美元。 (`TT/articles/one.md:L12`)"
    ) is None


def test_verifier_web_verdict_requires_a_direct_page_quote_with_claim_numbers():
    claim_ref = "claim_6_wgc_q2_2026_central_bank_289t_62pct"
    source = "World Gold Council: central banks bought 289 tonnes, up 62 percent."
    verdict = {
        "claim_ref": claim_ref, "decision": "pass", "evidence_refs": ["web_1"],
        "reason": "Search result says 289 tonnes and 62 percent.",
    }
    guard = runtime_module._finance_verifier_web_quote_issue
    assert guard({"verdicts": [verdict]}, {"web_1": "Important disclaimers."})
    assert guard({"verdicts": [{**verdict, "supporting_quote": source}]},
                 {"web_1": "Important disclaimers."})
    assert guard({"verdicts": [{**verdict, "supporting_quote": "central banks bought 289 tonnes"}]},
                 {"web_1": source})
    assert guard({"verdicts": [{**verdict, "supporting_quote": source}]}, {"web_1": source}) is None
    assert guard({"verdicts": [{**verdict, "decision": "unverified"}]},
                 {"web_1": "Important disclaimers."}) is None


def test_verifier_web_verdict_accepts_bounded_quotes_from_opened_pages():
    guard = runtime_module._finance_verifier_web_quote_issue
    verdict = {
        "claim_ref": "claim_wgc_289t_62pct", "decision": "pass",
        "evidence_refs": ["web_1"], "reason": "Reported values align.",
        "supporting_quotes": ["Central banks purchased 289 tonnes in Q2.",
                              "This was up 62 percent year-on-year."],
    }
    page = "1: Central banks purchased 289 tonnes in Q2.\n2: This was up 62 percent year-on-year."
    assert guard({"verdicts": [verdict]}, {"web_1": page}) is None
    assert guard({"verdicts": [{**verdict, "supporting_quotes": verdict["supporting_quotes"][:1]}]},
                 {"web_1": page}) is not None
    assert guard({"verdicts": [{**verdict, "supporting_quotes": ["Search snippet says 289 and 62"]}]},
                 {"web_1": page}) is not None
    assert guard({"verdicts": [{**verdict, "supporting_quotes": ["A"] * 4}]},
                 {"web_1": page}) is not None


def test_verifier_web_verdict_counts_a_separate_prose_quote_from_the_opened_page():
    guard = runtime_module._finance_verifier_web_quote_issue
    page = ("1: Central banks made significant gold purchases in Q2 (289t).\n"
            "2: Table 1: Quarterly gold supply and demand by sector, tonnes\n"
            "3: Q2'25 | Q2'26 | q/q % change | y/y % change\n"
            "4: Central Banks & Other inst. | 177.9 | 288.9 | 411 | 62")
    verdict = {
        "claim_ref": "claim_1_2026_Q2_approx_289t", "decision": "pass",
        "evidence_refs": ["web_1"], "reason": "Q2 figure rounds to 289 tonnes.",
        "supporting_quote": "Central banks made significant gold purchases in Q2 (289t).",
        "supporting_quotes": [
            "Table 1: Quarterly gold supply and demand by sector, tonnes",
            "Q2'25 | Q2'26 | q/q % change | y/y % change",
            "Central Banks & Other inst. | 177.9 | 288.9 | 411 | 62",
        ],
    }
    assert guard({"verdicts": [verdict]}, {"web_1": page}) is None
    assert "289" in guard(
        {"verdicts": [{**verdict, "supporting_quote": "Unseen 289t quote"}]},
        {"web_1": page},
    )


def test_market_fail_cannot_use_a_window_after_the_authors_claimed_period():
    guard = runtime_module._finance_verifier_market_fail_issue
    quote = "在五月份之后，黄金从4500回到4000。"
    evidence = {"evidence": [
        {"evidence_id": "article_1", "source_type": "author_article",
         "excerpt": quote, "published_at": "2026-07-30"},
        {"evidence_id": "bars_1", "source_type": "market_bar_series",
         "excerpt": "xauusd daily bars 2026-07-24 to 2026-08-05"},
    ]}
    verdict = {"decision": "fail", "evidence_refs": ["bars_1"], "supporting_quote": quote}
    assert "article" in guard({"verdicts": [verdict]}, evidence, author_article_task=True)
    verdict["evidence_refs"].append("article_1")
    assert "starts after" in guard({"verdicts": [verdict]}, evidence, author_article_task=True)
    evidence["evidence"][1]["excerpt"] = "xauusd daily bars 2026-05-01 to 2026-08-05"
    assert guard({"verdicts": [verdict]}, evidence, author_article_task=True) is None
    assert guard({"verdicts": [verdict]}, evidence, author_article_task=False) is None


def test_market_fail_cannot_falsify_a_conditional_price_signal():
    guard = runtime_module._finance_verifier_market_fail_issue
    quote = "白银真正突破73才是大级别看涨的信号"
    evidence = {"evidence": [
        {"evidence_id": "article_1", "source_type": "author_article", "excerpt": quote,
         "published_at": "2026-07-22"},
        {"evidence_id": "bars_1", "source_type": "market_bar_series",
         "excerpt": "xagusd daily bars 2026-07-05 to 2026-08-31"},
    ]}
    verdict = {"claim_ref": "claim_silver_break_73", "decision": "fail",
               "evidence_refs": ["article_1", "bars_1"], "supporting_quote": quote}
    assert "conditional" in guard({"verdicts": [verdict]}, evidence, author_article_task=True)


def test_market_fail_requires_claim_matched_article_passage():
    guard = runtime_module._finance_verifier_market_fail_issue
    quote = "30-year Treasury yield moved above five percent."
    evidence = {"evidence": [
        {"evidence_id": "article_1", "source_type": "author_article", "excerpt": quote,
         "published_at": "2026-07-30"},
        {"evidence_id": "bars_1", "source_type": "market_bar_series",
         "excerpt": "xauusd daily bars 2026-07-05 to 2026-08-31"},
    ]}
    verdict = {"claim_ref": "claim_gold_price_range", "decision": "fail",
               "evidence_refs": ["article_1", "bars_1"], "supporting_quote": quote}
    assert "different topic" in guard({"verdicts": [verdict]}, evidence, author_article_task=True)


def test_verifier_market_verdict_cannot_extend_past_last_bar():
    guard = runtime_module._finance_verifier_market_fail_issue
    evidence = {"evidence": [{
        "evidence_id": "bars_1", "source_type": "market_bar_series",
        "excerpt": "xagusd daily bars 2026-07-22 to 2026-10-01",
    }]}
    verdict = {"claim_ref": "silver_as_of_2026-10-02", "decision": "pass",
               "evidence_refs": ["bars_1"]}
    assert "before the as-of date" in guard(
        {"verdicts": [verdict]}, evidence, author_article_task=True
    )
    verdict["claim_ref"] = "silver_as_of_2026-10-01"
    assert guard({"verdicts": [verdict]}, evidence, author_article_task=True) is None


def test_main_answer_uses_latest_observed_market_date():
    guard = runtime_module._finance_market_horizon_issue
    evidence = {"evidence": [{
        "source_type": "market_bar_series",
        "excerpt": "xagusd daily bars 2026-07-22 to 2026-10-01",
        "metadata": {"symbol": "xagusd"},
    }]}
    assert "latest observed xagusd bar" in guard(
        "截至当前（2026-10-02）现货白银未触及73。", evidence
    )
    assert guard("截至可用日线（2026-10-01）现货白银未触及73。", evidence) is None


def test_market_return_math_rejects_mixed_start_baseline():
    guard = runtime_module._finance_market_return_math_issue
    mixed = "- 黄金：期初 4348.81 美元/盎司（9-11），期末 4194.37（10-10），区间 **-2.78%**。"
    assert "baseline date/value" in guard(mixed)
    corrected = "- 黄金：期初 4314.35 美元/盎司（9-10），期末 4194.37（10-10），区间 **-2.78%**。"
    assert guard(corrected) is None
    assert guard("- 黄金：期初 4348.81，期末 4194.37；另以9-10为基准计算区间涨跌幅。") is None


def test_future_watch_rejects_elapsed_month_without_rejecting_historical_review():
    guard = runtime_module._finance_future_watch_issue
    assert guard(
        "## 历史回顾\n9月的利率决议已结束。\n**后续可观察指标**\n- 9月加息概率。",
        as_of=date(2026, 10, 2),
    )
    assert guard(
        "## 历史回顾\n9月的利率决议已结束。\n**后续可观察指标**\n- 下一次利率决议。",
        as_of=date(2026, 10, 2),
    ) is None
    assert guard(
        "**后续可观察指标**\n- 2027年1月的通胀数据。",
        as_of=date(2026, 10, 2),
    ) is None
    assert guard(
        "## 六、后续可观察指标\n- 美联储7月底会议。",
        as_of=date(2026, 10, 2),
    )
    assert guard(
        "## 六、后续可观察指标\n- 美联储7月底会议后的政策预期变化。",
        as_of=date(2026, 10, 2),
    ) is None
    assert guard(
        "## 六、后续可观察指标\n- 美联储9月经济预测（点阵图）PDF 原文\n- 10月FOMC 决议",
        as_of=date(2026, 10, 11),
    ) is None
    assert guard(
        "## 四、后续可观察指标\n- 2026年11月中期选举结果。\n"
        "（说明：9月FOMC 与9月核心CPI 已属过去，只作历史回顾。）",
        as_of=date(2026, 10, 11),
    ) is None
    assert guard(
        "**后续观察点（截至 2026-10-11 尚未发生）**：\n"
        "- **2026 年 10 月 FOMC 会议**：核心变量是 2026 年 9 月核心 CPI 环比数据。",
        as_of=date(2026, 10, 11),
    ) is None
    assert guard(
        "**后续可观察指标**：2026年10月FOMC会议是否加息、"
        "2026年9月CPI数据（10月发布）、2026年11月中期选举结果。",
        as_of=date(2026, 10, 11),
    ) is None
    assert guard(
        "**后续可观察指标**\n- 2026 年 9 月 CPI 数据将公布。",
        as_of=date(2026, 10, 11),
    )


def test_future_watch_rejects_elapsed_not_due_horizon_in_analysis_line():
    issue = runtime_module._finance_future_watch_issue(
        "价格预测与时间窗口：2026-07-30 会议，当前标记为 not_due。",
        as_of=date(2026, 10, 13),
    )
    assert issue and "marks 2026-07-30 as not_due" in issue


def test_future_watch_ignores_article_publication_date_in_citation():
    answer = (
        "2031 年黄金预测仍标记为 not_due。"
        "（来源：TT/articles/2026-06-04-70edc46f5d60bf25.md:L71-L75）"
    )
    assert runtime_module._finance_future_watch_issue(
        answer, as_of=date(2026, 10, 13)
    ) is None


def test_finance_comparison_synthesis_accepts_analytical_bullets_not_inventory():
    analysis = "\n".join([
        "- TT 的黄金观点从等待信号变化到宣布拐点，但缺少独立验证。",
        "- 培风客与 TT 的分歧是确认条件不同，短期行情不能直接证明长期预测。",
        "- 对比两位作者，模型推断与原文观点必须分开，未经核实的数字要标注。",
    ] * 14)
    assert runtime_module._finance_comparison_has_synthesis(
        "# 黄金与美股研究\n## 观点变化\n" + analysis + "\n## 证据索引\n" + "- TT:1.md:L3\n" * 70
    )
    inventory = "# Article inventory\n## Sources\n" + ("- TT, 2026-07-01, gold.md:L3\n" * 100)
    assert not runtime_module._finance_comparison_has_synthesis(inventory)


@pytest.fixture
def finance_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    company = tmp_path / "data" / "companies" / "finance_observer"
    (company / "wiki").mkdir(parents=True)
    (company / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance research"}),
        encoding="utf-8",
    )
    root = company / "articles"
    author = root / "TT" / "articles"
    author.mkdir(parents=True)
    (author / "gold.md").write_text(
        "# Gold outlook\n2026-09-29\nThe author expects rates to affect gold.\n",
        encoding="utf-8",
    )
    (company / "research_policy.md").write_text("Compare sources and uncertainties.\n", encoding="utf-8")
    (company / "current_holdings.md").write_text(
        '```json\n{"as_of":"2026-09-29","currency":"CNY","approximate":true,'
        '"positions":[{"name":"gold","amount":7000}]}\n```', encoding="utf-8",
    )
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(root))
    PolicyMemoryStore("finance_observer").seed_model_configs()
    corpus = ArticleCorpus("finance_observer", root)
    corpus.sync()
    article_id = corpus.list_articles()[0]["article_id"]
    return article_id


def _run(actions, message, *, auto_plan=True):
    if auto_plan and not any(
        action.get("type") == "tool_call"
        and action.get("tool_name") == "research.plan"
        and (action.get("arguments") or {}).get("operation") == "create"
        for action in actions
    ):
        actions = [
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "create",
                "goals": ["Preserve the user request", "Complete the evidence and answer check"],
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 1, "status": "done",
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 2, "status": "done",
            }},
            *actions,
        ]
    scripted_actions = []
    for action in actions:
        if action.get("type") == "final" and "answer" in (action.get("output") or {}):
            scripted_actions.append(_scripted_checkpoint_action())
        scripted_actions.append(action)
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=ScriptedModelAdapter(scripted_actions))
    session_id = runtime.store.create_chat_session(
        title="Finance autonomous test", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session_id, message=message, mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    return result, runtime.store.list_agent_steps(result["run_id"])


def _scripted_checkpoint_action():
    return {
        "type": "tool_call", "tool_name": "research.checkpoint", "arguments": {
            "checks": [{
                "claim": "Scripted test answer requires an explicit verification decision",
                "decision": "check", "outcome": "inconclusive",
                "method": "Scripted contract test; final-answer audit is tested separately",
                "evidence_refs": [],
                "reason": "This fixture tests the other runtime gates, not factual accuracy",
                "gap": "Live evidence verification is outside this scripted fixture",
            }],
        },
    }


@pytest.mark.parametrize("failure", ["missing_article", "contract", "timeout"])
def test_tool_trace_keeps_exact_binding_after_failure(finance_workspace, monkeypatch, failure):
    from policy_impact.domains.finance.web_research import WebResearch

    if failure == "missing_article":
        failed_action = {"type": "tool_call", "tool_name": "article.read",
                         "arguments": {"article_id": "missing-article"}}
    elif failure == "contract":
        failed_action = {"type": "tool_call", "tool_name": "article.list",
                         "arguments": {"author": "TT", "unknown_field": True}}
    else:
        def timeout(self, **kwargs):
            raise TimeoutError("injected fixture timeout")

        monkeypatch.setattr(WebResearch, "open", timeout)
        failed_action = {"type": "tool_call", "tool_name": "web.open",
                         "arguments": {"url": "https://example.com/source"}}

    answer = "TT expects rates to affect gold (TT/articles/gold.md:L3). This is the author's view, not an independently verified fact."
    result, steps = _run([
        failed_action,
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": answer}},
    ], "Explain TT's gold viewpoint using the local article, disclosing any missing evidence.")

    assert result["answer"] == answer
    model_steps = {
        step["metadata"]["turn_index"]: step for step in steps
        if step["step_type"] == "model_call"
        and step.get("input_payload", {}).get("schema_name") == "general_chat_agent_loop.v1"
    }
    events = [step for step in steps if step["step_type"] == "tool_result"]
    failures = [step for step in events if step["status"] == "failed"]
    assert len(failures) == 1
    assert failed_action["tool_name"] in failures[0]["title"]
    assert bool(failures[0]["tool_calls"]) is (failure != "contract")
    ids = []
    for step in events:
        main = model_steps[step["metadata"]["turn_index"]]
        action = main["output_payload"]["payload"]
        observation = main["output_payload"]["observation"]
        assert action["tool_name"] in step["title"]
        assert main["metadata"]["tool_call_id"] == step["metadata"]["tool_call_id"]
        key = "tool_error" if step["status"] == "failed" else "tool_result"
        assert step["output_payload"]["observation"] == observation[key]
        if step["tool_calls"]:
            record = step["tool_calls"][0]
            assert record["call_id"] == step["metadata"]["tool_call_id"]
            assert record["tool_name"] == action["tool_name"]
            assert record["status"] == ("failed" if key == "tool_error" else "ok")
            ids.append(record["call_id"])
        else:
            assert key == "tool_error" and step["metadata"]["tool_call_id"] is None
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("synthesize", [False, True])
@pytest.mark.parametrize("inline_evidence", [False, True])
def test_versioned_runtime_plugin_to_delivery_uses_real_task_inputs(finance_workspace, monkeypatch, synthesize, inline_evidence):
    from policy_impact.domains.finance.research_execution import _walk

    import os
    from pathlib import Path
    root = Path(os.environ["FINANCE_ARTICLE_ROOT"])
    if synthesize:
        folder = root / "Beta" / "articles"
        folder.mkdir(parents=True)
        (folder / "contrary.md").write_text("# Another view\n2026-09-30\nGold also depends on risk appetite.\n", encoding="utf-8")
    corpus = ArticleCorpus("finance_observer", root)
    corpus.sync()
    ids = [row["article_id"] for row in corpus.list_articles()]
    read_count = 2 if synthesize else 1
    result_count = read_count + int(synthesize)

    def research_task(refs, kind):
        return {"schema_version": "finance.research_task.v1", "original_query": "Explain the article arguments",
                "objective": "Explain the argument and limits", "material_refs": refs,
                "requested_result_kind": kind, "completion_expectation": "Keep actual evidence scope",
                **({"reading_request": {"material_ref": refs[0], "depth": "deep"}}
                   if kind == "article_understanding" else {})}

    class Model:
        def __init__(self):
            self.main_calls = 0
            self.child_calls = 0

        def complete(self, messages, schema_name):
            rows = list(_walk(messages))
            evidence = sorted({row["evidence_id"] for row in rows if row.get("text") and row.get("evidence_id")})
            if schema_name == "finance.research_result.v1":
                self.child_calls += 1
                brief = messages[0]["content"]["visible_context"]["task_brief"]
                for row in rows:
                    evidence.extend(row.get("evidence_ids", []))
                evidence = sorted(set(evidence))
                text = "The supplied author views describe possible influences on gold; they do not establish an independently verified causal mechanism."
                payload = {"type": "final", "output": {
                    "schema_version": "finance.research_result.v1", "content": text,
                    "evidence_links": [{"text": text, "evidence_refs": evidence}],
                    "material_disposition": [{"material_ref": ref, "decision": "considered", "reason": "Task input"}
                                             for ref in brief["research_task"]["material_refs"]],
                    "gaps": [], "limitations": ["No external verification in this offline fixture"],
                }}
            else:
                self.main_calls += 1
                index = self.main_calls
                returned = [row["subagent_result"] for row in rows if isinstance(row.get("subagent_result"), dict)]
                receipts = [row["research_receipt"] for row in returned if row.get("research_receipt")]
                evidence = sorted(set(evidence) | {
                    ref for result in returned for link in result.get("output", {}).get("evidence_links", [])
                    for ref in link["evidence_refs"]})
                if index == 1:
                    name, args = "research.plan", {"operation": "create", "goals": ["Explain the source arguments", "Check evidence and limits"]}
                elif index == 2:
                    name, args = "article.list", {"limit": 10}
                elif index <= 2 + read_count:
                    payload = {"type": "delegate", "role": "analyst", "role_version": "2.0.0",
                               "research_task": research_task([ids[index - 3]], "article_understanding")}
                    return ModelResponse(response_id=f"main-{index}", payload=payload)
                elif synthesize and index == 3 + read_count:
                    payload = {"type": "delegate", "role": "analyst", "role_version": "2.0.0",
                               "research_task": research_task([row["artifact_id"] for row in receipts], "synthesis")}
                    return ModelResponse(response_id=f"main-{index}", payload=payload)
                else:
                    after = index - (2 + result_count)
                    if after <= result_count:
                        name, args = "research.disposition", {"result_ref": receipts[after - 1]["artifact_id"],
                                                             "decision": "qualify", "reason": "Sourced author argument; no independent verification"}
                    elif after <= result_count + 2:
                        name, args = "research.plan", {"operation": "update", "goal_id": after - result_count, "status": "done"}
                    elif after == result_count + 3:
                        name, args = "research.checkpoint", {"checks": [{
                            "claim": "The answer attributes possible gold drivers to these authors",
                            "decision": "not_needed", "outcome": "not_applicable", "method": "Compare source wording",
                            "evidence_refs": evidence, "reason": "Attribution only, not verified causality", "gap": ""}]}
                    else:
                        citations = "`TT/articles/gold.md:L3`" + (" `Beta/articles/contrary.md:L3`" if synthesize else "")
                        if inline_evidence:
                            citations = " ".join(evidence)
                        payload = {"type": "final", "output": {"answer":
                            "The authors identify potential drivers, not a proven pricing rule. Interest rates and risk appetite can matter under different conditions; "
                            "the supplied articles do not establish their relative importance or independently verify causality. " + citations}}
                        return ModelResponse(response_id=f"main-{index}", payload=payload)
                payload = {"type": "tool_call", "tool_name": name, "arguments": args}
            return ModelResponse(response_id=f"fixture-{self.main_calls}-{self.child_calls}", payload=payload)

    model = Model()
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=model)
    session_id = runtime.store.create_chat_session(title="Versioned offline integration", mode="chat",
                                                   model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat")
    response = runtime.run_turn(session_id=session_id, message="解释本地文章的思路，指出论证的局限。", mode="chat",
                                model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat")
    steps = runtime.store.list_agent_steps(response["run_id"])
    assert response["answer"].startswith("The authors identify potential drivers")
    assert model.child_calls == result_count
    events = [step["step_type"] for step in steps]
    assert events.count("research_result_ingest") == result_count
    assert events.count("research_result_disposition") == result_count
    assert events.count("research_analysis_input") == result_count
    linkage = next(step["output_payload"] for step in steps if step["step_type"] == "research_final_linkage")
    assert linkage["pending_dispositions"] == []
    assert len(linkage["used_articles"]) == read_count
    assert "research_budget" in events
    assert "evidence_citation_grounding" not in events


@pytest.mark.parametrize("invalid_ref", [False, True])
def test_late_main_candidate_preserves_timeout_and_never_claims_reviewed(finance_workspace, monkeypatch, invalid_ref):
    import policy_impact.harness.research_budget as budget
    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])

    class LateModel(ScriptedModelAdapter):
        def complete(self, messages, schema_name):
            response = super().complete(messages, schema_name)
            if response.payload["type"] == "final":
                clock[0] += 4000
            return response

    answer = "Duration measures the sensitivity of a bond price to changes in yield."
    if invalid_ref:
        answer += " Verified by evidence_fabricated."
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=LateModel([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Explain duration", "Check the explanation"]}},
        {"type": "final", "output": {"answer": answer, "verification_status": "verified",
                                        "semantic_review": "complete", "answer_audit_coverage": "complete"}},
    ]))
    session_id = runtime.store.create_chat_session(title="Late candidate", mode="chat", model_id=DEFAULT_MODEL_ID,
                                                   active_skill_id="general_chat")
    response = runtime.run_turn(session_id=session_id, message="解释久期是什么。", mode="chat",
                                model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat")
    steps = runtime.store.list_agent_steps(response["run_id"])
    late = next(row["output_payload"] for row in steps if row["step_type"] == "research_late_candidate")
    assert late["candidate_retained"] is not invalid_ref
    assert late["semantic_review_performed"] is False
    if invalid_ref:
        assert "evidence_fabricated" not in response["answer"]
    else:
        assert answer in response["answer"]
        assert "终稿审核未完成" in response["answer"]


def test_finance_delivery_checkpoint_requires_published_answer(finance_workspace):
    result, steps = _run([{"type": "final", "output": {"answer": "A bounded definition."}}], "Define gold")
    delivery = next(step for step in steps if step["step_type"] == "research_delivery")
    assert delivery["output_payload"]["published"] is True
    assert delivery["output_payload"]["research_plan"]["delivery_status"] == "done"
    assert result["answer"] == "A bounded definition."


def test_main_loads_exact_review_target_schema_on_demand(finance_workspace, monkeypatch):
    from policy_impact.skills.schemas import FinanceReviewTarget

    captured = []
    disclosed = []
    original = ScriptedModelAdapter.complete

    def capture(self, messages, schema_name):
        if schema_name == "general_chat_agent_loop.v1":
            for message in messages:
                content = message.get("content")
                if isinstance(content, dict) and "visible_context" in content:
                    captured.append(content["visible_context"])
                if isinstance(content, dict) and "tool_result" in content:
                    result = content["tool_result"]
                    if isinstance(result, dict) and result.get("skill_id") == "evidence_review":
                        disclosed.append(result)
        return original(self, messages, schema_name)

    monkeypatch.setattr(ScriptedModelAdapter, "complete", capture)
    result, _ = _run([
        {"type": "tool_call", "tool_name": "skill.open", "arguments": {"skill_id": "evidence_review"}},
        {"type": "final", "output": {"answer": "A bounded definition."}},
    ], "Define duration")
    assert "review_target_json_schema" not in captured[0]
    schema = disclosed[-1]["schemas"]["review_target"]
    assert schema == FinanceReviewTarget.model_json_schema()
    assert schema["properties"]["external_check_needed"]["enum"] == ["yes", "no", "undecided"]
    assert schema["properties"]["importance"]["enum"] == ["core", "supporting", "context"]
    assert result["answer"] == "A bounded definition."


@pytest.mark.parametrize("invalid_ref", [False, True])
def test_protocol_failure_keeps_complete_candidate_only_after_local_source_check(finance_workspace, invalid_ref):
    from policy_impact.harness.model_gateway import ModelInvocationError

    answer = "Duration is a measure of bond price sensitivity to interest rates. " * 40
    if invalid_ref:
        answer += " evidence_fabricated"

    class FailedModel(ScriptedModelAdapter):
        def complete(self, messages, schema_name):
            if self._index < len(self._payloads):
                return super().complete(messages, schema_name)
            raise ModelInvocationError("research model decision failed: format", metadata={
                "failure_kind": "format", "preserved_final_output": {"answer": answer}})

    runtime = AgentRuntime(company_id="finance_observer", model_adapter=FailedModel([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Explain duration", "Check explanation"]}},
    ]))
    session = runtime.store.create_chat_session(title="Protocol recovery", mode="chat", model_id=DEFAULT_MODEL_ID,
                                                active_skill_id="general_chat")
    response = runtime.run_turn(session_id=session, message="解释久期是什么。", mode="chat",
                                model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat")
    if invalid_ref:
        assert "evidence_fabricated" not in response["answer"]
    else:
        assert answer.rstrip() in response["answer"]
        assert "终稿审核未完成" in response["answer"]
        assert response["verification_status"] != "verified"
        assert response["delivery_status"] == "done"


def test_finance_failed_research_delivers_safe_runtime_fallback(finance_workspace):
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
    ], "Research the gold outlook")
    delivery = next(step for step in steps if step["step_type"] == "research_delivery")
    assert delivery["output_payload"]["published"] is False
    # Safe fallback was committed, but it is not a completed model analysis.
    assert delivery["output_payload"]["research_plan"]["delivery_status"] == "done"
    assert result["delivery_status"] == "done"
    assert delivery["output_payload"]["research_plan"]["delivery_mode"] == "source_only"
    assert result["verification_status"] == result["semantic_review"] == "failed"
    assert "未形成可交付" in result["answer"]


def test_finance_failure_before_plan_delivers_safe_runtime_fallback(finance_workspace):
    result, steps = _run([{
        "type": "tool_call", "tool_name": "research.plan",
        "arguments": {"operation": "update", "goal_id": 1, "status": "done"},
    }], "Research the gold outlook", auto_plan=False)
    delivery = next(step for step in steps if step["step_type"] == "research_delivery")
    snapshot = delivery["output_payload"]["research_plan"]
    assert delivery["output_payload"]["published"] is False
    assert snapshot["delivery_status"] == "done"
    assert snapshot["delivery_mode"] == "source_only"
    assert snapshot["goals"][0]["kind"] == "runtime_delivery"
    assert "未形成可交付" in result["answer"]


def test_material_delivery_after_model_failure_closes_pending_research_goals(finance_workspace):
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Read the article", "Analyze its reasoning"],
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {
            "article_id": finance_workspace,
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done", "note": "Full article read",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "pending", "note": "Analysis not completed",
        }},
    ], "Explain TT's gold reasoning", auto_plan=False)

    delivery = next(step for step in steps if step["step_type"] == "research_delivery")["output_payload"]
    plan = delivery["research_plan"]
    assert result["delivery_status"] == plan["delivery_status"] == "done"
    assert result["delivery_mode"] == plan["delivery_mode"] == "source_only"
    assert plan["research_pending_count"] == 0
    assert plan["goals"][0]["status"] == "done"
    assert plan["goals"][0]["note"] == "Full article read"
    assert plan["goals"][1]["status"] == "blocked"
    assert plan["goals"][1]["interruption"]["previous_note"] == "Analysis not completed"
    assert plan["goals"][1]["interruption"]["reason"]


def test_finance_repeated_answer_dispute_delivers_source_preserving_partial(
    finance_workspace, monkeypatch,
):
    from policy_impact.domains.finance.answer_audit import AnswerAudit

    disputed = "TT says gold is guaranteed to rise. (TT/articles/gold.md:L3)"

    def disputed_audit(self, answer, **kwargs):
        return {
            "status": "revise", "coverage_complete": True,
            "issues": [{
                "span": disputed, "claim_type": "prediction", "action": "revise",
                "evidence_refs": [],
                "reason": "The article describes a conditional rate-sensitive view, not a guarantee.",
            }],
        }

    monkeypatch.setattr(AnswerAudit, "call", disputed_audit)
    actions = [
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        *[{"type": "final", "output": {"answer": disputed}} for _ in range(3)],
    ]
    result, steps = _run(actions, "Summarize TT's gold view")
    assert result["answer"].endswith(disputed)
    assert "审核状态：部分结论存在争议或证据不足" in result["answer"]
    assert "guaranteed to rise" in result["answer"]
    assert "conditional rate-sensitive view" in result["answer"]
    resolution = [step for step in steps if step["step_type"] == "answer_review_resolution"]
    assert len(resolution) == 1
    assert resolution[0]["output_payload"]["resolution"] == "disputed_full_draft"
    delivery = next(step for step in steps if step["step_type"] == "research_delivery")
    assert delivery["output_payload"]["research_plan"]["delivery_status"] == "done"


@pytest.mark.parametrize(("review", "verification", "audit_coverage", "semantic", "mode"), [
    ("error", "failed", "failed", "failed", "qualified"),
    ("partial", "partial", "partial", "partial", "qualified"),
    ("disputed", "disputed", "complete", "complete", "qualified"),
    ("accepted", "partial", "complete", "complete", "qualified"),
])
def test_finance_audit_status_survives_chat_delivery_and_plan_reload(
    finance_workspace, monkeypatch, review, verification, audit_coverage, semantic, mode,
):
    from policy_impact.domains.finance.research_plan import ResearchPlan

    candidate = "TT expects rates to affect gold; this is an author view, not proven causality. (TT/articles/gold.md:L3)"

    def audit(self, answer, **kwargs):
        if review == "error":
            raise RuntimeError("fixture audit transport failed")
        return {
            "status": "accept" if review == "accepted" else "revise",
            "coverage_complete": review != "partial",
            "limitations": "Some selected claims remain unreviewed" if review == "partial" else "",
            "checks": [{"status": "viewpoint_faithful", "action": "retain", "target": {"type": "author_view"}}]
                      if review in {"partial", "accepted"} else [],
            "issues": [{"span": candidate, "claim_type": "author_view", "action": "revise",
                        "reason": "A reviewer disputes the attribution", "evidence_refs": []}]
                      if review == "disputed" else [],
        }

    monkeypatch.setattr(AnswerAudit, "call", audit)
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        *[{"type": "final", "output": {"answer": candidate,
            "verification_status": "verified", "answer_audit_coverage": "complete",
            "semantic_review": "complete"}} for _ in range(3)],
    ], "Explain TT's argument")
    assert candidate in result["answer"]
    if review == "error":
        assert "终稿审核未完成" in result["answer"]
    expected = {"delivery_status": "done", "delivery_mode": mode,
                "verification_status": verification, "answer_audit_coverage": audit_coverage}
    run = PolicyMemoryStore("finance_observer").get_agent_run(result["run_id"])
    delivery = next(step["output_payload"] for step in steps if step["step_type"] == "research_delivery")
    for field, value in expected.items():
        assert result[field] == value
        assert run["metadata"][field] == value
        assert delivery[field] == value
        assert delivery["research_plan"][field] == value
    assert result["semantic_review"] == run["metadata"]["semantic_review"] == semantic
    assert result["evidence_coverage"] == run["metadata"]["evidence_coverage"] == delivery["evidence_coverage"]
    plan_path = wiki_loader.company_dir("finance_observer") / "runs" / result["run_id"] / "research_plan.json"
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["delivery"] == delivery["research_plan"]["delivery"]
    restored = ResearchPlan(session_id=result["session_id"], run_id=result["run_id"], state_path=plan_path)
    assert restored.call(operation="load", run_id=result["run_id"])["delivery"] == saved["delivery"]


def test_finance_final_audit_receives_main_agent_selected_review_targets(
    finance_workspace, monkeypatch,
):
    seen = {}

    def selected_audit(self, answer, **kwargs):
        seen["targets"] = kwargs.get("review_targets")
        return {
            "status": "accept", "issues": [], "checks": [],
            "reviewed_claim_count": 1, "coverage_complete": True,
            "review_scope": "main_agent_selected_targets", "review_status": "complete",
            "limitations": "",
        }

    monkeypatch.setattr(AnswerAudit, "call", selected_audit)
    target = {
        "target_id": "tt-view-1", "type": "author_view",
        "claim_or_question": "TT says rates affect the gold outlook.",
        "article_refs": [finance_workspace], "evidence_refs": [],
        "review_question": "Does the article support this attribution?",
        "external_check_needed": "no", "importance": "core",
    }
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {
            "answer": "TT says rates affect the gold outlook.",
            "review_targets": [target],
            "review_status": "planned",
        }},
    ], "Summarize TT's gold view")
    assert seen.get("targets") == [target], [step["output_payload"] for step in steps
                                            if step["step_type"] == "answer_candidate_selection"]
    assert result["answer"].startswith("TT says rates affect the gold outlook.")
    assert "TT/articles/gold.md:L1" in result["answer"]
    audit_steps = [step for step in steps if step["step_type"] == "answer_claim_audit"]
    assert audit_steps[-1]["output_payload"]["review_scope"] == "main_agent_selected_targets"


def test_explicit_empty_review_targets_do_not_certify_verification(finance_workspace, monkeypatch):
    monkeypatch.setattr(AnswerAudit, "call", _REAL_ANSWER_AUDIT_CALL)
    candidate = "This answer only reports the limited article scope; it is not separately reviewed."
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate, "review_targets": []}},
    ], "Describe TT's article scope")
    assert candidate in result["answer"]
    assert result["delivery_status"] == "done"
    assert result["verification_status"] == "partial"
    assert result["semantic_review"] == "partial"
    audit = next(step["output_payload"] for step in steps if step["step_type"] == "answer_claim_audit")
    assert audit["review_status"] == "not_requested"
    assert audit["reviewed_claim_count"] == 0


@pytest.mark.parametrize("invalid_field", [{}, {"review_targets": None}, {"review_targets": {}}])
def test_omitted_target_metadata_is_completed_without_legacy_audit(finance_workspace, monkeypatch, invalid_field):
    monkeypatch.setattr(AnswerAudit, "select_review_targets", _REAL_REVIEW_TARGET_SELECTION)
    captured = []

    def audit(self, answer, **kwargs):
        captured.append(kwargs["review_targets"])
        assert isinstance(kwargs["review_targets"], list)
        return {"status": "accept", "issues": [], "checks": [], "coverage_complete": True,
                "review_scope": "main_agent_selected_targets", "review_status": "complete"}

    monkeypatch.setattr(AnswerAudit, "call", audit)
    candidate = "TT treats rates as a possible influence on gold, not an independently verified cause."
    target = {"target_id": "view-1", "type": "author_view", "scope": "article",
              "claim_or_question": "TT expects rates to affect gold.", "article_refs": [finance_workspace],
              "evidence_refs": [], "review_question": "Does the article support this attribution?",
              "external_check_needed": "no", "importance": "core"}
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate, **invalid_field}},
        {"type": "final", "output": {"review_targets": [target],
                                       "selection_reason": "Check the material attributed conditional author view."}},
    ], "Explain TT's argument")
    assert candidate in result["answer"]
    assert captured[0][0]["target_id"] == "view-1"
    selection = next(step for step in steps if step["step_type"] == "answer_review_target_selection")
    assert selection["metadata"]["metadata_only"] is True
    schemas = [step["input_payload"].get("schema_name") for step in steps if step["step_type"] == "model_call"]
    assert "finance_review_targets.v1" in schemas
    assert "finance_answer_audit.v1" not in schemas


@pytest.mark.parametrize("failure", ["structure", "transport"])
def test_target_metadata_failure_preserves_full_answer_without_final_retry(finance_workspace, monkeypatch, failure):
    from policy_impact.harness.model_gateway import ModelInvocationError

    monkeypatch.setattr(AnswerAudit, "select_review_targets", _REAL_REVIEW_TARGET_SELECTION)
    candidate = " ".join(["TT's full conditional analysis remains visible even if metadata completion fails."] * 5)
    if failure == "transport":
        def select(*args, **kwargs):
            raise ModelInvocationError("Remote transport failed")
        monkeypatch.setattr(AnswerAudit, "select_review_targets", select)
    replies = [{"type": "final", "output": {"wrong_shape": True}} for _ in range(3)]
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate}},
        *replies,
    ], "Explain TT's argument")
    assert candidate in result["answer"]
    assert "终稿审核未完成" in result["answer"]
    assert result["delivery_status"] == "done"
    assert result["verification_status"] == result["answer_audit_coverage"] == "failed"
    assert result["semantic_review"] == "failed"
    assert not any(step["step_type"] == "answer_claim_audit" for step in steps)
    resolution = next(step["output_payload"] for step in steps if step["step_type"] == "answer_review_resolution")
    assert resolution["target_selection_failed"] is True
    assert resolution["audit_attempts"] == 1


@pytest.mark.parametrize("interruption", ["model_error", "timeout", "invalid_model_action", "tool_denied"])
def test_rejected_complete_candidate_survives_later_failure(finance_workspace, monkeypatch, interruption):
    import policy_impact.harness.research_budget as budget
    from policy_impact.harness.model_gateway import ModelInvocationError

    clock = [100.0]
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])
    original = ScriptedModelAdapter.complete
    candidate = "\n\n".join(["TT's rate-sensitive gold analysis remains conditional, not proven causality."] * 8)
    candidate += " (TT/articles/gold.md:L3)"

    def audit(self, answer, **kwargs):
        return {"status": "revise", "answer_audit_coverage": "complete", "checks": [], "issues": [{
            "span": "rate-sensitive gold analysis", "action": "qualify", "claim_type": "author_view",
            "reason": "Keep the condition explicit; independent market verification is incomplete.",
        }]}

    def interrupted(self, messages, schema_name):
        if self._index >= len(self._payloads) and schema_name == "general_chat_agent_loop.v1":
            if interruption == "model_error":
                raise ModelInvocationError("fixture connection dropped during revision")
            if interruption == "timeout":
                clock[0] += 4000
                return ModelResponse("late_revision", {"type": "final", "output": {}})
            if interruption == "tool_denied":
                return ModelResponse("denied_revision", {"type": "tool_call", "tool_name": "not.allowed", "arguments": {}})
            return ModelResponse("malformed_revision", {"type": "invalid"})
        return original(self, messages, schema_name)

    monkeypatch.setattr(AnswerAudit, "call", audit)
    monkeypatch.setattr(ScriptedModelAdapter, "complete", interrupted)
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate}},
    ], "Explain TT's argument")
    run = PolicyMemoryStore("finance_observer").get_agent_run(result["run_id"])
    assert run["metadata"]["stop_reason"] == interruption
    assert candidate in result["answer"]
    assert "independent market verification is incomplete" in result["answer"]
    assert "没有获得模型的最终综合答案" not in result["answer"]
    assert result["delivery_mode"] == "qualified"
    assert result["verification_status"] != "verified"
    assert any(step["step_type"] == "answer_candidate_selection" and step["output_payload"]["selected"]
               for step in steps)
    checkpoint = next(step["output_payload"] for step in steps if step["step_type"] == "answer_candidate_checkpoint")
    assert checkpoint["persistence_status"] == "saved"


def test_main_agent_may_deliver_with_limits_after_initial_audit(finance_workspace, monkeypatch):
    calls = []
    candidate = "TT's view is conditional on rates, not proven causality. (TT/articles/gold.md:L3)"

    def audit(self, answer, **kwargs):
        calls.append(answer)
        return {"status": "revise", "answer_audit_coverage": "complete", "checks": [], "issues": [{
            "span": "conditional on rates", "action": "qualify", "claim_type": "author_view",
            "reason": "This statement is not independently verified market causality.",
        }]}

    monkeypatch.setattr(AnswerAudit, "call", audit)
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate, "review_disposition": "deliver_with_limits",
                                       "review_resolution_reason": "Preserve the attributed view and disclose the missing external check."}},
    ], "Explain TT's argument")
    assert len(calls) == 1
    assert PolicyMemoryStore("finance_observer").get_agent_run(result["run_id"])["metadata"]["stop_reason"] == "success"
    assert candidate in result["answer"]
    resolution = next(step["output_payload"] for step in steps if step["step_type"] == "answer_review_resolution")
    assert resolution["review_disposition"] == "deliver_with_limits"
    assert resolution["audit_attempts"] == 1


def test_same_candidate_reuses_unresolved_audit_instead_of_fixed_three_rewrites(finance_workspace, monkeypatch):
    calls = []
    candidate = "TT's gold view remains conditional on rates. (TT/articles/gold.md:L3)"

    def audit(self, answer, **kwargs):
        calls.append(answer)
        return {"status": "revise", "answer_audit_coverage": "complete", "issues": [{
            "span": "conditional on rates", "action": "qualify", "claim_type": "author_view",
            "reason": "The evidence scope should remain explicit, not treated as external proof.",
        }]}

    monkeypatch.setattr(AnswerAudit, "call", audit)
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        *[{"type": "final", "output": {"answer": candidate}} for _ in range(2)],
    ], "Explain TT's argument")
    assert len(calls) == 1
    assert PolicyMemoryStore("finance_observer").get_agent_run(result["run_id"])["metadata"]["stop_reason"] == "success"
    assert candidate in result["answer"]
    assert any(step["step_type"] == "answer_review_resolution" and step["output_payload"].get("no_progress_reuse")
               for step in steps)


@pytest.mark.parametrize("disclaimer", ["", "Independent review was not completed. "])
def test_terminal_fallback_does_not_republish_rejected_independent_review_provenance(finance_workspace, disclaimer):
    false_claim = "Verifier independently read article.read and independently verified this source."
    candidate = disclaimer + false_claim + " TT's rate-sensitive gold view is conditional. (TT/articles/gold.md:L3)"
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate, "review_targets": []}},
    ], "Please independently verify the latest TT article.")
    assert false_claim not in result["answer"]
    assert result["delivery_mode"] == "source_only"
    assert result["verification_status"] != "verified"
    assert any(step["step_type"] == "answer_candidate_selection" and not step["output_payload"]["selected"]
               and "no successful independent source read" in step["output_payload"]["reason"] for step in steps)


@pytest.mark.parametrize("interruption", ["gate_blocked", "model_error"])
def test_missing_independent_review_disclosure_preserves_safe_complete_candidate(finance_workspace, monkeypatch, interruption):
    from policy_impact.harness.model_gateway import ModelInvocationError

    original = ScriptedModelAdapter.complete

    def interrupted(self, messages, schema_name):
        if interruption == "model_error" and self._index >= len(self._payloads):
            raise ModelInvocationError("fixture connection closed after candidate")
        return original(self, messages, schema_name)

    monkeypatch.setattr(ScriptedModelAdapter, "complete", interrupted)
    candidate = "TT expects rates to affect gold under specific conditions. (TT/articles/gold.md:L3)"
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "final", "output": {"answer": candidate, "review_targets": []}},
    ], "Please independently verify the latest TT article.")
    assert candidate in result["answer"]
    assert "独立复核状态" in result["answer"]
    assert result["delivery_mode"] == "qualified"
    assert result["verification_status"] != "verified"
    assert any(step["step_type"] == "answer_candidate_checkpoint" for step in steps)
    assert PolicyMemoryStore("finance_observer").get_agent_run(result["run_id"])["metadata"]["stop_reason"] == interruption


def test_subagent_claim_audit_binds_claims_and_verdicts_to_known_evidence():
    records = [
        {
            "finding": {
                "role": "analyst",
                "status": "success",
                "task_id": "task_analyst",
                "output": {
                    "claims": [{
                        "claim_ref": "gold-view",
                        "statement": "The author expects rates to affect gold.",
                        "evidence_refs": ["article-alias"],
                        "confidence": 0.8,
                    }],
                },
                "child_evidence_bundle": {
                    "evidence_ids": [],
                    "aliases": {},
                },
                "evidence_ids": [],
            }
        },
        {
            "finding": {
                "role": "verifier",
                "status": "success",
                "task_id": "task_verifier",
                "output": {
                    "verdicts": [{
                        "claim_ref": "gold-view",
                        "decision": "pass",
                        "evidence_refs": ["evidence_article"],
                        "reason": "The source supports the author's stated view.",
                    }],
                },
                "child_evidence_bundle": {
                    "evidence_ids": ["evidence_article"],
                    "aliases": {},
                },
                "evidence_ids": ["evidence_article"],
            }
        },
    ]
    audit = AgentRuntime._finance_subagent_evidence_audit(
        records,
        {
            "evidence_ids": ["evidence_article"],
            "aliases": {"article-alias": "evidence_article"},
        },
    )
    assert audit["claim_count"] == 2
    assert audit["invalid_binding_count"] == 0
    assert audit["unreviewed_claim_count"] == 0
    assert {row["verifier_decision"] for row in audit["claim_bindings"]} == {"pass"}


def test_subagent_audit_reports_partial_review_without_mislabeling_unknown_as_invalid():
    records = [
        {"finding": {"role": "analyst", "status": "success", "output": {"claims": [
            {"claim_ref": "a", "evidence_refs": ["article"]},
            {"claim_ref": "b", "evidence_refs": ["article"]},
        ]}}},
        {"finding": {"role": "verifier", "status": "success", "output": {"verdicts": [
            {"claim_ref": "a", "decision": "unverified", "evidence_refs": [], "reason": "No matching source."},
        ]}}},
    ]
    audit = AgentRuntime._finance_subagent_evidence_audit(
        records, {"evidence_ids": ["article"], "aliases": {}}, review_required=False
    )
    assert audit["invalid_binding_count"] == 0
    assert audit["unverified_verdict_count"] == 1
    assert audit["unreviewed_claim_count"] == 1
    assert audit["review_status"] == "partial"
    assert audit["review_complete"] is False
    assert audit["review_gate_satisfied"] is True


def test_market_series_counts_as_independent_source_read():
    evidence = {"evidence_ids": ["evidence_series"], "evidence": [{
        "evidence_id": "evidence_series", "source_type": "market_bar_series",
    }]}
    assert AgentRuntime._finance_subagent_review_scope("verifier", evidence) == "independent_source_read"
    assert AgentRuntime._finance_subagent_review_scope("analyst", evidence) == "delegated_source_read"


def test_simple_finance_question_uses_a_complete_plan_without_tools_or_delegation(finance_workspace):
    result, steps = _run(
        [
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "create",
                "goals": ["回答概念问题", "确认是否需要实时证据"],
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 1, "status": "done",
            }},
            {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                "operation": "update", "goal_id": 2, "status": "done",
            }},
            {"type": "final", "output": {"answer": "黄金是贵金属；这里没有查询实时价格。"}},
        ],
        "什么是黄金？",
    )
    assert "黄金是贵金属" in result["answer"]
    assert not any(step["step_type"] == "subagent_delegate" for step in steps)
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    plugin = next(step for step in steps if step["step_type"] == "plugin_load")
    assert plugin["output_payload"]["toolkits"] == ["finance_research.json"]
    assert plugin["output_payload"]["prompt_modules"] == ["finance/00_system.md"]
    assert plugin["output_payload"]["skill_modules"]
    assert plugin["output_payload"]["composition"] == "one_system_prompt_plus_capability_catalog"
    memory_step = next(step for step in steps if step["step_type"] == "memory_read")
    context_step = next(
        step for step in steps
        if step["step_type"] == "tool_call" and step["title"] == "延迟读取金融 Wiki"
    )
    assert memory_step["status"] == "skipped"
    assert context_step["status"] == "skipped"
    assert context_step["output_payload"]["initiated_by"] == "model"
    assert not audit["output_payload"]["article_reads"]
    assert not audit["output_payload"]["public_web_searches"]
    assert not audit["output_payload"]["market_reads"]
    assert audit["output_payload"]["research_plan"]["pending_count"] == 0
    assert len(audit["output_payload"]["research_plan"]["goals"]) == 3
    assert audit["output_payload"]["research_plan"]["delivery_status"] == "pending"


def test_finance_final_without_a_plan_is_blocked(finance_workspace):
    result, steps = _run(
        [{"type": "final", "output": {"answer": "A finance answer without a plan."}}],
        "What is gold?",
        auto_plan=False,
    )
    assert result["answer"] != "A finance answer without a plan."
    assert any(
        step["step_type"] == "gate_check" and "research.plan" in str(step)
        for step in steps
    )


@pytest.mark.parametrize("message", [
    "黄金最近一个月表现如何？",
    "标普500今年涨了多少？",
    "美元汇率最新是多少？",
    "纳斯达克最近走势和原因",
    "今天白银价格怎样？",
])
def test_market_fact_intent_covers_natural_wording(message):
    assert runtime_module._requests_market_evidence(message)


@pytest.mark.parametrize("message", ["黄金是什么？", "黄金和白银有什么概念区别？"])
def test_market_concept_question_does_not_force_market_evidence(message):
    assert not runtime_module._requests_market_evidence(message)


def test_mixed_concept_and_price_question_still_requires_market_evidence():
    assert runtime_module._requests_market_evidence("黄金是什么，今天价格多少？")


@pytest.mark.parametrize("message", [
    "总结某位作者最近五篇黄金文章，并给出引用。",
    "概括 TT 今年发布的黄金文章中的核心观点。",
    "阅读这位作者本月的白银文章，解释他的思路。",
    "Summarize recent gold articles by this author.",
])
def test_article_time_scope_alone_does_not_require_market_prices(message):
    assert not runtime_module._requests_market_evidence(message)


def test_missing_author_answer_is_delivered_without_irrelevant_market_tools(finance_workspace):
    answer = "文章库中没有该作者的文章，无法总结最近五篇黄金文章，也无法提供正文引用。"
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.list", "arguments": {
            "author": "missing_test_author", "limit": 5,
        }},
        {"type": "final", "output": {"answer": answer}},
    ], "总结 missing_test_author 最近五篇黄金文章，并给出引用。")

    assert result["answer"] == answer
    assert not any("The user asked for market facts" in str(step) for step in steps)
    audit = next(step["output_payload"] for step in steps if step["step_type"] == "evidence_audit")
    assert not audit["market_reads"]
    delivery = next(step["output_payload"] for step in steps if step["step_type"] == "research_delivery")
    assert delivery["published"] is True
    assert delivery["delivery_status"] == "done"


def test_runtime_drops_model_minted_market_citations():
    citations = runtime_module._drop_model_market_citations([
        {"type": "company_fact", "fact_id": "profile"},
        {"type": "market_metric", "evidence_id": "forged"},
        {"citation_type": "market_metric", "evidence_id": "forged-2"},
        "TT/articles/gold.md:L3",
    ])
    assert citations == [
        {"type": "company_fact", "fact_id": "profile"},
        "TT/articles/gold.md:L3",
    ]


@pytest.mark.parametrize("message", ["市场今天涨多少？", "今天大盘怎么样？"])
def test_market_question_without_an_asset_does_not_force_a_quote_lookup(message):
    assert not runtime_module._requests_market_evidence(message)


def test_explicit_article_authors_are_detected_for_scope_completeness():
    authors = [
        {"author": "TT"},
        {"author": "培风客"},
        {"author": "万种心法"},
    ]
    assert runtime_module._explicit_article_authors(
        "比较最近90天 TT、培风客和万种心法的观点", authors
    ) == ["TT", "培风客", "万种心法"]
    assert runtime_module._explicit_article_authors("只看 TT", authors) == ["TT"]


def test_finance_agent_receives_one_system_prompt_and_declared_skills(finance_workspace):
    class CapturingAdapter:
        def __init__(self):
            self.messages = []

        def complete(self, messages, schema_name):
            self.messages.append(messages)
            return ModelResponse(
                response_id="capture_1",
                payload={"type": "final", "output": {"answer": "直接回答。"}},
            )

    adapter = CapturingAdapter()
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)
    session_id = runtime.store.create_chat_session(
        title="Plugin prompt contract", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session_id, message="什么是黄金？", mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )

    system = adapter.messages[0][0]
    assert system["role"] == "system"
    descriptors = system["content"]["allowed_tool_descriptors"]
    assert descriptors
    plan_descriptor = next(item for item in descriptors if item["name"] == "research.plan")
    assert "authors" in plan_descriptor["parameters"]["$defs"]["PlanScope"]["properties"]
    assert "date_range" in plan_descriptor["parameters"]["$defs"]["PlanScope"]["properties"]
    assert {item["name"] for item in descriptors if "parameters" in item} == {
        "research.plan", "skill.open", "tool.describe",
    }
    assert "tool_descriptions" not in system["content"]["visible_context"]
    workspace_prompt = system["content"]["workspace_prompt"]
    assert "Finance Research Agent: core system prompt" in workspace_prompt
    assert "`source_access`" in workspace_prompt
    assert "`evidence_review`" in workspace_prompt
    assert "`subagent_review`" in workspace_prompt
    assert "# Source Access skill" not in workspace_prompt
    assert "# Evidence Review skill" not in workspace_prompt
    assert "# Controlled Subagent skill" not in workspace_prompt
    assert "# Main Agent Loop skill" not in workspace_prompt
    assert "action_examples" not in system["content"]
    assert "output_contract" not in system["content"]
    assert system["content"]["instructions"] == []
    assert workspace_prompt.count("## JSON 动作协议") == 1
    assert system["content"]["visible_context"]["workspace_paths"]["articles"]
    assert "finance/10_tools.md" not in workspace_prompt
    plugin = next(
        step for step in runtime.store.list_agent_steps(result["run_id"])
        if step["step_type"] == "plugin_load"
    )
    assert plugin["output_payload"]["composition"] == "one_system_prompt_plus_capability_catalog"
    assert plugin["output_payload"]["initial_loaded_skill_modules"] == []
    assert len(plugin["output_payload"]["skill_modules"]) == 7


def test_finance_discovery_then_reading_keeps_delivery_and_trace(finance_workspace):
    answer = "TT expects rates to affect gold (TT/articles/gold.md:L3). This is the author's view, not an independently verified fact."
    result, steps = _run([
        {"type": "tool_call", "tool_name": "skill.open", "arguments": {"skill_id": "source_access"}},
        {"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "article.read"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "tool_call", "tool_name": "skill.open", "arguments": {"skill_id": "evidence_review"}},
        {"type": "final", "output": {"answer": answer}},
    ], "Explain TT's gold viewpoint from the local article with limits disclosed.")
    assert result["answer"] == answer
    events = [step for step in steps if step["step_type"] == "skill_load"]
    assert [step["output_payload"]["skill_id"] for step in events] == ["source_access", "evidence_review"]
    assert all(len(step["output_payload"]["checksum"]) == 64 for step in events)
    assert any(step["step_type"] == "tool_describe" for step in steps)
    audit = next(step["output_payload"] for step in steps if step["step_type"] == "evidence_audit")
    assert len(audit["article_reads"]) == 1
    assert audit["research_plan"]["pending_count"] == 0


def test_budget_stop_returns_auditable_finance_fallback(finance_workspace, monkeypatch):
    adapter = ScriptedModelAdapter([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create",
            "goals": ["Read the latest TT article", "Compare its gold view"],
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        # The final slot must not execute another acquisition action.
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
    ])
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)

    real_loop = runtime_module.AgentLoop

    class LimitedLoop(real_loop):
        def run(self, state, config, **kwargs):
            return super().run(state, config, max_steps=10, max_model_calls=4, **kwargs)

    monkeypatch.setattr(runtime_module, "AgentLoop", LimitedLoop)
    session_id = runtime.store.create_chat_session(
        title="Finance budget fallback", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session_id,
        message="Read the latest TT article and compare its gold view.",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    run = runtime.store.get_agent_run(result["run_id"])
    assert run["metadata"]["stop_reason"] == "max_model_calls"
    assert result["answer"].startswith("## ")
    assert "已获取的证据" in result["answer"]
    assert "TT/articles/gold.md:L1" in result["answer"]
    assert "跨文章观点变化" not in result["answer"]
    assert len(result["citations"]) == 1
    steps = runtime.store.list_agent_steps(result["run_id"])
    boundary = [
        step for step in steps
        if step["step_type"] == "model_call"
        and step["output_payload"].get("payload", {}).get("type") == "tool_call"
        and step["output_payload"].get("payload", {}).get("tool_name") == "article.read"
    ]
    assert boundary[-1]["output_payload"]["payload"]["arguments"] == {
        "article_id": finance_workspace,
    }
    assert boundary[-1]["output_payload"]["observation"]["budget_boundary"]["decision"] == "deny"


def test_runtime_grounds_missing_model_citations_from_read_ledger(finance_workspace):
    result, steps = _run(
        [
            {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
            {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
            {"type": "final", "output": {"answer": "这是作者对黄金的观点，市场事实仍未核实。"}},
        ],
        "请读取 TT 的文章并总结黄金观点，区分作者观点和未核实事实。",
    )
    assert result["answer"]
    assert any(":L" in str(item) for item in result["citations"]), (
        result,
        [(step["step_type"], step.get("title"), step.get("status")) for step in steps],
    )
    assert "本轮已读取的文章证据" in result["answer"]
    grounding = [step for step in steps if step["step_type"] == "evidence_citation_grounding"]
    assert len(grounding) == 1
    assert grounding[0]["gate_result"]["policy_id"] == "finance.article_citation_grounding.v1"


def test_market_fact_request_requires_market_evidence_or_explicit_disclosure(finance_workspace):
    result, steps = _run(
        [
            {"type": "final", "output": {"answer": "黄金最近一个月上涨，原因是利率变化。"}},
            {"type": "final", "output": {"answer": "本轮没有获取行情或公开原文，市场事实尚未核实，不能把文章观点当成价格事实。"}},
        ],
        "请分析黄金最近一个月行情，并说明大致原因。",
    )
    assert "尚未核实" in result["answer"]
    finals = [
        step for step in steps
        if step["step_type"] == "model_call"
        and step["output_payload"]["payload"].get("type") == "final"
    ]
    assert len(finals) == 2


def test_named_author_comparison_rejects_unread_nonempty_folder(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    other_dir = article_root() / "培风客" / "articles"
    other_dir.mkdir(parents=True)
    (other_dir / "silver.md").write_text(
        "# Silver view\n2026-09-30\nThe author discusses silver supply.\n",
        encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    tt_id = corpus.list_articles(author="TT")[0]["article_id"]
    other_id = corpus.list_articles(author="培风客")[0]["article_id"]
    answer = (
        "TT discusses interest rates as a possible influence on gold, while 培风客 discusses "
        "silver supply. They are not making the same asset claim, so an agreement or conflict "
        "cannot be inferred from these two short excerpts alone. Neither author supplies a "
        "checked price series, and no causal effect has been independently established. "
        "(articles/TT/articles/gold.md:L3) "
        "(articles/培风客/articles/silver.md:L3)"
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create",
            "goals": ["Preserve both author scopes", "Read and synthesize grounded evidence"],
            "scope": {"authors": ["TT", "培风客"], "topics": ["gold"]},
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "培风客"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": tt_id}},
        {"type": "final", "output": {"answer": "TT and 培风客 agree about the market."}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": other_id}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": answer}},
    ], "比较 TT 和培风客的文章观点，并说明证据限制。")
    assert "silver supply" in result["answer"]
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert {ref["article_id"] for ref in audit["output_payload"]["article_reads"]} == {tt_id, other_id}


def test_natural_multi_author_change_query_checks_both_authors_before_final(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    other_dir = article_root() / "培风客" / "articles"
    other_dir.mkdir(parents=True)
    (other_dir / "silver.md").write_text(
        "# Gold view\n2026-09-28\nThe author takes a conditional gold view.\n",
        encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    tt_id = corpus.list_articles(author="TT")[0]["article_id"]
    other_id = corpus.list_articles(author="培风客")[0]["article_id"]
    query = "TT 和培风客在已有文章中对黄金的看法有什么变化"
    answer = (
        "TT discusses rates and gold (TT/articles/gold.md:L3). "
        "培风客 gives a conditional gold view (培风客/articles/silver.md:L3). "
        "These are author viewpoints, not independently verified market facts."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create",
            "goals": ["Keep both named author scopes", "Read sources before answering"],
            "scope": {"authors": ["TT", "培风客"], "topics": ["gold"]},
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "final", "output": {"answer": "TT has a view; comparison complete."}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "培风客"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": tt_id}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": other_id}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": answer}},
    ], query)

    assert result["answer"] == answer
    list_results = [
        step for step in steps
        if step["step_type"] == "tool_result"
        and "author_coverage" in step.get("output_payload", {}).get("observation", {})
    ]
    assert len(list_results) == 2
    assert any(
        step["step_type"] == "gate_check" and "research.plan" in str(step)
        for step in steps
    )


def test_broad_multi_author_answer_does_not_force_review_delegation(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    root = article_root()
    for number in range(6):
        (root / "TT" / "articles" / f"other-{number}.md").write_text(
            f"# Background {number}\n2026-09-28\nOther article context.\n", encoding="utf-8",
        )
    other = root / "PFK" / "articles"
    other.mkdir(parents=True)
    (other / "view.md").write_text(
        "# View\n2026-09-28\nGold depends on real yields and risk appetite.\n", encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", root)
    corpus.sync()
    pfk_id = corpus.list_articles(author="PFK")[0]["article_id"]
    answer = (
        "TT discusses rates as one influence on gold (TT/articles/gold.md:L3). "
        "PFK discusses real yields and risk appetite (PFK/articles/view.md:L3). "
        "These passages suggest a common concern with yields but do not establish "
        "a verified market cause. This is a comparison of the two observed passages, "
        "not a full-corpus or independently verified conclusion."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Compare both named authors", "Check source scope"],
            "scope": {"authors": ["TT", "PFK"], "topics": ["gold"]},
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "PFK"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": finance_workspace}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": pfk_id}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": answer}},
    ], "Compare TT and PFK's views of gold")
    assert result["answer"] == answer, [
        step.get("gate_result", {}).get("reason") for step in steps if step["step_type"] == "gate_check"
    ]
    assert not any(step["step_type"] == "subagent_result" for step in steps)
    assert not any("Ask a bounded skeptic" in str(step) for step in steps)
    finals = [step for step in steps if step["step_type"] == "model_call"
              and (step["output_payload"].get("payload") or {}).get("type") == "final"]
    assert len(finals) == 1


def test_multi_author_scope_does_not_depend_on_comparison_vocabulary(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    other_dir = article_root() / "PFK" / "articles"
    other_dir.mkdir(parents=True)
    (other_dir / "gold.md").write_text(
        "# PFK gold view\n2026-09-28\nPFK describes a conditional gold view.\n",
        encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    tt_id = corpus.list_articles(author="TT")[0]["article_id"]
    pfk_id = corpus.list_articles(author="PFK")[0]["article_id"]
    answer = (
        "TT describes rates as relevant to gold (articles/TT/articles/gold.md:L3). "
        "PFK describes a conditional gold view (articles/PFK/articles/gold.md:L3)."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create",
            "goals": ["Preserve both named sources", "Read the evidence before answering"],
            "scope": {"authors": ["TT", "PFK"], "topics": ["gold"]},
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "final", "output": {"answer": "TT was checked."}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "PFK"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": tt_id}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": pfk_id}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": answer}},
    ], "TT and PFK separately describe their gold views")

    assert result["answer"] == answer
    assert any(
        step["step_type"] == "gate_check"
        and "research.plan" in str(step)
        for step in steps
    )


def test_named_author_time_comparison_reads_each_listed_month(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    other_dir = article_root() / "培风客" / "articles"
    other_dir.mkdir(parents=True)
    for name, published, body in (
        ("july.md", "2026-07-21", "July: wait for stronger gold confirmation."),
        ("august.md", "2026-08-08", "August: a more positive but conditional gold view."),
    ):
        (other_dir / name).write_text(f"# View\n{published}\n{body}\n", encoding="utf-8")
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    tt_id = corpus.list_articles(author="TT")[0]["article_id"]
    other_ids = {row["rel_path"].split("/")[-1]: row["article_id"]
                 for row in corpus.list_articles(author="培风客")}
    answer = (
        "TT discusses interest rates as a possible gold influence "
        "(articles/TT/articles/gold.md:L3). 培风客 said in July that confirmation "
        "was still needed (articles/培风客/articles/july.md:L3), then in August "
        "expressed a more positive but still conditional view "
        "(articles/培风客/articles/august.md:L3). These are author viewpoints, "
        "not independently verified market outcomes or proof of causation."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create",
            "goals": ["Preserve both author scopes", "Read representative periods and synthesize"],
            "scope": {"authors": ["TT", "培风客"], "topics": ["gold"],
                      "date_range": {"published_after": "2026-07-01", "published_before": "2026-09-30"}},
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "培风客"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": tt_id}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": other_ids["august.md"]}},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": answer}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": other_ids["july.md"]}},
        {"type": "final", "output": {"answer": answer}},
    ], "比较 TT 和培风客的文章观点如何随时间变化。")
    assert "July" in result["answer"]
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert {ref["article_id"] for ref in audit["output_payload"]["article_reads"]} == {
        tt_id, *other_ids.values(),
    }


def test_finance_loop_can_read_more_than_twenty_articles_without_budget_denial(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    author_dir = article_root() / "TT" / "articles"
    for index in range(21):
        (author_dir / f"long-{index:02d}.md").write_text(
            f"# Long view {index}\n2026-09-{(index % 9) + 1:02d}\nA dated gold observation {index}.\n",
            encoding="utf-8",
        )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    articles = corpus.list_articles(author="TT")
    citations = " ".join(f"(articles/TT/articles/{row['rel_path'].split('/')[-1]}:L3)" for row in articles)
    synthesis = (
        "Across the dated entries, these are changes in the author's stated observations and hypotheses, "
        "not verified market reversals. The answer keeps article viewpoints separate from checked prices, "
        "and the source set does not by itself prove a causal relationship. "
    ) * 10
    actions = [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Read each dated article", "Compare the time changes"],
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        *[
            {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": row["article_id"]}}
            for row in articles
        ],
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": synthesis + citations + "这些市场事实未核实。"}},
    ]
    result, steps = _run(actions, "请阅读TT的文章并总结黄金观点，说明未核实事实")
    assert "市场事实未核实" in result["answer"]
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert len(audit["output_payload"]["article_reads"]) == len(articles)
    assert not any(
        step["step_type"] == "run_stopped" and step["output_payload"].get("stop_reason") == "tool_denied"
        for step in steps
    )


def test_multi_period_comparison_tracks_goals_and_rejects_article_inventory(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    author_dir = article_root() / "TT" / "articles"
    for index in range(5):
        (author_dir / f"period-{index}.md").write_text(
            f"# Period {index}\n2026-09-{20 + index}\nGold, silver and rates view {index}.\n",
            encoding="utf-8",
        )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    articles = corpus.list_articles(author="TT")
    citations = " ".join(
        f"(articles/{row['rel_path']}:L3)" for row in articles
    )
    short_answer = "Six articles were read. " + citations
    synthesis = (
        "The older period treated interest rates as a headwind for gold, while the newer "
        "period separated liquidity pressure from the author's long-run gold thesis. "
        "For silver, the author moved from a general upside view to a conditional case "
        "dependent on supply and positioning. These are changes in the author's stated "
        "hypotheses, not proof of a market reversal or of Federal Reserve action. "
        "The comparison remains uncertain because the articles do not provide a primary "
        "rate series or checked spot prices. "
    ) * 5
    actions = [
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "create", "goals": ["Read each dated article", "Synthesize changes and gaps"],
        }},
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        *[
            {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": row["article_id"]}}
            for row in articles
        ],
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 1, "status": "done",
        }},
        {"type": "tool_call", "tool_name": "research.plan", "arguments": {
            "operation": "update", "goal_id": 2, "status": "done",
        }},
        {"type": "final", "output": {"answer": short_answer}},
        {"type": "final", "output": {"answer": synthesis + citations + " Market facts remain 未核实."}},
    ]
    result, steps = _run(
        actions,
        "请比较 TT 的全部6篇文章中黄金白银美联储观点如何随时间变化，逐篇读取，说明未核实事实",
    )
    assert "older period" in result["answer"]
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert audit["output_payload"]["research_plan"]["pending_count"] == 0
    assert audit["output_payload"]["research_plan"]["revision"] == 3
    assert len(audit["output_payload"]["article_reads"]) == 6
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2


def test_explicit_unverified_market_request_requires_disclosure(finance_workspace):
    result, steps = _run([
        {"type": "final", "output": {"answer": "The article gives a price target."}},
        {"type": "final", "output": {"answer": "The author gives a price target; the market price and cause remain 未核实."}},
    ], "请说明哪些市场事实未核实。")
    assert "未核实" in result["answer"]
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2


def test_main_reads_article_and_verifier_independently_reads_it(finance_workspace):
    article_id = finance_workspace

    class ReviewAdapter:
        def __init__(self):
            self.main_calls = 0
            self.child_calls = 0
            self.child_evidence_id = ""

        def complete(self, messages, schema_name):
            if schema_name == "general_chat_agent_loop.v1":
                self.main_calls += 1
                actions = [
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "create",
                        "goals": ["Preserve the requested source", "Check and answer the claim"],
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 1, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 2, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
                    {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": article_id}},
                    {"type": "delegate", "role": "verifier", "task": "Independently read the TT article and check its claim."},
                    _scripted_checkpoint_action(),
                    {"type": "final", "output": {"answer": "TT 认为利率可能影响黄金；子智能体已独立阅读原文。 (articles/TT/articles/gold.md:L3)"}},
                ]
                payload = actions[self.main_calls - 1]
            elif schema_name == "claim_verdict_set.v1":
                self.child_calls += 1
                if self.child_calls == 1:
                    payload = {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": article_id}}
                else:
                    self.child_evidence_id = messages[-1]["content"]["tool_result"]["evidence_id"]
                    payload = {"type": "final", "output": {"verdicts": [{
                        "claim_ref": "TT rates view", "decision": "pass",
                        "evidence_refs": [self.child_evidence_id],
                        "reason": "The article says rates may affect gold, not that causation is proved.",
                    }]}}
            else:
                raise AssertionError(schema_name)
            return ModelResponse(response_id=f"test_{self.main_calls}_{self.child_calls}", payload=payload)

    adapter = ReviewAdapter()
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)
    session = runtime.store.create_chat_session(
        title="Finance review", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session, message="请核查TT的黄金观点", mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    assert adapter.child_evidence_id.startswith("evidence_")
    assert "子智能体已独立阅读" in result["answer"]
    assert any(step["step_type"] == "subagent_delegate" and step["status"] == "done" for step in steps), [
        step["output_payload"].get("error") for step in steps if step["step_type"] == "subagent_delegate"
    ]
    delegated = next(step for step in steps if step["step_type"] == "subagent_delegate")
    assert delegated["output_payload"]["review_scope"] == "independent_article_read"
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert adapter.child_evidence_id in audit["output_payload"]["subagent_evidence"][0]["evidence_ids"]


def test_requested_independent_review_cannot_pass_from_delegated_artifact_only(finance_workspace):
    article_id = finance_workspace
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": article_id}},
        {"type": "delegate", "role": "verifier", "task": "Independently read the source."},
        {"type": "final", "output": {"verdicts": []}},
        {"type": "final", "output": {"answer": "未完成独立核查；verifier 没有自行读取来源。 (articles/TT/articles/gold.md:L3)"}},
    ], "Please independently verify the latest TT article.")
    delegated = next(step for step in steps if step["step_type"] == "subagent_delegate")
    assert delegated["status"] == "failed"
    assert delegated["output_payload"]["stop_reason"] == "gate_blocked", delegated["output_payload"].get("error")
    assert delegated["output_payload"]["review_scope"] == "delegated_context_only"
    assert "未完成独立核查" in result["answer"]


def test_main_can_delegate_multi_article_reading_to_analyst(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    author_dir = article_root() / "TT" / "articles"
    for published_date, view in (("2026-09-27", "Gold may rise."), ("2026-09-28", "Gold may fall.")):
        (author_dir / f"{published_date}.md").write_text(
            f"# Gold view\n{published_date}\n{view}\n", encoding="utf-8"
        )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    ids = [row["article_id"] for row in corpus.list_articles(author="TT")]

    class MultiArticleAdapter:
        def __init__(self):
            self.main_calls = 0
            self.child_calls = 0
            self.child_evidence = []

        def complete(self, messages, schema_name):
            if schema_name == "general_chat_agent_loop.v1":
                self.main_calls += 1
                actions = [
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "create",
                        "goals": ["Preserve the requested scope", "Read and synthesize evidence"],
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 1, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 2, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
                    {"type": "delegate", "role": "analyst", "task": "Read three TT articles and compare the changes."},
                    _scripted_checkpoint_action(),
                    {"type": "final", "output": {"answer": (
                        "The latest article treats rates as a possible factor "
                        "(articles/TT/articles/gold.md:L3). The earlier article says gold may fall "
                        "(articles/TT/articles/2026-09-28.md:L3), whereas the oldest says it may rise "
                        "(articles/TT/articles/2026-09-27.md:L3). This is a genuine directional conflict "
                        "among the author's short statements, not evidence that the market actually reversed. "
                        "No measured price series or independent cause is supplied by these three articles."
                    )}},
                ]
                payload = actions[self.main_calls - 1]
            elif schema_name == "claim_set.v1":
                self.child_calls += 1
                if self.child_calls == 1:
                    candidates = messages[-1]["content"]["tool_result"]["article_candidates"]
                    assert {item["article_id"] for item in candidates} == set(ids)
                    payload = {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}}
                elif self.child_calls <= 4:
                    if self.child_calls > 2:
                        self.child_evidence.append(messages[-1]["content"]["tool_result"]["evidence_id"])
                    payload = {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": ids[self.child_calls - 2]}}
                else:
                    self.child_evidence.append(messages[-1]["content"]["tool_result"]["evidence_id"])
                    payload = {"type": "final", "output": {"claims": [
                        {"claim_ref": f"TT-{index}", "statement": f"Article {index} was read",
                         "evidence_refs": ["articles/TT/articles/gold.md:L3" if index == 1 else ref],
                         "confidence": 0.8}
                        for index, ref in enumerate(self.child_evidence, 1)
                    ]}}
            else:
                raise AssertionError(schema_name)
            return ModelResponse(response_id=f"multi_{self.main_calls}_{self.child_calls}", payload=payload)

    adapter = MultiArticleAdapter()
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)
    session = runtime.store.create_chat_session(
        title="Multi-article review", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session, message="Compare recent TT articles", mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    delegated = next(step for step in steps if step["step_type"] == "subagent_delegate")
    assert delegated["status"] == "done", delegated["output_payload"]
    assert delegated["output_payload"]["role"] == "analyst"
    assert len(delegated["output_payload"]["observed_article_refs"]) == 3
    assert len(adapter.child_evidence) == 3
    assert all(
        ref.startswith("evidence_")
        for claim in delegated["output_payload"]["output"]["claims"]
        for ref in claim["evidence_refs"]
    )
    assert delegated["output_payload"]["review_scope"] == "delegated_source_read"
    assert len(result["citations"]) == 3
    assert any(step["step_type"] == "answer_claim_audit" for step in steps)
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    assert set(adapter.child_evidence).issubset(
        set(audit["output_payload"]["evidence_bundle"]["evidence_ids"])
    )
    assert not any(step["step_type"] == "gate_check" and step["status"] == "failed" for step in steps)


def test_analyst_cannot_read_unlisted_author_when_listing_is_small(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    other_dir = article_root() / "Other" / "articles"
    other_dir.mkdir(parents=True)
    (other_dir / "private.md").write_text(
        "# Other view\n2026-09-29\nDifferent author.\n", encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    other_id = corpus.list_articles(author="Other")[0]["article_id"]

    class OutsideBatchAdapter:
        def __init__(self):
            self.main_calls = 0

        def complete(self, messages, schema_name):
            if schema_name == "general_chat_agent_loop.v1":
                self.main_calls += 1
                payload = [
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "create",
                        "goals": ["Preserve the requested source", "Complete the scope check"],
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 1, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 2, "status": "done",
                    }},
                    {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
                    {"type": "delegate", "role": "analyst", "task": "Read the listed TT article."},
                    _scripted_checkpoint_action(),
                    {"type": "final", "output": {"answer": "The out-of-scope read was blocked."}},
                ][self.main_calls - 1]
            elif schema_name == "claim_set.v1":
                payload = {"type": "tool_call", "tool_name": "article.read",
                           "arguments": {"article_id": other_id}}
            else:
                raise AssertionError(schema_name)
            return ModelResponse(response_id=f"outside_{self.main_calls}", payload=payload)

    runtime = AgentRuntime(company_id="finance_observer", model_adapter=OutsideBatchAdapter())
    session = runtime.store.create_chat_session(
        title="Scoped analyst", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session, message="Ask analyst to read the listed TT article",
        mode="chat", model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    delegated = next(step for step in steps if step["step_type"] == "subagent_delegate")
    assert delegated["status"] == "failed"
    assert delegated["output_payload"]["stop_reason"] == "tool_denied"
    assert "blocked" in result["answer"]


def test_multi_article_comparison_retries_short_uncited_final(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    (article_root() / "TT" / "articles" / "later.md").write_text(
        "# Later view\n2026-09-30\nThe author now sees stronger gold demand.\n",
        encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    ids = {row["title"]: row["article_id"] for row in corpus.list_articles(author="TT")}
    expanded = (
        "The September 29 article treats rates as a possible driver, without proving a price effect "
        "(articles/TT/articles/gold.md:L3). The September 30 article turns to demand, a different "
        "hypothesis rather than a verified reversal (articles/TT/articles/later.md:L3). "
        "Both discuss possible influences on gold, but neither supplies matched market data. "
        "The causal link and relative importance of rates versus demand remain unverified."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": ids["Gold outlook"]}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": ids["Later view"]}},
        {"type": "final", "output": {"answer": "They changed their view."}},
        {"type": "final", "output": {"answer": expanded}},
    ], "Compare the two TT articles over time.")
    assert result["answer"] == expanded
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2


def test_all_article_answer_requires_a_line_citation_for_each_read_article(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    (article_root() / "TT" / "articles" / "later.md").write_text(
        "# Later view\n2026-09-30\nGold demand may strengthen.\n",
        encoding="utf-8",
    )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    ids = {row["title"]: row["article_id"] for row in corpus.list_articles(author="TT")}
    first = "The earlier article treats rates as a possible factor (articles/TT/articles/gold.md:L3)."
    second = (
        first + " The later article raises a distinct demand hypothesis "
        "(articles/TT/articles/later.md:L3). These are author viewpoints, not verified "
        "market outcomes. Neither source provides a matched price series or independent "
        "causal identification, so the comparison is limited to what each author wrote."
    )
    result, steps = _run([
        {"type": "tool_call", "tool_name": "article.list", "arguments": {"author": "TT"}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": ids["Gold outlook"]}},
        {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": ids["Later view"]}},
        {"type": "final", "output": {"answer": first}},
        {"type": "final", "output": {"answer": second}},
    ], "请对比 TT 的全部文章。")
    assert result["answer"] == second
    finals = [step for step in steps if step["step_type"] == "model_call"
              and step["output_payload"]["payload"].get("type") == "final"]
    assert len(finals) == 2


def test_large_analyst_task_is_runtime_chunked_before_child_starts(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    author_dir = article_root() / "TT" / "articles"
    for index in range(5):
        (author_dir / f"batch-{index}.md").write_text(
            f"# Batch {index}\n2026-09-{20 + index}\nA distinct author view.\n",
            encoding="utf-8",
        )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    first_path = corpus.list_articles(author="TT")[0]["rel_path"]

    class ChunkedAdapter:
        def __init__(self):
            self.main_calls = 0
            self.assigned_ids = []
            self.evidence_ids = []
            self.checkpoint_sent = False

        def complete(self, messages, schema_name):
            if schema_name == "general_chat_agent_loop.v1":
                self.main_calls += 1
                if self.main_calls == 1:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "create",
                        "goals": ["Preserve the article scope", "Complete the bounded review"],
                    }}
                elif self.main_calls == 2:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 1, "status": "done",
                    }}
                elif self.main_calls == 3:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 2, "status": "done",
                    }}
                elif self.main_calls == 4:
                    payload = {"type": "tool_call", "tool_name": "article.list",
                               "arguments": {"author": "TT"}}
                elif self.main_calls == 5:
                    payload = {"type": "delegate", "role": "analyst",
                               "task": "Read all six articles at once."}
                elif not self.checkpoint_sent:
                    self.checkpoint_sent = True
                    payload = _scripted_checkpoint_action()
                else:
                    answer = (
                        "The Runtime chunked the oversized analyst request into a bounded batch. "
                        "The child read three assigned articles and returned an evidence-bound "
                        "claim for each one. The other three articles were not included in this "
                        "batch, so no full-corpus comparison is claimed. The observation is about "
                        "delegation scope, not an independently checked market conclusion. "
                        f"(articles/{first_path}:L3)"
                    )
                    payload = {"type": "final", "output": {"answer": answer}}
            elif schema_name == "claim_set.v1":
                observation = messages[-1]["content"]["tool_result"]
                if "article_candidates" in observation:
                    self.assigned_ids = [row["article_id"] for row in observation["article_candidates"]]
                    self.evidence_ids = []
                elif "evidence_id" in observation:
                    self.evidence_ids.append(observation["evidence_id"])
                if len(self.evidence_ids) < len(self.assigned_ids):
                    payload = {"type": "tool_call", "tool_name": "article.read",
                               "arguments": {"article_id": self.assigned_ids[len(self.evidence_ids)]}}
                else:
                    payload = {"type": "final", "output": {"claims": [
                        {"claim_ref": f"batch-{index}", "statement": "Source read",
                         "evidence_refs": [ref], "confidence": 0.8}
                        for index, ref in enumerate(self.evidence_ids)
                    ]}}
            else:
                raise AssertionError(schema_name)
            return ModelResponse(response_id=f"chunk_{self.main_calls}_{len(self.evidence_ids)}", payload=payload)

    runtime = AgentRuntime(company_id="finance_observer", model_adapter=ChunkedAdapter())
    session = runtime.store.create_chat_session(
        title="Chunked analyst", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session, message="Compare six TT articles.", mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    assert "chunked" in result["answer"]
    delegates = [step for step in steps if step["step_type"] == "subagent_delegate"]
    assert len(delegates) == 1
    assigned = delegates[0]["output_payload"].get("assigned_article_ids")
    assert assigned and len(assigned) <= 3
    assert not any("gate_blocked" in json.dumps(step, ensure_ascii=False) for step in delegates)


def test_fifteen_articles_use_distinct_batches_and_recover_at_delegate_limit(finance_workspace):
    from policy_impact.domains.finance.article_corpus import article_root

    author_dir = article_root() / "TT" / "articles"
    for index in range(14):
        (author_dir / f"batch-{index:02d}.md").write_text(
            f"# View {index}\n2026-09-{index + 1:02d}\nObservation {index}.\n",
            encoding="utf-8",
        )
    corpus = ArticleCorpus("finance_observer", article_root())
    corpus.sync()
    articles = corpus.list_articles(author="TT")
    assert len(articles) == 15
    batches = [articles[index:index + 3] for index in (0, 3, 6, 9, 12)]

    class BatchAdapter:
        def __init__(self):
            self.main_calls = 0
            self.active_ids = []
            self.child_refs = []
            self.read_count = 0
            self.saw_compact_refs = False
            self.checkpoint_sent = False

        def complete(self, messages, schema_name):
            if schema_name == "general_chat_agent_loop.v1":
                self.main_calls += 1
                research_call = self.main_calls - 3
                if research_call == 3:
                    assert "subagent_result" in messages[-1]["content"], messages[-1]
                    refs = messages[-1]["content"]["subagent_result"]["observed_article_refs"]
                    self.saw_compact_refs = len(refs) == 3 and all(
                        set(ref) == {"article_id", "path", "line_start", "line_end", "evidence_id"}
                        for ref in refs
                    )
                if research_call == -2:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "create",
                        "goals": ["Preserve the article scope", "Complete the batch review"],
                    }}
                elif research_call == -1:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 1, "status": "done",
                    }}
                elif research_call == 0:
                    payload = {"type": "tool_call", "tool_name": "research.plan", "arguments": {
                        "operation": "update", "goal_id": 2, "status": "done",
                    }}
                elif research_call == 1:
                    payload = {"type": "tool_call", "tool_name": "article.list",
                               "arguments": {"author": "TT"}}
                elif research_call in (2, 3, 4, 5, 6, 7):
                    batch = batches[{2: 0, 3: 0, 4: 1, 5: 2, 6: 3, 7: 4}[research_call]]
                    payload = {"type": "delegate", "role": "analyst",
                               "task": "Read and compare: " + " ".join(row["article_id"] for row in batch)}
                elif research_call == 8:
                    payload = {"type": "delegate", "role": "analyst",
                               "task": "Read more articles."}
                elif not self.checkpoint_sent:
                    self.checkpoint_sent = True
                    payload = _scripted_checkpoint_action()
                else:
                    citations = " ".join(
                        f"(articles/TT/articles/{row['rel_path'].split('/')[-1]}:L3)"
                        for row in articles
                    )
                    payload = {"type": "final", "output": {
                        "answer": "All fifteen articles were read. " + citations,
                    }}
            elif schema_name == "claim_set.v1":
                observation = messages[-1]["content"]["tool_result"]
                if "article_candidates" in observation:
                    self.active_ids = [row["article_id"] for row in observation["article_candidates"]]
                    self.child_refs = []
                    self.read_count = 0
                elif "evidence_id" in observation:
                    self.child_refs.append(observation["evidence_id"])
                if self.read_count < len(self.active_ids):
                    payload = {"type": "tool_call", "tool_name": "article.read",
                               "arguments": {"article_id": self.active_ids[self.read_count]}}
                    self.read_count += 1
                else:
                    payload = {"type": "final", "output": {"claims": [
                        {"claim_ref": f"item-{index}", "statement": "Source read",
                         "evidence_refs": [ref], "confidence": 0.8}
                        for index, ref in enumerate(self.child_refs)
                    ]}}
            else:
                raise AssertionError(schema_name)
            return ModelResponse(response_id=f"batch_{self.main_calls}_{self.read_count}", payload=payload)

    adapter = BatchAdapter()
    runtime = AgentRuntime(company_id="finance_observer", model_adapter=adapter)
    session = runtime.store.create_chat_session(
        title="Fifteen article review", mode="chat", model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session, message="Compare all fifteen TT articles", mode="chat",
        model_id=DEFAULT_MODEL_ID, active_skill_id="general_chat",
    )
    steps = runtime.store.list_agent_steps(result["run_id"])
    audit = next(step for step in steps if step["step_type"] == "evidence_audit")
    read_ids = {
        item["article_id"]
        for bundle in audit["output_payload"]["subagent_evidence"]
        for item in bundle.get("evidence", [])
        if item.get("source_type") == "author_article"
    }
    assert read_ids == {row["article_id"] for row in articles}, [
        (step["step_index"], step["step_type"], step["status"],
         step["output_payload"].get("stop_reason"), step.get("gate_result", {}).get("reason"))
        for step in steps if step["step_type"] in {"subagent_delegate", "run_stopped"}
    ]
    assert len([step for step in steps if step["step_type"] == "subagent_delegate"]) == 5
    assert adapter.saw_compact_refs
    assert "All fifteen articles were read" in result["answer"]
    assert len(result["citations"]) == 15


def test_subagent_line_refs_normalize_only_within_observed_range():
    from policy_impact.harness.subagents import _normalize_observed_article_refs

    payload = {"claims": [{"evidence_refs": [
        "articles/TT/articles/13.md:L25",
        "articles/TT/articles/13.md:L99",
        "articles/TT/articles/other.md:L25",
    ]}]}
    refs = [{"path": "D:/research/articles/TT/articles/13.md", "line_start": 20,
             "line_end": 30, "evidence_id": "evidence_test"}]
    normalized = _normalize_observed_article_refs(payload, refs)
    assert normalized["claims"][0]["evidence_refs"] == [
        "evidence_test", "articles/TT/articles/13.md:L99",
        "articles/TT/articles/other.md:L25",
    ]
