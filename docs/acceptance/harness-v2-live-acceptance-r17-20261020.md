# H2 R17 Isolated-Correction Revalidation

Date: 2026-10-20. Status: fixed batch completed; all Root answers delivered,
but full child-chain and semantic acceptance remain incomplete.

## Scope And Freeze

[065](../changes/065-h2-correction-input-isolation-and-json-fence.md) was approved,
documented, implemented and offline-tested before this new
[066](../changes/066-h2-isolation-live-protocol.md) batch. Unchanged Q08/Q15 and
064's exact N01 assignment run once each, DeepSeek only and FakeRedis. Bounds
remain 120 physical calls/tree, 360/batch, 60 minutes/case, with unchanged Root
and child reserves. There is no fallback, extra probe, added grant or rerun.

Final pre-edit integrity confirms all **417** source/design/configuration/H2-test
inventory entries, **898** snapshot files and **five** private helpers unchanged.
The snapshot and temporary databases are isolated from the user's live data.
Public model inputs are captured privately with credentials redacted; no HTTP
authorization headers or hidden reasoning are captured. The final zero-call
audit reads SQLite/artifacts, not a provider API. Old R14-R16 evidence is intact.

## Actual Results

| Case | Root / answer | Physical calls | Seconds | Answer characters | Descendants |
| --- | --- | --- | --- | --- | --- |
| Q08 | succeeded / delivered | 39 | 360.81 | 3117 | One reviewer failed on its 8-call grant |
| Q15 | succeeded / delivered | 16 | 140.67 | 1794 | None; Root chose direct analysis |
| N01 | succeeded / delivered | 20 | 301.66 | 2643 | Depth two succeeded; depth one partial |

Total **75** physical requests, all retained in accounting. Each case passes
the twelve read-only structural checks: identity/model/budget binding, exact
grants, conservation, call caps, charges, settlement, retry bounds and artifact
hashes. No provider/transport failure occurred in this batch. Transport retry
is not_triggered; these three cases cannot establish a stable service rate.

All three Root states remain incomplete_metadata, with no structured review and
empty coverage metadata. Root succeeded means delivered, not full evidence or
semantic acceptance. Q08/Q15 now deliver where their prior R14 executions failed;
this is not a controlled attribution study or proof of broad improvement.

## Correction Input Evidence

Q08 has five initial syntax-contract failures. Four corrections are physically
sent and accepted. The fifth correction is prepared but blocked by the child's
exhausted original grant before reservation/transmission. Its input capture is
not a provider call: Q08 has **40 captured inputs but 39 physical calls**. The
child requested eight calls, consumed eight and failed budget_denied; no hidden
top-up or uncharged provider request occurs. Root preserves its own analysis
and discloses the missing independent review in the final answer.

Q15 has three initial syntax failures and three accepted corrections. N01 has
two ResearchResult verbatim-anchor failures after completed physical responses;
both are corrected once. These latter failures appear in repair diagnostics,
not adapter-ledger contract_error counts, because inner validation happens
after capabilities.complete. Do not infer absence of format/anchor errors from
N01's twenty physically succeeded, adapter-error-free ledger rows.

All **nine physically sent corrections**, plus the one unsent prepared request,
retain the original messages and schemas without replaying the rejected body
or exposing its audit handle in feedback. Syntax excerpts are bounded and
nonsemantic; final.output diagnostics retain their field errors. Complete
rejected outputs and repair-call linkage remain auditable. None requires a
second semantic correction, model switch or new budget. This proves the chosen
input boundary for these triggered branches, not universal injection resistance.

No paid public response uses a Markdown fence. Fence normalization is therefore
**not_triggered live**, with deterministic Chat/Responses, streaming/nonstreaming,
raw-artifact, negative-envelope and single-charge proof in the 065 offline tests.
It is not credited as a live recovery in this batch.

## N01 Two-Level Return

The actual Task tree has exactly depths zero, one and two. Only depth two reads
the assigned original: 1-63 of 63 lines, has_more=false. The deepest result is
accepted. Depth one fully consumes it before submitting; Root fully consumes
depth one's complete returned text before its own submission. Both ordering
checks use Agent read events, not post-run exports.

The remaining failure is the evidence binding at depth one. Its final output
cites the grandchild's private original-source artifact instead of an accessible
returned-result handle. The raw source ref appears in known_evidence_refs after
the grandchild return, but is not readable by the parent: knowing an ID does
not grant permission. Validation reports research_source_unavailable/not_found,
keeps the complete candidate and returns partial with no accepted source binding.
No permission is widened or invalid reference silently rewritten.

Consequently only the deepest result carries accepted original-source lineage;
it does not survive the second return as accepted evidence. Root correctly keeps
the article unread/undecided and discloses that the analysis is based on a partial
child result, rather than inventing a verified read. The eight diagnostic checks
have five true and three false outcomes: all-three-succeeded, both accepted
source returns and both ancestor read/use states fail. This is **partial live
recursion evidence**, not complete two-level acceptance.

The concrete follow-up is to distinguish usable evidence handles from provenance
IDs in the Agent-facing context. _consume_observations currently imports child
envelope refs into known_evidence_refs without making those artifacts readable.
Do not solve this by granting access to every nested ref, dropping source audits
or promoting a partial candidate. The approved 065 change is complete; a precise
reference-handoff refinement must be documented separately, with authority and
recovery tests, rather than silently expanding scope during this frozen batch.

## Content Review And Remaining Limits

Q08 uses ten article bodies across multiple months, nine complete and one missing
the last of 101 lines; it explicitly discloses that range and the failed reviewer.
Its broad author-framework generalizations still exceed heterogeneous sample
support in places. This is useful source-based analysis, not a certified complete
author portrait or independent review.

Q15 reads six complete articles from both authors in the requested month and
distinguishes author assertions from external verification. However it compares
an earlier general macro framework with a later event-specific explanation and
still characterizes them as competing explanations, despite noting the date
mismatch. That conclusion needs more careful same-event/condition alignment.

N01 preserves author uncertainty and offers a substantive critique, while
honestly disclosing its partial evidence return. Some criticism overstates the
absence of forecast conditions even though qualitative conditions are summarized
elsewhere in the answer. Complete delivery does not certify that every criticism
is supported. No external financial fact verification is claimed.

Reference usability, model-selected child budgets, structured review/coverage,
semantic overstatement, real Redis, missing H2 business adapters and production
integration remain open. Reserve-backed financial closing is a separate pending
design. No automatic follow-on paid batch or default-1.0 switch occurs.

## Offline Verification And Records

Before paid execution: H2 **888 passed / 7 skipped** (474.38 seconds), default
finance/shared-runtime **1638 / 30 skipped**, 52 modules (208.90 seconds), affected
modules **447 passed**, configuration 10/10, scoped full Ruff and diff checks
passed. There are 63 net new tests. The existing Starlette/httpx warning and
environment skips remain. These counts are not financial Query success rates.

Private sibling runtime-records directories:

- h2-isolation-r17-20261020: manifest, frozen provenance, snapshot, temporary
  databases, complete original outputs/answers, model-inputs and final summary.
- h2-isolation-r17-protocol: once-only execution and zero-call preflight wrapper.
- h2-isolation-r17-audit: read-only structural/input/recursion audit and audit.json.

Final integrity above predates these intentional report/index edits; retain it
instead of overwriting it against the subsequently edited documentation. No Git
commit/push, live corpus/DB change, Docker operation or real Redis test occurs.
