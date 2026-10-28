# H2 Explicit Acceptance Call Ceiling

Date: 2026-10-19

Status: acceptance-driver preparation for the user's requested lower call cap.
At this preparation stage it did not authorize paid execution or decide whether
120 covered one case or the whole batch. The user has now confirmed 120 per
case / 240 total; the separately authorized R8 protocol is [043](043-h2-bounded-spawn-revalidation.md).

## Existing Boundary

[013 budgets](013-harness-v2-dispatch-baseline.md) and
[interface C14](../harness-v2/interface-contracts.md) require an explicit, conserved
whole-tree allowance, including research children, format correction and transport
retries. The finished [036/R7](036-h2-explicit-spawn-live-protocol.md) batch used
600 per case; its authorization and immutable outputs must not be reused.

The current run_h2_case driver always constructs SchedulerConfig with its
600-call default. A bounded new batch needs a supported, tested way to apply
the user's lower ceiling before accepting work, not just a statement in a report.

## Driver Contract

- Add optional max_model_calls to run_h2_case and --max-model-calls to the CLI.
  It is explicitly a per-case whole-tree cap, not a per-Agent allowance.
- Absent an override, preserve the existing 600-call driver behavior. An explicit
  value must be an integer above the unchanged Root closing reserve of 24 and
  at most the existing default of 600. Reject booleans, strings, floats, values
  invading that reserve and attempts to raise the default.
- Validate before creating output directories, databases, snapshots or model
  requests. Apply the exact value as SchedulerConfig.root_calls, so Root/child
  grants, corrections and retries all use the existing ledger and fences.
- Preserve 60-minute Run duration, 24-call/10-minute Root closing reserves,
  child grants, model binding, original analysis retention and no fallback.
  A ceiling is not guaranteed usable research calls or guaranteed delivery.
- Save the applied full scheduler configuration per case. The generic CLI
  manifest declares its per-case and batch upper bounds (cases x repetitions x
  per-case cap); it does not choose a split for the pending custom S01/S02 batch.
- A future once-only batch wrapper must freeze the approved exact allocation
  before execution. No probes, extra repetitions, carry-over or automatic reruns
  are authorized by adding this argument. Production defaults remain unchanged.

## Offline Acceptance

- Default and explicit valid caps preserve all other configuration fields.
- Invalid caps fail before output/corpus/adapter access, including CLI preflight.
- Run the actual driver on fixture HTTP with an explicit lower cap; assert that
  the model sees the exact grant and records/SQLite agree on the initial budget.
- Exercise real durable Root spawn/wait/return with fixture child correction,
  showing physical calls on both Tasks debit the same fixed tree grant.
- Retain bounded same-model transport-retry and Root-correction tests under an
  explicit lower cap; do not relabel fixture checks as paid acceptance.

## Verification

The driver and CLI implement the explicit lower ceiling. Focused driver checks:
37 passed in 20.29 seconds, comprising 20 net additions plus existing checks
now exercised with explicit caps. Fixture Root/child correction used five calls
against one 60-call grant; bounded transport recovery used two against that same
fixed grant. A 25-call case stopped research after one physical request, preserving
the unchanged 24-call closing reserve and exposing budget_denied instead of
pretending to deliver. This is not a change in closing/delivery policy.

Scoped Ruff passed. Full H2/state/compatibility/live-driver regression passed
578 cases with seven real-Redis skips in 222.69 seconds. Default finance runtime
code is unchanged and that collection was not repeated in this driver-only
change; its latest completed post-041 regression remains 1606 passed / 30 skipped.
Eleven relevant Markdown documents have 145 resolving local file links; this is
not a heading-anchor or whole-design consistency proof. No new paid calls have
started, and no S01/S02 allocation is frozen.
