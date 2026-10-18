# Observability Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 append-only AgentEvent、父子 Span、受控不可变 ArtifactStore 和带 checksum 的 Checkpoint，为审批恢复、Replay、Eval 与 Lazy Trace 提供可信事实来源。

**Architecture:** SQLite 保存结构化事件和元数据，文件系统只保存受控 Artifact/Checkpoint payload。`ObservabilityRecorder` 是 Runtime、Model、Tool、Memory 和 Gate 的统一写入端；现有 `agent_steps` 暂时作为兼容投影，不再是唯一事实来源。

**Tech Stack:** Python 3.10+、SQLite WAL/事务、Pydantic v2、SHA-256、原子文件 rename、pytest。

## Global Constraints

- AgentEvent 必须 append-only，并在同一 Run 内获得原子递增 `sequence_no`。
- Event、ModelCall、ToolExecution、Checkpoint 必须通过 Run 外键关联。
- Trace 默认只返回摘要和脱敏属性，不默认返回完整 Prompt、凭证或隐藏字段。
- Artifact 只能写入项目控制根目录，采用临时文件加原子 rename，并记录 hash 和大小。
- Checkpoint checksum 不一致时禁止恢复。
- 不引入外部可观测平台或对象存储；SQLite 和本地 ArtifactStore 是当前事实来源。

---

### Task 1: Add Transactional AgentEvent Storage

**Files:**
- Create: `src/policy_impact/observability/__init__.py`
- Create: `src/policy_impact/observability/events.py`
- Create: `src/policy_impact/observability/records.py`
- Create: `src/policy_impact/observability/event_store.py`
- Modify: `src/policy_impact/memory/store.py:57-225`
- Test: `tests/test_agent_event_store.py`

**Interfaces:**
- Consumes: existing `agent_runs` rows.
- Produces: `AgentEvent`, `EventStore.append()`, and `EventStore.list_for_run()`.

- [ ] **Step 1: Write failing event-order tests**

```python
def test_events_receive_atomic_monotonic_sequence(store, run_id):
    event_store = EventStore(store.db_path)
    first = event_store.append(event(run_id, "TurnReceived"))
    second = event_store.append(event(run_id, "SkillSelected"))
    assert [first.sequence_no, second.sequence_no] == [1, 2]


def test_event_idempotency_key_prevents_duplicate_append(store, run_id):
    event_store = EventStore(store.db_path)
    first = event_store.append(event(run_id, "ToolCompleted", idempotency_key="call-1"))
    second = event_store.append(event(run_id, "ToolCompleted", idempotency_key="call-1"))
    assert second.event_id == first.event_id
    assert len(event_store.list_for_run(run_id)) == 1


def test_append_in_transaction_uses_caller_transaction(store, run_id):
    event_store = EventStore(store.db_path)
    with store.begin_immediate() as connection:
        saved = event_store.append_in_transaction(
            connection, event(run_id, "CheckpointSaved")
        )
        assert connection.in_transaction
    assert event_store.get(saved.event_id).sequence_no == 1


def test_event_for_unknown_run_fails_foreign_key(store):
    with pytest.raises(ValueError, match="unknown run"):
        EventStore(store.db_path).append(event("missing", "TurnReceived"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_agent_event_store.py -q`

Expected: observability modules do not exist.

- [ ] **Step 3: Add the event model**

```python
from datetime import datetime
from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    sequence_no: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    public_summary: str
    payload_ref: str | None = None
    attributes: dict = Field(default_factory=dict)
    idempotency_key: str | None = None
    created_at: datetime
```

Define addressable Model/Gate records used by Replay and Eval:

