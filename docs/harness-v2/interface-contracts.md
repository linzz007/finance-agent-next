# Harness 2.0 调度接口契约

状态：用户已确认并冻结；实现进度另见 [014](../changes/014-harness-v2-foundation.md)，不等同已全部实现。  
版本：`h2.agent.v1`；契约冻结：`H2-CONTRACTS-2026-10-18-v1`；日期：2026-10-18。  
依据：[冻结基线](../changes/013-harness-v2-dispatch-baseline.md)；配套：[SQLite](sqlite-schema.md)、[Redis](redis-protocol.md)。本契约是 2.0 调度层，不替代[既有金融研究接口](../finance_agent_interface_contracts.md)，也不把旧 analyst 的接口版本误称 Harness 2.0。

## 1. 三项已确认的细化

2026-10-18 用户明确回复“好的，我都确认，你继续吧”，确认以下 R1/R2/R3 及后续实现。设计确认不代表代码、迁移或真实验证已经完成。

| 编号 | 基线未展开之处 | 建议细化 | 需要确认的原因 |
| --- | --- | --- | --- |
| R1 | 基线有 Task waiting，但 Attempt 状态没有“保存后释放执行”的状态 | 新增 Attempt `suspended`；wait/等待输入先保存 Checkpoint，释放 lease/执行名额，唤醒后保留 Task、创建新 Attempt | 否则要么空占 Worker，要么把尚未完成的任务错误标为 succeeded；这是对冻结状态枚举的补充 |
| R2 | 基线允许已完成 Thread 接收后续消息，但未定义新工作身份 | 运行中 send 在下一安全边界投递，不抢断；Task 已终态、Thread 未关闭且 Run 仍开放时，send 创建同 Thread 的 follow_up Task，返回新 task_id；不把旧成功任务改回 running | 保留每次工作与结果的历史，解决 Thread 持续身份与 Task 一次目标之间的边界 |
| R3 | 已定义 task_dependencies，但失败依赖未指定处置 | 依赖默认只接受 succeeded；partial/failed/cancelled/timed_out 使依赖方 blocked 并通知父 Agent，由父 Agent 决定采用部分结果/另建修订任务，不静默跳过依赖 | 这是工作推进判断，不能替用户预先批准“失败也当完成” |

三份契约现冻结为 H2-CONTRACTS-2026-10-18-v1，作为 013 的经批准补充；R1 也适用于主 Agent 自己的 durable wait，不要求主 Agent 固定采用某种金融分工。参数与代码映射可细化，语义改变仍须先确认。

## 1.1 C16 兼容性状态

2026-10-29 用户批准 [113](../changes/113-runtime-review-status-presentation.md)：Analyst
最终结果包装新增只读 `review_status: finance.h2.review_status.v1`，位于被核验的
`result/review_targets` 之外，由 Runtime 从持久化 `review` 生成，模型不填写。
字段包括 `status/label/verdict/scope/scope_labels/mode/rounds/max_rounds`、
`draft_ref/draft_revision/report_ref` 和快照/范围说明。`passed` 必须同时有通过判定、
草稿版本和报告引用且无阻塞项；历史 `reviewed_with_resolutions` 不补造通过判定。
父 Agent 完整取得子结果后，将相同投影保存到检查点并在后续上下文的
`child_review_statuses` 中展示；未读全不登记，不替代证据授权或全文读取。
旧结果在读取时兼容投影，不重写原 Artifact，不变更完整结果哈希门控或历史结论。

2026-10-24 用户确认 [102](../changes/102-h2-five-action-corrections.md)：新 Run 的 Root/子/孙
每次决策在首次生成后最多补正五次，成功即停；以下历史“一次补正/最多三次物理请求”不再适用于新规则快照。
所有补正共享最多一次额外传输重发，故每决策至多七次物理请求，受原模型、预算和截止时间约束。
补正只保留原任务上下文与最新一条安全反馈，不累积被拒正文；每次调用/诊断均审计，耗尽不得重启决策。
规则随 Run 冻结，缺少 action_repair_limit 的旧快照继续一次。锚点冻结正文、部分交付边界不变。

2026-10-19 的 C16 离线回归已确认：旧版同步路径与 H2 异步路径能够保留同一篇文章的作者、发布日期、原文、EvidenceRef 和答案语义；`article.list` 不会单独制造 `read_status=complete`。记录见 [024](../changes/024-harness-v2-c16-compatibility.md)。

该回归的状态是 `verified-partial`：H2 当前 `FinanceRootState.review` 仍可能为 `None`。模型输出中的审核字段不能直接充当 Runtime 审核完成证明。统一 `state_update` 增量协议已由用户确认，正式定义和验收范围见 [026](../changes/026-finance-state-update-protocol.md)；它只适用于显式 H2 金融 Root，不替代 1.0 接口或调度生命周期。

2026-10-19 用户另行确认 [030](../changes/030-h2-action-contract-handoff.md)：仅 Root
收到完整公开输出后的格式/动作结构错误，可在同模型、原预算内补正一次，物理调用独立记账。
Adapter 仍为单次请求；额度、权限、传输和无完整输出不进入这次格式补正。以下冻结规则中的
“格式错误交父 Agent、不自动重试”保留为通用底层语义，Root 层仅有这项已确认例外。

R7 完成后，用户另行确认 [037](../changes/037-h2-child-action-envelope-and-budget-errors.md)：
研究子 Agent 在同模型、原申请预算内每次决策也最多补正一次公开动作或 ResearchResult
结构/证据锚点。完整失败公开输出、原调用和补正调用分别保留；无完整输出、传输、额度、
权限或预算失败不触发补正，补正失败不重启整次决策。内层输出 Schema 只描述 `final.output`，
不得把裸 ResearchResult 自动包装为成功结果。该例外不回溯改变冻结 R7 的失败结果。
Runtime 执行预约被 `budget_denied` 拒绝时保留该原因，不误记为 `internal_error`。

R9 后的恢复调整见 [048](../changes/048-h2-correction-transport-retry.md)：只有上述唯一
格式补正的首次发送遇到 `transport_error`，才允许在同 Attempt、同模型和原预算/时限内，
将相同补正消息重发一次。每个决策最多三个独立记账的物理请求；不是再补正一次，也不重发
原始决策。第三次任意失败都终止本次决策；额度、权限、取消、超时和非传输错误不适用。
原调用结果未知时仍保留计费。该新边界取代 030/037 在此特定传输分支的旧限制，不改历史结果。

2026-10-20 用户确认 [065](../changes/065-h2-correction-input-isolation-and-json-fence.md)：
Root/研究子 Agent 补正只追加有界错误定位和去正文的局部语法片段，不再整段回灌被拒输出；
完整原始公开输出仍留在审计中。保留原任务、真实观察、Schema、原模型/预算和一次补正边界。
H2 仅可规范化包住单个 JSON 对象的唯一一层 bare/json Markdown 围栏，不接受说明文字、
额外 XML、多个对象或非法参数；不增加调用、不改变原始输出记录，严格校验和执行边界不变。
这两项明确批准取代历史待确认状态，不代表旧批次失败已被复验解决。

[038](../changes/038-h2-model-revocation-boundary.md)细化既有模型撤销与发送记账边界：
`call_started` 事务再次检查原 Attempt 模型仍在当前用户允许列表内。预约后、发送前被撤销，
不得发送请求；确认未发送的预约走原有 `not_sent` 退款路径，执行错误保留 `forbidden`。
已开始的请求不保证撤回或免计费；仅更改默认模型、仍允许原模型时不改写当前 Attempt。

2026-10-19 用户确认 [032](../changes/032-h2-durable-action-continuation.md) 的方案 A：
动作后持久化并重新入队。可信 `Continue(checkpoint)` 和金融 `FinanceStep(continuing)`
表示同一 Task 的下一步，不是完成或失败重试；事务内保存检查点、挂起旧 Attempt、
创建新 Attempt 和 Outbox 后才可 ACK。异步金融动作和子循环的实现状态按 032 分项记录。

## 2. 共同规则

### 2.1 调用方与权限

