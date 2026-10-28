# 103 H2 Agent Profile 与 Analyst-Verifier 核验闭环

日期：2026-10-27  
状态：设计契约冻结；代码实现及离线验证完成。真实模型质量验证不在本变更范围内，未调用付费模型。

本文补充 013 调度基线、101 规则资产外置，以及当前 H2 接口契约。Thread/Task/Attempt、SQLite 权威状态、Redis 通知、独立调用上限和模型选择边界不变。本文与旧规则快照兼容：旧 Run 继续使用其原有冻结规则；新行为仅适用于采用新规则快照创建的 Run。

后续更新：[104](104-h2-general-agent-and-portable-profiles.md) 按用户新要求加入通用角色和独立 Profile 文件，
开放所有角色在剩余深度内委派，简化核验入口，默认轮数改为 5。以下 103 的旧权限表和默认 3 轮记录保留为该版本历史；当前新 Run 按 104。

## 1. 目标和职责

- Root 负责理解用户问题、范围、每篇任务的目标与验收条件、来源授权，以及跨文章综合和最终答案审核目标。
- Agent Profile 是版本化静态角色定义：短用途说明、Prompt 引用、最大工具权限、可委派角色、输入/输出契约及上下文策略。Profile 提供能力上限，不要求每次加载全部技能或调用全部工具。
- 父 Agent 生成本次 Task Brief：目标、具体指令、成功标准、授权材料引用及核验策略。Runtime 绑定身份、深度、规则快照、权限、预算、截止时间和契约，不接受模型自行提供这些可信字段。
- Runtime 将 Profile 与 Task Brief 装配成子任务。模型不能借动态指令改变 System 规则、工具白名单、模型/预算政策、Schema 或父级权限。
- Analyst 按文章任务生成分析结果与可核验目标；可依据 `required | auto | off` 请求文章级独立核验。Root 仍负责全局答案级审核，不把两类审核合并。
- Verifier 检查指定版本的草稿和已授权证据，返回有版本绑定的 ReviewReport；不修改草稿、不发布最终答案、不派生更多 Agent。
- Analyst 收到报告后逐项采纳、说明拒绝理由或披露未解决项，再交付给 Root。

Profile 是稳定默认值，不是一段通用 Prompt。动态简报是本次具体任务，也不是另一份可覆盖 System 的 Prompt。新增任务通常先复用 Profile，并生成不同 Task Brief；需要新增职责或权限时才新增/调整 Profile，并通过新规则快照生效。

## 2. 版本化 Profile

新规则清单使用独立 schema version，并为每个角色版本明示：`description`、`instructions`、`allowed_tools`、`allowed_child_profiles`、`input_contracts`、`output_contracts` 和 `context_policy`。Root Profile 单独声明其可派角色。未知字段、工具、角色、Prompt 路径、契约名或版本均拒绝；路径必须保持在仓库声明目录中。旧 `finance.h2.agent_rules.v1` 快照继续按原校验逻辑加载，不从当前文件补字段。

新金融 Profile 的默认权限：

| Profile | 工具边界 | 可派角色 | 输出 |
|---|---|---|---|
| Root | 本次 Run 已授权的业务与调度工具 | analyst、verifier、skeptic | Root 自有终稿契约 |
| Analyst | 已授权的文章/材料工具及调度能力；实际权限为 Run 授权与 Profile 白名单交集 | verifier | `finance.analyst_result.v2` |
| Verifier | 只读授权材料与明确允许的核验来源，不含 Agent 调度工具 | 无 | 带草稿版本绑定的 `finance.review_report.v1`；兼容普通验证任务时用注册的研究结果契约 |
| Skeptic | 只读授权材料与明确允许的分析工具 | 无 | 注册的研究结果契约 |

运行时在 spawn 时检查父角色的 `allowed_child_profiles`，并将子角色的工具集合与父权限、Run 权限及服务已注册执行器取交集。Prompt 中的“不要调用”不能代替该校验。Verifier 即使输出要求继续委派，也会被 Runtime 拒绝。

## 3. Task Brief 与上下文装配

`Spawn` 增加可选、受长度限制的 `task_instructions`、`review_policy` 和 `review_request`。这些是任务数据，不是执行权限。对于根 Agent 生成的版本化 `ResearchTask`，Runtime/Finance adapter 将 `original_query`、`objective`、`focus`、`user_requirements`、`completion_expectation` 转为可读的任务段落和 success criteria；明确授权的 `material_refs` 仍由 Runtime 校验。恢复时从已持久化 Brief 与规则 Artifact 重建上下文，不在恢复时重新询问模型生成历史字段。

