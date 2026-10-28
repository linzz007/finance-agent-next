# H2 Market MVP Live Protocol

Date: 2026-10-21. Scope: one diagnostic after change 089 offline acceptance.
This uses the user's standing paid-test authorization, not a new financial rule.

## Fixed Batch

- Execute frozen business Query Q03 once, unchanged. Reuse the verified article
  snapshot from data/acceptance/h2-code-fix-r1-20261019; no live user database.
- Use DeepSeek only, FakeRedis and the explicit --market-sources binding.
  Leave web search unbound to isolate the market path. No automatic fallback,
  top-up, semantic scoring or success-seeking repeat.
- Retain Root own120, child/grandchild own60, Run600 physical requests/60min,
  all existing closing reserves and actual retry/correction accounting.
- Output a new runtime-records/h2-market-live-20261021 directory. Save source,
  design, test and runner hashes before requests. No edits to those files until
  the terminal records and first run-end integrity check have been preserved.

## Checks

1. Report actual catalog/snapshot/metrics/series calls. A catalog or acquisition
   receipt alone is not market-price evidence. Unused branches are not_triggered.
2. Inspect symbol, price unit, source URL, actual retrieval/collection times,
   sampling-day or completed-session status. Daily/reference prices are not
   realtime exchange closes. Unavailable instruments must remain unavailable.
3. Confirm immutable snapshot persistence, subsequent authorized loads and H2
   source refs. No legacy evidence IDs, invented article reads or price values.
4. Keep full candidate/final content and failures. Separate protocol execution,
   output delivery, data-provider availability and manual financial correctness.
5. Preserve actual model-call ledger, terminal Task states and frozen integrity
   before report edits. A single run cannot establish reliability percentages,
   real Redis, concurrent deployment or whole-suite financial acceptance.

Q21, Wiki/holdings adapters and unresolved review/automatic-closing design are
outside this batch. The frozen snapshot research_day is recorded by the runner
but not injected as a model clock; inspect any relative/live time claims against
actual acquisition timestamps rather than pretending clock propagation exists.

## Actual Result

This fixed batch is terminal. Q03 succeeded and delivered 1536 characters in
67.37 seconds, with one Root Task and nine successful physical DeepSeek
requests. All nine were charged exactly once; no failure, unknown transport
outcome, model switch, budget increase or paid repetition occurred.

The actual path was three on-demand tool descriptions, market.catalog, one
market.snapshot, three market.series reads, then final output. The three source
observations cover xauusd, xagusd and ixic, and final evidence refs match the
actual reads. Source hashes/canonical text and the one stored snapshot validate;
article_states stays empty. Acquisition and provider retrieval times differ and
are both retained, exercising the 089 time-field fix.

Manual inspection against saved tool returns found the principal closing/
reference values, prior-day values, units and dates consistent. Rounded changes
are approximately -1.26%, -0.85% and -0.02% from those stored pairs. The answer
distinguishes OTC metal reference days from equity-index sessions, daily data
from realtime quotes, missing opens from fabricated prices, and unknown volume
units from interpreted volume. This is consistency with the fetched provider
responses, not independent market-data ground truth or a financial SLA.

Runtime metadata_status remains incomplete_metadata, review is null and coverage
is empty. They were not force-filled or turned into output gates. This is a
market-adapter diagnostic, not whole T0-T9 business acceptance. market.metrics,
worker restart, child delegation, format correction and real Redis were
not_triggered in this live batch; their offline tests are separate evidence.

Records: sibling runtime-records/h2-market-live-20261021; the read-only structural
audit is readonly-structural-audit.json and makes zero model calls. Before any
report edits, first-run-end-integrity.json was saved under sibling
runtime-records/h2-market-live-protocol-20261021: all 568 frozen source/test
entries, 440 runner source entries and 898 snapshot files match, zero mismatches.
Preserve that first integrity result; later deliberate report edits are not a
reason to overwrite it against the now-updated checkout.
