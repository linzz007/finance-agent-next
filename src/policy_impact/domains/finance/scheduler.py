"""Foreground collection worker with durable SQLite jobs and process mutexes.

No scheduler starts on import or from the status/pause API. run_worker must be
kept alive by an external supervisor. Missed intervals coalesce into one current
window, not a backlog. Pausing stops new acquisitions, not one already in flight.

Two dedicated SQLite locks protect the worker lifetime and individual collection
calls (including API calls). They never expire while the owning process lives.
Jobs, attempts and immutable snapshots share finance.sqlite. A successful result
and its attempt/job outcome commit together; a crash before commit leaves an
interrupted attempt that can be retried after the collection lock is released.
At-most-one active acquisition is guaranteed, not exactly-once HTTP: a process
crash after a request but before commit can repeat that read-only request.

Manual collect_once calls also respect pause and failure backoff, and deduplicate
within the current interval/parameter/profile-version bucket. A new profile
version or time bucket is a new request. Partial/empty/error are never success.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from threading import Event
from zoneinfo import ZoneInfo

from policy_impact.domains.finance.profile import load_profile
from policy_impact.domains.finance.request import parse_request
from policy_impact.domains.finance.metrics import compute_market_metrics
from policy_impact.domains.finance.quality import require_market_coverage
from policy_impact.domains.finance.schemas import FinanceSnapshot
from policy_impact.domains.finance.store import FinanceStore, canonical_json, exclusive_lock


JOB_ID = "finance.collection"
DEFAULT_INTERVAL_MINUTES = 60
MAX_BASELINE_LOOKBACK_DAYS = 45  # Same hard cap as acquisition._collect_market.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _store(company_id: str) -> FinanceStore:
    store = FinanceStore(company_id)
    with store.connection(immediate=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS finance_collection_jobs (
            job_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
            interval_seconds REAL NOT NULL, next_run_at REAL NOT NULL,
            last_status TEXT NOT NULL DEFAULT 'idle',
            last_started_at TEXT, last_finished_at TEXT, last_success_at TEXT,
            last_snapshot_id TEXT, last_error TEXT, last_attempt_key TEXT,
            failures INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS finance_collection_attempts (
            job_key TEXT PRIMARY KEY, request_json TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            snapshot_id TEXT, error TEXT, attempt_count INTEGER NOT NULL DEFAULT 1)""")
        conn.execute("INSERT OR IGNORE INTO finance_collection_jobs "
                     "(job_id, enabled, interval_seconds, next_run_at) VALUES (?, 1, ?, ?)",
                     (JOB_ID, DEFAULT_INTERVAL_MINUTES * 60, _now().timestamp()))
    return store


def _job(store: FinanceStore) -> dict:
    with store.connection() as conn:
        return dict(conn.execute("SELECT * FROM finance_collection_jobs WHERE job_id = ?",
                                 (JOB_ID,)).fetchone())


def get_collection_status(company_id: str) -> dict:
    store = _store(company_id)
    with exclusive_lock(company_id, "collection") as free:
        collecting = not free
    with exclusive_lock(company_id, "worker") as free:
        worker_running = not free
    row = _job(store)
    if collecting:
        status = "running"
    elif row["last_status"] == "running":
        status = "interrupted"
    elif not row["enabled"]:
        status = "paused"
    else:
        status = row["last_status"]
    snapshot = store.latest_snapshot() or {}
    sources = snapshot.get("source_statuses", [])
    warnings = snapshot.get("warnings", [])
    source_projection = [{key: (value[:1200] if key == "detail" else value)
                          for key, value in source.items()
                          if key in {"source_id", "status", "count", "detail", "latest_date", "elapsed_ms"}}
                         for source in sources[:64]]
    return {
        "schema_version": "finance.collection_status.v1", "company_id": company_id,
        "job_id": JOB_ID, "enabled": bool(row["enabled"]), "status": status,
        "worker_running": worker_running, "collecting": collecting,
        "interval_minutes": row["interval_seconds"] / 60,
        "next_run_at": _iso(row["next_run_at"]) if row["enabled"] else None,
        "last_status": row["last_status"], "last_started_at": row["last_started_at"],
        "last_finished_at": row["last_finished_at"], "last_success_at": row["last_success_at"],
        "last_snapshot_id": row["last_snapshot_id"], "last_error": row["last_error"],
        "last_attempt_key": row["last_attempt_key"], "consecutive_failures": row["failures"],
        "latest_snapshot_id": snapshot.get("snapshot_id"), "collected_at": snapshot.get("collected_at"),
        "source_statuses": source_projection, "warnings": [warning[:1000] for warning in warnings[:32]],
        "snapshot_projection_truncated": (len(sources) > 64 or len(warnings) > 32
            or any(len(source.get("detail", "")) > 1200 for source in sources)
            or any(len(warning) > 1000 for warning in warnings)),
    }


