# H2 规则资产、子任务上下文与结果交接讨论稿

日期：2026-10-22。

后续记录：规则资产外置已获得确认并按 [101](../changes/101-h2-external-agent-rules.md)实现，验证与现有网页进程未重启的边界见对应验收记录。本稿其余上下文选择建议仍是讨论稿，不因规则外置而自动获批或实现。后续的 Agent Profile 与文章级分析-核验协作候选设计另见[方案稿](h2-agent-profile-and-review-loop.md)；其状态同样是待复核，不覆盖现行基线。

状态：**现状说明与待确认方案，不是已冻结基线，不代表已经实现。** 本轮只阅读代码、核对官方资料和保存讨论，没有修改运行逻辑、表结构、模型配置或启动付费测试。已确认设计仍以设计入口及其引用契约为准。

## 1. 结论与范围

当前 H2 不是主 Agent 同步调用子 Agent 的旧循环。它已具有独立 Task、Attempt、授权输入、持久检查点、异步派发、等待恢复和结果索引。

但还不能因此把整个 Harness 称为优秀或成熟：主/子指令仍散在 Python 字符串中，主 Agent 缺少完整的角色用途目录；上下文装配有技术能力，但没有实现自动判断历史相关性的语义选择器。结果的结构验收也不是研究质量的完整验收。

本轮讨论三个边界，不重做金融分析策略：

1. 规则文件与执行代码如何解耦。
2. 主 Agent 与 Runtime 如何分工装配子任务输入。
3. 错误恢复、子结果可交接和主 Agent 最终采用分别代表什么。

## 2. 当前失败处理，不是一种通用重试

### 2.1 输出格式与动作契约

处理顺序为：保留原始输出 -> 有界文本兼容 -> 解析首个动作 -> 校验 AgentAction -> 校验实际工具参数/权限，或校验子任务 final.output。

- 唯一外层 bare/json Markdown 围栏只在包住一个完整对象时移除。
- JSON 字符串内部的裸控制字符及非法转义可规范化；不是猜测丢失的字段、括号或研究结论。
- 无围栏输出按已有首对象边界处理，尾部保留审计；不能跳过第一个错误对象而接受后面的另一个对象。
- 当前还有已实现的有限供应方文本动作兼容，例如 DeepSeek DSML 方言适配；适配后仍必须经过公共动作、工具参数和权限校验，不能把任意 XML 当作工具结果。
- 这些兼容成功时不额外调用模型；原始文本与兼容诊断仍保存。

解析/结构仍不合法时，Root 与研究子任务共用一次同模型补正机会：

1. 只使用原有可信任务上下文、Schema、字段错误路径和有界脱敏语法诊断。
2. 被拒输出全文仅留审计，不整段重新作为上下文或证据输入。
3. 不给补正新增研究额度或更换模型。
4. 补正请求发生 transport_error 时，同一补正请求最多重发一次，沿用原预算和截止时间。
5. 普通补正仍失败则报告失败，不无限重启原决策。

因此，这一补正子流程最多是原请求、一次补正、一次补正传输重发三个物理请求。**这不是整个 Task 所有失败路径合计最多三次请求的承诺。** 首次请求的可重试传输故障还有 2.3 的独立 Attempt 恢复路径。

### 2.2 子结果的证据锚点补正

如果已产生可识别的研究终稿，只是证据锚点等适用字段不合法，现有专用分支会冻结候选正文，要求模型只返回允许的锚点补丁，再由程序组装。

这与普通结构补正不同：它不能借修格式重新写金融结论。补丁非法或专用补正因上下文容量无法发出时，适用分支保留原正文和警告；并非任何完全不可解析输出都能进入该分支。

### 2.3 首次请求的传输失败

Worker 将执行故障写入 Repository。只有标记为可重试的 transport_error 才进入这一任务重试路径，恢复已保存的检查点并新建 Attempt。

