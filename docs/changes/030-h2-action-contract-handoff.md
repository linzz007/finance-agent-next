# H2 Action Contract Handoff And Root Correction

Date: 2026-10-19

Status: implement existing contract visibility; Root correction explicitly
approved by the user on 2026-10-19 before implementation.

## Observed Gap

The fixed R4 batch used the actual H2 service, DeepSeek and FakeRedis. Q04,
Q08 and Q14 all ended without delivery. Q04 returned JSON missing one closing
brace. Q08 returned an unsupported action wrapper; Q14 invented delegate
fields. Five preceding article calls used unsupported parameters. H2 showed
names but did not implement the approved on-demand tool.describe interface or
provide precise tool failure feedback. These are not Redis outage findings.

Authority: 013 sections 8-10, the H2 interface sections 2.1/2.3/7-8, change 010
progressive descriptions, and change 026 state facts. Do not add aliases,
financial keyword routing, fixed sample counts, new review gates, or a model
fallback to make these cases pass.

## Existing-Contract Repairs

- Keep the initial Root prompt concise: literal public action examples, a short
  capability catalog, and tool.describe(name). Detailed article/action Schemas
  are returned only on demand, derived from the validators used at execution.
- Reuse the existing article input models. Preserve H2's bounded read default
  and maximum of 100 lines. Validate unknown fields/types before corpus access;
  never silently ignore an end_line, plural article_ids, or unrelated fields.
- Return a failed child Result containing a sanitized contract diagnostic and
  the description lookup to use. It remains failed, not successful evidence.
  Root decides whether to correct or stop. Failed reads do not mark an article
  complete, used, or create evidence bindings.
- Supply the delegated research task, role, success criteria and actual output
  Schema to the child. Do not ask for finance.research_result.v1 without telling
  the model its structure. Support the already accepted legacy analyst action
  in the trusted role registry as well as analyst@2.0.0.
- Use the existing strict text-action parser at the single-request adapter
  boundary, including its approved single-DSML-tool compatibility. Do not salvage
  arbitrary prose, multiple actions, native tool calls or incomplete streams.

## User-Approved Root Correction

The user approved: "Root, same model and original budget, at most one format
correction, record both calls". This replaces the previous no-correction rule
only for a Root decision with a completed public output and contract_error.

1. Preserve the failed public output artifact and charged physical-call row.
2. Pass the original task/state, failed output (as diagnostic data), action Schema
   and an explicit format-only instruction to one more capabilities.complete.
3. Use the same immutable Attempt model snapshot and ordinary remaining budget.
   No old retrying gateway, new budget, provider fallback, or hidden third call.
4. Validate the corrected output normally before any tool/delegate side effect.
   A second failure is reported honestly; no bracket insertion or field guessing.
   Do not restart the whole decision after the one correction fails, including
   a transport failure during that correction. Initial transport retry policy
   outside this correction remains unchanged.
5. Link original/correction call IDs and diagnostic artifact in Root state on
   success. The ledger and artifacts retain both attempts even if correction fails.

No correction for transport/provider/quota/permission/cancellation failures,
unknown tools, tool argument errors, missing public output or truncated streams.
An invalid optional terminal state_update still uses 028 body retention, not
regeneration. Non-Root retries are unchanged.

## Verification

- Described Schemas and execution validators agree; initial input has no full
  business Schema; undescribed/unknown names do not grant access.
- Invalid fields/types cause zero corpus access and a failed, useful child
  observation. Correcting a later action is a new model choice, not auto replay.
- Root format correction succeeds with two ledger rows on the same model; two
  malformed responses stop after two, with no side effects or refunded calls.
- Quota/transport/invalid final deltas retain their separate existing behavior.
- A child receives its exact task and output contract. Shared 1.0 is unchanged.
- Run a new finite Q04/Q08/Q14 batch only after offline checks. Keep R4 unchanged.

This change does not finish the separate scope audit of parallel financial
dispatch, nested child action loops, or real Redis/process-failure validation.

## Offline Results Before R5

Final-source H2 regression: 242 passed / 7 skipped, 98.37 seconds. This includes
the actual H2 runner, same-model one-shot Root correction and a failed correction
transport case that must not restart the decision. The focused 39 passing cases
overlap this count; do not add them. A test-only DSML marker typo was corrected
to match the already approved parser, without expanding accepted syntax.

Unchanged shared finance regression: 1606 passed / 30 skipped, 152.03 seconds.
Configuration and project-separation checks: 10/10. Ruff on dispatch and affected
runner/tests passed; git diff --check found no whitespace errors. The inherited
Starlette TestClient deprecation warning remains. No paid calls were made by
these checks, and skipped Redis/process scenarios remain unverified.

Next fixed live batch: Q04/Q08/Q14 once each, explicit deepseek, FakeRedis,
verified existing snapshot, normal configured budgets. Store it separately as
runtime-records/h2-live-r5-20261019; do not edit frozen sources during the batch.
R4 remains unchanged at zero of three deliveries. Offline success is not a
prediction of semantic acceptance or overall baseline completion.
