# H2 Durable Personal Context Adapter

Date: 2026-10-21. Status: recorded before implementation; offline verified.
Authority: 013/017 preserve existing finance behavior; architecture personal
context boundaries and acceptance Q23-Q25 already require Wiki/preferences and
file-authoritative holdings. This fills an adapter gap, not a new investment policy.

## Scope

- Explicit optional `personal_sources` binding with trusted company ID and
  article-profile root. No automatic binding, profile writes or live migration.
- `personal.catalog {}` is read-only acquisition/discovery. Root captures Wiki
  pages, research_policy.md and valid current_holdings.md as separate immutable
  `finance.h2.personal_source.v1` Artifacts. Catalogs contain descriptors/handles
  and unavailable reasons, not bodies or successful reads.
- A completed catalog observation is cached/checkpointed by existing H2
  deterministic actions. A restored Service uses those same handles, not changed
  files. A crash before the observation is committed may leave unused immutable
  Artifacts; this is not exactly-once filesystem acquisition or an accepted read.
- Children cannot capture ambient files. Their catalog/search scope contains
  only directly granted personal-source input Artifacts. Nested handles and
  provenance do not grant raw access. Root retains confirmed same-Run authority,
  never cross-Run authority.
- Root query/mailbox inputs are questions, not implicit personal-source grants,
  even if their JSON resembles a valid source record. Root catalog/search uses
  only previously acquired personal refs or actual file capture; child inputs
  remain the explicit source-grant boundary.

## Reading And Search

- Reuse legacy strict `context.open(context_id, offset=0, limit?)` input Schema
  (limit 1-12000). Here context_id identifies an authorized H2 personal-source
  Artifact, not a legacy filesystem observation ID. Return exact continuous
  text, offset/next_offset/has_more/total_chars, source identity, capture time,
  source version hash and view hash. A page is not a full read.
- Reuse `company_wiki_search(query, top_k=5)` and metadata-aware lexical ranking
  as a pure function over authorized frozen Wiki facts. Return matching facts
  and versions, not fabricated full-page reading. Empty matches/catalogs are not
  supporting evidence. No extra provider request or embedding framework.
- Keep compact on-demand tool catalogs. Granted raw personal snapshots appear
  as descriptors in model materials; use context.open for text. Do not inject
  every Wiki body merely because the catalog discovered it.
- Share holdings parsing, Wiki parsing and ranking with legacy wrappers without
  changing their behavior. H2 captures full decoded policy text rather than
  silently applying the legacy 8500-character input truncation; context.open
  bounds visible pages. Preserve file and decoded-text hashes separately.

## Evidence And Recovery

- Successful nonempty reads use source_kind=personal_context, a personal_kind,
  original reader Task, source ID/path/version, date/capture time and actual
  view range. Store in execution.personal_sources, not public_sources/articles.
- Actual read observations become local H2 evidence handles. Snapshot handles
  alone do not certify reading. Research quotes must occur in the observed view.
  Bad bindings preserve complete analysis with partial disclosure, no new gate.
- Existing finance_sources lineage preserves original reader/version through
  both delegation depths. Parent use of a child result remains indirect evidence,
  not a raw source grant or a claim that the parent read the original.
- Personal context is user background, not market truth. Historical/unconfirmed
  Wiki holdings do not replace current_holdings.md. Preserve as_of/approximate;
  never infer cost, profit/loss, risk tolerance or investment returns.
- Missing/malformed/linked files are unavailable, not invented holdings or proven
  empty facts. Sanitize acquisition failures; storage/lease failures propagate.

## Verification

1. Default route and legacy strict input Schemas remain unchanged.
2. Snapshot hashes, pagination, full policy tail, holdings parsing, empty/missing
   files, link rejection, duplicate Wiki IDs and lexical-search parity.
3. Fresh-Service restore uses frozen sources after original files change.
4. Explicit child grants, no child ambient reads, cross-Run denial and depth-two
   provenance, separate from public/author article evidence.
5. Actual view/quote binding; bad quotes retain complete candidate analysis.
6. Focused, broad H2/state and default-finance/configuration regressions first.
   Freeze any paid Q23/Q24 batch separately. Offline tests are not live semantic
   acceptance, real Redis or production reliability.

Budgets, models, recursion, optional state_update, review/coverage/delivery,
default 1.0 and the unapproved automatic closing policy do not change.

## Actual Offline Evidence

- New personal-source tests: 27 passed, 13.67 seconds. Includes a reproduced
  Root-question source-grant counterexample, fixed within the input boundary.
- Final H2/state/compatibility/live-runner regression after that fix:
  1201 passed, 7 skipped, 426.00 seconds. Skips require real Redis.
- Default finance/shared-runtime baseline: 52 modules, 1638 passed, 30 skipped,
  113.95 seconds; isolated workspaces with live credentials removed.
- Configuration/isolation: 10/10, zero model requests. Scoped full Ruff and
  tracked diff checks pass. Shared legacy helpers pass critical Ruff rules;
  their unrelated historical style findings were not rewritten.
- Earlier combined focused run: 220 passed, 68.24 seconds, before the final
  Root-question boundary refinement. Earlier H2 1200/7 is also pre-refinement,
  not evidence for the final code. Preserve both original logs.

Logs/JUnit: sibling runtime-records/h2-personal-context-mvp-20261021.
New paid Q23/Q24 diagnostics are separately frozen in [092](092-h2-personal-context-live-mvp-protocol.md).
No live-model quality, real Redis, full 30-Query or whole-goal pass follows from
these offline counts. Earlier full-suite 17 failures remain historical evidence.
