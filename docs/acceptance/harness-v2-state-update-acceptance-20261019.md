# Harness 2.0 State Update Acceptance

Date: 2026-10-19

Baseline: `finance.state_update.v1`, as confirmed in `docs/changes/026-finance-state-update-protocol.md`.

## Scope

This acceptance covers the Runtime implementation of H2-F08/F09. It does not claim that live model quality, real Redis deployment, or commercial concurrency has been proven.

The implementation now provides:

- Optional `state_update` on every public finance action.
- Monotonic research-plan revisions that survive checkpoint recovery.
- Runtime-owned article discovery and read status. Only real `article.list`/`article.search` results create `listed`; only real `article.read` results change `unread`, `partial`, `complete`, or `failed`.
- Multiple material references retained when one article is read in segments.
- Runtime-derived `used` status from final valid EvidenceRefs. Model metadata cannot directly set `read_status` or `use_status`.
- Run-bound, idempotent Evidence Ledger additions. Supporting and contradicting entries remain parallel.
- `model_inference` cannot be marked `direct`.
- Invalid final metadata never discards the complete candidate answer; delivery is marked `partial/incomplete_metadata`.
- Missing metadata updates remain deliverable and are visible as `metadata_status=incomplete_metadata`.
- Compact action serialization omits empty optional fields, including `state_update: null`.

## Verification

Command:

```text
uv run --offline pytest -q tests/test_finance_state_update.py tests/test_dispatch_finance_agent.py tests/test_dispatch_finance_service.py tests/test_dispatch_finance_root.py tests/test_harness_v2_compatibility.py
```

Result: `18 passed`.

H2 dispatch range, including the new protocol tests:

```text
uv run --offline pytest -q <all tests/test_dispatch_*.py> tests/test_harness_v2_compatibility.py tests/test_finance_state_update.py
```

Result: `204 passed, 2 skipped, 1 warning`.

The skipped cases require a configured real Redis URL. The warning is an upstream Starlette/httpx deprecation warning and is unrelated to the state-update implementation.

Static checks:

- `python -m compileall` passed for the changed Runtime and finance dispatch modules.
- Ruff passed for the changed Runtime, finance dispatch, and protocol test files.
- `git diff --check` passed; line-ending warnings from Git are not content errors.

## Remaining boundary

This acceptance proves the state transition and persistence contract offline. It does not replace the later live Query suite, real Redis multi-worker test, or model-specific quality evaluation.
