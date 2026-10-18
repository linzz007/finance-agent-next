# Harness 2.0 Agent 调度设计基线

状态：设计已确认，已通过一致性审查并冻结；代码尚未实现  
冻结版本：H2-DISPATCH-2026-10-18-v1  
确认日期：2026-10-18  
适用项目：finance-agent-next

本文集中记录当前已经确认的 Harness 2.0 多 Agent 调度设计。旧的 012-agent-dispatch-design.md 保留为讨论稿和决策历史，不与本文重复定义现行规则。

## 1. 范围

本轮设计：

- 主 Agent 创建、等待和管理 Subagent；
- Thread、Task、Attempt、Message、Event、Checkpoint、ResultRef；
- 异步 spawn、wait、status、send、cancel、resume；
- SQLite 权威状态与 Redis Streams 任务分发；
- 上下文交接、结果读取、工具动作和模型适配；
- 租约、心跳、幂等、重试、取消、恢复和并发控制。

本轮不改变：

- ArticleCard、浅读/深读和金融证据分析方法；
- 外部核查、答案审核和最终金融结论规则；
- 当前 1.0 金融研究链路；
- 文章、Wiki、持仓和用户研究 Prompt 的业务语义。

## 2. 当前形态与目标

当前实现更接近同步委派：

~~~text
AgentLoop
  -> 解析 delegate
  -> 创建 TaskBrief
  -> 同步调用 delegate_fn
  -> 等待 SubagentResult
  -> 校验并返回主 Agent
~~~

目标是：

~~~text
主 Agent
  -> spawn：创建 Thread/Task，立即返回句柄
  -> 继续规划或创建其他任务
  -> wait/status：读取状态和事件
  -> send：向已有 Thread 投递后续消息
  -> cancel/resume：取消或从 Checkpoint 恢复

Dispatcher
  -> 从 SQLite Outbox 发布到 Redis Streams

Worker
  -> claim Task
  -> 创建 Attempt
  -> 执行模型和工具
  -> 保存结果、事件和 Checkpoint
  -> ack Redis 消息
~~~

目标是可恢复、可审计、可并发的 Agent 调度，不是简单把同步函数放进线程池。

## 3. 三层身份模型

Thread 是 Agent 的持续身份和通信边界；Task 是要完成的一项具体工作；Attempt 是该工作的某次实际执行。

~~~text
Thread：文章分析 Agent
└── Task：分析文章 A
    ├── Attempt 1：超时
    ├── Attempt 2：模型格式错误
    └── Attempt 3：成功
~~~

重试必须创建新的 Attempt，旧 Attempt 不覆盖、不删除。

### 3.1 Thread

一次用户研究请求创建一个根 Thread。子 Agent 使用子 Thread。Thread 可以在任务结束后继续接收消息并恢复。

| 字段 | 作用 |
| --- | --- |
| thread_id | Agent 身份 |
| run_id | 所属研究运行；不替代 Thread 身份 |
| parent_thread_id | 父子 Agent 关系 |
| root_thread_id | 归属同一研究请求 |
| depth | 递归深度，根为 0 |
| role / role_version | 角色和契约版本 |
| status | 当前状态 |
| model_policy_ref | 模型选择策略 |
| permission_snapshot_ref | 工具权限快照 |
| budget_ref | 分配预算 |
| last_event_seq | 上次读取的事件序号 |
| created_at / updated_at | 生命周期审计 |

### 3.2 Task

Task 是可调度的逻辑工作，例如读取文章 A 或比较两个作者的观点。

| 字段 | 作用 |
| --- | --- |
| task_id | 工作身份 |
| run_id | 所属研究运行 |
| thread_id | 执行该工作的 Thread |
| parent_task_id | 派生来源 |
| task_type | 任务类型 |
| objective | 工作目标 |
| input_refs | 文章、结果或证据引用 |
| output_contract | 期望结果结构 |
| priority | 调度优先级 |
| status | 当前状态 |
| budget_ref | 预算分配 |
| deadline_at | 任务截止时间 |
| result_ref | 最终结果引用 |
| dispatch_key | 调度幂等键 |

