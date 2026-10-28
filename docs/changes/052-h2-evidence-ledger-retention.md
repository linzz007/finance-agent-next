# H2 Evidence Ledger Retention

Date: 2026-10-20. Status: existing-contract defect repaired and offline-verified.

## Authority And Defect

[026](026-finance-state-update-protocol.md), sections 2-3, requires idempotent
append of claim/evidence/relation triples and retention of contrary evidence.
[H2 interfaces](../harness-v2/interface-contracts.md), sections 8.2-8.3, keeps
that ledger in durable financial state and enforces an explicit 128 KiB compact
state guard. It does not authorize discarding older evidence at 200 entries.

Before this repair, `FinanceH2Domain._apply_state_update` assigned `additions[-200:]` to
the next ledger. A new valid entry can silently remove an earlier opposing
binding. An unrelated coverage/review update can also truncate a larger ledger
previously populated from actual observations. Original artifact existence is
not a substitute for preserving these bindings in the recoverable ledger.

This is independent of R13's recorded semantic failures; no evidence establishes
that its cases crossed this boundary. Do not rewrite frozen results.

## Repair Boundary

- Remove only the undocumented 200-entry eviction. Preserve accepted prior
  bindings and append each new valid triple once, in existing order.
- Keep unknown-reference rejection, certainty checks and duplicate handling.
  Contradicting relations remain separate bindings, not replacements.
- Root and research children use the existing shared merge.
- Keep the explicit compact-state size guard and actual model-input limits.
  This change neither increases those limits nor promises unbounded inline
  state. A future externalized-ledger policy would need separate design; do not
  silently summarize, prune, spill or expand budgets in this repair.
- No new tool, DDL, Redis message, model retry, financial gate, fallback or
  default-1.0 entry change.

## Verification Plan

Use synthetic evidence only: reproduce a 200-entry ledger receiving a contrary
binding; a larger ledger receiving metadata-only changes;
duplicate updates; and a rejected foreign reference. Confirm both Root and
research roles preserve their inputs and pass the existing compact-state guard
for the tested payloads. Exercise actual durable actions and fresh-service
checkpoint recovery with a mocked provider, checking the next model input and
terminal state retain the complete ledger and original artifact bindings.

Run focused and broad H2/state/compatibility/driver tests, default-finance
regression, configuration/isolation, scoped static checks and document links.
These deterministic boundaries need no paid test. Preserve previous live
failures and real-Redis gaps separately.

## Implementation And Results

The runtime change removes the slice from the shared state merge; no additional
automatic retention or research policy was introduced. Six seeded pre-fix cases
failed because the earliest binding disappeared, including metadata-only and
foreign-reference updates. Final tests cover Root and research role inheritance,
the 200-to-201 boundary with an opposing relation, duplicate patches, rejection
of a foreign reference without deleting history, and the unchanged size guard.
A 1000-entry synthetic ledger remains intact and is explicitly rejected by the
128 KiB compact-state guard, not silently shortened to fit.

The durable integration uses actual mocked-provider actions and isolated
SQLite/FakeRedis. After a successful article read, 200 synthetic bindings and
one opposing binding are added. Services/domains are reconstructed between
actions. The next model inputs and terminal `finance_state` retain all 202
entries (201 model bindings plus the original actual-read binding), both
relations, and the original readable source artifact. No real financial claim
is being validated by this synthetic volume fixture.

| Completed check | Actual result |
| --- | --- |
| State-update + durable-continuation tests | 54 passed, 58.37 seconds |
| All dispatch + state-update + H2 compatibility/live-driver tests | 690 passed / 7 skipped, 310.83 seconds |
| Default finance/shared-runtime, 52 modules | 1621 passed / 30 skipped, 184.81 seconds |
| State-update recheck after import-only formatting | 29 passed, 0.71 seconds |
| Configuration/separation | 10/10; zero model calls |
| Full Ruff on the three changed Python files and Git diff whitespace check | Passed; existing CRLF notices remain |

The collections overlap; do not sum them. Seven H2 tests still require real
Redis, and the existing Starlette/httpx deprecation warning remains. The full
inherited repository collection was not rerun. No new paid call, Docker
operation, live-source edit, production switch, Git commit or push occurred.

[R13](../acceptance/harness-v2-live-acceptance-r13-20261020.md) remains the latest
business run, with its original failures. This repair is not live evidence of
financial reasoning quality, JSON reliability or real-Redis recovery. Stronger
format-correction provenance isolation remains a separate user decision;
it is not implemented through this evidence-retention change. Overall acceptance
remains incomplete.