```python
class ModelCallRecord(BaseModel):
    call_id: str
    run_id: str
    span_id: str
    model_id: str
    model_profile_version: str
    context_manifest_id: str
    prompt_template_id: str
    prompt_template_version: str
    prompt_hash: str
    request_hash: str
    response_id: str | None = None
    output_ref: str | None = None
    output_hash: str | None = None
    usage: dict = Field(default_factory=dict)
    status: Literal["requested", "completed", "failed"]
    created_at: datetime
    finished_at: datetime | None = None


class GateDecisionRecord(BaseModel):
    gate_decision_id: str
    run_id: str
    span_id: str
    gate_id: str
    gate_version: str
    decision: Literal["pass", "block", "repair"]
    reason: str
    input_refs: tuple[str, ...] = ()
    output_ref: str | None = None
    repair_action: dict | None = None
    created_at: datetime
```

- [ ] **Step 4: Add SQLite schema and atomic append**

Add `next_event_sequence INTEGER NOT NULL DEFAULT 1` to new `agent_runs` creation and migrate existing DBs with `PRAGMA table_info` plus `ALTER TABLE` when absent. Add:

```sql
CREATE TABLE IF NOT EXISTS agent_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    public_summary TEXT NOT NULL,
    payload_ref TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    UNIQUE(run_id, sequence_no),
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS model_call_records (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_profile_version TEXT NOT NULL,
    context_manifest_id TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    prompt_template_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_id TEXT,
    output_ref TEXT,
    output_hash TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS gate_decision_records (
    gate_decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    gate_version TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    input_refs_json TEXT NOT NULL DEFAULT '[]',
    output_ref TEXT,
    repair_action_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_run_gate
ON gate_decision_records(run_id, gate_id, gate_version, created_at);
```

`EventStore.append()` uses `BEGIN IMMEDIATE`, verifies the Run, reads and increments `next_event_sequence`, inserts the event, then commits. On an idempotency conflict, read and return the existing event. The same implementation exposes `append_in_transaction(connection, event)` for Checkpoint/Gate atomic writes; it requires an already active `BEGIN IMMEDIATE`, never commits or rolls back the caller's transaction, and uses the same sequence/idempotency logic.

Update `PolicyMemoryStore._conn()` to execute `PRAGMA foreign_keys = ON` on every connection; table declarations alone are not sufficient to enforce SQLite foreign keys.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_agent_event_store.py tests/test_chat_workbench_store.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/observability src/policy_impact/memory/store.py tests/test_agent_event_store.py
git commit -m "feat: persist ordered agent events"
```

### Task 2: Add Span-Aware ObservabilityRecorder

**Files:**
- Create: `src/policy_impact/observability/recorder.py`
- Create: `src/policy_impact/observability/artifacts.py`
- Create: `src/policy_impact/observability/gate_sink.py`
- Modify: `src/policy_impact/security/redaction.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `src/policy_impact/context/governed_model_gateway.py`
- Modify: `src/policy_impact/harness/subagents.py`
- Modify: `src/policy_impact/app/api.py`
- Test: `tests/test_observability_recorder.py`

**Interfaces:**
- Consumes: `EventStore`, Runtime lifecycle calls, and Runtime-owned `GateDecision`/`GateDecisionSink` contracts.
- Produces: typed helper methods for all canonical event names, parent/child spans, and `ObservabilityGateDecisionSink` as the production GateRunner sink.

- [ ] **Step 1: Write failing recorder tests**

