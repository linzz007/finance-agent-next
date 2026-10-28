# Harness 2.0 Live Acceptance R11

Date: 2026-10-19  
Scope: S02 repair revalidation only  
Model: DeepSeek only  
Transport backend: FakeRedis  
Runtime path: `FinanceHarnessV2Service.for_finance_corpus`

## Result

R11 reran the two-author S02 comparison after the bounded correction-recovery
changes. The Root task and both explicitly spawned analyst tasks succeeded. The
Root delivered a 4,025-character answer after 27 physical model calls in
401.2 seconds. No model fallback, budget top-up, unknown call, pending Outbox,
or real Redis verification was used.

This is a successful targeted scheduling and delivery revalidation. It is not
proof of stable raw model contract compliance, semantic financial correctness,
commercial reliability, or the full 30-Query suite.

| Dimension | Result |
| --- | --- |
| Root delivery | pass; `succeeded`, 4,025 characters |
| Analyst Tasks | pass; 2/2 succeeded |
| Explicit separate spawn | pass; 2 child Tasks created before the synthesis wait |
| Complete child result reads | pass; both child `result.full_viewed` events precede Root delivery |
| Model calls | 27 physical calls; 120-call per-tree ceiling respected |
| Contract recovery | pass; 4 Root contract errors recovered by the permitted single semantic correction |
| Correction transport retransmit | not triggered; no three-call correction sequence occurred |
| Model fallback | not used; DeepSeek remained the only selected profile |
| Real Redis | not run; FakeRedis only |
| Semantic acceptance | manual review required |

## Trace And Scheduling

- Root planned the comparison and selected one September gold article for each
  author.
- Root created two independent `agent.spawn` Tasks with explicit budgets and
  requested the second spawn before waiting for the first result.
- Both children read their complete short article and returned an accepted
  source-grounded analysis.
- Root waited, retrieved both results, recorded full-result reads for both
  child result references, and only then produced the final synthesis.
- The durable loop created 36 checkpoints and included a wait/wake transition.
  The run used one Worker, so this proves dispatch-before-wait and resumability,
  not actual parallel throughput.

## Contract Recovery

The live model produced four invalid Root decisions. The observed failures were
model-output contract problems, including provider markup, an unsupported
action shape, and an EvidenceRef binding problem. Each was preserved in the
Trace and repaired once using the same model and the original budget/deadline.
No third semantic correction was permitted. The transport-retransmit branch
from [048](../changes/048-h2-correction-transport-retry.md) was not reached;
its status for this run is `not_triggered`, not live evidence of network
recovery.

This distinction matters: the runtime recovered this run within its bounded
policy, but the model's first-response contract adherence was 23 Root calls
with 4 contract errors, not 100 percent. The result should therefore be
treated as recoverable rather than intrinsically stable.

## Accounting And Integrity

The run record reports three Tasks, 27 successful physical model calls, zero
failed or unknown calls, zero pending Outbox entries, and no automatic model
switch. The run stayed inside the existing 120-call whole-tree ceiling. The
private record is preserved at:

`D:\AAAcode\code-code\agent+\harness\runtime-records\h2-spawn-s02-r11-20261019`

The exported event stream contains the two `task.spawned` events, two child
`task.succeeded`/`result.accepted` pairs, two Root-side child
`result.full_viewed` events, and the final Root `result.accepted` event. Real
Redis, multi-process workers, and sustained-load behavior remain untested.

## Content Review

The delivered answer is materially usable for the requested comparison. It
discloses the one-article-per-author sample, preserves dates and conditions,
separates common views from different reasoning, and states that agreement is
not external fact verification. It also records that the two articles do not
represent either author's complete history.

The answer still has `semantic_acceptance=manual_review_required` and
`state_metadata_status=incomplete_metadata`. Evidence binding and full reads
show where the answer came from; they do not prove that each financial claim
is true. A human review is still needed for overstrong summaries, article
interpretation, and whether the cited passages support the exact wording.

## Conclusion

R11 passes the targeted H2 properties for explicit delegation, durable
wait/resume, complete child-result retrieval, bounded correction recovery,
accounting, and final delivery. It partially demonstrates that the system can
degrade and recover when DeepSeek emits invalid actions. It does not close the
remaining issues of raw model compliance, live transport retry, real Redis,
semantic review, full business-tool parity, or production reliability.