Agent 收到的有效输入由以下部分组成：

1. 当前 Run 冻结的共享安全/动作规则和所选 Profile Prompt。
2. 当前 Task 的 objective、task_instructions、success_criteria、输入/输出契约和核验策略。
3. 仅显式授权的材料引用及其可装配内容；超出窗口时沿用已有按需材料读取能力。
4. 当前 Task/Attempt 身份、预算、截止时间、研究时钟和权限快照摘要。
5. 该 Task 自己保存的历史状态、工具观察和恢复检查点。

不自动复制父 Agent 的完整对话、私有推理或兄弟任务历史。需要的背景必须写进任务简报或由父 Agent 显式授权材料引用。Verifier 收到指定草稿 Artifact、绑定的 ReviewRequest、必要的原文/证据材料和自己的任务状态；不接收 Analyst 私有 transcript。

## 4. 核验策略

`ResearchTask.review_policy` 取值如下：

```json
{
  "mode": "auto",
  "scope": ["source_fidelity", "reasoning_conditions"],
  "max_rounds": 3
}
```

- `required`：Analyst 必须请求独立 Verifier。不能以自行复读、空报告或提示词声明替代。
- `auto`：Analyst 可请求核验，也可提交明确原因跳过；缺少跳过理由是契约不完整。
- `off`：不创建文章级 Verifier，也不能由 Analyst 升级。Root 最终答案审核是独立流程，不受此字段关闭。
- 默认 `auto`，默认最多 3 轮，调用方可为本任务选择 1..5；Runtime 对任何任务硬限制为 5。
- 一轮从 Runtime 接受一个 Verifier Task 起计，失败、超时或输出无效也消耗一轮。模型格式补正和同请求传输重发属于模型调用账本，不额外算核验轮。

Analyst 的版本化交付包括完整研究正文、EvidenceLinks、材料处理/缺口/局限、ReviewTargets、`review_decision` 及每个 finding 的处置。若请求核验，Runtime 将该版本写成不可变草稿 Artifact，再创建一个 Verifier Task。Verifier 返回的 `reviewed_draft_ref` 和 `reviewed_revision` 必须与该任务绑定的草稿完全一致；旧版本报告不能证明新草稿已核验。

Verifier 报告逐项声明 `checked_target_ids`，必须与 ReviewRequest 的目标集合完全一致；没有发现问题也必须明确列出检查过的目标，不能用空 `findings` 隐式表达覆盖。报告中的 finding 含 `finding_id`、可选 `target_id`、登记类别、严重性、判断、解释、证据引用/原文锚点及未检查范围。它表达证据评估，不是“金融事实已证明”的通行证。Runtime 校验 Schema、目标覆盖、草稿版本、引用授权、引用可定位性和报告来源；不自动判断分析在语义上正确。

Analyst 对整个核验流程已收到的每个 finding 必须选择 `accepted`、`rejected_with_reason` 或 `unresolved`，后续报告没有重复某个 finding 不会清除此前的处置义务。Runtime 在接受下一轮派发或最终交付前校验 finding ID 集合与处置集合一一对应。提交新 revision 时生成新的草稿版本；有预算且未达轮数上限时可请求下一轮。到达上限后 Runtime 不再创建 Verifier，返回现有可用分析并标注 `round_limit_reached`/partial，逐项列出未解决问题；`required` 也不得伪称核验已完成。

模型调用预算沿用 067：Root 自身最多 120 次、每个子/孙 Task 自身最多 60 次、整轮共享最多 600 次和 60 分钟。Verifier 的每次模型请求同时计入它自身 Task 与整轮 Run；不扣父 Task 的独立 120/60 额度，不为轮次预占 60 次。额度、超时、取消、模型错误和权限失败按真实原因记录，不自动换模型或加预算。

## 5. 有界失败交付

- Profile/Brief/schema 不合法：Runtime 在模型调用前拒绝 spawn，记录脱敏的契约错误，不创建孤儿 Task 或扣模型调用。
- Analyst 要求 `required` 但没有 ReviewRequest：不发布为已核验结果；保留已存草稿，反馈可恢复的状态，预算/截止边界仍生效。
- Verifier 失败、超时、取消、预算耗尽、报告格式错误或报告引用版本不匹配：报告标记失败/无效；保留 Analyst 草稿，允许 Analyst 在原预算内披露缺口或按剩余轮次决定继续。无效报告不可作为证据。
- 找不到草稿或授权原文：Verifier 报告 `inconclusive`/unchecked scope；不得编造引用或把资料不可用说成观点错误。
- 达到 5 轮硬上限或没有剩余预算：停止派发，交付可用正文并标记核验未完成和限制；除非正文本身因越权/伪造等安全原因不可交付，否则不因核验失败抹掉合法分析。
- Artifact/SQLite/outbox 提交失败：按现有事务与恢复规则报告基础设施失败，不声称任务已创建或报告已保存。

