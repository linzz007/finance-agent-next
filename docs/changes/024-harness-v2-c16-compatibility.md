# Harness 2.0 C16 兼容性回归记录

日期：2026-10-19  
状态：已验证；Runtime 审核状态仍受 019 待确认协议限制  
基线：`H2-CONTRACTS-2026-10-18-v1`、`H2-DISPATCH-2026-10-18-v1`

## 1. 目的

C16 用同一个离线金融 Query 对照旧版同步路径和 Harness 2.0 异步路径，检查调度迁移是否丢失：

- 作者和发布日期；
- 文章原文及其行范围；
- EvidenceRef；
- 保留作者条件性观点的答案语义；
- 最终交付状态。

该测试不评价模型的金融判断质量，也不把 fixture 中模型返回的 `answer_audit_coverage=complete` 当作 Runtime 已完成审核。

## 2. 结果

测试文件：[tests/test_harness_v2_compatibility.py](../../tests/test_harness_v2_compatibility.py)

结果：`1 passed`。

旧版和 H2 都保留了同一篇 TT 文章的作者、日期、原文内容和证据引用；H2 的 Task 进入 `succeeded`，候选答案保留 `verification_status=partial`。因此当前可以确认：

1. H2 的 `article.list` -> `article.read` -> final 路径没有把文章来源语义丢掉；
2. H2 不会因为读取目录列表就把文章错误标记为 `read_status=complete`；
3. 作者观点仍然可以与外部事实区分，条件性表述没有在调度迁移中被提升为确定事实。

## 3. 尚未通过的边界

H2 的 `FinanceRootState.review` 在本测试中仍为 `None`，所以本次状态为 `verified-partial`，不是完整审核等价。原因不是 C16 发现了文章或答案语义丢失，而是当前 019 状态更新协议尚未获得确认并实现：

- 模型如何提交 `review`、`coverage` 和 `delivery` 的增量状态尚未冻结；
- Runtime 目前不能从模型最终 JSON 中推导“审核已完成”；
- 模型自报的审核字段不能替代 Runtime 审核事件。

在 019 确认前，不能把 H2 的 `review=None` 自动改写成 `complete`，也不能为了让验收通过而放宽这一边界。

## 4. 代码修复

`FinanceRootExecutor` 现在只在真实 `article.read` 结果存在时创建 `read_status=complete` 的文章读取状态。`article.list` 只增加发现层材料，不代表文章已经读取。

## 5. 下一步

待用户确认 019 的状态更新协议后，再实现 H2-F08/F09：验证增量状态的版本、合并、幂等、非法引用拒绝和恢复语义。真实 Redis 联测仍需要配置 `H2_TEST_REDIS_URL`；真实金融 Query 和模型质量不由本次离线 C16 代替。
