# 双 Wiki、数据源与报告任务约定

日期：2026-09-10。**本轮更新：共享来源池、任务化探查、报告参数贯通及真实模型自主委派已实现，正在进行双 Wiki 实测与修复。** 测试 Wiki 通过公开 HTTP API 创建，原有个人资料未被替换。最终通过范围以本轮验收报告为准，不把接口成功当作内容质量通过。

本文按2026-09-10当前工作区代码核对；“已实现”只表示代码路径存在，不表示所有入口实时可用或内容质量通过。最初H03/P10与9个入口的前置探测统一放在第7节历史记录，不作为当前实现状态。GitHub公共仓库发现和有限README/Release读取、动态arXiv/新闻检索、合并清洗及诊断已接入；没有全网完整索引、通用PDF全文精读或独立复现能力承诺。

**所有 Wiki 共用固定的外部数据源池；新增 Wiki 只改变探查与分析规则，不新增一套专属采集系统。**

## 0. 项目边界：共享数据源，不同探查规则

本项目是结合用户背景的信息探查与分析系统，不是为每个公司定制的官网监控系统。同花顺是分析视角和业务背景，Agent 自进化是另一个研究视角；两者不是各自的数据源配置。

```text
系统共享 SourceRegistry
  GitHub / 政府政策网站 / 论文网站 / 已配置新闻与官方技术资料
                         ^
                         | 在已配置平台范围内查询、有界读取
当前 Wiki + 用户 query -> ReportRequest -> DiscoveryPlan
                         | 主题、检索词、业务限定词、时间、排除规则
                         v
         获取摘要/有限正文 -> 清洗去重 -> 关联 Wiki -> 分析 -> 报告
```

- 固定的是平台、允许访问范围、连接器和采集能力，不是永久固定几篇文章、几个仓库或一条 Agent 搜索词。
- 每个Wiki使用同一配置池。当前采集器枚举已启用的新闻和政策源并分配预算；主题改变查询和筛选，不给某Wiki永久绑定独占来源，也不由模型任意增加URL或权限。
- 支持查询的平台接收本轮检索参数；列表/订阅入口有界读取后筛选，并可复用采集缓存。这里不是已实现所有网站的全文检索或通用增量游标。
- API、RSS/Atom是获取通道；摘要、HTML正文、README和Release说明的读取范围必须分别记录。历史探测能下载PDF，不等于正式报告链已经解析或精读该PDF。
- 共享池覆盖不到的问题必须说明资料不足。更换 Wiki 不会凭空增加数据覆盖，也不能把没有证据的内容补成结论。

## 1. 当前实现与验收状态

| 方面 | 当前代码事实 | 不应推导的结论 |
| --- | --- | --- |
| 请求贯通 | 统一报告链先解析Wiki/query，再生成`report_request.v1`和`discovery_plan.v1`，交给采集、筛选、分析、标题与产物记录 | 不是消息API已新增一套结构化请求表单，也不是任意自然语言都能正确解析 |
| 共享来源池 | `config/sources.json`是共享注册配置；请求与计划保存`source_pool`和`source_registry_checksum` | 已配置不等于可访问；同配置不等于同批快照或同样的成功来源 |
| 模型规划 | Wiki阶段可调用规划模型生成短检索词、主题同义词和业务限定词；失败保留规则回退与错误诊断 | 有模型调用不等于主题准确；中文主题不要求等于旧固定ID |
| 普通聊天 | 主AgentLoop直接使用模型适配器决定`delegate`或`final`，Runtime校验并执行子任务 | 不要求每条自然问题委派；`/report`确定性启动Workflow不是自主委派证据 |
| 验收 | 已有真实首轮证据和独立质量审阅，修复与最终14条验收正在推进 | 不用机械通过、代码更新或历史探测替代最终质量结论 |

首轮结论见[独立质量审阅](D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/shared_initial_review_cn.md)。本文不追认首轮问题已全部解决，也不预先宣布最终14条通过；本次文档核对没有运行测试或模型，最终证据另行审阅。

## 2. 两个 Wiki 应写什么

