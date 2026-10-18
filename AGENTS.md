# finance-agent-next

This is an independent financial-research product. Do not edit the source project
`../policy_impact`, reuse its databases, or start its workers during this work.
The internal Python namespace `policy_impact` is retained for compatibility.

## Read First

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

Within the 1.0 review, user-approved change 010 replaces eager finance guidance
with a short capability catalog, run-local skill.open and tool.describe(name).
Keep core research/permission/delivery invariants in the main prompt; do not
restore full Skill bodies or business Schemas to every initial model input.
The financial main prompt owns its JSON protocol; generic loops remain compatible.
Change 010 offline baseline: 1589 passed / 30 skipped / 51 modules, 118.97 seconds;
configuration/separation 10/10 and modified-file Ruff critical rules passed.
No paid model tests; do not turn the earlier r009 strict 4/6 into a new live pass.

Current phase (2026-10-18): jointly explain and review the existing 1.0 design,
then compare approved behavior with code and test/Trace evidence. Follow the
review tracker in `docs/README.md`; do not mark user acceptance from a static
read or equate implementation with live validation. Discuss unspecified or
conflicting design choices with the user; fix defects against confirmed design.
Align documents, code and acceptance evidence before freezing a Git baseline.
The repository currently has no first commit or remote; inspect privacy and
secrets before staging, and confirm the remote before uploading. No database,
queue, frontend or original-project changes are authorized by this review.

Harness 2.0 is on hold until the 1.0 review and baseline freeze. The user's
initial focus is main/subagent scheduling and context handoff/return inspired
by Codex, not every backend capability in the broad proposal. Establish actual
needs and approve a scoped design before implementation; do not invent 1.0
defects to justify copying Codex.

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
