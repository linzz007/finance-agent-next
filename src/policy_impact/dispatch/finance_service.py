"""Explicit opt-in entry for the durable Harness 2.0 finance scheduler.

The service owns composition of the already-tested repository, transport,
worker and root bridge.  It deliberately does not contain financial prompts
or article-selection rules.  A caller must provide the domain step and child
executor, which keeps the legacy synchronous AgentLoop from being silently
treated as a recoverable H2 implementation.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .contracts import CallerContext, DispatchError, Submission
from .finance_root import FinanceRootExecutor, FinanceStepFunction
from .transport import Dispatcher, RedisStreams
from .worker import Continue, Suspend, Worker

ChildExecutor = Callable[[Any, Any], Awaitable[Submission] | Submission]


@dataclass(frozen=True)
class FinanceRunHandle:
    """Public identifiers returned when an explicit H2 run is created."""

    run_id: str
    session_ref: str
    thread_id: str
    task_id: str
    attempt_id: str
    selected_profile: str


class FinanceHarnessV2Service:
    """Compose one H2 finance run without changing the default 1.0 path."""

    def __init__(
        self,
        repository,
        transport: RedisStreams,
        *,
        finance_step: FinanceStepFunction,
        child_executor: ChildExecutor,
        model_adapter=None,
        worker_id: str = "finance-h2",
        rule_loader=None,
    ) -> None:
        if not callable(finance_step) or not callable(child_executor):
            raise TypeError("finance_step and child_executor are required callables")
        self.repository = repository
        self.transport = transport
        self.dispatcher = Dispatcher(repository, transport)
        repository.evidence_validator = self._validate_evidence_refs
        self.root_executor = FinanceRootExecutor(finance_step)
        self.child_executor = child_executor
        self.model_adapter = model_adapter
        self.worker_id = worker_id
        self.rule_loader = rule_loader
        self.worker = Worker(
            repository,
            transport,
            worker_id,
            executor=self._execute,
            model_adapter=model_adapter,
            context_accepts=self._context_accepts,
        )

    @classmethod
    def for_finance_corpus(
        cls,
        repository,
        transport: RedisStreams,
        *,
        corpus,
        public_sources=None,
        market_sources=None,
        personal_sources=None,
        model_adapter=None,
        worker_id: str = "finance-h2",
    ):
        """Build the explicit model-driven finance path for one article corpus."""
        from .finance_agent import ROOT_TOOLS, FinanceH2ChildExecutor, FinanceH2Domain
        from .finance_rules import ROLE_VERSIONS, load_rules
        from .finance_tool_contracts import MARKET_TOOLS, PERSONAL_TOOLS, PUBLIC_TOOLS

        # These roles are executable by this trusted domain adapter. They are
        # still recorded in the permission snapshot and cannot be invented by
        # a model at runtime.
        repository.roles.setdefault("finance_tool@1.0.0", [])
        tools = [*ROOT_TOOLS, *(sorted(PUBLIC_TOOLS) if public_sources is not None else []),
                 *(sorted(MARKET_TOOLS) if market_sources is not None else []),
                 *(sorted(PERSONAL_TOOLS) if personal_sources is not None else [])]
        for role, versions in ROLE_VERSIONS.items():
            for version in versions:
                repository.roles.setdefault(f"{role}@{version}", tools.copy())
        domain = FinanceH2Domain(corpus, public_sources=public_sources, market_sources=market_sources,
                                 personal_sources=personal_sources)
        child = FinanceH2ChildExecutor(corpus, public_sources=public_sources, market_sources=market_sources,
                                      personal_sources=personal_sources)
        service = cls(
            repository,
            transport,
            finance_step=domain,
            child_executor=child,
            model_adapter=model_adapter,
            worker_id=worker_id,
            rule_loader=load_rules,
        )
        service.profile_tools = tools
        return service

    def create_run(
        self,
        *,
        run_id: str,
        session_ref: str,
        query: str,
        selected_profile: str,
        allowed_profiles: list[str],
        tools: list[str],
        research_clock: dict | None = None,
        dispatch_constraints: dict | None = None,
    ) -> FinanceRunHandle:
        """Create a run with an explicit model/profile and permission snapshot."""
        with self.repository.db.connection() as connection:
            exists = connection.execute("SELECT 1 FROM h2_runs WHERE run_id=?", (run_id,)).fetchone()
        rules = self.rule_loader().snapshot() if self.rule_loader is not None and not exists else None
        if rules is not None:
            self._register_profiles(rules)
        receipt = self.repository.create_run(
            run_id=run_id,
            session_ref=session_ref,
            query=query,
            selected_profile=selected_profile,
            allowed_profiles=allowed_profiles,
            tools=tools,
            **({"research_clock": research_clock} if research_clock is not None else {}),
            **({"dispatch_constraints": dispatch_constraints} if dispatch_constraints is not None else {}),
            **({"instruction_rules": rules} if rules is not None else {}),
        )
        data = receipt["data"]
        return FinanceRunHandle(
            run_id=data["run_id"],
            session_ref=session_ref,
            thread_id=data["thread_id"],
            task_id=data["task_id"],
            attempt_id=data["attempt_id"],
            selected_profile=selected_profile,
        )

    def caller(self, handle: FinanceRunHandle) -> CallerContext:
        return CallerContext(
            handle.run_id,
            handle.thread_id,
            handle.task_id,
            trusted_user=True,
        )

    def handle_for(self, run_id: str, *, session_ref: str) -> FinanceRunHandle:
        """Load a run handle only after its session binding has been checked."""
        with self.repository.db.connection() as connection:
            run = connection.execute(
                "SELECT * FROM h2_runs WHERE run_id=? AND session_ref=?",
                (run_id, session_ref),
            ).fetchone()
            if run is None:
                raise DispatchError("not_found")
            policy = connection.execute(
                "SELECT payload_json FROM h2_snapshots WHERE snapshot_id=?",
                (run["model_policy_ref"],),
            ).fetchone()
            if policy is None:
                raise DispatchError("contract_error", "run model policy snapshot is missing")
            selected_profile = json.loads(policy[0])["selected_profile"]
            task = connection.execute(
                "SELECT current_attempt_id FROM h2_tasks WHERE run_id=? AND task_id=?",
                (run_id, run["root_task_id"]),
            ).fetchone()
            if task is None:
                raise DispatchError("contract_error", "run root task is missing")
        return FinanceRunHandle(
            run_id=run_id,
            session_ref=session_ref,
            thread_id=run["root_thread_id"],
            task_id=run["root_task_id"],
            attempt_id=task["current_attempt_id"] or "",
            selected_profile=selected_profile,
        )

    def status(self, handle: FinanceRunHandle, *, after_event_id="0", max_events=100):
        return self.repository.status(
            self.caller(handle),
            [handle.task_id],
            after_event_id=after_event_id,
            max_events=max_events,
        )

    def publish_once(self, *, limit=32) -> int:
        """Publish persisted outbox rows; Redis remains notification-only."""
        return self.dispatcher.publish_batch(limit=limit)

    async def worker_once(self, *, block_ms=0) -> int:
        return await self.worker.run_once(block_ms=block_ms)

    async def recover_once(self, *, cursor="0-0", idle_ms=0):
        return await self.worker.recover_once(cursor=cursor, idle_ms=idle_ms)

    async def run_until_terminal(
        self,
        handle: FinanceRunHandle,
        *,
        max_cycles: int = 100,
    ) -> dict:
        """Bounded local acceptance driver, not a production polling loop."""
        if type(max_cycles) is not int or not 1 <= max_cycles <= 1000:
            raise ValueError("max_cycles must be between 1 and 1000")
        for _ in range(max_cycles):
            self.publish_once()
            await self.worker_once(block_ms=0)
            status = self.status(handle)
            task = status["tasks"][0]
            if task["status"] in {"succeeded", "partial", "failed", "cancelled", "timed_out"}:
                return status
        raise TimeoutError("H2 finance run did not reach a terminal state within max_cycles")

    def _context_accepts(self, data):
        from policy_impact.harness.context_manifest import estimate_tokens

        from .model_execution import GatewayModelAdapter

        adapter = self.model_adapter if self.model_adapter is not None else GatewayModelAdapter
        try:
            limit = adapter.settings(data["model"]).input_limit()
        except (DispatchError, KeyError, TypeError):
            return False
        # The full frozen asset bundle is internal; only selected instructions enter model input.
        brief = {key: value for key, value in data.get("brief", {}).items() if key != "instruction_rules"}
        return estimate_tokens({**data, "brief": brief}) <= limit

    async def _execute(self, work, capabilities):
        self._register_profiles(getattr(capabilities, "instruction_rules", None))
        depth = work.brief.get("depth")
        if depth == 0:
            return await self.root_executor(work, capabilities)
        if depth not in {1, 2}:
            raise DispatchError("depth_exceeded")
        result = self.child_executor(work, capabilities)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, (Submission, Continue, Suspend)):
            raise DispatchError("contract_error", "finance child executor returned an invalid result")
        return result

    def _register_profiles(self, snapshot):
        if snapshot is None or not hasattr(self, "profile_tools"):
            return
        from .finance_rules import FinanceRules

        for role, profile in FinanceRules.from_snapshot(snapshot).manifest["roles"].items():
            for version in profile["versions"]:
                self.repository.roles.setdefault(f"{role}@{version}", self.profile_tools.copy())

    def _validate_evidence_refs(self, run_id: str, refs: list[dict]) -> bool:
        """Accept only artifact references already registered in this run."""
        values = []
        for item in refs:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict) and isinstance(item.get("evidence_ref"), str):
                values.append(item["evidence_ref"])
            else:
                return False
        if not values:
            return True
        with self.repository.db.connection() as connection:
            rows = connection.execute(
                "SELECT artifact_ref FROM h2_artifacts WHERE run_id=? AND artifact_ref IN ({})".format(
                    ",".join("?" for _ in values)
                ),
                (run_id, *values),
            ).fetchall()
            results = connection.execute(
                "SELECT result_ref FROM h2_results WHERE run_id=? AND disposition='accepted' "
                "AND result_ref IN ({})".format(",".join("?" for _ in values)),
                (run_id, *values),
            ).fetchall()
        return {row[0] for row in [*rows, *results]} == set(values)


__all__ = ["FinanceHarnessV2Service", "FinanceRunHandle"]
