# Runtime Kernel And Skill Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 `ExecutionMode`、可执行 `SkillManifest`、`ExecutorRegistry` 和轻量 `TurnCoordinator` 替换 `AgentRuntime` 内的 Skill 分支，同时把 Agent Loop 和 Subagent 预算、动作与停止语义固化为类型契约。

**Architecture:** 保留 `AgentRuntime` 作为兼容入口，但将生命周期控制迁移到 `runtime/turn_coordinator.py`。现有政策、新闻、调研、Wiki 和普通对话实现先通过独立 Executor 接入；Coordinator 只解析、编排和持久化，不包含业务分支。

**Tech Stack:** Python 3.10+、Pydantic v2、dataclasses、Protocol、SQLite、pytest、FastAPI 兼容层。

## Global Constraints

- 固定模型为 `deepseek-v4-flash`；未知模型和无鉴权必须拒绝执行。
- 保持现有 Slash 命令、同步 Message API、会话数据和 178 个基线测试兼容。
- Skill 接入不能修改 `TurnCoordinator` 的条件分支。
- Run 必须记录实际 `ExecutionMode`，不得统一伪装成 `subagent_driven`。
- Agent Loop 只接受 `final`、`tool_call`、`delegate`、`request_approval`。
- 不展示或持久化隐藏思维链。
- 每个行为变化先写失败测试，再写最小实现，再跑相关测试与全量回归。

---

### Task 1: Freeze Runtime Contracts

**Files:**
- Create: `src/policy_impact/runtime/__init__.py`
- Create: `src/policy_impact/runtime/execution_mode.py`
- Create: `src/policy_impact/runtime/run_status.py`
- Create: `src/policy_impact/runtime/result.py`
- Create: `src/policy_impact/runtime/execution_context.py`
- Create: `src/policy_impact/runtime/hooks.py`
- Create: `src/policy_impact/runtime/gates.py`
- Test: `tests/test_runtime_contracts.py`

**Interfaces:**
- Consumes: existing `PolicyMemoryStore`, `SkillDefinition`, and dict-shaped skill results.
- Produces: `ExecutionMode`, `SkillExecutionContext`, `SkillResult`, `SkillExecutor`, shared Hook contracts, and fail-closed Gate contracts used by all later runtime tasks.

- [ ] **Step 1: Write failing contract tests**

```python
import pytest

from policy_impact.runtime.execution_context import SkillExecutionContext, SkillExecutor
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.gates import GateDefinition, GateDecision, GateRunner, GateRegistry
from policy_impact.runtime.hooks import HookDecision, HookManager
from policy_impact.runtime.result import SkillResult
from policy_impact.runtime.run_status import RunStatus, require_run_transition
from pydantic import BaseModel


class GatePayload(BaseModel):
    evidence_refs: tuple[str, ...] = ()


class RecordingGateDecisionSink:
    def __init__(self):
        self.records = []

    def record(self, run_id, span_id, decision):
        self.records.append((run_id, span_id, decision))
        return f"gate-record-{len(self.records)}"


def test_execution_mode_values_are_stable():
    assert [item.value for item in ExecutionMode] == [
        "deterministic",
        "model_once",
        "agent_loop",
        "workflow",
        "subagent_workflow",
    ]


def test_run_status_and_recovery_transitions_are_frozen():
    assert {item.value for item in RunStatus} == {
        "created",
        "running",
        "awaiting_approval",
        "resuming",
        "done",
        "failed",
        "cancelled",
        "interrupted_recoverable",
        "interrupted_failed",
    }
    require_run_transition(RunStatus.RUNNING, RunStatus.INTERRUPTED_RECOVERABLE)
    with pytest.raises(ValueError, match="illegal Run transition"):
        require_run_transition(RunStatus.DONE, RunStatus.RUNNING)


def test_skill_result_rejects_unknown_execution_mode():
    try:
        SkillResult(
            answer="x",
            actual_execution_mode="pretend",
            stop_reason="success",
        )
    except ValueError:
        return
    raise AssertionError("unknown execution mode must fail closed")


def test_skill_executor_protocol_is_runtime_checkable():
    class Executor:
        def execute(self, context: SkillExecutionContext) -> SkillResult:
            return SkillResult(
                answer=context.message,
                actual_execution_mode=ExecutionMode.MODEL_ONCE,
                stop_reason="success",
            )

    assert isinstance(Executor(), SkillExecutor)


def test_hook_manager_preserves_registration_order():
    hooks = HookManager()
    hooks.register("BeforeTurn", lambda payload: HookDecision(action="allow", reason="first"))
    hooks.register("BeforeTurn", lambda payload: HookDecision(action="emit_event", reason="second"))
    assert [item.reason for item in hooks.run("BeforeTurn", {})] == ["first", "second"]


def test_unknown_gate_fails_closed():
    sink = RecordingGateDecisionSink()
    decision = GateRunner(GateRegistry(), sink).evaluate(
        run_id="r1", span_id="s1", gate_id="missing", version="1.0.0", payload={}
    )
    assert decision.decision == "block"
    assert decision.reason == "unknown gate: missing@1.0.0"
    assert sink.records == [("r1", "s1", decision)]


def test_non_repairable_gate_cannot_return_repair():
    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="evidence_binding",
            version="1.0.0",
            description="requires evidence refs",
            input_schema=GatePayload,
            hard_constraint=True,
            repairable=False,
        ),
        lambda payload: GateDecision(
            gate_id="evidence_binding",
            gate_version="1.0.0",
            decision="repair",
            reason="attach evidence",
        ),
    )
    sink = RecordingGateDecisionSink()
    decision = GateRunner(registry, sink).evaluate(
        run_id="r1",
        span_id="s1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload={},
    )
    assert decision.decision == "block"
    assert len(sink.records) == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_runtime_contracts.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'policy_impact.runtime'`.

- [ ] **Step 3: Implement the contracts**