接口绑定 `CallerContext`，由可信 Runtime 注入：run_id、caller_thread_id、caller_task_id、caller_attempt_id、有效 lease、权限快照和用户身份引用。模型 JSON 不能自行声明身份、worker、租约、最终状态、父预算余额或任意 run_id。

- root 管理当前 run 的后代；Subagent 只管理自己派生的后代和读取明确授予的 input/result refs。不能取消祖先、读兄弟全部上下文或跨 run 访问。
- 持续 Thread 的历史保留不等于跨 run 自动授权；Run 完成后，新的用户 Query 创建新 Run，旧 Thread 不悄悄归属新 Run。
- 深度为 2 禁止继续 spawn；权限不能比父级扩大；模型选择只能来自当前用户授权 allowed_models。预算和 deadline 必须在祖先剩余边界内。
- 只读 status、wait、result.get 不调用模型；权限、身份、引用范围仍检查。Agent 的 wait 作为执行动作可以使自身挂起。
- JSON 动作沿用现有文本协议；工具参数详细 Schema 通过 tool.describe 按需展示，不恢复全量上下文，也不引入 provider-native Function Calling。

```json
{
  "action": "tool.call",
  "tool": "agent.status",
  "arguments": {"task_ids": ["tsk_002"]}
}
```

新调度 `agent.*`、`result.*` 是注册工具名称，不是允许 Subagent 直接操作 SQL/Redis 的后门。旧 `delegate` 先保留兼容；切换由明确的新路径开关控制，不能同时对同一业务意图同步 delegate 和异步 spawn 两次。

新建 H2 Run 采用 [067 独立调用上限](../changes/067-h2-independent-call-limits.md)：
Root 自身 120 次，每个子/孙 Task 自身 60 次，整轮共用 600 次/60 分钟。
模型侧 spawn 与终态 follow_up 不含 requested_budget，可选 wall_time_ms 只申请时长；
未指定时按现有 20 分钟上限与父级剩余研究时间取界。自身调用与整轮消耗分别披露，
不预占子任务的全部调用额度，恢复不清零。下面的显式申请规则只适用于旧 hierarchical Run。

旧 hierarchical H2 研究任务由 Agent 使用 `agent.spawn` 显式申请 `requested_budget`。
旧 `delegate`（含版本化 analyst）在模型选择前披露兼容申请值：analyst 为
4 次调用 / 600000 ms，verifier、skeptic 为 1 次 / 120000 ms。它们不是新任务的
默认推荐额度；不自动扩额、降额或转换调用。主/子提示、按需动作说明及实际兼容
执行使用同一定义，详见 [033](../changes/033-h2-explicit-delegation-budget.md)。

### 2.2 回执、幂等与错误

```json
{
  "schema_version": "h2.agent.v1",
  "request_id": "req_001",
  "ok": true,
  "data": {},
  "error": null,
  "event_cursor": "204",
  "replayed": false
}
```

失败时 `ok=false,data=null,error={code,message,retryable,details}`。message/details 脱敏；`retryable` 只表示调用方可按原 key 重试，不授权模型 fallback、额外预算或重复外部写入。游标为十进制字符串，避免 JSON/JavaScript 精度丢失；事件序号和 Redis entry ID 不混用。

写命令必带 idempotency_key，spawn 使用 dispatch_key 作为它的工作幂等键；scope 为 `(run, caller_thread, operation, key)`，Task 另有 `(run, dispatch_key)` 唯一约束。规范 JSON 排序/编码后计算 hash。相同 key+相同参数返回持久原回执（replayed=true），可再用 status 取新状态；不同参数返回 idempotency_conflict，不重新消耗预算。

Runtime 在模型动作成功后、观察写回前崩溃，必须保存/恢复原动作 ID 和 key；不得为同一动作随机生成新 key。key 由执行上下文分配并告知模型使用，不把金融业务对象 hash 当成幂等键，避免两次明确重做被误合并。

| 错误 code | 含义/处置 |
| --- | --- |
| invalid_arguments / contract_error | Schema、结果元数据或版本不合法；不给新工作隐式副作用 |
| forbidden / not_found | 越权与不存在对外可合并为 not_found，避免跨 run 信息泄漏；内部审计保留原因 |
| idempotency_conflict | 同 key 不同请求；先检查原回执，不自动换 key 重跑 |
| depth_exceeded / thread_busy / invalid_state / state_conflict | 递归、同 Thread 串行、生命周期或准备后状态/CAS 版本冲突；state_conflict 可读新状态后按原 key 重试 |
| budget_denied / budget_reduced / deadline_exceeded | 明确披露额度；预算缩减只有调用方显式接受后再提交工作 |
| checkpoint_invalid / artifact_unavailable | 快照无效、版本/引用丢失；不伪造恢复成功 |
| stale_lease / cancelled | 当前执行已失去写资格；停止新增模型/工具动作 |
| dispatch_failed / backpressured | 消息分发失败/拥堵；已接收工作的句柄仍可查询 |
| transport_error / provider_error / quota_error / timeout | 保留原模型错误分类；不换模型 |
| outcome_unknown | 外部动作可能发生但未获确认；不能声称未执行或自动安全重做 |

### 2.3 参数通则与后端入口

示例中省略的可选字段不代表必填字段可以由模型任意省略：所有写操作要求稳定 key；spawn 必填 role/role_version、objective、success_criteria、input_refs、output_contract 和 dispatch_key。现行 fixed Run 禁止模型指定 requested_budget，可选 wall_time_ms；历史 hierarchical Run 仍必须提供 requested_budget。priority 默认 0、depends_on_task_ids 默认空、parent_context_refs 默认空、model_profile 默认继承。身份、状态、租约和生成时间只能由 Runtime 填充。空目标、重复目标、未知字段和错误 JSON 类型均拒绝，不能静默丢弃后继续执行。

wait 的 mode 默认 all、after_event_id 默认 "0"、timeout_ms 默认 30000；status/wait 的 max_events 默认 100、上限 200；timeout_ms 范围 0..60000，实际不得超调用方 Task/Run 剩余时限。大事件集分页，不截断伪装完整。result.get 的 view 默认 summary、offset 默认 0；字符分页初值 limit=16000、上限 64000，返回 next_offset。调整这些传输参数不得缩减研究任务预算。

HTTP 映射已作为显式注入的新 namespace 实现，不覆盖现有金融 API。FastAPI 路由和模型工具共享同一个应用服务，不能各自实现一份状态机。认证后的 session/run 权限由服务校验，不信任路径中的 run_id。默认 `create_app()` 不注册 H2 路由，只有显式注入 `FinanceHarnessV2Service` 才启用。

创建 Run 使用 `POST /api/harness/v2/runs`，请求体必须显式提供 `run_id`、`session_ref`、`query`、`selected_profile`、`allowed_profiles` 和 `tools`，成功返回 202。后续请求通过 `X-Harness-Session` 绑定同一 session；`FinanceHarnessV2Service.handle_for()` 会从 `h2_tasks.current_attempt_id` 读取当前 Attempt。

| 方法与路径（前缀 `/api/harness/v2/runs/{run_id}`） | 对应服务 |
| --- | --- |
| POST `/agents` | agent.spawn |
| POST `/wait` | 只读长轮询 wait；不暂停研究 Task |
| POST `/status` | agent.status |
| POST `/threads/{thread_id}/messages` | agent.send |
| POST `/tasks/{task_id}/cancel` | agent.cancel |
| POST `/tasks/{task_id}/resume` | agent.resume；只有可信用户入口可显式传 model_profile |
| GET `/results/{result_ref}` | result.get，view/section/offset/limit 为查询参数 |
| PUT `/model-policy` | 仅可信用户更新允许模型/默认模型，详见下文 |

result.submit 走内部执行上下文，不提供可绕过 Worker lease 的公共 HTTP 发布接口。HTTP 成功创建/接收为 202，已完成只读为 200；参数错误 422、幂等/状态冲突 409、权限隐藏为 404、暂时基础设施不可用为 503。已持久接收但 Redis 不可用仍为 202，不返回让调用方误以为需要创建第二次工作的 503。

