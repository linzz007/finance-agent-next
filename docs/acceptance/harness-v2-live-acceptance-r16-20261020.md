# H2 R16 Two-Level Delegation Diagnostic

Date: 2026-10-20. Status: fixed batch completed; Root failed before delegation.
The depth-two requirement remains live-unverified, not disproved by this run.

## Frozen Execution

[064](../changes/064-h2-depth-two-live-protocol.md) defines one new N01 case,
not a rerun of R15. It explicitly requests Root -> analyst -> analyst, with
only depth two reading the original and complete results/source lineage
returning through both ancestors. Whole-tree/batch cap is 120 physical calls,
60 minutes and unchanged reserves. DeepSeek/deepseek-flash and FakeRedis only;
no fallback, budget expansion, probe or repetition occurs.

Final pre-edit checks confirm **414** source/design/configuration/test inventory
entries, **898** snapshot files and all **three** private helpers unchanged.
Those helpers include the execution wrapper, depth-two audit and imported R10
accounting audit. The audit uses read-only SQLite and makes zero model calls.

## Actual Result

| Measure | Observed |
| --- | --- |
| Root status / final answer | failed / no final answer |
| Duration | 6.55 seconds |
| Charged physical requests | 2, both completed responses with contract_error |
| Tasks / research descendants | 1 / 0 |
| Executed tools / source reads | 0 / 0 |
| Structural accounting/artifact checks | 12 / 12 pass |
| Depth-two flow and lineage | Not reached |
| Correction transport retransmission | not_triggered |

The first public response contains a tool.describe action followed by an XML
result block containing a fabricated article.list description. No tool had
executed; this block is not a real observation. The current strict single-JSON
contract correctly rejects the whole response before executing either part.

The one approved same-model correction returns malformed JSON: a research-plan
steps array is closed incorrectly. Runtime rejects it and terminates that
decision as specified, preserving both public outputs and charges. It does
not create a child or claim that any original article was read. This failure
is not caused by the 120-call or 60-minute ceilings.

The correction also repeats publication_date from the fabricated description.
The real input registry in domains/finance/tool_contracts.py uses published_after
and published_before, not that field. Its reuse is consistent with the known
rejected-output contamination risk. It is not controlled causal proof that
input isolation would make this response succeed. Syntax failure occurs first;
the invalid parameter is a separate latent error, not an executed tool rejection.

## Acceptance Interpretation

The private audit emits eight diagnostic booleans. Seven are false because no
descendant/source/result return exists; no_depth_three_task is true only because
there are no children. Do not report that vacuous negative as a passed live
recursion boundary. The proper conclusion is not_triggered for depth-two
execution, return ordering and original-reader propagation.

All twelve structural checks pass: exact model, identity/ledger bindings,
conserved grants, charged physical requests, limits, settled records and
immutable artifacts. These checks do not certify a delivered answer or an
executed recursive workflow. No semantic answer exists to review, and retained
rejected model text must not be presented as delivered financial analysis.

The three targeted offline recursion/lineage tests were actually rerun before
the batch: **3 passed, 16.23 seconds**. Configuration/isolation is **10/10**,
zero calls; wrapper preflight and configured full Ruff on both new private
scripts pass. Existing broad H2 **825/7 skipped** and default finance
**1638/30 skipped** remain prior post-062 results, not new R16 executions.
No runtime code changed in this diagnostic.

## Disposition

This run establishes no new deviation from the approved implementation contract:
the invalid public outputs were rejected, the correction remained bounded,
charges were retained and no fake tool observation became executable state.
It adds a concrete example of the already pending recovery-input design choice.
Do not change those rules or rerun this fixed case merely to obtain a pass.

Two decisions were separately presented to the user after the run:

- Isolate correction inputs to trusted context plus bounded diagnostics/error
  excerpts, keeping full rejected output only in audit. Preserve one correction,
  the same model and original budget/deadline.
- Optionally normalize exactly one outer Markdown fence around a single JSON
  object; still reject explanatory prose, XML tool results, multiple objects
  and invalid parameters. N01 does not contain that fence pattern, so this
  option alone would not repair its observed failure.

Neither decision is inferred from general paid-test authorization or implemented
without confirmation. Reserve-backed financial closing entry, broader research
quality, missing H2 business adapters, real Redis and production integration
remain separate unresolved items. R15's successful depth-one source return and
R14's broad business failures remain unchanged. The overall goal is not complete.

## Records

Private sibling runtime-records directories:

- h2-depth-two-r16-20261020: final manifest/summary, isolated SQLite, original
  failed public responses, call ledger, checkpoints and immutable provenance.
- h2-depth-two-r16-protocol: once-only execution/preflight wrapper.
- h2-depth-two-r16-audit: zero-call read-only audit and audit.json.

The final source/snapshot/helper hash audit predates documentation edits.
Do not overwrite it by comparing against later intentional changes. No source
article bodies, full responses, credentials or raw traces are added to repository
reports. No live user database/corpus mutation, Docker operation, default-1.0
switch, Git commit/push or additional paid run occurs. The process is terminal.
