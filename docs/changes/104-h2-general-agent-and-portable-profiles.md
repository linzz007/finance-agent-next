# 104 通用子 Agent 与可组合专家配置

日期：2026-10-27  
状态：已按用户确认的设计实现，离线回归完成；未追加真实模型测试。

本文更新 103 的角色权限和核验入口；103 的版本绑定、逐项处置、持久化、失败披露继续有效。
模型侧 `agent.spawn` 字段后来由 [106](106-h2-profile-aware-spawn-contract.md) 明确分层：本文件第 6 节旧式 `Spawn` 示例用于说明当时的完整内部业务字段，不再是 v4 Run 的模型输入 Schema。

## 1. 统一执行底座

所有子 Agent 使用同一 H2 执行循环、Task/Attempt、工具调用、检查点、预算和结果传递。
增加 `general` 默认配置：父 Agent 通过轻量任务消息和显式材料引用，
动态指定本次身份、方法和交付要求。例如论文盲审、跨材料比较或问题拆解，无须新增 Python 角色执行类。
动态指令作为任务级内容装配；共享 System 规则、授权、模型选择及预算仍由 Harness 管理。

专家配置从总清单拆到 `config/agents/profiles/*.json`：用途、Prompt 引用、工具白名单、
可委派配置、输入和输出合同、上下文策略都可独立维护。Prompt 保存在 prompts/finance。
general、analyst、verifier、skeptic 都是同一执行器加载的配置。
通用结果 `finance.task_result.v1` 必须包含 content，其余证据、材料处置、缺口和限制可省略；
任务可以要求正文内的任意格式，但不能通过任务指令发明执行器、绕过合同或伪造来源。
专家仍加载各自充分的专业指令和既有研究/核验结果合同。

## 2. 委派权限

新配置为上述所有角色开放现有业务与调度工具，并允许选择所有已注册配置。
工具实际权限仍为服务可执行工具、本次 Run 授权、父任务权限与配置白名单的交集；
这里的“全部权限”指项目已提供的能力，不自动增加 shell、文件访问或未绑定的数据服务。
维持 Root→子→孙的总深度上限：深度 1 的任何角色可以继续委派一层；深度 2 不能再派。
孙任务可自行完成核验或调用数据工具；如要求独立核验但没有剩余层级，交付可用结果并明确核验未完成。
Verifier 的辅助委派不代替它自身签发版本绑定报告，也不能把子任务意见冒充已读取的原始证据。

## 3. 简化核验入口

`agent.spawn` 推荐填写 `review_mode: required | auto | off`；不填默认 auto。
高级配置移到可选 `review_options`：scope（核验范围）、instructions（本次附加核验要求）、
max_rounds（最多 1..5 轮，默认 5）。主 Prompt 说明只有用户指定特殊查证范围、方法或更小轮数时才需要这些参数。
Runtime 将该入口展开为冻结的 ReviewPolicy；兼容旧 `review_policy`，禁止新旧入口同时填写以免歧义。
此自动闭环仅用于 Analyst 2.0；其他角色可根据任务自由委派，不自动套用文章核验流程。
显式选择 off 可直接交付分析；required 必须取得匹配版本的独立报告才能宣称完成；auto 由 Analyst 有理由地选择。

核验默认和硬上限均为 5，信息充分时立即完成，不强制用满。
补充核验要求由 Runtime 写入 Verifier 任务指令；来源仍须显式授权，联网范围仍受实际工具权限约束。
五轮核验与五次动作格式补正属于不同计数，均消耗既有模型账本。

## 4. 规则版本与兼容

新规则使用 finance.h2.agent_rules.v3，独立配置文件和 Prompt 均进入同一不可变 Run 规则快照。
新建 Run 使用新配置；已有 v1/v2 Run 从保存的快照恢复，保持原角色、工具及已存核验上限。
未知工具、无执行器的结果合同、非法路径及无效配置仍拒绝加载；不修改历史快照或付费验收记录。
SQLite/Redis 格式及 Root120、各子/孙60、整轮600次/60分钟等预算保持原值。

## 5. 验证