共同区分：**身份/业务事实、用户目标、长期关注点、任务默认值、硬约束**。用户关注什么不是公司已实现什么，研究计划也不是已具备的技术能力。

### A. 同花顺业务研判

| 内容 | 推荐文本或范围 | 性质 |
| --- | --- | --- |
| 身份 | 从同花顺产品与业务研究人员的视角，研究公开信息；未接入公司内部系统 | 用户选择的研究视角，不冒充官方授权 |
| 基础业务 | 金融信息与数据服务、金融数据、投研及公开资料研究支持 | 当前验收为用户确认的测试背景；作为外部公司事实使用时需另附核定出处 |
| 业务背景对象 | 同花顺及用户确认的产品/业务，如问财、iFinD | 用于解释外部资料的意义，不据此创建公司官网采集器；不推定内部功能或采用状态 |
| 主要目标 | 从共享平台的政策、技术、研究和行业资料中发现与金融信息服务有关的内容，判断业务影响、适用条件和后续核查事项 | 用户研究目标，不限定为查找关于同花顺的新闻 |
| 默认关注 | 金融数据服务、智能投研、信息可信度与数据治理，以及明确相关的政策和技术进展 | 主选题，不默认套用Runtime/Trace/Eval关键词 |
| 技术信息进入条件 | 用户本轮明确提问，或资料能证明与已选业务问题直接相关 | 允许有AI技术内容，但不能自动变成Agent技术日报 |
| 排除 | 无关个股涨跌、全市场行情堆砌、普通开发工具更新、没有业务关联的Agent论文 | 防止数量很多但与用户无关 |
| 输出 | 发生了什么、影响哪个业务对象、为什么相关、哪些条件未知、建议跟踪或核查什么 | 不是投资买卖建议，不假定公司已经采取行动 |
| 约束 | 不访问内部数据，不编采用率/收益/客户数据；规则只做有条件的资料研判 | 不允许被普通query解除的边界 |

公司背景可由用户提供并标明性质，或另行核定官方披露。历史探测中的年度报告摘要与公司旧简介只保留为出处候选，不表示本次已重新核验披露内容，也不要求为同花顺建立专属采集器。本方案不录入未经核定的内部技术栈。

**默认问题**：`/report 生成一份报告。`

预期是同花顺相关业务研判；可以没有Agent框架新闻。若本周没有合格资料，应说明覆盖和空缺，不能用旧技术新闻填满。

**专题问题**：`/report 最近30天，公开政策和技术资料中有哪些变化与我们的金融数据服务有关？说明业务影响和适用前提，不推测公司内部计划。`

### B. Agent 自进化研究

| 内容 | 推荐文本或范围 | 性质 |
| --- | --- | --- |
| 身份 | 个人AI工程师，研究Agent如何从任务反馈中持续改进 | 用户研究身份 |
| 主要目标 | 找到自进化方法的机制、证据、局限和适合现有项目的小范围学习方案 | 不是泛泛的Agent框架更新汇总 |
| 核心关注 | 经验/轨迹反馈、反思与纠错、技能沉淀、记忆更新、提示或工作流优化、改进前后的评估与退化防护 | 主选题 |
| 支撑关注 | Runtime、Trace、Context、Subagent | 只有与自进化机制或验证有直接关系时进入主报告 |
| 现状 | Python、SQLite、LLM API及现有Agent项目；实际能力仍以用户确认内容为准 | 不因为论文出现某能力就认定本项目已有 |
| 学习约束 | 每次约两小时；优先阅读、纸面用例设计和少量样本对比 | 大规模训练、整栈迁移、大负载运行不能包装成轻量练习 |
| 排除 | 金融公司业务、普通UI修复、没有方法增量的版本更新、无指标支持的“热门”榜 | 保持主题区分 |
| 输出 | 进化对象、反馈信号、更新机制、如何验证、失败风险、阅读或实验前置条件 | 区分作者自报结果与独立验证 |

Hermes与EvoAgentX是共享GitHub池中的研究对象及已配置Release入口样本，不是个人Wiki的独占来源。具体机制、版本可用性和效果须以本轮实际读取资料核定，不能认定本项目已有同等能力或这些项目被证明最优。

