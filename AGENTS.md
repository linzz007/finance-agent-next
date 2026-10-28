# finance-agent-next

This is an independent financial-research product. Do not edit the source project
`../policy_impact`, reuse its databases, or start its workers during this work.
The internal Python namespace `policy_impact` is retained for compatibility.

## Read First

110 redesigns the H2 Workbench with the approved warm editorial research UI.
Research, test archives, reports, source artifacts and execution traces share
the existing API; no model/budget/queue/database behavior is changed. Keep real
failed/partial source and child receipts visible. Responsive navigation and
inspector drawers, report export and source reading are implemented. Validation:
9 frontend + 8 Python tests pass; one real DeepSeek browser Run succeeded with
6 requests / 1 Task / 4 Attempts / 78 events, including a failed article limit
validation followed by a successful read. Refresh did not add calls. Desktop
1440 and mobile 390/360 widths were checked. See docs/changes/110 and local
../runtime-records/h2-editorial-workbench-20261028 for screenshots and evidence.

109 adds a read-only Test Records tab to the H2 Workbench. It reads original
per-case SQLite and exported scheduling traces under sibling runtime-records
(FINANCE_H2_ARCHIVES override), without importing Runs or invoking models.
Root delivery does not imply case acceptance; preserve partial/failed children.
Archive artifact reads are Run-bound, hash-checked and paginated. The 8511
Workbench was normally restarted on 2026-10-28 to load the current code; this
supersedes older notes below saying that its process still needs restarting.
Focused verification: 8 Python tests and 3 frontend tests passed. Live archive
views retain SCH-02 r5 37 calls/3 Tasks/476 events, SCH-03 r4 36/5/475,
and SCH-04 r4 31/2/467. No new paid calls or historical result changes.

108 records actual scheduling questions, not just offline model fixtures.
SCH-01/02/03 used 80 paid requests; structural tree checks alone overstated
acceptance (review failures, sequential driver, one summary-only result read).
SCH-02-r2 used 33 requests and exposed oversized report binding; r3 used 37
and was formally cancelled after Analyst failure and Verifier timeouts.
Nonterminal wait observations must preserve pending review identity/draft;
premature final waits on that task, and unread reports require full paging.
Use scripts/harness_v2_scheduling_live.py for explicitly authorized live cases:
multiple Workers, same-scope source/Prompt/Profile/catalog freezing, and
separate trace facts for child success, full reads and overlapping requests.
Do not rewrite historical paid results or equate delivery with semantic pass.
Latest live r4/r5 used 104 requests (254 including prior batches): natural
SCH-04 passes this case; SCH-03 proves actual overlap but missed one off option
and needed general replacement; SCH-02 binds/replies to one review but final
revision is unreviewed and partial. Both inventories/snapshots are unchanged.
Post-run Prompt clarifications are not live-validated. Required review hashes
all ResearchResult fields and targets; do not silently relax it for minor edits.

105 adds a separate subagent acceptance catalog without changing the frozen 30 business Queries.
Its 16 new end-to-end cases plus the existing general-profile regression total 45 passed,
0 failed/skipped (104.18s), with deterministic offline model responses through actual
H2 Worker/SQLite/FakeRedis. Coverage includes general, Analyst off/auto/two-round required,
Verifier, Skeptic, each role delegating general, explicit parent context, Prompt/tool
replacement, and a config-only added expert. Ruff and diff checks pass. Logs/JUnit:
../runtime-records/h2-subagent-acceptance-20261027. Initial fixture failures are retained;
these tests prove orchestration and contracts, not live-model semantic quality.

