# H2 Reject Forged Dispatch Notifications Without Stopping The Worker

Date: 2026-10-20. Status: existing-contract repair implemented and offline-verified
after specification and failure reproduction.

## Authority And Failure Path

The frozen [Redis protocol, sections 2 and 4](../harness-v2/redis-protocol.md)
requires notifications to match the persisted Outbox and requires malformed or
forged messages to be refused, durably audited, then acknowledged. SQLite
failures or an unavailable audit sink must leave the message pending. This is
an existing dispatch contract, not a new model-retry or financial policy.

`Worker.process` quarantines errors from wire-format validation, but calls
`Repository.claim` outside that boundary. A well-shaped notification with an
unknown Outbox ID or a changed persisted field passes wire validation and is
correctly rejected by claim with contract_error. That rejection currently
escapes the Worker and DispatchService, leaves an unaudited pending message,
and can interrupt processing of subsequent legitimate work. The Repository's
identity rejection is correct and must not be relaxed.

A second reproducer at the same rejection boundary uses a superscript digit in
dispatch_generation. str.isdigit accepts it, but int rejects it with ValueError
instead of the protocol's contract_error. Restrict that existing numeric check
to decimal digits accepted by int; retain previously accepted decimal forms.
Do not broadly catch ValueError around storage or quarantine operations.
The existing 4 KiB wire limit must also be checked before converting the
generation to int: an oversized decimal string can otherwise reach Python's
integer digit-limit exception before the protocol-size rejection. This changes
validation order, not the accepted size limit or any model-action parsing rule.

## Narrow Repair

- Apply the existing contract-error quarantine path to both wire validation
  and the initial claim. Never create execution authority for a mismatched
  message, mutate the referenced Task, or invoke its handler/model/tools.
- Persist the redacted rejected notification using the existing runtime-record
  sink, then ACK only its own Stream entry and return False. Preserve the
  original Outbox and permit its real notification to execute normally.
- Do not catch noncontract DispatchErrors, SQLite errors or storage failures
  as malformed input. If quarantine or ACK fails, propagate that failure;
  successful audit is a prerequisite for ACK, not a claim of execution success.
- Use the same behavior for ordinary receive and pending-message recovery.
- No DDL, queue semantics, budgets, models, model-action parser normalization, correction
  context, reading/review rules, default 1.0 path or Docker changes.

## Verification Plan

1. Before the runtime edit, reproduce rejection escaping for an unknown Outbox
   and for each well-shaped changed identity/generation/timestamp field.
2. Assert no handler invocation, Task/Attempt/Event/budget/model-call mutation,
   credential leakage, or ACK before durable quarantine.
3. Recover a forged pending entry with a new Worker; it must be audited/ACKed
   without acquiring a lease. Then process the genuine Outbox notification and
   deliver the original result exactly once through DispatchService.
4. Inject quarantine, SQLite, noncontract claim and ACK failures. Keep pending
   entries on failure; do not convert infrastructure errors into success.
   Add three nondecimal-digit failures and retain two valid decimal forms in
   wire validation; malformed numeric fields must reach the same audit boundary.
   Add an oversized numeric generation to prove size rejection precedes integer
   conversion rather than escaping the Worker as ValueError.
5. Run the focused module, H2/state/compatibility/live-driver collection,
   default finance regression, configuration/isolation, scoped Ruff and diff
   checks. Record actual results below, not predicted counts.

All cases use temporary SQLite/records and FakeRedis, with zero paid model
requests. They do not demonstrate real Redis, semantic answer quality or
commercial reliability. Preserve R14's outputs and pre-edit final audit.

## Reproduction And Focused Results

Fourteen net added cases cover eight changed message fields, fresh-Worker PEL
recovery, service continuation to the genuine notification, failed quarantine,
two noncontract claim failures, and failed ACK. Before the runtime edit, twelve
fail at the unhandled Repository contract rejection; the two noncontract
negative guards already pass. After the narrow Worker edit, the complete
observability module passes all 44 cases in 6.13 seconds. The identity checks
and no-mutation SQL dump assertions remain strict. Full scoped Ruff passes.

The numeric extension adds three nondecimal-digit cases, two previously valid
decimal forms, and an oversized 5,000-digit generation. The three nondecimal
cases fail before the decimal guard; the oversized case fails before moving
the 4 KiB bound ahead of conversion. The two compatibility guards already pass.
All 50 cases in the final observability module pass in 6.42 seconds. Overall,
twenty tests are new, with sixteen reproduced failures and four negative or
compatibility guards. Selected and broad counts overlap and are not additive.

The current acceptance matrix also corrects a stale statement about 048: R14
Q28 actually exercises one correction transport recovery. Older R10/R11 reports
remain unchanged; that single occurrence is not commercial reliability proof.

Before the numeric-validator extension, the first complete H2 collection passes
799 cases / 7 skipped in 261.43 seconds. This is an intermediate result, not
verification of the subsequent numeric-validator change.

## Final Verification

The final Worker/transport changes are verified by fresh completed collections:

| Check | Actual result |
| --- | --- |
| Complete observability module | 50 passed, 6.42 seconds |
| H2/state/compatibility/live-driver | 805 passed / 7 skipped, 330.38 seconds |
| Default finance/shared runtime, 52 modules | 1638 passed / 30 skipped, 186.89 seconds |
| Configuration/separation, rerun after final runtime edit | 10/10 |
| Full Ruff on Worker, transport and observability tests | Passed |
| Seven relevant Markdown files | 188 local file links resolve; not heading/semantic certification |
| Git diff whitespace check | Passed |

The broad collections overlap in coverage and ran with overlapping execution;
their counts and times are not additive or financial Agent latency measures.
Existing real-Redis skips, default-finance skips and the Starlette/httpx
deprecation warning remain. No paid model call, live DB/corpus mutation, Docker
operation or Git commit/push occurs. The frozen R14 report and pre-edit final
audit remain unchanged. Its semantic failures, missing adapters, real Redis,
and pending correction-input/fence/closing-mode choices remain unresolved.
