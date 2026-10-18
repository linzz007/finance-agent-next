"""Run-local public drafts, preserved independently of later model failures."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from policy_impact.domains.finance.answer_audit import _target_answer_context
from policy_impact.domains.finance.state import answer_audit_coverage
from policy_impact.skills.schemas import FinanceReviewTarget


def _checksum(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _sources(bundle: dict[str, Any]) -> dict[str, str]:
    return {
        str(row["evidence_id"]): _checksum({
            key: value for key, value in row.items() if key != "retrieved_at"
        })
        for row in bundle.get("evidence") or [] if row.get("evidence_id")
    }


def _review_target(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("target")
    if isinstance(nested, dict):
        return nested
    return {key: value for key, value in row.items() if key in FinanceReviewTarget.model_fields}


def _target_task(target: dict[str, Any]) -> str | None:
    if not target.get("claim_or_question"):
        return None
    # Evidence may be added to repair an input gap without changing the task.
    return _checksum({key: value for key, value in target.items()
                      if key not in {"target_id", "answer_context", "evidence_refs"}})


def _covers_objection(
    answer: str, target: dict[str, Any], issue: dict[str, Any],
    original_answer: str, original_target: dict[str, Any],
) -> bool:
    reviewed_text, context = _target_answer_context(answer, target.get("answer_context") or [])
    if context["scope"] == "full_answer":
        return True
    span = issue.get("span")
    if isinstance(span, str) and span.strip() and span in original_answer:
        return answer.count(span) == 1 and span in reviewed_text
    original_context, _ = _target_answer_context(
        original_answer, original_target.get("answer_context") or [],
    )
    return bool(original_context) and original_context in reviewed_text


class AnswerCandidates:
    """Immutable disk checkpoints plus an inline fallback if a write fails.

    This is not a restart recovery store. Runtime still revalidates a selected
    draft against current permissions, source identity and concrete local checks.
    """

    def __init__(self, directory: Path, emit: Callable[[str, dict[str, Any]], None]):
        self.directory = directory
        self.emit = emit
        self.records: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self._event_count = 0

    def _save(self, name: str, record: dict[str, Any]) -> dict[str, Any]:
        temporary = None
        try:
            if self.directory.is_symlink():
                raise OSError("candidate directory cannot be a symlink")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / name
            if path.exists() or path.is_symlink():
                raise OSError("candidate checkpoints are immutable")
            fd, temporary = tempfile.mkstemp(prefix="candidate.", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            return {"persistence_status": "saved", "checkpoint_path": str(path)}
        except OSError as exc:
            return {"persistence_status": "failed", "error_type": type(exc).__name__}
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def capture(self, output: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any] | None:
        answer = output.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return None
        public = deepcopy(output)
        identity = {key: value for key, value in public.items() if key not in {
            "verification_status", "semantic_review", "answer_audit_coverage", "review_status",
            "review_disposition", "review_resolution_reason", "open_gaps", "delivery_mode",
        }}
        sources = _sources(bundle)
        signature = _checksum({"output": identity, "source_snapshots": sources})
        for record in reversed(self.records):
            if record["input_signature"] == signature:
                return record
        version = len(self.records) + 1
        record = {
            "schema_version": "finance.answer_candidate.v1",
            "candidate_version": version,
            "parent_version": version - 1 if version > 1 else None,
            "answer_checksum": _checksum(answer),
            "input_signature": signature,
            "output": public,
            "source_snapshots": sources,
            "local_checks": {"status": "passed", "semantic_certification": False},
        }
        receipt = self._save(f"candidate-{version:04d}.json", record)
        self.records.append({**record, **receipt})
        self.emit("answer_candidate_checkpoint", {
            "candidate_version": version, "answer_checksum": record["answer_checksum"],
            "source_ids": list(sources), **receipt,
        })
        return self.records[-1]

    def reviewed(self, candidate: dict[str, Any], audit: dict[str, Any] | None) -> None:
        self._event_count += 1
        record = {
            "candidate_version": candidate["candidate_version"],
            "answer_checksum": candidate["answer_checksum"],
            "source_snapshots": candidate["source_snapshots"],
            "audit": deepcopy(audit),
        }
        receipt = self._save(f"review-{self._event_count:04d}.json", record)
        self.reviews.append({**record, **receipt})
        self.emit("answer_candidate_checkpoint", {
            "candidate_version": record["candidate_version"], "record_kind": "review",
            "answer_checksum": record["answer_checksum"], **receipt,
        })

    def latest_review(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        return next((deepcopy(row) for row in reversed(self.reviews)
                     if row["candidate_version"] == candidate["candidate_version"]), None)

    def unresolved(self, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        # Older/newer issues cannot disappear merely by choosing a different ID,
        # an empty target list, or a prior draft after a failed revision.
        versions = {record["candidate_version"]: record for record in self.records}
        issues: dict[str, tuple[dict[str, Any], str | None, str, dict[str, Any]]] = {}
        for row in self.reviews:
            audit = row.get("audit") or {}
            original_answer = versions[row["candidate_version"]]["output"]["answer"]
            for issue in audit.get("issues") or []:
                target = _review_target(issue)
                semantic = _target_task(target)
                key = _checksum({"task": semantic, "issue": {
                    key: value for key, value in issue.items() if key not in {"target_id", "target"}
                }})
                issues[key] = (deepcopy(issue), semantic, original_answer, target)
            if row["candidate_version"] == candidate["candidate_version"]:
                details = (audit.get("coverage_detail") or {}).get("targets") or []
                for check in audit.get("checks") or []:
                    target = _review_target(check)
                    if check.get("action") != "retain" or not target:
                        continue
                    detail = next((item for item in details if item["target_id"] == target.get("target_id")), None)
                    complete = (detail.get("checked") is True and not detail.get("gap_types")) if detail else (
                        answer_audit_coverage(audit, default="failed") == "complete")
                    semantic = _target_task(target)
                    if not complete or semantic is None:
                        continue
                    issues = {key: value for key, value in issues.items() if not (
                        value[1] == semantic and _covers_objection(
                            candidate["output"]["answer"], target, value[0], value[2], value[3],
                        )
                    )}
        return [value[0] for value in issues.values()]

    def select(self, bundle: dict[str, Any], validate: Callable[[dict[str, Any]], str | None]):
        current = _sources(bundle)
        for candidate in reversed(self.records):
            output = deepcopy(candidate["output"])
            changed = [key for key, value in candidate["source_snapshots"].items()
                       if current.get(key) != value]
            issue = "candidate source snapshot changed: " + ", ".join(changed) if changed else validate(output)
            self.emit("answer_candidate_selection", {
                "candidate_version": candidate["candidate_version"],
                "answer_checksum": candidate["answer_checksum"],
                "selected": not bool(issue), "reason": issue or "eligible_complete_public_candidate",
                "persistence_status": candidate["persistence_status"],
            })
            if not issue:
                review = self.latest_review(candidate)
                audit = deepcopy((review or {}).get("audit"))
                issues = self.unresolved(candidate)
                if issues:
                    audit = {**(audit or {}), "issues": issues}
                return output, audit, candidate
        return {}, None, None
