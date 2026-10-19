# Harness 2.0 SQLite 存储契约

状态：契约已确认并冻结；真实数据库尚未迁移，实现进度见 [014](../changes/014-harness-v2-foundation.md)。  
版本：`h2.storage.v1`；契约冻结：`H2-CONTRACTS-2026-10-18-v1`；日期：2026-10-18。  
语义依据：[冻结基线 H2-DISPATCH-2026-10-18-v1](../changes/013-harness-v2-dispatch-baseline.md)。

配套：[Redis 投递协议](redis-protocol.md)、[调度接口与验收](interface-contracts.md)。本文拥有表、约束和事务定义；其他文档只引用，不重复定义。

## 1. 存储边界

- SQLite 是任务、租约、事件、预算、Mailbox 和结果索引的唯一权威。Redis 的存在与否不决定任务是否已接收或完成。
- 新表统一使用 `h2_` 前缀，增量加入新项目自己的金融数据库；不修改旧表，不连接原项目数据库。不额外创建一个需要跨库事务的调度库。
- `h2_runs.session_ref`、`legacy_run_ref` 只桥接现有会话/研究运行标识；不伪造旧表外键。接入前要核对实际会话存储路径；不能用两个数据库各成功一次来声称原子接收。
- Query、完整结果、模型流和 Trace 使用基线指定的 `D:/AAAcode/code-code/agent+/harness/runtime-records/runs/<run_id>/`；数据库只保存必要目标、索引和引用。数据库与此目录都属于私有运行数据，不进 Git。
- ID 由 Runtime 生成，公开为不透明字符串；时间字段统一为 UTC Unix 毫秒整数，API 展示可转换为 ISO 8601。游标不得使用时间戳代替。
- `*_json` 是结构化小对象，不是放文章全文的通道。引用按现有 Artifact/EvidenceRef 解析器检查 run、来源版本和权限，不另造一套金融证据格式。

每个连接显式开启 `foreign_keys=ON`；本地文件系统采用 WAL、`synchronous=FULL` 和有界 busy 重试。WAL 仍只有一个 Writer，网络、模型、文件大写入和 Redis 调用不能放在 SQLite 写事务内。配置初值 `busy_timeout=5000ms`，只影响竞争等待，不改变任务预算。事务重试必须复用命令幂等键。

## 2. 关系与权威字段

```text
h2_runs -> h2_threads -> h2_tasks -> h2_attempts
                  |          |          |
              messages   dependencies   checkpoints / model_calls
                  |          |          |
                  +----------+----- results -> artifacts
                             |
                        events / outbox

budgets + budget_entries: 统一预算账本
snapshots: 权限、模型策略、实际模型配置的不可变快照
commands: 对外写命令幂等回执
waits + wait_targets: 可释放 Worker 的持久等待
```

| 对象 | 额外约束及职责 |
| --- | --- |
| run | 一次 Query；根 Thread/Task 在同一事务内建立；不等同于跨轮 Session |
| thread | 持续 Agent 身份；同一 Thread 最多一个非终态 Task，避免两次模型执行竞争同一 Mailbox |
| task | 逻辑目标；`dispatch_key` 为调用方在当前 run 内提交工作的稳定幂等键 |
| attempt | 一次执行；`attempt_key` 每次重新执行都不同；`task.current_attempt_id` 是发布资格指针 |
| event | 全局递增 `event_id` 和 Thread 内递增 `thread_seq`；不可更新删除；不保存模型私有思考 |
| message | Thread 内 `sequence`；`consumed` 只表示已提交进上下文/Checkpoint，不证明理解正确 |
| result | 所有候选版本保留；Task 的 `result_ref` 只指向已接受发布的版本 |
| checkpoint | 不可变执行快照，不是摘要；含消息消费位置、完成动作和恢复上下文引用 |
| outbox | 对一次 Attempt 的一次投递记录；`published` 不是 Task 完成 |
| command | 写命令的请求 hash 和持久回执；同 key 不同参数必须冲突，不能返回一个不相关旧结果 |

基线有“事务创建 Attempt”和“Worker 创建 Attempt”两种简写。本契约采用：提交工作时预建 `queued` Attempt；Worker claim 将其变为 `leased`，才绑定 Worker、租约和模型快照。不会在 claim 时再创建同编号 Attempt。

## 3. DDL

