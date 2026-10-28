# 金融 Agent 设计入口

更新时间：2026-10-28。用户已确认：新项目以仓库 Markdown 为设计权威，飞书不再作为后续开发的必需依赖。

快速阅读：[1.0 系统总览与策略地图](system-overview-v1.md)汇总当前流程、Prompt/Skills 关联、上下文的不同层次、多 Agent/存储边界和验证证据，再链接下列权威细节，不另外定义一套规则。[013 Harness 2.0 Agent 调度设计基线](changes/013-harness-v2-dispatch-baseline.md)是已冻结的调度语义基线；用户已确认三份细化契约，调度底座实现和剩余金融接入见 [014](changes/014-harness-v2-foundation.md)，队列压力、服务循环和安全清理补充见 [020](changes/020-dispatch-pressure-and-stream-retention.md)，验收 Query 的诊断/全量选择边界见 [021](changes/021-acceptance-query-selection.md)，供应方文本动作兼容见 [022](changes/022-text-action-compatibility.md)，状态版本不变量修复见 [023](changes/023-finance-state-revision-invariant.md)，C16 兼容回归见 [024](changes/024-harness-v2-c16-compatibility.md)，金融状态增量协议见 [026](changes/026-finance-state-update-protocol.md)。金融 Root 接入边界见 [017](changes/017-harness-v2-finance-loop-integration.md)。最新代码级 C01-C18 审查见[Harness 2.0 代码级验收报告](acceptance/harness-v2-code-acceptance-20261018.md)，跟进结果见[2026-10-19 验收跟进](acceptance/harness-v2-code-acceptance-followup-20261019.md)，DeepSeek 定向复验见[金融定向复验报告](acceptance/finance-live-acceptance-r3-20261019.md)。[012 Agent Dispatch Design](changes/012-agent-dispatch-design.md)保留为早期讨论稿；[Harness 2.0 多 Agent 研究方案](proposals/harness-v2-multi-agent.md)保留 Codex 调研依据，011 是较早的总体候选。

## 1. 从哪里开始

[2026-10-28 调度 Trace 二次复核](acceptance/h2-agent-scheduling-trace-reaudit-20261028.md)
只读核对四题的 SQLite、原始 Trace 与 962 份 Artifact，另有 61 项定向离线回归通过；本次无新增付费调用。
递归、并行、等待恢复与全文读取已实际覆盖；最终版本复审及领域合同失败纠正仍需决策，不能把 Root 交付当作全套验收通过。

[110 投研工作台 UI/UX](changes/110-editorial-research-workbench.md)
统一暖米白、纸白与墨黑视觉，优化研究输入、报告、来源阅读、历史检索和可收起的 Agent 执行详情。
已完成桌面/手机检查、17 项相关回归和一条真实网页研究（6 次请求）；不修改金融分析或调度策略。

[109 工作台查看真实测试记录](changes/109-h2-test-record-browser.md)
网页左侧“测试记录”可查看原始问题、最终答案、任务树、子结果与 Trace；直接读取独立测试库和导出记录，保留失败与部分完成，不重新调用模型。接口见[工作台契约](harness-v2/workbench-contracts.md)。

[108 真实调度验收与核验恢复修复](changes/108-h2-live-scheduling-review-recovery.md)
实际问题和模型 Trace 发现：较大报告待完整读取、等待超时被误判为任务失败；修复保留原核验任务和草稿绑定，并区分结构通过与业务验收。
最新三题真实调用 104 次：自然提问核对通过；双文章真实并行成立但漏传一次 off；专业核验完成一轮意见处理，最终改稿未复审，整体尚未全通过。

[107 Agent 调度验收集与层级 Trace](changes/107-h2-agent-scheduling-trace-acceptance.md)
新增独立调度验收场景，并把 Run、Agent Task、Attempt、模型调用和调度事件关联成可还原的层级 Trace；不修改 30 条业务 Query、调度语义或数据库表结构。

[105 子 Agent 调用与配置替换验收](changes/105-h2-subagent-acceptance-cases.md)
新增独立通用/专家调用测试目录，不改已冻结的 30 条业务 Query。以真实 H2 Worker、SQLite、FakeRedis 和离线模型替身验证 Prompt/任务简报装配、工具权限、显式上下文、角色委派、Analyst 核验闭环、结果回传与配置替换。最终 45 项通过；不据此宣称真实模型语义质量已验收。

[104 通用子 Agent 与可组合专家配置](changes/104-h2-general-agent-and-portable-profiles.md)
更新 103：通用动态任务、独立专家配置、全角色在两层深度内委派，核验入口简化为模式与可选高级参数，默认五轮。
已实现并完成离线定向回归（99 通过），实际范围与验证记录见 104；旧 Run 保持原规则快照。

[102 H2 动作最多五次补正](changes/102-h2-five-action-corrections.md)是当前恢复次数规则：
新 Run 首次生成后最多补正五次，同决策仅共享一次额外传输重发；原模型、预算和截止时间不变。
旧快照仍为一次。该规则覆盖下面历史说明中的“一次补正”，不改写历史验收结果。

[2026-10-22 主 Agent 与工具链路核查](proposals/h2-tool-agent-recovery-audit-20261022.md)
逐项解释半截 JSON、错误参数、工具不可用、结果进入上下文及 Codex/框架对照。
本轮定向离线复核 18 项通过；只新增核查说明，恢复策略建议尚未确认，不改变运行代码。

