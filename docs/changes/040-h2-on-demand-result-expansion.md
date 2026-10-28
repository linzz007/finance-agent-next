# H2 On-Demand Result Expansion

Date: 2026-10-19

Status: existing-contract repair discovered during result-return audit.
No new research method, mandatory full-read policy or paid batch.

## Authority And Defect

[013 section 8](013-harness-v2-dispatch-baseline.md) and
[interface section 7.2](../harness-v2/interface-contracts.md) distinguish the
always-delivered summary/ref envelope, capacity-checked short-body injection
and on-demand long-result reading. Main Agent selects result.get; a read/export
or dispatch success must not be substituted for its actual result consumption.

Repository.execution_input already applies the 16-KiB and context-capacity
checks. However, FinanceH2Domain._consume_observations automatically calls
result.get(full, limit=64000) when a notification has no full_content. This
undoes the omission decision, can load a large result without model selection,
and creates a read event for a page the model did not request. The existing
long-result unit test checked incomplete JSON handling, but expected this
unapproved eager expansion.

Removing that fallback exposed another composition omission: the finance Service
did not supply Worker's context_accepts callback. Consequently, even eligible
short results were never auto-injected by Repository; the hidden domain fetch
had masked this missing connection. Both sides must follow the existing contract,
not change tests to accept missing short-result delivery.

## Repair

- Respect the original observation: consume supplied full_content when present;
  otherwise retain the real status, summary/key points/EvidenceRefs/gaps/ref.
- Mark full_read=false and identify result.get for explicit expansion. Do not
  retrieve a fallback page, parse partial JSON, fabricate an analysis material
  or imply that a result notification contains the entire child analysis.
- Model-selected result.get actions retain their durable page observations and
  existing complete-page reconstruction. [039](039-h2-result-read-coverage.md)
  records completed coverage across those actual actions. Do not perform a
  second hidden fetch to make the material appear fully read.
- Keep short-body injection, all permissions/budgets, financial decisions,
  original analysis retention and default 1.0 unchanged. This does not require
  Main Agent to read or use every successful child result.
- Connect the finance Worker's capacity callback to the actual Attempt model's
  settings and required execution input, honoring input/context/output/safety
  limits. If no valid capacity can be established, omit the optional body. The
  domain still checks its fully assembled model input against the same limit;
  the preliminary transport check does not certify all future prompt contents.

## Offline Acceptance

- A notification without full_content causes zero result.get calls while its
  envelope and explicit unread-body limitation reach the next model input.
- A supplied complete body still becomes a run-local material unchanged.
- Eligible short results still auto-inject at the Worker boundary. Small input
  limits and output/safety reservations can refuse injection without an eager
  domain fetch. Invalid capacity settings do not authorize optional injection.
- Through the actual opt-in finance Worker/adapter/checkpoint loop, notify a
  completed fixture result larger than 16 KiB; assert no full/page read occurs
  before the model requests it. Then explicitly request both pages in different
  Attempts, verify exact reconstruction and one truthful full_viewed event.
- Retain direct expansion coverage and ledger counts, run full H2 and default
  finance regression, and leave frozen live results unchanged.

## Verification

Before the fix, the no-eager-fetch unit test and notification-first integration
failed; direct model-selected expansion passed. The fixture notification was
automatically read in full despite exceeding 16 KiB. After removing that fallback,
the broad regression exposed missing short-result injection; the Service callback
was then connected before repeating the complete collection.

Final focused context/continuation/Service/compatibility regression: 35 passed
in 42.48 seconds. Full H2/state/compatibility/live-driver regression: 536 passed /
7 skipped in 206.81 seconds. Default finance/shared-runtime: 1606 passed /
30 skipped in 123.39 seconds. Configuration/separation was 10/10 and scoped Ruff
passed. The 31 net additions after 037 comprise 12 in 038, 14 in 039 and five
in 040. Collections overlap and must not be summed as independent evidence.
All requests are fixture-only; real-model child return and real Redis still
require separate validation.
