# Harness 2.0 明确基线缺口修复

日期：2026-10-19  
状态：已实现，待代码级回归与金融 Query 复验  
基线：`H2-DISPATCH-2026-10-18-v1` / `H2-CONTRACTS-2026-10-18-v1`

本次只修复冻结基线已经明确、且实现行为可直接对照的事实问题；金融状态增量协议
已在用户确认后由 026 单独冻结，本变更不实现其业务语义。

## 修复内容

1. `agent.spawn` 的持久任务已经进入 `backpressured` 时，接收回执现在返回真实
   的 `status` 与 `dispatch_state`，不再伪装成 `pending_dispatch`。
2. 当 Run 仍开放时，终态 Thread 的 `agent.send` 可以创建同 Thread 的 follow-up
   Task。它从可用的父预算账户重新分配预算，不复活旧 Task，也不覆盖旧结果。
3. 运行中取消现在先持久化 `cancel_requested` 并返回 `settled=false`。Worker 在
   安全边界调用窄权限的 cancellation settlement；重协调器可以处理失联 Worker。
   queued、blocked、waiting 等未开始或已释放执行权的任务仍可立即结算。

## 非目标

- 不改变模型选择、自动 fallback、文章分析、证据覆盖或金融审核语义。
- 不在本变更中实现 `026-finance-state-update-protocol.md`。
- 不把测试通过描述为真实 Redis、付费模型或金融答案质量已经验收。

## 验收

- 新增回归覆盖背压回执、根 Thread follow-up 和运行中取消的未结算语义。
- 既有 Harness 2.0 回归继续覆盖迟到结果保留、取消后的新增调用拦截和旧 Attempt fencing。
