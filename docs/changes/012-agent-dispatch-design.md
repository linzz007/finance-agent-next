# 012 Harness 2.0：Agent Dispatch Design

状态：**设计讨论稿，尚未确认，尚未实现**  
日期：2026-10-18

## 1. 这份设计解决什么问题

当前系统的委派方式是同步函数调用：主 Agent 生成 `delegate`，Runtime 创建 `TaskBrief`，然后同步调用 `delegate_fn`，直到 Subagent 返回 `SubagentResult`，主 Agent 才能继续。

本设计只处理一个问题：

> 如何把“主 Agent 调度 Subagent”设计成有独立身份、可异步运行、可等待、可恢复、可取消、可追踪的调度系统。

本设计暂时不修改：

- 文章阅读和 ArticleCard；
- 观点、事实、预测和因果分析；
- EvidenceRef 的生成规则；
- 外部核查、审核和最终答案质量；
- 上下文压缩策略；
- 金融研究 Prompt 和工具选择。

调度器只保证任务被正确管理，不保证 Subagent 的金融结论正确。

## 2. 当前系统与目标系统

### 2.1 当前同步委派

```text
主 Agent AgentLoop
  -> 解析 delegate action
  -> 创建 TaskBrief
  -> 同步调用 delegate_fn(brief)
  -> 等待 SubagentResult
  -> 校验结果和 EvidenceRef
  -> 将结果放回主 Agent 上下文
```

这里的 Subagent 更像一个函数：调用开始后，父 Agent 被阻塞；调用结束后，子 Agent 的运行身份、等待状态和后续通信能力都不再独立存在。

### 2.2 Codex 式目标

```text
主 Agent
  -> spawn 子 Agent，立即获得 agent_id/task_id
  -> 主 Agent 可以继续规划或创建其他子任务
  -> 子 Agent 独立执行
  -> 主 Agent wait/status 读取状态或完成事件
  -> 子 Agent 可以接收 send_input
  -> 需要暂停时保存 checkpoint
  -> 需要继续时 resume 同一个逻辑 Agent
```

公开 Codex 机制中值得借鉴的不是固定的“分析师/审核员/总结员 Prompt”，而是 Agent 的身份、生命周期、输入投递、状态等待、结果通知和父子关系。本文不据此声称掌握 ChatGPT 服务端的全部架构，只采用公开文档和源码中可观察的机制。

## 3. 为什么需要异步

这里的异步不是模型流式输出，也不是 Python `async def` 本身，而是：

> `spawn` 提交子任务后，主 Agent 不必同步等待子任务执行完毕。

例如用户要求比较 TT 和培风客：

```text
主 Agent spawn 读取 TT
主 Agent spawn 读取培风客
主 Agent wait 两个任务
主 Agent 综合两个结果
```

同步方案是：

```text
读取 TT 完成
  -> 读取培风客完成
  -> 主 Agent 综合
```

异步方案的价值：

1. 独立任务可以并行执行；
2. 父 Agent 等待时可以释放执行资源；
3. 子任务超时不会直接摧毁整个父任务；
4. 子任务完成通知丢失时，可以通过持久状态查询；
5. 后续可以继续向同一个 Agent 发送补充问题。

异步也会增加成本：任务状态、并发限制、结果顺序、重复执行、取消和预算都必须定义。因此“使用异步”本身不是目标，目标是让异步任务可控、可恢复、可解释。

## 4. Thread 到底是什么

`AgentThread` 不是操作系统线程，也不一定等于一个 Python 线程。

它是一个**有身份的 Agent 对话与执行上下文**，包含：

- 这是哪个 Agent；
- 它属于哪个 RootRun；
- 它继承了哪些权限和预算；
- 它收到过哪些输入；
- 它产生过哪些结果；
- 它是否还能继续接收消息。

可以把它理解成“一个可以持续通信的 Agent 会话”。真正执行它的可以是 Python 线程、进程、协程或未来的远程 Worker；Thread 身份不应绑定到某一种执行技术。

