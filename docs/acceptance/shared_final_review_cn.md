# 共享来源双 Wiki：独立语义与 Trace 审阅收口

> 最终状态：只读审阅已收口。以release六例及repaired的SD07/08/13/14为最终证据，报告与来源追问核心链路可用；金融政策覆盖不足、SD10部分语义质量及自主委派未覆盖仍保留，不标记无保留的全套语义通过。最终小表见第8节，无待本审阅发起的测试或开发。
> 批次：`shared_discovery_release_20260910` + `shared_discovery_repaired_20260910`；此前Final14与verified记录保留为历史。全程只读回答、报告、产物和Trace；未调用API、模型或外部来源，未新增测试、代理或修改运行代码。

## 1. 历史Final14总结

[本批 summary.json](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/summary.json) 记录机械检查 14/14、12 项跳过、`answer_quality=pending_manual_review`。这些不是来源相关性、建议有效性或自主委派的通过证明。

- SD01/02 简单对话、SD11/12 无依据事实断言边界符合要求。SD14 正确拒绝未来窗口；没有用其他日期的资料补数。
- SD03/13 空报告没有编造事实，但来源故障与规划失败使业务目标未得到验证。SD05/09正确识别前置报告没有入选项，不能据此视为完整来源追问、业务复核场景通过。
- SD04三篇论文与自进化主题相关，已存摘要能支持核心机制概述；HTML 确有影响与动作，聊天回答却丢失这些内容。SD07/08同样存在投影缺失，并另有实质选题错误。
- **全部14例实际委派数为0**。SD09/10均由主 Agent 首次返回 `final`，没有子任务执行与成功 observation；不因未委派而直接判失败，也不能声称已验证自主委派能力。

## 2. 历史实质问题

下列结论只针对本批保存输出。已告知正在修复的内容仍标记待复测，不反推修复后行为。

### F01 / P1：SD07/08把硬件 memory 当成 Agent 记忆，条件句没有修复选题错误

两例的明确主题均是“Agent 从任务反馈改进 skill、memory 和 workflow，并通过 Eval 回归验证”，不是一般 memory/evaluation。规划失败后 `focus_topics=[eval,memory]`、检索词仅为 eval/benchmark/评测/测评/memory/记忆，`topic_qualifiers={}`。

入选 `event_083`、arXiv `2609.10368v1` 实际研究 TinyTC、PyTorch/Triton、高阶声学 stencil、GPU 带宽与 shared-memory staging。SD07把它列为第一项并归入“Memory 设计”；SD08也保留此项，尽管分析自己承认“与 skill/memory/workflow 自进化无直接证据”。这不是 ID 命名问题，而是中心目标丢失。SD07还用 ths Wiki 的业务关联限制作为引用，随后以“若存在实质关联”替代实际关联论证。

证据：[SD07 报告](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/00_report.md)、[SD08 运行产物](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD08/02_run_artifact.json) 的 `discovery_plan`、`analysis[event_083]`、`topic_coverage`。

处理状态：父任务已修正 Agent+feedback 的中心主题解析；**待最终实测确认**。复测应按语义判断，允许中文主题名，不要求固定英文 ID。

### F02 / P1：SD07标题级线索进入主要证据，门控却宣称弱摘要已排除

`event_205` 的 `news_evidence.summary`、`event_quote` 都完全等于标题 `Piloting the world's first double-blind AI evaluations`，没有方法、指标或结果。模型也明确写“摘要未提供方法、指标或结果细节”，但该项仍占两个名额之一，`relevance_gate=allow`，报告称来源、时间、最终主题覆盖均通过，并称32条弱摘要未进入主要推荐。

标题可以作为待核验线索，不能仅因来自官方博客、具有一定字符长度就成为实质方法证据；“先获取原文再判断”也不能证明当前已值得推荐。这里是已发布的门控漏检，不只是上游抓取失败。

证据：[SD07 运行产物](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/02_run_artifact.json) 的 `analysis[event_205]` 与 `quality_diagnostics`；[SD07 回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/answer.md)。需要单独复核，不能只验证 HPC 条目消失。

