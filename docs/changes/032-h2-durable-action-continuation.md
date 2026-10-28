# H2 Durable Action Continuation

Date: 2026-10-19

Status: user approved option A before implementation: finish one action,
persist a checkpoint and requeue; do not run multiple decisions in one Attempt.
Authority: frozen 013 sections 6-9, interface-contracts R1 and sections 2/8,
and the user's 2026-10-19 continuation decision. Default 1.0 is unchanged.

## Reason

R5 found that the actual financial domain always waits after one child. Generic
scheduler capabilities therefore do not by themselves implement model-directed
asynchronous finance work or two-level research delegation. FinanceStep currently
accepts waiting or terminal outcomes only. Define the missing continuation
boundary explicitly rather than hiding a loop in the Root executor.

## Continue Contract

- Add a trusted Worker outcome Continue(checkpoint), distinct from Suspend and
  Submission. It is not a model-selected Task status or a new terminal state.
- FinanceStep(status="continuing") requires state, no Wait and no Submission.
  FinanceRootExecutor stores its compact state and yields Continue(checkpoint).
- One SQLite transaction fences the old lease, saves the checkpoint, marks the
  old Attempt suspended, creates exactly one queued Attempt for the same Task,
  and inserts its Outbox notification. Record task.continued with both Attempt
  references and the checkpoint reference. Thread returns to existing ready.
- Redis is notified later. ACK the old delivery only after this transaction.
  Duplicate old messages cannot create another successor; stale leases cannot
  continue. DB failure rolls back the whole transition and prevents ACK.
- Continue retains run/thread/task identity, input grants, original budget and
  deadline. It neither spends a model call nor consumes failure-retry allowance.
  Model/profile changes remain governed by the existing user policy and fences.
- The next invocation consumes only saved state, mailbox and real observations;
  never rerun a submitted child simply to reconstruct the parent's local stack.

## Financial Action Integration

Use the existing public AgentAction envelope for both main and research children,
with different trusted permission snapshots. Add descriptions for authorized
agent.spawn/wait/status/send/cancel/resume and result.get through tool.describe;
description lookup does not grant execution rights. No native Function Calling.

- agent.spawn returns its durable receipt to the requesting Agent. Persist it as
  an observation and Continue; do not implicitly wait. It may issue another
  action on the next Attempt, such as spawning independent work or reading.
- agent.wait is an explicit choice: save its checkpoint and use existing Suspend.
  Status/read/send operations complete one bounded action then Continue.
- Preserve legacy delegate compatibility separately; do not claim its implicit
  wait is asynchronous spawn or dispatch the same intent by both paths.
- Research children get the same action machinery, bounded role tools, exact
  task/output contract and authorized input refs; depth two cannot spawn again.
  Ordinary article tools must not become extra research-recursion levels.
- Domain action keys and observations must be persisted before advancing. Use
  existing Repository idempotency receipts for scheduler writes. Record input
  errors as failed observations for a subsequent model choice; cancellation,
  stale lease and infrastructure failures are not converted into source success.

Before executing a model-selected scheduler operation, save its validated action
as an artifact and checkpoint a prepared_action_ref. On recovery, execute that
same prepared action/key without another model decision. A completed operation
stores a bounded observation ref and clears the prepared ref before Continue.
Write replay uses the existing receipt; read replay cannot create a new child.
Explicit wait instead returns Suspend with its next checkpoint. This journal
does not assert that arbitrary external side effects are safely replayable.

## Context And Result Boundaries

Keep the primary semantic data in authorized artifacts. The durable state holds
indexes and observation refs, not article bodies or large tool payloads. Short
results may be injected in full; long results retain summary, evidence, gaps and
result_ref, with visible pagination and result.get access. Do not present the
first 64000 characters as a complete JSON result. Do not evict all but twelve
materials irrespective of actual configured context pressure.

This integration does not introduce fixed sample sizes, financial keyword
policies, mandatory delegation or more restrictive verification gates. Missing
review/coverage remain disclosed independently of delivery. Web/market/Wiki
business-tool parity and real-service verification remain explicit scope items,
not capabilities implied by a Continue outcome.

### Implementation Mapping

Ordinary article actions execute inside the current bounded domain step, with
tool.started/tool.completed events and artifact observations, then Continue.
They do not create research Threads. The legacy finance_tool executor remains
readable for already queued work; new article actions do not submit it again.
Business tool names must be included in the run's permission snapshot, alongside
the explicitly enabled scheduling tools. Role tools are intersected with that
snapshot; tool.describe is discovery, not a permission grant.

Root article scope is the bound corpus. A research child's scope is the article
IDs in its explicit input materials (including supplied discovery metadata) and
its own authorized read observations. Its list/search output is filtered to that
scope; an ungranted article ID is rejected before corpus access. Research children
may delegate only that authorized material, never their parent's whole context.

Research roles use the same per-action continuation and explicit wait machinery.
Their final output remains finance.research_result.v1, not the Root answer
contract. Initial legacy child_request envelopes are unpacked without dropping
the first material of a normal Spawn request. Exact task, success criteria,
role and output contract remain visible after restart. New mailbox content is
included as messages with sequence/kind, not promoted to fabricated evidence.

Material and observation indexes survive checkpoints. Context assembly uses the
configured input allowance rather than a fixed last-twelve selection. Full
materials fitting the allowance stay visible; otherwise show a reference/index
with an explicit omitted flag and authorized expansion tools. Result summaries
never imply the full result has been read; paged JSON is not parsed as complete
until all pages are present. These are transport/context rules, not financial
sampling or verification requirements.