```python
def test_subagent_events_have_child_span(recorder, run_id):
    root = recorder.turn_received(run_id, "session-1")
    child = recorder.subagent_started(run_id, root.span_id, "verifier")
    assert child.trace_id == root.trace_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id


def test_recorder_redacts_secrets_before_persistence(recorder, run_id):
    recorder.emit(
        event_type="ModelRequested",
        run_id=run_id,
        span_id="s1",
        public_summary="请求模型",
        attributes={"Authorization": "Bearer secret", "prompt_hash": "abc"},
    )
    event = recorder.events(run_id)[0]
    assert event.attributes["Authorization"] == "[REDACTED]"
    assert event.attributes["prompt_hash"] == "abc"


def test_model_completion_materializes_replayable_output(recorder, run_id):
    request = model_request(run_id=run_id, step_id="step-1")
    recorder.model_requested(request, context_manifest(), prompt_envelope())
    record = recorder.model_completed(request, model_response())
    assert record.output_ref
    assert len(record.output_hash) == 64
    assert Path(record.output_ref).is_file()


def test_gate_runner_writes_one_record_and_event(recorder, registry, run_id):
    sink = ObservabilityGateDecisionSink(recorder)
    runner = GateRunner(registry, sink)
    decision = runner.evaluate(
        run_id=run_id,
        span_id="s1",
        gate_id="claim_schema",
        version="1.0.0",
        payload={
            "claim_id": "c1",
            "claim_text": "A verified impact statement",
            "impact_level": "low",
            "evidence_refs": ["e1"],
        },
    )
    records = recorder.gate_records(run_id)
    assert len(records) == 1
    assert records[0].gate_id == decision.gate_id
    assert records[0].gate_version == decision.gate_version
    assert records[0].decision == decision.decision
    assert [event.event_type for event in recorder.events(run_id)] == ["GateEvaluated"]


def test_application_composition_replaces_temporary_gate_sink(app, seeded_run):
    services = app.state.workbench_services
    assert isinstance(services.gate_runner.sink, ObservabilityGateDecisionSink)
    assert not isinstance(services.gate_runner.sink, AgentStepGateDecisionSink)
    services.gate_engine.evaluate_claim(
        run_id=seeded_run.run_id,
        span_id=seeded_run.root_span_id,
        payload={
            "claim_id": "c1",
            "claim_text": "A verified impact statement",
            "impact_level": "low",
            "evidence_refs": ["e1"],
        },
    )
    assert len(services.recorder.gate_records(seeded_run.run_id)) == 2
    assert [
        event.event_type for event in services.recorder.events(seeded_run.run_id)
        if event.event_type == "GateEvaluated"
    ] == ["GateEvaluated", "GateEvaluated"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_observability_recorder.py -q`

Expected: recorder is missing.

- [ ] **Step 3: Implement canonical event helpers**

Recorder methods map exactly to:

```text
TurnReceived
SkillSelected
ContextCompiled
ModelRequested
ModelCompleted
ToolProposed
ToolPolicyDecided
ToolStarted
ToolCompleted
ApprovalRequested
ApprovalDecided
SubagentStarted
SubagentCompleted
GateEvaluated
MemoryProposed
MemoryWritten
ArtifactWritten
CheckpointSaved
RunInterrupted
RunResumed
RunStopped
```

Each helper creates only a public summary and redacted attributes. Payloads are accepted only through `payload_ref`.

The generic primitive is `emit(event_type, run_id, span_id, public_summary, attributes, parent_span_id=None, status="done", payload_ref=None) -> AgentEvent`. The dedicated model methods preserve the `ModelCallRecorder` signatures defined by the Context plan and return the persisted `ModelCallRecord` from `model_completed()`.

Task 2 creates the initial full `ArtifactStore` used for ModelCall output payloads: controlled root, relative-path validation, temporary file, flush/fsync, atomic rename, SHA-256 and immutable hash-addressed name. Task 3 migrates all remaining business Artifact writers to this same store and adds the broader path/immutability suite.

`model_requested()` inserts one `model_call_records` row with status `requested`; `model_completed()` updates the same row with response/output refs, hash, usage and status `completed`. `gate_evaluated(run_id, span_id, decision: GateDecision)` inserts `GateDecisionRecord` using the same gate ID/version, decision, reason, input refs and output ref. The operations also append their corresponding public AgentEvent in the same SQLite transaction. `ObservabilityGateDecisionSink.record()` delegates only to `gate_evaluated()` and returns its record ID. The FastAPI application composition root must build exactly one shared GateRunner with `ObservabilityGateDecisionSink` and inject that runner into `gate_engine.py`, `stage_gates.py`, Subagent and publication paths; `AgentStepGateDecisionSink` remains available only to the earlier Runtime-stage compatibility tests. No final production dependency graph may instantiate it or evaluate through `GateRegistry._evaluate()` directly.

