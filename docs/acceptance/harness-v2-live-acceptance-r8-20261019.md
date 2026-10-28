# H2 R8 Bounded Spawn Revalidation

Date: 2026-10-19

Protocol: [043](../changes/043-h2-bounded-spawn-revalidation.md), S01/S02 once
each, DeepSeek only, explicit H2 corpus entry and FakeRedis. User-approved
ceiling: 120 physical calls per whole-tree case, 240 total; original 60-minute
Run and 24-call/10-minute Root closing reserve retained.

## Outcome

**Neither case delivered a Root answer or passed the complete chain.** Child
format correction worked in two instances, but Root later failed its own action
contract twice, including the one permitted correction. This is not a pass or
a commercial reliability estimate. No final answer exists to grade for semantic
quality; retained child analyses are not substitutes for delivered Root answers.

| Case | Duration | Physical calls | Children | Accepted child results | Root delivery |
| --- | --- | --- | --- | --- | --- |
| S01 single-article analysis | 274.98 s | 17 | 1 | 1 | No |
| S02 two-author comparison | 420.74 s | 36 | 3 | 1; one failed, replacement cancelled | No |

Total: 53 physical calls, including two S02 transport failures with unknown
remote outcomes. All stayed charged. No fallback, paid probes, case repetition,
extra allowance or post-repair paid rerun. S02's replacement child was selected
by Root inside the original Run/grant, not a repeated acceptance execution.

## Trace And Handoff

- S01: Root listed the dated article, made five rejected spawn proposals with
  unauthorized/non-artifact input references, then read the full 63-line source
  and successfully spawned one analyst with exactly six calls/900000 ms.
  Original article metadata/text reached the child; its completed analysis and
  one correction used two physical calls. Root never read the successful child
  result: both subsequent wait proposals failed outer action validation.
- S02: actual separate author Tasks/Threads were created at events 165 and 223.
  Neither was an implicit legacy delegate. No wait was accepted in this case;
  the two spawns therefore preceded the attempted final wait, but successful
  durable wait/wake is **not** established by this batch.
- The TT child read all 63 article lines and returned an accepted corrected
  result. Root explicitly read its full 7010-character result at event 314,
  identified as the actual Root Task/Attempt and reader_kind=agent, not exporter.
- The other author's full 59-line article was available from Root's source
  read. Its analyst produced useful text twice but failed evidence-anchor
  structure both times. Root learned the failure, spawned a new six-call child
  and that child read the full article. Root then failed its wait action;
  Run termination cancelled the unfinished replacement. It did not exhaust its
  six-call grant. Two physical calls were retained for that cancelled child.
- Both successful child corrections demonstrate real-model repair of the
  content/evidence-anchor schema. The failing child demonstrates it is not a
  reliable guarantee. Original and corrected outputs remain separately recorded.
- One acceptance Worker proves lifecycle progress, not parallel model throughput.
  No depth-2 research delegation, real Redis or full 30-Query acceptance was proved.

## Failure Attribution

### Root Action Contract

Both terminal Root decisions returned tool_call/agent.wait with an extra
next_action_key at the action's top level. The S02 initial proposal also placed
that field inside state_update. The literal correction still retained the
unsupported top-level field. Independent replay through the actual validator
reported extra_forbidden at tool_call.next_action_key. No wait side effect was
accepted and no complete Root candidate answer had yet been generated.

The input exposes next_action_key and says to use it for scheduling writes, but
does not explicitly state that it is input-only and belongs only in the specific
argument field declared by a tool. This is ambiguous protocol guidance, not a
reason to accept unknown action fields or silently mutate model output.

A second implementation gap is confirmed: adapter action validation classifies
the failure as contract_error but discards structured field diagnostics. The
execution capability only passes call_id/output_ref onward. Consequently the
format-correction prompt sees the full Schema but validation_errors=[] for this
outer action error. Inner research-output validation already passes diagnostics.
Existing-contract repair is specified in [044](../changes/044-h2-action-validation-feedback.md).

### Retained Child Content

Both accepted analyses distinguish the author's conditional causal hypothesis
from external verification and describe missing evidence. This is useful retained
work, not proof of a correct final synthesis. S01's accepted child also adds a
currency unit absent from the original sentence while later acknowledging that
the source leaves the currency unspecified. This is an internal overprecision
finding; schema acceptance and quoted-source bindings do not certify semantic
accuracy. Neither accepted result is treated as financial fact verification.

### Child Anchor Failure And Transport

The failed comparison-author child violated ResearchResult's requirement that
each evidence-link text occur verbatim in content/candidate_answer, before and
after its one correction. Its error was not caused by Redis, a missing source
article, budget exhaustion or an external fact-verification gate. Root's later
claim that the failed child exhausted its budget was inaccurate: it used two
of six calls. Failure annotations must be checked against Runtime facts.

Two Root physical requests had transport_error/unknown and were retained in
the ledger. Bounded same-model recovery continued the original task; neither
transport failure was the terminal cause. No claim is made that DeepSeek or
its network is perfectly stable.

Changing the one-correction policy, accepting invalid evidence anchors, or
automatically presenting raw child text when Root fails would change behavior.
They are not silently introduced to make this batch pass. Useful raw analyses
remain in private artifacts, distinct from accepted results and final delivery.

## Accounting And Integrity

An independent read-only SQLite/artifact audit made zero model calls:

- 53 started physical calls, 53 one-call charges, correct Task/Attempt/budget/
  model-snapshot association, only deepseek, at most two calls per Attempt.
- Exact initial grant 120 for each tree; all child grants were six calls, with
  S01 900000 ms and S02 600000 ms each. No silent reductions or additions.
- Transfer totals net to zero, no negative balances. Final remaining balances
  103 and 84 plus 17 and 36 consumed calls each equal 120. All calls settled.
- All artifact hashes matched. At completion all **385** frozen source/test/
  design files, article/company snapshot and both private protocol files matched.
- The batch process ended with exit code 1 because the two cases failed, not
  because a new test should be restarted. Historical R7 records stay unchanged.

Private protocol, exact Queries, SQLite, child text, outputs and audit remain at
sibling runtime-records/h2-spawn-r8-20261019 and h2-spawn-r8-audit. No credentials,
article bodies or complete conversation/Trace contents enter this public report.

## Limits And Next Action

R8 improves observed child return from R7's zero of five to two of four, but
Root delivery regressed from two to zero in these different bounded runs. This
small sample is descriptive, not a measured reliability improvement/regression.
Absence of a Root answer means R7's unsupported consensus, strengthened
conditions and sample-disclosure problems have not been revalidated or solved.

Fix the demonstrated guidance/diagnostic gaps under 044 and verify them offline.
Preserve these exact failed outcomes. Any additional paid acceptance needs a
new user authorization. Real Redis, full business adapters, depth-2 live research
and production load remain separate unproved boundaries. Overall goal stays open.

044 subsequently clarifies input-only key placement and carries bounded actual
validation diagnostics into the existing correction. It does not relax the failed
child's anchor policy or create a final answer for these failed runs. Offline
results are recorded in 044 and the cumulative report, not substituted into R8.
