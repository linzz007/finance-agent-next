# Tool Policy And Approval Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前名称级 allowlist 升级为带输入输出 Schema、风险、副作用、Hook、幂等账本的 Tool Gateway，并完整实现 `ask -> checkpoint -> pause -> approve/deny/expire -> resume`。

**Architecture:** Tool Registry 保存定义和 handler；PolicyCompiler 对 Global、Environment、Skill、Role 和 Dynamic Risk 规则做交集；ToolGateway 在执行前后进行验证和 Hook；PermissionBroker 持久化 PendingApproval；TurnCoordinator 从已校验 Checkpoint 恢复同一动作。

**Tech Stack:** Python 3.10+、Pydantic v2、SQLite 事务、SHA-256、现有 CheckpointRepository、FastAPI、pytest。

## Global Constraints

- 决策顺序固定为 `deny -> ask -> allow`，未知工具和未匹配规则默认 deny。
- Shell、Git、代码修改和任意路径写入固定 `forbidden`。
- `ask` 不能抛普通 PermissionError；必须持久化审批与 Checkpoint，并暂停 Run。
- 批准恢复前必须重新校验有效期、Run 状态、arguments hash、Tool/Policy 版本、Checkpoint checksum 和幂等键。
- Memory、发布和未知副作用不能盲目重试。
- Tool 输出校验失败必须隔离，不能进入 Context。

---

### Task 1: Define Typed Tools And Registry

**Files:**
- Create: `src/policy_impact/tools/__init__.py`
- Create: `src/policy_impact/tools/definition.py`
- Create: `src/policy_impact/tools/schemas.py`
- Create: `src/policy_impact/tools/registry.py`
- Modify: `src/policy_impact/mcp_server/registry.py`
- Modify: `src/policy_impact/harness/tool_gateway.py`
- Test: `tests/test_tool_registry.py`

**Interfaces:**
- Consumes: current registered tool handlers.
- Produces: `ToolDefinition`, `SideEffectClass`, and `ToolRegistry.resolve(name, version)`.

- [ ] **Step 1: Write failing registry tests**

```python
def test_registered_tool_has_schema_risk_and_side_effect(tool_registry):
    tool = tool_registry.resolve("company_wiki_search", "1.0.0")
    assert tool.definition.risk_level == "low"
    assert tool.definition.side_effect is SideEffectClass.READ_ONLY
    assert tool.definition.input_schema.model_fields
    assert tool.definition.output_schema.model_fields


def test_unknown_tool_version_fails_closed(tool_registry):
    with pytest.raises(KeyError, match="unknown tool"):
        tool_registry.resolve("company_wiki_search", "9.9.9")


def test_forbidden_tool_cannot_register_handler(tool_registry):
    definition = forbidden_definition("shell_exec")
    with pytest.raises(ValueError, match="forbidden"):
        tool_registry.register(definition, lambda: None)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_tool_registry.py -q`

Expected: `policy_impact.tools` does not exist.

- [ ] **Step 3: Implement definition and registry**

```python
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class SideEffectClass(str, Enum):
    READ_ONLY = "read_only"
    ARTIFACT_WRITE = "artifact_write"
    MEMORY_WRITE = "memory_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    FORBIDDEN = "forbidden"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    version: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    risk_level: str
    side_effect: SideEffectClass
    timeout_seconds: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    idempotent: bool
```

```python
class RegisteredTool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    definition: ToolDefinition
    handler: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], RegisteredTool] = {}

    def register(self, definition: ToolDefinition, handler: Callable[..., Any]) -> None:
        key = (definition.name, definition.version)
        if definition.side_effect is SideEffectClass.FORBIDDEN:
            raise ValueError(f"forbidden tool cannot have handler: {definition.name}")
        if key in self._tools:
            raise ValueError(f"duplicate tool: {key}")
        self._tools[key] = RegisteredTool(definition=definition, handler=handler)

    def resolve(self, name: str, version: str) -> RegisteredTool:
        try:
            return self._tools[(name, version)]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}@{version}") from exc
```

- [ ] **Step 4: Add explicit schemas for current tools**

Define and reuse these concrete models:

