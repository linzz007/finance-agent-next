"""Immutable v2 analytical records; source identity is supplied by Runtime only."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import tempfile
from typing import Any

from policy_impact.runtime.research_contracts import ResearchResult, ResearchTask


SCHEMA = "finance.research_artifact.v2"
PREFIXES = {"article_understanding": "understanding", "synthesis": "synthesis", "working_view": "view"}
ID_RE = re.compile(r"(?:understanding|synthesis|view)_[0-9a-f]{24}\Z")


def checksum(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


class ResearchRecords:
    def __init__(self, owner, *, page_size: int = 50):
        self.owner = owner
        self.page_size = page_size
        self._cursor_key = os.urandom(32)
        self.context_provider = None

    def call(self, operation, *, kind=None, payload=None, artifact_id=None, research_task=None, cursor=None):
        if operation == "get":
            return self.get(artifact_id)
        if operation == "list":
            return self.list(kind=kind, cursor=cursor)
        if operation != "save" or self.context_provider is None:
            raise ValueError("v2 save requires Runtime-bound research context")
        task = ResearchTask.model_validate(research_task)
        if kind != task.requested_result_kind:
            raise ValueError("kind must match research_task.requested_result_kind")
        context = self.context_provider(task, payload)
        task = context.pop("task", task)
        after_save = context.pop("after_save", None)
        try:
            receipt = self.persist(task=task, payload=payload, **context)
        except OSError as exc:
            if after_save is None:
                raise
            receipt = {"persistence_status": "failed", "error_type": type(exc).__name__}
        return after_save(receipt) if after_save else receipt

    def validate(self, task, payload, *, material_bindings, source_bindings, processing):
        body = ResearchResult.model_validate(payload).model_dump(mode="json", exclude_none=True)
        aliases = {key: row["material_ref"] for row in material_bindings
                   for key in (row["material_ref"], row["lookup_ref"])}
        visible = set(processing.get("visible_material_refs", []))
        visible_evidence = {ref for row in processing.get("analysis_input_calls", [])
                            for ref in row.get("evidence_ids", [])}
        allowed_evidence = {row["evidence_id"] for row in source_bindings
                            if row.get("evidence_id") in visible_evidence}
        for item in body["material_disposition"]:
            if item["material_ref"] not in aliases:
                raise PermissionError("result material is outside the task grant")
            item["material_ref"] = aliases[item["material_ref"]]
            if item["decision"] == "considered" and item["material_ref"] not in visible:
                raise ValueError("considered material was not included in an actual model input")
        normalized = [item["material_ref"] for item in body["material_disposition"]]
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate decisions for equivalent material handles")
        for gap in body["gaps"]:
            if any(ref not in aliases for ref in gap["material_refs"]):
                raise PermissionError("gap refers to an ungranted material")
            gap["material_refs"] = [aliases[ref] for ref in gap["material_refs"]]
        for item in body["evidence_links"]:
            for ref in item["evidence_refs"]:
                if ref not in allowed_evidence or self.owner.evidence.get(ref) is None:
                    raise PermissionError("research result references unobserved/ungranted evidence")
            for quote in item["quotes"]:
                source = self.owner.evidence.get(quote["evidence_ref"])
                if source is None or quote["text"] not in source.excerpt:
                    raise ValueError("research quote is not verbatim in the observed source")
        allowed_artifacts = {row["lookup_ref"] for row in material_bindings if row["kind"] == "artifact"}
        if any(ref not in allowed_artifacts for ref in body["supporting_artifact_refs"]):
            raise PermissionError("supporting artifact was not granted")
        return body

    def persist(self, *, task, payload, task_id, producer, material_bindings, source_bindings,
                dependencies, processing, execution):
        task = ResearchTask.model_validate(task)
        body = self.validate(task, payload, material_bindings=material_bindings,
                             source_bindings=source_bindings, processing=processing)
        for binding in source_bindings:
            self._validate_source(binding)
        for dependency in dependencies:
            record = self.owner.call(operation="get", artifact_id=dependency["artifact_id"])
            if self.record_checksum(record) != dependency["checksum"]:
                raise ValueError("stale upstream research artifact")
        identity = {
            "schema_version": SCHEMA, "kind": task.requested_result_kind,
            "session_id": self.owner.session_id, "run_id": self.owner.run_id,
            "task_id": task_id, "producer": producer,
            "task_snapshot": task.model_dump(mode="json", exclude_none=True),
            "material_bindings": material_bindings, "source_bindings": source_bindings,
            "dependencies": dependencies, "processing": processing,
            "execution": execution, "payload": body,
        }
        digest = checksum(identity)
        artifact_id = f"{PREFIXES[task.requested_result_kind]}_{digest[:24]}"
        record = {**identity, "artifact_id": artifact_id, "checksum": digest}
        root = self.owner._directory(create=True)
        path = root / f"{artifact_id}.json"
        if path.is_symlink():
            raise PermissionError("research artifact cannot be a symlink")
        if path.exists():
            if self.get(artifact_id) != record:
                raise ValueError("research artifact ID collision")
        else:
            fd, temporary = tempfile.mkstemp(prefix="research.", suffix=".tmp", dir=root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=False, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {**self.summary(record), "persistence_status": "saved"}

    def get(self, artifact_id, *, seen=None):
        if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
            raise ValueError("artifact_id must be a generated v2 research ID")
        seen = set(seen or ())
        if artifact_id in seen:
            raise ValueError("cyclic research artifact dependency")
        seen.add(artifact_id)
        path = self.owner._directory() / f"{artifact_id}.json"
        if path.is_symlink():
            raise PermissionError("research artifact cannot be a symlink")
        record = json.loads(path.read_text(encoding="utf-8"))
        required = {"schema_version", "artifact_id", "kind", "session_id", "run_id", "task_id",
                    "producer", "task_snapshot", "material_bindings", "source_bindings",
                    "dependencies", "processing", "execution", "payload", "checksum"}
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("invalid research record envelope")
        identity = {key: value for key, value in record.items() if key not in {"artifact_id", "checksum"}}
        digest = checksum(identity)
        if (record.get("schema_version") != SCHEMA or record.get("kind") not in PREFIXES
                or record.get("artifact_id") != artifact_id or record.get("checksum") != digest
                or artifact_id != f"{PREFIXES[record['kind']]}_{digest[:24]}"
                or record.get("run_id") != self.owner.run_id
                or record.get("session_id") != self.owner.session_id):
            raise ValueError("research record schema, identity, scope or checksum is invalid")
        task = ResearchTask.model_validate(record["task_snapshot"])
        if task.requested_result_kind != record["kind"]:
            raise ValueError("research result kind does not match its task")
        ResearchResult.model_validate(record["payload"])
        if (not all(isinstance(record[key], dict) for key in ("producer", "processing", "execution"))
                or not all(isinstance(record[key], list) for key in (
                    "material_bindings", "source_bindings", "dependencies"))):
            raise ValueError("invalid research record provenance")
        materials = record["material_bindings"]
        sources = record["source_bindings"]
        if (any(not isinstance(row, dict) or not all(isinstance(row.get(key), str) and row[key]
                                                    for key in ("material_ref", "lookup_ref", "kind", "content_hash"))
                or row["kind"] not in {"article", "artifact"} for row in materials)
                or {row["material_ref"] for row in materials} != set(task.material_refs)
                or len(materials) != len(task.material_refs)
                or any(not isinstance(row, dict) or not isinstance(row.get("evidence_id"), str)
                       or not isinstance(row.get("content_hash"), str) for row in sources)):
            raise ValueError("invalid research material/source bindings")
        granted_evidence = {row["evidence_id"] for row in sources}
        visible = record["processing"].get("visible_material_refs")
        calls = record["processing"].get("analysis_input_calls")
        if (not isinstance(visible, list) or not isinstance(calls, list)
                or any(ref not in task.material_refs for ref in visible)
                or any(not isinstance(row, dict) or not isinstance(row.get("evidence_ids"), list) for row in calls)):
            raise ValueError("invalid research input scope")
        processed_evidence = {ref for row in calls for ref in row["evidence_ids"]}
        if any(ref not in granted_evidence or ref not in processed_evidence
               for link in record["payload"]["evidence_links"] for ref in link["evidence_refs"]):
            raise ValueError("research payload evidence is outside its observed input scope")
        if any(row["material_ref"] not in task.material_refs
               or row["decision"] == "considered" and row["material_ref"] not in visible
               for row in record["payload"]["material_disposition"]):
            raise ValueError("research material disposition exceeds actual input scope")
        for binding in record["source_bindings"]:
            self._validate_source(binding)
        for dependency in record["dependencies"]:
            if (not isinstance(dependency, dict)
                    or not isinstance(dependency.get("artifact_id"), str)
                    or not isinstance(dependency.get("checksum"), str)):
                raise ValueError("invalid research artifact dependency")
            ref = dependency["artifact_id"]
            upstream = (self.get(ref, seen=seen) if ID_RE.fullmatch(ref)
                        else self.owner.call(operation="get", artifact_id=ref))
            if self.record_checksum(upstream) != dependency["checksum"]:
                raise ValueError("stale upstream research artifact")
        return record

    def _validate_source(self, binding):
        if not isinstance(binding, dict) or not isinstance(binding.get("content_hash"), str):
            raise ValueError("invalid research source version")
        if binding.get("article_id"):
            article = self.owner.corpus.get_article(binding["article_id"])
            if article is None or article["current_hash"] != binding["content_hash"]:
                raise ValueError("stale source version; reanalysis requires an explicit new task")

    @staticmethod
    def record_checksum(record):
        return record.get("checksum") or checksum(record)

    @staticmethod
    def summary(record):
        return {key: record[key] for key in (
            "schema_version", "artifact_id", "kind", "task_id", "checksum",
        )}

    def list(self, *, kind=None, cursor=None):
        if kind is not None and kind not in PREFIXES:
            raise ValueError("unknown v2 research kind")
        root = self.owner._directory()
        files = sorted(path for path in root.glob("*.json") if ID_RE.fullmatch(path.stem))
        if any(path.is_symlink() for path in files):
            raise PermissionError("research artifact cannot be a symlink")
        snapshot = checksum([(path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in files])
        offset = 0
        if cursor is not None:
            try:
                encoded, signature = cursor.split(".")
                if not hmac.compare_digest(signature, hmac.new(self._cursor_key, encoded.encode(), hashlib.sha256).hexdigest()):
                    raise ValueError("invalid cursor signature")
                marker = json.loads(base64.urlsafe_b64decode(encoded))
                if marker["run_id"] != self.owner.run_id or marker["kind"] != kind:
                    raise ValueError("cursor scope mismatch")
                if marker["snapshot"] != snapshot:
                    raise ValueError("stale_cursor: relist the changed collection")
                offset = marker["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError("invalid cursor offset")
            except (TypeError, KeyError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid research cursor") from exc
        records = [self.get(path.stem) for path in files]
        records = [row for row in records if kind is None or row["kind"] == kind]
        page = records[offset:offset + self.page_size]
        next_offset = offset + len(page)
        complete = next_offset >= len(records)
        next_cursor = None
        if not complete:
            marker = {"run_id": self.owner.run_id, "kind": kind, "snapshot": snapshot, "offset": next_offset}
            encoded = base64.urlsafe_b64encode(json.dumps(marker, sort_keys=True).encode()).decode()
            next_cursor = encoded + "." + hmac.new(self._cursor_key, encoded.encode(), hashlib.sha256).hexdigest()
        return {"schema_version": SCHEMA, "artifacts": [self.summary(row) for row in page],
                "count": len(page), "next_cursor": next_cursor, "listing_complete": complete}