依赖关系使用独立的 task_dependencies 表保存。

budget_ref 指向预算记录；预算记录解析出 requested_budget、allocated_budget、consumed_budget 和 remaining_budget，不把同一组数值复制到多个业务表。

### 3.3 Attempt

Attempt 记录一次实际 Worker、模型、租约和错误。

| 字段 | 作用 |
| --- | --- |
| attempt_id | 本次执行身份 |
| run_id | 所属研究运行 |
| task_id | 所属 Task |
| attempt_no | 第几次执行 |
| worker_id | 执行 Worker |
| lease_token | 当前执行所有权 |
| lease_expires_at | 租约过期时间 |
| heartbeat_at | 最近心跳时间 |
| status | 执行状态 |
| model_snapshot_ref | 不可变模型配置快照 |
| checkpoint_ref | 恢复位置 |
| result_ref | 执行结果 |
| started_at / finished_at | 执行时间 |
| usage | 调用次数、Token、耗时等统计 |
| error_code / error_message | 可审计错误 |

## 4. SQLite 与 Redis Streams

两者职责不同：

~~~text
SQLite：权威状态、历史、结果索引和审计
Redis Streams：任务消息、分发、消费者确认和临时协调
~~~

Redis 中只放任务消息和必要引用，不放整篇文章或完整金融答案。

### 4.1 Outbox

~~~text
1. SQLite 事务中创建 Task、Attempt、OutboxEvent
2. Dispatcher 读取 pending OutboxEvent
3. Dispatcher 发布到 Redis Streams
4. 发布成功后标记 published
~~~

Outbox 状态为 pending、publishing、published、retrying、dead_letter。

OutboxEvent 建议字段：

| 字段 | 作用 |
| --- | --- |
| outbox_id | 事件身份 |
| run_id | 研究运行边界 |
| task_id / attempt_id | 关联任务和执行 |
| dispatch_key | 发布幂等键 |
| stream_name | Redis Stream |
| payload_ref | 消息内容或引用 |
| status | 投递状态 |
| retry_count | 已重试次数 |
| next_retry_at | 下次重试时间 |
| published_at | 成功发布时间 |
| last_error | 最近错误 |

如果发布后、标记前进程崩溃，可能产生重复消息。Worker 必须使用 dispatch_key、attempt_key 或 result_key 幂等处理。

### 4.2 Worker ack 顺序

~~~text
Worker 执行
  -> 事务性写入 SQLite 结果和状态
  -> 确认 SQLite 提交成功
  -> ack Redis 消息
~~~

不能先 ack 再写数据库，否则 Worker 在两步之间崩溃时，Redis 认为任务完成但 SQLite 没有结果。

## 5. 状态、取消和恢复

Task 状态：

~~~text
pending_dispatch -> queued -> running -> waiting -> succeeded
                      -> backpressured -> queued
                                      -> partial
                                      -> failed
                                      -> cancelled
                                      -> timed_out
pending_dispatch -> blocked
~~~

队列拥堵时可以进入 backpressured，不丢弃任务。Outbox 进入 dead_letter 时，相关 Task 必须产生 dispatch_failed 事件并进入可观察的 blocked 状态，不能继续伪装为 queued。

Attempt 状态：

~~~text
queued -> leased -> running -> succeeded
                            -> failed
                            -> expired
                            -> cancelled
~~~

租约过期后，旧 Attempt 保留为 expired，Task 创建新 Attempt 接管。迟到结果只能进入审计，不能覆盖已经取消或被新 Attempt 接管的结果。

取消采用协作式机制：

~~~text
Runtime 设置 cancel_requested
  -> Worker 在模型调用、工具调用和阶段边界检查
  -> 保存必要 Checkpoint
  -> 停止新增动作
~~~

取消父 Task 时，递归取消尚未完成的后代 Task。

恢复时保留原 Task，选择最近的有效 Checkpoint，创建新的 Attempt，从 Checkpoint 继续。

