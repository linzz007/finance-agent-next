# Harness 2.0 Code and Query Acceptance R2

Date: 2026-10-19

## Code-level result

The previously identified contract gap in `agent.status` was fixed. The
implementation now supports `thread_ids` with exact-Thread scope, existing
authorization checks, and event pagination unchanged.

Checks:

```text
uv run --locked pytest -q <all tests/test_dispatch_*.py> tests/test_finance_state_update.py tests/test_harness_v2_compatibility.py
207 passed, 2 skipped, 1 warning

uv run --locked python scripts/check_finance_baseline.py
passed: true

uv run --locked python -m compileall -q src scripts tests
passed

uv run --locked ruff check src/policy_impact/dispatch/execution.py src/policy_impact/dispatch/repository.py tests/test_dispatch_foundation.py tests/test_dispatch_http_api.py
All checks passed
```

The skipped cases require a real Redis URL. FakeRedis and SQLite coverage do
not prove a production Redis deployment or long-running multi-worker safety.

## Fixed-query revalidation

Batch: `data/acceptance/h2-code-fix-r1-20261019`  
Model: DeepSeek `deepseek-flash`; no automatic model fallback.

Entry-point clarification from the subsequent
[R3 audit](harness-v2-fault-acceptance-r3-20261019.md): this batch ran through the
legacy `ChatWorkbenchService`. The batch name does not make it live H2
Root/Redis/state-update acceptance. The original results below are unchanged.

| Query | Runtime | Delivery | Model failures | Tool failures |
| --- | ---: | --- | ---: | ---: |
| Q04 | 537.67 s | returned | 0 | 5 |
| Q08 | 287.75 s | returned | 0 | 1 |
| Q14 | 380.47 s | returned | 0 | 1 |

All three runs produced an answer. The delivery records report
`done/qualified`; verification remains `disputed` and evidence coverage is
`partial`, so this is not a claim that financial answer quality is complete.

The remaining tool failures are outside the repaired status path:

- Q04: model supplied obsolete `article.read`, `market.metrics`, and
  `research.checkpoint` fields; one `web.open` request also returned HTTP 401.
- Q08: one obsolete `article.read` argument shape.
- Q14: obsolete `article.read` line-range fields.

These are model/tool-contract compatibility and external-source issues. They
are not evidence of a scheduler, lease, budget, or delivery failure.

