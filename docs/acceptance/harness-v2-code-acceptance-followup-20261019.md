# Harness 2.0 代码级验收跟进

日期：2026-10-19  
基线：`H2-CONTRACTS-2026-10-18-v1`、`H2-DISPATCH-2026-10-18-v1`  
前置报告：[harness-v2-code-acceptance-20261018](harness-v2-code-acceptance-20261018.md)

## 1. 本轮变更

修复 `FinanceRootExecutor` 首次 domain step 可以复用原 `revision` 的问题。现在首次
调用和 checkpoint 恢复都必须发布更高版本的 `finance.root_state.v1`。详细记录见
[023](../changes/023-finance-state-revision-invariant.md)。

本轮没有实现 `019-finance-state-update-protocol-proposal.md`，因为它仍是待确认设计；
没有把 `research_plan`、文章三层状态、coverage、review 和 delivery 的模型增量协议
擅自加入冻结契约。

本轮新增 C16 离线兼容回归，详见 [024](../changes/024-harness-v2-c16-compatibility.md)。
它确认旧版同步路径与 H2 异步路径保留同一篇文章的作者、日期、原文、EvidenceRef
和答案语义，但 H2 的 `FinanceRootState.review` 仍为 `None`，因此结果是
`verified-partial`，不是完整审核等价。

## 2. 验收结果

### H2 定向集合

命令：

```text
$files = Get-ChildItem tests -Filter 'test_dispatch_*.py' | ForEach-Object { $_.FullName }
uv run pytest -q @files
```

结果：`195 passed, 2 skipped, 1 warning`。

跳过项是未配置 `H2_TEST_REDIS_URL` 的两项真实 Redis 联测。FakeRedis、临时 SQLite、
Worker、模型执行、HTTP、Root、压力和观测测试均通过。该结果不等于真实 Redis 部署、
真实多 Worker 长压或金融回答质量已通过。

### 全仓回归

命令：`uv run pytest -q`。

结果：`2683 passed, 47 skipped, 19 failed, 1 warning`。

19 项失败没有出现在 H2 定向集合，当前分为三类：

1. 工作区缺失既有业务夹具：`data/companies/company_001/company.yaml` 和
   `data/eval/gold_policy_impact.jsonl` 不存在，导致旧企业报告、Wiki、评估运行器和
   产物测试失败；
2. 既有 Skill 注册表兼容断言不一致，默认 manifest 已包含 `skill.open`、
   `tool.describe`、`research.disposition`，但旧测试仍期待旧清单；
3. 既有 general-chat 兼容断言仍要求 Prompt 中出现 `projection_scope`。

这些问题不是本轮 H2 状态版本修复引入的，但它们意味着“全仓通过”尚未成立。不能用
H2 定向通过替代对这些项目级问题的处理。

## 3. 当前仍未证明

- H2-C04/C17/C18 的真实 Redis、磁盘损坏和长时间多 Worker 验证；
- H2-C16 同一金融 Query 的 1.0 同步与 H2 异步语义对照已完成；但其 Runtime 审核
  状态仍受 019 待确认协议限制；
- 模型驱动路径完整提交 `research_plan`、`article_states`、`evidence_ledger`、
  `coverage`、`review` 和 `delivery` 的结构化更新；
- 真实模型批次的稳定性、金融内容质量和商业化并发能力。

## 4. 下一步边界

优先级不变：先确认 019 的状态更新协议，再实现并补 H2-F08/F09；同时需要有效的真实
Redis URL 才能执行联测；最后再运行同 Query 的 1.0/H2 对照。模型失败仍按既定策略原样
报告，不自动切换 GPT 与 DeepSeek。
