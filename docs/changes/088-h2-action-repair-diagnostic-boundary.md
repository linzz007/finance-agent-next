# H2 Action Repair Diagnostic Boundary

Date: 2026-10-21

Status: Implemented after design and failing regression tests; bounded technical
MVP under the user's framework-informed repair instruction. No new financial or
scheduling policy.

## Defect

Change 083 accepts the first complete unfenced public action and discards the
remaining model text. Change 087 repairs string literals before strict decoding.
The combined implementation counts repairs in the discarded tail as repairs of
the accepted action, and reports tail length after normalization rather than its
original length. The executed action is unaffected, but Trace diagnostics are
misleading. Fenced multi-object output remains rejected under 065/083.

## Contract And Implementation

- Keep 083's first-object selection, strict action/argument validation and raw
  audit artifacts unchanged. Never execute the second object or use the tail as
  an observation, state update or evidence.
- Track only string repair positions and their length expansions. Use the
  standard JSON decoder's first-object boundary to select the repairs belonging
  to that action and recover its boundary in the original public text.
- Successful `json_string_controls` and `json_string_invalid_escapes` counts
  describe only the selected action. `trailing_chars` counts original discarded
  characters after the existing outer whitespace/fence handling, not a
  string-repaired copy. Rejected-output diagnostics retain their
  existing bounded behavior; no accepted action is implied by a failure count.
- Do not add a permissive JSON parser, brace completion, extra model request,
  retry, model fallback, budget, database table or financial review requirement.

## Framework Evidence

The fixed revisions reviewed for 087/088 are implementation references, not
claims of production success rates:

- [Pi JSON parsing](https://github.com/badlogic/pi-mono/blob/fbc456d827d7a4a951c50e7145af066226e2f8e9/packages/ai/src/utils/json-parse.ts):
  `repairJson` repairs controls and invalid string escapes; completed tool
  arguments still use standard JSON parsing rather than partial streaming JSON.
- [DeepSeek Harness tool calls](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/agent-loop/src/tool-calls.ts)
  and [tool schemas](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/tools/src/schema.ts):
  argument parsing, schema validation and execution are distinct; invalid
  arguments are not silently treated as successful executions.

These sources do not implement our first-object policy or isolated correction
policy. Those are this project's existing choices, not attributed to Pi/DSH.

## Acceptance

1. Reproduce incorrect counts and length when a valid first action is followed
   by malformed strings in an ignored second object or transcript.
2. Count repairs inside the first action, but not those in its discarded tail;
   preserve decoded values, original text, exact raw tail length and one call.
3. Test quoted braces, legal escapes, repaired key strings, XML-only wrappers,
   and both supported transports in streaming/non-streaming modes.
4. Keep invalid-first-object, fenced multi-object, natural-language prefix,
   permission, truncated-stream and strict tool-parameter tests passing.
5. Report offline, live-model and environment evidence separately. Do not
   reinterpret historical paid batches or claim financial correctness from JSON.

## Verification

- Before the implementation change, the 28 new checks produced 26 failures and
  two passing fenced-object controls. The failures reproduce inflated action
  repair counts and the wrong discarded-tail length; action payloads were
  already correct. This first result is retained in the tool execution record.
- After repair, JSON/execution tests: 283 passed in 45.84 seconds.
- H2/state/compatibility/live-runner regression: 1145 passed, 7 skipped,
  2553 deselected in 374.51 seconds. The seven skips require real Redis.
- Default finance/shared-runtime baseline: 1638 passed, 30 skipped in 112.59
  seconds, using 52 modules, temporary workspaces and no live credentials.
- Configuration/isolation checks: 10/10, zero model calls.
- Scoped dispatch/test Ruff and tracked diff checks pass. Git's existing
  LF/CRLF notices are not test failures; no formatting rewrite was performed.

Generated logs and JUnit reports are under the project's sibling directory
`runtime-records/h2-json-boundary-mvp-20261021/`. Default finance/shared-runtime
and H2 logs/JUnit files are separate. The earlier full-suite result
`3608 passed, 52 skipped, 17 failed` belongs to 087; it is not a rerun of 088.

No paid model rerun, live database migration, Redis/Docker operation, default
route change or Git commit/push occurred. Semantic errors, missing business
adapters, the unapproved automatic closing policy and production reliability
remain separate from this diagnostic repair.
