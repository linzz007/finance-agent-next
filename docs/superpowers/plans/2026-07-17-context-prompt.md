# Context And Prompt Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通对话、调研、Workflow 和 Subagent 的每次模型调用都经过统一的 Context Provider、预算编译、版本化 Prompt 和最终 PromptGuard，并生成可复核的 ContextManifest。

**Architecture:** Provider 只生成带来源和优先级的候选项；`ContextCompiler` 在模型预算内执行去重、选择和压缩；`PromptRegistry` 生成固定分区的 `PromptEnvelope`；`PromptGuard` 对最终序列化消息做硬约束。现有 `chat_context.py` 和 `context_manifest.py` 保留兼容导出，逐步委托到新模块。

**Tech Stack:** Python 3.10+、Pydantic v2、SHA-256、现有保守 Token estimator、pytest、SQLite-backed providers。

## Global Constraints

- 必须保留 System Contract、Current Task、输出 Schema 和 Pinned Evidence；预算不足时拒绝模型调用。
- 外部网页、政策、新闻、Memory 和 Tool Output 始终作为不可信数据，不得改变工具权限。
- 所有模型调用只能经过 `ContextCompiler -> PromptRegistry -> PromptGuard -> ModelGateway`。
- 不增加向量数据库；Memory 检索仍由 SQLite/FTS 路线负责。
- 不记录隐藏思维链；Prompt Trace 默认只保存 hash、版本、可见项和脱敏摘要。
- 保持当前普通对话、增量摘要、调研和政策证据行为兼容。

---

### Task 1: Define Context And Prompt Contracts

**Files:**
- Create: `src/policy_impact/context/__init__.py`
- Create: `src/policy_impact/context/models.py`
- Create: `src/policy_impact/context/request.py`
- Create: `src/policy_impact/context/prompt_envelope.py`
- Test: `tests/test_context_contracts.py`

**Interfaces:**
- Consumes: Runtime `run_id`, `step_id`, `skill_id`, role, model ID, and task.
- Produces: `ContextRequest`, `ContextItem`, `ContextManifest`, `PromptEnvelope`, and `PromptGuardError` used throughout this plan.

- [ ] **Step 1: Write failing model-validation tests**

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from policy_impact.context.models import ConversationSummary, ContextItem, TokenBudget
from policy_impact.context.request import ContextRequest


def test_context_request_requires_reserved_output_tokens():
    with pytest.raises(ValidationError):
        ContextRequest(
            run_id="r1",
            step_id="s1",
            skill_id="general_chat",
            agent_role="main",
            model_id="deepseek-v4-flash",
            task="hello",
            context_policy_id="context.general_chat.v1",
            reserved_output_tokens=0,
        )


def test_context_item_requires_source_and_checksum():
    with pytest.raises(ValidationError):
        ContextItem(
            item_id="i1",
            section="memory_and_profile",
            content="x",
            source_ref="",
            checksum="",
            estimated_tokens=1,
            selection_reason="relevant",
            trust_level="trusted_user",
        )


def test_token_budget_rejects_partition_over_total():
    with pytest.raises(ValidationError):
        TokenBudget(
            total_input_tokens=10,
            reserved_output_tokens=2,
            tool_result_margin=1,
            safety_margin=1,
            partitions={"current_task": 11},
        )


def test_conversation_summary_tracks_exact_message_coverage():
    summary = ConversationSummary(
        summary_id="sum1",
        session_id="session1",
        summary_version=2,
        covered_message_ids=("m1", "m2"),
        source_checksum="a" * 64,
        generated_by="conversation_summary.v2@deepseek-v4-flash",
        summary_text="用户正在比较两个实现方案。",
        open_loops=("等待用户选择方案",),
        stable_user_facts=("用户表示偏好可审计方案",),
        decisions=(),
        created_at=datetime.now(timezone.utc),
    )
    assert summary.covered_message_ids == ("m1", "m2")
    assert summary.summary_version == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_context_contracts.py -q`

Expected: import fails for `policy_impact.context`.

- [ ] **Step 3: Implement the immutable contracts**

```python
# src/policy_impact/context/request.py
from pydantic import BaseModel, Field


class ContextRequest(BaseModel):
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    agent_role: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    required_sources: tuple[str, ...] = ()
    optional_sources: tuple[str, ...] = ()
    pinned_evidence_ids: tuple[str, ...] = ()
    context_policy_id: str = Field(min_length=1)
    reserved_output_tokens: int = Field(gt=0)
