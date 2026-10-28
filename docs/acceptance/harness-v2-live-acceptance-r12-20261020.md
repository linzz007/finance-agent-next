# Harness 2.0 Live Acceptance R12

Date: 2026-10-20 (Asia/Shanghai)  
Scope: one fixed Q14 finance comparison run after the bounded correction
transport-retry implementation.  
Model: DeepSeek only (`deepseek` / `deepseek-flash`)  
Transport backend: FakeRedis  
Runtime path: `FinanceHarnessV2Service.for_finance_corpus`

## Result

Q14 completed with a delivered Root answer. The run used 17 physical model
calls in 141.19 seconds and stayed inside the 120-call per-tree cap. No model
fallback, budget top-up, unknown call, pending Outbox entry, or failed model
call was recorded. The complete private record is:

`D:\AAAcode\code-code\agent+\harness\runtime-records\h2-postauth-q14-r12-20261020`

| Dimension | Result |
| --- | --- |
| Root delivery | pass; `succeeded`, 2,977 characters |
| Model calls | 17 physical calls; 17 succeeded; 0 failed/unknown |
| Contract recovery | 2 initial `contract_error` responses, each recovered by the one permitted semantic correction |
| Correction transport retransmit | not triggered; no correction call encountered `transport_error` |
| Model selection | DeepSeek only; no automatic fallback |
| Whole-tree call ceiling | pass; 17 / 120 |
| Outbox settlement | pass; 0 pending, retrying or dead-letter entries |
| Snapshot integrity | pass; source snapshot unchanged |
| Real Redis | not run; FakeRedis only |
| Semantic acceptance | manual review required |
| Metadata status | `incomplete_metadata`; full answer was retained and delivered |

## Trace And Scheduling

The trace contains 15 durable task Attempts, 14 task continuations, 28
checkpoints, 14 tool action pairs, one final result acceptance, and one full
result read. The model first used the available article tools, then produced a
source-bound comparison, and the Root result was read in full before the run
was reported as succeeded.

This Q14 wording did not explicitly require independent child analyses, and
the Root did not spawn a child Task. That is consistent with the current
design: the Root owns the delegation decision and Runtime does not force
universal delegation merely to exercise the scheduler. This run therefore
validates the single-Root durable path, not multi-agent parallel delegation.

## Contract Recovery And Retry Boundary

Two model calls returned public action contract errors with complete output.
For each, Runtime preserved the rejected output, recorded an
`finance.h2.action_repair.v1` artifact, sent exactly one same-model semantic
correction, and continued from the corrected action. No research was added by
the Runtime and no rejected action was executed.

Neither correction transmission failed at the transport layer. Therefore the
new `finance.h2.action_repair_transport.v1` retransmission branch remains
offline-verified but live-untriggered. This run must not be described as proof
of recovery from a real network disconnect.

## Content Review

The delivered answer is materially useful for Q14. It:

- discloses the seven TT articles and three directly relevant 培风客 articles;
- separates common views, substantive disagreements, internal tension and
  topics not directly discussed;
- preserves dates, conditions and uncertainty instead of turning agreement
  into external fact verification;
- explicitly identifies the author statements that were not independently
  verified.

The answer is not an automated proof of financial correctness. The run keeps
`semantic_acceptance=manual_review_required`; external verification and
financial claim truth remain a human-review responsibility under the 1.0
business design.

## Remaining Gaps

1. The transport-retry branch still needs a controlled live network-fault
   observation or a later naturally occurring provider disconnect. Offline
   fault tests cover the exact third-call boundary.
2. This run does not validate explicit `agent.spawn`, child result reads, or
   multi-agent throughput. R10/R11 remain the relevant prior evidence.
3. Real Redis, multi-process workers, sustained load, the complete 30-query
   suite, and semantic financial correctness remain unproved.

## Conclusion

R12 passes the durable single-Root delivery, bounded correction recovery,
accounting, and snapshot-integrity checks. It shows that DeepSeek can complete
the current Q14 workflow with useful output, but the two recovered contract
errors show that raw model contract compliance is not perfect. The transport
retry change is implemented and offline-tested, while its live network branch
is still not exercised.