```python
# src/policy_impact/runtime/execution_mode.py
from enum import Enum


class ExecutionMode(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ONCE = "model_once"
    AGENT_LOOP = "agent_loop"
    WORKFLOW = "workflow"
    SUBAGENT_WORKFLOW = "subagent_workflow"
```

```python
# src/policy_impact/runtime/run_status.py
from enum import Enum


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RESUMING = "resuming"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED_RECOVERABLE = "interrupted_recoverable"
    INTERRUPTED_FAILED = "interrupted_failed"


LEGAL_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED_FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.AWAITING_APPROVAL, RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED_RECOVERABLE, RunStatus.INTERRUPTED_FAILED}),
    RunStatus.AWAITING_APPROVAL: frozenset({RunStatus.RESUMING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RESUMING: frozenset({RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL, RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED_RECOVERABLE, RunStatus.INTERRUPTED_FAILED}),
    RunStatus.INTERRUPTED_RECOVERABLE: frozenset({RunStatus.RESUMING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.DONE: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.INTERRUPTED_FAILED: frozenset(),
}


def require_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in LEGAL_RUN_TRANSITIONS[current]:
        raise ValueError(f"illegal Run transition: {current.value} -> {target.value}")
```

```python
# src/policy_impact/runtime/execution_context.py
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from policy_impact.runtime.result import SkillResult


@dataclass(frozen=True)
class SkillExecutionContext:
    run_id: str
    session_id: str
    company_id: str
    message: str
    model_id: str
    context_manifest: dict[str, Any]
    prepared_context: dict[str, Any]
    services: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SkillExecutor(Protocol):
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise NotImplementedError
```

```python
# src/policy_impact/runtime/hooks.py
from collections.abc import Callable
from typing import Literal
from pydantic import BaseModel, Field


class HookDecision(BaseModel):
    action: Literal["allow", "deny", "ask", "patch_context", "emit_event"]
    reason: str = ""
    context_patch: dict = Field(default_factory=dict)
    event_attributes: dict = Field(default_factory=dict)


class HookManager:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], HookDecision]]] = {}

    def register(self, hook_name: str, handler: Callable[[dict], HookDecision]) -> None:
        self._handlers.setdefault(hook_name, []).append(handler)

    def run(self, hook_name: str, payload: dict) -> tuple[HookDecision, ...]:
        return tuple(handler(payload) for handler in self._handlers.get(hook_name, ()))
```

```python
# src/policy_impact/runtime/gates.py
from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class GateDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    gate_id: str
    version: str
    description: str
    input_schema: type[BaseModel]
    hard_constraint: bool
    repairable: bool


class GateDecision(BaseModel):
    gate_id: str
    gate_version: str
    decision: Literal["pass", "block", "repair"]
    reason: str
    input_refs: tuple[str, ...] = ()
    output_ref: str | None = None
    repair_action: dict[str, Any] | None = None


GateEvaluator = Callable[[BaseModel], GateDecision]


class GateRegistry:
    def __init__(self) -> None:
        self._gates: dict[tuple[str, str], tuple[GateDefinition, GateEvaluator]] = {}

    def register(self, definition: GateDefinition, evaluator: GateEvaluator) -> None:
        key = (definition.gate_id, definition.version)
        if key in self._gates:
            raise ValueError(f"duplicate gate: {definition.gate_id}@{definition.version}")
        self._gates[key] = (definition, evaluator)

    def definitions(self) -> tuple[GateDefinition, ...]:
        return tuple(item[0] for _, item in sorted(self._gates.items()))

    def _evaluate(self, gate_id: str, version: str, payload: dict) -> GateDecision:
        registered = self._gates.get((gate_id, version))
        if registered is None:
            return GateDecision(
                gate_id=gate_id,
                gate_version=version,
                decision="block",
                reason=f"unknown gate: {gate_id}@{version}",
            )
        definition, evaluator = registered
        try:
            validated = definition.input_schema.model_validate(payload)
            decision = evaluator(validated)
        except Exception as exc:
            return GateDecision(
                gate_id=gate_id,
                gate_version=version,
                decision="block",
                reason=f"gate evaluation failed: {type(exc).__name__}",
            )
        if decision.gate_id != gate_id or decision.gate_version != version:
            return GateDecision(
                gate_id=gate_id,
                gate_version=version,
                decision="block",
                reason="gate identity mismatch",
            )
        if decision.decision == "repair" and not definition.repairable:
            return decision.model_copy(
                update={"decision": "block", "reason": "gate is not repairable"}
            )
        return decision


@runtime_checkable
class GateDecisionSink(Protocol):
    def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
        raise NotImplementedError


class GateRunner:
    def __init__(self, registry: GateRegistry, sink: GateDecisionSink) -> None:
        self.registry = registry
        self.sink = sink

    def evaluate(
        self,
        *,
        run_id: str,
        span_id: str,
        gate_id: str,
        version: str,
        payload: dict,
    ) -> GateDecision:
        decision = self.registry._evaluate(gate_id, version, payload)
        self.sink.record(run_id, span_id, decision)
        return decision
```

`GateRunner` has no no-op/default sink. A sink persistence error propagates and aborts the guarded business action; the system must never continue after an unrecorded Gate decision.

```python
# src/policy_impact/runtime/result.py
from pydantic import BaseModel, Field

from policy_impact.runtime.execution_mode import ExecutionMode


class CitationRef(BaseModel):
    citation_type: str = ""
    title: str = ""
    url: str = ""
    source_ref: str = ""


class SkillResult(BaseModel):
    answer: str
    actual_execution_mode: ExecutionMode
    stop_reason: str
    citations: list[CitationRef] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    memory_proposals: list[str] = Field(default_factory=list)
    gate_decision_refs: list[str] = Field(default_factory=list)
    resumable: bool = False
```

`src/policy_impact/runtime/__init__.py` re-exports all frozen Runtime, Hook and Gate public contracts.

- [ ] **Step 4: Run the contract tests and full regression**

Run: `py -3 -m pytest tests/test_runtime_contracts.py -q`

Expected: `7 passed`.

Run: `py -3 -m pytest -q`