为什么不能只保留 Task：

- Task 表示一次具体工作；
- Thread 表示持续的 Agent 身份；
- 同一个 Thread 可以先执行“读取文章”，再接收“补充说明这篇文章的反例”；
- 每次实际运行又应该有独立的 Attempt。

## 5. 核心对象

### 5.1 Session

用户对话范围。一个 Session 可以包含多个 RootRun。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `session_id` | 对话身份 |
| `user_id` | 用户隔离；单用户阶段也保留字段 |
| `created_at` / `updated_at` | 生命周期审计 |
| `metadata` | 非业务性的会话信息 |

### 5.2 RootRun

一次用户 Query 的完整研究运行，是预算、截止时间和最终交付责任的根。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `root_run_id` | 与现有 `run_id` 对齐 |
| `session_id` | 回到用户会话 |
| `status` | `running / waiting / succeeded / failed / cancelled` |
| `deadline_at` | 整轮研究的截止时间 |
| `budget_ref` | 指向统一预算，不把数字复制到每个子任务 |
| `created_at` / `finished_at` | 运行审计 |

RootRun 不等于主 Agent 的某一次模型调用，而是整轮研究的容器。

### 5.3 AgentThread

一个可持续通信的 Agent 身份。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `thread_id` | Agent 身份 |
| `root_run_id` | 防止跨研究访问 |
| `parent_thread_id` | 建立父子 Agent 关系 |
| `role` / `role_version` | 角色和 Prompt 契约版本 |
| `model_profile` | 本 Thread 固定使用的模型配置 |
| `permission_snapshot_ref` | 使用的工具权限快照 |
| `budget_ref` | 继承或分配到的预算 |
| `status` | `ready / running / waiting / closed / failed` |
| `context_ref` | 当前上下文或 checkpoint 的引用 |
| `created_at` / `closed_at` | 生命周期 |

这里的 `role` 是执行配置，不是要求模型只能做一种固定业务。金融任务仍由主 Agent 根据 Query 决定如何分工。

### 5.4 Task

一次逻辑工作。例如“读取某篇文章”“查找两个作者的共同观点”“核查某个外部事实”。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `task_id` | 工作身份 |
| `thread_id` | 由哪个 Agent Thread 执行 |
| `root_run_id` | 所属研究运行 |
| `parent_task_id` | 哪个任务派生了它 |
| `objective` | 工作目标 |
| `input_refs` | 输入材料或 Artifact 引用，不直接复制大文本 |
| `output_contract` | 期望结果类型 |
| `idempotency_key` | 防止同一次提交重复创建 |
| `status` | `queued / running / waiting / succeeded / failed / cancelled / timed_out` |
| `deadline_at` | 单任务截止时间 |
| `result_ref` | 完成后的结果引用 |
| `created_at` / `finished_at` | 生命周期 |

### 5.5 Attempt

Task 的一次实际执行。网络错误或 Worker 崩溃后重试，必须创建新的 Attempt。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `attempt_id` | 本次物理执行身份 |
| `task_id` | 所属逻辑任务 |
| `attempt_no` | 第几次尝试 |
| `worker_id` | 哪个 Worker 执行 |
| `lease_token` | 当前执行所有权 |
| `status` | `claimed / running / succeeded / failed / expired / cancelled` |
| `model_profile` | 实际使用的模型配置 |
| `started_at` / `finished_at` | 执行时间 |
| `usage` | 调用数、Token、耗时等统计 |
| `error_code` / `error_message` | 公开可审计错误 |

旧 Attempt 不能被新 Attempt 覆盖。否则无法判断结果是第一次执行还是重试结果。

### 5.6 Message

