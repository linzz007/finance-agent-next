# Harness 2.0 金融状态增量协议

日期：2026-10-19  
状态：用户已确认，作为 H2-F08/F09 实现基线  
版本：`finance.state_update.v1`  
依据：[019 提案](019-finance-state-update-protocol-proposal.md)；调度接口见[接口契约](../harness-v2/interface-contracts.md)

## 1. 决定

用户确认采用方案 A：所有 Harness 2.0 金融 Root 模型动作都可以携带一个可选的
`state_update`。它是本次动作对 `FinanceRootState` 的增量，不是完整状态替换，也不是
让模型直接写 SQLite、Redis、Task、Attempt 或 Thread 生命周期。

旧版 1.0 AgentLoop、旧动作调用方和默认金融入口保持兼容。只有显式 H2 金融 Root
使用该协议。

## 2. 动作结构

动作仍然只有 `tool_call`、`delegate`、`final` 和 `request_approval` 四类；新增字段为：

```json
{
  "type": "tool_call",
  "tool_name": "article.read",
  "arguments": {"article_id": "article-1", "start_line": 1, "limit": 100},
  "state_update": {
    "research_plan": {
      "revision": 2,
      "goals": ["解释作者对黄金的条件性判断"],
      "scope": {"authors": ["培风客"], "period": "最近三个月"},
      "next_question": "先确认文章是否完整读取"
    },
    "article_updates": [
      {
        "article_id": "article-1",
        "relevance": "primary",
        "requested_depth": "deep",
        "reason": "该文章包含核心判断"
      }
    ],
    "coverage": {
      "covered_dimensions": ["author_viewpoint"],
      "counterexamples": [],
      "gaps": ["尚未检查外部事实"],
      "stop_rationale": "继续读取该文章后再判断"
    }
  }
}
```

顶层补丁字段及语义：

| 字段 | 合并方式 | Runtime 边界 |
| --- | --- | --- |
| `research_plan` | 计划快照 | 必须有递增的计划 revision；只存紧凑 JSON |
| `article_updates` | 按 `article_id` 合并 | 文章必须先由真实 list/search 发现；不得由模型伪造已读状态 |
| `evidence_additions` | 按三元组幂等追加 | EvidenceRef 必须属于当前 Run；冲突证据并列保留 |
| `coverage` | 覆盖判断快照 | 模型决定覆盖、反例、缺口和停止理由；Runtime 只校验引用和大小 |
| `review` | 审核判断快照 | 保留作者观点、反驳和未裁定争议；不因争议丢弃候选正文 |
| `delivery` | 交付建议快照 | 最终交付状态仍由有效正文、EvidenceRef 和 Runtime 事实共同决定 |

## 3. 事实合并规则

文章仍采用三层状态：

1. 发现层记录 `listed`；只有 `article.list`/`article.search` 的真实结果才能加入。
2. 读取层记录 `unread`、`partial`、`complete`、`failed`；由真实 `article.read` 结果维护，
   模型只能提交深度、相关性和理由。
3. 使用层记录 `undecided`、`used`、`excluded`；`used` 从最终答案的有效 EvidenceRef
   和审计推导，模型建议只作为理由保存。

模型不得通过 `article_updates` 直接设置 `read_status` 或 `use_status`。不存在的文章、
跨 Run 的 EvidenceRef、正文/逐字稿等大字段都被 Runtime 拒绝或忽略并留下 warning。

Evidence addition 至少包含 `claim_id`、`evidence_ref`、`relation`、`source_kind`、
`scope`、`certainty`。`relation` 为 `supports|contradicts|contextualizes`；
`source_kind` 为 `article|external|model_inference`；`certainty` 为
`direct|conditional|inconclusive`。`model_inference` 不得标记为 `direct`。

## 4. 缺失和失败

- 没有 `state_update`：动作仍可继续执行或交付，但 Root 标记 `incomplete_metadata`，
  Runtime 不猜测 coverage、review 或 used。
- 合法字段与非法字段同时出现：合法字段原子合并，非法字段留下 warning；不回滚已经
  观察到的工具事实。
- 结构错误或越权引用：不伪造状态。非终态动作返回 `contract_error`；终态候选保留完整
  正文，并以 `partial/incomplete_metadata` 交付，不能静默丢失答案。
- 相同补丁重复提交：不重复增加状态版本或 Evidence Ledger 条目。

## 5. F08/F09 验收

- 计划、文章状态、coverage、review 和 delivery 能随 Checkpoint 进入下一 Attempt；
- 未真实 list/read 的文章不能成为已读或 used；
- 有效 EvidenceRef 才能支持最终 `used` 推导；
- 作者观点、外部事实和模型推断类型不互相升级；
- 支持与反驳证据并列保存；
- 非法补丁不会丢失完整终稿；
- 无补丁时仍可交付，但元数据状态诚实可见；
- 重复补丁具有幂等性；
- 完成离线回归后，再用固定金融 Query 验证模型动作是否实际提交并正确使用这些状态。

本协议不规定固定文章数量、固定 subagent 分工、固定审核工具或自动模型切换；这些仍由
主 Agent 根据 Query 判断，Runtime 只守权限、预算、事实引用和持久化边界。

实现校验补充见 [028](028-finance-state-update-delivery-fixes.md)：终稿可选补丁结构错误时
保留正文并明确部分交付，恢复后的模型输入包含既有审核/交付状态。离线与真实 Redis、
真实模型验证的边界分别记录在 [R3 验收](../acceptance/harness-v2-fault-acceptance-r3-20261019.md)，
不能把旧版入口的 Query 结果当作 H2 状态协议的真实模型验收。
