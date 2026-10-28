# H2 Process Fault Validation

Date: 2026-10-19

Status: verification of already approved C01/C03/C04/C05/C09/C10/C14/C17 and
032 transaction boundaries. No new research, budget or model policy is proposed.
Real Redis validation remains deferred by the user; do not repair Docker.

## Method And Expected Outcomes

Use Windows-compatible multiprocessing spawn, isolated file-backed SQLite/WAL,
synthetic artifacts and controlled transport. Synchronize at a known boundary,
terminate only the test-owned process, then reopen the same DB in a fresh
Repository. Every child must be joined and closed even on assertion failure.

1. Spawn transaction before commit: kill after receipt writes but before commit.
   No new child, spawn command, Outbox or allocation is visible; same key can create one
   child later. Unreferenced artifact files are not accepted work.
2. Spawn committed before queue publish: kill after receiving the DB receipt.
   Original child, budget and pending Outbox survive; replay returns the same
   receipt. FakeRedis may demonstrate later publication, not real networking.
   Also pause a real Dispatcher process after controlled notification publication
   but before DB published: publisher lease expiry permits the same notification
   again; duplicate consumption executes the task once, without a new budget.
3. Result committed before ACK: pause Worker at ACK, kill it, re-deliver the
   original message to a fresh Worker. It only ACKs; no handler/model or second
   accepted result. Full persisted body remains retrievable.
4. Continue committed before ACK: same crash window retains exactly one
   successor, checkpoint and Outbox. Old delivery only ACKs; successor uses the
   saved progress, not an in-memory stack or repeated completed action.
5. Continue transaction before commit: pause after task.continued is inserted,
   kill; checkpoint/successor/Outbox/event all roll back. Lease expiry can create
   one replacement, not two active Attempts.
6. Saved checkpoint and started synthetic call: kill, expire lease, reconcile.
   Replacement restores checkpoint/mailbox cursor; old call is unknown, charge
   is retained without refund; late old result cannot replace accepted output.
7. Persist cancellation while Worker is paused, then kill. Reconcile settles
   cancellation; no successful replacement or new model reservation.
8. Two real processes contend for the same task for 20 bounded rounds. Exactly
   one valid lease per round, one accepted result, no invalid active successor.
9. Corrupt or remove temporary checkpoint/context/brief artifacts before fresh
   Worker recovery. No handler runs or false success. Preserve checkpoint_invalid
   for invalid recovery state and artifact_unavailable for missing task material,
   rather than recategorizing either as a model contract_error.

## Reporting Boundary

Record real process termination separately from prior exception injection and
FakeRedis tests. It does not simulate power loss, real Redis PEL/network behavior,
multi-host operation, paid model choices or sustained production load. Do not
rewrite R4-R6, expand budgets, start live batches, or switch default 1.0.

The first run reproduced a classification mismatch: Worker replaced
checkpoint_invalid/artifact_unavailable from execution_input with contract_error.
No damaged state reached the handler, but the persisted cause blamed the model
contract. Preserve the original supported code through _fail_execution; do not
change valid model-contract failures or add retries. Fixture corrections are
separate: run.create already has a command receipt, and context_ref is inside
the checkpoint artifact, not a column of h2_checkpoints.

Final broad H2/state/compatibility/live-driver regression: **443 passed /
7 skipped, 173.19 seconds**. All **15 process-fault cases** passed within that
collection, including 20 synchronized rounds between two actual processes.
The earlier 14-case run passed in 30.33 seconds before adding the Dispatcher
notification/DB-mark kill window. Do not sum those overlapping runs or call the
20 claim rounds 20 different business Queries. Scoped Ruff passed.

Seven real Redis tests remain skipped. No paid model, Docker operation or
production-service change occurred. For the default-finance regression and
remaining scope see the [current report](../acceptance/harness-v2-current-acceptance-20261019.md).
