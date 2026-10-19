# 020 Harness 2.0 调度压力与 Stream 保留

日期：2026-10-19

状态：已实现并完成离线验证；本记录只落地已经冻结在 `H2-CONTRACTS-2026-10-18-v1` 中的调度事实，不改变金融研究语义、模型选择或默认 1.0 入口。

## 1. 目标与边界

本变更补齐调度底座的两个明确缺口：

1. SQLite 待投递队列的高低水位、排队老化和可观测状态；
2. Redis Stream 的安全清理。

它们不负责选择文章、决定证据覆盖、审核金融结论或自动切换模型。SQLite 仍是 Task/Attempt/Outbox 的权威状态，Redis 仍只是投递通知和 PEL 恢复载体。

## 2. 队列压力协议

### 2.1 水位

- `outbox_high_watermark` 是待投递 Outbox 数量达到后，后续新建任务进入 `backpressured` 的阈值；已经接受的任务不能被伪装成未接收。
- `outbox_low_watermark` 必须小于高水位。待投递数量降到低水位后，仍未发布的 `backpressured` 任务恢复为 `pending_dispatch`。
- 水位只控制 SQLite 中尚未发布的 Outbox，不撤销已经进入 Redis 的消息，也不抢占运行中的 Attempt。
- Dispatcher 仍可发布已经接受的 `pending_dispatch`/`backpressured` 任务；否则高水位会把已接受的任务自身锁死。
- `backpressured` 也可能来自 Worker 并发上限。只有存在未发布 Outbox 的任务才属于队列压力恢复范围，不能误把运行并发压力恢复成待投递状态。

### 2.2 老化

SQLite 发布批次按“有效优先级、创建时间、Outbox ID”排序。有效优先级是原始 Task priority 加上按等待时间计算的有界老化增量。老化只用于防止低优先级任务永久饥饿，不改变 Task 的原始优先级快照，也不承诺 Redis Stream 内已经发布消息的严格优先级。

### 2.3 失败与可观测性

水位变化记录事件或指标，但不丢弃任务。到期、死信、期限和发布失败仍按原有 Outbox/Attempt 协议处理。

## 3. Stream 安全清理

清理必须是显式调用，默认 dry-run。执行清理前同时满足：

- 只扫描超过保留年龄的 Stream entry；
- 不删除当前 Consumer Group 的 PEL pending entry；
- 能解析并核对消息契约；
- `delivery_settled` 证明 SQLite 中对应 Outbox 已终态、已切换 Attempt 或已持久等待；
- 不删除 SQLite 审计记录，只删除 Redis 传输副本。

无法确认 PEL、消息契约或 SQLite 对账状态时保留 entry 并返回原因。XACK 不等于删除；不能使用无条件 `XTRIM MAXLEN` 或盲目 XDEL。清理结果必须包含候选、删除、保留及原因，便于审计。

## 4. 验收

- 高水位下新任务仍有可查询句柄，已接受任务可以继续发布；低水位后未发布任务恢复。
- 低优先级老任务在配置的老化步长后能先于持续到达的高优先级新任务被选中；同优先级仍按创建时间稳定排序。
- 清理不会删除 pending PEL、未终态 Outbox、坏消息或无法对账的 entry；已安全结算的老 entry 可以删除。
- 所有行为通过 FakeRedis/临时 SQLite 离线验证；真实 Redis 联测单独记录，不把离线通过写成部署通过。

## 5. 本次验证

- `tests/test_dispatch_pressure.py`：3 passed；覆盖水位、老化、未结算/PEL 保留和安全删除。
- `tests/test_dispatch_observability.py`：3 passed；覆盖显式服务单周期、畸形消息隔离、运行记录/指标/孤儿产物。
- H2 定向集合：`194 passed, 2 skipped, 1 warning`；跳过项是未设置 `H2_TEST_REDIS_URL` 的真实 Redis 联测。
- 1.0 金融/共享 Runtime 回归：`1589 passed, 30 skipped, 1 warning`；未调用真实模型。

真实 Redis、多进程 kill-point、长时间压力和金融 Query 质量仍不属于本次离线验证结论。
