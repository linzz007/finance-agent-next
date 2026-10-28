"""Fixed personal limits share a durable Run pool without prepaid child grants."""

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from policy_impact.dispatch import CallerContext, DispatchError, DispatchRepository, SchedulerConfig
from policy_impact.dispatch.contracts import Resume, Send, Spawn
from policy_impact.dispatch.execution import ExecutionCapabilities
from policy_impact.dispatch.finance_tool_contracts import delegation_policy
from policy_impact.dispatch.storage import INDEPENDENT_BUDGET_VERSION, VERSION, Database, digest
from tests.test_dispatch_foundation import Clock, claim, finish, state, status


@pytest.fixture
def fixed(tmp_path):
    clock = Clock()
    config = SchedulerConfig(stream_name="h2:fixed-budgets:fixture")
    repo = DispatchRepository(tmp_path / "h2.sqlite", tmp_path / "records", clock=clock,
                              config=config, profiles={"fixture": {
                                  "model_id": "fixture", "base_url": "https://fixture.invalid/v1",
                                  "protocol": "openai-compatible",
                              }}, roles={"analyst@2.0.0": ["agent.spawn", "agent.send"]})
    repo.migrate()
    root = repo.create_run(run_id="fixed", session_ref="fixture", query="Explain source reasoning",
                           selected_profile="fixture", allowed_profiles=["fixture"],
                           tools=["agent.spawn", "agent.send"])["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    return repo, clock, root, user


def spawn(repo, caller, key="child", **changes):
    args = {"dispatch_key": key, "role": "analyst", "role_version": "2.0.0",
            "objective": "Explain the assigned source", "success_criteria": ["Preserve conditions"],
            "input_refs": [], "output_contract": {"name": "fixture", "version": "1"}}
    args.update(changes)
    return repo.spawn(caller, Spawn(**args))["data"]


def charge(repo, lease, key, *, final=False):
    repo.reserve_call(lease, key, final=final)
    repo.call_started(lease, key)
    repo.call_finished(lease, key, status="succeeded")


def balance(repo, budget):
    with repo.db.connection() as c:
        return repo._balance(c, budget)


def assert_conservation(repo, run_id="fixed"):
    with repo.db.connection() as c:
        run = repo._run_budget(c, run_id)
        budgets = c.execute("SELECT budget_id FROM h2_budgets WHERE run_id=?", (run_id,)).fetchall()
        assert sum(repo._balance(c, b[0]) for b in budgets) + run["consumed"] == run["limit"]
        assert all(repo._balance(c, b[0]) >= 0 for b in budgets)
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_default_fixed_limits_and_idle_children_do_not_reserve(fixed):
    repo, _, root, user = fixed
    children = [spawn(repo, user, f"idle-{i}") for i in range(12)]
    root_budget = status(repo, user, root["task_id"])["budget"]
    assert root_budget["allocated"] == 120 and root_budget["run_budget"] == {
        "limit": 600, "consumed": 0, "remaining": 600,
    }
    with repo.db.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM h2_budget_entries").fetchone()[0] == 1
    for child in children:
        view = status(repo, user, child["task_id"])["budget"]
        assert view["allocated"] == view["remaining"] == 60
    assert spawn(repo, user, "idle-0") == children[0]
    assert_conservation(repo)


def test_root_120_and_child_60_are_independent(fixed):
    repo, _, root, user = fixed
    parent = claim(repo, root["task_id"])
    child = spawn(repo, parent.caller())
    worker = claim(repo, child["task_id"])
    for i in range(60):
        charge(repo, worker, f"child-{i}")
    with pytest.raises(DispatchError) as child_error:
        repo.reserve_call(worker, "child-61")
    assert child_error.value.details == {"scope": "task"}
    assert status(repo, user, root["task_id"])["budget"]["consumed"] == 0
    for i in range(120):
        charge(repo, parent, f"root-{i}")
    with pytest.raises(DispatchError) as root_error:
        repo.reserve_call(parent, "root-121", final=True)
    assert root_error.value.details == {"scope": "task"}
    view = status(repo, user, root["task_id"])["budget"]
    assert view["consumed"] == 120 and view["run_budget"]["consumed"] == 180
    assert_conservation(repo)


def test_child_can_read_six_articles_and_deliver_after_eight_calls(fixed):
    repo, _, root, user = fixed
    child = spawn(repo, user)
    worker = claim(repo, child["task_id"])
    # Describe, six first pages, continuation page, then a result-producing call.
    for i in range(9):
        charge(repo, worker, f"read-or-final-{i}")
    finish(repo, worker)
    view = status(repo, user, child["task_id"])
    assert view["status"] == "succeeded" and view["result_ref"]
    assert view["budget"]["consumed"] == 9
    assert status(repo, user, root["task_id"])["budget"]["consumed"] == 0
    assert_conservation(repo)


def test_grandchild_charges_run_pool_not_parent_allowance(fixed):
    repo, _, root, user = fixed
    child = spawn(repo, user)
    first = claim(repo, child["task_id"])
    grandchild = spawn(repo, first.caller(), "grandchild", wall_time_ms=300000)
    second = claim(repo, grandchild["task_id"])
    for i in range(60):
        charge(repo, second, f"deep-{i}")
    charge(repo, first, "parent-own-call")
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1
    assert status(repo, user, grandchild["task_id"])["budget"]["consumed"] == 60
    assert status(repo, user, root["task_id"])["budget"]["run_budget"]["consumed"] == 61
    with pytest.raises(DispatchError, match="depth_exceeded"):
        spawn(repo, second.caller(), "too-deep")
    assert_conservation(repo)


def test_reopen_resume_preserves_personal_and_run_consumption(fixed):
    repo, clock, root, user = fixed
    child = spawn(repo, user)
    first = claim(repo, child["task_id"])
    checkpoint = repo.save_checkpoint(first, state(repo, first))
    for i in range(8):
        charge(repo, first, f"original-{i}")
    repo.fail(first, "provider_error")
    reopened = DispatchRepository(repo.db.path, repo.artifacts.root, clock=clock,
                                  config=replace(repo.config, child_task_calls=2, root_task_calls=3),
                                  profiles=repo.profiles, roles=repo.roles)
    reopened.resume(user, Resume(task_id=child["task_id"], checkpoint_ref=checkpoint,
                                idempotency_key="resume"))
    next_lease = claim(reopened, child["task_id"])
    for i in range(52):
        charge(reopened, next_lease, f"resumed-{i}")
    with pytest.raises(DispatchError, match="budget_denied"):
        reopened.reserve_call(next_lease, "over-limit")
    another = spawn(reopened, user, "after-config-change")
    assert status(reopened, user, another["task_id"])["budget"]["allocated"] == 60
    assert status(reopened, user, root["task_id"])["budget"]["allocated"] == 120
    assert_conservation(reopened)


def test_fixed_followup_gets_new_personal_limit_without_preallocation(fixed):
    repo, _, root, user = fixed
    child = spawn(repo, user)
    first = claim(repo, child["task_id"])
    charge(repo, first, "first")
    finish(repo, first)
    reply = repo.send(user, Send(thread_id=child["thread_id"], idempotency_key="followup",
                                content="Explain a further assumption", follow_up={
                                    "output_contract": {"name": "fixture", "version": "1"},
                                    "success_criteria": ["Preserve evidence"],
                                }))["data"]
    assert reply["created_follow_up_task"]
    worker = claim(repo, reply["task_id"])
    charge(repo, worker, "followup-call")
    assert status(repo, user, reply["task_id"])["budget"]["allocated"] == 60
    assert status(repo, user, root["task_id"])["budget"]["run_budget"]["consumed"] == 2
    assert_conservation(repo)


def test_unsent_refunds_only_once_and_unknown_stays_charged(fixed):
    repo, _, root, user = fixed
    child = spawn(repo, user)
    worker = claim(repo, child["task_id"])
    repo.reserve_call(worker, "unsent")
    repo.refund_unsent_call(worker, "unsent")
    repo.refund_unsent_call(worker, "unsent")
    repo.reserve_call(worker, "unknown")
    repo.call_started(worker, "unknown")
    repo.call_finished(worker, "unknown", status="unknown")
    with pytest.raises(DispatchError, match="invalid_state"):
        repo.refund_unsent_call(worker, "unknown")
    assert status(repo, user, child["task_id"])["budget"]["consumed"] == 1
    repo.fail(worker, "transport_error")
    with repo.db.connection() as c:
        budget = c.execute("SELECT budget_ref FROM h2_tasks WHERE task_id=?", (child["task_id"],)).fetchone()[0]
    assert balance(repo, budget) == 0
    assert status(repo, user, root["task_id"])["budget"]["run_budget"]["consumed"] == 1
    assert_conservation(repo)


def test_concurrent_reservations_cannot_spend_shared_closing_reserve(fixed):
    repo, _, root, user = fixed
    repo.config = replace(repo.config, run_concurrency=20, worker_concurrency=20)
    children = [spawn(repo, user, f"c-{i}") for i in range(12)]
    leases = [claim(repo, child["task_id"], worker=f"worker-{i}") for i, child in enumerate(children)]
    for i, lease in enumerate(leases[:9]):
        for j in range(60):
            charge(repo, lease, f"pre-{i}-{j}")
    for j in range(35):
        charge(repo, leases[9], f"near-cap-{j}")
    # Exactly one credit remains outside the existing 24-call Root reserve.
    barrier = Barrier(2)

    def reserve(index):
        barrier.wait(timeout=10)
        try:
            repo.reserve_call(leases[index], f"race-{index}")
            return "reserved"
        except DispatchError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(reserve, [10, 11]))
    assert sorted(results) == ["budget_denied", "reserved"]
    checkpoint = repo.save_checkpoint(leases[9], state(repo, leases[9]))
    repo.fail(leases[9], "provider_error")
    assert status(repo, user, children[9]["task_id"])["budget"]["local_available"] == 0
    with pytest.raises(DispatchError) as resume_error:
        repo.resume(user, Resume(task_id=children[9]["task_id"], checkpoint_ref=checkpoint,
                                 idempotency_key="reserve-protected"))
    assert resume_error.value.details == {"scope": "run"}
    parent = claim(repo, root["task_id"])
    for j in range(24):
        charge(repo, parent, f"root-closing-{j}", final=True)
    with pytest.raises(DispatchError) as exc:
        repo.reserve_call(parent, "run-601", final=True)
    assert exc.value.details == {"scope": "run"}
    assert status(repo, user, root["task_id"])["budget"]["run_budget"] == {
        "limit": 600, "consumed": 600, "remaining": 0,
    }
    assert_conservation(repo)


