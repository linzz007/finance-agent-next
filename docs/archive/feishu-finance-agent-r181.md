<title>金融agent架构</title>

# 输入信息

目前都是文本形态的

1. 微信金融博主的文章，然后是想实现一个脚本自动获取保存到本地
2. llm自己结合query去提取关键词search相对应的一些文章



# 流程架构设计

<whiteboard token="TVS6wU98DhtY7UbDhyocuwOxnZZ"></whiteboard>

# 输出



# 解决什么稀缺问题

# 严谨候选方案与证据审计（五个研究点）

本章节是对前一版候选方案的校正。前一版把部分论文的思想组合成了看起来完整的方案，证据边界不够清晰。本版本只把有明确主论文或正式技术报告、公开评测范围、可复现资产或明确工程实现的内容列为候选；单纯的设计启发只列为支撑证据，不再冒充完整解决方案。

A～E 仍然是五个研究点，而不是五条互斥路线。每个研究点分别选出 A\*、B\*、C\*、D\*、E\*，最终形成复合架构。单篇论文没有证明“它适合本项目”时，必须明确写成“论文已证明的能力”和“本项目需要验证的适配假设”。

```Plain Text
研究规划
  -> A 单篇文章阅读
  -> B 长文章与多文章的分层组织
  -> C 主 Agent 的动态控制
  -> D 观点、论证与证据核验
  -> E 中间状态与上下文管理
  -> 组合层：作者时间线、跨文章分析、行情核验、Subagent
```

## 一、强论据的候选资格

**S 级：**论文或正式技术报告提出了完整方法，研究问题与本研究点直接相关，有公开实验、数据或基准，并且有代码或可复现实验资产。

**A 级：**论文方法和本研究点高度相关，有公开实验或代码，但原始任务与金融文章存在差异，必须做适配实验。

**B 级：**只有诊断结论、综述或工程经验，能帮助设计约束，但不能单独成为最终候选方案。

