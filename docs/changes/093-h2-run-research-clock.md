# H2 Run Research Clock

Date: 2026-10-21. Status: recorded before implementation; implemented and offline verified.
Authority: architecture section 3 requires relative article periods to use the
research clock; section 8 requires clock/configuration traceability. This repairs
an existing H2 integration gap, not a new research or financial-review policy.

## Minimal Contract

- A Run has one trusted `research_clock`: `today` is an exact ISO calendar date
  (`YYYY-MM-DD`), and `timezone` is `Asia/Shanghai`. No free-form instructions,
  extra fields, model-supplied clocks or arbitrary timezone conversion rules.
- Trusted Python `create_run` callers may supply this object for reproducible
  acceptance. When omitted, derive it once from Run creation time in Shanghai
  and store it in the immutable Root brief. The HTTP request shape is unchanged;
  its ordinary creation path receives the same default.
- Every Runtime TaskBrief, including children, grandchildren and follow-ups in
  this Run, obtains its clock from the original Root brief. A checkpoint, query,
  source, mailbox or child assignment cannot override trusted clock metadata.
- Restore the original date after a Worker/Service restart or midnight. For an
  older Run without stored clock metadata, derive the date from that Run's
  persisted `created_at`, never the current host date. Do not rewrite old briefs
  or require a database migration.
- Omitted optional metadata must not change historical `run.create` replay
  hashes. Explicit clocks are validated before new artifacts are written and
  participate in idempotency checks; conflicting explicit requests are rejected.

## Model And Trace

The shared finance input exposes the trusted clock separately from source dates.
Root and research prompts briefly require resolving relative periods to exact
publication-date bounds before article filtering. An explicit historical date
in the user's question remains an explicit date, not a request to change the Run
clock. Publication date, research date, acquisition timestamp and host time stay
distinct. No Runtime-selected article quota or forced analysis template is added.

The acceptance runner must pass the frozen snapshot's research date into the
actual Run. Records separately identify configured clock propagation and actual
model execution; unexecuted/blocked cases are not claimed as model observations.
Preserve all existing paid batches and their original missing-clock disclosures.

## Verification

1. Strict date/shape validation; invalid input writes no Run or artifact.
2. Omitted/explicit idempotency and conflicts, including a retry after midnight.
3. Root, depth-one/two and follow-up receive the same original clock after restore.
4. Historical no-clock Runs use original creation time; no implicit live migration.
5. Trusted Brief replaces a stale checkpoint clock; source/query clocks stay data.
6. Actual mock-provider messages and runner arguments carry the frozen date.
7. Focused tests, H2/state/compatibility regression, default-finance baseline,
   configuration isolation, scoped lint and diff checks. Offline green tests do
   not certify real-model date reasoning, real Redis or semantic quality.

No change to budgets, source permissions, JSON recovery, native Function Calling,
model fallback, review gates, automatic closing or the default 1.0 route.

## Actual Evidence

- The initial 13 repository cases failed before implementation, reproducing the
  absent clock and unsupported explicit-clock argument. Keep `red.xml`.
- Final focused repository/context/runner checks: 117 passed, 28.18 seconds.
  Intermediate fixture failures are retained separately: error-code assertion
  matched exception text instead of `.code`, result fixtures omitted summaries,
  and four context fixtures omitted required `phase`. These were test mistakes,
  not additional product defects.
- Final H2/state/compatibility/runner regression: 1219 passed, 7 skipped,
  2553 deselected, 418.35 seconds. This includes actual mock-provider input
  assertions at depths zero/one/two and across a fresh Service after Shanghai
  midnight, within original leases and deadlines. Seven skips need real Redis.
- Default finance/shared Runtime: 52 modules, 1638 passed, 30 skipped,
  114.46 seconds. Temporary workspaces, no live credentials or paid calls.
- Configuration/isolation: 10/10; scoped Ruff and tracked diff checks pass.
  JUnit confirms zero H2 failures/errors. The H2 PowerShell wrapper returned
  nonzero because inherited `SSL_CERT_DIR` made uv write a warning to stderr;
  preserve that wrapper outcome and log, not a fictional clean exit code.

Evidence: sibling `runtime-records/h2-research-clock-mvp-20261021/`. Preserve
the earlier red/intermediate XML files, Q23/Q24 records and run-end integrity.
No new paid Query, full-suite rerun, real-Redis validation, live database
migration, automatic model switch or Git upload occurred.

`research_clock_bound_to_run` records persisted configuration. The per-case
`research_clock_propagated_to_model` flag means that a model-adapter invocation
started on the clock-bearing path; it is not proof that a remote provider
received the request or that the model reasoned correctly about dates.
The batch manifest declares the binding contract, not an executed-model pass;
blocked cases without calls retain false.
