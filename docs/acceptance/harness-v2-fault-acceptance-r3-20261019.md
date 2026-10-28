# Harness 2.0 Fault Acceptance R3

Date: 2026-10-19

## Scope And Authority

This is additional verification of the already approved failure matrix in
`docs/harness-v2/redis-protocol.md` section 7. No new dispatch behavior, financial
research policy, model fallback, default entry point, or production migration is
authorized by this report. `finance.state_update.v1` remains the approved F08/F09
protocol.

All tests use temporary SQLite databases, synthetic tasks, and a unique Redis
stream per case. They never call a model, read the live article corpus, or flush
a shared Redis database. Real-service tests require `H2_TEST_REDIS_URL`; the
same scenarios also run against FakeRedis, reported separately.

## Verification Plan

| Scenario | Required observation |
| --- | --- |
| XADD succeeds; SQLite published confirmation fails | Republishing may duplicate notifications, but competing Workers execute the Attempt once |
| Result commits; ACK response fails | Result remains readable; recovery only ACKs and does not rerun the handler |
| Lease expires; old result arrives late | New Attempt may execute; old result cannot replace it; synthetic sent-call charge remains unknown, not refunded |
| Parent waits; Worker/Repository is recreated | Same Task resumes with a new Attempt, checkpoint, and real child result |
| Four Workers process 24 child tasks | Each Task has one accepted result; parent wakes with all 24 results; PEL settles |
| PEL ownership changes during a valid lease | SQLite lease remains authoritative; no second execution |
| Stream is lost | SQLite rebuilds the queued notification without changing Attempt identity |

These are bounded integration/fault tests, not a long-duration load test or proof
of process-kill safety at every instruction boundary.

## Environment

The installed Docker client does not establish an available Redis service.
An explicit attempt to start Docker Desktop failed during its own inference
manager socket initialization. No Docker reset, WSL shutdown, deletion of Docker
state, or mutation of unrelated services was attempted. Real Redis results must
remain unverified until a service is available.
The user explicitly chose to continue other verification without a Redis
service. No further environment repair or service startup is part of this run.

## Previous Query Evidence: Entry-Point Correction

The `data/acceptance/h2-code-fix-r1-20261019` Q04/Q08/Q14 batch uses
`scripts/finance_research_live_acceptance.py::run_case`, which constructs
`ChatWorkbenchService` and executes its prepared turn. It does not construct
`FinanceHarnessV2Service`, a Redis Worker, or a durable H2 Root task.

Its three returned answers and tool errors are valid legacy-finance regression
evidence, not evidence that H2 Root state updates, wait/resume, or Redis dispatch
worked with a live model. The batch name must not be treated as execution-path
proof. Original runs and their recorded outcomes are retained unchanged.

## Tool Failure Attribution

Inspecting the private traces gives seven failed tool observations, not seven
failed research deliveries:

| Case | Failure | Observable recovery and classification |
| --- | --- | --- |
| Q04 | `article.read` received `line_start/line_end` | Turns 4 -> 5 -> 6: rejection, `tool.describe`, valid `start_line/limit` call |
| Q04 | `market.metrics` received `metrics/windows` | Turns 10 -> 11 -> 12: rejection, description, valid `snapshot_id` call |
| Q04 | `web.open` received `max_lines` | Turns 16 -> 17 -> 18: rejection, description, valid `url` call |
| Q04 | A later `web.open` returned HTTP 401 | External-source access failure; not a model-credential or scheduler failure |
| Q04 | `research.checkpoint` used unrecognized/missing fields | Turns 25 -> 26 -> 27: rejection, description, valid `checks/summary` call |
| Q08 | `article.read` had an extra nested `arguments` field | Turns 26 -> 27: invalid call rejected, next call corrected; the schema was already described at turn 3 |
| Q14 | `article.read` received `line_start/line_end` | Turns 7 -> 8 -> 9: rejection, description, valid `start_line/limit` call |

Six are model-produced argument contract violations. The trace proves that
schema lookup/correction worked after these failures; it does not prove the
fields were ever supported in an older tool version. Q08 also shows that prior
schema exposure alone cannot guarantee consistent model output. These failures
cost calls and time, but they do not by themselves justify parameter aliases,
relaxed validation, or a claim of H2 dispatch failure. No such behavior change
was made in this run.

## Approved-Protocol Defects And Repair

[028](../changes/028-finance-state-update-delivery-fixes.md) records two actual
implementation mismatches with 026: invalid optional terminal deltas could
discard valid final bodies; persisted review/delivery were absent from the next
Root model input. Both were reproduced through the real H2 composition using a
mock HTTP model, then repaired without changing 1.0 research semantics.

The initial five-scenario integration run had one pass and four failures.
Exposing persisted review/delivery gave two passes and three failures, isolating
the invalid-final-delta boundary. After repairing that boundary, the focused
integration/state checks passed. Negative cases retain strict rejection for
invalid non-final deltas, invalid output, unrelated unknown fields, and
non-finite JSON values. No candidate is recovered from prose or truncated JSON.

## Results

| Check | Result |
| --- | --- |
| All `test_dispatch_*.py` + state-update + H2 compatibility | 224 passed, 7 skipped, 1 existing deprecation warning; 91.84 seconds |
| Financial/shared Runtime baseline (`test_finance_baseline.py`) | 1606 passed, 30 skipped, 1 existing deprecation warning; 168.71 seconds |
| Separation/configuration (`check_finance_baseline.py`) | All 10 checks passed; zero model calls |
| Changed Python files Ruff | Passed |
| Dispatch/test compileall | Passed |
| `git diff --check` | Passed; Windows line-ending notices only |
| Real Redis service | Not run; all 7 service variants skipped |
| New paid financial Query runs | None |

The two pytest collections overlap; their pass counts must not be summed into a
larger unique-test count. The financial baseline finished before the final
H2-only clarification distinguishing invalid deltas from missing deltas; the
final H2 collection above covers that clarification.

The new bounded campaign uses four Worker objects in one Python process, not
four OS processes, and has no physical model calls. Repository recreation and
injected commit/ACK failures are not equivalent to abrupt OS process termination
at every kill point. Long-running multi-process validation remains pending.

FakeRedis, real Redis, offline state protocol tests, and live financial quality
remain separate evidence categories. This run verifies the repaired offline H2
boundaries, not commercial readiness or full live-model state-update compliance.

## Remaining Work

- Obtain an isolated Redis service and run the seven `redis-service` variants;
  then separately test restart/kill points and sustained multi-process pressure.
- Run live financial acceptance through the explicit H2 service, recording the
  selected model, allowed profiles, state updates, physical-call ledger, root and
  child Attempts, preserved final body and actual evidence scope. Do not reuse a
  legacy batch name as proof, switch models implicitly, or rerun until lucky.
- Keep financial nested child action loops and public HTTP/front-end integration
  outside this repair; reconcile their precise approved scope before expanding
  those paths.