- [ ] **Step 4: Integrate lifecycle and spans**

`TurnCoordinator` creates the root trace/span at `TurnReceived`. Model and tool operations use child spans. Subagent events use a child span whose parent is the delegating loop span. Persist `trace_id` and `root_span_id` in Run metadata.

- [ ] **Step 5: Keep agent_steps as a compatibility projection**

During this stage, continue writing `agent_steps` for current UI/tests, but add `event_id` and `span_id` to step metadata. Do not reconstruct events from steps; events are written first and steps project their public fields.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_observability_recorder.py tests/test_agent_runtime.py tests/test_subagents.py -q`

Expected: all pass.

```powershell
git add src/policy_impact/observability src/policy_impact/security/redaction.py src/policy_impact/runtime/turn_coordinator.py src/policy_impact/context/governed_model_gateway.py src/policy_impact/harness/subagents.py src/policy_impact/app/api.py tests/test_observability_recorder.py
git commit -m "feat: record span-aware agent lifecycle"
```

### Task 3: Replace Arbitrary Artifact Writes With ArtifactStore

**Files:**
- Modify: `src/policy_impact/observability/artifacts.py`
- Modify: `src/policy_impact/harness/artifacts.py`
- Modify: `src/policy_impact/context/snapshot_store.py`
- Modify: `src/policy_impact/skill_executors/policy_weekly_impact.py`
- Modify: `src/policy_impact/skill_executors/recent_news_report.py`
- Modify: `src/policy_impact/harness/agent_runtime.py`
- Test: `tests/test_artifact_store.py`

**Interfaces:**
- Consumes: run/step IDs, controlled relative artifact name, media type, bytes, and the Runtime-owned `HookManager`.
- Produces: `StagedArtifact`, immutable `ArtifactRecord`, `stage_bytes()`, `publish_staged_in_transaction()`, and API-safe path under `data/artifacts/{run_id}`.

- [ ] **Step 1: Write failing security and immutability tests**

```python
def test_artifact_store_rejects_path_escape(store):
    with pytest.raises(ArtifactPathError):
        store.write_bytes(
            run_id="r1",
            step_id="s1",
            artifact_type="report",
            relative_name="../../outside.txt",
            media_type="text/plain",
            content=b"x",
        )


def test_artifact_write_is_hash_addressed_and_immutable(store):
    first = store.write_text("r1", "s1", "report", "report.md", "text/markdown", "hello")
    second = store.write_text("r1", "s1", "report", "report.md", "text/markdown", "changed")
    assert first.storage_path != second.storage_path
    assert first.content_hash != second.content_hash
    assert Path(first.storage_path).read_text(encoding="utf-8") == "hello"


def test_before_artifact_publish_hook_can_deny(store, hooks):
    hooks.register(
        "BeforeArtifactPublish",
        lambda payload: HookDecision(action="deny", reason="missing evidence"),
    )
    with pytest.raises(ArtifactPublishDenied, match="missing evidence"):
        store.write_text("r1", "s1", "report", "blocked.md", "text/markdown", "unsafe")
    assert not tuple(store.root.rglob("blocked*"))


def test_staged_artifact_record_joins_caller_transaction(store, db):
    staged = store.stage_bytes("r1", "s1", "tool_result", "result.json", "application/json", b"{}")
    with pytest.raises(RuntimeError, match="rollback"):
        with db.begin_immediate() as connection:
            store.publish_staged_in_transaction(connection, staged)
            raise RuntimeError("rollback")
    assert store.list_records("r1", artifact_type="tool_result") == ()
    assert Path(staged.storage_path).is_file()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_artifact_store.py -q`

Expected: the controlled ArtifactStore passes its own ModelCall path, but current business helpers still bypass it or overwrite fixed filenames.

- [ ] **Step 3: Complete ArtifactRecord persistence and harden the atomic store**

```python
class ArtifactRecord(BaseModel):
    artifact_id: str
    run_id: str
    artifact_type: str
    content_hash: str
    media_type: str
    size_bytes: int
    storage_path: str
    source_refs: tuple[str, ...]
    created_by_step_id: str
    created_at: datetime