最终选型只允许从 S 级和 A 级候选中产生。B 级材料只能作为风险依据。例如，[Lost in the Middle](https://arxiv.org/abs/2307.03172) 能证明长上下文存在位置利用问题，但它没有提出完整的金融文章阅读流程，因此不能单独作为 E 的最终方案。

每个候选方案都必须分开写清楚：论文原方法、论文评测证明了什么、论文没有证明什么、我们打算如何适配、适配部分如何验收。不能把多篇论文的结论拼成一条“论文已经证明的完整链路”。

## 二、研究点 A：单篇文章如何完整、深入地读取

**定位：**A 研究一篇已选定的文章如何保留作者观点、论据和适用条件。选哪些文章、读多深属于 C；跨文章聚合属于 B；外部事实查证属于 D。A 的输出应可回到原文，但具体卡片格式仍待实验决定。

**证据纠偏：**先前的 PaperQA 研究跨科学论文的检索问答，Chain-of-Note 研究噪声检索结果的逐项笔记；都没有直接证明能完整深读单篇中文金融短文。两者降为相关背景，不再作为 A 的正式候选。下列论文证明的是各自原任务上的效果，不代表在本项目文章上已经胜出。

### 候选 A1：阅读全文后做主张—证据结构化分析

**主来源：**[Choi 与 Kim，FEVER Workshop 2025，Automated Claim–Evidence Extraction for Political Discourse Analysis](https://aclanthology.org/2025.fever-1.1/)。研究对象为 87 篇韩文意见性社论，每篇单独交给 LLM；零样本或一份专家示例引导模型识别中心主张、引用原文证据、解释潜在意图，再以专家分析、自动评分和抽样人工审查比较。论文报告 one-shot 的结构与解释对齐更好，同时指出解释性幻觉、单专家评估、缺少步骤消融和跨体裁验证。

**本项目适配：**全文一次读入，逐篇输出观点、文内依据、作者推断及其边界，并标出原文位置；可用一份中文金融文章专家示例，但示例是否带来收益要单独比较。优点是与短篇高密度意见文章及现有 LLM 工具链接近。缺点是论文只聚焦中心信息，不能证明所有次要观点都被覆盖；固定五步提示也不应直接变成所有 Query 的强制流程。

### 候选 A2：文档级中心主张抽取与去语境化

**主来源：**[Deng 等，ACL 2024，Document-level Claim Extraction and Decontextualisation for Fact-Checking](https://aclanthology.org/2024.acl-long.645/)。原方法按全文中心性抽取候选句，补充文章语境改写成可独立理解的主张，再筛出值得核查的内容。论文在事实核查材料上报告中心句 P@1 为 47.8，高于其列出的 Baseline；专家评估和证据检索亦有改善。

**本项目适配：**适合为后续联网核验准备清晰的主张，尤其补齐代词、时间和所指对象。风险是原方法优化“中心且值得查证”，不是作者所有观点的覆盖率；若独立用作完整文章阅读，会遗漏条件、反例和非中心但与用户问题相关的内容。

### 候选 A3：论证角色感知的多候选生成与重排

**主来源：**[Elaraby 等，Findings of ACL 2023，Towards Argument-Aware Abstractive Summarization of Long Legal Opinions with Summary Reranking](https://aclanthology.org/2023.findings-acl.481/)。原方法给长篇法律意见标出问题、理由、结论等论证角色；以不同角色标注训练摘要模型产生多个候选，再按候选与原文主要论点的词汇重合重排。1049 份法律文书/摘要对上的 ROUGE 与 BERTScore 优于论文对比基线。

**本项目适配：**适用于担心只保留结论、丢失理由的情况；多候选比较也可借鉴。但原方法依赖法律角色标注、模型训练和长文任务，词汇重合评分不适合直接照搬中文金融短文。若只把 LLM 多次生成后重排，属于新假设，不是这篇论文的已验证结果。

**辅助评测依据，不冒充候选：**[EditorialSum（COLING 2020）](https://aclanthology.org/2020.coling-main.470/)提供意见性文章的论点、理由与可读性评价维度；[ARC（EACL 2026）](https://aclanthology.org/2026.eacl-long.167/)区分论证遗漏与事实错误。这些启发验收指标，不证明上述方法在金融短文上的效果。

**选型前实验：**从真实中文金融文章中抽取广泛概述与细问两类 Query；人工标注每篇的主张、支撑材料、条件/时间、反对观点及原文位置。用相同文章和模型预算比较直接全文阅读、A1、A2、A3 可实现版本的主张覆盖率、误归因率、遗漏率、证据定位率和后续问答正确率。A2 可与 A1 互补，但这种组合收益须由本地实验验证。当前不宣布最终 A 方案。

## 三、研究点 B：多篇文章的观点关联与综合

**研究边界：**在 C 已选出与用户问题有关的文章、A 已逐篇读出主张与依据之后，B 研究如何保留各篇的作者和发表时间，发现同一议题上的一致、互补、分歧及观点变化，再综合回答。B 不决定文章筛选或阅读深度（C），也不把作者共识直接判为事实（D）。不同主题的文章不应强行合并。

**候选资格纠偏：**原 B1 RAPTOR 是长材料层级检索，转到 C 的扩展参考；原 B3 LongT5 是长输入模型，转到 E 的技术参考。它们不与多篇观点综合方法竞争。原 B2 Multi-News 的“先逐篇形成观点卡、按作者日期找冲突”是本项目设想，不是论文原流程，以下按论文原方法重写。

### 候选 B1：Multi-News 多新闻聚合基线

**来源：**[Fabbri 等，ACL 2019，Multi-News](https://aclanthology.org/P19-1102/)。论文建立同一新闻事件的多文档摘要数据集，并把句子相关性与去冗余的 MMR 评分接入 pointer-generator 摘要模型，以覆盖共同事件、减少重复。

**对本项目：**可作为“把相关几篇文章合成一个答案”的对照；但原任务以新闻事件为中心，不负责作者立场、论证条件或时间演变。若直接用其聚合思路，少数观点和旧文/新文差别容易被压平。

### 候选 B2：FacSum 论证侧面覆盖

**来源：**[Wang 等，Findings of EMNLP 2025，Inducing Argument Facets for Faithful Opinion Summarization](https://aclanthology.org/2025.findings-emnlp.876/)。论文先从同主题文档诱导不同论证侧面，再按侧面分组、抽代表短语，引导 LLM 在精炼摘要里覆盖多数与少数观点；原实验在意见摘要与专业论文评审摘要上有收益。

**对本项目：**适合发现黄金、利率、美元等同一问题下被忽略的解释角度，也能避免只写共识。边界是原任务以同一主题的意见集合为前提，不能把库中所有不同主题文章直接混合；论文没有验证金融作者的时序变化或独立证据判断。

### 候选 B3：MoDS 逐文档视角与围绕问题的协调

**来源：**[Balepur 等，NAACL 2025，MoDS](https://aclanthology.org/2025.naacl-long.20/)。论文针对可争论的问题：先规划讨论维度，给不同文档定制问题，由对应文档视角回答，再用协调者将各视角写入来源明确的 outline，最后生成覆盖不同来源和立场的摘要。原实验在争议性问答上提升覆盖与平衡，代价是更多 LLM 调用。

**对本项目：**与“TT、培风客等作者对同一金融问题有哪些共识、分歧、条件”最接近。借鉴其逐篇保留视角、围绕 Query 提问、再综合的原则；不照搬固定二元赞反框架、每文档一个 Subagent 或论文中的检索器。金融短文、日期变化和来源相关性仍须本地实验验证。

**推荐与未决：**优先把 B3 的逐篇视角和协调综合作为 B 的主候选，B2 用作防止漏掉少数论证侧面的对照，B1 作新闻式去冗余基线。作者 A 与 B 同意同一结论，只说明存在跨作者观点一致；若共用原始数据、互相转述或讨论的不是同一时间条件，不能把它当作两份独立事实证据。事实、行情数值和因果判断的外部核验属于 D。

**验收：**用同主题一致、同主题相反、同一作者新旧观点变化、以及主题无关四类真实文章组测试。检查作者覆盖、原文可追溯、少数观点保留、时间/条件不混淆、无关文章不强行合并，并检查输入顺序变化是否改写结论。[DeYoung 等，TACL 2024](https://aclanthology.org/2024.tacl-1.58/)发现多文档摘要对输入顺序敏感，可作为这一测试的依据。以上组合适配尚未在本项目验证，不宣布最终 B 选型。

## 四、研究点 C：主 Agent 如何主动读取、回读、检索、委派和停止

**研究问题：**C 研究控制层：用户问题很散时如何快速扫描，问题很细时如何只深入相关文章，出现证据缺口时如何回读或查外部来源，文章很多时如何委派 Subagent，以及如何证明停止是因为任务完成而不是模型过早放弃。

### 候选 C1：ReAct 推理与行动循环

**证据边界：**主来源是 ReAct: Synergizing Reasoning and Acting in Language Models，https://arxiv.org/abs/2210.03629。它是论文级的通用 Agent 控制研究，并在多类推理和交互任务上验证了推理与行动交错的价值；这些实验不等于中文金融文章场景已经验证。

**论文方法流程：**

```Plain Text
当前目标和已知信息
  -> 产生下一步行动理由
  -> 调用工具或环境
  -> 获得 observation
  -> 将 observation 写回下一轮上下文
  -> 继续行动或结束
```

**论文证明的能力：**工具结果可以进入后续推理，Agent 可以在多个行动之间循环，而不是一次性生成答案。

**没有证明的部分：**论文没有替当前项目定义作者覆盖 Gate、证据充分 Gate 或金融风险分级。

**适配和验证：**把 `article.list`、`article.read`、`web.search`、`web.open`、`delegate` 作为工具；每轮更新 `research.plan`、Coverage 和 Evidence Ledger；测试过早停止、重复调用和错误恢复。

### 候选 C2：ReWOO 预规划与工具执行解耦

**证据边界：**主来源是公开论文 ReWOO：Decoupling Reasoning from Observations for Efficient Augmented Language Models，https://arxiv.org/abs/2305.18323。论文报告在其 HotpotQA 实验中约 5 倍 token 效率和约 4 个百分点准确率提升，并观察到工具失败下的稳健性；这些结果属于论文任务，不能直接当成本项目效果。

**论文机制：**先由 Planner 生成带工具占位符的整体计划，再由 Worker 执行计划中的工具调用，最后由 Solver 读取计划和工具结果生成答案。它把规划和工具 observation 解耦，减少每次工具返回后重复把全部历史提示重新交给模型。

**适合本项目的部分：**当主 Agent 已经确定作者、日期范围和文章 ID 时，可以把多个互不依赖的逐篇读取或网页打开任务作为受控批次执行；每个结果仍必须带 article_id、作者、日期和 EvidenceRef，不能把批次结果拼成无来源摘要。

**不适合默认承担的部分：**金融研究经常要等第一批文章返回后才知道缺少某个作者、存在时间口径冲突或需要追查外部事实。ReWOO 的完全预规划对这些新 observation 不够灵活，若强行全程使用，可能导致计划过时、漏读和过早停止。

**本项目判断：**把 ReWOO 作为可选的批量执行和效率优化策略，而不是 C 的主控制骨架。主 Agent 仍需要保留根据 observation 动态回读、补检索和停止的能力；因此 C2 与 C1 可以在局部协同，但不能替代 C1。

### 候选 C3：STORM 多视角研究规划

**证据边界：**主来源是 STORM: Synthesis of Topic Outlines through Retrieval and Multi-perspective Question Asking，https://arxiv.org/abs/2402.14207；实现参考为 Stanford OVAL STORM，https://github.com/stanford-oval/storm。它是论文级的多视角研究规划研究，主要验证百科式主题研究和长文组织，不等于已经验证金融博主文章分析。

**论文方法流程：**

```Plain Text
识别研究主题和交付物
  -> 生成多个研究视角
  -> 为视角生成具体问题
  -> 检索和收集资料
  -> 发现缺口后继续追问或扩展检索
  -> 形成有结构的综合结果
```

**论文证明的能力：**多视角提问和检索可以改善百科类文章的研究和结构化生成。

**没有证明的部分：**它没有证明所有金融 Query 都需要多视角，也没有证明它能自动选择最适合公众号文章的阅读深度。

**适配和验证：**STORM 只能作为复杂研究的规划插件；普通问题允许跳过。测试简单问题是否被过度规划，以及复杂问题是否能发现多作者、时间、行情和证据缺口。

**C 点当前结论：**C1 ReAct 是主控制骨架；C2 ReWOO 是在工具调用集合已知且边界清晰时使用的批量执行和效率优化策略；C3 STORM 是复杂研究 Query 的可选规划插件。对当前金融文章场景，推荐先选 C1 作为 C\*，再把 C2 作为局部优化，把 C3 作为按需启用的复杂规划能力。三者可以分层协同，但不能把它们混成一条所有问题都必须经过的固定流水线。以上是基于论文机制和场景匹配的架构建议，不代表本项目已经通过真实文章实验。

## 五、研究点 D：观点、论证、事实和证据如何提取与核验

**外部证据采集参考（原 C2 WebGPT，非 D 的事实核验正式候选）：**WebGPT: Browser-assisted question-answering with human feedback，https://arxiv.org/abs/2112.09332。论文让模型在文本浏览器中搜索、打开网页、读取内容并给出引用；这是公开网页资料采集和引用来源的研究依据，不是主 Agent 的全局调度架构，也没有解决本地作者文章逐篇读取或金融因果验证。本项目可借鉴搜索后打开正文、保存 URL 和来源时间，再交给 D 的事实与论证核验方法；不能把搜索摘要或两位作者的共识直接当成事实已证实。

**研究问题：**D 负责把文章中的事实、作者观点、预测、条件判断、原因解释和投资建议分开，并检查“结论—证据”关系。它还要区分作者原话、文章归纳、外部事实和主 Agent 的推断。

### 候选 D1：FActScore 原子事实评估

**证据等级：**S 级的事实粒度评估方法。**主来源：**[FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://arxiv.org/abs/2305.14251)；实现参考：[FActScore repository](https://github.com/shmsw25/FActScore)。

**方法流程：**把一段回答拆成原子事实；为每个事实寻找支持证据；判断支持、部分支持、冲突或缺失；汇总事实级精确性。

**论文证明的能力：**事实级拆分比只看整体摘要更适合评估长文本事实精确性。

**没有证明的部分：**事实被支持不代表因果关系成立，也不代表作者观点已经兑现；金融条件和隐含假设仍需额外抽取。

### 候选 D2：RARR 研究、证据对齐与结论修订

**证据等级：**S 级的研究修订候选。**主来源：**[RARR: Researching and Revising What Language Models Say, Using Language Models](https://arxiv.org/abs/2210.08726)。

**方法流程：**

```Plain Text
已有分析草稿
  -> 识别需要支持的陈述
  -> 为陈述检索资料
  -> 将资料与陈述对齐
  -> 删除、降低强度或修订不被支持的陈述
  -> 保留修订后的证据链
```

**论文证明的能力：**已有生成内容可以通过研究和证据检索进行事实性修订。

**没有证明的部分：**检索到的来源不一定可靠；它不自动判断金融市场中的替代解释，也不替代原文作者观点。

### 候选 D3：ALCE 带引用生成与引用归因

**证据等级：**S 级的引用和归因候选。**主来源：**[ALCE: Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627)；实现参考：[ALCE repository](https://github.com/princeton-nlp/ALCE)。

**方法流程：**生成候选答案；识别需要引用的陈述；选择来源；检查来源对陈述的支持关系；引用不匹配时修改陈述或更换来源；输出带引用的答案。

**论文证明的能力：**引用质量可以单独评估，不能只看答案是否有链接。

**没有证明的部分：**引用正确不等于论证正确，尤其不能自动证明“美元下跌导致黄金上涨”。时间对齐和因果替代解释仍需金融验证。

### D\* 暂定方案：按陈述类型路由的 Typed-RARR

**定位：**Typed-RARR 是本项目基于 RARR 的金融场景适配方案，不是某篇论文已经直接验证的完整系统。它把 RARR 的“发现陈述、检索证据、对齐、修订”作为主流程，再用 FActScore 的原子事实检查和 ALCE 的引用归因约束结果。它是动态核验策略，不要求所有 Query 都经过相同的固定流水线。

```Plain Text
文章卡片或初始答案
  -> 拆分独立陈述并识别类型
  -> 按类型选择核验目标
  -> RARR 检索并对齐证据
  -> 必要时独立复核或补查
  -> 保留、降级、修订或删除
  -> ALCE 绑定 EvidenceRef
  -> FActScore 检查事实性陈述
```

**观点：**检查是否准确转述原作者、是否保留语气、条件和时间范围。原文是“作者说了什么”的证据，不是观点正确性的证明；不能把作者观点改写成客观事实。

**事实：**拆成原子事实，分别检查日期、数字、行情、事件和口径，并绑定原文、行情接口或可靠外部来源。证据不足时标记为未核验，不自动等同于错误。

**预测：**记录预测内容、提出者、提出时间、预测期限、成立条件和当前状态。未到验证时间只能标记为尚未验证，不能提前判定正确或错误。

**因果：**分别检查事件是否发生、时间是否对齐、原文是否明确提出因果、是否存在替代解释。仅有同时发生或相关性时，输出“作者归因”“可能解释”或“尚不足以确认因果”。

**模型推断：**必须列出使用的文章、外部事实和行情证据，并标明哪些是来源直接表达、哪些是模型综合。模型推断不能伪装成作者原话或已验证事实。

**来源角色：**文章原文证明作者表达；行情和公开资料证明可观测事实；多作者一致只证明存在观点共识，不能自动升级为独立事实证明；模型推断必须回指其输入证据。

**修订保护：**RARR 只能修订主 Agent 新增的事实性陈述和推断。对作者观点优先做保真修订，不因外部资料不一致就删除作者观点；必要时改写成“作者认为”并保留原文 EvidenceRef。

**结构化复核结果：**每条结果至少保存 `claim_id`、`claim_type`、`status`、`evidence_refs`、`conflicts`、`alternative_explanations`、`suggested_revision` 和 `as_of`。状态至少区分支持、反驳、证据不足、尚未到验证时间和观点保真。

**参考：**Chain-of-Verification 提供先生成独立核验问题、再独立回答、最后修订的思路；FEVER 提供支持、反驳和信息不足的事实核验标签。两者是辅助依据，不代表金融文章适配已经被论文直接证明。

**D 点当前结论：**暂定 D\* = RARR 作为主流程 + FActScore 作为原子事实检查 + ALCE 作为证据引用和可追溯性约束。三者不是已经被论文直接证明的完整组合，而是本项目的适配架构。下一步重点是按“观点、事实、预测、因果、模型推断”对陈述分类，再为不同类型选择核验目标和修订规则；具体设计记录在下方“Typed-RARR”小节。组合效果仍需用真实金融文章和 Query 实测。

## 六、研究点 E：中间结果和上下文如何组织、压缩、恢复与回溯

**长材料分层检索扩展参考（原 B1，曾暂列 C，非 E 正式候选）：**RAPTOR，ICLR 2024，https://arxiv.org/abs/2401.18059。它通过文本切块、聚类、递归摘要组织长材料并进行不同粒度的检索，属于材料索引与上下文载入问题，而不是主 Agent 调度算法。当前公众号短文应优先逐篇保留全文和来源；只有接入长篇研报或大型语料时再评估 RAPTOR，并检验摘要树是否丢失作者观点、日期和原文定位。

**研究问题：**E 不只是“减少 Token”，而是决定文章卡片、原子主张、证据账本、未决问题和工具结果如何分层保存；在上下文过长时保留什么；需要细节时如何从文件或文章正文恢复；如何避免不同文章的结果混淆。

### 候选 E1：LLMLingua 任务感知提示压缩

**证据等级：**A 级的压缩方法。**主来源：**[LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models](https://arxiv.org/abs/2310.05736)；实现参考：[Microsoft LLMLingua](https://github.com/microsoft/LLMLingua)。

**方法流程：**读取任务目标；估计片段重要性；压缩低价值或重复内容；保留关键证据和恢复索引；将压缩视图交给模型；必要时回到原文。

**论文证明的能力：**提示压缩可以降低输入成本，并在特定任务上保持一定效果。

**没有证明的部分：**它没有证明压缩后金融因果证据不会丢失，也没有证明压缩视图可以替代原始文章。

### 候选 E2：MemGPT 分层记忆与上下文换入换出

**证据等级：**A 级的 Agent 状态管理方法。**主来源：**[MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)；实现参考：[MemGPT repository](https://github.com/cpacker/MemGPT)。

**方法流程：**把当前任务和高价值证据放入工作上下文；把文章卡片、证据账本和未决问题保存到外部状态；需要细节时按 ID 换入；完成阶段后更新状态并释放上下文。

**论文证明的能力：**有限上下文可以通过外部记忆和显式换入换出扩展到更长的任务。

**没有证明的部分：**错误记忆、权限越界和旧文章污染是当前金融场景的额外风险；必须由 Runtime 和 EvidenceRef 约束。

### 候选 E3：目前不把 Lost in the Middle 单独列为正式方案

**处理结论：**[Lost in the Middle](https://arxiv.org/abs/2307.03172) 是重要的诊断证据，说明模型可能忽略长上下文中间的信息，但它不是上下文管理系统，也没有提出完整的压缩、记忆或证据恢复流程。因此它只能作为 E1、E2 的设计约束和实验依据，不能冒充 E3 正式候选。

**长输入模型参考（原 B3，非 E 正式候选）：**[LongT5，Findings of NAACL 2022](https://aclanthology.org/2022.findings-naacl.55/)改进模型处理长序列的注意力机制，是模型层技术，不负责文章关联、作者观点或证据核验。仅在未来更换底层模型或长输入实验时比较，不把它写成当前 Runtime 已采用的能力。

### E\* 暂定方案：MemGPT 式分层上下文管理与证据可恢复

**定位：**E\* 是本项目基于 MemGPT 的金融场景适配方案，不是把 MemGPT 原样移植，也不是某篇论文已经直接验证的完整金融 Agent。由于当前文章篇幅不长但观点密度高，默认优先保留原文和来源；只有上下文压力真实出现时才换出、恢复或压缩。

```Plain Text
原始资料层：文章正文、作者、日期、路径、article_id
        -> 证据层：ArticleCard、ClaimSet、EvidenceRef、VerificationResult
        -> 任务层：research.plan、未决问题、工具结果、Subagent 结果
        -> 派生视图：摘要、时间线、观点矩阵、压缩上下文
        -> 需要细节时按 article_id 或 claim_id 恢复原文和证据
```

**不可变原则：**原始文章和原始证据不能被摘要、压缩结果或模型推断替代。派生视图必须记录 `derived_from`、生成时间、生成方式和覆盖范围；需要精确引用或发现冲突时必须回读原文。

**历史候选（不作为当前强制流程）：**文章较少时可以逐篇完整读取；文章较多时也可以先逐篇形成带来源的 ArticleCard，再按当前问题载入相关 ClaimSet。是否形成 ArticleCard、是否持久化中间状态以及如何换入上下文，均由主 Agent 按当前问题决定；上下文接近上限时不能默认压缩原始文章。

**上下文顺序验收：**Lost in the Middle 只作为诊断依据。系统应通过按文章、作者和证据 ID 精确载入，减少把大量全文拼成一个长上下文；验收时改变文章顺序，检查结论、作者覆盖、少数观点和 EvidenceRef 是否保持稳定。

**污染和权限约束：**运行状态必须区分 run-local 中间结果与长期 Wiki；旧文章、其他工作空间和未授权路径不能自动进入当前上下文。所有换入操作记录来源 ID、权限判断、上下文版本和恢复位置。

**压缩保护：**压缩只能生成可丢弃的视图，不能删除原始文章、Evidence Ledger、未决问题和恢复路径。压缩后如果无法定位原文段落、作者、日期或证据状态，该视图不能用于最终结论。

**与其他研究点的接口：**A1 输出的 ArticleCard 是 E 的可恢复对象；B3 的作者时间线和观点矩阵是派生视图，必须保留文章 ID 和日期；C1 决定何时读取、换入、回读或委派；D\* 的 EvidenceRef 和 VerificationResult 优先级高于普通摘要。

**不纳入当前正式方案：**RAPTOR 仅作为未来长篇研报或大型语料的分层检索扩展；LongT5 和 LongMem 属于模型层改造，需要更换或训练底层模型，不适合当前插件化 Runtime。LLMLingua 保留为上下文压力下的可选压缩插件。

**E 点当前结论：**暂定 E\* = MemGPT 式分层上下文管理与证据可恢复设计。LLMLingua 作为真实上下文压力下的可选压缩插件；Lost in the Middle 作为上下文顺序和信息利用稳定性的验收依据；RAPTOR 只作为未来长篇研报扩展。当前不再强行寻找第三个正式候选。

## 七、五个研究点的接口与组合冲突

五个研究点在职责上基本正交，但局部最优不能直接相加。组合时必须遵守以下接口：

1. **A 输出给 D：**必须同时传递文章主线、原子主张和 EvidenceRef。D 不能只收到脱离上下文的句子。
2. **B 输出给 A 和 E：**B 的分层节点必须带文章 ID、段落范围和父节点；B 不得把多篇文章合并为一个无来源摘要。
3. **C 调度 A、B、D、E：**C 决定是否调用和调用几次，但完成 Gate 负责检查覆盖、证据和错误状态，避免动态性变成提前停止。
4. **D 输出给 E：**D 的 Evidence Ledger 必须比普通摘要优先级更高；压缩只能生成视图，不能删除原始证据和恢复路径。
5. **E 不能改变语义：**E 可以改变上下文的组织和载入方式，不能未经标记地修改作者观点、事实或证据状态。

所以五个研究点不是互相冲突的五套系统，而是不同职责的层级。真正的冲突发生在边界：A 的完整性与 E 的压缩、B 的分片与 D 的论证连续性、C 的灵活停止与任务覆盖要求。每个冲突都必须通过接口字段和测试解决。

## 八、历史组合草案（已失效，待重审）

以下仅保留早期组合思路的历史记录。它们建立在已经降级的 PaperQA、RAPTOR 等旧 A/B 候选上，不是当前推荐，也不能用于声称 A 或 B 已完成选型。等 C～E 研究和真实文章对照实验结束后，再另建有效的整体组合。

### 组合一：文献证据优先

**候选：**PaperQA + RAPTOR + ReAct + FActScore + ALCE + LLMLingua。

**已有证据：**各组件分别在文献研究、长文层级检索、工具交互、事实级评估、引用归因和提示压缩任务中有论文或代码支持。

**本项目假设：**把它们通过 Article Card、ClaimSet 和 Evidence Ledger 连接后，能够提升金融文章分析质量。这个假设尚未被任何一篇论文直接证明。

**主要风险：**组件较多，链路较长；压缩或原子拆分可能破坏作者主线；需要高质量 Trace 和回读测试。

### 组合二：动态研究优先

**候选：**PaperQA + RAPTOR + ReAct + STORM + RARR + MemGPT。

**已有证据：**各组件分别有文献研究、长文处理、工具循环、多视角研究、证据修订和外部记忆的研究依据。

**本项目假设：**主 Agent 可以根据 Query 复杂度动态选择浅读、深读、外部检索和 Subagent，同时维持跨轮状态。

**主要风险：**可能过度规划、过度检索、记忆污染或迟迟不停止；必须有预算和完成 Gate。

### 组合三：当前公众号文章的保守增强

**候选：**PaperQA 的来源化文章结果 + Chain-of-Note 的逐篇笔记 + ReAct 的动态调度 + FActScore 与 ALCE 的关键结论核验 + MemGPT 的结构化中间状态。

**已有证据：**每个组件的单点能力都有来源，且没有把长文递归、复杂规划和压缩默认加入每次运行。

**本项目假设：**对于篇幅不长但观点密集的文章，逐篇笔记和关键证据核验比全量递归摘要更适合。

**主要风险：**“关键结论”如何定义需要主 Agent 动态判断；如果判断标准过松，仍可能漏掉文章的重要细节。

**当前状态：**以上组合仅是历史草案，均已暂停，不再排序。用户目前暂定 A1、B3、D\*（RARR 主流程 + FActScore 原子事实检查 + ALCE 证据引用）和 E\*（MemGPT 式分层上下文管理与证据可恢复）。C 推荐以 C1 ReAct 为主控制骨架，仍需通过真实金融文章 Query 验证。研究选型阶段暂告一段落，后续进入接口定义、代码实现和验收。

## 九、最终选型的证据链

1. 先核对论文原文和公开代码，确认方法流程与候选描述一致。
2. 分别记录论文评测任务、数据集、指标和限制，不能只记录论文标题。
3. 用当前文章构造单点测试，区分论文能力是否能迁移到中文金融公众号文章。
4. 用相同 Query 对比组合候选，测文章覆盖、观点保留、证据支持、冲突保留、答案完整度和 Trace 可解释性。
5. 从最终结论反向回读到 Article Card、ClaimSet、EvidenceRef 和原文段落，任何断链都不能称为通过。
6. 只有在“论文证据 + 单点实验 + 接口实验 + 组合实验”都通过后，才能把某个候选写成最终方案。

## 十、最终选型记录

```Plain Text
A* = ____________________（主论文、证据等级、适配实验）
B* = ____________________（主论文、证据等级、适配实验）
C* = ____________________（主论文、证据等级、适配实验）
D* = ____________________（主论文、证据等级、适配实验）
E* = ____________________（主论文、证据等级、适配实验）
组合层 = 多文章聚合 / 行情核验 / Subagent / 其他：____________________
```

最终方案选择不是“论文名投票”，而是基于证据和实验的工程决策。论文证明的是原始任务中的能力，当前项目还必须证明：它能处理中文金融文章、能保留作者主线、能回溯证据、能与其他研究点连接，并且不会在动态流程中丢失用户目标。

**接口关系：**A1 的 Article Card 是 D 的观点和证据提取输入，也是 E 的压缩对象；原文正文和 EvidenceRef 必须始终保留，不能被卡片替代。



# 当前项目设计（以代码实现为准）

本节记录当前金融 Agent 的实际设计主线，作为阅读 Trace 和定位问题的共同基准。这里区分“应该发生什么”和“本次运行实际发生了什么”：前者是架构设计，后者要以 Run、AgentStep、Tool Audit、Gate 和 Evidence 记录为准。

## 一、项目边界

- 项目是一个受控的金融研究 Agent，不是固定模板的报告生成器。
- 主 Agent 负责理解问题、决定是否读取 Wiki、文章、行情、公开网页或调用 Subagent，并综合最终答案。
- Runtime 负责统一接住每轮请求，构造上下文，执行模型和工具调用，检查权限，记录 Trace，并保存可复用产物。
- 工具和 Subagent 是可选插件。系统提示词只规定数据边界、证据要求和可用能力，不规定每个问题必须经过同一条流程。

## 二、统一运行链路

```
用户 Query
  -> AgentRuntime 创建 AgentRun
  -> 读取本轮可见 Wiki、工作空间路径和长期记忆
  -> 主 Agent 先解析 Query 并创建简洁但完整的 research.plan
  -> AgentLoop 自主决定工具、Skill、文章读取、行情核验或 Subagent
  -> ContextManifest 构造本轮模型可见上下文
  -> ModelGateway 产生下一步动作
  -> ToolGateway + PermissionEngine 执行或拒绝工具
  -> Gate 检查证据、权限、产物、计划和委派结果
  -> 更新 Todo，主 Agent 综合答案
  -> AgentStep / Trace 持久化全过程
```

每轮金融 Query 都先创建简洁但完整的研究计划；普通闲聊可以不进入金融研究链路。研究 Query 再根据实际需要展开为文章读取、行情核验、网页查证、Subagent 复核和最终综合，不要求每轮调用所有模块。

## 三、研究型 Query 的分流规则

1. **先判断任务范围并建计划。**每轮金融 Query 都先由主 Agent 识别研究对象、时间窗口、主题、交付物、所需证据和完成条件，并创建一份简洁但完整的 `research.plan`。简单问题也要覆盖答案和必要的完成检查，复杂问题按用户目标拆分；计划只防止目标漂移，不规定读取顺序、RAG、分析方法或 Subagent。
2. **需要本地文章时先看元数据。**调用 `article.list` 获取作者文件夹、日期和文章 ID。它只返回目录信息，不能直接当作文章观点。
3. **文章按独立单位读取。**对选中的文章逐篇调用 `article.read`。如果范围很大，主 Agent 可以把明确的文章 ID 分配给受控 analyst 批次，不能把几十篇全文无边界拼入主上下文。
4. **需要外部事实时查正文。**`web.search` 用于发现公开页面，`web.open` 用于读取页面正文并形成网页证据；搜索摘要不能替代正文。
5. **问题涉及行情事实或同期表现时查行情。**先用 `market.catalog` 确认品种，再用 `market.snapshot` 获取数据，最后按需用 `market.metrics` 或 `market.series` 计算和展开区间变化。文章作者声称的行情不能代替行情工具返回值。
6. **需要多文章比较时分层聚合。**先形成文章级观点卡片，再形成作者时间线和跨作者观点矩阵，最后交给主 Agent 综合；不能把文章目录或标题直接升级为事实。
7. **需要复核时动态委派。**主 Agent 根据问题和上下文决定是否调用 analyst、skeptic 或 verifier。Subagent 的结果只能作为受限研究结果回填，最终答案仍由主 Agent 负责。

## 四、文章读取与上下文组织

```
单篇文章正文
  -> 文章观点卡片（作者、日期、对象、观点、预测、条件、解释、证据）
  -> 同作者时间线
  -> 跨作者观点矩阵
  -> 行情和外部事实对齐
  -> 主 Agent 综合
```

- 观点卡片用于减少长上下文压力，但不能替代必要的原文证据。需要精确引用时必须回读文章正文。
- 文章中的事实、作者观点、作者预测、作者解释和模型推断必须分开。
- 大范围读取时优先保留文章 ID、日期、作者、观点陈述、证据引用和未决问题；不要只保留一个脱离来源的结论。
- 当前代码已提供文章目录、逐篇读取、可选 claims 提取、研究计划和受限上下文；是否完整覆盖用户指定作者，仍需结合本次 Trace 验收。

## 五、Subagent 的边界

- **Analyst：**在主 Agent 分配的文章 ID 和工具权限内逐篇提炼观点，返回结构化 `claim_set.v1`，包括 claim、statement、evidence_refs 和 confidence 等字段。
- **Skeptic：**寻找矛盾、反例、时间口径问题、因果跳跃和替代解释。
- **Verifier：**核查关键事实、数字、行情和引用是否被证据支持，返回通过、不通过或未核验状态。
- 每个子任务必须带有 parent run、角色、任务范围、可见上下文和允许工具。子 Agent 不能越过 Runtime 直接读取任意文件，也不能直接发布最终答案。
- 委派不是固定流水线。没有必要时可以不委派；文章很多、证据冲突或结论风险较高时，主 Agent 可以委派。

## 六、证据链与结论分级

```
作者说了什么
  -> 原文 EvidenceRef
实际发生了什么
  -> 行情、网页或其他可核验来源
作者如何解释
  -> 作者原文中的解释
模型如何推断
  -> 明确标为模型推断或假设
  -> Verifier / Gate 检查后再输出
```

- 文章路径和文章正文是文章观点证据；行情快照和确定性指标是行情事实证据；`web.open` 的页面正文是外部网页证据；Wiki 是用户背景和研究偏好的证据。
- 高风险结论必须绑定对应 EvidenceRef。证据不足时应写为“未核验”“待确认”或“文章提出的解释”，不能写成已证实因果。
- “时间上同时发生”不等于“因果关系成立”。金融问题要分别检查事实是否发生、时间是否对齐、是否存在替代解释。

## 七、Trace 应该记录什么

- **Run：**用户 Query、工作空间、运行状态和最终结果。
- **Context：**本轮可见字段、隐藏字段、预算、估算和校验信息。
- **Step：**意图判断、技能选择、上下文构造、记忆读取、模型调用、工具调用、委派、Gate、记忆写入和最终回答。
- **Tool Audit：**工具名、参数范围、allow/deny/ask 决策、返回状态和错误。
- **Delegation：**角色、任务、输入证据范围、输出契约、父子 Run 关系和完成状态。
- **Evidence / Artifact：**证据引用、验证状态、产物路径和摘要。Trace 是运行证据，不是把所有模型内部思考原文暴露出来。

## 八、如何区分架构问题和代码 Bug

1. **设计要求没有发生：**先问架构是否明确要求它发生。例如 Query 指定比较 TT 和培风客，设计应要求两个作者都进入候选和读取覆盖范围。
2. **设计要求明确，但工具没有正确执行：**如果 `article.list` 应该返回作者却返回空，检查路径、索引、日期过滤和分页，这是数据或工具 Bug。
3. **工具返回了文章，但后续没有读取：**这是 AgentLoop 的调度或停止条件 Bug；如果系统没有“作者覆盖完成”的检查，则同时是架构缺口。
4. **文章读到了，但观点被混写：**检查 claims 结构、EvidenceRef 粒度和主 Agent 的综合提示，这是分析流程或证据绑定设计问题。
5. **用户要求同期行情，但没有 market 调用：**不是作者文章的证据不足，而是研究任务没有完成，属于调度缺口；若工具已调用但返回错误，才是数据源或工具 Bug。
6. **Trace 显示完成但答案没有回答问题：**检查最终 Gate 是否只检查流程结束，没有检查任务覆盖和答案内容，这是验收设计问题。

## 九、当前已知边界

- 当前系统已经具备 Runtime、插件工具、文章逐篇读取、行情工具、网页工具、研究计划、受控 Subagent、Evidence Ledger、Gate 和 Trace。
- 这些能力存在不等于每次运行都会自动调用。当前主 Agent 保持动态决策，因此必须通过 Trace 检查本轮是否真的完成了用户要求。
- 已出现过“一个作者读取较充分、另一个作者没有完成读取”的运行现象。它说明文章获取链路仍需要作者覆盖验收，不能只看最终回答是否有文字。
- 当前设计的优先级是：先保证研究对象和时间范围覆盖，再保证文章观点被分层聚合，最后保证重要结论有独立证据核验。

## 十、Trace 验收示例

对于“TT 和培风客在已有文章中对黄金的看法有什么变化，哪些解释与同期行情相符，哪些仍缺证据？”这类问题，期望的可审计链路是：

```
1. 识别两个作者、黄金主题、时间范围、观点变化和同期行情核验目标
2. 分别 article.list 两个作者的文章元数据
3. 分别 article.read 选中的文章，不能只看标题和目录
4. 文章较多时，按明确 article_id 委派 analyst 批次提炼 claim_set.v1
5. 按作者建立时间线，再建立跨作者观点矩阵
6. 调用 market.catalog、market.snapshot、market.metrics 或 market.series 核验行情
7. 对关键数字、解释和冲突按需调用 skeptic / verifier
8. 主 Agent 输出：作者观点、实际行情、相符程度、替代解释、证据缺口
9. 若某个作者或行情没有完成覆盖，最终答案必须明确披露，不能宣称比较已完成
```

**阅读原则：**看 Trace 时先按本节的“期望链路”检查步骤是否存在，再判断存在的步骤是否返回正确结果。缺步骤通常是架构或调度问题；有步骤但结果错误通常是代码、数据或工具 Bug；结果正确但结论过强通常是证据绑定或最终回答 Gate 的问题。

## 十一、System Prompt、Skill 与 Runtime 代码索引

下面是当前金融 Agent 的核心实现入口。代码路径以当前仓库为基准，文章根目录由 Workspace 配置解析，不在设计文档中写死机器路径。

- **总体 System Prompt：**`plugins/prompts/finance/00_system.md`。定义金融 Agent 的总体目标、事实/观点/推断边界、每轮先识别 Query 并创建研究计划，以及工具和 Subagent 的自主选择边界。
- **研究计划 Skill：**`plugins/prompts/finance/40_plan.md`、`plugins/skills/finance/research_plan.md`。定义 Todo 的目标、scope、进度和 blocked 语义；不规定具体分析链路。
- **资料与证据 Skills：**`plugins/prompts/finance/10_tools.md`、`plugins/prompts/finance/20_evidence.md`、`plugins/prompts/finance/30_delegation.md`，以及 `plugins/skills/finance/source_access.md`、`evidence_review.md`、`subagent_review.md`。它们提供可选能力说明，主 Agent 按 Query 自主选用。
- **Skill 装配配置：**`plugins/skills/manifest.json`。登记金融对话的 Prompt、Skills、工具包和可用 Subagent 角色。
- **主 Runtime：**`src/policy_impact/harness/agent_runtime.py`。绑定 Workspace、组装 Prompt/Skill、构造金融 Research Gateway、驱动 Agent Loop、执行最终 Gate 并记录 Trace。
- **Agent Loop：**`src/policy_impact/harness/agent_loop.py`。执行主 Agent 的 tool_call、delegate、final 动作和 Gate 重试。
- **金融 Research Gateway：**`src/policy_impact/domains/finance/research_gateway.py`。绑定作者目录、逐篇文章、公开网页、行情、证据账本和 run-local 研究计划工具。
- **研究计划实现：**`src/policy_impact/domains/finance/research_plan.py`。负责目标创建、scope 结构化、pending/done/blocked 状态和受控持久化。
- **文章数据实现：**`src/policy_impact/domains/finance/article_corpus.py`、`article_gateway.py`。按 Workspace 配置解析文章根目录，先取元数据，再逐篇读取正文。

### 当前设计约束（保证完整、不限制方法）

1. 每轮金融 Query 先创建一份简洁但完整的研究计划，保证用户目标、证据需求和完成条件不会在长链路中丢失。
2. 计划完成或明确 blocked 后才允许最终回答；它不规定工具顺序、分析方法或是否使用 Subagent。
3. Runtime 只对权限、来源范围、证据绑定和最终交付做确定性校验；具体研究路径由主 Agent 动态决定。

- ? Agent ?????? Query?????????????????????????????????? Subagent?????????
- Skill ???????????????????????????????????
- Runtime ????????????????????????EvidenceRef ???Subagent ???Gate?Artifact ? Trace?
- Final Hook ??????????????????????????????????????? Agent ???????
- ????? Wiki????????????????????????????

1. `article.list` ???????????????????????????????????
2. `article.read` ????????????????????????????????
3. ????????????????? Agent ?????????`article.study` ????????? analyst?
4. `article.claims` ? `article.study` ??????????????????????????
5. ???????? ArticleCard????????????????????????????????? ID??????? EvidenceRef?
6. ???????????????????????????????????????????????????

- ???????????????
- ?????????????????????????????
- ?????????????????????????????
- ???????????????????????? Subagent?
- ??????????????????????
- ????????????????????????????
- ???????????????????????????
- ? `web.search` ????????? `web.open` ?????
- ? `market.snapshot`?`market.series` ? `market.metrics` ?????????????
- ? `research.verify` ???? EvidenceRef ???? Claim ???
- ?? `analyst`?`skeptic` ? `verifier` ??????????????????
- ??????????????????????
- ???????????
- ????????????????
- ??????????????
- EvidenceRef????URL?????????????
- Subagent ????????????
- ??????????????????
- ?????????????
- ??????? Agent ????????

1. ?? Query ???????????????? blocked?
2. ???????????????????????
3. ?????????????
4. ??????????????????????????
5. ?????????????
6. ?????? Final Hook??????????????

# 十二、当前实现基线：自主研究、验证阶段与最终答案审核

> 更新时间：2026-10-10
> 
> 本节是当前代码实现对应的权威设计补充。前文的论文候选、组合草案和旧流程保留为研究历史；如果前文与本节冲突，以本节和代码当前实现为准。

## 12.1 设计目标与职责边界

本项目是一个受控的金融研究 Agent，不是固定模板的报告生成器。

- 主 Agent 负责理解用户 Query、制定研究计划、选择文章和工具、组织分析、判断验证方式、决定是否委派 Subagent，并生成最终答案。
- Skill 负责描述可选能力、工具用途和判断边界，不把所有问题强制成同一条工作流。
- Runtime 负责工具注册、权限、路径隔离、来源是否真实读取、EvidenceRef 绑定、Subagent 隔离、Gate、Artifact 和 Trace。
- Final Hook 只审核候选答案的证据可追溯性、引用有效性、主张分类、证据缺口和发布安全，不替主 Agent 决定分析方法。
- 用户提供的 Wiki、研究偏好和持仓是用户上下文，不自动升级成外部市场事实。

核心原则：不限制模型如何分析，但限制模型能依据什么、能调用什么、能写入什么。

## 12.2 当前统一运行链路

```text
用户 Query
  -> AgentRuntime 创建 AgentRun
  -> 构造本轮可见 Wiki、记忆、工作空间路径和授权能力
  -> 主 Agent 识别 Query 并创建 research.plan
  -> 主 Agent 自主选择 article / web / market / artifact / Subagent 能力
  -> Runtime 执行工具、权限检查并写入 EvidenceLedger
  -> 主 Agent 形成文章观点、事实、推断或预测分析
  -> 进入验证阶段：判断哪些结论需要验证、选择验证方式、记录结果和缺口
  -> 主 Agent 根据验证结果继续取证、修订、降级或保留结论
  -> 生成候选答案
  -> AnswerAudit Final Hook 检查证据绑定、引用和发布安全
  -> 不通过则回到主 Agent 修订或披露限制；通过则发布答案
  -> AgentStep / Tool Audit / Evidence / Artifact 持久化全过程
```

这是一条职责链，不是固定工具序列。简单问题可以只读取一篇文章并完成简短验证；复杂问题可以读取多篇文章、调用行情和网页工具、委派 Subagent，再多轮修订。

## 12.3 文章定位、读取和分析

文章库中的每篇文章是独立研究单位。

1. `article.list` 按作者目录、日期和元数据定位候选文章。列表结果只是候选，不是正文证据。
2. `article.read` 逐篇读取正文。只有本轮实际读取的路径和行号才能支持作者观点归因。
3. 文章较短时可以读全文；文章较多时主 Agent 自主决定逐篇读取、`article.study` 深读或委派有边界的 analyst。
4. `article.claims` 和 `article.study` 可以提炼作者主张，但输出仍是作者观点，不是外部事实。
5. 多篇文章可以形成 ArticleCard、作者时间线、跨作者观点比较或研究笔记，但所有派生结果必须保留文章 ID、作者、日期和 EvidenceRef。
6. 未被最终答案使用的已读文章不自动成为答案引用义务；答案需要覆盖实际使用的来源，而不是所有读取过的来源。

当前工具名称以 `article.list`、`article.read`、`article.claims` 和 `article.study` 为准。旧文档中的 `article.search` 仅保留为历史表述，不代表当前接口。

### 12.3.1 按证据覆盖收敛的取材决策（2026-10-11 选定，2026-10-12 部分验收）

普通作者画像、概括与跨文研究默认采用代表性样本，并在答案中披露作者、时间窗口、实际已读篇目和未覆盖范围。只有用户明确要求全部文章或逐篇阅读时才进入穷尽模式；“只出现一次”默认仅指已读样本，不能推断全库仅一次。

1. 主 Agent 先从用户问题提取需要回答的维度与时间、作者边界，article.list 只用于发现候选，不等于已读正文。
2. 主 Agent 自主选择下一篇：优先能补尚缺维度、不同时间阶段、可能反例或重要条件的文章；可逐篇浅读判断相关性，再对关键短文全文深读。每篇的观点、条件、日期和 EvidenceRef 独立保留。
3. 每轮取材后形成可审计覆盖判断：每个维度有哪些正文依据，是否见到相反或限定性证据，哪些仍缺，下一次读取预计补什么。判断是主 Agent 的职责，不按固定篇数自动判充分。
4. 若各维度已有可回答的证据、重要反例或条件已检查、继续取材预计不会改变主要判断，停止取材，转入综合、验证和交付；若仍有实质缺口，针对缺口继续读或查外部来源。信息不足时输出带范围和不确定性的答案，不用无穷取材换取虚假的完整性。
5. Runtime 只守边界：防止无新增信息的重复读取或委派，预留综合与审核调用，外部工具超时后允许换来源或说明缺口；硬预算是失控保护，不是研究充分性的证明。到边界仍须交付已知内容与未完成范围。

Trace 应能回溯候选范围、实际已读文章、逐维度的支持与反例、继续或停止理由、剩余缺口以及最终答案对应的正文证据。验收重点为 Q07/Q09 是否在形成画像后收敛并交付，Q25 是否在外部来源失败时保留 Wiki、作者观点与已得市场事实的分层结论。该策略扩展现有 C1 ReAct 控制层，不把作者画像变成固定工作流，也不为每个维度设置统一的文章数量门槛。

实施与验收（2026-10-12）：research.coverage 已记录问题维度、EvidenceRef、反例、缺口及继续/综合理由；Runtime 只守防重复、权限与综合/审核预算。Q07、Q09、Q25 已交付但仍为 disputed_partial。充值后 Q22 在普通聊天入口真实复跑五轮，不再出现 HTTP 402；已实际读取 TT、培风客、Spread Trading 文章、美联储声明和 SEP HTML，执行独立 subagent 复核并交付实质分析。针对实测问题已修复：会议页只抽出页眉而丢失正文链接、长网页缺少续读位置、网页证据账本只保留前 1600 字、审核对无行号数值主张找不到相关表格行、审核将无关长网页纳入每段判定，以及审核不完整时整篇退为材料清单。全量测试 2009 passed、7 skipped。Q22 当前仍为部分通过而非最终通过：最后一轮答案审核 coverage_complete=false，密集 SEP 表格的片段选择仍偶发遗漏或错配，终稿还把三位作者都说成描述了目标区间（实际上本轮已读文章中仅 Spread Trading 明确给出）；争议已标注，但这些表述不能当作高可信结论。下一步应按单条事实主张选取官方表格原文并校验作者覆盖归纳，再复验 Q22。success 仅表示交付，不等于事实全部核实。

## 12.4 分析完成后的验证阶段

验证阶段是每轮实质分析后的必经阶段，但验证方法由主 Agent 自主决定。

验证阶段至少需要判断：

- 最终准备表达的关键结论是什么；
- 每个结论是作者观点、客观事实、预测、因果解释还是模型推断；
- 哪些结论已经有直接证据，哪些只是来源之间的关系或模型归纳；
- 哪些结论需要独立网页、行情数据、时间序列、反例或 Subagent；
- 哪些内容只需要保持原文归因，不需要外部验证；
- 当前结果属于支持、反驳、观点一致、合理推断还是证据不足。

可以选择的验证方式包括：

- 用已读取的文章正文和行号核对作者是否真的表达了该观点；
- 用 `web.search` 发现公开资料，再用 `web.open` 读取正文；
- 用 `market.snapshot`、`market.series` 和 `market.metrics` 核对行情、日期和统计口径；
- 用 `research.verify` 对已观察 EvidenceRef 做结构化 Claim 复核；
- 委派 `analyst`、`skeptic` 或 `verifier` 做分篇提炼、反例检查或独立来源核查；
- 明确记录某个观点、定义或归因不需要外部验证。

不要求每轮使用以上全部能力，也不要求固定角色顺序。搜索摘要、作者共识、同期变化和 Subagent 意见都不能自动升级为客观事实或已证实因果。

## 12.5 Claim、Evidence 和验证状态

可审计结论使用统一的主张类型：

```text
author_view          作者或机构观点
objective_fact       客观事实或可核对数据
prediction           带时间范围和条件的预测
causal_explanation   因果解释或机制解释
model_inference      主 Agent 基于资料的推断
personal_context     用户背景、偏好和持仓上下文
```

验证结果使用以下状态：

```text
viewpoint_faithful   准确保持作者归因
supported            被口径匹配的独立证据支持
refuted              被直接相反证据反驳
reasoned             有明确前提的分析推断，但不是客观事实认证
not_due              预测尚未到期，且时间范围可解析
insufficient_evidence 证据不足，不能判断
```

边界：作者之间的一致只能证明观点关系；相关性和同期变化不能证明因果；预测必须保留条件和时间范围；`supported` 和 `refuted` 必须绑定本轮实际读取且口径匹配的 EvidenceRef；证据不足时不能为了完整而编造反证。

## 12.6 Hook 与 Runtime 的审核边界

主 Agent 的验证决策不由 Hook 代替。Runtime 和 Hook 的确定性职责是：

- 工具是否在授权范围内；
- 文件和文章是否属于当前工作空间；
- 引用的文章是否本轮真实读取；
- EvidenceRef、行号、URL、行情品种和日期是否有效；
- Subagent 是否只访问分配的上下文；
- 候选答案中的关键主张是否有对应证据；
- 未解决的证据缺口是否披露；
- 是否需要退回主 Agent 修订或降级措辞。

Hook 不强制联网、不强制 `research.verify`、不强制启动 Subagent，也不要求答案提及所有读取过的文章。它是发布安全和可追溯性检查，不是固定金融分析模板。

## 12.7 Trace 观察标准

每次研究至少应能够从 Trace 中判断以下阶段是否发生：

```text
research.plan
  -> source/tool acquisition
  -> article or market analysis
  -> verification decision
  -> verification tool / delegation / explicit no-external-check
  -> verification result and open gaps
  -> answer_claim_audit
  -> final answer or revision
```

Trace 不暴露模型隐藏思考，只记录可审计事实：使用了哪些来源、读取了什么、调用了哪些工具、委派了什么任务、验证结果是什么、哪些结论仍未核实。

看到 Trace 时：缺少设计要求的阶段，优先判断架构或调度问题；工具已调用但返回错误，判断工具、数据源或环境问题；证据存在但结论表达过强，判断证据绑定或答案审核问题。

## 12.8 Todo 完成条件

`research.plan` 是每轮研究都使用的 Todo 插件。计划不规定工具顺序、分析方法或 Subagent 顺序，但完成条件必须覆盖：

1. 用户 Query 的所有目标已经回答，或明确标记为 blocked；
2. 需要的文章、行情或外部事实已经按实际范围处理；
3. 实质分析已经进入验证阶段；
4. 已记录验证结果，或说明为什么某些内容不需要外部验证；
5. 未解决的证据缺口已经披露；
6. 最终答案通过 Final Hook，或返回明确的不可发布原因。

## 12.9 设计到代码、Trace 和测试映射

| 设计内容 | Prompt / Skill | 主要实现 | Trace / 验收 |
|-|-|-|-|
| 总体目标和自主决策 | `plugins/prompts/finance/00_system.md` | `AgentRuntime`、`AgentLoop` | Prompt 装配测试、真实 Query Trace |
| 工具与来源边界 | `finance/10_tools.md`、`finance/source_access.md` | `FinanceResearchGateway`、PermissionEngine | Tool Audit、越界和空结果测试 |
| Todo 与目标保持 | `finance/40_plan.md`、`finance/research_plan.md` | `research_plan.py` | plan 创建、更新、blocked 测试 |
| 文章逐篇读取 | `finance/runtime_execution.md` | `article_corpus.py`、`article_gateway.py` | Article EvidenceRef、文章覆盖验收 |
| 文章卡片与多文章综合 | `finance/research_artifacts.md` | `research_artifacts.py`、`research_synthesis.py` | card/comparison Artifact 和 Trace |
| 验证阶段与方法选择 | `finance/verification.md`、`finance/evidence_review.md` | `research_verification.py` | verification tool/delegation/evidence Trace |
| 最终答案审核 | `finance/verification.md` | `answer_audit.py`、`AgentLoop.final_guard` | `answer_claim_audit`、来源覆盖验收 |
| 上下文恢复和压缩 | `finance/runtime_execution.md` | `context_offload.py`、`context.open` | checksum、restore、压力测试 |
| Subagent | `finance/subagent_review.md` | `subagent_roles.py`、`agent_runtime.py` | 父子 Run、输入输出契约、权限测试 |

设计先于代码的最低记录要求是：每个新能力在实现前先写清目标、输入、输出、边界、失败路径、Trace 表现和验收方式；代码实现后再把实际路径和测试结果补回本节或接口契约文档。

# 十三、终稿审核稳定性与完整交付（2026-10-12 修订）

本节是对 12.3～12.7 的补充设计，作为后续代码实现和 Trace 验收的权威基准。它不规定金融分析的固定步骤，而规定文章证据、审核目标、运行时稳定性和交付边界。

## 13.1 文章是独立研究单位

- 每篇文章必须作为独立研究单位读取，文章之间不得因为主题相近而自动合并成一篇证据；是否保存 ArticleCard 由主 Agent 根据当前问题决定。只有文章被用于单篇深度理解、跨文章综合、审核或恢复时，才需要形成带 EvidenceRef 的 ArticleCard。
- ArticleCard 至少保留 article_id、作者、日期、文章范围、关键观点、观点条件、相关事实、反例或限制、EvidenceRef 和不确定性。
- Runtime 不拆最终答案的每个句子，也不根据句子数量决定审核对象。它只校验 ArticleCard、EvidenceRef 和审核结果的结构完整性。
- 主 Agent 可以根据当前问题决定只浅读、逐篇深读、委派 Subagent 或继续回读原文。不同文章可以有不同的重点和分析深度。

## 13.2 审核对象由主 Agent 选择

主 Agent 生成完整研究稿后，自主选择真正影响答案的审核目标。审核目标是一个观点或推理链节点，而不是机械拆出的单句。

```text
review_target
  target_id
  type: author_view | objective_fact | prediction | causal_explanation | model_inference | personal_context
  scope: article | cross_article | external | personal
  relation_type: agreement | disagreement | change_over_time | complementary (optional)
  claim_or_question
  article_refs
  evidence_refs
  review_question
  external_check_needed: yes | no | undecided
  importance: core | supporting | context
```

无法确认的字段必须标记为 insufficient，不允许通过审核修复调用猜测原文没有表达的内容。主 Agent 可以不提交低价值的审核目标，但不得遗漏会改变核心结论的作者观点、跨作者共识或分歧、因果解释、预测和模型推断。

## 13.3 分层审核范围

- 文章数量、作者名称、日期、文件位置等元数据不进入语义深度审核，但必须经过确定性的来源和权限校验。
- 直接转述文章内容只需要绑定实际读取的 EvidenceRef；如果转述被升级为事实、因果或共识，就进入深度审核。
- 作者观点、跨文章相似或相反观点、趋势变化、因果解释、预测、投资判断和“证明/支持/导致”等强措辞必须进入相应审核。
- 外部核验由主 Agent 根据审核目标自主决定。可以使用文章原文、公开网页、行情工具、Subagent 或明确记录“不需要外部核验”；Runtime 不强制使用某一种验证工具。

## 13.4 结构化输出与稳定重试

最终答案不要求写成固定模板，正文仍由主 Agent 自主组织。为了让审核链路稳定，系统使用一个轻量结果信封承载正文和可选审核计划：

```text
answer_markdown: 完整研究稿
review_targets: 主 Agent 选出的审核目标
review_status: planned | partial | complete | failed
open_gaps: 未解决的证据缺口
```

1. 首次调用要求主 Agent 返回完整正文和审核目标。
2. 若结构化部分格式错误，Runtime 将具体错误反馈给原模型，只要求修复结构化字段，不重新生成正文。
3. 再次失败时，调用 review.normalize 修复 Subagent。它只根据现有研究稿、ArticleCard 和 EvidenceRef 补全结构，不重新研究、不新增事实、不改变正文。
4. 修复仍失败时，Runtime 使用已有 ArticleCard 和工具调用记录生成审核未完成状态，并保留完整正文。

重试只解决格式和结构问题，不借格式修复偷偷改变研究结论。Runtime 可以限制重试次数防止循环，但不能因为审核结构失败而丢弃主 Agent 已完成的分析。

**运行时稳定性边界（2026-10-15 补充）**

- 文章的时间范围与任务意图分开：最近、本月、今年等仅限定材料时效，不因与资产名称同时出现就把文章概括、作者画像或阅读任务强制升级为行情查询。没有文章时，主 Agent 可以交付有检索依据的缺资料说明；不能为满足门控获取无关行情。明确请求价格、涨跌或市场事实核查时，既有证据约束仍然保留。
- 传输失败与结构失败分开恢复：网络断开、限流或暂时服务故障仅在有界次数内重发当前请求，不能注入模型未遵守 JSON 的修复提示。只有实际返回内容违反结构契约时，才进入格式修复。
- 输出截断不能冒充完整结果：OpenAI-compatible 的 finish_reason=length 与 max_tokens 截断同等处理；即使部分文本可以解析为 JSON，也不得作为完整终稿发布。预算内重试，耗尽后保留已有证据并明确未完成。
- Trace 中的 attempts 只记录适配器报告的实际尝试次数；max_attempts 单独记录配置上限，未知实际次数保持未知，不用配置上限代替实际值。代码回归通过、模型调用成功和业务答案通过验收分别判断。

**可配置的模型侧流式传输（2026-10-15，已确认后实施）**

1. 范围：仅为 OpenAI-compatible /chat/completions 增加 SSE 传输选项，AGENT_WORKBENCH_MODEL_STREAM 默认 false，当前 gpt-6.1-sol 本地配置启用 true；Anthropic 旧路径不变。此开关不改变 Prompt、取材方式、Subagent 职责、证据审核或主 Agent 的自主决策。它与浏览器展示执行过程的 SSE 是两条不同链路。
2. 流程：发送 stream=true → 按 SSE 空行事件边界解码 UTF-8 和 data JSON → 只累积主 choice 的 delta.content → 保存 usage 与 finish_reason → 必须同时收到明确 finish_reason 和 [DONE]，才组装成既有 ModelResponse 并进入 JSON 解析、契约校验与证据审核。流式片段不直接发布、不执行其中的工具动作，reasoning_content 不作为答案。
3. 恢复：提前 EOF、网络断开、损坏事件、缺少终止标记均为传输失败；丢弃本次部分正文，在既有次数和退避边界内重试原请求，不拼接两次生成、不插入 JSON 修复提示。finish_reason=length 仍按输出截断处理，不能发布即使能解析的半份答案。鉴权失败不盲目重试，不自动切回可能失败的非流式路径或绕过系统代理。
4. 边界：现有 MODEL_TIMEOUT 作为连接及空闲读取超时；新增 AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS（默认 600 秒，可配 1–900）作为单次流的总时长检查，每次读取前后检查，阻塞读取最多延伸一个空闲窗口；心跳不重置总时长。单个事件限制 1 MiB，整条流限制 8 MiB，防无限缓存。耗尽后保留既有证据并明确未完成，不冒充完整研究。
5. 可观测：每次模型调用记录 transport、stream_complete、stream_event_count、stream_content_chars、time_to_first_token_ms、stream_duration_ms 以及实际 attempts。重试的各次传输诊断单独保留，不记录凭据或把正文片段当作成功结果。子 Agent、文章读取模型调用和最终审核调用同样继承该配置及 Trace。
6. 验收：分别验证正常 SSE、跨 UTF-8 字符、心跳、多行 data、usage 尾块、缺少 DONE 或 finish_reason、断流后重试、超时、限长、敏感错误脱敏、非流式兼容和配置继承；真实重跑不存在作者、单篇深读及多作者比较。必须完整交付才算业务通过，收到 token 或单测通过不能替代业务验收。

协议参考：[OpenAI Chat Completions Streaming](https://platform.openai.com/docs/api-reference/chat/streaming)；[WHATWG SSE 规范](https://html.spec.whatwg.org/multipage/server-sent-events.html)。600 秒为本项目可调整的传输预算，不是模型最优思考时长或收益保证。

## 13.5 审核失败时的完整交付

一般的审核失败、证据不足、外部证据反驳作者观点或审核结果存在争议，不应阻止安全内容交付。若存在来源伪造、EvidenceRef 断链、越权读取或其他 Runtime 安全问题，只阻止受影响的不安全内容；仍可安全交付的研究稿、来源片段或限制性答案继续交付。答案必须保留完整研究稿或可安全保留的完整部分，并明确展示：

- 已审核且得到证据支持的目标；
- 证据不足或只保持作者归因的目标；
- 与外部证据冲突的目标，以及双方依据；
- 尚未完成审核的目标和具体原因；
- 本轮实际读取的文章、时间范围和未覆盖范围。

“审核成功”只表示相应审核目标完成，不表示整篇答案全部事实都已确认。未完成审核的研究稿可以发布，但不得把未审核内容表述为已验证事实。

## 13.6 Trace 验收节点

```text
article.read / article.study
  -> ArticleCard
  -> analysis draft
  -> main Agent selects review_targets
  -> verifier / skeptic / analyst / external tool (when chosen)
  -> target-level review results
  -> retry or review.normalize (when needed)
  -> complete answer + audit status
```

验收时重点检查：每篇被引用文章是否实际读取；ArticleCard 是否保留作者、日期和 EvidenceRef；审核目标是否由主 Agent 选择；审核是否只覆盖需要审核的内容；格式失败是否重试且不丢正文；审核失败是否仍交付完整答案；冲突是否并列展示；未审核范围是否披露。

## 13.7 代码实现边界

- 主 Agent 负责研究决策、文章分析深度、审核目标选择和是否外部核验。
- ArticleCard、审核目标和审核结果使用结构化接口，便于 Trace、Subagent 和恢复流程使用。
- Runtime 负责格式校验、重试编排、权限边界、EvidenceRef 存在性、重复调用保护和最终状态记录。
- Hook 负责发布前安全检查，不替代主 Agent 做金融判断，不强制所有答案使用相同分析模板。

# 十四、统一研究状态与文章集合契约（2026-10-14，当前权威）

本节统一前文关于文章读取、证据覆盖、审核、Subagent 和运行状态的设计。前文候选方案和历史验收记录继续保留；如果前文与本节冲突，以本节为准。本节描述设计契约，不要求每个 Query 使用相同工具或相同分析顺序。

## 14.1 主 Agent 与 Runtime 的总边界

- 主 Agent 负责理解 Query、制定研究计划、选择文章和工具、决定浅读或深读、决定是否继续取材、选择审核目标、决定是否委派 Subagent，并组织最终答案。
- Runtime 负责记录实际发生的事实：文章是否被列出、哪些行被读取、EvidenceRef 是否真实、工具和文件是否在授权范围、Subagent 的输入输出、Trace、Artifact 和交付状态。
- Runtime 不替主 Agent 决定金融语义，不把所有文章强制套用同一分析模板，也不把所有读取过的文章自动变成最终答案来源。
- Final Hook 负责证据可追溯性、来源绑定、主张类型、缺口披露和发布安全；它不是固定金融分析流程，也不替代主 Agent 做研究判断。

## 14.2 文章分析单位与 ArticleCard

每篇文章是独立研究单位，但每篇被读取文章不必须生成 ArticleCard。ArticleCard 是主 Agent 按当前问题选择的、可选的单篇文章证据产物，不是最终答案，也不是外部事实验证结果。

- 浅读表示围绕当前问题读取有限范围并形成有限观察；输出内容由 Query 决定，可以只有相关性、一个观点、一个条件或一个待深入问题。
- 深读表示读取文章全文，按边界分段提取相关主张，再增加一次文章级归并，整合跨段观点、条件、时间范围、论证关系和限制。
- 深读的分段证据必须保留；文章级归并是工作记忆，不得替代原文 EvidenceRef。归并结果优先保存为与 ArticleCard 关联的 research_note。
- claims 可以为空。文章与当前问题无关时返回 no_relevant_claims，不伪造作者观点。
- 主 Agent 决定哪些文章进入最终答案；Runtime 只校验证据绑定。已读但未使用的文章不自动成为引用义务。

ArticleCard 的稳定字段是 article_id、source_hash、author、published_at、title、source_url、question、depth、line_count、observed_lines、claims 和 limitations。每条主张至少保留 kind、statement、exact quote、line_start、line_end 和 evidence_id；conditions、horizon 按问题需要保留。Runtime 负责生成 claim_id 并校验引用属于当前文章版本和实际读取范围。

## 14.3 文章集合采用三层状态

文章集合不是五个互斥列表，而是发现层、读取层和使用层的组合。这样可以表达一篇文章已经读取但未使用，也可以区分没有读取和读取失败。

- **发现层：**listed_articles 表示在本轮查询范围内发现的候选文章，不等于已经读取正文。
- **读取层：**每篇文章记录 unread、partial、complete 或 failed。partial 表示只观察了部分范围，complete 表示当前版本全文覆盖，failed 表示曾尝试读取但工具、权限、文件或模型路径失败。
- **使用层：**每篇文章记录 undecided、used 或 excluded。used 可以带 primary 或 background 角色；excluded 表示明确不纳入本轮分析。

read_articles 是读取层中实际发生过 article.read 或 article.study 的文章集合；unread_articles 是已列出但正文尚未读取的文章；excluded_articles 可以与 read_articles 重叠。planned_articles 只表示主 Agent 的计划，不等于最终使用。

used_articles 以最终答案实际依赖的证据为准：Final Hook 根据答案中的 EvidenceRef、作者日期锚点和实际使用证据反查 article_id。主 Agent 的声明只能作为计划或候选来源，不能单独把文章标成 used。

listed_articles 必须带查询作者、日期范围、数据源和 listing_complete 等范围信息。未列出不等于不存在，可能是日期过滤、分页、索引或数据源范围不完整。

## 14.4 证据覆盖与停止规则

覆盖按用户问题的回答维度判断，不按文章数量判断。主 Agent 在有意义的一组读取之后调用 research.coverage，记录每个维度的证据、反例或限制证据、缺口和下一步决定。

- **covered：**该问题维度已有正文或其他适用 EvidenceRef，足以在当前范围内回答。
- **partial：**已有部分证据，但作者、时间、条件、反例或外部事实仍有明确缺口。
- **uncovered：**当前没有足以回答该维度的证据。

decision=synthesize 的含义是当前证据足以在披露范围和缺口后开始综合，不代表事实已经证明；decision=continue 时必须写出具体 next_need。停止条件是主要回答维度已有依据或明确缺口、重要反例或限定条件已经检查或明确未检查、继续读取预计不会改变主要判断，且没有具体下一步信息需求。

以下情形应优先继续读取或核查：用户要求全部或逐篇、点名作者尚未覆盖、关键时间段缺失、结论只有单一方向、需要比较共识或分歧、结论包含强因果或预测、或者存在未解释的文章冲突。普通概括默认采用代表性样本并披露作者、日期、实际读取和未覆盖范围；只出现一次默认只对已读样本负责，只有声明范围完整且已穷尽检查时才可以作全量表述。

partial 或 uncovered 不自动阻止答案。最终答案应区分已知内容、样本范围、证据不足、未检查反例和下一步缺口。

## 14.5 结论类型与审核范围

审核对象是会影响答案的观点或推理链节点，不是 Runtime 机械拆出的每个句子。结论类型统一为 author_view、objective_fact、prediction、causal_explanation、model_inference 和 personal_context。

- author_view 只要求忠实转述原文，保留语气、条件、时间和作者归因；普通概括不强制外部核查作者是否正确。
- objective_fact 需要检查数字、日期、事件和市场状态的口径与 EvidenceRef。
- prediction 必须保留预测者、方向、条件和时间范围；未到期不能判定实现或失败。
- causal_explanation 必须区分作者提出的机制、同期事实是否发生和因果是否真正得到证明。
- model_inference 必须标明是主 Agent 基于资料的推断，不能伪装成原文或客观事实。
- personal_context 只表示 Wiki、记忆或用户提供的背景，不自动升级为外部市场事实。

主 Agent 选择 review_targets。高价值目标包括核心作者观点、跨文章共识或分歧、趋势变化、因果解释、预测、投资判断、重要客观事实和模型推断；普通背景句不需要逐句审核。

## 14.6 外部核查绑定与冲突呈现

主 Agent 决定是否需要外部核查。若目标需要 supported 或 refuted，必须绑定本轮实际读取、口径匹配的独立外部 EvidenceRef。没有外部证据时，有具体信息需求就继续检索；无法继续或仍不足则记录 inconclusive，不能伪造 supported 或 refuted。

作者观点与外部证据冲突时并列呈现：保留作者原始观点和原文证据，列出外部事实、时间范围、口径和冲突理由，并标注当前争议。外部数据支持价格方向，不等于证明完整因果链；作者之间达成共识，也不等于独立事实验证。

viewpoint_faithful 表示准确保持作者归因，不表示作者观点正确。reasoned 表示有明确前提的分析推断，不表示事实认证。not_due 只用于时间范围可解析且尚未到期的预测。

## 14.7 Subagent 是可插拔研究工具

- **Analyst：**在主 Agent 分配的文章和权限范围内逐篇提炼观点、条件、证据和限制；不负责证明作者观点。
- **Verifier：**在被委派外部核查时读取独立公开来源，返回支持、反驳或无法判断及其 EvidenceRef；没有独立来源不能声称 supported 或 refuted。
- **Skeptic：**寻找反例、遗漏条件、时间口径错误、因果跳跃、来源非独立和过度推断；只有实际读取独立来源时才可以把意见升级为外部事实反驳。

主 Agent 自主决定是否委派、委派哪个角色、输入哪些文章或主张以及是否采纳结果。Subagent 不能直接发布最终答案、修改主 Agent 的计划、访问未授权路径或把委派成功当成验证成功。所有输入、输出、角色、权限、EvidenceRef 和完成状态都进入父子 Trace。

## 14.8 五个独立运行状态

以下状态不能互相替代：

- **trace_contract：**pass 或 fail，表示关键 Trace、工具、EvidenceRef 和委派事件是否完整。
- **delivery_status：**done 或 blocked。done 表示已交付完整答案、带限制答案或 source-only 答案；blocked 只表示连安全的部分内容都无法交付。
- **evidence_coverage：**complete 或 partial，表示用户问题的证据范围是否覆盖，不表示事实已经证明。
- **semantic_review：**complete、partial 或 failed，表示实质性审核是否执行完成，不表示审核结论一定为 supported。
- **confidence：**用于具体结论的证据把握程度，不是概率或真理分数；应同时保留理由。

delivery_status=done 可以与 evidence_coverage=partial 或 semantic_review=partial 同时存在；trace_contract=pass 不代表结论正确；evidence_coverage=complete 不代表因果成立。

为避免与 Wiki 事实已有的 0 到 1 数值 confidence 混淆，研究结论建议在结构和 Trace 中使用 claim_confidence_label 或 research_confidence，值为 high、medium、low，并记录理由。Wiki fact confidence 继续保持原数值语义，不与研究结论置信度互换。

## 14.9 冲突审查与裁决

**冲突一：每篇文章是否必须生成 ArticleCard。**旧段落中“每篇文章由主 Agent 或 Subagent 独立读取并形成 ArticleCard”的表述过强，与当前设计冲突。裁决：文章是独立读取单位，但 ArticleCard 是可选产物；只有被主 Agent 选中用于单篇深度理解、跨文章综合、审核或恢复时才需要保存。

**冲突二：全局 coverage_complete 与问题范围覆盖。**AnswerAudit 中的 coverage_complete 表示候选答案的实质主张是否被审核覆盖；research.coverage 中的 covered、partial、uncovered 表示研究问题维度的证据获取状态。两者不是同一状态，不能互相赋值。建议 Trace 和交付元数据分别使用 answer_audit_coverage 与 evidence_coverage。

**冲突三：审核失败是否阻止交付。**旧流程中部分“审核不通过则不发布”的表达过于绝对。裁决：来源伪造、EvidenceRef 断链、越权读取等 Runtime 安全问题可以阻止对应内容；证据不足、外部冲突、审核模型失败或争议应保留完整研究稿并降级措辞、并列双方和披露缺口。只要有安全可交付内容，delivery_status 可以是 done。

**冲突四：confidence 的字段语义。**Wiki 和既有事实模型中的 confidence 是数值字段；研究结论的 high、medium、low 是语义标签。裁决：不复用同一语义，新增研究结论命名或在 Trace 中明确字段域，避免把用户事实可信度和本轮结论把握程度混为一谈。

**不构成冲突的内容：**research.plan 要求每轮先识别目标，和主 Agent 自主选择工具并不冲突；research.coverage 要求继续时写 next_need，和不设固定文章数量不冲突；Final Hook 要求证据可追溯，和主 Agent 自主决定哪些文章进入最终答案不冲突；Subagent 可选，和复杂任务可以委派不冲突。

## 14.10 当前设计的最小确定性接口

Runtime 只应稳定校验以下内容：文章和证据是否真实、读取范围是否存在、主张和审核目标的结构是否有效、外部核查是否绑定独立证据、继续取材是否给出具体缺口、Subagent 是否越权、Trace 是否可回放、最终使用来源是否能反查到 EvidenceRef。文章如何理解、哪些观点重要、是否继续研究、是否调用哪个插件以及答案如何组织，交由主 Agent 自主决定。

## 14.11 跨文档冲突审查结果（2026-10-14）

本次对照飞书架构文档、金融 Prompt/Skill 和本地接口契约后，结论如下。第十四节仍是金融设计的权威裁决；下列未同步项在代码修改前必须先处理。

### 已消除的冲突

1. 第 13.1 节原先把“独立读取”和“必须形成 ArticleCard”绑定在一起，现已改为：文章必须独立读取，但 ArticleCard 由主 Agent 按问题需要选择性生成。
2. 第 13.5 节原先写成审核失败一律不能交付，现已改为：安全问题阻止受影响内容；证据不足、外部冲突、审核失败或争议时，仍交付安全的完整研究稿、来源片段或限制性答案，并披露缺口。
3. 早期研究候选中的“文章较多时先逐篇形成 ArticleCard”已标为历史候选，不再是当前强制流程。

### 仍存在的文本冲突或命名不一致

1. 本地接口契约已将 `answer_audit_coverage` 作为答案审核覆盖的规范字段，并把 `coverage_complete` 保留为只读兼容别名；`evidence_coverage` 仍表示研究范围覆盖，二者不能互相赋值。Runtime 代码仍需后续切换新字段并补充兼容测试。
2. 本地 `docs/finance_agent_interface_contracts.md` 已同步为：安全的完整答案、带限制答案和 source-only 结果都可以是 `delivery_status=done`，并通过 `delivery_mode` 区分交付形态；只有没有安全内容可交付时才是 `blocked`。Runtime 代码的字段写入仍需后续实现和测试确认。
3. 第 13.2 节已同步：`review_target.type` 不再包含 `synthesis`；跨文章综合由 `scope=cross_article`、可选 `relation_type` 和实际的主张类型共同表达。Prompt 和 Runtime 代码仍需切换到该规范结构并补充兼容测试。
4. `claim_set.v1` 的旧说明仍使用裸字段 `confidence`，而第 14.8 节已经把研究结论置信度定义为 `research_confidence` 或 `claim_confidence_label`，并与 Wiki 事实的数值 confidence 分开。后续应给字段加命名空间或兼容映射，避免同名异义。

### 不是文本冲突，而是实现差距

- 当前 `article_study.py` 的 deep 路径已经逐段读取并合并 claims，但还没有完整执行设计中“文章级归并并保存关联 research_note”的步骤。这是代码实现缺口，不应通过放宽设计来掩盖。
- 当前代码是否能在所有交付分支正确写入 `delivery_status`、`evidence_coverage` 和 `semantic_review`，需要以接口同步后重新测试确认。

### 当前结论

设计主线已经一致：主 Agent 自主决定读取、分析、验证和委派；Runtime 只守权限、来源真实性、证据绑定、状态完整性和交付安全。下一次实现前应先同步上面的四项接口/枚举命名与交付语义，再处理 deep 文章级归并和测试；在此之前不应继续新增金融分析流程。

## 14.12 已确定的状态与接口语义（2026-10-14，当前权威）

本节将上一节的冲突裁决落实为可供接口和 Trace 使用的统一语义。它只约束状态和证据边界，不规定主 Agent 的具体分析方法。

### 交付与验证分离

```text
delivery_status:     done | blocked
 delivery_mode:      full | qualified | source_only
verification_status: verified | partial | disputed | failed
```

- `delivery_status=done` 表示已经向用户交付安全的可见结果，可以是完整答案、带限制答案或 source-only 材料；`blocked` 只表示没有任何安全内容可以交付。
- `delivery_mode` 表示交付形态，不表示结论正确。
- `verification_status` 表示验证执行和结果状态。争议、证据不足或审核失败不能自动抹掉作者观点，也不能自动阻止安全内容交付。

### 两类覆盖状态

```text
evidence_coverage:     complete | partial | uncovered
answer_audit_coverage:  complete | partial | failed
```

`evidence_coverage` 表示用户问题需要的资料范围是否覆盖；`answer_audit_coverage` 表示最终答案中的重要主张是否经过审核。两者可以独立变化，例如资料范围只有 partial，但答案中已使用的主张全部审核通过。旧字段 `coverage_complete` 只作为 `answer_audit_coverage` 的兼容读取别名，不能代表研究范围覆盖，新写入使用显式字段名。

### 主张类型与跨文章关系

主张类型只使用 `author_view`、`objective_fact`、`prediction`、`causal_explanation`、`model_inference` 和 `personal_context`。`synthesis` 不是主张类型，而是一个分析动作或结果范围。

跨文章综合使用 `scope=cross_article`，并可以附带 `relation_type`：`agreement`、`disagreement`、`change_over_time` 或 `complementary`。综合后的判断通常是 `model_inference`，不能因为多个作者表达一致就升级为 `objective_fact`。

### 研究置信度与事实置信度

研究结论使用 `claim_confidence_label` 或 `research_confidence`，值为 `high`、`medium` 或 `low`，并保留 `confidence_reason`。Wiki 或其他事实记录中的数值 `fact_confidence` 保持原有语义。新的研究记录不再使用没有命名空间的裸字段 `confidence`，避免把“事实数据置信度”和“本轮研究结论把握程度”混为一谈。

### 兼容与实现顺序

- 本地接口契约已经同步以上语义。
- Prompt 和 Runtime 代码仍需切换到规范字段，并对旧字段建立只读兼容和迁移测试。
- 完成字段迁移后，再处理 deep 文章级归并和 `research_note`，最后执行状态、审核和 Trace 回归验收。