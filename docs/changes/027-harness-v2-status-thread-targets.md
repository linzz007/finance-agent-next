# Harness 2.0 Status Thread Targets

Date: 2026-10-19
Status: implemented
Baseline: `H2-CONTRACTS-2026-10-18-v1`

## Change

`agent.status` now accepts either `task_ids`, `thread_ids`, or both. The request
must include at least one target and rejects duplicate targets within either
list. A Thread target returns the Tasks belonging to that exact Thread,
including follow-up Tasks created on the same Thread; it does not implicitly
traverse the descendant Thread tree.

Repository access checks remain authoritative for every resolved Task. A
caller cannot use a Thread ID to cross a Run, read a sibling Thread, or bypass
the existing parent/child visibility boundary. Tasks selected through both
forms are de-duplicated in the response.

## Verification

- `test_status_accepts_thread_targets_and_returns_followups_with_scope`
- HTTP status route accepts `thread_ids` and rejects an empty target set.
- H2 dispatch regression: `207 passed, 2 skipped, 1 warning`.
- The two skipped tests require `H2_TEST_REDIS_URL`; they are not Redis
  deployment evidence.