发给 AgentThread 的输入。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `message_id` | 消息身份 |
| `thread_id` | 投递给哪个 Agent |
| `task_id` | 关联哪项工作 |
| `sequence` | Thread 内顺序 |
| `kind` | `initial / follow_up / cancel / system` |
| `payload_ref` | 消息内容或 Artifact 引用 |
| `idempotency_key` | 防止重复投递 |
| `status` | `queued / delivered / consumed / rejected` |
| `created_at` / `consumed_at` | 投递审计 |

`accepted` 只能表示 Runtime 已接收，不能表示模型已经读到或理解。

### 5.7 Event

系统已经发生的事实，例如 `task.spawned`、`task.claimed`、`task.succeeded`、`task.failed`。

建议字段：

| 字段 | 作用 |
| --- | --- |
| `event_id` | 全局递增游标 |
| `root_run_id` | 权限隔离 |
| `aggregate_type` / `aggregate_id` | 事件属于 Task、Thread 还是 Run |
| `event_type` | 状态变化类型 |
| `payload` | 变化详情 |
| `created_at` | 发生时间 |

通知可以丢失，但 Event 不能丢失。父 Agent 可以用游标补读。

### 5.8 Checkpoint 与 ResultRef

`Checkpoint` 保存“如何继续执行”；`ResultRef` 保存“已经产生了什么结果”。两者不能混为一个大 JSON。

Checkpoint 主要包含：Thread 状态、消息游标、已完成调用、待处理任务和上下文引用。ResultRef 主要包含：结果产物、EvidenceRef、来源版本和结果元数据。

## 6. 推荐生命周期

```text
Root Agent 判断需要委派
  -> spawn：保存 Thread + Task，返回 handle
  -> Worker claim：创建 Attempt，取得 lease
  -> 子 Agent 执行模型和工具
  -> 需要补充输入：Task=waiting，保存 Checkpoint，释放 lease
  -> 子 Agent完成：ResultRef + Task终态 + Event 一起提交
  -> Root Agent wait/status 读取结果
  -> Root Agent 决定采用、继续追问、排除或交付
```

失败时：

```text
Worker 崩溃 / lease 过期
  -> 当前 Attempt=expired
  -> Task 保留历史并重新 queued
  -> 新 Worker 创建新 Attempt
```

迟到结果不能覆盖新 Attempt；取消后的迟到结果只能进入审计，不能自动发布。

## 7. 调度操作

| 操作 | 语义 |
| --- | --- |
| `agent.spawn` | 创建独立 Thread 和 Task，立即返回 handle |
| `agent.send` | 向现有 Thread 投递追加输入，不自动创建新 Thread |
| `agent.wait` | 等待指定任务或事件；超时只结束等待，不杀死任务 |
| `agent.status` | 查询真实状态，不触发模型调用 |
| `agent.cancel` | 请求取消任务及明确的后代 |
| `agent.close` | 关闭不再使用的 Thread，保留历史和结果 |
| `agent.resume` | 从有效 Checkpoint 继续同一个逻辑 Thread |

第一版不应该直接让模型自由构造所有内部字段。模型只表达目标、输入引用和操作意图；Runtime 负责生成 ID、状态、权限、租约和时间。

## 8. 必须由用户确认的设计问题

下面是进入代码前最重要的判断，不是实现细节。

### 问题一：Thread 和 Task 是否分开？

推荐：分开。

- Thread 是持续 Agent 身份；
- Task 是一次具体工作；
- Attempt 是一次物理执行。

如果合并，第一次读取和后续追问会被当成同一个状态，重试和恢复也会混乱。代价是数据结构更多，但这是 Codex 式调度的核心。

### 问题二：是否要求真正异步？

推荐：要求 `spawn` 立即返回，子 Agent 独立执行。

如果只是把同步函数放进线程池，然后父 Agent 仍然一直等待，本质仍是同步委派，不能证明实现了目标。第一阶段可以只做单机，但语义必须是真异步。

### 问题三：子 Agent 是否允许继续派生？

推荐：第一阶段不开放默认递归，只允许 Root Agent 派生；数据模型保留 `parent_task_id` 和深度字段。

