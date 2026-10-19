# Harness 2.0 金融主循环接入设计

日期：2026-10-18  
状态：已落地为接入设计；默认 1.0 入口不切换  
设计版本：`H2-FINANCE-INTEGRATION-2026-10-18-v1`

调度底座后续的压力/观测实现不改变本金融域接入设计，见 [020 调度压力与 Stream 保留](020-dispatch-pressure-and-stream-retention.md)。

本文只定义已经确认的 Harness 2.0 接入边界，不重新设计金融研究方法，也不把
当前旧 `AgentLoop` 直接包进线程池后宣称获得了可恢复能力。代码必须先满足本文的
边界，再逐步把真实金融域步骤接入。

## 1. 目标与非目标

目标是让一个金融 Root Task 能够：

1. 由 H2 创建并固定 Thread、Task、Attempt、模型策略、权限快照和预算；
2. 由金融域每次只推进一个明确步骤，步骤之间用 SQLite 权威状态和 artifact 引用连接；
3. 通过 `agent.spawn` 派发文章分析、综合或复核子任务；
4. 在等待子任务时保存 checkpoint，进程退出后从 checkpoint 恢复，不重复已经提交的文章读取、子任务或审核；
5. 最终交付完整候选答案，并同时保留 `finance_state`、证据引用、缺口和审核状态。

本轮不做：

- 不切换默认 1.0 `AgentRuntime` 或默认 API；
- 不把现有同步 `AgentLoop` 隐式包装成 H2 已恢复的执行；
- 不新增金融关键词路由、固定文章数量或固定审核流程；
- 不迁移真实数据库、文章、Wiki、持仓和历史 Trace；
- 不自动切换 GPT 与 DeepSeek，不调用付费模型做离线验收；
- 不把多用户部署、鉴权、水平扩展和商业化负载验收伪装成本轮完成。

## 2. 分层职责

```text
HTTP / 业务入口（显式注入，见 018）
  -> FinanceHarnessV2Service（创建 Root Run、查询状态、驱动一次调度）
  -> Dispatcher / Worker（Outbox、Redis、lease、ACK、恢复）
  -> FinanceRootExecutor（加载/保存 finance.root_state.v1）
  -> FinanceDomainStep（一次金融域判断）
  -> capabilities.tool / complete / store_context / wait
  -> SQLite 状态 + run-scoped artifacts
```

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| `FinanceHarnessV2Service` | 显式创建 H2 run，绑定选定模型，提供测试/内部驱动入口 | 选择金融文章、判断证据是否充分 |
| `Worker` | claim、lease、heartbeat、取消边界、提交/挂起、ACK | 解释金融语义，重放旧工具调用 |
| `FinanceRootExecutor` | 恢复 root state，调用一步 domain step，保存 state，生成 checkpoint 或结果 section | 直接操作 SQL/Redis、编写金融 prompt |
| `FinanceDomainStep` | 读取当前状态和观察结果，决定下一步、派发子任务、形成候选或交付 | 绕过 capabilities，修改调度状态 |
| 金融工具/子 Agent | 按主 Agent 选择读取文章、抽取证据、综合和审核 | 假定自己拥有 root 权限或跨 run 读取 |

Runtime 的边界是可靠性和可观察性；主 Agent 仍拥有研究计划、文章选择、浅读/深读、
证据覆盖停止、审核目标和最终内容。H2 不因为任务名里出现 `analyst` 或 `review` 就
替主 Agent 决定金融方法。

## 3. Root Task 的一次推进

每个 Root Attempt 只允许执行一个 `FinanceDomainStep`：

