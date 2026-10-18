"""Versioned finance profiles with a recoverable Wiki/facts projection.

Commit boundary: profile history + pending projection commit in finance.sqlite
FIRST. Markdown is then atomically replaced, and ONLY this page's rows are
updated in the existing facts index. Those are three separate commits, not a
distributed transaction. A failed projection raises ProfileSyncError carrying
the committed profile/version; callers must not blindly repeat the save.
load_profile retries pending projections without incrementing the version.
Unknown or manually edited Wiki content is never replaced. Other Wiki pages,
company.yaml, and conversational memory_items are never written here.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from policy_impact.company_wiki import loader
from policy_impact.domains.finance.market_catalog import INSTRUMENTS
from policy_impact.domains.finance.store import (
    FinanceConflictError, FinanceStore, canonical_json, database_path,
    sqlite_connection, utc_now,
)


GLOBAL_DEFAULT_SYMBOLS = ("xauusd", "xagusd", "spx", "ixic", "dji")
PAGE_PATH = "wiki/finance_profile.md"


class ProfileVersionConflict(FinanceConflictError):
    """The expected profile version is stale (zero means no saved profile)."""


class ProfileSyncError(RuntimeError):
    def __init__(self, profile: dict):
        self.profile = profile
        self.committed_version = profile["version"]
        super().__init__(f"finance profile version {self.committed_version} committed; "
                         "Wiki/facts sync pending; retry load_profile before saving again")


def default_profile() -> dict:
    return {
        "domain": "finance", "version": 0, "status": "draft",
        "display_name": "\u5168\u7403\u5b8f\u89c2\u91d1\u878d\u89c2\u5bdf",
        "purpose": "\u8ddf\u8e2a\u5168\u7403\u5b8f\u89c2\u3001\u56fd\u9645\u73b0\u8d27\u91d1\u94f6\u548c\u7f8e\u80a1\u4e3b\u8981\u6307\u6570",
        "markets": ["\u5168\u7403\u5b8f\u89c2", "\u56fd\u9645\u73b0\u8d27\u8d35\u91d1\u5c5e", "\u7f8e\u80a1\u4e3b\u8981\u6307\u6570"],
        "symbols": list(GLOBAL_DEFAULT_SYMBOLS),
        "focus_topics": ["\u5b8f\u89c2\u6570\u636e", "\u6307\u6570\u53d8\u5316", "\u673a\u6784\u516c\u5f00\u89c2\u70b9"],
        "report_days": 7, "timezone": "Asia/Shanghai", "language": "zh-CN",
        "quality_policy": "strict", "default_session_mode": "latest_completed", "document_days": 7,
        "cadence": ["daily", "weekly_review"],
        "constraints": ["\u53ea\u89c2\u5bdf\uff0c\u4e0d\u6267\u884c\u4ea4\u6613", "\u4fdd\u7559\u6765\u6e90\u4e0e\u7f3a\u5931\u8bf4\u660e"],
        "holdings": None,
        "preference_basis": "conversation_inference_unconfirmed",
    }


def _string(value, field: str, maximum: int = 2000) -> None:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{field} must be nonempty text without control characters")


def _validate(data: dict) -> None:
    canonical_json(data)
    if data.get("domain") != "finance":
        raise ValueError("domain must be finance")
    if data.get("status") not in ("draft", "confirmed"):
        raise ValueError("status must be draft or confirmed")
    if type(data.get("version")) is not int or data["version"] < 0:
        raise ValueError("version must be a nonnegative integer")
    for field in ("display_name", "purpose", "language", "timezone"):
        _string(data.get(field), field)
    for field in ("markets", "symbols", "focus_topics", "constraints", "cadence"):
        values = data.get(field)
        if (not isinstance(values, list) or len(values) > 100
                or (field in ("markets", "symbols", "cadence") and not values)):
            raise ValueError(f"{field} must be a list with an appropriate number of entries")
        for value in values:
            _string(value, field, 500)
        if len(set(values)) != len(values):
            raise ValueError(f"{field} must not contain duplicates")
    if len(data["symbols"]) > 8:
        raise ValueError("symbols must contain at most eight configured index codes")
    # Identity support is not a promise of an available/verified live provider.
    unknown = set(data["symbols"]) - set(INSTRUMENTS)
    if unknown:
        raise ValueError(f"unsupported finance symbols: {', '.join(sorted(unknown))}")
    if type(data.get("report_days")) is not int or not 1 <= data["report_days"] <= 365:
        raise ValueError("report_days must be an integer between 1 and 365")
    if data.get("quality_policy", "allow_partial") not in ("strict", "allow_partial"):
        raise ValueError("quality_policy must be strict or allow_partial")
    if data.get("default_session_mode", "explicit_period") not in ("latest_completed", "explicit_period"):
        raise ValueError("default_session_mode must be latest_completed or explicit_period")
    if type(data.get("document_days", 7)) is not int or not 1 <= data.get("document_days", 7) <= 365:
        raise ValueError("document_days must be an integer between 1 and 365")
    try:
        ZoneInfo(data["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be an installed IANA timezone") from exc
    if data["holdings"] is not None:
        if not isinstance(data["holdings"], list) or any(
            not isinstance(item, dict) for item in data["holdings"]
        ):
            raise ValueError("holdings must be null or a list of explicitly provided objects")


def _ensure_tables(store: FinanceStore) -> None:
    with store.connection(immediate=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS finance_profiles (
            version INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS finance_profile_projection (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            version INTEGER NOT NULL, target_text TEXT NOT NULL,
            applied_text TEXT, pending INTEGER NOT NULL)""")
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS finance_profiles_{operation.lower()}
                BEFORE {operation} ON finance_profiles BEGIN
                SELECT RAISE(ABORT, 'profile history is immutable'); END""")


def _render(data: dict) -> str:
    draft = data["status"] == "draft"
    meta = {"title": "Finance profile", "domain": "finance", "owner": "user",
            "source": "project_design" if draft else "user_confirmed",
            "last_updated": data["updated_at"], "profile_version": data["version"]}
    lines = [loader.dump_frontmatter(meta), "# Finance profile", "",
             "> DRAFT: suggested preferences, not confirmed personal facts." if draft else
             "> CONFIRMED: user-provided finance observation profile.", ""]
    for field in ("display_name", "purpose", "markets", "symbols", "focus_topics",
                  "report_days", "timezone", "language", "cadence", "constraints", "holdings",
                  "quality_policy", "default_session_mode", "document_days"):
        if field not in data:
            continue
        value = json.dumps({"status": data["status"], "value": data[field]},
                           ensure_ascii=False, sort_keys=True)
        lines.extend([f"## FACT: finance.profile.{field}", f"- value: {value}",
                      f"- source: {meta['source']}", "- importance: 4",
                      f"- confidence: {0.3 if draft else 1.0}",
                      "- policy_relevance: [finance]", ""])
    return "\n".join(lines)


def _projection_page(text: str) -> dict:
    meta, body = loader.parse_frontmatter(text)
    return {"path": PAGE_PATH, "meta": meta, "body": body,
            "facts": loader._extract_fact_blocks(body, meta, PAGE_PATH)}


def _check_projection(company_id: str, target: str, applied: str | None) -> Path:
    path = loader.resolve_wiki_page_path(company_id, PAGE_PATH)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current != target and (applied is None or current != applied):
            raise loader.WikiConflictError("existing finance_profile.md has unmanaged edits")
    pages = [page for page in loader.load_wiki_pages(company_id) if page["path"] != PAGE_PATH]
    loader._validate_unique_facts([*pages, _projection_page(target)])
    return path


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sync_facts(company_id: str, text: str) -> None:
    # Reuse the existing index schema, without instantiating/writing Memory.
    path = database_path(company_id, "company_memory.sqlite")
    page = _projection_page(text)
    with sqlite_connection(path, immediate=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wiki_pages (
            page_id TEXT PRIMARY KEY, path TEXT NOT NULL, domain TEXT DEFAULT '',
            importance REAL DEFAULT 0.5, source_hash TEXT DEFAULT '', updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS company_facts (
            fact_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, value TEXT DEFAULT '',
            importance REAL DEFAULT 0.5, confidence REAL DEFAULT 0.5, source TEXT DEFAULT '',
            tags_json TEXT DEFAULT '[]', updated_at TEXT NOT NULL)""")
        for fact in page["facts"]:
            existing = conn.execute("SELECT source_path FROM company_facts WHERE fact_id = ?",
                                    (fact["fact_id"],)).fetchone()
            if existing and existing[0] != PAGE_PATH:
                raise loader.WikiConflictError(f"fact ID belongs to another page: {fact['fact_id']}")
        conn.execute("DELETE FROM company_facts WHERE source_path = ?", (PAGE_PATH,))
        for fact in page["facts"]:
            conn.execute("INSERT INTO company_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (fact["fact_id"], PAGE_PATH, str(fact["value"]), fact["importance"],
                          fact["confidence"], fact["source"], json.dumps(fact["policy_relevance"]),
                          page["meta"]["last_updated"]))
        source_hash = hashlib.sha256(json.dumps(
            {"body": page["body"], "facts": page["facts"]}, ensure_ascii=False,
            sort_keys=True).encode("utf-8")).hexdigest()
        conn.execute("INSERT OR REPLACE INTO wiki_pages VALUES (?, ?, ?, ?, ?, ?)",
                     (PAGE_PATH, PAGE_PATH, "finance", 0.5, source_hash,
                      page["meta"]["last_updated"]))


