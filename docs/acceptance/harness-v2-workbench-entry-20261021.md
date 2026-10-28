# H2 网页主入口接入验收

日期：2026-10-21。范围：[100 设计](../changes/100-h2-workbench-entry.md)及
[网页接口契约](../harness-v2/workbench-contracts.md)。这是接入验证，不替代金融内容或全套业务验收。

## 1. 实际接入

- 默认 `finance_research_app:app` 开启 H2；`run_finance.py` 启动该应用。
- `/workbench` 使用独立 H2 页面，消息进入 `/api/h2-workbench`。
- H2Workbench 调用真实 FinanceHarnessV2Service、SQLite Repository、Outbox 和 Worker；
  不调用旧 AgentRuntime/AgentLoop。旧网页消息执行路由在 H2 应用中不挂载。
- 后台发布、消费、恢复、结果投影不依赖浏览器请求存活。模型按 Run 固定，不自动换模型。
- 默认 Worker 并发数 4，沿用 H2 租约和并发限制。Root/child/Run 调用上限仍为 120/60/600，
  整轮最多 60 分钟，没有为验证扩额。
- UI 投影表仅保存会话和结果映射，调度状态以原 H2 表为权威。答案分页读取直到结束，
  保留 partial、warnings 和 gaps。浏览器重试同 request_id 不新建付费 Run。

本机展示地址为 `http://127.0.0.1:8511/workbench`。8510 已有监听，因此未覆盖该端口。
当前通知后端明确显示 `fakeredis-local`；SQLite 位于新项目自己的
`data/finance/h2-runtime.sqlite`，未迁移旧项目数据库。

## 2. 离线结果

最终命令：

```powershell
uv run --offline python -m pytest tests/test_h2_workbench.py tests/test_dispatch_http_api.py tests/test_dispatch_finance_service.py tests/test_dispatch_finance_continuation.py tests/test_dispatch_pressure.py tests/test_finance_next.py tests/test_model_profiles.py -q
```

结果：**155 passed，137.64 秒**。模型 HTTP 使用 MockTransport，没有付费请求。
另有一次 Starlette/httpx TestClient 弃用告警；uv 的本机 SSL_CERT_DIR 警告不影响离线结果。

新增网页测试 5 项，覆盖真实 H2 服务与 Worker 的文章读取、超过 64000 字符答案的完整投影、
刷新不重新调用模型、幂等提交与冲突、已有排队任务启动后执行、会话归属检查、跨站写入拒绝、
取消，以及真实 child spawn/wait/wake 后的父任务交付和缺口披露。
排队恢复 fixture 重建的是网页适配器，不应称为全新进程/全新 Redis 的灾难恢复实测。

历史中间结果保留：第一版长答案断言没有考虑既有 final 的 strip；断言改为匹配既有行为。
child fixture 最初没有递增 State revision，被既有不变量正确拒绝；修正 fixture 后通过，
没有放宽 Runtime 校验。最终 Ruff 格式/检查、前端 node --check 和本次 tracked diff 检查通过。

## 3. 一次真实网页测试

使用页面选择 DeepSeek，要求本地选取培风客最近一篇金融文章、Root 读取后委派 analyst，
子任务分析观点/前提/漏洞，再由 Root 综合。不联网、不读取持仓或 Wiki。
没有为了追求通过追加付费重跑。

| 项目 | 实际值 |
| --- | --- |
| Run | `web-h2-4db95c319e0963be5b447c831af4d0fa1707c819` |
| Root Task | `tsk_8aa7a50016e64d449a2a8a64f6abc203` |
| Child Task | `tsk_ee469e308455471796f139c5d39e01fa` |
| 终态 | Root succeeded；analyst succeeded |
| 物理模型请求 | Root 10 + child 2 = 12；均 succeeded，无 provider/transport 错误 |
| Attempt | Root 10 + child 1 = 11；Attempt 数不等于模型请求数 |
| 交付正文 | 3367 字符 |
| 事件/检查点 | 182 个事件、18 个 checkpoint |
| 调度事件 | 1 个 spawned、7 个 continued、2 个 waiting、2 个 woken |
| 剩余预算 | Root 110、child 58、整轮 588 |

物理请求时间存在父子重叠，证明多个 Worker 实际消费；不是在 UI 伪造子任务树。
Agent 读取子结果和用户读取 Root 结果的事件归属分别保存。结果信封 metadata_status=complete
不等于金融语义审核已完整通过，不应把这次测试当作逐条原文核验报告。

浏览器验证了任务树、委派事件展开、Artifact 读取弹窗、刷新恢复；刷新后仍为 12 次请求。
桌面截图显示真实 Root/child 状态；390x844 移动视口没有横向溢出，已恢复正常桌面视口。
检查时浏览器没有 error/warn 控制台记录。截图与完整私有运行记录位于：

```text
../runtime-records/web-h2/workbench-desktop.jpg
../runtime-records/web-h2/workbench-mobile.jpg
../runtime-records/web-h2/runs/<run_id>/query.json
../runtime-records/web-h2/runs/<run_id>/events.jsonl
../runtime-records/web-h2/runs/<run_id>/model-calls.jsonl
../runtime-records/web-h2/runs/<run_id>/trace.jsonl
```

以上为相对项目根目录的位置；私有文章、答案、Trace 和凭证不纳入 Git。

## 4. 结论与限制

E100-1 至 E100-6 的定向接入检查通过：新版页面确实执行 H2，结果与 Trace 有数据库依据，
刷新不重复付费，旧同步消息入口不参与该路径，相关离线回归通过。

仍需准确区分：

- 单次成功不是稳定成功率、压力测试或商业化证明；本轮没有全业务 Query 批次复跑。
- 无真实 Redis 环境；FakeRedis 丢通知后的进程级恢复和真实 Redis 多进程高可用未在本轮实测。
- 目前面向本机个人使用，不是多用户权限、登录和公网部署方案。
- 网页 session 只组织历史，每条问题独立创建 Run，不自动继承前一 Run 的证据上下文。
- 既有未知远端结果记账继续适用，不承诺网络中断下远端绝对无重复收费。
- 旧源码保留作兼容与对照；没有大范围删除共享依赖，也未提交或推送 Git。
