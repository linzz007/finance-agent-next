# H2 Recovery Attempt Accounting

Date: 2026-10-19

Status: implementation correction within the approved bounded recovery design.
Authority: 013 sections 5/10/13 and interface-contracts sections 2/8, including
the R1 suspended Attempt semantics. This is not a new Root correction allowance.

## Defect And Intended Behavior

R5 Q08 and Q14 reached their first transport failure after multiple successful
wait/resume steps. Both stopped because the implementation uses chronological
attempt_no to enforce max_attempts=5. Suspensions are normal progress, not failed
retries. Lease-expiry reconciliation uses the same incorrect comparison.

Keep attempt_no monotonically increasing for history. Enforce the existing
max_attempts setting against actual failed or expired Attempts for the Task:
after recording the current failure, retry only while that count is below the
configured cap. Suspended, queued and successful Attempts do not consume this
allowance. This is a cumulative Task allowance, not a reset after every wait.
Explicit resume preserves its existing authorization and history semantics.

No new table, larger cap, budget reset, deadline extension, new profile, hidden
HTTP retry or broader retryable error class. Contract/quota/permission failures
remain terminal at their existing boundary. The approved one-shot Root correction
still must not restart the whole decision after its second call fails.

## Implementation And Verification

Use one SQL count helper in normal transport failure and expired-lease recovery.
Record failed/expired history before evaluating it, retain the last checkpoint,
and let existing budget, policy and lease fences govern the next Attempt.

Tests must cover:

- More normal suspensions than max_attempts, then the first transport failure:
  a new Attempt resumes the same checkpoint and model without replaying children.
- The same normal progress before a lease expiry: recovery still works, and the
  old lease is fenced with an unknown physical charge retained.
- Mixed transport failures and expiries share the cap; further normal waits do
  not reset it. Reaching the cap terminates without another Attempt.
- Each physical request remains charged; suspended Attempts create no charges.
- Root's failed format correction remains one-shot; default 1.0 is unchanged.

R5 remains frozen. Verify this correction offline before any separately defined
live batch; do not append a paid rerun merely to turn R5 into a pass.