```

```python
# src/policy_impact/context/models.py
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ContextItem(BaseModel):
    item_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    content: object
    source_ref: str = Field(min_length=1)
    trust_level: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    estimated_tokens: int = Field(ge=0)
    checksum: str = Field(min_length=64, max_length=64)
    pinned: bool = False
    relevance_score: float = 0.0


class OmittedContextItem(BaseModel):
    item_id: str
    source_ref: str
    reason: str
    estimated_tokens: int = 0


class CompressionRecord(BaseModel):
    operation: str
    source_item_ids: tuple[str, ...]
    output_item_id: str
    before_tokens: int
    after_tokens: int
    source_checksum: str
    output_checksum: str


class ConversationSummary(BaseModel):
    summary_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    summary_version: int = Field(gt=0)
    covered_message_ids: tuple[str, ...] = Field(min_length=1)
    source_checksum: str = Field(min_length=64, max_length=64)
    generated_by: str = Field(min_length=1)
    summary_text: str = Field(min_length=1)
    open_loops: tuple[str, ...] = ()
    stable_user_facts: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    created_at: datetime


class TokenBudget(BaseModel):
    total_input_tokens: int = Field(gt=0)
    reserved_output_tokens: int = Field(gt=0)
    tool_result_margin: int = Field(ge=0)
    safety_margin: int = Field(ge=0)
    partitions: dict[str, int]

    @model_validator(mode="after")
    def validate_partitions(self):
        if any(value < 0 for value in self.partitions.values()):
            raise ValueError("partition budgets must be non-negative")
        if sum(self.partitions.values()) > self.total_input_tokens:
            raise ValueError("partition budgets exceed total input budget")
        return self


class ContextManifestDraft(BaseModel):
    manifest_id: str
    run_id: str
    step_id: str
    skill_id: str
    agent_role: str
    visible_items: tuple[ContextItem, ...]
    omitted_items: tuple[OmittedContextItem, ...]
    token_budget: TokenBudget
    estimated_input_tokens: int
    content_checksums: dict[str, str]
    compression_records: tuple[CompressionRecord, ...]


class ContextManifestCandidate(ContextManifestDraft):
    prompt_template_id: str
    prompt_template_version: str
    prompt_hash: str


class ContextManifest(ContextManifestCandidate):
    snapshot_ref: str
```

Implement the envelope with fixed named sections and canonical hashing:

```python
# src/policy_impact/context/prompt_envelope.py
import hashlib
import json
from pydantic import BaseModel, Field


class PromptEnvelope(BaseModel):
    template_id: str
    template_version: str
    model_id: str
    system_contract: str
    skill_contract: dict
    current_task: str
    trusted_profile_facts: list[dict] = Field(default_factory=list)
    untrusted_external_evidence: list[dict] = Field(default_factory=list)
    memory: list[dict] = Field(default_factory=list)
    tool_observations: list[dict] = Field(default_factory=list)
    output_schema: dict
    prompt_hash: str = ""

    def canonical_payload(self) -> dict:
        return self.model_dump(exclude={"prompt_hash"})

    def compute_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_messages(self) -> list[dict]:
        data = self.canonical_payload()
        system = data.pop("system_contract")
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": data},
        ]
```

- [ ] **Step 4: Verify and commit**

Run: `py -3 -m pytest tests/test_context_contracts.py -q`

Expected: all new tests pass.

```powershell
git add src/policy_impact/context tests/test_context_contracts.py
git commit -m "feat: define context and prompt contracts"
```

### Task 2: Implement Context Providers

**Files:**
- Create: `src/policy_impact/context/providers.py`
- Create: `src/policy_impact/context/candidate.py`
- Modify: `src/policy_impact/memory/store.py`
- Test: `tests/test_context_providers.py`

**Interfaces:**
- Consumes: `ContextRequest`, `PolicyMemoryStore`, chat history, tool observations, evidence, and Artifact refs.
- Produces: ordered `ContextCandidate` values; providers never choose final visibility.

- [ ] **Step 1: Write failing provider tests**

```python
def test_recent_history_provider_returns_newest_messages_first(store, request):
    provider = RecentHistoryProvider(store, session_id="s1")
    candidates = provider.provide(request)
    assert [item.metadata["message_id"] for item in candidates] == ["m3", "m2", "m1"]


