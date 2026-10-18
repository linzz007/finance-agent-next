# 双 Wiki 首轮独立质量审阅

审阅日期：2026-09-10。对象：`shared_discovery_initial_20260910` 的 SD03、SD04。

本轮仅只读已有回答、报告、RunArtifact、Trace 和检查结果；唯一新增文件为本文。没有调用 Workbench API、模型、来源连接器或外网，没有更改运行产物、运行时和机械验收结果。下文的“引用核对”指与**已保存来源快照**一致，不代表重新访问原站、核验论文全文、独立复现实验或确认政策法律效力。

## 结论

| 对象 | 来源与主题 | 回答质量 | 独立结论 |
| --- | --- | --- | --- |
| SD03 企业 | 最终规划回到金融数据、投研与数据治理；但相关性筛选出现业务线索漏选和宽泛词误配，来源健康度不足 | 唯一项目中文分析未完成，回退为巨量原文和通用建议 | **不通过业务质量验收**。不是仅需格式美化 |
| SD04 个人 | 三篇论文确实讨论技能演化、经验固化或一致性反馈；保存摘要支持所引文字 | 摘要和事实边界基本可用，但三项建议、验证指标高度模板化，缺少个性化学习优先级及两小时计划 | **部分可用，仍未通过完整质量验收** |
| 双画像对照 | 同一问题、日期和 17 个来源配置 ID；实际检索主题不同 | 内容不同部分也受来源成功/失败差异影响 | 可证明共享配置和目标驱动的规划差异，**不能把全部结果差异归因于 Wiki** |

`mechanical_pass=true`、`status=done`、产物可下载均不改变上述结论。SD04 的 `analysis_status=done` 也不是独立质量认可。14 条全量查询及后续追问尚未在本文完成验收。

## 新增发现

### F1 [P1] 企业筛选存在业务相关性倒挂

SD03 的唯一入选 `event_066` 是数字化绿色化协同转型政策。它包含碳数据、数据质量和开放共享内容，可以作为**条件性的碳数据/绿色投研观察线索**，不能仅因出现“数据质量”就证明覆盖“金融数据治理与数据质量”。`discovery_plan.topic_terms` 将无金融限定的“数据质量”与金融短语并列，最终该主题被标记为已覆盖；回答没有补上具体金融业务适用条件。

同批 `event_015` 的保存标题是 *Legora reviewed 41 documents in minutes with GPT-6 Astra*，摘要明确谈及 financial-review workflow 和文档错误检查。这至少是金融资料研究支持值得进一步核验的技术线索，却未进入唯一匹配集合。**不是要求强行推荐它或认可厂商效果数字**，而是目前未见解释为何该业务线索被排除、碳治理政策却通过。此问题在没有任何来源 403 的前提下也成立，不能只靠修复采集解决。

证据：[SD03 规划](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:154)、[已存在的财务审阅线索](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:3860)、[最终分析](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:34588)。对应 JSON 路径：`discovery_plan.topic_terms`、`structured_events[event_id=event_015]`、`analysis[event_id=event_066]`、`topic_coverage`。

复验应要求：保留“业务直接相关 / 条件相关 / 泛化同词”区别；给出每个入选项的具体业务桥接依据；对已在池中的显著业务线索保留可解释的排除原因。不要用“必须标题带金融”替代这项语义判断。

### F2 [P2] 请求仍残留已明确排除的默认 Agent 主题

SD03 的 `report_request.focus_topics` 是 `agent_harness, data_governance`，`excluded_topics=[]`，尽管 `ths_goal` 已明确“不默认追踪通用 Agent 开发技术”。规划模型最终输出金融主题是正向证据，但持久化请求与实际规划没有清晰区分“解析初稿”和“当前有效主题”。

这会误导审计与后续请求继承；在规划失败回退时还存在重新变成 Agent 日报的风险。**后者是风险推断，本次没有发生规划失败，不能说本次已按 Agent 主题检索。** 也不是要求中文模型主题与固定 ID 完全相等：问题是 `agent_harness` 的语义与用户明确边界相反。

证据：[SD03 report_request](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:85)。复验应查看有效主题及其来源，并单独覆盖无模型规划/规划失败场景。

