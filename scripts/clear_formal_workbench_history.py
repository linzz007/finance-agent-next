"""Clear formal Workbench runtime history while preserving Wiki/profile data.

This is intended for demo handoff: keep the two formal workspaces and their
Wiki facts, but remove old conversations, runs, traces, and generated reports.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPANIES_ROOT = ROOT / "data" / "companies"
FORMAL_WORKSPACES = ("company_001", "agent_self_evolution")

HISTORY_TABLES = (
    "gate_evaluations",
    "agent_steps",
    "staged_assistant_messages",
    "agent_runs",
    "chat_messages",
    "conversation_summaries",
    "chat_sessions",
    "report_messages",
    "report_sessions",
    "tool_calls",
    "run_artifacts",
    "correction_proposals",
    "memory_items",
)

GENERATED_DIRS = ("reports", "runs")


def _safe_child(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    if not resolved.is_relative_to(resolved_parent):
        raise RuntimeError(f"refusing to operate outside workspace: {resolved}")
    return resolved


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] if row else 0)


def clear_workspace(company_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    if company_id not in FORMAL_WORKSPACES:
        raise ValueError(f"refusing to clear non-formal workspace: {company_id}")

    workspace_dir = _safe_child(COMPANIES_ROOT / company_id, COMPANIES_ROOT)
    db_path = _safe_child(workspace_dir / "db" / "company_memory.sqlite", workspace_dir)
    result: dict[str, Any] = {
        "company_id": company_id,
        "db": str(db_path),
        "tables": {},
        "generated_dirs": {},
    }

    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            before = {table: _table_count(conn, table) for table in HISTORY_TABLES}
            if not dry_run:
                conn.execute("BEGIN IMMEDIATE")
                for table in HISTORY_TABLES:
                    try:
                        conn.execute(f"DELETE FROM {table}")
                    except sqlite3.OperationalError:
                        continue
                conn.commit()
            after = {table: _table_count(conn, table) for table in HISTORY_TABLES}
        result["tables"] = {
            table: {"before": before[table], "after": after[table]}
            for table in HISTORY_TABLES
            if before[table] or after[table]
        }

    for dirname in GENERATED_DIRS:
        target = _safe_child(workspace_dir / dirname, workspace_dir)
        count = 0
        if target.exists():
            count = sum(1 for _ in target.rglob("*"))
            if not dry_run:
                shutil.rmtree(target)
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
        result["generated_dirs"][dirname] = {"before_entries": count, "exists_after": target.exists()}

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workspace", choices=[*FORMAL_WORKSPACES, "all"], default="all")
    args = parser.parse_args()

    workspaces = FORMAL_WORKSPACES if args.workspace == "all" else (args.workspace,)
    payload = {
        "dry_run": args.dry_run,
        "root": str(ROOT),
        "workspaces": [clear_workspace(company_id, dry_run=args.dry_run) for company_id in workspaces],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