可信 `run.model_policy.set` 参数：`idempotency_key, expected_policy_ref, selected_profile, allowed_profiles, auto_fallback=false`。校验 profile/用户权限后创建新不可变策略快照，CAS 更新 Run 当前策略，旧 Attempt/快照不变。新子任务/新 Attempt 受最新用户允许列表约束，同时不能超其父权限快照；撤销的模型在下一调用边界停止新增请求，已发送请求不承诺免计费。主 Agent 无权扩大 allowed_profiles。政策变更不自动重启失败任务；用户需另行显式 resume。此入口只定义后端契约，本轮不实现前端。

## 3. agent.spawn

用途：派出一个独立子 Thread 和初始 Task，SQLite 接收后立即返回，不等 Redis/子 Agent。

```json
{
  "dispatch_key": "author-a-reading-1",
  "role": "analyst",
  "role_version": "2.0.0",
  "objective": "围绕当前比较问题，解释文章 A 的核心推理、条件与缺口",
  "success_criteria": ["观点有原文依据", "保留条件与不确定性"],
  "input_refs": ["artifact_article_a"],
  "parent_context_refs": ["artifact_parent_brief"],
  "output_contract": {"name": "finance.article_understanding", "version": "2.0.0"},
  "depends_on_task_ids": [],
  "priority": 0,
  "wall_time_ms": 1200000,
  "model_profile": "gpt-responses"
}
```

上述是现行 fixed Run 的示例：子任务自身上限由 Runtime 固定为 60，不在派发时预占 60；主 Agent 只可申请时长。历史 hierarchical Run 将 wall_time_ms 替换为 requested_budget={model_calls,wall_time_ms}，保留原申请分配机制。model_profile 可省略，继承父策略；传 profile ID 而不是混用供应商 model_id。允许使用的工具、权限、Prompt 和 deadline 由 Runtime 生成不可变 TaskBrief，模型不能直接提供权限快照或密钥。

成功 `data`：

```json
{
  "accepted": true,
  "thread_id": "thr_002",
  "task_id": "tsk_002",
  "attempt_id": "att_002_1",
  "status": "pending_dispatch",
  "allocated_budget": {"model_calls": 60, "wall_time_ms": 1200000},
  "deadline_at": 1792286400000,
  "dispatch_state": "pending",
  "result_ref": null
}
```

接收事务见 T01。相同 dispatch_key 只能返回已有 Task；请求 hash 不同必须冲突。依赖图和引用校验在提交前完成，失败不能残留孤儿 Thread、空预算账户或消息。

TaskBrief 至少含：目标、成功标准、输入引用/版本、必要父结论、role/version、允许工具及契约版本、输出契约、模型策略、预算、deadline、run/thread/task/parent/depth、Trace 关联。只携带与子任务有关的父材料；不复制父完整会话、全部兄弟结果和私有思考。

每轮还包含可信 `research_clock={today: YYYY-MM-DD, timezone: Asia/Shanghai}`。Python `create_run` 可显式传入该可选对象用于冻结验收；未传时按 Run 创建时刻确定并写入不可变 Root Brief。主、子、孙及同 Run 的 follow-up 每次从原 Root Brief 刷新，恢复和跨日不重置。历史 Run 无该字段时按原 `created_at` 推导，不修改旧数据或历史幂等 hash。来源、用户问题和检查点不能覆盖可信研究日；明确的历史日期仍可限定查询区间。HTTP 请求形状不变，不给模型新增改时钟工具。字段验证、传播与验收见 [093](../changes/093-h2-run-research-clock.md)。

显式 H2 金融路径以紧凑的 `task_identity` 将当前 Runtime Brief 中的运行、Thread、Task、Attempt、父子身份及 role/version/depth 传给主/子模型。每次执行从可信 Brief 刷新，不复用旧检查点的 Attempt 身份；该只读说明不改变 CallerContext 或授予新的权限。字段白名单和验证边界见 [054](../changes/054-h2-trusted-task-identity-context.md)。

授权的父材料既可为结构化 JSON，也可为 UTF-8 纯文本；文本可见不等于原文证据或额外文章授权。既有输入容量、按需展开、引用权限及结果来源绑定不变，确定性交接修复与验证见 [057](../changes/057-h2-text-parent-context-handoff.md)。这不放宽模型动作或恢复状态的 JSON 契约。

金融子任务初始化以可信 TaskBrief 中已接收的材料清单为准；兼容请求包装中的内嵌 `input_refs` 不得替换这份清单、丢弃单独授权的父上下文或增加未授权引用。识别兼容包装后仅将该控制包装从来源材料中移除，任务说明与终态追问语义不变；实现修复及边界验证见 [058](../changes/058-h2-legacy-envelope-input-grants.md)。

## 4. agent.wait 与 agent.status

### 4.1 wait 参数和返回

```json
{
  "task_ids": ["tsk_002", "tsk_003"],
  "mode": "all",
  "after_event_id": "204",
  "timeout_ms": 30000,
  "max_events": 100
}
```

mode：any 为任意目标终态；all 为全部目标终态；specific 必须只指定一个目标。终态定义见 SQLite 文档；failed/cancelled/timed_out 也会满足“结束”，不等于 succeeded。blocked/waiting 不自动算完成。

```json
{
  "wait_id": "wait_001",
  "reason": "condition_met",
  "condition_met": true,
  "tasks": [
    {"task_id": "tsk_002", "status": "succeeded", "result_ref": "res_002_v1"},
    {"task_id": "tsk_003", "status": "partial", "result_ref": "res_003_v1"}
  ],
  "events": [],
  "next_event_id": "211",
  "has_more": false
}
```

reason 枚举 condition_met/timeout/cancelled/run_finished。timeout 只结束本次等待，不取消目标；整轮 deadline/用户取消另外走状态转换。返回所有请求目标的快照，不只返回成功任务，避免父 Agent 忘记失败的一位作者。

wait_all 只是 mode=all 的便捷别名，不另建状态模型。

### 4.2 durable wait 与防漏事件

Agent 执行动作未满足时：T10 先登记目标与 Checkpoint，事务内最后一次检查状态，提交 waiting/suspended；完成事件、定时器和 DB 扫描都可 T11 唤醒。原 Attempt 结束，新的 Attempt 接续同 Task，并将原 wait 的结构化观察放回上下文。重复唤醒只生成一次新 Attempt。

任务 wait 必须拒绝自己、互相等待及 wait/dependency 联合图中的间接环。内部“等待补充输入”使用 wait_kind=mailbox，不与公开 tasks 类型 wait 的空目标混用；send 只唤醒匹配的 Mailbox 等待。它不把模型的不确定性自动变成必须等待用户的问题。

客户端只读长轮询可断开重连；它不挂起研究 Task。HTTP 连接、Redis 通知或进程内 Future 都不是 wait 唯一状态来源。

读取状态和事件采用一致性读快照。返回的 next_event_id 只推进到**本次已扫描范围**；分页不能直接跳到数据库全局最新 ID。页内只返回有权查看的事件，has_more=true 时按 cursor 继续；事件页满不能导致略过未读结果。wait 条件由持久目标状态独立计算，不依赖该页恰好含终态事件。

Thread 内序号保证单 Thread 顺序，全局 event_id 用于恢复同 run 事件。Event 产生与相关状态更新在同一事务；恢复不根据日志时间排序。

### 4.3 status

参数：`{task_ids?:[], thread_ids?:[], after_event_id?:"0", max_events?:100}`；至少一个目标，默认不遍历不授权的整棵树。返回任务/Attempt/子任务摘要、cancel_requested、deadline、模型 profile 快照标识、result_ref、Outbox/blocked 原因、预算 requested/allocated/consumed/local_available/delegated、最新游标及可分页事件。不查询模型，不以 queued 伪装 blocked，不暴露 lease token/凭证。

金融上下文映射保留 wait 目标已有的 status、attempts、budget、deadline_at、cancel_requested 和 dispatch 字段；有无 Result 均不丢失这些 Runtime 事实，见 [045](../changes/045-h2-child-context-contract-parity.md)。终态子任务未用余额会返还父任务，不能只凭 remaining=0 推定预算耗尽。

主 Agent 自身等待也不占有效执行并发名额，否则“根 + 多子任务”的有限并发可能永远等不到空位。

## 5. agent.send

