# H2 Explicit Spawn Live Protocol

Date: 2026-10-19

Status: user authorized two DeepSeek targeted acceptance cases, one execution
each, after protocol/source freeze. This is verification of 032/033/035, not a
new financial-analysis method or authorization for more paid repetitions.

## Fixed Scope

- Entry: FinanceHarnessV2Service.for_finance_corpus via the already tested
  run_h2_case helper. A private batch wrapper supplies the two approved custom
  queries; do not relabel them as the unchanged 30-query business catalog.
- S01: explicitly delegate the analysis of one dated TT article to an analyst,
  then explain its reasoning, conditions and gaps from the returned evidence.
- S02: explicitly create separate analyst tasks for TT and the comparison author,
  read their complete returned analyses and compare common/opposite gold views
  in the specified month. Do not wait for the first before requesting the second.
  The query requests delegation; Runtime must not enforce it for unrelated work.
- Each case once, selected/allowed profile deepseek only. No provider/model
  fallback, extra paid probes, repeated batch until success or repair-and-rerun
  authorization. Existing same-model bounded recovery and the one Root-format
  correction remain separately charged under the original contracts.
- Keep SchedulerConfig defaults: whole-tree maximum 600 calls, Run 60 minutes,
  Root closing reserve 24 calls/10 minutes. Model-selected explicit child grants
  must fit the same parent funds/deadline; do not add money or shorten defaults.
- Reuse the verified article/company snapshot used by R6. Independent workspace,
  SQLite database, unique stream and artifacts for each case; no live DB mutation.
  Use FakeRedis explicitly because real Redis remains user-deferred.
- Store exact queries, wrapper, manifest, profile metadata, source/test hashes,
  answers, finance state, SQLite and event/call exports only under the approved
  sibling runtime-records directory. Never copy credentials or private outputs
  into Git. Source and snapshot comparisons run before/after the fixed batch.
- Finish and record each case even if its result is failed/partial. A process
  crash is not permission to replay a partially charged case. Retain any useful
  full output and separate runtime, delivery and semantic-review status.

## Acceptance Layers

1. Scheduling: actual explicit agent.spawn events, distinct child Tasks/Threads,
   bounded exact grants, durable checkpoints/Continue, truthful wait/status and
   child termination. No hidden legacy delegate conversion. One Worker can prove
   non-blocking lifecycle semantics, not parallel throughput or commercial load.
2. Handoff and return: original article author/date/version and allowed material
   references reach the intended child; Root observes real failures/results and
   pulls requested full result pages rather than treating an unread summary as
   a full read. Child private reasoning or unrelated parent history is not needed.
3. Accounting: every physical call references its actual Task/budget/Attempt and
   authorized model. Charge/refund/transfer arithmetic conserves funds; no silent
   budget reduction or unpaid-looking retry. Model/transport failures stay visible.
4. Content: manually compare the saved full answer and relevant source passages.
   S01 should reconstruct the author's argument with conditions and distinguish
   critique from source facts. S02 should preserve both author/date perspectives,
   genuine agreements/differences and missing coverage; agreement is not external
   fact validation. Review/coverage bookkeeping gaps remain visible, not invented.
5. Integrity: sources and private snapshot match their frozen manifest when the
   batch completes. Later report edits are post-batch, not retroactive live evidence.

Do not equate delivery 2/2 with full acceptance, production readiness, real Redis
or a statistical success rate. Record failed criteria and design-versus-code
attribution before proposing further changes.

## Completed Batch

S01/S02 ran once each and the source/snapshot/private-protocol comparisons all
matched at completion. Both Root answers were delivered in 419.87/258.45 seconds
with 33/23 physical calls, but none of five children returned an accepted result.
Full-chain acceptance failed in both cases. See the [R7 report](../acceptance/harness-v2-live-acceptance-r7-20261019.md)
for failure attribution, content review, accounting and explicit proof limits.
Post-batch repairs and the subsequently approved child correction are recorded
in [037](037-h2-child-action-envelope-and-budget-errors.md); they do not alter
the frozen outcomes or authorize another paid batch.