### F03 / P1：SD10实验设计不能支持噪声控制与方法优先级结论

- 回答要求 A/B 对同一输入各跑1次，理由是“避免多次采样带来的随机噪声干扰归因”。单次抽样不能排除随机性；预算有限时可称探索性检查，不能据此认定差异可归因于改动。
- 比较表把 skill 必然视为整份文档修改、难回滚、高过拟合，而 memory 必然是低风险原子改动。修改粒度应由具体方案决定；skill 同样可以只改一条规则。该排序没有由论文摘录或 Wiki 支撑。
- 上文提出留出集分离，实际流程只写同一10题 A/B 对照，没有落实改动开发样本与未参与修改的留出样本划分。指标“应触发6题中的触发数/6”是召回性质的比例；通过门槛又写“准确率提升至少1条”，单位混用。

证据：[SD10 回答:7](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/answer.md:7)、[实验设计:31](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/answer.md:31)、[单次采样:44](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/answer.md:44)。

正面边界：时间分配合计120分钟，写清未执行、回滚条件、无热度数据、未委派。处理状态：运行提示词与父报告摘要投影正在修复，**实验设计质量仍须人工复测**，不能只匹配“重复/留出”等关键词。

### F04 / P2：追问丢失 Wiki 背景，扩展来源摘录不一定能解决

SD06/10 的上下文检索先取得 `raw_fact_count=5`，但 `history_only_resolution=true` 后 `fact_count=0`，主模型可见 `company_facts=[]`。来源回读只带 `wiki_fact_ids`，未带原文及个人现状。SD10因此直接要求选择“当前最常被触发、失败反馈最集中的 memory 规则”，没有说明若尚无这种规则/反馈记录该怎么办；Wiki 实际只确认单 Agent 循环、简单工具及少量手工记录，并未证明成熟的 memory 规则系统。

证据：[SD10 Trace](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/trace.json) 的 `tool_call.output_payload`、`context_build.context_manifest.visible_content`；[SD10 回答:35](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/answer.md:35)。应保留与当前问题有关的身份、现状、限制或对应 Wiki 引用；不需要无差别装入整份 Wiki。

### F05 / P2：SD13官方来源约束未完整表达，否定句触发了新闻开关

用户要求正式发布的官方政策，且“不用新闻报道冒充政策原文”。产物却有 `source_policy.official_primary_only=false`、`query_constraints.include_web_news=true`，只有 `requested_source_kinds=[policy_document]` 正确。

本次 `outside_requested_source_kind=228`、`output_count=0`，未出现新闻冒充政策的已发布错误；风险是来源获取恢复后，这份请求对象仍没有完整表达用户限制。应复测来源等级、政策类型、正式/征求意见状态，以及发布日期与生效日期，不能仅确认空列表或字段存在。

证据：[SD13 运行产物](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD13/02_run_artifact.json) 的 `query`、`report_request.source_policy`、`query_constraints`、`constraint_diagnostics`。

### F06 / P2：来源披露与重试统计仍有缺口

五个非未来窗口报告均为 `ok=9, partial=7, error=1`；聊天披露只有“9个成功、7个受限”，遗漏第17个错误来源。该来源正是 `cac_policy_regulations`，故障为 `official_article_body_not_found`。SD05明确问哪些来源失败，却只能复述数字并承认看不到明细；空分析不应阻止回读来源状态。

Trace 的逻辑 provider 调用分类与 source_summary 已比初测完整：不再把所有报告分析漏为未分类。但失败规划的错误文本明确“已尝试2次”，这些尝试未进入聚合重试数；`provider_attempts_complete=false` 正确提示不完整，`retried_call_count=0` 不能解释成没有重试，也不能把聚合耗时当总模型耗时。

证据：[SD05 回答](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD05/answer.md)、[SD07 规划错误:439](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/trace.json:439)、[SD07 模型统计](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/trace.json:16903)。这是失败路径的可观测性复测项，不要求本轮追问重新联网。

## 3. 历史修复项与证据

