"""Execution shell with durable handoff. Handlers do not receive SQL or Redis."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from .contracts import CheckpointState, DispatchError, Submission, Wait
from .transport import validate_message


@dataclass(frozen=True)
class Suspend:
    checkpoint: CheckpointState
    wait: Wait | None = None


@dataclass(frozen=True)
class Continue:
    checkpoint: CheckpointState


@dataclass(frozen=True)
class WorkInput:
    run_id: str
    task_id: str
    thread_id: str
    attempt_id: str
    brief: dict
    checkpoint: dict | None
    model: dict
    messages: list[dict]
    observations: list[dict]


class Worker:
    """Handler invocation is async; no paid adapter is installed implicitly."""

    def __init__(
        self,
        repository,
        transport,
        worker_id: str,
        handler: Callable[[WorkInput], Awaitable[Submission | Suspend | Continue]] | None = None,
        *,
        executor=None,
        model_adapter=None,
        metadata_repair=None,
        context_accepts=None,
    ):
        self.repository, self.transport, self.worker_id = repository, transport, worker_id
        self.consumer = f"{worker_id}:{uuid4().hex}"
        if (handler is None) == (executor is None):
            raise ValueError("select exactly one handler or executor")
        if model_adapter is not None and executor is None:
            raise ValueError("model_adapter requires a bound executor")
        self.handler = handler
        self.executor = None
        if executor is not None:
            from .execution import bind_executor

            self.executor = bind_executor(repository, executor, model_adapter=model_adapter)
        self.metadata_repair = metadata_repair
        self.context_accepts = context_accepts
        self._reconcile_cursor = 0

    async def _pulse(self, lease, execution):
        try:
            while True:
                await asyncio.sleep(self.repository.config.lease_ms / 3000)
                await asyncio.to_thread(self.repository.heartbeat, lease)
        except asyncio.CancelledError:
            raise
        except (DispatchError, sqlite3.Error, OSError) as exc:
            # Loss of execution authority must stop this handler, not just its heartbeat.
            execution.cancel()
            return exc

    async def _fail_execution(self, lease, exc):
        code = exc.code if isinstance(exc, DispatchError) else "internal_error"
        if code == "cancelled":
            try:
                await asyncio.to_thread(self.repository.settle_cancelled, lease)
            except DispatchError as lost:
                if lost.code != "stale_lease":
                    raise
            return
        if code == "stale_lease":
            return
        allowed = {
            "transport_error",
            "provider_error",
            "contract_error",
            "quota_error",
            "timeout",
            "outcome_unknown",
            "checkpoint_invalid",
            "artifact_unavailable",
            "budget_denied",
            "forbidden",
        }
        try:
            await asyncio.to_thread(
                self.repository.fail,
                lease,
                code if code in allowed else "internal_error",
                retryable=isinstance(exc, DispatchError) and exc.retryable,
                details=exc.details if isinstance(exc, DispatchError) and code in allowed else None,
            )
        except DispatchError as lost:
            if lost.code not in {"stale_lease", "cancelled"}:
                raise

    async def _save_submission(self, lease, work, outcome):
        saved = await asyncio.to_thread(self.repository.submit, lease, outcome)
        if saved["metadata_status"] != "pending":
            return
        repaired, reason = None, "incomplete_metadata"
        if self.metadata_repair:
            try:
                if inspect.iscoroutinefunction(self.metadata_repair):
                    repaired = self.metadata_repair(work, saved, outcome)
                else:
                    repaired = await asyncio.to_thread(self.metadata_repair, work, saved, outcome)
                if inspect.isawaitable(repaired):
                    repaired = await repaired
            except (sqlite3.Error, OSError):
                raise
            except Exception:  # noqa: BLE001 - extension failure must retain the saved body
                reason = "metadata_repair_failed"
        if isinstance(repaired, Submission):
            if repaired.content != outcome.content or repaired.full_ref != outcome.full_ref:
                reason = "metadata_body_changed"
            else:
                repaired = repaired.model_copy(
                    update={
                        "result_key": "metadata:" + saved["result_ref"],
                        "content": None,
                        "full_ref": saved["full_ref"],
                    }
                )
                try:
                    result = await asyncio.to_thread(self.repository.submit, lease, repaired)
                except DispatchError as exc:
                    if exc.code in {"stale_lease", "cancelled", "artifact_unavailable"}:
                        raise
                    reason = "metadata_repair_rejected"
                else:
                    if result["metadata_status"] != "pending":
                        return
                    saved = result
        await asyncio.to_thread(
            self.repository.finalize_incomplete, lease, saved["result_ref"], reason=reason
        )

    async def _ack_if_settled(self, payload, entry_id):
        if await asyncio.to_thread(self.repository.delivery_settled, payload):
            await asyncio.to_thread(self.transport.ack, entry_id)

    async def process(self, entry_id, raw):
        try:
            payload = validate_message(raw)
            lease, ack_safe = await asyncio.to_thread(self.repository.claim, payload, self.worker_id)
        except DispatchError as exc:
            if exc.code != "contract_error":
                raise
            # Wire and persisted-Outbox mismatches share the durable rejection
            # boundary. Infrastructure failures must still leave the entry pending.
            await asyncio.to_thread(
                self.repository.records.quarantine,
                entry_id=str(entry_id),
                raw=raw,
                error_code=exc.code,
                error_message=str(exc),
            )
            await asyncio.to_thread(self.transport.ack, entry_id)
            return False
        if lease is None:
            if ack_safe:
                await asyncio.to_thread(self.transport.ack, entry_id)
            return False
        try:
            await asyncio.to_thread(self.repository.start, lease)
            data = await asyncio.to_thread(
                self.repository.execution_input, lease, context_accepts=self.context_accepts
            )
        except DispatchError as exc:
            if exc.code in {"artifact_unavailable", "checkpoint_invalid", "cancelled"}:
                await self._fail_execution(lease, exc)
            elif exc.code not in {"stale_lease", "cancelled"}:
                raise
            await self._ack_if_settled(payload, entry_id)
            return False
        work = WorkInput(lease.run_id, lease.task_id, lease.thread_id, lease.attempt_id, **data)
        execution = asyncio.create_task(
            self.executor(work, lease) if self.executor is not None else self.handler(work)
        )
        pulse = asyncio.create_task(self._pulse(lease, execution))
        try:
            try:
                outcome = await execution
            except asyncio.CancelledError:
                if pulse.done() and not pulse.cancelled():
                    failure = pulse.result()
                    if failure:
                        if isinstance(failure, DispatchError):
                            await self._fail_execution(lease, failure)
                            await self._ack_if_settled(payload, entry_id)
                            return False
                        raise failure
                raise
            except (sqlite3.Error, OSError):
                raise
            except Exception as exc:  # noqa: BLE001 - only the injected handler is untrusted
                await self._fail_execution(lease, exc)
                await self._ack_if_settled(payload, entry_id)
                return True
            try:
                await asyncio.to_thread(self.repository.validate_lease, lease)
                if isinstance(outcome, Suspend):
                    await asyncio.to_thread(
                        self.repository.suspend, lease, outcome.checkpoint, outcome.wait
                    )
                elif isinstance(outcome, Continue):
                    await asyncio.to_thread(
                        self.repository.continue_task, lease, outcome.checkpoint
                    )
                elif isinstance(outcome, Submission):
                    await self._save_submission(lease, work, outcome)
                else:
                    await self._fail_execution(lease, DispatchError("contract_error"))
            except DispatchError as exc:
                if exc.code in {"cancelled", "stale_lease"}:
                    if exc.code == "cancelled":
                        await self._fail_execution(lease, exc)
                    await self._ack_if_settled(payload, entry_id)
                    return False
                raise
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        await self._ack_if_settled(payload, entry_id)
        return True

    async def run_once(self, *, block_ms=1000):
        entries = await asyncio.to_thread(
            self.transport.receive, self.consumer, count=1, block_ms=block_ms
        )
        for entry, payload in entries:
            await self.process(entry, payload)
        return len(entries)

    async def recover_once(self, *, cursor="0-0", idle_ms=60000):
        scan = await asyncio.to_thread(
            self.repository.reconcile, after_task_rowid=self._reconcile_cursor
        )
        self._reconcile_cursor = scan["next_task_rowid"]
        next_cursor, entries, deleted = await asyncio.to_thread(
            self.transport.recover, self.consumer, cursor=cursor, idle_ms=idle_ms
        )
        for entry, payload in entries:
            await self.process(entry, payload)
        return {
            "cursor": next_cursor,
            "recovered": len(entries),
            "missing_entries": deleted,
            "reconcile": scan,
        }
