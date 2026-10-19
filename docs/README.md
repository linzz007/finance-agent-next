# 金融 Agent 设计入口

更新时间：2026-10-18。用户已确认：新项目以仓库 Markdown 为设计权威，飞书不再作为后续开发的必需依赖。

快速阅读：[1.0 系统总览与策略地图](system-overview-v1.md)汇总当前流程、Prompt/Skills 关联、上下文的不同层次、多 Agent/存储边界和验证证据，再链接下列权威细节，不另外定义一套规则。[013 Harness 2.0 Agent 调度设计基线](changes/013-harness-v2-dispatch-baseline.md)是已冻结的调度语义基线；用户已确认三份细化契约，调度底座实现和剩余金融接入见 [014](changes/014-harness-v2-foundation.md)，队列压力、服务循环和安全清理补充见 [020](changes/020-dispatch-pressure-and-stream-retention.md)，验收 Query 的诊断/全量选择边界见 [021](changes/021-acceptance-query-selection.md)，供应方文本动作兼容见 [022](changes/022-text-action-compatibility.md)，状态版本不变量修复见 [023](changes/023-finance-state-revision-invariant.md)，C16 兼容回归见 [024](changes/024-harness-v2-c16-compatibility.md)，金融状态增量协议见 [026](changes/026-finance-state-update-protocol.md)。金融 Root 接入边界见 [017](changes/017-harness-v2-finance-loop-integration.md)。最新代码级 C01-C18 审查见[Harness 2.0 代码级验收报告](acceptance/harness-v2-code-acceptance-20261018.md)，跟进结果见[2026-10-19 验收跟进](acceptance/harness-v2-code-acceptance-followup-20261019.md)，DeepSeek 定向复验见[金融定向复验报告](acceptance/finance-live-acceptance-r3-20261019.md)。[012 Agent Dispatch Design](changes/012-agent-dispatch-design.md)保留为早期讨论稿；[Harness 2.0 多 Agent 研究方案](proposals/harness-v2-multi-agent.md)保留 Codex 调研依据，011 是较早的总体候选。

## 1. 从哪里开始

本轮实时定向复验记录见[金融实时验收跟进报告（2026-10-19）](acceptance/finance-live-acceptance-followup-20261019.md)；它只报告 Q03/Q14 定向结果，不替代完整 30 条 Query 验收。

### Harness 2.0 调度契约

以下三份文档及 R1/R2/R3（等待挂起、终态追问、失败依赖）已获用户确认，冻结为 **H2-CONTRACTS-2026-10-18-v1**；金融状态增量协议另以 **finance.state_update.v1** 冻结，见 026。阅读顺序为接口流程 -> 存储事务 -> Redis 投递 -> 金融状态交接；代码与未完成项在 014/026 单独映射，不把设计确认等同全套实现。

| 文档 | 唯一负责的内容 | 当前验证 |
| --- | --- | --- |
| [调度接口与验收](harness-v2/interface-contracts.md) | spawn/wait/status/send/cancel/resume、result.submit/get、内部 Worker/Adapter、兼容与故障验收 | Repository、Worker、单次模型执行/可信能力和显式注入 HTTP 边界已离线验证；默认 1.0 与生产接入保持不变 |
| [SQLite 存储契约](harness-v2/sqlite-schema.md) | 18 张表、外键/唯一约束、状态迁移、原子事务和预算账本 | 显式迁移与临时 DB 故障/并发验证；未迁移真实数据库 |
| [Redis Streams 协议](harness-v2/redis-protocol.md) | 消息格式、Outbox、claim/ACK、PEL 回收、Redis 丢失后的重建 | FakeRedis、故障注入及 20 轮脚本链路通过；真实 Redis 联测未运行 |

