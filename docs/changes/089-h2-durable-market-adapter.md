# H2 Durable Market Adapter

Date: 2026-10-21

Status: Implemented and offline verified; technical design was recorded before
code. Live verification is separately recorded in 090. Implements the
existing 013/017 financial compatibility boundary and the approved 1.0 market
tool chain; does not approve a new financial method or closing/review policy.

## Existing Requirement And Defect

The business acceptance contract T5/Q03/Q21 already requires catalog, immutable
market acquisition, deterministic metrics and bounded series reads. The legacy
MarketResearch implementation exists, but H2 has no binding. Simply sharing its
in-memory snapshots would lose them on worker restart and could mix runs.

## MVP Contract

1. Add an explicit optional market_sources binding and --market-sources runner
   flag. Both binding and Task tool permission are required. Default ROOT_TOOLS,
   the 1.0 route and existing web-source permissions remain unchanged.
2. Reuse the exact legacy input models and MarketResearch catalog, acquisition,
   metrics and series code. No replacement price math, provider selection,
   interval definition, timezone or session policy.
3. Each snapshot operation uses a new legacy ledger. Save its complete acquired
   snapshot as finance.h2.market_snapshot.v1 in an immutable H2 Artifact before
   returning success. snapshot_id is the opaque H2 artifact handle; preserve the
   underlying source_snapshot_id separately. Never put bars in compact state.
4. metrics/series load that handle through the current Task's load_context grant,
   validate its schema/hash, and rebuild a fresh MarketResearch instance using
   that frozen data. No refetch, process-global cache, cross-run lookup, grant
   inferred from a nested ref, or fallback to the latest snapshot. A descendant
   can reuse it only through an explicit authorized input_ref.
   Root retains its existing run-wide artifact authority; do not narrow or
   broaden Repository permissions for this adapter. Even Root cannot load a
   foreign Run's snapshot. Provenance alone grants nothing to a child.
5. The catalog and compact acquisition receipt are not price evidence. A
   nonempty metrics or series read becomes an immutable H2 source observation,
   carrying canonical text/hash, source snapshot and handle, period, collection
   time and per-instrument provider/URL/unit/price basis/session/timezone data.
   Preserve partial/unavailable metric statuses; observed data is not proof of
   causal explanations or author predictions. Daily data is not a realtime quote.
   Canonical source text includes retrieved_at and collected_at. A series keeps
   its original provider retrieval time, which can precede snapshot collection;
   a composite metrics observation uses collection time and retains the actual
   per-instrument retrieval times. Do not overwrite these two distinct times or
   require equality. Both remain in returned provenance.
6. Strip nested legacy evidence_id values. Source text and H2 EvidenceRefs,
   rather than legacy in-memory ledger IDs, support source quotes and result
   lineage. public_market_data is a public-source provenance kind, never an
   article state or a new permission grant. Existing external ledger semantics
   and 074's usable-handle/provenance distinction remain unchanged at both depths.
7. Acquisition exceptions become sanitized structured failures, never fabricated
   prices. Keep cancellation/stale leases/storage failures as real failures.
   No automatic retry, model switch, increased budget or mandatory browsing.

## Acceptance

- Identical tool schemas and deterministic legacy results on synthetic fixtures.
- Default/offline composition unchanged; missing binding/permission cannot fetch.
- Snapshot-to-metrics/series works with a fresh adapter/service after checkpoint,
  with one original acquisition and exact provider/date/unit/baseline metadata.
- Unknown/cross-run/ungranted/corrupt snapshots fail rather than refetch; granting
  a parent result or nested provenance does not grant its private raw snapshot.
- Quotes, multi-level source lineage and complete analysis retention work for
  market data; no article state or inherited verification judgment is invented.
- Empty/partial data and provider failure retain gaps and raw acquisition status.
- Runner preflight only removes the actual market_history blocker; Wiki/holdings,
  isolated filesystem tests and external fault injection remain independent.
- H2 and default finance/configuration regressions, then separately frozen live
  Query evidence. Offline success does not prove provider availability, real
  Redis, semantic accuracy or commercial reliability.

## Offline Verification

- Thirty new market tests pass, including legacy Schema/math equivalence,
  separate retrieval/collection times, pagination, partial-baseline disclosure,
  hash and visible-text consistency, failures, actual SQLite/FakeRedis restart,
  explicit grants, cross-run denial and two-depth source return.
- Combined market/public-source/runner tests: 114 passed in 77.13 seconds.
- Full H2/state/compatibility/runner scope: 1174 passed, 7 skipped in 432.57
  seconds. Skips need real Redis. One existing Starlette deprecation warning.
- Default finance/shared-runtime: 52 modules, 1638 passed, 30 skipped in 113.15
  seconds, temporary workspaces and no live credentials; same warning.
- Configuration/isolation 10/10; scoped Ruff and tracked diff checks pass.
- Logs/JUnit: sibling runtime-records/h2-market-mvp-20261021.

The first failures included a genuine retrieval-time/canonical-text defect and
test-helper errors (a nonexistent Repository helper and a mistaken assumption
that Root lacks its existing run-wide reads). Correct the tests to the authority;
do not modify Repository permissions to make those assertions pass.

No full inherited-suite rerun, live database migration, real Redis, default-route
switch or Git upload occurred. These results do not certify financial semantics,
live-provider availability or unresolved adapters/review/closing policy.

The separately frozen [090](090-h2-market-live-mvp-protocol.md) Q03 run now
delivers with nine successful physical DeepSeek requests in 67.37 seconds. It
exercises catalog/acquisition/three series reads and preserves both time fields.
Final values align with saved provider returns, with daily/reference limitations
disclosed. metrics, child delegation and restart are not live-triggered here.
review/coverage metadata remains incomplete; this is not overall acceptance.
