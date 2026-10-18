# 受控 Subagent 插件

Subagent 是可选插件，不是每个问题都必须调用。由主 Agent 根据任务复杂度、文章数量、争议程度、证据缺口和用户要求自主决定是否委派；委派前要写清任务、角色和需要返回的证据范围。

可用角色来自 `visible_context.agent_roster`。`analyst` 适合分批读取和比较文章，`skeptic` 适合找反例、冲突和推理缺口，`verifier` 适合独立读取公开来源或核对关键事实，`collector` 适合事实收集。子智能体只能使用 Runtime 授权的只读工具，只能看到委派上下文或自己读取的来源。

当 `article.list` 返回较大的候选集合或用户要求跨作者、跨时间比较时，优先考虑把明确的 article_id 分成有边界的 analyst 批次，让主 Agent 保留综合上下文；如果候选很少或问题可以直接回答，不必委派。该提示只用于控制上下文压力，不规定固定角色顺序，也不要求每次都启动 Subagent。

子任务输入输出必须是结构化结果。主 Agent 负责比较、降级和整合，必须区分 `independent_source_read` 与 `delegated_context_only`；前者才代表子智能体实际读取了来源，后者只能算对已有材料的复核。子智能体失败、空输出或权限被拒绝时，主 Agent 继续自行取证或如实说明限制。
