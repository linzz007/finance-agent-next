# H2 Bounded Correction Transport Retry

Date: 2026-10-19; evidence updated 2026-10-20. Status: implemented;
offline-verified; one live recovery observed in R14 Q28.

## Decision And Authority

R9 S02 failed when the single format correction encountered transport_error.
The prior [030](030-h2-action-contract-handoff.md) /
[037](037-h2-child-action-envelope-and-budget-errors.md) rule deliberately ended
the decision at that boundary. That behavior was correct for that version.

The user was presented with a narrow proposed change: retransmit only that
same correction once, on the same model and original budget/deadline, with at
most three physical requests per decision and possible duplicate remote charges.
The user then instructed the agent to arrange necessary paid tests without
asking for each execution and to solve the problem. This iteration applies
that narrow recovery design, not a general retry/fallback expansion. The new
test authorization supersedes the older pending-authorization notes, not the
existing 120-call-per-tree and 240-call-batch safety ceilings.

## Behavior

1. Make the original decision using the existing single-request capability.
2. Only a saved complete public output rejected as contract_error can start
   the already approved one semantic format/anchor correction.
3. If that correction fails with transport_error, persist an audit record of
   its call/output handles and retransmit exactly the same correction messages
   once. This is a distinct capability.complete call with its own reservation,
   physical call ID, original model-policy checks and charge.
4. Do not resend the original decision, append new research or execute an
   unvalidated partial action. Keep the original failed text and diagnostics.
5. Any error from the final transmission is terminal for that decision, with
   retryable=false so the Worker cannot restart the entire sequence. A second
   contract_error never gets another semantic correction.
6. No retry for quota/provider/permission/budget/deadline/cancellation failures,
   timeout, persistence failure or an initial nonformat failure. No new model,
   grant, deadline, hidden SDK retry, refund of unknown started calls or production
   1.0 change. At most three physical requests, often fewer or none if fenced.

The single semantic correction limit stays one. The transport-retry limit is
one and applies only to its first failed transmission. Both Root and research
roles use the same implementation. Previous accepted tool effects are never
replayed to manufacture a successful run.

## Audit And Acceptance

Record the failed correction call/output and original repair request in a
finance.h2.action_repair_transport.v1 Artifact. Link it from the final repair
record on success; retain it and all call records on failure. Do not log raw
credentials or private reasoning.

Offline checks must cover Root/child success, identical correction messages,
terminal third errors, nontransport exclusions, no fourth call, exact ledger
charges and no replay of the original decision. The actual capability retains
authority to refuse the third call for budget, lease, policy or deadline reasons.

The offline suite covers these cases. R10 used 53 DeepSeek calls across S01/S02
and exercised no three-call correction attempt, so its live audit records the
transport-retry branch as `not_triggered`; it does not turn the offline result
into live provider/network evidence.

[R14](../acceptance/harness-v2-live-acceptance-r14-20261020.md) subsequently
exercises this branch in Q28: an original contract_error, a correction
transport_error with unknown remote outcome, then a successful correction in
the same Attempt. Three distinct requests remain charged, and the linked
finance.h2.action_repair_transport.v1 record is present. This is one live
recovery, not a measured commercial success rate or saved-wire equality proof.
Exact correction-message reuse and no-fourth-call behavior retain their
separate deterministic offline evidence. R10's not_triggered result is unchanged.

After offline regressions, freeze a separate bounded S01/S02 live protocol and
snapshot. Run each once on DeepSeek with 120 calls/tree, 240/batch and 60 minutes
per Run; no fallback or additional unrecorded probes. Preserve R7/R8/R9. A new
batch can demonstrate progress, not commercial reliability or real Redis.
