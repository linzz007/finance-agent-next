# Harness 2.0 代码级验收报告

日期：2026-10-19  
基线：`H2-CONTRACTS-2026-10-18-v1`、`H2-DISPATCH-2026-10-18-v1`  
范围：SQLite 权威调度、Redis Streams 投递、Worker、模型执行边界和 opt-in 金融 Root。  
本报告不是金融答案质量验收，也不是商业化稳定性证明。

## 1. 执行结果

定向命令：

```text
uv run pytest -q tests/test_dispatch_pressure.py \
  tests/test_dispatch_observability.py \
  tests/test_dispatch_foundation.py \
  tests/test_dispatch_execution.py \
  tests/test_dispatch_finance_root.py \
  tests/test_dispatch_finance_service.py \
  tests/test_dispatch_finance_agent.py \
  tests/test_dispatch_http_api.py
```

结果：`194 passed, 2 skipped`。跳过项是未设置 `H2_TEST_REDIS_URL` 的真实 Redis
联测。当前环境没有运行付费模型、真实金融 Query 或同步/异步业务语义对照。

本轮新增或修复后的重点验证：

- 结果只授权 `full_ref` 时可以读取全文，但不能读取未授权 summary/section。
- 终态 Thread follow-up 没有 initial 消息时，Root 可从 follow-up mailbox 消息建立新
  Task 的初始 Query；它不复用旧 Task 的状态。
- `wait_snapshot` 的 `any` 和 `specific` 语义有回归覆盖。
- 子 Agent 等待兄弟任务会在权限层拒绝，不留下 waiting 状态；自环检测仍由调度层拒绝。

## 2. C01-C18 矩阵

状态含义：

- `verified-offline`：代码与 FakeRedis/临时 SQLite 测试均验证。
- `verified-partial`：核心离线语义已验证，但真实 Redis、故障注入或业务对照仍缺。
- `not-verified`：契约有定义，但当前没有足够的实现或验收证据。

| 编号 | 当前状态 | 证据与边界 |
| --- | --- | --- |
| H2-C01 | verified-offline | spawn 幂等、参数冲突、并发同 key 和持久回执。 |
| H2-C02 | verified-offline | 跨 Run、兄弟、越权状态/结果/输入引用拒绝；本轮补充了按具体视图授权。 |
| H2-C03 | verified-offline | 双 Worker claim、租约过期、旧 lease fencing。 |
| H2-C04 | verified-partial | FakeRedis 的发布崩溃、Redis 清空、dead-letter/redrive 和非 PEL 终态 Stream 安全清理已覆盖；真实 Redis 未运行。 |
| H2-C05 | verified-offline | 结果提交后 ACK 失败，恢复只确认，不重复执行或覆盖结果。 |
| H2-C06 | verified-offline | all/any/specific、事件分页和等待超时已覆盖；真实网络长轮询未验收。 |
| H2-C07 | verified-offline | durable wait 释放执行名额、两层递归、第三层拒绝和父子预算边界。 |
| H2-C08 | verified-offline | message sequence、幂等 send、子 Thread 终态 follow-up；Root 终态随 Run 关闭，不能继续 follow-up。 |
| H2-C09 | verified-offline | 取消子树、迟到结果审计、取消后的调用预约 fencing。 |
| H2-C10 | verified-offline | 有效/缺失/损坏 checkpoint、恢复新 Attempt、动作不重放和预算不重置。 |
| H2-C11 | verified-offline | 模型错误不自动 fallback；显式用户模型策略和 resume 选择可追溯。 |
| H2-C12 | verified-offline | metadata repair 失败仍保留完整正文并标记 partial/incomplete_metadata。 |
| H2-C13 | verified-offline | summary/full/section、分页、版本和完整读取事件边界。 |
| H2-C14 | verified-offline | 并发父子预算、调用 charge/refund、未知在途调用和余额守恒。 |
| H2-C15 | verified-offline | 半流式 JSON、未知工具、原生 tool call、截断输出和单次物理请求。 |
| H2-C16 | not-verified | 尚无同 Query 的 1.0 同步路径与 H2 异步路径语义对照。 |
| H2-C17 | verified-partial | DB/Artifact 故障不伪造 succeeded 且不提前 ACK、运行记录导出和孤儿产物 dry-run/GC 已覆盖；真实磁盘损坏和部署日志尚未验收。 |
| H2-C18 | verified-partial | 失败依赖 blocked、自环、兄弟 wait 权限拒绝和显式服务循环已覆盖；真实多 Worker 长时间死锁/环路压力未验收。 |

