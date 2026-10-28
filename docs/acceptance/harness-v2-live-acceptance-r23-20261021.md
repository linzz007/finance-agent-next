# Harness 2.0 Live Acceptance R23

Date: 2026-10-21. Model: DeepSeek-V4.1-Flash (`deepseek-flash`). Backend:
FakeRedis. No automatic model fallback. Runtime record:
`D:/AAAcode/code-code/agent+/harness/runtime-records/h2-q01-guidance-r3-20261021`.

## Scope

This was a targeted Q01 rerun after changes 084 and 085. It was not a full
30-query semantic acceptance and it does not certify real Redis.

## Result

| Item | Result |
| --- | --- |
| Query | Q01 article metadata/count lookup |
| Root Task | succeeded |
| Delivery | delivered |
| Child Tasks | 0 |
| Model calls | 4 succeeded, 0 failed, 0 unknown |
| Duration | 90.18 seconds |
| Physical `article.list` calls | 2 |
| Repeated exact-argument reads | 0 |
| Model/provider fallback | none |

The two `article.list` calls used different filters: the first requested the
large metadata page, and the second requested an earlier date range. Runtime
therefore executed both, as required. The prompt change avoided the earlier
unnecessary analyst delegation and the child timeout. The deterministic cache
unit test separately proves that an identical successful `article.list` result
is reused without calling the corpus again.

## Remaining Boundary

This result demonstrates operational convergence for Q01, not semantic answer
quality. Article body reads remain uncached because a durable Task may observe
an article reindex; version-aware body caching is intentionally outside this
MVP.

Offline verification after the changes: `1059 passed, 7 skipped`. The skipped
cases require a real Redis service that is not configured in this environment.