```python
class CompanyInput(BaseModel):
    company_id: str = Field(min_length=1)


class SearchInput(CompanyInput):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class SearchOutput(BaseModel):
    items: list[dict] = Field(default_factory=list)


class ReportWriteInput(CompanyInput):
    run_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    relative_name: str = Field(min_length=1)
    content: str
    media_type: str


class ArtifactOutput(BaseModel):
    artifact_id: str
    storage_path: str
    content_hash: str
```

Register each current tool at version `1.0.0`. Read-only search/fetch/read tools are `READ_ONLY`; `report_write` is `ARTIFACT_WRITE`; `memory_save` is `MEMORY_WRITE`. Remove the global callable-only `TOOL_REGISTRY` after compatibility tests migrate.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_tool_registry.py tests/test_mcp_server_stdio.py tests/test_permissions.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/tools src/policy_impact/mcp_server/registry.py src/policy_impact/harness/tool_gateway.py tests/test_tool_registry.py
git commit -m "feat: register typed versioned tools"
```

### Task 2: Compile Layered Tool Policies

**Files:**
- Create: `src/policy_impact/tools/policy.py`
- Modify: `src/policy_impact/harness/permissions.py`
- Modify: `src/policy_impact/skills/manifest.py`
- Modify: `tests/test_permissions.py`
- Test: `tests/test_compiled_tool_policy.py`

**Interfaces:**
- Consumes: ToolDefinition, SkillManifest, Agent role, environment flags, and dynamic risk.
- Produces: immutable `CompiledToolPolicy` and `PolicyDecision` with version/hash.

- [ ] **Step 1: Write failing precedence and intersection tests**

```python
def test_global_deny_overrides_skill_allow(compiler, request):
    policy = compiler.compile(request_for("shell_exec", skill_allow=True))
    assert policy.decide("shell_exec").decision == "deny"


def test_dynamic_risk_ask_overrides_static_allow(compiler, request):
    policy = compiler.compile(request_for("report_write", dynamic_risk="ask"))
    assert policy.decide("report_write").decision == "ask"


def test_unmatched_tool_is_denied(compiler, request):
    assert compiler.compile(request).decide("not_registered").decision == "deny"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_compiled_tool_policy.py tests/test_permissions.py -q`

Expected: current engine lacks layered policy version/hash.

- [ ] **Step 3: Implement policy models**

```python
class PolicyRule(BaseModel):
    layer: str
    tool_pattern: str
    decision: Literal["deny", "ask", "allow"]
    reason: str
    priority: int = 0


class PolicyDecision(BaseModel):
    decision: Literal["deny", "ask", "allow"]
    reason: str
    matched_rule_refs: tuple[str, ...]
    policy_version: str


class CompiledToolPolicy(BaseModel):
    policy_version: str
    policy_hash: str
    allowed_tool_versions: dict[str, str]
    decisions: dict[str, PolicyDecision]

    def decide(self, tool_name: str) -> PolicyDecision:
        return self.decisions.get(
            tool_name,
            PolicyDecision(
                decision="deny",
                reason="no matching compiled policy",
                matched_rule_refs=(),
                policy_version=self.policy_version,
            ),
        )
```

Compiler evaluates layers in this order and takes the most restrictive matching decision:

```text
GlobalPolicy
EnvironmentPolicy
SkillPolicy
AgentRolePolicy
DynamicRiskPolicy
```

The rank is `deny=0`, `ask=1`, `allow=2`; select the minimum rank. Canonical JSON of the sorted rules generates `policy_hash`.

- [ ] **Step 4: Keep PermissionEngine compatibility**

`PermissionEngine.decide(stage_name, tool_name)` delegates to a compiled policy generated from current stage allowlists. Existing tests continue to pass while new Runtime callers pass a Run-scoped policy directly.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_compiled_tool_policy.py tests/test_permissions.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/tools/policy.py src/policy_impact/harness/permissions.py src/policy_impact/skills/manifest.py tests/test_compiled_tool_policy.py tests/test_permissions.py
git commit -m "feat: compile layered tool policies"
```

### Task 3: Validate Tool Calls And Run Shared Hooks

**Files:**
- Create: `src/policy_impact/tools/gateway.py`
- Create: `src/policy_impact/tools/normalization.py`
- Modify: `src/policy_impact/runtime/hooks.py`
- Modify: `src/policy_impact/harness/tool_gateway.py`
- Modify: `src/policy_impact/harness/hooks/stage_hooks.py`
- Test: `tests/test_governed_tool_gateway.py`