def test_memory_provider_applies_namespace_before_retrieval(store, request):
    provider = MemoryProvider(store, namespaces=("user/global",))
    candidates = provider.provide(request)
    assert {item.namespace for item in candidates} == {"user/global"}
    assert store.last_memory_query["namespaces"] == ("user/global",)


def test_pinned_evidence_is_marked_required(request):
    provider = EvidenceProvider({"e1": {"text": "official", "source_ref": "policy:e1"}})
    candidate = provider.provide(request.model_copy(update={"pinned_evidence_ids": ("e1",)}))[0]
    assert candidate.pinned is True
    assert candidate.priority == 30
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_context_providers.py -q`

Expected: provider classes do not exist.

- [ ] **Step 3: Implement candidate and provider protocol**

```python
from typing import Protocol
from pydantic import BaseModel, Field


class ContextCandidate(BaseModel):
    item_id: str
    section: str
    content: object
    source_ref: str
    trust_level: str
    namespace: str = ""
    priority: int = Field(ge=0)
    relevance_score: float = 0.0
    pinned: bool = False
    metadata: dict = Field(default_factory=dict)


class ContextProvider(Protocol):
    provider_id: str

    def provide(self, request: ContextRequest) -> list[ContextCandidate]:
        raise NotImplementedError
```

Implement these exact providers:

```text
RecentHistoryProvider
ConversationSummaryProvider
MemoryProvider
CompanyKnowledgeProvider
EvidenceProvider
ToolObservationProvider
ArtifactProvider
```

Every provider sets a stable `source_ref`; external evidence uses `trust_level="untrusted_external"`, user-confirmed profile facts use `trusted_user`, and tool output uses `tool_observation`.

- [ ] **Step 4: Add query-layer namespace support**

Add this store signature without removing the existing compatibility wrapper:

```python
def search_memory_records(
    self,
    query: str,
    *,
    namespaces: tuple[str, ...],
    statuses: tuple[str, ...] = ("active",),
    top_k: int = 5,
) -> list[dict[str, Any]]:
    if not namespaces or "active" not in statuses:
        return []
    terms = [term for term in tokenize(query) if term.strip()]
    if not terms:
        return []
    namespace_marks = ",".join("?" for _ in namespaces)
    term_sql = " OR ".join("content LIKE ?" for _ in terms)
    params = [*namespaces, *(f"%{term}%" for term in terms), int(top_k)]
    with self._conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM memory_items
            WHERE namespace IN ({namespace_marks})
              AND ({term_sql})
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        data = dict(row)
        data["status"] = "active"
        data["metadata"] = _json_loads(data.get("metadata"), {})
        records.append(data)
    return records
```

The SQL must include a parameterized `namespace IN (?, ?)` form (with the placeholder count generated from the requested namespaces) and status predicates before text matching. Until the versioned-memory plan creates the new table, map legacy rows to status `active` in the compatibility query.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_context_providers.py tests/test_chat_context.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/context src/policy_impact/memory/store.py tests/test_context_providers.py
git commit -m "feat: add source-aware context providers"
```

### Task 3: Compile Context With Budgets And Loss-Aware Compression

**Files:**
- Create: `src/policy_impact/context/model_profile.py`
- Create: `src/policy_impact/context/compiler.py`
- Create: `src/policy_impact/context/token_estimator.py`
- Create: `src/policy_impact/context/compression.py`
- Create: `src/policy_impact/context/snapshot_store.py`
- Modify: `src/policy_impact/harness/chat_context.py`
- Modify: `src/policy_impact/harness/context_manifest.py`
- Test: `tests/test_context_compiler.py`

**Interfaces:**
- Consumes: provider candidates and fixed model profile.
- Produces: `CompiledContextDraft(manifest, sections)` with immutable visible/omitted evidence plus `ContextManifestFinalizer.build_candidate()` and `publish()` for the guarded publication boundary.

- [ ] **Step 1: Add failing budget tests**