**默认问题**：`/report 生成一份报告。`

预期是近期自进化方法与工程进展，不输出问财/iFinD业务建议。

**专题问题**：`/report 最近90天有哪些利用任务反馈改进Agent的开源项目或论文？说明改进了什么、如何验证，以及我该先读哪部分。`

### Wiki 页面分工

两个空间各自保留自己的页面：身份与背景、研究对象、目标与关注点、限制与偏好。Report本轮的时间和选题不反写成长期画像。

建议事实记录保留fact_id、内容、来源性质、URL及披露/确认时间，未知值明确未知；这不是声明API强制校验了全部字段。当前新fixture通过API录入用户确认事实，不含公司内部事实；两个默认均为最近7天、最多3条。同一事实只维护一处，本轮专题不反写长期Wiki。

## 3. 固定共享数据源池，任务动态生成探查规则

不再采用上一版的 `ths_business` / `agent_evolution` 两个画像专属源组，也不把同花顺官网、问财首页、iFinD页面作为企业画像的必备采集入口。之前混淆了研究对象和来源，这一设计予以撤回。

| 共享平台类别 | 当前已实现的获取范围 | 本轮可变规则 | 限制与验收边界 |
| --- | --- | --- | --- |
| GitHub | 固定仓库Release入口、公共仓库主题搜索、预算内README/Release说明读取 | 检索词、时间、候选及正文读取预算 | 不遍历全部仓库；README/仓库push时间不冒充发布日期；API限流、补充失败和截断须披露 |
| 政府政策网站 | 已配置网信办列表、固定政策页及工信部页的HTML正文提取 | 日期窗口、内容相关性筛选 | 不是全政府网站搜索；发布日期不等于生效日期，附件/PDF不自动等于已解析 |
| arXiv | 参数化主题与日期检索、元信息和摘要，带请求节流、重试及采集缓存 | 本轮检索词、窗口、条数 | 不是固定Agent查询；没有自动全文精读、独立复现或热门指标保证 |
| 新闻与官方技术资料 | 已配置RSS/Atom、动态Google News主题搜索 | 查询词、日期、候选筛选与来源层级 | 聚合标题仍是线索；不能把来源状态ok、标题媒体名或合法URL当作正文真实性验证 |

核对时配置包含17个已启用入口，另有停用入口；实际ID集合以每份`report_request.source_pool`及配置checksum为准，不将此数量写成长期不变的产品承诺。新增平台是系统级扩展，与新增Wiki分开管理；入口稳定性以对应运行的来源诊断为准。

例如相同GitHub/政策/论文池：同花顺Wiki以金融信息服务、数据治理、智能投研等条件探查；个人Wiki以反馈学习、技能演化、记忆更新、工作流优化等条件探查。两者可以引用同一份资料，但必须解释不同的相关性和建议，不能要求URL完全不重合。

本文的SourceRegistry指共享来源配置，不暗示额外的注册管理API或同名运行时类。DiscoveryPlan保存检索词、主题、限定词、排除项、日期和预算；实际请求URL、状态、采集时间和失败原因在`source_manifest`及各条`provenance`中记录，不在Wiki里复制配置或凭据。

每个正式接入源都要验证：能合法访问、能提取正文与原始URL、能区分发布日期/抓取时间、能识别目标主题、能处理分页/增量/重复、能说明缺全文或失败。缺日期的页面只作背景，不能以抓取日冒充本周发布。

**任务化探查代码已落地，完整质量验收尚未完成。** 来源成功不等于候选相关，清洗计数守恒不等于正文干净；已存在逐源/合并清洗、正文区域提取和报告二次清洗，仍须人工检查引用、噪音与业务相关性。不能靠增加公司官网入口替代这些检查。

## 4. 当前ReportRequest与DiscoveryPlan

公开消息API仍使用`WorkbenchMessageRequest(message)`；`report_request.v1`是报告Workflow内部生成并保存的任务单，不是客户端可直接提交的完整请求体。以下使用真实字段名，不把早期设计草案当作已开放API。