[101 角色与 Prompt 资产外置](changes/101-h2-external-agent-rules.md)记录已确认的规则/执行解耦：
独立角色与指令文件、简短主角色目录、Run 规则快照及兼容边界。先设计后实现，
不增加重试、自动换模型或金融输出门控；[实际验证记录](acceptance/h2-external-rules-20261022.md)
保存定向 346、H2 1272/7 跳过、金融 1639/30 跳过及网页进程仍待重启的边界。

[2026-10-22 规则资产与上下文装配讨论稿](proposals/h2-agent-rules-and-context-assembly-discussion.md)
解释当前失败处理、角色 Prompt、子任务输入与结果交接，并保留“主 Agent 定目标/验收，
Runtime 装配授权上下文”的候选方案。**仅供讨论，未冻结、未改运行代码，不替换现有基线。**

[2026-10-26 Agent Profile 与分析-核验协作方案](proposals/h2-agent-profile-and-review-loop.md)
提出“静态 Agent Profile + 动态任务简报”及 Analyst 可选委派 Verifier 的逐篇研究闭环，
已获用户批准并形成 [103 H2 Profile 与核验闭环契约](changes/103-h2-agent-profile-and-review-loop.md)。
该契约补充 013/101，不改 Thread/Task/Attempt 和预算基线；实现进度及未覆盖范围见 103。

[100 H2 网页主入口](changes/100-h2-workbench-entry.md)将 `/workbench` 默认接到 H2。
这是用户本次明确要求的入口切换，覆盖历史变更中“默认 1.0 不切换”的阶段边界；
不是把旧同步循环包装为 H2。网页 Trace 必须来自实际 H2 Run/Task/Attempt。
已完成接入；[网页接口契约](harness-v2/workbench-contracts.md)单独保存 HTTP 与投影表定义，
[本次接入验收记录](acceptance/harness-v2-workbench-entry-20261021.md)保存 155 项离线通过和
一次真实网页父子任务完成的证据及限制。真实 Redis、业务质量与多人并发不随入口接入宣称通过。

[097 修复后固定业务复验](changes/097-h2-business-post-context-protocol.md)登记原30题，
28题可执行、2题保留环境限制；DeepSeek/FakeRedis，冻结后逐题单次执行。
不覆盖094失败记录，不把代码回归或答案交付冒称业务质量通过。
[098 业务复验启动环境修正](changes/098-h2-business-runner-environment.md)记录097在付费前因
私有目录未加载项目 `fakeredis` 依赖而停止；无模型调用、无扣费。新批次只修正启动命令，
不改代码、模型、预算或业务设计。

[096 作者目录统计兼容补齐](changes/096-h2-author-catalog-parity.md)复用旧版已有的作者、
篇数和最新日期统计，补齐H2的article.list返回；全库统计与子任务已返回授权样本明确区分，
不改变选文、研究流程或预算。已先记录并复现：定向337通过，H2 1236通过/7跳过，
默认金融1638通过/30跳过；真实效果另按097复验。

[095 上下文重复副本去重](changes/095-h2-context-payload-deduplication.md)修复模型输入
中的材料/动作回执/兼容视图重复；保留完整单份内容、所有句柄和原始审计。
H2 1229 passed/7 skipped，默认金融 1638/30 skipped；只读 Q01 回放估算输入减少 60.92%。
这不证明远端报错原因、模型语义或整套业务已通过；094 的原始失败和未运行题保留。
实际结果与问题分类见[业务批次和上下文修复报告](acceptance/harness-v2-business-context-20261021.md)。

[094 完整业务 MVP 复验](changes/094-h2-full-business-mvp-protocol.md)保留全部 30 题，
固定 DeepSeek、原输入快照、三类已批准适配与现有预算，各执行一次；
Q29/Q30 保留隔离主机/故障注入的限制。先冻结，结束先存首次完整性，再做分层验收；
不自动评分、不加金融输出门控，也不把单次交付当作语义或商业化通过。

[093 研究时钟传递](changes/093-h2-run-research-clock.md)补齐已确认设计的漏接：
研究日写入 Run 的不可变 Root Brief，并传给主、子任务及恢复后的模型输入。
相对时间基于同一研究日，发布日期、来源抓取时间和主机时间分别保留；不改选文与审核规则。
验收脚本实际传入冻结日期，不再只在记录中注明。离线 H2 1219 passed/7 skipped、
默认金融 1638/30 skipped、配置 10/10；真实模型时间推理与真实 Redis 尚未验证。
命令外壳的 SSL 警告和中间测试 fixture 失败均保留，不将离线结果冒称业务全通过。

[091 Personal Context Adapter](changes/091-h2-durable-personal-context.md) records
the optional durable Wiki/preferences/holdings integration. Final offline:
H2 1201 passed/7 skipped, default finance1638/30 skipped, configuration10/10.
[092](changes/092-h2-personal-context-live-mvp-protocol.md) separately freezes
Q23/Q24 once each with DeepSeek/FakeRedis; offline success is not live acceptance.
The batch has ended: both delivered, 68 physical requests, all frozen hashes
match before report edits. [实际报告](acceptance/harness-v2-personal-context-20261021.md)
separates source integration from Q24's missing article grants, semantic
overstatement and unresolved clock/review/environment evidence.