Expected: all baseline tests plus 7 new tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/policy_impact/runtime tests/test_runtime_contracts.py
git commit -m "feat: define runtime execution contracts"
```

### Task 2: Replace Metadata-Only Skills With Executable Manifests

**Files:**
- Create: `src/policy_impact/skills/__init__.py`
- Create: `src/policy_impact/skills/schemas.py`
- Create: `src/policy_impact/skills/manifest.py`
- Create: `src/policy_impact/skills/registry.py`
- Modify: `src/policy_impact/harness/skills.py:1-191`
- Modify: `tests/test_skill_registry.py`

**Interfaces:**
- Consumes: `ExecutionMode` from Task 1.
- Produces: `SkillManifest`, `SkillRegistry.get_manifest()`, and typed default skill schemas.

- [ ] **Step 1: Add failing manifest tests**

```python
from pydantic import ValidationError

from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.skills.registry import SkillRegistry


def test_default_manifests_have_executable_contracts():
    registry = SkillRegistry()
    policy = registry.get_manifest("policy_weekly_impact")
    assert policy.execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert policy.executor_id == "policy_weekly_impact"
    assert policy.max_steps == 24
    assert policy.timeout_seconds == 180
    assert "skeptic" in policy.allowed_subagents


def test_manifest_rejects_non_positive_budgets():
    manifest = SkillRegistry().get_manifest("general_chat")
    payload = {**manifest.model_dump(), "max_model_calls": 0}
    try:
        type(manifest).model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("max_model_calls=0 must be rejected")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_skill_registry.py -q`

Expected: import fails for `policy_impact.skills.registry`.

- [ ] **Step 3: Implement schemas and manifest**

```python
# src/policy_impact/skills/schemas.py
from pydantic import BaseModel, ConfigDict, Field


class SkillInput(BaseModel):
    message: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SkillOutput(BaseModel):
    answer: str
    citations: list[dict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


class GeneralChatOutput(SkillOutput):
    model_config = ConfigDict(json_schema_extra={"artifact_types": ["message"]})


class PolicyImpactOutput(SkillOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "artifact_types": ["markdown_report", "html_report", "run_artifact"]
        }
    )


class NewsReportOutput(PolicyImpactOutput):
    pass


class ResearchReportOutput(SkillOutput):
    model_config = ConfigDict(json_schema_extra={"artifact_types": ["markdown_report"]})


class WikiBlueprintOutput(SkillOutput):
    model_config = ConfigDict(json_schema_extra={"artifact_types": ["json_blueprint"]})
```

```python
# src/policy_impact/skills/manifest.py
from pydantic import BaseModel, ConfigDict, Field

from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.skills.schemas import SkillInput, SkillOutput


class SkillManifest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    id: str
    version: str
    name: str
    description: str
    execution_mode: ExecutionMode
    executor_id: str
    input_schema: type[BaseModel] = SkillInput
    output_schema: type[BaseModel] = SkillOutput
    allowed_tools: tuple[str, ...] = ()
    allowed_subagents: tuple[str, ...] = ()
    context_policy_id: str
    memory_policy_id: str
    permission_policy_id: str
    max_steps: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
```

Implement `src/policy_impact/skills/registry.py` with the five current manifests and these exact modes:

```python
DEFAULT_EXECUTION_MODES = {
    "general_chat": ExecutionMode.AGENT_LOOP,
    "policy_weekly_impact": ExecutionMode.SUBAGENT_WORKFLOW,
    "recent_news_report": ExecutionMode.WORKFLOW,
    "research_report": ExecutionMode.AGENT_LOOP,
    "company_wiki_blueprint": ExecutionMode.DETERMINISTIC,
}
```

The registry must expose `list_manifests()`, `get_manifest(skill_id)`, `register_for_test(manifest)`, and the current deterministic `resolve_skill()` behavior. `harness/skills.py` becomes a compatibility re-export with `SkillDefinition = SkillManifest` only after all call sites use manifest field names.

- [ ] **Step 4: Update current call sites without changing routing behavior**

Replace:

```python
skill.mode
skill.tool_policy
skill.artifact_types
skill.subagents
```

with:

```python
skill.execution_mode.value
skill.allowed_tools
skill.output_schema.model_json_schema().get("artifact_types", [])
skill.allowed_subagents
```

Assign the corresponding output class to each default Manifest. Where the UI needs artifact types, read `skill.output_schema.model_json_schema().get("artifact_types", [])` rather than retaining a second source of truth. Keep existing JSON response keys during this plan.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_skill_registry.py tests/test_agent_runtime.py tests/test_chat_workbench_service.py -q`

Expected: all selected tests pass with unchanged Slash routing.

Run: `py -3 -m pytest -q`

Expected: full suite passes.

```powershell
git add src/policy_impact/skills src/policy_impact/harness/skills.py src/policy_impact/harness/agent_runtime.py src/policy_impact/app/chat_workbench_service.py tests/test_skill_registry.py
git commit -m "feat: add executable skill manifests"
```

### Task 3: Add ExecutorRegistry Without Skill Branches

**Files:**
- Create: `src/policy_impact/runtime/executor_registry.py`
- Create: `src/policy_impact/skills/executors/__init__.py`
- Create: `src/policy_impact/skills/executors/current_skills.py`
- Modify: `src/policy_impact/harness/agent_runtime.py:573-612`
- Test: `tests/test_executor_registry.py`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: `SkillExecutor`, `SkillExecutionContext`, `SkillResult`, and `SkillManifest.executor_id`.
- Produces: `ExecutorRegistry.register()`, `ExecutorRegistry.get()`, and branch-free skill execution.

- [ ] **Step 1: Write failing registry tests**