| 参数 | 来源/作用 | 边界 |
| --- | --- | --- |
| subject_id、subject_name、profile_checksum、wiki_fact_refs | 当前空间及Wiki快照引用 | 完整快照另存`wiki_snapshot`；引用存在不等于支持结论 |
| question、focus_topics、excluded_topics、constraints | 当前问题和Wiki背景；内部`task_profile`也可提供覆盖项 | 模型规划成功时保留`parsed_focus_topics`，将有效`focus_topics`更新为计划主题，并记录`parameter_sources.effective_focus` |
| period.start/end、period.timezone、as_of | 明确日期优先；否则解析Wiki默认值，再用系统默认 | 默认最近7天、Asia/Shanghai；上周自然周单独解析；当前按包含起止日期的日历窗口过滤，并记录`period.boundary`，带时区时间先转换再比较；不能宣称严格168小时窗口 |
| period.datetime_start/end、period.boundary | 支持的显式时刻区间 | 无具体时刻时是`inclusive_calendar_dates`；有时刻时记录`inclusive_instants`，不凭空补造精准起止时刻 |
| source_pool、source_registry_checksum、source_policy | 共享配置ID、配置摘要、官方/来源类型约束 | `source_pool`不是本轮成功来源清单，也不是画像独占组 |
| max_items、language | 显式条数或Wiki默认；当前中文输出 | 系统条数兜底5，当前双Wiki fixture为3；解析条数限制在1至10，不足不凑 |
| resource_budget、parameter_sources | 候选预算与各参数来源 | 当前请求记录每源40、候选500、规划模型调用预算1；逻辑模型调用、网络重试与分析预算须分别看Trace，不能混算 |

面向用户的时间、数量和主题通常按“本轮明确要求 -> Wiki默认 -> 系统默认”解析；内部`task_profile`的显式字段具有自身覆盖优先级。服务端权限和硬预算不由规划模型扩展。日期锚点仍使用服务端`date.today()`，验收要记录服务端日期/时区，跨日差异不能悄悄放宽。

`DiscoveryPlan`实际包含`search_terms`、`focus_topics`、`topic_terms`、`topic_qualifiers`、排除项、日期/时区、来源池、预算与`diagnostics`。模型输出经Schema和公开词项格式检查，失败返回规则计划和错误类型；这些检查不等于完备的隐私脱敏或语义正确性保证。未来窗口跳过模型探查规划和来源采集。

早期草案中的`entities`、`wiki_query`、`selected_fact_refs`、`sections`、独立`evidence_rules`及UI参数冲突处理，不是当前这两个Schema已实现的字段/交互。不要据本文要求客户端发送它们。

### 标题与范围展示

当前`report_title()`格式为`{subject_name} · 资讯研判报告 | {start} 至 {end}`，Markdown与HTML复用；不是已支持任意专题标题模板。正文/元信息包含问题、窗口、生成时刻、Wiki checksum及来源披露。查询范围不等于资料齐全覆盖范围，具体字段是否正确展示仍须看实际产物。

## 5. Report仍然是Workflow，但由参数驱动

```text
用户 /report + 当前空间
  -> 读取Wiki身份、目标和约束
  -> 解析并校验ReportRequest
  -> Wiki阶段生成DiscoveryPlan：检索词、主题及限定词、时间窗
  -> 向共享来源池传入计划，执行有界检索/列表/订阅读取
  -> 保存摘要或有限正文、来源链接、provenance与读取范围
  -> 清洗、去重、日期校验、正文与来源质量检查
  -> 针对事件检索相关Wiki事实
  -> 结合来源事实与Wiki事实进行条件式分析
  -> Gate检查引用、范围、覆盖、未知条件
  -> 生成有时间范围的HTML报告
  -> 返回报告摘要/产物引用到主Agent上下文
  -> 用户在同一对话中追问
```

固定的是共享数据源池、阶段契约与失败边界，不是固定搜哪些Agent新闻。任务快照键由有效计划、来源配置和缓存版本生成；抓取时间用于新鲜度判断，不是每次把当前时间拼进缓存键。静态来源和arXiv另有有界采集缓存，可复用公开获取结果；不同查询不会直接取另一个计划的快照。这里不声明已实现独立的Wiki分析结果缓存。

