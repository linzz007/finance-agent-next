# H2 调度测试 Trace 二次复核

日期：2026-10-28。复核代码基线：`cbb3c68a58f3652d3433420b91fc191a4ecef1dc`。

结论：基础调度已经实际用起来，包括通用子任务、两层递归、专业角色、真实并行、等待恢复、全文结果读取和持久化追踪。严格调度验收尚未全部通过，主要缺口是专业分析的最终版本复审，以及领域合同拒绝后的反馈与纠正。Root 成功交付不能替代这些要求。

本次只读检查已有真实 DeepSeek 记录，新增付费请求为零；未修改历史答案、状态、规则快照和验收结论。另运行当前代码的定向离线回归，61 项通过。

## 1. 范围与证据

题目来自 [调度测试目录](../../tests/fixtures/h2_agent_scheduling_acceptance.json)。选择以下原始运行，每题保留其批次身份，不将不同轮次拼成一次成功运行：

| 用例 | 原始批次 | Run | 历史物理请求 | Trace 事件 | 校验的 Artifact |
| --- | --- | --- | ---: | ---: | ---: |
| SCH-01 | h2-agent-scheduling-live-20261027-r1 | h2-9d93153982ca4353be51d1dc20314a9f | 23 | 323 | 177 |
| SCH-02 | h2-sched-r5-20261028 | h2-23ea35646ff0432c8739c8d5e6dde2a7 | 37 | 476 | 254 |
| SCH-03 | h2-sched-r4-20261028 | h2-87e2db7568bd415f93751f50667938a5 | 36 | 475 | 260 |
| SCH-04 | h2-sched-r4-20261028 | h2-4da34a1ac4824e8f93e0566cb5bb139a | 31 | 467 | 271 |

合计检查 127 次历史请求、1,741 个事件、962 份 Artifact。请求中 125 次响应成功，2 次远端结果未知；两类均已记账。响应成功只说明模型接口返回，后续动作、证据和研究质量另行判断。这不是本次调用量，也不是所有历史测试批次的总调用量。

只读复核程序与逐项事实保存在仓库外：

- `../runtime-records/h2-trace-reaudit-20261028/audit.py`
- `../runtime-records/h2-trace-reaudit-20261028/facts.json`
- 较早的人工评估：`../runtime-records/h2-agent-scheduling-assessment-20261028/report.md`

四个运行均满足：导出 Trace 与 SQLite 事件 ID 完全对应且无重复，模型调用 Span 与账本对应，父 Span 引用有效，Artifact 长度和 SHA-256 校验通过，复核前后数据库与 Trace 文件哈希不变。数据库以只读连接及 `query_only` 打开。

以上证明记录之间一致、保存内容完整。它不能倒推证明历史执行时所有源文件都已正确冻结：r1 当时的 `sources_unchanged=false` 仍保留；r4/r5 的原始运行总结记录 `source_differences={}`、`snapshot_unchanged=true`。

## 2. 逐题判断

事件编号是各 Run 内的顺序编号；全文读取只计 `reader_kind=agent` 的记录，网页用户打开结果不计入 Agent 读取验收。

### SCH-01：通用 Agent 两层委派

预期：Root 读取文章，授权给 general；general 再委派 general 独立寻找反例，比较后回传；Root 读完最终子结果再交付。

实际路径为 `main -> general -> general`，三个任务均成功。Root、子、孙自身分别使用 12、8、3 次请求。两层收到同一显式授权正文引用，额外父背景引用为空，没有依赖隐式复制父历史。

关键 Trace：事件 73 创建第一层，131 创建第二层；249 第一层读完第二层结果，274 第一层结果接收；305 Root 读完第一层结果，319 Root 最终交付。158 记录一次传输重发。

判断：递归、授权和完整结果交接通过；独立性和分析质量尚未充分达标。第一层给第二层的任务中写入了预设的矛盾方向，会影响所谓独立寻找反例。最终答案又将“方向不确定”和“政治变化可能影响利率”视作必须二选一的矛盾，而两者可以同时成立。问题在任务内容与推理质量，不能归因于子任务没收到材料。本题未要求隐去作者身份，不能另加匿名标准判失败。

### SCH-02：Analyst 草稿、Verifier 核验、改稿回传

