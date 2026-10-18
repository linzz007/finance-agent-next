# 011 Harness 2.0：持久化 Agent 调度核心

状态：**设计草案，待用户确认；尚未实现**  
日期：2026-10-18

## 1. 目标

当前金融 Agent 已有 `AgentLoop`、受控工具、Subagent 角色、任务/结果契约、EvidenceRef 和 Trace，但委派仍是一次同步的 `delegate_fn(brief)` 调用。父 Agent 必须阻塞等待，任务没有持久身份、租约、可重放事件或恢复入口。

本变更学习 Codex 公开可观察的调度分层，先为金融研究增加一个**单机、SQLite、可恢复的调度核心**。它解决的是“任务如何可靠地被接收、执行、等待、取消、恢复和审计”，不是替主 Agent 决定金融问题该读哪些文章，也不是新增固定业务 DAG。

## 2. Codex 思路在本项目中的取舍

可借鉴的机制是：独立 Agent thread、父子关系、`spawn/send/wait/close/resume` 操作、状态通知、预算/权限边界和可恢复身份。

不照搬的机制是：代码编辑、worktree、diff 冲突处理和代码项目专用角色。金融任务还必须保留作者、日期、文章版本、EvidenceRef、结果处置和审核/交付层；调度成功不代表金融事实已验证。

本项目不使用供应商原生 Function Calling 作为依赖。Agent 继续输出当前公开 JSON action，由 Runtime 做严格解析、幂等和权限校验；将来可以让模型选择 `agent.spawn` 等动作，但模型不能自行填写任务状态、租约或真实调用 ID。

## 3. 当前调度与目标调度

### 当前 1.0 的事实

```text
主 Agent AgentLoop
  -> 解析 delegate action
  -> 创建 TaskBrief
  -> 同步调用 delegate_fn(brief)
  -> 等待 SubagentResult
  -> 校验结果与 EvidenceRef
  -> 把结果放回父 Agent 的 observation
```

子 Agent 使用独立的 `AgentLoop` 和受限工具，但没有持久任务队列、异步 `spawn/wait`、mailbox、lease/heartbeat、取消、worker 崩溃恢复或调度级幂等键。

### Harness 2.0 第一阶段

```text
主 Agent / API
  -> Coordinator / AgentScheduler
  -> SQLite：任务、thread、attempt、消息、事件、checkpoint、结果引用
  -> worker claim + lease
  -> 现有 AgentLoop / delegate_fn 执行
  -> 结果原子落库 + completion event
  -> 主 Agent 查询状态或 wait，并按自己的判断采用/排除结果
```

第一阶段先让现有同步委派获得真实的任务身份、租约和审计记录；随后调度 API 可以支持父 Agent 的独立工作和有界等待。同步适配器不能被称为已经实现了异步并行调度。

## 4. 对象模型

| 对象 | 语义 |
| --- | --- |
| `Session` | 用户对话范围；可包含多次研究。 |
| `RootRun` | 一次用户请求的预算、研究截止时间和最终交付责任。现有 `run_id` 是它的关联键。 |
| `AgentThread` | Agent 的逻辑上下文、角色和授权身份，不等于 OS 线程。一个 thread 可以接收多条补充消息。 |
| `Task` | 有目标、材料、预算和完成期待的逻辑工作，例如读一篇文章或核查一个命题。 |
| `Attempt` | Task 的一次物理执行。重试必须新建 Attempt，不能覆盖旧失败。 |
| `Message` | 发给 thread 的输入；accepted 不表示模型已经读取。 |
| `Event` | 已发生的状态变化，可按游标重放。 |
| `Checkpoint` | 恢复所需的上下文、消息游标、任务状态和已完成调用。 |
| `ResultRef` | 研究产物或分析结果的不可变引用；完整正文按现有授权保存和展开。 |

现有 `run_id/task_id/span_id/TaskBrief/SubagentResult/EvidenceRef` 保留。调度层的 ID 和租约由 Runtime 生成，不让模型伪造。

