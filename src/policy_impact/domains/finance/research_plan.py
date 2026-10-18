"""Run-local research goals for long finance conversations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


TOOL_DESCRIPTION = {
    "name": "research.plan",
    "description": "Create, inspect, update or explicitly load run-local research goals and scope for every finance turn. Keep simple plans concise but complete; use scope only to preserve user requirements. For create, goals is a 2-8 item string list; scope.authors and scope.topics are lists, while scope.date_range is an object such as {start, end}.",
    "parameters": {
        "operation": "create | update | get | load",
        "goals": "For create: 2-8 short user-facing goals",
        "scope": "Optional structured scope: authors, date_range, topics, deliverable",
        "goal_id": "For update: 1-based research goal number; never update the runtime_delivery goal, which Runtime completes after the final answer is published",
        "status": "For update: pending | done | blocked",
        "note": "Optional progress note; required when blocked",
        "run_id": "For load: a prior run ID in the same session",
    },
}


class ResearchPlan:
    DELIVERY_KIND = "runtime_delivery"

    def __init__(
        self,
        *,
        session_id: str = "",
        run_id: str = "",
        state_path: Path | None = None,
    ) -> None:
        self._goals: list[dict[str, Any]] = []
        self._scope: dict[str, Any] = {}
        self._delivery: dict[str, Any] = {}
        self._revision = 0
        self.session_id = str(session_id or "")
        self.run_id = str(run_id or "")
        self.state_path = Path(state_path).resolve() if state_path is not None else None

    @property
    def exists(self) -> bool:
        return bool(self._goals)

    @property
    def pending(self) -> list[dict[str, Any]]:
        return [dict(goal) for goal in self._goals if goal["status"] == "pending"]

    def snapshot(self) -> dict[str, Any]:
        snapshot = {
            "revision": self._revision,
            "goals": [dict(goal) for goal in self._goals],
            "scope": dict(self._scope),
            "delivery": dict(self._delivery),
            "pending_count": sum(
                goal["status"] == "pending" for goal in self._goals
                if goal.get("kind") != self.DELIVERY_KIND
            ),
            "research_pending_count": sum(
                goal["status"] == "pending" for goal in self._goals
                if goal.get("kind") != self.DELIVERY_KIND
            ),
            "delivery_status": next(
                (goal["status"] for goal in self._goals
                 if goal.get("kind") == self.DELIVERY_KIND), "missing"
            ),
        }
        snapshot.update(self._delivery)
        if self.session_id:
            snapshot["session_id"] = self.session_id
        if self.run_id:
            snapshot["run_id"] = self.run_id
        if self.state_path is not None:
            snapshot["state_file"] = str(self.state_path)
        return snapshot

    def call(
        self,
        operation: str,
        goals: list[str] | None = None,
        scope: dict[str, Any] | None = None,
        goal_id: int | None = None,
        status: str | None = None,
        note: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if operation == "get":
            return self.snapshot()
        if operation == "load":
            self._load_from_run(run_id)
            return self.snapshot()
        if operation == "create":
            if self.exists:
                raise ValueError("research plan already exists; update individual goals")
            if not isinstance(goals, list) or not 2 <= len(goals) <= 8:
                raise ValueError("create requires 2-8 goals")
            cleaned = [goal.strip() for goal in goals if isinstance(goal, str)]
            if len(cleaned) != len(goals) or any(not goal or len(goal) > 160 for goal in cleaned):
                raise ValueError("each goal must be 1-160 characters")
            normalized_scope = self._normalize_scope(scope)
            self._goals = [
                {"goal_id": index, "goal": goal, "status": "pending", "note": ""}
                for index, goal in enumerate(cleaned, 1)
            ]
            self._ensure_delivery_goal()
            self._scope = normalized_scope
        elif operation == "update":
            if isinstance(goal_id, str) and re.fullmatch(r"[1-9][0-9]*", goal_id):
                goal_id = int(goal_id)
            if isinstance(goal_id, bool) or not isinstance(goal_id, int) or not 1 <= goal_id <= len(self._goals):
                raise ValueError("goal_id must identify an existing goal")
            if self._goals[goal_id - 1].get("kind") == self.DELIVERY_KIND:
                raise ValueError(
                    "runtime_delivery is not a research goal; submit the final answer "
                    "instead and Runtime will update its delivery status"
                )
            if status not in {"pending", "done", "blocked"}:
                raise ValueError("status must be pending, done or blocked")
            if not isinstance(note, str) or len(note) > 240 or (status == "blocked" and not note.strip()):
                raise ValueError("blocked goals require a note of at most 240 characters")
            normalized_scope = self._normalize_scope(scope) if scope is not None else None
            self._goals[goal_id - 1].update(status=status, note=note.strip())
            if normalized_scope is not None:
                self._scope = normalized_scope
        else:
            raise ValueError("operation must be create, update, load or get")
        self._revision += 1
        self._persist()
        return self.snapshot()

    def finish_delivery(
        self,
        *,
        published: bool | None = None,
        delivered: bool | None = None,
        delivery_mode: str | None = None,
        verification_status: str | None = None,
        evidence_coverage: str | None = None,
        answer_audit_coverage: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Record delivery independently from research-goal completeness."""
        if delivered is None:
            delivered = bool(published)
        self._ensure_delivery_goal()
        goal = next(goal for goal in self._goals if goal.get("kind") == self.DELIVERY_KIND)
        mode = str(delivery_mode or ("qualified" if delivered else "source_only"))
        verification = str(verification_status or ("partial" if delivered else "failed"))
        evidence = str(evidence_coverage or ("partial" if delivered else "uncovered"))
        audit = str(answer_audit_coverage or ("partial" if delivered else "failed"))
        if mode not in {"full", "qualified", "source_only"}:
            raise ValueError("delivery_mode must be full, qualified or source_only")
        if verification not in {"verified", "partial", "disputed", "failed"}:
            raise ValueError("verification_status is invalid")
        if evidence not in {"complete", "partial", "uncovered"}:
            raise ValueError("evidence_coverage is invalid")
        if audit not in {"complete", "partial", "failed"}:
            raise ValueError("answer_audit_coverage is invalid")
        for research_goal in self._goals:
            if research_goal.get("kind") != self.DELIVERY_KIND and research_goal["status"] == "pending":
                progress = str(research_goal.get("note") or "").strip()
                interruption_reason = reason.strip() or (
                    "Goal completion not recorded before final delivery" if delivered
                    else "No final answer delivered"
                )
                prefix = "Completion not recorded before final delivery" if delivered else "Interrupted"
                interruption = (interruption_reason if delivered and not reason.strip()
                                else f"{prefix}: {interruption_reason}")
                research_goal.update(
                    status="blocked",
                    note=(interruption + ("; prior progress: " + progress if progress else ""))[:240],
                    interruption={"reason": interruption_reason, "previous_note": progress},
                )
        goal.update(
            status="done" if delivered else "blocked",
            note="Final answer delivered" if delivered else (reason.strip() or "No safe answer delivered")[:240],
        )
        self._delivery = {
            "delivery_status": "done" if delivered else "blocked",
            "delivery_mode": mode,
            "verification_status": verification,
            "evidence_coverage": evidence,
            "answer_audit_coverage": audit,
        }
        self._revision += 1
        self._persist()
        return self.snapshot()

    def _ensure_delivery_goal(self) -> None:
        if any(goal.get("kind") == self.DELIVERY_KIND for goal in self._goals):
            return
        self._goals.append({
            "goal_id": len(self._goals) + 1,
            "goal": "Review and deliver the final answer",
            "kind": self.DELIVERY_KIND,
            "status": "pending",
            "note": "",
        })

    def _persist(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "research_plan.v1",
            "session_id": self.session_id,
            "run_id": self.run_id,
            "revision": self._revision,
            "goals": [dict(goal) for goal in self._goals],
            "scope": dict(self._scope),
            "delivery": dict(self._delivery),
        }
        fd, temp_name = tempfile.mkstemp(
            prefix="research_plan.", suffix=".tmp", dir=str(self.state_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.state_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _load_from_run(self, source_run_id: str | None) -> None:
        if self.state_path is None or not self.session_id:
            raise PermissionError("research plan loading requires a bound session and state directory")
        source = str(source_run_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", source):
            raise ValueError("run_id must be a generated run identifier")
        runs_root = self.state_path.parent.parent
        source_path = (runs_root / source / "research_plan.json").resolve()
        if source_path != runs_root.resolve() / source / "research_plan.json":
            raise PermissionError("research plan source escaped the run directory")
        if not source_path.is_file():
            raise FileNotFoundError(f"research plan not found for run: {source}")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "research_plan.v1":
            raise ValueError("unsupported research plan schema")
        if str(payload.get("session_id") or "") != self.session_id:
            raise PermissionError("research plan belongs to another session")
        goals = payload.get("goals")
        if not isinstance(goals, list) or any(not isinstance(goal, dict) for goal in goals):
            raise ValueError("research plan state is invalid")
        self._goals = [dict(goal) for goal in goals]
        self._ensure_delivery_goal()
        self._scope = self._normalize_scope(payload.get("scope"))
        delivery = payload.get("delivery")
        if isinstance(delivery, dict):
            self._delivery = dict(delivery)
        self._revision = int(payload.get("revision") or 0)

    @staticmethod
    def _normalize_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
        """Keep planning metadata structured without constraining the analysis method."""
        if scope is None:
            return {}
        if not isinstance(scope, dict):
            raise ValueError("scope must be an object")
        normalized: dict[str, Any] = {}
        authors = scope.get("authors")
        if authors is not None:
            if not isinstance(authors, list) or len(authors) > 20:
                raise ValueError("scope.authors must be a list of at most 20 names; use [] when no author scope is known")
            cleaned_authors = [str(author).strip() for author in authors]
            if any(not author or len(author) > 80 for author in cleaned_authors):
                raise ValueError("scope.authors contains an invalid name")
            normalized["authors"] = list(dict.fromkeys(cleaned_authors))
        date_range = scope.get("date_range")
        if date_range is not None:
            if not isinstance(date_range, dict):
                raise ValueError('scope.date_range must be an object like {"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}')
            normalized["date_range"] = {
                str(key): str(value).strip()[:80]
                for key, value in date_range.items()
                if str(key).strip() and str(value).strip()
            }
        topics = scope.get("topics")
        if topics is not None:
            if not isinstance(topics, list) or len(topics) > 30:
                raise ValueError("scope.topics must be a list of at most 30 items")
            normalized["topics"] = [str(topic).strip()[:160] for topic in topics if str(topic).strip()]
        deliverable = scope.get("deliverable")
        if deliverable is not None:
            if not isinstance(deliverable, str) or len(deliverable.strip()) > 500:
                raise ValueError("scope.deliverable must be bounded text")
            normalized["deliverable"] = deliverable.strip()
        return normalized