[技术问题 MVP 与剩余边界](acceptance/harness-v2-technical-mvp-20261021.md)区分协议故障、
模型语义、已有接入缺口和未定设计，不把离线通过当作整体验收。
[087 框架借鉴的字符串修复](changes/087-h2-framework-informed-json-escape-repair.md)给出
Pi/DeepSeek Harness 固定源码与适配范围；[088 诊断边界](changes/088-h2-action-repair-diagnostic-boundary.md)
只修正首对象的计数和原始尾部长度。多对象边界以 [083](changes/083-h2-bounded-action-json-recovery.md)
为准；026/028 的可选状态增量已实现，不因审核元数据缺失阻断正文交付。

[089 行情持久化适配](changes/089-h2-durable-market-adapter.md)补齐已有的行情能力接入：
复用旧版目录、抓取、指标计算和分页；快照写入不可变 Artifact 后可跨 Worker 恢复。
显式绑定和工具授权缺一不可，默认仍离线；不改变金融分析、预算和权限边界。
抓取时间与快照生成时间分别保留，市场数据不是因果验证。H2 1174 passed/7 skipped、
默认金融 1638/30 skipped、配置 10/10；范围与原始证据见 089，不当作金融语义验收。
离线通过后的单条 Q03 真实路径验证，范围冻结于 [090](changes/090-h2-market-live-mvp-protocol.md)；
实际为 9 次成功请求、67.37 秒交付，数值/日期与保存数据一致；仅 DeepSeek/行情工具/FakeRedis。
review/coverage 元数据仍不完整，不以单条交付替代金融质量或商业化验收。

[079 公开来源最小接入](changes/079-h2-public-source-adapter.md)先记录 RC09 的工程补齐范围：
显式授权才复用旧版安全检索/网页读取，搜索只是线索，公开来源与作者文章状态分离。
行情、Wiki/持仓、审核与收尾设计不随之改变；实际实现和验证结果单列。
[081](changes/081-public-url-secret-scan-mvp.md)记录 R23 暴露的 URL 凭证扫描误报：普通公开分页
参数允许持久化，认证参数和密钥模式仍拒绝；不关闭通用安全检查。

[R22](acceptance/harness-v2-live-acceptance-r22-20261020.md) 已单次完成真实两层委派与来源回传：
31 次调用、358.62 秒、三个 Task 均成功；专用补正修好全部 12 项锚点，正文不变。
仍有条件加强、仓位限定遗漏和 Trace 归属混写；结构成功不是语义全通过。
[078 JSON 字符串控制字符规范化](changes/078-h2-json-string-control-normalization.md)针对本轮
捕获的裸换行故障已先设计后实现，不放宽其他 JSON/权限或重试边界。H2 与状态更新合计
1059 passed/7 skipped，默认金融 1638/30 skipped，配置 10/10；净增 89 项用例。
原稿只读回放保留全部解码字段，但仍有 13 项锚点待补正；没有新付费调用或语义全通过声明。

[077 补正契约真实复验](changes/077-h2-patch-schema-live-revalidation.md)冻结 076 后的新 R22：
原 N01 指令、DeepSeek/FakeRedis、两层委派，个人 120/60、整树 600 次/60 分钟，仅一次。
先保存运行结束完整性，再分别报告补正动作、来源链和语义；未触发不能算通过。

[076 补正完整动作契约](changes/076-h2-anchor-correction-action-schema.md)已按最小技术修复实现：
只给锚点补正专用 final Schema 与完整示例，不自动包裹裸 JSON、不加重试、不改金融流程。
R21 实际两次补正分别为孙任务裸补丁拒绝、一级 6 项全部修好；结构修好不代表原文证据补齐。
076 后 H2 **970 passed / 7 skipped**、默认金融 **1638 / 30 skipped**、配置隔离 **10/10**；
新增 7 项用例；随后 R22 补上专用 Schema 的一次真实触发证据，仍不能宣称真实成功率已提升。

[074 委派引用视图修复](changes/074-h2-delegated-reference-views.md)处理 R17 深度二的既有契约缺陷：
返回的来源 ID 只作溯源，可用句柄仍是授权的子结果或本任务 wrapper；不扩大原文权限，不重复读文。
先复现和实现，再分别记录代码、真实模型与环境证据；整体基线验收尚未完成。
离线回归通过后按 [075 固定复验](changes/075-h2-reference-view-live-protocol.md)运行新的单条两层委派，
只检验引用交接与完整正文保留，不修改 R17/R20，也不将未触发的补正分支计作真实通过。
[R21 结果](acceptance/harness-v2-live-acceptance-r21-20261020.md)：25 次 DeepSeek 请求完成 Root 交付，
两个子结果为 partial；调度/账本/失败保留通过，模型 DSML/XML 与裸补丁外壳错误仍待技术稳定性优化。
074 的完整离线结果为 H2 **963 passed / 7 skipped**、默认金融 **1638 / 30 skipped**，配置 **10/10**；
私有来源反例没有在 R21 触发，不能用本轮交付替代该分支的真实模型验证。

[当前问题归因](acceptance/harness-v2-root-cause-review-20261020.md)逐项定位 R20 内容错误的首发环节：
格式补正重生成正文并新增错误，Root 随后沿用；另区分深度二引用缺陷、验证记录衔接、
缺失业务适配、传输故障与待验证环境。
[072 证据交接与定向验证草案](changes/072-h2-evidence-handoff-and-review-proposal.md)是下一轮讨论入口，
其余部分仍待讨论；其中终稿锚点补正先按用户本轮“常规技术借鉴、复杂问题 MVP”的授权，
细化为 [073 框架借鉴与最小补正](changes/073-h2-framework-informed-anchor-repair.md)。
该分支冻结正文、只定位引用，不重做金融分析；不把结构修复等同于语义正确。
073 已实现：新增 38 项用例，最终 H2 **954 passed / 7 skipped**、默认金融
**1638 / 30 skipped**、配置隔离 **10/10**；R20 原候选无模型回放保留全文及候选答案，
未能修好的绑定明确披露。没有新增付费测试；金融判断、两层句柄和真实 Redis 仍需后续处理。

