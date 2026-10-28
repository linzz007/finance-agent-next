# H2 Current Acceptance And Remaining Evidence

Date: 2026-10-19; latest evidence update: 2026-10-20.

## Latest 079 Public Source Integration

[079](../changes/079-h2-public-source-adapter.md) fills only the public-source
part of RC09. Explicit service composition plus Task permission is required;
default ROOT_TOOLS/1.0 stay offline/unchanged. It reuses safe legacy web tools
and strict input models, not a new Agent framework. Only opened page text is
source evidence; search leads/failures are not. Public provenance retains the
original reader, URL, dates, hash and ranges across both handoffs without
entering article_states or granting private source access. Saved observations
survive a fresh Service without refetching the committed page.

33 new tests, combined H2/state1092 pass/7 skipped (404.33s); default1638/30
(125.06s,52 modules),configuration10/10,scoped full Ruff/diff and181 local link
targets pass. Preserve the six initial reproductions and two later fixture
budget failures. Opt-in preflight now enables24/30 cases, not24 passed cases:
Q21/Q23-Q25/Q30 still lack other capabilities;Q29 needs isolated environment.
[080](../changes/080-h2-public-source-live-protocol.md) freezes one new R23/Q19
DeepSeek/FakeRedis test; it has not yet executed at this documentation point.
Review/closing decisions, semantic quality and real Redis/load remain open.

[081](../changes/081-public-url-secret-scan-mvp.md) fixes a confirmed technical
false positive found in R23: the generic secret scanner rejected every URL with
a query string, so a normal public `?page=2` navigation link failed while the
page had already been fetched. Credential parameter names and key-shaped values
remain blocked; ordinary public pagination/filter parameters are allowed.
The focused execution regression is 191 passed. [082](../changes/082-h2-public-source-r24-protocol.md)
freezes a new same-Q19/same-model/same-budget R24; R23 remains unchanged.

R24b has now completed in `runtime-records/h2-public-r24b-20261020`: 32 DeepSeek
requests, 539.51 seconds, Root delivery succeeded with 2,772 characters and
two page-level public sources. The URL scan error did not recur. One analyst
task ended with six contract errors; Root delivered with
`metadata_status=incomplete_metadata`, so this is a technical-fix confirmation,
not semantic or overall acceptance. Snapshot/source integrity remained true;
FakeRedis only, real Redis/load remain unverified.

## Historical 078 Technical Repair Evidence

[078](../changes/078-h2-json-string-control-normalization.md) implements only raw
string-control escaping for complete public H2 agent_action.v1 JSON. Strict
action/argument/permission checks, immutable anchor correction, raw artifacts,
budgets and default1.0 remain unchanged. The allowlisted positive count is stored
with the original output_ref in the atomic model.call_finished event and in a
successful ModelResponse. No provider text/arbitrary metadata is copied.

Eight synthetic wire cases first reproduce the fault. Two later counterexamples
identify an unintended widening of022 DSML compatibility; the adapter now limits
normalization to brace-starting JSON object candidates and preserves022 behavior.
Final combined H2/state1059 pass/7 skipped (435.02s),89 net new tests; default
finance1638 pass/30 skipped (189.49s,52 modules),configuration10/10,scoped full
Ruff/diff and257 local-link targets pass. Intermediate fixture errors and red
logs remain. Earlier separate H2/state results are retained, not called final.

Read-only R22 replay escapes31 controls and preserves2677 content characters,
every decoded field and the raw artifact hash. The public action parses but13
anchors still fail ResearchResult validation;073 repair eligibility is not a
completed repair. Zero new paid requests, no live stability or semantic claim.

## Latest R22 Live Evidence

[R22](harness-v2-live-acceptance-r22-20261020.md) completes one new fixed N01
depth-two DeepSeek/FakeRedis run:31 physical requests (Root15/child10/grandchild6),
358.62 seconds,2962-character answer; all three Tasks succeed.25 structural and
8 assignment checks pass.074's private-source/provenance boundary and076's
whole-action patch Schema are actually exercised;12 anchors repaired,0 dropped,
with prose/candidate/refs/quotes unchanged.Only the grandchild reads the original,
once; both ancestors fully consume direct results before submission.

The grandchild first fails, then resumes the same Task from its saved checkpoint;
no new quota, deadline or source read. One unknown Root transport charge stays
charged;048 correction retransmission is not_triggered.The pre-report integrity
record preserves441 source/design/test entries,898 snapshot files and5 helpers.
This is not repeated-success, real Redis, load or financial semantic acceptance:
conditional explanations are overstated, a sizing condition is omitted, and Root
misattributes the grandchild failure to the depth-one Attempt history.Review is
absent and financial delivery metadata incomplete.

[078](../changes/078-h2-json-string-control-normalization.md) separately addresses
the observed raw-string-control syntax fault. Its new offline replay is not a
rerun or rewrite of R22; all other semantic/design/integration gaps remain open.

## Historical 076 Offline Evidence

[076](../changes/076-h2-anchor-correction-action-schema.md) now supplies and
validates only the allowed complete final/output patch action during immutable
anchor correction. Its example is itself a valid complete action. Ordinary
Root/non-anchor correction and strict public parsing remain unchanged; no bare
patch wrapping, new retry, model switch or budget change. The action Schema
field is877 characters versus the generic7034 with the same JSON serialization;
this is not a total-context or model-success-rate result.

Seven new tests include three first reproduced failures. Focused50 pass (17.84s);
full H2 **970 passed/7 skipped** (401.18s), default finance **1638/30 skipped**
(128.20s,52 modules), configuration10/10, scoped full Ruff and diff checks pass.
Logs:runtime-records/framework-research-20261020/anchor-schema-*.xml. No paid
revalidation in076 itself; the subsequent R22 evidence is recorded above.
Remaining semantic/design/integration/environment issues stay explicitly open
in [current attribution](harness-v2-root-cause-review-20261020.md), section8.

## Historical R21 And Technical Repair Evidence

[073](../changes/073-h2-framework-informed-anchor-repair.md) freezes full research
prose during anchor repair; [074](../changes/074-h2-delegated-reference-views.md)
separates provenance-only source IDs from usable parent result handles without
expanding permissions. Final offline H2:963 passed/7 skipped (392.38s), default
finance:1638 passed/30 skipped (122.14s), configuration10/10 and scoped Ruff/diff.
The depth-two offline wrapper/ResultRef paths each preserve one original reader.