```python
def test_compiler_never_drops_required_sections(compiler, request):
    compiled = compiler.compile(request, oversized_candidates())
    sections = {item.section for item in compiled.manifest.visible_items}
    assert {"system_contract", "current_task", "output_schema"} <= sections


def test_compiler_rejects_when_pinned_items_cannot_fit(compiler, request):
    with pytest.raises(ContextBudgetExceeded, match="pinned evidence"):
        compiler.compile(request, oversized_pinned_candidates())


def test_compiler_records_every_omission(compiler, request):
    compiled = compiler.compile(request, optional_candidates())
    assert compiled.manifest.omitted_items
    assert all(item.reason for item in compiled.manifest.omitted_items)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_context_compiler.py -q`

Expected: `ContextCompiler` is missing.

- [ ] **Step 3: Implement fixed model profile and conservative estimator**

```python
class ModelProfile(BaseModel):
    model_id: str
    version: str
    context_window: int = Field(gt=0)
    default_reserved_output_tokens: int = Field(gt=0)
    tool_result_margin: int = Field(ge=0)
    safety_margin: int = Field(ge=0)


class ContextBudgetExceeded(RuntimeError):
    pass


class SummaryRefreshRequired(ContextBudgetExceeded):
    def __init__(self, session_id: str, message_ids: tuple[str, ...], source_checksum: str):
        super().__init__("conversation summary refresh required")
        self.session_id = session_id
        self.message_ids = message_ids
        self.source_checksum = source_checksum


class CompiledContextDraft(BaseModel):
    manifest: ContextManifestDraft
    sections: dict[str, tuple[ContextItem, ...]]


class CompiledContextCandidate(BaseModel):
    manifest: ContextManifestCandidate
    sections: dict[str, tuple[ContextItem, ...]]


class CompiledContext(BaseModel):
    manifest: ContextManifest
    sections: dict[str, tuple[ContextItem, ...]]


class ContextSnapshotError(RuntimeError):
    pass


class ContextManifestFinalizer:
    def __init__(self, snapshot_store: ContextSnapshotStore) -> None:
        self.snapshot_store = snapshot_store

    def build_candidate(
        self,
        draft: CompiledContextDraft,
        *,
        prompt_template_id: str,
        prompt_template_version: str,
        prompt_hash: str,
    ) -> CompiledContextCandidate:
        manifest = ContextManifestCandidate(
            **draft.manifest.model_dump(),
            prompt_template_id=prompt_template_id,
            prompt_template_version=prompt_template_version,
            prompt_hash=prompt_hash,
        )
        return CompiledContextCandidate(manifest=manifest, sections=draft.sections)

    def publish(self, candidate: CompiledContextCandidate) -> CompiledContext:
        manifest = self.snapshot_store.publish(
            candidate.manifest,
            candidate.sections,
        )
        return CompiledContext(manifest=manifest, sections=candidate.sections)
```

Register only `deepseek-v4-flash`. The estimator uses UTF-8 byte length and CJK count conservatively:

```python
def estimate_tokens(value: object) -> int:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in text)
    other_bytes = len(text.encode("utf-8")) - cjk * 3
    return max(1, cjk + math.ceil(max(0, other_bytes) / 3))
```

- [ ] **Step 4: Implement deterministic selection and compression order**

Apply this exact sequence:

```text
deduplicate by checksum
materialize long tool output as ArtifactRef
remove lowest relevance optional Memory
reuse a checksum-valid incremental conversation summary or request refresh
compress non-pinned external evidence
remove oldest non-critical raw messages
raise ContextBudgetExceeded if required content still does not fit
```

Calculate `TokenBudget.total_input_tokens` as `context_window - reserved_output_tokens - tool_result_margin - safety_margin`, reject non-positive results, then allocate the fixed partition ceilings from that value. Use stable ordering `(priority desc, pinned desc, relevance desc, item_id asc)`. Record every omission and compression in the manifest. `ConversationSummaryProvider` may only return a persisted `ConversationSummary` whose `source_checksum` matches its exact `covered_message_ids`. If the compiler must remove old raw messages and no valid covering summary exists, raise `SummaryRefreshRequired` instead of fabricating a summary or silently dropping those messages. Task 6 refreshes the summary through the governed model path and retries compilation once.

`ContextSnapshotStore.publish()` is the only persistence entry point and may be called only after `PromptGuard` passes. It derives the immutable path `data/context/{run_id}/{manifest_id}.json`, sets `snapshot_ref` before serialization, writes the redacted final Manifest plus visible-item snapshot as canonical JSON through a temporary file plus atomic rename, and rejects overwrite, absolute/parent paths or a Run-ID mismatch. It catches path validation, serialization and filesystem exceptions, removes any temporary file, and raises `ContextSnapshotError` without publishing a completed path. A Draft or Candidate is never persisted as a completed ContextManifest. The Observability plan migrates this narrow store to the shared ArtifactStore without changing `ContextManifest.snapshot_ref`.

