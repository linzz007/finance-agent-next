# Agent Workbench (Historical Reference)

单用户、本地优先的 Agent Workbench。用户通过一个聊天入口使用普通对话、外部变化影响分析和 Company Wiki 等能力；项目重点是把新闻、政策、行业事件、技术发布、开源更新等外部变化数据放进同一套 Agent Runtime / Harness 中，形成可控执行、可审计证据、可复盘 Trace 和可回归测试。

> 当前状态：可运行的 Workbench；Runtime Kernel 已完成当前计划范围内的 Skill、Loop、Subagent 与政策业务 Gate 迁移，其他 Harness 能力仍按 Canonical Spec 继续建设。
> 唯一权威规格：`docs/superpowers/specs/2026-07-17-agent-workbench-harness-design.md`
> 实施路线图：`docs/superpowers/plans/2026-07-17-agent-workbench-harness-roadmap.md`
> 重要说明：规格中的目标能力不等于当前已经实现的能力。

## 当前产品形态

- FastAPI 提供 API 和原生 HTML/CSS/JavaScript Workbench。
- 左侧为可折叠会话历史、少量报告命令和当前工具边界。
- 中间为聊天、执行过程、Wiki 资料页和回答。
- Trace 位于回答上方。
- 当前固定使用真实模型 `deepseek-v4-flash`。
- 页面不提供模型市场或模型配置中心。
- 没有模型鉴权时后端拒绝执行，不使用隐藏 Mock 假跑。
- 默认消息进入 `general_chat`，只有 `/report` 才触发 `external_impact_report`；旧的 `policy_weekly_impact`、`recent_news_report`、`research_report` 保留为内部兼容路线，不作为主要用户入口。
- Wiki 作为独立数据页展示和维护 Company Wiki 页面、facts 和来源，不再作为 slash skill；保存后写回 markdown 并同步 SQLite facts 索引。
- 用户输入 `/report` 询问“有哪些数据源、最近快照是什么、来源边界是什么”时，系统进入数据源盘点路线，并产出回答、Trace 数据源摘要和 RunArtifact。

## 启动

### 1. 安装

```powershell
py -3 -m pip install -e .
```

### 2. 配置模型

只在启动服务的 Shell 中配置凭证：

```powershell
$env:ANTHROPIC_BASE_URL = "https://www.cctq.ai/v1"
$env:ANTHROPIC_MODEL = "gpt-6.1-sol"
$env:ANTHROPIC_AUTH_TOKEN = "<your-token>"
$env:AGENT_WORKBENCH_MODEL_PROTOCOL = "openai-compatible"
```

API Key 不应写入仓库、SQLite、Trace、Memory 或前端。

OpenAI-compatible 可选模型侧 SSE：设置 `AGENT_WORKBENCH_MODEL_STREAM=true`。
`AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS` 默认 600 秒，是每次流式响应的总时长检查；
`AGENT_WORKBENCH_MODEL_TIMEOUT` 仍限制连接和空闲读取。只在收到完整终止信号后进行动作解析及审核，
断流会在既有重试预算内重新请求，不把部分正文当作答案。此开关与浏览器展示执行过程的 SSE 不同。

### 3. 运行

```powershell
py -3 run_policy_api.py
```

默认地址：

```text
http://127.0.0.1:8501/
```

修改端口：

```powershell
$env:POLICY_IMPACT_API_PORT = "8502"
py -3 run_policy_api.py
```

## 对话入口与 Slash Commands

### 金融观察扩展

工作空间 `finance_observer` 使用同一套聊天、AgentLoop、Wiki 和 Trace。其 `/report` 读取金融档案，获取指数日线与宏观/机构原文，计算指标，生成中文 HTML 报告；随后可追问同一报告的数值和证据。

普通对话中询问博主文章、作者观点或交叉核验时，走独立的问答式文章研判链路，不自动调用 `/report` 或生成 HTML。文章源为 `D:\AAAcode\code-code\agent+\harness\articles\<博主>\articles\*.md`；持仓和研究偏好单一可编辑来源为 `D:\AAAcode\code-code\agent+\harness\current_holdings.md` 与 `D:\AAAcode\code-code\agent+\harness\research_policy.md`。Agent 会按问题搜索并读取原文，必要时抓取允许的公开官方页面，将作者观点、官方事实和自身推断分开说明，并在 Trace 中留下来源读取记录。使用和边界见 [金融文章研判说明](docs/finance_article_research_cn.md)。

金融工作空间的“文章”页展示 TT、万种心法、培风客等博主的原文，支持按博主/标题筛选、阅读、查看带行号原文和在聊天中分析。点“添加文章”可粘贴正文或导入 `.md`/`.txt`，经 API 保存到对应博主目录并立即进入检索索引；文章不写入 Wiki。

