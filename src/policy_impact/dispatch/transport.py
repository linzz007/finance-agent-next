"""Redis carries notifications, never business data or execution authority."""

from __future__ import annotations

import json
import random
import time

from .contracts import DispatchError

WIRE_FIELDS = frozenset(
    {
        "schema_version",
        "outbox_id",
        "run_id",
        "task_id",
        "attempt_id",
        "attempt_key",
        "dispatch_key",
        "dispatch_generation",
        "emitted_at",
    }
)


def validate_message(fields: dict) -> dict[str, str]:
    if (
        set(fields) != WIRE_FIELDS
        or any(not isinstance(v, str) or not v for v in fields.values())
        or fields["schema_version"] != "h2.dispatch.v1"
        or not fields["dispatch_generation"].isdigit()
        or int(fields["dispatch_generation"]) < 1
        or not fields["emitted_at"].isdigit()
        or len(json.dumps(fields, ensure_ascii=False).encode()) > 4096
    ):
        raise DispatchError("contract_error", "invalid dispatch wire message")
    return dict(fields)


class RedisStreams:
    def __init__(self, client, stream: str, *, group="h2-workers-v1"):
        self.client, self.stream, self.group = client, stream, group

    @classmethod
    def from_url(cls, url: str, stream: str):
        from redis import Redis

        return cls(
            Redis.from_url(url, decode_responses=True, socket_connect_timeout=5, socket_timeout=10),
            stream,
        )

    def ensure_group(self):
        from redis.exceptions import ResponseError

        try:
            self.client.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise

    def publish(self, fields):
        return self.client.xadd(self.stream, validate_message(fields))

    def receive(self, consumer: str, *, count=1, block_ms=1000):
        if not 1 <= count <= 32 or not 0 <= block_ms <= 5000:
            raise ValueError("invalid receive bounds")
        self.ensure_group()
        options = {"count": count}
        if block_ms:
            options["block"] = block_ms
        batches = self.client.xreadgroup(self.group, consumer, {self.stream: ">"}, **options)
        return [(eid, fields) for _, entries in batches for eid, fields in entries]

    def recover(self, consumer: str, *, idle_ms=60000, cursor="0-0", count=32):
        self.ensure_group()
        response = self.client.xautoclaim(
            self.stream, self.group, consumer, min_idle_time=idle_ms, start_id=cursor, count=count
        )
        return response[0], response[1], response[2] if len(response) > 2 else []

    def ack(self, entry_id):
        return self.client.xack(self.stream, self.group, entry_id)

    @staticmethod
    def _entry_time(entry_id):
        try:
            return int(str(entry_id).split("-", 1)[0])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _field_text(value):
        return value.decode() if isinstance(value, bytes) else value

    def _pending_ids(self, *, limit=100000):
        """Enumerate PEL IDs; failure means cleanup must conservatively retain all."""

        try:
            rows = self.client.xpending_range(
                self.stream, self.group, min="-", max="+", count=limit
            )
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        if len(rows) >= limit:
            # The range may be truncated. Without a complete PEL snapshot,
            # deleting any old entry could remove an unlisted pending item.
            return None
        result = set()
        for row in rows:
            if isinstance(row, dict):
                value = row.get("message_id", row.get("message-id"))
            else:
                value = row[0] if row else None
            if value is not None:
                result.add(self._field_text(value))
        return result

    def trim_safe(
        self,
        repository,
        *,
        min_age_ms: int,
        max_entries: int = 500,
        dry_run: bool = True,
        now_ms: int | None = None,
    ):
        """Delete only old, settled, non-pending entries after explicit checks."""

        if type(min_age_ms) is not int or min_age_ms < 0:
            raise ValueError("min_age_ms must not be negative")
        if type(max_entries) is not int or not 0 < max_entries <= 10000:
            raise ValueError("max_entries must be between 1 and 10000")
        self.ensure_group()
        pending = self._pending_ids(limit=max_entries * 20)
        if pending is None:
            return {
                "dry_run": dry_run,
                "scanned": 0,
                "candidates": [],
                "removed": [],
                "retained": [{"reason": "pending_state_unavailable"}],
            }
        current = int(time.time_ns() // 1_000_000) if now_ms is None else now_ms
        cutoff = current - min_age_ms
        entries = self.client.xrange(self.stream, count=max_entries)
        candidates, retained = [], []
        for entry_id, raw in entries:
            entry_id = self._field_text(entry_id)
            timestamp = self._entry_time(entry_id)
            if timestamp is None or timestamp > cutoff:
                retained.append({"entry_id": entry_id, "reason": "too_new"})
                continue
            if entry_id in pending:
                retained.append({"entry_id": entry_id, "reason": "pending"})
                continue
            fields = {self._field_text(k): self._field_text(v) for k, v in raw.items()}
            try:
                payload = validate_message(fields)
                settled = repository.delivery_settled(payload)
            except Exception as exc:  # noqa: BLE001 - unknown state must retain data
                retained.append({"entry_id": entry_id, "reason": type(exc).__name__})
                continue
            if not settled:
                retained.append({"entry_id": entry_id, "reason": "delivery_unsettled"})
                continue
            candidates.append(entry_id)
            if len(candidates) >= max_entries:
                break
        removed = []
        if not dry_run:
            for entry_id in candidates:
                if self.client.xdel(self.stream, entry_id):
                    removed.append(entry_id)
        return {
            "dry_run": dry_run,
            "scanned": len(entries),
            "candidates": candidates,
            "removed": removed,
            "retained": retained,
        }


class Dispatcher:
    def __init__(self, repository, transport: RedisStreams):
        self.repository, self.transport = repository, transport
        if transport.stream != repository.config.stream_name:
            raise ValueError("stream mismatch")

    def publish_batch(self, limit=32):
        from redis.exceptions import RedisError

        done = 0
        for row in self.repository.take_outbox(limit):
            try:
                entry = self.transport.publish(json.loads(row["payload_json"]))
            except (RedisError, OSError):
                self.repository.publish_failed(
                    row["outbox_id"], row["publisher_token"], jitter_ms=random.randint(0, 250)
                )
                continue
            # DB failure here must propagate; XADD may already have succeeded.
            done += self.repository.published(row["outbox_id"], row["publisher_token"], entry)
        return done

    def trim_safe(self, **kwargs):
        return self.transport.trim_safe(self.repository, **kwargs)