下列为一个完整 SQL 块，可在临时内存数据库校验，**不得直接对真实数据库执行**。建表顺序中的循环引用使用延迟外键，根对象与当前 Attempt 指针在同一事务中完成。DDL 只负责可表达的约束；状态转换、祖先权限、预算守恒和事件同时写入必须由下节的 Repository 事务保证。

`suspended` 是接口文档 R1 的经用户确认补充；后续迁移保留本 SQL 与可执行 schema 的一致性测试。

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE h2_runs (
  run_id TEXT PRIMARY KEY,
  session_ref TEXT NOT NULL,
  legacy_run_ref TEXT,
  status TEXT NOT NULL CHECK (status IN
    ('running','waiting','succeeded','partial','failed','cancelled','timed_out')),
  root_thread_id TEXT NOT NULL,
  root_task_id TEXT NOT NULL,
  budget_ref TEXT NOT NULL,
  model_policy_ref TEXT NOT NULL,
  query_ref TEXT NOT NULL,
  deadline_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  finished_at INTEGER,
  FOREIGN KEY (run_id,root_thread_id) REFERENCES h2_threads(run_id,thread_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (run_id,root_task_id) REFERENCES h2_tasks(run_id,task_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (run_id,budget_ref) REFERENCES h2_budgets(run_id,budget_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (run_id,model_policy_ref) REFERENCES h2_snapshots(run_id,snapshot_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (run_id,query_ref) REFERENCES h2_artifacts(run_id,artifact_ref)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE h2_artifacts (
  artifact_ref TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
  kind TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256)=64),
  byte_length INTEGER NOT NULL CHECK (byte_length>=0),
  media_type TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  retention_until INTEGER,
  UNIQUE (run_id,artifact_ref)
);

CREATE TABLE h2_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  kind TEXT NOT NULL CHECK (kind IN ('permission','model_policy','model')),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  sha256 TEXT NOT NULL CHECK (length(sha256)=64),
  created_at INTEGER NOT NULL,
  UNIQUE (run_id,snapshot_id)
);

CREATE TABLE h2_budgets (
  budget_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  parent_budget_id TEXT,
  requested_calls INTEGER NOT NULL CHECK (requested_calls>=0),
  allocated_calls INTEGER NOT NULL CHECK (allocated_calls>=0),
  final_reserve_calls INTEGER NOT NULL CHECK
    (final_reserve_calls>=0 AND final_reserve_calls<=allocated_calls),
  allocation_status TEXT NOT NULL CHECK (allocation_status IN
    ('granted','budget_reduced','budget_denied')),
  deadline_at INTEGER NOT NULL,
  final_reserve_ms INTEGER NOT NULL CHECK (final_reserve_ms>=0),
  created_at INTEGER NOT NULL,
  UNIQUE (run_id,budget_id),
  CHECK (parent_budget_id IS NULL OR parent_budget_id<>budget_id),
  FOREIGN KEY (run_id,parent_budget_id) REFERENCES h2_budgets(run_id,budget_id)
);

CREATE TABLE h2_threads (
  thread_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  parent_thread_id TEXT,
  root_thread_id TEXT NOT NULL,
  depth INTEGER NOT NULL CHECK (depth BETWEEN 0 AND 2),
  role TEXT NOT NULL,
  role_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ready','running','waiting','closed','failed')),
  model_policy_ref TEXT NOT NULL,
  permission_snapshot_ref TEXT NOT NULL,
  budget_ref TEXT NOT NULL,
  last_event_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_event_seq>=0),
  last_message_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_message_seq>=0),
  row_version INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (run_id,thread_id),
  CHECK ((depth=0 AND parent_thread_id IS NULL AND root_thread_id=thread_id)
    OR (depth>0 AND parent_thread_id IS NOT NULL AND parent_thread_id<>thread_id)),
  FOREIGN KEY (run_id,parent_thread_id) REFERENCES h2_threads(run_id,thread_id),
  FOREIGN KEY (run_id,root_thread_id) REFERENCES h2_threads(run_id,thread_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (run_id,model_policy_ref) REFERENCES h2_snapshots(run_id,snapshot_id),
  FOREIGN KEY (run_id,permission_snapshot_ref) REFERENCES h2_snapshots(run_id,snapshot_id),
  FOREIGN KEY (run_id,budget_ref) REFERENCES h2_budgets(run_id,budget_id)
);

CREATE TABLE h2_tasks (
  task_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  thread_id TEXT NOT NULL,
  parent_task_id TEXT,
  task_type TEXT NOT NULL,
  objective TEXT NOT NULL CHECK (length(trim(objective))>0),
  input_refs_json TEXT NOT NULL CHECK (json_valid(input_refs_json)),
  output_contract_json TEXT NOT NULL CHECK (json_valid(output_contract_json)),
  brief_ref TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('pending_dispatch','queued','running',
    'waiting','backpressured','blocked','succeeded','partial','failed','cancelled','timed_out')),
  budget_ref TEXT NOT NULL,
  deadline_at INTEGER NOT NULL,
  dispatch_key TEXT NOT NULL,
  current_attempt_id TEXT,
  result_ref TEXT,
  cancel_requested_at INTEGER,
  cancel_reason TEXT,
  row_version INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  finished_at INTEGER,
  UNIQUE (run_id,task_id),
  UNIQUE (run_id,dispatch_key),
  CHECK (parent_task_id IS NULL OR parent_task_id<>task_id),
  FOREIGN KEY (run_id,thread_id) REFERENCES h2_threads(run_id,thread_id),
  FOREIGN KEY (run_id,parent_task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (run_id,brief_ref) REFERENCES h2_artifacts(run_id,artifact_ref),
  FOREIGN KEY (run_id,budget_ref) REFERENCES h2_budgets(run_id,budget_id),
  FOREIGN KEY (task_id,current_attempt_id) REFERENCES h2_attempts(task_id,attempt_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (task_id,result_ref) REFERENCES h2_results(task_id,result_ref)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX h2_one_open_task_per_thread ON h2_tasks(thread_id)
  WHERE status NOT IN ('succeeded','partial','failed','cancelled','timed_out');
CREATE INDEX h2_tasks_run_status ON h2_tasks(run_id,status,priority DESC,created_at);
CREATE INDEX h2_tasks_parent ON h2_tasks(run_id,parent_task_id);

CREATE TABLE h2_task_dependencies (
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  depends_on_task_id TEXT NOT NULL,
  PRIMARY KEY (task_id,depends_on_task_id),
  CHECK (task_id<>depends_on_task_id),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (run_id,depends_on_task_id) REFERENCES h2_tasks(run_id,task_id)
);

CREATE TABLE h2_attempts (
  attempt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL CHECK (attempt_no>0),
  attempt_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN
    ('queued','leased','running','suspended','succeeded','failed','expired','cancelled')),
  worker_id TEXT,
  lease_token TEXT,
  lease_expires_at INTEGER,
  heartbeat_at INTEGER,
  model_snapshot_ref TEXT,
  resume_checkpoint_ref TEXT,
  checkpoint_ref TEXT,
  result_ref TEXT,
  usage_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(usage_json)),
  error_code TEXT,
  error_message TEXT,
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  finished_at INTEGER,
  UNIQUE (run_id,attempt_id),
  UNIQUE (task_id,attempt_id),
  UNIQUE (task_id,attempt_no),
  CHECK (status NOT IN ('leased','running') OR
    (worker_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
      AND model_snapshot_ref IS NOT NULL)),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (run_id,model_snapshot_ref) REFERENCES h2_snapshots(run_id,snapshot_id),
  FOREIGN KEY (task_id,resume_checkpoint_ref) REFERENCES h2_checkpoints(task_id,checkpoint_ref),
  FOREIGN KEY (task_id,checkpoint_ref) REFERENCES h2_checkpoints(task_id,checkpoint_ref)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (task_id,result_ref) REFERENCES h2_results(task_id,result_ref)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX h2_one_live_attempt ON h2_attempts(task_id)
  WHERE status IN ('queued','leased','running');
CREATE INDEX h2_attempts_expiry ON h2_attempts(status,lease_expires_at);

CREATE TABLE h2_messages (
  message_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK (sequence>0),
  kind TEXT NOT NULL CHECK (kind IN ('initial','follow_up','cancel','system')),
  payload_ref TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','delivered','consumed','rejected')),
  delivered_attempt_id TEXT,
  created_at INTEGER NOT NULL,
  consumed_at INTEGER,
  rejection_code TEXT,
  UNIQUE (thread_id,sequence),
  UNIQUE (thread_id,idempotency_key),
  FOREIGN KEY (run_id,thread_id) REFERENCES h2_threads(run_id,thread_id),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (task_id,delivered_attempt_id) REFERENCES h2_attempts(task_id,attempt_id),
  FOREIGN KEY (run_id,payload_ref) REFERENCES h2_artifacts(run_id,artifact_ref)
);
CREATE INDEX h2_mailbox_pending ON h2_messages(thread_id,status,sequence);

CREATE TABLE h2_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  thread_id TEXT,
  thread_seq INTEGER,
  aggregate_type TEXT NOT NULL CHECK (aggregate_type IN
    ('run','thread','task','attempt','message','result','outbox','budget','wait','model_call')),
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  payload_ref TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE (thread_id,thread_seq),
  CHECK ((thread_id IS NULL AND thread_seq IS NULL) OR
    (thread_id IS NOT NULL AND thread_seq>0)),
  FOREIGN KEY (run_id,thread_id) REFERENCES h2_threads(run_id,thread_id),
  FOREIGN KEY (run_id,payload_ref) REFERENCES h2_artifacts(run_id,artifact_ref)
);
CREATE INDEX h2_events_run_cursor ON h2_events(run_id,event_id);
CREATE TRIGGER h2_events_no_update BEFORE UPDATE ON h2_events
  BEGIN SELECT RAISE(ABORT,'events_are_append_only'); END;
CREATE TRIGGER h2_events_no_delete BEFORE DELETE ON h2_events
  BEGIN SELECT RAISE(ABORT,'events_are_append_only'); END;

CREATE TABLE h2_checkpoints (
  checkpoint_ref TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  checkpoint_version INTEGER NOT NULL CHECK (checkpoint_version>0),
  schema_version TEXT NOT NULL,
  state_ref TEXT NOT NULL,
  consumed_message_seq INTEGER NOT NULL CHECK (consumed_message_seq>=0),
  last_event_id INTEGER NOT NULL CHECK (last_event_id>=0),
  created_at INTEGER NOT NULL,
  UNIQUE (task_id,checkpoint_ref),
  UNIQUE (task_id,checkpoint_version),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (task_id,attempt_id) REFERENCES h2_attempts(task_id,attempt_id),
  FOREIGN KEY (run_id,state_ref) REFERENCES h2_artifacts(run_id,artifact_ref)
);

CREATE TABLE h2_results (
  result_ref TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  result_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  result_version INTEGER NOT NULL CHECK (result_version>0),
  status TEXT NOT NULL CHECK (status IN ('succeeded','partial','failed','cancelled')),
  disposition TEXT NOT NULL CHECK (disposition IN ('candidate','accepted','late')),
  metadata_status TEXT NOT NULL CHECK (metadata_status IN
    ('pending','complete','incomplete_metadata')),
  summary_ref TEXT NOT NULL,
  full_ref TEXT NOT NULL,
  sections_json TEXT NOT NULL CHECK (json_valid(sections_json)),
  evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json)),
  warnings_json TEXT NOT NULL CHECK (json_valid(warnings_json)),
  created_at INTEGER NOT NULL,
  retention_until INTEGER,
  UNIQUE (task_id,result_ref),
  UNIQUE (task_id,result_version),
  UNIQUE (attempt_id,result_key),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (task_id,attempt_id) REFERENCES h2_attempts(task_id,attempt_id),
  FOREIGN KEY (run_id,summary_ref) REFERENCES h2_artifacts(run_id,artifact_ref),
  FOREIGN KEY (run_id,full_ref) REFERENCES h2_artifacts(run_id,artifact_ref)
);