- 金融 Wiki 使用结构化表单，默认草稿，不推断持仓或风险承受能力。
- API 服务：`py -3 run_policy_api.py --port 8503`，浏览器 `http://127.0.0.1:8503/`。
- 另开采集进程：`py -3 scripts/run_finance_worker.py --company-id finance_observer --interval-minutes 60`。
- Worker 仅采集，不调用模型、不下单；退出进程或关闭电脑后停止采集，重启会合并补跑，不伪造错过时段的快照。
- 页面 Wiki 可暂停/恢复采集和查看实际 Worker 状态。采集开关开启不等于 Worker 已启动。
- 未建立金融档案时，先通过 `/companies` 创建空间，再通过 `/companies/{id}/workbench/finance/profile` 保存档案；示例准备可运行 `py -3 scripts/run_finance_acceptance.py --prepare-only`，不会覆盖现有档案。
- 启动脚本会读取本地 `.env.local`；凭证不应提交到仓库或手工放进 Wiki。

实施、用例和边界见 [金融验收说明](docs/acceptance/finance_delivery_cn.md) 与 [Todo](docs/financial_observation_todo_cn.md)。

| 命令 | 当前能力 |
|---|---|
| 普通输入 | 默认对话，结合企业事实和长期 Memory |
| `/report` | 统一外部变化影响分析，内部按需选择政策、新闻、调研或数据源盘点路线 |
| Wiki 页面 | 层级浏览和编辑 Company Wiki 页面与 facts，保存后重建 SQLite 索引 |

兼容说明：后端仍接受旧 `/impact`、`/policy`、`/news`、`/research`、`/wiki` 命令，但它们只作为兼容入口；前端只展示 `/report`。

## 当前 Runtime 链路

```text
用户输入
-> 模型和会话边界校验
-> Slash / Skill 路由
-> ContextManifest
-> Skill 执行；外部变化类请求进入 external_impact_report
-> 内部路线选择：source_inventory / policy / news / research
-> Tool / Gate / Model / Artifact 记录
-> 回答与 AgentRun / AgentStep 持久化
-> 前端展示 Trace
```

当前由 `TurnCoordinator` 负责回合生命周期，`ExecutorRegistry` 按 manifest 的 `executor_id` 无业务分支分发。`AgentRuntime` 仍是组合根，并继续承载较多业务实现；统一 Context/Policy/Memory/Replay 边界尚未全部完成。

## 当前已验证能力

- 持久化会话和聊天记录。
- 单 `/report` 公开 Slash Command、默认普通对话和固定模型限制。
- AgentRun 与有序 AgentStep。
- 可执行、版本化的 Skill manifests，包含 schema、executor、工具、Subagent 与预算契约。
- `external_impact_report` 作为统一外部变化报告入口，能把政策、新闻、调研和数据源盘点收敛为一个公开能力。
- Workbench Wiki API 和 Wiki 页面可层级展示 Company Wiki 页面、facts 和来源。
- 数据源盘点路线可以读取 `config/sources.json`、新闻/政策快照，并写入 `source-inventory.json` RunArtifact。
- `TurnCoordinator` 与 branch-free `ExecutorRegistry` 分发；默认 manifest 与 executor 注册集合由 AST Linter 校验。
- 基于 `SkillResult` 执行证据写入真实 `ExecutionMode`，只有成功委派证据才能标记 `subagent_workflow`。
- 有预算上限、超时、结构化 Action 校验、repair/replan 与明确停止原因的 Agent Loop。
- 隔离上下文、受限工具与预算、父子 Span 和公开结果留痕的 Subagent delegation。
- `claim_schema`、`evidence_binding`、`review_verdict`、`publication` 政策业务 Gate Catalog，以及报告首次写入前的 Publication Gate；当前只覆盖政策链路。
- 普通对话的 ContextManifest、历史摘要和 Prompt 预算检查。
- 显式长期 Memory 写入和跨会话召回。
- 未显式持久化信息的跨会话隔离。
- Tool allowlist 和调用审计。
- 外部变化分析、政策、新闻、调研 Artifact；Wiki 作为独立数据页读取。
- 回答上方的折叠式 Trace。
- 自动化测试、Harness Linter 和浏览器验收脚本。

## 当前部分实现或尚未实现

| 能力 | 当前状态 |
|---|---|
| Context Hard Gate | 普通对话较完整，尚未覆盖所有模型调用 |
| Permission `ask` | 当前按阻断处理，没有审批暂停和恢复 |
| 四层版本化 Memory | 尚未完成 Proposal、版本、Namespace 查询和 Profile Snapshot |
| Trace Event Model | 当前仍以通用 AgentStep JSON 为主 |
| Checkpoint | 主要用于政策 Workflow |
| Replay | 当前仅支持 Trace/Checkpoint 查看，不是 Recorded/Fork/Fresh Replay |
| Eval | 有关键词 Fixture 和业务验收脚本，尚无统一 42 Case 与等预算 Baseline |
| Lazy Trace | 前端仍预渲染部分原始详情 |

