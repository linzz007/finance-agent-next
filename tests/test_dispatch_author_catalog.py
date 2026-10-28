"""Author metadata parity never broadens a child's article grants."""

import pytest

from policy_impact.company_wiki import loader
from policy_impact.dispatch.finance_agent import FinanceH2ChildExecutor
from policy_impact.domains.finance.article_corpus import ArticleCorpus

ROWS = [
    {"article_id": "a-new", "author": "Alpha", "published_at": "2026-10-01"},
    {"article_id": "b", "author": "Beta", "published_at": "2026-09-30"},
    {"article_id": "a-old", "author": "Alpha", "published_at": "2026-09-01"},
]


class Corpus:
    def __init__(self):
        self.global_calls = 0

    def list_articles(self, *, limit=100, **_filters):
        return ROWS[:limit]

    def list_authors(self):
        self.global_calls += 1
        return [
            {"author": "Alpha", "article_count": 2, "latest_published_at": "2026-10-01"},
            {"author": "Beta", "article_count": 1, "latest_published_at": "2026-09-30"},
        ]


def execute(corpus, *, scope=None, **arguments):
    return FinanceH2ChildExecutor(corpus)._tool(
        {"tool_name": "article.list", "arguments": arguments}, allowed_article_ids=scope,
    )


def test_root_catalog_is_global_and_independent_of_returned_limit():
    corpus = Corpus()
    result = execute(corpus, limit=1, author="Alpha")
    assert result["articles"] == ROWS[:1]
    assert result["authors_scope"] == "corpus_all_active"
    assert result["authors"][0]["article_count"] == 2
    assert result["authors"][1]["author"] == "Beta"
    assert corpus.global_calls == 1


@pytest.mark.parametrize("scope,limit,expected", [
    ({"a-new", "a-old"}, 100, [
        {"author": "Alpha", "article_count": 2, "latest_published_at": "2026-10-01"},
    ]),
    ({"a-new", "a-old"}, 1, [
        {"author": "Alpha", "article_count": 1, "latest_published_at": "2026-10-01"},
    ]),
    (set(), 100, []),
    ({"absent"}, 100, []),
])
def test_child_statistics_never_call_or_disclose_global_catalog(scope, limit, expected):
    corpus = Corpus()
    result = execute(corpus, scope=scope, limit=limit)
    assert result["authors_scope"] == "returned_authorized_articles"
    assert result["authors"] == expected
    assert corpus.global_calls == 0
    assert all(row["article_id"] in scope for row in result["articles"])


def test_compatible_corpus_without_catalog_does_not_claim_whole_corpus_count():
    class MinimalCorpus:
        def list_articles(self, **_filters):
            return ROWS[:1]

    result = execute(MinimalCorpus())
    assert result["authors_scope"] == "returned_articles"
    assert result["authors"] == [
        {"author": "Alpha", "article_count": 1, "latest_published_at": "2026-10-01"},
    ]


def test_real_sqlite_catalog_counts_all_active_without_loading_body(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "articles"
    corpus = ArticleCorpus("finance_observer", root)
    for author, name, date in (
        ("Alpha", "old", "2026-09-01"), ("Alpha", "new", "2026-10-01"),
        ("Beta", "only", "2026-09-30"),
    ):
        folder = root / author / "articles"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.md").write_text(
            f"# {name}\n发布日期：{date}\nBody must not be read.\n", encoding="utf-8",
        )
    corpus.sync()
    with corpus.store.connection() as db:
        db.execute("UPDATE research_articles SET active=0 WHERE author='Beta'")

    def forbidden_body(*_args, **_kwargs):
        raise AssertionError("metadata lookup must not load an article body")

    monkeypatch.setattr(corpus, "get_article", forbidden_body)
    result = execute(corpus, limit=1, published_after="2026-10-01")
    assert len(result["articles"]) == 1
    assert result["authors_scope"] == "corpus_all_active"
    assert result["authors"] == [
        {"author": "Alpha", "article_count": 2, "latest_published_at": "2026-10-01"},
    ]


def test_unknown_parameters_still_fail_before_catalog_access():
    corpus = Corpus()
    with pytest.raises(ValueError):
        execute(corpus, publication_date="2026-10-01")
    assert corpus.global_calls == 0
