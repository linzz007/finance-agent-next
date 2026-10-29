"""Local web projection and lifecycle for the real H2 finance service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from policy_impact.app.h2_archives import H2Archives
from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import TERMINAL, Cancel, DispatchError
from policy_impact.dispatch.finance_agent import ROOT_TOOLS
from policy_impact.dispatch.finance_market_sources import FinanceMarketSources
from policy_impact.dispatch.finance_personal_sources import FinancePersonalSources
from policy_impact.dispatch.finance_public_sources import FinancePublicSources
from policy_impact.dispatch.finance_review_status import collect_review_statuses
from policy_impact.dispatch.finance_tool_contracts import MARKET_TOOLS, PERSONAL_TOOLS, PUBLIC_TOOLS
from policy_impact.dispatch.model_execution import GatewayModelAdapter, profiles_from_registry
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.dispatch.worker import Worker
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.harness.model_config import named_model_profiles

LOG = logging.getLogger(__name__)


class H2Workbench:
    """UI tables are projections, never an alternative scheduler or AgentLoop."""

    def __init__(
        self, service, *, transport_backend: str, default_profile: str, models: list[dict],
        archive_root: Path | None = None,
    ):
        self.service = service
        self.repository = service.repository
        self.transport_backend = transport_backend
        self.default_profile = default_profile
        self.models = models
        self.archives = H2Archives(archive_root) if archive_root is not None else None
        self.tools = sorted(set(ROOT_TOOLS) | PUBLIC_TOOLS | MARKET_TOOLS | PERSONAL_TOOLS)
        self.loops: list[asyncio.Task] = []
        self.last_error = ""
        self._migrate()

    def _migrate(self):
        with self.repository.db.connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS h2_web_sessions (
                session_id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at INTEGER NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS h2_web_runs (
                run_id TEXT PRIMARY KEY REFERENCES h2_runs(run_id),
                session_id TEXT NOT NULL REFERENCES h2_web_sessions(session_id),
                query TEXT NOT NULL, profile TEXT NOT NULL, created_at INTEGER NOT NULL,
                answer TEXT, metadata_json TEXT)""")

    def create_session(self, title="新的研究"):
        session = {
            "session_id": "web_" + uuid4().hex,
            "title": title[:160],
            "created_at": self.repository.clock(),
        }
        with self.repository.db.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO h2_web_sessions VALUES (:session_id,:title,:created_at)", session
            )
        return session

    def _session(self, conn, session_id):
        row = conn.execute(
            "SELECT * FROM h2_web_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise DispatchError("not_found")
        return row

    def sessions(self):
        with self.repository.db.connection() as conn:
            return [
                dict(row)
                for row in conn.execute("""SELECT s.*,
                (SELECT run_id FROM h2_web_runs w WHERE w.session_id=s.session_id
                 ORDER BY created_at DESC,rowid DESC LIMIT 1) AS latest_run_id
                FROM h2_web_sessions s ORDER BY created_at DESC,rowid DESC""")
            ]

    def history(self, session_id):
        with self.repository.db.connection() as conn:
            self._session(conn, session_id)
            rows = conn.execute(
                """SELECT w.*,r.status,r.root_task_id FROM h2_web_runs w
                JOIN h2_runs r ON r.run_id=w.run_id WHERE w.session_id=?
                ORDER BY w.created_at,w.rowid""",
                (session_id,),
            ).fetchall()
        return [{**dict(row), "metadata": {**json.loads(row["metadata_json"] or "{}"),
                                          "review_statuses": self.review_statuses(row["run_id"])}}
                for row in rows]

    def review_statuses(self, run_id):
        with self.repository.db.connection() as conn:
            def read(ref):
                row = conn.execute(
                    "SELECT * FROM h2_artifacts WHERE run_id=? AND artifact_ref=?", (run_id, ref),
                ).fetchone()
                if row is None:
                    raise DispatchError("artifact_unavailable")
                return self.repository.artifacts.read(row).decode("utf-8")
            return collect_review_statuses(conn, run_id, read)

    def submit(self, session_id, query, profile, request_id, *, dispatch_constraints=None):
        query = query.strip()
        if not query or len(query) > 4096 or profile not in self.repository.profiles:
            raise DispatchError("invalid_arguments")
        # A replay reaches run.create's request hash check, not a new model run.
        identity = hashlib.sha256(f"{session_id}:{request_id}".encode()).hexdigest()[:40]
        run_id = "web-h2-" + identity
        with self.repository.db.connection() as conn:
            self._session(conn, session_id)
        handle = self.service.create_run(
            run_id=run_id,
            session_ref=session_id,
            query=query,
            selected_profile=profile,
            allowed_profiles=[profile],
            tools=self.tools,
            dispatch_constraints=dispatch_constraints,
        )
        with self.repository.db.connection(write=True) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO h2_web_runs
                (run_id,session_id,query,profile,created_at) VALUES (?,?,?,?,?)""",
                (run_id, session_id, query, profile, self.repository.clock()),
            )
            conn.execute(
                "UPDATE h2_web_sessions SET title=? WHERE session_id=? AND title='新的研究'",
                (query[:60], session_id),
            )
        return {
            "run_id": run_id,
            "task_id": handle.task_id,
            "thread_id": handle.thread_id,
            "session_id": session_id,
            "model_profile": profile,
            "status": "accepted",
        }

    def view(self, run_id, session_id, *, after_event_id="0"):
        handle = self.service.handle_for(run_id, session_ref=session_id)
        with self.repository.db.connection() as conn:
            self._session(conn, session_id)
            tasks = conn.execute(
                """SELECT t.task_id,t.parent_task_id,th.depth,th.role,
                th.role_version FROM h2_tasks t JOIN h2_threads th ON th.thread_id=t.thread_id
                WHERE t.run_id=? ORDER BY t.created_at,t.rowid""",
                (run_id,),
            ).fetchall()
        caller = self.service.caller(handle)
        status = self.repository.status(
            caller,
            [t["task_id"] for t in tasks],
            after_event_id=after_event_id,
            max_events=200,
        )
        trace = self.repository.scheduling_trace(
            caller, [t["task_id"] for t in tasks], status["events"],
        )
        identities = {t["task_id"]: dict(t) for t in tasks}
        reviews = self.review_statuses(run_id)
        reviews_by_task = {r["task_id"]: r for r in reviews}
        for task in status["tasks"]:
            task.update(identities[task["task_id"]])
            if task["task_id"] in reviews_by_task:
                task["review_status"] = reviews_by_task[task["task_id"]]
        return {
            "run_id": run_id,
            "root_task_id": handle.task_id,
            "model_profile": handle.selected_profile,
            "transport_backend": self.transport_backend,
            "status": status,
            "trace": trace,
            "metrics": self.repository.metrics_snapshot(run_id),
            "review_statuses": reviews,
            "driver_error": self.last_error,
        }

    def cancel(self, run_id, session_id, key):
        handle = self.service.handle_for(run_id, session_ref=session_id)
        return self.repository.cancel(
            self.service.caller(handle),
            Cancel(
                task_id=handle.task_id, idempotency_key=key, reason="cancelled from H2 workbench"
            ),
        )

    def _project_results(self):
        with self.repository.db.connection() as conn:
            rows = conn.execute("""SELECT w.run_id,w.session_id,t.status,t.result_ref
                FROM h2_web_runs w JOIN h2_runs r ON r.run_id=w.run_id
                JOIN h2_tasks t ON t.task_id=r.root_task_id WHERE w.answer IS NULL""").fetchall()
        for row in rows:
            if row["status"] not in TERMINAL:
                continue
            handle = self.service.handle_for(row["run_id"], session_ref=row["session_id"])
            chunks, offset, metadata = [], 0, {"status": row["status"]}
            if row["result_ref"]:
                while True:
                    page = self.repository.result_get(
                        self.service.caller(handle),
                        row["result_ref"],
                        view="full",
                        offset=offset,
                        limit=64000,
                    )
                    chunks.append(page["content"])
                    metadata.update(
                        {k: page[k] for k in ("metadata_status", "warnings", "evidence_refs")}
                    )
                    if not page["has_more"]:
                        break
                    next_offset = page["next_offset"]
                    if type(next_offset) is not int or next_offset <= offset:
                        raise DispatchError("contract_error", "result pagination made no progress")
                    offset = next_offset
                summary = self.repository.result_get(
                    self.service.caller(handle), row["result_ref"], view="summary", limit=64000
                )
                try:
                    envelope = json.loads(summary["content"])
                    metadata["gaps"] = envelope.get("gaps", [])
                except (ValueError, AttributeError):
                    pass
            answer = "".join(chunks)
            if not answer:
                answer = "任务未产生可交付正文。请查看 H2 Trace 中的失败原因与 Attempt 记录。"
            with self.repository.db.connection(write=True) as conn:
                conn.execute(
                    "UPDATE h2_web_runs SET answer=?,metadata_json=? WHERE run_id=? AND answer IS NULL",
                    (answer, json.dumps(metadata, ensure_ascii=False), row["run_id"]),
                )

    async def start(self):
        if self.loops:
            return
        workers = [self.service.worker]
        for index in range(1, self.repository.config.worker_concurrency):
            workers.append(
                Worker(
                    self.repository,
                    self.service.transport,
                    f"finance-web-{index}",
                    executor=self.service._execute,
                    model_adapter=self.service.model_adapter,
                    context_accepts=self.service._context_accepts,
                )
            )
        self.loops = [
            asyncio.create_task(self._maintenance(workers[-1]), name="h2-web-maintenance")
        ]
        self.loops.extend(
            asyncio.create_task(self._consume(worker), name=f"h2-web-worker-{i}")
            for i, worker in enumerate(workers)
        )

    async def _consume(self, worker):
        while True:
            try:
                await worker.run_once(block_ms=500)
            except Exception as exc:
                self.last_error = type(exc).__name__
                LOG.exception("H2 web worker infrastructure failure")
                await asyncio.sleep(2)

    async def _maintenance(self, recovery_worker):
        cursor = "0-0"
        while True:
            try:
                await asyncio.to_thread(self.service.publish_once)
                recovered = await recovery_worker.recover_once(cursor=cursor, idle_ms=60000)
                cursor = recovered["cursor"]
                await asyncio.to_thread(self._project_results)
                self.last_error = ""
            except Exception as exc:
                self.last_error = type(exc).__name__
                LOG.exception("H2 web maintenance failure")
            await asyncio.sleep(0.5)

    async def stop(self):
        for task in self.loops:
            task.cancel()
        await asyncio.gather(*self.loops, return_exceptions=True)
        self.loops = []


def build_h2_workbench(article_root: Path, *, project_root: Path) -> H2Workbench:
    registry = named_model_profiles()
    profiles = profiles_from_registry(list(registry))
    default = os.getenv(
        "FINANCE_H2_MODEL_PROFILE", os.getenv("AGENT_WORKBENCH_MODEL_PROFILE", "deepseek")
    )
    if default not in profiles:
        raise ValueError("unknown H2 default model profile")
    db_path = Path(
        os.getenv("FINANCE_H2_DB", str(project_root / "data/finance/h2-runtime.sqlite"))
    ).resolve()
    records = Path(
        os.getenv("FINANCE_H2_RECORDS", str(project_root.parent / "runtime-records/web-h2"))
    ).resolve()
    repo = DispatchRepository(
        db_path,
        records,
        config=SchedulerConfig(stream_name="finance-agent-next:web:h2:dispatch"),
        profiles=profiles,
        roles={},
    )
    repo.migrate()
    corpus = ArticleCorpus("finance_observer", root=article_root)
    corpus.sync()
    url = os.getenv("H2_REDIS_URL", "").strip()
    if url:
        transport = RedisStreams.from_url(url, repo.config.stream_name)
        transport.client.ping()
        backend = "redis"
    else:
        import fakeredis

        transport = RedisStreams(
            fakeredis.FakeRedis(decode_responses=True), repo.config.stream_name
        )
        backend = "fakeredis-local"
    service = FinanceHarnessV2Service.for_finance_corpus(
        repo,
        transport,
        corpus=corpus,
        public_sources=FinancePublicSources(),
        market_sources=FinanceMarketSources(),
        personal_sources=FinancePersonalSources("finance_observer", article_root),
        model_adapter=GatewayModelAdapter(),
        worker_id="finance-web-root",
    )
    models = [
        {
            "id": name,
            "name": p.display_name,
            "model_id": p.model_id,
            "configured": bool(os.getenv(p.auth_env, "").strip()),
        }
        for name, p in registry.items()
    ]
    archives = Path(os.getenv("FINANCE_H2_ARCHIVES", str(project_root.parent / "runtime-records")))
    return H2Workbench(service, transport_backend=backend, default_profile=default, models=models,
                       archive_root=archives)
