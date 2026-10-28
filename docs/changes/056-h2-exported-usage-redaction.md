# H2 Exported Usage Redaction

Date: 2026-10-20. Status: existing-contract repair implemented; offline verified.

## Authority And Reproduction

[013](013-harness-v2-dispatch-baseline.md), sections 3.3 and 11, requires usage
statistics and credential-free runtime records. The
[SQLite contract](../harness-v2/sqlite-schema.md) stores each physical request's
`usage_json`; section 4 describes exports as reconstructible records, not a
second authority. The [model interface](../harness-v2/interface-contracts.md),
section 8, includes normalized usage events and call accounting.

The adapter already admits only nonnegative integers in five provider usage
fields: input_tokens, output_tokens, prompt_tokens, completion_tokens and
total_tokens. However, the record redactor matches any key containing token.
Consequently valid usage numbers become "[redacted]" in exported model-calls
and their trace records, even though the SQLite values are intact. The same
redactor is also used for JSONL events and quarantine. A synthetic
redact call reproduces this without network access or live user records.

This is an export-fidelity defect under the existing accounting contract,
not permission to log credentials, invent missing usage or estimate a bill.

## Repair Boundary

- Preserve values only for those five exact usage field names, with an actual
  integer type and value >= 0. Booleans, strings, negative/fractional numbers,
  nulls and nested objects under these names do not qualify.
- Keep the existing secret-key matching for every other key. Keep value-based
  masking and diagnostic string limits. Credential-shaped values under usage
  field names must remain hidden; do not exempt whole usage dictionaries.
- Apply the same rule through recursive JSON/JSONL/quarantine redaction. Do not
  rewrite the authoritative database, old batch files, physical-request status,
  budgets, permission grants, model selection or research policies.
- Preserve absent/empty usage as absent/empty: provider silence is not zero
  measured usage. Do not change the default 1.0 path or add a dependency.

## Verification Plan

First reproduce valid count loss with focused tests. Check all five names,
zero and positive values, nested lists/tuples, repeat redaction and invalid
values. Guard ordinary token/API-key/authorization/password/secret fields and
credential-shaped strings. Exercise actual JSON, JSONL and quarantine files in
temporary roots, then compare real SQLite/mock-provider request usage against
exported call and trace records, including a contract-failed response.
No paid call or real user database is needed.

After repair run broad H2/state/compatibility/live-driver and default financial
regressions, configuration/separation, full scoped Ruff and documentation/diff
checks. Preserve R13 semantic findings and pending design decisions. Green
export tests do not certify financial correctness, real Redis or production load.

## Actual Verification

Before changing the runtime, five pure-redaction and three file-write cases
failed on valid count loss. The two strengthened SQLite/mock-provider export
cases also reproduced count loss after correcting a missing test import. Their
authoritative usage_json remained numeric while exported usage was redacted.
No provider call was made to reproduce the issue.

After repair the two complete focused modules passed: **131 passed**, 40.83
seconds. This includes seventeen newly added redaction/file cases and the two
strengthened existing physical-call cases. Both successful and contract-failed
responses retain their original physical status, error and numeric usage in
model-calls/trace exports. Invalid usage-like credentials remain hidden.

Actual final post-056 results:

| Check | Result |
| --- | --- |
| H2/state/compatibility/live-driver | 739 passed / 7 skipped, 236.75 seconds |
| Default finance/shared runtime, 52 modules | 1631 passed / 30 skipped, 122.17 seconds |
| Configuration/separation | 10/10 |
| Full Ruff on three changed Python files | Passed |
| Seven current Markdown documents | 176 local file links resolve; heading anchors/whole-design semantics not certified |
| Git diff whitespace check | Passed |

The focused modules overlap the broad collections; do not sum them. The seven
H2 skips still require real Redis, and the existing Starlette/httpx deprecation
warning remains. The inherited repository-wide collection was not rerun.

No paid request, Docker change, live DB/corpus mutation, default-route switch or
Git commit/push occurred. Old R13 files are unchanged; this is not evidence that
its financial semantic failures, strict-JSON failures or absent adapters are
fixed. Correction-input isolation and finance closing-mode entry remain pending
design decisions. The overall goal remains open.
