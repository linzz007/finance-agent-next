# H2 Live Diagnostic R4

Date: 2026-10-19

## Fixed Batch

Actual entry: FinanceHarnessV2Service.for_finance_corpus, not legacy chat.
Profile: deepseek (deepseek-flash), one allowed profile, no automatic fallback.
Backend: FakeRedis; no Redis service, Docker changes or production DB migration.
Private records: sibling runtime-records/h2-live-r4-20261019.
Frozen snapshot: reused and hash-checked from h2-code-fix-r1-20261019.
Normal 60-minute Root / configured child limits, not a 600-second diagnostic cap.

| Case | Duration | Model calls | Tasks | Checkpoints | Delivery |
| --- | ---: | ---: | ---: | ---: | --- |
| Q04 | 4.09 s | 1 | 1 | 0 | Failed, none |
| Q08 | 30.09 s | 4 | 4 | 3 | Failed, none |
| Q14 | 64.93 s | 8 | 8 | 7 | Failed, none |

All three ended in contract_error, not provider quota/network or Redis errors.
Physical model-call status succeeded records receipt of a paid response, not
successful action validation or successful financial analysis. No content-quality
pass can be assigned to these undelivered runs.

## Attribution

- Q04: completed public JSON lacks one closing brace. It was correctly rejected;
  the then-current H2 policy did not permit Root format correction.
- Q08: one article.read used article_ids; a later output put type/tool_name inside
  an unsupported action wrapper and used end_line. Root stopped on the wrapper.
- Q14: four article.list calls used unsupported date_from/date_to, authors or
  query. The model later produced valid list/read calls for both authors, then
  an invalid delegate with objective/instructions/article_ids instead of the
  accepted role/task or versioned research_task envelope.
- The H2 prompt lists capabilities without the required on-demand descriptions,
  and failed tool children return only a status without corrective detail. This
  is an implementation mismatch, not evidence for loosening parameter validation.
- The delegated-model input also lacks its actual output Schema; this is a
  code-inspection finding, not a child-model result from this batch.

## Follow-Up

The source was not edited while the fixed batch ran. Offline H2 checks before
launch were 229 passed / 7 skipped; six runner checks then passed (one additional
source-provenance check). These overlapping results must not be added.

Change 030 records approved fixes and the user's subsequent explicit one-shot
Root format-correction decision. Any post-fix batch gets a new directory and
report; R4 remains 0/3 deliveries. The other 27 Queries and real Redis remain
unverified, and the active baseline objective is not complete.
