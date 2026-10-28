# H2 锚点补正的完整动作契约

日期：2026-10-20。范围：073 已批准技术机制的最小修复；不改变金融分析或权限设计。
用户授权常规技术问题借鉴成熟实现后直接解决，复杂问题采用 MVP；先记录设计再修改代码。

## 1. 已复查的原因

[R21](../acceptance/harness-v2-live-acceptance-r21-20261020.md) 实际触发两次锚点补正：
孙任务返回裸 `AnchorPatch`，缺少 AgentAction 的 `type=final/output` 外壳，被公开动作解析拒绝；
一级子任务返回完整动作，6 个锚点全部补正，但无法补回上游未接纳的原始来源。
孙任务不是“通过补丁校验后仍不匹配正文”，原报告的这项归因需纠正。

当前反馈把 `final_output_json_schema` 改为 AnchorPatch，但 `action_json_schema` 仍继承
整个工具/调度动作联合。文字虽已要求 final，机器可读的允许动作范围却比本次实际能力宽。
这是常规输出契约呈现问题，不是需要引入新 Agent 框架或放宽 JSON 解析的证据。

[073](073-h2-framework-informed-anchor-repair.md) 固定的 pi/DeepSeek Harness 源码提供
解析、严格终态校验、错误观察的分层参考；不声称它们已经实现本项目的金融锚点补正。
网页读取仍不可用，结论来自固定 commit 的官方源码副本，不来自二手文章或搜索摘要。

## 2. 最小契约

1. 仅 `finance.h2.immutable_anchor_correction.v1` 替换 `action_json_schema`：
   顶层只允许 `type: "final"` 与 `output: AnchorPatch`，两者必填，禁止额外字段。
   使用严格 Pydantic 模型同时生成完整 Schema 与执行校验，避免两份规则漂移。
2. 反馈提供 `action_example`，为可校验的完整最小动作，而不是裸 output：

```json
{"type":"final","output":{"schema_version":"finance.h2.anchor_patch.v1","anchors":[]}}
```

3. 明确区分完整动作与内部 `final_output_json_schema`；锚点 text 必须复制冻结分析的
   `content/candidate_answer`，不是从原始来源摘录另造一段；不确定允许省略。
4. 原有 link_index 唯一/范围、逐字定位、来源 refs/quotes/正文不可变校验保持。
   不自动给裸补丁加外壳，不截取 JSON，不执行工具，不接受新正文或 state_update。
5. 不改变 `agent_action.v1`、模型、预算、一次补正、相同请求一次传输重发或记账。
   普通 Root/非锚点错误仍使用原隔离补正；容量不足、部分结果保留和真实故障边界不变。

## 3. 验证

- 先以失败测试复现补正仍收到通用动作联合、缺少完整示例的问题。
- 检查 Schema 与完整示例，正向补丁通过，裸补丁/工具/额外正文/refs/quotes/state 被拒绝。
- 检查真实 SQLite/FakeRedis/模拟 HTTP 模型输入，首发与普通补正不变；补正仍同 Attempt/
  模型/预算、逐请求记账；传输重发消息完全相同，不新增研究或请求。
- 保留正文不可变、部分结果交付、原来源校验的全部既有测试；执行 H2、默认金融、
  配置隔离和静态回归。未运行的真实模型分支不能报通过，不追加到 R21。

## 4. 实现与实际结果

已实现：[finance_anchor_repair.py](../../src/policy_impact/dispatch/finance_anchor_repair.py)
用同一私有严格动作模型生成完整 Schema、生成可校验示例并验证补丁。`_decide` 的既有
反馈覆盖机制即可注入专用 Schema，无须新增执行分支或修改公开解析器。

- 新增 7 项用例；其中 3 项先复现反馈缺少专用 Schema/完整示例的失败，再通过修复。
  锚点与实际调度补正定向测试 **50 passed**（17.84 秒）。
- 完整 H2/state/compatibility/live-driver **970 passed / 7 skipped**（401.18 秒）。
- 默认金融/shared-runtime **1638 passed / 30 skipped**，52 模块（128.20 秒）。
- 配置/项目隔离 **10/10**；3 个受影响 Python 文件完整 Ruff、tracked diff 和
  3 份相关 Markdown 的 5 个本地链接目标检查通过。跳过项不算通过。
- 按相同 `json.dumps` 序列化方式，反馈的动作 Schema 从通用联合 **7,034 字符**缩小为
  **877 字符**。这是该字段的字符数，不是总上下文 Token、耗时或成功率测量。
- SQLite/FakeRedis/模拟 HTTP 模型用例核对完整专用 Schema 和示例确实进入模型输入，
  首发和非锚点补正保持原契约，同模型/Attempt/预算与物理记账不变。

测试记录保存在项目旁 `runtime-records/framework-research-20261020/anchor-schema-*.xml`，
包括原失败记录。两套广域回归仅有既有 Starlette/httpx 弃用警告。
076 修复后未追加付费请求，不回写 R21 的成功率；R21 仍是 partial 研究证据。
没有更改默认 1.0 入口、真实数据库、Redis/Docker、模型选择、预算、重试数或 Git 上传。
本项不证明金融语义质量、真实 Redis、生产并发或完整业务验收；总体目标仍未完成。
