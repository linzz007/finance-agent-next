# Harness 2.0 Redis Streams 投递协议

状态：契约已确认并冻结；部署和故障验证进度见 [014](../changes/014-harness-v2-foundation.md)。  
版本：`h2.dispatch.v1`；契约冻结：`H2-CONTRACTS-2026-10-18-v1`；日期：2026-10-18。  
依据：[冻结调度基线](../changes/013-harness-v2-dispatch-baseline.md)。配套：[SQLite 契约](sqlite-schema.md)、[调度接口](interface-contracts.md)。

## 1. Redis 的职责和保证

Redis 只搬运“哪个 Attempt 值得尝试领取”的消息，不保存文章、Query、完整金融答案、API Key、模型配置正文或租约令牌。SQLite 的 Task/Attempt/Outbox/Event 才是权威。

本协议提供**至少一次投递 + 数据库幂等提交**，不承诺模型和工具副作用 exactly-once。网络断开时，供应商可能已经执行/计费；没有供应方幂等能力的写工具不能自动重放，必须标为 outcome_unknown 交父 Agent/用户判断。

第一阶段单机：一个消费组、多 Worker。Redis 重启/数据丢失可以从 SQLite 中尚需执行的工作重建。Redis 可采用 AOF 辅助降低重建量，但 AOF、PEL 和 consumer ownership 都不能替代 SQLite 的状态、有效 lease 和权限。

| 名称 | 初始命名 | 含义 |
| --- | --- | --- |
| namespace | `finance-agent-next:<instance_id>:h2` | 持久实例标识，不使用进程 PID；开发/测试不同 namespace |
| task stream | `<namespace>:dispatch` | 所有可执行 Attempt 的调度通知 |
| consumer group | `h2-workers-v1` | 只创建一个逻辑工作组；多个组会导致重复广播，不是扩容 |
| consumer | `<worker_id>:<process_boot_id>` | 进程重启有新 boot_id，不冒用旧租约 |

名称是可配置实现参数，不是业务身份；消息必须与 SQLite Outbox 的 stream_name 对应。优先级先在 SQLite 待发布队列按 priority DESC、created_at、outbox_id 排序；Redis 单 Stream 仍 FIFO，已发布消息不承诺严格优先级，不抢占正在执行的任务。有限发布批次和老任务老化防止长时间饥饿；不能把 priority 宣称为强实时保证。

## 2. Stream 消息格式

Redis field/value 全部是 UTF-8 字符串；非数字 ID 作为不透明字符串。只接受以下字段，不把未知参数直接传给模型或工具。

```json
{
  "schema_version": "h2.dispatch.v1",
  "outbox_id": "obx_001",
  "run_id": "run_001",
  "task_id": "tsk_002",
  "attempt_id": "att_002_1",
  "attempt_key": "atk_002_1",
  "dispatch_key": "compare-author-a",
  "dispatch_generation": "1",
  "emitted_at": "1792285200000"
}
```

| 字段 | 校验和作用 |
| --- | --- |
| schema_version | 稳定 wire 版本；文档草案标签不进入 wire；未知主版本拒绝执行并告警 |
| outbox_id | 指向已提交的 Outbox；消费时读取该记录，不能信任消息自己声明的 run/task |
| run_id/task_id/attempt_id | 必须与 Outbox、current Attempt 和 Task 的数据库关系全部一致 |
| attempt_key | 一次执行的唯一身份；重试创建新 key，重复通知仍是原 key |
| dispatch_key | spawn 逻辑工作幂等键；不能只用它去重消费，否则会错误丢弃同一 Task 的新 Attempt |
| dispatch_generation | 同一 Attempt 的再次通知编号；只重新投递通知，不增加模型执行身份 |
| emitted_at | UTC 毫秒时间字符串，仅供延迟观测；不是 lease、deadline 或事件游标 |

消息初始上限 4 KiB，超出应作为协议错误拒绝，不截断。Stream entry ID（如 `1792285200000-0`）由 Redis 返回，单独写回 Outbox；它不是 SQLite event_id，也不是任务身份。

`h2_outbox.payload_json` 保存上述消息的规范内容；重复发布同一个 Outbox 时字段不变，即使产生多个 Redis entry ID。Worker 必须容忍消费早于 Dispatcher 将 Outbox 标为 published，因为 XADD 与 DB 确认之间存在窗口；匹配 pending/publishing/retrying 的已提交 Outbox 也可 claim。

## 3. Outbox 发布

```text
spawn / resume / 基础设施重试
  -> SQLite 事务保存 Task、queued Attempt、Event、Outbox(pending)
  -> commit，返回可靠接收句柄

Dispatcher
  -> T12 事务 CAS 领取到期 pending/retrying Outbox
       status=publishing，记录 publisher_token 和 publishing_expires_at
  -> commit
  -> XADD <stream> * <上述 field/value>
  -> T12 事务用 publisher_token CAS 确认 published、redis_entry_id、published_at
       Task 仍 pending_dispatch/backpressured 才可投影为 queued
       Task 已 running/终态则只记录发布结果，不向后改状态
```

