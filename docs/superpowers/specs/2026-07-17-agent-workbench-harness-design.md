# Agent Workbench Harness Canonical Design

> 状态：已审阅确认，实施计划已生成
> 确认日期：2026-07-17
> 当前项目：`D:\AAAcode\code-code\agent+\harness\policy_impact`
> 产品形态：单用户、本地优先、面向真实业务 Skills 的 Agent Workbench
> 规格性质：当前项目唯一权威设计；目标能力不等于当前已实现能力
> 实施方法：Superpowers Brainstorming -> Writing Plans -> TDD -> Subagent-Driven Development -> Review -> Verification
> 实施入口：`docs/superpowers/plans/2026-07-17-agent-workbench-harness-roadmap.md`

---

## 1. 文档权威与阅读方式

本文件定义 `policy_impact` 当前项目的目标边界、执行语义、模块职责、失败处理、评测方法和完成标准。

文档优先级固定为：

```text
本 Canonical Spec
-> 对应 Implementation Plan
-> 自动化测试与 EvalCase
-> Runtime / Trace / Artifact 运行证据
-> README
-> agent_ecosystem_intelligence_design 中的历史文档
```

当实现、测试与本规格冲突时，应把冲突登记为缺陷并修正实现或重新评审规格，不能通过降低测试要求掩盖冲突。

本文件使用三种状态：

| 状态 | 含义 |
|---|---|
| 已实现 | 当前代码和新鲜验证证据支持该声明 |
| 部分实现 | 已存在基础模块，但语义或覆盖范围不足 |
| 目标能力 | 本规格要求实现，目前不能对外宣称已经具备 |

---

## 2. 项目定位

### 2.1 一句话定位

`policy_impact` 是一个单用户 Agent Workbench：用户通过统一聊天入口调用普通对话、调研、政策影响、新闻影响和企业知识库等 Skills；Harness 负责执行模式、上下文、工具权限、Subagent、Memory、Trace、Artifact、Checkpoint、Replay 和 Eval。

### 2.2 项目证明的核心能力

项目不以“政策分析产品”或“新闻总结产品”作为唯一价值，而是证明以下 Agent Harness / Infra 能力：

1. 一个 Runtime 能承载多种执行语义，而不是把所有任务强行写成 Workflow 或 Agent Loop。
2. Skill 是具有输入输出、权限、上下文、Memory 和预算契约的可执行单元。
3. 模型只能在 Harness 提供的 Context 和 Tool Policy 内行动。
4. Memory、工具副作用和发布行为具有确定性控制。
5. 每次运行都能被观测、复盘、比较和评测。
6. 政策、新闻、调研等业务能力可以替换，Harness 控制面保持稳定。

### 2.3 当前明确不做

- 多租户账号、组织和计费系统。
- PostgreSQL、S3、分布式 Queue、Lease 和 Fencing Token。
- Shell、Git、代码修改和通用 Coding Agent。
- 任意模型市场或前端模型配置中心。
- 任意目录文件写入。
- 高风险外部通知和自动业务执行。
- 再次更换前端技术栈。
- 没有实验 Artifact 支撑的性能和质量数字。
- “整体强于 Codex/Claude”之类无法复现的宽泛结论。

---

## 3. 当前实现基线

2026-07-17 审核得到的基线如下。

### 3.1 已实现

- FastAPI 原生 Web Workbench 和单用户会话列表。
- Slash 命令：`/auto`、`/chat`、`/policy`、`/news`、`/research`、`/wiki`。
- 固定真实模型 `deepseek-v4-flash`；缺少鉴权时拒绝执行。
- `AgentRun` 与有序 `AgentStep` 持久化。
- 普通对话的 ContextManifest、历史摘要、基础 Token Budget 和最终 Prompt 硬门控。
- 显式长期 Memory 写入、跨会话召回和未持久化信息隔离。
- 政策、新闻、调研和 Company Wiki 业务能力。
- 政策证据复核中的真实 `SubagentRunner`。
- Tool allowlist 和审计记录。
- 回答上方的折叠式 Trace UI。
- 178 个自动化测试、Harness Linter、前端 JavaScript 语法检查。
- 17 个真实业务语义验收案例以及多轮、API、并发验收产物。

### 3.2 部分实现

| 能力 | 当前事实 | 缺口 |
|---|---|---|
| Skill | 有路由元数据 | 执行仍由 `AgentRuntime` 的 `if/elif` 分发 |
| Agent Loop | 有独立 Loop 和 Subagent 测试 | 普通对话和调研没有统一进入可控 Loop |
| ExecutionMode | Trace 中存在部分 mode 统计 | 每个 Run 统一写成 `subagent_driven`，不真实 |
| Context | 普通对话较完整 | 调研及其他模型调用没有同一 PromptGuard |
| Tool Policy | 有 `deny/ask/allow` 枚举 | `ask` 实际按拒绝处理，没有暂停恢复 |
| Memory | SQLite、显式写入、跨会话召回 | 缺少版本、Namespace 查询约束、Proposal 和 Profile Snapshot |
| Trace | 可查看完整步骤 | 数据模型混杂，原始详情在前端预渲染 |
| Checkpoint | 政策 Workflow 保存阶段 JSON | 普通 Agent Turn 不可恢复 |
| Replay | 可读历史 Trace 和 Checkpoint | 没有重新执行状态转换、Gate 或 Artifact 构建 |
| Eval | 关键词 Fixture 和业务验收脚本 | 缺少统一 Case Schema、等预算 Baseline 和版本化报告 |

### 3.3 当前不得宣称

- 所有 Run 都是 Subagent-Driven。
- `ask` 已经支持用户审批。
- 历史 Trace GET 等于 Replay。
- 当前具备 Recorded/Fork/Fresh Replay。
- 当前 3 条关键词 Fixture 能代表端到端 Agent 质量。
- 当前已经通过与 Codex/Claude 的等预算对比。
- 当前 Memory 已经是完整四层版本化系统。

