# H2 Business Acceptance Recording And Scope

Date: 2026-10-20. Status: implementation repair under the existing acceptance
contract; no change to financial research or scheduler semantics.

Authority: [013](013-harness-v2-dispatch-baseline.md),
[H2 interfaces](../harness-v2/interface-contracts.md),
[1.0 business acceptance](../finance_agent_eval_v1_acceptance.md), and the
user's authorization to perform necessary paid tests without repeated questions.

## Entry And Attribution

Only `FinanceHarnessV2Service.for_finance_corpus` exercises the opt-in H2 path.
The legacy ChatWorkbenchService cannot establish H2 acceptance. An accidental
legacy R13 batch was stopped during Q07 after Q01-Q06 returned; preserve its
private records and interrupted trace. Its expenses and outcomes stay separate
and must never be counted as H2 results. Revert this iteration's legacy-driver
edits rather than maintain a competing H2 runner.

## Complete Catalog, Honest Preconditions

Retain all 30 approved Query IDs and original text. Materialize Q26's existing
missing-author placeholder with a name verified absent from the snapshot; save
both the original and executed text. Never rewrite other Queries.

The corpus adapter currently supports article tools and scheduling, not full
web/market/Wiki/holdings integration. Preflight each selected case:

- Q01-Q02, Q04-Q18, Q26-Q28 can exercise the local-article H2 path (20 cases).
- Q03 and Q19-Q25 require missing business capabilities: record
  `blocked_missing_capability`, not pass, model failure, or silently skip.
- Q29 requires an isolated host: record `requires_isolated_environment`, do not
  read the host system file, and make no model call for this case here.
- Q30 requires external-tool failure injection. With no external adapter to
  fail, record `blocked_missing_capability`; absence of a tool is not proof of
  network-fault handling.

This preflight records the known opt-in integration limit; it does not remove
these cases from the business suite or authorize new business adapters.
Likewise, recording a snapshot research day does not prove it reached the
model: the H2 prompt has no dedicated research_clock input. Record that gap
explicitly; do not inject a rewritten Query or change research rules to hide it.

## Bounded R13 H2 Protocol

- One execution per executable Query, DeepSeek only, FakeRedis explicitly.
- 120 physical calls per whole tree per case; at most 2400 for 20 executable
  cases. Preserve the existing 60-minute deadline and Root closing reserves.
  All child calls, corrections and transport retransmits count; no top-ups,
  automatic fallback, synthetic paid probes or success-seeking repetitions.
- New private sibling `runtime-records/h2-business-r13-20261020` output.
  Reuse the hash-verified article/company snapshot from
  `data/acceptance/h2-code-fix-r1-20261019` with its original research day.
- Freeze source/test/design hashes before any paid request; compare before
  every next case and at completion. Never edit frozen inputs during the run.
- Retain full H2 SQLite, public artifacts, tool results, events, checkpoints,
  model-call and budget ledgers, final answer and finance state. Save the
  original Query, applied config, model profile, input versions and host clock.
- Export in `finally`, including host interruption, without inventing a
  Runtime terminal state. Authentication/quota failures stop remaining paid
  cases instead of repeating the same environmental failure across the suite.
- Classify content, evidence and workflow separately in the report. No
  automatic semantic scoring. `succeeded` is not a financial correctness pass.

## Verification

Offline fixtures must prove preflight never reaches a provider, Q26 preserves
both texts, source changes are rejected, interrupted exports survive, and the
existing H2 driver still uses durable Attempts, bounded calls and full results.
Rerun scoped H2 and default-finance regression before freezing the live batch.
Real Redis, production routing, load, business-adapter parity and semantic
review completeness remain separate gaps, not silently changed designs.

## Pre-Freeze Offline Evidence

- Broad H2/state/compatibility/driver collection: 670 passed, 7 skipped,
  307.35 seconds. Skips require real Redis.
- Final acceptance-driver collection, including two additional complete-catalog
  fixtures: 50 passed, 34.18 seconds. This overlaps the broad collection;
  do not sum the results as independent tests.
- Default finance/shared runtime: 1606 passed, 30 skipped, 188.06 seconds.
- Configuration/separation: 10/10. Scoped Ruff and compile checks passed.
- Existing Starlette/httpx deprecation warning remains. No offline check called
  a paid provider; live results are reported separately after the batch.

## R13 Completed

See the [R13 business report](../acceptance/harness-v2-live-acceptance-r13-20261020.md).
All 30 catalog entries were recorded: 20 executed once on DeepSeek/FakeRedis,
9 blocked on missing capabilities, and Q29 deferred to an isolated environment.
There were 331 physical calls, 18 Root deliveries and two contract-error failures
(Q16/Q18). All per-case accounting/artifact checks passed; 382 frozen source
files and 898 snapshot files remained unchanged. The correction-transport
retransmit branch did not trigger. Neither delivery nor structural audit proves
semantic acceptance: retain Q12's invented-source failure and the source-backed
counting, time/condition, probability and author-attribution findings. No extra
paid rerun or new Runtime policy was introduced after this batch.
