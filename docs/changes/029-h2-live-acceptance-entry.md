# Explicit H2 Live Acceptance Entry

Date: 2026-10-19

Status: verification tooling for approved H2 contracts; not a new research policy.

## Reason

R3 established that the existing `finance_research_live_acceptance.py` invokes
the legacy ChatWorkbenchService. It cannot prove H2 Root/Attempt state handoff.
The active user goal requires code acceptance followed by Query acceptance, so
an explicit H2 runner is needed before making further live-H2 claims.

## Fixed Protocol

- Invoke `FinanceHarnessV2Service.for_finance_corpus`, not the legacy AgentLoop.
- Require `--live`, an explicit named model profile and an existing verified
  private acceptance snapshot. No implicit model fallback or provider switch.
- Select the same frozen 30-query catalog as 1.0; default to the diagnostic
  Q04/Q08/Q14 set once each. `--all-cases` is explicit; this first diagnostic
  batch is not the full-set goal or proof of all baseline requirements.
- Preserve the approved SchedulerConfig/ModelSettings budgets. Only an actual
  terminal task ends a case; host errors remain separate from runtime status.
- Use an independent corpus index, H2 SQLite database, unique stream namespace
  and run-local artifacts. Never reuse the user's live database.
- Save private artifacts under the approved sibling `runtime-records` directory;
  refuse existing batches and linked/escaped output paths. Freeze source hashes,
  snapshot hashes, selected profile metadata, configuration and query selection.
- Export H2 events, physical model-call ledger, checkpoints/state and complete
  paginated final body. Keep gaps, warnings and semantic status separate from
  successful scheduling. There is no automatic semantic grading.
- FakeRedis is an explicitly selected verification transport when no service is
  available. Label it in the manifest and report; it is never called real Redis
  integration. Do not start or repair Docker for this batch.
- A failed batch is retained. Code defects within approved contracts can be
  fixed and revalidated in a new batch; unresolved design choices are listed
  without changing them or rerunning until lucky.

## Offline Checks Before Paid Runs

Run the actual H2 composition with a mock HTTP model and fixture corpus. Prove
that it records Root/child Attempts and model-call rows, preserves structured
state and complete final bodies, and exports the actual H2 path. Prove explicit
live/profile/selection/path guards and terminal failures without output. No real
credentials or corpus are needed for these tests.
