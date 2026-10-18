from fastapi.testclient import TestClient
import pytest

from policy_impact.app import api
from policy_impact.company_wiki import loader
from policy_impact.domains.finance.profile import default_profile


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    with TestClient(api.app) as client:
        for cid in ("finance_test", "other_test"):
            assert client.post("/companies", json={"company_id": cid, "name": cid, "profile_type": "personal"}).status_code == 201
        yield client


BASE = "/companies/finance_test/workbench/finance"


def test_profile_saved_through_api_versioned_searchable_and_isolated(client):
    assert client.get(BASE + "/profile").json() == {"profile": None}
    profile = default_profile()
    response = client.post(BASE + "/profile", json={"profile": profile, "expected_version": 0})
    assert response.status_code == 200, response.text
    saved = response.json()["profile"]
    assert saved["version"] == 1 and saved["holdings"] is None and saved["status"] == "draft"
    search = client.get("/companies/finance_test/workbench/wiki/search", params={"query": "金融", "top_k": 8}).json()
    assert "finance_profile.md" in str(search)
    assert client.get("/companies/other_test/workbench/finance/profile").json() == {"profile": None}
    assert client.post(BASE + "/profile", json={"profile": profile, "expected_version": 0}).status_code == 409
    saved["report_days"] = 30
    assert client.post(BASE + "/profile", json={"profile": saved, "expected_version": 1}).json()["profile"]["version"] == 2
    assert client.post("/companies/finance_test/workbench/wiki/pages", json={"path": "wiki/finance_profile.md", "body": "replace"}).status_code == 400


def test_collection_control_and_no_profile_no_collection(client):
    assert client.post("/companies/other_test/workbench/finance/collect", json={}).status_code == 400
    client.post(BASE + "/profile", json={"profile": default_profile(), "expected_version": 0})
    paused = client.post(BASE + "/schedule", json={"enabled": False})
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"]["enabled"] is False
    assert client.post(BASE + "/collect", json={}).json()["status"] == "paused"
    status = client.get(BASE + "/status").json()["status"]
    assert status["worker_running"] is False
    assert client.post(BASE + "/schedule", json={"enabled": True}).json()["status"]["enabled"] is True
    assert client.get(BASE + "/reports/missing").status_code == 404
