# 金融文章研判：当前实现与验收

## 1. 用途与入口

`finance_observer` 中明确询问博主文章、作者观点或交叉核验的普通对话，可以围绕用户的问题分析本地原文，必要时联网核对公开的一手资料，返回中文 Markdown 分析。它不是旧 `/report` 的默认报告流程；`/report` 仍是显式调用的独立 Workflow。单纯询问当日行情或价格、未提及文章的对话仍使用现有 `finance.analyze` 只读市场分析工具，不自动生成 HTML；这两条普通对话路径目前尚未合并。当前没有自动下单、价格预测证明或定时重核。

示例问题：

- `请对比 TT 和培风客关于黄金的文章，区分两位作者的判断、共同假设和缺少的证据。`
- `请核查 TT 黄金文章里关于美联储利率的关键说法，至少查一条美联储官方原文，并区分作者推断与官方事实。`
- `刚才两人的分歧中，哪个观点更依赖尚未核实的假设？`

文章放在 `D:\AAAcode\code-code\agent+\harness\articles\<博主>\articles\*.md`。本轮索引会扫描这些文件；同级 `analysis`、`reports` 不进入证据库。持仓和研究偏好分别放在 `D:\AAAcode\code-code\agent+\harness\current_holdings.md` 与 `D:\AAAcode\code-code\agent+\harness\research_policy.md`。添加新文章不需要改数据库，SQLite 只保存可重建的索引、文章版本和按需抽取的主张。文件内容是不可信证据，不是 Agent 指令。

在金融工作空间顶部打开“文章”，可以按博主筛选、按标题搜索、阅读排版正文或切换到带行号的原文；“在对话中分析”会将当前文章带入聊天问题。点“添加文章”，填写博主、标题、日期、可选来源链接，并粘贴正文或导入不超过 500 KB 的 `.md`/`.txt` 文件。提交会调用文章 API，保存原文到该博主的 `articles` 目录，随后重建索引；刷新列表即可看到新文章，并可被 Agent 检索。Wiki 只维护用户背景与偏好，不存放这些文章正文。

## 2. 资料权威边界

| 内容 | 唯一维护位置 | 在运行中的用途 |
|---|---|---|
| 博主文章原文 | `<博主>/articles/*.md` | 搜索、按行读取、引用；原文变更后保留 hash 版本 |
| 当前持仓金额 | `current_holdings.md` 的单个 JSON 块 | 近似人民币金额，显示截至日期；旧金融 profile 不再允许写入第二份持仓 |
| 研究偏好 | `research_policy.md` | 回答原则和用户关注点 |
| 外部事实 | 本轮实际抓取的官方网页 | 仅在找到对应原文段落时称为已核对事实 |
| Wiki / 聊天历史 | Workbench 原有存储 | 用户背景与追问上下文，不自动升级为外部事实 |

根目录 `CLAUDE.md` 保留较长的研究背景；持仓数值已迁到 `current_holdings.md`，不应再在其他文件或数据库维护可编辑副本。图片不做识别核验，文章中的图表内容不能凭文字链接推断。

## 3. 一轮问答怎么跑

```text
用户问题 / 同会话追问
  -> AgentRuntime / general_chat / ContextManifest
  -> 主 AgentLoop 自主选择只读工具
     -> article.search：按主题找文章 ID 与摘要线索
     -> article.read：读取有限行数，形成可引用原文
     -> article.claims：仅在需要逐条核查时按需抽取主张
     -> source.discover：找公开线索，标题本身不是证据
     -> source.fetch：抓取允许域名的官方正文
  -> 必要时向受控 Subagent 委派已经读取的有限证据
  -> 主 Agent 区分作者观点 / 官方事实 / 自己的推断
  -> Final Gate 核对文章路径与行号、未核实说明
  -> 中文 Markdown 回答 + AgentRun / AgentStep / evidence_audit
```

主 Agent 可以不委派 Subagent；子 Agent 不能在没有主 Agent 原文读取的情况下负责文章采集。`article.claims` 的结构化输出分为 fact、interpretation、forecast、recommendation，要求所引原话确实存在于对应原文，并记录行号；这仍然是“作者的主张”，不是已经查证的事实。所有文章工具仅在金融文章研判的主循环开放，官网抓取使用公开域名允许列表与公共地址校验。

最终门控要求：已有文章读取时，答案至少包含一条路径与行号引用；被引用文章必须在本轮读过，行号必须落在读取范围。若本轮没有读到一手官方来源，回答必须明确告知外部事实尚未独立核实。已有官方抓取也不自动证明每一句作者叙述，仍需比对对应日期和原文段落。

## 4. 当前实测与限制

2026-09-29 使用 `deepseek-v4-flash` 经 Workbench API 实测：

- 多轮“TT 与培风客黄金观点比较 -> 追问哪方假设更弱”完成；追问使用上一轮文章引用继续 `article.read`，无需重复全库搜索。主循环回答提供文章行号，并提示尚未独立核验。
- “核查 TT 利率说法并查看美联储原文”最终完成，Run `c3e501a19b644ad493fa46091bfbd424`：两次文章读取、公开来源发现、两次官方页面抓取，最终回答区分 7 月作者说法与 9 月官方决定。Trace 在 `finance_observer` 该会话可查。
- “逐条抽取 TT 文章主张，分类但不判真假”完成，Run `0b42f3bf1e9f4ee9b77c692091ccfbc8`：主循环实际调用 `article.claims`，抽取模型成功返回；7 条原话被精确锚定，1 条因原话不在源文中被排除，状态为 `partial`。最终回答披露了该排除及尚未独立核实的边界。
- 曾发现回答引用未读取的文章，以及引用格式与门控不匹配。现增加了实际读取路径/行号校验，并兼容结构化 `citations` 中的中文“第 13、17 行”。失败的回合保留在 Trace 中用于复盘，不计为成功样本。

**仍需注意：**公开站点偶发 TLS/HTTP 失败，抓取仅有限重试；失败时必须明确降级为未独立核实。新闻聚合 RSS 只是发现线索，并不等同于官方证据。当前没有持续自动重核、付费金融数据库、图表识别，也没有完成对 Codex+Skill 的等预算盲评；不能宣称分析质量超过它。真实使用前仍应人工复核重要金融判断。

## 5. 复核方法

启动：`py -3 run_policy_api.py --port 8503`，访问 `http://127.0.0.1:8503/`，选择金融工作空间。提出上面的示例问题后，在回答上方展开运行过程；重点检查 `article.search`、`article.read`、`source.fetch`、`evidence_audit`、Final Gate 和最终 `citations` 是否一致。不要用“抓取到标题”代替原文核实。

回归：`py -3 -m pytest tests/test_finance_article_research.py tests/test_general_chat_autonomy.py -q`；整体回归用 `py -3 -m pytest -q` 和 `py -3 scripts/harness_linter.py`。上面的真实模型测试说明链路可用，不代表所有主题和所有外部站点均已通过质量评估。