预期：Root 明确要求 `review_mode=required`；Verifier 核验具体草稿和原文；Analyst 处理意见，交回满足版本核验要求的最终结果。

实际为 `main -> analyst -> verifier`。Root 自身 23 次、Analyst 10 次、Verifier 4 次请求。只有一轮核验，配置上限为五轮。

关键 Trace：61 创建 Analyst，148 创建 Verifier；351 Verifier 为 partial，359 Analyst 完整读取报告；419 Analyst 为 partial，421 结果保存并接收；458 Root 完整读取 Analyst 最终结果，472 Root 交付。246 记录一次传输重发。

核验报告 `res_063630f8bf8040ad94680e4a81ef3b1a` 正确绑定草稿 `art_56619f58a68d427eb4c73f111fe184c2`、revision 1，`validation_status=complete`。18 个目标有检查记录，3 条正式意见均提交了处置，没有漏项或未知编号。意见涉及概率表述过强、条件遗漏、历史类比合并不准。

随后 Analyst 修改了正文、候选答案、证据绑定、缺口、限制、材料处置、支持材料引用及核验目标，却选择 `review_decision=skip`，理由是修改轻微、不再核验。当前合同比较整个 `ResearchResult + review_targets`，Runtime 正确记录 `final_draft_differs_from_last_review`，最终核验状态为 `review_failed`，以 partial 保留研究结果。

判断：一轮版本绑定、意见回传、逐项处置和两级全文读取已成立；最终版本核验闭环未通过。该运行只用 37/600 次整轮额度，约 12 分钟，尚余四轮核验，Root/子任务额度也均未耗尽。原因是改稿后没有请求复审，加上 Runtime 当前没有自动为这一分支发起复审。

Verifier 的 partial 还与它把本次未要求的外部事实核查放进 `unchecked_scope` 有关：已完成的原文核验与未授权外部核查应分别表达，不能混作本次核验未完成。

等待恢复方面，Root 有 9 次、Analyst 有 5 次超时唤醒；其间同一 pending Verifier 和 `rounds=1` 得以保存，未被误判失败、重复创建核验。这验证了已有等待修复。频繁唤醒的调用成本仍可优化；不能仅凭唤醒数断言每一次模型请求都浪费。

最新核验报告只有 4,065 字节；超过 16KiB 的核验报告分页绑定目前有离线覆盖，本题没有覆盖这一真实边界。

### SCH-03：双文章、两个 Analyst 并行、均关闭核验

预期：两篇正文分别授权给两个同级 Analyst，每次派发均设置 `review_mode=off`，等两者完成并读取全文后综合。

实际创建了两个同级 Analyst。其模型请求存在三对重叠，分别为 45,218、3,295、7,170 毫秒，证明真实并行能力已经执行。

但第一次派发携带 off，第二次遗漏该选项，按 Profile 默认值变成 auto，随后产生题目未要求的 Verifier。工具参数按每次调用独立处理；Runtime 没有收到第二次的 off，不能假定继承上一次参数。

关键 Trace：84、106 创建两个 Analyst，210 创建额外 Verifier；237 Root 读完第一份 Analyst 结果；348 Verifier partial，356 第二个 Analyst 读完报告；400 第二个 Analyst 因 `contract_error` 失败；412 Root 创建 general 替代，454 读完替代结果，471 最终交付。

失败链进一步还原为：

1. Verifier 报告包含与原文不逐字一致的引文，出现 `review_quote_mismatch`，报告 `validation_status=incomplete`，未正式绑定。
2. Analyst 仍把该报告中的 8 个 finding_id 写进正式处置列表；工作流正式认可的 finding_id 集合为空。
3. 两个集合不一致，领域校验抛出 `contract_error`，重建可得到 8 个 `unknown_finding_ids`。
4. 对外失败信息只保留通用的 “Agent output did not satisfy its contract.”，该领域错误未形成同任务可纠正的详细观察；Root 后来用 general 补救。

本题 36 次请求均获得成功响应。错误发生在 JSON 和字段结构检查之后的跨字段合同校验，不能说是 JSON 缺括号、五次格式补正用尽或模型接口不可用。

判断：并行实现成立，有用答案最终交付；严格的“两 Analyst 均 off、均交付”场景未通过。替代 general 的答案不能倒填成第二个 Analyst 成功。

### SCH-04：自然语言要求独立核对