## 5.1 分层设计

这不是“一层主 Agent 加几层 Subagent Prompt”。调度、执行、上下文、金融产物和交付要分开，否则一个模型输出就会同时承担计划、队列、状态、证据和质量判断，无法知道失败发生在哪一层。

### L0：产品与责任边界

输入是用户问题，最终责任仍由 Root Agent 负责。Root Agent 可以自己完成，也可以委派；调度器不替它决定文章选择、观点关联、证据覆盖或最终结论。

### L1：运行身份层

`Session -> RootRun -> AgentThread -> Task -> Attempt` 形成身份链。每次用户请求有一个 RootRun；每个 AgentThread 有独立上下文和权限；一个逻辑 Task 的重试产生新 Attempt。任何结果、事件和预算都必须带 root/task/attempt 身份。

### L2：协调与控制层

Coordinator 接受 `spawn/send/wait/status/cancel/close/resume`，校验调用者、root 隔离、父子关系、幂等键和状态迁移。它只返回“已接受、排队、运行、完成、失败”等事实，不把 accepted 写成模型已经处理。

### L3：执行资源层

Worker 从 queued/waiting 任务中 claim，获得 lease 和 heartbeat。过期 lease 只能被新 Attempt 重新领取，旧 worker 不能提交结果。供应商请求可能重复或迟到，执行层不声称 exactly-once。

### L4：消息、上下文与恢复层

Message 是投递给 thread 的输入；Event 是可重放的状态事实；Checkpoint 是继续执行所需的最小状态。父 Agent 等待时应释放执行资源；恢复使用原逻辑身份和新 Attempt，不能只重新发送一段没有身份的 Prompt。

### L5：金融研究产物层

文章读取、ArticleCard、观点/事实/预测分类、EvidenceRef、跨文章综合和外部核查属于金融研究层，不属于通用调度器。调度器只保存 `ResultRef` 和授权边界；主 Agent 决定结果是 `use/qualify/exclude/follow_up`。

### L6：预算与权限层

RootRun 统一拥有截止时间、模型调用预算、并发额度和来源权限；子任务只能继承或缩小边界，不能自行扩大。第一阶段只定义预留接口，不能把现有研究预算器和新的调度预算重复计算。

### L7：Trace、审计与交付层

Trace 记录动作、状态迁移、Attempt、消息游标、EvidenceRef 和结果处置。任务成功只表示执行成功；金融答案是否充分、事实是否被外部证据支持、争议是否保留，继续由现有审核/交付层判断。

### 一次完整调用的时序

```text
Root Agent 判断需要子任务
  -> Coordinator 校验权限/预算/幂等键，持久化 Task + Thread
  -> 返回 queued，不等待模型
  -> Worker claim，创建 Attempt，获得 lease
  -> 子 Agent 读取授权上下文并执行工具/模型
  -> 需要等待：保存 Checkpoint，释放 lease，Task=waiting
  -> 完成：ResultRef、Task 状态、completion Event 原子提交
  -> Root Agent wait/status 读取事件和结果
  -> Root Agent 结合证据决定采用、补充、排除或交付
```

## 5. 任务状态与不变量

任务状态为：

```text
queued -> running -> waiting -> running -> succeeded
                           \\-> failed / cancelled / timed_out
```

- `queued`：已持久接收，等待 worker。
- `running`：持有有效 lease 的 Attempt 正在执行。
- `waiting`：检查点已保存，等待消息或子任务事件，不长期占用 worker。
- `succeeded`：执行成功，不等于内容或事实正确。
- `failed`：执行发生错误；重试时保留原 Attempt。
- `cancelled`：收到取消，不再接受新的外部调用。
- `timed_out`：超过任务或 Attempt 的时间边界。

关键不变量：