DispatchConfig 的默认 max_attempts 为 5；判断依据是失败 Attempt 计数，不是正常 continue/wait 所累积的 Attempt 编号。重试仍受任务自身额度、整轮额度和截止时间限制。补正终态错误会去掉可重试标记，防止重新跑完整原决策。

provider_error、quota_error、contract_error 等不是一律自动重试。远端可能已处理但本地未收到结果的情况，仍可能带来未知结果或重复费用；数据库幂等不等于第三方模型请求恰好执行一次。

### 2.4 动作 JSON 合法，但工具参数或授权不合法

这是工具执行失败，不一定是需要补正 JSON 的格式错误。例如 article.read 参数缺失、字段名错误、引用未授权。

适用的非重试型工具拒绝会保存失败观察，包含错误码、工具名/describe_name、有限字段定位，并明确没有成功读取来源。下次模型调用可据此选择 tool.describe、修正参数、改用有权限的材料或披露缺口。

Runtime 不替模型猜作者、文章 ID 或扩大权限，也不把失败观察计作有效证据。取消、租约失效以及需要上抛的基础设施故障走各自安全路径，不应吞成普通读文失败。

### 2.5 子任务整体失败与研究质量不足

主 Agent 可以收到子任务终态和脱敏错误诊断；Prompt 建议最多一次有理由的纠正/替代尝试，再交付已有分析和缺口。这是行为引导，不是已经实现的独立“只能重新派一次”硬计数器。

证据薄弱、存在反例、审核不完整，本身不应等同于禁止输出。反之，结构补正与任务恢复也不能保证每种模型/环境故障都能产生有效终稿。保留原始输出、保存候选分析和成功发布最终答案是三种不同能力。

主要代码：dispatch/finance_actions.py、finance_agent.py::_decide/_business_action、finance_anchor_repair.py、repository.py::fail、worker.py::_fail_execution。

## 3. 当前 Prompt、角色和工具组织

### 3.1 已经存在的内容

- finance_agent.py：Root 指令、公共上下文规则、一次格式补正的指令。
- finance_research.py：子任务公共指令、analyst/verifier/skeptic 职责、结果 Schema 装配。
- finance_tool_contracts.py：工具目录、调度说明、预算及失败后的行为建议。
- finance_public_sources.py、finance_market_sources.py、finance_personal_sources.py：三类来源规则。
- finance_anchor_repair.py：冻结正文、仅补正锚点的专用指令。

这些是主要指令载体，不是七份每次全部加载的独立模板。工具参数、动作和结果的结构还来自程序 Schema。

仓库已有 prompts/agents 目录，但里面主要是继承的其他业务模板；当前 H2 金融指令没有因此完成外部文件化。

### 3.2 主 Agent 目前知道与不知道什么

主输入提供角色名称/版本、调度规则和 agent.spawn 的按需 Schema；主指令泛称可委派来源分析或审核，但没有像子 Prompt 那样完整逐项解释三个角色。

三个角色默认注册的工具集合相同。实际差异主要来自角色指令、每次 objective/success_criteria、显式材料授权以及父级权限，不是三个互不相同的基础执行器。

| 角色 | 当前职责 | 不代表什么 |
| --- | --- | --- |
| analyst | 分析指定材料，保留作者推理与条件 | 不代表必须得出肯定结论 |
| verifier | 检查论断的来源支持，区分原文支持与外部事实验证 | 不代表每次都已联网或已核实真伪 |
| skeptic | 找真实反证、隐含假设与失效条件 | 不代表必须反驳作者 |

三者共用 Spawn 输入和 ResearchResult 输出。output_contract 名称/版本不是让主 Agent 任意创造 Schema 的机制；当前金融子执行器仍校验既定 ResearchResult。

## 4. 规则资产解耦建议，待确认

将“规则放到文件”与“取消代码约束”分开：前者提升可维护性；后者会破坏可靠性，不在建议范围。

建议的资产组织只是候选布局，不是已经生效的路径或契约：