题目只描述业务需求，没有指定角色、工具或协议字段。Root 自行先分析，再委派 Verifier，完整读取意见后修订。Root 自身 29 次，Verifier 2 次请求，两任务均成功。

关键 Trace：351 创建 Verifier，418 子结果接收，449 Root 完整读取报告 `res_fd25715cc4cf40358e92ffcb4c51f499`，463 Root 最终结果接收。最终答案包含三条主要观点、前提、不确定性和核对边界。

判断：这次自然语言自主委派与反馈修订通过。这里的 Verifier 返回普通研究结果，没有走专业 `ReviewRequest/ReviewReport` 草稿版本闭环，因此不能用本题证明 SCH-02 已通过。单题成功也不足以推导整体成功率。

## 3. 已有实现与待决定事项

| 项目 | 当前证据或问题 | 下一步 |
| --- | --- | --- |
| 通用/专家统一底座、两层委派 | 真实任务树、授权和结果读取已覆盖 | 保留现有机制 |
| 并行调度 | SCH-03 请求实际重叠 | 已证明能力；业务约束另验 |
| 等待后恢复同一核验 | SCH-02 保留 pending 身份与轮数 | 已真实覆盖 |
| 每次 spawn 遵循本次约束 | SCH-03 第二次遗漏 off | 已补 Prompt 说明，尚待新真实运行验证 |
| required 最终版本核验 | 改稿后 skip，返回 partial | 待确认恢复策略 |
| 不合法报告的意见处置 | 未绑定报告却提交正式意见编号，子任务终止 | 待确认领域错误反馈与纠正边界 |
| 独立反例与成本 | SCH-01 预设反驳方向；SCH-02 多次超时唤醒 | 后续定向优化，不扩张为全面重构 |

优先建议两项，均是待确认的设计，不在本次只读复核中改变执行行为：

1. 当 required 下最终提交版本与已核验版本不同，在现有五轮、时间与调用上限内自动请求下一轮；触顶仍交付带明确缺口的 partial。保留完整版本绑定，避免靠缩小比较字段掩盖正文或证据变更。
2. 将可纠正的领域合同失败转成有界结构化观察，携带校验阶段及 missing/unknown finding_id，保留原候选，允许同任务在现有上限内纠正；引用不合格报告仍不算正式通过。需要先确定次数、终止条件及与动作格式补正的关系，避免无限重试。

当前根因位置：[finance_research.py](../../src/policy_impact/dispatch/finance_research.py) 的意见集合检查及 required/request/skip 分支；[diagnostics.py](../../src/policy_impact/dispatch/diagnostics.py) 的公开失败诊断白名单；[finance_tool_contracts.py](../../src/policy_impact/dispatch/finance_tool_contracts.py) 的逐次派发参数规范化。

测试结束后已有三份 Prompt 澄清：每次派发独立提供选项、正式处置只使用工作流认可的 finding_id、未请求外部核查归入限制。同时明确现行 required 比较范围。当前三份 Prompt 与 r4/r5 冻结快照有差异，两个执行模块与快照一致。因此历史失败不能证明这些新说明无效，离线通过也不能证明新说明已被真实模型遵循。

## 4. 本次验证与查看方式

本次运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dispatch_finance_review_loop.py tests/test_dispatch_finance_rules.py tests/test_harness_v2_scheduling_live.py -q
```

结果：61 passed，11.70 秒。这是当前代码的离线规则、恢复与 runner 回归，不是新增真实模型验收。

工作台左侧“测试记录”可打开原始问题、任务树、结果和 Trace：

- [SCH-02 专业核验 r5](http://127.0.0.1:8511/workbench?archive=f6fd245bf310e10cde2ad8fd)
- [SCH-03 双文章并行 r4](http://127.0.0.1:8511/workbench?archive=a555b819349c95ed354cf659)
- [SCH-04 自然语言独立核对 r4](http://127.0.0.1:8511/workbench?archive=f0498b0baf97e9b6f56ae582)

下一次真实复验应在解决并冻结上述恢复策略后，优先各执行一次 SCH-02、SCH-03，检查最终版本绑定、约束传递和错误纠正全过程；已有结论充分的路径无需盲目重跑。当前测试使用 FakeRedis，尚未据此证明真实 Redis 或多用户部署能力。
