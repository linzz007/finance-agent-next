# H2 R9 Post-Feedback Live Revalidation

Date: 2026-10-19

Protocol: [046](../changes/046-h2-post-feedback-live-protocol.md). DeepSeek only,
explicit H2 corpus entry, FakeRedis, original S01/S02 Queries and source snapshot.
Each case once, 120 physical calls per tree / 240 maximum batch; 60-minute Runs
and 24-call/10-minute Root closing reserves unchanged.

## Outcome

| Case | Duration | Physical calls | Research children | Root delivery | Targeted chain/content audit |
| --- | --- | --- | --- | --- | --- |
| S01 single article | 229.07 s | 14 | One succeeded | 3606-character complete answer | Targeted chain passes, content broadly usable with wording caveat below; structured semantic review absent |
| S02 two authors | 304.01 s | 20 | One succeeded, one cancelled on Root failure | None | Fail; no synthesis to inspect |

**Delivery 1/2; targeted scheduling/handoff chain 1/2.** S01's manual content
review is broadly positive, not flawless; do not call this two full business
acceptance passes. These observations do not establish a reliability rate or commercial readiness.
Total **34 physical requests**, including one unknown-outcome transport failure,
all charged. No fallback, added probe, case repetition or post-repair paid run.
This authorization is spent; R7/R8 remain separate, unchanged evidence.

## S01 Trace And Content

- Root identified the dated source and read its complete 63 lines. Its actual
  explicit spawn granted four calls / 600000 ms and handed over authorized
  original text, author/date and source identity. No legacy delegate conversion.
- The analyst used two calls on its first Attempt: an initial ResearchResult
  failed inner anchors and one same-model correction succeeded. Both physical
  completions remained charged; the invalid original was not accepted evidence.
- Root's wait actually persisted and resumed. The child result was too large
  for short automatic injection and remained summary/ref only at wake-up.
  Root explicitly fetched the complete 13046-character result, with actual
  Agent full-read event 174 and the Root Task/Attempt identity. The later user
  export event 190 is not counted as Agent reading.
- The final answer reconstructs the author's causal reasoning, identifies
  assumptions and missing transaction evidence, preserves conditional language,
  and separates critique/alternative hypotheses from the author's assertions.
  It explicitly declines external factual verification of source claims.
- Manual comparison against the original finds no author/date confusion or
  earlier currency-unit overprecision: the answer treats the unspecified loss
  amount's currency and measurement basis as unknown rather than filling them in.
  The alternative explanations are framed as hypotheses, not observed events.
- A local criticism is too absolute: the answer says the original provides no
  statistical window/update timing, although it contains relative timing such
  as the prior year-end, July and the current day. The defensible gap is missing
  traceable data provenance and precise measurement basis, not an absence of all
  timing information. This wording caveat is retained in the original answer;
  it is a model-content limitation, not evidence that the scheduler lost dates.
- Limits remain: only one article, one execution, broad source-level references
  rather than sentence-level citation links. Runtime finance review is null and
  finance delivery metadata is incomplete_metadata even though the H2 Result
  envelope was accepted. Manual review here does not fabricate a saved Agent
  review, external verification, or a complete five-layer financial audit.

S01 therefore demonstrates the requested source-bound child return, durable
wait, explicit full consumption and useful final delivery. Its chain pass is
not certification of flawless content, all optional metadata, tools or production behavior.

## S02 Trace And Failure Attribution

- Root read one actual September article per author, 63 and 59 lines, and
  created separate analyst Tasks/Threads at events 148 and 194 before waiting.
  Each received exactly four calls / 600000 ms and authorized source materials.
  One Worker proves non-blocking dispatch ordering, not parallel throughput.
- The first analyst succeeded after its one anchor correction, using three
  calls including a tool description. The other analyst fully read its article
  in its first call, but was cancelled before another research decision when
  Root failure ended the Run. It did not exhaust its four-call grant.
- Root had described agent.wait. Its next complete public response was two
  separate JSON objects: a wait action followed by a state_update-only object.
  That is not one valid public action, so no wait side effect was accepted.
  This was not the previous R8 extra next_action_key failure.
- Runtime preserved the malformed output and attempted the single approved
  format correction on the same model and Attempt. That correction encountered
  transport_error after about 21.1 seconds, with no complete public output and
  unknown remote usage. The request remains charged.
- [030](../changes/030-h2-action-contract-handoff.md) and
  [037](../changes/037-h2-child-action-envelope-and-budget-errors.md) explicitly
  prohibit restarting a decision after its correction fails, including a
  transport failure. The actual terminal behavior follows that approved rule;
  it is not a newly demonstrated missing retry implementation.
- Neither accepted child result was fully consumed by Root in S02. There is
  no final comparison or retained complete Root candidate to grade. Do not
  present the successful child artifact as a delivered two-author synthesis.

Two earlier Root format corrections succeeded. One specifically received
044's actual extra_forbidden field locations and removed the unsupported
runtime-warning fields. This demonstrates diagnostic propagation in a live
correction, not guaranteed model compliance. JSON parsing failures have no
Pydantic field location and retain the full failed public output for correction.

Changing transport recovery during an ongoing format correction is a design
decision for the user, not permission to add retries, budgets or model switches.
No such policy change was made to obtain a pass. The precise network/provider
root cause is not established by the normalized transport_error alone.

## Accounting And Integrity

The separate read-only audit made zero model calls and verified both cases:

- Every physical request has one charge and the actual Task/Attempt/budget/model
  snapshot association. All selected profiles are DeepSeek; at most two requests
  per Attempt. Original and correction outputs/call records remain available.
- Grants requested equal grants allocated; each tree starts with 120. Transfer
  totals net to zero, no negative balances, all call records settled. Final
  balances 106 and 100 plus the respective 14 and 20 charges each equal 120.
- No refund of started/unknown requests, no hidden grant increase, no carry-over;
  total physical calls 34 stays within 240. Only one request has unknown outcome.
- Artifact hashes match. At batch completion all **389 frozen source/test/design
  files**, article/company snapshot and both private protocol files matched.

The once-only wrapper exited 1 because S02 failed. It must not be restarted.
Private protocol, SQLite, full answers, child work and audit remain under sibling
runtime-records/h2-spawn-r9-protocol, h2-spawn-r9-20261019 and h2-spawn-r9-audit.
S01/answer.md is a faithful post-batch rendering of the saved answer.json, not a
new model response. No credentials, article bodies or complete Trace enter Git.

## Current Limits

Post-045 offline results remain **609 H2 passes / 7 skips**, **1606 default
finance passes / 30 skips**, configuration/separation 10/10. R9 used those frozen
sources; no runtime or Prompt was changed during or after the batch for this
report. The report is new evidence, not another offline test execution.

R9 proves one complete live child-return chain. It does not prove stable
two-author synthesis, live depth-2 recursion, structured semantic review, real
Redis, full web/market/Wiki/holdings parity, the complete 30-Query set or deployed
multi-user behavior. R7's synthesis overstatement findings are not revalidated
by S02's missing answer. Overall baseline acceptance remains open.