1. 同一 root run 隔离；任务、消息、事件不能跨 root 查询。
2. 同一幂等键只创建一个逻辑任务或消息。
3. 一个任务同时最多一个有效 lease；旧 lease 失效后不能提交结果。
4. 任务完成、结果索引和 completion event 在同一 SQLite 事务中提交。
5. 事件通知丢失时，父 Agent 可用游标重新读取事件和当前状态。
6. 取消与完成竞态不抹掉任何事件；状态版本和 lease 决定最终可发布状态。
7. 供应商模型请求不承诺 exactly-once；调度层只保证过期执行不能覆盖新 Attempt。

## 6. SQLite 逻辑模型

第一阶段使用独立的 Harness SQLite 文件，不修改既有金融业务表：

- `harness_agent_threads`：thread 身份、root、父 thread、角色和状态。
- `harness_tasks`：任务目标、root、thread、父任务、状态、幂等键、当前 Attempt 和 lease。
- `harness_attempts`：每次执行、worker、lease token、开始/结束时间和错误。
- `harness_messages`：thread 输入、幂等键、queued/consumed 状态。
- `harness_events`：单调事件 ID、类型、任务和 JSON payload。
- `harness_checkpoints`：可恢复快照、消息游标和版本。
- `harness_result_refs`：结果引用及其 JSON 元数据，不覆盖历史结果。
- `harness_budget_reservations`：为后续父子共享额度预留；第一阶段只提供数据边界，不自动替换现有金融预算器。

数据库启用 WAL、外键和 busy timeout。所有状态迁移使用事务和条件更新；不把内存 mailbox 当作持久完成保证。

## 7. 调度操作契约

这些操作由 Runtime 暴露给主 Agent/API，仍使用项目现有 JSON action 协议：

| 操作 | 成功语义 | 失败边界 |
| --- | --- | --- |
| `agent.spawn` | 持久化任务并返回 task/thread ID，不等待完成。 | 幂等键重复返回原任务；排队不能写成 running。 |
| `agent.send` | 将消息写入指定 thread，返回 message ID。 | accepted 不等于模型已读；不能静默扩大授权。 |
| `agent.wait` | 有界等待指定任务的状态或事件。 | wait 超时只结束等待，不杀死子任务。 |
| `agent.status` | 返回当前任务、Attempt、结果引用和错误。 | 不能跨 root 查询，也不触发模型调用。 |
| `agent.cancel` | 请求停止任务及明确指定的后代。 | 已发出的供应商请求未必可撤回；迟到结果保留审计但不可自动发布。 |
| `agent.close` | 释放不再需要的 thread 执行资源，保留历史和产物。 | close 不等于 task succeeded。 |
| `agent.resume` | 从有效 checkpoint 恢复同一逻辑任务。 | 不默认复活 cancelled、延长 deadline 或换模型。 |

主 Agent 仍决定结果是否 `use/qualify/exclude/follow_up`；调度器不代替金融判断。

## 8. 候选第一阶段实现边界（待确认）

建议第一阶段实现：

- SQLite schema 和迁移初始化。
- `spawn/claim/heartbeat/complete/fail/cancel/send/status/wait/events_after/recover_expired`。
- Attempt、lease、幂等键和事件游标。
- `run_sync` 适配现有 `delegate_fn`，让旧金融流程获得调度审计。
- AgentLoop 可选注入 scheduler；未注入时保持现有行为。
- 离线测试覆盖并发领取、过期 lease、旧 lease 拒绝、取消、重试、事件重放和 callback 成功/异常/超时。

建议本轮明确不做：

- 多机分布式 worker、外部消息队列、自动递归委派。
- 前端重构、全量数据库迁移和金融文章阅读策略重写。
- 把同步适配器宣传成完整异步并行系统。
- 让调度器决定文章选择、证据覆盖或答案质量。

## 8.1 当前问题的归类

### 已知事实问题

- 当前 `AgentLoop._run_delegate_action()` 直接同步调用 `delegate_fn`；父 Agent 无法在子任务运行时继续独立工作。
- 当前没有调度级持久任务身份、Attempt 历史、lease/heartbeat、事件游标、取消和恢复操作。
- 当前已有 `run_id/task_id/span_id`、SubagentResult、EvidenceRef 和 Trace，但它们是运行内研究契约，不等于持久调度状态。