class StagedArtifact(BaseModel):
    artifact_id: str
    run_id: str
    artifact_type: str
    content_hash: str
    media_type: str
    size_bytes: int
    storage_path: str
    source_refs: tuple[str, ...]
    created_by_step_id: str
```

`stage_bytes()` writes content to `<name>.<uuid>.tmp`, flushes and calls `os.fsync`, then runs the shared `BeforeArtifactPublish` Hook with `run_id`, `step_id`, `artifact_type`, normalized relative path, source refs, media type, size and content hash. `deny` deletes the temporary file and raises `ArtifactPublishDenied`; `ask` fails closed because publishing is not an approval boundary; `patch_context` is invalid for this Hook; `emit_event` is recorded before publication. Only an allowed decision may call `Path.replace()` to publish `<stem>.<sha256[:12]><suffix>` and return `StagedArtifact`. Reject absolute paths, `..`, drive prefixes, symlinks escaping the root, and files above the configured byte limit.

`publish_staged_in_transaction(connection, staged)` verifies the final file hash, inserts one ArtifactRecord, and appends `ArtifactWritten` through `EventStore.append_in_transaction()` without committing the caller's transaction. `write_bytes()` and `write_text()` are convenience wrappers that call both methods inside their own `BEGIN IMMEDIATE`. Tool Approval later joins ArtifactRecord, ToolExecution, Approval and ToolCompleted in one caller-owned transaction. A staged file whose transaction rolls back is an unreferenced orphan and is never queryable as an ArtifactRecord.

Add an `artifact_records` SQLite table keyed by `artifact_id`, with `UNIQUE(run_id, artifact_type, content_hash)`.

- [ ] **Step 4: Migrate all production writes**

`harness/artifacts.py` and `context/snapshot_store.py` become compatibility wrappers over ArtifactStore. Policy, news, research, Wiki and Context snapshot output paths must be returned from `ArtifactRecord.storage_path`; direct production `Path.write_text()` is allowed only for controlled fixture/config writes identified by the linter allowlist.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_artifact_store.py tests/test_checkpoint_replay.py tests/test_agent_runtime.py tests/test_full_harness_pipeline.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/observability/artifacts.py src/policy_impact/harness/artifacts.py src/policy_impact/context/snapshot_store.py src/policy_impact/skill_executors src/policy_impact/harness/agent_runtime.py tests/test_artifact_store.py
git commit -m "feat: write immutable controlled artifacts"
```

### Task 4: Create Checksummed CheckpointEnvelope

**Files:**
- Create: `src/policy_impact/observability/checkpoints.py`
- Modify: `src/policy_impact/harness/checkpoints.py`
- Modify: `src/policy_impact/memory/store.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `tests/test_checkpoint_replay.py`
- Test: `tests/test_checkpoint_integrity.py`

**Interfaces:**
- Consumes: Runtime executor state, version refs, EventStore, and an owned `RunExecutionLockRegistry` lock for the target Run.
- Produces: `CheckpointDraft`, `CheckpointEnvelope`, `StoredCheckpoint`, `CheckpointRepository.save()`, `CheckpointRepository.load()`, `CheckpointRepository.latest_valid()`, distinct checkpoint/event cursors, persisted `checkpoint_records`, and checksum validation.

- [ ] **Step 1: Write failing integrity tests**

```python
def test_checkpoint_round_trip_validates_checksum(repository, envelope):
    saved = repository.save(envelope)
    loaded = repository.load(saved.checkpoint_id)
    assert loaded.state_checksum == saved.state_checksum


