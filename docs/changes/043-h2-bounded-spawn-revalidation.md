# H2 Bounded Spawn Revalidation Protocol

Date: 2026-10-19

Status: the user resolved the earlier budget question: **120 physical model
calls per case, 240 maximum for the two-case batch**. S01 and S02 are each
authorized once on DeepSeek. This supersedes the pending clarification in 042;
it does not renew the finished 036/R7 authorization or change production defaults.

## Frozen Scope

- Use the explicit FinanceHarnessV2Service.for_finance_corpus entry and the
  tested run_h2_case(max_model_calls=120) driver from [042](042-h2-acceptance-call-ceiling.md).
  Keep the exact S01/S02 Queries and original article/company snapshot used in
  [036](036-h2-explicit-spawn-live-protocol.md). These are two custom scheduling
  cases, not the full 30-Query business acceptance catalog.
- S01: one dated article, explicit analyst spawn, original-source handoff,
  full returned-analysis consumption, reasoning/conditions/gaps and full delivery.
- S02: separate analyst Tasks for two authors, both spawned before the first
  wait, original-source handoff, full returned analyses and source-bound synthesis.
  Disclose actual sample, dates, uncertainty and failures. Agreement is not
  independent fact verification.
- Selected and allowed profile: deepseek only. No automatic model/provider
  switch, extra paid probe, additional repetition or post-failure paid rerun.
  Same-model correction/recovery follows existing contracts; every physical
  request, including children, format corrections and transport retries, is
  charged against its case's single 120-call tree grant. No carry-over between
  cases, hidden grant increase or free-looking retry.
- Keep Run duration 3600000 ms, Root reserve 24 calls/600000 ms and original
  deadline inheritance. Root chooses explicit child grants within remaining
  funds. A ceiling is not guaranteed usable research calls or guaranteed delivery.
- Backend: FakeRedis, explicitly labeled. Real Redis remains user-deferred;
  no Docker operations, production workers, live DB migration or default-1.0 switch.
- New private protocol/output directories under sibling runtime-records. Refuse
  an existing output directory. Preserve R7 protocol, answers and records.
  Freeze code/design/test/private-protocol hashes before paid execution; verify
  them before the next case and after completion. Snapshot article bodies,
  credentials and traces never enter public documents or Git.
- Run the two cases sequentially once each, including recording a failed first
  case. A host crash is not authorization to restart a partially charged case.
  Stop the batch if integrity/accounting checks fail; do not replay observations.
  Confirm each applied cap in stored SchedulerConfig and count physical started
  calls from its SQLite ledger; assert <=120 per case and <=240 in total.

## Acceptance And Reporting

Apply the five layers in [036](036-h2-explicit-spawn-live-protocol.md): actual
scheduling, handoff/return, conserved accounting, manually reviewed full content,
and frozen-source integrity. Specifically inspect post-R7 child correction,
cross-Attempt result reads and model-selected expansion from 037-042.

Short-result injection counts only when the full body was actually admitted;
exporter/user reads after termination never prove Root read a child result.
Keep delivery, child success, semantic review and evidence coverage separate.
Review the saved answer against original passages for author/date confusion,
strengthened conditions, unsupported consensus and inaccurate sample disclosure.

No automatic semantic scoring or new financial gate. Retain useful full output
on partial/failed review. Two cases cannot establish statistical reliability,
commercial readiness, successful depth-2 live recursion or full business coverage.
Document implementation deviations before fixing; new design choices remain
the user's decision. No further paid rerun is authorized by a repair.

## Pre-Execution Evidence

Post-042 H2/state/compatibility/live-driver regression: 578 passed / 7 skipped,
222.69 seconds. Focused driver checks: 37 passed. Latest actual default finance
regression remains post-041: 1606 passed / 30 skipped, 126.63 seconds. This
protocol changes no finance runtime code. The private R8 wrapper must pass
offline scope/config/snapshot preflight before its once-only --live execution.

Preflight completed with zero physical model calls: exact original Queries,
120/240 limits, unchanged reserves/deadline and source snapshot validated.
The private wrapper passed Ruff; the actual driver test module passed all
37 cases in 21.63 seconds. git diff --check passed (existing CRLF notices only).
These are execution preparation, not live results or a new full-suite regression.

## Completed Once-Only Batch

R8 completed S01/S02 once each. Actual calls were 17/36, 53 total; both Root
deliveries failed. Two of four spawned children returned accepted corrected
analyses; one failed anchors and one was cancelled after Root failure. All 385
frozen source/test/design files, snapshot and private protocol matched at batch
completion. See [R8](../acceptance/harness-v2-live-acceptance-r8-20261019.md) for
attribution and accounting. Existing-contract feedback repairs in [044](044-h2-action-validation-feedback.md)
are post-batch offline work, not paid revalidation. This authorization is spent.
