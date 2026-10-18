"""Product isolation and endpoint composition, without model/network calls."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from policy_impact.app import api as shared
from policy_impact.app.finance_research_app import create_app
from policy_impact.company_wiki import loader
from run_finance import configure_environment

BASE = "/companies/finance_observer/workbench"


def test_main_model_trace_uses_shared_action_contract():
    from policy_impact.harness.agent_runtime import AgentRuntime
    from policy_impact.runtime.actions import ALLOWED_ACTION_NAMES

    recorded = []
    runtime = object.__new__(AgentRuntime)
    runtime.store = SimpleNamespace(add_agent_step=lambda *args, **kwargs: recorded.append(kwargs))
    runtime._record_general_chat_loop_model_call("run_fixture", {
        "schema_name": "general_chat_agent_loop.v1", "agent_role": "main",
        "payload": {"type": "tool_call", "tool_name": "research.plan", "arguments": {}},
    })
    contract = recorded[0]["input_payload"]["prompt_contract"]
    assert contract["allowed_actions"] == list(ALLOWED_ACTION_NAMES)
    assert "tool_call" in contract["allowed_actions"]


def test_relative_corpus_uses_project_root_not_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    corpus = project / "data/finance/articles"
    corpus.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", "data/finance/articles")
    assert configure_environment(project, load_files=False) == corpus.resolve()


def test_default_corpus_and_local_env_are_independent(tmp_path, monkeypatch):
    corpus = tmp_path / "data/finance/articles"
    corpus.mkdir(parents=True)
    monkeypatch.delenv("FINANCE_ARTICLE_ROOT", raising=False)
    assert configure_environment(tmp_path, load_files=False) == corpus.resolve()
    (tmp_path / ".env.local").write_text("FINANCE_ARTICLE_ROOT=data/finance/articles\n")
    assert configure_environment(tmp_path) == corpus.resolve()


@pytest.mark.parametrize("configured", ["../old-project/articles", "..", "."])
def test_corpus_cannot_escape_or_use_project_root(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", configured)
    with pytest.raises(ValueError, match="inside finance-agent-next"):
        configure_environment(tmp_path, load_files=False)


def test_absolute_external_and_missing_corpus_are_rejected(tmp_path, monkeypatch):
    outside = tmp_path / "old-project"
    outside.mkdir()
    project = tmp_path / "new-project"
    project.mkdir()
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(outside))
    with pytest.raises(ValueError, match="inside finance-agent-next"):
        configure_environment(project, load_files=False)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", "missing")
    with pytest.raises(ValueError, match="does not exist"):
        configure_environment(project, load_files=False)


@pytest.fixture
def finance_client(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    company = tmp_path / "data/companies/finance_observer"
    (company / "wiki").mkdir(parents=True)
    (company / "company.yaml").write_text(json.dumps({
        "company_id": "finance_observer", "company_name": "Financial Research",
        "profile_type": "personal",
    }), encoding="utf-8")
    (company / "wiki/index.md").write_text("# Research Wiki\n", encoding="utf-8")
    corpus = tmp_path / "articles"
    articles = corpus / "TT/articles"
    articles.mkdir(parents=True)
    (articles / "2026-09-30-fixture.md").write_text(
        "# Article fixture\n2026-09-30\nA conditional view on real yields and gold.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(corpus))
    with TestClient(create_app()) as client:
        yield client, tmp_path


def test_health_and_only_finance_workspace_are_exposed(finance_client):
    client, root = finance_client
    assert client.get("/health").json()["product"] == "finance-agent-next"
    assert client.get("/companies").json()["company_ids"] == ["finance_observer"]
    assert client.get("/companies/company_001").status_code == 404
    assert not (root / "data/companies/company_001").exists()
    assert client.post("/companies", json={"company_id": "new", "name": "new"}).status_code == 405


@pytest.mark.parametrize("path", [
    "/weekly-analysis", "/recent-news-report", "/latest-report", "/report-chat", "/chat",
    "/workbench/finance/reports", "/workbench/finance/reports/old",
])
def test_legacy_product_routes_are_not_mounted(finance_client, path):
    client, _ = finance_client
    for method in ("GET", "POST"):
        assert client.request(method, "/companies/finance_observer" + path).status_code == 404


@pytest.mark.parametrize("path", ["/sessions", "/models", "/wiki", "/skills", "/finance/articles"])
def test_other_workspaces_are_denied_before_database_access(finance_client, path):
    client, root = finance_client
    response = client.get("/companies/company_001/workbench" + path)
    assert response.status_code == 404
    assert not (root / "data/companies/company_001").exists()


def test_article_wiki_session_and_trace_contracts_still_work(finance_client):
    client, _ = finance_client
    assert client.get(BASE + "/wiki").status_code == 200
    listing = client.get(BASE + "/finance/articles").json()
    article_id = listing["articles"][0]["article_id"]
    assert client.get(BASE + "/finance/articles/" + article_id).status_code == 200
    created = client.post(BASE + "/sessions", json={"title": "Offline fixture", "mode": "chat"})
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    assert len(client.get(BASE + "/sessions").json()["sessions"]) == 1
    assert client.get(BASE + f"/sessions/{session_id}/messages").json()["messages"] == []
    assert client.get(BASE + f"/sessions/{session_id}/trace").status_code == 200


def test_public_skills_are_research_only(finance_client):
    client, _ = finance_client
    assert [s["id"] for s in client.get(BASE + "/skills").json()["skills"]] == ["general_chat"]


@pytest.mark.parametrize("binding", [
    {"active_skill_id": "external_impact_report"},
    {"active_skill_id": "research_report"},
    {"mode": "report"}, {"mode": "policy"},
])
def test_session_bindings_cannot_enable_legacy_workflows(finance_client, binding):
    client, root = finance_client
    assert client.post(BASE + "/sessions", json=binding).status_code == 400
    assert client.post(BASE + "/sessions/absent/settings", json=binding).status_code == 400
    assert not (root / "data/companies/finance_observer/db").exists()


@pytest.mark.parametrize("message", ["/report gold", "  /REPORT gold", "/impact", "/policy gold"])
@pytest.mark.parametrize("suffix", ["messages", "messages/stream"])
def test_legacy_commands_rejected_before_model_calls(finance_client, monkeypatch, message, suffix):
    client, _ = finance_client

    def forbidden(*args, **kwargs):
        raise AssertionError("An excluded command must never reach model execution")

    monkeypatch.setattr(shared.workbench_service, "send_message", forbidden)
    monkeypatch.setattr(shared.workbench_service, "_message_runtime", forbidden)
    assert client.post(BASE + f"/sessions/absent/{suffix}", json={"message": message}).status_code == 400


def test_normal_questions_are_not_rewritten_or_classified(finance_client, monkeypatch):
    client, _ = finance_client
    message = "Compare two authors, or explain a financial concept, based on the evidence."
    seen = []

    def respond(company_id, session_id, question):
        seen.append((company_id, session_id, question))
        return {"answer": "Offline fixture, not a real model answer"}

    monkeypatch.setattr(shared.workbench_service, "send_message", respond)
    response = client.post(BASE + "/sessions/fixture/messages", json={"message": message})
    assert response.status_code == 200
    assert seen == [("finance_observer", "fixture", message)]


def test_artifacts_cannot_read_another_workspace_or_outside_file(finance_client):
    client, root = finance_client
    private = root / "not-an-artifact.txt"
    private.write_text("fixture", encoding="utf-8")
    assert client.get(BASE + "/artifact", params={"path": str(private)}).status_code == 403


def test_frontend_entry_is_financial_not_legacy_product(finance_client):
    client, _ = finance_client
    html = client.get("/").text
    assert "Finance Agent Next" in html
    assert 'id="create-workspace" class="workspace-create-button hidden"' in html
    javascript = client.get("/static/workbench/app.js").text
    assert "const slashCommands = [];" in javascript
    assert 'data-prompt="/report' not in javascript
    assert 'id="wiki-new-workspace"' not in javascript
