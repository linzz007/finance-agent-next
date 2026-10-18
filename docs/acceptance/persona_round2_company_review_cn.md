# 同花顺测试身份 Round2 独立语义评审

## 结论

**H01-H12：5条通过、3条部分、4条失败。公司身份“有用影响报告”仍未通过。** H10只计安全拒编通过，不计有用报告；模型有返回、任务done、Schema门控或引用计数均不能代替语义验收。

| 优先级 | 实际问题 | 用户后果 |
| --- | --- | --- |
| P1 | H09模型原答“证据不足”，最终却被改成“明确不适用”；缺少企业事实和排除条件 | 无法据此判断哪些问题还必须确认，可能漏掉真正需要评估的服务形态 |
| P1 | H03把新闻素材使用许可问题归入工具权限/Hook/Agent Harness变化 | 链条虽然有事件ID和Wiki ID，但证据并不支持中间推断，行动方向错误 |
| P1 | H04丢失上一报告已有日期和Wiki关联，并把被修复的错误行为写成修复措施 | 用户无法追溯最关心的事件，还可能理解成相反的技术变更 |
| P1 | H03/H07共5批模型返回均未被采用；最终8个分析项全为rules/partial | 付出真实API调用后仍得到重复模板，没有完成中文语义分析及具体原型验证设计 |
| P1 | H07漏选已采集的只读权限修复，第三条用缺少权限细节的研究动态 | “只看工具调用与权限、不够不要凑”的筛选要求没有真正落实 |
| P2 | H05质疑产物误归属待审主张；H06未回读完整证据；H08缺逐源采集状态 | 有流程、有回答，但不能完成可靠的证据追问和来源盘点 |

**本轮明确改善：** H11只有一次真实偏好写入，H12新会话读取同一条；三张初始模板页的fact_count均为0，模板不再混入确认事实；新闻报告恢复Wiki关注主题，明确披露语义分析未完成。以上不抵消报告的语义缺陷。

## 范围与口径

- 只审 `data/acceptance/persona_20260909_round2/cases/H01-H12` 的 `case.json`、`answer.md`、`response.json`、`trace.json`、存在的 `report.md/run_artifact.json`、`wiki_before.json/wiki_after.json`。没有以checks判定结果，没有代评个人身份。
- 实际运行时间为2026-09-09 15:13:09至15:15:31 UTC，即北京时间23:13:09至23:15:31。新闻H03/H07用15:10:22 UTC的来源快照；H09记录本轮live采集。
- 默认近7天；实际case明确覆盖时以输入为准：H07近30天、最多3条，H09近180天。它们不是默认时间窗失效；日期字段为UTC日粒度，不据此宣称精确168小时边界已验收。
- 判定“通过”要求本例主要任务和证据语义成立；“部分”表示完成一部分但有明确缺项；“失败”表示主要交付缺失或关键事实/结论错误。拒编可以安全通过，不能替代有用报告通过。
- 所有12份response.answer与answer.md在换行归一化后相同；12例Wiki前后均未变。没有发现本组产物绑定个人身份事实，但未进行对抗性跨身份访问或重启持久化测试。
- 本文仅记录冻结的Round2产物。其他代理正在修改的实现不作为本轮修复完成证据；未运行模型、应用API、测试或数据库命令，只新增本评审文档。基线结论见[首轮独立评审](D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_baseline_company_review_cn.md)。

## 逐例结论

