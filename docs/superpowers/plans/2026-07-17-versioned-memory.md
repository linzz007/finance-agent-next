# Versioned Memory And Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 `memory_items` 升级为 Namespace-first、版本化、可确认、可遗忘的四类 Memory 系统，并生成只读派生的用户/企业 Profile Snapshot。

**Architecture:** SQLite `memory_records` 是事实来源，FTS5 只做经过 Namespace/状态过滤后的候选召回；`MemoryPolicy` 将普通表达转为 Proposal，将 `/remember` 或明确“记住”视为授权；冲突写入新版本并 supersede 旧版本；Snapshot 由 active records 派生到 ArtifactStore。

**Tech Stack:** Python 3.10+、Pydantic v2、SQLite/FTS5、ArtifactStore、Context Provider、FastAPI、pytest。

## Global Constraints

- Working、Episodic、Semantic、Decision Memory 必须语义分离。
- 聊天记录和 Conversation Summary 不是 Semantic Memory。
- 查询必须在 SQL 层先应用 Namespace、状态、权限和有效期过滤。
- 普通表达、模型推断、网页和新闻不能直接写为稳定用户事实。
- `/remember` 或明确“记住”构成授权；`/forget` 创建 tombstone，不物理删除历史。
- Snapshot 是派生产物，不能反向覆盖数据库。
- 当前不引入向量数据库；仅在 Memory Eval 证明 FTS5 不足后再评审 Embedding。

---

### Task 1: Add Versioned Memory Schemas And Migration

**Files:**
- Create: `src/policy_impact/memory/models.py`
- Create: `src/policy_impact/memory/migrations.py`
- Create: `src/policy_impact/memory/repository.py`
- Modify: `src/policy_impact/memory/store.py:32-225`
- Test: `tests/test_memory_migration.py`

**Interfaces:**
- Consumes: current `memory_items` rows.
- Produces: `MemoryRecord`, `MemoryProposal`, `memory_records`, `memory_proposals`, and FTS index.

- [ ] **Step 1: Write failing migration tests**

```python
def test_legacy_memory_is_migrated_once(store_with_legacy_memory):
    migrate_memory_v2(store_with_legacy_memory.db_path)
    migrate_memory_v2(store_with_legacy_memory.db_path)
    records = MemoryRepository(store_with_legacy_memory.db_path).list_active(("user/global",))
    assert len(records) == 1
    assert records[0].source_type == "legacy_memory_item"


def test_memory_record_rejects_invalid_status():
    with pytest.raises(ValidationError):
        MemoryRecord(**valid_record(), status="deleted")


def test_fts_index_contains_migrated_active_content(store_with_legacy_memory):
    migrate_memory_v2(store_with_legacy_memory.db_path)
    repository = MemoryRepository(store_with_legacy_memory.db_path)
    results = repository.search_fts("python", namespaces=("user/global",))
    assert len(results) == 1
    assert results[0].status == "active"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_memory_migration.py -q`

Expected: versioned models/repository are missing.

- [ ] **Step 3: Implement models**

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


MemoryType = Literal["working", "episodic", "semantic", "decision"]
MemoryStatus = Literal["active", "superseded", "tombstone", "expired"]


class MemoryRecord(BaseModel):
    memory_id: str
    namespace: str
    memory_type: MemoryType
    subject_key: str
    content: str
    status: MemoryStatus
    version: int = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    source_type: str
    source_ref: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime
    supersedes_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class MemoryProposal(BaseModel):
    proposal_id: str
    proposed_type: Literal["episodic", "semantic", "decision"]
    namespace: str
    subject_key: str
    proposed_content: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_ids: tuple[str, ...]
    sensitivity: str
    conflict_ids: tuple[str, ...]
    status: Literal["pending", "approved", "rejected", "expired", "consumed"]
    created_at: datetime
    expires_at: datetime | None = None
```

- [ ] **Step 4: Add idempotent migration**

Create `schema_migrations(migration_id PRIMARY KEY, applied_at)` and migration ID `20260717_memory_v2`. Add:

```sql
CREATE TABLE memory_records (
    memory_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    supersedes_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(namespace, memory_type, subject_key, version)
);

