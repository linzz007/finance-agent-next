# Harness 2.0 Live Acceptance R24

Date: 2026-10-21. Model: DeepSeek-V4.1-Flash (`deepseek-flash`). Backend:
FakeRedis. Automatic model fallback: disabled. Snapshot:
`data/acceptance/h2-baseline-r1-20261019`.

Runtime records:
`D:/AAAcode/code-code/agent+/harness/runtime-records/h2-dsml-live-recheck-20261021/`.

## Scope

This was a focused live recheck of two representative paths after the bounded
DeepSeek text-protocol adapter and the existing H2 recovery changes:

- Q04: deep analysis of one specified article.
- Q14: comparison of two authors, with one child task per author and Root
  synthesis after both results were available.

Each case had a per-run maximum of 120 physical model calls. No automatic
model switch, extra quota, or real Redis was used.

## Delivery results

| Case | Tasks | Model calls | Transport failures | Contract errors | Duration | Delivery |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Q04 | 1 | 17 | 0 | 1 | 219.70 s | succeeded / ready |
| Q14 | 3 | 36 | 0 | 3 | 356.57 s | succeeded / ready |
| Total | 4 | 53 | 0 | 4 | - | 2/2 delivered |

The `contract_error` count is an action-contract failure after a successful
provider response. It is not a transport failure and is not counted as a
failed physical model request in the low-level call status. All 53 physical
requests reached a terminal `succeeded` provider status; the four invalid
actions were recovered by the existing bounded correction/continuation path.

This gives a live delivery rate of 2/2 for this small sample, but it does not
prove a production success rate. The sample is too small and both cases use
FakeRedis.

## Runtime and delegation checks

### Q04

- The task was resumed through 15 `task.continued` transitions and 30 saved
  checkpoints.
- The one contract error did not terminate the task.
- The candidate result was accepted and the full final result was available to
  the user.

### Q14

- Root spawned exactly two child tasks.
- Both child tasks succeeded and Root waited for their results before final
  synthesis.
- Each child result was first inspected through a summary view and then read
  through a full view by Root:
  `res_f357679a6e264dfb96bb9d88bbd5fecc` and
  `res_497247fb65f147f589514b991bc8ec63`.
- Root then produced and accepted its own final result. The final user view is
  separate from the Root's internal result reads.
- Root used 18 calls, the TT child used 11, and the 培风客 child used 7.

These events confirm the current H2 behavior: one model action per durable
attempt, checkpoint persistence, re-queue continuation, explicit child tasks,
wait/wake, and result expansion on demand.

## Provider-output observations

The live responses contained two distinct classes of non-ideal output:

| Observation | Q04 | Q14 | Total |
| --- | ---: | ---: | ---: |
| `contract_error` | 1/17 | 3/36 | 4/53 |
| `json_action_boundary` with ignored trailing text | 5/17 | 8/36 | 13/53 |

The trailing text was ignored only after a complete first JSON action had been
identified. This is the bounded public-output normalization rule; the raw
provider output remains in the audit artifact. It did not execute the extra
text as a second action.

The new `tool.call` plus `name` DSML dialect was not emitted in this batch, so
the live batch does not validate that new adapter branch. Its parser contract
and rejection boundaries remain covered by the offline tests recorded in
[099](../changes/099-h2-deepseek-dsml-dialect-adapter.md).

## Answer-quality review

### Q04

The answer is useful for the requested deep read. It separates the author's
long-term framework, short-term causal hypothesis, evidence chain, hidden
assumptions, alternative explanations, and unresolved verification points. It
also preserves the author's hedges around the Jane Street explanation instead
of publishing that explanation as an established fact.

Residual risk: the answer is still a model-generated semantic analysis. Claims
about position size, losses, credit conditions, and market causality remain
author claims or hypotheses unless independently checked. The answer says so,
but this is not an automated semantic proof.

### Q14

The answer correctly reports the different article counts and the non-overlap
of the two date ranges. It distinguishes consensus, disagreement, silence,
coverage gaps, author claims, and unverified external facts. It does not force a
consensus where one author did not discuss the issue.

Residual risk: the comparison is still dependent on the model's interpretation
of the two child results. The trace proves both full results were read, not that
every semantic comparison is correct.

## Metadata and evidence status

Both cases have `finance_delivery.status=ready` and
`metadata_status=incomplete_metadata`. This did not block delivery, consistent
with the current design that missing optional state metadata must not discard a
complete answer. The runtime did not run a separate semantic review gate in
this batch; semantic acceptance remains `manual_review_required`.

This is therefore a successful runtime/delivery acceptance, not a claim that
all evidence-review metadata is complete or that all financial conclusions are
verified.

## Root-cause classification

| Finding | Classification | Action |
| --- | --- | --- |
| 2/2 cases delivered with no provider, quota, or transport failure | Current implementation works on these paths | Keep as baseline evidence; do not change code |
| 4 invalid actions from 53 successful provider responses | Model protocol adherence / model-output behavior | Keep bounded correction and audit; measure on a larger fixed sample before changing prompts |
| 13 responses had extra text after the first action | Model-output formatting behavior | Current bounded normalization handled it; do not execute trailing text |
| Q14 children were fully read by Root | Runtime behavior confirmed | No repair required |
| `metadata_status=incomplete_metadata` | Known non-blocking metadata boundary | Track separately; do not turn it into an output gate without a new design decision |
| FakeRedis only | Environment/acceptance gap | Real Redis validation remains open |
| New DSML dialect not hit live | Coverage gap, not a failure | Offline parser coverage is sufficient for this batch; run a live dialect check only if the provider actually emits it or a deterministic provider fixture is available |

## Decision

The H2 MVP is **operationally accepted for these two paths**:

- Root delivery: pass.
- Child delegation and result handoff: pass for Q14.
- Durable continuation and checkpointing: pass in both cases.
- Provider transport stability: pass for this batch.
- Provider action-contract stability: partial; 4 recoverable violations in 53
  calls.
- Financial semantic correctness: manual review required.
- Real Redis and production-scale concurrency: not tested.

No additional paid model run is justified immediately by this batch. The next
paid test should be chosen only to answer a specific remaining question, such
as a fixed repetition set for protocol-violation rate or a real-Redis-backed
recovery test; repeating Q04/Q14 without changing that question would not add
much evidence.
