-- H2-CONTRACTS-2026-10-18-v1. Keep in sync with docs/harness-v2/sqlite-schema.md.
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
