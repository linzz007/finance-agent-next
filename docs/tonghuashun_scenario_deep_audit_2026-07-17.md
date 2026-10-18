# Agent Workbench 同花顺场景深度审计

日期：2026-07-17

## 1. 最终判断

当前版本已经满足本项目约定的核心目标：它是一个可真实使用、可观察、可回放的单用户 Agent Workbench，而不是只会返回一段文本的静态 Demo。

已经实际验证的范围包括：

- 只使用真实可用的 `deepseek-v4-flash`；没有模型鉴权时前后端均拒绝执行。
- 普通对话、Slash Skill、自动路由、政策分析、新闻分析、调研和企业 Wiki 查询能够进入各自的运行边界。
- 每轮都有 AgentRun、AgentStep、ContextManifest、Tool/Gate、Model、Memory 和最终回答证据；未触发的能力按 0 记录。
- 同一会话上下文、跨会话长期 Memory、会话隔离和长对话压缩均通过真实多轮测试。
- 同花顺企业事实、系统分析推断、项目设计观点和用户偏好不会再混成同一种“企业公开事实”。
- 桌面端、平板端和手机端均完成动态操作测试，而不是只检查静态 HTML。

这不代表所有生产化需求都已完成。SSE Token 流式输出、多租户鉴权、生产级限流与背压、全量金融监管语料、完整 Artifact 在线预览等仍不在当前完成范围内。

## 2. Company Wiki 中实际放了什么

当前同花顺 Wiki 位于 `data/companies/company_001/wiki/`，共有 6 个 Markdown 文件、15 条结构化事实。主要内容如下：

- `public_disclosure`：公司身份、所在地、核心业务、收入与客户、问财、iFinD、AI 研发方向、上市公司资质等公开披露。
- `analysis_inference`：数据安全风险、投资建议合规风险。这些是系统分析推断，不是公司自述。
- `project_design`：为什么金融 Agent 需要 Harness，以及报告展示偏好。这些是项目设计观点。
- `user_confirmed`：用户确认的同花顺分析重点。

关键边界：

- 问财、iFinD 和 AI 研发方向主要使用巨潮资讯公告作为一手证据。
- BizFinBench 使用新浪财经公告镜像作为二手入口，置信度为 0.8，不能与一手公告等价。
- Wiki 只确认了 AI 研发、微调和知识能力方向；“产品已经采用某种具体 RAG 架构”仍是未知项，系统不得补写。
- 每条事实现在统一携带 `source_kind`，回答和报告会显示来源类型与 `fact_id`。

## 3. 外部数据源实际获取结果

### 技术新闻

快照：`data/news/snapshots/2026-07-17/news-snapshot-a797c8ffd349.json`

- 6/6 数据源成功，共 62 条原始事件。
- Claude Code Release 10 条、OpenAI Codex Release 10 条、LangGraph Release 10 条、MCP Release 8 条、Hugging Face Blog 12 条、arXiv 12 条。
- Hugging Face 缺失或质量不足的摘要不会直接作为可靠事件摘要。
- 用户未请求论文时，arXiv 论文不会混入产品 Release 结果。
- 新闻输出会保留发布时间、抓取时间、规范 URL、来源级别和内容校验和。

### 政策数据

快照：`data/policies/snapshots/2026-07-17/policies-snapshot-759b4aad33ca.json`

- 3/3 已配置入口成功，共得到 4 份可审计文件。
- 当前集合足以验证政策检索、日期过滤、适用性分类和证据引用链。
- 当前集合不是完整金融监管库，证监会、个人信息保护、算法推荐和生成式 AI 的全量规则仍需继续接入。

因此，新闻连接器已经可以支撑技术情报 Demo；政策连接器可以支撑 Harness 和证据链验证，但不能宣称覆盖完整金融合规判断。

## 4. 一次真实普通对话的后台链路

浏览器中实际执行的问题：

> 请介绍 Agent Workbench 能做什么，并说明哪些步骤按条件触发。

该轮 Trace 为 8 个步骤：

1. 识别本轮意图。
2. 选择 `general_chat` Skill。
3. 读取长期 Memory。
4. 通过 ToolGateway 调用 `memory_search` 和 `company_wiki_search`。
5. 构建 ContextManifest。
6. 调用 `deepseek-v4-flash`。
7. 执行回答契约 Gate。
8. 保存最终回答。

本轮运行证据：