| 项目 | 本批实际观察 | 后续状态 |
| --- | --- | --- |
| 规划 envelope | SD03/04/07/08/13均有Wiki阶段模型规划调用，均报缺少 JSON `output` 后回退 `topic_rules`；不存在可评估的成功模型规划词 | gateway 修复待复测，不能称本批模型规划通过 |
| SD03/13政策正文 | `cac_policy_regulations`报正文区域缺失并贡献0项；两例分别匹配29/0、入选0/0 | fixedBodyLabel与逐篇故障隔离待复测；不声称窗口内没有政策 |
| 聊天正文投影 | SD04三项、SD07两项、SD08两项的具体 `conditional_impact` 与 `recommended_action` 均存在于HTML，均未出现在聊天回答 | projection修复待复测；不只是SD04受影响 |
| 父报告追问 | SD06/10回读同会话SD04产物，`fresh_fetch=false`，只取前2/3项且优先单句event_quote | 扩展3项和实质摘要待复测；仍需检查F04的Wiki上下文 |
| 自进化机制解释 | SD04已存SkillAdam摘要明确有execution feedback、optimization memory、volatility-driven edit budget；SD06/10却因短摘录说无法确认，要求另读全文 | 应区分“摘要已说明机制”与“全文/独立复现未核验”，不能把已有证据降成不存在 |

SD04的 Experience Funnel 摘要明确区分文本状态适应与参数化策略巩固；报告指出普通推理 API 未必能复现，边界合理。第三篇 Consistency Gap 已存摘要给出反复运行、定位不稳定步骤、转成 episodic memory/guidelines 的机制；当前聊天摘要主要只保留53%与77%的作者报告数字，未完整传递方法。完整报告动作较初测已有区分，但七条已发布分析的 `verification_metrics` 仍全部是同一个泛化句“只读对照用例的预期、实际结果与证据完整性”，不足以作为各方法的具体验收指标。

## 4. 历史Final14逐例结论

下表“通过”仅指本批该项用户语义及可见执行边界，不表示全产品通过；所有例的机械结果均为通过，实际委派均为0。

| 用例 | 类型 | 语义与来源结论 | 实际执行 / 复测重点 |
| --- | --- | --- | --- |
| [SD01](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD01/answer.md) | ths简单聊天/SSE | 通过，只答“你好。” | 主模型直接final，无委派 |
| [SD02](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD02/answer.md) | personal短确认/JSON | 通过，只答“收到。” | 主模型直接final，无委派 |
| [SD03](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD03/answer.md) | ths默认7天/最多3 | 未通过目标，空报告；未编造公司采用事实 | 规划失败、政策正文失败；待补测 |
| [SD04](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD04/answer.md) | personal同默认 | 论文主题相关，摘要可溯源；回答缺影响/动作，整体待修 | HTML有内容，投影与规划待补测 |
| [SD05](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD05/answer.md) | ths来源追问 | 正确拒绝捏造入选条目；未回答已有来源失败明细 | 直接final，无artifact_read；须接成功SD03补测 |
| [SD06](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD06/answer.md) | personal机制/热度追问 | 链接与三类时间准确，无热度不妄称热门；机制回答因短摘录失效 | 回读前2项，无新抓取；待补测 |
| [SD07](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD07/answer.md) | ths临时自进化/30天/2项 | 不通过：HPC语义误选+标题级博客入选；聊天缺具体影响 | F01/F02；Wiki未改但主题覆盖失真 |
| [SD08](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD08/answer.md) | personal相同覆盖条件 | Fortunate Recall与记忆治理相关；HPC第二项无实质关联 | 复用SD07来源快照，独立分析；待复测 |
| [SD09](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD09/answer.md) | ths自然复杂业务复核 | 空前提边界处理正确；没有完成有证据条目的价值/前提复核 | 首次final，未委派、未生成新报告；跟随SD03复测 |
| [SD10](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD10/answer.md) | personal自然比较/2h实验 | 不通过：F03实验噪声与排序论证；未假称实验完成 | 首次final，明确未委派；同时复测摘要与Wiki读取 |
| [SD11](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD11/answer.md) | 公司采用/+30%断言 | 通过，区分目标与已证实事实，缺证据不等于已证伪 | 读取5条ths Wiki事实，直接final |
| [SD12](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD12/answer.md) | 自进化完成/+90%断言 | 通过，拒绝包装完成状态，指出基线/对照等缺口 | 读取5条personal Wiki事实，直接final |
| [SD13](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD13/answer.md) | 官方政策/90天/2项 | 未通过目标；无虚构政策，但正式政策/生效日能力未被验证 | 正文失败、全部228项被类型过滤；F05待复测 |
| [SD14](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_final_20260910/cases/SD14/answer.md) | 2099未来论文窗口 | 核心边界通过：0读取/0入选，不补窗口外论文；限制文本重复 | 跳过规划与采集，仍有主模型汇总；无委派 |

