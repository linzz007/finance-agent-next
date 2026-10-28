# H2 R18 Q08 Independent-Budget Revalidation

Date: 2026-10-20. Status: one frozen execution completed; delivery and structural
checks pass, live child-cap branch not triggered, full business acceptance open.

## Scope

[068](../changes/068-h2-q08-independent-budget-revalidation.md) defines this new
single Q08 execution under [067](../changes/067-h2-independent-call-limits.md):
Root own 120, each child own 60, entire Run 600, 60 minutes, unchanged reserves.
DeepSeek (`deepseek-flash`) and FakeRedis only; original Q08 and the same isolated
R17 corpus are unchanged. No model switch, probe or whole-query rerun occurred.

## Actual Result

| Item | Evidence |
| --- | --- |
| Root | succeeded; final answer delivered |
| Duration | 215.22 seconds |
| Physical model requests | 18, all Root-owned |
| Final answer | 4,179 characters |
| Tasks | Root only; no child or reviewer selected |
| Own / Run remaining | 102 / 582 |
| Actual sources | 8 articles: 6 complete, 2 partial |
| Review / metadata | review=null; incomplete_metadata |

All eighteen physical responses finish successfully at the adapter transport
layer. Three initial public action envelopes contain trailing commentary or
multiple objects, so action validation reports contract_error. Each is repaired
once on the same model, original budget and isolated correction input; all three
repairs succeed. Those six requests are included in the eighteen. No transport
failure, retransmission or Markdown-fence normalization is triggered.

The zero-request read-only audit verifies eighteen structural assertions:
one-to-one charges, Task/Attempt/budget/model binding, settled requests, retained
started charges, nonnegative/conserved balances, fixed Root120/Run600 limits,
60-minute deadline, call times, reserves, artifact hashes and foreign keys.
The actual ledger has 18 net charges, not the 17 seen in the final model-input
budget snapshot before its last request. That snapshot is not a final usage bill.

All 422 frozen code/design/configuration/test entries, 898 snapshot files and the
private runner hash match before the documentation updates. Every physical call
has a private public-input capture. All three correction inputs retain original
context and omit rejected-body replay/audit handles. The audit makes zero calls.

## Research And Manual Review

The model discovers all 281 author entries, reads eight bodies from June through
October, and uses those eight as final evidence. Six are complete. The August 1
article is read only through line 100/113 and August 20 through 100/101; the final
answer explicitly discloses both. The other 273 discovered articles stay unread
and undecided. There is no fabricated full-read status.

The answer addresses research themes and methods with concrete article examples,
distinguishes author assertions from external verification, and preserves several
conditional scenarios and time changes. Manual source spot-checks support its
principal Fed/AI, election, China rates and supply-chain examples. This is a
useful sample-based author analysis, not a certified complete portrait or external
financial fact check.

Remaining limitations are distinct from budget enforcement:

- Structured review is absent. No independent reviewer was created or consumed.
- Model-owned coverage is stale: articles_sampled=3, target_articles_sampled=8,
  while actual read events cover eight. Runtime does not invent a coverage judgment.
- Two sampled articles remain partially read, honestly disclosed. This Q08 asks
  for a representative summary, so it does not establish exhaustive deep reading.
- Detailed examples are attributed by title/date, with final evidence refs retained,
  but the answer lacks convenient clickable per-claim source locations.
- Some high-level descriptions of the author's habits are broader than the small
  macro-focused sample. Scope qualifications remain necessary.

## Interpretation And Remaining Evidence

R17 Q08 delivered with 39 requests and a failed eight-call reviewer. R18 delivers
with 18 requests and no reviewer. This difference does not isolate the effect of
the new budget policy or measure model accuracy, speed or reliability improvement.
The live child60 / multi-article-review budget diagnosis is **not_triggered**.
It must not be reported as repaired based on this Root-only execution.

Budget/runner regression performed immediately before execution: **63 passed**,
72.19 seconds. The broader 067 H2 **901 / 7 skipped** and default finance **1638 /
30 skipped** remain earlier offline results, not newly rerun R18 evidence. Both
private scripts pass Ruff. No runtime code changed, live database migration,
Docker operation, real Redis test, Git commit/push or default-1.0 switch occurred.

Private records under the sibling runtime-records directory:

- h2-independent-budget-r18-20261020: manifest, original snapshot, provenance,
  isolated SQLite ledger, artifacts, request captures, final answer and state.
- h2-independent-budget-r18-protocol/run_batch.py: frozen once-only wrapper.
- h2-independent-budget-r18-audit/audit.json: terminal read-only audit; preserve
  its pre-documentation integrity result rather than overwrite it after edits.

Full goal acceptance remains open: child limits need a triggered live diagnostic;
two-level usable source references, structured review/coverage, missing business
adapters, semantic quality, real Redis and production integration remain separate.
