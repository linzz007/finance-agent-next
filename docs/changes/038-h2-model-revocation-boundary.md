# H2 Model Revocation At The Send Boundary

Date: 2026-10-19

Status: existing-contract implementation audit and regression work. This does
not authorize paid calls, a new model, larger budgets or a production switch.

## Authority And Observed Gap

[013 section 10](013-harness-v2-dispatch-baseline.md) and
[interface sections 2.2-2.3 and 8](../harness-v2/interface-contracts.md) require
current user model permissions to stop new requests at the next call boundary,
immutable Attempt snapshots, truthful permission failures and no automatic
fallback. An already authorized in-flight request is not guaranteed cancellation
or a refund. [T04](../harness-v2/sqlite-schema.md) distinguishes a reservation
from a physically started request; confirmed unsent reservations may be refunded.

Code inspection found two missing parts of these existing rules:

1. reserve_call checks the current allowed profiles, but call_started checks
   only lease/state. Revocation committed after reservation can therefore pass
   the final persisted pre-send boundary unchecked.
2. ExecutionCapabilities already raises forbidden on an unauthorized model
   reservation. Worker does not preserve that known error and Repository.fail
   does not accept it, so parents see internal_error instead of permission denial.

These are implementation defects, not a request for Runtime to select a
different model, retry a revoked model or decide financial research policy.

## Repair Boundary

- In the same transaction as reserved -> started, recheck the Attempt's original
  model profile against the Run's current allowed list. A revoked profile raises
  forbidden without a physical request or a replacement snapshot.
- Keep reservation and send accounting separate. If this final check refuses a
  reserved but unsent call, use the existing idempotent not_sent refund path;
  do not refund a started/completed call or relabel it as unsent.
- Persist forbidden as a terminal execution error visible in status/wait/events.
  It is not a transport retry and does not create another Attempt or model call.
- A change in the default model that still permits the Attempt's original model
  must not silently replace that snapshot or block an otherwise authorized call.
- Once call_started commits, the request is treated as authorized in-flight.
  A later policy change cannot promise remote cancellation or erase its charge;
  the next request must be refused if the original profile is now revoked.
- Preserve default 1.0, all budgets, existing repair limits, public JSON and
  user-driven resume/model selection. No DDL change or Docker operation.

## Offline Acceptance

Use temporary SQLite, FakeRedis and httpx.MockTransport only:

- Root and child execution: revoke before reservation, after reservation/before
  start, and after one physically completed call. Assert the correct forbidden
  cause, no fallback or automatic retry, and exact call/refund counts.
- Parents observe a failed child's real permission error, not an internal outage.
- Assert the in-flight original call remains charged, and an original profile
  still allowed after a default selection change remains the executing model.
- Retain unknown-error classification as an independent control. Run H2,
  default-finance regression and separation checks after the focused cases.

Live-model and real-Redis validation remain separate and unproved. Preserve all
frozen R7 outcomes; these offline checks are not a new paid acceptance batch.

## Verification

Before production changes, six Root/child revocation cases failed and two
default-selection controls passed. A focused check separately confirmed both
post-reservation cases sent one mock HTTP request instead of zero. No real
provider was contacted. After repair, the execution module passed 93 cases;
the final collection after 038-040 passed 536 cases with seven real-Redis skips
in 206.81 seconds. Default finance/shared-runtime passed 1606 cases with 30 skips
in 123.39 seconds. All 12 added revocation/accounting cases are included in the
536, not independent live-model evidence. Configuration/separation was 10/10.