[R21](harness-v2-live-acceptance-r21-20261020.md) is one new DeepSeek/FakeRedis
depth-two run:25 physical requests (13/8/4),306.64s,4401-character Root delivery;
both research descendants are partial. The deepest task alone reads lines1-63,
and each ancestor consumes its direct result in full. Four ledger contract_error
responses comprise three Root XML/DSML responses and one bare grandchild patch;
two original anchor rejections are domain-level checks, not additional ledger
errors. Both patch attempts preserve all prose/refs/quotes: grandchild drops8
bindings after missing its action envelope; depth-one repairs all6 but cannot
restore unaccepted upstream source bindings. The private-source counterexample
and transport retransmission are not_triggered live, not passed branches.

Audit-v2 corrects a retained audit.json false budget failure:12 child requests
transfer one credit each, not prepaid60;13 Root+12 child requests charge25,
remaining575.23 structural checks pass,3 of8 assignment diagnostics fail.
Original run-end freeze evidence predates intentional report/audit edits.
[076](../changes/076-h2-anchor-correction-action-schema.md) next narrows the
patch correction's whole-action Schema and example; it does not relax parsing.
No full semantic, real Redis, production or commercial acceptance is established.

## Latest R20 Delegated-Read Revalidation

[R20](harness-v2-live-acceptance-r20-20261020.md) completes one full delegated
six-article path: Root12 + child13 =25 physical requests in453.79 seconds.
The child reads all six originals including tails, returns accepted source-bound
analysis, and Root consumes it fully without original-body reads. Structural20/20,
assignment6/6 and source-lineage4/4 checks pass after a separately retained audit
refinement for domain-vs-transport validation. Correction retransmission stays
within the same model/Attempt/budget and is charged. The new terminal-failure
handoff does not trigger live;070 offline coverage passes. Content still has a
false contradiction and date/premise over-attribution; review/coverage metadata
is absent and real Redis unverified. This is not complete business acceptance.

## Post-R19 Terminal Diagnostic Repair

[070](../changes/070-h2-terminal-failure-diagnostics.md) repairs the existing
failure-handoff contract: parent status/wait/model input now retains the current
Attempt's sanitized stage/errors, rather than only contract_error. Failed output
remains failed. No new correction, budget, migration or default-path behavior.
Offline H2916/7 skipped (430.50s), default1638/30 (192.21s), configuration10/10
and scoped Ruff/diff passed. Fifteen new tests cover persistence/recreation,
cursor-independent wait, masking/bounds, malformed diagnostics, lease/access,
resumption and retry eligibility; integrated child/model-input checks pass too.
Separate071/R20 live protocol is frozen for one B01 execution, not a R19 rewrite.

## Latest R19 Triggered Child Budget Diagnostic

[R19](harness-v2-live-acceptance-r19-20261020.md) separately triggers one child
after R18's Root-only run. Persisted personal caps are Root120/child60; Root9 +
child2 =11 requests over 131.76 seconds, with no prepaid60 or cross-personal debit.
Twenty structural checks and 424 source/test / 898 snapshot / helper hashes pass.
The child fails before article tools: DSML first response, then a final whose
evidence-link text is absent verbatim from its content during the sole correction.
The current approved strict contract ends the Task;58 personal calls remain.
Root delivers an honest failure explanation, not the requested six-article study.
Six complete reads, accepted child return and Root full consumption fail.
Budget policy is active; over-eight-call successful live child completion remains
not_triggered. No Runtime or recovery-policy change, fallback or follow-on rerun.

## Latest R18 Independent-Budget Evidence

[R18](harness-v2-live-acceptance-r18-20261020.md) runs Q08 once under 067's
Root120/child60/Run600 policy. It delivers in 215.22 seconds using 18 Root-owned
requests; no child or reviewer is selected. Eighteen read-only structural checks
pass and 422 frozen entries / 898 snapshot files / private runner match before
edits. Three envelope failures recover through one isolated correction each.
Actual reads cover six complete and two partial articles, disclosed in the answer.
Review is absent and model-owned coverage stale. Live child-cap verification is
not_triggered, not a pass; R17's failed reviewer remains historical evidence.
Full semantic, recursive-source, real Redis and production acceptance remain open.

## Latest R17 Post-Isolation Evidence

[R17](harness-v2-live-acceptance-r17-20261020.md) completes the fixed 066 batch:
Q08/Q15/N01 deliver with 39/16/20 physical DeepSeek requests (75 total),
360.81/140.67/301.66 seconds. All twelve structural checks pass per case, with
417 frozen inventory entries, 898 snapshot files and five helpers unchanged.
Nine transmitted corrections and one prepared-but-budget-denied request verify
that rejected text is not reinjected. Q08 has 40 public input captures but only
39 physical calls; capture is not proof of transmission. No live fence or
correction-transport retransmission is triggered.

Q08's reviewer consumes its requested eight calls and fails without top-up.
N01 actually executes depths zero/one/two, deepest-only full source reading,
and both complete return reads before submission. The depth-one result is
partial because it cites a private original-source artifact, exposed as known
but not readable by that parent. Original-source lineage therefore does not
reach Root as accepted evidence. Root discloses the failure and keeps unread/
undecided rather than fabricating coverage. Usable handles versus provenance
IDs need a separate precise refinement; do not widen permissions automatically.
All Root reviews are absent and semantic overstatement remains. Three deliveries
are not full-chain acceptance, repeated reliability or commercial readiness.

## Approved 065 Recovery Refinements

The user explicitly approved correction-input isolation and single outer JSON
fence normalization. [065](../changes/065-h2-correction-input-isolation-and-json-fence.md)
records design before code, shared Root/child implementation and actual final
offline results: H2 **888 passed / 7 skipped**, default finance **1638 / 30
skipped**, configuration 10/10 and full scoped Ruff/diff pass. There are 63 net
new cases; affected modules pass 447 tests. Full rejected text remains in audit,
not reinjected as correction context; strict schemas/permissions and physical
call limits remain unchanged. Default 1.0 is untouched.

These approvals supersede the earlier pending-choice notes below, not the old
batch results. [066](../changes/066-h2-isolation-live-protocol.md) defines a new
once-only Q08/Q15/N01 DeepSeek/FakeRedis revalidation, not an automatic old-batch
rerun. Its results must be recorded separately. Reserve-backed financial closing,
real Redis, missing business adapters and semantic quality remain open.

