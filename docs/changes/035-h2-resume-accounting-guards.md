# H2 Resume Accounting Guards

Date: 2026-10-19

Status: implemented and offline-verified against existing frozen contracts,
not a new budget or financial-research policy. Authority: interface-contracts
2.1/2.3/6.2, sqlite-schema 5.1/5.2 and the unique open-Task-per-Thread invariant.

## Required Behavior

- Resume restores the original unconsumed grant, without increasing it. If the
  unused allocation was returned to the parent, reacquisition must preserve that
  parent's finalization reserve just as a new child allocation does. Insufficient
  research funds returns budget_denied atomically; do not grant a smaller amount.
- A Thread may have only one open Task. Resuming an older failed/partial Task
  while a follow-up Task is open returns thread_busy, not an SQLite integrity
  failure or an HTTP 500. A closed Thread rejects new execution.
- After a follow-up has ended, resuming an eligible older Task restores the
  Thread's budget reference to that Task's account. Each model-call ledger row
  and charge must reference the executing Task's actual account, not a stale
  Thread default left by another Task. Keep old Task/result/call history intact.
- Messages to a Thread target its sole open Task, including an older resumed
  Task. Only when there is no open Task may send create the explicit terminal
  follow-up. Select again in the acceptance transaction; a changed target returns
  state_conflict instead of misrouting a message or violating the unique index.
- Public errors retain their established categories: lifecycle/recursion/budget
  rejection is a 409 state conflict; temporary dispatch/transport/storage failure
  is 503. An already accepted pending-dispatch receipt remains 202. Unknown
  internal failures remain 500; no user-visible credential/provider body.
- Task preparation must not commit a stale parent model-policy snapshot after
  an explicit user change. Sections 2.3/3 require new work to inherit the current
  parent policy and permit state_conflict when preparation races a state update.
  Detect a changed parent snapshot before allocating/accepting spawn; retrying
  the same unaccepted key may prepare from the new snapshot. Never reinterpret
  an already accepted idempotent receipt or mutate a running Attempt's model.

## Reproduction Before Repair

1. Fail a 30-call child with a valid checkpoint and return its unused funds.
   Allocate another child 551 of the original 600 calls, leaving 49. Resume
   must not take 30 and leave only 19 of the parent's 24 closing calls. At the
   exact boundary (54 available), restore exactly 30 and retain 24.
2. Start a follow-up on the same Thread, then resume the original failed Task.
  Pending-dispatch/running/waiting follow-ups must all cause atomic thread_busy. Closing
   the Thread must cause invalid_state. No new Attempt, transfer or model policy
   snapshot may survive a rejection.
3. Complete the follow-up, resume the original Task, reserve a synthetic call,
   and compare Task budget_ref, Thread budget_ref, model-call budget_id and charge
   budget_id. All must match the restored account; aggregate consumption remains
   conserved and the follow-up's result/account stay unchanged.
4. Exercise known lifecycle/budget and infrastructure error categories through
   the HTTP mapping and representative requests. No real model/Redis is needed.
5. Change the parent's selected model while preparing a child brief. Both
   implicit and explicit selections must use one coherent policy snapshot or
   reject atomically with state_conflict. Retrying the same unaccepted key uses
   the new policy; an explicit allowed profile remains explicit.
6. Complete a follow-up and resume the older Task, then send while it is pending
   dispatch, running or waiting. A plain message targets that Task without a new budget;
   an explicit new-work contract is invalid while it is open. Resume racing
   terminal follow-up preparation must reject the stale send target atomically.

Record the reproduced failures and final regression separately. Real Redis
remains user-deferred. The subsequently authorized two-case paid batch is
specified separately in [036](036-h2-explicit-spawn-live-protocol.md).

## Reproduction Results And Repair

Before the corresponding production fixes:

- Resume reproduction: **6 failed / 1 passed / 119 deselected**, 4.38 seconds.
  The exact funding boundary already passed. The failures demonstrated reserve
  invasion, raw SQLite unique-index failures in three follow-up states, closed
  Thread execution and mismatched Task/call budget references.
- HTTP reproduction: **12 failed / 14 passed / 4 deselected**, 5.43 seconds.
  Ten known error categories fell through to 500; real budget-denied and busy
  resume requests also failed their expected public state-conflict behavior.
- Model-policy preparation race: **2 failed / 126 deselected**, 1.72 seconds.
  Both forms incorrectly accepted a child after the parent's policy changed.
- Continued message routing audit: **7 failed / 128 deselected**, 6.57 seconds.
  A plain send mistook the newest-created terminal follow-up for current work;
  an explicit work contract or a resume/preparation race hit the unique index.

Repository now checks Thread lifecycle/occupancy and the parent's finalization
reserve in the same resume write transaction. Rejection rolls back policy
snapshots, transfers, Attempts, events and command receipts. Successful resume
restores the Thread pointer to the Task account, and reserve_call independently
binds the call ledger to that actual account. No DDL or migration changed.

Spawn compares the parent policy reference again before accepting the prepared
brief; state_conflict leaves no child allocation/receipt. A retry can use the new
snapshot. The running model snapshot remains unchanged. HTTP mapping classifies
known errors without exposing provider bodies or private details.
Send now uses the same open-Task-first selector before preparation and inside
its write transaction. Terminal-history ordering is unchanged when no work is
open. A changed target rejects before new message/Task/budget acceptance.

## Offline Evidence

- Resume/HTTP focused collection after their fixes: **149 passed / 7 skipped**,
  51.13 seconds. The two newly added policy-race cases then passed separately
  in 2.84 seconds, checking error.code rather than human-readable error text.
- Intermediate complete H2/state/compatibility/live-driver collection: **478 passed /
  7 skipped**, 178.86 seconds. All **35 added cases** are included: seven resume,
  24 error classifications, two real HTTP requests and two policy-race cases.
- Subsequent message-routing repair: **7 passed / 128 deselected**, 5.72 seconds,
  covering pending-dispatch/running/waiting, message replay, no extra allocation,
  explicit-work rejection and resume racing preparation.
- Final complete H2/state/compatibility/live-driver collection after routing:
  **485 passed / 7 skipped**, 178.74 seconds, including all **42 added cases**.
  Default finance/shared-runtime: **1606 passed / 30 skipped**, 114.78 seconds,
  across 52 modules. These are offline regressions, not live Query results.
- Scoped Ruff and git diff --check passed. The existing Starlette/httpx warning
  remains; the seven skipped tests need real Redis. Counts overlap, not additive.

These use temporary SQLite, fixture models, FakeRedis or controlled transports.
They do not establish real Redis behavior, live-model research quality or a
production entry switch. Default finance regression and remaining acceptance
are recorded in the [current report](../acceptance/harness-v2-current-acceptance-20261019.md).