```json
{
  "thread_id": "thr_002",
  "idempotency_key": "followup-001",
  "content": "请保留作者的条件假设，并补充最重要的反例。"
}
```

`content` 或 `content_ref` 恰好一个。Runtime 将内联 content 持久化为 run 内 Artifact，分配 Message sequence；kind 由 Runtime 固定为 follow_up，模型不能伪造 system 消息。

R2 的终态追问要求显式 `follow_up` 工作契约。现行 fixed Run 使用 `follow_up={output_contract,success_criteria,wall_time_ms?}`，禁止 requested_budget，新 Task 按角色获得自身上限，仍共用原 Run 的剩余额度和 deadline，不预占 60。历史 hierarchical Run 仍使用 `follow_up={requested_budget,output_contract,success_criteria}`。若 Task 正在运行则不能同时传 follow_up 创建第二份工作。终态追问预算不足时整条命令拒绝，不先留下一个永远不会处理的 queued Message。

成功返回 `{accepted:true,message_id,sequence,status:"queued",task_id,created_follow_up_task:false}`。这只表示入 Mailbox，不表示模型已经读取。

- 正在运行：下一模型/工具安全边界按 sequence 取消息，不抢断供应商调用；同一 Thread 不开启第二个有效 Task。
- 正在等待输入：消息和唤醒在同一事务；等待子任务时保留原 wait，由原条件或超时唤醒，消息在下一边界交付，不提前篡改等待目标。
- 当前 Task 已终态、Run 开放、Thread 未关闭：按 R2 创建 follow_up Task 和初始 Attempt/Outbox，成功回执含新 task_id，created_follow_up_task=true；目标来自新消息，旧结果只作为授权引用。预算必须明确可用，不重置根余额。
- Run 已终态/Thread closed/取消尚未收敛：拒绝，不能暗中复活已取消工作。

delivered 表示组装进某次执行输入；consumed 与该输入/Checkpoint 同事务确认。崩溃后可以重放未确认消费的消息；保留 delivered_attempt_id，不能仅因发过 HTTP 就宣称模型永久记住。

终态追问的 Runtime TaskBrief 将已保存的 `follow_up.success_criteria` 映射到顶层
`success_criteria`，不修改历史 brief。金融新 Task 从实际追问 Message 初始化目标，
不重新执行继承材料中的旧 `research_task`。`finance.root_state.v1.execution.follow_up_context`
保存 `previous_task_id`、可空的 `previous_result_ref` 和新 `success_criteria`，随 Checkpoint
恢复并提供给主/子 Agent。旧结果仅提供授权句柄，按需 `result.get`；没有读取时不能算
已读证据，不能因追问自动扩大输入范围或预算。实现修复与离线证据见
[047](../changes/047-h2-follow-up-context-handoff.md)。

## 6. agent.cancel 与 agent.resume

### 6.1 cancel

参数 `{task_id,idempotency_key,reason?}`；级联后代是固定基线语义，不给模型传 cascade=false 绕过。

回执 `{accepted:true,cancel_requested:true,task_id,affected_task_ids,settled:false}`；already_terminal 情况返回真实状态、replayed/无操作信息，不改旧结果。

取消事务立刻撤销未完成工作的新增动作/成功发布资格。queued、blocked、waiting 可直接 cancelled；running 标记请求并在边界保存必要产物、终止 Attempt。有效 lease 不续为可工作状态。失联 Worker 由 Reconciler 收敛 cancelled；迟到输出仅审计。不能因为中转站不能撤回请求而谎称取消失败，也不能承诺其不继续计费。

SQLite 与供应商请求不是一个事务：取消提交前已获许可/已预约的调用，存在尚未发送就遇到取消的竞态。Worker 发送前再检查并尽力中断，审计保留许可与取消顺序，但不能保证远端绝对不会执行。可严格验收的是“取消提交后不再批准新的调用”和“取消先提交则不接受成功结果”，不是虚构外部计费也能原子回滚。

### 6.2 resume

```json
{
  "task_id": "tsk_002",
  "checkpoint_ref": "ckpt_002_v3",
  "idempotency_key": "resume-001"
}
```

checkpoint_ref 可省略：Runtime 按版本选择最近**有效** Checkpoint，检查 schema/hash/文件、来源 Task、上下文/消息游标、完成工具回执、预算、权限版本。不是“最新文件存在”就有效。

成功 `{accepted:true,task_id,thread_id,new_attempt_id,resumed_from,status:"pending_dispatch"}`。旧 Attempt 和结果版本保留；新 Attempt 从已确认的执行位置继续，不重做已完成调用。

现行 fixed Run 恢复不重置自身已用计数，也不预占剩余的全部额度；检查自身与整轮余额，并保留 Root 收尾预留，逐次物理调用按实际扣减。历史 hierarchical Run 若旧任务结束时已把未用预算归还父账户，恢复需从该父账户重新预留，并明确返回 allocated_budget；不重复计算已消耗部分，不增加根 grant，不延长原 deadline。无法重新提供请求额度则拒绝或显式提出 budget_reduced 等待调用方接受，不静默降额。

failed/partial/timed_out 的显式恢复必须仍在 Run 剩余 deadline/预算内；没有有效 Checkpoint 返回 checkpoint_invalid，不自动重头开始。cancelled、succeeded、Run 终态拒绝普通 resume。等待条件未满足时不能强制 resume 越过依赖。

恢复默认同模型。**模型失败后 Runtime、主 Agent 均不自行换另一个模型重试。** 用户可以通过可信运行管理接口明确指定 allowed_models 内的其他 profile 创建新 Attempt；新模型快照记录授权来源、Prompt/工具版本，既有 Attempt 不热改。主 Agent 初次为独立子任务选择不同已授权模型是分工，不等于失败后的自动 fallback。

## 7. result.submit 与 result.get

### 7.1 提交与保留

result.submit 由持有有效执行上下文的 Agent 调用；Runtime 生成 result_ref、版本和来源，不信任模型自报 attempt/thread/lease。

```json
{
  "result_key": "answer-001",
  "status": "succeeded",
  "summary": "作者的判断依赖两个条件，其中一个缺少外部支持。",
  "key_points": ["条件与结论的对应关系已保留"],
  "full_ref": "artifact_understanding_002",
  "sections": {"argument": "artifact_argument_002"},
  "evidence_refs": [],
  "gaps": ["某个事实尚未完成外部核查"],
  "warnings": []
}
```

字段允许自由正文 `content` 与 full_ref 二选一；content 首先写 Artifact。基线简例中的 result_ref 表示已保存产物的引用，本契约将它明确拆为输入 full_ref 和 Runtime 输出 result_ref，避免自引用。证据是否充分沿用金融研究契约；调度层不把 succeeded 等同金融结论已验证。

summary_ref 指向完整摘要 envelope，内含 summary、key_points、gaps、warnings 和证据引用，不只存一行摘要。result_key 是提交幂等键；补交使用同一候选的独立 metadata command key，不能以“更改原 hash”的方式覆盖初稿。

处理顺序：持久保存完整候选 -> 检查元数据/引用/权限 -> 正常 T05 发布；缺元数据时保留候选并返回一次补交机会，补交必须指向该 candidate_ref，不重新生成正文。仍不完整或无法补交则保留全文，以 partial + incomplete_metadata 可见地交付，不能从基线退回“丢弃答案”。

伪造/越权 EvidenceRef 不接纳为证据；保留合法正文并标记无效绑定，不以模型编造引用填补缺口。真正失效 lease 或取消后提交返回 stale_lease/cancelled，正文只进入 late 审计，不能借“保留分析”绕过发布资格。

回执包含 `{result_ref,result_version,task_status,metadata_status,warnings}`；相同 attempt/result_key + hash 返回原版本，不增加版本；不同正文相同 key 冲突。修订创建新 key/版本，旧正文不覆盖。result.submit 是子任务结果，不是 root 的金融 final/审核替代品。

### 7.2 渐进式读取

参数 `{result_ref,view:"summary"|"full"|"section",section?:string,offset?:0,limit?:number}`。固定到具体版本，不接受“latest”在分页中悄悄替换来源。

返回 `{result_ref,result_version,status,metadata_status,content,byte_length,sha256,next_offset,has_more,evidence_refs,warnings}`；section 模式必带已存在的 section key，拒绝任意文件路径。