## Latest R16 Depth-Two Attempt

[R16](harness-v2-live-acceptance-r16-20261020.md) executes the fixed 064 N01
diagnostic once: 2 charged requests, 6.55 seconds, Root failed and no final
answer. The original includes an unexecuted, fabricated XML tool result;
the sole correction is malformed JSON and repeats an unsupported parameter
from that fake description. No tool or child executes. Runtime correctly
rejects the outputs and retains charges; depth-two execution/lineage remain
not_triggered. The audit's no-depth-three boolean is vacuous, not a live pass.
Twelve structural checks pass, and 414 frozen inventory entries, 898 snapshot
files and three private helpers match before documentation edits.

Three focused offline recursion/lineage cases pass (16.23 seconds), configuration
10/10 and zero-call preflight/Ruff pass. Runtime is unchanged and broader
post-062 results are not rerun. This adds no confirmed baseline implementation
defect; the two recovery-input/parser policy choices were submitted to the user,
not silently adopted. No extra N01 run, model switch or expanded grant occurs.
R15's depth-one evidence remains valid; real depth-two return is still unproved.

## Latest R15 Targeted Source Return Evidence

[R15](harness-v2-live-acceptance-r15-20261020.md) completed the fixed 063
L01/L02 diagnostics once each: two Root deliveries, three of three successful
analyst returns, 13/33 physical requests and 208.72/600.11 seconds. Root reads
no original bodies and fully consumes all three child analyses before delivery.
Author/date/hash/ranges/original reader and final used state match the actual
child article.read observations. L02's 35 other discovered articles remain
unread/undecided. Twelve structural checks pass per case; all six/seven
applicable source-return diagnostics pass for L01/L02 respectively.

All 412 frozen inventory entries, 898 snapshot files and the private execution
wrapper match before post-batch edits. The batch uses 46 charged calls, including
one unknown transport outcome; the correction-transport retry is not_triggered.
The audit makes zero calls. This supplies live evidence for 062 at depth one,
not a new broad regression, repeated success rate or real Redis proof. L01/L02
are controlled diagnostic variants, not unchanged S01/S02 comparisons.

Content still needs work: unsupported joint-necessity and total-dependency
claims, overstated criticisms and commonality across different dates remain.
Both structured reviews are absent; L02 discloses partial, weak gold coverage.
The targeted handoff passes, not full semantic acceptance. R14's business
results and pending parser/correction-input/closing choices stay unchanged.
Runtime code is unchanged; post-062 H2 825/7 skipped and default 1638/30 skipped
remain the latest broad offline runs, not rerun results of R15.

## Latest R14 Business Evidence

[R14](harness-v2-live-acceptance-r14-20261020.md) completed the fixed 059 batch:
30 registered, 20 executed, 17 delivered, 403 charged physical DeepSeek calls.
Q08/Q09/Q15 failed the public action contract; nine cases lack adapters and Q29
needs isolation. All 20 pass the 13 read-only structural checks. All 392 source
inventory entries and 898 snapshot files match at completion; the source scope
includes the live-driver test, not all offline tests. Two of twelve research
children succeed and their full results are read by Root. Q28 actually recovers
the single allowed correction transmission retry; this is one live occurrence,
not broad network reliability. Legacy child-request initialization is not used.

Semantic acceptance is not complete: Q27 analyzes fabricated truncated content
with zero body reads, Q26 describes a search that was never executed, and several
multi-author answers overstate conditions/contradictions or misattribute figures.
Nineteen states have no review and Q14 remains pending. Delivery falls from
R13's 18/20 to 17/20, so no overall stability improvement is claimed.

[060](../changes/060-h2-first-successful-read-version-warning.md) implements the
narrow post-batch repair for false source-version warnings after failed initial
read parameters. It does not change parser/correction/research policy or rewrite
the R14 evidence. Stronger correction-source isolation and outer-fence handling
remain separate user decisions, as does reserve-backed financial closing mode.

Post-060 actual offline verification: fifteen selected checks pass, the two
complete related modules pass **79 cases** (80.18 seconds), and the broad H2
collection passes **785 / 7 skipped** (284.81 seconds). Default finance/shared
runtime passes **1638 / 30 skipped** (121.77 seconds, 52 modules), configuration
10/10, scoped full Ruff and diff checks pass. Eight relevant Markdown files have
162 resolving local file links, without heading/semantic certification. Nine
net added tests include six red-before/green-after first-read cases and three
failed-after-prior-reading guards. No extra paid rerun, live-data mutation,
Docker action or Git commit/push occurs in this deterministic repair.

## Post-R14 Dispatch Boundary Repair

[061](../changes/061-h2-outbox-mismatch-quarantine.md) extends the existing
quarantine-before-ACK boundary to well-shaped notifications that fail persisted
Outbox identity checks. Previously that correct Repository rejection escaped
the Worker/DispatchService. The mismatch now leaves the real Task untouched,
saves its redacted rejection, and acknowledges only the rejected Stream entry;
the genuine task can still execute. Storage/SQLite/noncontract errors are not
converted into malformed-message success. Receive and fresh-Worker recovery
are both covered. Wire validation also rejects nondecimal generation digits and
checks the unchanged 4 KiB limit before integer conversion. Twenty added tests
include sixteen checks reproduced before their corresponding repairs and four
already-passing negative/compatibility guards. The complete focused module
passes 50 cases. Final broad verification is H2 **805 passed / 7 skipped**
(330.38 seconds), default finance **1638 passed / 30 skipped** (186.89 seconds,
52 modules), configuration 10/10 and scoped full Ruff/diff passed. Seven relevant
Markdown files have 188 resolving local file links, not heading/semantic
certification. The two broad collections overlap in coverage and execution.
This makes no paid calls and does not resolve
R14's semantic failures, pending parser/correction policies or real Redis.

## Post-R14 Delegated Source Return Repair

[062](../changes/062-h2-delegated-source-lineage.md) repairs a normal research
handoff defect: Root could cite an accepted child's analysis while its own
article state remained unread/undecided. Two initial corpus-service cases
reproduced this before the repair. Accepted source bindings now carry a compact
Runtime-generated finance_sources section, preserving actual author/date/hash,
observed ranges and original reader through both research depths. Parent final
references, not child use/coverage/review judgments, determine actual use.
Long analysis bodies remain on demand; returning source metadata does not
manufacture result.full_viewed, an extra source read or a new input grant.

