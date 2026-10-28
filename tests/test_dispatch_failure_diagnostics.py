"""Terminal failures remain diagnostic data, not accepted research evidence."""

import asyncio
import json
from dataclasses import replace

import pytest

from policy_impact.dispatch import DispatchError, DispatchRepository
from policy_impact.dispatch.contracts import Resume, Wait
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.dispatch.worker import Worker
from tests.test_dispatch_foundation import claim, delivery, spawn, state
from tests.test_dispatch_foundation import setup as foundation_setup


@pytest.fixture
def setup(tmp_path):
    return foundation_setup.__wrapped__(tmp_path)


def diagnostic():
    return {
        "validation_stage": "final.output",
        "validation_errors": [{
            "loc": ["evidence_links", 0, "text"], "type": "value_error",
            "msg": "evidence link text must occur verbatim in content/candidate_answer",
            "input": "private rejected input", "ctx": {"private": True},
        }],
        "output_ref": "private-output", "arbitrary": "private details",
    }


def test_failure_diagnostic_survives_worker_restart_cursor_and_wait(setup):
    import fakeredis

    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    transport = RedisStreams(fakeredis.FakeRedis(decode_responses=True), repo.config.stream_name)

    async def execute(work):
        raise DispatchError("contract_error", "private exception text", details=diagnostic())

    async def run():
        assert await Worker(repo, transport, "worker", handler=execute).process(
            "0-1", delivery(repo, child["task_id"])
        )

    asyncio.run(run())
    restored = DispatchRepository(repo.db.path, repo.artifacts.root, profiles=repo.profiles,
                                 roles=repo.roles, config=repo.config, clock=repo.clock)
    view = restored.status(user, [child["task_id"]], after_event_id="999999")
    assert view["events"] == []
    task = view["tasks"][0]
    assert task["status"] == "failed" and task["result_ref"] is None
    assert task["attempts"][-1]["error_message"] == "Agent output did not satisfy its contract."
    detail = task["failure_diagnostic"]
    assert detail["attempt_id"] == task["attempts"][-1]["attempt_id"]
    assert detail["validation_stage"] == "final.output"
    assert detail["validation_errors"] == [{key: value for key, value in
                                             diagnostic()["validation_errors"][0].items()
                                             if key in {"loc", "type", "msg"}}]
    waited = restored.wait_snapshot(user, Wait(task_ids=[child["task_id"]], timeout_ms=0))
    assert waited["condition_met"] and waited["tasks"][0]["failure_diagnostic"] == detail
    event = next(item for item in waited["events"] if item["event_type"] == "task.failed")
    assert event["payload"] == detail
    assert "private" not in json.dumps(view)
    with repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_results").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0


@pytest.mark.parametrize("code,stage,exposed", [
    ("contract_error", "action", True), ("contract_error", "final.output", True),
    ("contract_error", "private stage", False), ("budget_denied", "action", False),
    ("internal_error", "action", False),
])
def test_diagnostic_allowlist_masks_secrets_urls_and_bounds(setup, code, stage, exposed):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    secret = "sk-fixture-credential-must-not-escape"
    details = {**diagnostic(), "validation_stage": stage, "validation_errors": [{
        "loc": [secret, "https://example.invalid/?token=private"] + ["x" * 300] * 20,
        "type": "custom_" + "x" * 200,
        "msg": f"{secret} Bearer private-token https://example.invalid/private " + "x" * 500,
        "input": "private input", "ctx": {"private": True}, "url": "private URL",
    }] * 30}
    repo.fail(lease, code, details=details)
    task = repo.status(user, [child["task_id"]])["tasks"][0]
    detail = task["failure_diagnostic"]
    assert ("validation_stage" in detail) == exposed
    if exposed:
        errors = detail["validation_errors"]
        assert len(errors) == 12
        assert all(set(e) == {"loc", "type", "msg"} for e in errors)
        assert all(len(e["loc"]) <= 12 and len(e["type"]) <= 80 and len(e["msg"]) <= 320 for e in errors)
        assert all(len(p) <= 160 for e in errors for p in e["loc"] if isinstance(p, str))
    serialized = json.dumps(task)
    assert secret not in serialized and "private" not in serialized and "https://" not in serialized


def test_diagnostic_write_is_fenced_and_cannot_reappear_on_resume(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(lease, state(repo, lease))
    with pytest.raises(DispatchError, match="stale_lease"):
        repo.fail(replace(lease, token="wrong"), "contract_error", details=diagnostic())
    repo.fail(lease, "contract_error", details=diagnostic())
    repo.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                             idempotency_key="restore"))
    task = repo.status(user, [child["task_id"]])["tasks"][0]
    assert "failure_diagnostic" not in task
    assert task["attempts"][-1]["error_message"] is None
    assert task["attempts"][0]["error_message"]
    with pytest.raises(DispatchError, match="stale_lease"):
        repo.fail(lease, "contract_error", details=diagnostic())
    foreign = repo.create_run(run_id="other", session_ref="other", query="other",
                              selected_profile="gpt", allowed_profiles=["gpt"], tools=[])["data"]
    with pytest.raises(DispatchError):
        repo.status(replace(user, run_id=foreign["run_id"], task_id=foreign["task_id"],
                            thread_id=foreign["thread_id"]), [child["task_id"]])


@pytest.mark.parametrize("exception", [ValueError("private exception"),
    DispatchError("fixture_unknown", "private exception", details=diagnostic())])
def test_unknown_worker_error_never_publishes_exception_or_details(setup, exception):
    repo, _, user, root = setup
    lease = claim(repo, root["task_id"])
    asyncio.run(Worker(repo, None, "fixture", handler=lambda work: None)._fail_execution(lease, exception))
    task = repo.status(user, [root["task_id"]])["tasks"][0]
    detail = task["failure_diagnostic"]
    assert detail["error_code"] == "internal_error"
    assert "validation_errors" not in detail
    assert "private" not in json.dumps(task)


@pytest.mark.parametrize("details", [None, [], {"validation_stage": []},
    {"validation_stage": "action", "validation_errors": "private"},
    {"validation_stage": "action", "validation_errors": [None, {"loc": {}}, {
        "loc": [True, {}, 0], "type": {"private": True}, "msg": {"private": True},
    }]},
])
def test_malformed_diagnostic_cannot_prevent_failure_persistence(setup, details):
    repo, _, user, root = setup
    lease = claim(repo, root["task_id"])
    repo.fail(lease, "contract_error", details=details)
    task = repo.status(user, [root["task_id"]])["tasks"][0]
    assert task["status"] == "failed" and task["failure_diagnostic"]["error_code"] == "contract_error"
    assert "private" not in json.dumps(task)


def test_retryable_transport_keeps_attempt_messages_without_cross_attempt_diagnostic(setup):
    repo, _, user, _ = setup
    child = spawn(repo, user)["data"]
    for number in range(repo.config.max_attempts):
        lease = claim(repo, child["task_id"])
        repo.fail(lease, "transport_error", retryable=True, details=diagnostic())
        task = repo.status(user, [child["task_id"]])["tasks"][0]
        if number < repo.config.max_attempts - 1:
            assert task["status"] == "pending_dispatch" and "failure_diagnostic" not in task
    assert task["status"] == "failed"
    assert task["failure_diagnostic"]["attempt_id"] == lease.attempt_id
    assert "validation_errors" not in task["failure_diagnostic"]
    assert all(a["error_message"] == "Model transport failed." for a in task["attempts"])