- [ ] **Step 5: Keep compatibility helpers**

`compile_chat_context()` and `build_context_manifest()` delegate to the new compiler for chat-turn use but retain their current return shape until all callers migrate. Add equality tests for current fixture inputs.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_context_compiler.py tests/test_chat_context.py tests/test_context_manifest.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/context src/policy_impact/harness/chat_context.py src/policy_impact/harness/context_manifest.py tests/test_context_compiler.py
git commit -m "feat: compile bounded traceable context"
```

### Task 4: Add Versioned Prompt Registry And Fixed Envelope

**Files:**
- Create: `src/policy_impact/context/prompt_registry.py`
- Create: `src/policy_impact/context/prompt_templates.py`
- Modify: `src/policy_impact/harness/prompt_builder.py`
- Test: `tests/test_prompt_registry.py`

**Interfaces:**
- Consumes: `CompiledContextDraft` and a registered template ID/version.
- Produces: `PromptEnvelope` and serialized model messages with stable hashes.

- [ ] **Step 1: Write failing registry tests**

```python
def test_prompt_registry_requires_explicit_version():
    registry = PromptRegistry(default_prompt_templates())
    with pytest.raises(KeyError):
        registry.get("general_chat", "latest")


def test_external_evidence_is_wrapped_as_data(compiled_context):
    envelope = registry.render("general_chat", "1.0.0", compiled_context)
    assert envelope.untrusted_external_evidence
    assert "ignore previous instructions" not in envelope.system_contract.lower()
    assert envelope.prompt_hash == envelope.compute_hash()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_prompt_registry.py -q`

Expected: registry classes do not exist.

- [ ] **Step 3: Implement registered templates**

Register exact IDs and initial versions:

```text
general_chat@1.0.0
conversation_summary@1.0.0
research_report@1.0.0
agent_loop@1.0.0
subagent@1.0.0
```

Each template renders the same fixed section order. The external-data sections are JSON values under explicit labels, never concatenated into system instructions.

- [ ] **Step 4: Convert old prompt builders into compatibility wrappers**

`build_general_chat_prompt()`, `build_conversation_summary_prompt()`, and `build_research_report_prompt()` call the registry and return the existing `(messages, prompt_contract)` shape. Add `template_id`, `template_version`, and `prompt_hash` to the contract.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_prompt_registry.py tests/test_chat_context.py tests/test_agent_runtime.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/context src/policy_impact/harness/prompt_builder.py tests/test_prompt_registry.py
git commit -m "feat: register versioned prompt envelopes"
```

### Task 5: Enforce The Final PromptGuard Boundary

**Files:**
- Create: `src/policy_impact/context/prompt_guard.py`
- Create: `src/policy_impact/context/governed_model_gateway.py`
- Create: `src/policy_impact/context/model_call_recorder.py`
- Create: `src/policy_impact/security/__init__.py`
- Create: `src/policy_impact/security/redaction.py`
- Modify: `src/policy_impact/harness/model_gateway.py`
- Test: `tests/test_prompt_guard.py`
- Modify: `tests/test_model_gateway.py`

**Interfaces:**
- Consumes: `PromptEnvelope`, `CompiledContextCandidate`, `ContextManifestFinalizer`, `ModelProfile`, shared `HookManager`, and existing `ModelAdapter`.
- Produces: `GovernedModelGateway.complete()` as the sole permitted adapter call path and a final Context snapshot only after the hard guard passes.

- [ ] **Step 1: Write failing hard-gate tests**