**Interfaces:**
- Consumes: ToolRegistry, CompiledToolPolicy, ArtifactStore, ObservabilityRecorder, and the Runtime-owned `HookManager`.
- Produces: validated `ToolObservation` or structured `ToolCallOutcome` without leaking invalid results into Context.

- [ ] **Step 1: Write failing validation tests**

```python
def test_input_schema_failure_never_calls_handler(gateway, handler):
    outcome = gateway.call(request("company_wiki_search", {"top_k": -1}))
    assert outcome.status == "invalid_tool_input"
    assert handler.call_count == 0


def test_output_schema_failure_is_quarantined(gateway_with_bad_output):
    outcome = gateway_with_bad_output.call(valid_request())
    assert outcome.status == "invalid_tool_output"
    assert outcome.observation is None


def test_large_output_is_materialized_as_artifact(gateway_with_large_output):
    outcome = gateway_with_large_output.call(valid_request())
    assert outcome.observation.artifact_ref
    assert len(outcome.observation.preview) <= 2048
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_governed_tool_gateway.py -q`

Expected: current gateway has no input/output validation or quarantine status.

- [ ] **Step 3: Add tool outcomes and reduce shared Hook decisions**

```python
class ToolObservation(BaseModel):
    tool_name: str
    tool_version: str
    public_summary: str
    preview: str = ""
    structured_output: dict = Field(default_factory=dict)
    artifact_ref: str | None = None
    output_hash: str


class ToolCallOutcome(BaseModel):
    call_id: str
    status: Literal[
        "succeeded",
        "denied",
        "awaiting_approval",
        "invalid_tool_input",
        "invalid_tool_output",
        "tool_timeout",
        "tool_failed",
        "unknown_outcome",
    ]
    policy_decision: Literal["deny", "ask", "allow"]
    policy_reason: str
    arguments_hash: str
    observation: ToolObservation | None = None
    approval_id: str | None = None


def reduce_tool_hook_decisions(
    decisions: tuple[HookDecision, ...],
) -> Literal["deny", "ask", "allow"]:
    actions = {item.action for item in decisions}
    if "deny" in actions:
        return "deny"
    if "ask" in actions:
        return "ask"
    return "allow"
```

Register `PreToolUse` and `PostToolUse` on the Runtime-owned `HookManager`; do not introduce a second Hook registry under `tools/`. Any `deny` wins, otherwise `ask` wins, and context patches are applied only after before/after checksums are recorded.

- [ ] **Step 4: Implement governed gateway sequence**

```text
resolve exact tool version
validate and normalize input
compute arguments_hash
check call budget and policy
run PreToolUse
record ToolProposed/ToolPolicyDecided
return ask outcome without handler call when required
record ToolStarted
execute with timeout policy
validate output
redact and cap output
materialize large result
run PostToolUse
record ToolCompleted
return ToolObservation
```