013 继续是已确认语义基线；三份契约不是 1.0 金融研究接口的替代品。014 底座验证为 95 passed / 2 skipped；[015 模型执行与可信 Worker 能力](changes/015-harness-v2-model-execution.md)新增 78 项通过，020 又补充压力/观测用例；当前 H2 调度定向合计为 196 passed / 2 skipped（其中包含 C16 离线兼容测试 1 passed）。默认 1.0 回归仍为 1589 passed / 30 skipped。不拼成真实金融质量或商业化成功率。金融业务状态恢复尚未接通；环境配置另有一项既有工作区检查未通过，详情见 014 和最新[代码级验收报告](acceptance/harness-v2-code-acceptance-20261018.md)。

### 现有研究设计

| 文档 | 负责回答的问题 | 当前状态 |
| --- | --- | --- |
| [产品目标](product.md) | 给谁使用、解决什么问题、做到什么程度、不做什么 | 已记录本轮个人使用、三项优先需求与认可回答样例；其余迁移内容待共同复核 |
| [系统架构](architecture.md) | Agent、插件、证据、上下文、审核和交付如何协作 | 已补入连贯理解、全文优先、可选阅读/综合委派与可恢复工作视图；其余迁移内容待共同复核 |
| [接口契约](finance_agent_interface_contracts.md) | 输入输出、标识、状态、权限和兼容字段怎么定义 | 第 8 节可选新版已接通，旧 v1 兼容；离线证据与剩余边界见变更 004 |
| [业务验收](finance_agent_eval_v1_acceptance.md) | 30 条 Query 的内容、证据、Trace 分别如何验收 | 1.0 Query 不变；最新Q04/Q08/Q14各两次完整交付，冻结版严格4/6通过及批次后记录修复见变更009；全30题尚未验收 |
| [研究与选型](research-decisions.md) | A-E 为什么这样选，论文证明了什么、没有证明什么 | 迁移既有选型，不是本轮重新完成论文评测 |
| [开发流程](development.md) | 人和 AI 如何从设计走到实现、审查、测试 | 本轮建立设计确认与实现的明确边界 |

六份职责文档之外，[AGENTS.md](../AGENTS.md) 只作开发 Agent 的短入口；本页只作导航。变更记录按实际变更创建，不为每个小修改新建一套设计。文章、持仓、凭证、对话和 Trace 不是设计文档，不写进这些文件。

## 2. 权威与冲突处理

1. 产品目标约束范围；系统架构约束行为；接口契约细化可交换数据；验收规范定义证明方式。不同文档不要重复定义同一规则。
2. 已确认的历史决定继续有效。此次从飞书搬入仓库，不代表重新选择 A-E，也不代表批准新的队列、数据库或固定分析流程。
3. 新增或改变产品/研究/架构语义的方案必须明确标为待确认，由用户决定后才能实现。已确认设计内的代码缺陷可以直接修复并补回归，不必重新选择方案。
4. 发现跨文档冲突时记录到变更说明，引用已确认的裁决；没有裁决则提问，不根据“哪个文件更新得晚”静默决定。
5. 实现与文档冲突时，不以当前代码反过来修改设计使其看起来通过。分别记录设计要求、实际行为和验证状态。

## 3. 飞书迁移与历史资料

- [迁移审查记录](changes/002-design-authority-migration.md)：完整来源、章节去向、冲突、缺损和待确认项。
- [飞书 revision 181 全文快照](archive/feishu-finance-agent-r181.md)：原样导出，作为历史来源，不是第二套现行规范。
- [原流程画板图片](archive/assets/feishu-finance-flow-r181.jpg)及[原始节点](archive/assets/feishu-finance-flow-r181.nodes.json)：图中含早期设想，不能覆盖系统架构的后续裁决。
- [迁移前接口](archive/finance_agent_interface_contracts.before-migration.md)和[迁移前验收](archive/finance_agent_eval_v1_acceptance.before-migration.md)：只用于差异追溯。

本页未列为现行入口的继承设计、旧产品文档及旧验收报告，一律作为历史参考。运行时加载的 `plugins/prompts/`、`plugins/skills/` 仍是程序输入，不因为文档迁移而自动更新，也不能代替已确认的设计权威。

## 4. 当前：保留 1.0 基线，推进 Harness 2.0