## 6. 调度操作

| 操作 | 语义 |
| --- | --- |
| agent.spawn | 创建 Thread/Task，SQLite 提交后立即返回句柄 |
| agent.wait | 基于 Event 游标等待 all、any 或指定 Task，可超时 |
| agent.status | 返回当前快照、子任务摘要、预算和最新事件序号 |
| agent.send | 向已有 Thread 的 Mailbox 投递后续消息 |
| agent.cancel | 请求取消当前 Task 及后代 |
| agent.resume | 从有效 Checkpoint 创建新 Attempt |
| result.get | 读取摘要、完整结果或指定 section |

spawn 的返回表示任务已经可靠写入 SQLite，不表示 Redis 已经投递，也不表示子 Agent 已经完成。Redis 由后台 Dispatcher 重试投递。

重复 spawn 使用 dispatch_key 幂等；相同 key 返回已有 Task，明确生成新 key 才创建新 Task。

## 7. 递归、并发和预算

第一阶段允许两层子 Agent 派生：

~~~text
根 Agent：depth 0
第一层 Subagent：depth 1
第二层 Subagent：depth 2
~~~

depth 为 2 的 Agent 不能继续派生。Runtime 还限制单个 Task 的子任务数、Run/Thread/Worker 三层并发数、总截止时间和父级预算。

预算不会被 Runtime 静默降低。每个 Task 记录 requested_budget、allocated_budget、consumed_budget 和 remaining_budget。如果无法提供申请预算，必须明确标记 budget_reduced 或 budget_denied。

当前采用调用次数限制，不做费用金额估算。因此只能防止无限调用，不能保证精确金额上限。该限制必须显式披露。

## 8. Subagent 上下文和结果

### 8.1 TaskBrief

Subagent 不接收父 Agent 的完整上下文，而接收 Runtime 生成的隔离 TaskBrief：

~~~text
任务目标和成功标准
文章、文件和 EvidenceRef
必要的父任务结论
允许使用的工具
输出契约
模型和预算策略
deadline、depth 和父子标识
~~~

父 Agent 的无关历史、其他 Subagent 的全部输出和内部思考不会默认复制过去。

### 8.2 渐进式结果读取

采用三层结果：

1. 摘要、状态、关键观点、EvidenceRef、缺口和 result_ref 始终注入父 Agent；
2. 短结果自动完整注入；
3. 长结果通过 result.get 按需读取。

result.get 支持 summary、full、section。读取行为写入 result.summary_viewed、result.section_viewed 和 result.full_viewed。

结果不可覆盖。修正结果产生新的 result_version，旧版本继续保留。

### 8.3 结果提交

Subagent 使用统一 result.submit：

~~~json
{
  "status": "succeeded",
  "summary": "...",
  "evidence_refs": [],
  "result_ref": "...",
  "warnings": []
}
~~~

Runtime 校验必填字段、EvidenceRef、状态和权限。缺少元数据时允许补交一次；仍失败则保留完整正文并标记 incomplete_metadata，不丢弃分析。

父 Agent 通过 wait_all 获得所有子任务状态，自行决定共识、冲突、缺口和最终交付。Runtime 不替父 Agent 自动合并金融结论。

## 9. 工具和 Agent 动作

主 Agent 和 Subagent 使用同一套动作协议，但使用不同的权限快照：

~~~json
{
  "action": "tool.call",
  "tool": "article.read",
  "arguments": {}
}
~~~

Runtime 依次检查 JSON 结构、动作契约、工具权限、Task 状态、递归深度、预算、截止时间和工具参数。

Subagent 通过受 Runtime 管理的 agent.spawn 派生下一级 Agent，不能直接访问数据库创建任务，也不能通过 JSON 绕过权限。

工具事件为 tool.requested、tool.started、tool.completed 和 tool.failed。

## 10. 模型适配和选择

不同模型通过统一 ModelAdapter 接入：

~~~text
ModelAdapter
  -> request
  -> stream events
  -> normalized result
  -> normalized error
  -> cancel
