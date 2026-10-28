# Harness 2.0 Live Acceptance R20

Date: 2026-10-20. One fixed Run, no appended repeat.

## Scope And Result

[071](../changes/071-h2-terminal-diagnostic-live-protocol.md) reuses R19's exact
B01 query and original isolated snapshot. Root locates six Peifengke articles,
delegates all original-body reading to one analyst, then consumes its complete
return before synthesis. This is a scheduling fixture, not a mandatory workflow
for all author summaries. DeepSeek/FakeRedis; Root own120, child own60, Run600,
60 minutes, original reserves. No model fallback, live migration or default switch.

| Item | Observed result |
| --- | --- |
| Final delivery | succeeded; 2,632 characters |
| Duration | 453.79 seconds, about 7 minutes 34 seconds |
| Physical requests | Root12 + child13 =25 |
| Child source reads | 9 successful pages, 6 complete articles |
| Root original-body reads | zero |
| Accepted child return | succeeded, metadata complete |
| Root full return consumption | 16,739 characters before final submission |
| Independent personal limits and Run pool | preserved; 575 requests remain in pool |
| Structural audit | 20 checks pass in refined read-only audit |
| Diagnostic assignment checks | all 6 pass |
| Additional source lineage checks | all 4 pass |

Actual source ranges (inclusive), read solely by the child:

| Article date | Returned ranges | Total lines |
| --- | --- | --- |
| 2026-08-01 | 1-100, 101-113 | 113 |
| 2026-08-20 | 1-100, 101-101 | 101 |
| 2026-08-26 | 1-100, 101-139 | 139 |
| 2026-09-12 | 1-47 | 47 |
| 2026-09-17 | 1-59 | 59 |
| 2026-10-01 | 1-85 | 85 |

Nine returned source bindings match actual read events, including author/date,
version and original reader. Root imports the six complete/used article states;
17 metadata-listed articles remain unread. Full-result event323 precedes Root
completion event336; the later user export is not counted as an Agent read.

## Recovery And Audit Refinement

Two Root action-envelope errors recovered through their allowed corrections.
The child's initial final was physically successful but failed domain-level
verbatim-anchor validation. Its correction had one transport_error/unknown call;
the same correction payload was retransmitted once successfully. All25 requests,
including the unknown request, remain charged. No fourth request was made in
that decision. Independent validation reproduces the original anchor error.

The original read-only audit first stopped on a null output_ref from that
transport failure. After handling absent public output, it reported19/20 because
the reused R10 checker expected the original request's physical ledger to carry
contract_error. Inner ResearchResult rejection deliberately leaves the physically
successful HTTP request marked succeeded with a separate domain diagnostic.

The original audit.json is retained, including its false flag. A separate
audit-v2.json verifies the domain rejection from the saved original output and
repair artifact, transport record, identical correction inputs and common
model/Attempt/budget. It passes20/20 without changing Runtime, test outputs or
accounting. A separate source-audit.json verifies lineage. The audit scripts
make zero model requests; their own development fixes are not Runtime repairs.

All429 frozen source/design/test entries,898 snapshot files and the private
runner matched at termination and before report edits. Re-auditing after these
intentional documentation edits must not replace that pre-edit integrity proof.

The [070](../changes/070-h2-terminal-failure-diagnostics.md) terminal-failure
handoff is **not_triggered live**: the child ultimately succeeded. Its failed
second-candidate branch is verified offline, including actual parent model input.

## Content Findings

The answer captures the six-article themes, date progression and explicit
conditions. It discloses missing image data and the lack of external verification.
The source-bound summary remains useful, but manual checks find semantic errors:

1. It calls the supply-recovery discussion a conditional contradiction. The
   October1 original, lines73-77, says risk-on requires supply recovery and that
   the author does not assume such recovery happens automatically. These are
   compatible statements: a necessary condition can remain uncertain. Calling
   them contradictory manufactures a weakness rather than demonstrating one.
2. It attributes a joint fiscal/AI-Capex premise to the September12 article's
   election branches. September12 lines13-15 discuss a possible neutral rate;
   the explicit fiscal/AI-Capex conditional is developed in September17 lines27-31
   and October1 line75. This can be a valid cross-article synthesis if labeled
   as such, but should not be presented as September12's own explicit premises.
3. Phrases describing the author's entire method as non-model-based and saying
   any premise change invalidates the conclusion are stronger than the evidence
   from this six-article sample. Preserve the sample boundary and distinguish
   model interpretation from the author's conditional claims.

These are model-semantic findings. This change does not rewrite the saved answer,
force a new review tool, tighten delivery gates or claim a schema check proves
semantic entailment.

## Remaining Evidence

Structured review is null and coverage metadata is empty; Root finance delivery
state says incomplete_metadata, despite the child's accepted metadata being
complete. This diagnostic did not mandate a separate reviewer. Record the missing
review evidence rather than label it an evidence dispute or block delivery.

Real Redis remains deferred. This is one successful depth-one delegated read,
not repeated reliability, depth-two source-handle certification, the full30-query
business acceptance or commercial-readiness proof. Overall goal remains open.

## Records And Offline Verification

Private records live under sibling runtime-records:

- h2-child60-r20-20261020: manifest, snapshot, B01 SQLite, redacted public inputs,
  output artifacts, finance state and answer.json.
- h2-child60-r20-protocol/run_batch.py: once-only runner.
- h2-child60-r20-audit: initial audit, refined audit-v2 and source audit, retained
  separately. No article bodies or credentials are copied into this report.

Pre-live final code verification: H2 **916 passed /7 skipped** (430.50 seconds),
default finance **1638 passed /30 skipped** (192.21 seconds), configuration10/10,
scoped Ruff and diff checks passed. R19 and earlier reports are unchanged.