[070 终态失败诊断交接](changes/070-h2-terminal-failure-diagnostics.md)修复一个既有契约漏接：
Worker 曾只保存 `contract_error` 而丢失子任务的具体校验原因。现在主 Agent 的状态/等待上下文
可收到当前 Attempt 的脱敏诊断，失败结果仍不作为有效证据，不增加重试或预算。
最新离线回归为 H2 **916 passed / 7 skipped**、默认金融 **1638 / 30 skipped**、配置 **10/10**。
独立的单次真实模型复验按 [071](changes/071-h2-terminal-diagnostic-live-protocol.md) 执行，
不回写 R19，也不以离线通过替代研究内容或真实 Redis 验收。

[R20 结果](acceptance/harness-v2-live-acceptance-r20-20261020.md)：主12次、子13次，合计25次，
453.79秒完成“六篇全文与尾页读取→子结果返回→主任务完整读取→综合交付”。独立预算、
来源交接及有界补正重发均通过审计。终态失败诊断本轮未触发；内容仍有把必要条件的不确定性
误说成矛盾、跨文章前提归因过强的问题，审核/覆盖元数据也未补齐，整体业务验收仍未完成。

当前预算设计以 [067 独立调用上限](changes/067-h2-independent-call-limits.md)为准：
主任务自身 120 次、每个子/孙任务自身 60 次、整轮合计 600 次、60 分钟。
Agent 不再申请调用次数；子额度不预占，实际请求分别计入自身与整轮账本。
该设计已实现，验收证据见 067：H2 离线 **901 passed / 7 skipped**，默认金融
**1638 passed / 30 skipped**，配置隔离 **10/10**。模拟模型经实际 SQLite/FakeRedis
调度完成六篇阅读和尾页补读：子任务 9 次、主任务 4 次、合计 13 次并完成交付与来源回传。
本轮未付费复跑、未迁移真实数据库；历史批次保留其原预算与结果，不视为新预算下的实测。

新预算下的单题诊断已冻结于 [068 Q08 复验协议](changes/068-h2-q08-independent-budget-revalidation.md)：
只复跑 Q08，使用 DeepSeek、FakeRedis 和 R17 的隔离语料快照；新建账本，
按 Root 120 / 每个子任务 60 / 全树 600 / 60 分钟执行。
[R18 结果](acceptance/harness-v2-live-acceptance-r18-20261020.md)：215.22 秒、18 次调用完成交付，
18 项结构检查通过；本轮未派子任务，不能算子任务 60 次上限的真实路径验证。
三次格式错误各补正一次成功；审核为空、覆盖元数据滞后，完整业务验收仍未完成。不改写 R17。

随后按 [069 定向诊断](changes/069-h2-child60-live-diagnostic.md)单独触发子任务，
[R19 结果](acceptance/harness-v2-live-acceptance-r19-20261020.md)：主 9 次、子 2 次、合计 11 次，
子任务确实有独立 60 次上限，20 项结构检查通过。子任务首响应用 DSML，唯一补正的证据文字
又未逐字对应正文，因契约错误停止；尚余 58 次，属于协议问题，不是预算耗尽。
主任务诚实输出失败说明，六篇读文与结果回传未通过。未加重试或修改既有契约。

最新定向复验见 [R17 报告](acceptance/harness-v2-live-acceptance-r17-20261020.md)：
Q08/Q15/N01 各一次，三题均交付，共 75 次实际 DeepSeek 调用；补正输入隔离已在真实请求中核对，
单层围栏本轮未触发，仍仅有离线证据。Q08 的独立评审耗尽原申请预算；N01 已执行两层委派及
两次完整结果读取，但深度 1 使用了不可直接读取的内部来源句柄而返回 partial，证据链未完整
传回 Root。结构审计通过不等于整体质量或商业稳定性通过；后续应先明确可引用句柄与溯源 ID。

[065 补正输入隔离与单层 JSON 围栏](changes/065-h2-correction-input-isolation-and-json-fence.md)
已获用户明确确认，先记录设计再实现：拒绝输出全文仅留审计，补正依据真实执行上下文和有界
错误定位；H2 允许唯一外层 bare/json 围栏，不放宽 JSON、工具参数或权限校验。原预算、模型、
一次补正和独立物理记账不变。已实现并通过 H2 **888 / 7 skipped**、默认金融
**1638 / 30 skipped**、配置隔离 10/10；新增 63 项测试。新一轮付费验证按
[066 固定三题协议](changes/066-h2-isolation-live-protocol.md)执行，不回写 R14/R16 等历史结果。

最新全量目录复验见 [H2 R14 业务报告](acceptance/harness-v2-live-acceptance-r14-20261020.md)：30 题登记、20 题执行、17 题交付，共 403 次 DeepSeek 调用；Q08/Q09/Q15 因公共动作协议失败，9 题缺少业务接入、Q29 待隔离环境。20 题结构审计通过不代表内容通过：Q27 未读正文却分析模型编造的残文，多作者归因与条件对齐仍有错误。Q28 实际触发一次补正传输重发并恢复；未触发 058 的兼容包装分支。真实 Redis、并发负载和完整金融流程仍未验收。此前 [R13](acceptance/harness-v2-live-acceptance-r13-20261020.md) 的 18/20 交付及严重语义失败保留，不回写，也不把本轮说成总体稳定性提高。

