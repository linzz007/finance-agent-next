# 018 Harness 2.0 Optional HTTP Boundary

状态：已实现，默认不启用

## 1. 目标

为已经接通的 `FinanceHarnessV2Service` 提供可选的 FastAPI HTTP 映射，同时保持
`finance_research_app.create_app()` 的默认 1.0 路径不变。HTTP 层不创建数据库、Redis
或 Worker，也不复制状态机；所有状态变化继续进入同一个 SQLite 权威 Repository。

## 2. 启用方式

```python
app = create_app(harness_v2_service=service)
```

默认的 `app = create_app()` 不注册 `/api/harness/v2` 路由。应用装配方必须显式提供已经
完成迁移和配置的 `FinanceHarnessV2Service`。

## 3. 会话与运行身份

- `POST /api/harness/v2/runs` 要求请求体显式提供 `run_id`，不能由 `session_ref` 推导。
- 除创建接口外，所有接口都要求 `X-Harness-Session` 请求头。
- 服务通过 `(run_id, session_ref)` 查询并绑定 Root Thread/Task；错误会话统一隐藏为 404。
- `FinanceHarnessV2Service.handle_for()` 从 `h2_tasks.current_attempt_id` 读取当前 Attempt，
  不从 `h2_runs` 猜测或读取不存在的字段。

## 4. 路由

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/api/harness/v2/runs` | 持久化创建 Run，返回 202 |
| POST | `/runs/{run_id}/agents` | `agent.spawn` |
| POST | `/runs/{run_id}/wait` | 只读等待快照 |
| POST | `/runs/{run_id}/status` | `agent.status` |
| POST | `/runs/{run_id}/threads/{thread_id}/messages` | `agent.send` |
| POST | `/runs/{run_id}/tasks/{task_id}/cancel` | `agent.cancel` |
| POST | `/runs/{run_id}/tasks/{task_id}/resume` | `agent.resume` |
| GET | `/runs/{run_id}/results/{result_ref}` | `result.get` |
| PUT | `/runs/{run_id}/model-policy` | 用户显式更新模型策略 |

路径中的 `thread_id`/`task_id` 必须与请求体一致；不一致返回 409。模型策略使用
`Repository.set_model_policy`，通过 `expected_policy_ref` 做 CAS，并沿用原有幂等键。

## 5. HTTP 错误映射

| 类别 | HTTP |
| --- | --- |
| 参数/契约错误 | 422 |
| 幂等、状态或路径冲突 | 409 |
| 权限/不存在 | 404 |
| Provider、额度或超时 | 503 |

## 6. 验收

`tests/test_dispatch_http_api.py` 使用临时 SQLite、`fakeredis` 和 fixture domain step 验证：

- 显式 `run_id` 创建、当前 Attempt 查询和会话隔离；
- 终态结果读取；
- send/cancel/resume 的路径参数一致性；
- 模型策略更新和幂等重放；
- 默认应用不因未注入 service 而启用 H2 路由（由既有 1.0 应用测试覆盖）。

本变更只接通 HTTP 边界，不代表真实 Redis、付费模型、生产 Worker 或默认 1.0 已切换。