For local artifact expansion, `material.get(material_ref, offset=0, limit=16000)`
uses the existing read_input grant checks and returns content/has_more/next_offset.
It is separate from result.get, whose result_ref continues to identify accepted
Task results. It cannot read paths, other runs, or ungranted sibling artifacts.
This small domain tool exposes the already-approved authorized artifact access;
it does not enlarge input grants or treat a page as newly verified evidence.

The shared research finalizer follows interface-contracts section 7.1: a valid
ResearchResult with bad source metadata retains its complete candidate body.
Unknown references, receipts presented as evidence and quote mismatches are
recorded as validation warnings; invalid bindings are excluded from the accepted
evidence list and the result is partial/incomplete_metadata. This is not a new
semantic output gate. Invalid action/result structure remains a contract error;
cancelled/stale leases and storage failures retain their original handling.

## Verification And Implementation Order

1. Continue transaction, Worker mapping and FinanceStep bridge; test duplicate
   delivery, stale lease, transaction rollback and restart at the saved state.
2. Main Agent's explicit scheduling actions and progressive result retrieval;
   test two independent spawns before wait and parent progress with child pending.
3. Research-child shared action loop and scoped article access; test actual
   depth-0 -> depth-1 -> depth-2 execution, forbidden depth-3 and no permission
   expansion, including the single-worker case.
4. Context/observation refs, physical ledger and business tool events; test
   long-result pagination, source-preserving compact state and failure delivery.
5. Final-source offline regression, then a separately fixed live Query batch.

Each step records its own evidence. Completing step 1 does not complete the
financial integration, Query acceptance, real Redis testing or the active goal.

## Implementation Evidence And Live Protocol

Continue is wired through Repository, Worker and FinanceRootExecutor. The actual
opt-in corpus entry now uses bounded direct tools, explicit scheduling, shared
research-child continuation and granted material scope. Legacy delegate still
waits; the legacy finance_tool handler is retained only for queued compatibility.
Prepared writes save the original action and child-request reference before any
scheduler side effect. Checkpoint corruption reports checkpoint_invalid without
another model decision. Normal continuations neither reset deadlines/budgets nor
consume the failed-attempt recovery allowance fixed in 031.

Focused offline evidence includes six integration cases: independent spawns and
parent progress with two children pending on one Worker; real depth 0/1/2,
depth-three denial and article-scope rejection; three invalid-wait branches;
and committed-spawn recovery with one child and no extra Root decision before
the saved receipt. Context tests retain all fifteen source indexes, avoid body
storage in checkpoints, preserve byte mailbox messages across continuations,
and distinguish a long result page from a complete result.

Pre-R6 frozen-source regression (2026-10-19): H2/financial state/compatibility/live
driver tests **419 passed / 7 skipped** in 197.02 seconds. The unchanged default
financial/shared-runtime baseline is **1606 passed / 30 skipped** across 52
modules in 190.05 seconds. Separation/configuration checks are **10/10**; scoped
Ruff and git diff --check passed. The existing Starlette/httpx deprecation warning
remains. All these runs made zero paid model calls; skipped service/fault cases
are not counted as real Redis or sustained-load proof.

After the final-source offline regression, R6 is a separate fixed diagnostic
batch: Q04/Q08/Q14 once each, explicit deepseek, the unchanged R5 source snapshot,
ordinary existing budgets and FakeRedis. Output is a new directory under
runtime-records/h2-live-r6-20261019. There is no automatic provider switch, added
paid repetition, or rewrite of R4/R5. Evaluate delivery, evidence/content and
trace independently. This batch cannot prove real Redis, load behavior or full
thirty-Query acceptance. Record failures, including remote/model errors, as-is.

## R6 Follow-Up Within The Existing Article-Set Contract

The frozen R6 batch has ended. All three cases delivered, but their actual
article.read source references were absent from article_states.material_refs:
only the material wrapper was indexed. Final answers citing the original read
artifact consequently left use_status undecided. This violates the existing
actual-final-evidence definition; it is not a new reading or selection policy.

On a successful article.read observation, retain both the wrapper and the
accepted original source references in that article's material_refs. Final use
is still derived by intersection with accepted final evidence_refs. Discovery
alone, successful reading alone, and unrelated artifacts do not imply use.
Preserve failed-read handling, source identity and prior page references.

Add an integration regression citing the raw read reference as well as the
existing wrapper-reference case. Check that a listed but unread/uncited article
stays undecided. Verify offline after this repair; do not rewrite R6 state or
describe the repair as live-revalidated. R6's stale model-written coverage counts
and absent structured review remain separate from these observed read/use facts.

Post-repair final-source regression: **420 passed / 7 skipped** in 132.40 seconds
for H2/state/compatibility/live-driver tests; default finance/shared-runtime
**1606 passed / 30 skipped** in 116.19 seconds across 52 modules. Configuration
and separation checks remain **10/10**; scoped Ruff and git diff --check passed.
The known Starlette/httpx deprecation warning remains. These offline checks made
zero paid calls. The frozen R6 report is
[R6 acceptance](../acceptance/harness-v2-live-acceptance-r6-20261019.md): three
deliveries, no live research delegation, incomplete/stale metadata and explicit
remaining semantic/real-service acceptance gaps. The active goal stays open.
