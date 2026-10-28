# H2 Confirmed-Unsent Settlement After Cancellation

Date: 2026-10-19

Status: existing-contract implementation repair. No paid calls, new budgets,
model fallback, research rule or production-path change is authorized here.

## Authority And Gap

[SQLite T04 and budget accounting](../harness-v2/sqlite-schema.md) separate a
reserved call from a physically started call and permit an idempotent refund
only for a confirmed-unsent request. [Interface C09/C14](../harness-v2/interface-contracts.md)
require cancellation to stop new requests and preserve truthful physical-call
accounting. This is the same send boundary examined in [038](038-h2-model-revocation-boundary.md).

ExecutionCapabilities confirms that no send began when cancellation interrupts
reservation before call_started. However, refund_unsent_call requires the live
execution fence, which cancellation invalidates. Its cancelled/stale_lease error
is suppressed; the reserved charge remains, and termination labels it unknown.
The existing cancellation test checks zero HTTP sends but not the ledger.

## Repair Boundary

- Permit this internal audit settlement for the original Run/Thread/Task/Attempt
  and exact original lease token, even after cancellation, expiry or replacement.
  This does not restore execution, publish results or authorize another send.
- Refund only a still-reserved call, or an unknown call with no started_at after
  lifecycle settlement, when the original consumer confirms it never sent.
  Do not automatically refund abandoned reservations in reconcile: a missing
  consumer is not confirmation. A started_at value always forbids refund.
- Bind the credit to the call's original budget, not another Task's active
  account. If the original Task has terminated, return the restored unused
  balance through the existing parent-release chain in the same transaction.
- Repeating settlement gives one refund only. Competing start/refund operations
  serialize: start wins and no refund is allowed, or refund wins and no send
  may start. Forged identities/tokens and unrelated calls are rejected.
- Keep started/in-flight/unknown-after-start charges, immutable Attempt history,
  all grants/reserves, cancellation/result fencing and default 1.0 unchanged.

## Offline Acceptance

Use temporary SQLite, fixture adapters and FakeRedis only:

- Strengthen the actual capability cancellation-before-start test with not_sent
  status, exactly one refund, zero consumed calls and no HTTP request.
- Root/depth-1/depth-2 reservations: cancellation requested, cancellation settled,
  lease replacement and deadline settlement retain the original refund scope.
- Confirm exact budget conservation and return of terminated-child unused funds.
- Reject started calls and forged identity/token settlement; retain unknown
  in-flight charges and prove idempotency and competing start/refund outcomes.
- Run the focused cases, all H2 cases, default finance regression and scoped lint.
  Real Redis and real-model results remain separate evidence.

## Verification

Before repair, the strengthened capability test and nine cancellation/deadline
cases reproduced the missing refund. The first three lease-replacement checks
used a status field absent from the public response; after correcting that test
to inspect the actual current Attempt, all three independently reproduced
stale_lease at refund. This is one accounting defect across lifecycle boundaries,
not 13 unrelated defects.

The first post-repair focused run passed 22 cases, including existing controls;
two terminated-ancestor release-order cases were then added. Complete regression
of the execution/foundation modules passed 256 cases with seven real-Redis skips
in 99.29 seconds. There are 22 net added cases plus the strengthened existing
capability case; one case includes 20 synchronized start/refund races. Scoped
Ruff passed. Full H2/state/compatibility/live-driver regression passed 558 cases
with seven real-Redis skips in 226.83 seconds. Default finance/shared-runtime
regression passed 1606 cases with 30 skips in 126.63 seconds (52 modules).
Configuration/separation passed 10/10. No paid request has started; the new
S01/S02 authorization still awaits the 120-call scope clarification. These
offline collections overlap and do not prove real-model quality or real Redis.
