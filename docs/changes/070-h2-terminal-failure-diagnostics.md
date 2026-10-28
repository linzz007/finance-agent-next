# H2 Terminal Failure Diagnostic Handoff

Date: 2026-10-20

Status: existing-contract repair; no recovery-policy change.

## Evidence And Authority

[R19](../acceptance/harness-v2-live-acceptance-r19-20261020.md) saved a child
correction that failed final.output validation because evidence-link text did
not occur verbatim in its analysis. FinanceH2Domain raised a DispatchError with
validation_stage/errors, but Worker passed only its code to Repository.fail.
The parent consequently received contract_error without its field diagnostic.

[030](030-h2-action-contract-handoff.md),
[037](037-h2-child-action-envelope-and-budget-errors.md) and
[interface section 8](../harness-v2/interface-contracts.md) already require a
truthful, sanitized failure handoff. This repairs that omission, not the failed
child analysis or the original R19 evidence.

## Persistence And Interfaces

- Worker forwards structured DispatchError details, never arbitrary exception
  text. Repository applies the diagnostic allowlist before persistence.
- The existing h2_attempts.error_message stores a fixed public description for
  the normalized error code. No base DDL rewrite or new migration is needed.
- The existing task.failed event additionally records the actual attempt_id,
  error_code and fixed message. Only contract_error with a known validation
  stage (action or final.output) can include validation_stage/validation_errors.
  Errors contain only loc/type/msg, at most 12 items with the existing field
  bounds. Mask credential patterns, Bearer values and URLs. Exclude rejected
  input/context, raw output, arbitrary exception messages and artifact refs.
- agent.status and agent.wait expose error_message in each Attempt and the
  current terminal Task's optional failure_diagnostic from its persisted failed
  event, independently of the requested event cursor. Match the current Attempt;
  do not attach a historical failure after resumption or a later successful Task.
- Finance context retains failure_diagnostic as diagnostic data. It neither
  grants access to failed output nor creates an accepted ResultRef or evidence.
  Normal access checks and lease fences precede every write/read.

Retry eligibility, number of corrections, budgets, model choice, failure states,
result validation and default 1.0 behavior remain as previously approved. The
parent decides what to do with the failure; Runtime does not invent a new task.

## Acceptance

1. Two invalid child candidates still terminate after the existing correction;
   parent model input includes final.output and the verbatim-anchor reason.
2. status/wait expose the same diagnostic after Repository recreation and after
   the failure event is outside the requested event page. No accepted child
   result, extra physical request, replay or refund appears.
3. Unknown exceptions/fields and rejected values do not enter public errors.
   Bounds, credential/URL masking, cross-run access and stale-lease rejection
   hold; resumption cannot attach the previous failure to a new Attempt.
4. Run focused tests and the H2 regression; report offline evidence separately
   from live model quality and the still-deferred real Redis validation.

## Results

Implemented the allowlisted Worker -> Repository -> status/wait -> finance
context handoff, using existing Attempt/message and event storage. Shared
validation diagnostics were extracted without changing the adapter's default
behavior; terminal publication additionally masks URLs/Bearer values.

- Fifteen new boundary tests pass; nine initially reproduced the missing
  interfaces/fields before implementation. Test setup mistakes (processing the
  Root rather than the intended child, lease field name, and pending_dispatch
  versus queued) were corrected separately, not treated as Runtime defects.
- The actual child adapter/SQLite/FakeRedis integration now asserts that two bad
  candidates retain the existing call count/no accepted result, while the parent
  model receives final.output and the verbatim-anchor reason. Fifteen existing
  context variants also retain the diagnostic through persistence and model input.
- Final H2/state/compatibility/live-driver: **916 passed / 7 skipped**,
  430.50 seconds. Default finance/shared-runtime: **1638 passed / 30 skipped**,
  192.21 seconds across 52 modules. Configuration/separation: **10/10**.
- Full scoped Ruff and tracked diff checks pass. Existing dependency deprecation
  warnings remain. No live DB migration, default-path switch or Git operation.

These are offline results. R20 under the separate once-only protocol
[071](071-h2-terminal-diagnostic-live-protocol.md) completes a delegated six-article
read/return, but its child recovers and does not trigger the terminal-failure
handoff live. See the [R20 report](../acceptance/harness-v2-live-acceptance-r20-20261020.md).
Neither result retroactively repairs R19 or certifies real Redis, financial
semantics or commercial reliability.
