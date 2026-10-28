# H2 R19 Child60 Diagnostic

Date: 2026-10-20. Status: fixed diagnostic completed; independent budgets apply,
but the requested six-article child research/return path fails before reading.

## Scope

[069](../changes/069-h2-child60-live-diagnostic.md) freezes a separate B01 fixture
because [R18](harness-v2-live-acceptance-r18-20261020.md)'s unchanged Q08 selected
no child. This is not a Q08 retry or a new universal delegation requirement.
DeepSeek (`deepseek-flash`) and FakeRedis only; same isolated R17 article snapshot.
Root120 / child60 / Run600 / 60 minutes with original reserves. One execution;
no model switch, new child after failure, added correction or top-up occurred.

## Actual Result

| Item | Result |
| --- | --- |
| Root | succeeded; delivered an honest failure explanation and metadata table |
| Child | failed / contract_error, no accepted ResultRef |
| Duration | 131.76 seconds |
| Own requests | Root 9; child 2 |
| Run requests | 11, all physically completed and charged |
| Child persisted allowance | self_call_limit=60; 20-minute duration |
| Final answer | 1,850 characters; no substantive six-article analysis claimed |
| Original article reads | zero |

The child is an analyst@2.0.0 assigned a source-review task, not a new reviewer
role or structured finance-review implementation. Root correctly locates the
specified six article IDs, creates exactly one depth-one Task and does not read
the bodies itself. After failure it does not invent source observations, expand
the grant or create replacement work.

## Failure Attribution

1. The child's first public response uses provider-specific DSML calls/invoke
   markup for tool.describe. It is not public JSON and executes no tool.
2. The one permitted correction receives the original trusted task/materials,
   outer action Schema, inner ResearchResult Schema and isolated syntax feedback.
   It returns a valid JSON final envelope with an honest missing-body explanation.
3. Independent validation of that exact saved final reproduces the actual inner
   error: `evidence link text must occur verbatim in content/candidate_answer`.
   Its six evidence-link text values are newly phrased identity claims absent
   verbatim from its content. Correct JSON syntax does not make those anchors valid.
4. The correction has already been used. Approved 037/065 require a second
   structural/anchor error to end the decision. The Task correctly fails with
   contract_error; it does not receive a third semantic correction or execute DSML.

This is model public-protocol/anchor compliance failure under the current
one-correction policy, not budget exhaustion, provider unavailability or a Redis
fault. The first adapter event carries contract_error; the correction is rejected
later by domain validation, so the physical ledger alone shows only one error
code. Both responses remain physically succeeded and charged. No article tool
actually runs, even though the first response names tool.describe.

Raw outputs remain in audit. They do not become an accepted result or evidence.
Changing to more corrections, accepting provider markup, or accepting invalid
ResearchResult anchors would change the approved recovery contract; none is done
in this run. The current child prompt already contains an explicit JSON tool
example, the schema scope, and the verbatim binding requirement. A missing example
is therefore not supported as the cause of this occurrence.

## Verified Scheduling And Accounting

All twenty read-only structural checks pass: identities, one charge per request,
independent counters, nonnegative balances, no prepaid sixty-call transfer,
Run600 conservation, local caps, original deadlines, persisted closing reserve,
settlement, model binding, artifact hashes and SQLite foreign keys.

The child's two calls leave Root's own usage at nine, while aggregate usage is
eleven. Root has 111 personal requests left, the child 58, and the Run 589. The
budget is not the reason the child stops. This verifies that live child60 policy
and independent accounting are active. It does not verify a successful live child
that uses more than eight calls; that branch is not_triggered. The corresponding
six-article mock path remains the offline evidence in 067.

Of the diagnostic outcomes, the exact depth0/1 tree, no Root body reads and honest
Root delivery hold. Six complete child reads, accepted child research return and
complete Root consumption fail. Delivery of a failure explanation is not delivery
of the requested financial analysis. No structured review is saved.

The pre-documentation audit confirms all 424 frozen source/design/config/test
entries, 898 snapshot files and the private runner hash match. All eleven physical
requests have public-input captures; the one correction obeys input isolation.
No live fence or correction-transport retransmission occurs. The audit uses zero
model calls. Private runner and audit pass Ruff.

## Records And Next Boundary

Private sibling runtime-records folders:

- h2-child60-r19-20261020: frozen manifest/provenance, original snapshot, temporary
  SQLite DB, Task/Attempt/event/budget ledgers, complete outputs/inputs and answer.
- h2-child60-r19-protocol/run_batch.py: exact diagnostic instruction and wrapper.
- h2-child60-r19-audit/audit.json: terminal structural and failed-path evidence;
  preserve its pre-edit integrity result.

No Runtime code, default 1.0 route, live DB/corpus, Docker, model policy or Git
commit/push changes occur. No further paid rerun follows this fixed case.

067's budget change works on the triggered creation/accounting boundary. Full
child analysis remains unproved by this live attempt. Further recovery policy,
two-level usable evidence handles, model-owned coverage/review, real Redis,
business-adapter completeness and production integration remain separate open
items. Full goal acceptance is not complete.
