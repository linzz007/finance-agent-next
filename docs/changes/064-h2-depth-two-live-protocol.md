# H2 R16 Two-Level Delegation Diagnostic

Date: 2026-10-20. Status: fixed batch completed; frozen protocol retained.

## Existing Requirement And Evidence Gap

[013](013-harness-v2-dispatch-baseline.md) section 7 and
[H2 interfaces](../harness-v2/interface-contracts.md) C07 allow Root depth zero,
research child depth one and research grandchild depth two; depth three is
forbidden. [062](062-h2-delegated-source-lineage.md) preserves original source
lineage through both return boundaries. R15 proves live depth-one handoff,
while depth-two execution and lineage only have deterministic offline evidence.

The active goal and the user's general paid-test authorization permit closing
this evidence gap without another payment question. This is one new scheduling
diagnostic, not another repetition of the completed R15 batch or a new financial
research method. Pending parser, correction-input and closing designs stay
unchanged. Default 1.0, HTTP deployment, real Redis and live user data stay untouched.

## Frozen Case N01

Use the same isolated article/company snapshot as R15, with DeepSeek only and
FakeRedis. Root identifies TT's 2026-09-30 article through metadata, then asks
one analyst@2.0.0 child to coordinate an independent reading by exactly one
analyst@2.0.0 grandchild. Only the grandchild calls article.read. The child fully
consumes the grandchild's accepted analysis and returns its own useful analysis;
Root fully consumes the child's analysis before final synthesis.

Grant actual authorized metadata artifacts, not article IDs as artifact refs.
Root requests 32 calls/20 minutes for the child; the child requests 10 calls/10
minutes for the grandchild, all from the original tree account and deadline.
These explicit diagnostic grants are not new runtime defaults. No ancestor
body reread, new corpus access, direct SQL/Redis access or universal forced
delegation is introduced. Report partial/failure honestly rather than inventing
a completed descendant. Both financial outputs retain author/date, conditions,
uncertainty and source citations; no external verification is implied.

## Bounds And Freeze

- N01 once; whole-tree and batch ceiling 120 physical requests, 60 minutes.
  Preserve the existing 24-call/10-minute Root reserve and child reserve policy.
- Count all ancestors, descendants, format corrections and allowed transport
  recovery. No model switch, fallback, added budget, extra probe or automatic rerun.
- Before first request freeze source/design/configuration, dispatch tests,
  private execution/audit helpers and the reused input snapshot. Check all at
  completion before repository edits. Private runtime-records output is separate
  from R15 and all old evidence remains immutable.
- Recheck the three relevant recursion/lineage tests, configuration isolation
  and zero-request preflight before execution. No runtime source changes are
  required to test this existing capability.

## Acceptance And Limits

Audit accounting/model/artifact invariants separately from these case checks:

1. Exactly three Tasks with actual parent links and depths zero/one/two.
2. Both accepted research results return and all Tasks complete honestly.
3. Only depth two successfully reads the assigned original, completely.
4. The child consumes the complete grandchild result before its submission;
   Root consumes the complete child result before its submission. Post-completion
   user exports do not count as Agent reads.
5. Accepted finance_sources at both return boundaries match the deepest real
   source observation. Child and Root state preserve its author/date/version,
   complete ranges, original reader and actual use without ancestor rereads.
6. No depth-three task, permission expansion, unknown-call refund or cap breach.

Missing or failed descendant return means its lineage check is not proved, not
a successful no-op. Review final semantics against the saved original even if
all scheduling checks pass. One diagnostic cannot certify sustained reliability,
parallel throughput, real Redis, full business acceptance or financial truth.

## Actual Preconditions

Before freezing the paid input, the three focused tests (two-level permission
boundary, shared research recursion and original-reader lineage) pass in 16.23
seconds. Configuration/isolation checks pass 10/10 with zero calls. Private
execution/audit scripts pass full configured Ruff checks, and the once-only
wrapper's preflight passes with zero requests. Runtime source is unchanged
since 062; broader 825/7-skipped and default 1638/30-skipped results remain
historical post-062 executions, not new results of this diagnostic.

## Completed Run

[R16 acceptance](../acceptance/harness-v2-live-acceptance-r16-20261020.md)
records N01's failure before any tool or descendant execution: 2 charged
contract_error responses, 6.55 seconds, no final answer. The original appends
a fabricated XML tool result; the single correction is malformed JSON. Runtime
rejects both under the existing rule. Depth-two execution and lineage are
not_triggered, not a passed or disproved recursive workflow. Twelve structural
checks pass; final pre-edit hashes match 414 inventory entries, 898 snapshot
files and three private helpers. No automatic rerun or runtime policy change.
Correction-input isolation and single-fence normalization remain user decisions;
the latter would not accept the XML/malformed output observed in N01.