```python
import pytest

from policy_impact.runtime.executor_registry import ExecutorRegistry
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.result import SkillResult


class EchoExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        return SkillResult(
            answer=context.message,
            actual_execution_mode=ExecutionMode.MODEL_ONCE,
            stop_reason="success",
        )


def test_registry_resolves_executor_by_id():
    registry = ExecutorRegistry()
    registry.register("echo", EchoExecutor())
    assert registry.get("echo").execute(_context()).answer == "hello"


def test_registry_fails_closed_for_unknown_executor():
    with pytest.raises(KeyError, match="unknown executor"):
        ExecutorRegistry().get("missing")
```

Define `_context()` in the test with non-empty IDs and empty context dictionaries.

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_executor_registry.py -q`

Expected: import fails for `runtime.executor_registry`.

- [ ] **Step 3: Implement registry and explicit current-skill executors**

```python
# src/policy_impact/runtime/executor_registry.py
from policy_impact.runtime.execution_context import SkillExecutor


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, SkillExecutor] = {}

    def register(self, executor_id: str, executor: SkillExecutor) -> None:
        if not executor_id or executor_id in self._executors:
            raise ValueError(f"duplicate or empty executor id: {executor_id!r}")
        self._executors[executor_id] = executor

    def get(self, executor_id: str) -> SkillExecutor:
        try:
            return self._executors[executor_id]
        except KeyError as exc:
            raise KeyError(f"unknown executor: {executor_id}") from exc
```

In `current_skills.py`, define one class per current skill. Each class receives one callable in `__init__`, validates the returned dict with the manifest output schema, and returns a `SkillResult` with its real mode. Example:

```python
class WorkflowSkillExecutor:
    def __init__(self, fn, mode: ExecutionMode) -> None:
        self.fn = fn
        self.mode = mode

    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raw = self.fn(context)
        return SkillResult(
            answer=str(raw["answer"]),
            actual_execution_mode=self.mode,
            stop_reason=str(raw.get("stop_reason") or "success"),
            citations=list(raw.get("citations") or []),
            artifacts=dict(raw.get("artifacts") or {}),
            artifact_refs=[str(value) for value in (raw.get("artifacts") or {}).values()],
            memory_proposals=list(raw.get("memory_proposals") or []),
            gate_decision_refs=list(raw.get("gate_decision_refs") or []),
            resumable=bool(raw.get("resumable", False)),
        )
```

- [ ] **Step 4: Replace `_execute_skill` branching**

Build the registry once in `AgentRuntime.__init__`. `_execute_skill` must contain no `if skill.id` or `elif skill.id`; it becomes:

```python
context = SkillExecutionContext(
    run_id=run_id,
    session_id=session_id,
    company_id=self.company_id,
    message=message,
    model_id=model_id,
    context_manifest=context_manifest,
    prepared_context=prepared_context,
)
result = self.executor_registry.get(skill.executor_id).execute(context)
self._persist_skill_memory_if_requested(run_id, message, skill, result.model_dump())
return result
```

The official legal-reference shortcut must be registered as a deterministic resolver/executor before normal Skill resolution; it must not remain a hidden branch inside `_execute_skill`.

- [ ] **Step 5: Add branch-absence characterization and verify**

```python
def test_execute_skill_does_not_branch_on_skill_id():
    source = inspect.getsource(AgentRuntime._execute_skill)
    assert "if skill.id" not in source
    assert "elif skill.id" not in source
```

Run: `py -3 -m pytest tests/test_executor_registry.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass.

Run: `py -3 -m pytest -q`

Expected: full suite passes.

- [ ] **Step 6: Commit**

```powershell
git add src/policy_impact/runtime/executor_registry.py src/policy_impact/skills/executors src/policy_impact/harness/agent_runtime.py tests/test_executor_registry.py tests/test_agent_runtime.py
git commit -m "refactor: dispatch skills through executor registry"
```

### Task 4: Introduce TurnCoordinator And Truthful Run Lifecycle

**Files:**
- Create: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `src/policy_impact/harness/agent_runtime.py:38-180`
- Modify: `src/policy_impact/memory/store.py:542-681`
- Modify: `src/policy_impact/app/chat_workbench_service.py:86-113`
- Test: `tests/test_turn_coordinator.py`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: Skill and Executor registries from Tasks 2-3 plus shared `RunStatus` and `require_run_transition()` from Task 1.
- Produces: `TurnCoordinator.run_turn()`, process-local `RunExecutionLockRegistry`, and persisted truthful execution mode/status without module-local status strings.

- [ ] **Step 1: Write lifecycle tests**

```python
class BrokenExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise RuntimeError("planned executor failure")


def broken_skill_manifest() -> SkillManifest:
    return SkillManifest(
        id="broken_skill",
        version="1.0.0",
        name="Broken Test Skill",
        description="Only used to verify failed Run persistence.",
        execution_mode=ExecutionMode.DETERMINISTIC,
        executor_id="broken",
        input_schema=SkillInput,
        output_schema=SkillOutput,
        context_policy_id="context.test.v1",
        memory_policy_id="memory.deny.v1",
        permission_policy_id="tools.deny.v1",
        max_steps=1,
        max_model_calls=1,
        timeout_seconds=5,
    )


def test_run_records_actual_execution_mode(runtime_fixture, store):
    result = runtime_fixture.run_turn(
        session_id=store.session_id,
        message="/wiki 规划公司知识库",
        mode="auto",
        model_id="deepseek-v4-flash",
    )
    run = store.get_agent_run(result["run_id"])
    assert run["metadata"]["execution_mode"] == "deterministic"
    assert run["status"] == "done"


def test_failed_run_has_structured_stop_reason(runtime_fixture, store):
    runtime_fixture.registry.register_for_test(broken_skill_manifest())
    runtime_fixture.executor_registry.register("broken", BrokenExecutor())
    with pytest.raises(RuntimeError):
        runtime_fixture.run_turn(
            session_id=store.session_id,
            message="trigger failure",
            mode="auto",
            model_id="deepseek-v4-flash",
            active_skill_id="broken_skill",
        )
    run = store.latest_agent_run(store.session_id)
    assert run["status"] == "failed"
    assert run["metadata"]["stop_reason"] == "executor_error"


def test_same_run_cannot_have_two_active_executors(run_locks):
    with run_locks.hold("run-1"):
        with pytest.raises(RunExecutionConflict):
            with run_locks.hold("run-1", blocking=False):
                pass
```

