# H2 Q08 Independent-Budget Revalidation

Date: 2026-10-20. Status: fixed execution completed; original protocol retained.

## Purpose

Run Q08 once under approved [067 independent limits](067-h2-independent-call-limits.md)
to determine whether the reviewer failure in R17 was caused by its old eight-call
grant. This is a new diagnostic run, not a rewrite or continuation of R17. The
question, model, corpus snapshot, finance workflow and review policy remain
unchanged. No other query is included.

## Frozen Execution

- Query: exact Q08 text from the frozen business catalog: “根据培风客近几个月的文章，概括其研究主题和分析方式。请用具体文章说明，不要仅凭标题给作者贴标签。”
- Source snapshot: `data/acceptance/h2-code-fix-r1-20261019`, the same isolated corpus used by R17.
- Model: profile `deepseek` (`deepseek-flash`) only. No model fallback or probe.
- Queue backend: FakeRedis. This run does not verify real Redis.
- New isolated output and SQLite ledger: sibling `runtime-records/h2-independent-budget-r18-20261020`.
- One execution only. No success-seeking rerun or automatic retry of the whole query.

## Limits

Apply 067 without the former per-tree 120-call cap:

- Root: at most 120 of its own physical model requests.
- Each child or grandchild: at most 60 of its own physical requests.
- Entire Run: at most 600 physical requests across all Tasks.
- Entire Run deadline: 60 minutes.
- Preserve existing Root closing reserve (24 calls / 10 minutes) and the Run
  time reserve. Do not preallocate or precharge idle child allowances.
- Corrections, retransmissions, and started/unknown requests count. No model
  switching, silent top-up, manual state mutation or mid-run source edits.

The model remains free to choose whether and how to delegate or invoke review.
Acceptance observes actual task creation, per-task limits, charges, and whether
the Root uses returned work; it does not force a reviewer into the plan.

## Freeze And Execution Controls

Before execution, verify the original R17 source snapshot against its manifest,
then copy it into the new output and record a fresh file inventory. Record the
current code/design/config/test inventory and selected model profile. The runner
must reject changed source or snapshot inventory while the case runs. Store no
credentials in the record. Inspect ledger and artifacts read-only after the Run;
the audit itself makes no model calls. Keep R17 and its audit immutable.

## Acceptance And Diagnosis

Report separate outcomes for:

1. Delivery: Root terminal status and whether a final answer was published.
2. Scheduling: actual Task tree, selected review/analyst work, each Task's
   persisted `self_call_limit`, consumed calls, status and accepted result.
3. Accounting: each physical request has a matching charge; no Task exceeds its
   own limit, the Run stays within 600, and time/reserves are respected.
4. Research: actual article reads and completion ranges, with author/date/source
   identity preserved; no inferred reads from plans or child metadata.
5. Answer quality: manual review for sample disclosure, concrete article support,
   faithful author attribution, time/condition distinctions, and calibrated
   generalizations. Delivery or a completed reviewer is not a semantic pass.

Compare with R17 descriptively, not as a controlled model-quality experiment:
the independent limits are the intended change, while single-run model variance
remains. If the reviewer completes under 60 but its result is not consumed, report
that separately. If no reviewer is selected, mark reviewer-cap diagnosis
`not_triggered`. Do not claim commercial reliability from this one run.

## Result

[R18 report](../acceptance/harness-v2-live-acceptance-r18-20261020.md) records one
delivered Q08 answer in 215.22 seconds using 18 Root-owned requests. No child or
reviewer is selected, so live child-cap diagnosis is not_triggered. All eighteen
structural checks and the pre-edit integrity audit pass (422 source/test entries,
898 snapshot files and the private helper). Three public-envelope errors are
corrected once each. Actual sources are six complete and two partial articles;
review remains absent and model-owned coverage stale. No runtime code changed.
