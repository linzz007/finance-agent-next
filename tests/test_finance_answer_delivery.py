import pytest

from policy_impact.domains.finance.answer_delivery import (
    audit_delivery_state,
    bounded_audit_answer,
    observed_evidence_answer,
)
from policy_impact.domains.finance.evidence import EvidenceLedger


def _bundle():
    ledger = EvidenceLedger()
    article = ledger.add_article({
        "article_id": "tt-gold", "author": "TT",
        "path": "C:/articles/TT/articles/gold.md",
        "published_at": "2026-09-30", "line_start": 1, "line_end": 2,
        "content_hash": "source-hash",
    }, excerpt="1: TT suspects forced selling.\n2: The author says this remains unproven.")
    return ledger.bundle(), article.evidence_id


def test_bounded_review_preserves_author_quote_and_labels_rebuttal_as_unsettled():
    bundle, article_id = _bundle()
    disputed = "TT proved forced selling caused gold to fall. (TT/articles/gold.md:L1)"
    candidate = "Gold fell in the observed period.\n" + disputed
    audit = {"coverage_complete": True, "issues": [{
        "span": disputed, "action": "revise", "claim_type": "causal_explanation",
        "evidence_refs": [article_id],
        "reason": "The article states a suspicion, not proof of forced selling.",
    }]}
    answer, status = bounded_audit_answer(candidate, audit, bundle)
    assert status == "disputed_partial"
    assert "Gold fell in the observed period." in answer
    assert "**争议 1（尚未裁定）**" in answer
    assert "TT suspects forced selling." in answer
    assert "TT/articles/gold.md:L1" in answer
    assert "反驳或保留理由（尚未独立裁定）" in answer
    assert "原稿归纳" in answer
    assert "此处主张" not in answer


def test_multiple_objections_to_one_span_are_grouped_without_losing_the_answer():
    bundle, article_id = _bundle()
    span = "TT proved forced selling caused gold to fall. (TT/articles/gold.md:L1)"
    answer, status = bounded_audit_answer(
        "Market summary remains available.\n" + span,
        {"coverage_complete": True, "issues": [
            {"span": span, "evidence_refs": [article_id], "reason": "No causal proof."},
            {"span": span, "evidence_refs": [article_id], "reason": "The author says unproven."},
        ]}, bundle,
    )
    assert status == "disputed_partial"
    assert "Market summary remains available." in answer
    assert answer.count("原稿归纳") == 1
    assert "No causal proof." in answer
    assert "The author says unproven." in answer


def test_table_divider_is_never_removed_by_audit_resolution():
    bundle, _ = _bundle()
    candidate = "| 日期 | 黄金 |\n|---|---|\n| 9月10日 | 下跌 |"
    answer, status = bounded_audit_answer(
        candidate,
        {"coverage_complete": True, "issues": [{
            "span": "|---|---|", "action": "qualify", "reason": "Unsupported fact",
        }]}, bundle,
    )
    assert status == "disputed_partial"
    assert candidate in answer


def test_disputed_table_row_stays_in_table_with_source_and_objection_after_table():
    bundle, article_id = _bundle()
    row = "| TT proved forced selling | 09-30 |"
    candidate = f"## Summary\n\n| Claim | Date |\n|---|---|\n{row}\n\nNext section."
    answer, status = bounded_audit_answer(candidate, {
        "coverage_complete": True, "issues": [{
            "span": row, "evidence_refs": [article_id],
            "source_quote": "TT suspects forced selling.",
            "reason": "The author only suspected forced selling.",
        }],
    }, bundle)
    assert status == "disputed_partial"
    assert "| 待核实的归纳：TT proved forced selling（争议 1） | 09-30 |" in answer
    assert answer.index("**争议 1（尚未裁定）**") < answer.index("Next section.")
    assert "TT suspects forced selling." in answer
    assert "The author only suspected forced selling." in answer


def test_many_objections_do_not_force_a_source_only_answer():
    bundle, article_id = _bundle()
    span = "TT proved forced selling caused gold to fall. (TT/articles/gold.md:L1)"
    answer, status = bounded_audit_answer(
        "Market summary remains available.\n" + span,
        {"coverage_complete": True, "issues": [
            {"span": span, "evidence_refs": [article_id], "reason": f"Concern {index}"}
            for index in range(15)
        ]}, bundle,
    )
    assert status == "disputed_partial"
    assert "Market summary remains available." in answer
    assert "Concern 14" in answer


def test_incomplete_review_never_publishes_unchecked_candidate():
    bundle, _ = _bundle()
    answer, status = bounded_audit_answer(
        "This unchecked draft alleges certain manipulation.",
        {"coverage_complete": False, "issues": [], "limitations": "Missing source text"},
        bundle,
    )
    assert status == "source_only"
    assert "unchecked draft" not in answer
    assert "TT suspects forced selling." in answer
    assert "Missing source text" in answer