### 2.1 新增调度压力与观测验证

`tests/test_dispatch_pressure.py` 新增 3 个离线用例：

- 高水位下新任务标记为 `backpressured`，已接受 Outbox 仍可发布；降到低水位后仍未发布的任务恢复为 `pending_dispatch`；
- 等待老化会影响 SQLite 发布顺序，但不改变原始优先级或 Redis FIFO 语义；
- Stream 清理保留未结算消息和 PEL pending 消息，只允许删除已满足年龄、契约和 SQLite 终态对账的 entry。

`tests/test_dispatch_observability.py` 另外覆盖服务单周期、畸形消息隔离、脱敏运行记录、指标和孤儿产物清理。它们证明离线实现边界，不证明 Redis 服务部署或并发竞态下的清理安全。

## 3. 金融 Root 的真实完成度

### 已实现并验证

- Root 使用真实 H2 Thread/Task/Attempt，不再只停留在同步 Python 调用栈。
- 每次 Root Attempt 只推进一个 Domain step；子任务通过 `agent.spawn`，等待通过
  checkpoint + durable wait，恢复创建新的 Attempt。
- 文章 list/read、analyst delegate 和 final 可通过 artifact ref 交接。
- 最终正文、`finance_state`、EvidenceRef、gap/warning 和 partial metadata 仍走统一
  Result 提交路径。
- 默认 1.0 入口保持不变，H2 只通过显式 Service/HTTP 注入启用。

### 已定义但模型驱动路径尚未完整实现

`FinanceRootState` 已有以下字段：`research_plan`、`article_states`、
`evidence_ledger`、`coverage`、`candidate_answer`、`review`、`delivery`。
但是当前 `FinanceH2Domain` 的动作契约只有 `tool_call`、`delegate`、`final` 和
`request_approval`，没有定义模型如何在每个动作中提交这些字段的结构化更新。
因此当前模型路径中：

- `article_states` 主要由 Runtime 观察到的子结果粗略追加，尚未完整区分 listed/read/used/excluded；
- `research_plan`、`coverage`、`review` 不会自动从 final 文本推导；
- `candidate_answer` 当前保存候选 artifact 引用，但还不是最终 ResultRef 的完整版本索引；
- 不能把脚本 Domain fixture 中手工填充的状态，冒充成模型已经稳定产出的状态。

这属于需要单独确定的金融 Domain 动作协议，不在本轮擅自用 Prompt 猜测或硬编码
“必须审核/必须读几篇”来解决。

## 4. 结论与下一步

当前可以确认：H2 调度底座、压力/观测补充和 opt-in 金融纵向链路已经达到离线代码级可运行状态，
但不能确认 H2 金融业务已经完整实现，更不能确认真实 Redis、真实模型、多用户并发或
金融回答质量已经通过。

下一步按优先级：

1. 先确定 Domain action 的状态更新契约：模型如何提交计划、文章状态、证据绑定、coverage、review 和 delivery。
2. 再把这些字段接入 `FinanceRootState` 的每个 durable boundary，并补 H2-F08/F09 的模型驱动测试。
3. 做 H2-C04/C17/C18 的真实 Redis/故障验证。
4. 最后用固定金融 Query 做 1.0 同步与 H2 异步对照；结果必须同时报告链路完成度和金融语义质量。

