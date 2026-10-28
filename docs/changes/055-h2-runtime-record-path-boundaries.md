# H2 Runtime Record Path Boundaries

Date: 2026-10-20. Status: existing-contract repair implemented; offline verified.

## Authority And Reproduction Targets

[013](013-harness-v2-dispatch-baseline.md), section 11, places run records and
errors under the configured runtime-records root.
[SQLite contract](../harness-v2/sqlite-schema.md), section 4.1, requires paths,
temporary files and symbolic links to stay within that allowed root. Existing
ArtifactStore/export paths already reject linked path components.

RuntimeRecords.quarantine lacks that same guard, so an existing errors-directory
link can redirect an append outside the configured root. Orphan discovery and
removal reject only leaf symbolic links and lexical escape; a linked run or
artifacts directory can still point to a foreign file. The deletion helper must
recheck the current path rather than trusting a previous scan. An append-only
quarantine file can also be hard-linked to another file, so path resolution alone
does not protect that shared file from modification.

These are filesystem implementation defects, not financial reading, provenance,
format-repair or budget-policy changes. Reproduce them only with synthetic files
inside a temporary test directory. Never probe or repair the user's real record
directories, link targets, article corpus or Docker configuration.

## Repair Boundary

- Apply the existing lexical-root and resolved-path agreement checks consistently
  to RuntimeRecords writes, including quarantine. Validate before creating parent
  directories and again before opening the final quarantine file.
- Refuse a multiply-linked opened quarantine file before appending any bytes.
  Preserve the existing redaction, append/flush/fsync behavior and exception
  propagation. A failed quarantine must not ACK its wire message.
- Validate the runs, individual run and artifacts directories before enumerating
  them. Do not report linked-parent foreign files as orphan candidates.
- Revalidate every removal candidate's full resolved path immediately before
  deletion, including candidates scanned before a directory was replaced by a
  link. Skip invalid candidates without deleting their targets. Preserve normal
  local orphan removal, known-URI/age filters and dry-run defaults.
- Retain current ArtifactStore checks. Do not alter SQL state, Outbox/PEL rules,
  financial methods, model selection, retry policy, budgets or the default 1.0
  entry. No operating-system dependency or host service is added.

This patch does not claim a hostile concurrent-filesystem sandbox. It does not
define a new retention policy or solve artifact-registration-versus-GC races;
the existing explicit retention and DB-reference rules remain applicable.

## Verification Plan

First demonstrate failures using temporary directory links, with junctions on
Windows when ordinary symbolic-link creation is unavailable. All created link
and target paths must remain beneath the same test-owned directory. Test a
hard-linked quarantine file independently. Unsupported filesystem features must
be recorded as skips, not passed coverage.

Check root/errors/run/artifacts directory redirection, revalidation between
scan and removal, untouched foreign sentinels, and normal local records. A real
Worker/FakeRedis fixture must leave a malformed message pending when quarantine
cannot safely persist, then quarantine and ACK that same message after restoring
the test directory. This is local filesystem/PEL simulation, not real Redis or
paid model evidence.

Run the focused tests, broad H2/state/compatibility/live-driver regression,
default-finance compatibility, configuration/separation, scoped Ruff and
documentation/diff checks. Preserve R13 and the pending design decisions.

## Actual Verification

Before the runtime change, the initial eight directory/hard-link reproductions
all failed. They operated only on synthetic sentinels inside pytest temporary
directories; the deletion reproduction affected only such a test-owned file.
After repair all thirteen observability tests passed in 3.93 seconds, including
ten new cases and no added skip. On this Windows host the fixture exercised
directory links/junctions without changing system privileges or services.

Actual post-055 regression results:

| Check | Result |
| --- | --- |
| H2/state/compatibility/live-driver | 722 passed / 7 skipped, 242.12 seconds |
| Default finance/shared runtime, 52 modules | 1631 passed / 30 skipped, 124.45 seconds |
| Configuration/separation | 10/10 |
| Full Ruff on both changed Python files | Passed |
| Git diff whitespace check | Passed; existing LF/CRLF warnings only |

The seven H2 skips require real Redis; that environment remains deferred. The
existing Starlette/httpx deprecation warning remains in both broad collections.
Focused checks overlap the regressions and must not be summed. No paid request,
Docker operation, live-data mutation, default-entry switch or Git commit/push
occurred. The inherited repository-wide collection was not rerun.

This repairs record-path/ACK/cleanup boundaries only. R13 semantic failures,
missing business adapters and pending correction-input/closing-mode decisions
remain open; the overall goal is not complete.