Use the existing test fixtures for temporary company DB and scripted model; do not call the network.

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_turn_coordinator.py -q`

Expected: `TurnCoordinator` is missing or metadata remains `subagent_driven`.

- [ ] **Step 3: Implement coordinator responsibilities**

`TurnCoordinator.run_turn()` performs these exact operations in order:

```text
validate request
parse slash command
resolve skill manifest
persist user message
create run with manifest execution mode
emit intent and skill-selection records
compile current compatibility context
execute registered executor
persist assistant message
finish run with actual mode and stop reason
```

Run `BeforeTurn` after request validation and before persistence; `deny` stops without creating a Run, while `ask` is invalid for this Hook and fails closed. For every created Run, run `Stop` exactly once at the terminalization boundary after final Artifact refs are known but before committing the terminal status. A `deny` on an otherwise successful result changes the terminal StopReason to `gate_blocked`; failures still invoke `Stop` for audit but Hook decisions cannot revive them. Persist the terminal status and `RunStopped` evidence after Hook decisions. The payload contains candidate Run status, StopReason and Artifact refs, never hidden model reasoning.

Create each persisted Run as `RunStatus.CREATED`, transition to `RUNNING` before executor dispatch, and call `require_run_transition(current, target)` for every subsequent write. Task 4 may reach only `DONE`, `FAILED`, or `CANCELLED`; later Approval and Recovery plans add paths to the already frozen `AWAITING_APPROVAL`, `RESUMING`, `INTERRUPTED_RECOVERABLE`, and `INTERRUPTED_FAILED` states. API serialization uses `.value`; no module may introduce a parallel string enum.

`RunExecutionLockRegistry.hold(run_id, blocking=False)` provides one process-local re-entrant lock per Run and records the owning thread/task. `execute_prepared_turn()`, ResumeService and RunRecovery must acquire it before mutating Run state. Context/Checkpoint state is constructed while this lock is held; the lock remains held through Checkpoint persistence so no same-Run Event can advance between executor-state capture and `event_sequence_no` capture. This is local concurrency control, not a distributed lease or fencing claim.

Its public signature remains compatible:

```python
@dataclass(frozen=True)
class PreparedTurn:
    run_id: str
    session_id: str
    company_id: str
    user_message_id: str
    message: str
    model_id: str
    mode: str
    manifest: SkillManifest


def run_turn(
    self,
    session_id: str,
    message: str,
    mode: str,
    model_id: str,
    active_skill_id: str = "",
) -> dict[str, Any]:
    prepared = self.prepare_turn(
        session_id=session_id,
        message=message,
        mode=mode,
        model_id=model_id,
        active_skill_id=active_skill_id,
    )
    return self.execute_prepared_turn(prepared)
```

`prepare_turn()` validates input, resolves Slash/Skill, saves the user message and creates the Run; it returns a frozen `PreparedTurn` containing Run/session/message/model/Manifest IDs. `execute_prepared_turn()` performs Context compilation, Executor invocation and terminal persistence. The split is required by the later asynchronous Run API and is also used by the synchronous wrapper above.

Add `PolicyMemoryStore.update_agent_run_metadata(run_id, patch)` using a read-merge-write transaction. Persist both planned and actual mode:

```json
{
  "planned_execution_mode": "agent_loop",
  "execution_mode": "model_once",
  "stop_reason": "success",
  "runtime": "agent_workbench"
}
```

- [ ] **Step 4: Reduce `AgentRuntime` to a compatibility facade**

`AgentRuntime.__init__` composes dependencies and creates one Coordinator. `AgentRuntime.run_turn()` delegates directly:

```python
def run_turn(self, **kwargs: Any) -> dict[str, Any]:
    return self.coordinator.run_turn(**kwargs)
```

Business helpers may remain temporarily in an injected `CurrentSkillOperations` object, but Coordinator must not import policy stages or inspect Skill IDs.

- [ ] **Step 5: Verify selected and full tests**

Run: `py -3 -m pytest tests/test_turn_coordinator.py tests/test_agent_runtime.py tests/test_chat_workbench_service.py -q`

Expected: all pass and the Wiki case records `deterministic`.

Run: `py -3 -m pytest -q`

Expected: full suite passes.

- [ ] **Step 6: Commit**

```powershell
git add src/policy_impact/runtime/turn_coordinator.py src/policy_impact/harness/agent_runtime.py src/policy_impact/memory/store.py src/policy_impact/app/chat_workbench_service.py tests/test_turn_coordinator.py tests/test_agent_runtime.py
git commit -m "refactor: coordinate turns through runtime kernel"
```

### Task 5: Enforce Structured Agent Actions And Budgets

**Files:**
- Create: `src/policy_impact/runtime/actions.py`
- Modify: `src/policy_impact/harness/agent_loop.py:1-182`
- Modify: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ExecutionMode`, current `ModelAdapter`, and `ToolGateway`.
- Produces: validated action union, expanded `StopReason`, model/tool budgets, timeout, and repeated-failure detection.

- [ ] **Step 1: Add failing action and budget tests**

```python
same_call = {
    "type": "tool_call",
    "tool_name": "company_wiki_search",
    "arguments": {"company_id": "company_test", "query": "missing"},
}


def test_invalid_action_is_repaired_once_then_stops(scripted_loop):
    scripted_loop.adapter.responses = [
        {"type": "unknown"},
        {"type": "still_unknown"},
    ]
    result = scripted_loop.run()
    assert result.stop_reason == "invalid_model_action"
    assert scripted_loop.adapter.call_count == 2


def test_repeated_identical_tool_failure_stops_loop(scripted_loop):
    scripted_loop.adapter.responses = [same_call, same_call, same_call]
    result = scripted_loop.run()
    assert result.stop_reason == "repeated_tool_failure"


def test_model_call_budget_is_enforced(scripted_loop):
    result = scripted_loop.run(max_model_calls=1, max_steps=4)
    assert result.stop_reason == "max_model_calls"


def test_stop_reason_covers_harness_failure_taxonomy():
    required = {
        "tool_timeout",
        "unknown_outcome",
        "invalid_tool_output",
        "context_budget_exceeded",
        "integrity_error",
        "persistence_error",
    }
    assert required <= set(get_args(StopReason))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_agent_loop.py -q`

