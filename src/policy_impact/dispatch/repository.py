"""SQLite authority for durable Agent identities and execution lifecycles.

Only this repository writes scheduling state. Transport ownership is not a lease.
Artifacts are prepared before write transactions; DB rollback may leave orphans.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .contracts import (
    TERMINAL,
    CallerContext,
    Cancel,
    CheckpointState,
    DispatchConstraints,
    DispatchError,
    Lease,
    ModelPolicySet,
    ResearchClock,
    Resume,
    SchedulerConfig,
    Send,
    Spawn,
    Submission,
    Wait,
)
from .diagnostics import failure_diagnostic
from .diagnostics import public_output_normalization as _public_output_normalization
from .observability import RuntimeRecords
from .storage import SAFE_ID, Artifact, ArtifactStore, Database, digest, encode, new_id, now_ms


def _insert(c, table: str, **values):
    c.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


class DispatchRepository:
    def __init__(
        self,
        db_path: Path,
        records_root: Path,
        *,
        profiles: dict,
        roles: dict[str, list[str]],
        config: SchedulerConfig | None = None,
        clock: Callable[[], int] = now_ms,
        evidence_validator: Callable[[str, list[dict]], bool] | None = None,
    ):
        self.db = Database(db_path)
        self.artifacts = ArtifactStore(records_root)
        self.records = RuntimeRecords(records_root)
        self.config = config or SchedulerConfig()
        self.clock = clock
        self.profiles = json.loads(encode(profiles))
        self.roles = json.loads(encode(roles))
        allowed = {
            "model_id",
            "base_url",
            "protocol",
            "auth_env",
            "stream",
            "thinking",
            "reasoning_effort",
            "parameters",
            "prompt_version",
            "tool_contract_version",
        }
        for value in self.profiles.values():
            if set(value) - allowed or not {"model_id", "base_url", "protocol"} <= set(value):
                raise ValueError("profiles must contain metadata only")
            self._no_secrets(value)
        self.evidence_validator = evidence_validator or (lambda run, refs: not refs)

    @staticmethod
    def _no_secrets(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"api_key", "token", "authorization", "password", "secret"}:
                    raise ValueError("credentials do not belong in snapshots")
                DispatchRepository._no_secrets(item)
        elif isinstance(value, list):
            for item in value:
                DispatchRepository._no_secrets(item)
        elif isinstance(value, str):
            if re.search(r"(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+\S+)", value, re.IGNORECASE):
                raise ValueError("credentials do not belong in snapshots")
            if value.startswith(("http://", "https://")):
                url = urlsplit(value)
                if url.username or url.password:
                    raise ValueError("credential-bearing endpoint")
                credential_names = {
                    "api_key", "apikey", "access_token", "auth", "authorization",
                    "password", "passwd", "secret", "client_secret", "token", "signature", "sig",
                }
                for name, _ in parse_qsl(url.query, keep_blank_values=True):
                    normalized = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
                    if normalized in credential_names or normalized.endswith(("_token", "_secret", "_key")):
                        raise ValueError("credential-bearing endpoint")

    def migrate(self):
        self.db.migrate()

    def _register(self, c, art: Artifact):
        _insert(c, "h2_artifacts", **asdict(art), created_at=self.clock())

    def _snapshot(self, c, run_id, kind, payload):
        ref = new_id("snap")
        _insert(
            c,
            "h2_snapshots",
            snapshot_id=ref,
            run_id=run_id,
            kind=kind,
            payload_json=encode(payload),
            sha256=digest(payload),
            created_at=self.clock(),
        )
        return ref

    @staticmethod
    def _payload(c, ref):
        row = c.execute(
            "SELECT payload_json FROM h2_snapshots WHERE snapshot_id=?", (ref,)
        ).fetchone()
        if row is None:
            raise DispatchError("not_found")
        return json.loads(row[0])

    def _event(self, c, task, event_type, payload=None, *, key=None):
        thread = task["thread_id"]
        c.execute(
            "UPDATE h2_threads SET last_event_seq=last_event_seq+1,updated_at=? WHERE thread_id=?",
            (self.clock(), thread),
        )
        seq = c.execute(
            "SELECT last_event_seq FROM h2_threads WHERE thread_id=?", (thread,)
        ).fetchone()[0]
        cursor = c.execute(
            "INSERT INTO h2_events (event_key,run_id,thread_id,thread_seq,"
            "aggregate_type,aggregate_id,event_type,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                key or new_id("evt"),
                task["run_id"],
                thread,
                seq,
                "task",
                task["task_id"],
                event_type,
                encode(payload or {}),
                self.clock(),
            ),
        )
        return str(cursor.lastrowid)

    @staticmethod
    def _task(c, task_id):
        row = c.execute("SELECT * FROM h2_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise DispatchError("not_found")
        return row

    @staticmethod
    def _thread(c, thread_id):
        row = c.execute("SELECT * FROM h2_threads WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None:
            raise DispatchError("not_found")
        return row

    def _actor(self, c, caller: CallerContext, *, write=False):
        task = self._task(c, caller.task_id)
        if task["run_id"] != caller.run_id or task["thread_id"] != caller.thread_id:
            raise DispatchError("not_found")
        run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (caller.run_id,)).fetchone()
        if caller.trusted_user:
            if run["root_thread_id"] != caller.thread_id:
                raise DispatchError("forbidden")
        else:
            a = c.execute(
                "SELECT * FROM h2_attempts WHERE attempt_id=? AND task_id=?",
                (caller.attempt_id, caller.task_id),
            ).fetchone()
            if not a or not caller.lease_token or a["lease_token"] != caller.lease_token:
                raise DispatchError("stale_lease")
            if write:
                self._fence(c, caller)
        return task

    def _access(self, c, caller, task_id):
        task = self._task(c, task_id)
        if task["run_id"] != caller.run_id:
            raise DispatchError("not_found")
        ancestors = c.execute(
            """WITH RECURSIVE a(thread_id,parent_thread_id) AS (
          SELECT thread_id,parent_thread_id FROM h2_threads WHERE thread_id=?
          UNION ALL SELECT t.thread_id,t.parent_thread_id FROM h2_threads t
          JOIN a ON a.parent_thread_id=t.thread_id)
          SELECT thread_id FROM a""",
            (task["thread_id"],),
        ).fetchall()
        if caller.thread_id not in {r[0] for r in ancestors}:
            raise DispatchError("not_found")
        return task

    def _fence(self, c, caller):
        t = self._task(c, caller.task_id)
        a = c.execute(
            "SELECT * FROM h2_attempts WHERE attempt_id=?", (caller.attempt_id,)
        ).fetchone()
        run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (caller.run_id,)).fetchone()
        if t["cancel_requested_at"] is not None:
            raise DispatchError("cancelled")
        if (
            not a
            or t["current_attempt_id"] != caller.attempt_id
            or a["lease_token"] != caller.lease_token
            or a["status"] not in {"leased", "running"}
            or a["lease_expires_at"] <= self.clock()
            or t["status"] != "running"
            or run["status"] in TERMINAL
            or min(t["deadline_at"], run["deadline_at"]) <= self.clock()
        ):
            raise DispatchError("stale_lease")
        return t, a

    def _open(self, c, task):
        run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (task["run_id"],)).fetchone()
        if run["status"] in TERMINAL or task["cancel_requested_at"] is not None:
            raise DispatchError("invalid_state")
        if min(task["deadline_at"], run["deadline_at"]) <= self.clock():
            raise DispatchError("deadline_exceeded")
        return run

    def _replay(self, c, caller, op, key, payload):
        self._actor(c, caller)
        row = c.execute(
            "SELECT * FROM h2_commands WHERE run_id=? AND caller_thread_id=? "
            "AND operation=? AND idempotency_key=?",
            (caller.run_id, caller.thread_id, op, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest(payload):
            raise DispatchError("idempotency_conflict")
        receipt = json.loads(row["response_json"])
        receipt["replayed"] = True
        return receipt

    def _receipt(self, c, caller, op, key, payload, data):
        cursor = c.execute(
            "SELECT COALESCE(MAX(event_id),0) FROM h2_events WHERE run_id=?", (caller.run_id,)
        ).fetchone()[0]
        result = {
            "schema_version": "h2.agent.v1",
            "request_id": new_id("req"),
            "ok": True,
            "data": data,
            "error": None,
            "event_cursor": str(cursor),
            "replayed": False,
        }
        _insert(
            c,
            "h2_commands",
            command_id=new_id("cmd"),
            run_id=caller.run_id,
            caller_thread_id=caller.thread_id,
            operation=op,
            idempotency_key=key,
            request_hash=digest(payload),
            response_json=encode(result),
            created_at=self.clock(),
        )
        return result

    @staticmethod
    def _balance(c, budget):
        return c.execute(
            "SELECT COALESCE(SUM(delta_calls),0) FROM h2_budget_entries WHERE budget_id=?",
            (budget,),
        ).fetchone()[0]

    def _entry(self, c, run_id, budget, key, kind, amount, call_id=None):
        if amount:
            _insert(
                c,
                "h2_budget_entries",
                entry_id=new_id("be"),
                run_id=run_id,
                budget_id=budget,
                transaction_key=key,
                kind=kind,
                call_id=call_id,
                delta_calls=amount,
                created_at=self.clock(),
            )

    def _transfer(self, c, run_id, source, target, count, key):
        if type(count) is not int or count < 0:
            raise DispatchError("budget_denied")
        if self._balance(c, source) < count:
            raise DispatchError("budget_denied")
        self._entry(c, run_id, source, key, "transfer_out", -count)
        self._entry(c, run_id, target, key, "transfer_in", count)

    def _allocate(self, c, parent, request, budget_id):
        b = c.execute(
            "SELECT * FROM h2_budgets WHERE budget_id=?", (parent["budget_ref"],)
        ).fetchone()
        if request.model_calls > self._balance(c, b["budget_id"]) - b["final_reserve_calls"]:
            raise DispatchError("budget_denied")
        if self.clock() + request.wall_time_ms > b["deadline_at"] - b["final_reserve_ms"]:
            raise DispatchError(
                "budget_denied", "requested duration exceeds parent research budget"
            )
        deadline = self.clock() + request.wall_time_ms
        _insert(
            c,
            "h2_budgets",
            budget_id=budget_id,
            run_id=parent["run_id"],
            parent_budget_id=b["budget_id"],
            requested_calls=request.model_calls,
            allocated_calls=request.model_calls,
            final_reserve_calls=0,
            allocation_status="granted",
            deadline_at=deadline,
            final_reserve_ms=min(self.config.child_reserve_ms, request.wall_time_ms // 10),
            created_at=self.clock(),
        )
        self._transfer(
            c, parent["run_id"], b["budget_id"], budget_id, request.model_calls, new_id("allocate")
        )
        return deadline

    def _fixed_policy(self, c, run_id):
        root = c.execute(
            "SELECT t.brief_ref,b.* FROM h2_runs r JOIN h2_tasks t ON t.task_id=r.root_task_id "
            "JOIN h2_budgets b ON b.budget_id=r.budget_ref WHERE r.run_id=?", (run_id,),
        ).fetchone()
        if root["self_call_limit"] is None:
            return None
        artifact = self._artifact_rows(c, run_id, [root["brief_ref"]])[0]
        policy = json.loads(self.artifacts.read(artifact))["budget_policy"]
        return {
            **policy,
            "minimum_child_ms": policy.get("minimum_child_ms", 0),
            "required_review_child_ms": policy.get("required_review_child_ms", policy["child_ms"]),
            "child_window_policy": policy.get("child_window_policy", "legacy_shrink_to_fit"),
            "root_budget_id": root["budget_id"],
            "run_calls": root["allocated_calls"],
        }

    @staticmethod
    def _clock_at(created_at):
        return {
            "today": datetime.fromtimestamp(created_at / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "timezone": "Asia/Shanghai",
        }

    def _research_clock(self, c, run_id):
        root = c.execute(
            "SELECT r.created_at,t.brief_ref FROM h2_runs r JOIN h2_tasks t "
            "ON t.task_id=r.root_task_id WHERE r.run_id=?", (run_id,),
        ).fetchone()
        artifact = self._artifact_rows(c, run_id, [root["brief_ref"]])[0]
        stored = json.loads(self.artifacts.read(artifact))
        if "research_clock" not in stored:
            return self._clock_at(root["created_at"])
        try:
            return ResearchClock.model_validate(stored["research_clock"]).model_dump()
        except ValidationError as exc:
            raise DispatchError("contract_error", "persisted Run research clock is invalid") from exc

    @staticmethod
    def _self_consumed(c, budget_id):
        return c.execute(
            "SELECT COALESCE(-SUM(delta_calls),0) FROM h2_budget_entries "
            "WHERE budget_id=? AND kind IN ('charge','refund')", (budget_id,),
        ).fetchone()[0]

    def _run_budget(self, c, run_id):
        row = c.execute(
            "SELECT b.* FROM h2_runs r JOIN h2_budgets b ON b.budget_id=r.budget_ref "
            "WHERE r.run_id=?", (run_id,),
        ).fetchone()
        consumed = c.execute(
            "SELECT COALESCE(-SUM(delta_calls),0) FROM h2_budget_entries "
            "WHERE run_id=? AND kind IN ('charge','refund')", (run_id,),
        ).fetchone()[0]
        return {"limit": row["allocated_calls"], "consumed": consumed,
                "remaining": row["allocated_calls"] - consumed}

    def _allocate_fixed(self, c, parent, command, budget_id, *, root_task=False):
        policy = self._fixed_policy(c, parent["run_id"])
        if command.requested_budget is not None:
            raise DispatchError("invalid_arguments", "call limits are fixed by Runtime")
        b = c.execute("SELECT * FROM h2_budgets WHERE budget_id=?", (parent["budget_ref"],)).fetchone()
        remaining_ms = b["deadline_at"] - b["final_reserve_ms"] - self.clock()
        duration_kind = "explicit"
        if command.wall_time_ms is None:
            duration_kind = "default"
            default_ms = policy["child_ms"]
            if (getattr(command, "role", None) == "analyst"
                    and getattr(command, "review_mode", None) == "required"):
                default_ms = policy["required_review_child_ms"]
                duration_kind = "required_review"
            if policy["child_window_policy"] == "legacy_shrink_to_fit":
                duration = min(default_ms, remaining_ms)
            elif duration_kind == "required_review":
                duration = default_ms
            else:
                duration = min(default_ms, remaining_ms)
                if duration < policy["minimum_child_ms"]:
                    raise DispatchError(
                        "budget_denied",
                        "remaining time is below the minimum default child window",
                        details={"scope": "time", "required_ms": policy["minimum_child_ms"],
                                 "available_ms": max(0, remaining_ms), "window_kind": duration_kind},
                    )
        else:
            duration = command.wall_time_ms
        if duration <= 0 or duration > remaining_ms:
            raise DispatchError(
                "budget_denied",
                "child time window does not fit the parent's remaining research time",
                details={"scope": "time", "required_ms": duration,
                         "available_ms": max(0, remaining_ms), "window_kind": duration_kind},
            )
        run = self._run_budget(c, parent["run_id"])
        root = c.execute("SELECT * FROM h2_budgets WHERE budget_id=?",
                         (policy["root_budget_id"],)).fetchone()
        if run["remaining"] <= root["final_reserve_calls"]:
            raise DispatchError("budget_denied", details={"scope": "run"})
        limit = policy["root_task_calls"] if root_task else policy["child_task_calls"]
        deadline = self.clock() + duration
        _insert(c, "h2_budgets", budget_id=budget_id, run_id=parent["run_id"],
                parent_budget_id=policy["root_budget_id"], requested_calls=limit,
                allocated_calls=limit, self_call_limit=limit, final_reserve_calls=0,
                allocation_status="granted", deadline_at=deadline,
                final_reserve_ms=min(policy["child_reserve_ms"], duration // 10), created_at=self.clock())
        return deadline

    def _release_budget(self, c, task):
        b = c.execute(
            "SELECT * FROM h2_budgets WHERE budget_id=?", (task["budget_ref"],)
        ).fetchone()
        if b["parent_budget_id"]:
            count = self._balance(c, b["budget_id"])
            self._transfer(
                c, task["run_id"], b["budget_id"], b["parent_budget_id"], count, new_id("release")
            )
            if b["self_call_limit"] is not None:
                return
            parent = c.execute(
                "SELECT * FROM h2_tasks WHERE budget_ref=? ORDER BY rowid DESC LIMIT 1",
                (b["parent_budget_id"],),
            ).fetchone()
            if parent and parent["status"] in TERMINAL:
                self._release_budget(c, parent)

    def _attempt(self, c, task, checkpoint=None):
        attempt_id = new_id("att")
        number = c.execute(
            "SELECT COALESCE(MAX(attempt_no),0)+1 FROM h2_attempts WHERE task_id=?",
            (task["task_id"],),
        ).fetchone()[0]
        _insert(
            c,
            "h2_attempts",
            attempt_id=attempt_id,
            run_id=task["run_id"],
            task_id=task["task_id"],
            attempt_no=number,
            attempt_key=new_id("atk"),
            status="queued",
            resume_checkpoint_ref=checkpoint,
            created_at=self.clock(),
        )
        c.execute(
            "UPDATE h2_tasks SET current_attempt_id=?,status='pending_dispatch',"
            "finished_at=NULL,row_version=row_version+1,updated_at=? WHERE task_id=?",
            (attempt_id, self.clock(), task["task_id"]),
        )
        self._outbox(c, self._task(c, task["task_id"]), attempt_id)
        return attempt_id

    def _outbox(self, c, task, attempt_id):
        number = c.execute(
            "SELECT COALESCE(MAX(dispatch_generation),0)+1 FROM h2_outbox WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()[0]
        attempt = c.execute(
            "SELECT attempt_key FROM h2_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        outbox_id = new_id("obx")
        payload = {
            "schema_version": "h2.dispatch.v1",
            "outbox_id": outbox_id,
            "run_id": task["run_id"],
            "task_id": task["task_id"],
            "attempt_id": attempt_id,
            "attempt_key": attempt[0],
            "dispatch_key": task["dispatch_key"],
            "dispatch_generation": str(number),
            "emitted_at": str(self.clock()),
        }
        _insert(
            c,
            "h2_outbox",
            outbox_id=outbox_id,
            run_id=task["run_id"],
            task_id=task["task_id"],
            attempt_id=attempt_id,
            dispatch_key=task["dispatch_key"],
            dispatch_generation=number,
            stream_name=self.config.stream_name,
            payload_json=encode(payload),
            status="pending",
            next_retry_at=self.clock(),
            created_at=self.clock(),
        )
        return outbox_id

    def _pending_outbox_count(self, c):
        return c.execute(
            "SELECT COUNT(*) FROM h2_outbox "
            "WHERE status IN ('pending','retrying','publishing')"
        ).fetchone()[0]

    def _refresh_queue_pressure(self, c, now):
        """Apply watermarks without blocking publication of accepted work."""

        pending = self._pending_outbox_count(c)
        high = pending >= self.config.outbox_high_watermark
        changed = []
        if pending <= self.config.outbox_low_watermark:
            rows = c.execute(
                """SELECT t.task_id FROM h2_tasks t
                JOIN h2_outbox o ON o.task_id=t.task_id AND o.attempt_id=t.current_attempt_id
                WHERE t.status='backpressured'
                  AND o.status IN ('pending','retrying','publishing')"""
            ).fetchall()
            for row in rows:
                c.execute(
                    "UPDATE h2_tasks SET status='pending_dispatch',updated_at=?,row_version=row_version+1 "
                    "WHERE task_id=? AND status='backpressured'",
                    (now, row[0]),
                )
                task = self._task(c, row[0])
                self._event(c, task, "task.backpressure_released", {"reason": "queue_low_watermark"})
                changed.append(row[0])
        elif high:
            rows = c.execute(
                """SELECT t.task_id FROM h2_tasks t
                JOIN h2_outbox o ON o.task_id=t.task_id AND o.attempt_id=t.current_attempt_id
                WHERE t.status='pending_dispatch'
                  AND o.status IN ('pending','retrying','publishing')"""
            ).fetchall()
            for row in rows:
                c.execute(
                    "UPDATE h2_tasks SET status='backpressured',updated_at=?,row_version=row_version+1 "
                    "WHERE task_id=? AND status='pending_dispatch'",
                    (now, row[0]),
                )
                task = self._task(c, row[0])
                self._event(c, task, "task.backpressured", {"reason": "queue_high_watermark"})
                changed.append(row[0])
        return {
            "pending_outbox": pending,
            "high_watermark": self.config.outbox_high_watermark,
            "low_watermark": self.config.outbox_low_watermark,
            "over_high_watermark": high,
            "changed_task_ids": changed,
        }

    def queue_pressure(self):
        """Return and persist the current queue watermark projection."""

        with self.db.connection(write=True) as c:
            return self._refresh_queue_pressure(c, self.clock())

    def _message(self, c, task, ref, key, kind):
        c.execute(
            "UPDATE h2_threads SET last_message_seq=last_message_seq+1 WHERE thread_id=?",
            (task["thread_id"],),
        )
        sequence = self._thread(c, task["thread_id"])["last_message_seq"]
        mid = new_id("msg")
        _insert(
            c,
            "h2_messages",
            message_id=mid,
            run_id=task["run_id"],
            thread_id=task["thread_id"],
            task_id=task["task_id"],
            sequence=sequence,
            kind=kind,
            payload_ref=ref,
            idempotency_key=key,
            status="queued",
            created_at=self.clock(),
        )
        return mid, sequence

    def _artifact_rows(self, c, run_id, refs):
        rows = []
        for ref in refs:
            row = c.execute(
                "SELECT * FROM h2_artifacts WHERE run_id=? AND artifact_ref=?", (run_id, ref)
            ).fetchone()
            if row is None:
                raise DispatchError("not_found")
            rows.append(row)
        return rows

    def _inputs(self, c, caller, refs):
        rows = self._artifact_rows(c, caller.run_id, refs)
        thread = self._thread(c, caller.thread_id)
        if thread["depth"]:
            allowed = set(json.loads(self._task(c, caller.task_id)["input_refs_json"]))
            for result in c.execute(
                """WITH RECURSIVE tree(thread_id) AS (
                                    SELECT ? UNION ALL SELECT t.thread_id FROM h2_threads t
                                    JOIN tree ON t.parent_thread_id=tree.thread_id)
                                    SELECT r.full_ref,r.summary_ref,r.sections_json FROM h2_results r
                                    JOIN h2_tasks t ON t.task_id=r.task_id JOIN tree ON t.thread_id=tree.thread_id
                                    WHERE r.disposition IN ('candidate','accepted')""",
                (caller.thread_id,),
            ):
                allowed.update([result["full_ref"], result["summary_ref"]])
                allowed.update(json.loads(result["sections_json"]).values())
            for event in c.execute(
                "SELECT payload_json FROM h2_events WHERE run_id=? AND thread_id=? "
                "AND event_type='artifact.created'",
                (caller.run_id, caller.thread_id),
            ):
                allowed.add(json.loads(event[0])["artifact_ref"])
            for message in c.execute(
                "SELECT payload_ref FROM h2_messages WHERE thread_id=?", (caller.thread_id,)
            ):
                allowed.add(message[0])
            if set(refs) - allowed:
                raise DispatchError("not_found")
        return rows

    def create_run(
        self,
        *,
        run_id: str,
        session_ref: str,
        query: str,
        selected_profile: str,
        allowed_profiles: list[str],
        tools: list[str],
        research_clock: dict | None = None,
        instruction_rules: dict | None = None,
        dispatch_constraints: dict | None = None,
    ):
        if not SAFE_ID.fullmatch(run_id) or selected_profile not in allowed_profiles:
            raise DispatchError("invalid_arguments")
        if not allowed_profiles or set(allowed_profiles) - self.profiles.keys():
            raise DispatchError("invalid_arguments")
        if research_clock is not None:
            try:
                research_clock = ResearchClock.model_validate(research_clock).model_dump()
            except ValidationError as exc:
                raise DispatchError("invalid_arguments", "invalid research clock") from exc
        if dispatch_constraints is not None:
            try:
                dispatch_constraints = DispatchConstraints.model_validate(dispatch_constraints).model_dump()
            except ValidationError as exc:
                raise DispatchError("invalid_arguments", "invalid dispatch constraints") from exc
        payload = {
            "run_id": run_id,
            "session_ref": session_ref,
            "query": query,
            "selected_profile": selected_profile,
            "allowed_profiles": allowed_profiles,
            "tools": tools,
        }
        if research_clock is not None:
            payload["research_clock"] = research_clock
        if dispatch_constraints is not None:
            payload["dispatch_constraints"] = dispatch_constraints
        with self.db.connection() as c:
            old = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                caller = CallerContext(
                    run_id, old["root_thread_id"], old["root_task_id"], trusted_user=True
                )
                return self._replay(c, caller, "run.create", run_id, payload)
        now = self.clock()
        query_art = self.artifacts.put(run_id, "query", query.encode())
        brief_payload = {
            "objective": query, "input_refs": [query_art.artifact_ref],
            "research_clock": research_clock if research_clock is not None else self._clock_at(now),
        }
        if dispatch_constraints is not None:
            brief_payload["dispatch_constraints"] = dispatch_constraints
        if self.config.budget_policy == "fixed":
            brief_payload["budget_policy"] = {
                "name": "fixed", "root_task_calls": self.config.root_task_calls,
                "child_task_calls": self.config.child_task_calls, "child_ms": self.config.child_ms,
                "minimum_child_ms": self.config.minimum_child_ms,
                "required_review_child_ms": self.config.required_review_child_ms,
                "child_window_policy": "minimum_floor",
                "child_reserve_ms": self.config.child_reserve_ms,
            }
        rules_art = None
        if instruction_rules is not None:
            self._no_secrets(instruction_rules)
            rules_art = self.artifacts.json(run_id, "instruction_rules", instruction_rules)
            brief_payload["instruction_rules_ref"] = rules_art.artifact_ref
            brief_payload["instruction_rules_sha256"] = rules_art.sha256
        brief = self.artifacts.json(run_id, "brief", brief_payload)
        with self.db.connection(write=True) as c:
            old = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                caller = CallerContext(
                    run_id, old["root_thread_id"], old["root_task_id"], trusted_user=True
                )
                return self._replay(c, caller, "run.create", run_id, payload)
            tid, task_id, budget_id = new_id("thr"), new_id("tsk"), new_id("bud")
            policy_id = new_id("snap")
            deadline = now + self.config.run_ms
            _insert(
                c,
                "h2_runs",
                run_id=run_id,
                session_ref=session_ref,
                status="running",
                root_thread_id=tid,
                root_task_id=task_id,
                budget_ref=budget_id,
                model_policy_ref=policy_id,
                query_ref=query_art.artifact_ref,
                deadline_at=deadline,
                created_at=now,
            )
            policy = {
                "selected_profile": selected_profile,
                "allowed_profiles": allowed_profiles,
                "auto_fallback": False,
            }
            _insert(
                c,
                "h2_snapshots",
                snapshot_id=policy_id,
                run_id=run_id,
                kind="model_policy",
                payload_json=encode(policy),
                sha256=digest(policy),
                created_at=now,
            )
            permission = self._snapshot(
                c, run_id, "permission", {"tools": tools, "roles": list(self.roles)}
            )
            _insert(
                c,
                "h2_budgets",
                budget_id=budget_id,
                run_id=run_id,
                requested_calls=self.config.root_calls,
                allocated_calls=self.config.root_calls,
                self_call_limit=(self.config.root_task_calls if self.config.budget_policy == "fixed" else None),
                final_reserve_calls=self.config.root_reserve_calls,
                allocation_status="granted",
                deadline_at=deadline,
                final_reserve_ms=self.config.run_reserve_ms,
                created_at=now,
            )
            self._entry(c, run_id, budget_id, "initial", "grant", self.config.root_calls)
            _insert(
                c,
                "h2_threads",
                thread_id=tid,
                run_id=run_id,
                root_thread_id=tid,
                depth=0,
                role="main",
                role_version="1.0.0",
                status="ready",
                model_policy_ref=policy_id,
                permission_snapshot_ref=permission,
                budget_ref=budget_id,
                created_at=now,
                updated_at=now,
            )
            self._register(c, query_art)
            self._register(c, brief)
            if rules_art is not None:
                self._register(c, rules_art)
            _insert(
                c,
                "h2_tasks",
                task_id=task_id,
                run_id=run_id,
                thread_id=tid,
                task_type="research",
                objective="Answer the run query",
                brief_ref=brief.artifact_ref,
                input_refs_json=encode([query_art.artifact_ref]),
                output_contract_json=encode({"name": "root.final", "version": "1"}),
                status="pending_dispatch",
                budget_ref=budget_id,
                deadline_at=deadline,
                dispatch_key="root",
                created_at=now,
                updated_at=now,
            )
            task = self._task(c, task_id)
            attempt = self._attempt(c, task)
            self._message(c, task, query_art.artifact_ref, "initial", "initial")
            self._event(c, task, "run.created")
            caller = CallerContext(run_id, tid, task_id, trusted_user=True)
            return self._receipt(
                c,
                caller,
                "run.create",
                run_id,
                payload,
                {
                    "run_id": run_id,
                    "thread_id": tid,
                    "task_id": task_id,
                    "attempt_id": attempt,
                    "status": "pending_dispatch",
                },
            )

    def spawn(self, caller: CallerContext, request: Spawn):
        with self.db.connection() as c:
            parent = self._actor(c, caller)
            thread = self._thread(c, parent["thread_id"])
            self._inputs(c, caller, request.input_refs + request.parent_context_refs)
            permission = self._payload(c, thread["permission_snapshot_ref"])
            policy = self._payload(c, thread["model_policy_ref"])
            instruction_snapshot = self._run_instruction_rules(c, caller.run_id).get("instruction_rules")
            from policy_impact.runtime.research_contracts import ReviewPolicy

            from .finance_rules import FinanceRules

            rules = FinanceRules.from_snapshot(instruction_snapshot) if instruction_snapshot else None
            payload = request.model_dump()
            for key in ("review_mode", "review_options"):
                if payload[key] is None:
                    payload.pop(key)
            if payload.get("review_policy") is not None and payload["review_policy"].get("instructions") is None:
                payload["review_policy"].pop("instructions", None)
            if (rules is not None and rules.has_profiles and not rules.portable_profiles
                    and request.review_policy is not None):
                if request.review_policy.instructions is not None:
                    raise DispatchError("invalid_arguments", "review instructions require the new Profile rules")
                if "max_rounds" not in request.review_policy.model_fields_set:
                    payload["review_policy"]["max_rounds"] = 3
            new_review_controls = request.review_mode is not None or request.review_options is not None
            if (rules is not None and rules.review_verdicts and request.role == "analyst"
                    and request.role_version == "2.0.0"):
                explicit_mode = (request.review_policy.mode if request.review_policy is not None
                                 else request.review_mode)
                if explicit_mode is None:
                    raise DispatchError("invalid_arguments", "Analyst review_mode must be explicit", details={
                        "field": "profile_options.review_mode", "expected": "required, auto or off",
                    })
                required_mode = self._run_dispatch_constraints(c, caller.run_id).get("analyst_review_mode")
                if required_mode is not None and explicit_mode != required_mode:
                    raise DispatchError("invalid_arguments", "Analyst mode conflicts with immutable Run constraints",
                                        details={"field": "profile_options.review_mode", "expected": required_mode})
            if new_review_controls and (rules is None or not rules.portable_profiles):
                raise DispatchError("invalid_arguments", "review_mode requires the new Profile rules")
            if (rules is not None and rules.has_profiles and request.role == "analyst"
                    and request.role_version == "2.0.0" and request.review_policy is None):
                options = request.review_options.model_dump(exclude_none=True) if request.review_options else {}
                payload["review_policy"] = ReviewPolicy(
                    mode=request.review_mode or "auto", max_rounds=options.pop(
                        "max_rounds", 5 if rules.portable_profiles else 3,
                    ), **options,
                ).model_dump(mode="json", exclude_none=True)
            if request.wall_time_ms is None:
                payload.pop("wall_time_ms")
            replay = self._replay(c, caller, "agent.spawn", request.dispatch_key, payload)
            if replay:
                return replay
        profile = request.model_profile or policy["selected_profile"]
        brief = self.artifacts.json(
            caller.run_id,
            "brief",
            {
                **payload,
                "parent_task_id": caller.task_id,
                "parent_thread_id": caller.thread_id,
                "depth": thread["depth"] + 1,
                "model_profile": profile,
            },
        )
        with self.db.connection(write=True) as c:
            replay = self._replay(c, caller, "agent.spawn", request.dispatch_key, payload)
            if replay:
                return replay
            parent = self._actor(c, caller, write=True)
            self._tool_permission(c, caller, "agent.spawn")
            run = self._open(c, parent)
            if parent["status"] in TERMINAL or parent["status"] == "waiting":
                raise DispatchError("invalid_state")
            current_thread = self._thread(c, parent["thread_id"])
            if current_thread["model_policy_ref"] != thread["model_policy_ref"]:
                raise DispatchError(
                    "state_conflict", "parent model policy changed while preparing child; retry command"
                )
            if thread["depth"] >= 2:
                raise DispatchError("depth_exceeded")
            role_key = f"{request.role}@{request.role_version}"
            if role_key not in self.roles or role_key not in permission["roles"]:
                raise DispatchError("forbidden")
            current_rules_snapshot = self._run_instruction_rules(c, caller.run_id).get("instruction_rules")
            if current_rules_snapshot != instruction_snapshot:
                raise DispatchError("state_conflict", "Run instruction snapshot changed while spawning")
            if rules is not None and rules.has_profiles:
                if thread["depth"] == 0:
                    permitted_children = set(rules.allowed_root_children())
                else:
                    permitted_children = set(
                        rules.role_profile(thread["role"], thread["role_version"])["allowed_child_profiles"]
                    )
                if request.role not in permitted_children:
                    raise DispatchError("forbidden", "parent Profile cannot spawn this child role")
                target_profile = rules.role_profile(request.role, request.role_version)
                output_key = f"{request.output_contract.name}@{request.output_contract.version}"
                if output_key not in target_profile["output_contracts"]:
                    raise DispatchError("invalid_arguments", "output contract is not registered for this Profile")
                if request.role == "analyst" and request.role_version == "2.0.0":
                    if "finance.analyst_result@2" not in target_profile["output_contracts"]:
                        raise DispatchError("contract_error", "Analyst v2 result contract is not registered")
                    if output_key != "finance.analyst_result@2":
                        raise DispatchError("invalid_arguments", "Analyst v2 requires finance.analyst_result@2")
                    if request.review_request is not None:
                        raise DispatchError("invalid_arguments", "Analyst tasks cannot receive a verifier ReviewRequest")
                    if payload.get("review_policy") is None:
                        raise DispatchError("contract_error", "Analyst v2 requires a ReviewPolicy")
                elif request.review_policy is not None or new_review_controls:
                    raise DispatchError("invalid_arguments", "ReviewPolicy is valid only for Analyst v2")
                if request.review_request is not None:
                    expected_report = "finance.review_report@2" if rules.review_verdicts else "finance.review_report@1"
                    if request.role != "verifier" or output_key != expected_report:
                        raise DispatchError("invalid_arguments", "ReviewRequest requires the Verifier report contract")
                    if "finance.review_request@1" not in target_profile["input_contracts"]:
                        raise DispatchError("contract_error", "Verifier ReviewRequest contract is not registered")
                    granted_refs = set(request.input_refs + request.parent_context_refs)
                    review_request = request.review_request
                    required_refs = {review_request.draft_ref}
                    required_refs.update(ref for target in review_request.targets for ref in target.evidence_refs)
                    if not required_refs <= granted_refs:
                        raise DispatchError("invalid_arguments", "ReviewRequest references are not explicit task inputs")
                    if thread["role"] != "analyst":
                        raise DispatchError("forbidden", "only an Analyst may create an article review task")
                    parent_brief_row = self._artifact_rows(c, caller.run_id, [parent["brief_ref"]])[0]
                    parent_brief = json.loads(self.artifacts.read(parent_brief_row))
                    review_policy = parent_brief.get("review_policy") or payload.get("review_policy")
                    review_policy = ReviewPolicy.model_validate(review_policy or {}, strict=True)
                    if review_policy.mode == "off":
                        raise DispatchError("forbidden", "article review is disabled by the task policy")
                    prior_review_rows = c.execute(
                        "SELECT t.brief_ref FROM h2_tasks t JOIN h2_threads th ON th.thread_id=t.thread_id "
                        "WHERE t.parent_task_id=? AND th.role='verifier'",
                        (caller.task_id,),
                    ).fetchall()
                    review_count = 0
                    for row in prior_review_rows:
                        child_brief = self._artifact_rows(c, caller.run_id, [row["brief_ref"]])[0]
                        try:
                            if json.loads(self.artifacts.read(child_brief)).get("review_request") is not None:
                                review_count += 1
                        except (TypeError, ValueError):
                            raise DispatchError("checkpoint_invalid", "invalid existing review TaskBrief")
                    if (review_request.round_number != review_count + 1
                            or review_request.round_number > review_policy.max_rounds
                            or review_request.round_number > 5):
                        raise DispatchError("depth_exceeded", "review round limit reached")
                elif request.role == "verifier" and output_key in {"finance.review_report@1", "finance.review_report@2"}:
                    raise DispatchError("invalid_arguments", "review report task requires a ReviewRequest")
            current_policy = self._payload(c, run["model_policy_ref"])
            if (
                profile not in policy["allowed_profiles"]
                or profile not in current_policy["allowed_profiles"]
            ):
                raise DispatchError("forbidden")
            if c.execute(
                "SELECT 1 FROM h2_tasks WHERE run_id=? AND dispatch_key=?",
                (caller.run_id, request.dispatch_key),
            ).fetchone():
                raise DispatchError("idempotency_conflict")
            count = c.execute(
                "SELECT COUNT(*) FROM h2_tasks WHERE parent_task_id=?", (caller.task_id,)
            ).fetchone()[0]
            if count >= self.config.max_children:
                raise DispatchError("backpressured")
            for dep in request.depends_on_task_ids:
                self._access(c, caller, dep)
                if dep == caller.task_id:
                    raise DispatchError("invalid_arguments", "child cannot depend on its parent")
            self._inputs(c, caller, request.input_refs + request.parent_context_refs)
            tid, task_id, budget_id = new_id("thr"), new_id("tsk"), new_id("bud")
            if self._fixed_policy(c, caller.run_id):
                deadline = self._allocate_fixed(c, parent, request, budget_id)
            else:
                if request.requested_budget is None:
                    raise DispatchError("invalid_arguments", "requested_budget is required for this Run")
                deadline = self._allocate(c, parent, request.requested_budget, budget_id)
            child_policy = self._snapshot(
                c, caller.run_id, "model_policy", {**policy, "selected_profile": profile}
            )
            child_tools = set(permission["tools"]) & set(self.roles[role_key])
            if rules is not None and rules.has_profiles:
                child_tools &= set(target_profile["allowed_tools"])
            child_permission = self._snapshot(
                c,
                caller.run_id,
                "permission",
                {
                    "tools": sorted(child_tools),
                    "roles": permission["roles"],
                },
            )
            self._register(c, brief)
            now = self.clock()
            initial_status = (
                "backpressured"
                if self._pending_outbox_count(c) >= self.config.outbox_high_watermark
                else "pending_dispatch"
            )
            _insert(
                c,
                "h2_threads",
                thread_id=tid,
                run_id=caller.run_id,
                parent_thread_id=thread["thread_id"],
                root_thread_id=thread["root_thread_id"],
                depth=thread["depth"] + 1,
                role=request.role,
                role_version=request.role_version,
                status="ready",
                model_policy_ref=child_policy,
                permission_snapshot_ref=child_permission,
                budget_ref=budget_id,
                created_at=now,
                updated_at=now,
            )
            _insert(
                c,
                "h2_tasks",
                task_id=task_id,
                run_id=caller.run_id,
                thread_id=tid,
                parent_task_id=caller.task_id,
                task_type="delegated",
                objective=request.objective,
                brief_ref=brief.artifact_ref,
                input_refs_json=encode(request.input_refs + request.parent_context_refs),
                output_contract_json=encode(request.output_contract.model_dump()),
                priority=request.priority,
                status=initial_status,
                budget_ref=budget_id,
                deadline_at=deadline,
                dispatch_key=request.dispatch_key,
                created_at=now,
                updated_at=now,
            )
            for dep in request.depends_on_task_ids:
                _insert(
                    c,
                    "h2_task_dependencies",
                    run_id=caller.run_id,
                    task_id=task_id,
                    depends_on_task_id=dep,
                )
            task = self._task(c, task_id)
            aid = self._attempt(c, task)
            if initial_status == "backpressured":
                c.execute(
                    "UPDATE h2_tasks SET status='backpressured',updated_at=?,row_version=row_version+1 "
                    "WHERE task_id=?",
                    (now, task_id),
                )
                task = self._task(c, task_id)
            self._message(c, task, brief.artifact_ref, "initial", "initial")
            self._event(
                c,
                task,
                "task.spawned",
                {
                    "parent_task_id": caller.task_id,
                    "initial_status": initial_status,
                },
            )
            return self._receipt(
                c,
                caller,
                "agent.spawn",
                request.dispatch_key,
                payload,
                {
                    "accepted": True,
                    "thread_id": tid,
                    "task_id": task_id,
                    "attempt_id": aid,
                    "status": initial_status,
                    "dispatch_state": initial_status,
                    "result_ref": None,
                    "allocated_budget": (
                        request.requested_budget.model_dump()
                        if request.requested_budget is not None
                        else {
                            "model_calls": c.execute(
                                "SELECT self_call_limit FROM h2_budgets WHERE budget_id=?",
                                (budget_id,),
                            ).fetchone()[0],
                            "wall_time_ms": deadline - self.clock(),
                        }
                    ),
                    "deadline_at": deadline,
                },
            )

    def _tool_permission(self, c, caller, tool):
        if not caller.trusted_user:
            thread = self._thread(c, caller.thread_id)
            if tool not in self._payload(c, thread["permission_snapshot_ref"])["tools"]:
                raise DispatchError("forbidden")

    def record_tool(self, lease: Lease, name, action_key, status, artifact_ref=None):
        if (
            any(not isinstance(value, str) or not SAFE_ID.fullmatch(value)
                for value in (name, action_key))
            or not isinstance(status, str)
            or status not in {"started", "succeeded", "failed"}
            or artifact_ref is not None
            and (not isinstance(artifact_ref, str) or not SAFE_ID.fullmatch(artifact_ref))
        ):
            raise DispatchError("invalid_arguments")
        caller = lease.caller()
        with self.db.connection(write=True) as c:
            task, _ = self._fence(c, caller)
            self._tool_permission(c, caller, name)
            if artifact_ref is not None:
                self._inputs(c, caller, [artifact_ref])
            return self._event(
                c, task, "tool.started" if status == "started" else "tool.completed",
                {"name": name, "action_key": action_key, "attempt_id": lease.attempt_id,
                 "status": status, "observation_ref": artifact_ref},
            )

    def set_model_policy(self, caller: CallerContext, request: ModelPolicySet):
        payload = request.model_dump()
        with self.db.connection(write=True) as c:
            if not caller.trusted_user:
                raise DispatchError("forbidden")
            replay = self._replay(
                c, caller, "run.model_policy.set", request.idempotency_key, payload
            )
            if replay:
                return replay
            root = self._actor(c, caller, write=True)
            run = self._open(c, root)
            if run["model_policy_ref"] != request.expected_policy_ref:
                raise DispatchError(
                    "state_conflict", "model policy changed; read the current policy first"
                )
            if set(request.allowed_profiles) - self.profiles.keys():
                raise DispatchError("invalid_arguments")
            ref = self._snapshot(
                c,
                caller.run_id,
                "model_policy",
                {
                    "selected_profile": request.selected_profile,
                    "allowed_profiles": request.allowed_profiles,
                    "auto_fallback": False,
                    "authorization": "user.model_policy.set",
                },
            )
            c.execute("UPDATE h2_runs SET model_policy_ref=? WHERE run_id=?", (ref, caller.run_id))
            c.execute(
                "UPDATE h2_threads SET model_policy_ref=?,updated_at=? WHERE thread_id=?",
                (ref, self.clock(), run["root_thread_id"]),
            )
            self._event(
                c,
                root,
                "run.model_policy_changed",
                {"previous_policy_ref": request.expected_policy_ref, "model_policy_ref": ref},
            )
            return self._receipt(
                c,
                caller,
                "run.model_policy.set",
                request.idempotency_key,
                payload,
                {
                    "model_policy_ref": ref,
                    "auto_fallback": False,
                    "running_attempts_unchanged": True,
                },
            )

    def claim(self, payload: dict, worker_id: str):
        """Return (lease, ack_safe). Busy/blocked deliveries remain pending."""
        with self.db.connection(write=True) as c:
            outbox = c.execute(
                "SELECT * FROM h2_outbox WHERE outbox_id=?", (payload.get("outbox_id"),)
            ).fetchone()
            if not outbox or json.loads(outbox["payload_json"]) != payload:
                raise DispatchError("contract_error", "delivery does not match persisted outbox")
            task = self._task(c, outbox["task_id"])
            attempt = c.execute(
                "SELECT * FROM h2_attempts WHERE attempt_id=?", (outbox["attempt_id"],)
            ).fetchone()
            if task["current_attempt_id"] != attempt["attempt_id"] or task["status"] in TERMINAL:
                return None, True
            if task["cancel_requested_at"] is not None:
                return None, False
            if attempt["status"] not in {"queued"}:
                return None, attempt["status"] in {
                    "suspended",
                    "failed",
                    "expired",
                    "cancelled",
                    "succeeded",
                }
            run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (task["run_id"],)).fetchone()
            if min(task["deadline_at"], run["deadline_at"]) <= self.clock():
                self._terminate(c, task, "timed_out", "timeout")
                return None, True
            if run["status"] in TERMINAL:
                self._terminate(c, task, "cancelled", "run_finished")
                return None, True
            deps = c.execute(
                "SELECT t.status FROM h2_task_dependencies d JOIN h2_tasks t "
                "ON t.task_id=d.depends_on_task_id WHERE d.task_id=?",
                (task["task_id"],),
            ).fetchall()
            if any(r[0] != "succeeded" for r in deps):
                if task["status"] != "blocked":
                    c.execute(
                        "UPDATE h2_tasks SET status='blocked' WHERE task_id=?", (task["task_id"],)
                    )
                    self._event(c, task, "task.dependency_blocked")
                return None, False
            now = self.clock()
            active = c.execute(
                "SELECT run_id,worker_id FROM h2_attempts WHERE status IN ('leased','running') "
                "AND lease_expires_at>?",
                (now,),
            ).fetchall()
            if (
                sum(r["run_id"] == task["run_id"] for r in active) >= self.config.run_concurrency
                or sum(r["worker_id"] == worker_id for r in active)
                >= self.config.worker_concurrency
            ):
                if task["status"] != "backpressured":
                    c.execute(
                        "UPDATE h2_tasks SET status='backpressured' WHERE task_id=?",
                        (task["task_id"],),
                    )
                    self._event(c, task, "task.backpressured")
                return None, False
            thread = self._thread(c, task["thread_id"])
            policy = self._payload(c, thread["model_policy_ref"])
            current = self._payload(c, run["model_policy_ref"])
            profile = policy["selected_profile"]
            if profile not in current["allowed_profiles"]:
                self._terminate(c, task, "failed", "model_policy_revoked")
                return None, True
            model = {
                **self.profiles[profile],
                "profile": profile,
                "policy_ref": thread["model_policy_ref"],
                "permission_ref": thread["permission_snapshot_ref"],
            }
            snap = self._snapshot(c, task["run_id"], "model", model)
            token = secrets.token_hex(32)
            c.execute(
                "UPDATE h2_attempts SET status='leased',worker_id=?,lease_token=?,"
                "lease_expires_at=?,heartbeat_at=?,model_snapshot_ref=?,started_at=? WHERE attempt_id=?",
                (
                    worker_id,
                    token,
                    min(now + self.config.lease_ms, task["deadline_at"]),
                    now,
                    snap,
                    now,
                    attempt["attempt_id"],
                ),
            )
            c.execute(
                "UPDATE h2_tasks SET status='running',updated_at=?,row_version=row_version+1 WHERE task_id=?",
                (now, task["task_id"]),
            )
            c.execute(
                "UPDATE h2_threads SET status='running' WHERE thread_id=?", (task["thread_id"],)
            )
            self._event(
                c,
                task,
                "task.claimed",
                {"attempt_id": attempt["attempt_id"], "worker_id": worker_id},
            )
            return Lease(
                task["run_id"],
                task["thread_id"],
                task["task_id"],
                attempt["attempt_id"],
                worker_id,
                token,
            ), False

    def start(self, lease: Lease):
        with self.db.connection(write=True) as c:
            self._fence(c, lease.caller())
            c.execute(
                "UPDATE h2_attempts SET status='running' WHERE attempt_id=?", (lease.attempt_id,)
            )

    def heartbeat(self, lease: Lease):
        with self.db.connection(write=True) as c:
            task, _ = self._fence(c, lease.caller())
            expiry = min(self.clock() + self.config.lease_ms, task["deadline_at"])
            c.execute(
                "UPDATE h2_attempts SET heartbeat_at=?,lease_expires_at=? WHERE attempt_id=?",
                (self.clock(), expiry, lease.attempt_id),
            )
            return expiry

    def validate_lease(self, lease: Lease):
        with self.db.connection() as c:
            self._fence(c, lease.caller())

    def store_text(self, caller: CallerContext, kind: str, content: str):
        with self.db.connection() as c:
            task = self._actor(c, caller, write=True)
        art = self.artifacts.put(caller.run_id, kind, content.encode())
        with self.db.connection(write=True) as c:
            self._actor(c, caller, write=True)
            self._register(c, art)
            self._event(c, task, "artifact.created", {"artifact_ref": art.artifact_ref})
        return art.artifact_ref

    def read_input(self, caller: CallerContext, artifact_ref: str):
        with self.db.connection() as c:
            self._actor(c, caller)
            row = self._inputs(c, caller, [artifact_ref])[0]
        return self.artifacts.read(row)

    def reserve_call(self, lease: Lease, call_id: str, *, final=False):
        with self.db.connection(write=True) as c:
            task, attempt = self._fence(c, lease.caller())
            prior = c.execute("SELECT * FROM h2_model_calls WHERE call_id=?", (call_id,)).fetchone()
            if prior:
                if prior["attempt_id"] != lease.attempt_id:
                    raise DispatchError("idempotency_conflict")
                return dict(prior)
            run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (task["run_id"],)).fetchone()
            profile = self._payload(c, attempt["model_snapshot_ref"])["profile"]
            if profile not in self._payload(c, run["model_policy_ref"])["allowed_profiles"]:
                raise DispatchError("forbidden")
            budget = c.execute(
                "SELECT * FROM h2_budgets WHERE budget_id=?", (task["budget_ref"],)
            ).fetchone()
            reserve = 0 if final else budget["final_reserve_calls"]
            if budget["self_call_limit"] is not None:
                if self._self_consumed(c, budget["budget_id"]) >= budget["self_call_limit"]:
                    raise DispatchError("budget_denied", details={"scope": "task"})
                run_budget = self._run_budget(c, task["run_id"])
                policy = self._fixed_policy(c, task["run_id"])
                pool = c.execute("SELECT * FROM h2_budgets WHERE budget_id=?",
                                 (policy["root_budget_id"],)).fetchone()
                run_reserve = pool["final_reserve_calls"] if budget["parent_budget_id"] else reserve
                if run_budget["remaining"] <= run_reserve:
                    raise DispatchError("budget_denied", details={"scope": "run"})
                if budget["parent_budget_id"] and self._balance(c, budget["budget_id"]) == 0:
                    self._transfer(c, task["run_id"], policy["root_budget_id"],
                                   budget["budget_id"], 1, call_id)
            if self._balance(c, budget["budget_id"]) <= reserve:
                raise DispatchError("budget_denied")
            if not final and self.clock() >= task["deadline_at"] - budget["final_reserve_ms"]:
                raise DispatchError("budget_denied", "finalization time is reserved")
            _insert(
                c,
                "h2_model_calls",
                call_id=call_id,
                run_id=task["run_id"],
                task_id=task["task_id"],
                attempt_id=lease.attempt_id,
                budget_id=budget["budget_id"],
                model_snapshot_ref=attempt["model_snapshot_ref"],
                status="reserved",
            )
            self._entry(c, task["run_id"], budget["budget_id"], call_id, "charge", -1, call_id)
            self._event(c, task, "model.call_reserved", {"call_id": call_id})
            return {"call_id": call_id, "status": "reserved"}

    def call_started(self, lease: Lease, call_id: str):
        with self.db.connection(write=True) as c:
            task, attempt = self._fence(c, lease.caller())
            call = c.execute(
                "SELECT status FROM h2_model_calls WHERE call_id=? AND attempt_id=?",
                (call_id, lease.attempt_id),
            ).fetchone()
            if call is None or call["status"] != "reserved":
                raise DispatchError("invalid_state", "physical request already started")
            # Reservation is not authorization to send after a user revocation.
            run = c.execute("SELECT model_policy_ref FROM h2_runs WHERE run_id=?", (task["run_id"],)).fetchone()
            profile = self._payload(c, attempt["model_snapshot_ref"])["profile"]
            if profile not in self._payload(c, run["model_policy_ref"])["allowed_profiles"]:
                raise DispatchError("forbidden")
            n = c.execute(
                "UPDATE h2_model_calls SET status='started',started_at=? WHERE call_id=? "
                "AND attempt_id=? AND status='reserved'",
                (self.clock(), call_id, lease.attempt_id),
            ).rowcount
            if not n:
                raise DispatchError("invalid_state", "physical request already started")

    def call_finished(self, lease: Lease, call_id: str, *, status: str, usage=None,
                      error_code=None, output_ref=None, provider_request_id="",
                      public_output_normalization=None):
        if status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid call outcome")
        normalization = _public_output_normalization(public_output_normalization)
        with self.db.connection(write=True) as c:
            # Late usage is audit-only and may not publish a Task result.
            row = c.execute(
                "SELECT * FROM h2_model_calls WHERE call_id=? AND attempt_id=?",
                (call_id, lease.attempt_id),
            ).fetchone()
            if row is None:
                raise DispatchError("not_found")
            a = c.execute(
                "SELECT lease_token FROM h2_attempts WHERE attempt_id=?", (lease.attempt_id,)
            ).fetchone()
            if a[0] != lease.token:
                raise DispatchError("stale_lease")
            if row["status"] in {"reserved", "started", "unknown"}:
                if output_ref:
                    self._inputs(c, lease.caller(), [output_ref])
                c.execute(
                    "UPDATE h2_model_calls SET status=?,usage_json=?,error_code=?,finished_at=? WHERE call_id=?",
                    (status, encode(usage or {}), error_code, self.clock(), call_id),
                )
                self._event(c, self._task(c, lease.task_id), "model.call_finished", {
                    "call_id": call_id, "status": status, "error_code": error_code,
                    "output_ref": output_ref, "provider_request_id": provider_request_id,
                    **({"public_output_normalization": normalization} if normalization else {}),
                })

    def refund_unsent_call(self, lease: Lease, call_id: str):
        with self.db.connection(write=True) as c:
            # Audit settlement may outlive execution authority, but must retain
            # the original identity/token and never make a started call unsent.
            task = self._actor(c, lease.caller())
            row = c.execute(
                "SELECT * FROM h2_model_calls WHERE call_id=? AND attempt_id=?",
                (call_id, lease.attempt_id),
            ).fetchone()
            refunded = c.execute(
                "SELECT 1 FROM h2_budget_entries WHERE call_id=? AND kind='refund'", (call_id,)
            ).fetchone()
            if row and refunded and row["error_code"] == "not_sent":
                return
            if (
                not row
                or row["status"] not in {"reserved", "unknown"}
                or row["started_at"] is not None
            ):
                raise DispatchError("invalid_state")
            if not refunded:
                self._entry(c, task["run_id"], row["budget_id"], call_id, "refund", 1, call_id)
                c.execute(
                    "UPDATE h2_model_calls SET status='failed',error_code='not_sent',finished_at=? "
                    "WHERE call_id=?",
                    (self.clock(), call_id),
                )
                if task["status"] in TERMINAL:
                    self._release_budget(c, task)

    def _checkpoint_art(self, caller, state: CheckpointState):
        with self.db.connection() as c:
            self._actor(c, caller, write=True)
            rows = self._inputs(c, caller, [state.context_ref] + state.input_refs)
            if state.partial_result_ref:
                row = c.execute(
                    "SELECT 1 FROM h2_results WHERE task_id=? AND result_ref=?",
                    (caller.task_id, state.partial_result_ref),
                ).fetchone()
                if not row:
                    raise DispatchError("not_found")
        for row in rows:
            self.artifacts.read(row)
        return self.artifacts.json(
            caller.run_id,
            "checkpoint",
            {**state.model_dump(), "schema_version": "h2.checkpoint.v1"},
        )

    def _save_checkpoint(self, c, caller, state, art):
        task, _ = self._fence(c, caller)
        thread = self._thread(c, caller.thread_id)
        if state.consumed_message_seq > thread["last_message_seq"]:
            raise DispatchError("invalid_arguments")
        undelivered = c.execute(
            "SELECT 1 FROM h2_messages WHERE task_id=? AND sequence<=? AND status='queued' LIMIT 1",
            (caller.task_id, state.consumed_message_seq),
        ).fetchone()
        previous = c.execute(
            "SELECT COALESCE(MAX(consumed_message_seq),0) FROM h2_checkpoints WHERE task_id=?",
            (caller.task_id,),
        ).fetchone()[0]
        if undelivered or state.consumed_message_seq < previous:
            raise DispatchError(
                "invalid_arguments", "message cursor must refer to delivered, non-regressing input"
            )
        max_event = c.execute(
            "SELECT COALESCE(MAX(event_id),0) FROM h2_events WHERE run_id=?", (caller.run_id,)
        ).fetchone()[0]
        if int(state.last_event_id) > max_event:
            raise DispatchError("invalid_arguments")
        number = c.execute(
            "SELECT COALESCE(MAX(checkpoint_version),0)+1 FROM h2_checkpoints WHERE task_id=?",
            (caller.task_id,),
        ).fetchone()[0]
        self._register(c, art)
        ref = new_id("ckpt")
        _insert(
            c,
            "h2_checkpoints",
            checkpoint_ref=ref,
            run_id=caller.run_id,
            task_id=caller.task_id,
            attempt_id=caller.attempt_id,
            checkpoint_version=number,
            schema_version="h2.checkpoint.v1",
            state_ref=art.artifact_ref,
            consumed_message_seq=state.consumed_message_seq,
            last_event_id=int(state.last_event_id),
            created_at=self.clock(),
        )
        c.execute(
            "UPDATE h2_attempts SET checkpoint_ref=? WHERE attempt_id=?", (ref, caller.attempt_id)
        )
        c.execute(
            "UPDATE h2_messages SET status='consumed',consumed_at=?,delivered_attempt_id=? "
            "WHERE task_id=? AND sequence<=? AND status IN ('queued','delivered')",
            (self.clock(), caller.attempt_id, caller.task_id, state.consumed_message_seq),
        )
        self._event(c, task, "checkpoint.saved", {"checkpoint_ref": ref})
        return ref

    def save_checkpoint(self, lease: Lease, state: CheckpointState):
        art = self._checkpoint_art(lease.caller(), state)
        with self.db.connection(write=True) as c:
            return self._save_checkpoint(c, lease.caller(), state, art)

    def _terminate(self, c, task, status, error=None, *, diagnostic=None):
        if task["status"] in TERMINAL:
            return
        attempt_status = "cancelled" if status == "cancelled" else "failed"
        if status == "succeeded":
            attempt_status = "succeeded"
        c.execute(
            "UPDATE h2_attempts SET status=?,finished_at=?,error_code=?,error_message=? "
            "WHERE attempt_id=? AND status IN ('queued','leased','running','suspended')",
            (attempt_status, self.clock(), error,
             diagnostic["message"] if diagnostic else None, task["current_attempt_id"]),
        )
        c.execute(
            "UPDATE h2_tasks SET status=?,finished_at=?,updated_at=?,row_version=row_version+1 "
            "WHERE task_id=?",
            (status, self.clock(), self.clock(), task["task_id"]),
        )
        c.execute(
            "UPDATE h2_threads SET status='ready',updated_at=? WHERE thread_id=?",
            (self.clock(), task["thread_id"]),
        )
        c.execute(
            "UPDATE h2_waits SET status='cancelled',wake_reason=? WHERE owner_task_id=? AND status='waiting'",
            (status, task["task_id"]),
        )
        c.execute(
            "UPDATE h2_model_calls SET status='unknown' WHERE attempt_id=? AND status IN ('started','reserved')",
            (task["current_attempt_id"],),
        )
        self._release_budget(c, task)
        self._event(c, task, f"task.{status}",
                    {"attempt_id": task["current_attempt_id"], **diagnostic}
                    if diagnostic else {"error_code": error})
        run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (task["run_id"],)).fetchone()
        if run["root_task_id"] == task["task_id"]:
            c.execute(
                "UPDATE h2_runs SET status=?,finished_at=? WHERE run_id=?",
                (status, self.clock(), task["run_id"]),
            )
        self._wake(c, changed_task_ids=[task["task_id"]])

    @staticmethod
    def _failed_attempt_count(c, task_id: str) -> int:
        return c.execute(
            "SELECT count(*) FROM h2_attempts WHERE task_id=? AND status IN ('failed','expired')",
            (task_id,),
        ).fetchone()[0]

    def fail(self, lease: Lease, error_code: str, *, retryable=False, details=None):
        if error_code not in {
            "transport_error",
            "provider_error",
            "contract_error",
            "quota_error",
            "timeout",
            "outcome_unknown",
            "internal_error",
            "checkpoint_invalid",
            "artifact_unavailable",
            "budget_denied",
            "forbidden",
        }:
            raise ValueError("unknown execution error")
        with self.db.connection(write=True) as c:
            task, attempt = self._fence(c, lease.caller())
            diagnostic = failure_diagnostic(error_code, details)
            may_retry = retryable and error_code == "transport_error"
            if may_retry:
                c.execute(
                    "UPDATE h2_attempts SET status='failed',error_code=?,error_message=?,finished_at=? WHERE attempt_id=?",
                    (error_code, diagnostic["message"], self.clock(), lease.attempt_id),
                )
                c.execute(
                    "UPDATE h2_model_calls SET status='unknown' WHERE attempt_id=? AND status IN ('started','reserved')",
                    (lease.attempt_id,),
                )
            # Durable wait/resume advances attempt_no without a failed execution.
            if may_retry and self._failed_attempt_count(c, lease.task_id) < self.config.max_attempts:
                self._attempt(
                    c, task, attempt["checkpoint_ref"] or attempt["resume_checkpoint_ref"]
                )
                self._event(c, task, "attempt.transport_retry", {"attempt_id": lease.attempt_id})
            else:
                self._terminate(c, task, "failed", error_code, diagnostic=diagnostic)

    def cancel(self, caller: CallerContext, request: Cancel):
        payload = request.model_dump()
        with self.db.connection(write=True) as c:
            replay = self._replay(c, caller, "agent.cancel", request.idempotency_key, payload)
            if replay:
                return replay
            self._actor(c, caller, write=True)
            self._tool_permission(c, caller, "agent.cancel")
            task = self._access(c, caller, request.task_id)
            rows = c.execute(
                """WITH RECURSIVE tree(id) AS (
              SELECT ? UNION ALL SELECT t.task_id FROM h2_tasks t JOIN tree ON t.parent_task_id=tree.id)
              SELECT t.* FROM h2_tasks t JOIN tree ON t.task_id=tree.id""",
                (task["task_id"],),
            ).fetchall()
            affected = []
            pending = [t for t in rows if t["status"] not in TERMINAL]
            active = {
                t["task_id"] for t in pending if t["status"] in {"leased", "running"}
            }
            # Fence the entire tree before child termination can wake a waiting parent.
            for t in pending:
                c.execute(
                    "UPDATE h2_tasks SET cancel_requested_at=?,cancel_reason=? WHERE task_id=?",
                    (self.clock(), request.reason, t["task_id"]),
                )
            for t in reversed(pending):
                if t["task_id"] not in active:
                    self._terminate(c, self._task(c, t["task_id"]), "cancelled")
                affected.append(t["task_id"])
            return self._receipt(
                c,
                caller,
                "agent.cancel",
                request.idempotency_key,
                payload,
                {
                    "accepted": True,
                    "cancel_requested": bool(affected),
                    "task_id": request.task_id,
                    "affected_task_ids": affected,
                    "settled": not active,
                },
            )

    def settle_cancelled(self, lease: Lease):
        """Commit a running cancellation at a Worker safety boundary.

        Cancellation invalidates the normal write fence, so this narrow method
        validates the exact current Attempt and the persisted cancellation flag
        before terminating it. A stale Worker can never settle a replacement
        Attempt.
        """

        with self.db.connection(write=True) as c:
            task = self._task(c, lease.task_id)
            attempt = c.execute(
                "SELECT * FROM h2_attempts WHERE attempt_id=? AND task_id=?",
                (lease.attempt_id, lease.task_id),
            ).fetchone()
            if (
                task["run_id"] != lease.run_id
                or task["current_attempt_id"] != lease.attempt_id
                or task["cancel_requested_at"] is None
                or not attempt
                or attempt["lease_token"] != lease.token
            ):
                raise DispatchError("stale_lease")
            if task["status"] in TERMINAL:
                return False
            if task["status"] not in {"leased", "running"}:
                raise DispatchError("invalid_state")
            self._terminate(c, task, "cancelled", task["cancel_reason"] or "cancelled")
            return True

    def _checkpoint_for_resume(self, caller, task_id, ref=None):
        with self.db.connection() as c:
            self._actor(c, caller)
            self._access(c, caller, task_id)
            rows = c.execute(
                "SELECT p.*,a.storage_uri,a.sha256,a.byte_length,a.artifact_ref "
                "FROM h2_checkpoints p JOIN h2_artifacts a ON a.artifact_ref=p.state_ref "
                "WHERE p.task_id=? ORDER BY checkpoint_version DESC",
                (task_id,),
            ).fetchall()
        for row in rows:
            if ref and row["checkpoint_ref"] != ref:
                continue
            try:
                value = json.loads(self.artifacts.read(row))
                if value.pop("schema_version", None) != "h2.checkpoint.v1":
                    continue
                state = CheckpointState.model_validate(value)
                with self.db.connection() as c:
                    refs = self._artifact_rows(
                        c, caller.run_id, [state.context_ref] + state.input_refs
                    )
                for art in refs:
                    self.artifacts.read(art)
            except (ValueError, OSError, DispatchError):
                continue
            return row, state
        raise DispatchError("checkpoint_invalid")

    def resume(self, caller: CallerContext, request: Resume):
        payload = request.model_dump()
        with self.db.connection() as c:
            replay = self._replay(c, caller, "agent.resume", request.idempotency_key, payload)
            if replay:
                return replay
        checkpoint, _ = self._checkpoint_for_resume(caller, request.task_id, request.checkpoint_ref)
        with self.db.connection(write=True) as c:
            replay = self._replay(c, caller, "agent.resume", request.idempotency_key, payload)
            if replay:
                return replay
            self._actor(c, caller, write=True)
            self._tool_permission(c, caller, "agent.resume")
            task = self._access(c, caller, request.task_id)
            self._open(c, task)
            if task["status"] not in {"failed", "partial", "timed_out"}:
                raise DispatchError("invalid_state")
            thread = self._thread(c, task["thread_id"])
            if thread["status"] == "closed":
                raise DispatchError("invalid_state")
            if c.execute(
                "SELECT 1 FROM h2_tasks WHERE thread_id=? AND task_id<>? "
                "AND status NOT IN ('succeeded','partial','failed','cancelled','timed_out')",
                (thread["thread_id"], task["task_id"]),
            ).fetchone():
                raise DispatchError("thread_busy")
            policy = self._payload(c, thread["model_policy_ref"])
            if request.model_profile and request.model_profile != policy["selected_profile"]:
                if not caller.trusted_user:
                    raise DispatchError("forbidden")
                run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (caller.run_id,)).fetchone()
                current = self._payload(c, run["model_policy_ref"])
                if request.model_profile not in current["allowed_profiles"]:
                    raise DispatchError("forbidden")
                ref = self._snapshot(
                    c,
                    caller.run_id,
                    "model_policy",
                    {
                        **policy,
                        "selected_profile": request.model_profile,
                        "authorization": "user.resume",
                    },
                )
                c.execute(
                    "UPDATE h2_threads SET model_policy_ref=? WHERE thread_id=?",
                    (ref, thread["thread_id"]),
                )
            budget = c.execute(
                "SELECT * FROM h2_budgets WHERE budget_id=?", (task["budget_ref"],)
            ).fetchone()
            view = self._budget_view(c, budget["budget_id"])
            needed = (budget["self_call_limit"] or budget["allocated_calls"]) - view["consumed"]
            if needed <= 0:
                raise DispatchError("budget_denied")
            if budget["self_call_limit"] is not None:
                if view["local_available"] <= 0:
                    raise DispatchError("budget_denied", details={"scope": "run"})
            elif budget["parent_budget_id"]:
                local = self._balance(c, budget["budget_id"])
                missing = max(0, needed - view["delegated"] - local)
                parent_budget = c.execute(
                    "SELECT * FROM h2_budgets WHERE budget_id=?", (budget["parent_budget_id"],)
                ).fetchone()
                available = max(
                    0, self._balance(c, parent_budget["budget_id"]) - parent_budget["final_reserve_calls"]
                )
                if missing > available:
                    raise DispatchError("budget_denied")
                self._transfer(
                    c,
                    caller.run_id,
                    budget["parent_budget_id"],
                    budget["budget_id"],
                    missing,
                    new_id("resume_budget"),
                )
            c.execute(
                "UPDATE h2_threads SET budget_ref=?,status='ready',updated_at=? WHERE thread_id=?",
                (task["budget_ref"], self.clock(), thread["thread_id"]),
            )
            aid = self._attempt(c, task, checkpoint["checkpoint_ref"])
            self._event(
                c,
                task,
                "task.resumed",
                {"attempt_id": aid, "checkpoint_ref": checkpoint["checkpoint_ref"]},
            )
            return self._receipt(
                c,
                caller,
                "agent.resume",
                request.idempotency_key,
                payload,
                {
                    "accepted": True,
                    "thread_id": task["thread_id"],
                    "task_id": task["task_id"],
                    "new_attempt_id": aid,
                    "resumed_from": checkpoint["checkpoint_ref"],
                    "status": "pending_dispatch",
                    "allocated_budget": {"model_calls": needed},
                },
            )

    @staticmethod
    def _message_target(c, thread_id):
        # Resuming an older Task can make it current without changing created_at.
        task = c.execute(
            "SELECT * FROM h2_tasks WHERE thread_id=? "
            "ORDER BY status IN ('succeeded','partial','failed','cancelled','timed_out'),"
            "created_at DESC,rowid DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        if task is None:
            raise DispatchError("not_found")
        return task

    def send(self, caller: CallerContext, request: Send):
        payload = request.model_dump()
        if request.follow_up is not None and request.follow_up.wall_time_ms is None:
            payload["follow_up"].pop("wall_time_ms")
        with self.db.connection() as c:
            replay = self._replay(c, caller, "agent.send", request.idempotency_key, payload)
            if replay:
                return replay
            self._actor(c, caller)
            th = self._thread(c, request.thread_id)
            latest = self._message_target(c, th["thread_id"])
            self._access(c, caller, latest["task_id"])
            if request.content_ref:
                self._inputs(c, caller, [request.content_ref])
        art = (
            self.artifacts.put(caller.run_id, "message", request.content.encode())
            if request.content is not None
            else None
        )
        brief = self.artifacts.json(
            caller.run_id,
            "brief",
            {
                "objective": "Follow up on the existing Thread",
                "message_ref": art.artifact_ref if art else request.content_ref,
                "previous_task_id": latest["task_id"],
                "previous_result_ref": latest["result_ref"],
                "follow_up": request.follow_up.model_dump() if request.follow_up else None,
            },
        )
        with self.db.connection(write=True) as c:
            replay = self._replay(c, caller, "agent.send", request.idempotency_key, payload)
            if replay:
                return replay
            self._actor(c, caller, write=True)
            self._tool_permission(c, caller, "agent.send")
            th = self._thread(c, request.thread_id)
            task = self._message_target(c, th["thread_id"])
            if task["task_id"] != latest["task_id"] or task["result_ref"] != latest["result_ref"]:
                raise DispatchError(
                    "state_conflict", "Thread changed while preparing follow-up; retry command"
                )
            self._access(c, caller, task["task_id"])
            run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (caller.run_id,)).fetchone()
            if (
                run["status"] in TERMINAL
                or th["status"] == "closed"
                or task["cancel_requested_at"] is not None
            ):
                raise DispatchError("invalid_state")
            created = task["status"] in TERMINAL
            if created != (request.follow_up is not None):
                raise DispatchError(
                    "invalid_arguments", "terminal follow-up requires explicit work contract"
                )
            if art:
                self._register(c, art)
            else:
                self._inputs(c, caller, [request.content_ref])
            if created:
                # A root Thread has no parent Task. Its follow-up still draws
                # from the completed root budget, while a child follow-up
                # reuses the original parent budget after the old child has
                # returned its unused allocation.
                parent = (
                    task
                    if task["parent_task_id"] is None
                    else self._task(c, task["parent_task_id"])
                )
                self._open(c, parent)
                budget_id, task_id = new_id("bud"), new_id("tsk")
                if self._fixed_policy(c, caller.run_id):
                    deadline = self._allocate_fixed(c, parent, request.follow_up, budget_id,
                                                    root_task=task["parent_task_id"] is None)
                else:
                    if request.follow_up.requested_budget is None:
                        raise DispatchError("invalid_arguments", "requested_budget is required for this Run")
                    deadline = self._allocate(c, parent, request.follow_up.requested_budget, budget_id)
                self._register(c, brief)
                _insert(
                    c,
                    "h2_tasks",
                    task_id=task_id,
                    run_id=caller.run_id,
                    thread_id=th["thread_id"],
                    parent_task_id=(
                        parent["task_id"] if task["parent_task_id"] is not None else None
                    ),
                    task_type="follow_up",
                    objective="Respond to follow-up message",
                    brief_ref=brief.artifact_ref,
                    input_refs_json=task["input_refs_json"],
                    output_contract_json=encode(request.follow_up.output_contract.model_dump()),
                    status="pending_dispatch",
                    budget_ref=budget_id,
                    deadline_at=deadline,
                    dispatch_key=f"followup:{new_id('work')}",
                    created_at=self.clock(),
                    updated_at=self.clock(),
                )
                c.execute(
                    "UPDATE h2_threads SET budget_ref=? WHERE thread_id=?",
                    (budget_id, th["thread_id"]),
                )
                task = self._task(c, task_id)
                self._attempt(c, task)
            ref = art.artifact_ref if art else request.content_ref
            mid, sequence = self._message(c, task, ref, request.idempotency_key, "follow_up")
            self._event(c, task, "message.queued", {"message_id": mid, "sequence": sequence})
            self._wake(c, owner_task_ids=[task["task_id"]])
            return self._receipt(
                c,
                caller,
                "agent.send",
                request.idempotency_key,
                payload,
                {
                    "accepted": True,
                    "message_id": mid,
                    "sequence": sequence,
                    "status": "queued",
                    "task_id": task["task_id"],
                    "created_follow_up_task": created,
                },
            )

    def _wait_cycle(self, c, owner, targets):
        edges = {}
        for row in c.execute("SELECT task_id,depends_on_task_id FROM h2_task_dependencies"):
            edges.setdefault(row[0], set()).add(row[1])
        for row in c.execute(
            "SELECT w.owner_task_id,t.task_id FROM h2_waits w JOIN h2_wait_targets t "
            "ON w.wait_id=t.wait_id WHERE w.status='waiting'"
        ):
            edges.setdefault(row[0], set()).add(row[1])
        todo, seen = list(targets), set()
        while todo:
            node = todo.pop()
            if node == owner:
                raise DispatchError("invalid_arguments", "wait/dependency cycle")
            if node not in seen:
                seen.add(node)
                todo.extend(edges.get(node, ()))

    def continue_task(self, lease: Lease, state: CheckpointState):
        art = self._checkpoint_art(lease.caller(), state)
        with self.db.connection(write=True) as c:
            task, _ = self._fence(c, lease.caller())
            ref = self._save_checkpoint(c, lease.caller(), state, art)
            c.execute(
                "UPDATE h2_attempts SET status='suspended',finished_at=? WHERE attempt_id=?",
                (self.clock(), lease.attempt_id),
            )
            successor = self._attempt(c, task, ref)
            c.execute("UPDATE h2_threads SET status='ready' WHERE thread_id=?", (lease.thread_id,))
            self._event(c, task, "task.continued", {
                "previous_attempt_id": lease.attempt_id,
                "attempt_id": successor,
                "checkpoint_ref": ref,
            })
            return successor

    def _validate_wait(self, c, lease: Lease, request: Wait | None):
        caller = lease.caller()
        task, _ = self._fence(c, caller)
        run = c.execute("SELECT deadline_at FROM h2_runs WHERE run_id=?", (lease.run_id,)).fetchone()
        deadline = min(task["deadline_at"], run["deadline_at"])
        if request is not None:
            self._tool_permission(c, caller, "agent.wait")
            for target in request.task_ids:
                self._access(c, caller, target)
            self._wait_cycle(c, task["task_id"], request.task_ids)
            deadline = min(deadline, self.clock() + request.timeout_ms)
        return task, deadline

    def validate_wait(self, lease: Lease, request: Wait):
        """Preflight only; suspend rechecks the same rules in its write transaction."""
        with self.db.connection() as c:
            _, deadline = self._validate_wait(c, lease, request)
            return deadline

    def suspend(self, lease: Lease, state: CheckpointState, request: Wait | None = None):
        art = self._checkpoint_art(lease.caller(), state)
        with self.db.connection(write=True) as c:
            task, deadline = self._validate_wait(c, lease, request)
            ref = self._save_checkpoint(c, lease.caller(), state, art)
            wid = new_id("wait")
            _insert(
                c,
                "h2_waits",
                wait_id=wid,
                run_id=lease.run_id,
                owner_task_id=lease.task_id,
                owner_attempt_id=lease.attempt_id,
                wait_kind="tasks" if request else "mailbox",
                mode=request.mode if request else "all",
                status="waiting",
                after_event_id=int(request.after_event_id) if request else int(state.last_event_id),
                deadline_at=deadline,
                checkpoint_ref=ref,
                created_at=self.clock(),
            )
            if request:
                for target in request.task_ids:
                    _insert(c, "h2_wait_targets", wait_id=wid, run_id=lease.run_id, task_id=target)
            c.execute(
                "UPDATE h2_attempts SET status='suspended',finished_at=? WHERE attempt_id=?",
                (self.clock(), lease.attempt_id),
            )
            c.execute("UPDATE h2_tasks SET status='waiting' WHERE task_id=?", (lease.task_id,))
            c.execute(
                "UPDATE h2_threads SET status='waiting' WHERE thread_id=?", (lease.thread_id,)
            )
            self._event(c, task, "task.waiting", {"wait_id": wid})
            self._wake(c, owner_task_ids=[lease.task_id])
            return wid

    def _wake(self, c, *, owner_task_ids=(), changed_task_ids=()):
        if not owner_task_ids and not changed_task_ids:
            return
        owners = ",".join("?" for _ in owner_task_ids) or "NULL"
        targets = ",".join("?" for _ in changed_task_ids) or "NULL"
        waits = c.execute(
            f"SELECT * FROM h2_waits w WHERE status='waiting' AND "
            f"(owner_task_id IN ({owners}) OR EXISTS (SELECT 1 FROM h2_wait_targets t "
            f"WHERE t.wait_id=w.wait_id AND t.task_id IN ({targets})))",
            (*owner_task_ids, *changed_task_ids),
        ).fetchall()
        for wait in waits:
            task = self._task(c, wait["owner_task_id"])
            if task["status"] != "waiting" or task["cancel_requested_at"] is not None:
                continue
            run = c.execute(
                "SELECT status FROM h2_runs WHERE run_id=?", (task["run_id"],)
            ).fetchone()
            if run["status"] in TERMINAL:
                self._terminate(c, task, "cancelled", "run_finished")
                continue
            if wait["wait_kind"] == "mailbox":
                met = bool(
                    c.execute(
                        "SELECT 1 FROM h2_messages WHERE task_id=? AND status='queued'",
                        (task["task_id"],),
                    ).fetchone()
                )
            else:
                statuses = [
                    r[0]
                    for r in c.execute(
                        "SELECT t.status FROM h2_wait_targets wt "
                        "JOIN h2_tasks t ON t.task_id=wt.task_id WHERE wt.wait_id=?",
                        (wait["wait_id"],),
                    )
                ]
                met = (
                    all(s in TERMINAL for s in statuses)
                    if wait["mode"] == "all"
                    else any(s in TERMINAL for s in statuses)
                )
            timed_out = wait["deadline_at"] <= self.clock()
            if not met and not timed_out:
                continue
            if task["deadline_at"] <= self.clock():
                self._terminate(c, task, "timed_out", "timeout")
                continue
            reason = "condition_met" if met else "timeout"
            c.execute(
                "UPDATE h2_waits SET status=?,wake_reason=? WHERE wait_id=?",
                ("ready" if met else "timed_out", reason, wait["wait_id"]),
            )
            aid = self._attempt(c, task, wait["checkpoint_ref"])
            self._event(
                c,
                task,
                "task.woken",
                {"wait_id": wait["wait_id"], "reason": reason, "attempt_id": aid},
            )

    def mailbox(self, lease: Lease):
        with self.db.connection(write=True) as c:
            self._fence(c, lease.caller())
            rows = c.execute(
                "SELECT * FROM h2_messages WHERE task_id=? AND status IN ('queued','delivered') "
                "ORDER BY sequence",
                (lease.task_id,),
            ).fetchall()
            c.execute(
                "UPDATE h2_messages SET status='delivered',delivered_attempt_id=? "
                "WHERE task_id=? AND status IN ('queued','delivered')",
                (lease.attempt_id, lease.task_id),
            )
            refs = self._artifact_rows(c, lease.run_id, [r["payload_ref"] for r in rows])
        return [{**dict(r), "content": self.artifacts.read(a).decode()} for r, a in zip(rows, refs)]

    def _run_instruction_rules(self, c, run_id):
        root = c.execute(
            "SELECT t.brief_ref FROM h2_runs r JOIN h2_tasks t ON t.task_id=r.root_task_id "
            "WHERE r.run_id=?", (run_id,),
        ).fetchone()
        brief_row = self._artifact_rows(c, run_id, [root["brief_ref"]])[0]
        try:
            brief = json.loads(self.artifacts.read(brief_row))
            if not {"instruction_rules_ref", "instruction_rules_sha256"}.intersection(brief):
                return {}
            ref = brief["instruction_rules_ref"]
            rules_row = self._artifact_rows(c, run_id, [ref])[0]
            if rules_row["kind"] != "instruction_rules" or rules_row["sha256"] != brief["instruction_rules_sha256"]:
                raise ValueError("rule artifact identity changed")
            return {"instruction_rules_ref": ref,
                    "instruction_rules": json.loads(self.artifacts.read(rules_row))}
        except (KeyError, TypeError, ValueError) as exc:
            raise DispatchError("checkpoint_invalid", "invalid Run instruction snapshot") from exc

    def _run_dispatch_constraints(self, c, run_id):
        row = c.execute("SELECT t.brief_ref FROM h2_runs r JOIN h2_tasks t ON t.task_id=r.root_task_id "
                        "WHERE r.run_id=?", (run_id,)).fetchone()
        artifact = self._artifact_rows(c, run_id, [row["brief_ref"]])[0]
        value = json.loads(self.artifacts.read(artifact)).get("dispatch_constraints")
        return DispatchConstraints.model_validate(value).model_dump() if value is not None else {}

    def execution_input(self, lease: Lease, *, context_accepts=None):
        with self.db.connection() as c:
            task, attempt = self._fence(c, lease.caller())
            brief = self._artifact_rows(c, lease.run_id, [task["brief_ref"]])[0]
            ckpt = None
            if attempt["resume_checkpoint_ref"]:
                ckpt = c.execute(
                    "SELECT a.* FROM h2_checkpoints p JOIN h2_artifacts a "
                    "ON a.artifact_ref=p.state_ref WHERE p.checkpoint_ref=?",
                    (attempt["resume_checkpoint_ref"],),
                ).fetchone()
            model = self._payload(c, attempt["model_snapshot_ref"])
            thread = self._thread(c, lease.thread_id)
            runtime_brief = {
                "schema_version": "h2.task_brief.v1",
                "source_brief_ref": task["brief_ref"],
                "run_id": lease.run_id,
                "thread_id": lease.thread_id,
                "task_id": lease.task_id,
                "attempt_id": lease.attempt_id,
                "parent_task_id": task["parent_task_id"],
                "parent_thread_id": thread["parent_thread_id"],
                "depth": thread["depth"],
                "role": thread["role"],
                "role_version": thread["role_version"],
                "input_refs": json.loads(task["input_refs_json"]),
                "output_contract": json.loads(task["output_contract_json"]),
                "allowed_tools": self._payload(c, model["permission_ref"])["tools"],
                "permission_snapshot_ref": model["permission_ref"],
                "model_snapshot_ref": attempt["model_snapshot_ref"],
                "budget": self._budget_view(c, task["budget_ref"]),
                "deadline_at": task["deadline_at"],
                "research_clock": self._research_clock(c, lease.run_id),
                **self._run_instruction_rules(c, lease.run_id),
                "dispatch_constraints": self._run_dispatch_constraints(c, lease.run_id),
            }
            waits = c.execute(
                "SELECT * FROM h2_waits WHERE owner_task_id=? AND checkpoint_ref=? "
                "AND status IN ('ready','timed_out') ORDER BY created_at,wait_id",
                (lease.task_id, attempt["resume_checkpoint_ref"]),
            ).fetchall()
            targets = {
                w["wait_id"]: [
                    r[0]
                    for r in c.execute(
                        "SELECT task_id FROM h2_wait_targets WHERE wait_id=? ORDER BY task_id",
                        (w["wait_id"],),
                    )
                ]
                for w in waits
            }
        if attempt["resume_checkpoint_ref"]:
            # Validate the referenced context too, not only the checkpoint envelope.
            self._checkpoint_for_resume(
                lease.caller(), lease.task_id, attempt["resume_checkpoint_ref"]
            )
        stored_brief = json.loads(self.artifacts.read(brief))
        follow_up = stored_brief.get("follow_up")
        if isinstance(follow_up, dict):
            stored_brief["success_criteria"] = follow_up["success_criteria"]
        data = {
            "brief": {**stored_brief, **runtime_brief},
            "checkpoint": json.loads(self.artifacts.read(ckpt)) if ckpt else None,
            "model": model,
            "messages": self.mailbox(lease),
            "observations": [],
        }
        short_candidates = []
        for wait in waits:
            observed = {"wait_id": wait["wait_id"], "reason": wait["wake_reason"], "tasks": []}
            ids = targets[wait["wait_id"]]
            if ids:
                observed["tasks"] = self.status(lease.caller(), ids)["tasks"]
            data["observations"].append(observed)
            for task in observed["tasks"]:
                if not task["result_ref"]:
                    continue
                summary = self._whole_result(lease.caller(), task["result_ref"], "summary")
                envelope = json.loads(summary.pop("content"))
                task["result"] = {**summary, "envelope": envelope, "full_injected": False}
                with self.db.connection() as c:
                    result = c.execute(
                        "SELECT a.*,r.sections_json FROM h2_results r JOIN h2_artifacts a "
                        "ON a.artifact_ref=r.full_ref WHERE r.result_ref=?",
                        (task["result_ref"],),
                    ).fetchone()
                task["result"]["section_refs"] = json.loads(result["sections_json"])
                if context_accepts is not None and result["byte_length"] <= 16 * 1024:
                    short_candidates.append((task, result))
        # Check the complete mandatory input before considering any optional full text.
        for task, result in short_candidates:
            text = self.artifacts.read(result).decode()
            task["result"].update(full_content=text, full_injected=True)
            if context_accepts(data) is True:
                self._whole_result(lease.caller(), task["result_ref"], "full")
            else:
                task["result"].pop("full_content")
                task["result"]["full_injected"] = False
        with self.db.connection(write=True) as c:
            task, _ = self._fence(c, lease.caller())
            for observation in data["observations"]:
                self._event(
                    c,
                    task,
                    "wait.observed",
                    {
                        "wait_id": observation["wait_id"],
                        "reader_attempt_id": lease.attempt_id,
                        "results": [
                            {
                                "task_id": t["task_id"],
                                "result_ref": t["result_ref"],
                                "full_injected": t.get("result", {}).get("full_injected", False),
                            }
                            for t in observation["tasks"]
                        ],
                    },
                )
        return data

    def _whole_result(self, caller, result_ref, view):
        pages, offset = [], 0
        while True:
            page = self.result_get(caller, result_ref, view=view, offset=offset, limit=64000)
            pages.append(page["content"])
            if not page["has_more"]:
                return {**page, "content": "".join(pages)}
            offset = page["next_offset"]

    def submit(self, lease: Lease, request: Submission):
        caller, payload = lease.caller(), request.model_dump()
        with self.db.connection() as c:
            self._actor(c, caller)
            prior = c.execute(
                "SELECT * FROM h2_results WHERE attempt_id=? AND result_key=?",
                (lease.attempt_id, request.result_key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest(payload):
                    raise DispatchError("idempotency_conflict")
                if prior["disposition"] == "late":
                    raise DispatchError("stale_lease")
                return {
                    "result_ref": prior["result_ref"],
                    "metadata_status": prior["metadata_status"],
                    "result_version": prior["result_version"],
                    "disposition": prior["disposition"],
                    "full_ref": prior["full_ref"],
                }
            if request.full_ref:
                body_rows = self._inputs(c, caller, [request.full_ref])
            else:
                body_rows = []
            body_rows += self._inputs(c, caller, list(request.sections.values()))
        for row in body_rows:
            self.artifacts.read(row)
        full = (
            self.artifacts.put(lease.run_id, "result", request.content.encode())
            if request.content is not None
            else None
        )
        valid_evidence = self.evidence_validator(lease.run_id, request.evidence_refs)
        metadata = bool(request.summary.strip()) and valid_evidence
        envelope = {
            "summary": request.summary,
            "key_points": request.key_points,
            "gaps": request.gaps,
            "warnings": request.warnings + ([] if valid_evidence else ["invalid_evidence_binding"]),
            "evidence_refs": request.evidence_refs if valid_evidence else [],
        }
        summary = self.artifacts.json(lease.run_id, "result_summary", envelope)
        late = None
        with self.db.connection(write=True) as c:
            self._actor(c, caller)
            prior = c.execute(
                "SELECT * FROM h2_results WHERE attempt_id=? AND result_key=?",
                (lease.attempt_id, request.result_key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest(payload):
                    raise DispatchError("idempotency_conflict")
                if prior["disposition"] == "late":
                    raise DispatchError("stale_lease")
                return {
                    "result_ref": prior["result_ref"],
                    "result_version": prior["result_version"],
                    "metadata_status": prior["metadata_status"],
                    "disposition": prior["disposition"],
                    "full_ref": prior["full_ref"],
                }
            try:
                task, _ = self._fence(c, caller)
            except DispatchError as exc:
                late = exc.code
                task = self._task(c, lease.task_id)
            if full:
                self._register(c, full)
            else:
                self._inputs(c, caller, [request.full_ref])
            self._inputs(c, caller, list(request.sections.values()))
            self._register(c, summary)
            version = c.execute(
                "SELECT COALESCE(MAX(result_version),0)+1 FROM h2_results WHERE task_id=?",
                (lease.task_id,),
            ).fetchone()[0]
            result_ref = new_id("res")
            _insert(
                c,
                "h2_results",
                result_ref=result_ref,
                run_id=lease.run_id,
                task_id=lease.task_id,
                attempt_id=lease.attempt_id,
                result_key=request.result_key,
                request_hash=digest(payload),
                result_version=version,
                status=request.status,
                disposition="late" if late else "candidate",
                metadata_status="complete" if metadata else "pending",
                full_ref=full.artifact_ref if full else request.full_ref,
                summary_ref=summary.artifact_ref,
                sections_json=encode(request.sections),
                evidence_refs_json=encode(envelope["evidence_refs"]),
                warnings_json=encode(envelope["warnings"]),
                created_at=self.clock(),
            )
            self._event(
                c,
                task,
                "result.late" if late else "result.candidate_saved",
                {"result_ref": result_ref},
            )
            if not late and metadata:
                self._accept_result(c, task, result_ref, request.status, "complete")
            receipt = {
                "result_ref": result_ref,
                "result_version": version,
                "full_ref": full.artifact_ref if full else request.full_ref,
                "metadata_status": "complete" if metadata else "pending",
                "disposition": "late" if late else "accepted" if metadata else "candidate",
            }
        if late:
            raise DispatchError(late)
        return receipt

    def _accept_result(self, c, task, result_ref, status, metadata_status):
        c.execute(
            "UPDATE h2_results SET disposition='accepted',status=?,metadata_status=? WHERE result_ref=?",
            (status, metadata_status, result_ref),
        )
        c.execute("UPDATE h2_tasks SET result_ref=? WHERE task_id=?", (result_ref, task["task_id"]))
        c.execute(
            "UPDATE h2_attempts SET result_ref=? WHERE attempt_id=?",
            (result_ref, task["current_attempt_id"]),
        )
        self._terminate(c, task, status)
        self._event(c, task, "result.accepted", {"result_ref": result_ref})

    def finalize_incomplete(
        self, lease: Lease, candidate_ref: str, *, reason="incomplete_metadata"
    ):
        """Use after the one metadata-completion opportunity; never discard the body."""
        with self.db.connection(write=True) as c:
            task, _ = self._fence(c, lease.caller())
            row = c.execute(
                "SELECT * FROM h2_results WHERE result_ref=? AND attempt_id=?",
                (candidate_ref, lease.attempt_id),
            ).fetchone()
            if not row or row["disposition"] != "candidate" or row["metadata_status"] != "pending":
                raise DispatchError("invalid_state")
            if reason not in {
                "incomplete_metadata",
                "metadata_repair_failed",
                "metadata_body_changed",
                "metadata_repair_rejected",
            }:
                raise ValueError("invalid metadata failure category")
            self._event(
                c,
                task,
                "result.metadata_incomplete",
                {"result_ref": candidate_ref, "reason": reason},
            )
            self._accept_result(c, task, candidate_ref, "partial", "incomplete_metadata")
            return {
                "result_ref": candidate_ref,
                "task_status": "partial",
                "metadata_status": "incomplete_metadata",
            }

    def _budget_view(self, c, budget_id):
        b = c.execute("SELECT * FROM h2_budgets WHERE budget_id=?", (budget_id,)).fetchone()
        if b["self_call_limit"] is not None:
            consumed = self._self_consumed(c, budget_id)
            remaining = b["self_call_limit"] - consumed
            run = self._run_budget(c, b["run_id"])
            policy = self._fixed_policy(c, b["run_id"])
            reserve = 0
            if b["parent_budget_id"]:
                reserve = c.execute("SELECT final_reserve_calls FROM h2_budgets WHERE budget_id=?",
                                    (policy["root_budget_id"],)).fetchone()[0]
            return {"policy": "fixed", "requested": b["self_call_limit"],
                    "allocated": b["self_call_limit"], "self_call_limit": b["self_call_limit"],
                    "consumed": consumed, "remaining": remaining,
                    "local_available": min(remaining, max(0, run["remaining"] - reserve)), "delegated": 0,
                    "run_budget": run, "child_task_calls": policy["child_task_calls"],
                    "child_window_ms": policy["child_ms"],
                    "minimum_child_window_ms": policy["minimum_child_ms"],
                    "required_review_child_window_ms": policy["required_review_child_ms"],
                    "child_window_policy": policy["child_window_policy"],
                    "exact_cost_limit": False}
        ids = [
            r[0]
            for r in c.execute(
                """WITH RECURSIVE tree(id) AS (
            SELECT ? UNION ALL SELECT b.budget_id FROM h2_budgets b JOIN tree ON b.parent_budget_id=tree.id)
            SELECT id FROM tree""",
                (budget_id,),
            )
        ]
        local = self._balance(c, budget_id)
        delegated = sum(self._balance(c, bid) for bid in ids if bid != budget_id)
        consumed = sum(
            c.execute(
                "SELECT COALESCE(-SUM(delta_calls),0) FROM h2_budget_entries "
                "WHERE budget_id=? AND kind IN ('charge','refund')",
                (bid,),
            ).fetchone()[0]
            for bid in ids
        )
        return {
            "requested": b["requested_calls"],
            "allocated": b["allocated_calls"],
            "consumed": consumed,
            "local_available": local,
            "delegated": delegated,
            "remaining": local + delegated,
            "exact_cost_limit": False,
        }

    def status(
        self,
        caller: CallerContext,
        task_ids: list[str] | None = None,
        *,
        thread_ids: list[str] | None = None,
        after_event_id="0",
        max_events=100,
    ):
        task_ids = [] if task_ids is None else task_ids
        thread_ids = [] if thread_ids is None else thread_ids
        if (
            not task_ids
            and not thread_ids
            or len(task_ids) > 200
            or len(thread_ids) > 200
            or len(set(task_ids)) != len(task_ids)
            or len(set(thread_ids)) != len(thread_ids)
            or not 0 < max_events <= 200
        ):
            raise DispatchError("invalid_arguments")
        if not str(after_event_id).isdigit():
            raise DispatchError("invalid_arguments")
        with self.db.connection() as c:
            self._actor(c, caller)
            selected_task_ids = []
            selected = set()
            for task_id in task_ids:
                self._access(c, caller, task_id)
                selected_task_ids.append(task_id)
                selected.add(task_id)
            for thread_id in thread_ids:
                self._thread(c, thread_id)
                rows = c.execute(
                    "SELECT task_id FROM h2_tasks WHERE run_id=? AND thread_id=? "
                    "ORDER BY created_at,rowid",
                    (caller.run_id, thread_id),
                ).fetchall()
                if not rows:
                    raise DispatchError("not_found")
                for row in rows:
                    task_id = row["task_id"]
                    self._access(c, caller, task_id)
                    if task_id not in selected:
                        selected_task_ids.append(task_id)
                        selected.add(task_id)
            tasks = []
            for task_id in selected_task_ids:
                t = self._access(c, caller, task_id)
                attempts = c.execute(
                    "SELECT attempt_id,attempt_no,status,error_code,error_message,model_snapshot_ref,"
                    "started_at,finished_at FROM h2_attempts WHERE task_id=? ORDER BY attempt_no",
                    (task_id,),
                ).fetchall()
                outbox = c.execute(
                    "SELECT status,last_error,retry_count FROM h2_outbox WHERE task_id=? "
                    "ORDER BY created_at DESC,rowid DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                tasks.append(
                    {
                        "task_id": task_id,
                        "thread_id": t["thread_id"],
                        "status": t["status"],
                        "result_ref": t["result_ref"],
                        "deadline_at": t["deadline_at"],
                        "cancel_requested": t["cancel_requested_at"] is not None,
                        "attempts": [dict(a) for a in attempts],
                        "dispatch": dict(outbox) if outbox else None,
                        "budget": self._budget_view(c, t["budget_ref"]),
                    }
                )
                if t["status"] == "failed":
                    failure = c.execute(
                        "SELECT payload_json FROM h2_events WHERE run_id=? AND aggregate_id=? "
                        "AND event_type='task.failed' ORDER BY event_id DESC LIMIT 1",
                        (caller.run_id, task_id),
                    ).fetchone()
                    if failure:
                        diagnostic = json.loads(failure[0])
                        if diagnostic.get("attempt_id") == t["current_attempt_id"]:
                            tasks[-1]["failure_diagnostic"] = diagnostic
            rows = c.execute(
                "SELECT * FROM h2_events WHERE run_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (caller.run_id, int(after_event_id), max_events + 1),
            ).fetchall()
            events = []
            for row in rows[:max_events]:
                try:
                    self._access(c, caller, row["aggregate_id"])
                except DispatchError:
                    continue
                events.append(
                    {
                        "event_id": str(row["event_id"]),
                        "thread_seq": row["thread_seq"],
                        "event_type": row["event_type"],
                        "task_id": row["aggregate_id"],
                        "payload": json.loads(row["payload_json"]),
                    }
                )
            return {
                "tasks": tasks,
                "events": events,
                "has_more": len(rows) > max_events,
                "next_event_id": str(rows[min(len(rows), max_events) - 1]["event_id"])
                if rows
                else str(after_event_id),
            }

    def wait_snapshot(self, caller: CallerContext, request: Wait):
        def snapshot():
            view = self.status(
                caller,
                request.task_ids,
                after_event_id=request.after_event_id,
                max_events=request.max_events,
            )
            statuses = [t["status"] for t in view["tasks"]]
            met = (
                all(s in TERMINAL for s in statuses)
                if request.mode == "all"
                else any(s in TERMINAL for s in statuses)
            )
            return view, met

        view, met = snapshot()
        if met or request.timeout_ms == 0:
            return {
                **view,
                "condition_met": met,
                "reason": "condition_met" if met else "timeout",
            }

        # This is the read-only HTTP wait, not the durable agent.wait action.
        # Poll between short SQLite snapshots so the connection is never held
        # for the whole timeout and a disconnected client leaves no scheduler
        # state behind.
        deadline = time.monotonic() + request.timeout_ms / 1000
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
            view, met = snapshot()
            if met:
                return {**view, "condition_met": True, "reason": "condition_met"}
        return {**view, "condition_met": False, "reason": "timeout"}

    def result_get(
        self,
        caller: CallerContext,
        result_ref: str,
        *,
        view="summary",
        section=None,
        offset=0,
        limit=16000,
    ):
        if (
            view not in {"summary", "full", "section"}
            or type(offset) is not int
            or offset < 0
            or not 0 < limit <= 64000
        ):
            raise DispatchError("invalid_arguments")
        with self.db.connection() as c:
            self._actor(c, caller)
            row = c.execute(
                "SELECT * FROM h2_results WHERE result_ref=? AND run_id=?",
                (result_ref, caller.run_id),
            ).fetchone()
            if not row or row["disposition"] != "accepted":
                raise DispatchError("not_found")
            refs = json.loads(row["sections_json"])
            ref = (
                row["summary_ref"]
                if view == "summary"
                else row["full_ref"]
                if view == "full"
                else refs.get(section)
            )
            if not ref:
                raise DispatchError("not_found")
            readable_sections = refs
            try:
                self._access(c, caller, row["task_id"])
            except DispatchError:
                # Explicitly granted artifacts permit reading only the requested
                # view; granting a full body does not grant unrelated sections.
                granted = set(json.loads(self._task(c, caller.task_id)["input_refs_json"]))
                if ref not in granted:
                    raise DispatchError("not_found") from None
                readable_sections = {name: value for name, value in refs.items() if value in granted}
            self._inputs(c, caller, [ref])
            art = self._artifact_rows(c, caller.run_id, [ref])[0]
        content = self.artifacts.read(art).decode()
        end = min(offset + limit, len(content))
        if offset > len(content):
            raise DispatchError("invalid_arguments")
        with self.db.connection(write=True) as c:
            self._actor(c, caller)
            task = self._task(c, row["task_id"])
            identity = {
                "result_ref": result_ref,
                "reader_attempt_id": caller.attempt_id,
                "reader_thread_id": caller.thread_id,
                "reader_task_id": caller.task_id,
                "reader_kind": "user" if caller.trusted_user else "agent",
                "view": view,
                "section": section,
                "start": offset,
                "end": end,
            }
            self._event(c, task, "result.page_viewed", identity)
            intervals = [(offset, end)]
            scope = {key: identity[key] for key in (
                "result_ref", "reader_thread_id", "reader_task_id", "reader_kind", "view", "section",
            )}
            completion_type = f"result.{view}_viewed"
            completion_seen = False
            for event in c.execute(
                "SELECT e.payload_json,e.event_type,a.task_id AS legacy_task_id,"
                "t.thread_id AS legacy_thread_id FROM h2_events e "
                "LEFT JOIN h2_attempts a ON a.attempt_id=json_extract(e.payload_json,'$.reader_attempt_id') "
                "LEFT JOIN h2_tasks t ON t.task_id=a.task_id AND t.run_id=e.run_id "
                "WHERE e.run_id=? AND e.event_type IN ('result.page_viewed',?) "
                "AND json_extract(e.payload_json,'$.result_ref')=? "
                "AND json_extract(e.payload_json,'$.reader_thread_id')=?",
                (caller.run_id, completion_type, result_ref, caller.thread_id),
            ):
                p = json.loads(event["payload_json"])
                if "reader_task_id" not in p:
                    # Recover old Agent records from identity, never from a user's export.
                    if not event["legacy_task_id"] or event["legacy_thread_id"] != p.get("reader_thread_id"):
                        continue
                    p.update(reader_task_id=event["legacy_task_id"], reader_kind="agent")
                if all(p.get(key) == value for key, value in scope.items()):
                    if event["event_type"] == completion_type:
                        completion_seen = True
                    else:
                        intervals.append((p["start"], p["end"]))
            covered = 0
            for start, stop in sorted(intervals):
                if start > covered:
                    break
                covered = max(covered, stop)
            if covered >= len(content) and not completion_seen:
                self._event(c, task, completion_type, identity, key="view_" + digest(scope))
        return {
            "result_ref": result_ref,
            "result_version": row["result_version"],
            "status": row["status"],
            "metadata_status": row["metadata_status"],
            "content": content[offset:end],
            "sha256": art["sha256"],
            "byte_length": art["byte_length"],
            "has_more": end < len(content),
            "next_offset": end if end < len(content) else None,
            "evidence_refs": json.loads(row["evidence_refs_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "section_refs": readable_sections,
        }

    def reconcile(self, *, after_task_rowid=0, limit=200):
        """DB-only recovery; never infer ownership or completion from Redis PEL."""
        if (
            type(after_task_rowid) is not int
            or after_task_rowid < 0
            or type(limit) is not int
            or not 0 < limit <= 200
        ):
            raise DispatchError("invalid_arguments")
        changed = []
        with self.db.connection(write=True) as c:
            rows = c.execute(
                "SELECT rowid AS scan_rowid,* FROM h2_tasks WHERE rowid>? AND status NOT IN "
                "('succeeded','partial','failed','cancelled','timed_out') ORDER BY rowid LIMIT ?",
                (after_task_rowid, limit + 1),
            ).fetchall()
            page = rows[:limit]
            for task in page:
                run = c.execute(
                    "SELECT status FROM h2_runs WHERE run_id=?", (task["run_id"],)
                ).fetchone()
                if run["status"] in TERMINAL:
                    self._terminate(c, task, "cancelled", "run_finished")
                    changed.append(task["task_id"])
                    continue
                if task["cancel_requested_at"] is not None:
                    self._terminate(c, task, "cancelled")
                    changed.append(task["task_id"])
                    continue
                if task["deadline_at"] <= self.clock():
                    self._terminate(c, task, "timed_out", "timeout")
                    changed.append(task["task_id"])
                    continue
                a = c.execute(
                    "SELECT * FROM h2_attempts WHERE attempt_id=?", (task["current_attempt_id"],)
                ).fetchone()
                if a["status"] in {"leased", "running"} and a["lease_expires_at"] <= self.clock():
                    c.execute(
                        "UPDATE h2_attempts SET status='expired',finished_at=? WHERE attempt_id=?",
                        (self.clock(), a["attempt_id"]),
                    )
                    c.execute(
                        "UPDATE h2_model_calls SET status='unknown' WHERE attempt_id=? AND status IN ('reserved','started')",
                        (a["attempt_id"],),
                    )
                    self._event(c, task, "attempt.expired", {"attempt_id": a["attempt_id"]})
                    if self._failed_attempt_count(c, task["task_id"]) >= self.config.max_attempts:
                        self._terminate(c, task, "failed", "transport_error")
                    else:
                        self._attempt(c, task, a["checkpoint_ref"] or a["resume_checkpoint_ref"])
                    changed.append(task["task_id"])
                elif a["status"] == "queued" and task["status"] != "blocked":
                    last = c.execute(
                        "SELECT * FROM h2_outbox WHERE attempt_id=? ORDER BY dispatch_generation DESC LIMIT 1",
                        (a["attempt_id"],),
                    ).fetchone()
                    if (
                        last
                        and last["status"] == "published"
                        and last["published_at"] + self.config.republish_ms <= self.clock()
                    ):
                        self._outbox(c, task, a["attempt_id"])
                        changed.append(task["task_id"])
                elif task["status"] == "blocked":
                    # Dependency completion may unblock; dead letters require explicit redrive.
                    dead = c.execute(
                        "SELECT 1 FROM h2_outbox WHERE attempt_id=? AND status='dead_letter'",
                        (a["attempt_id"],),
                    ).fetchone()
                    deps = c.execute(
                        "SELECT t.status FROM h2_task_dependencies d JOIN h2_tasks t "
                        "ON t.task_id=d.depends_on_task_id WHERE d.task_id=?",
                        (task["task_id"],),
                    ).fetchall()
                    if deps and all(r[0] == "succeeded" for r in deps) and not dead:
                        c.execute(
                            "UPDATE h2_tasks SET status='pending_dispatch' WHERE task_id=?",
                            (task["task_id"],),
                        )
                        self._outbox(c, task, a["attempt_id"])
                        self._event(c, task, "task.dependency_ready")
            self._wake(c, owner_task_ids=[t["task_id"] for t in page], changed_task_ids=changed)
        return {
            "changed_task_ids": changed,
            "has_more": len(rows) > limit,
            "next_task_rowid": page[-1]["scan_rowid"] if len(rows) > limit else 0,
        }

    def take_outbox(self, limit=32):
        if type(limit) is not int or not 0 < limit <= 200:
            raise DispatchError("invalid_arguments")
        with self.db.connection(write=True) as c:
            now = self.clock()
            self._refresh_queue_pressure(c, now)
            rows = c.execute(
                """SELECT o.* FROM h2_outbox o JOIN h2_tasks t ON t.task_id=o.task_id
                JOIN h2_attempts a ON a.attempt_id=o.attempt_id
                WHERE ((o.status IN ('pending','retrying') AND o.next_retry_at<=?)
                    OR (o.status='publishing' AND o.publishing_expires_at<=?))
                  AND t.current_attempt_id=o.attempt_id AND a.status='queued'
                  AND t.status NOT IN ('succeeded','partial','failed','cancelled','timed_out','blocked')
                  AND t.cancel_requested_at IS NULL AND t.deadline_at>?
                ORDER BY
                  (t.priority + CAST(MAX(0, ? - t.created_at) / ? AS INTEGER) * ?) DESC,
                  t.created_at,o.outbox_id LIMIT ?""",
                (
                    now,
                    now,
                    now,
                    now,
                    self.config.aging_step_ms,
                    self.config.aging_priority_step,
                    limit,
                ),
            ).fetchall()
            result = []
            for row in rows:
                if row["status"] == "publishing":
                    count = row["retry_count"] + 1
                    dead = count >= self.config.publish_attempts
                    c.execute(
                        "UPDATE h2_outbox SET retry_count=?,last_error='publisher_expired',status=? "
                        "WHERE outbox_id=?",
                        (count, "dead_letter" if dead else "retrying", row["outbox_id"]),
                    )
                    if dead:
                        c.execute(
                            "UPDATE h2_tasks SET status='blocked' WHERE task_id=?",
                            (row["task_id"],),
                        )
                        self._event(
                            c,
                            self._task(c, row["task_id"]),
                            "dispatch_failed",
                            {"outbox_id": row["outbox_id"], "error_code": "publisher_expired"},
                        )
                        continue
                token = secrets.token_hex(24)
                c.execute(
                    "UPDATE h2_outbox SET status='publishing',publisher_token=?,publishing_expires_at=? "
                    "WHERE outbox_id=?",
                    (token, now + self.config.publisher_lease_ms, row["outbox_id"]),
                )
                result.append({**dict(row), "publisher_token": token})
            return result

    def published(self, outbox_id, token, entry_id):
        with self.db.connection(write=True) as c:
            row = c.execute("SELECT * FROM h2_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
            if (
                not row
                or row["status"] != "publishing"
                or row["publisher_token"] != token
                or row["publishing_expires_at"] <= self.clock()
            ):
                return False
            c.execute(
                "UPDATE h2_outbox SET status='published',published_at=?,redis_entry_id=? WHERE outbox_id=?",
                (self.clock(), entry_id, outbox_id),
            )
            c.execute(
                "UPDATE h2_tasks SET status='queued' WHERE task_id=? AND current_attempt_id=? "
                "AND status IN ('pending_dispatch','backpressured')",
                (row["task_id"], row["attempt_id"]),
            )
            self._event(
                c, self._task(c, row["task_id"]), "dispatch.published", {"outbox_id": outbox_id}
            )
            return True

    def publish_failed(self, outbox_id, token, error_code="redis_unavailable", *, jitter_ms=0):
        with self.db.connection(write=True) as c:
            row = c.execute("SELECT * FROM h2_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
            if (
                not row
                or row["status"] != "publishing"
                or row["publisher_token"] != token
                or row["publishing_expires_at"] <= self.clock()
            ):
                return False
            count = row["retry_count"] + 1
            dead = count >= self.config.publish_attempts
            delay = min(30000, 500 * 2 ** min(count - 1, 10)) + max(0, jitter_ms)
            c.execute(
                "UPDATE h2_outbox SET status=?,retry_count=?,next_retry_at=?,last_error=? WHERE outbox_id=?",
                (
                    "dead_letter" if dead else "retrying",
                    count,
                    self.clock() + delay,
                    "redis_unavailable" if error_code != "redis_unavailable" else error_code,
                    outbox_id,
                ),
            )
            task = self._task(c, row["task_id"])
            if (
                dead
                and task["current_attempt_id"] == row["attempt_id"]
                and task["status"] in {"pending_dispatch", "queued", "backpressured"}
            ):
                c.execute("UPDATE h2_tasks SET status='blocked' WHERE task_id=?", (row["task_id"],))
                self._event(c, task, "dispatch_failed", {"outbox_id": outbox_id})
            return True

    def redrive(self, caller: CallerContext, task_id: str, key: str):
        payload = {"task_id": task_id}
        with self.db.connection(write=True) as c:
            replay = self._replay(c, caller, "dispatch.redrive", key, payload)
            if replay:
                return replay
            self._actor(c, caller, write=True)
            task = self._access(c, caller, task_id)
            self._open(c, task)
            a = c.execute(
                "SELECT * FROM h2_attempts WHERE attempt_id=?", (task["current_attempt_id"],)
            ).fetchone()
            if not caller.trusted_user or a["status"] != "queued" or task["status"] != "blocked":
                raise DispatchError("invalid_state")
            obx = self._outbox(c, task, a["attempt_id"])
            c.execute("UPDATE h2_tasks SET status='pending_dispatch' WHERE task_id=?", (task_id,))
            self._event(c, task, "dispatch.redriven", {"outbox_id": obx})
            return self._receipt(c, caller, "dispatch.redrive", key, payload, {"outbox_id": obx})

    def delivery_settled(self, payload):
        with self.db.connection() as c:
            row = c.execute(
                "SELECT o.payload_json,t.status,t.current_attempt_id,a.status AS attempt_status "
                "FROM h2_outbox o JOIN h2_tasks t ON t.task_id=o.task_id "
                "JOIN h2_attempts a ON a.attempt_id=o.attempt_id WHERE o.outbox_id=?",
                (payload.get("outbox_id"),),
            ).fetchone()
            if not row or json.loads(row["payload_json"]) != payload:
                raise DispatchError("contract_error")
            return (
                row["status"] in TERMINAL
                or row["current_attempt_id"] != payload["attempt_id"]
                or row["attempt_status"] == "suspended"
            )

    @staticmethod
    def _percentile(values, fraction):
        ordered = sorted(int(value) for value in values if value is not None)
        if not ordered:
            return None
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
        return ordered[index]

    @staticmethod
    def _scheduling_trace(run_id, tasks, attempts, calls, events):
        task_by_id = {task["task_id"]: task for task in tasks}
        task_paths = {}

        def path_for(task_id):
            if task_id not in task_paths:
                chain = []
                current = task_by_id.get(task_id)
                while current is not None:
                    chain.append(current["role"])
                    current = task_by_id.get(current.get("parent_task_id"))
                task_paths[task_id] = list(reversed(chain))
            return task_paths[task_id]

        spans = []
        for task in tasks:
            started = min(
                (attempt.get("started_at") for attempt in attempts
                 if attempt["task_id"] == task["task_id"] and attempt.get("started_at") is not None),
                default=task.get("created_at"),
            )
            spans.append({
                "trace_id": run_id,
                "span_id": task["task_id"],
                "parent_span_id": task.get("parent_task_id"),
                "span_type": "agent",
                "task_id": task["task_id"],
                "thread_id": task["thread_id"],
                "role": task["role"],
                "role_version": task["role_version"],
                "depth": task["depth"],
                "status": task["status"],
                "started_at": started,
                "finished_at": task.get("finished_at"),
                "agent_path": path_for(task["task_id"]),
            })
        for attempt in attempts:
            spans.append({
                "trace_id": run_id,
                "span_id": attempt["attempt_id"],
                "parent_span_id": attempt["task_id"],
                "span_type": "attempt",
                "task_id": attempt["task_id"],
                "attempt_id": attempt["attempt_id"],
                "attempt_no": attempt["attempt_no"],
                "status": attempt["status"],
                "error_code": attempt.get("error_code"),
                "started_at": attempt.get("started_at"),
                "finished_at": attempt.get("finished_at"),
                "agent_path": path_for(attempt["task_id"]),
            })
        for call in calls:
            task_id = call["task_id"]
            duration = None
            if call.get("started_at") is not None and call.get("finished_at") is not None:
                duration = max(0, call["finished_at"] - call["started_at"])
            spans.append({
                "trace_id": run_id,
                "span_id": call["call_id"],
                "call_id": call["call_id"],
                "parent_span_id": call["attempt_id"],
                "span_type": "model_call",
                "task_id": task_id,
                "attempt_id": call["attempt_id"],
                "attempt_no": call.get("attempt_no"),
                "model_profile": call.get("model_profile"),
                "status": call["status"],
                "error_code": call.get("error_code"),
                "usage": call.get("usage", {}),
                "started_at": call.get("started_at"),
                "finished_at": call.get("finished_at"),
                "duration_ms": duration,
                "agent_path": path_for(task_id),
            })

        known_tasks = set(task_by_id)
        trace_events = []
        for event in events:
            task_id = event.get("task_id") or event.get("aggregate_id")
            task = task_by_id.get(task_id) if task_id in known_tasks else None
            trace_events.append({
                **event,
                "trace_id": run_id,
                "task_id": task_id if task else None,
                "span_id": task_id if task else None,
                "parent_span_id": task.get("parent_task_id") if task else None,
                "role": task.get("role") if task else None,
                "depth": task.get("depth") if task else None,
                "agent_path": path_for(task_id) if task else [],
            })
        return {"trace_id": run_id, "spans": spans, "events": trace_events}

    def scheduling_trace(self, caller: CallerContext, task_ids: list[str], events=None):
        if not task_ids or len(task_ids) > 200 or len(set(task_ids)) != len(task_ids):
            raise DispatchError("invalid_arguments")
        with self.db.connection() as c:
            self._actor(c, caller)
            for task_id in task_ids:
                self._access(c, caller, task_id)
            marks = ",".join("?" for _ in task_ids)
            tasks = [dict(row) for row in c.execute(
                f"SELECT t.task_id,t.parent_task_id,t.thread_id,t.status,t.created_at,t.finished_at,"
                f"th.role,th.role_version,th.depth FROM h2_tasks t "
                f"JOIN h2_threads th ON th.thread_id=t.thread_id "
                f"WHERE t.run_id=? AND t.task_id IN ({marks}) ORDER BY th.depth,t.created_at,t.rowid",
                (caller.run_id, *task_ids),
            )]
            attempts = [dict(row) for row in c.execute(
                f"SELECT attempt_id,task_id,attempt_no,status,error_code,started_at,finished_at "
                f"FROM h2_attempts WHERE run_id=? AND task_id IN ({marks}) ORDER BY created_at,rowid",
                (caller.run_id, *task_ids),
            )]
            calls = [dict(row) for row in c.execute(
                f"SELECT m.call_id,m.task_id,m.attempt_id,a.attempt_no,m.status,m.error_code,"
                f"m.usage_json,m.started_at,m.finished_at,s.payload_json AS model_payload "
                f"FROM h2_model_calls m JOIN h2_attempts a ON a.attempt_id=m.attempt_id "
                f"LEFT JOIN h2_snapshots s ON s.snapshot_id=m.model_snapshot_ref "
                f"WHERE m.run_id=? AND m.task_id IN ({marks}) ORDER BY m.rowid",
                (caller.run_id, *task_ids),
            )]
        for call in calls:
            call["usage"] = json.loads(call.pop("usage_json"))
            model = json.loads(call.pop("model_payload") or "{}")
            call["model_profile"] = model.get("profile")
        selected_events = [
            event for event in (events or [])
            if (event.get("task_id") or event.get("aggregate_id")) in set(task_ids)
        ]
        return self._scheduling_trace(caller.run_id, tasks, attempts, calls, selected_events)

    def export_run_records(self, run_id: str):
        """Export reconstructible records without making the filesystem a second authority."""

        if not SAFE_ID.fullmatch(run_id):
            raise DispatchError("invalid_arguments")
        with self.db.connection() as c:
            run = c.execute("SELECT * FROM h2_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise DispatchError("not_found")
            query = c.execute(
                "SELECT * FROM h2_artifacts WHERE run_id=? AND artifact_ref=?",
                (run_id, run["query_ref"]),
            ).fetchone()
            events = [dict(row) for row in c.execute(
                "SELECT * FROM h2_events WHERE run_id=? ORDER BY event_id", (run_id,)
            )]
            tasks = [dict(row) for row in c.execute(
                "SELECT t.task_id,t.parent_task_id,t.thread_id,t.status,t.created_at,t.finished_at,"
                "th.role,th.role_version,th.depth FROM h2_tasks t "
                "JOIN h2_threads th ON th.thread_id=t.thread_id WHERE t.run_id=? "
                "ORDER BY th.depth,t.created_at,t.rowid", (run_id,)
            )]
            attempts = [dict(row) for row in c.execute(
                "SELECT attempt_id,task_id,attempt_no,status,error_code,started_at,finished_at "
                "FROM h2_attempts WHERE run_id=? ORDER BY created_at,rowid", (run_id,)
            )]
            calls = [dict(row) for row in c.execute(
                "SELECT m.*,a.attempt_no,s.payload_json AS model_payload "
                "FROM h2_model_calls m JOIN h2_attempts a ON a.attempt_id=m.attempt_id "
                "LEFT JOIN h2_snapshots s ON s.snapshot_id=m.model_snapshot_ref "
                "WHERE m.run_id=? ORDER BY m.rowid", (run_id,)
            )]
        if query is None:
            raise DispatchError("artifact_unavailable")
        try:
            query_text = self.artifacts.read(query).decode("utf-8")
        except (UnicodeDecodeError, DispatchError) as exc:
            raise DispatchError("artifact_unavailable") from exc
        event_records = []
        for event in events:
            payload = json.loads(event.pop("payload_json"))
            event_records.append({**event, "payload": payload})
        call_records = []
        for call in calls:
            call["usage"] = json.loads(call.pop("usage_json"))
            model = json.loads(call.pop("model_payload") or "{}")
            call["model_profile"] = model.get("profile")
            call_records.append(call)
        scheduling_trace = self._scheduling_trace(
            run_id, tasks, attempts, call_records, event_records,
        )
        event_records = scheduling_trace["events"]
        spans = {span["span_id"]: span for span in scheduling_trace["spans"]}
        call_records = [
            {**call, **spans[call["call_id"]]} for call in call_records
        ]
        trace = [
            {
                "record_type": "model_call" if span["span_type"] == "model_call"
                else f"{span['span_type']}_span",
                "created_at": span.get("started_at") or span.get("finished_at"),
                **span,
            }
            for span in scheduling_trace["spans"]
        ] + [
            {"record_type": "event", "created_at": row.get("created_at"), **row}
            for row in event_records
        ]
        trace.sort(key=lambda row: (row.get("created_at") or 0, row["record_type"]))
        paths = {
            "query": self.records.write_json(run_id, "query.json", {
                "run_id": run_id,
                "session_ref": run["session_ref"],
                "query": query_text,
                "created_at": run["created_at"],
            }),
            "events": self.records.write_jsonl(run_id, "events.jsonl", event_records),
            "model_calls": self.records.write_jsonl(run_id, "model-calls.jsonl", call_records),
            "trace": self.records.write_jsonl(run_id, "trace.jsonl", trace),
            "scheduling_trace": self.records.write_json(run_id, "scheduling-trace.json", scheduling_trace),
        }
        return {
            "run_id": run_id,
            "files": {key: str(path) for key, path in paths.items()},
            "event_count": len(event_records),
            "model_call_count": len(call_records),
        }

    def metrics_snapshot(self, run_id: str | None = None):
        """Return bounded scheduler metrics; no article bodies or model prompts are included."""

        where = "WHERE run_id=?" if run_id else ""
        args = (run_id,) if run_id else ()
        with self.db.connection() as c:
            task_rows = c.execute(
                f"SELECT status,created_at,updated_at,finished_at FROM h2_tasks {where}", args
            ).fetchall()
            attempt_rows = c.execute(
                f"SELECT status,started_at,finished_at FROM h2_attempts {where}", args
            ).fetchall()
            call_rows = c.execute(
                f"SELECT status,started_at,finished_at FROM h2_model_calls {where}", args
            ).fetchall()
            outbox_where = "WHERE o.run_id=?" if run_id else ""
            outbox_rows = c.execute(
                f"SELECT o.status,o.retry_count,o.created_at FROM h2_outbox o {outbox_where}", args
            ).fetchall()
            now = self.clock()
        task_durations = [
            row["finished_at"] - row["created_at"]
            for row in task_rows
            if row["finished_at"] is not None
        ]
        attempt_durations = [
            row["finished_at"] - row["started_at"]
            for row in attempt_rows
            if row["finished_at"] is not None and row["started_at"] is not None
        ]
        call_durations = [
            row["finished_at"] - row["started_at"]
            for row in call_rows
            if row["finished_at"] is not None and row["started_at"] is not None
        ]
        queue_age = [now - row["created_at"] for row in outbox_rows if row["status"] in {
            "pending", "publishing", "retrying"
        }]
        return {
            "run_id": run_id,
            "tasks": {status: sum(row["status"] == status for row in task_rows) for status in (
                "pending_dispatch", "queued", "running", "waiting", "backpressured", "blocked",
                "succeeded", "partial", "failed", "cancelled", "timed_out",
            )},
            "attempts": {status: sum(row["status"] == status for row in attempt_rows) for status in (
                "queued", "leased", "running", "suspended", "succeeded", "failed", "expired", "cancelled",
            )},
            "model_calls": {status: sum(row["status"] == status for row in call_rows) for status in (
                "reserved", "started", "succeeded", "failed", "unknown",
            )},
            "outbox": {
                "pending": sum(row["status"] == "pending" for row in outbox_rows),
                "retrying": sum(row["status"] == "retrying" for row in outbox_rows),
                "dead_letter": sum(row["status"] == "dead_letter" for row in outbox_rows),
                "oldest_wait_ms": max(queue_age, default=0),
                "high_watermark": self.config.outbox_high_watermark,
                "low_watermark": self.config.outbox_low_watermark,
                "over_high_watermark": len(queue_age) >= self.config.outbox_high_watermark,
                "pending_outbox": len(queue_age),
            },
            "latency_ms": {
                "task": {"count": len(task_durations), "p50": self._percentile(task_durations, .50), "p95": self._percentile(task_durations, .95)},
                "attempt": {"count": len(attempt_durations), "p50": self._percentile(attempt_durations, .50), "p95": self._percentile(attempt_durations, .95)},
                "model_call": {"count": len(call_durations), "p50": self._percentile(call_durations, .50), "p95": self._percentile(call_durations, .95)},
            },
        }

    def gc_orphan_artifacts(self, *, min_age_ms: int = 3600000, dry_run: bool = True):
        """Scan only unregistered files; never delete a DB-referenced artifact."""

        with self.db.connection() as c:
            known = {row[0] for row in c.execute("SELECT storage_uri FROM h2_artifacts")}
        paths = self.records.scan_orphans(known_uris=known, now=self.clock(), min_age_ms=min_age_ms)
        removed = [] if dry_run else self.records.remove_orphans(paths)
        return {
            "dry_run": dry_run,
            "candidates": [str(path) for path in paths],
            "removed": removed,
        }
