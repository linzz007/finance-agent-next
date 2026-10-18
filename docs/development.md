# finance-agent-next 开发入口

## 1. 范围与权威

这是旧项目工作目录的独立副本，目标只包含金融研究。原项目中的同花顺、企业影响报告、个人 Agent 产品继续留在原项目；新项目仍可使用与金融问题有关的持仓和研究偏好。

新项目独立维护代码、虚拟环境、文章、Wiki、数据库和运行产物，不通过链接共享原项目的可写数据。不迁移旧金融对话、Trace 或数据库。复制实际工作目录而不是只复制 Git HEAD，保留原项目未提交的金融实现。

| 内容 | 唯一权威 / 入口 |
| --- | --- |
| 设计总入口 | [README.md](README.md)，列出现行文档、状态与历史来源 |
| 产品目标与金融研究行为设计 | [product.md](product.md)、[architecture.md](architecture.md)，迁移既有决定，新提议待用户确认 |
| 接口定义的执行契约 | [finance_agent_interface_contracts.md](finance_agent_interface_contracts.md)，细化已确认设计，不另创业务规则 |
| 业务验收标准 | [finance_agent_eval_v1_acceptance.md](finance_agent_eval_v1_acceptance.md)，不因为测试失败擅自降低标准 |
| 开发约束 | [AGENTS.md](../AGENTS.md) |
| 研究与选型理由 | [research-decisions.md](research-decisions.md)，保留 A-E 的来源、适配和证据边界 |
| 本次产品分离的设计、步骤与验收 | [001-finance-research-fork.md](changes/001-finance-research-fork.md) |
| 飞书向仓库的迁移与冲突审查 | [002-design-authority-migration.md](changes/002-design-authority-migration.md) |

用户于 2026-10-16 确认仓库 Markdown 为设计权威。飞书 revision 181 全文和画板已存档，之后不要求先读或先改飞书；未来即使停止使用飞书，也能在仓库找到来源、现行设计和裁决记录。迁移内容待共同复核不表示遗忘既有决定，也不表示已批准新架构实现。

其他继承的设计、路线图和验收报告暂为历史参考，不代表新产品已经实现其目标。`archive/original-readme.md` 是旧产品说明，不再是当前启动或设计入口。变更记录描述改动和验证结果，不复制一套金融设计，避免两份权威。

## 2. 当前技术与代码地图

保留 Python、FastAPI、原生 HTML/CSS/JavaScript、Pydantic、SQLite，以及自研 Agent Runtime / Harness。不引入新的 Agent 框架、消息队列、数据库服务或固定业务 DAG。后续已批准的版本化研究迭代新增可选能力 Prompt/Skills，旧版能力兼容保留。

| 位置 | 职责 |
| --- | --- |
| `run_finance.py` | 金融专用启动、环境配置和独立路径校验 |
| `src/policy_impact/app/finance_research_app.py` | 金融专用 API 组合；隔离旧产品入口 |
| `src/policy_impact/app/chat_workbench_service.py` | 会话、消息、Wiki 与 Trace 服务 |
| `src/policy_impact/harness/`、`runtime/` | AgentLoop、模型传输、上下文、工具权限、委派与运行状态 |
| `src/policy_impact/domains/finance/` | 文章、证据、研究状态、综合、验证与答案交付 |
| `src/policy_impact/runtime/research_contracts.py` | 新版严格任务/结果契约，不替代旧 claims 输出 |
| `src/policy_impact/domains/finance/research_execution.py`、`research_records.py` | 授权全文输入、理解/综合/工作视图、接收与运行内恢复 |
| `src/policy_impact/harness/research_budget.py` | 父子时限、收尾预留及各层共用的真实模型请求总账 |
| `plugins/prompts/finance/00_system.md` | 金融主 Agent System Prompt |
| `plugins/skills/manifest.json` | Prompt / Skill / 工具与 Subagent 的绑定入口 |
| `plugins/skills/finance/catalog.json`、`runtime/capability_catalog.py` | 金融能力目录与运行内按需读取；工具参数逐项发现，不绕过授权 |
| `plugins/skills/finance/`、`plugins/subagents/`、`plugins/tools/` | 按职责划分的能力说明与工具配置 |
| `data/finance/articles/<作者>/articles/*.md` | 新项目文章原文；同一根目录的持仓、研究偏好为其权威文件 |
| `data/companies/finance_observer/wiki/` | 新项目 Wiki |
| `data/companies/finance_observer/db/` | 首次使用时生成的独立数据库 |

内部包名暂保留 `policy_impact`，避免为了改产品名而重写大量导入。继承的无关模块暂不直接删除，因为共享服务存在静态依赖；金融入口不暴露其业务能力，之后按依赖测试逐步清理。

现有 Runtime 不等于生产级调度系统：Subagent 委派仍有同步调用，HTTP 流任务使用进程内线程池，数据库为本地 SQLite。多进程队列、租约恢复、多租户隔离、幂等和负载验收尚需分别设计，不能因这次复制宣称已实现商业化并发。

当前可选新版从主 Agent 的 [versioned_research Skill](../plugins/skills/finance/versioned_research.md)进入；显式 `analyst@2.0.0` 使用[角色 Prompt](../plugins/prompts/subagents/analyst_v2.md)和任务导向的[阅读](../plugins/prompts/finance/skills/research_reading.md)/[综合](../plugins/prompts/finance/skills/research_synthesis.md)能力说明。注册、参数、测试与未验收项统一见[变更 004](changes/004-versioned-research-implementation.md)，不在此再维护一份配置数值。