def test_tampered_checkpoint_is_rejected(repository, envelope):
    saved = repository.save(envelope)
    Path(saved.storage_path).write_text('{"executor_state":{"changed":true}}', encoding="utf-8")
    with pytest.raises(CheckpointIntegrityError):
        repository.load(saved.checkpoint_id)


def test_latest_valid_returns_highest_verified_sequence(repository, envelope):
    first = repository.save(envelope)
    second = repository.save(envelope.model_copy(update={"checkpoint_id": "cp2"}))
    latest = repository.latest_valid(envelope.run_id)
    assert latest is not None
    assert latest.checkpoint_id == second.checkpoint_id
    assert latest.checkpoint_id != first.checkpoint_id


def test_checkpoint_captures_event_cursor_not_checkpoint_index(repository, envelope, event_store):
    event_store.append(event(envelope.run_id, "TurnReceived"))
    event_store.append(event(envelope.run_id, "SkillSelected"))
    saved = repository.save(envelope)
    loaded = repository.load(saved.checkpoint_id)
    assert loaded.checkpoint_sequence_no == 1
    assert loaded.event_sequence_no == 2


def test_checkpoint_requires_owned_run_execution_lock(repository, envelope):
    with pytest.raises(RunExecutionLockRequired):
        repository.save(envelope)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_checkpoint_integrity.py -q`

Expected: checkpoint envelope/repository is missing.

- [ ] **Step 3: Implement the envelope**

```python
class CheckpointDraft(BaseModel):
    checkpoint_id: str
    run_id: str
    execution_mode: str
    executor_id: str
    executor_state: dict
    context_manifest_id: str
    prompt_version: str
    policy_version: str
    model_profile_version: str
    artifact_refs: tuple[str, ...]
    tool_execution_refs: tuple[str, ...]
    pending_approval_id: str | None


class CheckpointEnvelope(CheckpointDraft):
    checkpoint_sequence_no: int
    event_sequence_no: int
    state_checksum: str


class StoredCheckpoint(BaseModel):
    checkpoint_id: str
    run_id: str
    checkpoint_sequence_no: int
    event_sequence_no: int
    storage_path: str
    state_checksum: str


class CheckpointRepositoryPort(Protocol):
    def save(self, draft: CheckpointDraft) -> StoredCheckpoint:
        raise NotImplementedError

    def load(self, checkpoint_id: str) -> CheckpointEnvelope:
        raise NotImplementedError

    def latest_valid(self, run_id: str) -> CheckpointEnvelope | None:
        raise NotImplementedError
```

`CheckpointRepository.save()` first asserts that the current thread/task owns the target Run's `RunExecutionLockRegistry` lock, then assigns both sequence fields; callers cannot supply them. It computes `state_checksum` over canonical JSON of every envelope field except `state_checksum`, using sorted keys and compact separators. Save metadata through:

```sql
CREATE TABLE IF NOT EXISTS checkpoint_records (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    checkpoint_sequence_no INTEGER NOT NULL,
    event_sequence_no INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    state_checksum TEXT NOT NULL,
    pending_approval_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    UNIQUE(run_id, checkpoint_sequence_no)
);
```

Use this exact save transaction:

```text
BEGIN IMMEDIATE on the shared Event/Checkpoint SQLite database
verify Run exists
event_sequence_no = agent_runs.next_event_sequence - 1
checkpoint_sequence_no = max(checkpoint_records.checkpoint_sequence_no for Run) + 1
build canonical CheckpointEnvelope and state_checksum
write temporary Checkpoint file, fsync, then atomic rename
insert checkpoint_records metadata
append CheckpointSaved with EventStore.append_in_transaction using the same connection
COMMIT
```

If file publication succeeds but the database transaction rolls back, the unreferenced file is an orphan eligible for startup garbage collection. Metadata must never commit before a checksum-valid file exists. `latest_valid()` orders only by `checkpoint_sequence_no`; Replay reads source events only where `AgentEvent.sequence_no > CheckpointEnvelope.event_sequence_no`.

- [ ] **Step 4: Convert legacy checkpoint helper**

`write_checkpoint()` constructs a `CheckpointDraft` from `PolicyImpactState`; the repository assigns both sequence fields and returns the stored Checkpoint metadata. The compatibility helper returns the legacy dict plus `checkpoint_id`, `checkpoint_sequence_no`, `event_sequence_no`, `state_checksum`, and `storage_path`. Rename current “dry replay” UI wording to “checkpoint inspect” in backend metadata; Replay behavior is implemented in its own plan.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_checkpoint_integrity.py tests/test_checkpoint_replay.py tests/test_full_harness_pipeline.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/observability/checkpoints.py src/policy_impact/harness/checkpoints.py src/policy_impact/memory/store.py src/policy_impact/runtime/turn_coordinator.py tests/test_checkpoint_integrity.py tests/test_checkpoint_replay.py
git commit -m "feat: persist verifiable checkpoints"
```