关键分支：

1. **XADD 前崩溃**：publishing 锁到期后重试；没有丢任务。
2. **XADD 成功、数据库确认前崩溃/超时**：不知道是否发布，允许重新 XADD；Worker 依 Attempt 幂等，不能宣称没有重复。
3. **两个 Dispatcher 竞争**：只有拿到 publisher_token 的可以确认；过期发布者即使晚返回也不能覆盖新发布状态。
4. **Redis 不可用**：Outbox retrying，指数退避加 jitter；Task 为 pending_dispatch，拥堵时 backpressured；spawn 已接收不能改称未接收。
5. **有限重试耗尽**：Outbox dead_letter、Task blocked、dispatch_failed Event 同事务。它不是已经 failed 的金融判断，也不伪装 queued。父 Agent/status 能读到原因。
6. **显式重投**：工作尚未开始时可保留 queued Attempt，创建更高 dispatch_generation 的新 Outbox；已失效/结束的 Attempt 必须走新 Attempt 的恢复流程。历史 dead_letter 不覆盖。

有界重试次数、退避范围和发布锁时长属于实现参数，实施前固定进配置和测试快照。它们不能延长 Run deadline、扩大调用预算或触发不同模型 fallback。

## 4. Worker 消费、claim 和 ACK

首次建组使用 `XGROUP CREATE <stream> h2-workers-v1 0-0 MKSTREAM`；已存在组接受 BUSYGROUP。使用 `$` 建组可能跳过已有消息，因此本协议不采用。

正常消费：`XREADGROUP GROUP <group> <consumer> COUNT <batch> BLOCK <bounded_ms> STREAMS <stream> >`。`COUNT` 不大于本 Worker 当前可领取容量；不预取大量任务到某 Worker 的 PEL 中。

```text
读到 Stream entry
  -> 校验 wire、读取 Outbox 与 Task/Attempt
  -> T02 原子 claim（SQLite 才决定执行权）
  -> commit 后执行模型/工具，定期 T03 heartbeat
  -> 落盘 Artifact
  -> T05 原子提交结果/Task 状态/Event
  -> SQLite commit 确认
  -> XACK <stream> <group> <entry_id>
```

| 观察状态 | 动作 | 是否 XACK |
| --- | --- | --- |
| Task/Attempt 已终止、通知属于旧 Attempt | 写幂等的 ignored/stale 审计，不能重新执行 | 可；状态已持久化 |
| 同一 Attempt 由别的有效 lease 执行 | 不竞争模型调用，不修改 lease | 不能仅因读到重复就 ACK 正在处理的唯一消息；等有效执行完成或持久交接后确认 |
| 未到期、依赖满足、无有效 claim | T02 成功者执行；其他竞争者重读 | 完成持久交接/结果后才 ACK |
| 暂无并发容量/依赖未满足 | 保持 pending；或事务性创建后续 Outbox、记录 backpressure/blocked 后交回 | 仅后一种可靠交接完成才可 ACK |
| Worker 保存 waiting Checkpoint 并释放执行 | T10 保存持久 wait；未来唤醒创建新 Attempt/Outbox | T10 commit 后可；不是将 Task 声称 succeeded |
| Redis ACK 失败或 ACK 前 Worker 崩溃 | 留在 PEL，恢复者读取 DB 终态/交接记录 | 可重试 ACK，不重复模型执行 |
| SQLite 不可用/提交结果失败 | 停止新动作，保留临时产物，不声称完成 | 不可 |
| 格式错误或伪造消息 | 记录脱敏协议事件/错误产物并拒绝；不能读取越权正文 | 审计持久化后可；DB 不可用则先不 ACK |

XACK 只移除消费组的 pending 记录，**不删除 Stream entry**，也不表示模型已理解或答案正确。

### 4.1 有效租约才是执行权

claim、heartbeat、Checkpoint、工具开始、结果发布都要检查：

```text
task.current_attempt_id == attempt_id
AND attempt.lease_token == supplied_token
AND attempt.lease_expires_at > database_now
AND task.cancel_requested_at IS NULL
AND Task/Run 未终止且 deadline 未到
```

取消后允许专门的收尾事务保存检查点/partial 审计，但不允许以旧 token 发布成功。有效状态查询、续租和取得工具许可在短事务内完成；不能用查询后隔很久再执行的旧快照绕过检查。

SQLite 时间由受控 Repository 获取，单机统一；运行中用 monotonic clock 驱动计时，持久恢复用 UTC deadline。时钟明显跳变记录告警，不默默扩大预算。lease token 不向模型和公共 Trace 暴露。

## 5. PEL 回收与 SQLite 对账

使用 Redis 6.2+ 的 `XAUTOCLAIM` 按 idle 回收 pending；它只能把 Redis 消息转交给新 consumer，**不能证明原 Worker 的 SQLite lease 已过期**。命令可游标遍历，不能每次只看第一批。