- 工具 2 次，均由 allowlist 放行。
- Gate 2 个，结果为 `allow:1 / repair:1`。
- Memory 原始候选 5 条，实际读取 1 条，过滤 4 条。
- Context 可见 8 块、隐藏 3 块；可见内容估算 868 tokens，序列化模型 Prompt 为 2041 tokens。
- 模型调用 1 个，共尝试 2 次；第一次因 JSON 非法触发重试，第二次成功。
- 页面和 Trace 中均未出现 API Key 或鉴权环境变量。

这里的“思考”是可审计的 Runtime 阶段、输入、输出和决策摘要，不展示或伪造模型私有思维链。

## 5. 单轮、多轮与业务语义测试

### API 对话矩阵

证据：`data/acceptance/workbench_api_dialogue_matrix_20260717_123956.json`

- 12/12 通过。
- E2E 平均 4564.68 ms，最大 11098.23 ms。
- 模型调用平均 3463.37 ms，最大 6381.89 ms。
- 覆盖普通问答、项目范围澄清、专业调研、政策分析、实时新闻、企业事实来源和 Workbench 自描述。
- 10 次模型调用累计输入 6426 tokens、缓存读取 13056 tokens、输出 3327 tokens。

### 多轮与 Memory

证据：`data/acceptance/workbench_multiturn_acceptance_20260717_121502.json`

- 4/4 通过，总耗时 26984.81 ms。
- 同会话追问能够读取最近历史。
- 写入长期 Memory 后，新会话能读取同一条真实 `memory_id`。
- 其他会话的 `conversation_summary` 不会作为当前会话 Memory 泄漏。
- 长对话触发摘要后仍受历史条数和 Token Budget 约束，且不会自行补写用户动机。

### 业务语义

证据：`data/acceptance/business_semantic_acceptance_20260717_134529.json`

- 17/17 通过。
- 该矩阵在删除旧模型旁路、加入模型边界治理和移除重复配置写入后重新执行，绑定当前服务进程与当前代码。
- 覆盖模型边界、企业证据边界、政策四分类、未覆盖法律、无关政策、政策多轮追问、跨会话 Memory、新闻时间窗、发布主体白名单和上线评审契约。

### 无模型边界

- `test_chat_workbench_service_rejects_send_without_model_credentials` 通过。
- 缺少鉴权时模型状态为不可用，发送请求直接报错，不使用本地确定性模型或模板伪装成功。

## 6. 性能和参数判断

当前真实模型参数：

- Model：`deepseek-v4-flash`
- `temperature=0`
- `max_tokens=2048`
- `timeout=45s`
- `max_attempts=2`

判断：当前参数适合这个以结构化契约和可重复评测为主的 Workbench，暂时不建议继续调整。

- `temperature=0` 有利于结构稳定和回归复现。
- `max_tokens=2048` 足够普通对话和结构化中间结果；长报告应拆成 Artifact 阶段，不应单纯放大单次输出上限。
- `max_attempts=2` 有必要。本轮浏览器真实调用出现过一次非法 JSON，第二次成功；改成 1 会降低成功率。
- `timeout=45s` 对外部兼容接口较保守，但当前没有发现需要缩短的可靠证据。

并发证据：`data/acceptance/workbench_concurrency_benchmark_20260717_124806.json`

| 并发 | 成功 | 整波耗时 | 吞吐 | P95 |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 1310.26 ms | 0.763 rps | 1308.67 ms |
| 2 | 2/2 | 1715.49 ms | 1.166 rps | 1282.40 ms |
| 4 | 4/4 | 2967.39 ms | 1.348 rps | 2901.47 ms |

与较早的 C4 暖运行记录相比，整波耗时从约 5.32-5.58 秒降到 2.97 秒，吞吐从约 0.72-0.75 rps 提高到 1.348 rps。该对比不是严格控制变量实验，只能作为本机观察结果，不能包装成通用性能结论。

后续应该持续记录 JSON 重试率。如果稳定样本中的重试率超过 3%，优先研究 Provider 的结构化输出或 Tool Use 契约，而不是继续增大 Token 上限。

统一模型旁路后的最终真实冒烟请求耗时 1822.68 ms，使用 `deepseek-v4-flash` 1 次调用、1 次尝试、0 重试。回答正确说明了确定性 Runtime、模型阶段、Trace、无模型拒绝和无隐藏模型旁路，且没有误引企业 `fact_id`。单次暖运行不能代替完整基准，但可以证明最新服务进程仍能真实端到端执行。