### Task 5: Mark Interrupted Runs On Startup

**Files:**
- Create: `src/policy_impact/runtime/recovery.py`
- Modify: `src/policy_impact/app/api.py`
- Modify: `src/policy_impact/memory/store.py`
- Test: `tests/test_run_recovery.py`

**Interfaces:**
- Consumes: shared `RunStatus`, stale created/running/resuming Runs, valid checkpoint records, and an `InterruptedExecutionClassifier` port.
- Produces: `interrupted_recoverable` or `interrupted_failed` status with structured event.

- [ ] **Step 1: Write failing recovery tests**

```python
def test_running_run_with_valid_checkpoint_becomes_recoverable(recovery, run_with_checkpoint):
    recovery.scan()
    run = recovery.store.get_agent_run(run_with_checkpoint)
    assert run["status"] == "interrupted_recoverable"


def test_running_run_without_checkpoint_becomes_failed(recovery, running_run):
    recovery.scan()
    assert recovery.store.get_agent_run(running_run)["status"] == "interrupted_failed"


def test_created_run_is_never_marked_recoverable(recovery, created_run):
    recovery.attach_valid_checkpoint_for_test(created_run)
    recovery.scan()
    assert recovery.store.get_agent_run(created_run)["status"] == "interrupted_failed"


def test_unknown_side_effect_never_becomes_recoverable(recovery, run_with_unknown_side_effect):
    recovery.scan()
    assert recovery.store.get_agent_run(run_with_unknown_side_effect)["status"] == "interrupted_failed"


```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_run_recovery.py -q`

Expected: recovery scanner is missing.

- [ ] **Step 3: Implement deterministic scan**

Define `InterruptedExecutionClassifier.classify(run_id) -> Literal["safe_before_call", "safe_result_committed", "retryable", "unknown_outcome"]`. The Observability stage installs a no-op implementation that returns `safe_before_call` because the Tool ledger does not exist yet; Tool Task 4 replaces it and adds crash-window tests. `RunRecovery.scan()` runs once during FastAPI startup, in a transaction per stale Run; durable `awaiting_approval` Runs are left untouched. A stale `created` Run always follows the legal `CREATED -> INTERRUPTED_FAILED` transition, even if an inconsistent Checkpoint exists. For stale `running`/`resuming`, `unknown_outcome` forces terminal `interrupted_failed`; the other classifications require a checksum-valid latest Checkpoint and permit non-terminal `interrupted_recoverable`; missing/invalid Checkpoint produces terminal `interrupted_failed`. All transitions use shared `require_run_transition()`. Recoverable classification appends `RunInterrupted` and does not invoke the `Stop` Hook; terminal `interrupted_failed` invokes `Stop` once and appends `RunStopped`. A later resume appends `RunResumed`, and the eventual terminal outcome still produces exactly one `RunStopped`.

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_run_recovery.py tests/test_app_service.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/runtime/recovery.py src/policy_impact/app/api.py src/policy_impact/memory/store.py tests/test_run_recovery.py
git commit -m "feat: classify interrupted agent runs"
```

