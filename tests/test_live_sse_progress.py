import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from policy_impact.app import api


def parse_events(text):
    events = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        kind = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
        data = next((line[5:].strip() for line in lines if line.startswith("data:")), "{}")
        events.append((kind, json.loads(data)))
    return events


class FakeService:
    def __init__(self):
        self.observed_progress = threading.Event()

    def prepare_message(self, company_id, session_id, message):
        def execute(prepared):
            assert self.observed_progress.wait(3), "SSE did not expose a trace until execution completed"
            return {"run_id": prepared.run_id, "answer": "真实结果", "selected_skill_id": "general_chat"}
        return SimpleNamespace(coordinator=SimpleNamespace(execute_prepared_turn=execute)), SimpleNamespace(run_id="target-run")

    def trace_for_run(self, company_id, session_id, run_id):
        assert run_id == "target-run", "Must not stream a different simultaneous session run"
        self.observed_progress.set()
        return {"run_id": run_id, "status": "running", "steps": [{"step_index": 1, "title": "构建上下文"}]}


def test_sse_publishes_progress_before_result(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(api, "workbench_service", service)
    with TestClient(api.app) as client:
        response = client.post("/companies/test/workbench/sessions/test/messages/stream", json={"message": "你好"})
    assert response.status_code == 200
    events = parse_events(response.text)
    names = [kind for kind, _ in events]
    assert "error" not in names
    assert names.index("progress") < names.index("result")
    assert names[-1] == "done"
    assert "".join(data["content"] for kind, data in events if kind == "chunk") == "真实结果"


def test_sse_preparation_failure_is_explicit(monkeypatch):
    class FailureService:
        def prepare_message(self, *_args):
            raise ValueError("当前没有可用模型")
    monkeypatch.setattr(api, "workbench_service", FailureService())
    with TestClient(api.app) as client:
        response = client.post("/companies/test/workbench/sessions/test/messages/stream", json={"message": "你好"})
    events = parse_events(response.text)
    assert [kind for kind, _ in events] == ["start", "error"]
    assert "当前没有可用模型" in events[-1][1]["detail"]
