# Report Workflow API 验收记录（2026-09-13）

## 1. 本轮目标

本轮验收目标不是证明报告“永远正确”，而是验证 Report Workflow 是否符合当前产品设计：

- 使用共享外部来源池，而不是同花顺自有网页充当数据源。
- 企业 Wiki 与个人 Wiki 分开生效：`company_001` 服务同花顺场景，`agent_self_evolution` 服务个人 Agent Harness 学习场景。
- 严格政策查询不能用新闻、问答、解读材料冒充政策原文。
- 个人 Agent 自进化报告不能用 IPO、客户案例、泛 AI 新闻凑数。
- 找不到证据时必须 partial/空报，并说明原因。
- API 结果必须能追溯到 HTML/Markdown 报告、RunArtifact 和 Trace。

## 2. 本轮代码调整

### 2.1 数据源边界

当前数据源定位为“共享外部来源池”，包括：

- GitHub Release：Claude Code、OpenAI Codex、LangGraph、MCP、Hermes、EvoAgentX。
- 官方/准官方内容：OpenAI News、Hugging Face Blog、Google DeepMind Blog。
- 论文与仓库发现：arXiv Research Search、GitHub Public Repository Discovery。
- 新闻线索：TechCrunch AI、The Verge AI、Google News Public Topic Search。
- 政策原文/政策资料：国家网信办政策法规、国家网信办数据治理等。

说明：L5 新闻源只做线索，关键结论必须回链到官方源、原始公告或 Wiki 事实。

### 2.2 严格政策原文过滤

对包含“正式发布、官方政策、政策原文、不用新闻报道冒充政策原文”等约束的 query，启用严格模式：

- 只接受 `policy_document` 类型。
- 排除新闻报道、答记者问、政策法规问答、解读、图解、征求意见稿。
- 要求政策文本本身命中金融数据、投研、金融信息服务等主题，不能只靠 Wiki 背景强行桥接。
- 找不到合格条目时，报告状态为 `partial`，入选证据为 0，不补齐数量。

### 2.3 个人 Agent 自进化报告过滤

对包含 Agent 自进化、EvoAgentX、Hermes、Claude Code、Codex、Harness、Runtime、Subagent 等意图的 query，收紧入选条件：

- 必须同时命中目标实体或机制，以及技术变化信号。
- 排除 IPO、融资、估值、CEO 访谈、客户案例、纯提效案例、泛 AI 新闻。
- 若只找到开发者工具更新而没有 Agent 自进化机制证据，则报告为 `partial`，并明确“不为凑数量补齐”。

### 2.4 用户可见报告清理

调整行动建议生成：

- 个人 Wiki 中的执行边界仍进入 Context/Profile/Trace。
- 不再把整段用户偏好约束拼接到每条 `recommended_action` 后面。
- 用户可见建议保持短句，系统约束留在报告边界说明和 RunArtifact 中。

## 3. 自动化测试

### 3.1 单元与规则测试

命令：

```powershell
py -3 -m pytest tests\test_profile_aware_news_report.py tests\test_shared_report_request.py tests\test_report_discovery_gateway.py tests\test_shared_source_discovery.py -q
```

结果：

- `170 passed`

补充验证：

```powershell
py -3 -m pytest tests\test_profile_aware_news_report.py tests\test_report_chat_summary.py -q
```

结果：

- `71 passed`

### 3.2 API 级验收

服务地址：

- `http://127.0.0.1:8503`

命令：

```powershell
py -3 scripts\run_report_api_probe.py
```

结果文件：

- `data/acceptance/latest_workbench_api_report_probe.json`

## 4. API 验收用例与结果

### 4.1 企业严格政策报告

Query：

```text
/report 只看最近90天正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；不用新闻报道冒充政策原文，最多3条。
```

结果：

- `company_id`: `company_001`
- `skill_id`: `external_impact_report`
- `analysis_status`: `partial`
- 时间窗口：`2026-06-15` 至 `2026-09-13`
- 入选证据：0 条
- Trace：24 steps，7 gates，3 artifacts
- 结论：通过。没有用问答、解读或新闻冒充政策原文。

验收依据：

- Markdown 报告：`data/companies/company_001/reports/2026-09-13-recent-news-impact-d1680322.md`
- HTML 报告：`data/companies/company_001/reports/2026-09-13-recent-news-impact-d1680322.html`
- RunArtifact：`data/companies/company_001/runs/d168032251034758b8388b72162a664d/2026-09-13-news-run_artifact.json`