def set_collection_enabled(company_id: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    store = _store(company_id)
    with store.connection(immediate=True) as conn:
        conn.execute("UPDATE finance_collection_jobs SET next_run_at = "
                     "CASE WHEN enabled = 0 AND ? = 1 THEN MIN(next_run_at, ?) "
                     "ELSE next_run_at END, enabled = ? WHERE job_id = ?",
                     (int(enabled), _now().timestamp(), int(enabled), JOB_ID))
    return get_collection_status(company_id)


def _collect_snapshot(*, start: str, end: str, symbols: list[str],
                      session_mode: str = "explicit_period", as_of: str | None = None,
                      document_period: dict | None = None, include_documents: bool = True,
                      quality_policy: str = "allow_partial") -> dict:
    # Acquisition/dependencies can be installed after profile/status APIs are used.
    from policy_impact.domains.finance.acquisition import collect_finance_snapshot
    return collect_finance_snapshot(start=start, end=end, symbols=symbols,
        session_mode=session_mode, as_of=as_of, document_period=document_period,
        include_documents=include_documents, quality_policy=quality_policy)


def _classify(snapshot: FinanceSnapshot, request: dict) -> tuple[str, str | None]:
    if snapshot.period.model_dump(mode="json") != request["period"]:
        raise ValueError("acquisition returned a different period")
    if snapshot.session_mode != request["session_mode"] or snapshot.quality_policy != request["quality_policy"]:
        raise ValueError("acquisition returned a different session_mode or quality_policy")
    daily = request["session_mode"] == "latest_completed"
    cutoff = datetime.fromisoformat(request["as_of"])
    if (snapshot.as_of is not None and snapshot.as_of != cutoff) or (
        (daily or request["quality_policy"] == "strict") and snapshot.as_of is None
    ):
        raise ValueError("acquisition returned a different or missing as_of cutoff")
    document_period = request["document_period"]
    if snapshot.document_period is not None:
        if snapshot.document_period.model_dump(mode="json") != document_period:
            raise ValueError("acquisition returned a different document_period")
    elif document_period != request["period"]:
        raise ValueError("acquisition omitted the independent document_period")
    observed = [series.symbol for series in snapshot.series]
    if len(set(observed)) != len(observed) or not set(observed) <= set(request["symbols"]):
        raise ValueError("acquisition returned duplicate or unrequested symbols")
    for series in snapshot.series:
        earliest = snapshot.period.start - timedelta(days=MAX_BASELINE_LOOKBACK_DAYS)
        latest = cutoff.astimezone(ZoneInfo(series.timezone)).date() if daily else snapshot.period.end
        if any(not earliest <= bar.date <= latest for bar in series.bars):
            raise ValueError("acquisition returned bars beyond the end or bounded baseline lookback")
    if not request["include_documents"] and snapshot.documents:
        raise ValueError("acquisition returned unrequested documents")
    if any(not document_period["start"] <= doc.published_at.isoformat() <= document_period["end"]
           for doc in snapshot.documents):
        raise ValueError("acquisition returned documents outside the requested document_period")
    usable = {series.symbol for series in snapshot.series
              if any(bar.date == series.session_date if daily else
                     snapshot.period.start <= bar.date <= snapshot.period.end for bar in series.bars)}
    states = [source.status for source in snapshot.source_statuses if source.status != "disabled"]
    if not usable and not snapshot.documents:
        state = "error" if "error" in states else "empty"
        return state, "No usable observations or documents; inspect snapshot source_statuses"
    if request["quality_policy"] == "strict":
        try:
            data = snapshot.model_dump(mode="json")
            require_market_coverage(request, data, compute_market_metrics(data))
        except ValueError as exc:
            return "partial", f"Deterministic market coverage failed: {exc}"[:1500]
        # Collection is not causal analysis/publication. Non-core article coverage and
        # failed earlier provider attempts remain visible without vetoing valid markets.
        return "ok", None
    if usable != set(request["symbols"]) or not states:
        return "partial", "Incomplete symbol/source coverage; inspect snapshot source_statuses"
    core_states = [[source.status for source in snapshot.source_statuses
                    if source.source_id == symbol or source.source_id.endswith(":" + symbol)]
                   for symbol in request["symbols"]]
    if core_states and all(core_states):
        if any("ok" not in states_for_symbol for states_for_symbol in core_states):
            return "partial", "Incomplete market source coverage; inspect snapshot source_statuses"
    elif any(state in {"error", "partial"} for state in states):
        return "partial", "Incomplete symbol/source coverage; inspect snapshot source_statuses"
    if "ok" not in states:
        return "partial", "Data is present without any successful source status"
    return "ok", None


def _finish(conn, *, key: str | None, status: str, now: datetime,
            snapshot_id: str | None = None, error: str | None = None) -> None:
    row = conn.execute("SELECT * FROM finance_collection_jobs WHERE job_id = ?", (JOB_ID,)).fetchone()
    failures = 0 if status == "ok" else row["failures"] + 1
    delay = row["interval_seconds"] if status in {"ok", "partial"} else min(
        row["interval_seconds"], 60 * (2 ** min(max(failures - 1, 0), 6)))
    stamp = now.isoformat()
    if key is not None:
        conn.execute("UPDATE finance_collection_attempts SET status = ?, finished_at = ?, "
                     "snapshot_id = ?, error = ? WHERE job_key = ?",
                     (status, stamp, snapshot_id, error, key))
    conn.execute("""UPDATE finance_collection_jobs SET last_status = ?, last_finished_at = ?,
        last_success_at = CASE WHEN ? = 'ok' THEN ? ELSE last_success_at END,
        last_snapshot_id = COALESCE(?, last_snapshot_id), last_error = ?,
        failures = ?, next_run_at = ? WHERE job_id = ?""",
        (status, stamp, status, stamp, snapshot_id, error, failures,
         now.timestamp() + delay, JOB_ID))


def collect_once(company_id: str) -> dict:
    store = _store(company_id)
    with exclusive_lock(company_id, "collection") as acquired:
        if not acquired:
            return {"status": "busy", "company_id": company_id, "reason": "collection already running"}
        key = None
        try:
            now = _now()
            with store.connection(immediate=True) as conn:
                # Holding the collection lock proves any previous running attempt is orphaned.
                conn.execute("UPDATE finance_collection_attempts SET status = 'interrupted', "
                             "finished_at = ?, error = 'collector exited before commit' "
                             "WHERE status = 'running'", (now.isoformat(),))
                conn.execute("UPDATE finance_collection_jobs SET last_status = 'interrupted', "
                             "last_error = 'collector exited before commit', next_run_at = ? "
                             "WHERE job_id = ? AND last_status = 'running'", (now.timestamp(), JOB_ID))
            job = _job(store)
            if not job["enabled"]:
                return {"status": "paused", "company_id": company_id}
            if job["last_status"] in {"error", "empty"} and job["next_run_at"] > now.timestamp():
                return {"status": "retry_wait", "company_id": company_id,
                        "next_run_at": _iso(job["next_run_at"]), "last_error": job["last_error"]}
            profile = load_profile(company_id)
            if profile is None:
                raise ValueError("no finance profile; save a profile before collecting")
            request = parse_request("", profile, now=now)
            bucket = int(now.timestamp() // job["interval_seconds"])
            # Record the exact cutoff for audit/retries, but retain interval deduplication.
            identity = {field: value for field, value in request.items() if field != "as_of"}
            identity["symbols"] = sorted(identity["symbols"])
            key = hashlib.sha256(canonical_json({"company_id": company_id, "request": identity,
                "bucket": bucket, "interval_seconds": job["interval_seconds"]}).encode()).hexdigest()
            with store.connection(immediate=True) as conn:
                # Pause may have arrived while loading/recovering the profile.
                if not conn.execute("SELECT enabled FROM finance_collection_jobs WHERE job_id = ?",
                                    (JOB_ID,)).fetchone()[0]:
                    return {"status": "paused", "company_id": company_id}
                attempt = conn.execute("SELECT * FROM finance_collection_attempts WHERE job_key = ?",
                                       (key,)).fetchone()
                if attempt is not None and attempt["status"] in {"ok", "partial"}:
                    conn.execute("UPDATE finance_collection_jobs SET next_run_at = MAX(next_run_at, ?) "
                                 "WHERE job_id = ?", ((bucket + 1) * job["interval_seconds"], JOB_ID))
                    return {"status": "deduplicated", "company_id": company_id,
                            "collection_status": attempt["status"], "snapshot_id": attempt["snapshot_id"],
                            "job_key": key}
                conn.execute("""INSERT INTO finance_collection_attempts
                    (job_key, request_json, status, started_at) VALUES (?, ?, 'running', ?)
                    ON CONFLICT(job_key) DO UPDATE SET status = 'running', started_at = excluded.started_at,
                    request_json = excluded.request_json,
                    finished_at = NULL, snapshot_id = NULL, error = NULL, attempt_count = attempt_count + 1""",
                    (key, canonical_json(request), now.isoformat()))
                conn.execute("UPDATE finance_collection_jobs SET last_status = 'running', "
                             "last_started_at = ?, last_attempt_key = ?, last_error = NULL WHERE job_id = ?",
                             (now.isoformat(), key, JOB_ID))
            payload = _collect_snapshot(**request["period"], symbols=request["symbols"],
                **{field: request[field] for field in (
                    "session_mode", "as_of", "document_period", "include_documents", "quality_policy")})
            parsed = FinanceSnapshot.model_validate(payload)
            status, error = _classify(parsed, request)
            with store.connection(immediate=True) as conn:
                # Snapshot and final outcome must roll back together on either failure.
                snapshot_id = store._insert(conn, payload, snapshot=True)
                _finish(conn, key=key, status=status, now=_now(), snapshot_id=snapshot_id, error=error)
            return {"status": status, "company_id": company_id, "snapshot_id": snapshot_id,
                    "job_key": key, "error": error}
        except BaseException as exc:
            status = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "error"
            error = f"{type(exc).__name__}: {exc}"[:1500]
            with store.connection(immediate=True) as conn:
                _finish(conn, key=key, status=status, now=_now(), error=error)
            if not isinstance(exc, Exception):
                raise
            return {"status": "error", "company_id": company_id, "job_key": key, "error": error}


def _interval_seconds(interval_minutes: float) -> float:
    if (type(interval_minutes) not in (int, float) or not math.isfinite(interval_minutes)
            or not 1 <= interval_minutes <= 10080):
        raise ValueError("interval_minutes must be a finite number between 1 and 10080")
    return float(interval_minutes) * 60


def run_worker(company_id: str, interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
               *, once: bool = False, stop_event: Event | None = None) -> dict:
    """Blocking CLI entry point. Never invoke automatically from an API process."""
    seconds = _interval_seconds(interval_minutes)
    store = _store(company_id)
    with exclusive_lock(company_id, "worker") as acquired:
        if not acquired:
            return {"status": "worker_already_running", "company_id": company_id}
        with store.connection(immediate=True) as conn:
            conn.execute("UPDATE finance_collection_jobs SET next_run_at = "
                         "CASE WHEN interval_seconds != ? THEN ? ELSE next_run_at END, "
                         "interval_seconds = ? WHERE job_id = ?",
                         (seconds, _now().timestamp(), seconds, JOB_ID))
        if once:
            return collect_once(company_id)
        stop = stop_event if stop_event is not None else Event()
        while not stop.is_set():
            job = _job(store)
            if job["enabled"] and job["next_run_at"] <= _now().timestamp():
                collect_once(company_id)
            stop.wait(1.0)
        return {"status": "stopped", "company_id": company_id}