Expected: the current loop accepts unvalidated dicts and lacks these stop reasons.

- [ ] **Step 3: Implement discriminated action schemas**

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


class FinalAction(BaseModel):
    type: Literal["final"]
    output: dict


class ToolCallAction(BaseModel):
    type: Literal["tool_call"]
    tool_name: str
    arguments: dict


class DelegateAction(BaseModel):
    type: Literal["delegate"]
    role: str
    task: str
    input_artifact_refs: tuple[str, ...] = ()


class RequestApprovalAction(BaseModel):
    type: Literal["request_approval"]
    tool_name: str
    arguments: dict
    reason: str


AgentAction = Annotated[
    Union[FinalAction, ToolCallAction, DelegateAction, RequestApprovalAction],
    Field(discriminator="type"),
]

StopReason = Literal[
    "success",
    "deterministic_complete",
    "max_steps",
    "max_model_calls",
    "timeout",
    "model_error",
    "invalid_model_action",
    "tool_denied",
    "tool_failed",
    "tool_timeout",
    "unknown_outcome",
    "invalid_tool_output",
    "repeated_tool_failure",
    "context_budget_exceeded",
    "integrity_error",
    "persistence_error",
    "awaiting_approval",
    "gate_blocked",
    "cancelled",
]


@dataclass
class AgentLoopConfig:
    stage_name: str
    agent_role: str
    context_manifest: ContextManifestRecord
    output_key: str
    max_steps: int
    max_model_calls: int
    timeout_seconds: int
    max_calls_per_tool: int = 3
    max_repeated_failures: int = 2
    task_prompt: str = ""
```

Use `time.monotonic()` for deadline checks and a stable JSON hash of tool name plus normalized arguments for repeat detection.

- [ ] **Step 4: Implement one repair attempt without hidden reasoning**

On first schema failure, append a system-visible validation observation containing only the validation errors and allowed action names. On second failure, return `invalid_model_action`. Do not persist raw chain-of-thought fields such as `reasoning`, `thinking`, or `analysis`.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_agent_loop.py tests/test_subagents.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/runtime/actions.py src/policy_impact/harness/agent_loop.py tests/test_agent_loop.py
git commit -m "feat: enforce controlled agent loop actions"
```

### Task 6: Make Subagent Delegation Real And Isolated

**Files:**
- Create: `src/policy_impact/runtime/task_brief.py`
- Create: `src/policy_impact/runtime/subagent_roles.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `src/policy_impact/harness/subagents.py:1-105`
- Modify: `src/policy_impact/harness/agent_loop.py`
- Modify: `tests/test_subagents.py`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: `DelegateAction`, current `ContextManifestRecord`, `SkillManifest.allowed_subagents`, and role-scoped ToolGateway. The Context plan later upgrades this through the compatibility export.
- Produces: `TaskBrief`, versioned `SubagentRoleDefinition`, isolated Subagent budget, parent/child IDs, and truthful `subagent_workflow` only when delegation occurs.

- [ ] **Step 1: Add failing isolation tests**

```python
def test_subagent_receives_task_brief_not_full_chat_history(runner, state):
    result = runner.run(state, "verifier", task="核验 claim-1", input_artifact_refs=("a1",))
    call = runner.model_adapter.calls[0]
    serialized = json.dumps(call["messages"], ensure_ascii=False)
    assert "secret old chat" not in serialized
    assert "claim-1" in serialized
    assert result.parent_span_id


def test_unlisted_subagent_role_is_denied(runner, state):
    with pytest.raises(PermissionError, match="not allowed"):
        runner.run(state, "editor", task="publish")


def test_collector_role_cannot_publish_claims(role_registry):
    collector = role_registry.get("collector", "1.0.0")
    assert collector.can_publish is False
    assert collector.output_schema_name == "evidence_collection.v1"


def test_editor_rejects_ungated_claim_refs(runner, state):
    with pytest.raises(PermissionError, match="verified claim"):
        runner.run(state, "editor", task="publish", input_artifact_refs=("raw-claim",))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_subagents.py -q`

Expected: current `run()` has no TaskBrief, parent span, or caller allowlist.

- [ ] **Step 3: Implement TaskBrief and role budget**

```python
from pydantic import BaseModel, Field


class TaskBrief(BaseModel):
    task_id: str
    parent_run_id: str
    parent_span_id: str
    role: str
    task: str
    input_artifact_refs: tuple[str, ...] = ()
    output_schema_name: str
    max_steps: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)


class SubagentRoleDefinition(BaseModel):
    role: str
    version: str
    prompt_template_id: str
    prompt_template_version: str
    output_schema_name: str
    allowed_tools: tuple[str, ...]
    max_steps: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    can_publish: bool = False


class SubagentResult(BaseModel):
    role: str
    output: dict
    stop_reason: StopReason
    context_manifest_id: str
    parent_span_id: str
    span_id: str
    artifact_refs: tuple[str, ...] = ()