1. 首次调用从 root mailbox 读取原始 query，创建 `finance.root_state.v1`；恢复调用只从 checkpoint 的 `context_ref` 读取 state。
2. Domain step 获得 state、brief、已交付 mailbox、子任务 observations 和 lease-bound capabilities。
3. 如果需要子任务，通过 `capabilities.tool("agent.spawn", ...)` 创建有预算、有输入引用、有输出契约的 child Task。
4. 若仍需等待，返回 `FinanceStep(status="waiting")`。Executor 保存新 state，Worker 保存 checkpoint 并挂起当前 Attempt。
5. 子任务完成后由 scheduler 唤醒 root。下一次调用只读取新的 observations，domain step 根据稳定 action key 更新 state；不得重做已经在 `completed_steps` 中的动作。
6. 若可以交付，返回 `Submission(status="succeeded"|"partial"|"failed")`。完整候选正文必须保留；审核争议、证据不足和未完成项写入 `warnings`/`gaps`/`review`，不因审核不同意而静默丢稿。

父任务状态恢复不是重新构造一个新会话。模型输入可以由 domain step 重新组织，但事实位置必须来自
SQLite 的 state、observations 和 run-scoped artifact；不得用“再调用一次旧工具”来模拟恢复。

## 4. 金融状态映射

`FinanceRootState` 是调度边界上的紧凑索引，不是文章正文或完整模型 transcript：

| state 字段 | 来源/用途 | 允许内容 |
| --- | --- | --- |
| `query` | 初始 root mailbox | 原始用户问题，不改写 |
| `phase` | domain step | `plan`、`research`、`synthesis`、`review`、`delivery` 等有限阶段标识 |
| `completed_steps` / `pending_steps` | 稳定 action key | 已提交或下一次要判断的步骤，不放 prompt 正文 |
| `research_plan` | `ResearchPlan.snapshot()` 的紧凑版本 | goals、scope、revision、交付状态 |
| `article_states` | 发现/读取/使用三层状态索引 | article_id、author、日期、depth、read/use 状态、artifact refs |
| `evidence_ledger` | EvidenceRef 索引 | evidence/artifact 标识、claim 绑定和范围，不放全文 |
| `coverage` | 主 Agent 的覆盖判断 | 已覆盖维度、反例、缺口、stop rationale |
| `candidate_answer` | 最新完整候选的引用和摘要 | `full_ref`、版本、交付前状态，不只存截断正文 |
| `review` | 审核目标和结果 | role、target refs、findings、争议、状态 |
| `delivery` | 最终交付元数据 | status、verification、evidence coverage、warnings |

完整文章、完整候选、EvidenceLedger 原文和模型输出全部通过 artifact ref 保存；Redis 只保存
dispatch 元数据。所有 ref 必须在当前 run 和当前权限范围内可验证。

## 5. 子 Agent 交接

Root 不把整个会话复制给 child。每个 child 只收到：

- `TaskBrief`：objective、success criteria、role/version、budget、deadline；
- 明确的 `input_refs` / `parent_context_refs`；
- 输出契约和允许的模型 profile；
- 必要的研究状态摘要，而不是未授权的 root 全文。

Child 只通过自身结果 envelope 返回 summary、full ref、sections 和 EvidenceRefs。Root 必须
再次决定该结果是 `used`、`excluded` 还是需要 follow-up；Runtime 不把“读取过”自动当作“最终使用”。
递归深度保持 H2 基线的两层限制；预算由父任务显式分配，不能因为 child 要求而扩张。

## 6. 与现有 1.0 AgentLoop 的关系

现有 `_run_autonomous_finance_loop` 仍是 1.0 兼容实现，内部包含同步 AgentLoop、金融工具和
审核逻辑。它不能直接被标记为 H2 root。接入顺序是：

1. 先使用 `FinanceHarnessV2Service` 和 `FinanceRootExecutor` 验证创建、派发、等待、恢复、交付的纵向调度链路；
2. 提取一个真正的金融 domain step，把已有 `ResearchPlan`、文章状态、EvidenceLedger、候选和审核状态映射到 `FinanceRootState`；
3. 将真实文章工具和单次模型请求绑定到 H2 capabilities，保证每个物理调用进入 H2 ledger；
4. 用显式 feature flag 接入 H2，先做离线 fixture，再做用户批准的模型/真实 Redis 验收；
5. 只有新的金融 Query 验收证明恢复、来源引用和完整交付都符合设计后，才讨论默认入口切换。

