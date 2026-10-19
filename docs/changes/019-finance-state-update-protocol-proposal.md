# Harness 2.0 金融状态更新协议提案

日期：2026-10-18  
状态：已确认；正式基线见 [026-finance-state-update-protocol.md](026-finance-state-update-protocol.md)  
适用范围：`FinanceH2Domain`、`FinanceRootState` 和模型驱动金融 Root

## 1. 为什么需要单独定义

当前 Root 已经可以保存 `FinanceRootState`，也可以通过 `article.list`、
`article.read`、子 Agent 和 `final` 完成调度。但是模型动作协议目前只定义了
`tool_call`、`delegate`、`final` 和 `request_approval`，没有定义模型如何提交：

- 研究计划的目标、范围和当前完成度；
- 每篇文章的发现、读取深度、使用/排除理由；
- Evidence Ledger 的 claim 绑定；
- 证据覆盖、反例、缺口和停止理由；
- 审核目标、审核发现和最终交付状态。

因此，当前 fixture 可以手工填充这些字段，但不能证明真实模型路径已经可靠地产生了这些状态。
这不是通过增加固定文章数量或固定审核链路可以解决的问题，而是缺少一个明确的数据交接协议。

## 2. 拟采用的边界

模型负责研究判断，Runtime 负责事实约束和持久化：

| 内容 | 模型职责 | Runtime 职责 |
| --- | --- | --- |
| `research_plan` | 提出目标、范围、问题维度和下一步 | 校验结构、长度和版本递增，持久化快照 |
| `article_states` | 提出该文章对当前问题的分析深度、相关性和使用建议 | 根据 list/read/task/result 事实校验来源；不得伪造已读状态 |
| `evidence_ledger` | 提出 claim 与 EvidenceRef 的绑定、支持/反驳关系和缺口 | 校验 ref 属于当前 Run、去重、保留历史，不允许跨 Run 引用 |
| `coverage` | 判断哪些问题维度已覆盖、反例和缺口、是否停止 | 校验引用存在；不替模型决定是否已经充分覆盖 |
| `review` | 选择需要审核的结论，记录审核结果和未裁定争议 | 校验审核引用和状态枚举；不因争议自动丢弃候选正文 |
| `delivery` | 提出 `succeeded/partial`、警告和交付说明 | 依据最终正文、有效 EvidenceRef 和状态生成最终 ResultRef |

Runtime 只保证“事实可追溯、引用有权限、状态可恢复”，不替模型完成金融语义判断。

## 3. 建议动作结构

在所有模型动作上增加可选的 `state_update`。它是本次动作对 Root 状态的增量，不是让模型直接覆盖整个状态：

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

建议的增量顶层字段为：

```text
research_plan       计划快照（替换当前计划版本）
article_updates     文章状态补丁（按 article_id 合并）
evidence_additions  新增 EvidenceRef 绑定
coverage            覆盖判断快照
review              审核目标与结果快照
delivery            交付状态建议
```

`state_update` 可以为空。工具读取、子 Agent 返回和最终答案仍然是独立动作；状态更新不能代替
`result.submit`，也不能直接改变 Task/Attempt/Thread 状态。

## 4. 文章集合的合并规则

继续使用三层状态，不把五个集合当作互斥字段：

1. 发现层：`listed`。只有 `article.list/search` 的真实结果才能增加文章。
2. 读取层：`unread -> partial -> complete/failed`。由 Runtime 根据实际工具结果维护；模型只能提出读取意图和解释。
3. 使用层：`undecided -> used/excluded`。最终 `used` 由有效 EvidenceRef 与最终答案审计推导；模型的建议保留为理由，不直接等于最终使用。

`article_updates` 中不存在的文章不能被创建为已读或已使用。模型可以标记 `excluded`，但必须提供理由；
Runtime 保留该理由和原始模型动作，便于复核。

## 5. Evidence Ledger 合并规则

每条新增绑定至少包含：

```json
{
  "claim_id": "claim-1",
  "evidence_ref": "artifact:...",
  "relation": "supports|contradicts|contextualizes",
  "source_kind": "article|external|model_inference",
  "scope": "原文第 12-18 行",
  "certainty": "direct|conditional|inconclusive",
  "note": "该证据只支持条件性判断"
}
```

Runtime 必须校验：

- `evidence_ref` 属于当前 Run 且当前身份可读；
- 同一 `claim_id + evidence_ref + relation` 幂等；
- 不能把作者观点自动升级为外部事实；
- 冲突证据并列保留，不能静默覆盖旧证据；
- 不能把模型推断标记为 `direct`。

## 6. 状态更新失败时的处理

- 结构错误：保留原始候选动作并返回 `contract_error`，不伪造状态；允许模型在同一 Attempt 内做一次格式修复。
- 引用不存在或越权：拒绝对应补丁，保留正文和已验证状态，交付标记 `partial` 并记录 warning。
- 部分字段合法：按字段原子合并，非法字段单独记录，不回滚已经验证的工具事实。
- 模型没有提交 `state_update`：不自动猜测 `coverage/review/used`；继续允许它执行下一步或交付，但状态标记为 `incomplete_metadata`。

## 7. 需要用户确认的两个取舍

### 方案 A：统一 `state_update` 增量协议（推荐）

所有动作都可以带同一个可选 `state_update`，Runtime 做统一校验和合并。优点是协议简单、
状态更新与模型动作同一版本、恢复时容易重放；缺点是需要定义每个补丁字段的 schema。

### 方案 B：按动作拆分专用字段

例如 `article.read` 只允许 `article_update`，`delegate` 只允许 `research_update`，`final` 只允许
`review/delivery`。优点是约束更窄；缺点是动作类型和金融流程绑定更紧，后续扩展新研究动作时需要增加协议。

用户已确认方案 A。实现与当前约束以 026 为准；本文件保留为原始方案讨论记录，不再单独
定义一套可与 026 分叉的契约。

## 8. 验收要求

确认后至少新增以下离线验收：

- 模型提交计划后，恢复下一 Attempt 仍能看到同一计划版本；
- 未执行 `article.list/read` 的文章不能进入已读/已使用；
- 只有最终答案有效引用的文章才推导为 `used`；
- 外部证据、作者观点和模型推断不能互相升级类型；
- 冲突证据并列展示，不能覆盖；
- 非法状态补丁不会丢失完整候选正文；
- 没有状态补丁时可以交付，但明确标记元数据不完整；
- 重复提交同一补丁不会增加状态版本或重复 Ledger 条目。