```text
Reconciler/Recovery Worker
  -> XAUTOCLAIM 取回 idle 消息
  -> 查询 SQLite
     有效 lease：不接管执行，等待原执行提交/租约到期
     已持久完成/已交接：只 ACK
     lease 过期：T08 标旧 Attempt expired，创建新 Attempt + Outbox
     queued 且无 lease：按 T02 领取原 Attempt
  -> T08 提交后 ACK 旧消息；新 Attempt 由 Outbox 分发
```

长模型调用中 PEL 的 idle 时间可大于 SQLite heartbeat 间隔；不能把 idle 当作 Worker 死亡，不能因此启动第二次模型调用。原 Worker 即使不再是 Redis consumer owner，仍只能凭有效 SQLite lease 提交；ACK 不要求它重新取得业务执行权。

**对账不能只扫描 Redis。** Reconciler 还扫描 SQLite：

- pending/retrying/publishing 超时 Outbox：重试或回收发布锁；
- published 但长期 queued、无有效 lease 的 Attempt：检查并创建同 Attempt 下一 generation 的通知；
- leased/running 且 lease 过期：T08；
- waiting：按数据库目标状态/截止时间唤醒，不依赖 Redis 通知；
- cancelled/timed_out/terminal：回收并发名额，拒绝旧消息；
- blocked/dead_letter：告警并向 status 暴露，不悄悄无限重试；
- Redis flush/restart：重建组并从上述权威状态重发，历史 succeeded 不重跑。

这样，Redis 已确认 XADD 后消息被驱逐/丢失，或通知 ACK 后进程崩溃，也不会变成无法发现的孤儿任务。

## 6. 保留、背压、故障与观测

- 不使用无条件 `XADD MAXLEN ~ ...`，不在有未处理/PEL 任务时盲目 XTRIM/XDEL。
- 清理前查组进度、最早 pending ID 和 DB 状态，保留未持久完成或未持久交接的条目。删除过旧终态通知仅释放传输存储，不删除 SQLite 审计。
- XAUTOCLAIM 发现 pending entry 已从 Stream 删除时，记录 delivery_missing 并由 SQLite 对账补发；不能直接算完成。
- 背压按可配置待投递/排队高水位报告；恢复使用低水位滞回，避免频繁切状态。运行中的分析不因队列满而直接丢弃；deadline 仍生效。
- Redis 无密码/ACL、非本地端口暴露或测试/生产 namespace 混用在部署检查中拒绝；日志不打印 Redis URL 密码或模型凭证。
- 指标至少包括：Outbox 最老等待时长、retry/dead_letter、ready/PEL 数、claim 冲突、有效 lease、expired 接管、stale 拒绝、重复通知、SQLite busy、事件延迟、调度 p50/p95、无引用产物。指标与 run/task/attempt/trace 关联，但不含正文与密钥。

实现映射：`DispatchRepository.take_outbox()` 使用配置的高低水位和等待老化排序，
`RedisStreams.trim_safe()` 只在 PEL、消息契约和 SQLite `delivery_settled` 均可确认时
返回可删除 entry；显式服务循环、运行记录导出、时钟跳变告警和孤儿产物扫描见
[020 调度压力与 Stream 保留](../changes/020-dispatch-pressure-and-stream-retention.md)。
真实 Redis 联测和部署级故障仍需单独验收。

## 7. 必测故障矩阵

| 注入位置 | 必须观察到的结果 |
| --- | --- |
| spawn commit 前进程退出 | 没有半个 Thread/Task；同 key 重试可创建 |
| spawn commit 后、XADD 前退出 | 返回句柄可查，Outbox 最终可重新投递 |
| XADD 后、published 前退出 | 允许重复 entry；同 Attempt 最多一个有效 lease |
| 两 Worker 同时 claim | 一次成功；另一方不调用模型 |
| 结果 commit 后、ACK 前退出 | 重消费只 ACK，不产生新结果或调用 |
| XAUTOCLAIM 与长模型调用交叠 | 有效 DB lease 不被夺取，不重复计费调用 |
| Redis 全部清空 | 从 SQLite 重建必要通知，终态不重跑 |
| Worker 失联、新 Attempt 接管、旧结果迟到 | 旧结果仅审计，不改变 current Attempt/已接受结果 |
| Redis 长期不可用直到投递上限 | 可见 blocked + dispatch_failed，不丢任务、不虚假 queued |
| cancel 与模型完成竞争 | 按数据库提交顺序裁决；取消先提交则不发布 |
| ACK 后等待进程退出 | 持久 wait 可由 DB 扫描恢复唤醒 |

此矩阵定义完整目标验收；当前代码已完成其中一部分的 FakeRedis/脚本故障验证，实际覆盖及真实 Redis 未验证项见 [014](../changes/014-harness-v2-foundation.md)，不把设计矩阵的存在当成全部跑通。

机制来源：[XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/)、[XACK](https://redis.io/docs/latest/commands/xack/)、[XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/)。本协议在这些基础机制之上另行定义 SQLite 权威和业务幂等，Redis 不会自动替本项目完成它们。