产物保留`report_request`、`discovery_plan`、`source_manifest`、分阶段记录、分析状态和覆盖诊断；`quality_diagnostics`含`raw_count`、`cleaned_count`、`second_pass_removed_count`、`collection_cleaning`。受限/失败结果与完整分析必须区分。

同会话追问已通过`read_report_evidence`回读本空间历史报告，并记录路径/hash；当前投影仅取前两项及有界摘录，标记`first_two_items_only_not_a_complete_ranking`和`fresh_fetch=false`。这不是全报告重读、新联网核验或跨会话全量检索，不能据此前两项宣称比较了全部候选。

## 6. 当前按需委派与验收边界

普通聊天的`_run_general_chat_agent_loop`已经直接将真实模型适配器交给AgentLoop，包含首次决策；不再由旧关键词序列向这个入口注入强制delegate。模型依据当前任务、可见证据和角色清单选择`delegate`或`final`，显式角色要求作为用户任务处理，不是已经执行的动作。规则层保留授权、允许角色、上下文/工具范围、预算和失败处理。

过程：主模型输出final或delegate -> Runtime校验 -> SubagentRunner执行隔离任务 -> 结构化结果成为observation -> 主模型继续决定是否补充任务或final。

`/report`仍保留确定性首次`report_workflow.run`调用，之后再由模型整合结果；不能将此步骤或子任务内部的受控首次动作算作“主模型自主选择委派”。前端应核对实际子任务、输入证据范围、输出及终止状态；这里不以代码存在宣称所有UI展示已通过，不要求或展示模型私有思维链。

新的自主委派例子不写角色名：

- 同花顺用户：“这两份产品资料对同一个功能的说法不一致。我能把它当作竞品已经提供这项能力的依据吗？”
- 自进化研究用户：“这个项目说能从经验中持续提升。我每次只有两小时，现有资料足以支持我投入时间研究吗？哪些关键前提还不知道？”

验收看实际主模型是否按需要选择delegate，而不是要求每条都调用固定两个角色。Trace必须区分model_decision、用户显式请求和Runtime控制；不能用deterministic_action冒充自主规划。四角色的执行契约测试另外保留，不与自然用户任务混为一谈。

## 7. 历史记录：不得当作当前状态

### 旧H03/P10与修订前实现

旧H03输入只有`/report 生成一份报告。`，但当时`ths_goal`是只读报表Agent原型，`ths_watchlist`含Runtime、工具调用、检索、Trace、Eval。产物的`focus_origin=wiki`及技术主题符合**当时录入的画像**，不证明现在所需的同花顺业务领域已经区分。这是测试场景设计不足，不归咎用户。

当时`_load_news_items`没有接收任务发现计划，arXiv等查询尚未动态参数化，时间默认贯通证据也不足；早期完整ReportRequest、标题和auto委派调整当时确属待实现。旧P10明确要求分析与质疑，规则命中后通过`_ForcedActionSequenceModelAdapter`注入委派，证明的是受控执行，不是那次主模型自主决策。以上均为历史描述；当前路径见第1、3至6节。

旧回答、Trace、失败与通过数字全部保留，称为“技术研发双画像验收”，不重写为新业务/自进化双领域的通过证据。原文“没有改生产Wiki/Report/Subagent、没有运行付费模型”只描述下述preflight阶段，不描述后来的实现和live验收。

### 2026-09-10前置只读探测

历史阶段探测了9个候选入口，没有调用模型、没有改`config/sources.json`。记录见[13:52:57来源探测](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/source_preflight_20260910_135257/results.json)。iFinD初按HTML检查，识别实际PDF后补测，见[13:54:19格式复核](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/source_preflight_20260910_135419/results.json)，原始记录不覆盖。下表仅转述当时观察，本次没有重新请求这些站点。

