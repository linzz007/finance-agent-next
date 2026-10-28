# 106 Profile-aware Spawn 契约

日期：2026-10-27  
状态：字段设计已确认；实现与离线回归随本记录更新。

本文在 104 的通用 Agent 与可组合专家方案上，定义模型侧 `agent.spawn` 的精简入口和 Profile 专属字段。执行层继续使用既有持久化 `Spawn` 契约，不修改 SQLite 表结构、Thread/Task/Attempt 语义或预算规则。

## 1. 设计

所有 Agent 共用一个 Harness 和 `agent.spawn` 工具。`general` 是默认 Profile，父 Agent 以一次任务消息动态指定临时专家方法；Analyst、Verifier、Skeptic 等 Profile 提供预设方法、权限和结果合同。Runtime 按选择把任务简报与 Profile 组合起来。Profile 不是固定工作流节点；Analyst 的独立核验属于 Analyst 专属的可选能力。

新 v4 Run 的模型侧核心参数：

| 字段 | 归属 | 规则 |
|---|---|---|
| `task_name` | 通用必填 | 简短任务标签 |
| `message` | 通用必填 | 本次任务目标及必要说明 |
| `profile` | 通用可选 | 注册 Profile；省略默认 `general` |
| `input_refs` | 通用可选 | 需要材料时显式授权，不继承父任务全部历史 |
| `task_instructions` | 通用可选 | 本次额外方法、范围或交付要求 |
| `success_criteria` | Profile 可选 | general 可省略；专业 Profile 要求本任务验收条件 |
| `output_contract` | 通用可选 | 仅可选注册合同；省略使用 Profile 默认值 |
| `profile_options` | Profile 专属可选 | 当前仅 Analyst 接受 `review_mode` 与 `review_options` |
| `parent_context_refs`、`depends_on_task_ids`、`priority` | 调度可选 | 仅在任务确有相应需要时传递 |
| `model_profile`、`wall_time_ms` | 执行可选 | Runtime 仍校验模型授权、父级边界及期限 |
| `requested_budget` | 旧策略兼容字段 | 仅历史 requested-budget Run 需要；fixed 策略拒绝此字段 |

模型不填写 `dispatch_key` 或 `role_version`。Runtime 从当前动作生成幂等键，并根据冻结的 Profile 快照解析角色版本。Analyst 默认输出合同为 `finance.analyst_result@2`；其他角色采用各自 Profile 中已登记的默认结果合同。指定的输出合同仍由 Repository 与 Profile 白名单校验。

Analyst 可选配置：

```json
{
  "profile_options": {
    "review_mode": "auto",
    "review_options": {
      "scope": ["source_fidelity", "reasoning_conditions"],
      "max_rounds": 5
    }
  }
}
```

`review_mode` 允许 `required`、`auto`、`off`，不提供时由 Runtime/Analyst Profile 使用 `auto` 默认。`review_options` 可整体省略；轮数不得超过五轮。其他 Profile 传 `profile_options` 会被 Runtime 拒绝。

## 2. 运行时转换

`tool.describe(name="agent.spawn")` 为新 v4 Run 返回模型侧 Schema 和 Profile 摘要。收到请求后，Finance Runtime：

1. 严格校验模型侧字段，并拒绝未知字段。
2. 解析所选 Profile；校验专业 Profile 所需验收条件及 Profile 专属字段。
3. 解析 `output_contract` 默认值，保留显式材料与父上下文引用。
4. 将 `message` 映射到内部 `objective`，并使用 Runtime 动作键填充 `dispatch_key`。
5. 将 Profile 最新允许版本映射到内部 `role_version`，将 `profile_options` 转为现有 ReviewPolicy 输入。
6. 生成既有内部 `Spawn`，由 Repository 继续校验角色授权、材料权限、预算、依赖关系、白名单和幂等性。

`task_name` 作为任务标签放入任务级指令，不新增长期数据库字段。新的模型入口不削弱已有持久化、工具权限、模型白名单、调用计数或结果合同检查。

## 3. Profile 可见信息

Root 初始上下文只显示 Profile 的用途、输入要求、输出合同和可委派范围，不注入各 Profile 完整 System Prompt、所有工具白名单或版本号。Root 可用 `tool.describe` 按需查看完整 `agent.spawn` 参数与当前 Profile 摘要。Prompt 描述帮助模型选择和填写字段；Runtime 仍是权限和参数校验的权威边界。

## 4. 兼容边界

新精简动作合同使用 `finance.h2.agent_rules.v4`。v1/v2/v3 Run 的规则快照、Prompt 和旧 `Spawn` Schema 保持不变；恢复中的旧 Run 不切换至新字段。新 v4 动作适配只存在于 Finance H2 的模型侧调度入口；Analyst 内部生成 Verifier 任务仍调用既有内部 `Spawn`。

## 5. 验证范围

离线测试覆盖：通用最小请求及 Runtime 默认值、Analyst 的验收条件/输出合同/核验选项映射、非 Analyst 核验选项拒绝、专属 Profile 默认合同、无效合同拒绝、Runtime 生成的 dispatch key/role version、持久化 Thread 与 Brief、工具 Schema 暴露，以及 v1/v2/v3 旧快照沿用原 Schema。离线替身结果不代表真实模型质量或真实 Redis 验收。