这意味着本变更完成后，若真实金融 AgentLoop 仍未接入，文档和测试必须明确写“调度链路已接通、
金融业务步骤未切换”，不能写成 Harness 2.0 全部完成。

## 7. 验收矩阵

| ID | 必须证明的事实 | 方式 |
| --- | --- | --- |
| H2-F06 | 服务显式创建 root run，模型选择和工具权限进入快照 | 临时 SQLite + fake Redis 契约测试 |
| H2-F07 | root 可以 spawn child 后 durable wait，child 完成后只恢复一次 | 两阶段 Worker 测试，断言 spawn/action key 次数 |
| H2-F08 | state 包含计划、文章状态、证据、coverage、candidate、review、delivery 索引 | schema/fixture 测试，禁止正文进入 state 或 Redis |
| H2-F09 | child 失败、审核争议和候选不完整仍保留可交付 body 与状态 | 结果/事件/Artifact 测试 |
| H2-F10 | 默认 1.0 入口不变，H2 仅由显式入口调用 | 入口回归和 feature flag 测试 |
| H2-F11 | 显式 H2 finance domain 已验证，但默认 1.0、HTTP 和生产接入不会被误报为完成 | 文档状态审查；不是通过离线模型调用证明线上质量 |

当前实现已覆盖 H2-F06、H2-F07，并新增了显式 opt-in 的模型驱动金融路径：
`FinanceHarnessV2Service.for_finance_corpus(...)` 连接 `FinanceH2Domain`、文章工具
child 和 `analyst@2.0.0` child。Root 可以由模型选择 `article.list`、有界
`article.read`、版本化 delegate 或 final，工具/分析结果通过 artifact ref 返回，
再经 durable wait/resume 进入下一次 Root Attempt。默认 1.0 入口、常驻生产 worker
和真实金融数据接入仍未切换；HTTP 业务路由已作为显式注入的可选边界接通，详见
[018](018-harness-v2-http-api.md)。H2-F08 在通用 `FinanceRootState` 和脚本
Domain fixture 中可验证，但模型驱动 Domain 目前还没有定义并实现模型如何提交
`research_plan`、`coverage`、`review` 等结构化状态更新；H2-F09 的通用 partial
交付语义已验证，模型驱动路径的审核状态交接仍未完成。H2-F10/H2-F11 依旧单独验收。

## 8. 本轮验证

| 检查 | 结果 |
| --- | --- |
| `tests/test_dispatch_finance_root.py` + `tests/test_dispatch_finance_service.py` | 5 passed |
| `tests/test_dispatch_finance_agent.py` | 3 passed；覆盖模型驱动 article.list/read、analyst delegate、wait/resume、结果和 finance_state |
| 全部 H2 foundation/execution/root/service/finance-agent tests | 181 passed / 2 skipped；跳过真实 Redis 联测，因为未设置 `H2_TEST_REDIS_URL` |
| Ruff（dispatch 与本轮测试） | passed |
| 1.0 金融/共享 Runtime 离线回归 | 1589 passed / 30 skipped / 1 warning |
| 配置/隔离检查 | 9/10；唯一失败为既有工作区存在额外 `data/companies`，不由本轮生成 |
| 付费模型、真实金融 Query、真实 Redis | 未运行 |

补充修复了三项代码契约问题：子 Agent 的 `input_refs` 现在包含请求 artifact
和已授权材料；legacy delegate 的材料引用必须属于当前 Root 已知 material refs；
Root 在没有新 mailbox 消息的恢复轮次中继承上一个 checkpoint 游标。文章工具的
`article.read` 现在执行 `start_line`/`limit<=100` 的有界读取，提交证据使用统一的
`{"evidence_ref": ...}` 绑定对象。

这些结果证明显式 H2 金融路径的离线调度、模型动作契约、子任务交接、等待恢复和
紧凑存储边界；不证明真实金融模型质量、真实 Redis 稳定性或默认 1.0 已经切换到
H2。下一项仍是先定义并实现模型提交 `ResearchPlan`、文章集合状态、EvidenceLedger、
coverage、审核和交付状态的结构化更新协议，再以 feature flag 做真实 Query 验收。
