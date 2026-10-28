# H2 Live Acceptance R6

Date: 2026-10-19

## Frozen Execution

Entry: FinanceHarnessV2Service.for_finance_corpus, change 032. Q04/Q08/Q14
ran once each using the explicitly selected deepseek profile, FakeRedis and
the R5 article snapshot. No fallback, additional paid repetitions, Docker
changes or default-1.0 switch. Original normal budgets remained in force:
600 Root calls, 60 minutes and the existing closing reserves.

The batch completed before any follow-up edit. All 356 source/design hashes
were independently compared with manifest.source_hashes and matched. The
driver reported snapshot_unchanged=true. It does not itself emit a source
comparison flag; do not confuse that absence with an automated source check.
Private answers, article copies, SQLite files and call/event exports remain
under the sibling runtime-records/h2-live-r6-20261019 directory, not Git.

| Case | Seconds | Physical model calls | Tasks | Continue events | Delivered |
| --- | ---: | ---: | ---: | ---: | --- |
| Q04 | 77.93 | 6 | 1 | 5 | Yes, 3790 characters |
| Q08 | 178.04 | 18 | 1 | 15 | Yes, 2532 characters |
| Q14 | 167.31 | 20 | 1 | 17 | Yes, 2224 characters |

Delivery is **3/3 in this diagnostic batch**, not a measured production success
rate or full business/semantic acceptance. Preserve R4 at 0/3 and R5 at 1/3.
The pre-batch offline baseline was 419 passed / 7 skipped; default finance was
1606 passed / 30 skipped. Those are different evidence layers.

## Recovery And Action Trace

- Q04 used two descriptions, one list, one article.read and one article.claims.
- Q08 used two descriptions, one list and twelve article.read actions. One
  physical transport_error retained an unknown outcome; the same Task recovered
  from its checkpoint on the same selected model. It was neither refunded nor
  relabeled a successful physical call. A separate completed malformed action
  was corrected once; both calls are charged in the ledger.
- Q14 used three descriptions, two lists, one search and eleven article.read
  actions. Two separate completed malformed decisions each used their one
  permitted Root correction. All twenty physical calls are retained; two have
  contract_error even though physical transport completed successfully.
- The three final Tasks are succeeded with terminal results. Normal continued
  Attempts remain suspended audit records; they do not exhaust failure retry
  allowance. No pending outbox remained at completion.
- Article tools now execute within the existing Root Task. None of these live
  runs spawned an analyst/verifier/skeptic. They do not prove live model-chosen
  delegation, parallel research or nested research quality. Offline integration
  separately exercises two independent spawns, parent progress, depth 0/1/2,
  denied depth 3 and committed-spawn recovery on one Worker.

## Content And Evidence Review

This is manual comparison of the saved outputs, observed read ranges and
selected original-source passages, not an exhaustive fact-by-fact external
verification or automatic score.

**Q04:** all 63 lines of the named TT article were read. The answer follows
the requested judgment, reasoning, assumptions and open questions. It separates
the author's liquidation hypothesis from established fact, retains alternative
explanations and identifies missing exposure/trading evidence. Compared with
R5, it no longer imports unmentioned long-term macro causes as this article's
specific argument. General market explanations in its critique are model
analysis, not externally verified findings. article.claims returned no cached
claims, which is not proof the article contains no claims.

**Q08:** the answer samples eight articles dated June through October, six
read completely and two read through line 100/101. It explicitly discloses the
sample, the two missing last lines and that many other articles were not read.
Major themes and examples were checked against the source passages on fiscal
policy/rates, AI financing, Chinese supply/demand, political continuity and
short-/long-term research. The main themes have source support. This does not
establish an exhaustive author profile, prove every numeric/historical claim,
or demonstrate external verification. Article titles/dates are readable in the
answer, but the displayed body lacks per-claim navigable evidence links; saved
result evidence_refs provide a separate reference list, not a complete claim
mapping.

**Q14:** ten articles were read completely: seven TT and three from the other
author. The answer contrasts shared themes, opposite policy expectations and
topics not directly discussed, and explicitly says author agreement is not
external verification. The differing pre-FOMC probabilities and the different
gold timing frameworks are supported in the read source passages. However,
its description of TT as betting on a crisis trade is stronger than the
source's conditional branches and watch-for-confirmation language. The
unqualified statement that the other author did not discuss certain topics
should retain the narrower read-sample scope. These are semantic limitations,
not grounds to discard the useful full comparison or force external browsing.
The body also lacks directly navigable source references.

## Metadata Findings And Attribution

1. **Confirmed implementation defect, repaired after the batch:** original
   successful article.read artifacts were valid final evidence_refs but were
   missing from each article's material_refs, which indexed only wrappers.
   Actual final use consequently stayed undecided for all three cases. Add
   the accepted original read refs to the matching article index, preserving
   wrapper/page refs. Do not mark every listed/read article used. Change 032
   records this mapping before the repair. R6's original state stays unchanged.
2. **Model-written coverage remains stale or absent:** Q04/Q08 coverage is
   empty. Q14 coverage claims four read articles while the trusted read-state
   ledger shows ten; its author subtotals are also stale. Under 026, coverage
   is a model judgment snapshot and Runtime does not impose sampling or
   regenerate semantic coverage. Use actual article_states/events for factual
   read counts, not this free-form snapshot. No new overwrite/gating policy
   was invented during the continuation repair.
3. **Structured review is unproved:** review is null in all three states.
   Q04 execution metadata_status is complete, but that only describes accepted
   state updates; it is not proof of completed semantic review. Q08/Q14 carry
   incomplete_metadata. Bodies contain useful cautions, but this does not prove
   the complete designed review loop. Delivery and review remain separate.
4. **Planning exists, freshness varies:** each case persists a research_plan
   and delivers it across Attempts. Q14's final snapshot still has open
   questions/steps referring to work subsequently completed. Trace proves
   persistence, not that the model has reconciled all final bookkeeping.

## Post-Batch Repair Verification

The raw-reference regression first reproduced use_status=undecided despite
valid final citation. After the one-line source-index correction, all 27
finance-agent/context/continuation tests passed. They retain the existing
wrapper-citation path and check that a listed, unread and uncited article
remains undecided. No further paid model batch was run. Final broad offline
regression: H2/state/compatibility/live-driver **420 passed / 7 skipped** in
132.40 seconds; default finance/shared-runtime **1606 passed / 30 skipped** in
116.19 seconds across 52 modules. Configuration/separation **10/10**, scoped
Ruff and git diff --check passed. The existing Starlette/httpx warning remains.
The repair must not be reported as part of the frozen live source or as
live-revalidated.

## Outstanding Acceptance

Real Redis, multiprocess abrupt-failure/sustained-load verification, the full
30-query set, web/market/Wiki business-tool parity and public-entry integration
remain outside this batch's proof. The default financial path is unchanged.
Neither the frozen H2 baseline nor the active overall objective is complete.