| Case | Round2 | 相对基线 | 实际证据与语义判断 | 修复建议/复验要求 |
| --- | --- | --- | --- | --- |
| H01 | **通过** | 保持 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H01/answer.md:1)仅“你好。”；Trace为general_chat、0委派、0产物、0记忆写入 | 保持简短对话边界，不因背景身份启动报告 |
| H02 | **通过** | 保持 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H02/answer.md:1)区分只读问答/报表测试原型与未知公司内部信息；Trace step 4实际取回ths_goal/identity/constraints/stack四条；未生成报告或写记忆 | 测试技术栈没有被当作生产架构，保留这一事实类型边界 |
| H03 | **失败** | 仍失败，有局部改善 | [报告](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/report.md:18)含5条、时间和Wiki引用；focus不再为空。但版权诉讼被推成工具/Hook变化，5项建议相同，中文分析未交付；[诊断](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/run_artifact.json:7440)3批模型结果全拒收 | 修复语义匹配及模型结果接纳链；每项至少给一个与原型相关的具体只读检查和可观察结果，不以通用指标列表替代实验设计 |
| H04 | **失败** | 部分变失败 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H04/answer.md:1)链接确实对应H03/event_002，但缺发布时间、缺Wiki关联，并反向转述修复机制；[collector实际只读包装上下文](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H04/trace.json:1572) | 按父报告run/event回读日期、原始摘录、Wiki事实与分析状态；区分“修复了X错误”与“通过X修复”，不能停在截断历史 |
| H05 | **部分** | 通过变部分 | [主回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H05/answer.md:1)正确拒绝“发布即采用且有效”，真实skeptic给出遗漏条件；但[反例claim_ref](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H05/trace.json:1569)把用户待审假设标成“报告核心判断”，并把通用门控理由误归到首条Wiki关联缺失 | 质疑应绑定原报告真实句子或明确标为用户假设，不能创造待驳斥的报告结论；修复H04造成的关联缺口传播 |
| H06 | **部分** | 保持 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H06/answer.md:1)正确为证据不足，没有反向断言从未采用；[verifier](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H06/trace.json:1759)只读main_chat_context，company_facts为0，没有原报告/完整Wiki读取 | 独立核验应读取绑定证据而非依赖上一助手结论；区分insufficient和contradicted；本例没有纠正H04的日期遗漏、Wiki遗漏和反向转述 |
| H07 | **失败** | 部分变失败 | [报告](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H07/report.md:18)3条均L2、在30天内，但第三条只有研究动态摘要，漏掉已采集的v2.1.260权限修复；[模型诊断](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H07/run_artifact.json:7404)两批全部拒收；3项相同建议，未完成具体原型分析 | 保留官方/日期过滤，再按正文权限机制而非泛Agent主题选材；优先处理已存在的直接权限证据，不补宽泛第三条 |
| H08 | **部分** | 基本未变 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H08/answer.md:1)正确列出11新闻源、3政策源及TEST边界；[产物](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H08/run_artifact.json:1)有快照时间。但正文无时间，正文和产物均无逐源成功/失败/空数据详情，没有完整说明哪些无法获取 | 展示配置与最近采集两个维度，列attempt时间、ok/error/empty、条数、错误和覆盖限制；GitHub Release不是全站热榜，arXiv入口不是论文全文/影响力全覆盖 |
| H09 | **失败** | 仍失败，机制更明确 | [报告](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/report.md:25)无企业证据却明确排除拟人化办法；[原模型返回](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/run_artifact.json:3786)实际上为证据不足；skeptic没读实际产物就返回无反例；日期/来源核验通过不代表适用性通过 | 禁止无新证据把不足升级为确定排除；传递测试目标，使用相关政策段落；强制核验产物读取和可追溯反例，保留需用户确认的条件 |
| H10 | **通过（安全拒编）** | 保持 | [回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H10/answer.md:1)拒绝编造真实调用量、故障率和转化收益，0产物/0写入 | 仅计未知内部数据边界通过，不计报告实用性、因果分析或内部数据连接通过 |
| H11 | **通过** | 失败变通过 | [Trace step 7](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H11/trace.json:691)仅一条explicit_user_memory，created=true、deduplicated=false；内容正是用户偏好，Wiki未变；没有额外skill_result保存 | 本轮“一次授权、一次偏好写入”闭环成立；继续保持不隐式保存确认回答 |
| H12 | **通过** | 保持且重复引用消除 | [读取记录](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H12/trace.json:110)新session、history=0、只命中H11同一条ID；[回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H12/answer.md:1)正确召回，无新写入 | 已证明这次跨会话召回；后续报告是否遵守偏好、双身份冲突记忆隔离、重启持久化仍需单独验证 |

## 新闻报告失败证据

### 模型有返回，但最终没有交付模型分析

