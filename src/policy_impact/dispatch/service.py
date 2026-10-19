"""Explicit, bounded Dispatcher/Worker service loop for Harness 2.0."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .observability import RuntimeRecords


@dataclass(frozen=True)
class DispatchCycle:
    published: int
    processed: int
    recovered: int
    missing_entries: int
    reconcile: dict
    clock_warning: bool


class ClockMonitor:
    """Detect suspicious wall-clock jumps without changing scheduler state."""

    def __init__(self, records: RuntimeRecords, *, max_forward_jump_ms: int = 300000):
        if type(max_forward_jump_ms) is not int or max_forward_jump_ms <= 0:
            raise ValueError("max_forward_jump_ms must be positive")
        self.records = records
        self.max_forward_jump_ms = max_forward_jump_ms
        self._last: int | None = None

    def observe(self, current: int) -> bool:
        previous = self._last
        self._last = current
        if previous is None or previous <= current <= previous + self.max_forward_jump_ms:
            return False
        self.records.quarantine(
            entry_id="clock",
            raw={"previous_ms": previous, "current_ms": current},
            error_code="clock_jump",
            error_message="scheduler clock moved backwards or jumped forward",
        )
        return True


class DispatchService:
    """Compose the durable single-step APIs into an explicit run loop.

    Construction has no side effects. Callers choose when to run one cycle or
    start the loop, which keeps tests and deployment lifecycle separate.
    """

    def __init__(self, repository, transport, worker, *, max_forward_jump_ms=300000):
        self.repository = repository
        self.transport = transport
        self.worker = worker
        from .transport import Dispatcher

        self.dispatcher = Dispatcher(repository, transport)
        self.clock = ClockMonitor(repository.records, max_forward_jump_ms=max_forward_jump_ms)
        self.recovery_cursor = "0-0"

    async def run_once(
        self,
        *,
        publish_limit: int = 32,
        block_ms: int = 0,
        recover_idle_ms: int = 60000,
    ) -> DispatchCycle:
        if type(publish_limit) is not int or not 0 < publish_limit <= 200:
            raise ValueError("publish_limit must be between 1 and 200")
        if type(block_ms) is not int or not 0 <= block_ms <= 5000:
            raise ValueError("block_ms must be between 0 and 5000")
        if type(recover_idle_ms) is not int or recover_idle_ms < 0:
            raise ValueError("recover_idle_ms must not be negative")
        warning = self.clock.observe(self.repository.clock())
        published = self.dispatcher.publish_batch(limit=publish_limit)
        processed = await self.worker.run_once(block_ms=block_ms)
        recovered = await self.worker.recover_once(
            cursor=self.recovery_cursor, idle_ms=recover_idle_ms
        )
        self.recovery_cursor = recovered["cursor"]
        return DispatchCycle(
            published=published,
            processed=processed,
            recovered=recovered["recovered"],
            missing_entries=len(recovered["missing_entries"]),
            reconcile=recovered["reconcile"],
            clock_warning=warning,
        )

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_ms: int = 250,
        publish_limit: int = 32,
        recover_idle_ms: int = 60000,
    ) -> None:
        if type(interval_ms) is not int or interval_ms < 0:
            raise ValueError("interval_ms must not be negative")
        while not stop_event.is_set():
            await self.run_once(
                publish_limit=publish_limit,
                block_ms=0,
                recover_idle_ms=recover_idle_ms,
            )
            if interval_ms:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_ms / 1000)
                except asyncio.TimeoutError:
                    pass


__all__ = ["ClockMonitor", "DispatchCycle", "DispatchService"]
