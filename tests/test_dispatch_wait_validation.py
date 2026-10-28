"""Offline wait preflight and metadata-only model context budget checks."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Cancel, CheckpointState, Spawn, Wait
from policy_impact.dispatch.execution import ExecutionCapabilities
from policy_impact.dispatch.model_execution import GatewayModelAdapter, ModelSettings
from policy_impact.dispatch.worker import Suspend, WorkInput
from policy_impact.harness.context_manifest import estimate_tokens


def _claim(repo, task_id):
    with repo.db.connection() as c:
        payload = json.loads(c.execute(
            "SELECT payload_json FROM h2_outbox WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()[0])
    lease, _ = repo.claim(payload, "wait-validation")
    assert lease is not None
    repo.start(lease)
    return lease


def _spawn(runtime, key="child", caller=None):
    return runtime.repo.spawn(caller or runtime.user, Spawn(
        dispatch_key=key,
        role="analyst",
        role_version="2.0.0",
        objective="Read authorized source material",
        success_criteria=["Preserve source conditions"],
        input_refs=[],
        output_contract={"name": "fixture.article", "version": "1"},
        requested_budget={"model_calls": 4, "wall_time_ms": 60000},
    ))["data"]


@pytest.fixture
def runtime(tmp_path):
    clock = SimpleNamespace(value=1_000_000)
    repo = DispatchRepository(
        tmp_path / "wait.sqlite", tmp_path / "records",
        clock=lambda: clock.value,
        config=SchedulerConfig(stream_name="h2:wait-validation:fixture", budget_policy="hierarchical"),
        profiles={"fixture": {
            "model_id": "fixture-model", "base_url": "https://fixture.invalid/v1",
            "protocol": "openai-compatible", "auth_env": "H2_UNUSED_FIXTURE_KEY",
            "parameters": {"context_tokens": 4096, "max_tokens": 256,
                           "input_tokens": 2048, "safety_tokens": 128},
        }},
        roles={"analyst@2.0.0": ["agent.spawn", "agent.wait", "article.read"]},
    )
    repo.migrate()
    root = repo.create_run(
        run_id="wait-fixture", session_ref="fixture-session", query="Read sources",
        selected_profile="fixture", allowed_profiles=["fixture"],
        tools=["agent.spawn", "agent.wait", "article.read"],
    )["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    lease = _claim(repo, root["task_id"])
    work = WorkInput(lease.run_id, lease.task_id, lease.thread_id, lease.attempt_id,
                     **repo.execution_input(lease))
    capabilities = ExecutionCapabilities(repo, lease, work)
    ref = repo.store_text(lease.caller(), "context", "wait preflight fixture")
    checkpoint = CheckpointState(
        current_phase="research", completed_steps=[], pending_steps=[], input_refs=[],
        context_ref=ref, consumed_message_seq=1, last_event_id="0",
    )
    return SimpleNamespace(repo=repo, clock=clock, root=root, user=user, lease=lease,
                           work=work, capabilities=capabilities, checkpoint=checkpoint)


def _durable_snapshot(repo):
    with repo.db.connection() as c:
        rows = tuple(c.iterdump())
    artifacts = {str(path.relative_to(repo.artifacts.root)): path.read_bytes()
                 for path in repo.artifacts.root.rglob("*") if path.is_file()}
    return rows, artifacts


def test_wait_preflight_and_suspend_outcome_are_side_effect_free_without_read_permissions(runtime):
    target = _spawn(runtime)
    arguments = {"task_ids": [target["task_id"]]}
    before = _durable_snapshot(runtime.repo)
    assert "agent.status" not in runtime.capabilities.tool_names
    assert "result.get" not in runtime.capabilities.tool_names
    request = asyncio.run(runtime.capabilities.validate_wait(arguments))
    assert request == Wait.model_validate(arguments)
    outcome = asyncio.run(runtime.capabilities.wait(arguments, runtime.checkpoint))
    assert isinstance(outcome, Suspend)
    assert outcome.wait == request and outcome.checkpoint == runtime.checkpoint
    assert _durable_snapshot(runtime.repo) == before


@pytest.mark.parametrize("arguments", [
    {}, {"task_ids": []}, {"task_ids": ["one", "one"]},
    {"task_ids": ["one", "two"], "mode": "specific"},
    {"task_ids": ["one"], "timeout_ms": -1},
    {"task_ids": ["one"], "timeout_ms": 60001},
    {"task_ids": ["one"], "timeout_ms": "10"},
    {"task_ids": ["one"], "after_event_id": "-1"},
    {"task_ids": ["one"], "max_events": 201},
    {"task_ids": ["one"], "lease_token": "forged"},
])
def test_wait_preflight_rejects_invalid_contract_without_side_effects(runtime, arguments):
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError, match="invalid_arguments"):
        asyncio.run(runtime.capabilities.validate_wait(arguments))
    assert _durable_snapshot(runtime.repo) == before


@pytest.mark.parametrize("failure,code", [
    ("self", "invalid_arguments"), ("unknown", "not_found"),
    ("cross-run", "not_found"), ("sibling", "not_found"),
    ("cycle", "invalid_arguments"), ("permission", "forbidden"),
    ("stale", "stale_lease"), ("cancelled", "cancelled"),
    ("task-deadline", "stale_lease"), ("run-deadline", "stale_lease"),
])
def test_wait_preflight_shares_suspend_authority_rules(runtime, failure, code):
    repo, lease, checkpoint = runtime.repo, runtime.lease, runtime.checkpoint
    target = _spawn(runtime)
    target_id = target["task_id"]
    if failure == "self":
        target_id = lease.task_id
    elif failure == "unknown":
        target_id = "unknown-task"
    elif failure == "cross-run":
        target_id = repo.create_run(
            run_id="other-run", session_ref="other-session", query="Other work",
            selected_profile="fixture", allowed_profiles=["fixture"], tools=["agent.wait"],
        )["data"]["task_id"]
    elif failure == "sibling":
        sibling = _spawn(runtime, "sibling")
        lease = _claim(repo, sibling["task_id"])
        ref = repo.store_text(lease.caller(), "context", "sibling context")
        repo.mailbox(lease)
        checkpoint = checkpoint.model_copy(update={"context_ref": ref})
    elif failure == "cycle":
        second = _spawn(runtime, "cycle-middle")
        # Seed a combined-graph fault; public spawn already rejects parent dependencies.
        with repo.db.connection(write=True) as c:
            for owner, dependency in ((target_id, second["task_id"]),
                                      (second["task_id"], lease.task_id)):
                c.execute("INSERT INTO h2_task_dependencies VALUES (?,?,?)",
                          (lease.run_id, owner, dependency))
    elif failure == "permission":
        with repo.db.connection(write=True) as c:
            permission = repo._snapshot(c, lease.run_id, "permission", {"tools": [], "roles": []})
            c.execute("UPDATE h2_threads SET permission_snapshot_ref=? WHERE thread_id=?",
                      (permission, lease.thread_id))
    elif failure == "stale":
        lease = replace(lease, token="stale-token")
    elif failure == "cancelled":
        repo.cancel(runtime.user, Cancel(task_id=lease.task_id, idempotency_key="cancel-fixture"))
    elif failure in {"task-deadline", "run-deadline"}:
        with repo.db.connection(write=True) as c:
            table, key = ("h2_tasks", "task_id") if failure == "task-deadline" else ("h2_runs", "run_id")
            c.execute(f"UPDATE {table} SET deadline_at=? WHERE {key}=?",
                      (runtime.clock.value, lease.task_id if key == "task_id" else lease.run_id))
    work = replace(runtime.work, run_id=lease.run_id, thread_id=lease.thread_id,
                   task_id=lease.task_id, attempt_id=lease.attempt_id)
    capabilities = ExecutionCapabilities(repo, lease, work)
    request = Wait(task_ids=[target_id])
    before = _durable_snapshot(repo)
    with pytest.raises(DispatchError) as rejected:
        asyncio.run(capabilities.validate_wait(request.model_dump()))
    assert rejected.value.code == code
    assert _durable_snapshot(repo) == before
    with pytest.raises(DispatchError) as rejected:
        repo.suspend(lease, checkpoint, request)
    assert rejected.value.code == code


def test_wait_preflight_rejects_missing_capability_permission(runtime):
    work = replace(runtime.work, brief={**runtime.work.brief, "allowed_tools": []})
    capabilities = ExecutionCapabilities(runtime.repo, runtime.lease, work)
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError, match="forbidden"):
        asyncio.run(capabilities.validate_wait({"task_ids": ["unknown"]}))
    assert _durable_snapshot(runtime.repo) == before


def test_wait_preflight_rejects_closed_capability(runtime):
    asyncio.run(runtime.capabilities.close())
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError, match="stale_lease"):
        asyncio.run(runtime.capabilities.validate_wait({"task_ids": ["unknown"]}))
    assert _durable_snapshot(runtime.repo) == before


def test_wait_preflight_checks_union_of_wait_and_dependency_edges(runtime):
    first, second = _spawn(runtime, "cycle-first"), _spawn(runtime, "cycle-second")
    lease = _claim(runtime.repo, first["task_id"])
    runtime.repo.mailbox(lease)
    ref = runtime.repo.store_text(lease.caller(), "context", "first child context")
    checkpoint = runtime.checkpoint.model_copy(update={"context_ref": ref})
    checkpoint_ref = runtime.repo.save_checkpoint(lease, checkpoint)
    with runtime.repo.db.connection(write=True) as c:
        c.execute(
            "INSERT INTO h2_waits (wait_id,run_id,owner_task_id,owner_attempt_id,wait_kind,"
            "mode,status,after_event_id,deadline_at,checkpoint_ref,created_at) "
            "VALUES (?,?,?,?,'tasks','all','waiting',0,?,?,?)",
            ("cycle-wait", lease.run_id, lease.task_id, lease.attempt_id,
             runtime.clock.value + 1000, checkpoint_ref, runtime.clock.value),
        )
        c.execute("INSERT INTO h2_wait_targets VALUES (?,?,?)",
                  ("cycle-wait", lease.run_id, second["task_id"]))
        c.execute("INSERT INTO h2_task_dependencies VALUES (?,?,?)",
                  (lease.run_id, second["task_id"], runtime.lease.task_id))
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError) as rejected:
        asyncio.run(runtime.capabilities.validate_wait({"task_ids": [first["task_id"]]}))
    assert rejected.value.code == "invalid_arguments"
    assert _durable_snapshot(runtime.repo) == before


@pytest.mark.parametrize("timeout,task_remaining,run_remaining", [
    (0, 10000, 10000), (60000, 10000, 10000), (100, 10000, 10000),
    (60000, 200, 10000), (60000, 10000, 100),
])
def test_wait_deadline_clamps_to_timeout_task_and_run(runtime, timeout, task_remaining, run_remaining):
    target = _spawn(runtime)
    now = runtime.clock.value
    with runtime.repo.db.connection(write=True) as c:
        c.execute("UPDATE h2_tasks SET deadline_at=? WHERE task_id=?",
                  (now + task_remaining, runtime.lease.task_id))
        c.execute("UPDATE h2_runs SET deadline_at=? WHERE run_id=?",
                  (now + run_remaining, runtime.lease.run_id))
    request = Wait(task_ids=[target["task_id"]], timeout_ms=timeout)
    deadline = runtime.repo.validate_wait(runtime.lease, request)
    assert deadline == now + min(timeout, task_remaining, run_remaining)
    wait_id = runtime.repo.suspend(runtime.lease, runtime.checkpoint, request)
    with runtime.repo.db.connection() as c:
        wait = c.execute("SELECT * FROM h2_waits WHERE wait_id=?", (wait_id,)).fetchone()
        assert wait["deadline_at"] == deadline
        assert wait["wake_reason"] == ("timeout" if timeout == 0 else None)
        assert c.execute("SELECT status FROM h2_tasks WHERE task_id=?",
                         (target["task_id"],)).fetchone()[0] == target["status"]


def test_suspend_revalidates_after_successful_preflight(runtime):
    target = _spawn(runtime)
    request = asyncio.run(runtime.capabilities.validate_wait({"task_ids": [target["task_id"]]}))
    with runtime.repo.db.connection(write=True) as c:
        c.execute("INSERT INTO h2_task_dependencies VALUES (?,?,?)",
                  (runtime.lease.run_id, target["task_id"], runtime.lease.task_id))
    with pytest.raises(DispatchError) as rejected:
        runtime.repo.suspend(runtime.lease, runtime.checkpoint, request)
    assert rejected.value.code == "invalid_arguments"
    with runtime.repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_waits").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_checkpoints").fetchone()[0] == 0


def test_mailbox_suspend_needs_no_agent_wait_permission(runtime):
    with runtime.repo.db.connection(write=True) as c:
        permission = runtime.repo._snapshot(c, runtime.lease.run_id, "permission", {"tools": []})
        c.execute("UPDATE h2_threads SET permission_snapshot_ref=? WHERE thread_id=?",
                  (permission, runtime.lease.thread_id))
    wait_id = runtime.repo.suspend(runtime.lease, runtime.checkpoint)
    with runtime.repo.db.connection() as c:
        assert c.execute("SELECT wait_kind FROM h2_waits WHERE wait_id=?", (wait_id,)).fetchone()[0] == "mailbox"


@pytest.mark.parametrize("error", [sqlite3.OperationalError("fixture storage failure"), OSError("fixture IO failure")])
def test_wait_preflight_preserves_infrastructure_failures(runtime, monkeypatch, error):
    def fail(*_args):
        raise error

    monkeypatch.setattr(runtime.repo, "validate_wait", fail)
    with pytest.raises(type(error), match="fixture"):
        asyncio.run(runtime.capabilities.validate_wait({"task_ids": ["unknown"]}))


@pytest.mark.parametrize("with_adapter", [False, True])
def test_model_input_limit_uses_bound_snapshot_without_calls_or_secrets(runtime, monkeypatch, with_adapter):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Context budgeting must not perform IO or resolve credentials")

    adapter = GatewayModelAdapter(credential_resolver=forbidden) if with_adapter else None
    if adapter is not None:
        monkeypatch.setattr(adapter, "request", forbidden)
    else:
        monkeypatch.setattr(GatewayModelAdapter, "__init__", forbidden)
    capabilities = ExecutionCapabilities(runtime.repo, runtime.lease, runtime.work, model_adapter=adapter)
    runtime.work.model["parameters"]["input_tokens"] = 1
    monkeypatch.setattr(runtime.repo, "reserve_call", forbidden)
    before = _durable_snapshot(runtime.repo)
    assert capabilities.model_input_limit == 1920
    assert capabilities.context_accepts([{"role": "user", "content": "Source evidence"}])
    assert _durable_snapshot(runtime.repo) == before


@pytest.mark.parametrize("headroom", [-1, 0, 1])
def test_context_accepts_uses_existing_token_estimate_at_exact_boundary(runtime, headroom):
    messages = [{"role": "user", "content": "source \u4e2d\u6587 " * 20}]
    size = estimate_tokens(messages)
    parameters = {"context_tokens": 4096, "max_tokens": 256,
                  "input_tokens": size + 16 + headroom, "safety_tokens": 16}
    work = replace(runtime.work, model={**runtime.work.model, "parameters": parameters})
    capabilities = ExecutionCapabilities(runtime.repo, runtime.lease, work)
    assert capabilities.model_input_limit == size + headroom
    assert capabilities.context_accepts(messages) is (headroom >= 0)


@pytest.mark.parametrize("parameters", [
    {"context_tokens": 512, "max_tokens": 256, "input_tokens": 4096, "safety_tokens": 128},
    {"context_tokens": 256, "max_tokens": 256, "safety_tokens": 0},
])
def test_model_input_limit_reserves_output_and_safety(runtime, parameters):
    work = replace(runtime.work, model={**runtime.work.model, "parameters": parameters})
    capabilities = ExecutionCapabilities(runtime.repo, runtime.lease, work)
    assert capabilities.model_input_limit == ModelSettings.model_validate(parameters).input_limit()
    if capabilities.model_input_limit == 0:
        assert not capabilities.context_accepts([])


def test_context_budget_propagates_invalid_snapshot_without_model_call(runtime):
    work = replace(runtime.work, model={**runtime.work.model, "parameters": {"input_tokens": "1000"}})
    capabilities = ExecutionCapabilities(runtime.repo, runtime.lease, work)
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError) as rejected:
        capabilities.context_accepts([])
    assert rejected.value.code == "contract_error"
    assert _durable_snapshot(runtime.repo) == before


def _business_capabilities(runtime, tools):
    with runtime.repo.db.connection(write=True) as c:
        permission = runtime.repo._snapshot(
            c, runtime.lease.run_id, "permission", {"tools": tools, "roles": list(runtime.repo.roles)},
        )
        c.execute("UPDATE h2_threads SET permission_snapshot_ref=? WHERE thread_id=?",
                  (permission, runtime.lease.thread_id))
    work = replace(runtime.work, brief={**runtime.work.brief, "allowed_tools": tools})
    return ExecutionCapabilities(runtime.repo, runtime.lease, work)


@pytest.mark.parametrize("name", ["article.read", "tool.describe"])
@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_record_tool_emits_only_authorized_metadata_and_never_charges_model(runtime, name, status):
    capabilities = _business_capabilities(runtime, [name])
    before_artifacts = _durable_snapshot(runtime.repo)[1]
    started = asyncio.run(capabilities.record_tool(name, "bounded-read-1", "started"))
    completed = asyncio.run(capabilities.record_tool(
        name, "bounded-read-1", status, runtime.checkpoint.context_ref,
    ))
    with runtime.repo.db.connection() as c:
        events = c.execute(
            "SELECT event_id,event_type,payload_json FROM h2_events "
            "WHERE event_type IN ('tool.started','tool.completed') ORDER BY event_id",
        ).fetchall()
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='charge'").fetchone()[0] == 0
    assert [str(event["event_id"]) for event in events] == [started, completed]
    assert [event["event_type"] for event in events] == ["tool.started", "tool.completed"]
    assert [json.loads(event["payload_json"]) for event in events] == [
        {"name": name, "action_key": "bounded-read-1", "attempt_id": runtime.lease.attempt_id,
         "status": "started", "observation_ref": None},
        {"name": name, "action_key": "bounded-read-1", "attempt_id": runtime.lease.attempt_id,
         "status": status, "observation_ref": runtime.checkpoint.context_ref},
    ]
    assert _durable_snapshot(runtime.repo)[1] == before_artifacts


@pytest.mark.parametrize("failure,code", [
    ("missing-tool", "forbidden"), ("repository-permission", "forbidden"),
    ("stale", "stale_lease"), ("cancelled", "cancelled"),
    ("unknown-ref", "not_found"), ("cross-run-ref", "not_found"),
])
def test_record_tool_fences_and_rejects_invalid_observation_refs_atomically(runtime, failure, code):
    capabilities = _business_capabilities(runtime, ["article.read"])
    lease = runtime.lease
    artifact_ref = None
    if failure == "missing-tool":
        capabilities = _business_capabilities(runtime, ["tool.describe"])
    elif failure == "repository-permission":
        _business_capabilities(runtime, [])
    elif failure == "stale":
        runtime.clock.value += runtime.repo.config.lease_ms + 1
    elif failure == "cancelled":
        runtime.repo.cancel(runtime.user, Cancel(task_id=lease.task_id, idempotency_key="tool-cancel"))
    elif failure == "unknown-ref":
        artifact_ref = "unknown-artifact"
    elif failure == "cross-run-ref":
        root = runtime.repo.create_run(
            run_id="tool-other-run", session_ref="tool-other-session", query="Other source",
            selected_profile="fixture", allowed_profiles=["fixture"], tools=[],
        )["data"]
        caller = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
        artifact_ref = runtime.repo.store_text(caller, "context", "not authorized for this run")
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError) as rejected:
        asyncio.run(capabilities.record_tool("article.read", "bounded-read", "succeeded", artifact_ref))
    assert rejected.value.code == code
    assert _durable_snapshot(runtime.repo) == before
    with pytest.raises(DispatchError) as rejected:
        runtime.repo.record_tool(lease, "article.read", "bounded-read", "succeeded", artifact_ref)
    assert rejected.value.code == code
    assert _durable_snapshot(runtime.repo) == before


@pytest.mark.parametrize("name,action_key,status,artifact_ref", [
    ("article.read", "read-1", "completed", None),
    ("article.read", "read-1", [], None),
    ("article.read", "", "started", None),
    ("article.read", "source body with spaces", "started", None),
    ("article.read", "read-1", "succeeded", {"body": "source text"}),
    ("article.read", "read-1", "succeeded", "../outside"),
    ("article read", "read-1", "started", None),
])
def test_record_tool_rejects_unbounded_or_invalid_metadata(runtime, name, action_key, status, artifact_ref):
    _business_capabilities(runtime, ["article.read"])
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError) as rejected:
        runtime.repo.record_tool(runtime.lease, name, action_key, status, artifact_ref)
    assert rejected.value.code == "invalid_arguments"
    assert _durable_snapshot(runtime.repo) == before


def test_record_tool_does_not_grant_access_to_sibling_artifacts(runtime):
    first, second = _spawn(runtime, "reader-first"), _spawn(runtime, "reader-second")
    first_lease, second_lease = _claim(runtime.repo, first["task_id"]), _claim(runtime.repo, second["task_id"])
    artifact_ref = runtime.repo.store_text(second_lease.caller(), "context", "sibling observation")
    work = WorkInput(first_lease.run_id, first_lease.task_id, first_lease.thread_id,
                     first_lease.attempt_id, **runtime.repo.execution_input(first_lease))
    capabilities = ExecutionCapabilities(runtime.repo, first_lease, work)
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(DispatchError) as rejected:
        asyncio.run(capabilities.record_tool("article.read", "sibling-read", "succeeded", artifact_ref))
    assert rejected.value.code == "not_found"
    assert _durable_snapshot(runtime.repo) == before


def test_record_tool_storage_failure_rolls_back_event_and_preserves_error(runtime, monkeypatch):
    capabilities = _business_capabilities(runtime, ["article.read"])
    event = runtime.repo._event

    def fail(*args, **kwargs):
        event(*args, **kwargs)
        raise sqlite3.OperationalError("fixture audit transaction failure")

    monkeypatch.setattr(runtime.repo, "_event", fail)
    before = _durable_snapshot(runtime.repo)
    with pytest.raises(sqlite3.OperationalError, match="audit transaction failure"):
        asyncio.run(capabilities.record_tool("article.read", "failed-audit", "started"))
    assert _durable_snapshot(runtime.repo) == before