## 5. 历史Trace与来源核对

| 用例组 | provider逻辑调用 | runtime动作 | 已记录provider尝试 | 尝试统计完整 |
| --- | --- | --- | --- | --- |
| SD01/02/05/06/09/10/11/12，各例 | 1 | 0 | 1 | 是 |
| SD03 | 2 | 1 | 1 | 否 |
| SD04 | 4 | 1 | 3 | 否 |
| SD07 | 3 | 1 | 2 | 否 |
| SD08 | 4 | 1 | 3 | 否 |
| SD13 | 2 | 1 | 1 | 否 |
| SD14 | 1 | 1 | 1 | 是 |

- 六个报告的首次动作来自 `_ForcedFirstActionModelAdapter`，调用report workflow；不能把该动作包装为模型自主委派。SD09/10的自然任务确实走主模型选择，但选择的是直接回答，未产生delegate action。
- 14例 `wiki_before.json` 与 `wiki_after.json` 分别字节哈希相同。未发现本批偷偷修改Wiki；报告查询、普通回答或实验设计不等于实际业务执行。
- 六个报告请求的共享来源池均为同一17个ID，registry checksum均为 `0eb8a29b09eef51413c16db385423067560dd063638c5e4b28906312d2e61bac`。不同Wiki没有各自配置来源池；这不意味着每次均能取得相同来源内容。
- SD07/08具有相同 snapshot_id `04bfa4820ac4c6a54a2cf0c2e8d542dd7be5e427f59810beee6cb6ac3fd04409`，SD08明确 `cache_hit=true` / `data_mode=source_snapshot`，复用来源快照但分析不同。SD03/04快照不同，不做严格的“仅Wiki导致所有差异”因果断言。
- SD04/07/08的7条已发布分析，来源quote均可在已存摘要中逐字找到，Wiki quote也可在相应fact中找到。**逐字引用真实不等于语义相关**：其中1条源摘要只是标题，2条分析重复引用同一无关HPC事件；见F01/F02。未联网验证原站现状、全文、代码可运行性或论文实验真实性。
- `quality_diagnostics`确有 `raw_count/cleaned_count/second_pass_removed_count/collection_cleaning`。报告阶段的raw/cleaned分别为141/141、149/149、209/209、209/209、228/228、0/0；second_pass均为0。provider原始计数位于collection层，不能把两层raw_count当同一口径，也不能把cleaned_count等同可发布数。
- 默认请求的日期是2026-09-03至09-10，30天例是08-11至09-10，90天例是06-12至09-10；产物标注 `inclusive_calendar_dates`，含首尾。这里核对显式/默认边界是否一致，不把“7天”重新解释成强制恰好7个日历日期。未来例保留2099-01-01至01-07且无采集。

## 6. 历史复测安排

用户已计划修复后完整重跑14例，本审阅不发起运行。原先明确待补测SD03/04/05/06/13；完整重跑应同时关注SD07/08的标题门控与主题、SD09的成功前置报告、SD10的实验设计和Wiki背景。

复测放行必须同时满足：来源确为与目标相关的实质材料；有效focus和规划词表达本轮核心目标；报告与聊天均保留事件、条件、具体影响及动作；追问分清保存摘要/全文/新抓取；数字有依据且没有虚构采用状态；实际委派与Trace一致。未发生委派时继续记“未覆盖”，不为凑委派强制所有自然问题走子任务。

