from fastapi.testclient import TestClient
import pytest

from policy_impact.app import api
from policy_impact.company_wiki import loader
from policy_impact.domains.finance.article_corpus import ArticleCorpus


@pytest.fixture
def article_client(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "articles"
    (root / "TT" / "articles").mkdir(parents=True)
    (root / "TT" / "articles" / "old.md").write_text(
        "# 黄金观察\n2026-09-01\n作者认为实际利率可能压制黄金。\n", encoding="utf-8"
    )
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(root))
    with TestClient(api.app) as client:
        yield client, root


BASE = "/companies/finance_observer/workbench/finance/articles"


def test_article_library_lists_reads_and_creates_from_api(article_client):
    client, root = article_client
    listing = client.get(BASE)
    assert listing.status_code == 200
    assert listing.json()["authors"] == ["TT"]
    assert listing.json()["sync"]["issues"] == []
    existing = listing.json()["articles"][0]
    assert "content" not in existing
    detail = client.get(f"{BASE}/{existing['article_id']}").json()["article"]
    assert "实际利率" in detail["content"]

    created = client.post(BASE, json={
        "author": "培风客", "title": "白银供需观察", "published_at": "2026-09-29",
        "source_url": "https://www.federalreserve.gov/example",
        "content": "这是一篇供需观察原文，作者认为短期变化仍需要公开数据验证。",
    })
    assert created.status_code == 201, created.text
    article = created.json()["article"]
    assert article["author"] == "培风客"
    assert article["rel_path"].startswith("培风客/articles/")
    assert article["published_at"] == "2026-09-29"
    assert "# 白银供需观察" in article["content"]
    assert (root / article["rel_path"]).is_file()
    assert "培风客" in client.get(BASE).json()["authors"]
    assert ArticleCorpus("finance_observer").search("白银供需")[0]["article_id"] == article["article_id"]


def test_article_library_requires_publication_date(article_client):
    client, _ = article_client
    response = client.post(BASE, json={
        "author": "TT", "title": "Undated article",
        "content": "A long enough article body must not receive an invented publication date.",
    })
    assert response.status_code == 422


@pytest.mark.parametrize("author", ["../outside", "bad/name", "CON", "bad\\name"])
def test_article_library_rejects_unsafe_author_names(article_client, author):
    client, root = article_client
    response = client.post(BASE, json={"author": author, "title": "标题", "published_at": "2026-09-29",
                                       "content": "有效正文至少二十个字符，用于校验作者目录安全边界。"})
    assert response.status_code == 400
    assert not (root.parent / "outside").exists()


def test_article_library_is_scoped_and_does_not_overwrite(article_client):
    client, _ = article_client
    assert client.get("/companies/company_001/workbench/finance/articles").status_code == 400
    assert client.get(BASE + "/missing").status_code == 404
    mismatch = client.post(BASE, json={
        "author": "TT", "title": "页面标题", "published_at": "2026-09-29",
        "content": "# 原文标题\n\n这一段原文足够长，用来验证不悄悄更改原文标题。",
    })
    assert mismatch.status_code == 400
