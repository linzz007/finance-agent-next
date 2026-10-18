"""Workspace-local immutable finance records, not conversational Memory.

Snapshots/reports live entirely in db/finance.sqlite; there is no second file
copy to commit. Each insert (or idempotency check) is one SQLite transaction.
Profile Markdown and the shared facts index have a separate, recoverable
projection boundary documented in profile.py. Local filesystems only: the
worker relies on SQLite's inter-process locking, not network-share locks.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from policy_impact.company_wiki import loader
from policy_impact.domains.finance.schemas import FinanceSnapshot


class FinanceConflictError(ValueError):
    """An immutable ID already belongs to different content."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    # Reject non-JSON keys/containers instead of silently coercing them.
    def check(value):
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("JSON object keys must be strings")
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("payload must contain only JSON values")

    check(payload)
    try:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def database_path(company_id: str, filename: str = "finance.sqlite") -> Path:
    root = loader.company_dir(company_id)
    path = root / "db" / filename
    def regular_path(resolved: Path) -> Path:
        # Windows can retain this prefix while another SQLite handle is open.
        # Normalize only the spelling; redirected filesystem targets still fail.
        text = str(resolved)
        if text.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + text[8:])
        if text.startswith("\\\\?\\"):
            return Path(text[4:])
        return resolved

    if regular_path(path.resolve()) != path or regular_path(path.parent.resolve()) != path.parent:
        raise ValueError("finance database links are not allowed")
    return path


@contextmanager
def sqlite_connection(path: Path, *, immediate: bool = False,
                      timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA synchronous = FULL")
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        with conn:
            yield conn
    finally:
        conn.close()


@contextmanager
def exclusive_lock(company_id: str, name: str) -> Iterator[bool]:
    """Non-expiring SQLite mutex; process death releases it automatically.

    A dedicated database keeps slow acquisition from blocking profile writes,
    status reads, or pause requests in finance.sqlite. Never steal a live lock.
    """
    if name not in {"collection", "worker"}:
        raise ValueError("unknown finance lock")
    path = database_path(company_id, f"finance_{name}_lock.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=0)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            yield False
        else:
            yield True
    finally:
        conn.close()


class FinanceStore:
    def __init__(self, company_id: str):
        self.company_id = company_id
        self.db_path = database_path(company_id)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(immediate=True) as conn:
            for table, identity in (("finance_snapshots", "snapshot_id"),
                                    ("finance_reports", "report_id")):
                conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    {identity} TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    stored_at TEXT NOT NULL
                )""")
                for operation in ("UPDATE", "DELETE"):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'finance records are immutable'); END""")

    def connection(self, *, immediate: bool = False):
        return sqlite_connection(self.db_path, immediate=immediate)

    def _prepare(self, payload: dict, identity: str) -> tuple[str, str]:
        data = json.loads(canonical_json(payload))
        for key in ("company_id", "workspace_id"):
            if key in data and data[key] != self.company_id:
                raise ValueError(f"{key} does not match this workspace")
        if identity not in data:
            digest = hashlib.sha256(canonical_json(data).encode()).hexdigest()
            data[identity] = f"{identity.removesuffix('_id')}_{digest}"
        record_id = data[identity]
        if (not isinstance(record_id, str) or not record_id.strip()
                or record_id != record_id.strip() or len(record_id) > 200
                or any(ord(char) < 32 for char in record_id)):
            raise ValueError(f"invalid {identity}")
        if identity == "snapshot_id":
            FinanceSnapshot.model_validate(data)
        return record_id, canonical_json(data)

    def _insert(self, conn, payload: dict, *, snapshot: bool) -> str:
        identity = "snapshot_id" if snapshot else "report_id"
        table = "finance_snapshots" if snapshot else "finance_reports"
        record_id, serialized = self._prepare(payload, identity)
        row = conn.execute(f"SELECT payload_json FROM {table} WHERE {identity} = ?",
                           (record_id,)).fetchone()
        if row is not None:
            if row["payload_json"] != serialized:
                raise FinanceConflictError(f"{identity} already has different content: {record_id}")
            return record_id
        conn.execute(f"INSERT INTO {table} ({identity}, payload_json, stored_at) VALUES (?, ?, ?)",
                     (record_id, serialized, utc_now()))
        return record_id

    def save_snapshot(self, payload: dict) -> str:
        with self.connection(immediate=True) as conn:
            return self._insert(conn, payload, snapshot=True)

    def latest_snapshot(self) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT payload_json FROM finance_snapshots "
                               "ORDER BY sequence DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def save_report(self, payload: dict) -> str:
        # Report schema belongs to the workflow; preserve its full evidence payload.
        with self.connection(immediate=True) as conn:
            return self._insert(conn, payload, snapshot=False)

    def get_report(self, report_id: str) -> dict | None:
        if not isinstance(report_id, str):
            raise ValueError("report_id must be a string")
        with self.connection() as conn:
            row = conn.execute("SELECT payload_json FROM finance_reports WHERE report_id = ?",
                               (report_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_reports(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self.connection() as conn:
            rows = conn.execute("SELECT payload_json FROM finance_reports "
                                "ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]
