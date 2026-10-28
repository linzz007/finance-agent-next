# H2 Trusted Task Identity Context

Date: 2026-10-20. Status: existing-contract defect repaired and offline-verified.

## Authority And Defect

[013](013-harness-v2-dispatch-baseline.md), sections 3 and 8.1, separates
Thread, Task and Attempt and requires an isolated Runtime-generated TaskBrief.
[H2 interfaces](../harness-v2/interface-contracts.md), section 3, requires
run/thread/task/parent/depth and Trace correlation in that brief. Identity and
permissions remain Runtime-owned, not model-supplied action fields.

Repository.execution_input already constructs those trusted fields, but the
shared finance domain copies only allowed tools and budget/deadline into its
model-facing context. Root and research models consequently cannot identify
their current Task/Thread or distinguish a new Attempt from the same Task by
their own input identity. Existing scheduler enforcement remains correct; this
is an input handoff omission, not evidence of a permissions bypass or an
established cause of any R13 semantic failure.

## Repair Boundary

- On each domain invocation, refresh a compact `execution.task_identity` from
  the current trusted brief, replacing rather than merging saved values.
- Whitelist only `run_id`, `thread_id`, `task_id`, `attempt_id`,
  `parent_task_id`, `parent_thread_id`, `role`, `role_version` and `depth`.
  Preserve explicit null root parents; do not infer absent fields from source
  materials, mailbox text, saved state or another Task.
- Include that same `task_identity` in the model-facing user context shared by
  Root and depth-1/2 research roles. Keep it when optional source bodies are
  omitted under actual input pressure. This adds identifiers, not full schemas
  or another instruction document to every prompt.
- These values describe the current executor only. They do not grant access
  to an ancestor/sibling, allow an action to override CallerContext, expose
  lease tokens or credentials, change a budget, or promise private reasoning.
- Preserve prepared-action recovery without an additional model decision,
  existing action validation, result retention and the default 1.0 entry.

## Verification Plan

First reproduce missing model-facing identity with Root and each research role
at depths one/two. Seed stale identity in serialized state and verify all
current values come from the trusted brief, including null parents and the new
Attempt; unrelated/private fields must not be copied. Under input pressure,
retain identity while omitting optional source payloads.

An isolated SQLite/FakeRedis/mock-provider run must show actual Root and child
identities over multiple durable action Attempts, checked against database
lineage and the physical call ledger. Do not claim a real-Redis, multi-process,
paid-model or financial-quality result from this fixture.

Run focused tests, broad H2/state/compatibility/live-driver regression, default
finance compatibility, configuration/separation and scoped Ruff/diff checks.
Record actual results below after completion, without rewriting R13.

## Implementation And Actual Results

The runtime change is confined to the shared finance domain: refresh the
nine-field whitelist before either a new decision or a prepared-action replay,
and expose it in the shared user context. No scheduler identity, authorization,
budget, retry, DDL, default-entry or financial research rule changed.

All eight initial missing-identity cases failed before the runtime repair.
An intermediate two-case fixture failure came from an existing budget assertion
being displaced during test insertion; it was restored to its original test.
The final eleven new cases pass: seven role/depth cases with restored stale
state, one absent-field case, two pressure cases and one durable integration.

The integration performs five mocked physical requests for two Tasks, uses an
Agent's observed own Task ID for status, and replaces the Service instance
between two child Attempts. Every model-visible identity matches the actual
call ledger and database lineage; Thread/Task remain stable while Attempt
changes. The final stored finance_state contains the current Root identity.
This is an honest no-source identity fixture, not article-analysis quality
evidence, process-kill testing or a live Redis/model test.

| Completed check | Actual result |
| --- | --- |
| Eleven focused identity/pressure/durable cases | 11 passed, 4.15 seconds |
| All dispatch + state-update + H2 compatibility/live-driver | 712 passed / 7 skipped, 254.55 seconds |
| Default finance/shared-runtime, 52 modules | 1631 passed / 30 skipped, 126.10 seconds |
| Configuration/separation | 10/10; zero model calls |
| Full Ruff on the three changed Python files; Git whitespace check | Passed; existing CRLF notices remain |

Collections overlap and must not be summed. Seven H2 skips require real Redis;
the existing Starlette/httpx deprecation warning remains. The full inherited
repository collection was not rerun. No paid request, live-source modification,
Docker operation, default-route switch, Git commit or push occurred.

The [R13 report](../acceptance/harness-v2-live-acceptance-r13-20261020.md)
is unchanged. This repair does not certify model correctness, all-child success,
network recovery, missing business adapters or commercial readiness. Overall
acceptance remains incomplete; the design questions below stay separate.

## Separate Design Gap Found During Audit

The budget layer supports `complete(..., final=True)` to use the original
closing reserve. The shared finance decision path never selects that mode;
normal calls stop at the protected call/time boundary. Existing driver tests
explicitly preserve this behavior. The selected reserve is therefore protected,
but the model-driven path has no defined transition into reserve-backed closing.

[SQLite budget semantics](../harness-v2/sqlite-schema.md), section 5.2,
requires closing reserves to be usable only after entering closing. It does not
define the finance model's transition, permitted closing actions, or correction
handling in that phase. Those decisions need a separate design confirmation;
do not mark all calls `final=True`, spend the reserve on more research, shrink
it, increase the original cap, or silently change the driver to make tests pass.
No evidence establishes this gap caused Q16/Q18 or their contract errors.

Format-correction input isolation also remains a separate pending user choice.
General authorization for necessary bounded paid testing does not approve a new
provenance policy or an unlimited success-seeking rerun loop.
