# H2 Article Read Progress Must Stay Version-Bound

Date: 2026-10-20. Status: existing-contract defect repaired and offline-verified.
No new research policy or model authorization.

## Authority And Reproduction

[H2 interfaces, sections 8.1/8.3](../harness-v2/interface-contracts.md) preserve
source identity/version and derive reading facts from actual observations.
[026](026-finance-state-update-protocol.md) does not permit invented completion;
[business acceptance](../finance_agent_eval_v1_acceptance.md), sections 6-8,
requires source changes to remain distinguishable rather than silently merged.

Before this repair, the H2 article-state merge overwrote `current_hash` before combining
the existing `read_ranges` and `read_status` with the new observation. Two new
offline fixtures failed: old-version lines 1-5 plus new-version lines 6-10 became
`complete`, and an old complete read remained complete after reading only the
new tail. Both misstate what was read; this is not a financial reasoning rule.
The frozen R13 corpus did not change, so this reproducer does not establish
that the defect caused R13's semantic failures.

## Repair Boundary

- Compare source hashes before merging a successful article-read observation.
- For a version change, reset only the current-version read ranges/status,
  then derive them from the newly observed range. An old complete status must
  not certify the new version.
- A transition between a known hash and an absent hash cannot establish equal
  versions either. Two old unversioned envelopes retain legacy compatibility;
  this does not certify a source version that they do not expose.
- Preserve all previously observed material/result/EvidenceRefs and immutable
  observations. They still identify the old source, not a new independent
  source. Do not delete valid analysis or rewrite past reads.
- Record a source-version-change warning and incomplete metadata, visible in
  the existing checkpoint/context. Preserve existing review/coverage snapshots
  as historical judgments; do not silently certify their applicability to a
  new source, fabricate a review or add a delivery gate.
- Root and depth-one/two research roles use the same merge. No new SQL tables,
  Redis messages, model retries, fallback, budget or default-1.0 switch.

## Verification

Use temporary synthetic sources only. Check partial/complete old states,
known/missing hash transitions, same-version contiguous reads, new-version
completion and retention of old references. Also exercise the actual corpus
entry while reindexing a temporary article between actions, then resume its
checkpoint and confirm that only the newly read version can become complete.
No paid calls are necessary to prove this deterministic merge boundary.

Rerun H2/state/compatibility/driver regression, default-finance compatibility,
configuration/isolation and scoped static checks. Retain R13 unchanged and
record the new offline evidence separately; no paid rerun merely to replace
its failures.

## Implementation And Results

`FinanceH2Domain._consume_observations` now compares hashes before accumulating
read coverage. `FinanceH2ResearchDomain` inherits this same merge, so the repair
also applies to depth-one/two research roles without a second implementation.
Ten added regression cases cover six changed-version combinations, two
same-version compatibility combinations, and two actual corpus/resume paths.

The integration fixture uses a temporary `ArticleCorpus` and SQLite index,
reads an old partial/complete version, rewrites and reindexes only the fixture
article, then reconstructs the service/domain before each resumed read. The
persisted state contains only the new tail's range until the new head is read.
Both immutable corpus versions, old material references, original checkpoint,
source-change warning and incomplete metadata remain. A mocked provider then
delivers normally; the test does not impose a new output gate.

Completed offline checks:

| Scope | Actual result |
| --- | --- |
| State-update and durable-continuation tests | 46 passed, 54.92 seconds |
| All dispatch tests + state-update + H2 compatibility/live-driver tests | 682 passed / 7 skipped, 310.78 seconds |
| Default finance/shared-runtime, 52 modules | 1614 passed / 30 skipped, 187.25 seconds |
| Configuration/separation | 10/10; zero model calls |
| Scoped Ruff critical rules and Git diff whitespace check | Passed; existing CRLF notices remain |

These collections overlap and must not be summed as independent evidence.
The seven H2 skips require real Redis. The existing Starlette/httpx deprecation
warning remains. No new paid batch, Docker operation, live-source modification,
default-1.0 switch, Git commit or push was performed for this repair. The full
inherited repository collection was not rerun.

[R13](../acceptance/harness-v2-live-acceptance-r13-20261020.md) remains the latest
business run with its original failures. This source-version repair is not a
fix for fabricated analysis, probability interpretation, author attribution or
strict-JSON failures, and does not complete the overall acceptance goal.