~~~

模型返回的文本、动作和流式片段都转换为内部事件。

一次 Run 选择默认模型，子 Task 继承；单个子任务可以从当前 Run 的允许列表中选择其他模型：

~~~json
{
  "model_policy": {
    "selected_model": "gpt-6.1-sol",
    "allowed_models": ["gpt-6.1-sol", "deepseek-flash"],
    "auto_fallback": false
  }
}
~~~

模型元数据进入 model_profiles；API Key 只放环境变量或密钥管理系统。每个 Attempt 保存不可变的模型、Endpoint、Prompt 版本、工具契约版本和参数摘要。

系统不自动从 GPT 切换到 DeepSeek。模型失败后报告失败，用户可以选择其他模型创建新的 Attempt 并从 Checkpoint 恢复。

只对网络超时、临时 5xx 等传输错误自动重试同一模型。格式、权限、额度和逻辑失败直接交给父 Agent。错误类型包括 transport_error、provider_error、contract_error、quota_error、timeout 和 cancelled。

流式内容实时转发，同时写入运行记录。超时分为连接、读取、单次模型调用、Attempt 和 Task 截止时间五层。

## 11. Message、Event、Checkpoint 和运行记录

Message 追加写入数据库，使用 Thread 内递增 sequence 保证顺序。

Message 建议字段：

| 字段 | 作用 |
| --- | --- |
| message_id | 消息身份 |
| thread_id | 投递目标 |
| task_id | 关联工作 |
| sequence | Thread 内顺序 |
| kind | initial、follow_up、cancel 或 system |
| payload_ref | 消息正文或产物引用 |
| idempotency_key | 防止重复投递 |
| status | queued、delivered、consumed 或 rejected |
| created_at / consumed_at | 投递审计 |

Event 追加保存已经发生的事实，包含全局 event_id 和 Thread 内序号；父 Agent 使用游标恢复事件。

Event 建议字段：

| 字段 | 作用 |
| --- | --- |
| event_id | 全局递增标识 |
| run_id | 研究运行边界 |
| aggregate_type | Thread、Task 或 Attempt |
| aggregate_id | 事件所属对象 |
| thread_seq | Thread 内顺序 |
| event_type | 状态或动作类型 |
| payload_ref | 事件详情引用 |
| created_at | 发生时间 |

事件是事实记录，不由模型直接改写。当前状态是可查询快照，Event 是可重放历史。

Checkpoint 保存结构化恢复状态：

~~~text
current_phase
completed_steps
pending_steps
input_refs
partial_result_ref
last_event_seq
~~~

ResultRef 建议字段：

| 字段 | 作用 |
| --- | --- |
| result_ref | 结果身份 |
| task_id / attempt_id | 来源任务和执行 |
| result_version | 结果版本 |
| summary_ref | 摘要产物 |
| full_ref | 完整结果产物 |
| evidence_refs | 证据引用 |
| storage_uri | 文件或对象位置 |
| created_at | 生成时间 |
| retention_until | 保留期限 |

大结果、原始模型输出、Query、Trace 和中间产物保存到：

~~~text
D:/AAAcode/code-code/agent+/harness/runtime-records
~~~

建议结构：

~~~text
runtime-records/
├── runs/<run_id>/
│   ├── query.json
│   ├── events.jsonl
│   ├── model-calls.jsonl
│   ├── trace.jsonl
│   └── artifacts/
├── errors/
└── indexes/
~~~

该目录不进入 Git。日志必须脱敏，不得写入 API Key 或其他凭证。

## 12. 冲突审查

当前确认的设计之间没有必须阻止实现的直接冲突：

