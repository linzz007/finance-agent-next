# H2 金融规则资产

执行边界与快照规则见 [101](../../docs/changes/101-h2-external-agent-rules.md)。

| 想调整的内容 | 文件 |
| --- | --- |
| 主 Agent 如何研究与委派 | root.md |
| 主 Agent 看到的角色用途、输入要求 | ../../config/agents/finance.json |
| 子 Agent 共同的任务/证据/交接规则 | child-common.md |
| 分析、核验、反例的方法 | analyst.md、verifier.md、skeptic.md |
| 调度、预算和失败后的行为建议 | delegation-fixed.md、delegation-requested.md |
| 工具用途简介 | tool-descriptions.json |
| 文章/背景引用、研究时钟、去重视图规则 | action-context.md、research-clock.md、context-view.md |
| 公开网页、行情、个人资料边界 | public-source.md、market-source.md、personal-source.md |
| 动作结构补正、子结果结构补正、冻结正文锚点补正 | action-repair.md、research-repair.md、anchor-repair.md |

Markdown 是实际加载的指令，不会把本 README 发送给模型。root/child-common 仅支持已声明的 `{{...}}` 装配占位符，不能执行表达式；JSON 示例的普通花括号保留原样。工具参数和结果 Schema 由程序生成，不在这些文件中复制维护。

新金融 Run 在创建时冻结完整规则包及内容哈希，Root、子/孙和恢复只用该包。修改文件影响下一次新 Run，不热更新进行中的任务。内容哈希用于追溯，不是授权机制。

`legacy-pre-101.json` 是迁移前指令的固定兼容包，仅供没有规则快照的旧 Run 使用。不要编辑它来调整新任务；代码固定校验其总哈希。它不能还原更早未知版本的历史输入。

实际权限、额度、层级、租约、工具执行和结构验收仍在 Runtime。修改文案不能启用新工具、换付费模型或提高额度。先更新设计，再修改对应资产并运行离线回归。
