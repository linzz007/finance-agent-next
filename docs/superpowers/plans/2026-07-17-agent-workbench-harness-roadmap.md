# Agent Workbench Harness Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement the linked plans task-by-task. Do not execute two plans that modify the same files in parallel.

**Goal:** 将当前可运行的 `policy_impact` Workbench 渐进式重构为 Canonical Spec 定义的完整单用户 Agent Harness，并在每个阶段保留可运行、可测试、可回滚的系统。

**Architecture:** 八份子计划按 Runtime -> Context -> Observability -> Tool Governance -> Memory -> Replay -> Eval -> UI 的依赖顺序执行。每份计划只拥有明确文件边界，并通过兼容导出保持当前应用持续可运行。

**Tech Stack:** Python 3.10+、Pydantic v2、FastAPI、SQLite/FTS5、原生 HTML/CSS/JavaScript、pytest、Git/Superpowers。

**Canonical Spec:** `docs/superpowers/specs/2026-07-17-agent-workbench-harness-design.md`

**Baseline Commit:** `74780c0`

**Design Commit:** `b7e2538`

## Global Constraints

- Python 版本保持 `>=3.10`。
- 固定真实模型为 `deepseek-v4-flash`；没有鉴权时拒绝执行。
- 保留 FastAPI 和原生 HTML/CSS/JavaScript Workbench，不再次更换前端技术栈。
- SQLite 是当前事实来源；不引入 PostgreSQL、S3、分布式 Queue、Lease 或 Fencing Token。
- 不实现多租户、Shell、Git、代码修改、任意目录写入和高风险外部通知。
- 所有模型调用必须经过 `ContextCompiler -> PromptRegistry -> PromptGuard -> ModelGateway`。
- 未知工具、未知策略、未知模型和校验失败一律 fail closed。
- Trace 不保存或展示隐藏思维链，只记录动作、观察、决策摘要和停止原因。
- 所有行为变化执行 Red -> Green -> Refactor；每个 Task 独立提交。
- 没有版本化 Eval/Benchmark Artifact，不得写入简历性能结论。

## Why Multiple Plans

Canonical Spec 同时覆盖 Runtime、Context、Tool Governance、Memory、Observability、Replay、Eval 和 UI。把所有改动写在一个计划中会造成三个问题：

1. Reviewer 无法单独拒绝某个子系统而保留其他工作。
2. 多个实现者容易同时修改 `agent_runtime.py`、`store.py` 和 `app.js`。
3. 中途失败时没有可运行的稳定检查点。

因此按依赖关系拆为八份计划。每份计划结束都必须保持应用可启动、旧 API 兼容和全量回归通过。

## Execution Order

| 顺序 | 计划 | 主要交付 | 开始条件 | 阶段完成证据 |
|---:|---|---|---|---|
| 1 | `2026-07-17-runtime-kernel.md` | ExecutionMode、SkillManifest、ExecutorRegistry、TurnCoordinator、受控 Agent Loop | 当前基线 | 新 Skill 不修改 Coordinator；每个 Run 模式真实 |
| 2 | `2026-07-17-context-prompt.md` | Provider、ContextCompiler、PromptRegistry、PromptGuard | 计划 1 完成 | 所有模型调用统一过硬门控 |
| 3 | `2026-07-17-observability-foundation.md` | AgentEvent、ArtifactStore、Checkpoint 与崩溃标记 | 计划 1-2 完成 | Event 有序、Artifact 受控、Checkpoint checksum 可验证 |
| 4 | `2026-07-17-tool-policy-approval.md` | ToolDefinition、CompiledPolicy、Tool Hook 接入、PendingApproval、Resume | 计划 1-3 完成 | `ask` 可暂停/恢复，完成结果去重，未知副作用阻断重试 |
| 5 | `2026-07-17-versioned-memory.md` | 四类 Memory、Namespace、Proposal、版本、Forget、Profile Snapshot | 计划 1-4 完成 | 跨 Namespace 泄漏为零，未授权稳定事实写入为零 |
| 6 | `2026-07-17-replay.md` | Recorded/Fork/Fresh Replay 与结构化 Diff | 计划 1-5 完成 | Recorded Replay 无外部副作用且确定性 Artifact hash 一致 |
| 7 | `2026-07-17-eval-benchmark.md` | 42 Case、四层 Eval、B0/B1/T、Benchmark Artifact | 计划 1-6 完成 | Recorded Scenario 100%，真实模型结果有版本化证据 |
| 8 | `2026-07-17-workbench-api-ui.md` | Run/SSE、Lazy Trace、Approval、Memory、Artifact、Replay UI | 计划 1-7 完成 | 桌面/移动端 UI 验收和 API 契约测试通过 |

## Cross-Plan Interface Freeze

后续计划依赖以下稳定名称，未经规格评审不得自行改名：