```python
def test_guard_rejects_prompt_hash_mismatch(guard, envelope, candidate):
    bad = envelope.model_copy(update={"prompt_hash": "0" * 64})
    with pytest.raises(PromptGuardError, match="hash"):
        guard.validate(bad, candidate)


def test_guard_rejects_missing_pinned_evidence(guard, envelope, candidate):
    without_pinned = envelope.model_copy(update={"untrusted_external_evidence": []})
    with pytest.raises(PromptGuardError, match="pinned"):
        guard.validate(without_pinned, candidate)


def test_gateway_never_calls_adapter_after_budget_failure(gateway, adapter):
    with pytest.raises(ContextBudgetExceeded):
        gateway.complete(oversized_request())
    assert adapter.call_count == 0


def test_guard_failure_publishes_no_context_snapshot(gateway, adapter, snapshot_store):
    class RejectingGuard:
        def validate(self, envelope, candidate):
            raise PromptGuardError("hash mismatch")

    gateway.prompt_guard = RejectingGuard()
    with pytest.raises(PromptGuardError, match="hash"):
        gateway.complete(normal_request())
    assert snapshot_store.list_paths() == ()
    assert adapter.call_count == 0


def test_snapshot_publish_failure_stops_before_model_request(
    gateway, adapter, recorder, monkeypatch
):
    monkeypatch.setattr(
        gateway.finalizer,
        "publish",
        lambda candidate: (_ for _ in ()).throw(ContextSnapshotError("disk full")),
    )
    with pytest.raises(ContextPersistenceError, match="disk full"):
        gateway.complete(normal_request())
    assert recorder.model_requested_count == 0
    assert adapter.call_count == 0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_prompt_guard.py tests/test_model_gateway.py -q`

Expected: guard and governed gateway are missing.

- [ ] **Step 3: Implement guard checks**

```python
class PromptGuardError(RuntimeError):
    pass


class ContextPersistenceError(RuntimeError):
    pass
```

`PromptGuard.validate(envelope: PromptEnvelope, candidate: CompiledContextCandidate)` must check the Candidate manifest and its exact `sections` together:

```text
known model profile and prompt version
required sections are non-empty
pinned evidence IDs are represented
manifest checksums match serialized content
prompt hash matches canonical serialization
estimated tokens <= input budget
no credential-shaped values in persisted trace payload
```

Use a `SecretRedactor` that masks Authorization, API keys, cookies, and environment variable values before trace persistence. It must not mutate the payload sent to the configured model endpoint.

```python
class SecretRedactor:
    _sensitive_keys = {
        "authorization", "api_key", "apikey", "cookie", "set-cookie", "auth_token"
    }

    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        self._secret_values = tuple(value for value in secret_values if value)

    def redact(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]"
                if str(key).lower() in self._sensitive_keys
                else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, str):
            redacted = value
            for secret in self._secret_values:
                redacted = redacted.replace(secret, "[REDACTED]")
            return redacted
        return value
```

- [ ] **Step 4: Implement governed gateway**

```python
class GovernedModelGateway:
    def __init__(
        self,
        compiler,
        finalizer,
        prompt_registry,
        prompt_guard,
        adapter,
        recorder,
        hooks,
    ) -> None:
        self.compiler = compiler
        self.finalizer = finalizer
        self.prompt_registry = prompt_registry
        self.prompt_guard = prompt_guard
        self.adapter = adapter
        self.recorder = recorder
        self.hooks = hooks

    def complete(self, request: ModelCallRequest) -> ModelResponse:
        draft = self.compiler.compile(request.context_request, request.candidates)
        decisions = self.hooks.run(
            "BeforeModelCall",
            {"request": request.model_dump(), "manifest_draft": draft.manifest.model_dump()},
        )
        draft = self.compiler.apply_hook_decisions(draft, decisions)
        envelope = self.prompt_registry.render(
            request.prompt_template_id,
            request.prompt_template_version,
            draft,
        )
        candidate = self.finalizer.build_candidate(
            draft,
            prompt_template_id=request.prompt_template_id,
            prompt_template_version=request.prompt_template_version,
            prompt_hash=envelope.prompt_hash,
        )
        self.prompt_guard.validate(envelope, candidate)
        try:
            compiled = self.finalizer.publish(candidate)
        except ContextSnapshotError as exc:
            raise ContextPersistenceError(f"context snapshot publish failed: {exc}") from exc
        self.recorder.model_requested(request, compiled.manifest, envelope)
        response = self.adapter.complete(envelope.to_messages(), request.schema_name)
        self.prompt_guard.validate_model_response(response, request.schema_name)
        after = self.hooks.run(
            "AfterModelCall",
            {"request": request.model_dump(), "response_metadata": response.metadata},
        )
        self.prompt_guard.validate_after_model_hooks(after)
        self.recorder.model_completed(request, response)
        return response
```

