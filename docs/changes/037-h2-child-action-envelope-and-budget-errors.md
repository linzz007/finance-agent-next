# H2 Child Action Envelope And Budget Errors

Date: 2026-10-19

Status: existing-contract repairs after the fixed R7 batch, plus the user's
subsequent approval of one child format correction within its original budget.
Neither decision authorizes another paid run or a budget increase.

## Evidence And Authority

The user-authorized [036](036-h2-explicit-spawn-live-protocol.md) batch completed
S01/S02 once each using DeepSeek and FakeRedis. Both delivered a Root answer,
but none of five child Tasks returned an accepted research result. All 377
frozen source hashes, the article snapshot and private protocol matched when
the batch ended. Preserve those outputs and the original failure classifications.

- Two S01 children returned malformed provider-specific tool notation rather
  than the public JSON action. S01's third child and S02's TT child returned
  bare ResearchResult objects, missing the required final action envelope.
  Independent validation also found evidence-link text absent verbatim from
  content/candidate_answer in both objects; wrapping alone would not fix them.
- The child system prompt replaced the Root system prompt. It mentioned a
  shared envelope but omitted literal action examples, and did not explicitly
  distinguish the outer action Schema from the inner output_json_schema.
- S02's other child spent its exact two-call grant on tool.describe and
  article.read. The next model reservation was denied before HTTP dispatch;
  Worker mapped the known budget error to internal_error. Do not call this a
  provider outage or silently replenish its grant.

Authority: [013 sections 7-9](013-harness-v2-dispatch-baseline.md),
[interface sections 2.3 and 7-8](../harness-v2/interface-contracts.md),
[030](030-h2-action-contract-handoff.md) and
[033](033-h2-explicit-delegation-budget.md). These already require public action
contracts, exact child output handoff, explicit budget rejection and truthful
failure observations. The original Root correction is unchanged; the extension
below was approved separately after R7, not part of its frozen execution.

## Implementation Boundary

1. Child prompts explicitly require one public JSON action with top-level type,
   no prose or provider-specific tool syntax. Include a literal tool_call and
   final example. output_json_schema describes final.output only; the whole
   response uses agent_action.v1. Retain the existing schema field for callers.
   Existing parser compatibility is unchanged, not broadened or removed.
2. Retain strict execution validation. Do not auto-wrap a bare result, execute
   malformed provider markup or salvage failed child text into a successful
   ResultRef. Raw public output stays in the ledger. Only the approved bounded
   model correction below may make a new candidate.
3. Preserve budget_denied from Worker through Repository failure persistence.
   No new Task status, DDL, automatic retry, replenishment, refund of a sent call,
   model fallback or reduced grant. Parent status/wait sees the actual cause.
4. Do not modify the frozen answers to repair semantics. R7's comparison includes
   unsupported long-term agreement, overconfident author paraphrases and a
   two-article sample description despite three actual read/used articles.
   Runtime source bindings do not certify semantic entailment. These remain
   acceptance failures, not a reason to discard the full answer or tighten gates.

## User-Approved Child Correction

After reviewing R7, the user explicitly approved one research-child correction
on the same model and original requested budget. It covers analyst/verifier/
skeptic at either supported depth and both legacy and explicit spawn entries.

1. A complete public response with an invalid action, or a final whose inner
   ResearchResult fails strict structural/anchor validation, is eligible once
   per decision. No complete saved public output means no correction.
2. Preserve the original public artifact and physical-call charge. Supply that
   diagnostic as data, the outer action Schema and the inner final-output Schema
   to one more capabilities.complete on the same Attempt model snapshot.
3. Correct only structure, required fields and verbatim evidence anchors. Keep
   useful analysis, author uncertainty and gaps; do not authorize new research,
   invented observations/evidence, permission expansion or changed conclusions.
   Invalid evidence metadata after schema validation still uses existing partial
   preservation, not more model calls.
4. Both physical calls are charged independently. A schema-valid HTTP completion
   rejected by domain validation remains physically succeeded; its separate
   repair diagnostic identifies the inner-validation failure. Do not rewrite
   the physical ledger to suggest the request was not sent or was free.
5. The second candidate is strictly validated as well. Any second error ends the
   correction; a transport error during it must not replay the original decision.
   No correction for provider/quota/transport/permission/budget errors. If the
   remaining grant is exhausted, the correction is denied before another HTTP
   request and budget_denied remains visible. No automatic fallback or top-up.
6. Link original/correction call IDs and public artifact refs using the existing
   action_repair artifact/state format. Do not expose private reasoning. Failed
   repairs retain their call/output records and never become accepted evidence.

## Offline Acceptance

- All research roles, both depths and supported entry forms receive unambiguous
  outer/inner contract guidance. Wrapped output remains valid; bare results and
  malformed tool notation remain rejected with no tool side effects.
- Exhausting a two-call child grant produces failed/budget_denied, exactly two
  physical requests and charges, no third request/refund/retry. Root observes
  the same cause and retains its own remaining funds and closing reserve.
- Check refusal at the reserved-time boundary separately; unknown internal errors
  stay internal_error rather than being relabeled as budget failures.
- Test outer and inner correction, two bad candidates, forbidden failure classes,
  no complete public output, no remaining calls and transport failure during the
  correction. Assert physical-call accounting, same model/Attempt and no third
  request or tool side effects from rejected responses.
- Run H2 regression, unchanged default-finance regression and separation checks.
  Record new evidence as post-R7 offline-only. Successful child return with a
  real model still needs separately authorized live validation.

Automatic minimum grants and early-finalization calls are separate unapproved
choices. The user approved bounded child correction, not these budget policies.

## Verified Offline Results

- Before fixes: the new prompt/classification checks reproduced eight failures
  and one passing unknown-error control. After repair, all nine passed.
- Final H2/state/compatibility/live-driver collection: **505 passed / 7 skipped**,
  242.08 seconds. Compared with 035, twenty net added cases cover both-depth/
  three-role guidance, budget/time refusal, unknown failures, forbidden correction
  classes, missing public output, one-shot correction and durable integration.
- The four adapter/SQLite integration variants exercise a bare result, invalid
  inner anchors, two invalid candidates and insufficient remaining calls. The
  actual two-call child exhaustion case separately proves truthful parent
  notification and no third HTTP request. Requests use httpx.MockTransport and
  FakeRedis, not real models or a live Redis service.
- Default finance/shared-runtime regression: **1606 passed / 30 skipped**,
  193.22 seconds across 52 modules. Separation/configuration checks **10/10**.
- Scoped Ruff and git diff --check passed. All 118 local file links checked in
  the six relevant Markdown documents resolved; heading anchors and design
  semantics are not proved by this link check.
- The inherited Starlette/httpx deprecation and SSL_CERT_DIR environment notices
  remain. Seven H2 skips require real Redis. No Docker changes, migration,
  default-1.0 switch, Git commit/push or further paid calls were made.

During new test development, a fixture's tool-name/argument-name collision and
its old one-call-per-Attempt assertion were corrected. Ordinary actions still
require one call per Attempt; only the explicitly tested correction permits a
second call on the same Attempt, with both charges preserved.

These are post-R7 offline results. They do not prove that the revised prompt or
correction succeeds with DeepSeek live, nor resolve R7's semantic overstatement,
sample disclosure or missing structured-review findings.