返回及接受结果的通知还可携带 `section_refs`，只暴露调用方获准读取的 section 句柄，不自动展开正文。仅获授兄弟结果的 full artifact 不授予其其他 sections。

- 摘要/状态/关键观点/EvidenceRef/缺口/result_ref 在父 Agent 消费完成通知时始终交付；“写进事件”不等于“已经进入模型输入”，必须有父执行观察/Checkpoint 的交付记录。
- 短结果自动附全文；长结果按需 get。初始短结果阈值 16 KiB UTF-8 **且**不超父实际剩余上下文输入预算；不够则保留摘要+引用并明确未全文注入，不截断伪装完整。阈值是可调参数，不证明最佳值。
- [040](../changes/040-h2-on-demand-result-expansion.md)修复金融桥接的隐藏回读：通知未附全文时只保留摘要、引用与 `full_read=false`，不自动 `result.get`；由主 Agent 显式选择展开，已附短全文仍按原机制交接。
- 大结果 full 模式可连续分页；offset/next_offset 使用 Unicode 字符偏移，byte_length 是整个产物 UTF-8 大小，两者单位明确不同。摘要/全文 hash 对整个对应产物，页携带相同版本；不做页间重新摘要。
- Runtime 记录 result.summary_viewed/section_viewed/full_viewed，包含父 Attempt、版本、区间；只取一页不能写 full_viewed，应记 result.page_viewed，最后连续覆盖完整区间才可记 full_viewed。
- [039](../changes/039-h2-result-read-coverage.md)修复逐动作换 Attempt 时的累计：同一 Task 的连续页可跨 Attempt 累计，页事件保留真实 Attempt，完成事件归于补齐覆盖的 Attempt；不同 Task、Agent/用户读取、结果版本及 view/section 不混合。旧 Agent 页可从真实 Attempt 还原 Task，不将用户导出推定为 Agent 已读。
- “viewed”只证明返回/组装观察，不证明模型理解，更不强制父 Agent 把所有已读文章写进最终答案。

## 8. 内部接口：Repository、Dispatcher、Worker、Adapter

这些内部接口不直接注册给模型。Repository、Dispatcher、脚本 Worker 已有独立实现；[015](../changes/015-harness-v2-model-execution.md)又接通单次异步 ModelAdapter、物理请求账本和 Worker 可信能力入口。显式金融调度组合入口见 [017](../changes/017-harness-v2-finance-loop-integration.md)，可选 HTTP 边界见 [018](../changes/018-harness-v2-http-api.md)；默认金融入口和生产 Worker 仍未切换。后续显式语料路径的接入见 [032](../changes/032-h2-durable-action-continuation.md)，真实模型定向结果见 [R6](../acceptance/harness-v2-live-acceptance-r6-20261019.md)，不代表全量业务、真实 Redis 或生产验收。逐项底座边界见 [014](../changes/014-harness-v2-foundation.md)，以下仍是完整目标契约，不以已有部分代码替代剩余设计。

| 接口 | 输入 -> 输出 | 契约 |
| --- | --- | --- |
| Repository.accept_command | CallerContext + validated command -> durable receipt | T01/T06/T07/T09，幂等、授权、事务状态/Event 同步提交 |
| Repository.claim | Outbox identity + worker -> LeaseHandle 或 unavailable | T02 唯一 claim，检查 run/thread/worker 三层并发；等待释放名额 |
| Repository.heartbeat | LeaseHandle -> renewed/lost | T03；不延长祖先 deadline，不复活 expired |
| Repository.suspend | LeaseHandle + Checkpoint + wait spec -> wait_id | T10；保存与释放原子化，R1 已确认 |
| Repository.commit_result | LeaseHandle + Artifact refs + submission -> receipt | T05 fencing，终态/版本/Event 原子发布 |
| Repository.reconcile | bounded scan cursor -> repaired IDs/events | DB 为准，过期、取消、等待、Outbox 漏通知全部可发现 |
| Dispatcher.publish_batch | claimed Outbox batch -> published/retrying/dead_letter | XADD 不在 DB 写事务中；publisher_token fencing；无模型调用 |
| Worker.execute | LeaseHandle + TaskBrief + Checkpoint? -> result/suspension/error | 加载隔离上下文、边界查 Mailbox/cancel、调用 Adapter/工具；不自行改 SQL 状态 |
| ArtifactStore.put/get | run-scoped bytes/ref -> immutable ref/verified bytes | 原子文件 + hash；不泄漏凭证，拒绝外部路径 |
| ModelAdapter.request | ModelRequest -> AsyncIterator ModelEvent | 不含调度层 SQL/Redis；单次物理请求，不隐藏换模型或无限重试 |
| ModelAdapter.cancel | call_id -> abort_requested/unsupported/already_finished | best effort；不保证供应方撤回/免计费 |

ModelRequest：call_id、不可变模型快照、messages、输出动作契约、context/output 限制、timeout 配置、流式开关、可信凭证解析引用（不序列化真实密钥）。每次发送前 T04 预约一次物理请求，重连/重试也计次数。

[041](../changes/041-h2-confirmed-unsent-settlement.md)修复取消或租约失效后的确认未发送记账：
原执行者可凭完整原身份和 lease token 结算未发送预约，不恢复执行权限；仅当 `started_at` 为空且预约仍为 reserved/unknown 时幂等退款。
退款回原调用预算，已终止子任务的余额沿既有父级链归还；已开始或在途未知的请求不退款，失联本身也不触发自动退款。

ModelEvent：started / text_delta / action_candidate / usage / completed / failed；含 call_id 和调用内递增 seq。未完成 JSON 的流式片段只展示/保存，不执行工具；最终候选经现有 JSON 契约验证才能动作。公开分析输出和模型私有推理通道分离，不把隐藏思考复制为 TaskBrief 或父结果。

实际 Schema 校验失败时，failed 事件可携带 `validation_stage` 和有界的
`validation_errors`（仅 loc/type/msg，最多 12 项；排除 input/context/URL 并脱敏）。
可信执行能力只转交这些白名单字段及真实 call_id/output_ref，供已批准的一次同模型补正
定位错误；不因此接受非法动作或增加额度。金融输入中的 `next_action_key` 是只读上下文值，
仅用于工具 Schema 明确声明的 arguments.dispatch_key/idempotency_key，不是动作顶层或
state_update 字段。实现缺口及修复见 [044](../changes/044-h2-action-validation-feedback.md)。

归一化 ModelError：transport_error/provider_error/contract_error/quota_error/timeout/cancelled，携带 retryable、provider_request_id、脱敏细节、usage/计费是否未知。只对明确允许的传输故障做有界同模型重试；常规任务级重试建立新 Attempt，旧记录不覆盖。048 的补正传输重发是同 Attempt 内的明确例外，仍由单次请求能力逐次检查权限和记账。格式/权限/额度/逻辑失败交父 Agent；结果元数据补交是基线允许的一次契约补交，不是假称网络重试。

失败诊断经 Worker 到 SQLite 持久化，不只传错误类别：Attempt 的 `error_message`
为固定脱敏说明，`task.failed` 保存真实 `attempt_id` 和白名单校验诊断。
`agent.status` / `agent.wait` 的失败 Task 可含 `failure_diagnostic`，独立于事件翻页游标，
且只匹配当前 Attempt；恢复后的新 Attempt 不继承旧失败。金融上下文保留该诊断，
不把失败输出转换为有效 ResultRef 或证据。字段边界、兼容与验证见
[070](../changes/070-h2-terminal-failure-diagnostics.md)。

五层 timeout：连接、读取 idle、单次调用总时长、Attempt 截止、Task/Run deadline，实际执行取更早边界；stream 有数据也不能无限拖过总时长。参数初值引用现有运行配置并进入快照，不在本文重新调小研究预算。

取消通知和模型流可以进运行记录，但语义 Event 必须在 SQLite 中持久化。不能只靠 WebSocket/SSE 说明执行已完成。

### 8.1 与 1.0 接口的接入映射

