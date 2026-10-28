# H2 Read Index And Result Envelope Retention

Date: 2026-10-20. Status: existing-contract defects repaired and offline-verified.

## Authority

[026](026-finance-state-update-protocol.md), section 3, derives read state from
actual reads. [051](051-h2-version-bound-article-read-progress.md) keeps current
source-version coverage separate from old source versions.
[H2 interfaces](../harness-v2/interface-contracts.md), sections 7.1-7.2, requires
the complete result-summary envelope, including key points, gaps and warnings.
The existing compact-state and model-input guards remain applicable; none of
these contracts authorizes silently evicting a fixed count of semantic entries.

## Reproductions And Repair Boundary

1. Before this repair, `_merge_read_ranges` retained only the last 200 disjoint ranges. If early
   ranges are evicted, later filling the gaps cannot establish an actually
   complete read without reading those early lines again. Preserve the entire
   normalized interval union for the current version; retain version reset,
   interval coalescing, real-observation authority and the compact-state guard.
2. Root `_final` sliced valid model-supplied `key_points`, `gaps` and `warnings`
   to 20 entries before building the Submission. Preserve all currently accepted
   strings in their original order. Do not invent entries, change filtering of
   invalid types, alter evidence checks, reclassify semantic confidence, or
   force every read source into the answer. The complete candidate/body and
   existing result/summary/artifact behavior remain unchanged.

Leave bounded validation diagnostics, recent protocol-repair indexes, generated
summary previews and hashes unchanged. They are not the source coverage or the
model-provided complete semantic envelope. No new financial policy, prompt
repair rule, recursion/budget/DDL/Redis change or default-1.0 switch is included.

## Verification Plan

- Interval union: around/above 200 ranges, different arrival orders, duplicates,
  old-source isolation, and final gap filling. Serialize/restore the compact
  state and confirm early ranges remain available after a real observed read.
- Root result: more than 20 points/gaps/warnings, current accepted string
  filtering, unchanged complete answer and source bindings. A mocked-provider
  durable run must expose all entries through actual result.get(summary), not
  merely in an internal candidate artifact.
- Run focused tests, broad H2/state/compatibility/driver regression,
  default-finance compatibility, configuration/isolation, scoped Ruff and
  document/diff checks. Preserve R13 unchanged and report remaining gaps.

Use synthetic sources/temporary databases and mocked provider calls only.
These deterministic retention boundaries do not need paid repetitions and
do not establish financial correctness or real-Redis recovery.

## Implementation And Results

The runtime change removes four slices only: one interval-union eviction and
three model-supplied result-list truncations. Existing range normalization,
source-version reset, reference/type checks, diagnostics, summary previews,
permissions, budgets and size boundaries remain unchanged.

The initial ten new fixture cases produced six failures before the repair;
the four passing cases were the at/below-200 interval boundaries. Eleven new
regressions now cover eight order/count combinations (199/200/201/230 intervals,
forward/reverse, including duplicate union), one serialized-state restoration
case, one complete-envelope/type-filter case and one actual durable-result case.

The range-restoration case starts with a synthetic prior index, consumes an
observed new range, serializes/restores `FinanceRootState`, then consumes the
remaining gaps in pages no larger than the existing 100-line article-read bound.
It retains line one and reaches complete only after all missing ranges are read.
This is state/observation coverage, not a real-Redis or process-kill test; the
existing temporary-corpus version/restart cases remain in the broad collection.

The isolated SQLite/FakeRedis integration makes two mocked-provider calls,
reads a source, publishes a Root result, and obtains all 31 entries in each
field through `result.get(summary)`. It also checks the unchanged full body,
accepted evidence, candidate artifact and actual-used article state. These
synthetic metadata entries are not financial findings.

| Completed check | Actual result |
| --- | --- |
| State-update + durable-continuation tests | 65 passed, 57.12 seconds |
| State-update recheck with duplicate-union assertion | 39 passed, 1.24 seconds |
| All dispatch + state-update + H2 compatibility/live-driver tests | 701 passed / 7 skipped, 314.78 seconds |
| Default finance/shared-runtime, 52 modules | 1631 passed / 30 skipped, 187.74 seconds |
| Configuration/separation | 10/10; zero model calls |
| Full Ruff on the three changed Python files; Git whitespace check | Passed; existing CRLF notices remain |

These collections overlap and must not be summed. Seven H2 skips require real
Redis; the existing Starlette/httpx warning remains. The full inherited test
collection was not rerun. No paid request, live-source modification, Docker
operation, default-route switch, Git commit or push occurred.

The [R13 business run](../acceptance/harness-v2-live-acceptance-r13-20261020.md)
remains unchanged. There is no evidence these count thresholds caused its
semantic failures; this repair does not prove model truthfulness, all-child
completion, transport recovery or commercial stability. Correction-input
isolation and other unapproved research policies remain pending user decisions.
Overall acceptance remains incomplete.
