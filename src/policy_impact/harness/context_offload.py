"""Run-scoped storage for recoverable model-context observations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from policy_impact.harness.context_manifest import estimate_tokens


_ID = re.compile(r"context_[0-9a-f]{24}\Z")


class RunContextStore:
    def __init__(self, run_dir: Path) -> None:
        self.root = run_dir / "context_observations"

    def save(self, content: dict[str, Any], tool_name: str) -> dict[str, Any]:
        if not isinstance(content, dict) or not isinstance(tool_name, str):
            raise ValueError("context observation needs an object and tool name")
        serialized = json.dumps(
            {"tool_name": tool_name, "content": content},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        context_id = f"context_{checksum[:24]}"
        record = {
            "schema_version": "run.context_observation.v1",
            "context_id": context_id, "checksum": checksum,
            "tool_name": tool_name, "content": content,
        }
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise PermissionError("context observation directory cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{context_id}.json"
        if path.is_symlink():
            raise PermissionError("context observation cannot be a symlink")
        if path.exists():
            self.open(context_id)
        else:
            fd, temporary = tempfile.mkstemp(prefix="observation.", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {"context_id": context_id, "checksum": checksum, "tool_name": tool_name}

    def open(self, context_id: str) -> dict[str, Any]:
        if not isinstance(context_id, str) or not _ID.fullmatch(context_id):
            raise ValueError("context_id must be a generated observation ID")
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise PermissionError("context observation directory cannot be a symlink")
        path = self.root / f"{context_id}.json"
        if path.is_symlink():
            raise PermissionError("context observation cannot be a symlink")
        record = json.loads(path.read_text(encoding="utf-8"))
        content = record.get("content")
        checksum = hashlib.sha256(json.dumps(
            {"tool_name": record.get("tool_name"), "content": content},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if (record.get("schema_version") != "run.context_observation.v1"
                or record.get("context_id") != context_id
                or record.get("checksum") != checksum
                or context_id != f"context_{checksum[:24]}"
                or not isinstance(content, dict)):
            raise ValueError("context observation identity or content hash is invalid")
        return record

    def view(self, context_id: str, *, offset: int = 0, limit: int | None = None,
             max_tokens: int = 6000) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or (limit is not None and
                (type(limit) is not int or not 1 <= limit <= 12000)):
            raise ValueError("context page requires nonnegative offset and limit 1-12000")
        record = self.open(context_id)
        if not offset and limit is None and estimate_tokens(record) <= max_tokens:
            return record
        serialized = json.dumps(record["content"], ensure_ascii=False, sort_keys=True)
        if offset >= len(serialized):
            raise ValueError("context offset is outside the stored observation")
        size = limit or 12000
        page = {
            "schema_version": "run.context_view.v1", "context_id": context_id,
            "checksum": record["checksum"], "tool_name": record["tool_name"],
            "offset": offset, "total_chars": len(serialized),
            "notice": "Exact continuous JSON-text page, not a summary or new evidence. Use next_offset for the next page.",
        }
        while True:
            end = min(offset + size, len(serialized))
            result = {**page, "text": serialized[offset:end], "returned_chars": end - offset,
                      "next_offset": end if end < len(serialized) else None}
            if estimate_tokens(result) <= max_tokens:
                return result
            if size <= 1:
                raise ValueError("context_budget_exceeded: no room for a context page")
            size = max(1, size // 2)

    def validate_view(self, view: dict[str, Any]) -> dict[str, Any]:
        original = self.open(view.get("context_id"))
        if view.get("schema_version") == "run.context_view.v1":
            offset, size = view.get("offset"), view.get("returned_chars")
            expected = self.view(view["context_id"], offset=offset, limit=size,
                                 max_tokens=estimate_tokens(view) + 1)
        else:
            expected = original
        if view != expected:
            raise ValueError("restored observation differs from its stored source")
        return original


__all__ = ["RunContextStore"]