| 当前对象/行为 | 2.0 接法 | 保持不变 |
| --- | --- | --- |
| TaskBrief / ResearchTask | 在调度 Brief envelope 中引用现有任务描述，新增身份、deadline、权限/模型/预算快照 | 阅读任务目标与金融分析方法 |
| delegate_fn -> SubagentResult | 新路径改为 spawn + wait + ResultRef；遗留路径暂保留 | 旧委派调用方不被静默改签名 |
| SubagentResult public output / EvidenceLedger | 完整输出存不可变产物，导入原来源、日期、版本和 EvidenceRef | 不凭摘要生成新证据、不跨 run 偷读旧证据 |
| context_manifest / parent_span_id / span_id | 进入新 TaskBrief、Checkpoint 和执行 Trace 关联 | 不捏造旧 parent_run_id 的可验证事实 |
| 当前 ModelAdapter.complete | 新 transport adapter 包装现有 gateway，收敛隐藏重试配置并暴露实际请求数 | 不改模型 profile、协议或默认收费选择 |
| 最终答案/审核/候选保留 | root 仍走原金融交付策略；结果失败/partial 作为真实观察交给主 Agent | 调度 succeeded 不替代金融验证，不丢失败时完整正文 |

既有来源交接的实现修复见 [062](../changes/062-h2-delegated-source-lineage.md)：金融研究结果的 `finance_sources` section 保存按已接纳 EvidenceRef 绑定的实际文章读取元数据（作者、日期、版本、行范围、原读取 Task）。父任务合并这些事实，不复制子任务的 coverage/review/use_status；是否 used 仍由父任务终稿的实际证据决定。来源元数据返回不等于父任务已完整读取或理解子任务的分析正文，也不会扩大工具或文章授权。

[074](../changes/074-h2-delegated-reference-views.md)落实可用引用与来源身份的边界：子结果信封中的
原始 EvidenceRef 仅用于溯源，不因返回而成为父任务已授权的原文材料。父任务绑定收到的全文
wrapper，或完整读取后的 ResultRef；引用分析不冒充直接读过原文。Runtime 的
`delegated_source_refs`/`local_evidence_refs` 是真实观察的恢复索引，不是模型申报权限。
旧检查点从已有观察恢复一次；旧账本的 `source_provenance` 行保留历史，但不得被状态增量
或终稿重新当作可用证据。明确授权和本任务实际读取的相同引用仍可用；原权限和全文分页校验不变。

根 Agent 也须有真实 h2 Thread/Task/Attempt，不能永远留在同步 Python 栈上却声称父 Agent wait 可恢复。Queue/数据库层完成后再接业务入口，保留 1.0 feature flag 和回退测试；此处的回退指代码路径，不是自动切付费模型。

### 8.2 FinanceRootExecutor

`FinanceRootExecutor` is the opt-in bridge defined in [016](../changes/016-harness-v2-finance-root-executor.md).
It receives one root `WorkInput` and lease-bound capabilities, then invokes a
domain-owned finance step exactly once. The step returns either:

```json
{
  "status": "waiting",
  "state": {"schema_version": "finance.root_state.v1", "phase": "research"},
  "wait": {"task_ids": ["tsk_child"], "mode": "all"}
}
```

or a terminal `FinanceStep` containing a normal `Submission`. The bridge stores
the state as an immutable context artifact. A wait stores a `CheckpointState`
whose `context_ref` points to that artifact; a terminal submission exposes the
same artifact as the reserved `finance_state` result section. The bridge does
not contain article, evidence, review, or prompt policy and does not switch
the default 1.0 entry point.

`FinanceRootState` is `finance.root_state.v1` and carries the original query,
phase, completed/pending step IDs, research plan, per-article state, evidence
ledger, coverage, candidate, review, delivery, revision, and event cursor.
Large bodies remain artifacts and are referenced rather than copied into this
state. Invalid or missing state fails as `checkpoint_invalid`; the runtime does
not reconstruct it by replaying tools.

The bridge enforces this storage boundary with a 128 KiB state limit and rejects
the nested field names `article_body`, `article_content`, `body`, `full_text`,
`model_output`, `prompt`, `raw_content`, and `transcript`. This is a runtime
storage guard, not a rule that selects articles or decides financial meaning.

`FinanceHarnessV2Service` is the explicit composition entry from [017](../changes/017-harness-v2-finance-loop-integration.md).
It creates a run with an explicit profile and permission snapshot, exposes
bounded publish/worker/recovery methods for acceptance, and requires callers to
provide the finance domain step and child executor. It does not silently wrap
the legacy synchronous AgentLoop as a recoverable implementation.

### 8.3 Finance state updates

The finance model action envelope may contain an optional `state_update` object as
defined by [026](../changes/026-finance-state-update-protocol.md). The update is
an idempotent, compact delta for `FinanceRootState`; it never replaces the full
state and never changes Task/Attempt/Thread lifecycle. Runtime validates plan
revisions, discovered article IDs, current-run EvidenceRefs, duplicate ledger
keys, forbidden body fields, and the `model_inference` certainty boundary.

Observed list/read results remain authoritative for article discovery and read
status. The model may propose relevance, depth, exclusions, coverage, review and
delivery explanations, but it cannot claim an unread article was read or make a
planned article an answer source. Missing metadata remains visible as
`incomplete_metadata`; a malformed terminal patch must not discard the candidate
answer.

### 8.4 Durable action continuation and research children

[032](../changes/032-h2-durable-action-continuation.md) applies the approved
per-action boundary to the actual corpus entry: save the prepared action,
execute one bounded action, persist its observation, then Continue(checkpoint).
The transaction suspends the old Attempt, saves the checkpoint and creates the
next Attempt/Outbox for the same Task. ACK follows commit. This is not a model
retry, a fresh budget or a new Task. Only explicit agent.wait suspends on child
completion; agent.spawn returns its durable receipt and continues immediately.

Research roles at depth one/two use this same loop and optional state_update
delta, but deliver finance.research_result.v1 rather than the Root answer
contract. Role tools intersect the parent permission snapshot; article access is
limited to explicit input-material IDs. Ordinary article tools do not create
research Threads. The existing legacy delegate remains an implicit wait.

Local artifact expansion is a domain tool, separate from accepted Task results:

```json
{"type":"tool_call","tool_name":"material.get","arguments":{"material_ref":"art_known","offset":0,"limit":16000}}
```

Its strict input permits only material_ref (required string, at most 160 chars),
offset (integer >= 0, default 0) and limit (integer 1..64000, default 16000).
The response has tool/material_ref/content/has_more/next_offset. Existing
read_input grants apply; paths, cross-run references and ungranted siblings
remain forbidden. result.get retains its frozen accepted-result contract.
Contiguous full-result pages with a consistent source hash can be reassembled
from recorded observations, without an implicit reread or another model call.

Section 7.1 applies to research results too: preserve the whole valid candidate
before reference checks. Invalid metadata yields partial/incomplete_metadata
and warnings with invalid evidence bindings excluded, not loss of the analysis.
Actual cancellation/stale lease and storage failures keep their original fences.

### 8.5 Immutable research anchor repair

[073](../changes/073-h2-framework-informed-anchor-repair.md) narrows recovery for
an already parsed, otherwise valid research final with non-verbatim anchors.
Its temporary final.output is finance.h2.anchor_patch.v1: anchors contains only
link_index (strict integer >=0) and text (nonblank string). No other patch or
outer action fields are permitted. Indices must be unique and refer only to
original invalid anchors; text must occur in frozen content/candidate_answer.
The Runtime preserves all other candidate fields, including refs and quotes,
then validates the assembled ResearchResult through normal source checks.
Unrepaired bindings are excluded, with full analysis and explicit partial/
incomplete_metadata warnings retained. This is not semantic verification.
The correction limit follows the Run snapshot (102: five for new Runs, one for
older snapshots); one extra identical transport resend is shared per decision.
All corrections and that retransmission remain within the
same model, budget, deadline and physical-call ledger. Raw, patch and assembled
artifacts remain distinct. Other failures retain the 065 isolation contract.

[076](../changes/076-h2-anchor-correction-action-schema.md) gives this correction
a patch-only whole action_json_schema and action_example. The required outer
type=final/output envelope is generated and checked from one strict model;
final_output_json_schema describes only its output. Bare patches are not wrapped
automatically. This changes feedback clarity, not public parsing or retry limits.