### 设计尚未确定的问题

- Root Agent 是否允许真正 `spawn` 后继续工作，还是只在特定任务上等待。
- 子 Agent 是否允许再次委派；如果允许，最大深度、预算和权限如何继承。
- waiting 的恢复触发是消息、子任务完成事件、定时唤醒还是 Root Agent 主动 resume。
- `close` 是关闭 thread 还是终止 task；结果如何保留、哪些迟到结果不可发布。
- RootRun 的预算如何与现有研究预算、模型供应商重试和上下文预算合并。
- 多用户/多进程是否进入本版本；SQLite 单机边界能否满足实际使用。

### 不应误判为调度问题的内容

- 模型没有读到文章、遗漏作者观点、误判因果或过度概括，是金融研究/提示和证据层问题。
- 外部核查是否必要、如何判断证据覆盖，是研究策略问题，不由 `spawn` 或 lease 自动解决。
- 1.0 的答案质量不能因为引入 Codex 式状态机而自动变好；必须用同一 Query 和证据标准分别验证。

## 8.2 设计决策顺序

为了不在实现中途反复重构，按以下顺序确认：

1. 先确认 L0-L1：一次用户请求、一次 RootRun、thread 和 task 的责任边界。
2. 再确认 L2-L4：spawn/wait、消息、checkpoint、取消和恢复的状态语义。
3. 再确认 L6：预算、权限、模型切换和并发边界如何继承。
4. 最后确认 L5/L7 的接入点：金融结果如何回传，哪些结果由主 Agent 采用，Trace 如何展示。
5. 设计文本冻结后，才定义接口 Schema；接口通过后才写代码和故障测试。

## 8.3 验证设计正确性的指标

先验证调度事实，再验证金融质量，不能用一次答案好坏替代调度验收。

| 层 | 必须观察的指标 | 通过条件示例 |
| --- | --- | --- |
| L1 身份 | root/task/thread/attempt 关联完整率、跨 root 访问拒绝率 | 关联完整；跨 root 全部拒绝 |
| L2 状态 | 非法状态迁移率、幂等重复率、accepted 与 processed 区分率 | 非法迁移拒绝；同幂等键不重复创建 |
| L3 租约 | 双重 claim、旧 lease 回写、过期恢复、心跳丢失 | 同一时刻最多一个有效 lease；旧 lease 不能完成 |
| L4 消息恢复 | 事件缺口、游标重放一致性、checkpoint 恢复成功率 | 通知丢失后可从游标补齐；不重复消费 |
| 预算权限 | 父子累计调用、越权工具、超时和取消后的迟到结果 | 子任务不扩大预算/权限；迟到结果不可自动发布 |
| L5 研究接入 | 结果引用完整率、EvidenceRef 授权命中率、结果采用/排除可追溯率 | 调度不改变证据权限；每次采用都有来源处置 |
| L7 交付 | 任务执行成功率、完整候选保留率、Trace 可重建率 | 执行失败不伪装成功；失败仍可解释和恢复 |
| 综合稳定性 | p50/p95 调度延迟、恢复时间、孤儿任务数、重复模型请求数 | 先建立基线，再由实际负载确定阈值，不预设商业化结论 |

验证分三批：SQLite 状态机单测；模拟 worker 崩溃/断线/重复消息的故障测试；最后用固定金融 Query 比较调度前后文章读取、证据覆盖和最终交付，不把“调度成功”当成“答案优秀”。

## 9. 后续路线

1. 用调度 API 替换主 Agent 的阻塞委派：`spawn -> 独立执行 -> wait/status -> 结果处置`。
2. 增加 root run 级预算预留、父任务等待时释放 worker、恢复和取消的故障测试。
3. 再讨论多子任务并行、子任务是否允许委派以及金融结果的聚合策略。
4. 在真实模型和多轮负载下验证吞吐、重复执行、断线重连和答案质量；离线调度测试通过不能替代这些验证。