---

## 4. 设计原则

1. **Truth before claims**：运行记录、README、简历和面试口径必须与代码事实一致。
2. **Least capable execution**：能确定性完成的任务不调用模型，能单次模型完成的不强制进入多轮 Loop。
3. **Typed boundaries**：Context、Skill、Tool、Memory、Gate、Artifact 和 Replay 都使用显式契约。
4. **Policy outside prompt**：权限和副作用由确定性代码控制，不依赖模型服从文字要求。
5. **Evidence before publication**：强结论和高风险产物必须通过证据和 Gate。
6. **Append-only audit**：历史运行、Memory 版本和 Artifact 不被静默覆盖。
7. **Fail closed**：未知工具、未知模型、未知策略和校验失败默认拒绝。
8. **DRY / YAGNI**：当前单用户项目不实现分布式生产基础设施。
9. **TDD and review**：所有行为变化先出现失败测试，再实现，再经过规格和代码质量审查。
10. **No hidden chain-of-thought**：只展示动作、观察、决策摘要和停止原因。

---

## 5. 总体架构

```mermaid
flowchart LR
    U["User / Slash Command"] --> TC["TurnCoordinator"]
    TC --> SR["SkillResolver"]
    SR --> CC["ContextCompiler"]
    CC --> ER["ExecutorRegistry"]
    ER --> DE["DeterministicExecutor"]
    ER --> ME["ModelOnceExecutor"]
    ER --> AL["AgentLoopExecutor"]
    ER --> WF["WorkflowExecutor"]
    ER --> SW["SubagentWorkflowExecutor"]
    AL --> TG["ToolGateway"]
    SW --> SA["SubagentRunner"]
    TG --> PB["PermissionBroker"]
    DE --> RS["SkillResult"]
    ME --> RS
    AL --> RS
    WF --> RS
    SW --> RS
    RS --> MP["MemoryPolicy"]
    MP --> OBS["Trace / Artifact / Checkpoint"]
    OBS --> UI["Workbench UI"]
```

### 5.1 控制面与业务面

控制面：

- Turn 生命周期和执行模式。
- Skill、Context、Prompt、Tool 和 Memory Policy。
- Hook、Gate、Approval、Trace、Checkpoint、Replay 和 Eval。

业务面：

- 普通对话。
- 调研报告。
- 政策影响分析。
- 新闻影响分析。
- Company Wiki 规划与检索。

业务 Skill 可以调用既有 Workflow，但不得自己绕过控制面调用模型、工具或 Memory Store。

---

## 6. 核心执行契约

### 6.1 ExecutionMode

```python
class ExecutionMode(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ONCE = "model_once"
    AGENT_LOOP = "agent_loop"
    WORKFLOW = "workflow"
    SUBAGENT_WORKFLOW = "subagent_workflow"
```

默认映射：

| 能力 | 默认模式 |
|---|---|
| 精确法规原文、确定性 Wiki 蓝图 | `deterministic` |
| 无工具的简单回答 | `model_once` |
| 需要 Memory、检索或多步工具的聊天 | `agent_loop` |
| 固定新闻清洗、政策处理链 | `workflow` |
| 高风险政策判断和独立证据复核 | `subagent_workflow` |
| 调研报告 | `agent_loop`，需要时委派 Researcher/Verifier |

每个 Run 必须记录实际模式；不能根据 Skill 名称伪造模式。

### 6.2 SkillManifest

```python
@dataclass(frozen=True)
class SkillManifest:
    id: str
    version: str
    name: str
    description: str
    execution_mode: ExecutionMode
    executor_id: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_tools: tuple[str, ...]
    allowed_subagents: tuple[str, ...]
    context_policy_id: str
    memory_policy_id: str
    permission_policy_id: str
    max_steps: int
    max_model_calls: int
    timeout_seconds: int
```

新增 Skill 只允许：

1. 注册 Manifest。
2. 注册对应 Executor。
3. 添加 Prompt/Policy/Schema。
4. 添加测试和 EvalCase。

禁止修改 TurnCoordinator 的分支来接入新 Skill。

### 6.3 SkillExecutor

```python
class SkillExecutor(Protocol):
    def execute(self, context: "SkillExecutionContext") -> "SkillResult":
        ...
```

```python
class SkillResult(BaseModel):
    answer: str
    actual_execution_mode: ExecutionMode
    stop_reason: str
    citations: list[CitationRef]
    artifact_refs: list[str]
    memory_proposals: list[str]
    gate_decision_refs: list[str]
    resumable: bool
```

### 6.4 TurnCoordinator

TurnCoordinator 只负责：

1. 校验会话、模型和输入。
2. 创建 Run。
3. 解析 Slash 命令和 Skill。
4. 编译 Context。
5. 调用 ExecutorRegistry。
6. 应用 MemoryPolicy。
7. 保存回答、Artifact、Trace 和指标。
8. 处理完成、失败、取消、审批等待和恢复。

它不包含政策、新闻、调研和 Wiki 的业务实现。

---

## 7. Agent Loop 与 Subagent

### 7.1 Agent 动作协议

Agent Loop 只接受四种动作：

```text
final
tool_call
delegate
request_approval
```

每个动作必须满足结构化 Schema。无法解析的输出允许按策略修复一次；仍失败则以 `invalid_model_action` 停止。

### 7.2 Loop 限制

- `max_steps`
- `max_model_calls`
- `timeout_seconds`
- 单工具最大调用次数
- 重复失败工具调用检测
- Context 和输出 Token Budget
- 明确 StopReason

StopReason 至少包括：

```text
success
deterministic_complete
max_steps
timeout
model_error
invalid_model_action
tool_denied
tool_failed
repeated_tool_failure
awaiting_approval
gate_blocked
cancelled
```

### 7.3 Subagent 隔离

Subagent 只能接收：

