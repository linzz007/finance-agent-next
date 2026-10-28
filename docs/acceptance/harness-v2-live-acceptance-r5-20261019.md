# H2 Live Acceptance R5

Date: 2026-10-19

## Frozen Batch

Actual entry: FinanceHarnessV2Service.for_finance_corpus. Explicit deepseek,
FakeRedis, one repetition per Q04/Q08/Q14, normal configured budgets and the
same hash-checked article snapshot as R4. No automatic model fallback, Docker
changes or live database migration. Private records are under the sibling
runtime-records/h2-live-r5-20261019 directory. All 352 frozen source hashes
matched after the batch; no source edits were made during execution.

| Case | Seconds | Physical calls | Tasks | Checkpoints | Delivered |
| --- | ---: | ---: | ---: | ---: | --- |
| Q04 | 54.03 | 5 | 5 | 4 | Yes, 2607 characters |
| Q08 | 94.13 | 11 | 8 | 7 | No: transport_error |
| Q14 | 315.59 | 34 | 34 | 33 | No: transport_error |

Delivery is 1/3, not overall acceptance. R4 remains unchanged at 0/3. The
pre-batch offline regression was 242 passed / 7 skipped; shared finance was
1606 passed / 30 skipped. FakeRedis is not real Redis or load evidence.

## What Improved

- Q04 located the requested article and read all 63 lines. It answered the
  requested judgment, evidence, assumptions and remaining-check questions.
- Q08 had three separate malformed public responses. Each was corrected once
  within its own Root decision, on the same model and original budget. Both
  physical calls were charged; successful corrections did not execute the
  fabricated observations appended to the rejected outputs.
- Tool descriptions were obtained on demand (Q04: 2, Q08: 2, Q14: 6).
- Q14 reached both authors and continued after a failed tool child. No analyst,
  verifier or skeptic model delegation occurred in this batch; article-tool
  children are not evidence of successful research-subagent inference.

## Content And Trace Review

Q04: content useful but only partial evidence/trace acceptance. It distinguishes
author hypotheses from independent verification, but attributes additional
long-term macro assumptions to the author without support in this article.
Its final state records complete reading and actual source use, yet review is
null and coverage is empty. No structured verification completion is proved.
Do not solve this by suppressing its useful answer or requiring sentence-level
review; the approved design calls for model-chosen targets and disclosed gaps.

Q08: no final answer, so no semantic pass. It made four distinct article.read
requests before the last model call failed without completed public output.
The exported error is transport_error; it does not retain enough diagnostic
detail to distinguish the exact remote/network/framing cause. Do not claim a
specific provider outage. Physical call outcome is unknown and was not refunded.

Q14: no final answer. There were 19 article.read requests but only 13 distinct
parameter sets, including six repeated requests. The trace proves repetition,
not that every repeat was unnecessary. Code inspection shows that Root retains
only the last 12 materials and lacks a model-facing result.get path, so earlier
results are not reliably available through the approved progressive interface.
That is a concrete integration gap, not proof the model alone caused repetition.

## Confirmed Implementation Defect

Q08 failed at Root Attempt 8 and Q14 at Attempt 34. Before either failure, Root
had only normal suspended/resumed Attempts, not failed transport retries.
Repository.fail nevertheless compares attempt_no against max_attempts=5, so
ordinary progress exhausts the failure-retry allowance. Lease recovery has the
same comparison. Correct this under existing bounded recovery semantics;
do not increase research budgets or automatically switch models. See change 031.

## Remaining Baseline Scope

- Actual finance integration still immediately waits after a single child;
  generic scheduler parallelism/two-level recursion does not prove the financial
  agents can use them. Research children are still one-shot, without nested
  action loops and with empty role tool permissions.
- Finance domain bypasses the business tool-event path, auto-fetches only the
  first long-result page, and persists full observation payloads in compact
  state. These differ from the approved handoff/observability boundaries.
- H2 exposes article tools only; web/market/Wiki business cases cannot yet be
  passed honestly through this composition. Financial prompt parity, research
  clock and structured review/coverage remain incomplete.
- Real Redis, abrupt multiprocess failures, sustained load and the other 27
  business queries remain unverified. Default 1.0 is unchanged.

Do not mark the active baseline objective complete. Further integration needs
an explicit contract-to-code mapping, not changes to old acceptance results or
paid reruns until a favorable answer appears.
