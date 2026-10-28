# H2 Author Catalog Parity MVP

Date: 2026-10-21. Status: design recorded first; implemented/offline verified. This repairs
an existing metadata capability omitted from H2; it does not force a workflow
or add a new tool, provider, retry, budget or financial-review gate.

## Existing Authority And Failure

The frozen Q01 asks for corpus authors, counts and latest publication dates.
ArticleCorpus.list_authors already computes exactly those metadata in SQLite,
and the legacy FinanceResearchGateway.list_articles includes that authors
catalog. H2 FinanceH2ChildExecutor._tool omitted it. The094 Q01 trace instead
requested eight varied article lists and failed without delivery. This does
not prove missing statistics caused the final provider_error;095 separately
fixes duplicate presentation.

## Additive Return Contract

Keep article.list input validation and articles unchanged. Add authors entries
with author, article_count and latest_published_at, plus authors_scope:

- Unrestricted Root: call the existing corpus.list_authors; scope is
  corpus_all_active, independent of article list limit/author/date filters.
  These are whole-corpus metadata, not filtered-result counts or body reads.
- Explicit child scope, including an empty grant: never call the global
  catalog. Group only the already returned and authorized article rows; scope
  is returned_authorized_articles. Counts are for this returned set only, not
  all granted articles or the corpus. Do not fetch bodies or broaden queries.
- A compatible injected corpus lacking list_authors can only group returned
  rows, with scope returned_articles. Do not mislabel a partial list as global
  statistics. Real ArticleCorpus always supplies the full catalog method.

Catalog metadata do not mark any article read/used or certify viewpoints. All
existing persisted records, grants, default1.0 and deterministic cache rules
remain unchanged; historical cached outputs lacking authors stay readable.
The concise tool description mentions statistics; detailed scope is in the
result rather than an ever-growing main prompt.

## Verification

Reproduce the missing catalog before code. Test real SQLite whole-corpus totals
with a restricted list limit/date, no body reads, inactive articles, scoped
children with ungranted authors and empty grants, compatible injected corpora,
unchanged rows and strict parameters. Exercise normal business observations
and durable continuation in existing regressions. Live effects require a new
finite frozen batch after offline checks, not rewriting094 or repeat-until-pass.

## Offline Results

- Before implementation: seven missing-catalog failures and one strict-input
  control passed; original author-catalog-red.xml is retained.
- Focused metadata/context/Root/child/continuation/compatibility/runner:337
  passed,160.93s.
- H2/state/compatibility/runner:1236 passed/7 skipped,413.85s; skips need real
  Redis. The preceding095 scope also collected one additional generic fixture;
  do not infer a net-test count by subtracting those differently selected totals.
- Default finance/shared Runtime:1638 passed/30 skipped,113.94s,52 modules.
- Configuration10/10; scoped Ruff/tracked diff pass. One new test import-format
  warning was fixed mechanically; dependency/environment warnings remain.

Logs and JUnit: sibling runtime-records/h2-context-dedup-mvp-20261021/author-catalog-*.
These are offline results, not proof of live convergence or whole-goal completion.
