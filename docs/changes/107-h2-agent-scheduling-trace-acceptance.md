# 107 Agent 调度验收集与层级 Trace

日期：2026-10-27  
状态：已实现并通过离线调度验收。

## 范围

新增独立的 `tests/fixtures/h2_agent_scheduling_acceptance.json` 和调度验收测试，覆盖：

- Root 委派通用 Agent，子 Agent 再委派孙 Agent，等待、恢复并逐层交付。
- Root 委派 Analyst，Analyst 按既有审核策略创建 Verifier 任务、处理意见并完成版本绑定的复核。
- Root 委派多个兄弟任务，等待全部完成后合并结果。
- 子 Agent 以无效 Analyst 合同失败后，Runtime 将终态和诊断交给父 Agent；父 Agent据此停止等待并交付带缺口的部分结果。

场景由确定性离线模型替身驱动，但实际执行 H2 Worker、SQLite、FakeRedis、调度工具、检查点和结果交接。不调用付费模型；不修改 30 条业务 Query、预算、角色权限、调度决策权或金融分析规则。

## Trace 关联

- `trace_id` 使用 Run ID。
- Agent Task 是稳定的 `agent` span，`span_id=task_id`，父级为 `parent_task_id`。同一个 Task 的恢复不会丢失身份。
- 每个 Attempt 是 Task 的子 span，`span_id=attempt_id`；重试/恢复会形成同一 Agent Task 下的多个 Attempt。
- 每个物理模型请求是 Attempt 的子 span，`span_id=call_id`。成功、失败、未知和未发送结算都沿用模型调用账本状态。
- 调度事件保留现有事件 ID、游标和 payload，并标记其所属 Task span、角色、深度及 Agent 路径。事件仍按现有 API 游标分页。
- Workbench Run API 提供层级 span 投影；事件 Tab 显示事件所属 Agent 路径和模型请求归属。导出增加 `scheduling-trace.json`，并在既有 JSONL Trace 记录中追加 span 关联字段。
- Trace 只暴露可观测性元数据，不暴露 Prompt、文章正文、密钥或完整模型输出。所有任务身份先经现有 Run/session 与 H2 访问边界校验。

不增加 SQLite 表、不改变 Thread/Task/Attempt 生命周期，不把 Workbench 投影变成调度权威。实现共享现有事件、Attempt、模型调用、角色和父任务关系。

## 验收

测试要求能够从输出 Trace 还原父子 Agent 树、Attempt 归属、每次模型请求的父 Attempt、兄弟任务同属一个父 Agent、Analyst-Verifier 复核任务的角色关系和事件顺序；导出与 Workbench 投影的 ID 关联一致。还要验证 Run 间隔离及 Trace 不含 Prompt/文章正文。

离线替身证明 Runtime 的确定性调度和 Trace 投影，不证明真实模型会自主选择最佳委派方案、分析质量或真实 Redis 可用性。

## 复验发现

完整 Dispatch 回归最初暴露 7 个两层来源交接用例失败。根因是测试替身仍给 Analyst 返回 `finance.research_result.v1`，而 Profile-aware Analyst 2.0 的执行合同要求 `finance.analyst_result.v2` 及核验决定字段；其中一个公共来源替身还把 Analyst 合同错误用于孙级 Verifier。Runtime 按 Profile 拒绝不匹配输出是正确行为。更新替身后，这些交接链路均通过。

另外，requested-budget 的调度说明没有明确区分子任务“终态”和“成功”。现已补充要求：读取失败状态与诊断，不对终态任务重复 wait；最多做一次有理由的恢复或替代派发，之后交付部分结果，并将未解决问题写入 `gaps`/`warnings`，让 Runtime 持久化为 partial。新增验收验证失败诊断实际进入父 Agent 上下文、父 Agent 只等待一次、最终结果内容保留失败信息且 Task 状态为 partial。该次数边界仍是 Prompt 行为要求，不是 Runtime 对 resume/替代派发次数的硬计数器；总调用、期限和递归深度仍由 Runtime 强制限制。

## 复验发现与修复

首次专项用例发现：父 Agent 已读取子任务的 `failed` 状态和 `contract_error`，只等待一次，并把未解决原因写入最终答案、`gaps` 与 `warnings`；但 Root 仍被 Runtime 标为 `succeeded`。原因是 Root 最终状态原先只参考 Runtime 生成的警告，没有把模型提交中明确列出的缺口和警告纳入状态映射。

现已将 Root 提交状态统一为：Runtime 警告、非空 `gaps` 或非空模型 `warnings` 任一存在即为 `partial`，三者都为空才为 `succeeded`。专项验收同时检查已持久化 Result 和 Trace 的 Root Agent span 都标为 `partial`。

## 验证结果

- 全部 Dispatch Python 测试及 Workbench API 测试：1274 passed、7 skipped、0 failed；包含调度、Profile、预算、恢复、故障注入、来源继承、审核闭环与 Trace 投影。
- 专门调度集覆盖三种成功编排，并新增一条失败子任务交接验收；父 Agent 能读取具体 failed 状态和 `contract_error` 诊断，只等待一次后交付。
- Trace 面板 JavaScript 单测 1/1 通过；Ruff、Workbench JavaScript `node --check`、配置 JSON 解析及 `git diff --check` 均通过。仅有 Starlette/httpx 弃用警告和工作树换行符提示。
- 样例记录位于 `../runtime-records/h2-agent-scheduling-acceptance-20261027/`：通用三层委派 25 spans/167 events，Analyst-Verifier 两轮 30 spans/193 events，双兄弟 wait-all 15 spans/84 events。Span 数含 Agent、Attempt 与物理模型请求三类。
- 样例 JSON 是测试数据，不含 Prompt 或文章正文；该运行没有测真实模型的自主派发质量、真实 Redis 或生产环境稳定性。
