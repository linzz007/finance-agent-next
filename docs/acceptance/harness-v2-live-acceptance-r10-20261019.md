# Harness 2.0 Live Acceptance R10

Date: 2026-10-19  
Protocol: [049 H2 R10 correction recovery live protocol](../changes/049-h2-correction-recovery-live-protocol.md)  
Repair under test: [048 bounded correction transport retry](../changes/048-h2-correction-transport-retry.md)

## Result

R10 completed the two authorized DeepSeek cases once each through
`FinanceHarnessV2Service.for_finance_corpus` with FakeRedis. It used 53 physical
model calls in total: S01 used 16 calls / 264.72 seconds, and S02 used 37 calls /
763.82 seconds. Both Root tasks reached `succeeded` and delivered an answer.
Each case stayed below the 120-call whole-tree limit and the batch stayed below
the 240-call limit. No model fallback, extra run, or budget top-up occurred.

This is a meaningful improvement over R9, but not a complete multi-author
acceptance. S01 passed the targeted single-article scheduling and handoff chain.
S02 dispatched separate author work, received and fully read two successful child
results, but also retained two failed child tasks; the delivered synthesis is
therefore partial evidence of graceful degradation, not proof that every child
or every two-author run succeeds.

| Case | Root delivery | Calls | Duration | Child outcome | Result read by Root |
| --- | --- | ---: | ---: | --- | --- |
| S01 single article | yes, 3704 chars | 16 | 264.72 s | 1 partial child result after correction | 1 full child result |
| S02 two authors | yes, 3323 chars | 37 | 763.82 s | 2 succeeded, 2 failed | 2 full child results |

## Trace And Scheduling

### S01

- Root first established the requested plan, identified TT's 2026-09-30 article,
  and read the complete 63-line source.
- Root used explicit `agent.spawn`, with an analyst task and an explicit budget;
  it did not convert the request to legacy `delegate`.
- The child result was returned through durable wait/resume and was explicitly
  fetched with `result.get` in full before Root delivered the answer.
- The child remained `partial` because its result carried a
  `research_quote_mismatch` warning. The warning and the lack of external
  verification were retained in the final answer rather than hidden.

### S02

- Root read one September gold article for TT and two dated articles for the
  comparison author, then issued separate explicit analyst spawns before
  waiting for results.
- Two children returned accepted results and were each fully read by Root. Two
  other child Tasks ended with contract errors; they did not produce usable
  result artifacts and were not represented as successful evidence.
- Root delivered a comparison that disclosed the article dates, limited sample,
  different information sets, and the distinction between author agreement and
  external fact verification.

The trace shows durable task/attempt progression, explicit budgets, separate
child scopes, result retrieval and final delivery. It does not establish actual
parallel throughput because the run used one Worker; dispatch-before-wait is the
tested property.

## Accounting And Integrity

The read-only R10 audit made zero model calls and passed for both cases:

- DeepSeek was the only selected profile and automatic fallback was disabled.
- Every physical call had one ledger charge, correct Task/Attempt/budget binding,
  and a settled status. Started calls were not refunded.
- Grants, transfers, remaining balances and call charges conserved the original
  120-call tree budget. There were no negative balances or hidden grants.
- Artifact hashes and the frozen source/protocol snapshot matched.
- No real Redis verification was performed; FakeRedis was used as specified.

The live audit reports `correction_transport_retry_status=not_triggered` for both
cases and `attempts_with_three_calls=[]`. This is intentional evidence: neither
model run reached the new transport-retransmit branch. The branch remains
covered by offline tests only, and R10 must not be cited as proof of live network
recovery.

## Content Review

S01 is broadly useful and source-bound. It reconstructs the author's reasoning,
keeps conditional claims separate from the analyst's critique, and says that no
external factual check was performed. Its limitations are the retained quote
mismatch warning, incomplete structured finance metadata, and a remaining
wording risk where a criticism can sound stronger than the article's relative
timing evidence supports.

S02 is materially better than the earlier failed synthesis attempts. It does not
pretend that two sampled articles are an exhaustive author history and it
separates dates, horizons and information sets. However, two child analyses
failed, no structured semantic review was persisted, and the answer still needs
manual checking for any overstrong summary of gold, rate or macro claims. An
accepted EvidenceRef proves source binding, not that the financial proposition
is true.

## Acceptance Matrix

| Dimension | S01 | S02 | R10 conclusion |
| --- | --- | --- | --- |
| Explicit spawn and budget handoff | pass | pass | demonstrated |
| Durable wait/resume and result return | pass | pass for 2 returned children | demonstrated with partial degradation |
| Root complete read before delivery | pass | pass for 2 successful children | demonstrated, not exhaustive |
| Root delivery | pass | pass | 2/2 deliveries |
| All requested child analyses succeeded | n/a | fail, 2/4 | not demonstrated |
| Transport correction retransmit | not triggered | not triggered | offline only |
| Real Redis | not run | not run | unproved |
| Semantic financial correctness | manual review required | manual review required | not certified |

## Remaining Issues

1. The live model can still emit contract-invalid actions or fail a child task;
   the runtime preserves the failure and may deliver partial analysis, but this
   is not the same as reliable completion.
2. The new transport retry has no live provider/network evidence. A future
   controlled run should inspect the branch only if it occurs naturally or in a
   separately approved fault-injection environment; it must keep duplicate
   charge accounting because a remote result may be unknown.
3. Real Redis, the full 30-query business suite, sustained multi-user load,
   production workers, and broad web/market/Wiki/holdings parity remain outside
   this batch.
4. The default 1.0 route was not switched. R10 validates the opt-in Harness 2.0
   scheduling path only.

R10 is therefore **passed for the demonstrated scheduling, accounting and
delivery properties; partial for multi-author research quality; and unproved for
live transport recovery, real Redis and production reliability**.
