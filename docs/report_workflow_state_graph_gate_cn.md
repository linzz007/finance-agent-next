# 两条报告工作流的 State → Graph → Gate

## 先看边界

`/report` 由外层 AgentLoop 当作受限工具调用；进入后按工作空间能力选择业务管线。企业/个人技术 Wiki 共用资料管线 A；金融观察使用金融管线 B。两条管线的业务节点不同，但都使用 `ReportStageGraph` 约束节点顺序、阶段输出和转移。普通聊天与动态 Subagent 不因此变成固定工作流。

代码入口：`src/policy_impact/skill_executors/recent_news_report.py` 的 `run_recent_news_report`，以及 `src/policy_impact/domains/finance/workflow.py` 的 `run_finance_report`。共同的阶段控制器在 `src/policy_impact/harness/report_stage_graph.py`。

## 执行模型

```text
本轮输入 + Wiki / 金融档案
  → StageSpec：定义节点、允许的后继节点、必需输出与校验
  → ReportState：记录 current_stage、已完成节点的输出、内部中间值、转移与状态
  → graph.start(node)：只有当前节点能进入
  → 节点执行：资料获取、分析、模型调用或产物生成
  → graph.finish(node, outputs, gate)：先验收输出，再依据 Gate 选择下一节点
  → 下一节点读取 graph.state.require(...) / require_value(...)；deny 时不能进入后续节点
  → AgentStep / RunArtifact：记录 Gate、转移、阶段结果
```

`allow` 表示验收通过；`ask` 表示证据不完整，可沿明确的受限路径继续并将整轮标记 `partial`，不是自动向用户弹出审批；`deny` 阻断后续节点。模型 Schema 或引用错误的有限修复发生在分析节点内部，不是假装 Graph 重新跑了整个数据采集流程。

阶段公开输出进入 Trace；较大的快照、候选事件和 Wiki 事实进入本轮私有 State 值，后续节点从 State 读取，不把它们全部重复写进每个阶段事件。`workflow_graph` 摘要只暴露输出键与内部值的键，不把大对象再次复制进总结。

## 管线 A：资料影响报告

| 节点 | 写入 State 的关键结果 | Gate 后续 |
| --- | --- | --- |
| `wiki_context` | Wiki 快照、任务画像、报告请求、探查计划 | 输出齐全才进入采集 |
| `source_collection` | 原始来源快照、采集状态 | 快照结构正确才进入清洗 |
| `filtering` | 查询约束、质量诊断、可发布事件 ID | 有证据 `allow`；不足 `ask` 并携带原因进入受限分析 |
| `impact_analysis` | 分析状态、诊断、入选事件 ID | 完整 `allow`；缺证据或模型校验不完整 `ask` |
| `report_rendering` | Markdown / HTML 路径 | 终止；前面曾 `ask` 则整轮 `partial` |

资料与 Wiki 的差异由请求参数决定，不为每个工作空间复制一条硬编码管线。未来时间窗口不采集未发布资料；当前代码仍会生成解释“尚无证据”的受限诊断报告，不能把它称为正式有证据结论。

## 管线 B：金融观察报告

| 节点 | 写入 State 的关键结果 | Gate 后续 |
| --- | --- | --- |
| `profile` | 金融档案、时间范围、资产集合、Wiki fact ID | 档案不存在则阻断 |
| `acquisition` | 行情及资料快照 ID、来源状态 | 采集后的快照必须匹配本轮请求 |
| `validation` | 序列/文献数量、缺失警告 | Schema、报告期或品种不合格则阻断 |
| `metrics` | 确定性计算结果和公式 | 指标结构正确才进入覆盖度校验 |
| `coverage` | 覆盖度 Gate、缺失警告 | 严格模式证据不足 `deny`；宽松模式 `ask` 并披露 |
| `research_audit` | 资料用途、时间边界与反向观察 | 审核结果进入分析 |
| `analysis` | 中文分析、错误和引用复核 | 失败时严格模式阻断；宽松报告可部分发布；聊天分析到此结束 |
| `publication` | HTML / Markdown / JSON 产物位置 | 仅报告模式进入，发布后结束 |

`finance.collect`、`finance.metrics`、`finance.research_audit`、`finance.publish` 仍经 `ToolGateway` 和权限策略。`publish_saved_analysis` 是“复用已核验分析直接发布”的短路径，不会伪装成重新执行完整采集 Graph。

## 怎样核对是否真的生效

1. 看一次运行的 `workflow_stage` Trace：每个完成节点都有 `gate` 和 `graph_transition`，下一个节点与 `transition.to` 一致。
2. 看资料报告 RunArtifact 的 `workflow_graph`：应有每个节点的输出键、转移及最终 `done`/`partial` 状态。
3. 看金融报告返回值的 `workflow_graph`；HTML 发布后的最终转移在运行 Trace/返回值中。发布文件在发布节点完成前落盘，所以持久化的金融报告 JSON 不声称包含最终完成状态。
4. 故障测试：缺少必需输出不能进入下一节点；金融严格模式覆盖不足时停在 `coverage`，不会运行 `research_audit`、`analysis` 或 `publication`。

验证命令：`py -3 -m pytest tests/test_report_stage_graph.py tests/test_finance_workflow.py tests/test_profile_aware_news_report.py -q`。这套控制器是**固定业务节点的状态转移与门控**，不是并行 DAG 调度器，也不等于通用 AgentLoop。
