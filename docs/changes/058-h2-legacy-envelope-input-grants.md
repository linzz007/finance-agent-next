# H2 Legacy Envelope And Input Grants

Date: 2026-10-20. Status: existing-contract repair implemented and offline-verified.

## Authority And Reproduction Target

[013](013-harness-v2-dispatch-baseline.md), section 8.1, and the
[spawn contract](../harness-v2/interface-contracts.md), section 3, require the
child to receive the explicitly authorized inputs and necessary parent context.
[017](017-harness-v2-finance-loop-integration.md), section 5, preserves that
boundary; [032](032-h2-durable-action-continuation.md) retains input grants
across continuation. [057](057-h2-text-parent-context-handoff.md) repairs text
decoding, not selection of the initial material list.

Repository.execution_input builds the trusted TaskBrief.input_refs from the
accepted Task, including both spawn input_refs and parent_context_refs. However,
Before repair, FinanceRootExecutor's legacy child-request branch replaced that list with the
first request artifact's embedded input_refs when nonempty. This discarded other
authorized materials and let a data envelope nominate ungranted material refs
to the financial state. Later capability checks still refuse forbidden loads;
this is not evidence of a successful unauthorized source read.

## Repair Boundary

- Keep the compatibility request's task/action interpretation as before, with
  the existing new-assignment behavior for terminal follow-ups.
- When recognizing the first finance.h2.child_request.v1 artifact, remove only
  that control envelope from the model's source-material list. Retain every
  remaining ref in the trusted TaskBrief order, including parent context.
- Do not use the envelope's nested input_refs as a replacement grant list.
  No nested ref can add, remove or reorder trusted material permissions.
- Keep ordinary explicit-spawn initialization, strict action/checkpoint/result
  JSON, capability load checks and ResearchResult source binding unchanged.
  A context note remains context, not original article evidence.
- Do not change the database, queue/Attempt semantics, model policy, budgets,
  financial methodology, correction policy or default 1.0 entry.

## Verification Plan

First reproduce subset-grant loss and foreign-ref nomination before the runtime
repair. Cover current research roles/depths and terminal follow-ups, and verify
the compatibility task survives only when appropriate. Exercise actual
SQLite/FakeRedis/mock-provider spawn with the envelope plus separate parent
context. Prove extra granted sources remain readable, ungranted sources remain
refused, no envelope becomes source evidence, and a fresh Service continues from
the checkpoint without reinitializing from the legacy payload.

Check exact physical-request accounting and compact state. Run complete focused
modules, broad H2/state/compatibility/live-driver and default finance regression,
configuration/separation, scoped full Ruff, documentation links and diff checks.
Use synthetic artifacts and temporary DBs only; this deterministic defect needs
no paid provider requests. Preserve R13 and the pending correction-input and
closing-mode design decisions.

## Actual Focused Verification

Before the runtime change, twelve initialization cases and both actual
SQLite/FakeRedis/mock-provider integration cases failed. The initialization
cases exposed lost parent notes/extra materials and replacement with a foreign
ref; the integration cases failed on the absent note in actual child input.
No paid provider was involved.

The complete three related modules then passed **218 cases**, 78.75 seconds,
including fourteen new cases. Initialization covers analyst/verifier at depth
one, skeptic at depth two, ordinary assignments and terminal follow-ups. The
original compatibility task remains only for the ordinary assignment, and the
envelope is not retained as a source material.

Actual SQLite cases exercise two independent boundaries:

- A subset-only envelope retains the separately granted note and extra article.
  An ungranted article read is refused, then both assigned articles are read.
  Both Tasks succeed with seven mock physical requests.
- An envelope naming a root-owned but ungranted article does not include it in
  child model materials or scope. Its read is refused; the actual assigned
  article remains readable. Both Tasks succeed with six mock physical requests.

Both cases reconstruct the Service after the refused read, then continue from
the existing checkpoint. Physical-request ledger checks, immutable model policy
and compact Checkpoints pass. These are fresh Service instances, not fresh OS
processes or real Redis. Previously saved incomplete states are not rewritten
or automatically migrated by this initialization-only repair.

Actual final verification:

| Check | Result |
| --- | --- |
| Focused root/research/continuation modules | 218 passed, 78.75 seconds |
| H2/state/compatibility/live-driver | 776 passed / 7 skipped, 256.18 seconds |
| Default finance/shared runtime, 52 modules | 1631 passed / 30 skipped, 120.94 seconds |
| Configuration/separation | 10/10 |
| Full Ruff on changed runtime/tests | Passed |
| Git diff whitespace check | Passed |

The focused results overlap the H2 collection; do not sum them. Default finance
regression above was actually executed after this source change. Configuration
10/10 was rechecked again before post-R14 repair 060. The seven H2 skips
still require real Redis. No paid provider request, live-data mutation, Docker
operation, default-route switch or Git commit/push occurred. R13 records and the
pending correction-input/closing-mode designs remain unchanged.