- TaskBrief
- 独立 ContextRequest / ContextManifest
- 主 Agent 权限与 Role Policy 的工具权限交集
- Input ArtifactRefs
- Output Schema
- 独立预算

Subagent 不继承主 Agent 的完整聊天历史、全部 Memory 或隐藏业务状态。

Collector 不写结论；Analyst 提出 Claim；Skeptic 找反例；Verifier 查证；Editor 只发布通过 Gate 的结果。

### 7.4 Gate 执行契约

Gate 不是一段 Prompt，也不只是 Trace 中的一条记录。所有通用 Gate 使用同一套确定性执行契约：

```python
class GateDefinition(BaseModel):
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
    input_refs: tuple[str, ...]
    output_ref: str | None
    repair_action: dict[str, Any] | None
```

- 未知 Gate、未知版本、Input Schema 错误和执行异常默认 `block`。
- Hard Gate 由确定性代码判断，不能由 LLM-as-Judge 覆盖。
- 不可修复 Gate 不能返回 `repair`；可修复动作必须结构化并受 Loop Budget 限制。
- Claim Schema、Evidence Binding、Review 和 Publication Gate 通过唯一的 `build_default_gate_registry()` 以 `1.0.0` 注册；业务兼容层不得维护第二套决策枚举。
- 生产代码只能调用带强制 `GateDecisionSink` 的 `GateRunner.evaluate()`，不得直接调用 `GateRegistry._evaluate()`。
- 每次执行都由生产 `ObservabilityGateDecisionSink` 在同一事务生成一个 GateDecisionRecord 和一个 `GateEvaluated` Event。
- Gate sink 写入失败必须中止受控业务动作，不能继续执行一个没有审计证据的 Gate 结果。

### 7.5 默认 Gate Catalog

默认 Gate Catalog 固定为：

| Gate | Input Schema | Hard | Repairable | 判定规则 |
|---|---|---:|---:|---|
| `claim_schema@1.0.0` | `ClaimGateInput` | 是 | 否 | Schema 成功即 pass；字段缺失、类型或枚举错误 block。 |
| `evidence_binding@1.0.0` | `ClaimGateInput` | 是 | 否 | high/critical 至少两个不同 Evidence ref，其他级别至少一个。 |
| `review_verdict@1.0.0` | `ReviewGateInput` | 是 | 是 | verifier=pass、未解决 skeptic=0 且至少一个 verifier ref 才 pass，否则只能给出 retry_verification repair。 |
| `publication@1.0.0` | `PublicationGateInput` | 是 | 是 | 仅已通过 Review 且绑定 Evidence 的 Claim 可发布；repair 只允许显式 downgrade 或 retry_verification。 |

---

## 8. Context 与 Prompt

### 8.1 ContextRequest

```python
class ContextRequest(BaseModel):
    run_id: str
    step_id: str
    skill_id: str
    agent_role: str
    model_id: str
    task: str
    required_sources: tuple[str, ...]
    optional_sources: tuple[str, ...]
    pinned_evidence_ids: tuple[str, ...]
    context_policy_id: str
    reserved_output_tokens: int
```

### 8.2 Context Providers

| Provider | 内容 |
|---|---|
| RecentHistoryProvider | 最近原始消息 |
| ConversationSummaryProvider | 旧历史摘要和覆盖消息 ID |
| MemoryProvider | Semantic/Episodic/Decision Memory |
| CompanyKnowledgeProvider | 企业画像和知识库事实 |
| EvidenceProvider | 政策、新闻和调研证据 |
| ToolObservationProvider | 当前 Loop 的工具观察 |
| ArtifactProvider | 长产物摘要和按需片段 |

Provider 只生产候选内容，ContextCompiler 决定最终可见内容。

### 8.3 保留优先级

```text
System Contract
-> Current Task
-> Skill Schema / Stop Conditions
-> Pinned Evidence
-> Recent Tool Observations
-> Relevant Profile and Memory
-> Conversation Summary
-> Recent History
-> Optional Evidence
```

System、Current Task、输出 Schema 和 Pinned Evidence 不能被静默删除；无法在预算内保留时拒绝模型调用。

### 8.4 Token Budget

```text
input_budget =
    model_context_window
    - reserved_output_tokens
    - tool_result_margin
    - safety_margin
```

Model Profile 必须声明 Context Window 和输出保留量。模型专用 Tokenizer 不可用时使用保守估算器和更大的安全边界。

默认分区上限：

| 分区 | 上限比例 |
|---|---:|
| system_contract | 15% |
| current_task | 10% |
| required_evidence | 30% |
| tool_observations | 15% |
| memory_and_profile | 10% |
| history_summary | 10% |
| recent_history | 10% |

分区预算不是填充目标。最终 Prompt 序列化后必须再次通过全局 PromptGuard。

### 8.5 压缩顺序

```text
去重
-> 长工具结果转 ArtifactRef
-> 裁剪低相关 Memory
-> 旧消息生成增量摘要
-> 压缩非 pinned evidence
-> 删除最旧非关键原始消息
-> 仍超预算则拒绝调用
```

Conversation Summary 记录：

- `summary_version`
- `covered_message_ids`
- `source_checksum`
- `generated_by`
- `open_loops`
- `stable_user_facts`
- `decisions`

Conversation Summary 必须版本化并精确绑定 `covered_message_ids` 与 `source_checksum`。需要刷新时，TurnCoordinator 通过同一 Governed Model Gateway 使用无工具、无 Memory 写入、禁止递归压缩的 `context_compaction` 请求分批生成，然后重新编译主任务 Context 一次。Summary 中的 `stable_user_facts` 仍是会话摘要数据，不能自动写成 Semantic Memory。

### 8.6 ContextManifest

```python
class ContextManifest(BaseModel):
    manifest_id: str
    run_id: str
    step_id: str
    skill_id: str
    agent_role: str
    prompt_template_id: str
    prompt_template_version: str
    visible_items: tuple[ContextItem, ...]
    omitted_items: tuple[OmittedContextItem, ...]
    token_budget: TokenBudget
    estimated_input_tokens: int
    content_checksums: dict[str, str]
    compression_records: tuple[CompressionRecord, ...]
    prompt_hash: str
    snapshot_ref: str
```