def test_preserve_candidate_mode_keeps_full_draft_when_review_is_incomplete():
    bundle, _ = _bundle()
    candidate = "完整研究稿第一段。\n\n完整研究稿的核心分析和结论。"
    answer, status = bounded_audit_answer(
        candidate,
        {"coverage_complete": False, "issues": [], "limitations": "审核模型没有覆盖全部目标"},
        bundle,
        preserve_candidate=True,
    )
    assert status == "review_incomplete_full_draft"
    assert candidate in answer
    assert "审核模型没有覆盖全部目标" in answer


def test_preserve_candidate_mode_keeps_full_draft_when_audit_call_fails():
    bundle, _ = _bundle()
    candidate = "主 Agent 已完成的长篇分析不能因为审核格式失败而丢失。"
    answer, status = bounded_audit_answer(candidate, None, bundle, preserve_candidate=True)
    assert status == "review_failed_full_draft"
    assert candidate in answer
    assert "终稿审核未完成" in answer


def test_preserve_candidate_mode_lists_disputes_without_rewriting_the_draft():
    bundle, article_id = _bundle()
    candidate = "核心结论：TT 证明了强制平仓导致黄金下跌。"
    answer, status = bounded_audit_answer(
        candidate,
        {"coverage_complete": True, "issues": [{
            "span": "核心结论：TT 证明了强制平仓导致黄金下跌。",
            "evidence_refs": [article_id],
            "reason": "文章只表达怀疑，没有证明因果关系。",
        }]},
        bundle,
        preserve_candidate=True,
    )
    assert status == "disputed_full_draft"
    assert candidate in answer
    assert "文章只表达怀疑" in answer


def _execution_gap(**overrides):
    return {
        "issue_kind": "review_execution_gap", "target_id": "review-1",
        "claim_or_question": "Compare the two selected views.",
        "span": "Comparison target, not an exact candidate span.", "action": "qualify",
        "reason": "The target audit did not return a valid result (model_transport).",
        **overrides,
    }


@pytest.mark.parametrize("coverage", ["failed", "partial"])
def test_execution_gaps_list_unfinished_targets_and_reasons_without_disputes(coverage):
    candidate = "## Comparison\n\n| Author | View |\n|---|---|\n| A | Conditional |\n\nConclusion."
    issues = [_execution_gap(), _execution_gap(target_id="review-2", reason="No usable target check was returned.")]
    audit = {"answer_audit_coverage": coverage, "review_status": coverage,
             "checks": [] if coverage == "failed" else [{"status": "reasoned"}],
             "issues": issues, "limitations": "Selected review execution remains incomplete."}
    answer, resolution = bounded_audit_answer(candidate, audit, {}, preserve_candidate=True)
    assert resolution == ("review_failed_full_draft" if coverage == "failed" else "review_incomplete_full_draft")
    assert answer.endswith(candidate)
    assert "争议" not in answer
    assert "未完成目标 review-1" in answer and "未完成目标 review-2" in answer
    assert "Compare the two selected views." in answer
    assert all(issue["reason"] in answer for issue in issues)
    assert audit["limitations"] in answer
    state = audit_delivery_state(audit)
    assert state["answer_audit_coverage"] == state["semantic_review"] == coverage
    assert state["verification_status"] == ("failed" if coverage == "failed" else "partial")


@pytest.mark.parametrize("coverage", ["failed", "partial", "complete"])
@pytest.mark.parametrize("objection", [
    {"status": "refuted", "action": "revise"},
    {"status": "insufficient_evidence", "action": "qualify"},
    {"status": "needs_revision", "action": "revise"},
    {"issue_kind": "unknown_future_kind"},
    {},
])
def test_mixed_issues_keep_real_and_unknown_objections_even_when_review_later_fails(coverage, objection):
    candidate = "A full comparison with an unresolved conclusion.\n\n| A | B |\n|---|---|\n| X | Y |"
    semantic_issue = {"span": "An earlier disputed passage.", "reason": "An important condition is missing.", **objection}
    audit = {"answer_audit_coverage": coverage, "review_status": coverage,
             "checks": [], "issues": [_execution_gap(), semantic_issue]}
    answer, resolution = bounded_audit_answer(candidate, audit, {}, preserve_candidate=True)
    assert resolution == "disputed_full_draft"
    assert answer.endswith(candidate)
    assert "审核未完成" in answer and "未完成目标 review-1" in answer
    assert "争议 1：An earlier disputed passage." in answer
    assert "争议 2" not in answer
    assert semantic_issue["reason"] in answer
    state = audit_delivery_state(audit)
    assert state["answer_audit_coverage"] == coverage
    assert state["semantic_review"] == ("complete" if coverage == "complete" else "failed")
    assert state["verification_status"] == "disputed"
    assert all(issue["reason"] in state["open_gaps"] for issue in audit["issues"])


def test_untagged_historical_execution_issue_stays_conservatively_disputed():
    issue = _execution_gap()
    del issue["issue_kind"]
    answer, resolution = bounded_audit_answer(
        "Full preserved draft.", {"answer_audit_coverage": "failed", "checks": [], "issues": [issue]},
        {}, preserve_candidate=True,
    )
    assert resolution == "disputed_full_draft"
    assert "争议 1" in answer
    assert issue["reason"] in answer


