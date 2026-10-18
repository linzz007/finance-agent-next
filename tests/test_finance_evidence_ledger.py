import pytest

from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.article_gateway import FinanceArticleGateway
from policy_impact.domains.finance.evidence import EvidenceLedger


def test_article_read_creates_stable_turn_scoped_evidence_id(tmp_path):
    corpus = ArticleCorpus("evidence_ledger_test", root=tmp_path)
    article = corpus.add_article(
        author="TT",
        title="黄金观察",
        published_at="2026-09-20",
        content="# 黄金观察\n\n利率预期变化会影响黄金的短期表现。\n\n这是作者观点。",
    )
    gateway = FinanceArticleGateway("evidence_ledger_test", corpus=corpus)

    first = gateway.read(article_id=article["article_id"], start_line=1, limit=4)
    second = gateway.read(article_id=article["article_id"], start_line=1, limit=4)

    assert first["evidence_id"].startswith("evidence_")
    assert first["evidence_id"] == second["evidence_id"]
    bundle = gateway.evidence_ledger.bundle()
    assert bundle["evidence_ids"] == [first["evidence_id"]]
    item = bundle["evidence"][0]
    assert item["source_type"] == "author_article"
    assert item["provenance"] == "author_view"
    assert item["line_start"] == 1
    assert item["line_end"] == 4
    assert item["content_hash"] == article["current_hash"]


def test_evidence_id_changes_when_read_line_range_changes(tmp_path):
    corpus = ArticleCorpus("evidence_range_test", root=tmp_path)
    article = corpus.add_article(
        author="TT",
        title="市场观察",
        published_at="2026-09-20",
        content="# 市场观察\n\n第一段。\n\n第二段。\n\n第三段。",
    )
    gateway = FinanceArticleGateway("evidence_range_test", corpus=corpus)

    first = gateway.read(article_id=article["article_id"], start_line=1, limit=2)
    second = gateway.read(article_id=article["article_id"], start_line=3, limit=2)

    assert first["evidence_id"] != second["evidence_id"]
    assert set(gateway.evidence_ledger.evidence_ids) == {
        first["evidence_id"],
        second["evidence_id"],
    }


def test_ledger_unifies_market_document_and_wiki_aliases():
    ledger = EvidenceLedger()
    metric = ledger.add_market_metric(
        {"metric_id": "market_change_pct:spx", "symbol": "spx", "name": "标普500",
         "source_url": "https://example.test/market", "change_pct": 1.2},
        snapshot_id="snapshot_test",
    )
    document = ledger.add_source_document(
        {"document_id": "finance_doc_test", "url": "https://example.test/source",
         "content_hash": "a" * 64, "text": "官方原文", "published_at": "2026-09-30"}
    )
    fact = ledger.add_wiki_fact(
        {"fact_id": "finance.profile.focus_topics", "value": ["黄金"], "source_path": "wiki/profile.md"}
    )

    assert len(ledger.evidence_ids) == 3
    assert ledger.canonical_id("market_change_pct:spx") == metric.evidence_id
    assert ledger.canonical_id("finance_doc_test") == document.evidence_id
    assert ledger.canonical_id("finance.profile.focus_topics") == fact.evidence_id
    bundle = ledger.bundle()
    assert bundle["evidence_ref_schema"] == "finance.evidence_ref.v1"
    assert bundle["aliases"]["finance_doc_test"] == document.evidence_id
    assert ledger.get("finance.profile.focus_topics").evidence_id == fact.evidence_id


def test_public_source_retrieval_is_not_claim_verification():
    ledger = EvidenceLedger()
    source = ledger.add_public_source({
        "url": "https://example.org/policy",
        "sha256": "b" * 64,
        "retrieved_at": "2026-10-02T00:00:00+00:00",
        "line_start": 1,
        "line_end": 12,
        "anchor": "https://example.org/policy#L1-L12",
    }, excerpt="A primary source passage.")

    assert source.provenance == "retrieved_primary_source"
    assert source.line_start == 1
    assert source.line_end == 12
    assert source.metadata["anchor"].endswith("#L1-L12")


def test_market_metric_validation_checks_the_canonical_ledger_binding():
    ledger = EvidenceLedger()
    metric = {
        "metric_id": "market_change_pct:spx",
        "symbol": "spx",
        "name": "S&P 500",
        "source_url": "https://example.test/market",
        "change_pct": 1.2,
        "unit": "%",
        "price_unit": "points",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }
    item = ledger.add_market_metric(metric, snapshot_id="snapshot_test")

    assert ledger.validate_market_metric(item.evidence_id, {**metric, "snapshot_id": "snapshot_test"}) == (True, "")
    valid, reason = ledger.validate_market_metric(
        item.evidence_id, {**metric, "snapshot_id": "snapshot_test", "symbol": "xauusd"}
    )
    assert not valid and "symbol" in reason
    valid, reason = ledger.validate_market_metric(
        item.evidence_id, {**metric, "snapshot_id": "snapshot_test", "change_pct": 99.0}
    )
    assert not valid and "value mismatch" in reason


def test_merge_bundle_rejects_forged_child_evidence_id():
    child = EvidenceLedger()
    item = child.add_market_metric({
        "metric_id": "market_change_pct:spx", "symbol": "spx", "change_pct": 1.2,
        "unit": "%", "price_unit": "points", "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }, snapshot_id="snapshot_test")
    bundle = child.bundle()
    bundle["evidence"][0]["evidence_id"] = "evidence_forged"

    with pytest.raises(ValueError, match="invalid evidence_id"):
        EvidenceLedger().merge_bundle(bundle)

    parent = EvidenceLedger()
    assert parent.merge_bundle(child.bundle()) == (item.evidence_id,)


def test_merge_bundle_rejects_market_evidence_without_canonical_metadata():
    child = EvidenceLedger()
    child.add_reference(
        source_type="market_metric",
        provenance="calculated_observation",
        source_ref="snapshot_test:spx",
        content_hash="metric-hash",
        aliases=("market_change_pct:spx",),
        metadata={"snapshot_id": "snapshot_test", "symbol": "spx"},
    )

    with pytest.raises(ValueError, match="missing canonical metric metadata"):
        EvidenceLedger().merge_bundle(child.bundle())