交付状态区分 `not_requested`、`skipped_with_reason`、`reviewed_with_resolutions`、`issues_open`、`review_failed`、`round_limit_reached`。`reviewed_with_resolutions` 仅指发现均已被处置，不等于所有结论正确或报告无反对意见。

## 6. 数据与兼容

不新增 SQLite 表或 Redis 业务状态。Task Brief 继续作为不可变 Artifact，状态/轮数/当前草稿与报告引用保存在既有 Checkpoint Artifact，结果按既有 Submission/Result 注册，Outbox/Redis Streams 仍只负责通知。父子关系、权限快照、Attempt、预算和事件沿用 013/014。

历史 Rule Artifact 使用历史校验分支并维持原提示词与权限语义；新 manifest 只用于新建 Run。旧 Analyst/Verifier 研究结果可继续由旧契约读取；新 Analyst v2 和 Verifier ReviewReport 使用显式新契约版本，不以字段猜测旧数据版本。

Root 默认只取得 Analyst 最终结果及核验状态/报告引用，不注入 Verifier 全部对话。按既有授权可读取完整结果 Artifact。Root 跨文章结论、证据和最终答案审核仍由 Root 负责；逐篇 ReviewReport 不能代替 Root 最终审核。

## 7. 验收

离线验收覆盖 Profile 快照冻结与旧快照兼容、未知配置拒绝、父子角色权限交集、Verifier 禁止递归、required/auto/off 门控、0/1/3/5/6 轮边界、草稿版本绑定、无效/旧版报告拒绝、逐项处置、超时和失败 partial 交付、预算独立账本、跨 Attempt 恢复和重放幂等。离线通过只证明状态机/契约实现符合本文件，不证明真实模型会合理选择核验、报告语义正确或效果优于原路径。真实模型对照须另行冻结样本和预算，不由本变更自动发起。

## 8. 实现与离线验证记录

- 完成 Profile v2 加载/权限边界与旧规则快照兼容；Root 简报生成、Profile/Brief 组合和 Analyst/Verifier 输出契约已接入现有 H2 调度路径。
- 完成 Analyst `required | auto | off` 核验决策、版本绑定草稿、独立 Verifier Task、报告目标覆盖校验，以及 Analyst 对跨轮 findings 的逐项处置校验。
- Verifier 的失败、超时、预算耗尽、无效报告和轮数上限均保留可用草稿并进入有界交付；Verifier 无 Profile 子派发权限。未新增 SQLite 表或 Redis 业务状态。
- 修复研究结果 Schema 选择读取了错误上下文层级的问题：装配后的 `role`、`role_version`、`output_contract` 与 `review_request` 位于 user 消息顶层，Runtime 现同时兼容顶层简报和嵌套 `research_brief`，正确选择 Analyst v2 与 Verifier ReviewReport 校验器。
- 离线回归：`uv run pytest -q tests/test_dispatch_finance_agent.py tests/test_dispatch_finance_root.py tests/test_dispatch_finance_rules.py tests/test_dispatch_finance_research.py tests/test_dispatch_finance_review_loop.py tests/test_dispatch_finance_service.py tests/test_dispatch_finance_continuation.py tests/test_dispatch_market_sources.py tests/test_dispatch_public_sources.py tests/test_dispatch_personal_sources.py`，422 passed。
- 静态检查：`uv run ruff check src/policy_impact/runtime/research_contracts.py src/policy_impact/dispatch/finance_rules.py src/policy_impact/dispatch/finance_research.py src/policy_impact/dispatch/finance_agent.py tests/test_dispatch_finance_agent.py tests/test_dispatch_finance_rules.py tests/test_dispatch_finance_research.py tests/test_dispatch_finance_review_loop.py tests/test_dispatch_finance_continuation.py tests/test_dispatch_market_sources.py tests/test_dispatch_public_sources.py tests/test_dispatch_personal_sources.py`，All checks passed。
- 验证均为离线 fixture 测试；未验证真实模型对核验时机的判断质量、金融判断质量或相较旧路径的效果提升。全套 pytest、压力测试及真实 Redis/模型验证不属于以上通过结果。