### 4.2 企业默认影响报告

Query：

```text
/report 请基于当前 Wiki，生成最近一周值得同花顺 AI 产品关注的外部变化影响报告，最多3条，要求给出来源、影响判断和下一步建议。
```

结果：

- `company_id`: `company_001`
- `skill_id`: `external_impact_report`
- `analysis_status`: `done`
- 时间窗口：`2026-09-06` 至 `2026-09-13`
- 入选证据：2 条
- Trace：25 steps，7 gates，3 artifacts
- 结论：通过。报告把数据治理、金融服务 AI 产品变化与问财/iFinD Wiki 事实进行了条件式关联。

入选来源：

- `数据出境安全管理政策法规问答（2026年9月）`
- `Introducing ChatGPT for Financial Services`

验收依据：

- Markdown 报告：`data/companies/company_001/reports/2026-09-13-recent-news-impact-b804e008.md`
- HTML 报告：`data/companies/company_001/reports/2026-09-13-recent-news-impact-b804e008.html`
- RunArtifact：`data/companies/company_001/runs/b804e008197744ff947ce66eea6f0c61/2026-09-13-news-run_artifact.json`

### 4.3 个人 Agent 自进化报告

Query：

```text
/report 请基于当前 Wiki，生成最近30天 Agent 自进化、EvoAgentX、Hermes、Claude Code、Codex 相关技术变化对我学习 Agent Harness 的影响报告，最多3条，要求给出来源和行动建议。
```

结果：

- `company_id`: `agent_self_evolution`
- `skill_id`: `external_impact_report`
- `analysis_status`: `partial`
- 时间窗口：`2026-08-14` 至 `2026-09-13`
- 入选证据：1 条
- Trace：25 steps，7 gates，3 artifacts
- 结论：通过。已排除 IPO、客户案例、泛 AI 新闻；只保留与 Agent Harness 学习有关的技术变化，并承认证据不完整。

入选来源：

- `Introducing the Agents API`

验收依据：

- Markdown 报告：`data/companies/agent_self_evolution/reports/2026-09-13-recent-news-impact-c13fc92b.md`
- HTML 报告：`data/companies/agent_self_evolution/reports/2026-09-13-recent-news-impact-c13fc92b.html`
- RunArtifact：`data/companies/agent_self_evolution/runs/c13fc92b134249a7862510bd22b1ac83/2026-09-13-news-run_artifact.json`

## 5. Subagent 复核结论

验证 subagent 对最终 API 输出复核后给出结论：

- 企业严格政策报告：通过。空结果是合理结果，因为当前窗口内没有符合条件的政策原文。
- 企业默认报告：通过。能围绕同花顺 Wiki 输出来源、影响判断和建议。
- 个人 Agent 自进化报告：通过。已排除 IPO、客户案例和泛 AI 新闻；`partial` 状态合理。
- 剩余不足：个人报告仍只覆盖“开发者工具”，未覆盖 EvoAgentX/Hermes/Claude Code/Subagent/Agent 自进化机制本身，后续需要补更强的一手来源。

## 6. 当前结论

本轮功能验收通过。

当前系统已经满足：

- 同一 `/report` Workflow 可服务企业 Wiki 与个人 Wiki。
- 严格政策原文请求不会被新闻或解释材料污染。
- 个人 Agent 自进化请求不会被泛 AI 新闻和客户案例污染。
- 报告能生成 Markdown、HTML、RunArtifact，并可通过 Trace 复盘。
- 找不到证据时系统会 partial/空报，而不是硬凑结论。

## 7. 后续优化项

这些不是本轮阻塞项，但建议后续继续优化：

- 补强 Agent 自进化一手来源，例如更稳定的 EvoAgentX/Hermes/Claude Code/Codex 官方发布源、论文源和仓库详情。
- 将 `constraint_diagnostics.output_count` 改名或补充说明，区分“约束过滤后的候选数”和“最终报告入选数”。
- 单独增加 Source Health 页面，展示每个来源成功、受限、失败及最近抓取时间。
- 给 `/report` 增加前端参数化入口：时间窗口、最多条数、来源类型、是否严格政策原文、是否允许新闻线索。

