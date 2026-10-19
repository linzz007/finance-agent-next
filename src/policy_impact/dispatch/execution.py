"""Lease-bound capabilities for trusted executors; never serialized into prompts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Annotated
from uuid import uuid4

from pydantic import Field, ValidationError, model_validator

from .contracts import (
    Cancel,
    CheckpointState,
    Command,
    DispatchError,
    Identifier,
    Resume,
    Send,
    Spawn,
    Wait,
)
from .model_execution import GatewayModelAdapter, ModelRequest
from .worker import Suspend


class Status(Command):
    task_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    thread_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    after_event_id: str = Field(default="0", pattern=r"^(0|[1-9][0-9]*)$")
    max_events: int = Field(default=100, gt=0, le=200)

    @model_validator(mode="after")
    def valid_targets(self):
        if not self.task_ids and not self.thread_ids:
            raise ValueError("at least one task_id or thread_id is required")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("duplicate task targets")
        if len(set(self.thread_ids)) != len(self.thread_ids):
            raise ValueError("duplicate thread targets")
        return self


class ResultGet(Command):
    result_ref: Identifier
    view: Annotated[str, Field(pattern=r"^(summary|full|section)$")] = "summary"
    section: Identifier | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16000, gt=0, le=64000)


TOOL_CONTRACTS = {
    "agent.spawn": Spawn,
    "agent.status": Status,
    "agent.send": Send,
    "agent.cancel": Cancel,
    "agent.resume": Resume,
    "agent.wait": Wait,
    "result.get": ResultGet,
}


class ExecutionCapabilities:
    """Trusted handler API. The Worker alone supplies execution identity/lease.

    Business context serialization, stages, action keys and final financial review
    remain the executor's responsibility; this object does not replay tools.
    """

    def __init__(self, repository, lease, work, *, model_adapter=None, attempt_deadline_at=None):
        self._repository, self._lease, self._work = repository, lease, work
        self._adapter = model_adapter
        self._attempt_deadline_at = attempt_deadline_at
        self._deadline_at = work.brief["deadline_at"]
        self._model_snapshot = json.loads(json.dumps(work.model, allow_nan=False))
        self._tools = frozenset(work.brief["allowed_tools"])
        self._closed = False
        self._call_lock = asyncio.Lock()
        self._inflight = set()
        self._call_tasks = set()

    async def validate(self):
        if self._closed:
            raise DispatchError("stale_lease")
        await asyncio.to_thread(self._repository.validate_lease, self._lease)

    async def describe(self, name):
        await self.validate()
        if name not in self._tools or name not in TOOL_CONTRACTS:
            raise DispatchError("forbidden")
        return {"name": name, "input_schema": TOOL_CONTRACTS[name].model_json_schema()}

    async def tool(self, name, arguments):
        await self.validate()
        if name not in self._tools or name not in TOOL_CONTRACTS:
            raise DispatchError("forbidden")
        if name == "agent.wait":
            raise DispatchError("invalid_arguments", "durable wait requires a checkpoint")
        try:
            request = TOOL_CONTRACTS[name].model_validate(arguments)
        except ValidationError:
            raise DispatchError(
                "invalid_arguments", "tool arguments do not match the contract"
            ) from None
        if name == "agent.resume" and request.model_profile is not None:
            raise DispatchError("forbidden", "only the user may switch a failed task's model")
        caller = self._lease.caller()
        if name == "agent.status":
            return await asyncio.to_thread(self._repository.status, caller, **request.model_dump())
        if name == "result.get":
            return await asyncio.to_thread(
                self._repository.result_get, caller, **request.model_dump()
            )
        method = getattr(self._repository, name.split(".")[1])
        return await asyncio.to_thread(method, caller, request)

    async def store_context(self, content):
        await self.validate()
        self._repository._no_secrets(content)
        try:
            text = json.dumps(content, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise DispatchError("contract_error", "context must be public JSON") from None
        return await asyncio.to_thread(
            self._repository.store_text, self._lease.caller(), "context", text
        )

    async def load_context(self, artifact_ref):
        await self.validate()
        return await asyncio.to_thread(
            self._repository.read_input, self._lease.caller(), artifact_ref
        )

    async def checkpoint(self, state: CheckpointState):
        await self.validate()
        return await asyncio.to_thread(self._repository.save_checkpoint, self._lease, state)

    async def mailbox(self):
        await self.validate()
        return await asyncio.to_thread(self._repository.mailbox, self._lease)

    async def wait(self, arguments, checkpoint: CheckpointState):
        await self.validate()
        if "agent.wait" not in self._tools:
            raise DispatchError("forbidden")
        try:
            request = Wait.model_validate(arguments)
        except ValidationError:
            raise DispatchError("invalid_arguments") from None
        if not isinstance(checkpoint, CheckpointState):
            raise DispatchError("checkpoint_invalid")
        return Suspend(checkpoint, request)

    async def complete(self, messages, schema_name, *, final=False):
        """Single physical model call; no hidden format/transport retries."""
        from policy_impact.harness.model_gateway import ModelResponse

        try:
            public_messages = json.loads(json.dumps(messages, allow_nan=False))
        except (TypeError, ValueError):
            raise DispatchError("contract_error", "messages must be JSON") from None
        task = asyncio.current_task()
        self._call_tasks.add(task)
        try:
            async with self._call_lock:
                await self.validate()
                adapter = self._adapter
                if adapter is None:
                    self._adapter = adapter = GatewayModelAdapter()
                settings = adapter.settings(self._model_snapshot)
                deadline = self._deadline_at
                if self._attempt_deadline_at is not None:
                    deadline = min(deadline, self._attempt_deadline_at)
                remaining = (deadline - self._repository.clock()) / 1000
                if remaining <= 0:
                    raise DispatchError("timeout")
                call_id = "call_" + uuid4().hex
                request = ModelRequest(
                    call_id, self._model_snapshot, public_messages, schema_name, settings, remaining
                )
                stream = adapter.request(request)
                reserved = started = settled = False
                output_ref, payload, seq, usage = None, None, 0, {}
                self._inflight.add(call_id)
                try:
                    async for event in stream:
                        if event.call_id != call_id or event.seq <= seq:
                            raise DispatchError("contract_error", "invalid model event sequence")
                        seq = event.seq
                        if event.kind == "started":
                            if reserved:
                                raise DispatchError("contract_error", "duplicate model start")
                            await asyncio.to_thread(
                                self._repository.reserve_call, self._lease, call_id, final=final
                            )
                            reserved = True
                            await self.validate()
                            await asyncio.to_thread(
                                self._repository.call_started, self._lease, call_id
                            )
                            started = True
                        elif not started:
                            raise DispatchError("contract_error", "event before request start")
                        elif event.kind == "text_delta":
                            await self.validate()
                            output_ref = await asyncio.to_thread(
                                self._repository.store_text,
                                self._lease.caller(),
                                "model_output",
                                event.data["text"],
                            )
                        elif event.kind == "usage":
                            usage = event.data["usage"]
                        elif event.kind == "action_candidate":
                            payload = event.data["payload"]
                        elif event.kind in {"completed", "failed"}:
                            error_code = event.data.get("code") if event.kind == "failed" else None
                            await asyncio.to_thread(
                                self._repository.call_finished,
                                self._lease,
                                call_id,
                                status=event.data.get("physical_status", "succeeded"),
                                usage=event.data.get("usage", usage),
                                error_code=error_code,
                                output_ref=output_ref,
                                provider_request_id=event.data.get("provider_request_id", ""),
                            )
                            settled = True
                            if error_code:
                                raise DispatchError(
                                    error_code, retryable=event.data.get("retryable", False)
                                )
                            if payload is None or output_ref is None:
                                raise DispatchError(
                                    "contract_error", "completion without public output"
                                )
                            await self.validate()
                            return ModelResponse(
                                response_id=event.data.get("response_id") or call_id,
                                payload=payload,
                                metadata={
                                    "adapter": "h2.single_request.v1",
                                    "call_id": call_id,
                                    "model_id": self._model_snapshot["model_id"],
                                    "model_profile": self._model_snapshot["profile"],
                                    "output_ref": output_ref,
                                    "usage": event.data.get("usage", usage),
                                    "attempts": 1,
                                },
                            )
                    raise DispatchError("contract_error", "model event stream has no terminal")
                except (sqlite3.Error, OSError):
                    # Persistence failure is not an ordinary model error; Worker must not ACK.
                    raise
                except BaseException:
                    if reserved and not settled:
                        if started:
                            await asyncio.to_thread(
                                self._repository.call_finished,
                                self._lease,
                                call_id,
                                status="unknown",
                            )
                        else:
                            try:
                                await asyncio.to_thread(
                                    self._repository.refund_unsent_call, self._lease, call_id
                                )
                            except DispatchError as exc:
                                if exc.code not in {"stale_lease", "cancelled"}:
                                    raise
                    raise
                finally:
                    self._inflight.discard(call_id)
                    await stream.aclose()
        finally:
            self._call_tasks.discard(task)

    async def close(self):
        self._closed = True
        tasks = list(self._call_tasks)
        for task in tasks:
            task.cancel()
        if self._adapter:
            for call_id in list(self._inflight):
                await self._adapter.cancel(call_id)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, (sqlite3.Error, OSError)):
                    raise result


def bind_executor(repository, executor, *, model_adapter=None):
    """Used by Worker with its claimed lease, never supplied by model JSON."""

    async def execute(work, lease):
        capability = ExecutionCapabilities(repository, lease, work, model_adapter=model_adapter)
        try:
            return await executor(work, capability)
        finally:
            await capability.close()

    return execute