Final offline results: H2 **825 passed / 7 skipped** (307.68 seconds); default
finance/shared runtime **1638 passed / 30 skipped** (127.42 seconds, 52 modules);
configuration **10/10**; full scoped Ruff and diff checks passed. Twenty net new
tests plus a strengthened section-grant guard cover the boundary. Two final
multi-author/date/version assertions were rerun separately after their test-only
strengthening (2 passed, 0.82 seconds); runtime code was unchanged. These
collections overlap and are not independent business runs. No extra paid call,
production switch, live-data change, Docker action or Git commit/push occurred.
R14's business results and all pending strategy choices remain unchanged.

## Post-R13 Implementation Repairs

[058](../changes/058-h2-legacy-envelope-input-grants.md) repairs a confirmed
input-boundary defect in compatibility child initialization. The trusted
TaskBrief input list is now authoritative; the embedded legacy request may
describe the assignment but cannot replace, reorder, remove or add input grants.
The control envelope itself is omitted from source materials. Twelve new
initialization cases and two SQLite/FakeRedis/mock-provider continuations (fourteen
new cases total) cover
separate parent context, extra assigned articles, foreign references, follow-ups,
fresh-Service recovery and compact checkpoints. Focused related modules passed
**218 cases** (78.75 seconds). Final post-058 H2/state/compatibility/driver
regression: **776 passed / 7 skipped** (256.18 seconds); configuration 10/10;
scoped Ruff and diff checks passed. No paid request occurred. Default finance
regression after this source change is recorded separately below; this repair
does not alter R13 semantic findings, real Redis evidence or pending design
choices.

[059](../changes/059-h2-business-r14-after-input-grant-repair.md) freezes the
post-058 R14 business regression, now completed above. It is a bounded DeepSeek/FakeRedis run over
the same 30-query catalog and R13 snapshot, with 120 physical requests per
executable tree and no fallback or repetition. Results are recorded separately
in its separate report; this protocol does not alter the financial methodology.

The default finance/shared-runtime regression after the 058 source change also
passed **1631 cases / 30 skipped** (120.94 seconds, 52 modules), with the same
existing Starlette/httpx deprecation warning. The focused and broad counts
overlap and are not additive.

[057](../changes/057-h2-text-parent-context-handoff.md) repairs valid UTF-8
parent notes being marked unavailable and breaking child article-scope
collection. Text is preserved as context, not parsed into new article grants
or accepted as original source evidence. Twenty-three new cases include actual
SQLite/FakeRedis/mock-provider child input, refusal of an ungranted article,
assigned-source reading and continuation through a fresh Service. The complete
three-module focused run passed **246 cases** (70.15 seconds), overlapping the
broad collections. Final post-057 results: H2/state/compatibility/driver
**762 passed / 7 skipped** (274.08 seconds); default finance/shared-runtime
**1631 passed / 30 skipped** (123.50 seconds, 52 modules); configuration 10/10;
full Ruff on four changed Python files and diff whitespace checks passed.
Eight current Markdown documents have 186 resolving local file links; this
does not certify heading anchors or whole-design semantics.
No paid request occurred. Model actions, Checkpoints and source bindings remain
strict; R13 semantic failures, real Redis and pending correction-input/closing
policies are not resolved by this deterministic context repair.

[056](../changes/056-h2-exported-usage-redaction.md) repairs export redaction
that hid valid Token usage counts while SQLite retained them. Only exact known
usage names with nonnegative integer values are exempt; credential-shaped,
boolean, negative, fractional or nested values and all other secret keys remain
masked. Seventeen new cases plus two strengthened physical-call tests cover
recursive/file redaction and actual SQLite/mock-provider export comparisons.
Both focused modules passed **131 cases** (40.83 seconds), overlapping the
broad collections. Final post-056 results: H2/state/compatibility/driver
**739 passed / 7 skipped** (236.75 seconds); default finance/shared-runtime
**1631 passed / 30 skipped** (122.17 seconds, 52 modules); configuration 10/10;
full Ruff on three changed Python files passed. No paid request occurred. Real
Redis skips, existing warning, R13 semantic failures and pending design choices
remain; this is faithful usage export, not a monetary-cost or quality guarantee.
After final navigation updates, seven current Markdown documents have 176
resolving local file links; heading anchors/whole-design semantics are not
certified by that check. Git diff whitespace checks passed.

[055](../changes/055-h2-runtime-record-path-boundaries.md) repairs linked-path
escapes in quarantine append and orphan scan/removal. Directory links/junctions
are checked before traversal and again before deletion; multiply-linked
quarantine files are rejected before appending. An unsafe quarantine leaves the
same FakeRedis message pending until durable safe quarantine permits ACK.
All thirteen focused cases pass, including ten new tests without new skips.
Actual post-055 results: H2/state/compatibility/driver **722 passed / 7 skipped**
(242.12 seconds); default finance/shared-runtime **1631 passed / 30 skipped**
(124.45 seconds, 52 modules); configuration 10/10; full Ruff on two changed
Python files and diff checks passed. No paid call, live-data mutation or Docker
action occurred. This does not claim a hostile-filesystem race sandbox, repair
R13 semantic findings or settle pending research/budget decisions.

[054](../changes/054-h2-trusted-task-identity-context.md) repairs the omission
of trusted TaskBrief identity from Root/research model inputs. `task_identity`
is whitelisted and refreshed from the current brief on each invocation; restored
Attempt identity cannot override it. The eleven new focused cases pass,
including real SQLite/FakeRedis/mock-provider continuation through a fresh
Service instance. Actual post-054 regressions: H2/state/compatibility/driver
**712 passed / 7 skipped** (254.55 seconds); default finance/shared-runtime
**1631 passed / 30 skipped** (126.10 seconds, 52 modules); configuration 10/10;
full Ruff on the three changed Python files and diff checks passed. The focused
eleven-case check overlaps these collections. No paid request occurred. Existing
real-Redis skips and the Starlette/httpx warning remain.

The same audit found a separate design gap: normal finance decisions never
select reserve-backed closing mode. The budget layer protects the reserve,
but the finance transition/allowed closing actions are not defined. Existing
normal-call stop behavior stays unchanged pending that design. Do not treat
this as a demonstrated cause of Q16/Q18 or spend reserves on new research.

