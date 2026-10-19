# Harness 2.0 真实模型定向验收报告

日期：2026-10-19  
批次：`data/acceptance/h2-baseline-r1-20261019`  
模型：DeepSeek `deepseek-flash`（对应 DeepSeek-V4.1-Flash）  
模型策略：本批次固定使用 DeepSeek；没有自动切换 GPT，也没有把模型失败改写成其他模型成功。

## 1. 验收范围

本轮只验证三条代表性金融 Query，不等价于 30 条 Query 全量业务验收：

| Query | 场景 |
| --- | --- |
| Q04 | 单篇文章深读：核心判断、论据、隐含前提、待验证部分 |
| Q08 | 单作者画像：研究主题和分析方式，要求依据具体文章 |
| Q14 | 多作者交叉比较：共识、分歧，以及未直接讨论的主题 |

每个 case 的原始记录包含 `record.json`、`trace.json` 和工作区快照。原始运行产物只保留在本地验收目录，不进入 Git。

## 2. 结果摘要

| Query | 用时 | 答案字符数 | 交付 | 验证 | 证据覆盖 | 审核覆盖 | 工具调用/失败 |
| --- | ---: | ---: | --- | --- | --- | --- | ---: |
| Q04 | 479.90s | 8,367 | `done/qualified` | `disputed` | `partial` | `partial` | 27 / 5 |
| Q08 | 455.08s | 10,283 | `done/qualified` | `disputed` | `partial` | `complete` | 41 / 0 |
| Q14 | 454.67s | 8,283 | `done/qualified` | `disputed` | `partial` | `complete` | 27 / 2 |

本批次的运行稳定性结论：

- 3/3 返回非空答案并完成 Runtime 交付。
- 3/3 没有模型调用失败，也没有自动 fallback。
- 3/3 都保留了验证争议或证据范围限制，没有把未核实内容强行发布为确定事实。
- 3/3 的研究流程 Trace 都先记录 `research.plan`，再进行 `article.list`/`article.read` 等检索工具调用。
- 这只能证明“本批次能完成带限制的交付”，不能证明金融内容质量、工具契约遵守率或商业并发能力已经达标。

## 3. 失败归因

### 3.1 已确认的模型行为问题

Q04 出现 3 次工具参数契约错误：

- `article.read` 生成旧字段 `line_start`、`line_end`，当前契约要求单层 `start_line`/`limit`。
- `market.metrics` 生成未定义的 `metrics` 字段。
- `research.checkpoint` 使用旧版检查项字段，缺少 `claim`、`decision`、`outcome`、`reason`，并携带已废弃的 `item`、`note`。

Q14 出现 2 次工具参数契约错误：

- `market.snapshot` 使用单数 `symbol`，当前契约要求 `symbols`。
- `research.checkpoint` 把 `not_needed` 与不允许的结果状态组合，Runtime 正确拒绝了该组合。

这些不是 Runtime 放行错误。Runtime 按当前 schema 拒绝非法输入并把错误反馈给模型，模型随后继续完成了研究和交付。归因是：当前 DeepSeek 在长链路中仍会混用旧工具记忆、旧字段名或错误的语义组合。后续可以优化契约提示、错误反馈和模型适配统计，但不能通过放宽 schema 来掩盖问题。

### 3.2 外部环境问题

Q04 的两次 `web.open` 失败分别是 HTTP 403 和 HTTP 404。它们属于外部来源不可访问或链接失效，不是文章读取、SQLite、Redis 或调度 Runtime 的失败。答案保留了外部核查缺口，未将失败的网页内容当作已核实事实。

### 3.3 当前设计允许但需要披露的质量限制

- `evidence_coverage=partial` 表示当前答案或审核目标没有覆盖所有可用证据，不等于所有答案都错误。
- `verification_status=disputed` 表示存在争议、反例或未完成外部核查；按当前设计应并列呈现作者观点、外部证据和限制，而不是阻止输出。
- Q04 的 `answer_audit_coverage` 和 `semantic_review` 仍为 `partial`，说明单篇深读的原子主张审核没有达到完整覆盖。
- Q08/Q14 的答案审核与语义审核已完成，但证据覆盖仍为 `partial`，因此“审核完成”不能被解释为“所有内容都已外部证实”。

## 4. 代码回归结果

本轮执行：

```text
uv run --locked python -m pytest \
  tests/test_dispatch_foundation.py \
  tests/test_dispatch_execution.py \
  tests/test_dispatch_finance_root.py \
  tests/test_dispatch_finance_service.py \
  tests/test_dispatch_finance_agent.py \
  tests/test_dispatch_http_api.py \
  tests/test_dispatch_observability.py \
  tests/test_dispatch_pressure.py \
  tests/test_harness_v2_compatibility.py \
  tests/test_finance_state_update.py -q
```

结果：`206 passed, 2 skipped, 1 warning`。

- 两个 skipped 需要配置真实 `H2_TEST_REDIS_URL`，不能被解释成代码失败，也不能被解释成真实 Redis 已验收。
- 一个 warning 来自 FastAPI/Starlette 与 httpx 的上游兼容性提示。
- `uv run --locked python -m compileall -q src scripts tests` 通过。
- 全仓库 Ruff 当前不是绿色：报告 533 项；本轮相关文件定向检查报告 60 项，主要是既有格式、静态规则和部分审计代码提示。本轮没有进行无关格式化修复。

## 5. 当前验收结论

当前 Harness 2.0 已达到：**可在固定模型下完成计划、文章读取、验证、审核和带限制的最终交付**。

当前尚未达到：

1. DeepSeek 工具参数契约稳定遵守；
2. 每条金融答案的证据覆盖完整；
3. 30 条 Query 全量人工业务验收通过；
4. 真实 Redis、多 Worker、长时间运行和商业并发验收通过。

因此本批次结论是：**运行链路通过，内容与契约质量为有披露限制的部分通过，不应标记为 1.0 全部验收完成。**

## 6. 原始证据索引

- [批次摘要](../../data/acceptance/h2-baseline-r1-20261019/summary.json)
- [Q04 记录](../../data/acceptance/h2-baseline-r1-20261019/cases/Q04-r1/record.json)
- [Q04 Trace](../../data/acceptance/h2-baseline-r1-20261019/cases/Q04-r1/trace.json)
- [Q08 记录](../../data/acceptance/h2-baseline-r1-20261019/cases/Q08-r1/record.json)
- [Q08 Trace](../../data/acceptance/h2-baseline-r1-20261019/cases/Q08-r1/trace.json)
- [Q14 记录](../../data/acceptance/h2-baseline-r1-20261019/cases/Q14-r1/record.json)
- [Q14 Trace](../../data/acceptance/h2-baseline-r1-20261019/cases/Q14-r1/trace.json)
- [state_update.v1 设计基线](../changes/026-finance-state-update-protocol.md)
- [SQLite 契约](../harness-v2/sqlite-schema.md)
- [Redis Streams 契约](../harness-v2/redis-protocol.md)
- [调度接口契约](../harness-v2/interface-contracts.md)