def test_legacy_bounded_mode_keeps_its_existing_execution_issue_behavior():
    bundle, article_id = _bundle()
    span = "A target selected for checking."
    issue = _execution_gap(span=span, evidence_refs=[article_id])
    answer, resolution = bounded_audit_answer(
        span, {"answer_audit_coverage": "failed", "issues": [issue]}, bundle,
        preserve_candidate=False,
    )
    assert resolution == "disputed_partial"
    assert "争议 1" in answer
    assert issue["reason"] in answer


def test_incomplete_review_preserves_answer_with_local_dispute_and_scope_warning():
    bundle, article_id = _bundle()
    answer, status = bounded_audit_answer(
        "The unchecked draft asserts market manipulation.",
        {"coverage_complete": False, "limitations": "Other claims not checked", "issues": [{
            "span": "The unchecked draft asserts market manipulation.",
            "evidence_refs": [article_id],
            "source_quote": "TT suspects forced selling.",
            "reason": "The author described a suspicion, not a proven cause.",
        }]},
        bundle,
    )
    assert status == "disputed_partial"
    assert "审核范围未完成" in answer
    assert "Other claims not checked" in answer
    assert "unchecked draft" in answer
    assert "TT suspects forced selling." in answer
    assert "The author described a suspicion" in answer
    assert "尚未独立裁定" in answer


def test_ambiguous_duplicate_issue_span_does_not_publish_candidate():
    bundle, article_id = _bundle()
    answer, status = bounded_audit_answer(
        "Unsupported assertion. Unsupported assertion.",
        {"coverage_complete": True, "issues": [{
            "span": "Unsupported assertion.", "evidence_refs": [article_id],
            "reason": "Unproven",
        }]},
        bundle,
    )
    assert status == "source_only"
    assert "Unsupported assertion." not in answer


def test_unlocatable_issue_uses_source_only_delivery():
    bundle, article_id = _bundle()
    answer, status = bounded_audit_answer(
        "A different candidate claim.",
        {"coverage_complete": True, "issues": [{
            "span": "Claim not in candidate", "evidence_refs": [article_id],
        }]},
        bundle,
    )
    assert status == "source_only"
    assert "A different candidate claim" not in answer


def test_source_only_delivery_includes_observed_market_numbers():
    ledger = EvidenceLedger()
    ledger.add_market_metric({
        "metric_id": "gold-return", "symbol": "xauusd", "name": "Gold",
        "start_date": "2026-09-10", "end_date": "2026-10-10",
        "start_close": 4314.35, "end_close": 4194.00,
        "change_pct": -2.79, "unit": "%", "price_unit": "USD/troy_oz",
        "source_url": "https://example.com/gold",
    }, snapshot_id="snapshot-1")
    answer = observed_evidence_answer(ledger.bundle(), reason="Audit unavailable")
    assert "4314.35" in answer
    assert "4194.0" in answer
    assert "-2.79" in answer
    assert "Audit unavailable" in answer
    assert "完整审核尚未完成" in answer
    assert "未通过完整审核" not in answer


def test_source_only_article_excerpt_prefers_body_to_title():
    ledger = EvidenceLedger()
    ledger.add_article({
        "article_id": "tt-gold", "author": "TT",
        "path": "C:/articles/TT/articles/gold.md", "published_at": "2026-09-30",
        "line_start": 1, "line_end": 3, "content_hash": "source-hash",
    }, excerpt="1: # Gold and flows\n2: 发布日期：2026-09-30\n3: TT suspects forced selling.")
    answer = observed_evidence_answer(ledger.bundle(), reason="Audit unavailable")
    assert "TT suspects forced selling." in answer
    assert "# Gold and flows" not in answer


def test_source_only_article_excerpt_skips_image_and_short_boilerplate():
    ledger = EvidenceLedger()
    ledger.add_article({
        "article_id": "tt-gold", "author": "TT",
        "path": "C:/articles/TT/articles/gold.md", "published_at": "2026-09-30",
        "line_start": 1, "line_end": 5, "content_hash": "source-hash",
    }, excerpt=(
        "1: # Gold\n2: ![]()\n3: Welcome readers.\n"
        "4: ---\n5: The author argues that falling yields may change the gold outlook."
    ))
    answer = observed_evidence_answer(ledger.bundle(), reason="Audit unavailable")
    assert "falling yields may change" in answer
    assert "TT/articles/gold.md:L5" in answer
    assert "Welcome readers" not in answer
@pytest.mark.parametrize(("answer", "expected"), [
    ("Verifier independently read article.read and independently verified this source.", True),
    ("未完成独立复核。Verifier independently verified this source.", True),
    ("TT's conditional view is not independently verified.", False),
    ("This has not yet been independently verified.", False),
    ("The verifier should independently read the source.", False),
    ("Independent verification was completed.", True),
    ("子智能体已独立阅读原文。", True),
    ("子智能体未独立阅读原文。", False),
    ("独立复核尚未完成。", False),
    ("后续可以独立阅读原文。", False),
])
def test_independent_review_declaration_is_not_missing_or_negated_disclosure(answer, expected):
    from policy_impact.domains.finance.answer_delivery import independent_review_claimed

    assert independent_review_claimed(answer) is expected