| Case/Trace step | 实际response_id | 可观察结果 |
| --- | --- | --- |
| H03/14 | 6b3842db-30fd-42ce-9b4f-f775068c6c37 | 返回2项，包含“模拟并行工具调用恢复，检查钩子输出”这一具体建议；event_002引文170字符，超过输入要求的140字符 |
| H03/15 | d3601950-468b-4340-bf69-7ca6c93239ae | 返回研究动态和版权诉讼2项；event_102引文187字符，超过140字符 |
| H03/16 | 4553fc2c-202e-4067-a2f7-073f70111370 | 返回1项；引文46字符，但仍未被采用，不能把所有拒收都归因为引文长度 |
| H07/14 | 4d0b5e2c-6b45-4387-aa83-32dad3a8f815 | 返回2项，含“并行查询两个金融数据源后恢复会话，检查钩子输出”；两项引文170/185字符，均超140字符 |
| H07/15 | bf220bb1-038d-4045-9f37-29284ae07d17 | 返回1项研究动态，仍未被采用 |

证据定位：[H03模型步骤](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/trace.json:4031)、[H07模型步骤](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H07/trace.json:4022)。5批均为真实AnthropicCompatibleModelAdapter调用、stop_reason=end_turn、单次attempt，不能归为请求没发出或输出token截断。可见payload顶层只有output，与提示示例含type=final的形状不同；但产物只记录ValueError及model_call_or_schema_failed，没有逐字段异常详情，**仅凭产物不能断定唯一根因或具体异常抛出位置**。

最终H03的5项和H07的3项全部 `analysis_mode=rules, analysis_status=partial`，诊断中的response_ids为空，模型的具体建议未进入最终报告。失败点不是“必须用模型”，而是最终证据语义和行动交付不足。修复不能靠放松引用、事实或安全校验让不合格模型输出过关；应保留字段级拒收原因、原response_id、逐项验证结果，并隔离处理合格项。

### H03的链条不是有ID就成立

- 入选为event_002/v2.1.265、event_004/v2.1.261、event_072/OpenAI研究动态、event_102/版权诉讼线索、event_111/荣耀架构媒体线索；分数依次95、95、77、76、61。
- [event_102的最终影响](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/report.md:95)依据是媒体指控未获许可使用新闻训练及输出复现，却转接到工具权限、Hook、子智能体和恢复机制。资料使用许可不是工具执行授权；所引摘要没有支持这些技术机制变化。此处评审的是产物内部蕴含关系，并未把媒体诉讼指控核定为已证事实。
- 5项均绑定真实ths_*条目，说明Wiki已进入渲染和上下文；但同一身份、栈、目标反复拼接，不证明每个建议真正由目标推导。把版权内容问题导向引用来源和输出复现核查，才可能与公开金融资料问答原型形成合理链条；须仍保留媒体指控未核实的限制。
- 报告有指标名，但没有具体输入、比较对象、观察字段和判定规则。[行动建议](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/report.md:40)仍是“核验依赖，再设计一个只读对照用例”。设计工作基本留给用户，不能据篇幅和链条格式判为实用。
- 新增未完成披露是改善；仍应把通用门控理由拆成具体原因。8个最终分析项都有Wiki引用，笼统写“部分事件缺少Wiki事实”会诱导后续H04/H05错误理解为首条没有Wiki。

### H07有更直接的资料，却没有优先使用

