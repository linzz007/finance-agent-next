# H2 Observation View Separation

Date: 2026-10-21

Status: Implemented MVP

## Problem

The finance H2 prompt contains two different persisted records:

- `observation_refs`: normalized business observations, including the
  `finance.h2.tool_result.v1` payload used for article state, evidence and
  corrective feedback;
- `action_observation_refs`: scheduler/action receipts, including the
  `finance.h2.action_observation.v1` envelope used to inspect action status,
  task IDs and scheduler payloads.

The previous prompt assembly concatenated both records into one view without
declaring the distinction. A model or test that expected a normalized tool
result could therefore receive an action receipt with a different nesting
shape. This was a Runtime context assembly defect, not a model-quality or
financial-analysis design defect.

## MVP contract

The model-visible user context now exposes three views:

```json
{
  "observations": [],
  "normalized_observations": [],
  "action_observations": []
}
```

- `normalized_observations` contains only records loaded from
  `observation_refs`.
- `action_observations` contains only records loaded from
  `action_observation_refs`.
- `observations` remains the backward-compatible combined view, in the
  historical order: normalized observations first, action observations
  second. New code must use one of the explicit views and must not rely on
  list position or infer a schema from `status` alone.
- Repeated references are de-duplicated while loading each explicit view.
- No audit artifact is deleted, rewritten, or promoted to evidence by this
  change.

Context-pressure omission applies to both explicit views and keeps the
`payload_omitted` marker plus `material.get` expansion path. This preserves
the existing bounded-context behavior.

## Framework-informed boundary

The JSON/tool handling follows the principles already recorded in changes 073
and 078 and observed in Pi Agent and DeepSeek Harness implementations:

1. collect the complete model response before terminal parsing;
2. parse the public JSON strictly;
3. validate tool arguments against the exact schema before execution;
4. return tool failures as structured observations so the model can correct
   or disclose them;
5. never execute a truncated prefix or guess a tool call from arbitrary text;
6. retain the original response, diagnostic and usage information for Trace.

Those principles do not authorize automatic source selection, semantic
rewriting, model switching, or budget expansion.

## Verification

The following checks pass after the change:

- H2 live-acceptance and continuation regression: 94 passed;
- finance research regression: 213 passed;
- the tool-schema lookup and invalid `article.read` corrective-observation
  scenario passes;
- Ruff passes for the changed implementation and acceptance test.

The full suite finished at `3602 passed, 52 skipped, 17 failed`. The 17
failures are outside this H2 change: 13 require legacy company/policy/eval
fixtures intentionally not migrated into the new project, and 4 assert
general-chat prompt guidance that is present in the skill file but not injected
by that legacy prompt path. No H2 observation, continuation, or finance
research test failed after this change.