离线验证须覆盖：通用动态任务的输入与结果、专家配置文件加载、规则快照恢复、所有角色的深度 1
委派及深度 2 拒绝、授权交集、required/auto/off、可选核验要求、默认五轮与第六轮拒绝、
旧快照兼容、版本绑定及逐项处置。使用临时数据库和 fixture，不接触用户真实语料。
完整 Worker 核验链路新增测试发现：Analyst 最终提交的 finance_review 分节误用了报告的 ResultRef，
而存储合同要求 ArtifactRef；报告已成功返回并保存为 report_material_ref。
修复使用该已授权 ArtifactRef，不改变 report_ref 的结果索引含义，也不扩大读取权限。
另修复通用角色接入时共享恢复包装器仍只接受旧三个角色的漏接，改为从冻结的角色目录判定。

最终验证记录：

- `final-review-integration.xml`：99 通过，覆盖通用任务、可配置新专家、全部角色委派、五轮核验、
  旧快照恢复、独立预算，以及真实 Worker/SQLite/FakeRedis 的两条完整闭环。
  通用链路为 Root→general→general→父任务→Root；核验链路为 Root→Analyst→Verifier→Analyst→Root。
  模型响应使用离线替身；核验链路额外确认最终分节引用对应可读取的报告 Artifact。
- `finance-regression.xml`：金融基线 52 个测试模块，1639 通过、30 跳过。
  该批在最终报告分节引用修复之前执行，修复后由上述 99 项回归覆盖受影响路径。
- `h2-regression.xml`：较早的广域 H2 回归为 1288 通过、7 跳过、1 失败。
  失败是历史预算迁移 fixture 在重建旧命令哈希时带入新增的两个空可选字段；修正 fixture 后，
  `test_dispatch_independent_budgets.py` 已包含在最终 99 项全通过回归内。未重跑整批，不改写首次记录。
- 配置隔离检查 10/10；本轮改动 Python 文件的 Ruff 检查通过；Git diff 空白检查通过。
- 早期针对旧 Prompt、角色目录和 Verifier 禁止委派的 fixture 预期已随新设计更新；
  保留初次失败输出，不将 fixture 更新说成真实模型能力提升。

日志与 JUnit 保存在项目同级 `runtime-records/h2-general-profiles-104-20261027`。
使用临时数据库，没有新增付费模型调用，也未重启现有服务。
离线通过不代表真实模型质量、金融结论正确、真实 Redis 验收或完整复刻 Codex/Claude Code。

参考：本地 Codex 的 agent/role.rs 默认角色、multi_agents/spawn.rs 动态 message 与角色配置装配；
learn-claude-code 的 s04_subagent.py 展示通用子循环与新任务上下文，它是教学实现，并非 Claude Code 完整源码。

## 6. 调用示例与配置位置

通用任务的完整业务参数示意如下，派发键和材料引用须换成当前上下文中的真实值：

```json
{
  "dispatch_key": "runtime-provided-action-key",
  "role": "general",
  "role_version": "1.0.0",
  "objective": "对指定论文做方法层面的盲审",
  "task_instructions": "以独立审稿人的身份检查论证、假设和实验设计。区分实质问题与表达建议，按严重程度组织正文。",
  "success_criteria": ["每项实质意见定位到材料并说明影响；不臆测作者身份"],
  "input_refs": ["authorized-paper-ref"],
  "output_contract": {"name": "finance.task_result", "version": "1"}
}
```

article 分析使用 analyst/2.0.0 和 finance.analyst_result@2，基础参数相同，另填
`"review_mode": "required"`；明确不核验则填 off。
只有特殊核验要求才补充 `"review_options": {"scope": ["external_facts"], "instructions": "查证数据口径，查不到时保留缺口"}`。
轮数未填默认五轮，并不要求主 Agent 逐轮安排 Verifier。

- config/agents/finance.json：规则资产索引及 Root 可选配置目录。
- config/agents/profiles/general.json：通用默认能力及结果合同。
- config/agents/profiles/analyst.json、verifier.json、skeptic.json：专业默认能力及返回要求。
- prompts/finance/general.md、analyst.md、verifier.md、skeptic.md：可独立维护的工作方法。
- 新专家可新增 JSON/Prompt 并在清单中注册，不改 Python 角色分支；输入和输出合同必须由执行层实际支持。
- 通用任务使用 finance.task_result@1；分析核验闭环的专属返回合同仍绑定相应工作流，不能靠改配置名称冒充。