```

Register these immutable role contracts at `1.0.0`:

```text
collector -> evidence_collection.v1 -> read-only source tools -> cannot publish
analyst   -> claim_set.v1           -> evidence/artifact read -> cannot publish
skeptic   -> counterexample_set.v1  -> evidence/artifact read -> cannot publish
verifier  -> claim_verdict_set.v1   -> official-source verification tools -> cannot publish
editor    -> published_answer.v1    -> artifact read/write only -> can publish verified Claim refs
```

`SubagentRunner.run()` must build a new role-scoped `ContextManifestRecord` from TaskBrief fields and the versioned role definition. The allowed tools are the intersection of caller Skill tools and role tools. Empty intersection means no tools, not fallback to all tools. Collector output rejects conclusion/decision fields; Editor input accepts only Claim refs with a persisted Verifier pass Gate. The following Context plan replaces this construction with a typed `ContextRequest` while preserving the public result fields above.

- [ ] **Step 4: Wire `delegate` into AgentLoop**

Inject a `delegate_fn(TaskBrief) -> SubagentResult` into AgentLoop. When absent, `delegate` returns a denied observation and can replan once. When present, append only the Subagent public output and Artifact refs to the parent loop.

- [ ] **Step 5: Correct execution-mode evidence**

For a Run that plans `agent_loop` but delegates, update actual mode to `subagent_workflow`. For a Run that never delegates, retain `agent_loop` or `model_once` based on actual model/tool activity. Add tests for both cases.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_subagents.py tests/test_agent_loop.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass and both delegated/non-delegated modes are truthful.

Run: `py -3 -m pytest -q`

Expected: full suite passes.

```powershell
git add src/policy_impact/runtime/task_brief.py src/policy_impact/runtime/subagent_roles.py src/policy_impact/runtime/turn_coordinator.py src/policy_impact/harness/subagents.py src/policy_impact/harness/agent_loop.py tests/test_subagents.py tests/test_agent_loop.py tests/test_agent_runtime.py
git commit -m "feat: isolate and trace subagent delegation"
```

### Task 7: Register Audited Business Gates

**Files:**
- Create: `src/policy_impact/runtime/gate_catalog.py`
- Modify: `src/policy_impact/harness/gate_engine.py`
- Modify: `src/policy_impact/harness/stage_gates.py`
- Modify: `src/policy_impact/harness/contracts.py`
- Create: `tests/test_gate_catalog.py`
- Modify: `tests/test_gate_engine.py`
- Modify: `tests/test_stage_gates.py`

**Interfaces:**
- Consumes: shared `GateDefinition`, `GateRegistry`, `GateRunner`, `GateDecisionSink`, current Claim/Review compatibility shapes, and `PolicyMemoryStore`.
- Produces: exact `1.0.0` default Gate catalog, `AgentStepGateDecisionSink`, and compatibility adapters that can evaluate only through `GateRunner`.

- [ ] **Step 1: Write failing catalog and audit tests**

```python
def test_default_gate_catalog_has_exact_versioned_contracts():
    registry = build_default_gate_registry()
    contracts = {
        (
            item.gate_id,
            item.version,
            item.input_schema.__name__,
            item.hard_constraint,
            item.repairable,
        )
        for item in registry.definitions()
    }
    assert contracts == {
        ("claim_schema", "1.0.0", "ClaimGateInput", True, False),
        ("evidence_binding", "1.0.0", "ClaimGateInput", True, False),
        ("review_verdict", "1.0.0", "ReviewGateInput", True, True),
        ("publication", "1.0.0", "PublicationGateInput", True, True),
    }


def test_every_gate_evaluation_is_written_once(store, claim_payload):
    sink = AgentStepGateDecisionSink(store)
    resolver = PolicyMemoryReviewGateResolver(store)
    runner = GateRunner(
        build_default_gate_registry(proof_resolver=resolver),
        sink,
    )
    decision = runner.evaluate(
        run_id="r1",
        span_id="s1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=claim_payload,
    )
    records = [
        item for item in store.list_agent_steps("r1")
        if item["step_type"] == "gate_decision"
    ]
    assert len(records) == 1
    assert records[0]["metadata"]["gate_id"] == decision.gate_id
    assert records[0]["metadata"]["gate_version"] == decision.gate_version