Path normalization must resolve against ArtifactStore root; URL normalization permits only `http`/`https`; date normalization emits ISO-8601.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_governed_tool_gateway.py tests/test_permissions.py tests/test_tool_audit.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/tools src/policy_impact/runtime/hooks.py src/policy_impact/harness/tool_gateway.py src/policy_impact/harness/hooks/stage_hooks.py tests/test_governed_tool_gateway.py
git commit -m "feat: govern tool execution and observations"
```

### Task 4: Persist PendingApproval And ToolExecution Ledger

**Files:**
- Create: `src/policy_impact/tools/approval.py`
- Create: `src/policy_impact/tools/execution_ledger.py`
- Modify: `src/policy_impact/memory/store.py`
- Modify: `src/policy_impact/runtime/recovery.py`
- Test: `tests/test_tool_approval_store.py`
- Test: `tests/test_tool_execution_ledger.py`
- Modify: `tests/test_run_recovery.py`

**Interfaces:**
- Consumes: ask outcome, Run/Step IDs, tool definition, policy version, normalized arguments, and checkpoint ID.
- Produces: durable `PendingApproval`, deduplicated `ToolExecutionRecord`, and the real ledger-backed `InterruptedExecutionClassifier` with explicit crash uncertainty.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_pending_approval_round_trip(repository, approval):
    repository.create(approval)
    loaded = repository.get(approval.approval_id)
    assert loaded.arguments_hash == approval.arguments_hash
    assert loaded.status == "pending"


def test_approval_status_transition_is_compare_and_swap(repository, approval):
    repository.create(approval)
    repository.transition(approval.approval_id, expected="pending", target="approved")
    with pytest.raises(ApprovalConflict):
        repository.transition(approval.approval_id, expected="pending", target="denied")


def test_tool_execution_unique_key_prevents_duplicate_reservation(ledger, record):
    first = ledger.reserve(record)
    second = ledger.reserve(record)
    assert second.execution_id == first.execution_id
    assert second.status == first.status


def test_interrupted_non_idempotent_running_becomes_unknown_outcome(ledger, non_idempotent_record):
    reserved = ledger.reserve(non_idempotent_record)
    ledger.mark_running(reserved.execution_id)
    reconciled = ledger.reconcile_interrupted(reserved.execution_id)
    assert reconciled.status == "unknown_outcome"
    assert reconciled.attempt_count == 1


def test_run_recovery_blocks_interrupted_non_idempotent_attempt(
    recovery_with_ledger, run_with_running_non_idempotent_execution
):
    recovery_with_ledger.scan()
    run_id = run_with_running_non_idempotent_execution
    assert recovery_with_ledger.ledger.get_for_run(run_id).status == "unknown_outcome"
    assert recovery_with_ledger.store.get_agent_run(run_id)["status"] == "interrupted_failed"


def test_run_recovery_marks_interrupted_idempotent_attempt_retryable(
    recovery_with_ledger, run_with_running_idempotent_execution
):
    recovery_with_ledger.scan()
    run_id = run_with_running_idempotent_execution
    assert recovery_with_ledger.ledger.get_for_run(run_id).status == "retryable"
    assert recovery_with_ledger.store.get_agent_run(run_id)["status"] == "interrupted_recoverable"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_tool_approval_store.py tests/test_tool_execution_ledger.py tests/test_run_recovery.py -q`

Expected: repositories/tables are missing.

- [ ] **Step 3: Implement approval model and tables**

```python
class PendingApproval(BaseModel):
    approval_id: str
    run_id: str
    step_id: str
    checkpoint_id: str
    tool_name: str
    tool_version: str
    normalized_arguments: dict
    arguments_hash: str
    policy_version: str
    reason: str
    risk_summary: str
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired", "consumed", "cancelled"]
```

Create `pending_approvals` with all fields above, timestamps, Run foreign key, and index on `(run_id, status)`. Create `tool_executions` with:

```sql
UNIQUE(run_id, step_id, tool_name, arguments_hash)
```

and statuses `reserved`, `running`, `retryable`, `succeeded`, `failed`, `unknown_outcome`, `blocked`.

Use this record contract in `execution_ledger.py`:

```python
class ToolExecutionRecord(BaseModel):
    execution_id: str
    run_id: str
    step_id: str
    tool_name: str
    tool_version: str
    arguments_hash: str
    idempotency_key: str
    idempotent: bool
    side_effect: SideEffectClass
    status: Literal[
        "reserved", "running", "retryable", "succeeded", "failed", "unknown_outcome", "blocked"
    ]
    attempt_count: int = 0
    result_ref: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

The ledger state machine is `reserved -> running -> succeeded|failed|unknown_outcome`; only an explicitly idempotent tool may transition an interrupted `running` attempt to `retryable -> running` with the same idempotency key. An interrupted non-idempotent `running` attempt always becomes `unknown_outcome`. A stale Run with no execution row or a `reserved` row is `safe_before_call` because no Handler invocation began; `succeeded` is `safe_result_committed`. Implement `LedgerInterruptedExecutionClassifier.classify(run_id)` by transactionally reconciling interrupted executions and returning exactly `safe_before_call`, `safe_result_committed`, `retryable`, or `unknown_outcome`; inject it into `RunRecovery`. SQLite cannot prove exactly-once completion for an external side effect across a process crash.

- [ ] **Step 4: Implement transition and expiry rules**

Only these transitions are legal:

```text
pending -> approved|denied|expired|cancelled
approved -> consumed|expired|cancelled
reserved -> running|blocked
running -> succeeded|failed|unknown_outcome
running -> retryable only during recovery when ToolDefinition.idempotent=true
retryable -> running|blocked
```

Use `UPDATE pending_approvals SET status = ? WHERE approval_id = ? AND status = ?`; zero changed rows raises `ApprovalConflict`. Expiry is evaluated using UTC and persisted as `expired` before returning.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_tool_approval_store.py tests/test_tool_execution_ledger.py tests/test_run_recovery.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/tools/approval.py src/policy_impact/tools/execution_ledger.py src/policy_impact/memory/store.py src/policy_impact/runtime/recovery.py tests/test_tool_approval_store.py tests/test_tool_execution_ledger.py tests/test_run_recovery.py
git commit -m "feat: persist approvals and tool execution ledger"
```