`apply_hook_decisions()` accepts only `patch_context` operations on non-system Draft sections, records before/after checksums, and rejects `deny` or `ask`. The finalizer first builds an in-memory Candidate after those patches and Prompt rendering. `PromptGuard` validates that Candidate before `publish()` atomically creates the final snapshot, so a rejected Prompt leaves no completed Context snapshot and never reaches the adapter. A publication failure stops with `persistence_error` before `ModelRequested` or any adapter call. `validate_after_model_hooks()` rejects `deny`/`ask`; `emit_event` is recorded, and no Hook can replace the already validated output Schema.

Define the request with the same names used by the gateway:

```python
class ModelCallRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    step_id: str
    model_id: str
    context_request: ContextRequest
    candidates: tuple[ContextCandidate, ...]
    prompt_template_id: str
    prompt_template_version: str
    schema_name: str
```

Until the Observability Foundation plan installs the append-only recorder, use this compatibility recorder so the Context plan is independently runnable:

```python
class ModelCallRecorder(Protocol):
    def model_requested(
        self, request: ModelCallRequest, manifest: ContextManifest, envelope: PromptEnvelope
    ) -> None:
        raise NotImplementedError

    def model_completed(self, request: ModelCallRequest, response: ModelResponse) -> object:
        raise NotImplementedError


class AgentStepModelCallRecorder:
    def __init__(self, store: PolicyMemoryStore) -> None:
        self.store = store
        self._pending: dict[str, dict] = {}

    def model_requested(
        self, request: ModelCallRequest, manifest: ContextManifest, envelope: PromptEnvelope
    ) -> None:
        self._pending[request.step_id] = {
            "model_id": request.model_id,
            "schema_name": request.schema_name,
            "manifest_id": manifest.manifest_id,
            "prompt_template_id": envelope.template_id,
            "prompt_template_version": envelope.template_version,
            "prompt_hash": envelope.prompt_hash,
        }

    def model_completed(self, request: ModelCallRequest, response: ModelResponse) -> object:
        pending = self._pending.pop(request.step_id)
        return self.store.add_agent_step(
            request.run_id,
            step_type="model_call",
            title="调用受控模型",
            status="done",
            input_payload=pending,
            output_payload={"response_id": response.response_id},
            metadata={"loop_phase": "act", **response.metadata},
        )
```

The Observability plan replaces this adapter with `ObservabilityRecorder` without changing `GovernedModelGateway`.

- [ ] **Step 5: Verify and commit**

Run: `py -3 -m pytest tests/test_prompt_guard.py tests/test_model_gateway.py -q`

Expected: all selected tests pass; guard failure creates no Context snapshot and no adapter call, while publication failure stops before `ModelRequested`.

```powershell
git add src/policy_impact/context src/policy_impact/security src/policy_impact/harness/model_gateway.py tests/test_prompt_guard.py tests/test_model_gateway.py
git commit -m "feat: enforce governed model call boundary"
```

### Task 6: Migrate Every Model Call To The Governed Gateway

**Files:**
- Create: `src/policy_impact/context/summary_service.py`
- Modify: `src/policy_impact/harness/agent_runtime.py`
- Modify: `src/policy_impact/harness/agent_loop.py`
- Modify: `src/policy_impact/harness/subagents.py`
- Modify: `src/policy_impact/stages/review_evidence_and_risk.py`
- Modify: `src/policy_impact/harness/prompt_builder.py`
- Modify: `src/policy_impact/memory/store.py`
- Modify: `scripts/harness_linter.py`
- Test: `tests/test_model_call_boundary.py`
- Test: `tests/test_conversation_summary_service.py`

**Interfaces:**
- Consumes: `GovernedModelGateway.complete()` from Task 5 and `SummaryRefreshRequired` from Task 3.
- Produces: zero direct production calls to `ModelAdapter.complete()` outside the governed gateway and a versioned incremental `ConversationSummary` refresh path.

- [ ] **Step 1: Add a source-boundary test**

```python
def test_model_adapter_complete_is_only_called_by_governed_gateway():
    violations = production_python_files_containing(".adapter.complete(")
    assert violations == [
        Path("src/policy_impact/context/governed_model_gateway.py")
    ]
```

The helper searches `src/policy_impact/**/*.py`, ignores tests and compatibility Protocol declarations, and reports paths in sorted order.

Add summary-specific failures in `tests/test_conversation_summary_service.py`:

```python
def test_summary_refresh_uses_governed_gateway_and_exact_coverage(service, gateway, messages):
    summary = service.refresh("session1", previous=None, messages=messages)
    assert gateway.requests[0].context_request.skill_id == "context_compaction"
    assert summary.covered_message_ids == tuple(item["message_id"] for item in messages)
    assert summary.source_checksum == checksum_messages(messages)


def test_summary_facts_do_not_write_semantic_memory(service, memory_repository, messages):
    service.refresh("session1", previous=None, messages=messages)
    assert memory_repository.list_active(("user/global",)) == []
```

- [ ] **Step 2: Run test and verify RED**

Run: `py -3 -m pytest tests/test_model_call_boundary.py tests/test_conversation_summary_service.py -q`

Expected: current `agent_loop.py` and runtime paths are reported, and `IncrementalSummaryService` is missing.

- [ ] **Step 3: Migrate call sites one route at a time**

Perform and verify in this order:

```text
conversation summary
general chat
research report
main Agent Loop
Subagent
policy evidence review
```

After each route, run its existing focused tests. Do not change business prompts and model parameters in the same commit as boundary migration.

Implement `IncrementalSummaryService.refresh(session_id, previous, messages)` with these fixed rules:

```text
verify previous.covered_message_ids and source_checksum against stored messages
select the next chronological batch within context.summary.v2 input budget
build ContextRequest(skill_id=context_compaction, context_policy_id=context.summary.v2)
provide only the previous summary and selected raw messages as candidates
call GovernedModelGateway.complete(schema_name=conversation_summary.v2)
validate summary_text/open_loops/stable_user_facts/decisions
derive covered_message_ids and source_checksum in code rather than trusting model fields
persist with compare-and-swap on previous source_checksum
repeat until all requested messages are covered
```

The summary request disables recursive summary refresh and has no tools, Memory writes or Subagents. `stable_user_facts` remain untrusted Conversation Summary data; only `MemoryPolicy` can turn a separately authorized user statement into Semantic Memory. `TurnCoordinator` catches `SummaryRefreshRequired`, calls this service, and retries `ContextCompiler.compile()` exactly once; a second refresh requirement stops with `context_budget_exceeded`.

- [ ] **Step 4: Add linter enforcement**

`harness_linter.py` fails when a production file outside `governed_model_gateway.py` calls a concrete adapter's `complete()` method. It may allow a Protocol method declaration and test adapters.

- [ ] **Step 5: Run route and full verification**

Run:

```powershell
py -3 -m pytest tests/test_model_call_boundary.py tests/test_conversation_summary_service.py tests/test_agent_runtime.py tests/test_agent_loop.py tests/test_subagents.py tests/test_full_harness_pipeline.py -q
py -3 scripts\harness_linter.py
py -3 -m pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/policy_impact scripts/harness_linter.py tests/test_model_call_boundary.py tests/test_conversation_summary_service.py
git commit -m "refactor: govern every model invocation"
```

### Task 7: Context And Prompt Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-context-prompt-acceptance.md`
- Test: `tests/test_context_end_to_end.py`

**Interfaces:**
- Consumes: all context/prompt deliverables.
- Produces: end-to-end evidence for long chat, pinned evidence, injection isolation, and budget refusal.

- [ ] **Step 1: Add four end-to-end cases**

```text
CTX-E2E-01 long conversation compacts old messages and keeps recent turns
CTX-E2E-02 pinned policy evidence survives pressure
CTX-E2E-03 web text containing prompt injection remains untrusted data
CTX-E2E-04 impossible required budget stops before model call
```

Each test asserts ContextManifest visible items, omitted reasons, prompt template version, prompt hash, model call count, and final StopReason.

- [ ] **Step 2: Run tests and verify RED before completing assertions**

Run: `py -3 -m pytest tests/test_context_end_to_end.py -q`

Expected: at least one case fails until all routes use the new compiler and guard.

- [ ] **Step 3: Update README and acceptance evidence**

Mark unified Context/Prompt governance as implemented only after the four cases and full suite pass. Record exact commands, commit hashes, and output in the acceptance document.

- [ ] **Step 4: Run complete stage verification**

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 5: Commit**

```powershell
git add readme.md docs/acceptance/2026-07-17-context-prompt-acceptance.md tests/test_context_end_to_end.py
git commit -m "docs: verify context and prompt governance"
```