### 8.6 Public JSON string syntax normalization

[078](../changes/078-h2-json-string-control-normalization.md) permits standard JSON
escaping of otherwise raw U+0000..U+001F within double-quoted string literals in a
complete H2 agent_action.v1 public response, after the existing outer fence step.
Only a JSON object candidate starting with `{` enters this step; the existing
022 text-tool compatibility path is not normalized or widened.
Decoded values of legal JSON and the raw output artifact remain unchanged.
[087](../changes/087-h2-framework-informed-json-escape-repair.md) additionally
escapes illegal backslashes inside string literals as literal backslashes,
including malformed Unicode escapes. Legal JSON escapes remain byte-for-byte
unchanged. No delimiter completion or financial analysis rewrite is permitted.

Object boundaries follow [083](../changes/083-h2-bounded-action-json-recovery.md):
unfenced output selects the first complete object, optionally after a bounded
XML-tag-only wrapper, and discards all trailing model text. Never skip an invalid
first action to execute a later one. A normalized outer Markdown fence must
contain exactly one complete object with no extra transcript or second object.
Natural-language prefixes, truncated first objects and invalid action fields
remain rejected; all existing source/argument/permission checks still apply.
This makes no extra request. The allowlisted json_string_controls and
json_string_invalid_escapes counts are attached as public_output_normalization to model.call_finished and a successful
ModelResponse.metadata; no provider text or arbitrary metadata is copied.
The counts must be strict integers from 1 through 1,000,000; zero, booleans,
out-of-range values and other types are ignored, and unknown fields are dropped.
[088](../changes/088-h2-action-repair-diagnostic-boundary.md) binds successful
repair counts to the selected object only; discarded-tail repairs never inflate
them. Boundary metadata reports the original discarded character count.

### 8.7 Explicit Public Source Binding

[079](../changes/079-h2-public-source-adapter.md) adds an opt-in public_sources
binding to the corpus service. web.search/web.open reuse the legacy strict input
models and safe HTTP implementation. Binding does not grant permission: both
composition and the Task permission snapshot must allow execution. Defaults stay
offline. Each operation has an isolated legacy evidence ledger; its evidence_id
is not a usable H2 reference. Successful nonempty page text becomes a durable H2
source artifact. Search leads, unavailable results and unsupported formats do not.
Research quote checks apply to that text. Public provenance carries kind, URL,
response hash, retrieval/publication times, line ranges, pagination and original
reader through finance_sources, never through article_states. Delegation retains
the existing provenance-versus-usable-reference boundary and original budgets.
The acceptance runner's --public-sources enables only actually bound capabilities;
market history, private context and fault-injection blockers remain independent.

### 8.8 Persisted URL Secret Scan

[081](../changes/081-public-url-secret-scan-mvp.md) keeps the generic artifact/context
secret scanner, but distinguishes ordinary public URL query parameters from
credential parameters. Pagination/filter keys such as `page` may persist with
public source text; authentication keys, Bearer/API-key patterns, URL userinfo,
credential-shaped fields and control characters remain rejected. This is a
security false-positive repair, not a public-source bypass.

### 8.9 Durable Market Sources

[089](../changes/089-h2-durable-market-adapter.md) adds an explicit optional
market_sources binding and --market-sources acceptance flag. Reuse the legacy
market.catalog/snapshot/metrics/series input Schemas, acquisition and deterministic
calculations. Both binding and Task tool permission are required; defaults stay
offline. A snapshot is stored before success as finance.h2.market_snapshot.v1;
snapshot_id is its immutable H2 Artifact handle, source_snapshot_id the original
provider snapshot identity. metrics/series restore that authorized frozen
Artifact after a worker restart, without refetch or a process-global cache.
Children require explicit snapshot input grants; Root retains existing run-wide,
not cross-run, authority. Nested provenance never adds a grant.

Only nonempty metrics/series reads form public_market_data source observations.
Canonical text/hash include units, period, original session/baseline metadata,
source status, retrieved_at and collected_at. Series retain provider retrieval
time; composite metrics use collection time and retain per-instrument times.
Strip legacy evidence_id values; bind H2 source refs and preserve original readers
through both delegation levels. Catalog/receipts, empty responses and acquisition
failures are not price evidence. Do not invent article states, causal verification,
realtime quotes or complete-period returns from partial data. Quote failures
retain full analysis under existing partial-result semantics. No new retry,
budget, DDL, model policy or default-route change.

### 8.10 Durable Personal Context

[091](../changes/091-h2-durable-personal-context.md) defines optional
personal_sources and --personal-sources. personal.catalog accepts no arguments;
Root captures immutable per-source snapshots, children list only directly
granted sources. Catalogs have sources[{context_id, personal_kind, source_path,
source_hash, file_hash, captured_at, total_chars, optional as_of/approximate}]
and unavailable[{source_path,code}], never bodies or evidence of reading.
Completed catalogs reuse persisted action observations on recovery.

context.open reuses the strict legacy context_id/offset/limit input, with an H2
Artifact handle as context_id. Return exact text, returned_chars/next_offset/
has_more and the frozen identity/version. company_wiki_search reuses query/top_k
and existing lexical ranking over authorized frozen facts, not ambient files.
Only nonempty views/matches become personal_context evidence, with actual reader
and page/source versions in execution.personal_sources. Preserve that lineage
through both delegation depths, never as public_sources or article_states.
Granted raw snapshot inputs remain compact descriptors until context.open.

Personal context is private background; current_holdings.md is authoritative
over historical Wiki holdings. Preserve as_of/approximate and unavailable data;
no invented cost/P&L/risk profile, writeback, automatic refresh, new output gate,
model/budget change or default-route switch. Full candidate preservation and
quote checks follow the existing research-result contract.

### 8.11 Model Input Payload Views

[095](../changes/095-h2-context-payload-deduplication.md) refines 086's input
presentation only. Exact duplicate complete materials retain every handle and
use payload_material_ref to point to the one visible full copy. A successful
business action receipt may point to an identical typed tool-result material
instead of repeating its body. Different source versions/readers/wrappers and
failed or scheduler payloads are not collapsed into that material.

normalized_observations and action_observations are the explicit typed views.
The legacy observations index retains order/status/identity, using payload_view
{view,observation_ref} for duplicated payload/summary/content/description values.
These aliases are not new evidence, offload events or permission grants. If a
canonical body is actually omitted under configured pressure, expand its
authorized material.get handle. Root and research child both fit their final
prompt with the same routine; an unfittable input fails locally before sending.
All persisted observations, artifact bytes, checkpoints and read/evidence
accounting remain unchanged. No new threshold or semantic delivery gate.

### 8.12 Article Author Catalog

[096](../changes/096-h2-author-catalog-parity.md) restores the legacy catalog
capability in H2 article.list. Inputs and articles stay unchanged. Add
authors[{author,article_count,latest_published_at}] and authors_scope.
corpus_all_active is the existing whole-active-corpus SQLite catalog, independent
of list filters/limit, available only to an unrestricted Root. A child with
explicit grants gets returned_authorized_articles: statistics over only its
returned authorized rows, never the global catalog or all granted articles.
A compatible injected corpus without list_authors reports returned_articles,
not invented corpus totals. Empty scoped results yield no authors. Statistics
do not imply article-body reads, evidence support or new grants. Historical
cached results without these additive fields remain valid.

### 8.13 Run Instruction Rules

[101](../changes/101-h2-external-agent-rules.md) externalizes H2 finance role and
instruction assets without changing Spawn, public actions, result Schemas or
permissions. New finance Runs save an immutable instruction_rules Artifact;
Root Brief carries instruction_rules_ref and its artifact SHA-256. The trusted
execution brief resolves the same bundle for every Task depth and resumed Attempt.
These internal fields are not model-controlled Spawn parameters or evidence grants.

Only selected instructions, the compact role catalog and bundle digest/version
enter the financial model input. Do not inject the full asset pack into its user
context or count it as source material. New Run creation replay retains the first
pack; current file edits affect new Runs only. Invalid declared snapshots fail
visibly rather than falling back. Older Runs without the fields use the fixed
pre-101 compatibility pack, explicitly versioned, not a claim to reconstruct every
historical prompt. Existing DDL, Redis messages and access boundaries are unchanged.

