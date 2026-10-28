# H2 R15 Delegated Source Return Acceptance

Date: 2026-10-20. Status: fixed batch and read-only audit completed;
targeted scheduling/source-return checks pass, semantic acceptance incomplete.

## Scope And Freeze

[063](../changes/063-h2-delegated-lineage-live-protocol.md) defines L01/L02,
controlled variants of the earlier single-article and two-author cases. Root
discovers metadata and grants it to analysts; only children read original
bodies. This explicit diagnostic assignment isolates
[062's source-return repair](../changes/062-h2-delegated-source-lineage.md).
It is not a universal financial workflow or an unchanged S01/S02 comparison.

Both execute once with DeepSeek/deepseek-flash, configured streaming and
FakeRedis. Each entire tree retains 120 physical calls, 60 minutes and the
24-call/10-minute Root reserve; the batch ceiling is 240. No model switch,
top-up, extra probe or repeat-until-success occurs. The driver processes
worker_once serially; asynchronous task states are not a parallel-throughput
measurement.

Before post-batch documentation edits, the final audit confirms all **412**
inventory entries, **898** snapshot files and the private execution wrapper
unchanged. The inventory includes dispatch tests and source/design/configuration,
not every inherited test. The new read-only audit script and its imported older
audit helper are not part of that frozen wrapper inventory. The reused research
day is snapshot metadata, not an explicitly injected model clock.

## Results

| Measure | L01: single article | L02: two authors |
| --- | --- | --- |
| Root delivery | Succeeded | Succeeded |
| Physical requests, entire tree | 13 | 33 |
| Duration | 208.72 seconds | 600.11 seconds |
| Answer length, characters | 2,557 | 4,679 |
| Accepted analyst results / requested children | 1 / 1 | 2 / 2 |
| Original body reads by Root | 0 | 0 |
| Full original reads by children | 1: 63/63 lines | 2: 63/63 and 59/59 lines |
| Successful child analyses fully returned to Root | 1 / 1 | 2 / 2 |
| Structural accounting/artifact checks | 12 / 12 | 12 / 12 |
| Applicable diagnostic checks | 6 / 6 | 7 / 7 |
| Structured semantic review | Absent | Absent |
| Final metadata status | incomplete_metadata | incomplete_metadata |

The audit exposes seven boolean diagnostic keys for both cases. Its
two-spawns-before-wait key is trivially true for L01 and is not counted as an
applicable single-child criterion. Structural checks cover charges, identity,
budget conservation, caps, settled records, exact model, bounded requests and
artifact hashes; they do not certify financial truth.

The batch uses **46** charged physical requests: 45 completed HTTP requests
and one transport outcome_unknown. The latter remains charged, not refunded.
L02 has three adapter contract_error records and one transport_error; Root
recovers within existing rules and budgets. The single permitted correction
transport retransmission is **not_triggered** in both cases. R14 Q28 remains
the separate live occurrence of that branch. No exact monetary bill or stable
failure rate is inferred from request counts.

## Source Return And Actual Use

- L01's child reads TT's 2026-09-30 source; L02's children independently read
  that source and Peifengke's 2026-09-17 source. Each source is read completely
  once in its case; Root never rereads an original body.
- All three returned source bindings match actual successful article.read
  observations, including author, date, content hash, line ranges and original
  reader Task. Root preserves those facts and the accepted result linkage.
- Root fully consumes each accepted analysis before final delivery. L02's two
  full-result events precede its terminal result commit, not merely a user export
  after completion. Both children are spawned before Root's first durable wait.
- Final actual evidence derives complete/used for the one L01 source and both
  L02 sources. L02's other 35 discovered articles stay unread/undecided. Returning
  source metadata does not make all discovered articles read or used.
- No child coverage/review/use judgment is imported as a parent fact. L01 has
  empty coverage; L02 records partial coverage and the thin two-article sample.
  Both lack a structured review, honestly retaining incomplete_metadata.

This is live evidence for 062 at depth one. Two-level source propagation and
fresh-Service recovery retain their separate offline evidence. Neither is
claimed to have been exercised by these two paid cases.

## Manual Content Review

The full delivered answers and successful source observations were inspected.
These are identified findings, not an exhaustive correctness score.

Useful behavior: both answers preserve source author/date, distinguish author
assertions from external verification, and explain reasoning rather than just
list conclusions. L02 discloses its two-article sample, the weaker gold coverage
of the selected FOMC note and the distinction between commodities generally and
gold specifically. It refuses to manufacture a shared long-term gold outlook.

Remaining problems:

1. **L01 invents a joint-necessity claim.** After listing several possible
   assumptions it says they must all hold simultaneously. The original does
   not establish that conjunction; not every listed mechanism is a necessary
   condition for the author's entire conclusion.
2. **L01 overstates its criticism.** Missing explicit falsification thresholds
   in this short article supports saying the article does not specify them;
   it does not establish that the long-cycle claim is inherently untestable.
   Alternative explanations are critiques, not independently verified causes.
3. **L02 overstates commonality across time and objects.** It says both authors
   reject a rate-expectations explanation for this particular precious-metals
   fall, although the earlier FOMC note does not analyze that later fall. The
   nearby caveat recognizes this, but does not make the opening claim supported.
4. **L02 erases a source qualification.** It says TT does not use the rate path
   at all to explain gold, while the original explicitly retains US yields as
   an observation item. A more limited claim about the selected fall's main
   proposed cause would be defensible; total exclusion is not.
5. **L02 makes dependencies too strong.** The final reasoning chain makes all
   of TT's conclusions depend on the Jane Street hypothesis. His separate
   seasonal-repair scenario and long-cycle stance are not established as
   logically dependent on that attribution. It also labels two September
   publication dates collectively as September-October material.
6. **Both can misrepresent the author's certainty.** The original uses both
   tentative suspicion and emphatic causal language, and says detailed research
   or advance prediction was unavailable. The answer may assess the evidence
   as insufficient, but should not attribute its own weaker certainty assessment
   to an explicit author admission that the entire claim is unproved.

L02's source selection is weak for a detailed gold comparison even though the
answer discloses it. This is a research-selection/coverage issue, not evidence
that a returned body or its identity was lost. There is no independent external
fact verification in either case, and agreement is not such verification.

## Disposition And Remaining Work

No new baseline implementation deviation was established by this targeted batch.
That is not a claim that no defects remain elsewhere. No new runtime policy or
code repair is justified merely to turn these semantic findings into a pass.

The existing C01-C18 evidence map remains in the
[current acceptance report](harness-v2-current-acceptance-20261019.md). Separate
unresolved categories must remain visible:

- Design decisions: isolate rejected format-correction input from trusted source
  context; optionally normalize one outer JSON fence; define reserve-backed
  financial closing entry. None is silently implemented here.
- Financial quality: attribution, conditions, time horizons, certainty and
  representative-source selection need separately agreed research behavior.
- Missing capability/integration: the full business catalog still lacks H2
  market/Web/Wiki/holdings/fault adapters; opt-in HTTP/service entry is not a
  default production-route or deployed-worker migration.
- Environment/evidence: real Redis remains explicitly deferred; production
  identity, sustained load and repeated measured success rates are unproved.

R14's 17/20 deliveries and semantic failures are unchanged. L01/L02 prove a
specific post-062 return boundary, not that the full financial agent is accepted
or that its quality improved causally. The overall goal remains open.

## Verification And Records

Before execution: 20 focused source-return cases pass; configuration/isolation
10/10 and zero-request preflight pass. The previous post-062 broad results remain
H2 **825 passed / 7 skipped**, default finance **1638 passed / 30 skipped**;
they were not rerun or recounted as new tests here. Runtime code is unchanged.

Post-batch documentation checks: 159 local file links across five affected
Markdown files resolve, with no trailing whitespace; git diff --check passes.
This does not certify heading anchors or every design statement. Git reports
the existing Windows line-ending conversion warnings, not whitespace failures.

Private records stay under the sibling runtime-records directory:

- h2-lineage-r15-20261020: frozen manifest/summary, isolated SQLite, source
  observations, model-call records, full delivered answers and final finance states.
- h2-lineage-r15-protocol: once-only execution wrapper and zero-request preflight.
- h2-lineage-r15-audit: zero-call read-only audit and saved audit.json.

The read-only audit exits zero for structural checks; its exit status alone
does not certify diagnostic checks or semantics. Diagnostic booleans and source
state/order were examined separately. Do not overwrite the pre-edit final hash
result by comparing against intentionally updated documentation afterward.
No original article bodies, complete answers, traces or credentials are added
to repository reports. No live corpus/database mutation, Docker operation,
default-1.0 switch, Git commit or push occurs. The fixed batch is terminal;
there is no remaining test process to restart.