### Task 5: Pause And Resume The Exact Tool Action

**Files:**
- Create: `src/policy_impact/runtime/resume.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `src/policy_impact/harness/agent_loop.py`
- Modify: `src/policy_impact/tools/gateway.py`
- Test: `tests/test_approval_resume.py`

**Interfaces:**
- Consumes: valid CheckpointRepository, ApprovalRepository, ExecutionLedger, ToolGateway, ArtifactStore `stage_bytes()`/`publish_staged_in_transaction()`, EventStore `append_in_transaction()`, shared `RunStatus`, `require_run_transition()`, and Runtime-owned `RunExecutionLockRegistry`.
- Produces: `awaiting_approval` Run pause, deduplicated completed resume, and at-most-one non-idempotent attempt with explicit `unknown_outcome`.

- [ ] **Step 1: Write failing pause/resume tests**

```python
def test_ask_pauses_without_executing_tool(harness):
    result = harness.run(tool_action_requiring_ask())
    assert result.stop_reason == "awaiting_approval"
    assert result.resumable is True
    assert harness.handler.call_count == 0
    assert harness.store.get_agent_run(result.run_id)["status"] == "awaiting_approval"


def test_completed_approved_resume_returns_persisted_result_without_reexecution(harness):
    paused = harness.run(tool_action_requiring_ask())
    harness.approve(paused.approval_id)
    first = harness.resume(paused.run_id)
    second = harness.resume(paused.run_id)
    assert first.stop_reason == "success"
    assert second.stop_reason == "success"
    assert harness.handler.call_count == 1


def test_crash_after_non_idempotent_handler_never_retries(harness):
    paused = harness.run(non_idempotent_action_requiring_ask())
    harness.approve(paused.approval_id)
    harness.simulate_crash_after_handler_before_result_commit(paused.run_id)
    result = harness.recover_and_resume(paused.run_id)
    assert result.stop_reason == "unknown_outcome"
    assert harness.handler.call_count == 1


def test_crash_after_result_commit_reuses_persisted_tool_result(harness):
    paused = harness.run(tool_action_requiring_ask())
    harness.approve(paused.approval_id)
    harness.simulate_crash_after_result_commit_before_loop_continue(paused.run_id)
    result = harness.recover_and_resume(paused.run_id)
    assert result.stop_reason == "success"
    assert harness.handler.call_count == 1


def test_interrupted_reserved_action_executes_once_after_resume(harness):
    paused = harness.run(tool_action_requiring_ask())
    harness.approve(paused.approval_id)
    harness.simulate_crash_after_reservation_before_handler(paused.run_id)
    result = harness.recover_and_resume(paused.run_id)
    assert result.stop_reason == "success"
    assert harness.handler.call_count == 1


def test_interrupted_idempotent_action_retries_with_same_key(harness):
    paused = harness.run(idempotent_action_requiring_ask())
    harness.approve(paused.approval_id)
    first_key = harness.simulate_crash_while_idempotent_handler_runs(paused.run_id)
    result = harness.recover_and_resume(paused.run_id)
    assert result.stop_reason == "success"
    assert harness.handler.idempotency_keys == [first_key, first_key]


def test_live_duplicate_resume_returns_conflict_without_ledger_mutation(harness):
    paused = harness.run(tool_action_requiring_ask())
    harness.approve(paused.approval_id)
    with harness.pause_handler_while_running(paused.run_id):
        before = harness.ledger.get_for_run(paused.run_id).model_copy(deep=True)
        with pytest.raises(ResumeInProgress):
            harness.resume(paused.run_id)
        after = harness.ledger.get_for_run(paused.run_id)
        assert after == before
        assert harness.handler.call_count == 1