```text
ExecutionMode
RunStatus
StopReason
SkillManifest
SkillExecutionContext
SkillResult
SkillExecutor
ExecutorRegistry
TurnCoordinator
HookDecision
GateDefinition
GateDecision
GateDecisionSink
GateRunner
ContextRequest
ContextManifestDraft
ContextManifestCandidate
ContextManifest
PromptEnvelope
ToolDefinition
CompiledToolPolicy
PendingApproval
MemoryRecord
MemoryProposal
AgentEvent
ArtifactRecord
CheckpointDraft
CheckpointEnvelope
EvalCase
ReplayDiff
```

## Hook Ownership

`HookManager` 和 `HookDecision` 只由 Runtime 计划在 `src/policy_impact/runtime/hooks.py` 定义。其他计划只能注册或调用共享 Hook，不得创建第二套 Hook registry。

| Hook | 调用计划 | fail-closed 语义 |
|---|---|---|
| `BeforeTurn` | Runtime Task 4 | `deny` 不创建 Run；`ask` 非法 |
| `BeforeModelCall` | Context Task 5 | 只允许受控 `patch_context`；`deny`/`ask` 拒绝调用 |
| `AfterModelCall` | Context Task 5 | 输出已过 Schema 校验；`deny`/`ask` 拒绝返回 |
| `PreToolUse` | Tool Task 3 | `deny > ask > allow`，禁止执行未经批准的工具 |
| `PostToolUse` | Tool Task 3 | 校验、脱敏和 Artifact 化后才允许进入 Context |
| `BeforeMemoryWrite` | Memory Task 3 | `deny` 忽略，`ask` 创建 Proposal，未授权不得写入 |
| `BeforeArtifactPublish` | Observability Task 3 | `deny` 删除临时文件；`ask`/`patch_context` 非法 |
| `Stop` | Runtime Task 4 | final Artifact 就绪后、terminal commit 前恰好运行一次，只接收公开运行证据 |

## Canonical Spec Coverage

| Canonical Spec | 实施位置 | 覆盖结论 |
|---|---|---|
| 第 6 节 ExecutionMode / Skill / Coordinator | Runtime Task 1-4 | 完整覆盖 |
| 第 7 节 Agent Loop / Subagent / Gate | Runtime Task 1、5-7；Observability Task 2 | 完整覆盖 |
| 第 8 节 Context / Prompt | Context Task 1-7 | 完整覆盖 |
| 第 9 节 Tool Policy / Hook / Approval | Tool Task 1-7 | 完整覆盖 |
| 第 10 节 Memory / Profile | Memory Task 1-8 | 完整覆盖 |
| 第 11.1-11.5、11.7-11.8 Observability / Artifact / Checkpoint / Failure | Observability Task 1-7，Runtime/Tool 对应失败测试 | 完整覆盖 |
| 第 11.6 节 Replay | Replay Task 1-7 | 完整覆盖 |
| 第 12 节 Eval / Baseline / Benchmark | Eval Task 1-7 | 完整覆盖 |
| 第 13 节 UI / API | UI Task 1-8 | 完整覆盖 |
| 第 14 节目标模块结构 | 八份计划的 Files 清单 | 渐进兼容覆盖 |
| 第 15 节工作包 | 本路线图执行顺序 | 完整覆盖并修正 Checkpoint 前置依赖 |
| 第 16 节完成定义 | 每份 Acceptance Gate + UI Task 8 | 完整覆盖 |
| 第 17 节面试口径 | Eval Quality Gate + 最终 README | 只允许证据支持的表述 |
| 第 18 节已确认决策 | 全部 Global Constraints | 无新增冲突 |

自审未发现需要新增子系统的规格缺口。执行期间若测试暴露新语义，必须先更新 Canonical Spec 和本覆盖表。

## Per-Plan Review Gate

每份计划完成后执行：

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
git diff --check
git status --short
```

预期：

- 所有命令退出码为 `0`。
- `pytest` 不少于上一个阶段的测试数量。
- `git status --short` 只出现当前 Task 预期文件，提交后为空。
- README 的“已实现”描述只引用当前阶段的新鲜证据。

## Execution Discipline

1. 执行阶段先使用 `superpowers:using-git-worktrees` 创建隔离 worktree。
2. 每个 Task 使用一个新 Subagent 实现。
3. 每个 Task 先做 Spec Compliance Review，再做 Code Quality Review。
4. Reviewer 提出的缺陷由同一 Task 的实现者修复，复审通过后再进入下一 Task。
5. 不允许多个 Subagent 同时修改同一个 SQLite schema 或同一个前端文件。
6. 每个计划只修改其文件所有权范围；跨计划接口通过本路线图冻结。
7. 发现 Canonical Spec 缺口时先停止实现并更新规格，不在代码中偷偷决定新语义。

## Final Acceptance

八份计划全部完成后，项目仍需满足 Canonical Spec 第 16 节，并生成：

```text
data/eval/reports/<version>/contract-report.json
data/eval/reports/<version>/recorded-scenario-report.json
data/eval/reports/<version>/real-model-report.json
data/benchmark/<version>/baseline-comparison.json
data/benchmark/<version>/performance-report.json
docs/acceptance/<date>-agent-workbench-final-acceptance.md
```

最终简历数字只能从上述 Artifact 读取，不能从测试计划或预期值读取。
