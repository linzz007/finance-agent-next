# H2 Preserve Actual Source Lineage On Research Return

Date: 2026-10-20. Status: existing-contract repair implemented and offline-verified.

## Authority And Defect

[H2 interfaces, sections 7 and 8](../harness-v2/interface-contracts.md) require
source identity, date, version and EvidenceRefs to survive child-result return.
[026](026-finance-state-update-protocol.md) derives read/use facts from real
observations and final references, not model declarations. A parent may rely
on delegated analysis without repeating the original article read.

The current child result preserves accepted EvidenceRefs but drops their article
read metadata at the parent boundary. The parent merges only direct article.read
observations. A child-only source read can therefore support the final answer
while its parent article state remains unread/undecided or absent.

## Narrow Repair

- When validating a research result's existing source bindings, retain compact
  article identity/date/hash and observed line coverage from those same source
  artifacts. Save this Runtime-generated lineage in a compact finance_sources
  result section keyed by accepted EvidenceRef, and in the immutable research
  wrapper for onward delegation. The model's ResearchResult schema
  does not acquire a new field or permission.
- Only accepted bindings carry lineage. Metadata-only inputs, failed reads,
  rejected quotes/refs and model assertions cannot create successful reads.
  Do not copy the child's full finance state, coverage, review, dispositions or
  use_status into the parent.
- On result return, merge those exact read observations with the existing
  version/range rules. Keep the original reader task identity; returning a
  result does not mean the parent personally read the source or full result.
- Bind the returned result handle and material wrapper to its actual supporting
  articles. The parent's final accepted EvidenceRefs still determine used;
  an unrelated listed article or an unused source must not become used.
- Preserve lineage through both allowed research depths. It remains metadata,
  not a new article grant or an implicit result.get/full-source expansion.
- Accepted result notifications and result.get expose permitted section_refs.
  The domain consumes only the finance_sources metadata section. Source facts
  can therefore return even when a long analysis is not injected; this does not
  produce result.full_viewed or claim that the parent understood that analysis.
  Sibling input grants do not expose ungranted sections.
- No new financial judgment, parser relaxation, correction policy, model,
  budget, SQL table, Redis message or default-1.0 switch. Preserve R14 unchanged.

## Verification

First reproduce the missing source state through the real SQLite/FakeRedis
corpus service with a mocked provider: only the child reads; the parent cites
the accepted child result. Check unused listed sources, partial and complete
reads, distinct authors/dates, two-level recursion, checkpoint recovery and
no extra source/model reads. Add rejected-binding and source-version guards.
Then run focused and broad offline regressions and record actual results.
Paid-model quality and real Redis remain separate acceptance gaps.

## Reproduction And Implementation Evidence

Both initial corpus-service cases failed before the runtime repair: a child
read either one line or the complete article, returned a valid analysis, and
Root cited its result, but Root's article state still said unread. Both cases
pass after the repair.

Twenty net new tests cover accepted versus unused return, short versus omitted
long result bodies, partial/full source ranges, a fresh Service continuation,
both research depths with only one original corpus read, rejected quote/unknown
reference/metadata/failed-read guards, result.get metadata, changed source
versions and failed/timed-out/cancelled child results. The existing sibling
result-grant test additionally checks that ungranted section handles stay hidden.
The multi-source metadata check uses different authors and dates and excludes an
unaccepted forged source. No model task gains write access to this lineage.

The first nested fixture incorrectly requested all of its parent's remaining
grant/deadline and hit the existing reserve boundary. Its explicit test grant
was corrected; production budgets and deadlines were not changed. An earlier
related-module run also encountered the existing one-millisecond real-clock
budget assertion mismatch; the final broad run passed it without a budget edit.

Final H2/state/compatibility/live-driver collection on the repaired runtime:
**825 passed / 7 skipped**, 307.68 seconds. Real Redis accounts for all seven
skips; the existing Starlette/httpx deprecation warning remains. Other final
regressions and static checks are recorded after completion below.

## Final Verification

| Scope | Actual result |
| --- | --- |
| H2/state/compatibility/live-driver | 825 passed / 7 skipped; 307.68 seconds |
| Default finance/shared runtime, 52 modules | 1638 passed / 30 skipped; 127.42 seconds |
| Final strengthened two-author/date metadata and version checks | 2 passed; 0.82 seconds |
| Configuration/isolation | 10/10; zero model calls |
| Full Ruff on the six changed Python files | Passed |
| Git diff whitespace check | Passed |

The two strengthened metadata fixtures were rerun after their final test-only
assertion changes. Runtime code was unchanged across these final checks. The
collections overlap; counts/times must not be added as independent Queries or
Agent latency. The inherited Starlette/httpx warning remains. The full inherited
non-finance suite was not rerun.

No paid request, live corpus/database mutation, Docker operation, model or budget
change, default-route switch, Git commit or push was performed. Old accepted
results without the optional lineage section remain readable; missing metadata
is not fabricated retroactively. R14 and its pre-edit final source audit remain
immutable. Its contract/semantic failures and the parser, correction-input and
reserve-backed closing choices are not resolved by this deterministic repair.
The overall acceptance goal remains open.