## 9. 设计级验收矩阵

| 编号 | 检查内容 | 验收证据 |
| --- | --- | --- |
| H2-C01 | 同 spawn key 同参数重放、不同参数冲突 | Thread/Task/预算只创建一次；原持久回执可重取 |
| H2-C02 | 跨 run/祖先管理/兄弟越权拒绝 | 无正文泄漏、无写入副作用；审计记录拒绝 |
| H2-C03 | 两 Worker claim 与旧 lease 提交 | 一个有效执行；旧 lease 100% 拒绝发布 |
| H2-C04 | Outbox 两个崩溃窗口、Redis 断连/清空 | 最终可投递或可见 blocked；不静默丢失 |
| H2-C05 | 结果 commit 后 ACK 前崩溃 | 重消费不重复模型/结果；原版本不覆盖 |
| H2-C06 | all/any/specific、分页/丢通知/超时 | 所有目标状态真实，游标无漏读；超时不取消目标 |
| H2-C07 | 根 wait 释放 Worker、递归 depth=2 | 并发受限仍有进展；depth=3 拒绝且不预扣预算 |
| H2-C08 | send 顺序、重放、终态追问 | sequence 单调；同消息不重复创建工作；R2 已确认 |
| H2-C09 | 取消子树与结果竞争、失联 Worker | 未完成后代取消、迟到仅审计、取消后不再预约新调用；已授权在途请求单列 |
| H2-C10 | 从有效/缺失/损坏 Checkpoint 恢复 | 只用有效快照；新 Attempt、预算不重置、已完成动作不重放 |
| H2-C11 | 模型错误、额度和用户选择其他模型 | 不自动 fallback；用户授权新 Attempt 可追溯 |
| H2-C12 | partial/元数据补交失败 | 全文可取，metadata 状态诚实，不把失败说成证据争议 |
| H2-C13 | 短结果/长结果分页/section/版本变化 | 关键 envelope 始终交付，完整读取事件不虚报 |
| H2-C14 | 父子额度并发扣费与 Worker 崩溃 | 余额守恒、不透支、不重复 refund，次数与费用限制披露 |
| H2-C15 | JSON 半流式输出、未知工具和越权参数 | 未完成动作不执行，契约错误保留可诊断失败 |
| H2-C16 | 同 Query 的同步/异步金融兼容对照 | 作者/日期/原文/EvidenceRef/审核与最终答案语义不因调度丢失 |
| H2-C17 | 文件落盘与 DB 失败/损坏、恢复日志导出 | 不存在无正文 succeeded；DB Event 可补导，日志无密钥 |
| H2-C18 | 依赖 partial/失败/环、相互 wait 死锁 | 拒绝环路，blocked 可见，父 Agent 决策；不无限挂起 |

验证顺序：临时 DDL/约束 -> Repository 单元与并发 -> 本机 Redis 集成和 kill-point 故障注入 -> ScriptedModelAdapter 端到端 -> 固定预算真实金融对照。真实模型对照需另行确认批次与额度，不因写契约自动启动付费测试。

初始门槛：状态/权限/fencing/预算守恒等安全不变量零违规，固定离线脚本同种子至少重复 20 轮无孤儿有效任务；调度 p50/p95、恢复延迟、物理请求数单独报告，不预先捏造性能 SLA。金融质量仍按既有验收，不把调度成功率当答案准确率或商业化证明。

## 10. 本轮审查与下一步边界

金融角色配置与文章级核验契约见 [103](../changes/103-h2-agent-profile-and-review-loop.md)，
通用 Profile 与可组合专家见 [104](../changes/104-h2-general-agent-and-portable-profiles.md)，
模型侧轻量派发与专业字段分层见 [106](../changes/106-h2-profile-aware-spawn-contract.md)。
新 v4 Run 仍由同一执行循环承载 general 与专家 Profile。模型侧 `agent.spawn` 的核心字段为
`task_name` 和 `message`；`profile` 默认 `general`，授权材料用 `input_refs` 明确传入。
`task_instructions`、`success_criteria`、`output_contract`、上下文引用、调度偏好和模型选择
作为可选字段；非 general 专家须提供 `success_criteria`。Runtime 生成 `dispatch_key`、
解析当前 Profile 版本，并在省略时选取 Profile 默认输出合同，再转换成既有内部 `Spawn`。
SQLite 表结构与内部调度字段不因模型侧精简而改变。

Profile 清单只向 Root 展示用途和输入要求，不展示版本号或全部工具白名单；Root 可按需调用
`tool.describe(name="agent.spawn")` 读取参数和当前 Profile 摘要。`general` 返回
`finance.task_result.v1`，专家使用已注册的专业返回合同。配置不能增加代码未提供的工具或校验器。
Analyst 专属核验设置位于 `profile_options.review_mode` 和 `profile_options.review_options`，
分别控制 `required | auto | off` 与可选范围、说明及更小轮数；历史 v4 省略时按 Analyst Profile 默认处理，
默认及硬上限为五轮。Runtime 展开成持久化 Brief.review_policy；这些字段对其他 Profile 无效。
核验设置不扩大来源权限；版本绑定及逐项处置沿用 103。已启动 v1/v2/v3 Run 仍按自身冻结快照
使用旧派发 Schema，不受 v4 模型侧入口影响。

2026-10-28 的 [111](../changes/111-review-verdict-and-explicit-dispatch-policy.md) 为新 Run 冻结 v5：
每次 Analyst 派发必填 `profile_options.review_mode`，可选 Run 级
`dispatch_constraints.analyst_review_mode` 由可信入口冻结，所有深度不得违背。
正式核验采用 `finance.review_report.v2`，新增必填 `verdict`、`verdict_reason`、
`blocking_finding_ids`。`required` 成功必须由 Verifier 对最终完整研究结果与目标的准确版本
判定 `passed`；存在重大阻塞意见或范围内未完成检查不能通过。改稿/未通过自动继续核验，
最多五轮，触顶 partial。可纠正的领域合同错误以有界失败观察返回，同 Task 最多五次补正，
不扩大原模型、预算和时间。v1-v4 Run 继续使用自身冻结的旧语义。

文档引用、DDL 和示例格式检查只证明契约可检验，不证明运行实现。R1/R2/R3 已确认，后续代码、故障验证与金融接入的实际进度分别记在 014，不回写 draft1 时点的验证结果。

### 10.1 draft1 检查记录（2026-10-18）

- 从 SQLite 文档提取唯一 SQL 块，在 Python sqlite3 `:memory:` 中执行：18 张 h2 表建成，foreign_keys=ON，foreign_key_check 无错误。
- 用两个虚构 Run 做 16 项内存约束检查：根对象延迟外键可提交，跨 Run 预算/Thread 拒绝，跨 Task current Attempt 在 commit 时拒绝，多活跃 Attempt/Task 拒绝，缺租约 claim 拒绝，Event 改删拒绝，非法 JSON/depth/预算拒绝，重复事件/工作键拒绝，旧失败 Attempt 保留后可建立新 Attempt，最后外键检查通过。
- 初次检查脚本误用 savepoint 检查延迟外键，未等 commit 因而报告未拒绝；改为检查完整事务提交后通过。没有通过放宽 DDL 来消除失败。
- 三份新增契约中的 10 个 JSON 示例可解析；同时检查设计入口和金融接口中的示例，共 18 个 JSON 示例可解析、91 个相对文件链接目标存在、代码围栏成对。文件链接检查不等于逐个 Markdown heading 锚点认证。
- 本轮未运行全库业务测试、真实数据库迁移、Redis 集成、并发/kill-point 故障注入或付费模型测试；没有新增真实金融质量成功率。

以上是 draft1 时点的检查记录，不能回写为当时已实现。后续 014 增加了临时文件 SQLite 迁移、FakeRedis/脚本异步执行和故障验证；真实数据库迁移、Redis/Worker 部署、金融异步接入与真实质量复验仍未完成。不能仅因为 Git 中存在 DDL 或脚本测试，就把 1.0 的同步委派描述成 2.0。