def test_result_artifact_ledger_and_approval_commit_atomically(harness):
    paused = harness.run(non_idempotent_action_requiring_ask())
    harness.approve(paused.approval_id)
    harness.fail_before_result_transaction_commit(paused.run_id)
    assert harness.ledger.get_for_run(paused.run_id).status == "running"
    assert harness.approvals.get(paused.approval_id).status == "approved"
    assert harness.artifacts.list_for_run(paused.run_id, artifact_type="tool_result") == ()


def test_argument_hash_mismatch_blocks_resume(harness):
    paused = harness.run(tool_action_requiring_ask())
    harness.tamper_checkpoint_arguments(paused.checkpoint_id)
    with pytest.raises(ResumeIntegrityError):
        harness.resume(paused.run_id)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_approval_resume.py -q`

Expected: current `ask` is blocked as PermissionError and no resume service exists.

- [ ] **Step 3: Persist pause boundary**

When ToolGateway returns ask:

```text
save CheckpointDraft with pending action and receive assigned CheckpointEnvelope
create PendingApproval referencing checkpoint
append ApprovalRequested
require running -> awaiting_approval Run transition
return SkillResult(stop_reason=awaiting_approval, resumable=true)
```

Do not save an assistant final answer for a paused Run. Store a public system message only when the UI needs to show that approval is pending.

- [ ] **Step 4: Implement ResumeService validation and execution**

`ResumeService.resume(run_id)` first acquires `RunExecutionLockRegistry.hold(run_id, blocking=False)`; lock conflict maps to HTTP 409 `resume_in_progress`. While holding it, the service loads the Run, PendingApproval, checksum-verified Checkpoint and ledger record in one transaction, then validates in this order:

```text
checkpoint pending_approval_id matches
tool name/version and policy version still exist
normalized arguments hash matches
if ledger=succeeded: approval must be consumed and result Artifact checksum-valid
  - Run done: return persisted final result without transition
  - Run interrupted_recoverable: transition to resuming and continue from persisted tool observation
  - any other Run status: return ResumeConflict without handler execution
if ledger=unknown_outcome|blocked: return the persisted stop result without handler execution
if ledger=running: return ResumeInProgress; only startup RunRecovery may classify an interrupted attempt
if Run=interrupted_recoverable and ledger is missing|reserved: transition to resuming and execute the not-yet-started action once
if Run=interrupted_recoverable and ledger=retryable: require idempotent ToolDefinition and retry with the same idempotency_key
otherwise Run status must be awaiting_approval
approval decision must be approved, denied, expired or cancelled
approved decision must not be expired
```

For a first approved resume, call `require_run_transition(AWAITING_APPROVAL, RESUMING)`, atomically persist `RESUMING` and reserve the ledger entry, then transition `RESUMING -> RUNNING` before invoking the handler. A recovered safe-before-call or retryable action uses `INTERRUPTED_RECOVERABLE -> RESUMING -> RUNNING`; `reserved` does not increment an attempt until immediately before the first Handler call, while `retryable` increments and reuses the original idempotency key. The gateway commits ToolExecution `running` with `attempt_count + 1` before the call. After a normal return it stages the result Artifact, then one SQLite transaction inserts its ArtifactRecord, changes ToolExecution to `succeeded`, changes approval from `approved` to `consumed`, and appends `ArtifactWritten` plus `ToolCompleted`; partial combinations are impossible. Only after that commit does the Loop continue. A second resume reaches the completed-result branch above even though the Run is already `done`; it returns the checksum-verified persisted result without a state transition or handler call. A crash after the atomic result commit but before Loop continuation is recovered through `INTERRUPTED_RECOVERABLE -> RESUMING -> RUNNING`, appends `RunResumed`, and injects the persisted Tool observation. A live duplicate request that sees `running` returns HTTP 409 `resume_in_progress` and does not mutate the ledger. On startup, a stale non-idempotent `running` record becomes `unknown_outcome`; only an idempotent definition may retry with the same `idempotency_key`. External providers that support idempotency keys receive that key, but the Workbench does not claim universal exactly-once side effects.

- [ ] **Step 5: Handle deny, expire, cancel and unknown outcome**

Denied/expired/cancelled approvals use `AWAITING_APPROVAL -> RESUMING -> RUNNING`, inject a denied observation, and allow one replan opportunity without calling the rejected handler. A non-idempotent timeout during a live Run persists ToolExecution `unknown_outcome`, transitions the Run from `RUNNING -> FAILED`, and stops; a crash-discovered unknown outcome is classified by RunRecovery as `INTERRUPTED_FAILED`. Neither path retries automatically. The UI must ask the user to reconcile the external system before any new action.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_approval_resume.py tests/test_agent_loop.py tests/test_permissions.py -q`