## Replay 术语

当前项目必须使用准确术语：

| 名称 | 当前是否具备 |
|---|---|
| `trace_inspect` | 是 |
| `checkpoint_inspect` | 部分 |
| `recorded_replay` | 否 |
| `fork_replay` | 否 |
| `fresh_replay` | 否 |

`scripts/run_replay.py` 当前读取 Checkpoint 并生成摘要，不能作为完整 Replay 能力的证明。

## Memory 边界

当前规则：

- 聊天记录不是长期 Memory。
- 用户明确要求保存时才写入长期 Memory。
- 未保存的会话临时信息不能跨会话读取。
- 模型生成内容不能自动变成用户稳定事实。

目标 Memory 契约、Proposal、版本、Namespace 和 Profile Snapshot 见 Canonical Spec。

## 验证

基础质量门：

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
```

业务验收脚本：

```powershell
py -3 scripts\run_workbench_acceptance.py
py -3 scripts\run_workbench_multiturn_acceptance.py
py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui
py -3 scripts\run_workbench_full_capability_acceptance.py
py -3 scripts\run_workbench_api_dialogue_matrix.py
```

`scripts/run_business_semantic_acceptance.py` 仍保留为旧政策/新闻内部链路的语义回归参考，不作为当前公开 Workbench 形态的主验收入口。

UI 验收：

```powershell
node scripts\run_workbench_ui_cdp_check.mjs
```

真实模型验收需要服务已经启动，并配置有效模型凭证。运行产物保存在 `data/acceptance/`，该目录不进入 Git。

2026-07-17 设计审核前的自动化测试基线为 `178 passed`。对外引用前必须重新运行命令，不能依赖 README 中的历史数字。

## Eval 与性能声明

当前 `data/eval/gold_policy_impact.jsonl` 是 3 条确定性关键词 Fixture，Runner 已明确标记为非端到端 Agent 性能。

以下声明必须等待 Canonical Spec 对应实现和报告：

- 42 个版本化核心场景。
- Recorded Scenario 通过率。
- 真实模型 E2E 成功率。
- B0/B1/T 等预算对比。
- Context、缓存、Subagent 和 Replay 优化前后指标。
- “显著优于通用 Agent 基线”。

没有 Eval/Benchmark Artifact 时，不得在简历或面试中使用这些数字。

## 目录

```text
policy_impact/
  config/                    数据源配置
  data/                      固定夹具与本地运行数据
  docs/
    superpowers/specs/       当前权威规格
    runtime/                 Runtime 规则
  prompts/                   现有 Agent Prompt
  scripts/                   Eval、Benchmark 和验收入口
  skills/                    Skill 说明
  src/policy_impact/
    app/                     FastAPI 与原生 Workbench
    harness/                 当前 Runtime/Harness 模块
    memory/                  SQLite Store
    skill_executors/         业务 Skill 适配
    stages/                  政策 Workflow Stages
    eval/                    当前 Eval Runner
    benchmark/               当前 Benchmark Runner
  tests/                     自动化测试
```

Legacy Streamlit 页面和依赖目前仍保留在仓库中，但不是主入口。它们将在等价原生 UI 测试覆盖后按实施计划移除。

## 文档

- 当前权威规格：`docs/superpowers/specs/2026-07-17-agent-workbench-harness-design.md`
- 当前运行审计：`docs/tonghuashun_scenario_deep_audit_2026-07-17.md`
- 三遍学习路径：`docs/workbench_three_pass_learning_2026-07-05.md`
- 外部历史设计入口：`..\agent_ecosystem_intelligence_design\00_CURRENT_SPEC.md`

## 准确的项目介绍

> 我实现了一个面向外部变化影响分析场景的单用户 Agent Workbench。用户通过统一聊天入口提出新闻、政策、行业事件、技术发布、开源更新或数据源盘点问题，系统先由 `external_impact_report` 统一接收，再按语义进入政策、新闻、调研或数据源盘点内部路线；Harness 层负责 Context、Memory、Tool Policy、Gate、Trace、Artifact 和回归验收。当前已实现可执行 Skill manifests、无业务分支 executor 分发、证据化 ExecutionMode、受控 Agent Loop、隔离 Subagent delegation、数据源边界 Trace，以及仅覆盖政策链路的版本化业务 Gate 与 Publication Gate；Context Hard Gate、审批暂停恢复、版本化 Memory、完整 Replay 和统一分层 Eval 仍未完成。所有完成度和性能数字都以测试与运行 Artifact 为准。