104 implements the user-approved general child plus portable specialist Profiles.
Current v3 rules reference config/agents/profiles/*.json and prompts/finance;
all current roles share the durable child loop and may delegate any registered
Profile within the existing depth-two boundary. General receives dynamic
task_instructions and returns finance.task_result.v1 (content required, research
metadata optional). Analyst review uses review_mode plus optional review_options,
normalized into persisted ReviewPolicy; default/hard limit is five rounds.
Keep historical v1/v2 snapshots and permissions; tests use temporary data only.
See docs/changes/104 for actual validation and remaining live-model boundaries.
104 final focused regression: 99 passed, including real Worker/SQLite/FakeRedis
general-to-grandchild and Analyst-Verifier-resume-to-Root delivery. Report sections
reference saved report_material_ref artifacts, not res_ result IDs. Finance baseline
1639 passed/30 skipped; earlier broad H2 1288/7 skipped/1 fixture failure is retained,
then its historical-hash fixture was fixed and rechecked in the final 99. No paid
calls or server restart; do not claim full broad-suite rerun or live-model quality.

102 changes new H2 Runs to at most five action-format corrections after the
initial generation. One extra identical transport resend is shared across the
whole decision (at most seven physical requests); original budgets/model/time
limits remain authoritative. The limit is frozen with rule assets; missing in
older snapshots means one. Do not modify historical packs or paid records.
Implementation and offline verification are complete; see docs/changes/102.
Final checks: targeted 315 passed; H2 1302 passed / 7 skipped; finance/shared
428 passed / 30 skipped; configuration isolation 10/10; no paid model calls.
These verify offline recovery mechanics, not live-model correction success rates.

101 externalizes H2 finance instructions into config/agents/finance.json and
prompts/finance, with strict loading and a per-Run immutable artifact snapshot.
Root receives a compact same-source role catalog; schemas, permissions and retry
limits remain code-enforced. Children/grandchildren and resumed Attempts inherit
the Run snapshot, not current files. Snapshot-less older Runs use the pinned
pre-101 compatibility pack; invalid declared snapshots never fall back.
Offline: focused346, H2 1272/7 skipped, finance/shared1639/30 skipped, config10/10.
No paid calls. The environment rejected restarting port8511 before execution;
its old process is unchanged and still needs a normal restart to load this code.
Do not run old/new services against the same database to bypass that boundary.

100 supersedes the historical default-1.0 entry constraint for the financial
product: the user explicitly requested the latest 2.0 in the browser. The module
application now starts the H2 Workbench lifecycle and real H2 Workers. Preserve
legacy source for compatibility, but do not route the new page to AgentRuntime.
Web messages, Run mapping and result projection are separate from H2 authority.
No Redis URL means explicitly labeled local FakeRedis, not production Redis.
100 entry verification: 155 focused offline tests pass; one actual browser Run
completed Root+analyst with 12 DeepSeek requests, 11 Attempts and 182 events.
Refresh restored the saved result without model calls. Sessions only group Runs;
automatic cross-Run context, real Redis and multi-user acceptance are not claimed.


- `docs/README.md`: canonical design index, document roles and review status.
- `docs/product.md` and `docs/architecture.md`: product scope and behavior design.
- `docs/development.md`: development workflow, code map and current limits.
- `docs/finance_agent_interface_contracts.md`: local interface contract.
- `docs/finance_agent_eval_v1_acceptance.md`: approved business acceptance cases.
- `docs/research-decisions.md`: A-E selections and evidence boundaries.
- `docs/changes/002-design-authority-migration.md`: Feishu migration, conflicts and gaps.

The user selected repository Markdown as the design authority on 2026-10-16.
Feishu revision 181 is archived locally, including its diagram; future work must
not depend on fetching or editing Feishu. Historical specifications and source
snapshots are references, not competing authorities. If design, contracts, tests
or implementation disagree, report the mismatch; do not invent business rules.
The user owns design choices; implement approved facts.

096 restores the existing legacy author-catalog capability in H2 article.list:
Root uses corpus.list_authors with corpus_all_active scope; scoped children
group only returned authorized rows, never global statistics. Compatible test
corpora without list_authors report returned_articles, not global totals.
Input/rows/default1.0/grants/budgets are unchanged. Seven counterexamples fail
before repair; focused337 pass; H2/state1236 pass/7 skip(413.85s), default
finance1638/30(113.94s),configuration10/10,scoped Ruff/tracked diff. Differently
selected095 suites include one other generic fixture; do not infer net counts.
097 freezes one distinct post095/096 original business batch:28 executable
cases once, Q29/Q30 explicit limits, DeepSeek/FakeRedis/all3adapters, approved
budgets/reserves. Freeze includes reused094 template hash; FIRST integrity
before report edits, no source edits while executing. No semantic/whole-goal
pass is established by those offline counts; preserve094 and all old evidence.

095 repairs duplicate model-input payloads, not stored research: exact-equal
materials share one visible body, successful matching receipts alias its
handle, and the legacy combined observation index points to explicit typed
views. Both Root and child use the existing context-fit boundary; all handles,
permissions, records and full failure diagnostics are retained. Offline:
370 focused passed; H2/state1229 passed/7 skipped (423.23s); default finance
1638 passed/30 skipped (112.87s); configuration10/10, scoped Ruff/tracked diff.
The read-only Q01 replay estimates845443 ->330386 input tokens (-60.92%),
without changing DB bytes, source identities, article states or evidence handles.
That is not provider token measurement, semantic success or proven error cause.

094 is terminal: Q01 only,10 DeepSeek requests/310.94s,9 successful responses
then provider_error, no final answer;27 executable cases not run after blocker,
Q29 requires isolation and Q30 lacks outage injection.10 requests/10 charges,
Root110/Run590 remain.473 frozen/446 runner/898 snapshot entries match in the
FIRST integrity saved before edits. Do not overwrite or call30 executed failures.
Only generic remote error was retained; HTTP status/context rejection is unknown.
The8 successful article.list calls used varied arguments, not8 proven cache
misses for identical input. Catalog rows contain metadata, not article bodies.
Historical087 full-repo17 failures, real Redis and remaining072 design choices
stay open. No post095 paid result is established by offline checks.

093 repairs the existing relative-time integration gap: immutable per-Run
research_clock in the Root brief, inherited by all Task depths/follow-ups and
refreshed into actual finance model input. Legacy Runs derive from created_at;
omitted optional input preserves replay hashes. Runner passes the frozen date,
not just logging it. No DDL/default-1.0, budget, source-grant or financial-policy
change. Final offline: H2/state1219 passed/7 skipped (418.35s), default
finance1638/30 skipped (114.46s), focused117, configuration10/10, scoped
Ruff/tracked diff. H2 PowerShell wrapper was nonzero from inherited uv SSL
warning; JUnit/log have zero failures/errors. Preserve that outcome, earlier
red/fixture failures and all old paid records. No new paid batch or whole-goal
completion follows; actual model date reasoning and real Redis remain separate.

091 records the opt-in personal-context integration before implementation:
immutable per-source Wiki/policy/holdings snapshots, compact catalog, authorized
context.open pages and reused Wiki ranking. Catalogs are not reads; children
cannot capture ambient files. Keep personal provenance separate from articles
and public facts. No writeback, budget, DDL, default-route or review-policy change.
Final offline after the Root-question grant fix: H2/state1201 passed/7 skipped
(426.00s), default finance1638/30 skipped (113.95s), configuration10/10, scoped
Ruff/diff. Earlier focused220 and H2 1200/7 predate that refinement; keep logs
separate. Evidence: runtime-records/h2-personal-context-mvp-20261021. 092 freezes
Q23/Q24 once each, DeepSeek/FakeRedis/personal binding, original budgets/reserves.
Save first run-end integrity before report edits; no fallback, top-up or rerun.
Do not claim live quality, real Redis, full Query suite or whole-goal acceptance.
092 is now terminal: Q23 18 requests/205.87s/4294 chars; Q24 Root23+child27/
441.54s/3882 chars.68 charges; no transport failure or paid rerun. Q24 child
received only personal observations, not article grants:14 correct denials,
then Root read articles. Do not "fix" this by granting the whole corpus or
misattribute it to dropped JSON.469 frozen/443 runner/898 snapshot files match
in FIRST integrity before report edits. Read-only audit v2 ten checks per case;
v1 auditor's wrong article ok=true assumption is retained, not a runtime defect.
False copper contradiction and relative-clock differences remain; review null
and coverage empty do not block answers. See the personal-context report. No
whole-goal pass, real Redis, default switch or Git upload follows from this batch.

Current technical MVP: 087 extends H2 string-control normalization to illegal
string escapes, following fixed Pi source; legal escapes and raw audit artifacts
are preserved. 083 still governs object boundaries: select the first complete
unfenced object, ignore its tail, never skip an invalid first action; an outer
Markdown fence must contain exactly one object. 088 corrects accepted-action
repair counts and raw tail lengths, without changing parsing acceptance.
These supersede the narrower historical 078 notes below. Optional state_update
already exists under 026/028; do not call it wholly unimplemented or turn missing
review/coverage metadata into a new output gate. Technical parsing success does
not establish semantic correctness, live-model reliability or real Redis.
088 verification: new controls 26 failed/2 passed before repair; JSON/execution
283 passed; H2/state 1145 passed/7 skipped; default finance1638/30 skipped;
configuration10/10 and scoped Ruff/tracked diff pass. Logs/JUnit are under sibling
runtime-records/h2-json-boundary-mvp-20261021. No new paid Query, full-suite rerun,
live migration, model switch or Git upload. Preserve 087's earlier17 failures.

089 records the opt-in durable market binding before implementation: reuse
legacy catalog/snapshot/metrics/series, persist authorized immutable snapshots,
keep provider retrieval distinct from collection time and strip legacy IDs.
Children need explicit raw-snapshot grants; Root retains existing run-wide but
not cross-run authority. Market lineage is not article state, a new grant or
causal verification. Defaults/budgets/DDL/financial policy do not change.
Thirty targeted tests pass, including fresh-Service restore and depth-two
handoff. Final offline: H2/state1174 passed/7 skipped (432.57s), default finance
1638/30 skipped (113.15s), combined target114, configuration10/10, scoped Ruff/diff.
Logs: runtime-records/h2-market-mvp-20261021. 090 freezes one Q03 market-only
DeepSeek/FakeRedis diagnostic; preserve run-end integrity before report edits.
No semantic, real-Redis or whole-baseline acceptance follows from offline counts.
090 is terminal: Q03 delivers1536 chars/67.37s with9 successful DeepSeek requests
and9 charges; catalog/snapshot/three series sources validate, no article states.
All568 frozen entries/440 runner entries/898 snapshot files match BEFORE report
edits. Values match saved returns with limitations; review/coverage incomplete.
metrics/restart/delegation/correction not_triggered live. Preserve first integrity
and old reports; full goal remains active, no extra paid rerun or Git upload.

079 adds only explicit public_sources composition to H2, reusing legacy safe
web.search/open and strict Schemas. Default ROOT_TOOLS stay offline; permissions
are still required. Search leads/failures are not page evidence, legacy IDs do
not become H2 refs, and public lineage never becomes article_states or grants.
33 new tests include two-level source handoff and fresh-Service recovery with
one original fetch. H2/state1092/7 skipped (404.33s); default1638/30 (125.06s),
configuration10/10 and scoped full Ruff/diff/local-link checks pass.
080 freezes one new R23/Q19 DeepSeek/FakeRedis run after offline verification:
Root120/child60/Run600/60min, original reserves, no fallback/top-up/extra rerun.
Freeze source/design/tests/runner/snapshot and preserve first run-end integrity
before report edits. Existing072 review/closing decisions and other adapters
remain separate. Overall goal is not complete; do not reinterpret old batches.

R22/077 is terminal:31 DeepSeek/FakeRedis requests (15/10/6),358.62s,2962 chars,
all three Tasks succeeded.25 structural and8 assignment checks pass.074 source
views and076 patch Schema triggered live; all12 anchors repaired unchanged prose.
One unknown Root transport charge remains; patch retransmission not_triggered.
Grandchild resume preserved same Task/budget/checkpoint and sole original read.
441 source/test entries,898 snapshot files and5 helpers match in run-end integrity
before report edits. Semantic overstatement, omitted position sizing and Root
misattributed grandchild failure remain; review absent. Not overall acceptance.

078 is implemented after its narrow framework-informed design was recorded:
H2 agent_action.v1 complete public JSON only, escape raw string controls with
standard JSON encoding. Preserve decoded values/raw artifacts/strict validation;
At that revision unknown escapes, braces, multiple objects, prose and private
reasoning were not repaired; later 083/087 refine only the public boundaries and
string escapes as described above, not financial semantics.
Known normalization count goes into call_finished/response metadata without DDL,
extra requests or arbitrary provider metadata. Final combined H2/state1059/7 skipped
(435.02s),89 net new tests; default1638/30 skipped (189.49s),config10/10 and scoped
Ruff/diff pass. Normalize only JSON object candidates starting with a brace,
not the existing022 DSML path. Retain its2 red counterexamples, the8-test syntax
reproduction and intermediate fixture-method error, not only green logs.
257 local link targets pass, not heading anchors. Zero new paid calls.
R22 read-only replay escapes31 controls, retains2677 content chars/all decoded
fields/raw hash, but13 anchors remain invalid and only eligible for073 repair.
Do not call this successful research or rewrite old R22 evidence. Do not change
default1.0, budgets, permissions or072's remaining unapproved decisions.

077 freezes one new R22/N01 live revalidation after076, reusing R21's exact
query and isolated source snapshot. DeepSeek/FakeRedis, Root120/child60/Run600,
60 minutes and unchanged reserves. Freeze source/design/tests/helpers/snapshot
before requests and do not edit them until run-end integrity is saved. Audit
whole-action correction input, immutable prose, exact transfer accounting and
depth-two source/read boundaries separately. Replay R21 read-only first; no
audit overwrites, absent-column retry query, extra rerun, fallback or top-up.

Change076 recorded the narrow patch-correction action Schema fix before code:
only immutable anchor correction receives a whole final/output Schema and a
valid empty-patch action_example; ordinary isolated feedback remains unchanged.
Never auto-wrap bare patches, change prose/refs/quotes or add retries/budget.
R21 actually triggered TWO immutable patches: grandchild bare patch rejected
at public action parsing,8 bindings dropped; depth-one repaired all6 anchors,
but upstream missing bindings still made it partial. The four ledger errors
are Root3 plus grandchild1; two original domain anchor rejections are separate.
076 is implemented:7 new tests (3 first reproduced); focused50 passed; final
H2 970/7 skipped (401.18s), default finance1638/30 skipped (128.20s), config10/10,
scoped full Ruff/diff passed. Its action Schema field shrinks7034 to877 chars,
not a token/latency/success metric. No paid revalidation within076; subsequent
077/R22 is recorded above. There was no default switch,
live migration, real Redis or Git upload. Keep the overall goal active and
current attribution section8's semantic/design/integration/environment gaps open.

Change 074 separates delegated source provenance from usable parent handles,
repairing RC07 within existing062/interface7/8, not approving remaining072.
Keep original IDs in immutable results and source-lineage sections, but exclude
provenance-only IDs from active known/article refs, state updates and finals.
Runtime delegated_source_refs/local_evidence_refs indexes restore old checkpoints
once from saved observations, never from rereading sources. Explicit grants,
local observations and directly obtained result handles stay usable. Old ledger
rows are retained as source_provenance. Preserve original readers/date/version,
full-result read gates, permissions, budgets and default1.0. Focused tests include
both depth-two wrapper and fully-read ResultRef paths with denied private-source
access and one original read. Full offline regression in074 is H2 963/7 skipped,
default finance1638/30 skipped, configuration10/10;
do not report live or overall baseline acceptance from these offline tests.

R21/075 is now terminal: one DeepSeek/FakeRedis depth-0/1/2 run used 25 physical
requests (Root13, child8, grandchild4) in306.64s. Root delivered succeeded;
both descendants were partial after three DSML/XML Root contract errors and one
bare patch missing its outer action envelope. Two immutable patches preserved
prose/refs/quotes; the depth-one patch repaired all6 anchors. Budgets, source-reader chain, parent full-result
consumption, permissions and preserved partial text were audited; no quota,
transport or exhaustion fault occurred. The private-source reference counterexample
was not triggered because no private source binding was accepted. See the R21
report and runtime-records; this is not semantic or commercial acceptance. The
audit helper was refined after execution to correct its transfer accounting check;
the run's pre-edit freeze evidence remains the authoritative integrity record.

Change 073 applies the user's 2026-10-20 instruction to borrow established
technical mechanisms and use MVP scope. For H2 research anchor-only failures,
freeze the parsed candidate, expose only a field-validated untrusted draft view,
and accept a constrained anchor patch, never a rewritten analysis or tools.
This is the sole narrow refinement of 065 input isolation; other errors keep
065. Unrepaired bindings yield disclosed partial results retaining full text.
No extra correction, model switch, budget, permission or default-1.0 change.
072's remaining financial-review and handoff proposals are still unapproved.
073 is implemented and offline verified: 38 new tests, affected modules340,
H2 954 passed/7 skipped (340.98s), default finance1638/30 (114.91s), config10/10,
scoped full Ruff/diff and46 local-link targets pass. A saved R20 candidate replay
preserves5613 content chars/candidate_answer and discloses all28 omitted anchors;
this is not a live repair or semantic certification. No paid calls, real Redis,
default switch, migration or Git upload. Preserve older reports and remaining gaps.

Current approved H2 budget policy (2026-10-20) is change 067, superseding older
model-selected/hierarchical call grants for NEW Runs: Root own requests <=120,
each child/grandchild Task own requests <=60, whole Run <=600, Run <=60 minutes.
Spawn/follow-up do not let models choose call counts or prepay all 60 credits;
actual physical requests debit both personal and shared limits transactionally.
Corrections/retransmissions count; unknown/started calls stay charged. Restore
original persisted policy/counters/deadline, not new process defaults. Keep the
24-call Root closing reserve; automatic closing entry remains a separate design.
Use the explicit additive self_call_limit migration, never rewrite base DDL or
silently migrate live data. NULL keeps historical hierarchical accounting and
original command replay hashes. Follow-up is a new Task within the same Run pool.
Final 067 offline evidence: H2 901 passed / 7 skipped (372.31 seconds), default
finance 1638 passed / 30 skipped (142.26 seconds, 52 modules), configuration 10/10,
scoped full Ruff/diff checks passed. Thirteen new cases include a SQLite/FakeRedis/
mock-provider six-article path: child9 + Root4 =13, complete pagination/source
handoff, no child result lost at eight calls. No paid rerun, live migration or
commit/push in 067; preserve all historical reports and remaining live-quality/
real-Redis gaps. The user has standing authorization for necessary paid tests;
older per-batch permission notes below must not cause repeat permission requests.

Post-067 live checks R18/R19 are now terminal. R18 runs unchanged Q08 once:
18 Root calls/215.22 seconds, delivered, no child; six complete/two partial reads,
review absent and model coverage stale. R19 separately assigns six exact articles
to one child: Root9 + child2 =11/131.76 seconds. The child has persisted own60,
independent accounting/no prepayment, but fails on DSML then a corrected final's
non-verbatim content anchor before any article read. Root truthfully delivers a
failure explanation. Do not call this successful multi-article child research or
budget exhaustion (58 child calls remain). Eighteen/twenty structural checks pass,
with422/424 frozen inventory entries and898 snapshot files unchanged before each
report edit.63 focused budget/runner tests passed before R18. Reports and original
pre-edit audits are immutable; do not overwrite against later intentional docs.
No extra semantic correction, parser widening, model switch or Runtime changes
were made.037/065 deliberately reject a second structural/anchor error; changing
that recovery policy needs design confirmation. Full goal remains open, including
live over-eight-call child completion, usable deep-source refs, review/coverage,
semantic quality, real Redis and production integration. See R18/R19 reports.

Post-R19 change070 repairs lost terminal failure diagnostics within existing
030/037/interface authority. Worker forwards allowlisted DispatchError details;
Repository stores fixed error_message plus bounded sanitized task.failed fields.
status/wait expose current-attempt failure_diagnostic independent of event cursor,
and finance context retains it as diagnostics, never accepted evidence. No DDL,
extra correction, model fallback or budget change. Fifteen new tests plus child
integration/context assertions pass; final H2 916 passed/7 skipped (430.50s),
default finance1638/30 (192.21s), config10/10, scoped Ruff/diff pass.
071/R20 is now complete once: Root12 + child13 =25 DeepSeek/FakeRedis requests,
453.79s, six complete child-only article reads, accepted return and full Root
consumption. Audit-v2 proves20 structural checks,6 assignment checks and4 source
lineage checks; preserve the initial audit's domain-vs-physical mismatch flag,
do not overwrite it. One correction retransmission is charged on the same model/
Attempt/budget.429 source entries/898 snapshot files/private runner matched before
report edits. Terminal diagnostic handoff was not triggered live because the
child recovered. False contradiction/date-premise attribution, review/coverage,
real Redis, depth-two handles and full business acceptance remain open. Preserve
all R19/R20 records. Necessary paid tests have standing authorization; no repeat
permission question is needed, but do not append reruns without new evidence.

Within the 1.0 review, user-approved change 010 replaces eager finance guidance
with a short capability catalog, run-local skill.open and tool.describe(name).
Keep core research/permission/delivery invariants in the main prompt; do not
restore full Skill bodies or business Schemas to every initial model input.
The financial main prompt owns its JSON protocol; generic loops remain compatible.
Change 010 offline baseline: 1589 passed / 30 skipped / 51 modules, 118.97 seconds;
configuration/separation 10/10 and modified-file Ruff critical rules passed.
No paid model tests; do not turn the earlier r009 strict 4/6 into a new live pass.

Current phase (2026-10-18): retain 1.0 and implement Harness 2.0 scheduling contracts
against frozen `docs/changes/013-harness-v2-dispatch-baseline.md`, version
H2-DISPATCH-2026-10-18-v1. The user stopped further 1.0 review. The initial Git
baseline is commit 715ed93 on main, pushed to the user-selected finance-agent-next
repository; this is a backup, not proof of full acceptance. Inspect privacy and
secrets before subsequent staging or uploads.

Read `docs/harness-v2/interface-contracts.md`, `sqlite-schema.md` and
`redis-protocol.md` for the confirmed interfaces, transaction boundaries and delivery
protocol. The user approved R1/R2/R3 and implementation; contract freeze is
H2-CONTRACTS-2026-10-18-v1. Track implementation and remaining integration in
`docs/changes/014-harness-v2-foundation.md`. Use temporary databases and isolated
Redis namespaces in tests; do not migrate the user's live DB, change the default
financial path, modify the frontend or run paid model calls without authorization.
At the 014-017/020 stage, opt-in H2 foundation, model execution, finance-domain
acceptance and observability checks had 194 passing offline cases and two skipped
real Redis tests; finance regression had 1589 passed / 30 skipped. See 014-017
and 020 for the execution boundaries
and focused evidence. The explicit `FinanceHarnessV2Service.for_finance_corpus`
path can run model-selected article acquisition and `analyst@2.0.0` delegation;
the default legacy financial AgentLoop, public financial routes and deployed
workers remain unchanged. Later explicit H2 paid-model diagnostics are recorded
in 029 and R4-R6; they do not establish finished H2 production integration.
Change 026 implements the user-approved optional `finance.state_update.v1`
mapping for plan, article suggestions, evidence, coverage, review and delivery.
Change 028 repairs final-body retention on malformed optional terminal deltas
and exposes persisted review/delivery to the next Root decision. See
`docs/acceptance/harness-v2-fault-acceptance-r3-20261019.md` for current evidence.
Do not replay tools to simulate recovery, or call the legacy ChatWorkbenchService
Query batch live H2 acceptance merely because its folder name contains `h2`.
Real Redis verification is pending; the user chose to continue other validation
without repairing Docker or providing a Redis service in this iteration.
R4 now records three actual H2 diagnostic failures, not legacy chat results.
Change 030 fixes on-demand tool descriptions, strict finance-tool inputs and
failed-child diagnostics. The user explicitly approved one Root format
correction on the same model and original budget, using two separately charged
capabilities.complete calls. Never hide retries in the Adapter or apply this
exception to provider/quota/permission/transport failures. Preserve R4 results.
Keep default 1.0 unchanged. All physical model calls, including future structure
repairs, must use the new ledger; do not call the old retrying complete() inside the
new adapter.
The focus remains main/subagent scheduling and context handoff/return inspired
by Codex; do not expand financial research rules or invent 1.0 defects to justify
copying a broader backend architecture.

User-approved change 032 continues each action through a persisted checkpoint
and a new queued Attempt, not an in-Attempt loop. The opt-in corpus path now
exposes explicit scheduling actions and a shared depth-1/2 research action loop;
ordinary article tools run in the existing Task and have their own audit events.
Run permissions explicitly include business tools, child scopes remain granted
materials, and artifact bodies stay outside compact checkpoint state. Do not
discard useful research bodies on invalid evidence metadata: publish partial
with invalid bindings excluded, as required by interface-contracts section 7.1.
R6 is a separately fixed Q04/Q08/Q14 deepseek/FakeRedis diagnostic batch defined
in 032, not real Redis or a completed full-suite business acceptance.
R6 completed with 3/3 deliveries and all 356 frozen source hashes unchanged;
see docs/acceptance/harness-v2-live-acceptance-r6-20261019.md. No research child
was selected live. Structured review is absent and model coverage may be stale.
Post-batch repair indexes original successful article.read evidence refs as well
as material wrappers so actual final citations derive used correctly; uncited
articles stay undecided. It is an offline-verified repair, not a paid rerun.
At the close of 032, post-repair offline regression: H2/state/compatibility/live-driver 420 passed /
7 skipped; default finance/shared-runtime 1606 passed / 30 skipped (52 modules);
configuration/separation 10/10 and scoped Ruff passed. The overall goal remains
open; real Redis, full Query acceptance and production integration are not done.

User-approved 033 requires explicit agent.spawn budgets for new H2 research.
Legacy delegate retains its original grant request and implicit wait; disclose
analyst 4 calls/600000ms, verifier/skeptic 1 call/120000ms before model selection.
Root/children receive current trusted TaskBrief budget/deadline. Never silently
change a grant or auto-convert delegate. 034 adds real owned-process termination
and SQLite recovery tests, separate from real Redis and paid model evidence.
Current cumulative status: docs/acceptance/harness-v2-current-acceptance-20261019.md.
After 033/034: H2/state/compatibility/live-driver 443 passed / 7 skipped,
including 15 real-process cases and 20 synchronized two-process claim rounds;
default finance/shared-runtime 1606 passed / 30 skipped (52 modules),
configuration/separation 10/10, scoped Ruff and diff checks passed. No new paid
batch; prior live quality/coverage limitations remain unresolved evidence gaps.

035 repairs existing resume/accounting invariants: preserve the parent's closing
reserve on reacquisition, reject closed/busy Threads atomically, restore the
Thread budget pointer and charge the executing Task account. Spawn preparation
checks for a changed parent model-policy snapshot; no stale default is silently
accepted. Known HTTP state/budget conflicts map to 409, temporary infrastructure
errors to 503, while accepted pending dispatch still returns 202. No DDL change.
After 035, including open-Task-first message routing: H2/state/compatibility/live-
driver 485 passed / 7 skipped, 178.74 seconds, including all 42 added cases.
Default finance/shared-runtime 1606 passed / 30 skipped, 114.78 seconds. The user
now authorized two DeepSeek explicit-spawn cases once each. Freeze 036 and the
private protocol/source snapshot before running, retain all calls/results, no
fallback or extra paid reruns. Real Redis remains deferred; no Docker operations.

R7 (036) has now completed its two authorized DeepSeek/FakeRedis cases once each:
two Root deliveries, zero accepted child results out of five spawned children.
S01/S02 used 33/23 charged physical calls in 419.87/258.45 seconds; do not label
delivery 2/2 as full-chain success. All 377 frozen sources and private snapshot/
protocol comparisons matched before post-batch edits. See the R7 report.
037 first documents child JSON-envelope guidance and budget_denied incorrectly
mapped to internal_error, then the user's separately approved extension of one
format correction to research children on the same model/original grant. It may
repair structure/anchors only, never add research, funds or models; failed second
output/transport must not restart the decision. Inner schema describes final.output.
Original public outputs and all physical charges remain; strict bindings and
partial-result rules are unchanged. No post-037 paid rerun is authorized. Preserve
R7's semantic overstatement/scope failures and keep the overall goal open.
Final post-037 offline regression: H2/state/compatibility/live-driver 505 passed /
7 skipped (242.08 seconds); default finance/shared-runtime 1606 passed / 30 skipped
(193.22 seconds, 52 modules); configuration/separation 10/10; scoped Ruff and diff
checks passed. Six current Markdown documents have 118 resolving local file links.
The 20 net added cases do not constitute paid-model or real-Redis evidence.

038-040 repair existing-contract gaps: recheck model authorization transactionally
at call_started and preserve forbidden/unsent refunds; accumulate result-page
coverage by reader Task/kind across Attempts without mixing user exports or
follow-ups; remove hidden finance result expansion and connect short-result
capacity checks to the actual Attempt model. No new budget defaults, research
rules, DDL, fallback or default-1.0 switch. Final offline results: H2/state/
compatibility/live-driver 536 passed / 7 skipped (206.81 seconds), default finance/
shared-runtime 1606 passed / 30 skipped (123.39 seconds), configuration/separation
10/10 and scoped Ruff passed. Preserve original R7 failures as live evidence.
The user has authorized new DeepSeek S01/S02 executions once each with a requested
120-call ceiling; its batch-versus-per-case scope is awaiting clarification.
No new paid requests have started. Do not reuse the old 600-call R7 protocol;
freeze a separate bounded protocol/snapshot after the ceiling is resolved.

041 repairs confirmed-unsent reservation settlement after cancellation/lease
loss. The internal settlement checks original Run/Thread/Task/Attempt/token,
requires no started_at and reserved/unknown, credits the original call account
once and returns terminal-child funds through existing parent-release accounting.
It restores no execution/publication authority; started calls remain charged,
and reconcile does not automatically refund abandoned reservations. Tests add
22 cases plus a strengthened actual capability cancellation case, including 20
synchronized start/refund races and both terminal-ancestor release orders.
Final post-041 offline regression: H2/state/compatibility/live-driver 558 passed /
7 skipped (226.83 seconds), default finance/shared-runtime 1606 passed / 30 skipped
(126.63 seconds, 52 modules), configuration/separation 10/10, scoped Ruff passed.
No new paid requests, production switch, live DB migration or Docker operation.
The goal remains open; the 120-call batch/per-case clarification is still pending.

042 adds an explicit lower per-case whole-tree cap to the acceptance driver only:
run_h2_case(max_model_calls=...) and CLI --max-model-calls. Strict integer values
must exceed the unchanged 24-call closing reserve and cannot exceed default 600;
invalid values reject before output/provider access. Default remains 600. Each
case stores applied config; the generic CLI manifest declares per-case and batch
bounds. It does not resolve the pending S01/S02 budget allocation. Fixture tests
cover exact grants and shared child/format/transport accounting. Focused: 37 passed;
final H2/state/compatibility/live-driver: 578 passed / 7 skipped (222.69 seconds).
No finance runtime code changed; post-041 default 1606/30 remains the latest actual
default regression, not a new 042 execution. Scoped Ruff/diff passed; eleven docs/
145 local links resolve. No new paid requests or active live batch. The next paid
step requires the user's 120-call scope answer; do not invent authorization or
repeat prior batches. Overall acceptance is still incomplete.

The user has now resolved that question: S01 and S02 once each on DeepSeek,
120 physical calls per whole-tree case / 240 maximum for the batch. Change 043
records this new R8 authorization and overrides the historical pending notes
above. Freeze a separate private protocol/output and source snapshot before
execution, apply max_model_calls=120 to both cases, retain 60 minutes and Root's
24-call/10-minute reserve. Count children, corrections and transport retries;
no fallback, extra paid probe, carry-over, extra repetition or post-repair paid
rerun. Real Redis remains deferred. No new runtime semantics in this protocol.

R8/043 is now finished, not pending: S01/S02 used 17/36 calls (53 total),
274.98/420.74 seconds, zero Root deliveries and zero complete-chain passes.
Two of four children returned accepted corrected analyses; one failed anchors,
one replacement was cancelled on Root failure. S02 Root read one full child
result. Both Root wait actions failed an extra top-level next_action_key even
after correction. All 385 frozen source/snapshot/private-protocol comparisons
matched. Keep failures and charges; this authorization is spent, no extra paid
rerun. 044 records post-batch existing-contract guidance/diagnostic repair:
input-only key placement and bounded loc/type/msg passed through the actual
Adapter/capability into one correction. Do not relax strict validation, add a
fallback or equate offline repair with a paid success; retain raw child artifacts
without pretending they were delivered Root answers. Post-044 full H2/state/
compatibility/live-driver regression: 587 passed / 7 skipped, 210.12 seconds,
nine net additions. Configuration/separation 10/10, scoped Ruff/diff passed.
Fourteen relevant Markdown documents have 166 resolving local file links;
this is not whole-design semantic certification. No further paid calls.
Default finance/shared-runtime regression was then actually rerun after 044:
1606 passed / 30 skipped, 115.57 seconds, 52 modules. Both test sessions finished;
no live batch remains to resume or poll. Full inherited repository tests were
not rerun. Overall acceptance remains incomplete despite these offline passes.

045 repairs shared action-context guidance after child prompt specialization and
preserves existing wait-target status/budget/deadline/dispatch/attempt fields in
finance observations. Strict anchors, one correction and raw failure retention
stay unchanged. Before repair 21 added checks failed; final H2 regression is
609 passed / 7 skipped (237.74 seconds), default finance 1606 passed / 30 skipped
(121.69 seconds, 52 modules); configuration/separation 10/10 and scoped Ruff pass.
The user separately authorized 046/R9 S01/S02 once each after these checks:
DeepSeek only, 120 calls per tree / 240 batch, 60 minutes per case, no fallback,
extra probes or paid reruns. Private preflight passed with zero model requests.
Freeze new R9 sources/protocol/snapshot, preserve R7/R8 and do not edit during
the batch. R9 has now finished: S01 14 calls/229.07 seconds delivered and passed
its targeted scheduling/handoff chain; content broadly usable but one criticism
overstates missing source timing despite relative dates. S02 20 calls/304.01 seconds
failed with no final answer after two JSON objects and a transport failure during
its one correction. The approved rule stops that decision; do not silently add
retry/fallback. Two of three children succeeded; one was cancelled on Root failure.
All 389 frozen files/snapshot/private protocol matched; read-only accounting and
artifact audit passed, 34 charges total, one unknown remote outcome. No budget
violation, production switch, Git push or additional paid run. Authorization is
spent. S01 has no saved structured semantic review; S02 has no synthesis to
revalidate R7's claims. Overall goal stays open. See the R9 acceptance report.

048 changes only the already-approved format-correction transport boundary:
retransmit the identical correction once on the same model, within the original
Attempt budget/deadline, and charge every physical request. It never retries the
original decision, switches models, or expands research. Offline tests cover the
branch. R10 then ran the fixed DeepSeek/FakeRedis S01/S02 batch once each under
120 calls/tree and 240/batch: 16 and 37 calls, both Root answers delivered. S01
passed its targeted source-bound child chain. S02 fully returned two successful
child results but retained two failed child tasks and is only partial
multi-author evidence. Neither case used three calls in one Attempt, so the
audit's transport-retry status is `not_triggered`; do not call R10 live proof of
network recovery. Real Redis, semantic financial correctness, production load
and the full business Query set remain unproved. See the R10 report and 049.

Change 016 adds the opt-in `FinanceRootExecutor` bridge and the
`finance.root_state.v1` durable domain payload. Change 017 adds the explicit
`FinanceHarnessV2Service` composition entry, model-driven article/analyst path,
bounded article reads, authorized material handoff and compact-state guard. The
focused tests cover durable wait/resume, model ledger entries, final state
retention, invalid-state classification, explicit profile binding and partial
delivery. This is not a public route or a switch of the default 1.0 financial
entry point. The finance domain step remains responsible for planning,
article/evidence decisions, review, and delivery content; the bridge only maps
those decisions to capabilities, Checkpoints, and Result sections.

## Historical 1.0 Constraints

Latest evidence (2026-10-20): 050/R13 finished all 30 business catalog entries.
Twenty executable DeepSeek/FakeRedis cases used 331 physical calls, 18 delivered,
Q16/Q18 failed action contracts; nine lacked business adapters and Q29 requires
isolation. All 20 accounting/artifact checks and 382 source/898 snapshot hashes
matched. No correction transport retransmit triggered. Q12 fabricated article
analysis with zero trusted reads; other source-backed semantic errors remain.
No complete semantic acceptance, real Redis, production load or new Runtime
policy is established. Read the R13 report before new tests; do not erase failed
results or turn general paid-test permission into success-seeking repetitions.

Post-R13 change 051 fixes a reproduced source-version read-coverage defect:
never union old-version ranges/complete status into a newly observed hash.
Preserve old materials and immutable observations; surface the version warning
and incomplete metadata. Actual temporary-corpus reindex/fresh-service checkpoint
tests passed. Final offline H2/state/compatibility/driver: 682 passed / 7 skipped
(310.78 seconds); default finance/shared-runtime: 1614 passed / 30 skipped
(187.25 seconds, 52 modules); configuration/separation 10/10, scoped Ruff/diff
passed. Focused checks overlap; do not sum them. No new paid calls, live-source
changes or default-entry switch. This is not a fix for R13's fabricated analysis,
probability/time/author-attribution errors or strict-JSON failures. Keep the
overall acceptance goal open and new research policies for user confirmation.

Post-051 change 052 removes the remaining undocumented last-200 eviction in
the shared evidence state-update merge. Preserve early/opposing bindings and
observed-read entries across metadata changes and fresh-service checkpoints;
do not silently prune to meet the unchanged 128 KiB state guard. Eight added
regressions cover Root/research inheritance, deduplication, rejected foreign refs,
oversized explicit failure and durable recovery. Actual final H2 regression:
690 passed / 7 skipped (310.83 seconds); default finance: 1621 passed / 30 skipped
(184.81 seconds, 52 modules); configuration 10/10; full Ruff on changed Python
files and diff check passed. No new paid tests or R13 result changes. Stronger
format-correction input isolation was proposed for user decision, not implemented.
Do not treat these deterministic retention repairs as model-quality acceptance.

Change 053 removes undocumented read-range and Root-result-list caps. Preserve
all normalized current-version ranges and all already-accepted model strings
in key_points/gaps/warnings; keep bounded diagnostics, summary previews and
actual state/input guards unchanged. Eleven new tests include restored-state
gap filling and full summary-envelope return through result.get. Actual final
H2 regression: 701 passed / 7 skipped (314.78 seconds); default finance:
1631 passed / 30 skipped (187.74 seconds, 52 modules); configuration 10/10,
full Ruff on changed Python files and diff checks passed. No paid calls or
R13 rewrites. The repair does not certify financial quality or real Redis;
new correction/research policies still require user confirmation.

Change 054 repairs trusted TaskBrief identity omitted from finance model input.
Refresh only the whitelisted run/thread/task/attempt/parent/role/version/depth
fields each invocation, including prepared-action recovery; never inherit stale
Attempt identity, copy private Brief fields or treat identifiers as new grants.
Eleven new cases pass, including SQLite/FakeRedis/mock-provider continuation
through a fresh Service with identity checked against physical-call lineage.
Actual H2 regression: 712 passed / 7 skipped (254.55 seconds); default finance:
1631 passed / 30 skipped (126.10 seconds, 52 modules); configuration 10/10 and
full scoped Ruff/diff passed. No paid call, Docker action or Git commit/push.
The audit also found no finance transition into reserve-backed closing mode;
the existing protected normal-call boundary is unchanged. Closing entry/actions
need separate design; do not set every call final=True, shrink reserves or
increase budgets. Correction-input isolation remains pending user confirmation.
R13 semantic failures, missing adapters and real Redis are still unresolved.

Change 055 repairs existing runtime-record path boundaries for quarantine and
orphan scan/removal. Reject linked directory components before traversal/write,
recheck deletion candidates and refuse multiply-linked quarantine files before
append. Failed quarantine must leave its message pending. Thirteen focused
tests pass, with ten new cases/no new skips; actual H2 regression: 722 passed /
7 skipped (242.12 seconds), default finance: 1631 passed / 30 skipped
(124.45 seconds, 52 modules), configuration 10/10, scoped full Ruff/diff passed.
Only pytest-owned synthetic paths were used; no paid calls, live-data mutation,
Docker action or Git commit/push. This is not a hostile-filesystem sandbox or
proof of R13 quality; pending correction-input and closing-mode designs remain.

Change 056 repairs exported Token usage being mistaken for a credential. Exempt
only the five normalized usage names with exact nonnegative integer values;
never exempt strings, booleans, objects or arbitrary token keys. SQLite remains
authoritative and old batch records stay unchanged. Seventeen new tests and two
strengthened request/export cases pass; final H2 regression: 739 passed /
7 skipped (236.75 seconds), default finance: 1631 passed / 30 skipped
(122.17 seconds, 52 modules), configuration 10/10 and full scoped Ruff/diff passed.
Seven current Markdown documents have 176 resolving local file links; this does
not certify heading anchors or whole-design semantics.
Focused 131-case results overlap the collections. No paid call, Docker action,
live-data mutation or Git commit/push occurred. Do not label these record-path/
usage repairs as R13 semantic acceptance or completion of the overall goal.

Change 057 repairs valid UTF-8 text parent context being hidden as unavailable
and breaking granted child article-scope collection. Preserve text as context;
do not derive article grants from it or accept it as original article evidence.
Model actions, Checkpoints, observations and ResearchResult stay strict JSON.
Twenty-three new cases include SQLite/FakeRedis/mock-provider spawn, refused
ungranted reads, assigned-source reading and fresh-Service continuation. Final
focused three-module run: 246 passed (70.15 seconds); H2 regression: 762 passed /
7 skipped (274.08 seconds); default finance: 1631 passed / 30 skipped
(123.50 seconds, 52 modules); configuration 10/10; full Ruff on four changed
Python files and diff checks passed. Focused results overlap the collections.
No paid request, live-data mutation, Docker action or Git commit/push occurred.
R13 failures and pending correction-input/closing-mode choices remain unchanged;
this is not semantic acceptance, real Redis evidence or overall goal completion.

Change 058 repairs the legacy finance child-request envelope replacing the
trusted TaskBrief input grant list. When the control envelope is recognized,
only that envelope is removed; all separately authorized parent context/material
refs remain, and nested envelope refs cannot add or remove permissions. New
initialization and SQLite/FakeRedis continuation tests cover analyst/verifier/
skeptic roles, follow-ups, refused foreign reads, fresh-Service recovery and
compact checkpoints. Focused related modules: 218 passed; H2 regression: 776
passed / 7 skipped; configuration 10/10; scoped Ruff and diff checks passed.
Default finance regression after this source change passed 1631 / 30 skipped
(120.94 seconds, 52 modules); configuration 10/10 was actually rechecked before
post-R14 repair 060. Twelve new initialization cases plus two continuations
equal fourteen new tests total. No paid request or Git commit/push occurred
within 058. This does
not resolve R13 semantic failures, real Redis or pending design choices.

Change 059 freezes the post-058 R14 business regression: all 30 approved Query
IDs retained, 20 executable cases once on DeepSeek/FakeRedis, 120 physical
requests per whole tree, 60 minutes per case, 2,400 batch ceiling, no fallback,
model switch, top-up or repetition. The defined source inventory and the R13
snapshot are checked before and after the run. Runtime records stay under the
private sibling `runtime-records/h2-business-r14-20261020`; preserve R13.

R14 completed: 30 registered / 20 executed / 17 delivered, 403 charged physical
DeepSeek requests. Q08/Q09/Q15 fail the public action contract. All 20 cases
pass thirteen structural checks; 392 inventory entries and 898 copied snapshot
files match at completion. Inventory includes src Python, plugins, docs and
configuration/entrypoints plus AGENTS/schema/driver and its test module, not
the entire offline test suite. Q28 actually recovers the one allowed correction
transport retransmission in three charged calls; this is one live occurrence,
not commercial network proof. The legacy child-request branch is not exercised.
Two of twelve research children succeed and Root reads both full results.
Q27 still analyzes fabricated truncated content with zero body reads; Q26
describes an unexecuted search. Other attribution/time/condition errors and
incomplete review remain. See the R14 report; do not call delivery semantic
acceptance or claim improvement over R13's 18/20. Keep the pre-edit final audit.

060 implements the narrow existing-contract repair for a false source-version
warning after an initial failed read without observed hash/ranges. Genuine
previous partial/full reads and failed states retaining observed hash/ranges
must still warn/reset on a change; keep old evidence and errors. Add red/green
Root/research and fresh-Service continuation tests, then broad regressions.
Do not change correction-input isolation, fence parsing, budgets, research rules,
default 1.0 or missing adapters as part of this repair. These policy choices and
reserve-backed closing entry remain pending user confirmation. No paid rerun
is needed to prove this deterministic warning defect; overall goal stays open.

Post-060 actual results: six added first-read cases fail before runtime repair
and pass afterward, with three added genuine-change guards (nine net new cases).
Selected checks 15 passed; both full modules 79 passed (80.18 seconds); complete
H2/state/compatibility/live-driver 785 passed / 7 skipped (284.81 seconds);
default finance/shared-runtime 1638 passed / 30 skipped (121.77 seconds, 52
modules); configuration 10/10, full scoped Ruff and diff passed. Eight relevant
Markdown files have 162 resolving local file links, not whole-design/anchor
certification. No extra paid call, live-data mutation, Docker action or Git
commit/push occurred. Do not rerun the frozen-batch source comparison after
these intentional edits and overwrite its valid pre-edit final audit.

061 repairs an existing dispatch rejection boundary: wire-valid notifications
that fail persisted-Outbox identity checks must be quarantined before ACK,
not escape the Worker and interrupt genuine work. Decimal validation and the
existing 4 KiB guard prevent numeric conversion exceptions; do not relax claim
identity, swallow infrastructure errors or ACK before durable rejection.
Twenty added tests include sixteen reproduced failures and four existing-behavior
guards. Final H2: 805 passed / 7 skipped; default finance: 1638 / 30 skipped;
configuration 10/10, focused module 50 passed, scoped Ruff/diff passed. No paid
calls or changes to models, budgets, public action parsing, correction input,
financial research, default 1.0, live data or Docker. R14 and pending design
choices remain unchanged; this does not complete overall acceptance.

062 repairs delegated article-source lineage within the existing H2 return
contract. Runtime-generated finance_sources metadata is keyed only by accepted
evidence, with original reader, date, source version and observed ranges.
Result notifications/result.get expose permitted section handles; this is not
an implicit full analysis read or a new input grant. Root and depth-one/two
research share the existing range/version merge, and final refs still derive
used. Never import child coverage/review/use decisions or model-declared reads.
Preserve R14 and pending policy choices; offline verification does not establish
new live quality, real Redis or commercial reliability.

Post-062 final results: H2 825 passed / 7 skipped (307.68 seconds), default
finance/shared runtime 1638 passed / 30 skipped (127.42 seconds, 52 modules),
configuration 10/10, full scoped Ruff/diff passed. Twenty net added tests include
two reproduced pre-repair failures; final strengthened multi-author/date/version
fixtures passed separately (2 cases, 0.82 seconds). Runtime did not change
between final checks. No paid call, live-data/Docker operation or Git commit/push.

063 freezes R15 source-return diagnostics before paid execution: L01/L02 once
each, DeepSeek/FakeRedis only, 120 physical calls per whole tree and 240 batch,
60 minutes each with unchanged reserves. Root discovers metadata; children alone
read bodies by explicit test assignment, not by a new universal reading rule.
Freeze source/dispatch tests/private protocol and the original article snapshot;
no source edits during execution, fallback, top-up or success-seeking reruns.
Audit actual source reads and lineage separately from delivery and semantics.

R15/063 is now terminal, not an active process: L01/L02 deliver in 208.72/600.11
seconds with 13/33 physical DeepSeek calls (46 total), and all three analysts
return accepted results. Root never reads original bodies and fully consumes
each child analysis before delivery. Source identity/date/hash/ranges/original
reader and final actual use match the true reads; 35 unused discovered L02
articles stay unread/undecided. Twelve structural checks pass per case; L01's
six applicable diagnostics and L02's seven pass. The L01 second-spawn key is
not applicable, not an independent pass. All 412 frozen inventory entries, 898
snapshot files and the private wrapper match before documentation edits. One
unknown transport outcome stays charged; correction transport retransmission
is not_triggered in R15. The audit has zero paid calls. Preserve the final hash
record rather than overwrite it against later intentional edits.

R15 demonstrates the 062 depth-one source-return boundary, not repeated quality
or a controlled comparison with original S01/S02. Both reviews are absent and
financial conditions/commonality/criticism remain overstated. No runtime code
changed; 825/7 skipped and default 1638/30 skipped remain the latest broad
offline results, not new R15 runs. Real Redis, missing business adapters,
production integration and pending parser/correction-input/closing designs
remain unresolved. No fixed-batch rerun, Git commit/push or default-1.0 switch.
The overall goal remains open; see the R15 and current acceptance reports.

064 freezes one new R16/N01 depth-two diagnostic under the existing baseline,
not a repetition of R15: Root -> analyst -> analyst, only the grandchild reads
the original, then complete analyses and actual source lineage return through
both boundaries. DeepSeek/FakeRedis only, 120 physical calls for the whole tree
and fixed batch, 60 minutes, unchanged reserves. Child/grandchild diagnostic
grants are explicitly 32 calls/20 minutes and 10 calls/10 minutes, not runtime
defaults. Freeze source/dispatch tests/private helpers/snapshot before requests;
no source edits during execution, fallback, probes or success-seeking reruns.
Keep pending design choices and all old paid evidence unchanged. Real Redis,
default 1.0, deployment and live user data remain untouched.

R16/064 is now terminal: N01 failed before any tool or descendant in 6.55
seconds, with two charged contract_error responses and no final answer. The
first output invents an XML tool result; its one correction is malformed JSON
and reuses an unsupported publication_date from that fake description. The
real fields are published_after/published_before. Do not claim a tool executed,
an actual parameter validation happened before JSON parsing, or input isolation
is causally proved to fix it. Depth-two execution/lineage are not_triggered;
the no-depth-three boolean is vacuous. All twelve structural checks pass and
414 frozen inventory entries, 898 snapshot files and all three private helpers
match before edits. No runtime code changed or extra paid run occurred.
Three focused recursion tests pass in 16.23 seconds; configuration 10/10,
zero-request preflight and private-script Ruff pass. Broad 825/7-skipped and
default 1638/30-skipped results remain prior executions. Correction-input
isolation and optional single-outer-fence normalization were separately asked
of the user and remain unapproved; do not infer design consent from general
paid-test permission. Preserve R14/R15 and keep the overall goal open.

The user has now explicitly approved both choices in 065: isolated correction
input and one outer bare/json Markdown fence around a single JSON object. This
overrides historical pending notes, not recorded batch outcomes. Update design
before implementation. Never reinject the whole rejected output, even if short;
retain it in audit and use trusted original context, bounded field diagnostics
and syntax-only masked excerpts. Preserve the original public text and strict
action/argument/permission checks after fence normalization. Root and children
share the same one-correction/original-model/budget/deadline boundary and 048's
identical retransmission. Do not change default 1.0, native calling, closing
policy, live data, real Redis or old paid batches. Offline and new bounded live
evidence must be recorded separately; overall acceptance remains incomplete.

Post-065 actual final offline results: H2 888 passed / 7 skipped (474.38 seconds),
default finance/shared-runtime 1638 passed / 30 skipped (208.90 seconds), 52
modules; four affected modules 447 passed, configuration 10/10, scoped full Ruff
and diff checks pass. There are 63 net new tests. 066 records a new fixed R17
batch: unchanged Q08/Q15 and 064's N01 once each, DeepSeek/FakeRedis only,
120 physical calls per whole tree / 360 batch, 60 minutes each with unchanged
reserves. Freeze code/design/tests/helpers/snapshot before requests. Save
redacted public model inputs privately to audit real correction isolation; no
native tools, fallback, extra probe or automatic paid rerun. Never change source
while the batch runs or label an untriggered branch a live pass.

R17/066 is now terminal: Q08/Q15/N01 deliver in 360.81/140.67/301.66 seconds,
39/16/20 physical DeepSeek calls, 75 total. Twelve structural checks pass each;
417 frozen inventory entries, 898 snapshot files and five helpers match before
documentation edits. Nine sent corrections plus one prepared/unsent budget
denial verify input isolation. No live fence or correction-transport retry is
triggered. Q08's reviewer exhausts its explicit eight-call grant; no hidden
top-up. N01 executes depths zero/one/two and both full-result reads but depth
one returns partial after citing the grandchild's private source artifact.
known_evidence_refs includes that nested ref without an access grant. Root
discloses the broken accepted-source return and keeps unread/undecided. Preserve
permissions, complete candidates and this failed evidence; do not promote a
partial result or auto-grant nested IDs. A usable-ref/provenance refinement must
be documented separately. No extra paid rerun, source edit, default switch or
Git commit/push occurred. Reviews and semantic quality remain incomplete; the
overall goal is not complete. See the R17 and current acceptance reports.

The iteration records below remain compatibility requirements. References to
"current" in those records describe their original iterations, not a prohibition
on the approved Harness 2.0 dispatch work above.

Previously approved versioned research integration is recorded in change 004:
the complete optional v2 path, quality-first budgets and parent-coordinated
overflow reading. This local interface version is not Harness 2.0.

The current transport iteration is user-approved change 007: optional Responses
text transport and layered delivery verification. Keep existing default profiles,
public JSON actions and financial research decisions unchanged; no native tools,
automatic endpoint/model fallback or remote conversation storage is authorized.

The user approved change 008 option A: autonomous incremental review within
existing shared budgets and versioned candidate preservation. First-phase
review/delivery is implemented against architecture sections 6-7 and interface
5.2. Batch r008-01 stopped at a quota probe with zero business runs. After the
user recharged, r008-02 completed Q04/Q14 three times each using the frozen
600-second diagnostic protocol: four complete analyses passed content/evidence,
two source-only results failed content. Only two of six passed all three layers;
two Q14 research paths were valid but mislabeled failed review as disputes.
The fixed batch has ended; do not append paid tests without a new decision.
Post-batch fixes expose the full nested output Schema, close terminal pending
goals, and mark Runtime-only review_execution_gap separately from real issues.
Those fixes received their next live coverage in r009-01 below; keep frozen
r008 results unchanged. No-public-body
waiting/retry policy remains undecided. Second-phase context/legacy notes remain
separate and unimplemented.
The current goal is a working 1.0,
not broader backend or financial strategy redesign.

Current change 009 aligns finance context to the user's at-most-1M configuration,
with 70% pressure / 60% target and output/safety reservations. Honor smaller
configured limits; do not claim the GPT relay has certified 1M capacity. Complex
compaction and first-public-body waiting policy remain separate. Run the fixed
six-case r009-01 protocol only after freezing code/design and offline regression;
do not rewrite old results or append paid reruns until lucky success.
Live r009-01 completed Q04/Q08/Q14 twice each under the same frozen snapshot and
600-second diagnostic protocol, explicitly using gpt-responses, not the default
legacy Chat profile. All six delivered complete source-bound analyses; strict
content/evidence/trace pass is 4/6 because both Q04 traces misbound tool results
after failed calls. Six runs had zero offloads/restores and no pending goals.
Post-batch repair binds turns/results by actual call ID and preserves failed
observations, pre-invocation rejections and role-wrapper compatibility. Do not
rewrite the original live traces or count offline repair as live revalidation.
Offline baseline at the close of change 009: 1568 passed / 30 skipped / 50 modules, 149.47 seconds;
focused tests 202 passed, configuration/isolation checks 10/10, zero model calls.
The fixed paid batch has ended; do not append reruns without a new decision.
The detailed private report is data/acceptance/009-context-and-v1-report.md.

## Development Loop

1. Read the local design index and applicable confirmed design; inspect actual code/trace.
2. Distinguish an implementation defect from an unspecified design decision.
3. Record behavior changes, branches, failures and acceptance before code. Obtain
   user confirmation for product/research/architecture choices; draft text is not
   approval. Fix defects within an already confirmed design without reopening the
   design unnecessarily. Link existing contracts; do not duplicate them.
4. Implement a coherent change, update affected contracts and focused tests.
5. Run regression checks, review the diff, and record failures as well as successes.
6. Do not equate offline test success with live model quality or production readiness.

## Invariants

- Main Agent chooses evidence, depth, delegation, review targets, and coverage stop.
- Runtime enforces permissions, real observations, contracts, budgets, and trace.
- No keyword-specific financial workflows or forced universal answer template.
- Preserve useful analysis, uncertainty, author views, and contrary evidence on review failure.
- Keep each article's author, date, version, and EvidenceRefs distinguishable.
- Never commit article contents, holdings, Wiki, credentials, conversations, or traces.
- Use fixtures/temporary roots in tests; do not read or mutate the user's live corpus.

## Commands

```powershell
uv sync --locked --group dev
uv run --locked python scripts/check_finance_baseline.py
uv run --locked python scripts/test_finance_baseline.py
uv run --locked python run_finance.py --port 8510
```

The new application exposes only `finance_observer` and financial research chat.
Legacy launchers and unrelated modules are inherited dependency/reference code,
not product entry points. No model calls are needed for the offline baseline.