CREATE TABLE h2_outbox (
  outbox_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  dispatch_key TEXT NOT NULL,
  dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation>0),
  stream_name TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  status TEXT NOT NULL CHECK (status IN
    ('pending','publishing','published','retrying','dead_letter')),
  publisher_token TEXT,
  publishing_expires_at INTEGER,
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count>=0),
  next_retry_at INTEGER NOT NULL,
  redis_entry_id TEXT,
  published_at INTEGER,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE (attempt_id,dispatch_generation),
  CHECK (status<>'publishing' OR
    (publisher_token IS NOT NULL AND publishing_expires_at IS NOT NULL)),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (task_id,attempt_id) REFERENCES h2_attempts(task_id,attempt_id)
);
CREATE INDEX h2_outbox_due ON h2_outbox(status,next_retry_at);

CREATE TABLE h2_commands (
  command_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES h2_runs(run_id),
  caller_thread_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT NOT NULL CHECK (json_valid(response_json)),
  created_at INTEGER NOT NULL,
  UNIQUE (run_id,caller_thread_id,operation,idempotency_key),
  FOREIGN KEY (run_id,caller_thread_id) REFERENCES h2_threads(run_id,thread_id)
);

CREATE TABLE h2_waits (
  wait_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  owner_task_id TEXT NOT NULL,
  owner_attempt_id TEXT NOT NULL,
  wait_kind TEXT NOT NULL DEFAULT 'tasks' CHECK (wait_kind IN ('tasks','mailbox')),
  mode TEXT NOT NULL CHECK (mode IN ('any','all','specific')),
  status TEXT NOT NULL CHECK (status IN ('waiting','ready','timed_out','cancelled')),
  after_event_id INTEGER NOT NULL CHECK (after_event_id>=0),
  deadline_at INTEGER NOT NULL,
  wake_reason TEXT,
  checkpoint_ref TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (wait_id,run_id),
  FOREIGN KEY (run_id,owner_task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (owner_task_id,owner_attempt_id) REFERENCES h2_attempts(task_id,attempt_id),
  FOREIGN KEY (owner_task_id,checkpoint_ref) REFERENCES h2_checkpoints(task_id,checkpoint_ref)
);
CREATE UNIQUE INDEX h2_one_open_wait ON h2_waits(owner_task_id) WHERE status='waiting';
CREATE INDEX h2_wait_deadlines ON h2_waits(status,deadline_at);

CREATE TABLE h2_wait_targets (
  wait_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  PRIMARY KEY (wait_id,task_id),
  FOREIGN KEY (wait_id,run_id) REFERENCES h2_waits(wait_id,run_id),
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id)
);