def test_compatibility_gate_engine_delegates_to_gate_runner(gate_engine, runner_spy):
    gate_engine.evaluate_claim({
        "claim_id": "c1",
        "claim_text": "x",
        "impact_level": "P1",
        "policy_evidence_refs": ["policy:p1:e1"],
        "company_evidence_refs": ["company:f1"],
    })
    assert runner_spy.calls == [("claim_schema", "1.0.0"), ("evidence_binding", "1.0.0")]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_gate_catalog.py tests/test_gate_engine.py tests/test_stage_gates.py -q`

Expected: default catalog and mandatory Gate sink are missing.

- [ ] **Step 3: Define exact schemas and evaluator ownership**

```python
class ClaimGateInput(BaseModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    impact_level: Literal["P0", "P1", "P2", "P3", "P4"]
    policy_evidence_refs: tuple[str, ...]
    company_evidence_refs: tuple[str, ...]


class ReviewGateInput(BaseModel):
    run_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    reviewer_role: Literal["verifier", "deterministic_evidence_gate"]
    verdict: Literal["pass", "fail", "uncertain"]
    unresolved_skeptic_count: int = Field(ge=0)
    review_refs: tuple[str, ...]
    claim_snapshot_hash: str = Field(min_length=1)
    evidence_refs: tuple[str, ...]
    verifier_execution_audit_ref: str | None = None


class PublicationGateInput(BaseModel):
    publication_kind: Literal["claim", "no_updates"] = "claim"
    run_id: str = Field(min_length=1)
    claim_id: str = ""
    review_audit_ref: str = ""
    claim_snapshot_hash: str = ""
    evidence_refs: tuple[str, ...]
    no_updates: bool = False
    collection_succeeded: bool = False
    requested_repair: Literal["downgrade", "retry_verification"] | None = None


DEFAULT_GATE_DEFINITIONS = (
    GateDefinition(
        gate_id="claim_schema",
        version="1.0.0",
        description="Validate the canonical Claim contract.",
        input_schema=ClaimGateInput,
        hard_constraint=True,
        repairable=False,
    ),
    GateDefinition(
        gate_id="evidence_binding",
        version="1.0.0",
        description="Require Evidence refs for every Claim.",
        input_schema=ClaimGateInput,
        hard_constraint=True,
        repairable=False,
    ),
    GateDefinition(
        gate_id="review_verdict",
        version="1.0.0",
        description="Require verifier pass and resolved skeptic findings.",
        input_schema=ReviewGateInput,
        hard_constraint=True,
        repairable=True,
    ),
    GateDefinition(
        gate_id="publication",
        version="1.0.0",
        description="Publish only reviewed evidence-bound Claims.",
        input_schema=PublicationGateInput,
        hard_constraint=True,
        repairable=True,
    ),
)
```

`build_default_gate_registry()` is the only default registration owner and installs exactly:

| Gate | Evaluator | Hard | Repairable | Deterministic rule |
|---|---|---:|---:|---|
| `claim_schema@1.0.0` | `evaluate_claim_schema` | yes | no | Pydantic validation success is `pass`; invalid input is the Registry's fail-closed `block`. |
| `evidence_binding@1.0.0` | `evaluate_evidence_binding` | yes | no | `P0/P1` require at least one typed Policy ref and one typed Company ref; `P2-P4` require at least one Policy ref. Blank, duplicate, or category-confused refs block. |
| `review_verdict@1.0.0` | `evaluate_review_verdict` | yes | yes | `pass` requires an explicit reviewer role, a pass verdict, zero unresolved skeptic items, Claim snapshot hash, and exact Evidence refs. A Verifier role also requires a trusted Subagent execution proof; a caller-supplied role string is insufficient. |
| `publication@1.0.0` | `evaluate_publication` | yes | yes | Claim publication requires a persisted pass proof for the same Run, Claim snapshot, and exact Evidence set. The explicit `no_updates` kind requires a successful collection and typed Source ref, records a Publication decision, and carries no fake Claim. |

Every evaluator copies canonical input refs into `GateDecision.input_refs`; unknown ID/version, schema failure, evaluator exception, identity mismatch, input-ref mismatch, or illegal repair remains a fail-closed `block` in `GateRegistry`. `GateRunner` computes a canonical input hash, definition fingerprint, and stable evaluation ID before an explicit Sink records the decision. The database Sink writes one idempotent `gate_evaluations` record and one linked `agent_steps` record atomically; publication and Editor proofs resolve only from this dedicated audit table, never from an arbitrary Agent Step.

- [ ] **Step 4: Route compatibility adapters through GateRunner**

Implement `AgentStepGateDecisionSink.record(run_id, span_id, decision) -> str` over the dedicated atomic Gate audit Store API. It writes one idempotent `gate_evaluations` row and one linked `agent_steps` row with `step_type=gate_decision`, public reason, decision, gate ID/version, span ID, attempt, input hash/refs, definition fingerprint, output ref, and repair action. `gate_engine.py` and `stage_gates.py` receive an injected `GateRunner` and only translate shared `GateDecision` back to their current dict/dataclass response shapes. They cannot construct another Gate registry, decision enum, or call a private Registry evaluator. The policy pipeline executes Claim and Evidence gates after scoring, Review gates after deterministic/subagent review, and Publication gates before the first report write. The direct State Sink keeps a private authoritative decision index and mirrors records to `stage_gate_results`; it never trusts arbitrary entries appended to that public list. A successful no-updates collection follows an explicit no-Claim Publication Gate path and must not fabricate a Claim.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_gate_catalog.py tests/test_gate_engine.py tests/test_stage_gates.py tests/test_full_harness_pipeline.py -q`

Expected: exact catalog tests pass and every compatibility evaluation persists one auditable decision.

```powershell
git add src/policy_impact/runtime/gate_catalog.py src/policy_impact/harness/gate_engine.py src/policy_impact/harness/stage_gates.py src/policy_impact/harness/contracts.py tests/test_gate_catalog.py tests/test_gate_engine.py tests/test_stage_gates.py
git commit -m "feat: register versioned audited business gates"
```

### Task 8: Runtime Plan Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-runtime-kernel-acceptance.md`
- Modify: `scripts/harness_linter.py`
- Test: `tests/test_harness_contracts.py`

**Interfaces:**
- Consumes: all runtime deliverables in Tasks 1-7.
- Produces: machine-checked runtime invariants and a truthful stage acceptance report.

- [ ] **Step 1: Add linter assertions**

The linter must fail when:

```text
TurnCoordinator contains policy/news/research/wiki Skill ID branches
AgentRuntime writes metadata loop=subagent_driven for every Run
a default Skill has no registered executor
an AgentLoop action is accepted without schema validation
production code calls GateRegistry._evaluate outside runtime/gates.py
compatibility Gate modules define a second decision enum or construct a private GateRegistry
GateRunner is constructed without an explicit GateDecisionSink
```

Write a unit test that copies a violating source fixture to `tmp_path` and asserts a non-zero linter result.

- [ ] **Step 2: Run linter test and verify RED**

Run: `py -3 -m pytest tests/test_harness_contracts.py -q`

Expected: the new invariant test fails before the linter implementation.

- [ ] **Step 3: Implement linter rules and acceptance document**

The acceptance document records commit hashes and fresh outputs for:

```powershell
py -3 -m pytest tests/test_runtime_contracts.py tests/test_skill_registry.py tests/test_executor_registry.py tests/test_turn_coordinator.py tests/test_agent_loop.py tests/test_subagents.py tests/test_gate_catalog.py tests/test_gate_engine.py tests/test_stage_gates.py -q
py -3 scripts\harness_linter.py
py -3 -m pytest -q
```

Do not prefill pass counts; paste only outputs from commands run during execution.

- [ ] **Step 4: Update README current status**

Move only these verified claims to “已实现”:

- executable Skill manifests;
- branch-free ExecutorRegistry dispatch;
- truthful ExecutionMode;
- bounded Agent action protocol;
- isolated Subagent delegation.

Keep Context, Approval, versioned Memory and Replay in “部分实现/未实现”.

- [ ] **Step 5: Run complete stage verification**

Run:

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 6: Commit**

```powershell
git add readme.md docs/acceptance/2026-07-17-runtime-kernel-acceptance.md scripts/harness_linter.py tests/test_harness_contracts.py
git commit -m "docs: verify runtime kernel migration"
```