### F3 [P2] 搜索候选混入明显疑似推广垃圾，清洗诊断未表现出来

SD03 的 Google News 返回 14 条，`event_048`、`049`、`051` 等出现博彩站、安全认证、知名品牌混搭的异常推广式标题。例如 `event_048` 标题含“pg电子官网”，`event_059` 明确含“在线真人赌博娱乐平台”。这里不能据标题断定被冒用媒体站点遭入侵，但足以认定需要隔离核验的可疑内容，不能当一般高质量新闻候选。

该 provider 仍为 `status=ok`，14 条全部保留，清洗中的 `invalid_count=0`、`excluded_count=0`，二次清洗也移除 0 条。它们本次**没有进入最终推荐**，因此不是已经发生的回答事实污染；新增风险在于“传输成功/URL 合法”被当成足够的候选质量信号。聚合器 URL 和标题尾部媒体名也不能证明原始发布主体。

证据：[SD03 搜索候选](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:5784)、[清洗诊断](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/02_run_artifact.json:11583)。复验应区分采集成功、候选可信程度、原始发布方是否解析，以及垃圾/标题级线索的剔除原因。

### F4 [P1，未发布草稿风险] 引文存在不等于政策摘要强度正确

SD03 两次分析调用共产生四条草稿，四条 `event_quote` 均可逐字对应保存政策文本，Wiki 引文也对应正确。但 Trace step 16 将原文“指导……主动披露”的表述概括成“要求生成式AI服务商披露”。这把引导性表述升级为义务式表达，当前证据不足以支持这种转换。

这条草稿因重复事件 ID 校验失败，**并未作为有效中文分析发布**。因此不能把它写成当前最终回答已作出强制合规结论；但修好重复 ID 后不能自动放行同样措辞。碳数据开放共享、金融业务适用范围、生成式 AI 服务商身份，也应分别核验，不可相互替代。

证据：[SD03 step 16](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/trace.json:6331)，`output_payload.payload.output.analyses[0]`。复验应分别检查“事实摘录一致性”和“摘要/影响推断强度”，不能只用字符串引用校验。

### F5 [P2] 政策回退回答仍使用 SDK 模板，业务问题没有得到回答

SD03 最终待确认项是“是否实际使用该版本、模型、SDK”，验证指标为“只读对照用例的预期、实际结果与证据完整性”。这些内容既未解释政策对金融数据/投研有什么值得跟进的变化，也没有提出碳数据范围、公开数据目录、行业适用对象等具体待确认点。`publisher` 为空，最终显示“发布主体：未标注”；保存正文却至少有网站来源与组织实施单位信息，发布网站、发文主体和适用对象尚未被区分。

“中文语义分析未完成”的披露是正确的失败边界，不应为了通过而删掉；需要补的是一段可用的、具体且有边界的业务研判，或明确没有足够证据形成建议。

证据：[SD03 回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/answer.md:196)。此项与已知的 37 KB 长回答相关，但即使把原文删短，模板化业务内容仍未解决。

### F6 [P2] Trace 汇总显著少计真实 provider 调用

| 对象 | 可见模型事件 | 确定性动作 | 有 provider 适配器/response/usage 的逻辑调用 | 汇总 provider_call_count | 汇总 unclassified |
| --- | ---: | ---: | ---: | ---: | ---: |
| SD03 | 5 | 1 | 4：规划 1、分析 2、最终汇总 1 | 1 | 3 |
| SD04 | 7 | 1 | 6：规划 1、分析 4、最终汇总 1 | 1 | 5 |

SD04 最终汇总还记录一次 `max_tokens` 截断重试；逻辑调用数与 HTTP 尝试数应分开。这里只根据保存 Trace 计数，不推算账单金额。Wiki 阶段规划调用确实可见；问题是汇总没有把这些调用归入 provider，用汇总做预算或成本比较会漏算。

证据：[SD03 模型汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/trace.json:8111)、[SD04 模型汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD04/trace.json:7859)。复验应要求 provider 逻辑调用、真实尝试、修复调用和确定性动作可分别对账。

### F7 [P2] Trace 来源汇总全零，与实际采集矛盾