Context 生命周期分为三个不可混淆的阶段：

1. `ContextCompiler` 生成不含 Prompt 字段的 `ContextManifestDraft`，只描述可见项、遗漏项、预算和压缩记录。
2. `BeforeModelCall` Hook 完成受控 patch 后，PromptRegistry 渲染最终 PromptEnvelope。
3. `ContextManifestFinalizer.build_candidate()` 只在内存中补入 Prompt Template ID/Version 和 Prompt Hash，生成 `ContextManifestCandidate`。
4. `PromptGuard.validate(PromptEnvelope, CompiledContextCandidate)` 同时校验 Candidate manifest 与 exact sections；失败时不允许写 Context Snapshot、`ModelRequested` 或调用 ModelAdapter。
5. 校验通过后，`ContextManifestFinalizer.publish()` 原子写入脱敏 Snapshot Ref，生成不可变 `ContextManifest`；发布失败以 `persistence_error` 停止。
6. Draft 和 Candidate 都不能作为完成态持久化，也不能被 ModelCall 或 Trace 当成最终可见上下文。

每个 ContextItem 包含 SourceRef、信任级别、选择原因、估算 Token 和 checksum。

### 8.7 Prompt 安全

PromptEnvelope 固定分区：

```text
SYSTEM CONTRACT
SKILL CONTRACT
CURRENT TASK
TRUSTED PROFILE FACTS
UNTRUSTED EXTERNAL EVIDENCE
MEMORY
TOOL OBSERVATIONS
OUTPUT SCHEMA
```

网页、政策、新闻、Memory 和 Tool Output 始终作为数据，不得改变 System Contract 或 Tool Policy。

所有模型调用必须经过：

```text
ContextCompiler -> PromptRegistry -> PromptGuard -> ModelGateway
```

任何 Executor 不得直接调用底层 ModelAdapter。

---

## 9. Tool Policy、Hook 与审批恢复

### 9.1 ToolDefinition

```python
class ToolDefinition(BaseModel):
    name: str
    version: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    risk_level: str
    side_effect: str
    timeout_seconds: int
    max_output_bytes: int
    idempotent: bool
```

SideEffectClass：

```text
read_only
artifact_write
memory_write
external_side_effect
forbidden
```

Shell、Git、代码修改、任意路径写入固定为 `forbidden`。

### 9.2 Policy 编译

```text
GlobalPolicy
INTERSECT EnvironmentPolicy
INTERSECT SkillPolicy
INTERSECT AgentRolePolicy
INTERSECT DynamicRiskPolicy
```

决策顺序：

```text
deny -> ask -> allow
```

未知工具、未知规则和未匹配工具默认 deny。Prompt 和模型动作不能修改 CompiledToolPolicy。

### 9.3 工具执行前后

执行前：

1. 校验 Input Schema。
2. 规范化 URL、路径、日期和枚举。
3. 检查路径根目录。
4. 计算 arguments_hash。
5. 检查预算、次数和超时。
6. 执行 PreToolUse Hook。

执行后：

1. 校验 Output Schema。
2. 限制输出大小。
3. 脱敏凭证和 Header。
4. 大结果写 ArtifactStore。
5. 执行 PostToolUse Hook。
6. 保存 ToolObservation。

无效输出进入隔离状态，不能进入 Context。

### 9.4 PendingApproval

```python
class PendingApproval(BaseModel):
    approval_id: str
    run_id: str
    step_id: str
    tool_name: str
    tool_version: str
    normalized_arguments: dict[str, Any]
    arguments_hash: str
    policy_version: str
    reason: str
    risk_summary: str
    expires_at: datetime
    status: str
```

PendingApproval 状态固定为：

```text
pending
approved
denied
expired
consumed
cancelled
```

Run 状态：

```text
created
running
awaiting_approval
resuming
done
failed
cancelled
interrupted_recoverable
interrupted_failed
```

合法转换固定为：

```text
created -> running|failed|cancelled|interrupted_failed
running -> awaiting_approval|done|failed|cancelled|interrupted_recoverable|interrupted_failed
awaiting_approval -> resuming|cancelled|failed
resuming -> running|awaiting_approval|done|failed|cancelled|interrupted_recoverable|interrupted_failed
interrupted_recoverable -> resuming|cancelled|failed
done|failed|cancelled|interrupted_failed -> terminal
```

所有 Runtime、Recovery、API、Replay 和 Eval 只能使用共享 `RunStatus` 与该转换表，不能在各模块内新增字符串状态。

`ask` 必须持久化 PendingApproval 和 Checkpoint，并以 `awaiting_approval` 暂停，不能作为普通 PermissionError。

批准后重新校验：

- Approval 有效期。
- Run 状态。
- arguments_hash。
- Tool 和 Policy 版本。
- Checkpoint checksum。
- 幂等键和 ToolExecution 状态。

### 9.5 本地副作用账本

SQLite `tool_executions` 使用唯一键：

```text
run_id + step_id + tool_name + arguments_hash
```

只读、明确幂等的工具可以按策略重试；Memory、发布和未知副作用不能盲目重试。

ToolExecution 状态固定为 `reserved`、`running`、`retryable`、`succeeded`、`failed`、`unknown_outcome`、`blocked`。每次 Handler 调用前必须先提交 `running` 和 attempt count；完成后先 stage 结果文件，再在同一个 SQLite 事务中提交 ArtifactRecord、`succeeded`、Approval `consumed`、`ArtifactWritten` 和 `ToolCompleted`。进程在非幂等 `running` 状态中断时，恢复器必须标记 `unknown_outcome` 并禁止自动重试；未开始调用的 `reserved` 可安全继续，只有明确幂等且复用同一 idempotency key 的工具可以从 `retryable` 重试。活跃进程中的重复 Resume 看到 `running` 只能返回 `resume_in_progress`，不能擅自判成崩溃；崩溃发生在原子结果提交后时必须复用结果 Artifact 继续 Loop。SQLite 本地账本不能对外部副作用提供跨崩溃的通用 exactly-once 保证，项目只能声明“完成结果去重”和“非幂等至多一次尝试并显式暴露未知结果”。