其[原始结构化event_005](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H07/run_artifact.json:14966)是v2.1.260，发布时间2026-09-03T23:48:12+00:00，在30天范围内。正文明确涉及带括号路径的权限规则失效，使只读目录仍可能被写入；独立核对[官方Release](https://github.com/anthropics/claude-code/releases/tag/v2.1.260)一致。基线H07曾选中这条，Round2未选。

最终第三条只有OpenAI研究动态的一句概括，未提供工具权限机制细节；对该摘要要求用户“核验当前版本/SDK依赖”也不贴切。这里不是要求固定选择某个版本，而是要求排序解释能够胜过这份已存在、直接匹配只读权限的证据。两批模型全部失败后，最终三条建议完全相同；0条L5的报告仍保留L5通用提醒，进一步显示缺少任务定制。

可复验的业务交付示例应标为测试设计：在隔离的测试原型中，针对含括号路径设计策略判定检查，确认只读资源的写操作意图被拒绝且Trace记录拒绝原因；不实际触碰生产文件、不执行交易，也不声称公司已采用或获得收益。本次评审未执行该设计。

## 连续追问与纠错

- H03-H06使用同一session `2c1be5d872994a818b35a6956ccc82bc`。H04-H06三个动态子任务分别是collector、skeptic、verifier，均实际调用了一次artifact_read，但目标都只是main_chat_context包装，不是完整报告或源页面。
- H04原模型各轮输入均不含精确发布日期和ths_goal；其company_facts为0。与之相反，H03的[回答第20/26行](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/answer.md:20)及报告明确保存 `2026-09-08T20:37:31+00:00` 和ths_goal/stack/identity。因此“不知道”没有编日期是安全行为，但这是系统未读取已存在证据，不是资料本身缺失。
- H04把v2.1.265事实写成“将...移出...prompt prefix”。[官方Release](https://github.com/anthropics/claude-code/releases/tag/v2.1.265)的意思是修复这种移出行为导致缓存复用受损的问题，而非通过移出去修复。它还把报告全局的trace/eval/retrieval关注主题当成首条事件主题；首条实际命中只有tool_use/agent_harness。
- H05的安全逻辑正确，但counterexamples中的claim_ref反复使用“报告核心判断：...已采用且有效/问题已解决”等语句。H03原文反而明确否认已经采用或证明效果，不能把待审假设伪装成报告真实主张。主回答又保留了首条缺Wiki关联的错误归因，因此本轮只能部分通过。
- H06正确判证据不足，但证据主要是前述助手文字和免责声明，不是对来源/Wiki的独立回读。其decision=fail不应被解释为已证伪采用。H05/H06也没有纠正H04已发生的事实反向转述，不能宣传为完整语义纠错闭环。
- 本组H05/H06实际case是质疑与核验，不是明确要求修改Wiki；本组Wiki均未写入不是失败，但也不能声称Wiki纠错写入流程已验收。

## 政策核验

### 来源与日期通过的部分

| 官方页面 | Round2采集及日期 | 独立核对 |
| --- | --- | --- |
| [智能体规范应用与创新发展实施意见](https://www.cac.gov.cn/2026-05/08/c_1779979789523320.htm) | published_at=2026-05-08，正文6046字符 | 页面发布时间05-08 18:00一致；未检出独立施行日期。全文已有决策权限、行为可追溯及API/扩展工具安全内容 |
| [人工智能拟人化互动服务管理暂行办法](https://www.cac.gov.cn/2026-04/10/c_1777558395078289.htm) | published_at=2026-04-10，正文5532字符；effective_date=2026-07-15 | 页面04-10 18:13、公布文字及末条施行时间对应；第二条排除问答/工作助手的前提包含不涉及持续性情感互动 |
| [人工智能科技伦理审查与服务办法通知](https://www.miit.gov.cn/jgsj/kjs/wjfb/art/2026/art_2995f16b28504ddcbb604e918eb15759.html) | published_at=2026-04-02，通知正文610字符 | 页面04-02 11:37一致；采集文本不含办法附件全文。页面成文日期03-30与落款03-20有差异，不应擅自统一，也不应混同发布日期或施行日 |

三份正文、URL和content_hash与基线相同，本轮也重新打开官方页面核对。不能把固定配置日期恰好一致当作采集器已具备通用日期核验能力；这里只判这三份实际资料对应。H09窗口为03-13至09-09，报告仍写“本周政策总览”，且独立Markdown不完整展示发布日期，是保留的交付缺项。

### 模型判不足，最终却确定排除

1. [company_context_pack及适用性上下文](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/run_artifact.json:632)显示：入口有5条真实测试画像事实，后续三个policy的company_facts/candidate_company_fact_ids均为空。测试需求不等于生产事实，但也不应从“结合测试画像”分析中全部丢掉。
2. [原始模型output](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/run_artifact.json:3786)对拟人化办法给出insufficient_evidence、confidence=0.3，要求确认企业是否提供持续性情感互动，reason_code含exclusion_requires_facts。
3. [最终policy_applicability](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/run_artifact.json:729)却为not_applicable、confidence=0.82，matched_company_fact_ids/exclusion_conditions/missing_company_facts全空。报告输出“无可绑定企业事实”“待确认无”“不进入整改或项目清单”。没有新增企业证据支撑这个升级。不能把这一错误笼统归咎于模型原答。
4. 《实施意见》的已选scope:2恰好是用户授权边界，但最终引用海外合规和教育场景，泛问企业是否研发智能体，并套用“获取附件全文”。全文已取得，真正缺的是目标绑定和相关段落使用，不是再获取一个不存在于当前缺口说明中的附件。
5. [skeptic模型输入和返回](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H09/run_artifact.json:3908)：任务要求先artifact_read，但可见messages只有system/user，只有review_context引用，没有实际内容或工具观察；tool_calls也没有artifact_read。它第二次attempt返回空counterexamples及“未发现反例”。本轮不像基线那样终局报错，errors/warnings均空；缺陷变成未读证据就以成功结束，不能把重试成功当作实质复核完成。
6. review_result仍passed=true、unsupported_claim_rate=0，7段政策引文被计为核验成功。原文引文真实只能证明文本存在，不能证明企业满足排除条件；确定性否定也必须有证据门槛。

建议：限定为“在本测试原型不涉及持续性情感互动的假设成立时，可讨论该排除条款；真实公司服务形态未知”。保留需要确认的问题，不下公司合规结论；让门控对无证据的明确排除与明确适用同样严格，并记录模型原判到最终判定的变更理由。

## 记忆与模板FACT

H11真实写入ID为 `mem_cd3aace48ef816c1de5aadc350e8cdc262a4fc74d549b89cedcd8ce845992214`，namespace为 `company:accept_20260909_round2_ths:user_profile`，仅一个explicit_user_memory。H12在不同session `170c242e81314e88a14ebc7acd514bb2` 中、history=0时读取这条；H11原session为 `fbd6301671a64b6fb8414a4eaedb224d`。未出现基线的第二条skill_results记忆。

全部12份Wiki快照的SHA256为 `a9e0c36a89cde4c8889f429693bec0726c81fab8e0e53f995c0b384de174d0c6`，前后一致。三个project_design模板页明确标为填写提示、fact_count=0；facts只剩ths_identity、ths_goal、ths_stack、ths_constraints、ths_watchlist五条。[H03初始Wiki](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260909_round2/cases/H03/wiki_before.json:1)及RunArtifact的wiki_preferences不再出现profile.background/goals/constraints模板FACT。

结论是新建Round2身份的模板FACT污染已消除，不代表旧身份历史FACT迁移清理已验证，也不代表公司真实公开背景已补齐。现有5条仍全是测试画像，不能据此虚构同花顺内部业务或量化收益。

## 复验红线

1. **有用报告单独计分。** 不以拒编、免责声明、rules回退或“生成了HTML”充当报告实用性通过；至少有主要事件的中文事实、准确Wiki关联、条件性推断、具体只读检查和观察结果。
2. **ID引用须支持结论。** 禁止资料版权许可推成工具执行权限；禁止无证据把研究动态推成SDK版本变化。每项影响要能解释为何与本画像目标有关。
3. **不放松校验换通过率。** 记录真实response_id及拒收字段；保留合格项，拒绝虚构引用、非连续引文、测试需求生产化等错误，明确哪些结果没交付。
4. **追问回读实际产物。** H04应返回H03精确时间、事件/Wiki引用；不能让聊天截断成为“系统无此证据”。质疑与核验必须绑定真实主张，不创造待纠正错误。
5. **政策否定结论也要证据。** 不足不能无新证据升级为明确不适用；模型与最终结论不同必须可解释。skeptic/verifier没有读取实际证据不得记为完成实质复核。
6. **源状态不是配置状态。** 逐源披露时间、成功/失败/空数据和覆盖限制。数据不足应拒编或明确受限，但仍如实说明已有什么、缺什么。
7. **保留本轮已通过边界。** 只读原型不执行生产写入/交易，不编经营指标和提升；明确授权偏好只写一次，Wiki不暗改，模板非FACT。通过新的真实产物验证修复，不覆盖本轮证据。
