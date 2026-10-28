# H2 Text Parent Context Handoff

Date: 2026-10-20. Status: existing-contract repair implemented; offline verified.

## Authority And Reproduction Target

[013](013-harness-v2-dispatch-baseline.md), section 8.1, and the
[spawn contract](../harness-v2/interface-contracts.md), section 3, require
explicitly authorized input_refs/parent_context_refs and necessary parent
conclusions to reach the child. [017](017-harness-v2-finance-loop-integration.md),
section 5, preserves that isolation in the financial domain. The ArtifactStore
and Repository.store_text already support UTF-8 text/plain artifacts; these
contracts do not require every context artifact to be a JSON object.

Before repair, the finance message builder treated a JSON decoding error as an
unavailable material. Its child article-scope collector also decoded every
granted artifact as JSON before collecting structured article identities. A
valid plain-text parent note could therefore disappear from model input and
cause an otherwise granted article.read to fail at scope collection. Reproduce
this with synthetic text and a granted structured article, never live user files.

## Repair Boundary

- Decode authorized UTF-8 material into its existing JSON value when valid JSON;
  otherwise preserve the original text as a string. Do not normalize malformed
  actions, Checkpoints, observations or research results under this rule.
- Present text through the existing material handle/payload representation.
  Under actual input pressure retain the handle and material.get expansion
  marker, not a false unavailable status. No new full-text injection threshold.
- Article-scope traversal may inspect structured JSON as before, but plain text
  contributes no article identities. A mentioned ID in a parent note is not an
  additional grant. Existing authorization and artifact/hash checks still run.
- Keep invalid UTF-8, missing/foreign/corrupt artifacts and cancellation/fencing
  failures distinct from valid text. Do not swallow load/permission errors to
  make scope collection succeed.
- Preserve strict ResearchResult source binding: a parent note is not an
  original article or an externally verified fact. Full useful result bodies
  remain deliverable with honest partial metadata when a source link is invalid.
- Do not change schemas, SQL, queue/Attempt semantics, budgets, repair policies,
  model selection, financial methodology or the default 1.0 route.

## Verification Plan

Reproduce missing plain-text payloads and failed scope collection before the
runtime change. Test Root and research input, bytes/string text, configured
pressure and unchanged JSON values. Exercise real SQLite/FakeRedis/mock-provider
spawn with a plain-text parent_context_ref and a structured assigned article:
the note must reach the child, ungranted articles must remain refused, the
assigned article must be readable, and continuation through a fresh Service
must retain the note and source isolation. Verify physical-call accounting and
compact Checkpoints. Also assert a plain parent note cannot become accepted
source evidence in the research finalizer.

Run broad H2/state/compatibility/live-driver and default finance regressions,
configuration/separation, full scoped Ruff, documentation links and diff checks.
No paid request is needed for this deterministic handoff defect. Preserve R13
results and pending correction-input/closing-mode design decisions.

An existing test asserted the former plain-text-unavailable workaround. Update
that presentation expectation to visible text under the confirmed context
contract. Keep the separate strict source-binding tests unchanged: visibility
does not establish an original article read or accept the parent note as evidence.

## Actual Verification

Before the runtime repair, eight focused visibility/scope/pressure cases and
two SQLite/FakeRedis/mock-provider integration cases reproduced the defect.
After repair, the first complete three-module run had **245 passed / 1 failed**:
the sole failure was the historical unavailable-presentation expectation above.
Updating that expectation did not relax the separate strict source-binding
checks. The final three-module run passed **246 cases**, 70.15 seconds.

Twenty-three new cases cover text visibility, valid JSON-value preservation,
actual input pressure, scope collection, load/permission/fencing failures,
invalid UTF-8 and rejection of parent notes as article evidence. Two integration
cases place the text in parent_context_refs or first in input_refs. Each reaches
the actual child model input, refuses an ungranted article, reads the assigned
article and continues through a fresh Service before both Tasks succeed. Each
has six mock physical requests with matching ledger identities; Checkpoints
remain compact and the stored note bytes are unchanged. A fresh Service is not
a fresh OS process, and FakeRedis is not real Redis.

Actual final post-057 results:

| Check | Result |
| --- | --- |
| H2/state/compatibility/live-driver | 762 passed / 7 skipped, 274.08 seconds |
| Default finance/shared runtime, 52 modules | 1631 passed / 30 skipped, 123.50 seconds |
| Configuration/separation | 10/10 |
| Full Ruff on four changed Python files | Passed |
| Eight current Markdown documents | 186 local file links resolve; heading anchors/whole-design semantics not certified |
| Git diff whitespace check | Passed |

The focused results overlap the broad collections; do not sum them. The seven
H2 skips still require real Redis; the existing Starlette/httpx deprecation
warning remains. The inherited repository-wide collection was not rerun.

No paid request was needed for this deterministic defect. No live DB/corpus
mutation, Docker operation, default-route switch or Git commit/push occurred.
R13 records stay unchanged. This does not certify model interpretation, repair
R13's invented analysis or financial attribution errors, or establish full
business/real-Redis/load acceptance. Correction-input isolation and the finance
closing-mode entry remain pending design choices. The overall goal remains open.
