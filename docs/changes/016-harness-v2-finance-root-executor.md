# Harness 2.0 Finance Root Executor

Status: implementation record for the approved Harness 2.0 dispatch baseline.
Design version: `H2-FINANCE-ROOT-2026-10-18-v1`
Applies to: `finance-agent-next`

This change maps the existing financial research workflow onto the durable
Harness 2.0 scheduler. It does not replace the default 1.0 `AgentLoop`, enable
paid model calls, or add a public HTTP route. The new path is opt-in until a
later integration decision.

## 1. Problem And Boundary

The dispatch foundation can already create and recover a generic root task,
but a financial root task must also preserve the position of the research
itself. A root task must not pretend that a new worker invocation is a clean
conversation, and it must not replay completed article reads, delegated work,
or reviews merely because a process was restarted.

The root executor therefore owns only the bridge between scheduler state and
the existing finance domain. It does not decide financial methodology. The
main Agent remains responsible for planning, article depth, delegation,
coverage, review targets, and delivery wording. Runtime remains responsible
for leases, permissions, budgets, contracts, immutable artifacts, and trace.

The boundary is:

```text
user query
  -> h2 run/thread/task/attempt
  -> finance root executor
  -> existing finance planning and tools
  -> analyst/verifier sub-tasks through agent.spawn
  -> durable wait and checkpoint
  -> root resume with observations
  -> candidate/review/delivery state
  -> result.submit
```

The executor does not call SQL or Redis directly. It receives the lease-bound
`ExecutionCapabilities` supplied by `Worker`.

## 2. Financial Root State

Each durable boundary stores one immutable JSON context artifact with schema
`finance.root_state.v1`. The artifact is referenced by a `CheckpointState` when
the root waits and by the final ResultRef section `finance_state` when it
delivers.

The state has these responsibilities:

| Field | Meaning |
| --- | --- |
| `query` | The original user request, unchanged for the run. |
| `phase` | The current domain phase, such as `plan`, `research`, `synthesis`, `review`, or `delivery`. |
| `completed_steps` | Stable step identifiers already committed at a durable boundary. |
| `pending_steps` | Work the next root invocation is expected to consider. |
| `research_plan` | The current plan snapshot; the model owns its content. |
| `article_states` | Per-article discovery/read/use status and versioned references. |
| `evidence_ledger` | Evidence references and the claims or observations they support. |
| `coverage` | Covered dimensions, gaps, counterexamples, and the model's stop rationale. |
| `candidate_answer` | The latest complete candidate, if one exists. |
| `review` | Review targets and findings, if review has started. |
| `delivery` | Delivery status and disclosure information. |
| `revision` | Monotonic state revision within the run. |
| `last_event_id` | The event cursor used when resuming observations. |

These are domain payloads, not a second scheduler. Their contents remain
model-selected and are validated for public JSON and run-scoped artifact
references. A missing or invalid state artifact is a `checkpoint_invalid`
failure; the runtime does not silently reconstruct it by replaying tools.

## 3. One Root Invocation

The root executor is deliberately one-step-at-a-time:

1. Load the initial query from the root mailbox, or load `finance.root_state.v1`
   from the resume checkpoint.
2. Pass the state, newly observed child results, mailbox messages, task brief,
   and lease-bound capabilities to the financial domain step function.
3. The domain step returns either a durable wait decision or a terminal
   `Submission`. It may spawn child tasks through `capabilities.tool` before
   returning a wait decision.
4. Persist the updated state as an immutable context artifact.
5. For a wait, create a `CheckpointState` pointing to that artifact and let the
   scheduler suspend the current Attempt. The scheduler, not the model, wakes
   the root when child tasks or mailbox input make progress.
6. For a terminal result, attach the state artifact as the
   `finance_state` section and let the normal Worker result path preserve the
   complete candidate, metadata status, and evidence references.

There is no hidden loop around the domain step. A model or tool failure is a
real failed Attempt and is recorded by the existing execution ledger. A later
retry/resume must use an explicit checkpoint and idempotency keys; it must not
re-run an already committed step merely to rediscover its position.

## 4. Child Task Mapping

The root uses the existing `agent.spawn` contract for article analysts,
comparators, verifiers, or skeptics. Each child receives a narrow TaskBrief
with explicit input references and output contract. Child results return as
observations; the root decides whether they are used, excluded, or require a
follow-up.

The scheduler does not infer financial meaning from `task_type`, role names, or
keywords. The finance adapter may choose role identifiers, but Runtime only
checks the role registry, permission snapshot, depth, budget, deadlines, and
artifact scope. The existing maximum depth of two remains in force.

## 5. Failure And Delivery Semantics

- A lease loss, persistence error, or provider failure cannot be turned into a
  successful financial answer.
- A review disagreement does not discard the candidate. The final result may be
  `partial` and must retain the author's view, contrary evidence, gaps, and the
  review state.
- Incomplete result metadata uses the existing one-time metadata repair path;
  if repair fails, the full body is still delivered with
  `incomplete_metadata`.
- Model selection remains explicit per run/Attempt. The root executor never
  automatically switches from GPT to DeepSeek or the reverse.
- SQLite remains the authority. Redis contains dispatch messages only; root
  state, article content, evidence, candidates, and reviews remain immutable
  run-scoped artifacts referenced by SQLite.

## 6. Compatibility And Rollout

The implementation is an importable executor and offline test fixture only.
The default finance entry point continues to use 1.0. A later integration
change must add an explicit feature flag, create the H2 run before the first
model call, connect the existing finance tools through capabilities, and add
live acceptance evidence. That change must not be hidden inside this bridge.

## 7. Acceptance Matrix

| ID | Check | Evidence |
| --- | --- | --- |
| H2-F01 | Initial root invocation creates a versioned finance state and can suspend on child work. | Root executor test with a real child Task. |
| H2-F02 | Resume loads the saved state and child observations without replaying the initial query or child spawn. | Two-phase Worker test and dispatch events. |
| H2-F03 | Terminal delivery retains the full candidate and `finance_state` reference. | Result and artifact integrity assertions. |
| H2-F04 | Invalid state, cross-run refs, lease loss, and metadata failure remain visible failures. | Focused contract tests plus existing dispatch suite. |
| H2-F05 | Default 1.0 behavior and the 1.0 regression suite remain unchanged. | Existing finance regression. |

This record is not evidence that the public financial path is already on
Harness 2.0. It records the bridge and its tested boundary only.
