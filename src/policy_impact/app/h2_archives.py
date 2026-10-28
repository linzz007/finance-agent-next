"""Read exported acceptance Runs without attaching them to a live scheduler."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.storage import SAFE_ID, ArtifactStore


class H2Archives:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved != path.absolute():
            raise DispatchError("not_found")
        return resolved

    def _json(self, path):
        try:
            return json.loads(self._path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DispatchError("artifact_unavailable") from exc

    def _entries(self):
        for path in self.root.glob("*/cases/*/record.json"):
            try:
                record = self._json(path)
                run_id = record["handle"]["run_id"]
                if not all(isinstance(record.get(key), str) for key in ("case", "query", "model_profile")):
                    continue
                if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
                    continue
                case = path.parent
                trace = case / "records" / "runs" / run_id / "scheduling-trace.json"
                if not self._path(trace).is_file() or not self._path(case / "h2.sqlite").is_file():
                    continue
                identity = hashlib.sha256(case.relative_to(self.root).as_posix().encode()).hexdigest()[:24]
                yield identity, case, record
            except (DispatchError, KeyError, TypeError):
                continue

    def catalog(self):
        rows = [{
            "archive_id": identity, "batch": case.parent.parent.name,
            "case": record["case"], "query": record["query"],
            "run_id": record["handle"]["run_id"], "status": record.get("status", "unknown"),
            "model_profile": record.get("model_profile", "unknown"),
            "started_at": record.get("started_at", ""),
        } for identity, case, record in self._entries()]
        return sorted(rows, key=lambda row: (row["started_at"], row["archive_id"]), reverse=True)

    def _entry(self, identity):
        for key, case, record in self._entries():
            if key == identity:
                return case, record
        raise DispatchError("not_found")

    @contextmanager
    def _db(self, case):
        path = self._path(case / "h2.sqlite")
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise DispatchError("artifact_unavailable") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        except sqlite3.Error as exc:
            raise DispatchError("artifact_unavailable") from exc
        finally:
            connection.close()

    def _artifact(self, case, connection, run_id, ref):
        row = connection.execute(
            "SELECT * FROM h2_artifacts WHERE run_id=? AND artifact_ref=?", (run_id, ref),
        ).fetchone()
        if row is None:
            raise DispatchError("not_found")
        try:
            self._path(case / "records" / "runs" / run_id / "artifacts" / ref)
            return ArtifactStore(case / "records").read(row).decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise DispatchError("artifact_unavailable") from exc

    def artifact(self, identity, ref, *, offset=0, limit=16000):
        case, record = self._entry(identity)
        with self._db(case) as connection:
            text = self._artifact(case, connection, record["handle"]["run_id"], ref)
        return {"content": text[offset:offset + limit], "has_more": offset + limit < len(text),
                "next_offset": min(offset + limit, len(text))}

    def view(self, identity, *, after_event_id="0"):
        case, record = self._entry(identity)
        run_id = record["handle"]["run_id"]
        trace = self._json(case / "records" / "runs" / run_id / "scheduling-trace.json")
        if trace.get("trace_id") != run_id:
            raise DispatchError("artifact_unavailable")
        events = [event for event in trace["events"] if int(event["event_id"]) > int(after_event_id)]
        page = events[:200]
        spans = trace["spans"]
        tasks = []
        with self._db(case) as connection:
            rows = connection.execute("""SELECT t.*,th.role,th.role_version,th.depth,
                b.self_call_limit FROM h2_tasks t JOIN h2_threads th ON th.thread_id=t.thread_id
                JOIN h2_budgets b ON b.budget_id=t.budget_ref
                WHERE t.run_id=? ORDER BY t.created_at,t.rowid""", (run_id,)).fetchall()
            for row in rows:
                calls = sum(span["span_type"] == "model_call" and span["task_id"] == row["task_id"]
                            for span in spans)
                result = connection.execute(
                    "SELECT full_ref FROM h2_results WHERE run_id=? AND result_ref=?",
                    (run_id, row["result_ref"]),
                ).fetchone()
                tasks.append({
                    **{key: row[key] for key in (
                        "task_id", "thread_id", "parent_task_id", "role", "role_version", "depth",
                        "status", "objective", "result_ref",
                    )},
                    "result_artifact_ref": result["full_ref"] if result else None,
                    "budget": {"consumed": calls, "remaining": max(0, row["self_call_limit"] - calls)},
                    "attempts": [{**span, "attempt_id": span["span_id"]} for span in spans
                                 if span["span_type"] == "attempt" and span["task_id"] == row["task_id"]],
                })
            root = next((task for task in tasks if task["task_id"] == record["handle"]["task_id"]), None)
            if root is None:
                raise DispatchError("artifact_unavailable")
            answer = self._artifact(case, connection, run_id, root["result_artifact_ref"]) \
                if root["result_artifact_ref"] else "本次运行未产生最终答案。"
        config = record["scheduler_config"]
        budget = {"root": config["root_task_calls"], "child": config["child_task_calls"],
                  "run": config["root_calls"], "minutes": config["run_ms"] // 60000}
        metadata = record.get("finance_delivery") or {}
        return {
            "archive_id": identity, "batch": case.parent.parent.name, "case": record["case"],
            "readonly": True,
            "run": {"run_id": run_id, "query": record["query"], "profile": record["model_profile"],
                    "status": root["status"], "answer": answer, "metadata": metadata},
            "view": {
                "run_id": run_id, "root_task_id": root["task_id"],
                "model_profile": record["model_profile"], "transport_backend": record["transport_backend"],
                "readonly": True, "budget": budget,
                "status": {"tasks": tasks, "events": page, "has_more": len(events) > len(page),
                           "next_event_id": page[-1]["event_id"] if page else after_event_id},
                "trace": {**trace, "events": page}, "metrics": record["metrics"], "driver_error": "",
            },
        }