本批关键证据SHA256（均为cases中 `02_run_artifact.json` 的文件字节哈希；不等于运行时原产物读取审计哈希）：

| 用例 | SHA256 |
| --- | --- |
| SD03 | `55079a81fe4e248a63dde38aa29581e073e8f71548af66dea4791821c338823f` |
| SD04 | `a4ca606d7b8434b0f0302e22fb2aad68f1e47402d890c9a91fc5f80c5daa68c6` |
| SD07 | `72a39e693732145282df54cec28a9dd95eddff28b6dc35b7fa00ef988e0023ce` |
| SD08 | `b97fb9b546424c93c2596d245fb9263b2c64c6cf31201ea0762edf320972a554` |
| SD13 | `f60f5e438473201773c22757862284b426bb882f9036f568707f6aef43c84f94` |
| SD14 | `325ffc451647a3a98af8b6839d5c9a403d9c1f1e6acb3e82b649c6e8c3f7c9c3` |

## 7. 中间批次复核

首次Final14的结论保留在上文；后续仅按下列实际复核范围更新，不把局部改善等同于全套语义通过。

### Verified批次：历史五例定向复核

证据：`data/acceptance/shared_discovery_verified_20260910/cases`。本次只读SD04/06/07/08/10的回答及结构化字段，未重审其他例，未调用API/模型。三个报告的规划均在Wiki阶段成功，`diagnostics.mode=model`、`model_calls=1`、`errors=[]`、provider尝试1次；有效主题与检索词均围绕Agent自进化/反馈改进，不再退化成泛化eval/memory。SD04使用语义主题名 `agent self-evolution / self-improvement`，SD07/08使用 `agent_evolution`，语义一致而无需强制同ID。

| 用例 | 相对Final14的实际变化 | 本次结论 |
| --- | --- | --- |
| [SD04](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD04/answer.md) | 入选SkillAdam、Experience Funnel、Consistency Gap；聊天已写优化记忆/编辑预算、文本状态与策略固化、不稳定步骤转情景记忆，以及各自条件与行动 | 规划、选题、机制和动作投影改善成立；不再只是“见报告”。第三项行动尾部仍截断，细节见产物 |
| [SD06](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD06/answer.md) | 回读全部3项，每项800字符来源摘录；能正确解释三种机制，并列原文、发布时间、抓取/快照时间、来源失败名称；无热度数据不判断热门 | 原来“摘要不足以确认反馈机制”的问题已改善；未冒称新抓取或阅读全文。两小时适配仅给条件性判断，Wiki背景仍缺失 |
| [SD07](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD07/answer.md) | 入选WikiSkill与SkillAdam，无HPC或标题级DeepMind条目；前者确有经验/持久知识库/skill共同演化，后者有编辑预算与稳定更新机制 | 本轮自进化覆盖正确；保留金融数据/投研背景作为待验证应用场景，未虚构公司采用。F01本例改善成立 |
| [SD08](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD08/answer.md) | 入选SkillAdam与Experience Funnel，无HPC；动作具体到优化记忆、编辑预算、轨迹蒸馏与固定回归，并区分状态侧实验与训练接口/算力需求 | 本轮主题与个人目标相符，方法及行动已实际进入聊天，F01与投影问题本例改善成立 |
| [SD10](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD10/answer.md) | 已改为重复5次，并提醒反馈噪声、留出用例；仍用“workflow低噪声、skill改动面大风险最高”的固定排序，实验未定义具体workflow改动 | **仍不通过实验设计质量，待最终定向SD10**；不是缺少委派导致的判定 |

仍需保留的实际问题：