def test_fixed_descriptions_and_validation_do_not_allow_model_call_counts(fixed):
    repo, _, root, user = fixed
    lease = claim(repo, root["task_id"])
    from policy_impact.dispatch.worker import WorkInput

    work = WorkInput(run_id=lease.run_id, task_id=lease.task_id, thread_id=lease.thread_id,
                     attempt_id=lease.attempt_id, **repo.execution_input(lease))
    capabilities = ExecutionCapabilities(repo, lease, work)

    async def run():
        for name in ["agent.spawn", "agent.send"]:
            description = await capabilities.describe(name)
            assert "requested_budget" not in json.dumps(description)
            assert "wall_time_ms" in json.dumps(description)
        with pytest.raises(DispatchError) as error:
            await capabilities.tool("agent.spawn", {"requested_budget": {"model_calls": 8}})
        assert error.value.code == "invalid_arguments"

    asyncio.run(run())
    with pytest.raises(DispatchError) as error:
        spawn(repo, user, requested_budget={"model_calls": 8, "wall_time_ms": 600000})
    assert error.value.code == "invalid_arguments"
    policy = delegation_policy({"budget": work.brief["budget"]})
    assert policy["call_counts_model_selected"] is False and policy["child_task_calls"] == 60


def test_additive_migration_preserves_old_ddl_and_budget_data(tmp_path):
    path = tmp_path / "old.sqlite"
    sql = Path("src/policy_impact/dispatch/schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(path) as connection:
        connection.executescript(sql)
        connection.execute("CREATE TABLE h2_schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
        connection.execute("INSERT INTO h2_schema_migrations VALUES (?,?)",
                           (VERSION, hashlib.sha256(sql.encode()).hexdigest()))
    db = Database(path)
    db.migrate()
    db.migrate()
    with db.connection() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(h2_budgets)")}
        assert "self_call_limit" in columns
        assert connection.execute("SELECT count(*) FROM h2_schema_migrations").fetchone()[0] == 2
        assert connection.execute("SELECT checksum FROM h2_schema_migrations WHERE version=?", (VERSION,)).fetchone()[0] == hashlib.sha256(sql.encode()).hexdigest()


def test_populated_legacy_upgrade_retains_balances_and_command_replays(tmp_path):
    from tests.test_dispatch_foundation import spawn as legacy_spawn

    repo = DispatchRepository(tmp_path / "legacy.sqlite", tmp_path / "records", clock=Clock(),
                              config=SchedulerConfig(budget_policy="hierarchical"),
                              profiles={"fixture": {"model_id": "fixture", "base_url": "https://fixture.invalid/v1",
                                                    "protocol": "openai-compatible"}},
                              roles={"analyst@2.0.0": ["agent.spawn", "agent.send"]})
    repo.migrate()
    root = repo.create_run(run_id="legacy", session_ref="fixture", query="Preserve historical accounting",
                           selected_profile="fixture", allowed_profiles=["fixture"],
                           tools=["agent.spawn", "agent.send"])["data"]
    user = CallerContext(root["run_id"], root["thread_id"], root["task_id"], trusted_user=True)
    child = legacy_spawn(repo, user)["data"]
    lease = claim(repo, child["task_id"])
    charge(repo, lease, "old-call")
    finish(repo, lease)
    follow_up_args = {
        "thread_id": child["thread_id"], "idempotency_key": "old-followup", "content": "Explain more",
        "follow_up": {"requested_budget": {"model_calls": 20, "wall_time_ms": 300000},
                      "output_contract": {"name": "fixture", "version": "1"}, "success_criteria": ["Evidence"]},
    }
    reply = repo.send(user, Send(**follow_up_args))["data"]
    with repo.db.connection() as connection:
        before = {name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
                  for name in ("h2_model_calls", "h2_budget_entries")}
    # Recreate the pre-extension schema with real Tasks, charges and immutable receipts.
    with sqlite3.connect(repo.db.path) as connection:
        connection.execute("ALTER TABLE h2_budgets DROP COLUMN self_call_limit")
        connection.execute("DELETE FROM h2_schema_migrations WHERE version=?", (INDEPENDENT_BUDGET_VERSION,))
        for operation in ("agent.spawn", "agent.send"):
            for row in connection.execute("SELECT command_id,response_json FROM h2_commands WHERE operation=?",
                                          (operation,)).fetchall():
                data = json.loads(row[1])["data"]
                if operation == "agent.spawn":
                    payload = Spawn(dispatch_key="child", role="analyst", role_version="2.0.0",
                                    objective="explain the reasoning", success_criteria=["preserve conditions"],
                                    input_refs=[], output_contract={"name": "fixture.understanding", "version": "1"},
                                    requested_budget={"model_calls": 30, "wall_time_ms": 600000}).model_dump()
                    payload.pop("wall_time_ms")
                    # New optional controls were absent from historical command hashes.
                    payload.pop("review_mode")
                    payload.pop("review_options")
                else:
                    payload = Send(**follow_up_args).model_dump()
                    payload["follow_up"].pop("wall_time_ms")
                connection.execute("UPDATE h2_commands SET request_hash=? WHERE command_id=?",
                                   (digest(payload), row[0]))
                assert data["task_id"] in {child["task_id"], reply["task_id"]}
    repo.migrate()
    repo.config = replace(repo.config, budget_policy="fixed")
    assert legacy_spawn(repo, user)["data"] == child
    assert repo.send(user, Send(**follow_up_args))["data"] == reply
    with repo.db.connection() as connection:
        for name, rows in before.items():
            assert [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")] == rows
        assert connection.execute("SELECT count(*) FROM h2_budgets WHERE self_call_limit IS NOT NULL").fetchone()[0] == 0
    view = status(repo, user, root["task_id"])["budget"]
    assert "policy" not in view and view["consumed"] == 1 and view["remaining"] == 599
    assert status(repo, user, reply["task_id"])["budget"]["allocated"] == 20


def test_migration_bad_checksum_does_not_change_rows(fixed):
    repo, _, _, _ = fixed
    with repo.db.connection(write=True) as connection:
        connection.execute("UPDATE h2_schema_migrations SET checksum='invalid' WHERE version=?",
                           (INDEPENDENT_BUDGET_VERSION,))
    with repo.db.connection() as connection:
        before = [tuple(row) for row in connection.execute("SELECT * FROM h2_budget_entries")]
    with pytest.raises(DispatchError) as error:
        repo.migrate()
    assert error.value.code == "schema_version_mismatch"
    with repo.db.connection() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM h2_budget_entries")] == before
        assert connection.execute("SELECT count(*) FROM h2_budgets").fetchone()[0] == 1