```text
config/agents/
  analyst.toml
  verifier.toml
  skeptic.toml
prompts/finance/
  root.md
  child-common.md
  analyst.md
  verifier.md
  skeptic.md
  action-repair.md
  anchor-repair.md
```

Agent 定义引用名称、用途简介、角色指令文件、输入/输出契约、上下文策略和允许的工具策略。主输入目录与子指令应从同一份角色定义生成，避免维护两份不一致的说明。

主 Agent 默认只需看到角色用途、适用/不适用场景、必需交接信息及返回结果简介；完整参数和详细规则可以按需展开，不加载所有角色长 Prompt。

版本不是不重要，而是不宜让模型每次反复研究。Runtime 可解析当前配置的内部版本并保存内容哈希、来源及快照，保证正在运行和恢复的任务不因文件修改而换规则。是否允许热更新、新 Run 生效的具体时机需要另行确认。

权限、预算、租约、幂等、工具实际执行及 Schema 校验继续由程序负责。规则文件不能绕过父级权限、私自增加付费模型或授权数据。

## 5. 当前子任务上下文到底来自哪里

显式 agent.spawn 路径中，主 Agent 负责写 objective、success_criteria，并选择 role、input_refs、parent_context_refs 等任务输入。它**不是每次重新编写整份子 Agent system prompt，也不是每次重新设计 ResearchResult**。

Runtime 读取持久化任务简报，校验材料引用与权限，将 parent_context_refs 合入授权输入；当前没有单独的父历史相关性摘要器。子初始 query 默认是派发 objective，不是自动完整继承原始用户问题。

Worker 调用子执行器时装配：

```text
固定的角色/公共规则
+ 主 Agent 写的目标、验收条件和显式背景/材料
+ 工具目录、动作/结果 Schema
+ Runtime 提供的身份、层级、模型、预算、研究时钟
+ 子任务自己的计划、已读材料、真实工具观察和检查点
```

子任务不会自动继承父级全部文章、完整聊天历史和全部工具输出。父 Agent 如认为原始问题、时间范围或关键限制重要，需要通过任务目标或授权背景传递。材料过大时保留授权展开句柄，不能把“有句柄”冒称“本次输入已完整读过”。

## 6. 保留用户想法：主 Agent 定研究意图，Runtime 装配上下文

### 6.1 想法记录

用户提出：减少主 Agent 编写机械交接信息的负担，由主 Agent 给目标和验收条件，Runtime 结合子角色规则、历史上下文和实际工具结果组织调用。

这是合理的职责方向，但“Runtime 会自动得到完美上下文”不能作为未经验证的前提。

### 6.2 建议的最小分工

| 主 Agent 决定 | Runtime 确定性完成 |
| --- | --- |
| 委派给谁、要解决哪个问题 | 加载已经配置的角色指令 |
| 怎样的结果对当前研究有用 | 填入动作/结果 Schema 与真实身份 |
| 焦点材料和有必要传递的背景 | 校验引用、展开已授权材料、保留溯源 |
| 研究范围、时间范围和任务特有约束 | 继承可信用户限制、研究日及资源边界 |
| 是否接受、追问或使用子结论 | 保存上下文清单、检查点、结果与事件 |

不能只传“帮我分析”就期待 Runtime 精确重建研究意图；也不能为少写交接而复制所有历史。相关材料优先让主 Agent 选择已有句柄，避免重复抄写原文。

可以分层装配：必需信息为目标、验收条件、可信约束和角色规则；焦点信息为显式文章/论断/证据；背景为与任务有关的前序结论和观察，保留来源和未验证标记。其他历史保留授权可展开索引，不默认灌入全文。

历史父结论只能作为待检验背景，不得升级成事实。失败工具结果不是有效来源。verifier/skeptic 不应因接收父级长篇立场而默认被要求证明父级正确。

如以后增加模型驱动的历史选择/摘要，它本身是一次可能遗漏、耗时和付费的工作，需要单独的输入/输出、记录和验证；不是简单拼接模板已经解决了语义选择。