2026-10-18 用户明确停止继续验收 1.0，保留现有金融研究链路作为兼容基线，直接推进 Harness 2.0 的主 Agent 调度、上下文交接和结果回传。按[013 基线](changes/013-harness-v2-dispatch-baseline.md)及已确认契约，独立调度底座已实现，见 [014](changes/014-harness-v2-foundation.md)；单次模型执行与账本、可信能力入口进度见 [015](changes/015-harness-v2-model-execution.md)。金融 AgentLoop 尚未切换。不能把此前宽泛草案中的多机、多用户和完整业务异步并行全部当作本轮已实现能力，也不为学习 Codex 人为制造 1.0 缺陷。

`016 Harness 2.0 Finance Root Executor` defines and implements the opt-in
mapping from a financial root task to a durable finance state, checkpoint,
child-task observations, and final result section. `017 Harness 2.0 Finance
Loop Integration` now adds the opt-in model-driven finance composition: the
root selects bounded article actions or a versioned analyst delegate, child
results return through artifacts, and the root resumes durably before delivery.
The default 1.0 entry point remains unchanged.

`017 Harness 2.0 Finance Loop Integration` defines one durable finance domain
step per root Attempt, compact mapping of plan/article/evidence/review/delivery
state, and the explicit `FinanceHarnessV2Service.for_finance_corpus` entry.
This opt-in path is offline-verified for model-driven article acquisition and
analyst delegation, but it does not claim that the legacy synchronous financial
AgentLoop, HTTP routes, production workers, or live financial data have already
been converted.

### 4.1 怎么一起过文档

按问题主题复核，不要求用户一次通读所有历史变更或重新裁决已经确认的事项。每项先讲用途、现行流程和失败分支，用真实问题举例，再对照代码入口及验收证据。由用户判断研究行为是否符合目标；开发 Agent 负责查明实际行为，不把定位代码缺陷的任务交给用户。

