# Finance Root 状态版本不变量修复

日期：2026-10-19  
状态：已实现并完成离线回归  
适用范围：`FinanceRootExecutor` 的 `finance.root_state.v1`

## 1. 问题

`FinanceRootExecutor` 之前只在从 checkpoint 恢复时检查 `revision` 必须递增。
首次 Root 调用没有 checkpoint，因此可以用与初始状态相同的 revision 提交终态。
这与基线要求的“每个持久化金融状态版本单调递增”不一致，也会让第一次提交的
状态版本无法可靠区分一次新的领域推进。

## 2. 修复

- 所有 Root domain step，无论首次调用还是恢复调用，都必须提交
  `result.state.revision > context.state.revision`；
- 不改变 `FinanceRootState` 字段、文章分析方法或模型动作协议；
- 首次提交 revision 不递增时，按 `contract_error` 失败并保留调度审计。

## 3. 验证

新增 `test_finance_state_revision_must_advance_on_initial_terminal_step`，验证首次
终态提交同 revision 会失败；Finance Root 定向测试 `5 passed`，完整
`test_dispatch_*.py` 集合 `195 passed, 2 skipped, 1 warning`。

真实 Redis、真实模型和金融 Query 对照不属于本次离线验证，仍按代码级验收报告单独记录。
