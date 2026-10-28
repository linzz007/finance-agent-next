# H2 First Successful Read Is Not A Version Change

Date: 2026-10-20. Status: existing-contract repair implemented and offline-verified;
specification recorded before runtime edits.

## Authority And Reproduction

[051](051-h2-version-bound-article-read-progress.md),
[026](026-finance-state-update-protocol.md) and the
[H2 source observation contract](../harness-v2/interface-contracts.md) require
reading facts and source-version diagnostics to come from actual observations.
A rejected read parameter is not an observed original source version.

The frozen R14 batch reproduces a Runtime defect in Q15 and Q16. After an initial
invalid read, an indexed article has read_status=failed but no observed hash or
range. Its first successful read changes None to the source hash, and the merge
incorrectly reports article_source_version_changed. Q16 repeats that warning
in its public answer. Actual successful hashes agree and the frozen corpus is
unchanged. This warning is separate from Q15's terminal JSON-format failure.

A private synthetic zero-paid reproducer also fails its no-warning assertion
on the frozen implementation. The R14 batch and final read-only hash audit must
finish before this repair; its original outputs must not be rewritten.

## Narrow Repair

- Keep comparing hashes before merging coverage. On a different hash, reset
  current-version ranges/status exactly as in 051 and retain old observations,
  materials, result references and evidence.
- Warn only when prior reading was actually observed: partial/complete, or a
  failed state retaining a previous hash or nonempty read ranges. A bare failed
  status without either is not prior observed content.
- Preserve genuine changes after an earlier partial read followed by failure,
  including known-to-missing and missing-to-known version transitions.
- Do not remove earlier read errors or unrelated metadata warnings. Derive
  partial/full progress from the successful read, not from its former failure.
- Root and research roles inherit the same merge. No prompt, parser, external
  verification, research method, queue, DDL, budget, model or 1.0-route change.

## Verification Plan

1. Add failed-before-first-read cases for Root/research domains, both absent and
   explicit empty version/range fields. Prove the no-warning assertion fails
   before the runtime repair, then passes without mutating the previous state.
2. Extend changed-version tests to failed states with real earlier coverage.
   They must still warn, reset current-version ranges and retain old references.
3. Exercise SQLite/FakeRedis/mock-provider list -> invalid limit -> new Service
   instance -> successful partial/full reading -> final delivery. Keep the failed
   observation visible, the first true hash/ranges correct, and exact four/five
   physical mock-call charges. Verify compact checkpoints and unchanged model.
4. Run focused modules, complete H2/state/compatibility/driver regression, default
   finance regression, configuration/isolation, full scoped Ruff and diff checks.

No paid rerun is necessary to prove this deterministic defect. R14 semantic
failures and pending correction-input, parser and closing-reserve policies remain
separate; fixing the false warning does not certify answer quality.

## Actual Implementation And Verification

The merge now excludes a bare failed status from prior observed reading while
retaining failed states with an observed hash/range. Coverage resets and genuine
version-change warnings remain unchanged. Saved old batch/checkpoint warnings
are not retrospectively deleted or migrated.

Before the runtime edit, all six new first-read checks failed on the spurious
warning: four Root/research initialization cases and two SQLite/FakeRedis/
mock-provider continuations. Nine genuine-change parameter cases already passed,
including three added failed-after-prior-reading guards. After the edit the
same focused selection passes all fifteen. There are nine net added cases,
not fifteen new cases.

The two continuation tests reconstruct the Service after invalid limit=101,
then complete a partial or full first valid read. The rejected read does not
reach corpus.get_article, remains visible as a failed observation and receives
its normal model-call charge. Valid source hashes/ranges survive continuation;
neither checkpoints nor later model inputs contain the false warning. Exact
five/four physical mock requests, fixed model and compact checkpoints pass.

| Check | Actual post-repair result |
| --- | --- |
| Selected first-read/version checks | 15 passed, 8.76 seconds |
| Two complete state/continuation modules | 79 passed, 80.18 seconds |
| Full H2/state/compatibility/live-driver | 785 passed / 7 skipped, 284.81 seconds |
| Default finance/shared runtime, 52 modules | 1638 passed / 30 skipped, 121.77 seconds |
| Configuration/separation | 10/10, actually rerun after the repair |
| Full Ruff on three changed Python files | Passed |
| Eight relevant Markdown files | 162 local file links resolve; not heading/semantic certification |
| Git diff whitespace check | Passed |

The selections overlap and must not be summed. The H2 skips still require real
Redis; existing default-finance skips and Starlette/httpx deprecation warning
remain. This post-batch repair makes zero paid provider calls, changes no live
DB/corpus and does not perform Docker operations or a Git commit/push. The
frozen R14 answers and pre-edit hash audit remain unchanged; R14's semantic
failures and the pending policy choices are not resolved by these regressions.
