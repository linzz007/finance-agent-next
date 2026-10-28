# H2 Independent Task Call Limits

Date: 2026-10-20. Status: user-approved design; implemented and verified offline.
This supersedes 033's model-selected call
allocation for new H2 runs, not any frozen historical run or result.

## Confirmed Limits

The Root Task may make at most 120 physical model requests itself. Every depth-one
or depth-two research Task may make at most 60 requests itself. Descendant calls
do not consume the parent's personal limit. All Tasks in a Run share a separate
600-request ceiling and the existing 60-minute Run deadline. Corrections, allowed
transport retransmissions and outcome-unknown requests count. These are ceilings,
not an instruction to keep working until they are spent, or exact currency limits.

The Agent chooses assignments, materials, roles and completion. It does not choose
call counts. New fixed-policy `agent.spawn` and terminal `agent.send.follow_up`
descriptions omit `requested_budget`; optional `wall_time_ms` requests only a
duration within the original ancestor deadline/reserves. An omitted duration uses
the existing 20-minute analyst ceiling bounded by the parent's remaining research
time. Legacy delegate on a new fixed-policy Run uses the same 60-call limit;
old hierarchical Runs retain their original grants, durations and replay semantics.
Never silently accept a model-selected `requested_budget` on a fixed-policy Run.

## Persistent Accounting

Keep the existing root grant and transfer/charge/refund ledger. Add nullable
`self_call_limit` to `h2_budgets`: NULL denotes the original hierarchical policy;
new fixed Runs persist 120 on the root budget and 60 on child budgets. Persist the
root's child default in its immutable brief so restoring with different process
configuration cannot change it. The root's `allocated_calls=600` remains the Run
pool, separately from its personal 120. A child's allocated/requested 60 records
its ceiling, not 60 prepaid credits.

Creating or resuming a fixed Task does not transfer its full personal allowance.
Before each physical call, one SQLite write transaction checks the original local
limit, total Run balance, identity, permissions and time. A descendant lacking a
refunded credit transfers exactly one credit from the root pool, then charges
its own account once. Depth-two transfers come from the Run pool, not its parent's
personal allowance. Existing paired transfer entries preserve conservation.
The existing root closing reserve is unavailable to descendants; this does not
introduce an automatic closing phase or claim guaranteed delivery at exhaustion.

Only a confirmed unsent request can be refunded, once, to its original account;
terminal descendant refunds return to the root pool. Unknown or started requests
remain charged. Resume/checkpoint/Attempt replacement retains the original
personal counter, Run pool and deadline. A terminal Thread follow-up is a new Task
with its role's limit, still inside the same Run pool/deadline and permissions.

Fixed-policy status/model context separates personal requested/allocated/consumed/
remaining from `run_budget` (total limit, consumed, remaining). Descendant spending
cannot make the parent's personal consumed counter increase. All values derive
from persisted budgets/ledger, refreshed for every Attempt. State updates cannot
modify policy. Budget-denied diagnostics identify the exhausted local or Run scope.
Descendant `local_available` excludes the protected Root closing reserve; resume
must reject when only that reserve remains. Legacy command hashes omit the new
optional `wall_time_ms` when absent, preserving old spawn/follow-up replay keys.

## Compatibility And Migration

Preserve the original DDL checksum and append an explicit additive migration;
old budget rows receive NULL. Migrations remain explicit and transactional. Do not
migrate the user's live DB or rewrite R17. Existing hierarchical behavior remains
available for historical/compatibility tests. New SchedulerConfig defaults to fixed
policy (120/60/600); an explicitly smaller acceptance Run ceiling remains allowed
and is disclosed separately. Default financial 1.0, provider/model selection,
correction count, recursion depth, Redis protocol and research rules do not change.

## Required Verification

1. Default Root 120, children/grandchildren 60, total 600; no model-selected call
   count in spawn/follow-up descriptions; permissions and strict schemas hold.
2. More than ten idle children can exist without preallocating 60 each. Reading
   six articles plus final delivery can exceed eight calls and complete.
3. Parent/child/grandchild counters are independent. Local and total exhaustion
   deny before sending; concurrent reservations cannot exceed the shared cap.
4. Corrections/retransmissions charge each physical request; confirmed-unsent
   refunds are idempotent; unknown requests are not refunded.
5. New repository instances, checkpoint continuation, failed-task resume and
   terminal follow-up preserve original limits, accounting and deadline.
6. Additive migration preserves old data/checksums and original hierarchical
   accounting. Default-finance regression remains separate from H2 verification.

## Implementation Evidence

Implemented the fixed limits, persisted policy, on-demand model-facing schemas,
same-policy legacy delegate, atomic charge/refund paths, resume/follow-up behavior
and separate personal/Run budget status. The explicit additive migration preserves
the original DDL checksum; future acceptance inventories include the new SQL file.

Thirteen new offline cases cover the personal caps, whole-Run cap, no prepaid idle
children, depth-two spending, reopening with changed process config, resume and
follow-up, unknown/unsent settlement, concurrent last-credit reservations, strict
fixed tool parameters, and empty/populated historical migrations. The populated
upgrade preserves real charges/transfers and legacy spawn/follow-up replay hashes;
a bad extension checksum rejects without modifying the budget ledger.

The integrated SQLite/FakeRedis/mock-provider case executes article.list, spawn,
wait, child tool.describe, six first-page article reads, the remaining 79 lines
of a 179-line article, child final and Root final. The child uses 9 actual adapter
requests over persisted Attempts, Root uses 4, total 13. All six current article
versions are complete and their accepted source lineage reaches the Root. This
tests the former eight-call failure pattern without pretending the mock proves
financial understanding. Its first draft failed because the test treated every
known material as a source EvidenceRef; it now selects actual article.read artifacts
and keeps runtime evidence validation unchanged.

Final offline results:

- H2/state/compatibility/live-driver regression: **901 passed / 7 skipped**,
  372.31 seconds. The earlier intermediate run was 898 / 7 before the final three
  added cases; neither count is added to the other.
- Default finance/shared-runtime: **1638 passed / 30 skipped**, 142.26 seconds,
  52 modules. Configuration/separation: **10/10**.
- Full Ruff on the ten touched runtime/driver/new-test files and git diff checks
  passed. Existing dependency deprecation warnings remain.

No paid model rerun, live DB migration, Docker operation or Git commit/push was
performed for this change. Historical R17 and earlier batches remain immutable.
Real Redis, live model quality, delegated deep-source handle limitations and
entry into reserve-backed finalization are not certified or redesigned here.

## Subsequent Live Evidence

[R18](../acceptance/harness-v2-live-acceptance-r18-20261020.md) delivers unchanged
Q08 with 18 Root requests but no child; it does not exercise child60. The separate
[R19](../acceptance/harness-v2-live-acceptance-r19-20261020.md) diagnostic creates
one child with persisted60, consumes Root9 + child2 =11 without precharging60 or
debiting Root's personal count for child work. Its child fails on DSML then an
invalid corrected evidence anchor before reading, with58 personal calls unused.
Budget enforcement is active; successful live multi-article child completion
remains unverified. These new records do not rewrite historical grant results.