### 6.3 任务示例，不是新增工具 Schema

问题：“培风客最近三个月关于黄金的观点如何变化？”

主 Agent 可委派 analyst：“依据这些文章整理黄金观点的时间变化，保留成立条件和作者明确说不知道的部分；按日期区分观点，不把预测写成事实；不负责最终投资建议。”

Runtime 加入角色规则、明确的研究日和窗口、已授权文章及读取范围、工具、结果 Schema、预算和恢复状态。子 Agent 不需要父级全部无关行情尝试、其他作者材料或失败日志全文；若确有需要，应通过受授权的材料获取，而不是默认继承。

## 7. Codex 能确认到什么程度

本轮实际获取了 OpenAI 官方 Subagents 文档：
https://learn.chatgpt.com/docs/agent-configuration/subagents

官方文档支持这些事实：

- 自定义 Agent 可放在独立 TOML 文件中，每个文件有 name、description、developer_instructions。
- 指令、模型配置和能力配置可以与通用执行机制区分，子 Agent 继承父级权限边界，文件不能自行覆盖权限。
- 子 Agent 在独立线程工作，可以并行运行，再由主线程收集结果。
- 多 Agent 的目的之一是把嘈杂探索移出主上下文，返回总结而非复制全部中间输出。
- 良好的委派仍需描述如何分工、是否等待及期望输出；没有证明主 Agent 只写一个模糊目标就够。

仓库早期 Codex 调研另外记录了 2026-10-17 固定提交 ce0323f2f2d3b25e82806cd555237da3db4e1897 的显式 fork_context 分支，参见 harness-v2-multi-agent.md。这是既有源码研究记录，本轮没有重新验证该分支的最新实现，不据此推断默认复制范围或自动语义筛选能力。

公开资料不能证明 Codex 有“自动完美提取父历史”的算法，也不能用它证明本项目必须引入某种队列、数据库或上下文选择器。

## 8. 子结果什么时候能传回，如何验收

需要区分四个动作：保存候选、允许交接、研究质量判断、主 Agent 最终采用。

### 8.1 结构与引用检查

研究子 Agent 输出 final，其 output 必须匹配 ResearchResult：content、evidence_links、material_disposition、gaps、limitations，以及可选 supporting_artifact_refs/candidate_answer。

程序检查非空正文、字段类型、禁止额外字段、引用关系、重复项，以及证据锚点文字是否逐字出现在正文或候选答案中。部分列表允许为空；“有该字段”不代表“该方面研究已经完成”。

领域执行器先保存完整候选，再检查已授权/实际可用材料、可读取来源、引文是否包含在来源中。未知引用或引文不匹配会形成警告，并从已接受证据集合剔除相应绑定，不悄悄删除整篇分析。

有这些验证/状态警告时可返回 partial。validation_status=complete 表示完成了该程序校验过程，不是所有金融结论都已核实；子状态 succeeded 也不证明验收条件逐条语义通过。

### 8.2 持久化接收

Submission 提供 full_ref、摘要、有效证据引用、缺口与警告。当前金融子摘要来自正文前 500 个字符，不是另一次完整语义摘要。

Repository 校验当前租约/Attempt、幂等键、正文/分节引用可访问性和证据元数据，保存 result 索引与摘要。有效接收后将 Task 置为对应终态并记录 result.accepted。

元数据不完整会先保存候选；Worker 支持一个可选的元数据修复回调，但当前金融服务没有注入该通用回调，默认走保留正文、标记 incomplete_metadata/partial 的收口。不能把这个扩展点描述成每次都会实际调用模型修复。

过期 Worker 的迟到提交不能覆盖当前合法结果。accepted 在这里表示结果被系统接收，不是金融事实真实的认证。

### 8.3 研究质量与采用

目前没有保证所有 success_criteria 都经过自动语义验收器。来源存在、引文可找到，也不证明引文在逻辑上支持该结论。

