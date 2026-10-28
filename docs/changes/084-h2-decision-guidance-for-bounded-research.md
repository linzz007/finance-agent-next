# H2 Decision Guidance for Bounded Research

Date: 2026-10-21

Status: implemented as an MVP prompt-level correction; Runtime permissions,
budgets, research semantics and model selection are unchanged.

## Problem Classification

The live Q01 run exposed two decision-quality failures, not a missing Runtime
permission check:

1. The Root selected `wall_time_ms=60000` for a child that carried a large
   source context. The fixed-budget Runtime already supports an omitted
   duration and applies its bounded default child window, but the model-facing
   guidance did not say to omit the optional field for ordinary research.
2. The Root delegated a direct metadata/count question after repeated metadata
   reads. The Runtime correctly allowed delegation because delegation remains a
   model decision, but the prompt did not distinguish a direct lookup from
   source analysis.

The child timeout was therefore recorded as a real failure. It was not converted
to success, retried with another model, or given extra budget. The Root also
received a failure observation but lacked a concise convergence instruction.

## MVP Guidance

Under the fixed H2 policy, every Root and child receives these decision hints:

- Prefer direct `article.list`/`article.search` for counts, dates, author
  metadata and other lookup-only questions. Do not delegate only to repackage
  one tool result.
- For ordinary research, omit `wall_time_ms`; Runtime then uses the default
  child window bounded by the ancestor deadline. A shorter duration is an
  intentional choice, not a required parameter for every spawn.
- After a child failure or timeout, do not repeat the same failed action
  indefinitely. Make at most one justified correction or alternative-path
  attempt. If the available material is enough, deliver `partial` and disclose
  the failed task and evidence gap.

These are model-facing heuristics, not hard Runtime bans. Runtime continues to
validate permissions, depth, budgets, deadlines and action schemas, and records
the model's actual choice. This preserves the AI-native Root decision boundary
while addressing the observed stability failure with the smallest change.

## Verification Boundary

Offline tests assert that fixed-policy guidance contains the three rules. A
single Q01 live recheck is sufficient to test the observed behavior; a passing
prompt assertion does not prove model quality or real Redis stability. No paid
model rerun is implied by this change document.