Expected: all selected tests pass.

Run: `py -3 -m pytest -q`

Expected: full suite passes.

```powershell
git add src/policy_impact/runtime/resume.py src/policy_impact/runtime/turn_coordinator.py src/policy_impact/harness/agent_loop.py src/policy_impact/tools/gateway.py tests/test_approval_resume.py
git commit -m "feat: pause and resume approved tool calls"
```

### Task 6: Add Approval API

**Files:**
- Modify: `src/policy_impact/app/chat_workbench_service.py`
- Modify: `src/policy_impact/app/api.py`
- Test: `tests/test_approval_api.py`

**Interfaces:**
- Consumes: ApprovalRepository and ResumeService.
- Produces: list/get/decide/resume endpoints; visual cards are deferred to the UI plan.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_approve_endpoint_uses_expected_pending_status(client, paused_run):
    response = client.post(
        f"/companies/company_001/workbench/approvals/{paused_run.approval_id}/decision",
        json={"decision": "approved"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_resume_endpoint_returns_conflict_for_unapproved_run(client, paused_run):
    response = client.post(
        f"/companies/company_001/workbench/runs/{paused_run.run_id}/resume"
    )
    assert response.status_code == 409


def test_resume_endpoint_returns_409_while_same_run_is_active(
    client, approved_run_with_live_handler
):
    before = approved_run_with_live_handler.ledger_record.model_copy(deep=True)
    response = client.post(
        f"/companies/company_001/workbench/runs/{approved_run_with_live_handler.run_id}/resume"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "resume_in_progress"
    assert approved_run_with_live_handler.reload_ledger_record() == before
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_approval_api.py -q`

Expected: endpoints return 404.

- [ ] **Step 3: Add request model and endpoints**

```python
class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "denied", "cancelled"]
```

Add:

```text
GET  /companies/{company_id}/workbench/approvals?status=pending
GET  /companies/{company_id}/workbench/approvals/{approval_id}
POST /companies/{company_id}/workbench/approvals/{approval_id}/decision
POST /companies/{company_id}/workbench/runs/{run_id}/resume
```

Map stale status/checksum/policy conflicts to HTTP 409, missing records to 404, and expired approvals to 410.

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_approval_api.py tests/test_chat_workbench_service.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/app/chat_workbench_service.py src/policy_impact/app/api.py tests/test_approval_api.py
git commit -m "feat: expose tool approval and resume api"
```

### Task 7: Tool Policy Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-tool-policy-approval-acceptance.md`
- Modify: `scripts/harness_linter.py`
- Test: `tests/test_tool_policy_end_to_end.py`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: security, deduplicated-completion, and crash-uncertainty evidence.

- [ ] **Step 1: Add end-to-end cases**

```text
TOOL-01 unknown tool denied
TOOL-02 global forbidden overrides Skill allow
TOOL-03 malformed input never reaches handler
TOOL-04 invalid output never enters Context
TOOL-05 ask pauses without side effect
TOOL-06 completed approval resume returns the persisted result without re-execution
TOOL-07 deny/expire returns controlled observation
TOOL-08 non-idempotent timeout becomes unknown_outcome
TOOL-09 interrupted non-idempotent running attempt becomes unknown_outcome and is never retried
```

- [ ] **Step 2: Extend linter**

Fail when production code calls a registered handler directly outside `tools/gateway.py`, or when Runtime treats decision `ask` as PermissionError.

- [ ] **Step 3: Run complete stage verification**

Run:

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 4: Update evidence and commit**

README may claim typed ToolDefinition, layered policy, output quarantine, persistent ask approval, deduplicated completed resume and explicit crash uncertainty only after the end-to-end suite passes. It must not claim universal exactly-once external side effects. Record fresh outputs and commits in the acceptance document.

```powershell
git add readme.md docs/acceptance/2026-07-17-tool-policy-approval-acceptance.md scripts/harness_linter.py tests/test_tool_policy_end_to_end.py
git commit -m "docs: verify tool governance and approval resume"
```