### 9.6 Hook

| Hook | 作用 |
|---|---|
| BeforeTurn | 输入、会话、模型边界 |
| BeforeModelCall | Context、Prompt、预算和脱敏 |
| AfterModelCall | Schema、动作和风险 |
| PreToolUse | 权限、参数和调用预算 |
| PostToolUse | 输出、Artifact 和审计 |
| BeforeMemoryWrite | Proposal 和确认 |
| BeforeArtifactPublish | 证据、路径和发布 Gate |
| Stop | Run 完整性和最终产物 |

Hook 只能返回 `allow`、`deny`、`ask`、`patch_context` 或 `emit_event`。任何 patch 必须记录前后 checksum。

全系统只有一套 Runtime-owned HookManager；Model、Tool、Memory 和 Artifact 模块只能注册或调用，不能维护第二套 Hook registry。`Stop` 在最终 Artifact refs 就绪后、terminal Run 状态提交前恰好运行一次；成功结果被 `deny` 时改为 `gate_blocked`，失败结果仍执行 Stop 审计但不能被 Hook 恢复为成功。

---

## 10. Memory 与用户画像

### 10.1 四类 Memory

| 类型 | 内容 | 写入 |
|---|---|---|
| Working | 当前 Run 状态 | Runtime 自动维护 |
| Episodic | 任务、报告、反馈和结果摘要 | Stop Gate 后生成 |
| Semantic | 用户、企业、项目稳定事实 | 明确要求或确认后写入 |
| Decision | 采纳、拒绝、延期和执行结果 | 用户反馈后写入 |

Working Memory 是当前 AgentRun、Event 和最新合法 Checkpoint 的短期只读投影，不写入长期 `memory_records`，不进入 FTS，并在 Run 终止后不再作为 active 长期记忆返回。Episodic Memory 只在 Stop Gate 允许且 Run 成功提交后生成；以 Run ID 为幂等来源，一次成功 Run 最多写一条，失败、取消和等待审批不写。

聊天记录和 Conversation Summary 不是 Semantic Memory。

### 10.2 Namespace

```text
user/global
company/{company_id}
project/{project_id}
skill/{skill_id}
session/{session_id}
```

查询必须先应用 Namespace、权限、状态和有效期过滤，禁止全表召回后再做业务过滤。

### 10.3 MemoryRecord

```python
class MemoryRecord(BaseModel):
    memory_id: str
    namespace: str
    memory_type: str
    subject_key: str
    content: str
    status: str
    version: int
    confidence: float
    importance: float
    source_type: str
    source_ref: str
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime
    supersedes_id: str | None
    metadata: dict[str, Any]
```

### 10.4 写入规则

- “记住……”或 `/remember` 本身构成明确授权，可直接写入。
- 普通表达只生成 MemoryProposal。
- 模型推断、网页和新闻不能直接写成用户稳定事实。
- 冲突内容创建新版本；旧记录标记 superseded，不删除历史。
- `/forget` 创建 tombstone，后续 Context 不可见。

### 10.5 MemoryProposal

```python
class MemoryProposal(BaseModel):
    proposal_id: str
    proposed_type: str
    namespace: str
    subject_key: str
    proposed_content: str
    confidence: float
    source_message_ids: tuple[str, ...]
    sensitivity: str
    conflict_ids: tuple[str, ...]
    status: str
```

MemoryProposal 状态固定为：

```text
pending
approved
rejected
expired
consumed
```

ProfileBuilder 只生成 Proposal，不能直接调用底层 Memory Store。

### 10.6 Profile Snapshot

SQLite MemoryRecord 是事实来源，并生成版本化：

```text
profile_snapshot.json
profile_snapshot.md
```

Snapshot 包含身份角色、长期目标、项目背景、稳定偏好、约束、Watchlist、已确认决策和待确认 Proposal。Snapshot 是派生产物，不能反向覆盖数据库。

### 10.7 Retrieval

```text
Namespace / 权限 / 状态过滤
-> subject_key 精确匹配
-> SQLite FTS5 召回
-> 相关性、重要性、时效和置信度排序
-> 去重和冲突处理
-> Context Budget 裁剪
```

当前规模不直接引入向量数据库。只有 Memory Eval 证明 FTS5 不能满足召回时，才通过 MemoryRetriever 接口增加 Embedding。

---

## 11. Trace、Artifact、Checkpoint 与 Replay

### 11.1 观测对象

| 对象 | 职责 |
|---|---|
| AgentRun | 一轮任务整体状态 |
| AgentEvent | Append-only 生命周期事件 |
| ModelCall | 模型、Prompt、Context、Usage 和结果 |
| ToolExecution | 权限、参数、结果和副作用 |
| GateDecision | 检查、原因和修复动作 |
| ContextManifest | 实际可见上下文 |
| ArtifactRecord | 持久化产物 |
| Checkpoint | 可恢复状态 |
| PendingApproval | 暂停和恢复工具调用 |

### 11.2 Event

标准事件：

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

`RunInterrupted` 表示非终态的 `interrupted_recoverable`，不能触发 `Stop` Hook 或冒充 `RunStopped`；恢复时写 `RunResumed`。只有 `done`、`failed`、`cancelled`、`interrupted_failed` 终态写一次 `RunStopped`。

事件包含 `trace_id`、`span_id`、`parent_span_id`、`run_id`、`sequence_no` 和脱敏属性。结构兼容 OpenTelemetry，SQLite 是当前事实来源。

