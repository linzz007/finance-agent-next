# H2 R7 Explicit Spawn Acceptance

Date: 2026-10-19

Protocol: [036](../changes/036-h2-explicit-spawn-live-protocol.md). Two custom
targeted Queries, once each, DeepSeek only, explicit H2 corpus service and
FakeRedis. This is not the full business catalog, real Redis validation or a
statistical reliability estimate.

## Outcome

**Both Root answers were delivered; neither case passed the requested complete
child-analysis/return/synthesis chain.** Five child Tasks were actually spawned;
none returned an accepted ResultRef. Root disclosed the failures and completed
its own analysis. Delivery did not conceal that missing independent analysis.

| Case | Duration | Physical calls | Tasks | Child results | Answer characters |
| --- | --- | --- | --- | --- | --- |
| S01: single-article delegation | 419.87 s | 33 | Root + 3 children | 0/3 | 3973 |
| S02: independent author analyses and comparison | 258.45 s | 23 | Root + 2 children | 0/2 | 3251 |

Each case retained the configured 600-call/60-minute whole-tree cap and Root's
24-call/10-minute closing reserve. Actual charges were 33 and 23, not 600 each.
No model fallback, paid probes or post-repair paid reruns were performed.

## Trace Acceptance

| Boundary | S01 | S02 |
| --- | --- | --- |
| Explicit spawn rather than hidden legacy delegate | Observed, three actual child Tasks/Threads | Observed, two actual child Tasks/Threads |
| Independent dispatch before waiting for first result | Not the single-article objective | Observed: both spawn events precede Root's first wait |
| Durable action checkpoints and continuation | 50 checkpoints; real wait/wake observed | 32 checkpoints; real wait/wake observed |
| Source handoff and reads | Dated original source available; Root read full 63 lines | TT original body and comparison-author metadata/body granted; the second child completed a full 47-line article read |
| Child termination truthfully observed | Three contract_error failures; invalid resume refused | contract_error and an incorrectly classified internal_error |
| Independent child analysis accepted and retrieved | **Fail: none existed** | **Fail: none existed** |
| Full Root answer retained and delivered | Yes, with limitations | Yes, with limitations |
| Semantic review/coverage metadata | Incomplete, no structured review | Incomplete; sample metadata disagrees with actual sources |

One test Worker proves non-blocking lifecycle progress, not simultaneous model
execution or throughput. Exporter-generated full-result reads are reads of the
Root answer after termination, with reader_attempt_id absent. They are **not**
proof that Root retrieved a successful child's analysis.

## Failure Attribution

### Four Child Contract Failures

- S01 child 1 and 2 returned malformed provider-specific tool markup instead of
  a supported public action. They were rejected before tool execution.
- S01 child 3 and S02's TT child produced substantial analysis as bare
  finance.research_result.v1 objects. Both lacked the outer final action.
  Independent strict validation also rejected their evidence-link anchors:
  some anchor text did not occur verbatim in content/candidate_answer.
  Adding an outer wrapper alone would not have made these results valid.
- The child prompt replaced Root's prompt without copying its clear literal
  JSON examples. It supplied a ResearchResult Schema without explicitly marking
  its scope as final.output. This is a demonstrated contract-guidance omission,
  not proof that it alone caused all model failures or that prompt changes
  guarantee compliance.
- At the R7 source freeze, only Root was authorized to request one format
  correction. Children correctly had no such exception then. The subsequently
  approved child correction must not be counted as having run in R7.

### Budget Exhaustion Misclassified

S02's other child received exactly two calls/180000 ms, chosen by Root. It
spent them on tool.describe and article.read. There was no call left to produce
a final analysis. The next reservation was refused before HTTP dispatch, but
Worker mapped budget_denied to internal_error. This was reproduced offline.

The classification is a code defect. The model's undersized grant is a separate
planning observation. Runtime must not silently enlarge it. Automatic minimum
grants, child call reserves or early-finalization policy were not selected here.

## Answer Quality Review

### S01

The retained answer substantially addresses the question: it reconstructs the
author's reasoning, assumptions, missing causal evidence and limits of a single
article. It explicitly distinguishes author claims, its own critique and absent
external verification, and preserves uncertainty in the final conclusion.

It remains a Root-only analysis, not the requested completed independent child
analysis. A few stronger intermediate paraphrases should be read alongside the
answer's later conditional framing; this is not certified financial correctness.

### S02

Useful elements include author/date separation, short/long-horizon discussion,
separate reasoning chains, explicit failed-child disclosure and the warning that
agreement is not external factual verification. Important defects remain:

1. **Unsupported long-term consensus.** The answer attributes an unchanged
   long-term gold bull thesis to both authors and invokes earlier background
   articles that were not read. The comparison-author September sources do not
   establish that asserted shared long-term position. This exceeds the evidence.
2. **Uncertainty and time strengthened.** A tentative liquidation explanation
   is restated more categorically in the lead/table. A prior-dated article is
   presented as explaining a later month's-end fall, although it predates that
   event. Conditional arguments and temporal scopes must remain distinct.
3. **Incorrect sample disclosure.** The answer and coverage snapshot describe a
   two-article sample. Root actually read and cited three articles: one from the
   first author and two dated September 12/17 from the second. All three have
   Runtime read_status=complete and use_status=used. The third source is briefly
   mentioned later, but the headline scope claim still remains inaccurate.
4. **No external event verification.** A later post by the same author is used
   to call an earlier prediction verified. It is additional source testimony,
   not an independently checked official event. It cannot support an external
   verification claim by itself.

These are answer/coverage failures, not reasons to suppress the complete answer
or to impose an unapproved hard financial gate. Runtime reference validity and
physical read coverage do not prove semantic support for every conclusion.

## Accounting And Integrity

A separate read-only audit checked the saved SQLite/artifacts after completion:

- 56 physical calls and 56 individual -1 charges. Task, Attempt, model snapshot
  and actual budget-account bindings matched for every recorded call.
- Only deepseek was used. Each Root Attempt had at most two physical calls;
  each child Attempt at most one under the then-frozen policy.
- S01 had 32 physically completed calls and one unknown transport outcome, all
  charged. S02 had 23 physically completed calls. Completed responses rejected
  by contract validation remain charged, not treated as free or unsent calls.
- S01 grants were exactly 4/3/4 calls with 600000/300000/600000 ms. S02 grants
  were exactly 2/2 calls with 180000 ms each. Unused child funds returned to Root.
- Root's ending balances were 567 and 577. Transfers netted to zero; balance
  plus consumed calls equaled the original 600 in each case. No negative balances.
- Artifact hashes matched. The completed wrapper reported all source/snapshot/
  private-protocol hashes unchanged. An independent pre-edit check found zero
  mismatches in the 377 source hashes and both private protocol files.

Private manifests, exact Queries, original outputs, SQLite, public model-output
artifacts and the read-only audit stay under sibling runtime-records, not Git.

## Post-Batch Work

[037](../changes/037-h2-child-action-envelope-and-budget-errors.md) records the
protocol-guidance and error-classification repairs, then the user's separate
approval of one same-model child correction within the original grant. Its
offline results are reported there and in the cumulative acceptance report.

Do not overwrite R7 artifacts or relabel these two cases as passed after those
changes. A new paid revalidation needs a new authorization. Real Redis, full
business coverage, default-route integration and sustained production load
remain separate unproved boundaries.
