# H2 Business Runner Environment Correction

Date: 2026-10-21. Status: design recorded before the replacement batch.

## Observed Failure

The 097 replacement batch froze correctly but stopped before its first model
request: executing the private helper directory directly made `uv run` use the
workspace interpreter without the project's development extras, and importing
the approved FakeRedis backend raised `ModuleNotFoundError: fakeredis`.
No provider request, charge, database research operation or model output was
created. Its FIRST integrity is retained at
`runtime-records/h2-business-context-r2-protocol-20261021/` and the batch must
not be restarted or overwritten.

## Minimal Correction

Create a distinct finite batch using the same frozen 30-Query scope, snapshot,
model, adapter bindings and budgets, but invoke the helper through the project
environment explicitly:

`uv run --project D:/AAAcode/code-code/agent+/harness/finance-agent-next --locked python run_batch.py`

The helper still refuses existing output/freeze records, includes its template
hash, saves FIRST terminal integrity before report edits, and uses FakeRedis
only. No dependency, product, model, retry, budget or research-rule change is
needed. If the project environment itself cannot load dependencies, stop as an
environment blocker; do not install globally or silently switch to real Redis.

## Acceptance Boundary

This is a launcher correction only. Offline dependency loading is checked before
paid execution. The new finite batch is once per executable case, stops after a
provider/quota environment blocker, and reports Q29/Q30 separately. It cannot
prove commercial reliability or semantic quality by itself.
