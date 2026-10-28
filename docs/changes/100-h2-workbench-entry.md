# H2 2.0 网页主入口接入设计

日期：2026-10-21  
状态：已实现，完成定向网页验证与 155 项离线回归  
设计版本：`H2-WORKBENCH-ENTRY-2026-10-21-v1`

## 1. 目标

接口与 UI 投影表结构见 [专门接口契约](../harness-v2/workbench-contracts.md)。

将当前网页 `/workbench` 接到已经实现并验证过的 Harness 2.0 金融路径。用户从网页发送问题后，
必须经过 H2 的 `Run -> Thread -> Task -> Attempt`、SQLite 权威状态、Outbox、队列通知、Worker、
checkpoint 和结果交付链路。网页展示的 Trace 必须来自 H2 数据库，而不是旧版 `AgentRuntime` 的 Trace。

旧版同步 `AgentLoop` 保留为兼容代码和对照实现，但不再作为新项目网页的默认消息执行入口。

## 2. 网页边界

网页适配层只负责会话展示、提交问题、轮询状态和展示结果。它不执行模型动作、不直接调用金融工具，
也不绕过 H2 Repository 修改调度状态。

```text
网页 H2 Workbench
  -> H2 Web Adapter
  -> FinanceHarnessV2Service
  -> SQLite authority + Outbox
  -> Redis Streams notification (local fallback: FakeRedis)
  -> Worker / FinanceH2Domain / FinanceH2ChildExecutor
  -> H2 result + events + attempts
  -> H2 Web Adapter
  -> 页面消息、任务树、Trace、预算
```

本地没有 `H2_REDIS_URL` 时，默认使用进程内 FakeRedis 作为通知适配器，让个人验收可以启动；
SQLite 仍保存任务、Attempt、结果和审计。真实 Redis 通过 `H2_REDIS_URL` 显式启用，并在状态页标明后端。
FakeRedis 不被表述为真实 Redis 或商业化并发验证。

每次提交是一个独立研究 Run；本轮会话只组织历史记录，不声称已实现把之前 Run 的分析自动传给
新 Run 的多轮上下文。进程重启后的队列恢复使用既有 Outbox 重发/租约定时边界，不重置预算。

## 3. 会话与运行

- `web_session_id` 是网页会话标识，只用于消息历史和 UI 归属。
- 每次发送消息创建一个新的 H2 `run_id`，并把 `session_ref=web_session_id` 固定写入 Run。
- 每个 Run 固定一个用户选择的模型 profile；不自动切换模型、不自动增加额度。
- Web Adapter 只保存用户消息、最终结果和 Run 映射；研究状态、Attempt、证据和事件仍以 H2 SQLite 为准。
- 用户刷新页面时，根据 `web_session_id` 重新读取消息和 H2 状态，不从模型重新生成答案。

## 4. 执行与交付

提交请求返回 `202 Accepted`，后台驱动器反复执行有限的一次调度循环：

1. 发布 SQLite Outbox 到 Redis/FakeRedis；
2. Worker claim 一个合法 Attempt；
3. 执行一个金融 Domain Step 或 Child Step；
4. 持久化 Continue、Suspend 或 Submission；
5. ACK 已经由 SQLite 结算的通知；
6. 终态后从 `result.get(full)` 读取完整结果并写入网页消息投影。

网页轮询只读 H2 状态和事件，不参与调度决策。完整答案即使是 `partial` 或带 warnings 也保留交付，
并把失败原因、证据缺口和审核状态展示出来。

## 5. 2.0 可见信息

页面必须至少展示：

- 当前 Run、选定模型和传输后端；
- Root/Child 的 Thread、Task、Attempt 状态；
- 当前阶段、事件时间线、Attempt 错误与恢复次数；
- Root/Child 的预算已用量与剩余量；
- 最终答案、warnings、gaps 和结果状态。

这些展示是 Trace 的只读投影，不改变金融 Agent 自主选择文章、深读、委派、验证和综合的职责。

## 6. 接入验收

| 检查 | 必须证明 |
| --- | --- |
| E100-1 | `/workbench` 页面不再把消息发送到旧同步 `AgentRuntime` |
| E100-2 | 一条网页消息创建 H2 Run，并能在数据库中看到 Root Task/Attempt |
| E100-3 | 页面显示 H2 状态与事件，终态只从 H2 result 获取答案 |
| E100-4 | 刷新页面不会重复调用模型，只恢复已有网页投影和 H2 状态 |
| E100-5 | 模型、预算、失败和 FakeRedis/Redis 后端标识均可观察 |
| E100-6 | 旧版代码不影响新入口；H2 离线回归继续通过 |

## 7. 非目标

本轮不声称已完成真实 Redis、多人并发、跨进程 Worker 高可用或商业化验收；不删除旧版源代码，直到
H2 网页入口完成一次人工验收并确认可以移除兼容路径。

## 8. 实现与验证记录

默认启动应用已接入 H2 生命周期，独立页面和 API 不再调用旧版同步消息入口。
真实网页定向验证中 Root 和 analyst 均成功，合计 12 次 DeepSeek 请求，保留 11 个 Attempt、
182 个事件和完整结果；刷新后没有新增模型调用。该单次结果只证明接入路径，不能推算成功率。
离线最终回归 155 passed；具体验证、失败 fixture 修正和限制见
[本次接入验收记录](../acceptance/harness-v2-workbench-entry-20261021.md)。
