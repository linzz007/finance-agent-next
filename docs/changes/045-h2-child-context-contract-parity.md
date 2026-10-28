# H2 Child Context Contract Parity

Date: 2026-10-19

Status: existing-contract repair and offline audit after 044. No new paid run,
retry policy, acceptance rule or model selection is authorized.

## Authority And Findings

[013 sections 8-10](013-harness-v2-dispatch-baseline.md) require shared public
actions for Root and children and truthful result/status handoff.
[Interface sections 4.2-4.3](../harness-v2/interface-contracts.md) define durable
wait observations and trusted status/budget snapshots. [044](044-h2-action-validation-feedback.md)
clarifies input-only action keys and artifact identities, without relaxing validation.

Two implementation gaps remain:

1. The research domain replaces the Root system message. Consequently 044's
   key-placement and article-versus-artifact guidance disappears for all child
   roles/depths, although their inputs contain the same next_action_key and
   known_material_refs and their action validators are the same.
2. Repository.execution_input returns authorized wait-target status, attempts,
   budget, deadline, cancellation and dispatch information. The finance-domain
   observation mapper drops most of this information; result-bearing targets
   also lose their attempt diagnostics. The parent model cannot see those
   supplied facts in the resulting observation without another status request.

These are code-level findings, not new measured live failures. R8 accepted no
wait action; the dropped wait fields therefore do not explain R8's inaccurate
budget-exhaustion statement. Its explicit status action already returned the
budget. Do not claim that richer observations fix model interpretation.

## Repair Boundary

- Reuse one short action-context guidance string in Root and child system
  messages. Include it exactly once after role specialization; detailed tool
  Schemas remain on demand. It grants no permissions or additional recursion.
- Preserve only the existing authorized task snapshot fields in each durable
  observation: task/thread identity, status, attempts, budget, deadline,
  cancel_requested and dispatch. Handle targets with and without results.
  Do not copy arbitrary task fields, lease tokens or raw model outputs.
- Preserve these observations as artifacts across action Attempts. They are
  status data, not source evidence. No-result failures must not create evidence
  or claim a successful read. Existing pressure omission/expansion stays intact.
- A terminal child's remaining/local_available may be zero because unspent
  funds were returned to the parent. Preserve requested/allocated/consumed,
  rather than deriving a new failure cause from its current balance.
- Keep 037's strict ResearchResult structure/anchor checks and one original-
  budget correction. Invalid bindings after schema validation still preserve
  partial bodies; a twice-invalid schema remains failed with raw outputs saved.
  No automatic promotion of failed text into accepted results or Root delivery.

## Offline Acceptance

- All three research roles at both depths receive the shared key/ref guidance
  exactly once, with public action examples and on-demand descriptions intact.
- Targets without results and those with full/omitted results retain authorized
  status/budget fields through observation storage and final prompt assembly.
  Unexpected sensitive fields are not forwarded.
- An actual fixture child with unused granted calls may fail contract checking;
  the Root sees contract_error and consumed fewer than allocated, not an invented
  budget_denied. Both correction calls remain charged; returned funds conserve.
- Run focused checks, full H2 regression, default-finance compatibility and
  configuration/lint checks without model requests or real Redis/Docker changes.

## Verification

Before runtime edits, the new guidance/status tests reproduced **21 failures**.
After repair, research/context/continuation checks passed **159 cases**. Full
H2/state/compatibility/live-driver regression passed **609 / 7 skipped**, 237.74
seconds, twenty-two net additions after 044. The added integration case confirms
contract_error with three consumed of six granted calls, returned unused funds
and truthful Root observation. These tests made no real model requests.

Default finance/shared-runtime compatibility was actually rerun after 045:
**1606 passed / 30 skipped**, 121.69 seconds, 52 modules. Configuration/separation
checks passed **10/10**; scoped Ruff, including the private wrapper/audit, passed.
A six-document check resolved **142 local file links**, not heading anchors or
whole-design semantic consistency. Existing SSL_CERT_DIR/Starlette notices and
real-Redis skips remain. No Docker, migration, default switch or Git upload.

Original R7/R8 outcomes and post-044 results remain separate historical evidence.
The user separately authorized [046/R9](046-h2-post-feedback-live-protocol.md)
after the offline prerequisites. No live success is inferred from these passes.
