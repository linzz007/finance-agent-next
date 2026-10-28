# H2 Business Batch And Context Repair

Date: 2026-10-21. Scope: terminal [094](../changes/094-h2-full-business-mvp-protocol.md)
and offline [095](../changes/095-h2-context-payload-deduplication.md). This report
does not rewrite the frozen protocol, inputs or original paid artifacts.

## Actual Business Outcome

| Item | Observed result |
| --- | --- |
| Original suite | Q01-Q30 retained |
| Executed | Q01 once; 310.94s; Root failed, no answer |
| Physical requests | 10 DeepSeek: nine successful responses, one provider_error |
| Accounting | 10 charges for 10 requests; Root110/Run590 left |
| Tools | One tool.describe, eight successful article.list, no body read or child |
| Remaining cases | 27 not_run_after_environment_blocker |
| Q29/Q30 | Requires isolated host / missing external outage injection |
| FIRST terminal integrity | 473 frozen, 446 runner, 898 snapshot files match; helpers match |

The retained final remote error has no HTTP status or detailed cause. Neither
budget exhaustion nor a provider context-limit rejection can be asserted.
This is one executed failure, not 30 executed failures or a semantic pass.

## Confirmed Technical Defect

The saved inputs contain the same successful business body in materials, action
receipts and the combined compatibility view. Exact cache-hit wrappers can add
another duplicate material. These copies add no evidence and violate the intended
compact, on-demand presentation.095 shares one visible copy without changing
stored payloads, source readers/versions, permissions or actual observation facts.

Provider-reported prompt usage in the original nine successful calls rose from
2422 to998407 tokens. A read-only last-checkpoint comparison estimates845443
to330386 tokens (-60.92%), and2770588 to1056517 message characters after095.
The replay preserves all nine material handles, evidence handles, query, article
states and original DB hash. Its local token estimate is not a provider
measurement, live latency/success improvement, or proof of the remote failure.

The eight article.list requests had varied arguments. Their metadata row counts
were500,275,66,129,281,137,363,388; individual payloads were about28k-209k
characters. Saved rows contain metadata, not article bodies. This remaining
lookup inefficiency is distinct from duplicate presentation and cannot be
fixed by claiming identical arguments or silently truncating the corpus.

## Offline Verification

| Check | Actual result |
| --- | --- |
| New counterexamples before fix | 6 failed / 4 passed |
| Intermediate focused check | 113 passed / 1 fixture failure; retained |
| Final focused context/continuation/runner | 370 passed,139.79s |
| H2/state/compatibility/runner | 1229 passed / 7 skipped,423.23s |
| Default finance/shared Runtime | 1638 passed / 30 skipped,112.87s;52 modules |
| Configuration isolation | 10/10; zero model calls |
| Scoped Ruff and tracked diff check | Pass; existing environment/line-ending warnings retained |

The seven H2 skips require real Redis. Historical full-repo evidence at087 is
3608 passed /52 skipped /17 failed (13 absent legacy fixtures, four generic-chat
prompt assertions). No new whole-repo run clears those failures. Replay helper
schema and read-only inspection errors were corrected in helpers, not by
loosening runtime schemas or rewriting evidence.

## Remaining Classification

| Problem | Classification and next boundary |
| --- | --- |
| JSON escapes/action boundaries | Conventional protocol fixes, recorded with fixed Pi/DeepSeek sources in087/088; new counterexamples still need tests |
| Duplicate context copies | Confirmed implementation defect;095 offline repaired, live effect not established |
| Repeated large metadata discovery | Tool choice/efficiency; determine smallest existing-contract integration, not a new forced workflow |
| Missing child article grants | Actual permissions correct; parent delegation decision may be wrong, do not grant the whole corpus |
| False contradiction/stronger-than-source claims | Semantic/model judgment; parsing success cannot certify correctness |
| Verification integration and automatic closing entry | Remaining072 choices are not approved; preserve useful text and current budgets |
| Redis/deployment/concurrency | Environment and evidence gap; FakeRedis is not production validation |

Private evidence: sibling runtime-records/h2-business-live-20261021,
h2-business-live-protocol-20261021/first-run-end-integrity.json, and
h2-context-dedup-mvp-20261021. Original failures and first integrity are immutable.
Necessary paid tests remain authorized, but any new batch must be finite and
frozen separately. No provider-native calling, model fallback, new hard review
gate, live migration, default1.0 switch or Git upload is authorized by this repair.
