# H2 Post-Feedback Live Revalidation Protocol

Date: 2026-10-19

Status: separately authorized by the user after R8 and the post-batch
044/045 repairs. S01 and S02 once each, DeepSeek only. This is R9, not an
extension of the spent [043/R8](043-h2-bounded-spawn-revalidation.md) batch.

## Frozen Scope

- Keep exactly the original S01/S02 Queries and article/company snapshot from
  [036](036-h2-explicit-spawn-live-protocol.md) and R8. The private protocol
  comparison must refuse changed Queries before any provider request.
- Use FinanceHarnessV2Service.for_finance_corpus and run_h2_case with explicit
  max_model_calls=120 for each whole task tree, at most 240 for the batch.
  Children, format corrections and transport retries all share that ledger.
  No extra probes, repetitions, carry-over, model/provider fallback or top-up.
- Keep 60 minutes per Run, Root's 24-call/10-minute closing reserve and inherited
  child deadlines. Root selects exact child grants inside this allowance.
- Backend remains FakeRedis. Real Redis is still user-deferred; do not operate
  Docker, production Workers, the default 1.0 entry or live user databases.
- Before execution, finish offline H2/default-finance/configuration/lint checks.
  Freeze source, tests, design, private protocol and snapshot; compare before
  the second case and at completion. Do not edit frozen files during the batch.
- Use new private runtime-records/h2-spawn-r9-protocol and h2-spawn-r9-20261019
  directories; refuse an existing output directory. Preserve R7 and R8 exactly.
  Run each case once, even if the first fails. A crash does not authorize a
  restart of an already charged case. Stop on integrity or accounting violation.

## Acceptance

Apply [043](043-h2-bounded-spawn-revalidation.md)'s separate scheduling,
handoff/return, accounting, content and integrity layers:

1. S01 must actually spawn a source-bound analyst and consume its complete
   analysis before delivering. S02 must actually dispatch separate author
   Tasks before waiting, consume both complete results and synthesize.
2. Inspect actual authorized source reads, short-result injection or explicit
   result.get, real reader Task/Attempt and durable wait/wake. Exporter reads
   do not prove Agent consumption; one Worker does not prove parallel throughput.
3. Audit immutable model snapshots, exact grants, physical charges, conserved
   balances, limits and artifact hashes independently without model calls.
4. Review full answers against source text for conditions, author/date identity,
   sample disclosure, unsupported consensus and numeric/unit overprecision.
   Source binding/schema acceptance alone does not establish semantic truth.
5. Inspect 044's actual diagnostics/correction only if the model selects that
   path. Inspect 045's child action guidance and wait snapshots when exercised.
   A path not triggered is unproved, not automatically failed or passed.

Retain all useful partial analyses and honest failure statuses under the
unchanged strict contracts. Do not accept malformed anchors or introduce a
financial gate to obtain a pass. Delivery, accepted children, evidence coverage
and semantic acceptance are separate. These two cases cannot establish
commercial reliability, live depth-2 recursion or full business-tool parity.

## Preparation And Outcome

Offline prerequisites passed: H2 **609 / 7 skipped**, 237.74 seconds; default
finance **1606 / 30 skipped**, 121.69 seconds; configuration/separation **10/10**;
scoped Ruff including the private wrapper/audit passed. The zero-request private
preflight verified original Queries/snapshot, 120/240 limits, reserves and timing.

Record R9 separately after completion. Any post-R9 repair needs its own offline
evidence; this authorization includes no additional paid rerun.

## Completed Once-Only Batch

[R9](../acceptance/harness-v2-live-acceptance-r9-20261019.md) finished S01/S02
once each. S01 delivered and passed its targeted scheduling/handoff chain in 229.07
seconds / 14 calls; S02 failed without a final synthesis in 304.01 seconds / 20
calls. Its format correction encountered transport_error and stopped under
the approved policy. One of its two children succeeded; the other was cancelled
on Root failure. Total 34 charged physical requests, no fallback or extra run.

All 389 frozen sources, article/company snapshot and private protocol matched
at completion; read-only accounting/artifact checks passed. Overall acceptance
remains incomplete. S01 content is broadly usable but has a locally overstrong
criticism about source timing; no original answer was rewritten. This
authorization is spent; do not restart the wrapper.