[051 文章读取进度的版本边界](changes/051-h2-version-bound-article-read-progress.md)记录 R13 后修复的既有契约缺陷：旧版已读范围不能证明新版全文已读；保留旧证据，在新版补齐前标记部分读取。临时文章重新索引和检查点恢复已验证，H2 回归 **682 passed / 7 skipped**、默认金融 **1614 passed / 30 skipped**，配置隔离 10/10。本次没有新增付费测试，也未解决或覆盖 R13 的语义失败。

[052 证据账本历史保留](changes/052-h2-evidence-ledger-retention.md)移除另一处未获设计授权的截断：状态更新不再静默只保留最后 200 条证据绑定，正反关系和实际读取记录跨检查点保留；原有大小边界不变。该阶段离线回归为 **H2 690 passed / 7 skipped**、**默认金融 1621 passed / 30 skipped**，配置隔离 10/10，定向完整 Ruff 通过。没有新增付费测试；格式补正来源隔离仍需确认，不以此修复替代 R13 语义验收。

[053 读取索引与结果 envelope 保留](changes/053-h2-read-index-and-result-envelope-retention.md)继续修复既有契约内的两处静默裁剪：完整当前版本区间不再只保留最后 200 段；Root 的合法要点、缺口和提示不再只保留前 20 项，已通过实际 `result.get(summary)` 验证。该阶段离线结果为 **H2 701 passed / 7 skipped**、**默认金融 1631 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff 通过。本次不改诊断限长、研究策略、模型预算或默认入口，没有付费重跑，整体业务验收仍未完成。

本轮实时定向复验记录见[金融实时验收跟进报告（2026-10-19）](acceptance/finance-live-acceptance-followup-20261019.md)；它只报告 Q03/Q14 定向结果，不替代完整 30 条 Query 验收。

[054 可信任务身份交接](changes/054-h2-trusted-task-identity-context.md)修复另一处已确认契约的漏接：主/子模型的输入没有收到当前 TaskBrief 的身份字段。以白名单传递 `task_identity`，每个 Attempt 从可信 Brief 刷新，不暴露租约或扩大权限。最新离线结果为 **H2 712 passed / 7 skipped**、**默认金融 1631 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff 通过，新增 11 项检查包含新 Service 实例继续执行及实际账本身份比对。另将“金融链路如何进入收尾预留阶段”列为尚需设计的缺口，保持当前预算边界不变。本轮没有付费调用，不替代 R13 语义复验。

[055 运行记录路径边界](changes/055-h2-runtime-record-path-boundaries.md)修复隔离日志追加和孤儿清理对目录链接/硬链接检查不足的问题；隔离写入失败时保留同一队列消息待处理，安全落盘后才 ACK。13 项定向测试通过，最新 H2 回归 **722 passed / 7 skipped**、默认金融 **1631 passed / 30 skipped**、配置隔离 10/10。全部使用临时目录，没有付费调用，不代表 R13 语义问题已解决，也不声称能抵御恶意并发替换文件系统。

[056 用量导出的脱敏边界](changes/056-h2-exported-usage-redaction.md)修复数据库中的正常 Token 用量在导出时被误隐藏的问题：只保留五个已知字段中的非负整数，凭证和伪装成用量的异常值仍脱敏。最新 H2 回归 **739 passed / 7 skipped**、默认金融 **1631 passed / 30 skipped**、配置隔离 10/10，定向完整 Ruff 通过。本轮没有付费请求，不改旧批次结果；R13 语义失败、缺少业务接入和待确认的设计仍未解决。

[057 纯文本父上下文交接](changes/057-h2-text-parent-context-handoff.md)修复合法文本被误标不可用、并使子任务文章范围收集失败的问题。父文本可见但不增加文章授权，也不能替代原文证据；实际 SQLite/FakeRedis/模拟模型验证了越权拒绝、授权文章读取和新 Service 实例继续执行。新增 23 项检查，最新 H2 回归 **762 passed / 7 skipped**、默认金融 **1631 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff 通过。本轮没有付费调用，严格动作协议不变；不把确定性交接修复当作 R13 语义验收通过。

[058 兼容请求的输入授权边界](changes/058-h2-legacy-envelope-input-grants.md)修复旧 child-request 包装替换可信 TaskBrief 材料清单的问题：保留单独授权的父上下文和文章，包装内引用不能增加或删改权限。新增 12 项初始化测试及两条实际 SQLite/FakeRedis/模拟模型继续执行用例，合计 14 项；H2 当时回归 **776 passed / 7 skipped**、默认金融 **1631 passed / 30 skipped**，配置隔离 10/10、Ruff 通过。该修复验证无付费调用；不替代语义、真实 Redis 或待确认设计。

[059 R14 付费业务复验协议](changes/059-h2-business-r14-after-input-grant-repair.md)冻结本次修复后的 30 条 Query 复验边界：DeepSeek 固定模型、FakeRedis、可执行题各一次、每题整树 120 次调用上限，结果与 R13 分开保存。

[060 首次成功读取的版本误报](changes/060-h2-first-successful-read-version-warning.md)记录 R14 发现的既有契约缺陷：初次参数失败不是旧原文观察，之后首次成功读取不应报源版本变化；保留真正版本变化、失败观察和历史证据。批次先完成哈希审计，再单独记录离线修复，不改格式补正和金融判断策略。