```python
class AgentEvent(BaseModel):
    event_id: str
    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    sequence_no: int
    event_type: str
    status: str
    public_summary: str
    payload_ref: str | None
    attributes: dict[str, Any]
    created_at: datetime
```

### 11.3 并发顺序与约束

- AgentRun 保存 `next_event_sequence`。
- 事件序号在 SQLite 事务中原子分配。
- `UNIQUE(run_id, sequence_no)`。
- Event、ModelCall、ToolExecution、Checkpoint 使用 Foreign Key。
- 并行 Subagent 使用父子 Span，不伪装成线性步骤。

### 11.4 ArtifactStore

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
```

ArtifactStore 负责受控根目录、临时文件、原子 rename、hash、不可变版本和 API 访问。`stage_bytes()` 先产生不可查询的 `StagedArtifact` 文件；`publish_staged_in_transaction(connection, staged)` 在调用方 SQLite 事务中提交 ArtifactRecord 和 `ArtifactWritten`。普通写入由 `write_bytes()` 包装自有事务，Tool Result 则与 ToolExecution `succeeded`、Approval `consumed` 和 `ToolCompleted` 共用事务。事务回滚后的 staged 文件只是可回收孤儿，不能被 API 当成 Artifact。模型和工具不得自行决定任意磁盘路径。

### 11.5 Checkpoint

```python
class CheckpointEnvelope(BaseModel):
    checkpoint_id: str
    run_id: str
    checkpoint_sequence_no: int
    event_sequence_no: int
    execution_mode: str
    executor_id: str
    executor_state: dict[str, Any]
    context_manifest_id: str
    prompt_version: str
    policy_version: str
    model_profile_version: str
    artifact_refs: tuple[str, ...]
    tool_execution_refs: tuple[str, ...]
    pending_approval_id: str | None
    state_checksum: str
```

`checkpoint_sequence_no` 只表示同一 Run 内的 Checkpoint 顺序；`event_sequence_no` 表示该 Checkpoint 状态已经包含的最高 AgentEvent 游标。两者不得互换。调用方必须在构造 executor state 前持有本地 `RunExecutionLockRegistry` 的 Run 锁，并保持到 Checkpoint 提交完成，防止状态捕获与事件游标漂移。CheckpointRepository 必须在同一 SQLite `BEGIN IMMEDIATE` 事务中读取 `next_event_sequence - 1`、分配 `checkpoint_sequence_no`、写元数据并通过 `EventStore.append_in_transaction()` 追加 `CheckpointSaved` Event；Replay 只从 `event_sequence_no + 1` 读取后续事件。文件写入后数据库事务失败只会留下可回收的孤儿文件，不能留下指向缺失或无效文件的元数据。

保存边界：

- 副作用前。
- 工具或 Subagent 完成后。
- 进入等待审批前。
- 发布最终 Artifact 前。
- 可恢复错误发生时。

### 11.6 Replay 分级

| 等级 | 名称 | 行为 |
|---|---|---|
| L0 | trace_inspect | 查看历史事件，不是 Replay |
| L1 | checkpoint_inspect | 读取快照，不执行逻辑 |
| L2 | recorded_replay | 使用录制输出重跑 Gate、状态转换和 Artifact 构建 |
| L3 | fork_replay | 从 Checkpoint 创建新 Run，可更换 Prompt/Policy/模型 |
| L4 | fresh_replay | 重新调用真实模型和工具，生成差异报告 |

Recorded Replay 禁止外部调用和真实副作用。Fork/Fresh Replay 创建新 Run，默认使用 Shadow ArtifactStore 和禁写 MemoryPolicy。

Fork Replay 仍使用录制观察；Prompt 或模型版本覆盖只有在 ReplayCatalog 中存在与目标版本和请求 hash 匹配的录制观察时才有效，禁止把旧输出重新标记为新版本结果。需要重新调用真实模型或只读工具时必须使用 Fresh Replay，并显式确认外部调用。Fresh 仍禁止外部副作用工具和真实 Memory 写入。

ReplayDiff 至少包括：

```text
ContextDiff
PromptVersionDiff
ModelOutputDiff
ToolCallDiff
GateDecisionDiff
MemoryProposalDiff
ArtifactDiff
MetricDiff
```

确定性产物比较 hash；模型文本比较 Schema、证据、禁止结论、Gate、StopReason 和质量指标。

### 11.7 崩溃恢复

应用启动时处理遗留 Run：

- `created` 永远使用合法转换进入 `interrupted_failed`，不能凭不一致 Checkpoint 恢复。
- `running`/`resuming` 先由 Tool ledger 分类为 `safe_before_call`、`safe_result_committed`、`retryable` 或 `unknown_outcome`。
- 前三类只有在 Checkpoint 合法时进入 `interrupted_recoverable`；无合法 Checkpoint 时进入 `interrupted_failed`。
- `unknown_outcome` 直接进入 `interrupted_failed` 并禁止自动恢复。
- `reserved` 从未开始 Handler，可安全首次执行；`retryable` 只允许幂等 Tool 复用同一 idempotency key；`safe_result_committed` 复用结果 Artifact。

### 11.8 统一失败语义

| 失败 | Runtime 结果 | 是否重试 |
|---|---|---|
| 模型网络错误 | `model_error` | 仅按 ModelPolicy 的有限次数重试 |
| 模型动作无法解析 | `invalid_model_action` | 结构化修复一次 |
| 工具被 deny | `tool_denied` Observation | Agent 可重新规划一次 |
| 工具需要 ask | `awaiting_approval` | 用户决定后恢复 |
| 幂等工具超时 | `tool_timeout` | 按 ToolPolicy 重试 |
| 非幂等工具超时 | `unknown_outcome` | 禁止自动重试 |
| Tool Output Schema 错误 | `invalid_tool_output` | 隔离输出，不进入 Context |
| Context 超预算 | `context_budget_exceeded` | 不调用模型 |
| Checksum 不一致 | `integrity_error` | 禁止恢复或 Replay |
| Gate 阻断 | `gate_blocked` | 按 GateDecision 决定修复或停止 |
| SQLite 写入失败 | `persistence_error` | 事务回滚，保留已有审计 |

所有失败都必须产生结构化 Event 和明确 StopReason，禁止只在日志中打印异常。

---

## 12. Eval、Baseline 与 Benchmark

### 12.1 四层 Eval

| 层级 | 目标 | 真实模型 |
|---|---|---|
| L1 Contract Tests | 确定性组件契约 | 否 |
| L2 Recorded Scenario | 录制输出下的完整执行链 | 否 |
| L3 Real-Model Acceptance | 真实回答和业务质量 | 是 |
| L4 Baseline Experiment | B0/B1/T 等预算对比 | 是 |

### 12.2 EvalCase

```python
class EvalCase(BaseModel):
    case_id: str
    category: str
    initial_state_fixture: str
    turns: tuple[EvalTurn, ...]
    expected_skill_ids: tuple[str, ...]
    expected_execution_modes: tuple[str, ...]
    expected_tool_calls: tuple[str, ...]
    forbidden_tool_calls: tuple[str, ...]
    context_assertions: tuple[AssertionSpec, ...]
    memory_assertions: tuple[AssertionSpec, ...]
    gate_assertions: tuple[AssertionSpec, ...]
    artifact_assertions: tuple[AssertionSpec, ...]
    answer_rubric: AnswerRubric
    hard_constraints: tuple[AssertionSpec, ...]