两例 `source_summary` 的新闻/政策来源数、已启用来源数、快照数量、路径和时间均为零或空串，同时 `has_source_summary=true`。RunArtifact 实际保存 17 个来源状态，SD03 有 66 个事件、SD04 有 57 个事件。问题不是两例必须相同，而是“未汇总”不应以“0 个来源”的数据含义呈现。

证据：[SD03 来源汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD03/trace.json:8137)、[SD04 来源汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD04/trace.json:7887)。正文的“8 个来源成功、范围：配置新闻源”也没有直接交代 17 个总来源及 9 个失败，并把政策/论文共享池仍叫作新闻源；即使附录保留失败详情，面向用户的覆盖说明也应更准确。

### F8 [P2] SD04 有边界的条件句被误判，增加修复调用

SD04 step 15 的第一条已经有条件字段“仅当能获取全文方法细节与代码数据时”，影响句为“若成立……但摘要未证明适配本地单 Agent”；第二条也是“若需训练，则超出当前仅推理 API……”。`analysis_diagnostics.repair_attempts` 仍对两条报 `unconditional_impact` 并触发修复。

这不是在要求放宽真实的确定性收益断言。问题是语义上已经带条件和限制的表达被拒绝，修复后反而变成更通用的“可能影响……设计”。该现象与已知“同事件两条分析”是不同的问题。

证据：[SD04 step 15](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD04/trace.json:5785)，以及 RunArtifact 的 `analysis_diagnostics.repair_attempts[0]`。复验应加入真实条件句的正反例，不用是否包含某个固定开头判断全部语义。

### F9 [P2] SD04 相关论文选对了，但没有形成个性化学习决策

三篇的 `recommended_action` 完全相同，`verification_metrics` 也相同；只是复述“两小时、固定评测集、回滚条件”这些 Wiki 约束，没有具体安排一个实验或说明先读哪一篇。尤其第三篇的中文摘要只讲 53%/77% 的基线差异，遗漏摘要中已经给出的“定位不稳定轨迹步骤，再形成情景记忆/指导规则”的自进化机制。

保存的 SkillAdam 摘要还提供了代码仓库线索；可以明确“摘要列出仓库，尚未验证可访问性和复现条件”，而不是把已知线索与未知依赖全都压成同一段“待查全文”。不要求当前执行代码，也不要求用两小时完整复现论文。

证据：[SD04 三项建议](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD04/answer.md:30)、[结构化分析](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_initial_20260910/cases/SD04/02_run_artifact.json:32994)。复验应至少给出一个与特定论文机制相关的阅读/手工设计任务，包含固定输入、候选改动、对照和停止/回滚条件；不要承诺实际性能提升。

## 引用与事实边界

| 对象 | 已核对内容 | 判断与边界 |
| --- | --- | --- |
| SD03 初次分析两条、修复分析两条 | 四条政策引文、对应 Wiki 引文均为保存文本子串 | 无引文凭空生成迹象；F4 表明即使引文准确，摘要强度仍可能不当；四条草稿都未成为有效发布分析 |
| SD04 SkillAdam | 优化记忆用于累积问题和历次尝试；摘要另有波动驱动编辑预算、冻结模型、仓库线索 | 中文摘要核心方法有支持；不能据此保证当前项目可本地复现 |
| SD04 Experience Funnel | 快速文本状态适应与慢速参数策略固化的交替循环 | “普通推理 API 未必足够”的限制合理；不能把它简化成只改 memory 提示即可完整实现 |
| SD04 Consistency Gap | 保存摘要确有五次全过 53%、单次均值 77% | 回答明确“摘要称”，未把数字当用户项目收益；缺少自进化机制介绍，详见 F9 |

SD04 三条最终 `event_quote` 和 Wiki 引文均已做逐字核对；发布日期有 UTC 原值并在回答换算为 `+08:00`，都落在本轮窗口内。三篇仍仅是摘要级证据，没有本文执行的全文阅读、代码运行、热度核验或独立复现。

企业回答未声称同花顺已采用、已获益或具备某个内部技术栈；个人回答未编造自身已实现 90% 提升。两例也未混用对方 Wiki 事实。这些是明确保留的正向边界，不能因其他失败一并否定。