060 已修复并完成离线回归：新增 9 项检查，完整 H2 **785 passed / 7 skipped**，默认金融 **1638 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff 通过。6 项先复现误报再通过，另外 3 项保护真实版本变化；无额外付费重跑，不把该确定性修复视为 R14 语义问题已解决。

[061 队列身份不匹配的隔离处理](changes/061-h2-outbox-mismatch-quarantine.md)修复合法 JSON 消息与 SQLite Outbox 不符时异常逃逸的问题，并让特殊数字和超长数字按原协议拒绝：脱敏审计成功后才 ACK，不再打断后续真实任务；数据库、权限或落盘故障仍保留待处理消息。新增 20 项用例，定向模块 50 项通过；完整回归结果以该变更记录为准。不改模型补正、金融判断、预算或默认 1.0。

061 最终离线回归：完整 H2 **805 passed / 7 skipped**，默认金融 **1638 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff 和差异检查通过。20 项新增用例中 16 项先复现失败，另外 4 项守住既有负面或兼容行为。无新增付费调用；这不是 R14 语义质量或真实 Redis 验收通过。

[062 委派阅读的来源交接](changes/062-h2-delegated-source-lineage.md)修复子任务独立读文后父任务仍记录未读的问题：通过结果的紧凑来源 section 保留真实作者、日期、版本、范围和原读取 Task，最终是否使用仍由父任务实际引用决定。不要求重复读文，不继承子任务的覆盖/审核判断，也不把元数据返回标记为父任务已读全文；回归证据单列于该记录。

062 最终离线回归：H2 **825 passed / 7 skipped**，默认金融 **1638 passed / 30 skipped**，配置隔离 10/10、定向完整 Ruff/差异检查通过。新增 20 项检查，最初 2 项先复现失败；最终另复跑 2 项加强后的多作者/日期/版本断言。无新增付费调用，R14 的业务结果及尚待确认的策略不变，整体目标未完成。

[063/R15 来源交接定向复验](changes/063-h2-delegated-lineage-live-protocol.md)先冻结单篇、双作者两条“主任务定位、子任务独立读文”的诊断，DeepSeek/FakeRedis，每题最多 120 次/60 分钟、整批 240 次，不切模型或追加重跑。该诊断不等于完整业务集或商业稳定性验收。

该批次现已结束，见 [R15 验收报告](acceptance/harness-v2-live-acceptance-r15-20261020.md)：两题均交付，13/33 次调用、208.72/600.11 秒，三个子任务均成功，主任务在交付前完整读取三份分析，未重复读取原文。真实作者、日期、版本、范围与原读取任务均正确交接；每题 12 项结构检查及适用的来源诊断通过。冻结的 412 项文件和 898 项快照一致。但内容仍有条件、时间与共识的过度归纳，两题都没有结构化审核；不能称为全量内容验收通过或稳定性已证明。默认 1.0、生产入口、真实 Redis 和待确认策略保持不变。

[064/R16 两层递归定向协议](changes/064-h2-depth-two-live-protocol.md)补齐基线 C07 尚无真实模型证据的深度二路径：主任务派一级、一级派二级、仅二级读原文、分析与原始来源逐级回传。先冻结后运行一题，DeepSeek/FakeRedis，整树最多 120 次/60 分钟；不追加重跑、不修改金融策略或默认入口。

R16 已结束，见 [R16 验收报告](acceptance/harness-v2-live-acceptance-r16-20261020.md)：2 次调用、6.55 秒，首响应附带虚构 XML 工具结果，唯一补正仍为非法 JSON，Root 按原规则失败；没有执行工具、创建子任务或交付答案。因此两层递归实际链路未触发，不记通过。12 项结构审计通过，414 项冻结文件、898 项快照和 3 个私有辅助脚本一致。未追加重跑，输入隔离和围栏处理仍待用户确认；R15 的成功结果不回写。

### Harness 2.0 调度契约