[053](../changes/053-h2-read-index-and-result-envelope-retention.md) removes
two further confirmed retention defects: the last-200 cap on disjoint read
ranges and the first-20 caps on Root model-provided key_points/gaps/warnings.
Normalized current-version ranges survive restored state and final gap filling;
the complete semantic envelope is returned by actual result.get(summary).
Bounded diagnostics, source isolation, type/reference checks, budgets and the
compact-state/input limits are unchanged. These are deterministic repairs,
not new research or format-correction policies.

Actual post-053 results: H2/state/compatibility/driver **701 passed / 7 skipped**
(314.78 seconds); default finance/shared-runtime **1631 passed / 30 skipped**
(187.74 seconds, 52 modules); configuration/separation 10/10; full Ruff on three
changed Python files and diff checks passed. Focused 65-case and 39-case checks
overlap these collections. No paid call was made. Real Redis, missing business
adapters, correction-input policy and R13 semantic findings remain unresolved.

[052](../changes/052-h2-evidence-ledger-retention.md) subsequently removes an
undocumented last-200 eviction from the shared state-update merge. Original
read bindings and model-added supporting/contrary bindings survive metadata-only
updates and fresh-service checkpoint recovery. Idempotency, foreign-reference
rejection and the explicit 128 KiB compact-state guard remain unchanged. No
unbounded-inline-state promise or new financial policy is added.

Actual post-052 results: H2/state/compatibility/driver **690 passed / 7 skipped**
(310.83 seconds); default finance/shared-runtime **1621 passed / 30 skipped**
(184.81 seconds, 52 modules); configuration/separation 10/10; full Ruff on the
three changed Python files and diff checks passed. Focused 54-case and import-only
29-case rechecks overlap these collections. No new paid call occurred; no
evidence establishes this 200-entry boundary caused R13's errors. Correction-input
provenance isolation is still a pending design choice, not part of 052.

[051](../changes/051-h2-version-bound-article-read-progress.md) repairs the
existing source-version boundary: changing an article hash resets only active
read coverage before applying the new observed range. Old materials, immutable
versions and evidence references remain; no old complete status certifies a
new version. Temporary-corpus reindex and fresh-service checkpoint resume tests
exercise both old partial and old complete states. Root/research children share
the repaired merge; no new financial policy, gate, budget or fallback was added.

Actual post-repair offline results: H2/state/compatibility/driver **682 passed /
7 skipped** (310.78 seconds); default finance/shared-runtime **1614 passed /
30 skipped** (187.25 seconds, 52 modules); configuration/separation 10/10;
scoped Ruff and diff checks passed. Focused 46-case results overlap these
collections, not additional independent acceptance. Real Redis skips and the
existing Starlette/httpx warning remain. No new paid call was made. R13's frozen
corpus did not change during its run; this repair is not its established causal
explanation and does not resolve its semantic/strict-JSON failures below.

## Latest: R13

The [R13 full business catalog run](harness-v2-live-acceptance-r13-20261020.md)
has finished under [050](../changes/050-h2-business-acceptance-records.md).
All 30 IDs are retained: 20 executed, 9 blocked on missing business adapters,
Q29 requiring isolation. DeepSeek/FakeRedis used 331 physical calls, delivered
18 answers, and ended Q16/Q18 on public-action contract errors. All 20 read-only
accounting/artifact checks and 382 source/898 snapshot file comparisons passed.
The one-time correction transport retransmit did not trigger. The single-Worker
driver is not a concurrent-model throughput or real-Redis test.

Q12 is a severe semantic failure: zero executed tools or trusted article
evidence, but a final answer inventing article details after correction of a
rejected simulated conversation. Forged observations never entered trusted
state; existing diagnostic-only repair guidance did not prevent the model from
using them in its final. Keep this failure separate from syntax recovery.
Other retained findings include miscounts (Q01), probability interpretation
(Q07/Q14), time/condition mismatch (Q11/Q17), and author attribution/conditional
strengthening (Q15). All delivered cases still have incomplete metadata and
no saved structured review. Neither 18/20 delivery nor the structural checks
constitutes full content/evidence/workflow acceptance. Earlier results below
remain historical evidence; do not read their former "pending full suite"
status as if R13 had not run.

Authority: H2-DISPATCH-2026-10-18-v1, H2-CONTRACTS-2026-10-18-v1,
finance.state_update.v1 and approved changes 030-049 plus existing-contract
implementation repairs in 035/037-041 and the separately approved child correction
after the fixed 036/R7 batch; acceptance-only call-cap configuration is recorded
in [042](../changes/042-h2-acceptance-call-ceiling.md). The bounded [043/R8](harness-v2-live-acceptance-r8-20261019.md)
batch has completed; [044](../changes/044-h2-action-validation-feedback.md) repairs
its demonstrated guidance/diagnostic gaps without new retry policy.
[045](../changes/045-h2-child-context-contract-parity.md) repairs child guidance
and wait-status propagation parity. The user separately authorized the two-case
[046/R9](../changes/046-h2-post-feedback-live-protocol.md) protocol after the
offline prerequisites. R9 finished with one of two targeted scheduling/handoff
chains accepted. The user then authorized the fixed [049/R10](../changes/049-h2-correction-recovery-live-protocol.md)
batch after the narrow correction-transport repair; R10 completed with two Root
deliveries and partial multi-author evidence. This report adds current evidence;
it does not rewrite earlier frozen batches.

R11 subsequently reran S02 once after the same bounded recovery changes. Root
and both analyst Tasks succeeded, both child results were fully read before
delivery, and the Root delivered after 27 physical DeepSeek calls in 401.2
seconds. Four Root contract errors were recovered by the permitted one-time
semantic correction. R11 is a targeted improvement record, not a replacement
for R10 or evidence of stable raw model compliance.

R12 ran Q14 once on DeepSeek after R11. The single Root Task delivered a
2,977-character answer in 141.19 seconds using 17 physical calls. Two initial
contract errors were recovered by the permitted single semantic correction; no
correction transport retransmit was triggered. The answer was materially useful
and source-scope aware, while `semantic_acceptance=manual_review_required` and
`incomplete_metadata` remain. R12 validates the durable single-Root path, not
child delegation, real Redis, or live network-disconnect recovery. See the
[R12 report](harness-v2-live-acceptance-r12-20261020.md).