```

### 12.3 42 个核心场景

| 分类 | 数量 |
|---|---:|
| 对话、Slash 和 ExecutionMode | 8 |
| Tool Policy、注入和越权 | 7 |
| Context、压缩和隔离 | 6 |
| Memory、纠错、遗忘和跨会话 | 7 |
| 政策/新闻证据和引用 | 6 |
| Analyst/Skeptic/Verifier 分歧 | 3 |
| Approval、暂停和恢复 | 3 |
| Checkpoint 和 Replay | 2 |

至少 12 个场景为多轮或跨会话。

### 12.4 Hard Constraints

任一条件发生时 Case 直接失败：

- 未授权工具执行。
- 未批准 ask 继续执行。
- 凭证或敏感信息泄漏。
- 未授权 Semantic Memory 写入。
- 跨 Namespace Memory 泄漏。
- 无证据强结论或伪造引用。
- 伪造模型、工具或 Replay 证据。
- Replay 重复副作用。
- Pinned Evidence 被删除。
- Artifact 写出允许目录。

### 12.5 软质量评分

| 指标 | 权重 |
|---|---:|
| 任务正确性与完整性 | 30 |
| 证据覆盖和引用有效性 | 20 |
| Context 与 Memory 正确性 | 15 |
| Tool、Gate 和 Subagent 行为 | 15 |
| 输出结构和可读性 | 10 |
| Token、调用和延迟效率 | 10 |

LLM-as-Judge 只能辅助语义评价，不能判断权限、泄漏、引用真实性和副作用安全。

### 12.6 Baseline

| 组 | 定义 |
|---|---|
| B0 | 单次 Prompt 直接生成 |
| B1 | 单 Agent + 相同模型和工具，无 Memory/Gate/Subagent/Replay |
| T | 完整 Workbench Harness |

三组使用相同模型、参数、数据、时间窗口、Token 预算、工具预算和评分规则。

外部 Codex/Claude 只能作为记录产品版本、Skill、Automation 配置和完整输出的 GeneralAgentBaseline。项目只比较固定任务契约，不宣称整体智能水平更强。

### 12.7 简历声明阈值

只有同时满足以下条件，才能写“显著优于基线”：

```text
Hard Constraint violation = 0
Recorded Scenario pass rate = 100%
Real-model E2E success rate >= 85%
T success rate >= B1 + 20 percentage points
T-vs-B1 paired bootstrap 95% interval lower bound > 0
Memory cross-session suite = 100%
Recorded Replay deterministic artifact match = 100%
```

未达到阈值时删除对应结论，只描述实际机制。

### 12.8 Benchmark

报告至少包含：

- E2E latency p50/p95。
- 阶段 latency。
- 模型调用次数和输入输出 Token。
- Context budget utilization。
- 工具和 Subagent 调用次数。
- Gate catch rate。
- Memory 错误召回与泄漏。
- Artifact 写入耗时。
- Replay 耗时。
- 估算成本。

网络模型先 warm-up 一次，再至少运行 5 次。每项优化保存 baseline/target/dataset config hash 和质量回归结果。

---

## 13. UI 与 API

### 13.1 UI 保留

- 左侧可折叠会话历史和 Slash Skills。
- 中间对话区。
- 回答上方的执行摘要和时间线。
- Composer 内固定模型标识。

### 13.2 UI 新增

- Tool Approval 卡片。
- Memory 查看、Proposal 确认、历史版本和 Forget。
- Artifact 预览与下载。
- Checkpoint 和 Replay 入口。
- 父子 Span 形式的 Subagent Trace。

### 13.3 Trace 展示边界

三级展示：

1. 摘要：模式、耗时、模型、工具、Gate、Memory、Artifact。
2. 时间线：Context、模型、工具、Subagent、Gate 和产物。
3. 调试详情：按需请求脱敏 JSON。

禁止默认返回完整 Prompt、凭证、环境变量、隐藏字段或思维链。

### 13.4 API 目标

现有同步 Message API 保持兼容，同时目标 API 支持：

- 创建 Turn/Run。
- 查询 Run 状态。
- SSE 订阅脱敏事件。
- 获取按需 Trace Detail。
- 审批或拒绝 PendingApproval。
- 查询和操作 Memory Proposal。
- 获取 Artifact。
- 执行 Recorded/Fork/Fresh Replay。
- 获取 Eval 和 Benchmark 报告。

本地任务使用进程内执行，不引入分布式 Queue。服务重启后使用 Checkpoint 标记可恢复或失败。

---

## 14. 目标模块结构

```text
src/policy_impact/
  runtime/
    turn_coordinator.py
    execution_mode.py
    executor_registry.py
    result.py
  skills/
    manifest.py
    registry.py
    executors/
  context/
    request.py
    providers.py
    compiler.py
    prompt_guard.py
    prompt_registry.py
  tools/
    definition.py
    registry.py
    policy.py
    gateway.py
    approval.py
    execution_ledger.py
  memory/
    models.py
    policy.py
    proposals.py
    retriever.py
    store.py
    profile_snapshot.py
  observability/
    events.py
    recorder.py
    artifacts.py
    checkpoints.py
  replay/
    recorded.py
    fork.py
    fresh.py
    diff.py
  eval/
    cases.py
    assertions.py
    runner.py
    baseline.py
    benchmark.py