| 顺序 | 共同复核主题 | 主要入口 | 当前复核进度 |
| --- | --- | --- | --- |
| 1 | 产品目标、职责分工、一轮研究的总体流程 | [产品](product.md)、[架构 1-2](architecture.md#1-职责与插件边界)、[系统总览](system-overview-v1.md) | 已初读产品/架构/总览并抽查入口；待用户逐项讨论，不代表共同验收通过 |
| 2 | System Prompt、Skills、工具如何装配；先规划如何保证 | [总览 4](system-overview-v1.md#4-promptskills工具是否真的关联)、[变更 010](changes/010-progressive-capability-loading.md) | 用户已选择 Skills/详细参数均按需读取，主 Prompt 统一核心原则和动作协议；代码已接通，新验证结果见 010。旧版委派规划入口仍待核对，不代表金融质量已验收 |
| 3 | 选材、浅读/深读、文章集合、覆盖与停止 | [架构 3-4](architecture.md#3-规划选材与停止)、[接口](finance_agent_interface_contracts.md) | 已完成设计讲解与总体一致性审核；细节观察后置，不阻塞已验收 1.0 或 Harness 2.0 |
| 4 | 跨文章综合、当前 Subagent 输入/输出及父任务处置 | [架构 5 / 8](architecture.md#5-跨文章分析)、[接口 8](finance_agent_interface_contracts.md#8-versioned-research-integration-contract) | 未展开共同复核；已确认当前委派为同步调用 |
| 5 | 跨轮历史、本轮上下文、研究产物保存与恢复 | [总览 5](system-overview-v1.md#5-上下文必须分三层理解)、[架构 8](architecture.md#8-subagent-与上下文) | 已发现两类不同触发策略，待核对设计范围，不预定归入 2.0 |
| 6 | 验证目标、修订、失败时完整交付与状态区分 | [架构 6-7](architecture.md#6-验证目标结果与修订)、[接口 5](finance_agent_interface_contracts.md) | 未展开共同复核 |
| 7 | JSON 动作、模型传输、重试、权限与父子预算 | [架构 9](architecture.md#9-模型传输稳定性)、[接口 6](finance_agent_interface_contracts.md)、[变更 006](changes/006-json-tool-contracts-and-recovery.md) | 未展开共同复核 |
| 8 | Trace、30 题分层验收、剩余验证与 Git 冻结 | [验收](finance_agent_eval_v1_acceptance.md)、[开发流程](development.md) | 已核对汇总基线与 Git 状态；未重新运行验收，未冻结版本 |

理解、设计确认、代码核对、离线验证和真实验收分别记录。未覆盖某条路径是验证缺口，不自动证明实现错误；同样，代码存在也不自动证明有效。A-E 选型理由在对应主题按需回查[研究与选型](research-decisions.md)，不重新把它们变成五段固定流水线。

### 4.2 初步复核清单

这是待逐项查明的清单，不是已经完成的全系统审计。后续新增问题须同时写出设计依据、代码/Trace 事实、归类和处理结果。

| 编号 | 观察与证据 | 归类 | 下一步与状态 |
| --- | --- | --- | --- |
| V1-R01 | 历史记录曾把变更 004 写成当前阶段，并把 2.0 草案标为暂停 | 历史文档状态偏差 | 已由 011 取代；1.0 作为兼容基线，2.0 调度核心进入实现 |
| V1-R02 | 跨轮 `ChatContextConfig` 默认最近 8 条/历史 1600/摘要 700 tokens；研究循环另有 1M 上限及 70%/60% 换出。两者不能合称同一策略 | 设计适用范围待核对；不能先判成 bug 或延期到 2.0 | 第 5 项对照已确认容量要求与实际上下文输入，讨论是否满足 1.0 需要；尚未修改 |
| V1-R03 | `r009-01` 六次完整交付，严格三栏 4/6；两次 Q04 的 Trace 错绑在批次后修复，已有离线回归但未真实复验 | 已修实现缺陷的剩余验证缺口 | 第 8 项确定有边界的补验；保留原始 4/6，不反写历史 |
| V1-R04 | 同批未触发阅读/综合 Subagent 或换出恢复，且未覆盖全部 30 题、默认 Chat、DeepSeek 及长对话 | 验证缺口，不等于这些能力已失败或全部必测 | 按最终冻结范围选择必要用例与方法，不自动启动全套付费循环 |
| V1-R05 | 原 10 份 Skill 全量注入，全部业务参数默认展示 | 用户已选择改为按需加载 | 变更 010 已接通七项目录、`skill.open` 和逐工具 `tool.describe(name)`；验证单列，未运行新付费批次 |
| V1-R06 | 原通用“直接 final”与金融先规划存在歧义，动作枚举和综合职责有重复/旧表述 | 文本口径差异已收口 | 两份执行说明退出金融主链路，必要规则归并；核心原则及动作协议统一在主 Prompt，未改变权限和金融方法；不声称已证明其导致历史真实失败 |
| V1-R07 | 工具入口和新版 `prepare_delegate` 检查父计划；旧版 delegate 分支未见同等前置检查，终稿检查不能证明研究开始前已规划 | 潜在实现缺陷，需定向复现 | 本轮 5 项测试未覆盖旧版首次委派；后续补验，再按已确认“先规划”要求处理 |
| V1-R08 | 现行 `article.list` 只有 `limit`，返回结果没有文章集合的分页游标或 `listing_complete`；超过限制时无法证明“全部文章”已列出 | 细节观察，后置 | 暂不修改，不阻塞已验收 1.0；需要真实遇到大集合问题时再处理 |
| V1-R09 | 设计定义了发现、读取、使用三层状态；当前代码通过多处局部记录表达这些事实，尚未统一为一个文章级状态视图 | 细节观察，后置 | 暂不修改，继续以现有设计理解和验收结果为准 |
| V1-R10 | 旧 `article.study` 与新版 `analyst@2.0.0` 深读路径并存，分别承担兼容和新版全文交接能力 | 兼容设计观察，后置 | 暂不收敛路径，不阻塞 Harness 2.0 调度与上下文设计 |
| V1-R11 | `research.coverage` 允许模型自行判断覆盖和停止，范围披露的结构强制程度保留弹性 | 设计细节观察，后置 | 暂不修改，后续遇到实际遗漏时再优化 |

未定义或相互冲突的设计由用户确认后再改；已有明确设计却未执行的，按实现缺陷修复并回归；超出个人研究 1.0 的新能力留在后续候选区。标准不因测试失败而临时降低。

### 4.3 Git 冻结怎么做

2026-10-18 已按用户要求创建首次提交 `715ed93` 并推送 `main` 到 `https://github.com/linzz007/finance-agent-next`，包含 1.0 现有代码及冻结的 013 设计，尚无调度 2.0 运行实现，也未创建版本标签。首次保存前全库测试为 2486 passed / 45 skipped / 19 failed，失败包含缺失本地业务 fixture 和继承契约不一致，不以 Git 备份替代验收通过。

后续保存先核对拟提交文件及凭证/隐私排除，再记录能力、未通过或未覆盖项、配置与测试证据。文章、Wiki、持仓、密钥、对话、Trace 与私有运行产物不进 Git。保存 1.0 不要求实现未批准的能力，也不允许把已知不符合冻结范围的缺陷改写成通过；尚未完成全面验收时应明确基线的限制。

### 4.4 已确认决定继续有效

既有确认与实现记录保留如下，不因本轮讲解复核而重新变成草案。2026-10-16 用户已批准下述版本化研究迭代；数据库、队列、前端和更广生命周期不在该迭代范围。

选择记录：[文章理解与综合衔接复核](changes/003-article-understanding-review.md)。第 4、6、7、8、9、10 节已确认文章理解、多篇综合、可恢复上下文，以及版本化 analyst + Skills 与运行内产物映射。主 Agent 统筹并对交付负责，实际分析、综合与复核可按需委派，不必亲自重做。后续实现见变更 004；容量最佳值及更广生命周期仍需实测或设计。代码事实、设计确认和真实模型质量验收分别记录，不能互相替代。

正式接口见[版本化研究交接契约](finance_agent_interface_contracts.md#8-versioned-research-integration-contract)，选择与当时静态依据见[记录第 10 节](changes/003-article-understanding-review.md#10-已确认的接口与存储映射)，接口兼容与持久化检查见[验收第 8 节](finance_agent_eval_v1_acceptance.md#8-版本化接口与运行内存储的增量检查)。

当前[实现迭代](changes/004-versioned-research-implementation.md)已接通：完整可选 v2 路径、质量优先运行上限和主 Agent 协调超限分段。具体代码、离线结果及未验收项集中在该记录；不能把链路通过当成模型质量或商业化稳定性已通过。

## 5. 当前验收基线

本轮完成[上下文容量对齐与1.0复验](changes/009-context-capacity-and-v1-baseline.md)：最高1M配置、70%触发/60%目标，较小边界与输出预留优先；不新增复杂压缩和等待策略。该变更替代旧容量默认，不覆盖008的冻结失败记录。固定Q04/Q08/Q14各两次均完整交付，内容/证据通过，冻结版严格链路通过4/6；两次Q04的工具Trace错绑已批次后修复并离线回归，不反算成真实六次全通过。个人三类研究已有可重复基线，不能推广为全部能力或商业化稳定性已通过。

最新代码、真实复验与离线修复：[审核收敛与完整分析交付优化](changes/008-review-convergence-and-draft-delivery.md)。用户充值后`r008-02`已固定完成Q04/Q14各三次；四次完整分析的内容/证据通过、两次仅材料不通过，整条链路均通过为2/6。先前`r008-01`额度阻塞单列，不混作业务失败。现有默认配置和金融研究策略不变；这是600秒诊断预算，不是默认60分钟预算验收。

当前变更008按用户确认方案A实现：主Agent在现有共享预算内自主追加，Runtime管边界及候选保留。008真实样本已验证审核失败时保留完整稿，但出现过无候选时仅材料的问题。冻结批次后修复完整嵌套Schema可见性、终态pending收尾及执行失败误称争议三项技术缺陷；009首次继续真实复验，旧结果不反写。第二阶段复杂工作视图与旧analyst元数据单列；首公开正文等待/取消/重试策略仍待选择。目标是1.0正常运行，不借此次变更扩展架构。

当前工具可靠性迭代见 [JSON 动作契约与有界恢复](changes/006-json-tool-contracts-and-recovery.md)：保留公开 JSON 协议，不接原生 Function Calling；先记录契约、恢复、上下文与交付分支，再实现验证。不改变已确认的金融研究策略。

2026-10-17 用户确认推进文档状态收口及真实验收。本轮已确认设计不重新选型；“迁移内容待共同复核”仅指其余迁移章节，不能理解为已确认的文章理解、综合、上下文与接口全部仍是草案。

| 维度 | 本轮状态 |
| --- | --- |
| 设计 | 变更 003 的已确认决定、正式接口第 8 节及变更 004 的实现范围是本轮验收依据；未决设计不自动纳入 |
| 实现 | 完整可选 v2 路径已接通，旧 v1 保持兼容；变更 008 第一阶段已接通目标级覆盖/增量缓存、版本候选、普通故障完整稿交付及未决异议保留。第二阶段上下文/旧 analyst notes 未实现；代码存在不代表模型实际选择或正确使用 |
| 离线验证 | 变更010按需加载后的金融/共享Runtime基线 **1589项通过、30项跳过，51个模块，118.97秒**，一项继承Starlette弃用警告；配置/分离10项及修改文件Ruff关键规则通过。原009的1568项保留在历史记录，不与本轮或定向检查累加；均不调用真实模型 |
| 环境阻塞记录 | 变更008的`r008-01`短探针成功，配置窗口探针HTTP403/insufficient_user_quota，实际业务0/6，非六题失败。用户随后充值继续；原始记录不覆盖，也未自动切换DeepSeek |
| 最新真实验收 | `r009-01`：显式gpt-responses、复用898文件快照及600秒诊断预算，Q04/Q08/Q14各2次结束。143次业务物理请求另2探针；六份完整分析内容/证据通过，严格三栏通过4/6，两次Q04工具Trace错绑不通过。全部无换出/恢复、终态pending为0；近压力探针估算699935/供应方报告459352，不认证完整1M。记录修复仅离线验证，原始结果不回写 |
| 前一批真实验收 | `r008-02`：Q04/Q14各3次结束，226次业务物理请求另2探针。四份完整稿内容/证据通过，两次Q04仅材料不通过；两个Q14执行缺口误称争议，严格整体验收2/6。重复context.open 18-29次、无公开正文长等待及v2未触发均保留，后续修复不能抹除这些历史失败 |
| 上一版真实验收 | 变更007：合成Responses 21/21、Chat 20/21；金融Q04/Q14各1次仅交付材料，有完整候选却在审核退回及剩余时限中失败。Q04另5次断连重试恢复，Q14旧analyst及重复恢复，不证明v2；不与本批次拼接成功率 |
| 历史真实验收 | 变更 006 的 GPT Q14 有 3196 字完整比较稿（审核覆盖 partial），Q04 终稿断连后只返回资料；DeepSeek 首批 Q04/Q14 分别在空正文/研究时限中断，补测 Q04 连续三次 DSML 后正确以 format/source_only 收尾。完整历史见变更 005/006，不拼成当前成功率 |
| 后续设计 | 自定义逐篇分析要求、更广生命周期、队列/事务及多人并发不属于本轮，不阻塞当前个人研究路径验收 |

运行产物保存在Git忽略的`data/acceptance/`，不将文章、Wiki、持仓、凭证、完整回答或Trace提交到设计文档。各次变更记录只保留汇总、缺陷及未覆盖能力；最新六次人工验收及后续离线修复报告为`data/acceptance/009-context-and-v1-report.md`，此前报告为`data/acceptance/008-review-delivery-report.md`和`data/acceptance/007-transport-report.md`。固定批次已结束，不继续付费循环到偶然通过。
- [025 Harness 2.0 明确基线缺口修复](changes/025-harness-v2-explicit-baseline-fixes.md)
