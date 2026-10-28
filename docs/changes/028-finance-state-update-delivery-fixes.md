# H2 State Update Delivery And Handoff Fixes

Date: 2026-10-19

Status: implemented and offline-verified; no live-model or real-Redis claim.

Authority: approved `finance.state_update.v1`, documented in
[026](026-finance-state-update-protocol.md), sections 4 and 5. This change does
not select a new business strategy or modify the default 1.0 path.

## Defects

1. Both `GatewayModelAdapter` and `FinanceH2Domain` validate the whole action,
   including `state_update`, before accepting a final answer. A valid public
   final body with a structurally invalid optional delta can therefore fail
   before the candidate is saved. The approved protocol requires retaining the
   body and delivering it as partial with incomplete metadata.
2. Root Checkpoints contain review and delivery state, but the next model input
   exposes plan, articles, evidence and coverage without review/delivery. The
   resumed decision cannot inspect those two parts of its durable working state.

## Required Repair

- A final action with invalid `state_update` is recoverable only when the entire
  remaining public action validates after removing that one optional field.
  Malformed JSON, truncated responses, invalid output, other unknown action
  fields, and invalid non-final actions remain contract errors.
- The single-request adapter must not discard that valid public final body;
  the Root records `state_update_invalid`, applies no invalid state, preserves
  earlier observations, and submits a partial result with incomplete metadata.
- Root input includes its compact persisted review and delivery state, alongside
  the existing plan/articles/evidence/coverage. No full article or transcript is
  copied into those fields.
- No automatic model retry, fallback, tool argument alias, or permission
  relaxation is introduced.

## Verification

Use the actual opt-in `FinanceHarnessV2Service`, `GatewayModelAdapter` with
`httpx.MockTransport`, temporary SQLite and FakeRedis. Check a complete delta
through list/read/resume/final, retained earlier state after invalid terminal
deltas, unchanged full final body, real observed references, and explicit
partial/incomplete metadata. Keep strict invalid-action tests and legacy
compatibility tests. No paid model calls are needed.

The final H2 collection passed 224 cases with 7 real-service variants skipped.
The finance/shared Runtime baseline passed 1606 cases with 30 existing skips.
Detailed scope, initial failing reproductions, and remaining validation are in
[R3 acceptance](../acceptance/harness-v2-fault-acceptance-r3-20261019.md).
