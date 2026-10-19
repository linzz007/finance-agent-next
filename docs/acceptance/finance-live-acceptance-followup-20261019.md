# 金融实时验收跟进报告（2026-10-19）

状态：已完成定向复验；业务验收脚本仍保留 `manual_review_pending`，因为本项目暂不使用自动业务评分替代人工判断。

基线批次：`data/acceptance/d30ds`  
模型：`deepseek-flash`（DeepSeek-V4.1-Flash）  
复验批次：`data/acceptance/d30ds-r2-q03-evidence`、`data/acceptance/d30ds-r2-q14-scope`

## 1. 本轮范围

本轮针对上一轮已定位的问题做两项定向验证：

1. Q03：确认行情序列写入 EvidenceLedger 后，审核输入是否包含具体 `close/high/low` 数值，而不是只剩日期和元数据。
2. Q14：确认多作者比较是否披露实际读取范围，并把“未直接讨论”限定为已读取样本，而不是对作者全部文章作全称断言。

同时运行 Harness 2.0 调度回归和金融相关单元回归，确认证据修复没有破坏调度契约。

## 2. 定向结果

| Query | 结果 | 模型失败 | 交付 | 审核/覆盖状态 | 运行时间 |
| --- | --- | ---: | --- | --- | ---: |
| Q03 | returned | 0 | `done/full` | `verification=verified`；`evidence=partial`；`answer_audit=complete` | 79.14s |
| Q14 | returned | 0 | `done/qualified` | `verification=partial`；`evidence=partial`；`answer_audit=complete` | 347.81s |

### Q03：行情证据已可被审核读取

复验 Trace 中的 `observed_evidence` 已包含完整的 `market_bar_series` 证据。例如：

- 黄金：`close=4185.052428`、`high=4185.680097`、`low=4160.345071`；
- 白银：`close=57.377517`、`high=57.449988`、`low=56.833828`；
- 纳斯达克：`close=26140.029296875`，并包含开高低值和交易日。

这些片段的 `excerpt_truncated=false`、`selection_scope=complete_observation`，说明审核器现在能直接核对数值，不再依赖一个只写着“请检查工具结果”的占位正文。

Q03 仍显示 `evidence_coverage=partial`，不能把它解读为“数值证据缺失”。本轮答案包含多个行情对象，而审核目标只覆盖其中指定的目标范围；这是覆盖范围状态，和“证据正文无法核对”是两类问题。

### Q14：范围和作者归因已正确披露

复验答案明确写出：

- TT 读取 2026-09-03 至 09-30 的 7 篇；
- 培风客读取 2026-09-12 至 10-01 的 4 篇；
- 其中读书随笔只被记录为样本内未直接分析黄金，不被扩展为作者长期结论；
- “培风客没有直接讨论”被限定为“四篇样本”，没有写成作者全部文章都没有讨论；
- 对四季度和 10 月 FOMC 的判断保留为未用后续数据独立核验的作者预测。

Q14 的 `answer_audit_coverage=complete`、`semantic_review=complete`，但 `verification_status=partial` 是因为本题没有读取后续 CPI/FOMC/行情来裁定预测是否兑现。系统仍交付完整分析，并将未核实原因写入限制，不把它伪装成事实裁决。

## 3. 回归结果

### Harness 2.0 调度回归

命令：

```text
uv run --locked python -m pytest tests/test_dispatch_foundation.py tests/test_dispatch_execution.py tests/test_dispatch_finance_root.py tests/test_dispatch_finance_service.py tests/test_dispatch_finance_agent.py tests/test_dispatch_http_api.py tests/test_dispatch_observability.py tests/test_dispatch_pressure.py tests/test_harness_v2_compatibility.py -q
```

结果：`196 passed, 2 skipped, 1 warning`。警告来自测试依赖的 FastAPI/Starlette 与 httpx 兼容性提示，不影响本轮断言结果。

跳过的是没有配置 `H2_TEST_REDIS_URL` 的真实 Redis 联测。FakeRedis、SQLite 临时库、Outbox、租约、恢复、预算、递归深度和 H2 C16 兼容性回归均通过；这不等同于真实 Redis 部署验收。

### 金融相关回归

结果：`171 passed`。

覆盖本轮修改涉及的答案审核、实时验收脚本、金融模块化工具和模型网关测试。`git diff --check` 通过。

## 4. 当前结论

本轮已确认的事实问题是：行情 `market.series` 结果此前在 EvidenceLedger 中被压缩成元数据占位文本，导致审核器无法看到实际数值；该问题已修复，并由 Q03 Trace 和回归测试验证。

本轮没有证据表明审核规则需要放宽。当前仍应保留以下边界：

1. `evidence_coverage=partial` 只表示当前答案或审核目标没有覆盖全部可用证据，不自动表示答案错误。
2. `verification_status=partial` 允许在完整答案中明确说明“尚未核实”，不阻塞交付，也不允许把未到期预测写成已验证事实。
3. Q07/Q21/Q28 等历史问题仍按原归因处理：未授权文章不得交给子 Agent，工具参数契约失败不得通过放宽权限掩盖；它们不由本轮 Q03 证据修复自动解决。
4. 本轮结果是定向复验，不外推为 30 条 Query 全部通过，也不外推为真实 Redis、多用户并发或商业化成功率已经验收。

## 5. 产物索引

- Q03 记录：[d30ds-r2-q03-evidence](../../data/acceptance/d30ds-r2-q03-evidence/summary.json)
- Q14 记录：[d30ds-r2-q14-scope](../../data/acceptance/d30ds-r2-q14-scope/summary.json)
- Harness 2.0 代码验收：[harness-v2-code-acceptance-followup-20261019](harness-v2-code-acceptance-followup-20261019.md)
