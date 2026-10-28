# Harness 2.0 Offline Technical Recheck

Date: 2026-10-21
Model calls: none. DeepSeek live rerun was deferred because the account has no
available quota.

## Scope

This record covers the provider text-action boundary only. It does not certify
financial semantic quality, evidence quality, real Redis behavior, concurrency,
or live-model success rate.

## Change under test

The gateway now accepts the two observed DeepSeek text-mode DSML dialects:

- `invoke name="tool_call"` with `parameter name="tool_name"`;
- `invoke name="tool.call"` with `parameter name="name"`.

Both are converted to the existing internal `type=tool_call` action. The
existing JSON, Schema, permission, tool-argument, and business validation paths
remain authoritative. The parser still rejects unknown parameters, duplicate
aliases, multiple calls, extra body text, malformed nested JSON, and arbitrary
XML or prose.

The older dialect keeps its previous event behavior. Only the newly observed
dialect receives the `public_output_normalization.dsml_action` audit marker.
This preserves compatibility while making the provider adaptation observable.

## Offline results

| Check | Result |
| --- | --- |
| Gateway, JSON controls, H2 live-path fixtures | 363 passed |
| Ruff on changed gateway/dispatch/test files | passed |
| `git diff --check` | passed |
| Full repository suite before the final compatibility-boundary fix | 3726 passed, 52 skipped, 21 failed |

The five failures related to this change were reproduced and then removed by
the final boundary fix. The remaining full-suite failures are outside this
change and are attributed to missing legacy fixture data or unrelated prompt
assertions. The full suite was not rerun after that final narrow fix because the
affected tests were rerun directly and all passed.

## Conclusion

The offline code conclusion is **pass for the known DSML compatibility MVP**.
It is not evidence that a live DeepSeek run will complete successfully, nor
evidence that model-generated financial conclusions are correct. A live rerun
of the existing S01/S02 cases remains required after quota is restored.