CREATE TABLE h2_model_calls (
  call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  budget_id TEXT NOT NULL,
  model_snapshot_ref TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('reserved','started','succeeded','failed','unknown')),
  usage_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(usage_json)),
  error_code TEXT,
  started_at INTEGER,
  finished_at INTEGER,
  FOREIGN KEY (run_id,task_id) REFERENCES h2_tasks(run_id,task_id),
  FOREIGN KEY (task_id,attempt_id) REFERENCES h2_attempts(task_id,attempt_id),
  FOREIGN KEY (run_id,budget_id) REFERENCES h2_budgets(run_id,budget_id),
  FOREIGN KEY (run_id,model_snapshot_ref) REFERENCES h2_snapshots(run_id,snapshot_id)
);

CREATE TABLE h2_budget_entries (
  entry_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  budget_id TEXT NOT NULL,
  transaction_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN
    ('grant','transfer_in','transfer_out','charge','refund')),
  call_id TEXT REFERENCES h2_model_calls(call_id),
  delta_calls INTEGER NOT NULL CHECK (delta_calls<>0),
  created_at INTEGER NOT NULL,
  UNIQUE (budget_id,transaction_key,kind),
  CHECK ((kind IN ('grant','transfer_in','refund') AND delta_calls>0) OR
    (kind IN ('transfer_out','charge') AND delta_calls<0)),
  FOREIGN KEY (run_id,budget_id) REFERENCES h2_budgets(run_id,budget_id)
);
CREATE UNIQUE INDEX h2_budget_one_charge ON h2_budget_entries(call_id) WHERE kind='charge';
CREATE UNIQUE INDEX h2_budget_one_refund ON h2_budget_entries(call_id) WHERE kind='refund';
```

### 3.1 DDL 外仍必须检查的约束

1. Task 的 Thread、父 Task、Brief、Attempt、结果来源匹配；同 run 外键不等于有权访问。同 Thread 串行不限制其他 Thread 并行。
2. 子 Thread 的 `depth=parent.depth+1`、同 root；Task 和 budget 的祖先图无环。依赖图使用递归检查拒绝环路；不能仅靠“不依赖自己”。
3. 快照 kind 匹配使用位置；模型快照不含密钥，只含 profile/version、endpoint、协议、参数、Prompt/Skill/工具契约版本及 hash、凭证环境变量名。
4. `current_attempt_id`、有效租约、未取消、未超时共同决定写入资格；SQL 部分唯一索引不能替代 lease fencing。
5. `last_event_seq` 是 Thread 最新事件高水位，不是所有读取者共用的“已读位置”。各读取者持有自己的游标，Checkpoint 保存当前执行实际消费位置。
6. Result/Checkpoint/Artifact/快照正文不可覆盖；修订创建新版本。Result 发布资格和元数据标记可从 candidate 转 accepted/late，但正文、hash、来源不能改。
7. Event 的 `event_key` 来自命令或原子状态变更，事务重试不重复写同一事实；全局序号允许有间隙。
8. `sections_json` 引用、EvidenceRef、ModelCall 和账本 call_id 必须指向同 run/来源任务；不能用自由 JSON 绕过校验。
9. tasks 类型 wait 必须有目标且不等待自己；在同一事务检查 wait/dependency 联合图，拒绝直接和间接等待环。mailbox 类型没有目标，消费新消息才唤醒，不能套用“all 空集合已满足”。

## 4. 原子事务边界

所有写事务使用 `BEGIN IMMEDIATE`，CAS 更新必须检查影响行数；事务内不得调用模型或 Redis。

| 事务 | 必须一起成功的操作 | 外部动作 |
| --- | --- | --- |
| T01 接收 spawn | 校验命令 hash/权限/父状态/depth/预算；创建快照与 Brief 引用、Thread、Task、queued Attempt、initial Message、Event、Outbox、命令回执 | SQLite commit 后返回；不等 Redis |
| T02 claim | 验证消息匹配数据库且 Attempt 仍为 current；deadline/cancel/dependency/concurrency 检查；绑定 worker、随机 lease_token、期限、模型快照；Task running、Thread running、Event | commit 后才允许执行 |
| T03 heartbeat | 以 current Attempt、token、未到期、未取消为条件更新租约/心跳；期限不越过 Task/Run deadline | 更新 0 行立即停止新动作，不可“复活”过期租约 |
| T04 模型调用预约 | 检查余额和收尾预留；创建 call_id、一次 charge、model.call_reserved Event | commit 后调用；明确未发出的请求才可幂等 refund；超时未知仍计次数 |
| T05 交付结果 | 检查 fencing；登记已落盘候选，result_version；验证引用；设置接受结果、Attempt/Task 状态、释放并发名额及未用子预算、Thread 状态、Event | commit 后 XACK；丢 ACK 可安全重放 |
| T06 send | 命令 hash 校验；Thread sequence 递增；Message、Event、回执；必要的续调度一并写入 | 不依赖 Redis 保证 Mailbox 顺序 |
| T07 cancel | 标记整个未完成后代子树 cancel_requested；撤销发布资格；取消 queued/等待任务，写 Event、等待唤醒与回执；保留终态结果 | HTTP best-effort abort；运行 Worker 边界检查；不承诺远端停止计费 |
| T08 过期接管 | 旧 Attempt expired、Event；Task current 指向新 queued Attempt、Outbox；未完成外部动作登记 unknown | 仅基础设施故障自动接管；不能重放未知写副作用 |
| T09 resume | 校验 Checkpoint hash/版本/Task/来源、权限、模型授权、剩余预算/时限；新 Attempt、Task pending_dispatch、Outbox、Event、回执 | 不能清零已消耗预算或复用旧租约 |
| T10 wait/suspend | 注册 wait 和 targets、保存 Checkpoint，最后再检查目标状态；未满足时 Task waiting、Attempt suspended、释放有效 lease，Event | 醒来创建新 Attempt；R1 已确认 |
| T11 唤醒 | 命中状态/超时，wait 原子关闭；Task 仍可执行时新 Attempt、Outbox、Event；重复唤醒只成功一次 | 不占住 Worker 等子任务；到期不取消子任务 |
| T12 Outbox | CAS 领取发布锁；或用 publisher_token CAS 记录发布/重试；dead_letter 与 Task blocked、dispatch_failed Event 同事务 | XADD 在领取与确认两个事务之间 |

取消与结果发布若竞争，由 SQLite 提交顺序裁决：取消先提交则结果仅审计；结果先提交则已完成 Task 不被追溯改为 cancelled，取消继续作用于未完成后代。父任务被取消后，子任务即使晚完成也不得发布为该父任务的新研究结果。

### 4.1 大产物提交

先在 run 目录写临时文件，计算 SHA-256、flush/fsync，原子 rename；随后数据库事务登记 Artifact、Result/Checkpoint 和 Event。DB 回滚时允许留下无引用孤儿，由 GC 在保留期后清理。不能先写 succeeded 再补正文。临时文件、路径和符号链接都须验证仍在允许的运行根目录。

`events.jsonl` 是数据库 Event 的可重建导出，使用 event_id 去重；不是与 DB 额外双写才能完成的第二权威。原始流式记录采用单独产物，事件仅记录引用/完成边界，避免每个 token 抢占 SQLite Writer。恢复发现文件丢失/校验失败，返回 `artifact_unavailable`，不能用空结果冒充成功。

## 5. 状态、预算与恢复

### 5.1 状态迁移

- Task 主链：pending_dispatch -> queued -> running -> succeeded/partial/failed。
- running -> waiting；满足等待或超时返回 Agent 时，waiting -> pending_dispatch -> queued -> running，保留 Task，换 Attempt。
- pending_dispatch/queued -> backpressured；解除拥堵后恢复待投递，不能为过期消息无限创建新 Attempt。
- 依赖未满足/Outbox dead_letter -> blocked；依赖恢复、人工重投或父 Agent 明确决策后才能重新调度。失败依赖的默认处理见 R3。
- 有效 deadline 到期 -> timed_out；明确取消 -> cancelled；取消后的普通 resume 拒绝，必须显式创建新工作，不能撤销用户取消。
- failed/partial/timed_out 可在有效 Checkpoint、剩余 Run 时限和预算内显式 resume；succeeded 不恢复为旧任务，追问是新 Task。
- Attempt：queued -> leased -> running -> succeeded/failed/expired/cancelled；R1 已确认 running -> suspended，suspended 是执行片段结束，不等于 Task 完成。
- Task 终态集合：succeeded/partial/failed/cancelled/timed_out；blocked 不是终态，wait 可超时返回其真实状态。

Thread ready/running/waiting 是 Task 的生命周期投影；failed 不抹去结果，closed 不接受新工作。Run 状态不是金融事实可信度，也不覆盖 1.0 的 delivery/semantic review 状态。

### 5.2 预算账本

预算不在 Task、Thread 和 Attempt 三处复制余额。`budget_ref` 指向同一账户；Thread 是默认策略引用，不凭空多获一份额度。

根 grant 一次；给子任务分配时同一事务写 parent transfer_out 与 child transfer_in，金额相等。调用前 charge 一次，明确未发出才 refund；子任务结束把未用余额 transfer_out 回 parent。每一组 transfer 使用同一个 transaction_key，必须有相等的双边分录，所有账户余额不能为负。

- 余额 = 该账户所有 `delta_calls` 的和。
- requested/allocated 来自预算记录；consumed = 有效 charge 减 refund，跨子树按 call_id 去重汇总；remaining = 本账户可花余额 + 子树仍可回收余额，展示时分开列出 delegated 和 local_available，不能相加重复计算。
- 收尾预留不能再派给子任务；只有进入收尾阶段才可使用对应份额。Run/Task 的 wall time 是截止时间，不按子任务耗时相加扣减。
- 初始上限沿用现有配置：Run 60 分钟、收尾最多 10 分钟；analyst 20 分钟、收尾最多 2 分钟；请求额度继承现有配置，不因迁移静默缩减；子任务不能超父剩余时限和额度。
- 预算不足按基线显式返回 budget_reduced/budget_denied；不能以“接口接受”为名静默少分额度。状态信息披露调用次数不能精确保证金额。

Worker 失联后，已开始但未结束的模型调用记 unknown、保留 charge。不能因为换 Attempt 再返还并重复花掉同一余额。

## 6. 约束验证与迁移检查

设计审查必须覆盖：DDL 可执行、foreign_key_check 无错误、跨 run 外键拒绝、重复键拒绝、同 Thread 活跃 Task 唯一、同 Task 活跃 Attempt 唯一、Event 更新删除拒绝；另外用 Repository 故障注入验证事务与并发，不能把“表能建出来”当作调度已实现。

将来迁移步骤：备份新项目 DB -> 校验 SQLite/JSON/WAL 能力 -> 在维护窗口事务建 h2 表并记录 schema version -> 验证 FK/索引 -> 新路径显式开关接入。失败回滚新迁移，保留旧 1.0 路径；不回填旧运行历史，不启动旧项目 Worker，不迁移私有语料。首次迁移编号由实际迁移系统分配，不在文档里伪造已执行版本。

SQLite 机制依据：[事务与单 Writer](https://www.sqlite.org/lang_transaction.html)、[外键必须在连接上启用](https://www.sqlite.org/foreignkeys.html)。这些机制只支持存储实现，不证明本项目的调度语义或金融质量。