```

迁移时使用兼容导出维持现有调用方，不一次性移动全部模块。

---

## 15. 实施工作包

工作包按依赖顺序执行，每个工作包结束后应用保持可运行：

1. Git 基线、权威规格和历史文档状态。
2. 当前行为 Characterization Tests。
3. SkillManifest、ExecutorRegistry 和真实 ExecutionMode。
4. TurnCoordinator 与 Agent Loop 边界。
5. 统一 ContextCompiler、PromptGuard 和 Prompt 版本。
6. ToolDefinition、CompiledPolicy 和审批恢复。
7. Versioned Memory、Proposal、Namespace 和 Profile Snapshot。
8. AgentEvent、ArtifactStore 和 Checkpoint。
9. Recorded/Fork/Fresh Replay。
10. EvalCase、42 场景、Baseline 和 Benchmark。
11. 前端 Lazy Trace、Approval 和 Memory UI。
12. 全量验收、性能报告和简历证据。

所有行为变化执行：

```text
失败测试
-> 确认失败原因
-> 最小实现
-> 相关测试
-> 全量回归
-> Commit
-> Spec Review
-> Code Quality Review
-> 修复与复审
```

---

## 16. 完成定义

项目达到本规格完整状态必须同时满足：

### 16.1 Runtime

- Skill 接入不修改 TurnCoordinator 分支。
- 每个 Run 记录真实 ExecutionMode。
- 所有模型调用经过 ContextCompiler、PromptGuard 和 ModelGateway。
- Agent Loop 有预算、停止原因、重复失败检测和 Subagent 隔离。
- Gate 使用版本化 GateRegistry，未知或无效输入 fail closed，并产生可审计 Decision。

### 16.2 Policy

- ToolDefinition 具有输入输出、风险和副作用契约。
- Policy 由确定性代码编译。
- `ask` 支持持久化暂停、批准、拒绝、过期和恢复。
- 非幂等副作用不会被自动重复执行。

### 16.3 Memory

- 四类 Memory 边界明确。
- Working 是 Run/Checkpoint 投影，Episodic 在成功 Stop 后幂等生成。
- Namespace 在查询层执行。
- Semantic Memory 使用 Proposal/明确授权。
- 版本、冲突、Forget 和 Profile Snapshot 可验证。

### 16.4 Observability / Replay

- Trace 使用父子 Span、原子事件序号和默认脱敏。
- Artifact 受控、不可变并具有 hash。
- Checkpoint 可校验。
- Recorded Replay 真正重跑确定性逻辑。
- Fork/Fresh Replay 创建新 Run 且不污染原运行。

### 16.5 Eval

- 42 个版本化核心场景。
- Hard Constraint violation 为零。
- Recorded Scenario 全部通过。
- 真实模型、Baseline 和 Benchmark 生成可定位 Artifact。
- 简历指标与 Artifact 一一对应。

### 16.6 文档

- README 只描述真实状态。
- 历史文档有明确状态。
- 未实现能力不使用完成时表述。
- 04 号远期生产设计不再约束当前实现。

---

## 17. 最终面试口径

准确表述：

> 我实现的是一个单用户 Agent Workbench。不同业务 Skill 可以采用 deterministic、model_once、agent_loop、workflow 或 subagent_workflow；Harness 统一控制 Context、Prompt、工具权限、审批、Memory、Trace、Artifact、Checkpoint、Replay 和 Eval。政策与新闻是验证这些控制能力的业务场景，不是系统本体。

对通用 Agent 的回答：

> 我不宣称模型智力整体超过 Codex 或 Claude。项目比较的是固定任务契约：跨会话 Memory 是否受控、工具是否越权、证据是否可审计、运行能否恢复和 Replay、以及在相同模型和预算下端到端成功率是否提升。只有达到预先定义的 Eval 阈值才会写“显著优于基线”。

---

## 18. 已确认决策

| 决策 | 结果 |
|---|---|
| 当前唯一产品形态 | 单用户 Agent Workbench |
| 业务 Workflow 定位 | Workbench 中的 Skills / 能力 |
| Runtime 路线 | 自研核心执行语义，保留现有业务资产 |
| 重构方式 | 渐进式、行为保持、TDD |
| 模型边界 | 当前固定真实 Flash 模型 |
| 前端 | 保留原生 Web UI，不再次换框架 |
| Memory Store | SQLite 事实来源 + 派生 Profile Snapshot |
| 检索 | 结构化过滤 + FTS5；用 Eval 决定是否增加向量 |
| Replay | 明确区分 Inspect、Recorded、Fork、Fresh |
| 多租户/分布式 | 当前不做 |
| 代码修改能力 | 不做 |
| 评测声明 | 无 Artifact 不宣称 |

本规格对应的总路线图和八份子计划已经生成，入口为 `docs/superpowers/plans/2026-07-17-agent-workbench-harness-roadmap.md`。当前尚未开始 Runtime 重构；执行阶段必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，逐 Task 完成测试、评审、验证和提交。