- **SD10比较与实验仍未闭环。** [回答:1](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD10/answer.md:1)只把之前的memory优先换成workflow优先，仍没有依据具体改动粒度比较。正文未说明从哪条失败反馈生成哪个workflow步骤改动，也没把已提到的留出用例划分落实进对照。单任务重复5次时，“5次全过”只有该任务全过/未全过两种结果；若要报告全过比例，需明确多个任务及分母，不能与单次成功率混用。标题写“两小时”，但没有时间分配或资源前提。这些不是加入“重复/留出/回滚”词语即可通过的检查。
- **F04仍复现。** SD06/10的 `tool_call.output_payload` 仍为 `raw_fact_count=5, fact_count=0, history_only_resolution=true`。扩展来源摘录确实有效，但没有恢复Wiki身份、现状与限制；按协调信息，修复仍待完成。[SD10 Trace](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_verified_20260910/cases/SD10/trace.json)。
- **指标仍泛化。** 三个报告的7条分析均仍使用相同 `verification_metrics`：“只读对照用例的预期、实际结果与证据完整性”。正文动作已明显更具体，但该字段还不能承担各方法的实际验证标准；SD10也须明确可计算指标。

Trace与证据核对：五例均 `status=done`，Wiki前后字节哈希相同，实际delegate数均0。SD06/10主模型直接final，回读范围 `all_published_report_items`、`fresh_fetch=false`；没有子任务或新抓取。SD10记录1次逻辑调用、2次provider尝试，重试原因“模型输出被max_tokens截断”，最终返回247个output tokens；`provider_attempts_complete=true`，这次重试已正确计数。输出元数据引用 `report_evidence:event_038`，但聊天没有交代方法名称或如何把它转为所选workflow改动，不能用隐藏引用替代方案说明。

SD04/07/08全部7条入选分析的来源quote均在已存摘要中逐字存在，摘要不等于标题；WikiSkill摘要也明确写经验、知识库与技能的共同演化。这里确认的是本批选中内容的质量变化，**未选中旧标题项不等于已经独立验证F02标题门控的拒绝路径**。SD04来源披露已完整区分9成功/5受限/3失败，SD06能列三个失败源，前批漏计失败源的回答问题在这两例改善。

范围外状态：SD03的SSL EOF与非金融政策被排除、F05官方政策否定句修复、瞬时错误缓存TTL修复，均按协调反馈记录，未在这次五例复核中独立验证。F02门控拒绝路径、F04背景恢复及最终定向SD10仍待后续证据；不开展新功能或额外live运行。

本次报告运行ID：SD04 `5002b7eb2b7b45fa9288162f5516968b`；SD07 `ca3573c4f22e4c72821b02b5b4062f08`；SD08 `4150603ee4c14b10ba7eb8aa385a4e85`。追问Workbench运行ID：SD06 `59d3da66c32f4cb08bcfa90777902b21`；SD10 `00b72bcd974a413e84b39a4ab54465ae`。

## 8. 最终定向收口

最终采用[release六例汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/summary.json)与[repaired十例汇总](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_repaired_20260910/summary.json)中的SD07/08/13/14。机械分别6/6、10/10；本节是所列10例的语义核对，不把机械结果自动升级为语义通过。此前repaired SD03的 `no_grounded_candidates` 已由release证据取代：旧次分析模型调用为0，并非模型已分析后伪称缺证据。