## 已知修复项

以下按用户说明已由其他工作负责，本审阅不认领实现：

| 已知项 | 本批证据 | 复验不能遗漏 |
| --- | --- | --- |
| Release 补充请求 403 导致有效 RSS 丢弃 | 多个 Release 来源有 raw_count，但结果为 0；错误含 403 或超时 | 区分原始 RSS 成功与补充失败，不把二者合成“完全无资料” |
| 政策正文导航污染 | SD03 原文含首页、收藏、备案号、CMS 页尾；二次移除为 0 | 验证正文段落保留、导航消失、引文位置仍可追溯；计数对账不等于文本干净 |
| 同事件重复分析 | SD03 两轮都返回同一 event_066 的两条分析并失败；SD04 event_009 首轮也发生，后修复成功 | 唯一事件约束与多条引用的表达方式明确；去重后仍要审 F4 的语义强度 |
| 长聊天透传 raw impact chain | SD03 answer.md 37,506 字节，报告 Markdown 57,799 字节 | 限制主回答和影响链的引用范围，但保留可追溯原文附件；删长文不能替代 F5 的业务分析 |

## 双画像对照限制

两例请求均为 `/report 生成一份报告。`，窗口均为 2026-09-03 至 2026-09-10（Asia/Shanghai），上限 3 条。两份 `source_pool` 的 17 个 ID 相同，`source_registry_checksum` 同为 `afa348986de2f7e5f1f09e9f70a76dece733d65268228b2d22e916cf72d46be7`。

SD03 的检索词包括 `data governance finance`、`financial data quality`、`投研 数据治理`，没有把公司名或主页作为检索目标。SD04 使用 `self-evolving agent`、`agent self-improvement`、`skill evolution` 等，目标差异明确，不只是替换报告抬头。企业词表中泛化的 `data protection policy` 和“数据质量”仍需控制业务范围，见 F1/F3。

然而两个来源快照并不同批：采集完成时间分别为 `06:50:13.315473Z` 和 `06:50:55.308454Z`；SD03 arXiv 超时，SD04 arXiv 成功且返回 10 篇。不能据“企业 1 条政策、个人 3 篇论文”宣称已完成严格的画像效果对照。下一轮可以继续用同配置实时验收，但须把来源可用性差异列为混杂因素，不强制两例选材不同或文章相同。

## 后续验收

先复验 SD03/SD04 和各自来源追问，再按主任务安排运行其余查询。本文没有启动下一轮。

1. 企业默认有效主题不因否定句漂移；检查明显业务相关候选的遗漏理由和宽泛词误配。
2. 政策引文准确之外，检查引导/鼓励/要求、发文主体、适用范围和推断强度。
3. 个人报告至少形成一个有优先级、可在两小时内完成的阅读或设计任务，具体区别三篇方法。
4. 两种画像都核对 source_pool 与 registry checksum；对失败来源、清洗原因、标题级线索和原文读取范围做实质检查。
5. 模型规划、分析、修复、最终汇总的逻辑调用和尝试数对账；来源汇总不再以零代替缺失信息。
6. 追问应使用同会话实际报告，区分发布时间/抓取时间、摘要/全文、最新/热门，不用机械通过自动签署质量通过。

## 固定证据

| 对象 | Workbench run_id | Report workflow run_id | 保存产物 SHA256 |
| --- | --- | --- | --- |
| SD03 | `e23331206a014a89a5a9df7093861f3d` | `7711958dbdfe47e98a03d2a7e3230f10` | `5a4915132f13094a58e2a8bf7a8b87df783da2e9a99a205fc177e1632a1e43fe` |
| SD04 | `e2341bf39608494e8e76faf8fc5170a4` | `88b3f8419f1649eea8c015dc28415cfd` | `40d28321488aa4547dd15ec201516d49d90bab084bc0fbf7dadde80e447dc73b` |

SHA256 对应各例 `02_run_artifact.json` 的本地脱敏保存文件，而非远端原始文件。两个层级 run_id 不同是现有 Workbench/Workflow 结构，本次不将这种差异当作串用报告的证据。