主 Agent 仍要判断是否回答目标、是否保留条件、是否存在过度归纳、是否需要追问或外部核查，以及最终采用哪些结论。不是每个子结果都必须再派一次 verifier；审核范围应按已有设计和实际风险选择。

## 9. 异步交接与并行/串行关系

```text
主 Task: spawn(A) -> spawn(B) -> 自己继续读/分析 -> wait(A,B) -> 恢复综合
子 Task A:             读材料 -> 分析 -> final -> 保存/接收结果
子 Task B:                         读材料 -> 分析 -> final -> 保存/接收结果
```

每个 Task 内按动作顺序执行与持久化；不同 Task 可被不同 Worker 并行领取。默认 worker_concurrency=4，不是无限并行，也不是 Agent 数量等于操作系统线程数量。

spawn 返回身份/状态回执，不等待研究完成；主 Task 可以继续。wait 保存检查点并释放执行 Worker，条件满足或等待超时后新建 Attempt 恢复，而非占住一个函数一直同步等模型。

wait 的 all/any 判断针对目标是否终态，不是是否全部成功。失败也应被父任务看到；等待超时不等于取消仍在运行的子任务。

父任务恢复时可获得 result_ref、状态、摘要、证据与警告。小结果的完整 Artifact 不超过 16 KiB 且完整输入适配时可以自动注入；否则使用 result.get 的 summary/full/section 分页展开。默认分页 16,000 字符，最大 64,000 字符；这是字符数，不是 tokens 或字节。

完整内容在 Artifact，SQLite 存索引与状态，Redis/FakeRedis 只发调度通知。结果接收/等待恢复是可追溯交接，不等于强制把子内容中途插入正在执行的父模型请求。

“返回了句柄”“本次输入包含完整文本”“模型理解并采用”仍是三种状态。读取记录能证明输入与页范围，不能证明认知理解；不能用永久全量灌入替代这一边界。

## 10. 后续确认及验证建议

先确认两项设计，不在本轮直接重构：

1. 外部化角色/规则资产，主目录和子规则来自同源定义；内部保留版本与哈希追溯，旧 Run 恢复不被新文件改变。
2. 主 Agent 保留目标、验收和焦点材料选择，Runtime 做可信约束继承与确定性装配；暂不承诺自动从全部父历史中筛出最佳上下文。

若确认，先做离线验证：装配内容可回溯、规则外置前后语义/有效输入可对照、主角色用途可发现、未授权材料不会进入输入、重启后使用正确规则版本、完整候选不因元数据失败丢失。

需要真实模型比较时再冻结同题、同模型和同预算，观察目标完成度、归因/条件保留、背景遗漏、无关上下文干扰、输入量、交付耗时和总调用成本。JSON 合法率和一次成功运行均不能独立证明研究质量或商业稳定性。

## 11. 现状代码索引

- src/policy_impact/dispatch/finance_agent.py：_messages、_scheduler_action、_business_action、_decide。
- src/policy_impact/dispatch/finance_research.py：_messages、_final。
- src/policy_impact/dispatch/finance_anchor_repair.py：ResearchAnchorRepair。
- src/policy_impact/dispatch/finance_service.py：for_finance_corpus、Worker 组合。
- src/policy_impact/dispatch/finance_root.py：_load_state、检查点/继续/等待。
- src/policy_impact/dispatch/repository.py：fail、_wake、execution_input、submit、_accept_result、finalize_incomplete、result_get。
- src/policy_impact/dispatch/worker.py：_fail_execution、_save_submission、处理/心跳/安全提交。
- src/policy_impact/dispatch/contracts.py：Spawn、DispatchConfig。
- src/policy_impact/runtime/research_contracts.py：ResearchResult、EvidenceLink 等结构。

本稿仅解释并记录候选方案；不替换现有设计基线，不额外授权模型调用、自动切模型或新的金融门控。
