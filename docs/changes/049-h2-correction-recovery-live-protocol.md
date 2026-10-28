# H2 R10 Correction Recovery Live Protocol

Date: 2026-10-19. Status: completed; results recorded in the R10 acceptance report.

The user authorized necessary paid tests without repeated per-batch questions
after discussing R9's correction-transport failure. This fixed R10 batch tests
[047](047-h2-follow-up-context-handoff.md) and
[048](048-h2-correction-transport-retry.md) without rewriting R9 or retrying it.

## Frozen Scope

- Exact S01/S02 queries and the original article/company snapshot from
  [046/R9](046-h2-post-feedback-live-protocol.md), each once on DeepSeek only.
- Explicit FinanceHarnessV2Service.for_finance_corpus path, FakeRedis backend.
  Real Redis remains deferred. No Docker or production/default-1.0 changes.
- 120 physical calls per whole tree, at most 240 for the batch; original
  60-minute deadline and Root 24-call/10-minute closing reserve. Children,
  corrections and transport retries all count. No model switch or top-up.
- Freeze source/test/design, the private protocol/wrapper and original source
  snapshot before execution. Compare before the next case and at completion.
  No source edits during the batch; refuse any pre-existing output directory.
- Private paths are sibling runtime-records/h2-spawn-r10-protocol,
  h2-spawn-r10-20261019 and h2-spawn-r10-audit. Preserve all earlier batches.
- No extra paid probes, automatic case repetition or success-only reporting.
  Run both cases once unless integrity/accounting checks require stopping.

## Acceptance

Retain the separate scheduling, full-result consumption, content, accounting
and integrity criteria of 046. S02 must dispatch both authors before waiting,
actually consume both complete child analyses, and deliver a source-grounded
comparison with sample/date/condition disclosure. User exports are not Agent
reads. One Worker is not evidence of parallel throughput.

Inspect 048 only if triggered: one semantic correction, at most one retransmit
of identical correction messages on transport_error, distinct charged calls,
no fourth request or restart of the original decision. A path not exercised by
the model remains live-unproved even when offline fault tests pass. Likewise,
047's terminal follow-up flow is not forced into these unchanged two Queries.

Review full answers against actual source content. Do not equate accepted
EvidenceRefs, delivered text or matching model output with verified financial
facts. Preserve useful analysis and original failure records. Two cases do not
prove commercial reliability, full business-tool parity or full acceptance.

## Prerequisites And Results

Complete H2 regression, default-finance compatibility, configuration isolation,
Ruff and zero-request preflight before starting. Record all outcomes separately
in the R10 acceptance report after completion, with actual physical calls and
remaining gaps. The general testing authorization is not an infinite retry loop.

The fixed batch completed once per case. S01 used 16 physical calls in 264.72
seconds and delivered a 3704-character answer. S02 used 37 physical calls in
763.82 seconds and delivered a 3323-character answer. Both cases used DeepSeek
only, FakeRedis, and stayed within the 120-call-per-tree / 240-call-batch caps.
The transport-retry branch was not triggered in either case; the audit status is
`not_triggered`, not live proof of recovery.
