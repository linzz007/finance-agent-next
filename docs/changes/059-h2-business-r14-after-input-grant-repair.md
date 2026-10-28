# H2 Business R14 After Input-Grant Repair

Date: 2026-10-20. Status: fixed batch completed; original protocol retained.

## Purpose

This is a new one-time business regression after [058](058-h2-legacy-envelope-input-grants.md).
It checks whether the confirmed input-grant repair changes the opt-in H2 financial
path without rewriting the earlier R13 results. It is an acceptance record, not
a new financial sampling policy and not a success-seeking rerun.

## Frozen Scope And Limits

- Preserve the complete approved 30-Query catalog and the R13 article/company
  snapshot, with its recorded research day. Q26 keeps only the approved absent-
  author fixture substitution.
- Execute each currently executable Query once on the fixed `deepseek` profile
  (`deepseek-flash`) only. Use FakeRedis; real Redis remains deferred.
- The current adapter can execute Q01-Q02, Q04-Q18 and Q26-Q28 (20 cases).
  Q03, Q19-Q25 and Q30 remain honest `blocked_missing_capability`; Q29 remains
  `requires_isolated_environment` and must not read a host file.
- Each executable case has a maximum of 120 physical model requests for the
  whole task tree and a 60-minute deadline. The batch maximum is 2,400 physical
  requests. Children, structure corrections and correction-transport retries
  all count. Root reserves, no fallback, no model switch, no top-up and no
  automatic repetition remain unchanged.
- Preserve every SQLite record, artifact, event, checkpoint, model-call record,
  budget entry, final answer and failure. Stop future cases on a provider/quota
  environment blocker; do not repeat a charged case.
- Freeze the defined source inventory and the copied snapshot before the first
  request. Compare them before each next case and at completion. Do not edit the
  repository, prompts or protocol during execution.

The actual inventory contains 392 entries: src Python, plugin Markdown/JSON,
docs Markdown, configured entrypoints/configuration, AGENTS, schema.sql, the
acceptance driver and tests/test_harness_v2_live_acceptance.py. It does not hash
every offline test module. This clarifies the inventory scope without changing
the completed execution protocol.

## Acceptance And Interpretation

Review Runtime delivery, child material grants, actual source reads, evidence
binding, coverage/review metadata and answer content separately. A delivered
answer is not automatically semantically correct. Structural accounting and
source-hash checks do not prove financial reasoning, commercial reliability,
real-Redis behavior or concurrent throughput.

The input-grant repair is exercised only where the model chooses the compatibility
child-request path; an unchanged result does not prove that branch was live.
Retain R13's semantic findings even when R14 improves delivery. Do not add a
financial gate, fixed reading count, automatic external verification or tolerant
JSON parser during this batch.

## Records

Runtime records are written outside Git under:

`D:/AAAcode/code-code/agent+/harness/runtime-records/h2-business-r14-20261020`

The private batch output, original R13 snapshot, all physical request charges,
and the read-only audit are retained. The completed
[R14 report](../acceptance/harness-v2-live-acceptance-r14-20261020.md) records
403 physical calls, 17/20 deliveries, three terminal contract failures and one
actual successful correction-transport retry in Q28. All frozen hashes match
at completion. The compatibility envelope branch was not exercised, and the
batch does not certify semantic acceptance. Older R13 files remain unchanged.