## Current Scope

The explicit FinanceHarnessV2Service.for_finance_corpus path shares one durable
action loop between Root and depth-1/2 research roles. Article tools execute in
the current Task; explicit spawn returns a receipt, explicit wait releases the
Worker, and each completed action checkpoints and queues a new Attempt. Legacy
delegate remains an implicit-wait compatibility action.

New H2 research uses model-selected explicit requested_budget. Root and children
see the current trusted TaskBrief budget/deadline and supported research role
versions. Compatibility delegate's original role budgets are disclosed before
selection; the same definition drives execution and on-demand documentation.
Neither rejection nor transport failure silently changes a budget or model.

The opt-in path is not the default 1.0 public entry or a deployed production
service. Article tools do not imply full web/market/Wiki/holdings or Skill parity.
Those adapters cannot simply wrap legacy functions that hide model calls or
recovery state outside the H2 ledger.

## Design-To-Evidence Review

Rows describe examined boundaries, not proof against every future failure.
Fixture models and FakeRedis are different from live models and real Redis.

| Contract | Current evidence | Still unproved |
| --- | --- | --- |
| C01 idempotency | Same key replays exact receipt; denied allocation atomic; real kill before/after spawn commit | Cross-host deployment |
| C02 permissions | Run/ancestor/sibling fences; material and result-section grants; child article scope | Production identity integration |
| C03 lease ownership | Two real processes, 20 synchronized claim rounds; one accepted execution each; late lease fenced | Sustained multi-host load |
| C04 Outbox | Real kill after DB acceptance and after controlled notification before DB published; durable retry; FakeRedis duplicate consumption | Real Redis network/PEL/server-loss tests |
| C05 commit-before-ACK | Real Worker kill after final commit; fresh Worker only ACKs original delivery | Real Redis ACK/network boundary |
| C06 wait/status | Modes, timeout, cursor pagination, summary envelopes and truthful child failure observations; 045 preserves existing budget/deadline/dispatch/attempt fields into finance context, including failed children with unused grants | Real network interruption; correct live-model interpretation of these facts |
| C07 recursion/progress | Shared actions at depth 0/1/2; denied depth 3; R10/R11 live wait/return; R15 L02 spawns two children before wait, both succeed and Root consumes both before delivery | Repeated stable all-child multi-author synthesis and live depth-2 recursion |
| C08 mailbox | Monotonic sequence, replay, terminal follow-up; real-process continuation retains consumed cursor; old-Task resume rejects open follow-ups; send targets the sole open resumed Task, with preparation races fenced | Long-running interactive use |
| C09 cancellation | Subtree/result races; cancellation before real process termination stays cancelled | Remote provider cancellation guarantee |
| C10 checkpoint | Real kill before Continue commit rolls back successor; after commit retains one successor; missing/corrupt state rejected | Host power loss / storage failure campaigns |
| C11 model policy | Explicit snapshots/CAS/revocation; child preparation racing a parent policy update rejects atomically; reservation-to-start revocation is rechecked transactionally, forbidden remains visible and confirmed unsent calls refund; no automatic fallback | Quality/compatibility for every selectable model |
| C12 partial delivery | Full candidate retained on optional metadata/source-binding failures; review separate from delivery | Stable semantic review by live model |
| C13 result/context | Authorized pagination and contiguous reconstruction across Attempts of the same reader Task; no mixing user exports/follow-ups/views; short-body capacity callback connected, omitted/long bodies expand only by model-selected result.get; R15 verifies all three child full reads before final delivery without Root body rereads; no fixed last-twelve eviction | Large real research campaigns and repeated stable all-child synthesis |
| C14 budgets | Exact allocation/explicit denial; legacy disclosure matches allocation; resume preserves parent closing funds and restores actual Task account; confirmed-unsent reservations settle under original identity/token even after fencing, with terminated-child funds returned; real crash preserves synthetic started-call charge as unknown, no refund | Exact monetary cost limit (not promised) |
| C15 public JSON | Strict inputs/on-demand Schema; unfinished actions not executed; R14 retains Q08/Q09/Q15 contract failures; Q28 shows one same-Attempt correction transport recovery with three charged requests; 061 audits and ACKs well-shaped Outbox mismatches without execution or service interruption | Stable model compliance, commercial network reliability and all-child two-author completion; saved-wire equality remains unproved |
| C16 finance compatibility | Fixture author/date/source/evidence/body preserved; state updates and child research loop tested; 062 preserves delegated source lineage across both depths offline; R15 verifies depth-one source identity/read/use return live without ancestor rereads; R6 delivered three real answers | Full review equivalence; full business Query set; live depth-two lineage and semantic quality |
| C17 storage/logging | Atomic artifacts; DB failure prevents ACK; corrupt/missing recovery files fail closed with correct cause; scoped export/GC tests | Disk exhaustion, power failure, production retention |
| C18 dependencies | Cycles refused; failed/partial dependencies blocked and visible; parent chooses next action | Sustained production failure campaigns |

## Repairs In This Continuation

1. [033](../changes/033-h2-explicit-delegation-budget.md): prefer explicit
   requested_budget for new research; publish exact legacy values before selection.
   Forward existing TaskBrief budget/deadline, previously omitted from model input.
2. [034](../changes/034-h2-process-fault-validation.md): reproduced Worker
   misclassifying corrupt/unavailable recovery material as model contract_error.
   Preserve checkpoint_invalid/artifact_unavailable through the existing failure
   path. Invalid input never reached the handler; this repairs attribution, not
   integrity validation or retry policy.
3. [035](../changes/035-h2-resume-accounting-guards.md): reproduced and repaired
   resume invading the parent's final reserve, same-Thread open-Task collisions,
   closed-Thread resume and stale Thread/call budget references after follow-up.
   Messages now target the sole open Task even when an older Task was resumed,
   and recheck the target before acceptance.
   Known HTTP state/budget errors no longer fall through to 500; child preparation
   refuses an outdated parent model-policy snapshot. Rejections leave no accepted
   Task/Attempt/budget/command residue. No DDL or budget-default changes.
4. [037](../changes/037-h2-child-action-envelope-and-budget-errors.md): R7 exposed
   unclear outer/inner child action contracts and an exhausted child budget
   incorrectly classified as internal_error. Repair guidance/classification;
   the user then separately approved one same-model child structure/anchor
   correction within its exact remaining grant. Preserve both calls and refuse
   a third; failed correction transport cannot replay the original decision.