| 表面冲突 | 处理结果 |
| --- | --- |
| spawn 立即返回，但 Redis 可能尚未投递 | spawn 只承诺 SQLite 持久化接收，状态明确为 pending_dispatch |
| 结果摘要注入，但主 Agent 可能需要全文 | 摘要始终注入，短结果完整注入，长结果通过 result.get，重要读取记录 Event |
| 两层递归可能放大预算 | depth、子任务数、并发数、截止时间和父预算共同限制 |
| 模型自主判断与 Runtime 校验并存 | 模型决定任务和分析方向，Runtime 只守权限、契约、预算、状态和可靠性 |
| 用户选模型但不自动切换 | allowed_models 支持选择，auto_fallback=false |
| 允许部分结果但要求可靠状态 | Task/Attempt/Event 分离，父 Agent 收到全部状态后决定交付 |

已知限制：

1. 当前只限制调用次数，不计算费用金额，不能宣称精确费用安全。
2. 当前不维护模型能力矩阵，不兼容的模型能力将在调用时表现为结构化失败。
3. 本文不声称掌握 Codex 的私有内部实现，只借鉴公开可观察的持久化 Agent、异步任务、上下文隔离和恢复模式。
4. 本文不证明金融答案质量，也不替代 ArticleCard、证据和审核设计。

## 13. 设计验收指标

调度实现至少验证：

- 同一 dispatch_key 不重复创建 Task；
- 一个 Task 不被两个 Worker 同时有效 claim；
- 无有效 lease 的 Worker 不能提交结果；
- Redis 发布失败时 Outbox 可重试，任务不丢失；
- SQLite 提交后 Redis ack，重复消息不会重复产生结果；
- Attempt 重试保留旧记录并创建新 Attempt；
- Event 游标可以补读中断期间的事件；
- send 保持 Thread 内顺序；
- 两层递归之外的派生会被拒绝；
- 父取消传播到未完成子任务，迟到结果不会覆盖取消状态；
- resume 使用有效 Checkpoint；
- 模型失败不会自动切换模型；
- result.get、结果版本和 inspected Event 可追溯；
- 队列拥堵时进入 backpressured，不静默丢弃；
- 运行记录落在 runtime-records，且不包含凭证。

这些指标只验证 Harness 调度正确性，不等同于金融分析结果正确。

## 14. 实现前置条件

进入代码前还需要形成：

1. SQLite 表结构和状态转换约束；
2. Redis Streams 消息格式和 Outbox 投递协议；
3. spawn、wait、status、send、cancel、resume、result.get 接口契约；
4. ModelAdapter、Worker 和 Dispatcher 的内部接口；
5. 设计级测试用例和故障注入清单。

完成这些契约后才能进入代码实现。当前代码不能因为存在同步 delegate_fn 就被描述为已经实现 Harness 2.0。

## 15. 一致性审查与冻结结论

审查日期：2026-10-18。

本次审查覆盖：

- 本文已确认决策；
- 011、012 历史调度文档的适用范围；
- Thread、Task、Attempt、Message、Event、Checkpoint 和 ResultRef 的关系；
- SQLite、Redis Streams、Outbox、ack、租约和幂等顺序；
- spawn、wait、send、cancel、resume 的生命周期；
- Subagent 上下文、结果读取、工具动作和模型选择；
- 并发、递归、预算和运行记录边界。

审查结论：

1. 方案之间没有方向性冲突；
2. 本节之前补充的 run_id、Message/Event 字段、backpressured 和 dead_letter 处置已经纳入本基线；
3. 011、012 继续作为历史依据，不覆盖本文的已确认语义；
4. 文章分析、证据审核和金融回答质量仍属于其他设计，不被本基线替代；
5. 本基线冻结的是语义和边界，不冻结 Redis Stream 名称、SQLite 索引、租约时长、重试次数和短结果的具体字节阈值；
6. 上述实现参数不得改变本文语义；若需要改变语义，必须新增变更文档并重新确认。

冻结后的变更规则：

- 已确认语义的代码实现可以直接推进，但必须补契约和测试；
- 实现参数可以在不改变语义的前提下调整，并记录原因；
- 新增模型能力、改变自动 fallback、改变递归边界或改变结果交付语义，必须先修改设计并重新确认；
- 当前代码仍是同步委派，不能把文档冻结描述成 Harness 2.0 已实现。