CREATE TABLE memory_proposals (
    proposal_id TEXT PRIMARY KEY,
    proposed_type TEXT NOT NULL,
    namespace TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    proposed_content TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_message_ids_json TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    conflict_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE VIRTUAL TABLE memory_records_fts USING fts5(
    memory_id UNINDEXED,
    subject_key,
    content,
    tokenize='unicode61'
);
```

Migrate each legacy row once with namespace mapping: existing `company:{id}:explicit` becomes `company/{id}`, conversation summaries are not migrated, and unknown legacy namespaces remain namespaced under `company/{id}/legacy`. Task 1's repository implements read-only `list_active()` and `search_fts()`; Task 2 adds lifecycle writes.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_memory_migration.py tests/test_chat_workbench_store.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/memory/models.py src/policy_impact/memory/migrations.py src/policy_impact/memory/repository.py src/policy_impact/memory/store.py tests/test_memory_migration.py
git commit -m "feat: migrate to versioned memory records"
```

### Task 2: Implement Version, Conflict And Tombstone Semantics

**Files:**
- Modify: `src/policy_impact/memory/repository.py`
- Modify: `src/policy_impact/memory/store.py`
- Test: `tests/test_versioned_memory_repository.py`

**Interfaces:**
- Consumes: models and migrated tables from Task 1.
- Produces: atomic create/supersede/forget/list history operations.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_conflicting_fact_creates_new_version_and_supersedes_old(repository):
    v1 = repository.create(semantic_record("目标是 Agent Infra", subject="career.goal"))
    v2 = repository.supersede(v1.memory_id, "目标是 Report Agent", source_ref="message:m2")
    assert v2.version == 2
    assert v2.supersedes_id == v1.memory_id
    assert repository.get(v1.memory_id).status == "superseded"
    assert repository.current("user/global", "semantic", "career.goal").memory_id == v2.memory_id


def test_forget_creates_tombstone_without_deleting_history(repository):
    active = repository.create(semantic_record("偏好 Python", subject="preference.language"))
    tombstone = repository.forget(active.memory_id, source_ref="message:m3")
    assert tombstone.status == "tombstone"
    assert repository.get(active.memory_id).status == "superseded"
    assert len(repository.history("user/global", "semantic", "preference.language")) == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_versioned_memory_repository.py -q`

Expected: repository is missing.

- [ ] **Step 3: Implement transactional version operations**

For `supersede()` and `forget()` use `BEGIN IMMEDIATE`, read the current active version, calculate `version + 1`, update old status, insert new record, update FTS rows, then commit. A stale expected current ID raises `MemoryConflict` instead of silently branching history.

The concrete repository must implement this exact port; method bodies use the transactions described above rather than inheriting these default guards:

```python
class MemoryRepositoryPort(Protocol):
    def create(self, record: MemoryRecord) -> MemoryRecord:
        raise NotImplementedError

    def current(
        self, namespace: str, memory_type: str, subject_key: str
    ) -> MemoryRecord | None:
        raise NotImplementedError

    def supersede(self, memory_id: str, content: str, *, source_ref: str) -> MemoryRecord:
        raise NotImplementedError

    def forget(self, memory_id: str, *, source_ref: str) -> MemoryRecord:
        raise NotImplementedError

    def history(
        self, namespace: str, memory_type: str, subject_key: str
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    def list_active(self, namespaces: tuple[str, ...]) -> list[MemoryRecord]:
        raise NotImplementedError
```

- [ ] **Step 4: Add compatibility wrappers**

`PolicyMemoryStore.save_memory_once()` writes an active record through the repository and deduplicates exact active content. `delete_memory()` becomes a call to `forget()` and returns `False` only when the ID does not exist.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_versioned_memory_repository.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/memory/repository.py src/policy_impact/memory/store.py tests/test_versioned_memory_repository.py
git commit -m "feat: version supersede and forget memories"
```

### Task 3: Enforce MemoryPolicy And Proposal Confirmation

**Files:**
- Create: `src/policy_impact/memory/policy.py`
- Create: `src/policy_impact/memory/proposals.py`
- Modify: `src/policy_impact/harness/agent_runtime.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Modify: `src/policy_impact/harness/skills.py`
- Test: `tests/test_memory_policy.py`

**Interfaces:**
- Consumes: user message, source message ID, candidate facts, existing records, and the Runtime-owned `HookManager`.
- Produces: direct authorized write, pending proposal, or no-op with structured Memory event.

- [ ] **Step 1: Write failing authorization tests**

```python
def test_explicit_remember_writes_semantic_memory(policy, turn):
    outcome = policy.apply(turn(message="/remember 我的目标是 Agent Infra"))
    assert outcome.action == "written"
    assert outcome.record.subject_key


def test_ordinary_statement_creates_proposal_not_memory(policy, turn):
    outcome = policy.apply(turn(message="我最近可能更喜欢 Python"))
    assert outcome.action == "proposed"
    assert outcome.proposal.status == "pending"
    assert policy.repository.list_active(("user/global",)) == []


def test_web_or_model_inference_cannot_write_user_fact(policy, inferred_turn):
    outcome = policy.apply(inferred_turn)
    assert outcome.action in {"proposed", "ignored"}
    assert outcome.record is None


def test_before_memory_write_deny_prevents_record(policy, hooks, turn):
    hooks.register(
        "BeforeMemoryWrite",
        lambda payload: HookDecision(action="deny", reason="sensitive value"),
    )
    outcome = policy.apply(turn(message="/remember API token is secret"))
    assert outcome.action == "ignored"
    assert outcome.reason == "sensitive value"
    assert policy.repository.list_active(("user/global",)) == []


def test_explicit_decision_command_writes_decision_memory(policy, turn):
    outcome = policy.apply(
        turn(message="/decision adopt artifact_report_01 Use this migration plan")
    )
    assert outcome.action == "written"
    assert outcome.record.memory_type == "decision"
    assert outcome.record.subject_key == "decision/artifact_report_01"


def test_unbound_natural_feedback_does_not_directly_write(policy, turn):
    outcome = policy.apply(turn(message="这个建议我准备采纳"))
    assert outcome.action in {"proposed", "needs_clarification"}
    assert outcome.record is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_memory_policy.py -q`

Expected: current keyword heuristic directly writes explicit-looking content without Proposal model.

- [ ] **Step 3: Implement deterministic authorization parsing**

Recognize only:

```text
/remember <content>
记住：<content>
请记住 <content>
/forget <memory_id or subject_key>
忘记 <unambiguous subject>
/decision adopt <artifact_ref> [note]
/decision reject <artifact_ref> [note]
/decision defer <artifact_ref> [note]
```

Negative phrases such as “不要记住”“别记”“无需记忆” always produce no-op. Ambiguous forget creates a Proposal/clarification response and never deletes multiple subjects. A `/decision` command is authorized only when its Artifact ref belongs to the same company and is visible to the current session; it writes `memory_type=decision`, `subject_key=decision/{artifact_ref}` and `source_type=user_feedback`. Natural-language adoption/rejection/defer feedback creates a Proposal or clarification and never directly writes a Decision record.

`MemoryPolicy.apply()` returns one stable outcome type:

```python
class MemoryPolicyOutcome(BaseModel):
    action: Literal["written", "proposed", "forgotten", "ignored", "needs_clarification"]
    record: MemoryRecord | None = None
    proposal: MemoryProposal | None = None
    reason: str
```

- [ ] **Step 4: Implement proposal repository and transitions**

`ProposalRepository.create()`, `list(status)`, and `transition(expected, target)` use compare-and-swap. Approving a Proposal writes one MemoryRecord and atomically marks Proposal `consumed`; repeated approval returns the same Memory ID.

- [ ] **Step 5: Integrate BeforeMemoryWrite Hook**

Every Semantic/Decision write passes the shared Runtime `HookManager.run("BeforeMemoryWrite", payload)` before repository mutation. The payload contains candidate type, namespace, subject key, provenance type, source message ID and candidate checksum, but not raw secrets in event attributes. `deny` returns `ignored`; `ask` creates a pending Proposal; `allow` permits only an already authorized write; `patch_context` is invalid here; `emit_event` is recorded. Record `MemoryProposed`, `MemoryWritten` and the Hook decision; never include full sensitive content in public summaries.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_memory_policy.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass, including existing negative-instruction cases.

```powershell
git add src/policy_impact/memory/policy.py src/policy_impact/memory/proposals.py src/policy_impact/harness/agent_runtime.py src/policy_impact/runtime/turn_coordinator.py src/policy_impact/harness/skills.py tests/test_memory_policy.py
git commit -m "feat: require authorization or proposal for memory"
```

### Task 4: Separate Working Projection And Episodic Stop Memory

**Files:**
- Create: `src/policy_impact/memory/working.py`
- Create: `src/policy_impact/memory/episodic.py`
- Modify: `src/policy_impact/memory/repository.py`
- Modify: `src/policy_impact/runtime/turn_coordinator.py`
- Test: `tests/test_working_and_episodic_memory.py`

**Interfaces:**
- Consumes: `PolicyMemoryStore.get_agent_run()`, `EventStore.list_for_run()`, `CheckpointRepository.latest_valid()`, successful Stop decisions, and `MemoryRepositoryPort`.
- Produces: read-only `WorkingMemoryView` for the current Run and at most one idempotent Episodic `MemoryRecord` for a successfully completed Run.

- [ ] **Step 1: Write failing boundary and lifecycle tests**

```python
def test_working_memory_is_projection_not_long_term_record(
    working_projector, repository, completed_step_run
):
    view = working_projector.project(completed_step_run.run_id)
    assert view.current_step_id == completed_step_run.last_step_id
    assert view.context_manifest_id == completed_step_run.context_manifest_id
    assert view.checksum
    active = repository.list_active((f"company/{completed_step_run.company_id}",))
    assert all(record.memory_type != "working" for record in active)


def test_successful_stop_writes_one_idempotent_episode(episode_recorder, successful_stop):
    first = episode_recorder.record(successful_stop)
    second = episode_recorder.record(successful_stop)
    assert first is not None
    assert second is not None
    assert first.memory_id == second.memory_id
    assert first.memory_type == "episodic"
    assert first.source_ref == successful_stop.run_id


@pytest.mark.parametrize(
    ("status", "stop_reason"),
    [("failed", "model_error"), ("cancelled", "cancelled"), ("awaiting_approval", "awaiting_approval")],
)
def test_non_successful_stop_writes_no_episode(episode_recorder, stop_payload, status, stop_reason):
    payload = stop_payload.model_copy(update={"status": status, "stop_reason": stop_reason})
    assert episode_recorder.record(payload) is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_working_and_episodic_memory.py -q`

Expected: Working/Episodic lifecycle modules do not exist.

- [ ] **Step 3: Implement the explicit short-term and long-term contracts**

```python
class WorkingMemoryView(BaseModel):
    run_id: str
    run_status: str
    sequence_no: int
    current_step_id: str | None
    open_action: dict | None
    context_manifest_id: str | None
    artifact_refs: tuple[str, ...]
    updated_at: datetime
    checksum: str


class StopMemoryPayload(BaseModel):
    run_id: str
    session_id: str
    company_id: str
    skill_id: str
    status: Literal["done", "failed", "cancelled", "awaiting_approval"]
    stop_reason: StopReason
    public_answer_summary: str
    artifact_refs: tuple[str, ...]
    completed_at: datetime
```

`WorkingMemoryProjector.project(run_id)` reads the current AgentRun, ordered Events and `CheckpointRepository.latest_valid(run_id)`, then computes SHA-256 over canonical JSON. It never inserts into `memory_records`, never enters FTS, and never survives as an active long-term record after Run completion.

- [ ] **Step 4: Implement idempotent Episodic recording after the Stop Gate**

```python
class EpisodicMemoryRecorder:
    def __init__(self, repository: MemoryRepositoryPort) -> None:
        self.repository = repository

    def record(self, payload: StopMemoryPayload) -> MemoryRecord | None:
        if payload.status != "done" or payload.stop_reason not in {
            "success",
            "deterministic_complete",
        }:
            return None
        namespace = f"company/{payload.company_id}"
        subject_key = f"run/{payload.run_id}/outcome"
        existing = self.repository.current(namespace, "episodic", subject_key)
        if existing is not None:
            return existing
        content = json.dumps(
            {
                "skill_id": payload.skill_id,
                "summary": payload.public_answer_summary[:1000],
                "artifact_refs": list(payload.artifact_refs),
                "stop_reason": payload.stop_reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.repository.create(
            MemoryRecord(
                memory_id=uuid4().hex,
                namespace=namespace,
                memory_type="episodic",
                subject_key=subject_key,
                content=content,
                status="active",
                version=1,
                confidence=1.0,
                importance=0.5,
                source_type="run_stop",
                source_ref=payload.run_id,
                created_at=payload.completed_at,
                metadata={"session_id": payload.session_id, "skill_id": payload.skill_id},
            )
        )
```

`TurnCoordinator` first evaluates the shared `Stop` Hook and commits the terminal Run. Only when the committed status is `done` and the Stop decision is allowed does it call the injected `EpisodicMemoryRecorder`; the coordinator contains no Skill-specific branch. The payload uses a redacted public answer summary and Artifact refs, never Prompt、hidden reasoning or raw Tool output. Record `MemoryWritten` with `source_type=run_stop`; a recording failure emits `persistence_error` evidence and does not silently fabricate an episode.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_working_and_episodic_memory.py tests/test_memory_policy.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass, repeated Stop handling writes one episode, and Working state remains a Run projection.

```powershell
git add src/policy_impact/memory/working.py src/policy_impact/memory/episodic.py src/policy_impact/memory/repository.py src/policy_impact/runtime/turn_coordinator.py tests/test_working_and_episodic_memory.py
git commit -m "feat: separate working and episodic memory lifecycle"
```

### Task 5: Retrieve Memory With Namespace-First FTS Ranking

**Files:**
- Create: `src/policy_impact/memory/retriever.py`
- Modify: `src/policy_impact/context/providers.py`
- Modify: `src/policy_impact/memory/store.py`
- Test: `tests/test_memory_retriever.py`

**Interfaces:**
- Consumes: query, allowed namespaces, memory types, current UTC time, and top_k.
- Produces: ranked active Memory records and retrieval diagnostics.

- [ ] **Step 1: Write failing isolation and ranking tests**

```python
def test_namespace_filter_happens_in_candidate_sql(retriever, spy_connection):
    retriever.search("Agent", namespaces=("project/p1",), top_k=5)
    sql, params = spy_connection.last_execute
    assert "namespace IN" in sql
    assert "project/p1" in params


def test_expired_superseded_and_tombstone_records_are_never_returned(retriever, seeded_records):
    results = retriever.search("Python", namespaces=("user/global",), top_k=20)
    assert all(item.record.status == "active" for item in results)
    assert all(item.record.valid_until is None or item.record.valid_until > now_utc() for item in results)


def test_subject_exact_match_ranks_before_fts_only_match(retriever, seeded_records):
    results = retriever.search("career goal", namespaces=("user/global",), top_k=5)
    assert results[0].record.subject_key == "career.goal"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_memory_retriever.py -q`

Expected: current `search_memory()` uses full-table LIKE and has no namespace argument.

- [ ] **Step 3: Implement retrieval pipeline**

Execute:

```text
SQL namespace/status/validity/type filter
subject_key exact/prefix candidates
FTS5 MATCH candidates within allowed IDs
score relevance + importance + recency + confidence
deduplicate by namespace/type/subject current version
flag unresolved conflict groups
return top_k with diagnostics
```

Use deterministic score:

```python
score = (
    exact_subject * 4.0
    + normalized_fts * 3.0
    + record.importance
    + record.confidence
    + recency_score
)
```

Return typed results:

```python
class MemorySearchResult(BaseModel):
    record: MemoryRecord
    score: float
    match_sources: tuple[str, ...]
    diagnostics: dict = Field(default_factory=dict)
```

Unresolved equal-current conflicts are omitted from prompt context and surfaced as diagnostics.

- [ ] **Step 4: Integrate MemoryProvider**

MemoryProvider maps Skill ContextPolicy to exact namespaces. General chat may read `user/global`, `company/{company_id}`, `project/{project_id}` only when the session is bound to that project, and `skill/{skill_id}`. It never requests all namespaces.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_memory_retriever.py tests/test_context_providers.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/memory/retriever.py src/policy_impact/context/providers.py src/policy_impact/memory/store.py tests/test_memory_retriever.py
git commit -m "feat: retrieve memory within namespaces"
```

### Task 6: Generate Versioned Profile Snapshots

**Files:**
- Create: `src/policy_impact/memory/profile_snapshot.py`
- Test: `tests/test_profile_snapshot.py`

**Interfaces:**
- Consumes: active Semantic/Decision records and pending Proposals.
- Produces: immutable JSON and Markdown Artifact records; never writes back to Memory DB.

- [ ] **Step 1: Write failing snapshot tests**

```python
def test_snapshot_contains_only_active_confirmed_records(builder, seeded_memory):
    snapshot = builder.build(namespaces=("user/global", "project/p1"), run_id="r1", step_id="s1")
    payload = json.loads(Path(snapshot.json_artifact.storage_path).read_text(encoding="utf-8"))
    assert all(item["status"] == "active" for item in payload["records"])
    assert payload["pending_proposals"]


def test_snapshot_build_does_not_change_memory_rows(builder, repository):
    before = repository.memory_records_checksum()
    builder.build(namespaces=("user/global",), run_id="r1", step_id="s1")
    assert repository.memory_records_checksum() == before
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_profile_snapshot.py -q`

Expected: ProfileSnapshotBuilder is missing.

- [ ] **Step 3: Implement deterministic snapshot projection**

Group records under:

```text
identity_roles
long_term_goals
project_background
stable_preferences
constraints
watchlists
confirmed_decisions
```

Sort by namespace, subject key and version. Include source refs and generated checksum in JSON. Markdown contains readable summaries and a separate “待确认” section. Write both through ArtifactStore with type `profile_snapshot_json` and `profile_snapshot_markdown`.

Return:

```python
class ProfileSnapshotResult(BaseModel):
    snapshot_version: str
    source_checksum: str
    json_artifact: ArtifactRecord
    markdown_artifact: ArtifactRecord
    active_record_ids: tuple[str, ...]
    pending_proposal_ids: tuple[str, ...]
```

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_profile_snapshot.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/memory/profile_snapshot.py tests/test_profile_snapshot.py
git commit -m "feat: derive immutable profile snapshots"
```

### Task 7: Add Memory And Proposal APIs

**Files:**
- Modify: `src/policy_impact/app/chat_workbench_service.py`
- Modify: `src/policy_impact/app/api.py`
- Test: `tests/test_memory_api.py`

**Interfaces:**
- Consumes: MemoryRepository, ProposalRepository, MemoryPolicy, and ProfileSnapshotBuilder.
- Produces: list/history/proposal decision/forget/snapshot endpoints; visual UI is deferred.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_memory_list_requires_namespace(client):
    response = client.get("/companies/company_001/workbench/memories")
    assert response.status_code == 422


def test_approve_proposal_is_idempotent(client, proposal):
    url = f"/companies/company_001/workbench/memory-proposals/{proposal.proposal_id}/decision"
    first = client.post(url, json={"decision": "approved"})
    second = client.post(url, json={"decision": "approved"})
    assert first.json()["memory_id"] == second.json()["memory_id"]


def test_forget_returns_tombstone(client, active_memory):
    response = client.post(
        f"/companies/company_001/workbench/memories/{active_memory.memory_id}/forget"
    )
    assert response.json()["status"] == "tombstone"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_memory_api.py -q`

Expected: endpoints do not exist.

- [ ] **Step 3: Add endpoints**

```text
GET  /companies/{company_id}/workbench/memories?namespace=user%2Fglobal
GET  /companies/{company_id}/workbench/memories/{memory_id}/history
POST /companies/{company_id}/workbench/memories/{memory_id}/forget
GET  /companies/{company_id}/workbench/memory-proposals?status=pending
POST /companies/{company_id}/workbench/memory-proposals/{proposal_id}/decision
POST /companies/{company_id}/workbench/profile-snapshots
```

Proposal decision accepts only `approved` or `rejected`. Cross-company IDs return 404 without revealing whether the record exists elsewhere.

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_memory_api.py tests/test_chat_workbench_service.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/app/chat_workbench_service.py src/policy_impact/app/api.py tests/test_memory_api.py
git commit -m "feat: expose controlled memory operations"
```

### Task 8: Memory Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-versioned-memory-acceptance.md`
- Modify: `scripts/harness_linter.py`
- Test: `tests/test_memory_end_to_end.py`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: verified cross-session personalization without unauthorized writes or leakage.

- [ ] **Step 1: Add nine end-to-end cases**

```text
MEM-01 explicit remember is available in a new session
MEM-02 ordinary statement remains pending proposal
MEM-03 negative remember instruction writes nothing
MEM-04 project namespace does not leak into unbound session
MEM-05 correction creates a new version
MEM-06 forget hides content but preserves history
MEM-07 profile snapshot matches active records and pending proposals
MEM-08 working state is projected from Run/Checkpoint and never becomes long-term FTS content
MEM-09 successful Stop writes exactly one Episodic record; failed or pending Runs write none
```

- [ ] **Step 2: Extend linter**

Fail when production code inserts directly into `memory_records` outside repository/migration modules, or calls MemoryRepository create/supersede without going through MemoryPolicy/Proposal consumption. The only non-Proposal exception is `EpisodicMemoryRecorder.record()` after an allowed successful Stop; the linter verifies its caller and source type. Working projection must not call MemoryRepository writes.

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

README may claim four-class boundaries, namespace-first retrieval, explicit/Proposal writes, versions, Forget and Profile Snapshot only after these cases pass. Record exact fresh output and commits.

```powershell
git add readme.md docs/acceptance/2026-07-17-versioned-memory-acceptance.md scripts/harness_linter.py tests/test_memory_end_to_end.py
git commit -m "docs: verify versioned memory behavior"
```
