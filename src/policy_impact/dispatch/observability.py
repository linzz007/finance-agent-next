"""Durable runtime records, quarantine, metrics, and bounded cleanup helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .storage import SAFE_ID, now_ms

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token)", re.IGNORECASE
)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+\S+)", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Return JSON-safe diagnostics without credentials or unbounded strings."""

    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[redacted]", value[:16384])
    return value


def _json_line(value: Any) -> bytes:
    return (json.dumps(redact(value), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


class RuntimeRecords:
    """Filesystem records kept outside the SQLite authority."""

    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def _run_dir(self, run_id: str) -> Path:
        if not SAFE_ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        path = self.root / "runs" / run_id
        if path.resolve() != path or not path.is_relative_to(self.root):
            raise ValueError("runtime record path escaped root")
        return path

    def _write_bytes(self, path: Path, data: bytes) -> Path:
        path = path.absolute()
        if path.resolve() != path or not path.is_relative_to(self.root):
            raise ValueError("runtime record path escaped root")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def write_json(self, run_id: str, name: str, value: Any) -> Path:
        if Path(name).name != name or not name.endswith(".json"):
            raise ValueError("runtime JSON name must be a file name")
        data = json.dumps(redact(value), ensure_ascii=False, indent=2).encode("utf-8")
        return self._write_bytes(self._run_dir(run_id) / name, data)

    def write_jsonl(self, run_id: str, name: str, rows: Iterable[Any]) -> Path:
        if Path(name).name != name or not name.endswith(".jsonl"):
            raise ValueError("runtime JSONL name must be a file name")
        data = b"".join(_json_line(row) for row in rows)
        return self._write_bytes(self._run_dir(run_id) / name, data)

    def quarantine(self, *, entry_id: str, raw: Any, error_code: str, error_message: str = "") -> Path:
        path = self.root / "errors" / "malformed-messages.jsonl"
        record = {
            "entry_id": str(entry_id),
            "error_code": error_code,
            "error_message": error_message[:500],
            "raw": raw,
            "recorded_at": now_ms(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path = path.absolute()
        with path.open("ab") as handle:
            handle.write(_json_line(record))
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def scan_orphans(self, *, known_uris: set[str], now: int | None = None, min_age_ms: int = 0) -> list[Path]:
        """Find unregistered artifact files without deleting them."""

        if min_age_ms < 0:
            raise ValueError("min_age_ms must not be negative")
        now = now_ms() if now is None else now
        artifact_root = self.root / "runs"
        if not artifact_root.exists():
            return []
        candidates = []
        for path in artifact_root.glob("*/artifacts/*"):
            if not path.is_file() or path.is_symlink():
                continue
            uri = str(path.relative_to(self.root)).replace("\\", "/")
            age = max(0, now - int(path.stat().st_mtime * 1000))
            if uri not in known_uris and age >= min_age_ms:
                candidates.append(path)
        return sorted(candidates)

    def remove_orphans(self, paths: Iterable[Path]) -> list[str]:
        removed: list[str] = []
        for candidate in paths:
            path = Path(candidate).absolute()
            if path.is_symlink() or not path.is_file() or not path.is_relative_to(self.root):
                continue
            path.unlink()
            removed.append(str(path.relative_to(self.root)).replace("\\", "/"))
        return removed


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = ["RuntimeRecords", "redact", "sha256_bytes"]
