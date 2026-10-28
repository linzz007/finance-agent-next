# H2 Action Validation Feedback

Date: 2026-10-19

Status: existing-contract repair after the frozen [R8 batch](../acceptance/harness-v2-live-acceptance-r8-20261019.md).
Authority: [030](030-h2-action-contract-handoff.md) requires exact public-action
guidance and useful failure feedback for the single approved correction;
[037](037-h2-child-action-envelope-and-budget-errors.md) retains strict validation,
same-model correction and original budgets. No new retry or acceptance policy.

## Confirmed Defect

Both Root wait decisions kept an unsupported top-level next_action_key after
correction. Input guidance calls it a scheduling key without stating its allowed
location. Adapter validation discards ValidationError details and the trusted
capability passes only call/output references to the correction handler. Thus
the handler's existing validation_errors field is empty for outer action errors,
although the exact validator can identify extra_forbidden and its field path.

## Repair Boundary

1. Clarify next_action_key as input-only. Use its value only for the dispatch_key
   or idempotency_key argument required by the selected tool Schema, never as
   a top-level action field or state_update field. Tools without that parameter
   do not receive one. Article IDs are not input_refs: spawn uses granted artifact
   references, for example known_material_refs, without restricting other already
   authorized artifacts. Keep the initial capability catalog short.
2. At actual adapter ValidationError, attach a bounded diagnostic with stage
   action and field loc/type/msg only. Exclude input/context/error URLs and raw
   provider exception text. Redact credentials and bound diagnostic strings/counts.
   Retain the original public output artifact as diagnostic data, never execute it.
3. Carry only those allowlisted diagnostics through ExecutionCapabilities to
   the existing DispatchError.details and format-correction artifact/prompt.
   Do not copy arbitrary adapter event fields or introduce privileged data.
4. Preserve one correction, same model, same grant, all physical charges,
   strict output validation, unknown-field rejection and optional final-delta
   retention. No native Function Calling, output auto-rewriting, extra tools,
   provider/model fallback, new financial gate or default-1.0 modification.

The failed child's verbatim-anchor policy and raw-body delivery on Root failure
remain separate design boundaries. Do not weaken them while repairing feedback.

## Offline Acceptance

- Actual adapter events retain the exact extra_forbidden path with no input,
  context, credential or private reasoning. Cover both configured text protocols
  and streaming/non-streaming paths; one request per adapter call remains true.
- Trusted capability passes bounded allowed diagnostics and ignores unrelated
  event fields; charges both invalid and corrected requests normally.
- Actual Root correction receives the field error and corrects the fixture wait
  action without increasing its two-call limit. Two invalid outputs still stop.
- Initial Root guidance states the key's argument-only placement and artifact-
  versus-article identity distinction, without eagerly adding full tool Schemas.
- Run focused checks, full H2 regression, compatibility checks and scoped lint.
  No further paid calls. R8 remains a failed frozen batch after offline repair.

## Verification

Full dispatch/state/compatibility/live-driver regression: **587 passed / 7
skipped**, 210.12 seconds. Nine net additions cover four transport combinations,
bounded/redacted field errors, capability allowlisting, successful/failed actual
Root wait-action correction and concise initial guidance. The initial focused
run exposed one newly added fixture missing the required FinanceRootState.phase;
that fixture was corrected before this full passing run. It was not a live
model failure or a change in state requirements.

Configuration/separation checks passed 10/10 with zero model calls. Scoped Ruff
and git diff --check passed; fourteen relevant Markdown documents have 166
resolving local file links (not an anchor/semantic consistency certificate).
Seven real-Redis scenarios remain skipped and the existing Starlette/httpx
warning remains. No Docker, paid rerun, live migration, Git commit or push.

Final default finance/shared-runtime compatibility regression: **1606 passed /
30 skipped**, 115.57 seconds, 52 modules. This is a new completed post-044
execution, separate from the earlier post-041 result. The entire inherited
repository suite was not rerun; prior unrelated failures remain outside this
claim. Both regression collections made zero paid model calls.
