"""Explicit, transactional migration and verified run-scoped artifact storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .contracts import DispatchError

VERSION = "H2-CONTRACTS-2026-10-18-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def encode(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def clean_error(exc: Exception) -> str:
    # Errors are a category, not untrusted provider bodies or URLs with credentials.
    return type(exc).__name__


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connection(self, *, write: bool = False):
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def migrate(self) -> None:
        """Explicit call only. Never reached just by importing/constructing services."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.resolve() != self.path.parent.absolute():
            raise ValueError("linked database path is not allowed")
        script = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        checksum = hashlib.sha256(script.encode()).hexdigest()
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "CREATE TABLE IF NOT EXISTS h2_schema_migrations "
                "(version TEXT PRIMARY KEY, checksum TEXT NOT NULL)"
            )
            rows = c.execute("SELECT version,checksum FROM h2_schema_migrations").fetchall()
            if rows:
                if rows != [(VERSION, checksum)]:
                    raise DispatchError("schema_version_mismatch")
            else:
                statement = ""
                for line in script.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        c.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError("incomplete DDL")
                c.execute("INSERT INTO h2_schema_migrations VALUES (?,?)", (VERSION, checksum))
            if c.execute("PRAGMA foreign_key_check").fetchall():
                raise DispatchError("schema_foreign_key_error")
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()


@dataclass(frozen=True)
class Artifact:
    artifact_ref: str
    run_id: str
    kind: str
    storage_uri: str
    sha256: str
    byte_length: int
    media_type: str


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def _path(self, run_id: str, artifact_ref: str) -> Path:
        if not SAFE_ID.fullmatch(run_id) or not SAFE_ID.fullmatch(artifact_ref):
            raise DispatchError("artifact_unavailable")
        path = self.root / "runs" / run_id / "artifacts" / artifact_ref
        if path.resolve() != path or not path.is_relative_to(self.root):
            raise DispatchError("artifact_unavailable")
        return path

    def put(self, run_id: str, kind: str, data: bytes, media_type="text/plain") -> Artifact:
        ref = new_id("art")
        path = self._path(run_id, ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self._path(run_id, ref)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return Artifact(
            ref,
            run_id,
            kind,
            str(path.relative_to(self.root)),
            hashlib.sha256(data).hexdigest(),
            len(data),
            media_type,
        )

    def json(self, run_id: str, kind: str, value) -> Artifact:
        return self.put(run_id, kind, encode(value).encode(), "application/json")

    def read(self, row) -> bytes:
        path = self._path(row["run_id"], row["artifact_ref"])
        if str(path.relative_to(self.root)) != row["storage_uri"]:
            raise DispatchError("artifact_unavailable")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DispatchError("artifact_unavailable") from exc
        if len(data) != row["byte_length"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise DispatchError("artifact_unavailable")
        return data