这样可以先验证核心调度，不让递归委派带来预算爆炸和无限任务树。未来可以在明确额度和最大深度后开放。

### 问题四：`wait` 等待什么？

推荐同时支持三种模式：

- `any`：任意一个子任务完成就返回；
- `all`：全部子任务进入终态才返回；
- `specific`：等待指定任务。

金融比较任务常用 `all`，但长时间研究更需要 `any`，先拿到有效结果再决定是否继续。

### 问题五：结果直接传全文还是传引用？

推荐：调度层传 `ResultRef`，主 Agent 按权限展开全文。

这样可以减少消息重复和上下文膨胀；但金融文章的关键证据不能只剩摘要，ResultRef 必须能回到完整产物和 EvidenceRef。

### 问题六：失败后自动重试还是交给主 Agent？

推荐：基础设施故障可以有限自动重试；研究策略失败、证据不足和模型结论不确定必须交给主 Agent 判断。

调度器只能判断“执行失败”，不能判断“金融结论不够好”。

### 问题七：取消是否强制终止模型请求？

推荐：取消首先阻止新调用、标记任务不可发布；已经发出的供应商请求允许迟到，但迟到结果不能覆盖取消状态。

因为外部 HTTP 请求不一定可撤回，不能把“请求取消”伪装成“供应商已经停止”。

### 问题八：预算归谁？

推荐：RootRun 持有总预算，Task 只能获得预留额度，Attempt 消耗额度。

不能让父 Agent 和每个子 Agent 各自拥有一份独立余额，否则总调用量会超出用户预期。

## 9. 验证方法

### 9.1 状态和数据正确性

- 同一幂等键不会重复创建 Task；
- 同一 Task 不能被两个 Worker 同时 claim；
- 旧 lease 不能提交结果；
- 重试会创建新 Attempt，旧 Attempt 保留；
- Event 游标可以补读通知丢失期间的变化；
- 跨 RootRun 查询和消息投递全部拒绝。

### 9.2 故障恢复

模拟：Worker 崩溃、心跳停止、数据库重启、消息重复、通知丢失、子任务超时、父任务取消、子任务迟到完成。

检查：任务是否进入正确状态、是否产生正确 Event、是否保留历史、是否出现重复发布。

### 9.3 多 Agent 行为

用固定测试任务验证：

1. 并行派出两个独立子任务；
2. 父 Agent 等待任意一个；
3. 父 Agent 等待全部；
4. 对同一个 Thread 发送补充问题；
5. 子任务失败后主 Agent 决定重试或继续交付；
6. 父任务取消并检查迟到结果。

### 9.4 金融接入不变性

调度优化前后使用同一批 Query，检查：文章、EvidenceRef、作者日期、结果处置和最终答案没有因为调度改造而丢失。调度指标通过，不能代替金融答案质量验收。

### 9.5 核心指标

| 指标 | 目的 |
| --- | --- |
| Task 状态正确率 | 判断状态机是否可靠 |
| 旧 lease 回写拒绝率 | 判断并发和恢复是否安全 |
| 重复 Task 创建率 | 判断幂等是否有效 |
| 事件重放完整率 | 判断断线恢复能力 |
| 孤儿 Task 数量 | 判断 Worker 崩溃后的清理能力 |
| 任务恢复耗时 | 判断可恢复性 |
| 父子预算超额率 | 判断资源边界 |
| p50/p95 调度延迟 | 判断运行效率 |
| 金融结果引用完整率 | 判断调度是否破坏研究结果 |

## 10. 进入代码前的确认顺序

建议按这个顺序讨论，不要同时讨论所有字段：

1. 先确认 Thread、Task、Attempt 的三层关系；
2. 再确认 `spawn` 是否真正异步；
3. 再确认 wait、send、cancel、resume 的语义；
4. 再确认预算和权限继承；
5. 再确认 SQLite 表和字段；
6. 最后定义 JSON action 和 API；
7. 全部确认后才实现代码。