## 7. 前端设计参考与动态验收

前端没有复制其他项目源码，当前为原生 HTML/CSS/JavaScript 实现，但采用了成熟产品中已经验证的交互模式：

- ChatGPT/Claude 类产品：会话侧栏、底部 Composer、模型位于输入区、移动端抽屉。
- Claude Code/Codex 类 Harness：执行过程与本轮回答放在同一消息流，先展示运行摘要，再展示最终答案。
- 命令面板类产品：输入 `/` 发现并选择 Skill，不用大卡片占据聊天区域。
- Langfuse 类观测产品：Run -> Step -> Context/Model/Tool/Gate/Artifact 的分层证据。

本轮实际动态检查：

- 320x700、390x844、768x720、1280x720 四档均无整页横向溢出，App 高度等于视口高度。
- 320px 下占位文字自动增高，输入框不再出现原生滚动箭头。
- 手机抽屉打开和关闭后不会遗留 `sidebar-collapsed` 冲突状态。
- 桌面侧栏可从 288px 折叠到 64px，并正确恢复；辅助名称同步切换为“展开/收起侧边栏”。
- 聊天记录为独立滚动区；Trace 展开后 Composer 仍固定可用。
- 模型选择只有一个真实选项：`deepseek-v4-flash`。
- 模型重试摘要会显示“2 次尝试 · 1 个调用发生重试”，不再使用歧义文案。

当前前端已经达到成熟作品集和面试演示要求，但没有依据宣称它等同于某家大厂完整设计系统的像素级成品。

## 8. 本轮发现并修复的问题

1. 通用 Memory 检索可能读到其他会话摘要：改为 `conversation_summary` 只在本会话内使用。
2. “我的项目”范围不明确时直接使用同花顺画像：新增范围澄清 Gate，不调用模型和重型工具。
3. 长期 Memory 回答可能缺少真实 ID：回答现在绑定本轮实际读取的 `memory_id`。
4. Workbench 与 CoursePilot 比较时混入同花顺事实：改为未绑定项目范围并要求澄清。
5. 企业事实来源显示为 `other`：统一 `source_kind` 分类并在政策/新闻证据中保留。
6. Workbench 自描述夸大为所有能力每轮必触发：改为每轮固定留痕与条件触发能力的准确区分。
7. 新闻影响链使用通用模板：增加与会话恢复、后台任务等事件对应的专属影响链。
8. Trace 重试文案不明确：同时展示总尝试数和发生重试的调用数。
9. 移动端侧栏关闭误触发桌面折叠：修复侧栏状态机，并在断点变化时归一化状态。
10. 320px 输入框占位文字触发原生滚动条：改为自动高度并仅在超过 160px 后内部滚动。
11. 四个确定性政策 Stage 残留旧 `POLICY_IMPACT_LLM_ENABLED` 旁路：删除旧客户端及所有调用点，模型调用只能进入统一 Flash Gateway 和 Trace；Harness linter 会阻止旧标记再次出现。
12. 每次发送消息都重复 upsert 固定模型配置：改为仅在创建会话时初始化，发送和模型查询不再产生无意义配置写入。

## 9. 最终回归

- `pytest -q`：178 passed。
- `node --check src/policy_impact/app/static/workbench/app.js`：通过。
- `scripts/harness_linter.py`：通过。
- 浏览器页面：`http://127.0.0.1:8501/`。

## 10. 仍未完成的生产化能力

- 真正的 SSE/Token 流式输出；当前是运行完成后刷新整轮消息和 Trace。
- Artifact 在线预览、点击下载和独立检索页。
- 会话删除、归档、批量管理和完整键盘导航。
- 多租户、统一身份认证、密钥管理服务、限流、背压、熔断和生产 SLO。
- 原始 Trace 详情会在本地页面中展示完整 Prompt 与企业上下文；多用户部署前必须增加按需加载、字段级脱敏和 Trace 权限控制。
- 全量金融监管连接器和法律意见级知识覆盖。
- 对任意本地项目进行代码检索与代码画像；当前项目不以代码修改或代码库理解为核心能力。

这些缺口不阻塞“单用户 Agent Harness 学习、长期自用、秋招作品集和面试讲解”的当前目标，但在简历和面试中必须如实限定范围。