2026-10-19 补充：[R3 故障与状态交接验收](acceptance/harness-v2-fault-acceptance-r3-20261019.md)
记录本轮故障矩阵验证、工具失败归因及验证边界；[028](changes/028-finance-state-update-delivery-fixes.md)
记录已确认协议内的终稿保留和审核/交付状态交接修复。此前 `h2-code-fix-r1` 金融批次实际
走旧版入口，不是 H2 真实模型验收。真实 Redis 暂无服务，按用户决定后置，不能宣称已通过。
[029 显式 H2 实时验收入口](changes/029-h2-live-acceptance-entry.md)定义独立入口、版本留档、
模型/后端显式选择和完整答案导出；诊断题、全量验收与真实 Redis 验证分别记录。
[R4 实际 H2 诊断](acceptance/harness-v2-live-acceptance-r4-20261019.md)为三题零交付，
原因及修复见 [030 工具契约交接与一次格式补正](changes/030-h2-action-contract-handoff.md)。
用户已确认 Root 在同模型、原预算内最多补正一次格式；不是恢复无界重试或自动换模型。
[R5 复验](acceptance/harness-v2-live-acceptance-r5-20261019.md)记录一题交付、两题传输失败，
不能称为全套通过；[031](changes/031-h2-recovery-attempt-accounting.md)修复正常挂起/恢复
错误消耗失败重试名额的问题。剩余金融动作循环、结果拉取和真实 Redis 验证仍单独列明。
[032 动作后持久化继续](changes/032-h2-durable-action-continuation.md)记录用户确认的方案 A，
补充 Continue 事务和主/子共用动作。显式金融路径已接入普通工具、异步派发、两层研究子循环
和授权材料分页；该变更记录本轮验证与 R6 定向复验协议，不代表真实 Redis 或全部业务题通过。
[R6 真实模型诊断](acceptance/harness-v2-live-acceptance-r6-20261019.md)三题均交付，
但未调用研究子 Agent，覆盖/审核元数据仍有缺口，不能称为完整验收通过。
批次后修复原始读取引用未映射到文章使用状态的问题；离线修复不回写冻结运行结果。
[033 显式委派预算](changes/033-h2-explicit-delegation-budget.md)记录用户最新确认：
新 H2 任务显式 spawn 申请额度，旧 delegate 在选择前披露原兼容预算，不静默调整。
[034 真实进程故障验证](changes/034-h2-process-fault-validation.md)定义隔离 SQLite 的
进程终止、事务恢复和跨进程竞争验证；与真实 Redis、付费模型及商业负载验收分开。
[035 恢复预算与事务边界](changes/035-h2-resume-accounting-guards.md)修复恢复时的收尾额度保护、
同 Thread 占用拒绝、实际 Task 记账和 HTTP 错误分类，并防止子任务提交过期的父模型策略。
对应 H2 离线回归 478 passed / 7 skipped，不替代真实模型或真实 Redis 验收。
消息路由补验后最终为 485 passed / 7 skipped，默认金融回归 1606 passed / 30 skipped。
[036 显式 spawn 实时验收协议](changes/036-h2-explicit-spawn-live-protocol.md)记录用户授权的
两条 DeepSeek 定向测试，各一次；先冻结后运行，不自动换模型或追加重跑。
[R7 显式委派验收](acceptance/harness-v2-live-acceptance-r7-20261019.md)已完成：两份 Root
答案交付，但五个子任务没有成功返回，两题完整链路均未通过，不能以交付替代验收。
[037 子任务动作契约与预算错误](changes/037-h2-child-action-envelope-and-budget-errors.md)
先记录实现缺陷，再记录用户单独批准的子 Agent 同模型、原预算内一次结构/锚点补正。
这些是批次后修改，离线结果与真实模型证据分开，原始 R7 结果不回写。
037 后 H2 回归为 **505 passed / 7 skipped**；默认金融回归 **1606 passed / 30 skipped**，
配置隔离 10/10。未追加付费复验，真实 Redis 仍未验证。
[038 模型撤销与发送边界](changes/038-h2-model-revocation-boundary.md)修复预约后、发送前撤销模型仍可发送及权限错误归因；
[039 跨动作结果读取审计](changes/039-h2-result-read-coverage.md)修复同 Task 换 Attempt 后的连续分页累计，隔离用户导出与新任务；
[040 结果按需展开](changes/040-h2-on-demand-result-expansion.md)移除金融桥接的隐藏全文回读，补齐 Worker 的实际模型容量检查以保留短结果自动交接。
三项均对照既有契约先记录再修复；最新回归与剩余验证见下方累计报告，不回写 R7。
038-040 后最终 H2 回归为 **536 passed / 7 skipped**，默认金融回归为 **1606 passed / 30 skipped**；
配置隔离 10/10。两条新的 DeepSeek 复验已获授权，但 120 次调用是整批还是每题仍待确认，尚未启动付费调用。
[041 未发送预约结算](changes/041-h2-confirmed-unsent-settlement.md)补验取消/租约失效后的真实请求记账：确认未发出的预约可由原执行者结算，已开始的请求仍保留计费；最终 H2 回归 **558 passed / 7 skipped**，默认金融回归 **1606 passed / 30 skipped**，配置隔离 10/10。仍未启动新的付费调用。
[042 验收调用额度参数](changes/042-h2-acceptance-call-ceiling.md)为验收入口增加显式的每题整树上限，主/子、补正与重试共用原账本；不改生产默认额度，也不代替待确认的两题 120 次分配。入口定向离线检查 37 项通过，最终完整 H2 回归 **578 passed / 7 skipped**。默认金融 Runtime 未改动，最近完成的兼容回归仍为 041 后的 **1606 passed / 30 skipped**，未当作本次重复执行。
[043 有界委派复验协议](changes/043-h2-bounded-spawn-revalidation.md)记录用户已确认的额度：S01/S02 各一次、**每题整树最多 120 次，整批最多 240 次**，DeepSeek only，FakeRedis。它解决上文历史阶段的待确认项；新协议和快照冻结后才运行，不复用 R7 的 600 次协议，不自动换模型或追加付费重跑。
[R8 有界复验结果](acceptance/harness-v2-live-acceptance-r8-20261019.md)已结束：17/36 次调用、共 53 次；四个子任务两个返回成功，但两题 Root 均未交付，完整链路 0/2，不能称为通过。[044 动作校验反馈](changes/044-h2-action-validation-feedback.md)记录批次后修复：明确 input-only key 的位置、转交实际字段诊断，不增加重试或放宽校验；离线证据与付费结果分开。本轮授权已用完。
044 最终离线回归：**H2 587 passed / 7 skipped**（210.12 秒），**默认金融 1606 passed / 30 skipped**（115.57 秒，52 模块）；配置隔离 10/10、定向 Ruff/diff 检查通过。没有追加付费调用；真实 Redis 和修复后实时完整链路仍未证明。
[045 子任务上下文契约对齐](changes/045-h2-child-context-contract-parity.md)补齐子 Prompt 的动作 key/引用说明，并保留 wait 观察中的原预算、截止时间、分发和 Attempt 状态；H2 **609/7 skipped**、默认金融 **1606/30 skipped**，均为新执行的离线结果。另行授权的 [046/R9](changes/046-h2-post-feedback-live-protocol.md)现已结束，见 [R9 报告](acceptance/harness-v2-live-acceptance-r9-20261019.md)：S01 14 次调用、229.07 秒，调度/回传链路通过，内容基本可用但有一处批评措辞偏满；S02 20 次、304.01 秒，格式补正遇传输错误后按当前策略停止、无终稿。总计 34 次、未超 120/240，389 个冻结文件一致；总体仍未通过，授权已用完，不覆盖 R8。
048/049 R10 记录补正传输重发设计及一次固定复验：S01 16 次调用、264.72 秒，S02 37 次、763.82 秒，共 53 次，DeepSeek/FakeRedis，2/2 Root 交付。S02 有 2 个成功子结果被 Root 完整读取，另 2 个子任务因模型契约错误失败；因此是部分多作者证据，不是所有子任务通过。两题都没有触发三次调用的传输重发分支，审计明确记为 `not_triggered`；048 仅有离线分支验证，真实 Redis 和生产可靠性仍未证明。详见 [R10 报告](acceptance/harness-v2-live-acceptance-r10-20261019.md)。
R11 对 S02 做了新的定向复验：Root 与 2 个 analyst 子任务均成功，27 次调用、401.2 秒，两个子结果均被 Root 完整读取后交付；4 次 Root 契约错误在一次语义补正内恢复。该批次证明了有界恢复和完整交付，但不证明原始模型遵约率稳定；传输重试分支仍为 `not_triggered`，真实 Redis 和语义正确性仍未证明。详见 [R11 报告](acceptance/harness-v2-live-acceptance-r11-20261019.md)。
R12 对 Q14 做了新的单 Root 定向复验：DeepSeek 使用 17 次调用、141.19 秒完成 2,977 字答案；2 次首响应契约错误均由一次同模型语义补正恢复，传输重发分支未触发。答案披露了样本范围、共识、分歧、未讨论项和不确定性，但仍保留 `manual_review_required` 与 `incomplete_metadata`。详见 [R12 报告](acceptance/harness-v2-live-acceptance-r12-20261020.md)。