## 3. 后续每次变更怎么做

```text
用户需求 / Trace 问题
  -> 查仓库设计入口、已确认条款、接口契约和真实实现
  -> 已有设计但未执行：作为缺陷，明确回归用例
     设计未定义或改变语义：提出选项，由用户决定
  -> 在变更记录写清目的、边界、流程分支、失败路径、接口影响和验收
  -> 涉及新设计判断：用户确认文本；有未决事项则先讨论，不开始实现
  -> 实现代码、调整受影响契约和测试
  -> 离线回归；涉及模型行为时再进行真实 Query / Trace 验收
  -> 对照设计复核，记录结果、局限和剩余问题
```

小缺陷使用简短记录；跨模块行为变更才展开设计，不要求每个改动生成多份文档。需要改变金融行为时先明确仓库设计与用户确认，再对齐契约、实现和验收；飞书不再是前置步骤。开发文档不注入金融 Agent 的上下文；金融 Agent 继续由自身 System Prompt 与 Skills 配置驱动。

记录时分清三件事：设计是待确认/已确认/被替代；代码是未实现/部分实现/已实现；验证是未验证/通过/未通过/无法判断。设计确认、代码存在和验收通过不能相互替代。文档迁移中发现的旧规则冲突只先修文本、记录实现待核对项，不顺手重写 Runtime。

当前共同确认顺序是产品目标、总体架构、接口和验收，再选择具体迭代。Run/Subagent 的队列、事务、重启恢复和多用户设计尚未因本次文档整理获得实现授权。

验收至少分清：代码/权限/接口是否符合约定、真实模型链路是否符合设计、金融分析质量是否有充分证据，以及系统在重复调用/负载下是否稳定。后一层不能由前一层代替。

## 4. 可重复执行的基线

```powershell
uv sync --locked --group dev
uv run --locked python scripts/check_finance_baseline.py
uv run --locked python scripts/test_finance_baseline.py
uv run --locked python run_finance.py --port 8510
```

凭证使用忽略于 Git 的 `.env.local`。文章路径以新项目为基准解析；金融启动入口拒绝指向项目外的文章根。历史入口 `run_policy_api.py` 不作为新产品的启动方式。

用户于 2026-10-17 确认命名模型的启动/测试参数切换，设计与验证记录见[模型配置与复验](changes/005-model-profiles-and-revalidation.md)。每次运行固定供应方与模型，不进行热切换；既有不传参数的环境配置用法保留。

```powershell
# Choose one command per process. Credentials remain in ignored .env.local.
uv run --locked python run_finance.py --port 8510 --model-profile gpt
uv run --locked python run_finance.py --port 8510 --model-profile deepseek
# Optional transport; existing GPT profile remains unchanged.
uv run --locked python run_finance.py --port 8510 --model-profile gpt-responses
```

上述启动命令选择其一，不同时占用同一端口；切换时停止原进程再启动。命名配置位于 `config/model_profiles.json`，GPT 的凭证变量为 `ANTHROPIC_AUTH_TOKEN`，DeepSeek 为 `DEEPSEEK_API_KEY`。付费复验使用 `scripts/finance_research_live_acceptance.py --live --output data/acceptance/<新批次> --repetitions 1 --cases Q04 --model-profile deepseek`；可用 `--snapshot-from data/acceptance/<已有批次>` 复用经哈希校验的独立语料快照。不因换模型复用进行中的会话或混合模型成功率。

可选 Responses 设计、停止策略和分层验证见[变更 007](changes/007-responses-transport-and-delivery.md)。合成传输对照使用 `uv run --locked python scripts/finance_transport_comparison.py --live --output data/acceptance/<新批次> --repetitions 3`，最多 42 次原始请求、默认整批 1200 秒，未通过时不继续付费业务循环。该脚本不执行工具、不读取真实语料、不使用隐藏重试，也不为金融答案自动打分。

基线检查不调用付费模型，不把真实文章提交到仓库。真实 Query 测试应显式启动，并将 Query、输入样本、模型配置（不含凭证）、Trace、输出和分层判定放在同一验收记录里。当前业务测试集沿用既有 1.0，不擅自新增自动语义评分。

`test_finance_baseline.py` 显式选择金融与共享 Runtime 相关测试，清除运行进程的真实模型凭证，并为测试提供临时工作空间和文章根。它不是全量继承测试通过的替代声明。原项目部分旧测试直接依赖运行目录中的同花顺数据、政策样例与评测文件；这些数据不属于新产品，未迁移。全量继承测试的原始结果与归因保留在本次分离记录中，未把失败改为通过或更改断言。

## 5. 方法来源与适用边界

沿用先前依据 [Anthropic AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook) 建立的设计先行、版本化产物、人的判断责任与验证反馈闭环，不把它解释为必须全面重构。仓库 Markdown 作为当前设计权威是本项目用户的明确决定，不声称该文章规定必须使用某种文档模板或数量。

用户提供的微信公众号链接本次被环境校验页拦截，未读取正文，因此不据此推导开发规范。拿到正文后可再补充核对。