| 资料源 | 当时观察 | 当时用途判断及现在的解释边界 |
| --- | --- | --- |
| 巨潮同花顺年度报告摘要 | 200，PDF可获取 | 低频Wiki背景候选，不是本周新闻；[当时PDF链接](https://static.cninfo.com.cn/finalpage/2026-03-10/1225002185.PDF)可获取不等于已完成正式解析 |
| 同花顺官网公司简介 | HTML可读，但页面为2021年 | [历史简介](https://stock.10jqka.com.cn/20210331/c61833421.shtml)只作旧背景，不充新事件 |
| iFinD公开更新示例 | URL像网页，正文实际PDF | 需按内容类型处理，不是现成HTML新闻源 |
| 问财首页 | 200，静态可见正文只有“同花顺问财” | 不适合作新闻主源，不绕过登录/验证码 |
| Choice公开更新页 | 首条可见更新为2024年 | [当时公开页](https://choice.eastmoney.com/Product/ChoiceApp/)仅能支持该次观察，不能声称本周有更新 |
| 证监会要闻列表 | 列表可读 | 详情、日期、正文和分页另需验证，不代表该入口已正式接入或全部条目相关 |
| Hermes Releases Atom | 解析10条，示例最新日期2026-09-07 | 当时样本记录，不等于当前发布资格或可用性；现已配置共享Release入口 |
| EvoAgentX Releases Atom | 解析6条，示例最新日期2026-06-28，链接指向ANative-Lab仓库 | 保留当时重定向观察；更新日期与发布日期仍须按正式链区分，现已配置共享Release入口 |
| arXiv自进化主题查询 | 解析8篇，样本含2026-09-08论文 | 当时仅元信息/摘要；现已动态参数化，未因此获得全文或热度能力 |

探测时“新增候选尚未注册”已不是对整张表的当前判断。公司/产品网页和证监会候选未因本探测自动接入；Hermes、EvoAgentX及动态arXiv已进入共享配置/实现，实时成功率仍看各轮证据。此前`ths_business`/`agent_evolution`画像专属源组与公司主页主采集方案已撤回。

## 8. 剩余验收，不是重复实施计划

1. 使用新双Wiki和同一来源配置，对比默认请求及30/90天专题；核对有效主题、参数来源、日历边界和来源失败差异，不强制两份报告文章相同或不同。
2. 分开检查机械结构、来源质量、引用支持、业务适用范围、个人两小时学习方案；不编公司内部采用/收益，不把摘要当全文或作者效果当独立复现。
3. 核对同会话追问范围与自然自主委派，简单聊天不强制委派；角色契约测试不能替代主模型自然任务证据。
4. 对首轮问题和后续修复逐项回归，包括正文清洗、重复事件分析、条件句校验、行动具体性、主题限定词和Trace汇总；实现已改不等于复验已过。

最终14条结果以`data/acceptance/shared_discovery_final_20260910`对应证据与后续独立审阅为准；本文此次仅核对代码和文档，不审阅或签署该批结果。

## 9. 代码核对入口

- [测试画像与旧H03/P10](D:/AAAcode/code-code/agent+/harness/policy_impact/tests/fixtures/persona_acceptance.json)
- [新双Wiki与14条用例](D:/AAAcode/code-code/agent+/harness/policy_impact/tests/fixtures/shared_discovery_acceptance.json)
- [ReportRequest、模型规划与标题](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/skill_executors/report_request.py)
- [请求贯通、窗口默认、主题匹配及报告](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/skill_executors/recent_news_report.py)
- [共享采集与来源诊断](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/source_connectors/ingestion.py)
- [动态查询、日期清洗与缓存键](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/source_connectors/discovery.py)
- [GitHub有界发现与正文读取](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/source_connectors/github.py)
- [普通聊天主模型委派及Report受控入口](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/harness/agent_runtime.py)
- [同会话报告证据投影](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/harness/report_context.py)
- [消息API结构](D:/AAAcode/code-code/agent+/harness/policy_impact/src/policy_impact/app/api.py)
- [全局数据源](D:/AAAcode/code-code/agent+/harness/policy_impact/config/sources.json)
- [历史只读源探测脚本](D:/AAAcode/code-code/agent+/harness/policy_impact/scripts/probe_persona_source_candidates.py)