5. [038](../changes/038-h2-model-revocation-boundary.md): reproduced a revoked
   model still sending after reservation, and known permission refusals being
   persisted as internal_error. Recheck current authorization in call_started,
   preserve forbidden, refund only confirmed unsent reservations. An authorized
   in-flight call stays charged; default selection alone does not change an
   allowed original Attempt snapshot.
6. [039](../changes/039-h2-result-read-coverage.md): continuous result pages in
   different action Attempts failed to produce a complete-read audit event.
   Aggregate by actual reader Task/kind plus result/view/section, retain each
   Attempt association and deduplicate completion. Legacy Agent records resolve
   through their actual Attempt; user exports and follow-ups never stand in for
   the current Agent's reading.
7. [040](../changes/040-h2-on-demand-result-expansion.md): the finance bridge
   eagerly fetched omitted child results, undoing on-demand delivery. Removing
   it exposed a missing Worker capacity callback for short results. Connect the
   actual model-capacity check, retain eligible short injection and summary/ref
   notifications, and expand omitted/long bodies only on explicit model actions.
8. [041](../changes/041-h2-confirmed-unsent-settlement.md): cancellation or lease
   loss before send prevented refund of a confirmed-unsent reservation. Permit
   original identity/token audit settlement without restoring execution, credit
   only the original call account, and return terminal-child balances through
   the existing parent chain. Started/unknown-after-start calls stay charged;
   reconciliation alone never refunds a missing consumer's call.

## Regression

| Collection | Result |
| --- | --- |
| Latest: all dispatch + finance_state_update + harness_v2_compatibility + harness_v2_live_acceptance, after 045 | **609 passed / 7 skipped**, 237.74 seconds; twenty-two net additions; separate R9 evidence below |
| Latest: all dispatch + finance_state_update + harness_v2_compatibility + harness_v2_live_acceptance, after post-R8 repair 044 | **587 passed / 7 skipped**, 210.12 seconds; nine net additions, no paid revalidation |
| Same broad collection, after acceptance-driver 042 | **578 passed / 7 skipped**, 222.69 seconds; 20 net additions after 041, no finance runtime code change |
| Acceptance-driver checks, after 042 | **37 passed**, 20.29 seconds; 20 net additions including explicit cap validation and conserved Root/child/repair/retry accounting |
| Same broad H2 collection, after 041 | **558 passed / 7 skipped**, 226.83 seconds; 22 net added cases after 040 |
| Execution/foundation modules, after 041 | **256 passed / 7 skipped**, 99.29 seconds; 22 new cases and one strengthened cancellation case; includes 20 synchronized start/refund races |
| Same broad H2 collection, after 038-040 | **536 passed / 7 skipped**, 206.81 seconds; 31 net added cases after 037 |
| Same collection, after 037 | **505 passed / 7 skipped**, 242.08 seconds; 20 net added cases after 035 |
| All tests/test_dispatch_*.py + finance_state_update + harness_v2_compatibility + harness_v2_live_acceptance, after 035 | **485 passed / 7 skipped**, 178.74 seconds |
| Within that collection: real-process fault cases | **15 passed**, including 20 synchronized two-process claim rounds |
| Within that collection: 035 added cases | **42 passed**: seven resume, 24 error classifications, two real HTTP requests, two policy races and seven message-routing cases |
| Finance-agent/research/continuation focused run | **116 passed**, 40.45 seconds; overlaps the broad H2 collection |
| Latest scripts/test_finance_baseline.py, 52 finance/shared-runtime modules, after 045 | **1606 passed / 30 skipped**, 121.69 seconds; new post-045 execution, no paid requests |
| Same default collection, after 044 | **1606 passed / 30 skipped**, 115.57 seconds |
| Same default collection, after 041 | **1606 passed / 30 skipped**, 126.63 seconds |
| Same default collection, after 038-040 | **1606 passed / 30 skipped**, 123.39 seconds |
| Same default collection, after 037 | **1606 passed / 30 skipped**, 193.22 seconds |
| Context/continuation/Service/compatibility focused regression, after 040 | **35 passed**, 42.48 seconds; overlaps the broad collections |
| scripts/check_finance_baseline.py | **10/10**, zero model calls |
| Ruff: dispatch, dispatch tests, state-update, compatibility and live-driver tests | Passed |
| git diff --check | Passed; existing LF/CRLF notices are not whitespace failures |
| Eleven relevant Markdown documents, after 042 navigation | 145 local file links resolve; heading anchors/whole-design semantics not certified by this check |
| Fourteen relevant Markdown documents, after 043/044/R8 navigation | 166 local file links resolve; no claim of heading or whole-design semantic certification |

Both broad collections retain the existing Starlette/httpx deprecation warning.
The seven H2 skips require real Redis. These collections overlap; do not sum
them as independent cases or business Queries. These offline checks made no paid
model calls; the separately authorized R7/R8 batches are reported below. No Docker
operation, default-1.0 switch, Git commit/push or user's live DB migration was performed.
The entire inherited repository suite was not rerun; its previously recorded
19 non-finance fixture/legacy-contract failures are not erased by these results.

## Live Results Remain Separate

### R11

[R11](harness-v2-live-acceptance-r11-20261019.md) reran S02 once after the
bounded correction recovery. The Root and both analyst Tasks succeeded; the
Root fully read both child results before delivery and used 27 DeepSeek calls
in 401.2 seconds. Four Root contract errors were recovered by the one allowed
semantic correction. This passes the targeted delegation, wait/resume, result
read and delivery checks, but `semantic_acceptance=manual_review_required` and
`state_metadata_status=incomplete_metadata` remain. No three-call correction
transport sequence occurred, so 048 is still live-unproved. Real Redis was not
used. See the [R11 report](harness-v2-live-acceptance-r11-20261019.md).

[R10](harness-v2-live-acceptance-r10-20261019.md) completed the fixed
DeepSeek/FakeRedis S01/S02 batch: **2/2 Root deliveries**, 16 and 37 physical
calls, 53 total, within the 120/240 limits. S01 passed the targeted single-source
chain. S02 dispatched separate child work, fully read two successful child
results, and delivered a bounded comparison, but two other child tasks failed
contract validation. The result is partial multi-author evidence, not an
all-child success rate. The new correction transport branch was **not triggered**
in either case; its audit status is `not_triggered`, so 048 remains offline-
verified and live-unproved. Real Redis was not used. See the [R10 report](harness-v2-live-acceptance-r10-20261019.md).