def _sync_projection(store: FinanceStore) -> None:
    with loader.wiki_workspace_lock, store.connection(immediate=True) as conn:
        row = conn.execute("SELECT * FROM finance_profile_projection WHERE singleton = 1").fetchone()
        if row is None or not row["pending"]:
            return
        path = _check_projection(store.company_id, row["target_text"], row["applied_text"])
        if not path.exists() or path.read_text(encoding="utf-8") != row["target_text"]:
            _write_markdown(path, row["target_text"])
        _sync_facts(store.company_id, row["target_text"])
        conn.execute("UPDATE finance_profile_projection SET applied_text = target_text, "
                     "pending = 0 WHERE singleton = 1")


def _read_profile(company_id: str) -> tuple[dict | None, bool]:
    path = database_path(company_id)
    if not path.exists():
        return None, False
    # Runtime probes every workspace. Missing/nonfinance profiles must not create
    # directories, tables, journals, or projection state on that read path.
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("BEGIN")
        if conn.execute("SELECT name FROM sqlite_master WHERE name='finance_profiles'").fetchone() is None:
            return None, False
        row = conn.execute("SELECT payload_json FROM finance_profiles ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            return None, False
        projection = conn.execute("SELECT pending FROM finance_profile_projection WHERE singleton=1").fetchone()
        return json.loads(row[0]), bool(projection and projection[0])
    finally:
        conn.close()


def load_profile(company_id: str) -> dict | None:
    data, pending = _read_profile(company_id)
    if pending:
        try:
            _sync_projection(FinanceStore(company_id))
        except Exception as exc:
            raise ProfileSyncError(data) from exc
        data, _pending = _read_profile(company_id)
    return data


def save_profile(company_id: str, payload: dict, expected_version: int | None = None) -> dict:
    """Merge a validated patch; server assigns workspace, timestamp and version.

    expected_version=0 means create only; None permits last-writer-wins patches.
    payload.version is validated but never controls the new version.
    """
    canonical_json(payload)
    if company_id == "finance_observer" and payload.get("holdings") is not None:
        raise ValueError("finance_observer holdings are file-authoritative; edit current_holdings.md")
    if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
        raise ValueError("expected_version must be a nonnegative integer or null")
    if "workspace_id" in payload and payload["workspace_id"] != company_id:
        raise ValueError("workspace_id does not match this workspace")
    if "company_id" in payload and payload["company_id"] != company_id:
        raise ValueError("company_id does not match this workspace")
    store = FinanceStore(company_id)
    _ensure_tables(store)
    load_profile(company_id)
    with loader.wiki_workspace_lock, store.connection(immediate=True) as conn:
        row = conn.execute("SELECT payload_json FROM finance_profiles ORDER BY version DESC LIMIT 1").fetchone()
        previous = json.loads(row[0]) if row else default_profile()
        if expected_version is not None and previous["version"] != expected_version:
            raise ProfileVersionConflict(f"expected version {expected_version}, current {previous['version']}")
        data = {**previous, **payload}
        _validate(data)
        data.update(workspace_id=company_id, version=previous["version"] + 1, updated_at=utc_now())
        target = _render(data)
        projection = conn.execute("SELECT * FROM finance_profile_projection WHERE singleton = 1").fetchone()
        if projection is not None and projection["pending"]:
            raise ProfileSyncError(previous)
        applied = projection["applied_text"] if projection else None
        _check_projection(company_id, target, applied)
        conn.execute("INSERT INTO finance_profiles VALUES (?, ?)", (data["version"], canonical_json(data)))
        conn.execute("INSERT OR REPLACE INTO finance_profile_projection VALUES (1, ?, ?, ?, 1)",
                     (data["version"], target, applied))
    try:
        _sync_projection(store)
    except Exception as exc:
        raise ProfileSyncError(data) from exc
    return data
