"""Workspace fixtures are created only through the workbench's public HTTP API."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from policy_impact.app import api
from policy_impact.company_wiki import loader
from policy_impact.company_wiki.indexer import build_company_context_pack


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    with TestClient(api.app) as test_client:
        yield test_client


def create_workspace(client, company_id="workspace_test", profile_type="company", name="Test"):
    response = client.post("/companies", json={
        "company_id": company_id, "name": name, "profile_type": profile_type,
    })
    assert response.status_code == 201, response.text
    return response.json()


def fact_body(fact_id="test.fact", value="quartzcompany", source="user_confirmed", confidence=0.8):
    return (
        f"## FACT: {fact_id}\n- value: {value}\n- source: {source}\n"
        f"- importance: 4\n- confidence: {confidence}\n"
        "- policy_relevance: [AI, evidence]\n"
    )


def write_page(client, path, body, *, company_id="workspace_test", create=False):
    return client.post(f"/companies/{company_id}/workbench/wiki/pages", json={
        "path": path, "body": body, "create": create,
    })


def search(client, query, company_id="workspace_test", top_k=5):
    return client.get(f"/companies/{company_id}/workbench/wiki/search", params={
        "query": query, "top_k": top_k,
    })


def source_snapshot(tmp_path):
    return {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.suffix in {".yaml", ".md"}
    }


def indexed_facts(tmp_path, company_id="workspace_test"):
    path = tmp_path / "data" / "companies" / company_id / "db" / "company_memory.sqlite"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return dict(connection.execute("SELECT fact_id, value FROM company_facts"))


def test_create_two_profiles_and_refresh_company_selector(client, tmp_path):
    profiles = [
        ("ths_test", "company", "\u540c\u82b1\u987a\u6d4b\u8bd5\u753b\u50cf"),
        ("personal_ai", "personal", "\u4e2a\u4ebaAI\u5b66\u4e60\u753b\u50cf"),
    ]
    assert client.get("/companies").json()["companies"] == []
    for company_id, profile_type, name in profiles:
        created = create_workspace(client, company_id, profile_type, name)
        assert created["schema_version"] == "workbench.company_create.v1"
        assert created["company_id"] == company_id
        assert created["profile"]["company_name"] == name
        assert created["profile"]["profile_type"] == profile_type
        wiki = created["wiki"]
        assert wiki["stats"]["page_count"] == 3
        assert {page["title"] for page in wiki["pages"]} == {"\u80cc\u666f", "\u76ee\u6807", "\u7ea6\u675f"}
        assert wiki["facts"] == []
        assert wiki["stats"]["fact_count"] == 0
        assert wiki["stats"]["high_importance_fact_count"] == 0
        for page in wiki["pages"]:
            assert page["facts"] == []
            assert "## FACT:" not in page["body"]
            assert "\u5f85\u586b\u5199" in page["body"]
            assert "Pending confirmation" not in page["body"]
        profile_path = tmp_path / "data" / "companies" / company_id / "company.yaml"
        assert json.loads(profile_path.read_text(encoding="utf-8")) == created["profile"]
        assert indexed_facts(tmp_path, company_id) == {}
        assert search(client, "\u80cc\u666f \u76ee\u6807 \u7ea6\u675f \u5f85\u586b\u5199 profile", company_id).json()["results"] == []
        context = build_company_context_pack(company_id)
        for key in ("all_company_facts", "pinned_facts", "retrieved_company_facts", "open_questions"):
            assert context[key] == []
        assert client.get(f"/companies/{company_id}").json()["profile"] == created["profile"]
    listing = client.get("/companies").json()
    assert set(listing["company_ids"]) == {"ths_test", "personal_ai"}
    assert {row["company_name"] for row in listing["companies"]} == {row[2] for row in profiles}
    assert not (tmp_path / "data" / "companies" / "company_001").exists()


def test_workspace_create_conflict_preserves_existing_files(client, tmp_path):
    create_workspace(client)
    before = source_snapshot(tmp_path)
    response = client.post("/companies", json={
        "company_id": "workspace_test", "name": "Replacement", "profile_type": "personal",
    })
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert source_snapshot(tmp_path) == before


@pytest.mark.parametrize("company_id", [
    "", ".", "..", "../other", "a/b", "a\\b", "C:\\escape", "/absolute",
    "bad:stream", "bad.", "bad ", " bad", "bad%2fpath", "bad\x00id",
    "con", "NUL", "COM1", "LPT9", "x" * 65,
])
def test_create_rejects_unsafe_company_ids_without_writing(client, tmp_path, company_id):
    response = client.post("/companies", json={"company_id": company_id, "name": "Test"})
    assert response.status_code == 400, response.text
    assert "company_id" in response.json()["detail"]
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("fields", [
    {"name": " "}, {"name": "x" * 201}, {"name": "one\ntwo"}, {"profile_type": "invalid"},
])
def test_invalid_profile_does_not_create_workspace(client, tmp_path, fields):
    response = client.post("/companies", json={
        "company_id": "workspace_test", "name": "Test", **fields,
    })
    assert response.status_code == 400
    assert not (tmp_path / "data").exists()


def test_create_update_and_search_keep_provenance_separate_from_score(client, tmp_path):
    create_workspace(client)
    body = fact_body(source="https://example.org/disclosure", confidence=0.0)
    response = write_page(client, "wiki/research/new.md", body, create=True)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["wiki"]["stats"]["page_count"] == 4
    result = search(client, "quartzcompany").json()["results"][0]
    assert result["retrieval_score"] > 0
    assert result["matched_term_count"] > 0
    assert result["fact"]["source_kind"] == "public_disclosure"
    assert result["fact"]["source"] == "https://example.org/disclosure"
    assert result["fact"]["source_path"] == "wiki/research/new.md"
    assert result["fact"]["confidence"] == 0.0
    assert "retrieval_score" not in result["fact"]
    # Omit create to exercise the old frontend request contract.
    response = client.post("/companies/workspace_test/workbench/wiki/pages", json={
        "path": "wiki/research/new.md", "body": fact_body("test.replacement", "updatedmarker"),
    })
    assert response.status_code == 200
    assert response.json()["schema_version"] == "workbench.wiki_update.v1"
    assert response.json()["created"] is False
    assert search(client, "quartzcompany").json()["results"] == []
    assert search(client, "updatedmarker").json()["results"][0]["fact"]["fact_id"] == "test.replacement"
    indexed = indexed_facts(tmp_path)
    assert "test.fact" not in indexed
    assert indexed["test.replacement"] == "updatedmarker"


def test_same_fact_ids_are_independent_across_workspaces(client, tmp_path):
    for company_id, value in [("ths_test", "quartzcompany"), ("personal_ai", "opalpersonal")]:
        create_workspace(client, company_id)
        response = write_page(client, "wiki/shared.md", fact_body("same.id", value),
                              company_id=company_id, create=True)
        assert response.status_code == 200
        assert indexed_facts(tmp_path, company_id)["same.id"] == value
    assert search(client, "quartzcompany", "personal_ai").json()["results"] == []
    assert search(client, "opalpersonal", "ths_test").json()["results"] == []
    assert search(client, "quartzcompany", "ths_test").json()["results"][0]["fact"]["value"] == "quartzcompany"


def test_page_create_conflict_and_missing_legacy_update(client, tmp_path):
    create_workspace(client)
    before = source_snapshot(tmp_path)
    assert write_page(client, "wiki/missing.md", fact_body()).status_code == 404
    assert write_page(client, "wiki/00_background.md", fact_body(), create=True).status_code == 409
    assert source_snapshot(tmp_path) == before


@pytest.mark.parametrize("path", [
    "../company.yaml", "wiki/../../other.md", "wiki/../outside.md", "company.yaml",
    "/wiki/outside.md", "C:\\outside.md", "wiki\\..\\outside.md", "wiki/test.txt",
    "wiki/test.md:stream.md", "wiki/NUL.md", "wiki/COM1.md", "wiki/space /test.md",
    "wiki/end./test.md", "wiki/test?.md", "wiki/null\x00.md", "",
    "wiki/" + "x" * 256 + ".md",
])
def test_wiki_path_validation_is_400_and_does_not_mutate(client, tmp_path, path):
    create_workspace(client)
    before = source_snapshot(tmp_path)
    response = write_page(client, path, fact_body(), create=True)
    assert response.status_code == 400, response.text
    assert "wiki page path" in response.json()["detail"]
    assert source_snapshot(tmp_path) == before


def test_backslash_page_path_is_canonicalized_and_existing_metadata_preserved(client):
    create_workspace(client)
    response = write_page(client, "wiki\\00_background.md", fact_body())
    assert response.status_code == 200
    assert response.json()["page"]["path"] == "wiki/00_background.md"
    assert response.json()["page"]["title"] == "\u80cc\u666f"


@pytest.mark.parametrize("duplicate_type", ["same_page", "other_page", "update"])
def test_duplicate_fact_ids_return_409_before_file_or_index_changes(client, tmp_path, duplicate_type):
    create_workspace(client)
    assert write_page(client, "wiki/existing.md", fact_body(), create=True).status_code == 200
    before = source_snapshot(tmp_path)
    index_before = indexed_facts(tmp_path)
    if duplicate_type == "same_page":
        body = fact_body("duplicate.id") + "\n" + fact_body("duplicate.id")
    else:
        body = fact_body("test.fact")
    response = write_page(client,
                          "wiki/00_background.md" if duplicate_type == "update" else "wiki/conflict.md",
                          body, create=duplicate_type != "update")
    assert response.status_code == 409
    assert "duplicate fact_id" in response.json()["detail"]
    assert source_snapshot(tmp_path) == before
    assert indexed_facts(tmp_path) == index_before


@pytest.mark.parametrize("body", [
    fact_body(confidence="not-a-number"), fact_body(confidence="nan"),
    fact_body(confidence="inf"), fact_body(confidence=1.1), fact_body(confidence=-0.1),
    fact_body().replace("importance: 4", "importance: invalid"),
    fact_body().replace("importance: 4", "importance: inf"),
    fact_body().replace("importance: 4", "importance: 6"),
    fact_body().replace("[AI, evidence]", "42"),
    fact_body().replace("[AI, evidence]", '["AI", 42]'),
    fact_body(value="nan"), fact_body(source="inf"),
    fact_body(""), "---\nsource: override\n---\ncontent",
])
def test_bad_fact_input_is_400_before_write(client, tmp_path, body):
    create_workspace(client)
    before = source_snapshot(tmp_path)
    response = write_page(client, "wiki/invalid.md", body, create=True)
    assert response.status_code == 400, response.text
    assert source_snapshot(tmp_path) == before


def test_unknown_workspace_does_not_get_implicitly_created(client, tmp_path):
    assert client.get("/companies/missing/workbench/wiki").status_code == 404
    assert search(client, "AI", "missing").status_code == 404
    assert write_page(client, "wiki/new.md", fact_body(), company_id="missing", create=True).status_code == 404
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("query,top_k", [(" ", 5), ("x" * 2001, 5), ("AI", 0), ("AI", 101)])
def test_invalid_search_parameters_are_400(client, query, top_k):
    create_workspace(client)
    assert search(client, query, top_k=top_k).status_code == 400


def test_search_reuses_company_wiki_search_and_respects_limit(client, monkeypatch):
    create_workspace(client)
    body = fact_body("profile.one", "profile") + "\n" + fact_body("profile.two", "profile")
    assert write_page(client, "wiki/confirmed.md", body, create=True).status_code == 200
    original = api.workbench_service.search_wiki.__globals__["company_wiki_search"]
    calls = []

    def recording_search(company_id, query, top_k=5):
        calls.append((company_id, query, top_k))
        return original(company_id, query, top_k)

    monkeypatch.setitem(api.workbench_service.search_wiki.__globals__, "company_wiki_search", recording_search)
    response = search(client, "profile", top_k=1)
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    assert calls == [("workspace_test", "profile", 1)]


def test_concurrent_duplicate_fact_writes_only_accept_one(client, tmp_path):
    create_workspace(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda index: write_page(client, f"wiki/concurrent_{index}.md", fact_body(), create=True),
            range(2),
        ))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert len(indexed_facts(tmp_path)) == 1
    assert client.get("/companies/workspace_test/workbench/wiki").status_code == 200


def test_concurrent_workspace_creation_does_not_overwrite(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda index: client.post("/companies", json={
            "company_id": "workspace_test", "name": f"Name {index}",
        }), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(response.json()["profile"] for response in responses if response.status_code == 201)
    assert client.get("/companies/workspace_test").json()["profile"] == winner


def test_page_title_with_frontmatter_delimiter_round_trips(client):
    create_workspace(client)
    response = write_page(client, "wiki/a---b.md", fact_body(), create=True)
    assert response.status_code == 200
    page = next(page for page in response.json()["wiki"]["pages"] if page["path"] == "wiki/a---b.md")
    assert page["title"] == "a---b"
    assert page["body"] == fact_body().strip()
    assert write_page(client, "wiki/a---b.md", fact_body(value="replacement")).status_code == 200


@pytest.mark.parametrize("link_target", ["workspace", "wiki", "page", "profile"])
def test_resolved_links_cannot_escape_workspace(client, tmp_path, monkeypatch, link_target):
    create_workspace(client)
    workspace = tmp_path / "data" / "companies" / "workspace_test"
    link = {
        "workspace": workspace,
        "wiki": workspace / "wiki",
        "page": workspace / "wiki" / "00_background.md",
        "profile": workspace / "company.yaml",
    }[link_target]
    original_resolve = Path.resolve

    # Model a filesystem link without seeding any Wiki content outside the API.
    def resolve_link(path, *args, **kwargs):
        if path == link or path.is_relative_to(link):
            return tmp_path / "outside" / path.relative_to(link)
        return original_resolve(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "resolve", resolve_link)
        assert client.get("/companies/workspace_test/workbench/wiki").status_code == 400
        assert write_page(client, "wiki/00_background.md", fact_body()).status_code == 400


def test_file_directory_conflicts_do_not_return_500(client):
    create_workspace(client)
    assert write_page(client, "wiki/file.md", fact_body(), create=True).status_code == 200
    assert write_page(client, "wiki/file.md/nested.md", fact_body("new.id"), create=True).status_code == 409
    assert write_page(client, "wiki/folder.md/child.md", fact_body("child.id"), create=True).status_code == 200
    assert write_page(client, "wiki/folder.md", fact_body("another.id"), create=True).status_code == 400