[R9](harness-v2-live-acceptance-r9-20261019.md), separately authorized after
044/045, finished with **1/2 Root deliveries and 1/2 targeted scheduling/handoff
chain passes**. S01 used 14 calls / 229.07 seconds, delivered a 3606-character answer,
and actually consumed the complete child result after durable wait/resume.
Manual source comparison preserves conditions and unspecified numeric units;
structured finance review remains null and metadata incomplete, not silently
promoted by this manual audit. Content is broadly usable but one criticism
overstates missing source timing despite relative dates in the article; this is
a retained wording limitation, not a scheduler date-loss finding.
S02 used 20 calls / 304.01 seconds and delivered
nothing: two separate JSON objects were rejected, then the one permitted format
correction encountered transport_error. Current design forbids restarting that
decision. One child succeeded, one was cancelled; Root read neither full result.
Total **34 charged physical calls**, one unknown transport outcome, no fallback.
Both tree budgets/account associations/artifact hashes passed read-only checks;
all **389** frozen files, snapshot and private protocol matched at completion.
The 120/240 authorization is spent; no further paid execution is authorized.

[R7](harness-v2-live-acceptance-r7-20261019.md) completed S01/S02 once each,
DeepSeek/FakeRedis: **2/2 Root deliveries, 0/2 complete-chain acceptance**.
S01/S02 took 419.87/258.45 seconds and 33/23 calls. Five children were spawned,
none returned an accepted research result. Dispatch-before-wait worked in S02;
child success/full-result return did not. Four children failed contracts; one
exhausted its Root-selected two-call grant and was misclassified internal_error.
S02 also overstated author consensus/conditional claims and disclosed two sample
articles despite three actual read/used articles. No structured review was saved.

Read-only accounting verified charges, actual Task/Attempt/budget/snapshot
bindings, exact grants, conserved balances and artifact hashes. At batch completion
all frozen source/snapshot/private-protocol comparisons matched. 037 and its
subsequent approved child correction are post-batch, offline-only evidence.
The user subsequently authorized one new S01 and one new S02 DeepSeek run and
resolved the budget scope: **120 physical calls per whole tree per case, 240
maximum for the batch**, including children, corrections and transport retries.
[043](../changes/043-h2-bounded-spawn-revalidation.md) freezes a separate R8
protocol using the explicit cap from 042. No fallback, extra paid probes or
additional reruns. Keep 60 minutes and the 24-call/10-minute Root reserve.
This resolves the earlier pending clarification; never reuse R7's 600-call
authorization. Results must be recorded separately after the fixed new batch.

[R8](harness-v2-live-acceptance-r8-20261019.md) has now completed: S01/S02
274.98/420.74 seconds, 17/36 physical calls (53 total); **0/2 Root delivery and
0/2 full-chain acceptance**. Two of four children returned accepted corrected
results, one failed verbatim anchors after correction, and a replacement was
cancelled on Root failure. S02 Root actually read one complete child result.
Both Root tasks ended because an unsupported top-level next_action_key remained
after their one correction. No wait action was accepted. No final synthesis
exists to certify, and raw retained child text is not a delivered Root answer.
Two S02 transport failures stayed charged; they were not the terminal cause.
Read-only accounting/artifact checks passed and all 385 frozen sources/snapshot/
private protocol matched at batch completion. No further paid run is authorized.
Post-batch [044](../changes/044-h2-action-validation-feedback.md) clarifies the
input-only key and preserves actual bounded field diagnostics through Adapter/
capability/correction. It does not rewrite R8 or relax the child anchor policy.

[R6](harness-v2-live-acceptance-r6-20261019.md) remains Q04/Q08/Q14, DeepSeek,
FakeRedis, one repetition each: three full deliveries in 77.93/178.04/167.31
seconds, with 6/18/20 physical calls. None spawned a research child. Delivery
3/3 is not full semantic acceptance, live delegation validation or a commercial
success rate. R4 stays 0/3; R5 stays 1/3.

R6 found absent/stale coverage/review bookkeeping and two overstrong Q14 phrases.
Do not regenerate semantic coverage or force external browsing to make a report
green. Use trusted read events for factual read counts, retain full answers, and
disclose missing semantic verification. The post-R6 original EvidenceRef-to-used
mapping repair and 033-035 have offline evidence only. Frozen R6 answers, states
and source manifest are unchanged.

## Remaining Acceptance

The overall goal stays open. Do not silently substitute other evidence:

- Real Redis: seven conditional tests need an explicitly supplied isolated service.
  The user deferred this environment step; do not restart Docker.
- Live child delegation: R10 proves one complete single-source chain and a
  partial two-author synthesis with two successful full child reads, but not
  stable all-child multi-author completion. R11 adds a successful two-child
  S02 recovery run, but does not establish a repeated success rate. The
  transport-recovery branch was not triggered in R10 or R11, but R14 Q28
  subsequently exercises it once. R14 has only two successful research children
  out of twelve, so stable all-child completion is still unproved. R7-R14 are
  finished; do not turn the general test authorization into an unbounded paid loop.
  R15 adds one single-child and one two-child diagnostic, both fully returned;
  it validates 062's source return, not repeated reliability or live depth two.
  R16 tries the missing depth-two path once but fails the public JSON contract
  before tools/spawn, so it supplies no live recursion/lineage proof.
  A user Query may explicitly request independent child analyses; Runtime must not
  force universal delegation just to trigger a test.
- Full 30-Query acceptance: article-only tests cannot cover web/market/Wiki and
  holdings. Specify those adapters' recovery/ledger boundaries before claiming
  these H2 scenarios are implemented. Preserve current 1.0 behavior.
- Semantic quality and coverage: R7's unsupported consensus, strengthened
  uncertainty and sample discrepancy remain unresolved quality findings. New
  correction fixes structure/anchors, not semantic truth. Do not claim they
  disappeared, add mandatory verification gates or rewrite frozen answers.
- Production entry and sustained load: service/HTTP boundaries are tested, not
  proof of deployed workers, a default-route switch or commercial readiness.

No financial sampling policy, verification gate, new budget default or automatic
model switch was introduced. Green offline tests alone do not complete the goal.