### Task 6: Serve Summary And Lazy Redacted Trace Details

**Files:**
- Create: `src/policy_impact/observability/query_service.py`
- Modify: `src/policy_impact/app/chat_workbench_service.py`
- Modify: `src/policy_impact/app/api.py`
- Test: `tests/test_trace_api.py`

**Interfaces:**
- Consumes: events, artifact records, checkpoints, and compatibility steps.
- Produces: summary timeline response and separate redacted detail endpoint.

- [ ] **Step 1: Write failing API tests**

```python
def test_trace_summary_does_not_include_raw_prompt(client, seeded_run):
    response = client.get(seeded_run.trace_url)
    payload = response.json()
    assert response.status_code == 200
    assert "messages" not in json.dumps(payload)
    assert payload["events"][0]["public_summary"]


def test_trace_detail_is_requested_by_event_id(client, seeded_run):
    event = seeded_run.events[0]
    response = client.get(f"{seeded_run.trace_url}/events/{event.event_id}")
    assert response.status_code == 200
    assert response.json()["event_id"] == event.event_id
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_trace_api.py -q`

Expected: detail endpoint does not exist and summary currently embeds step payloads.

- [ ] **Step 3: Implement query service and endpoints**

Keep existing Trace endpoint path compatible, but return `observability_schema_version="workbench.trace.v3"`, Run summary, events without private payload, Artifact summaries and Checkpoint summaries. Add:

```text
GET /companies/{company_id}/workbench/sessions/{session_id}/runs/{run_id}/trace/events/{event_id}
```

The detail endpoint applies the same redactor and returns only allowlisted attributes plus a payload excerpt capped at 16 KiB.

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_trace_api.py tests/test_chat_workbench_service.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/observability/query_service.py src/policy_impact/app/chat_workbench_service.py src/policy_impact/app/api.py tests/test_trace_api.py
git commit -m "feat: expose lazy redacted trace data"
```

### Task 7: Observability Foundation Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-observability-foundation-acceptance.md`
- Modify: `scripts/harness_linter.py`
- Test: `tests/test_observability_invariants.py`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: verified invariants required by Tool Approval and Replay plans.

- [ ] **Step 1: Add invariant tests**

Assert:

```text
event sequence is contiguous per Run
subagent spans have a valid parent
every Gate evaluation has exactly one GateDecisionRecord and one GateEvaluated Event
application composition uses ObservabilityGateDecisionSink and never AgentStepGateDecisionSink
interrupted_recoverable has RunInterrupted but no RunStopped until terminal completion
no Artifact path escapes data/artifacts
checkpoint tampering is detected
checkpoint event_sequence_no equals the highest event represented by its state and is independent from checkpoint_sequence_no
trace summary contains no raw Authorization/API key/prompt messages
legacy agent_steps reference an event_id during migration
```

- [ ] **Step 2: Extend Harness Linter**

Fail on uncontrolled production writes outside the explicit fixture/config allowlist, direct production calls to `GateRegistry._evaluate()`, application composition that imports/constructs `AgentStepGateDecisionSink`, and trace serializers that return `input_payload` or `messages` by default.

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

- [ ] **Step 4: Update truthful docs and commit**

Record only fresh command output and commit IDs in the acceptance document. README may now claim ordered Event, parent/child Span, immutable ArtifactStore, checksummed Checkpoint and lazy Trace detail; it must still describe Replay and Approval as incomplete.

```powershell
git add readme.md docs/acceptance/2026-07-17-observability-foundation-acceptance.md scripts/harness_linter.py tests/test_observability_invariants.py
git commit -m "docs: verify observability foundation"
```