[050 H2 业务验收记录与范围](changes/050-h2-business-acceptance-records.md)规定完整 30 题矩阵的真实入口、前置能力检查、冻结校验与中断导出。R13 已结束，详见上方报告；误用旧 Chat 入口的私有结果单独保留并明确排除，不作为 H2 成绩。不能将未接入的 10 题或已交付但有实质语义错误的题记为通过。
当前累计的 C01-C18 实现证据、已修缺陷与剩余验收统一见
[H2 当前验收状态](acceptance/harness-v2-current-acceptance-20261019.md)；早期报告保留其当时结果。

[047 终态追问上下文交接](changes/047-h2-follow-up-context-handoff.md)记录 R9 后发现的
既有契约实现遗漏：将新成功标准、实际追问目标和旧结果句柄传入金融模型上下文，
不复用旧研究任务、不自动读取旧结果。离线验证与 R9 的原始实时结果分开；没有追加付费复验。

以下三份文档及 R1/R2/R3（等待挂起、终态追问、失败依赖）已获用户确认，冻结为 **H2-CONTRACTS-2026-10-18-v1**；金融状态增量协议另以 **finance.state_update.v1** 冻结，见 026。阅读顺序为接口流程 -> 存储事务 -> Redis 投递 -> 金融状态交接；代码与未完成项在 014/026 单独映射，不把设计确认等同全套实现。

| 文档 | 唯一负责的内容 | 当前验证 |
| --- | --- | --- |
| [调度接口与验收](harness-v2/interface-contracts.md) | spawn/wait/status/send/cancel/resume、result.submit/get、内部 Worker/Adapter、兼容与故障验收 | Repository、Worker、单次模型执行/可信能力和显式注入 HTTP 边界已离线验证；默认 1.0 与生产接入保持不变 |
| [SQLite 存储契约](harness-v2/sqlite-schema.md) | 18 张表、外键/唯一约束、状态迁移、原子事务和预算账本 | 显式迁移与临时 DB 故障/并发验证；未迁移真实数据库 |
| [Redis Streams 协议](harness-v2/redis-protocol.md) | 消息格式、Outbox、claim/ACK、PEL 回收、Redis 丢失后的重建 | FakeRedis、故障注入及 20 轮脚本链路通过；真实 Redis 联测未运行 |

013 继续是已确认语义基线；三份契约不是 1.0 金融研究接口的替代品。历史阶段中，014 底座验证为 95 passed / 2 skipped；[015 模型执行与可信 Worker 能力](changes/015-harness-v2-model-execution.md)新增 78 项通过，020-024 阶段调度定向累计 196 passed / 2 skipped，当时默认 1.0 回归为 1589 passed / 30 skipped。以上均为阶段记录，不代表当前完整验收或商业化成功率。此后金融业务状态与动作恢复的接入以 026/028/032 为准；旧环境检查结论与当时限制保留在[2026-10-18 代码级报告](acceptance/harness-v2-code-acceptance-20261018.md)，不覆盖后续证据。

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
