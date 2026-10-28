# H2 Explicit Delegation Budget

Date: 2026-10-19

Status: user approved explicit spawn for new H2 work and disclosure of legacy
delegate budgets before selection. This refines 032 without changing default 1.0.

## Decision And Flow

- New H2 research work uses `tool_call` / `agent.spawn` with an explicit
  `requested_budget={model_calls,wall_time_ms}` chosen by the calling Agent.
  Obtain the exact Spawn Schema with `tool.describe`; descriptions grant no
  permissions. A successful spawn returns a receipt, not an implicit wait.
- Keep both legacy and versioned `delegate` compatible. Before any decision,
  expose its original requested budget by role: analyst (both versions) requests
  4 calls / 600000 ms; verifier and skeptic request 1 call / 120000 ms.
  These are compatibility values, not recommended budgets for new research.
- Root and research children receive the same compact delegation policy. Also
  include it in the on-demand `agent_action.v1` description. Maintain a single
  budget definition used by both disclosure and legacy execution.
- Forward the existing trusted TaskBrief budget/deadline snapshot to every model
  decision, refreshed on each Attempt, rather than requiring it to infer remaining
  funds from earlier observations. List this adapter's supported research role
  versions in the compact policy; a catalog is not permission to execute them.
  This pre-decision snapshot is not a reservation: the current model call and
  concurrent work can consume funds, so Runtime rechecks allocation on spawn.
- Do not automatically convert delegate into an explicit model-selected spawn,
  change its implicit wait, enlarge its budget, or replace a denied request with
  a smaller grant. A model may choose spawn on a later decision.
- Explicit spawn retains the existing exact-allocation-or-rejection rule, parent
  finalization reserve, inherited deadline, permissions and depth limit. No new
  default child call count, model fallback, retry policy or research method.

## Acceptance Before Implementation

1. First Root and child model inputs disclose the original legacy role budgets
   and identify explicit spawn as the preferred path for new research.
   They also carry the current TaskBrief budget/deadline, not stale checkpoint
   values, and the supported role versions. Missing fixture fields stay absent;
   never invent a new budget or extend a deadline.
2. On-demand action description repeats the same values; mutating a returned
   description cannot alter a later disclosure or allocation.
3. Parameterized legacy analyst/verifier/skeptic runs (including analyst@2)
   allocate exactly the budget disclosed before the delegation decision.
4. A model-selected explicit Spawn with a different valid budget keeps exactly
   that request in its receipt and persisted account. An excessive calls/time
   request is rejected as budget_denied, leaves no child or allocation, and is
   shown as a failed observation for the next model decision.
5. Existing continuation, source-scope and default-finance regressions remain
   compatible. These tests use mock models and temporary state, not paid calls.

## Evidence

Implemented in finance_tool_contracts (shared budget/policy), FinanceH2Domain
(Root disclosure and legacy request), and FinanceH2ResearchDomain (child guidance).
The trusted budget snapshot is refreshed from execution_input's TaskBrief on
every Attempt; checkpoint values cannot replace it.

Final focused finance-agent/research/continuation collection: **116 passed,
40.45 seconds**. Final broad H2/state/compatibility/live-driver collection:
**443 passed / 7 skipped, 173.19 seconds**. Budget tests include all four legacy
role/version pairs, descriptor isolation, Root/child stale-budget replacement,
and exact explicit grants versus calls/time rejection. Scoped Ruff passed.
These collections overlap; do not add them as independent evidence counts.

R6 remains frozen: its three deliveries did not exercise live research
delegation. This change does not itself prove real-model budget choices or real
Redis operation. Full regression and remaining acceptance are recorded in the
[current report](../acceptance/harness-v2-current-acceptance-20261019.md).