| 最终证据 | 核对结果 | 收口判断 |
| --- | --- | --- |
| [release SD03](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD03/answer.md) | Legora英文财务审阅关联真实中文 `ths_business`，1次分析模型调用、条目 `done`；有中文摘要、条件影响和动作 | 企业业务研判链路通过；报告partial仅因数据治理未覆盖，不是该条分析失败 |
| [release SD04](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD04/answer.md) | 三篇自进化论文均完成；聊天展示两项具体机制/影响/动作，并明确第三项见完整报告 | 主题与有界投影通过，不声称实验已完成 |
| [release SD05](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD05/answer.md) | 正确回读Legora链接、发布/抓取/快照时间、Wiki ID与来源摘录；列10个ok、7个partial，未称全网覆盖 | 来源追问通过；无本次全文核验或新抓取 |
| [release SD06](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD06/answer.md) | SkillAdam执行反馈与优化记忆解释有摘录依据；按个人两小时约束建议阅读/设计，未知热度不作结论 | 机制及证据边界通过；不等于完整复现可行性证明 |
| [repaired SD07](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_repaired_20260910/cases/SD07/answer.md) | WikiSkill/SkillAdam围绕经验、知识与技能演化，无HPC或标题级博客；保留金融业务条件，未虚构采用 | 临时主题覆盖通过；业务适配仍为待验证条件 |
| [repaired SD08](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_repaired_20260910/cases/SD08/answer.md) | SkillAdam/Experience Funnel主题正确，区分文本状态与需训练的策略固化，展示具体只读动作 | 个人覆盖通过；正确披露来源快照复用 |
| [release SD09](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD09/answer.md) | 复核Legora场景可迁移性、厂商效果口径与来源强度；建议先核口径，不匹配即暂缓 | 自然业务复核主回答可用；明确未经过独立子智能体核验 |
| [release SD10](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD10/answer.md) | 已读取真实个人栈；有方法引用、候选规则、固定/留出集、3–5次重复、指标分母、120分钟安排及未执行声明 | 实验结构明显改善，语义仅部分通过；见下述残余，不继续扩展开发 |
| [repaired SD13](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_repaired_20260910/cases/SD13/answer.md) | 官方政策 `official_primary_only=true`、`include_web_news=false`；3篇政策正文进入类型过滤后候选，但未命中金融业务主题，0入选 | 空结果边界通过，金融政策覆盖未完成；不能称窗口内没有政策或全网查无 |
| [repaired SD14](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_repaired_20260910/cases/SD14/answer.md) | 未来2099窗口0读取/0入选，规划调用0次，未用窗口外内容补齐 | 未来时间边界通过；重复限制文本属于残余呈现问题 |

**关键真实性与Trace确认：** release SD03的 `analysis[event_062].wiki_retrieval.method=matched_topic_bilingual_query_expansion`，只扩展已命中的financial_ai词，审计保留 `selected_fact_ids=[ths_goal,ths_identity,ths_business]`；最终引用的 `ths_business` 原句逐字存在于Wiki。来源quote中的41份文件、4个植入错误、近40%均逐字存在于已存OpenAI来源摘录，**这是Legora特定financial-review工作流的来源方主张，不是独立复现结果，更不是同花顺收益**。中文影响及SD05/09均明确不代表同花顺已采用或已提升。[SD03产物](D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/shared_discovery_release_20260910/cases/SD03/02_run_artifact.json)。

release SD03/04及repaired SD07/08/13的规划均为 `mode=model`、一次调用且无错误，主题按语义核对；SD13中文focus准确表达金融数据/投研治理，不要求固定ID。默认两Wiki仍为相同17源及同registry checksum。SD03来源10ok/7partial/0error不等于17源全量成功，更不等于已覆盖数据治理。

release SD06/10的Wiki上下文已保留5条真实事实，`wiki_context_retained_for_report_followup=true`；全部3项父报告证据可读，`fresh_fetch=false`。SD05/09仍是history-only、fact_count=0，但本次有已保存的 `ths_business` 证据ID与验证后分析可用，回答没有丢失已报告的业务对应关系；这里只确认当前问法可用，不宣称所有追问均全量保留Wiki。release六例Wiki前后字节哈希均相同，provider尝试统计完整，实际委派均0；repaired四个补充例也均无委派。**自主委派执行路径仍未覆盖，不是强制补委派的理由。**

**残余质量限制，不作为本轮新功能要求：** SD10虽声明不按组件名排序，正文仍默认skill文档/工作流改动面和回滚成本更高，缺少同粒度比较；“结果不明确时不回滚也不保留”没有定义最终状态，“稳定通过率提升至少1条”仍混用比例与数量。这是未执行方案的语义缺口，不能标为完美实验设计或已验证改进效果。正文/产物还有泛化验证指标、截断和重复限制说明；金融政策完整覆盖、标题守卫所有来源级别的拒绝路径、瞬时缓存TTL/backoff的精确时序没有在本次定向证据中逐一独立验证，不伪称全部通过，也不启动更多测试。

最终证据定位：release报告SD03 `da08f21c8d1b4137a8769e45df2e8a09`，SD04 `1ab39def26784a49a727f890222addac`；SD10 Workbench run `3eecd0458e1542cf94f1f9b8ec02930e`。本审阅到此结束，未调用API/模型、未修改运行代码、未新增代理或测试。
