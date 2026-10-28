# H2 Business Acceptance R14

Date: 2026-10-20. Status: execution and read-only review complete; not full acceptance.

## Frozen Execution

[059](../changes/059-h2-business-r14-after-input-grant-repair.md) defines the
one-time post-058 batch. All 30 original Query IDs are retained. Twenty execute
once with DeepSeek/deepseek-flash and FakeRedis, with 120 physical calls per
whole tree, 60 minutes per case and a 2,400-call batch ceiling. There is no model
switch, fallback, top-up, extra repetition or rewrite of R13.

Q03, Q19-Q25 and Q30 are blocked_missing_capability. Q29 requires an isolated
environment; no host file was read. Missing market/Web/Wiki/holdings/fault
adapters and real Redis remain outside the demonstrated scope.

Final audit before any post-batch edit confirms all 392 inventory entries and
898 copied snapshot files unchanged. The source inventory includes src Python,
plugin Markdown/JSON, docs Markdown, configured entrypoints/configuration,
AGENTS, schema.sql, the driver and its one test module; it does not hash the
entire offline test suite. The recorded research day is 2026-10-19, but is not
explicitly injected as a model research clock.

## Results

| Measure | Actual result |
| --- | --- |
| Registered / executed | 30 / 20 |
| Root deliveries / Runtime failures | 17 / 3 |
| Failed cases | Q08, Q09, Q15 |
| Charged physical requests | 403 |
| Sum of case durations | 5,435.03 seconds, about 90m 35s |
| Contract / transport error calls | 43 / 13 |
| Structural checks | All 13 checks pass for each of 20 executed cases |
| Research children | 12: 2 succeeded, 9 failed, 1 timed_out |
| Successful child full results read by Root | 2 of 2 |
| Correction-transport retry | Q28 triggered and recovered; other 19 not_triggered |
| Legacy child-request envelope branch | Not exercised by any child |
| Completed structured semantic reviews | 0; 19 absent and Q14 pending |

The 13 structural checks cover one-to-one charges, Task/Attempt/budget/model
binding, call conservation, limits, charged started requests, nonnegative
balances, exact grants, settled call records, fixed model, at most three calls
per Attempt, retry audit linkage, artifact hashes and exported usage parity.
They do not test semantic truth. All 20 retained states mark incomplete_metadata;
failed cases are inspected through their latest durable checkpoint.

Successful responses report 12,214,390 prompt tokens and 816,365 completion
tokens, totaling 13,030,755 across many requests. This is not one context window
or a monetary bill; unknown transport outcomes may still incur provider charges.
The driver processes worker_once serially, so this is not concurrent throughput.

## Per-Case Review

These are manual engineering findings against saved source observations, not
automatic scores or proof that every remaining sentence is correct.

| Case | Delivery | Calls | Source-based finding |
| --- | --- | ---: | --- |
| Q01 | Yes | 28 | Correct 888-item/five-author inventory; unsupported title-only completeness claims and inaccurate child transport-error attribution remain. |
| Q02 | Yes | 4 | Seven dates/titles match metadata; an inventory query need not read article bodies. |
| Q04 | Yes | 21 | Root and analyst read the full article; Root reads the full successful child result. Skeptic budget request is refused; some criticism exceeds source support. |
| Q05 | Yes | 14 | Full article read; nonparticipation/travel is overstated as no holdings, and a necessary condition is presented too strongly. |
| Q06 | Yes | 8 | Full source read with useful conditions and attribution; no independent external validation claimed. |
| Q07 | Yes | 60 | Twenty full and one partial Root reads; disclosed sampling, but author total wrong and a single probability outcome treated as forecast-calibration failure. All three children fail. |
| Q08 | No | 28 | Ten full and one partial reads retained; original and correction both fenced JSON, rejected under current strict policy. |
| Q09 | No | 5 | Fake observations rejected; correction still violates state_update structure. No body read. |
| Q10 | Yes | 11 | Five full reads; rate-driver evolution and probability interpretation overstated. |
| Q11 | Yes | 13 | Five complete reads plus an excluded partial essay; better event chronology than R13, but gold stance overstrengthened. |
| Q12 | Yes | 16 | Three actual articles fully read; better than R13's zero-read fabrication, but not proof that correction contamination is fixed. |
| Q13 | Yes | 20 | Ten full reads; opening misdates a pre-holiday analysis as post-holiday, with overstrong contradiction claims. |
| Q14 | Yes | 15 | Seven full reads across both authors; one view misdated, absent forecast treated as opposing forecast, review pending and coverage stale. |
| Q15 | No | 22 | Eight full reads retained; original/correction fenced JSON. Also exposes a separate false source-version warning. |
| Q16 | Yes | 21 | Seven full reads; tentative observations upgraded to causal confirmation and a conditional hedge compared as baseline advice. Repeats false version warning. |
| Q17 | Yes | 55 | Fourteen complete Root sources; successful child result read. Different position/date/indicator conditions still turned into contradictions; child status disclosure inaccurate. |
| Q18 | Yes | 36 | Three authors read: eleven complete, one partial. Broad number attribution, probability error and scenario conflation remain; plan mislabels partial reading and failed child. |
| Q26 | Yes | 8 | No fake articles/citations and honest local absence; claims a gold-keyword search that exists only in rejected model text. |
| Q27 | Yes | 4 | Severe failure: real metadata identifies the nearest article, but zero body reads; final analyzes an invented truncated body from rejected output. |
| Q28 | Yes | 14 | Three complete reads; separates archive/title/body dates and retains original-publication uncertainty. Actual one-time correction transmission recovery. |

Q03/Q19-Q25/Q30 are unexecuted capability gaps, not semantic passes. Q29 is an
unexecuted isolation requirement. A delivered final is not an accepted analysis.

## Main Causes And Next Boundaries

1. Protocol reliability: Q08/Q15 keep Markdown fences after the one correction;
   Q09 keeps an invalid state-update location. None fails because of the 120-call
   ceiling. Optional single-outer-fence normalization remains a design choice,
   not a batch-time parser relaxation.
2. Correction-source contamination: Q27 repeats the R13 Q12 failure pattern.
   Runtime rejects fake tool results and unknown evidence refs correctly, but
   reinjecting the entire rejected output lets the model reuse invented source
   content. Q26 also describes an unexecuted broader search. Stronger isolation
   of correction input is pending user confirmation; no universal output gate
   or automatic research replay was added.
3. Financial reasoning: different dates, conditions, assets and horizons are
   sometimes conflated; one realized probabilistic outcome does not establish
   miscalibration. Source citations alone cannot prevent these semantic errors.
4. Proven implementation defect: failed-before-first-read is mistaken for an
   observed source version in Q15/Q16. The frozen snapshot did not change.
   Narrow post-batch repair and separate offline proof are tracked in
   [060](../changes/060-h2-first-successful-read-version-warning.md).
5. Delegation and workflow: two successful children are actually consumed, but
   ten others fail/time out. Plans, coverage, review and terminal child reporting
   remain incomplete/stale. Do not force a fixed article count or every role
   merely to make a trace look complete. Reserve-backed closing entry still
   needs a separate agreed design.

Q28 provides one real occurrence of contract_error -> correction transport_error
-> successful correction in one Attempt, with three distinct charged calls and
a linked retry artifact. It does not establish production network reliability
or capture wire-message equality; exact request reuse has separate offline proof.
058's compatibility initialization branch is absent, so R14 improvements cannot
be attributed causally to that repair.

R13 delivered 18/20 with 331 calls; R14 delivers 17/20 with 403. Some questions
improve while others regress. Different model choices and delegation paths mean
these batches are not a controlled causal comparison. Overall stability or full
business acceptance has not been established.

## Evidence

Private records remain outside Git under the sibling runtime-records folder:

- h2-business-r14-20261020: manifest, summary, SQLite, artifacts, exports and answers.
- h2-business-r14-audit: final observations.json, per-case audits, summary-facts.json,
  manual-review.md and the synthetic first-read reproducer.

The final hash audit was saved before documentation/runtime edits. Preserve it;
rerunning its live source comparison after intentional edits would describe a
different state, not invalidate the frozen batch. No original R13/R14 outputs,
article bodies, full answers, credentials or traces are added to Git documents.

## Separate Post-Batch Repair

060's deterministic version-warning repair was specified and tested after the
batch and final hash audit. Six first-read cases fail before repair and pass
after; three more new guards retain genuine version changes. Final offline
results are H2 785 passed / 7 skipped and default finance 1638 passed / 30 skipped,
configuration 10/10, full scoped Ruff/diff passed. No extra paid run is made.
These results do not amend the frozen 17/20 deliveries or claim that Q27's
fabrication, other semantic errors or pending correction/parser designs are fixed.
