# Finance Agent Research Interfaces

Harness 2.0 scheduling is specified separately in the confirmed [dispatch interfaces](harness-v2/interface-contracts.md), [SQLite storage contract](harness-v2/sqlite-schema.md) and [Redis delivery protocol](harness-v2/redis-protocol.md), frozen as H2-CONTRACTS-2026-10-18-v1 after user approval of R1/R2/R3. The opt-in repository/transport/scripted-worker foundation is implemented; HTTP endpoints and financial AgentLoop integration are not. Progress and verification are tracked in [change 014](changes/014-harness-v2-foundation.md). These contracts do not replace the financial research semantics here. The `v2` research integration in section 8 remains a different version boundary.

Execution update: [change 015](changes/015-harness-v2-model-execution.md) adds the single-request asynchronous model transport, physical-call ledger and trusted Worker capabilities. It does not yet serialize/restore the financial AgentLoop, EvidenceLedger or final-review state, and has no paid-model acceptance. Existing financial behavior and entry points remain unchanged.

This is the interface contract for the selected A1 + B3 + C1 + D* + E* design. The repository is the design authority; see [architecture](architecture.md) and the [migration review](changes/002-design-authority-migration.md). This document combines required semantics with inherited endpoint shapes and configurable implementation limits; none of those limits proves model quality or full implementation compliance. It is not an instruction to always use every tool. The main AgentLoop chooses research methods based on the question; final prose has no fixed template.

Migration history (2026-10-16): aligned with Feishu revision 181, especially chapter 14 and the non-conflicting transport additions in 13.4. The [pre-migration contract](archive/finance_agent_interface_contracts.before-migration.md) retains superseded implementation descriptions. Implementation update (2026-10-17): the separately approved [change 004](changes/004-versioned-research-implementation.md) connects the optional versioned path in section 8; offline checks do not certify live financial quality.

Design decision update (2026-10-16): the user confirmed task-focused coherent article understanding as the primary handoff, free-form analysis with a source/coverage envelope, synthesis that can use this understanding, full-text-first deep analysis when the model input budget permits, and a controlled optional reading Subagent with its own read-only tool loop. Structured claims remain optional supporting material. See [architecture sections 4 and 8](architecture.md#4-单篇文章与文章集合), the [decision record](changes/003-article-understanding-review.md#4-已确认的交接形式) and sections 3.1-3.2 below. Existing endpoint shapes remain v1 contracts; section 8 defines the explicitly selected new path, now implemented separately. These decisions neither remove existing endpoints nor make cards mandatory.

Further confirmed on 2026-10-16: the main Agent owns the overall goals, orchestration, acceptance of results and final delivery; it may delegate article analysis, cross-article synthesis, review and candidate drafting rather than personally repeating them. Full understandings are preferred when they fit the actual executor's input budget; recoverable working views are used under real pressure. Acquisition, task analysis and synthesis consideration are separate coverage facts. Section 4.1 specifies the handoff. Section 8 formalizes the selected versioned analyst + Skills, task/result fields and run-local artifact mapping; none silently replaces the v1 synthesis API.

## 1. Ownership and call graph

Tool reliability update (2026-10-17): [change 006](changes/006-json-tool-contracts-and-recovery.md)
defines the public JSON action/input-schema boundary, bounded corrections, classified
failures and budget-aware context views. No provider-native Function Calling is used.
Python tool input types are the executable schema authority; plugin JSON supplies tool
and field descriptions. Domain permission, source and evidence checks remain in force.
The finance main context also supplies `review_target_json_schema` generated directly
from `FinanceReviewTarget`, so final-review metadata enums are visible before generation.
This does not require extra targets or constrain the free-form answer body; bounded
metadata completion and source-preserving delivery remain unchanged.
`context.open` accepts a generated `context_id` and optional character `offset`/`limit`;
large records return a checksummed, continuous JSON-text page with `next_offset`, not
a summary or a new evidence source. Small unpaged observations retain the v1 shape.

This section maps retained v1 wiring; the optional full-text-first integration is mapped in section 8, not retrofitted into the old card API.

`agent_runtime.py` creates a `FinanceResearchGateway` for each turn. The gateway owns one `EvidenceLedger`, `FinanceArticleGateway`, and `ResearchArtifacts`. `plugins/skills/manifest.json` permits `research.artifact` only for the main chat agent and loads `plugins/skills/finance/research_artifacts.md` to show its call shapes to the model. `plugins/tools/finance_research.json` supplies the tool descriptor. Subagents keep their existing bounded, read-only finance gateways and return structured claim/verdict sets through the delegation contract; they do not write the parent's artifact store.

```
User query -> research.plan -> AgentLoop (tool/delegate/final chosen dynamically)
                       |-> article.list -> article.read -> EvidenceLedger
                        |-> optional article.study(one article, skim/deep) -> article_card
                        |-> optional research.artifact(article_card)
                        |-> optional research.artifact(research_note) -> compact run memory
                        |-> optional research.artifact(comparison)
                       |-> optional research.synthesize(article_card IDs) -> comparison
                       |-> web/market tools -> EvidenceLedger
                       |-> optional research.artifact(review)
                       |-> optional research.verify(selected claims + observed evidence) -> review
                       |-> substantive analysis -> research.checkpoint (method chosen by main Agent)
                       |                              -> revise/research or continue
                       |-> final candidate -> answer_claim_audit -> revise/research or publish
                       |-> context-pressure offload -> context.open(context_id) if needed
                       |-> analyst/verifier/skeptic delegation -> child evidence bundle -> parent audit
                       `-> final answer and existing final guards -> Runtime delivery status
```

`research.artifact` is a working-memory plugin, not a publication or report endpoint. Its records are model-authored and source-bound, not automatically true. Final-answer citation and subagent audits enforce the confirmed architecture; existing code gates do not override that design. A `review` saved by the main agent must not be presented as independent subagent verification.
The Runtime's `evidence_audit` trace step includes `research_artifacts` summaries, while individual tool calls remain visible in the ordinary tool trace.

## 2. Tool endpoint

`research.artifact(operation, kind?, payload?, artifact_id?)` returns JSON. The only operations are:

| Operation | Required input | Return | Failure |
| --- | --- | --- | --- |
| `save` | `kind`, `payload` | metadata with generated `artifact_id` and counts | `ValueError` for invalid schema/source binding; `PermissionError` without Runtime-bound run |
| `get` | `artifact_id` | complete saved record | `FileNotFoundError` for absent ID; `ValueError` for stale source or invalid ID/record |
| `list` | optional `kind` | at most 100 record summaries and count | `ValueError` for unknown kind |

Kinds: `article_card`, `comparison`, `review`, `research_note`. Record envelope: `{schema_version:"finance.research_artifact.v1", artifact_id, kind, session_id, run_id, payload}`. IDs are content-derived (`card_*`, `comparison_*`, `review_*`, `note_*`), immutable within the run and the only reload key. No API accepts a filesystem path or another run ID. Persistence is atomic under `<company>/runs/<run_id>/research_artifacts/`. Records are readable only from the same bound session/run; changed article versions make linked records stale. The store does not silently substitute the new article text.

### 2.1 `research_note` payload

`research_note` is optional run-local working memory for a long or multi-article turn. It is deliberately less prescriptive than `article_card`: the main Agent decides when a compact note will reduce repeated context restoration. It does not replace source reading, prove a claim, or bypass the final answer audit.

```json
{
  "question": "What should be compared across these articles?",
  "purpose": "Carry source-bound observations into the next research pass",
  "entries": [{
    "type": "article_view",
    "article_id": "article_...",
    "statement": "The author presents a conditional view.",
    "evidence_refs": ["evidence_..."],
    "claim_ids": ["claim_..."],
    "conditions": "Optional condition",
    "time_scope": "Optional time scope"
  }],
  "open_questions": ["What independent evidence is still needed?"],
  "limitations": "The note records article views; it does not verify them."
}
```

Each entry needs at least one canonical `evidence_*` from the current EvidenceLedger. Article EvidenceRefs are checked against the current article content hash both on save and on reload; a changed article makes the note stale. Evidence aliases, paths, cross-run IDs and unobserved claims are rejected. A note may include article IDs, claim IDs, conditions and time scope as optional indexing/context fields, but these fields do not create evidence by themselves. Subagents cannot read or write the parent's notes.

### 2.2 Article collection semantics

Article discovery, reading and use are separate dimensions, not mutually exclusive lists:

| Dimension | Contract |
| --- | --- |
| Discovery | `listed_articles` retains author/date/source scope, pagination and `listing_complete`; missing from a list does not prove nonexistence |
| Reading | `unread`, `partial`, `complete`, `failed`; current-version observed ranges establish actual coverage, not a planned read or successful delegation alone |
| Use | `undecided`, `used`, `excluded`; `used` may identify `primary` or `background` roles |

`planned_articles` is not `used_articles`. The final answer's observed EvidenceRefs and source bindings determine actual use; a main-Agent declaration alone cannot mark an article used. Read-but-unused articles are not mandatory citations. A failed read creates no source-text evidence. These are normative state semantics, not new tool names or a claim that every persisted field has been implemented.

### 2.3 Coverage and stopping

`research.coverage` records answer dimensions, observed EvidenceRefs, contrary/limiting evidence, gaps and the main Agent's next decision. Dimension status is `covered|partial|uncovered`; `decision=continue` requires a concrete `next_need`, while `decision=synthesize` means enough to answer with the stated scope and limitations, not factual proof. Normal summaries use representative samples and disclose scope; explicit all-article or per-article requests retain their completeness requirement. Runtime protects permissions, repetition and synthesis/review budgets, not fixed article counts or a mandatory multi-author path.

## 3. `article_card` payload (A1)

Input:

```json
{
  "article_id": "article_...",
  "question": "What is this author's gold thesis?",
  "depth": "skim",
  "claims": [{
    "kind": "author_view",
    "statement": "The author expects...",
    "quote": "An exact contiguous source quote",
    "line_start": 12,
    "line_end": 12,
    "evidence_id": "evidence_...",
    "conditions": "Optional assumption",
    "horizon": "Optional time horizon",
    "claim_confidence_label": "medium",
    "confidence_reason": "The claim is supported by the article, but independent evidence is incomplete."
  }],
  "limitations": "Optional unread or ambiguous material"
}
```

`depth` is `skim` or `deep`. Both require at least one `article.read` from the current turn and exact, observed source lines for every quote. `deep` additionally requires every line of the article version to have been read; `skim` can cover selected passages. Claims are capped at 50, each quote at 2,000 characters and each claim range at 30 lines. `kind` is one of `author_view`, `objective_fact`, `prediction`, `causal_explanation`, `model_inference`. The runtime generates `claim_id` and records author, title, publication date, article content hash, total lines and observed line count. A card establishes what the article said; it does not validate its market assertions.

An `evidence_id` must cover the exact claim line range. If a long article was read in multiple `article.read` calls, a card using the ID from the wrong segment is rejected; the error names the claim lines and matching observed EvidenceRefs so the agent can correct it without guessing.

An ArticleCard is optional, not an output requirement for every read article. Its stable source metadata includes `article_id`, `source_hash`, `author`, `published_at`, `title`, `source_url`, `question`, `depth`, `line_count`, `observed_lines`, `claims` and `limitations`. Source identity and observed ranges are Runtime-owned; no-relevant-claims is a valid result, not permission to invent content. The exact task determines what a skim should retain.

The existing v1 `article.study(article_id, question, depth, start_line?, limit?)` is an optional one-article model-assisted producer for this card. Its `deep` implementation reads every line in sections of at most 100 lines and sends each section to a separate model call; `skim` requires explicit `start_line` and `limit` (1-100). On this segmented path, deep study must additionally perform an article-level merge of the task-relevant claims, cross-section conditions, time scope, argument relations and limitations. Retain all section EvidenceRefs and save the merged understanding as a linked `research_note`; it does not replace source text. The existing result can include `research_note_id` and `research_note`. A merge failure must remain distinguishable from successful full-text acquisition and completed deep understanding. Fixed 100-line analysis is an inherited v1 mechanism, not the target deep-reading policy; section 3.1 supersedes it as the design direction, without pretending the implementation has changed.

The tool returns `card_id`, source-bound claims and limitations, or `no_relevant_claims` if no claim matches the question. Every returned claim is rechecked against the exact source quote and observed line EvidenceRef before the card is saved. Reading and merge model calls must be visible in Trace. Neither certifies external factual accuracy. A main Agent may also read and analyze directly; use of this producer is not mandatory.

### 3.1 Target single-article handoff

Status: the behavior and handoff principles are confirmed; section 8 now defines their versioned task/result and storage contract. This section retains ownership and reading semantics, not a second competing input schema. Existing source, session/run, task and permission contracts continue to apply. Do not silently reinterpret v1 `ArticleCard`, `AnalystOutput` or `research.synthesize` as the new output.

The main Agent supplies section 8.2's `original_query`, `objective`, material selection and optional reading request, focus and user requirements. Runtime resolves the article version and permitted scope. Skim needs explicit readable ranges or the authorized range-selection mode; deep means the requested full article version, not an arbitrary excerpt. Field and compatibility rules are defined only in sections 8.7-8.8; none is implemented merely by this documentation update.

Suggested result responsibilities:

| Component | Owner and meaning |
| --- | --- |
| Task/source identity | Runtime binds the originating task, article ID, author, title, publication date, source hash and run scope; it must not accept a model-authored replacement identity |
| Reading coverage | Runtime records actually acquired source ranges and source fragments included in successful analysis inputs; acquisition is not proof that a model received or understood the full text |
| `understanding` | Task-focused coherent explanation, freely organized by the model; preserve relevant conditions, uncertainty and distinguish the author's view from model interpretation, without compulsory analytical headings |
| `evidence_links` | Bind important statements or passages in that explanation to observed source EvidenceRefs; retain exact locators and verbatim quotes when supplied. These are selected analytical anchors, not Runtime sentence extraction or a claim for every sentence |
| `limitations` | Model-reported ambiguity, missing relevance or inference limits, plus Runtime-reported acquisition, transport, schema or processing failures kept distinguishable |
| Optional claims/artifact refs | Add fine-grained claims or existing card/note references where useful; no synthesis consumer may demand a full claims rewrite as the only route from this result |

Identity/range validation establishes provenance, not semantic support. Interpretation may cite the premises for a model inference without claiming that the article literally stated the inference. No-relevant-content is a legitimate scoped finding, not proof that the entire article has no relevant content if only part was processed.

The reading policy is full-text-first for a chosen deep/whole-article analysis. Input budgeting counts task instructions, required context, tool/schema overhead when present, output reserve and configured model limits. Several bounded source-read calls may be assembled into one full-text analysis input with their EvidenceRefs intact. If the source cannot fit, Runtime reports the overflow and the main Agent coordinates explicit range tasks and article-level integration; retain the ability to revisit the original sections rather than treating extracted claims as the only remaining text. Do not silently truncate or lower a deep request to skim. Initial input/request limits are in change 004, not inferred from the old 100-line limit or proven provider capacity.

Processing outcomes must distinguish the following situations; these are semantic cases, not new approved enum strings:

| Situation | Required behavior |
| --- | --- |
| Full text processed and an article-level understanding returned | May report processing completion; do not equate it with complete semantic understanding or independent factual verification |
| Text only partly acquired or included in analysis inputs | Report the observed/processed range and missing scope; retain usable analysis without claiming deep completion |
| Segments processed but article-level integration failed | Preserve source-bound section results, disclose missing integration and allow the main Agent to decide whether to recover or answer with limitations |
| No task-relevant content found | State that finding and its actual inspected scope; do not fabricate a card to satisfy structure |
| Transport/schema/model failure | Record the real failure separately, retain valid previous results, apply bounded recovery without inventing content; empty or truncated output is not a completed understanding |
| Source version changed | Mark dependent understanding stale; reread/reanalyze explicitly instead of mixing versions |

The main Agent may inspect, exclude, reread, request refinement or use a result in synthesis. When synthesis uses coherent understandings, inputs retain their task, source/version, dates, scope, evidence links and limitations. A synthesis result must resolve to original source EvidenceRefs through this lineage, not cite an understanding ID as independent evidence. Missing conditions can lead to a concrete follow-up request; no fixed requirement to reread all articles or build every possible relation is introduced.

Saved complete understandings and exact source observations must remain recoverable when compact working views are used. Reuse current run-local isolation and stale-source rules, with section 8's selected versioned artifact mapping. Subagents return results within the parent-child contract; they do not gain direct write access to the parent's store. Repaired structure does not upgrade reading or verification status.

Implemented compatibility: preserve v1 card, comparison and review endpoints alongside an explicitly versioned understanding handoff and consumer. Use `analyst@2.0.0` with task-directed Skills for the optional reading/synthesis loop, alongside the legacy `article.study` tool and `analyst@1.0.0`. Do not silently add fields to strict legacy role schemas, force card generation, replace the old tool or claim that its fixed model passes implement the selected tool loop.

### 3.2 Controlled reading Subagent

Status: the optional read-only tool-loop carrier is implemented through section 8's selected analyst version, task/result contracts and persistence mapping. This section elaborates handoff and safety semantics, not another role schema. Initial numeric caps are configured and offline-tested, not proven provider capacity or financial quality; no separate reader/synthesizer role is required.

| Boundary | Contract |
| --- | --- |
| Parent request | Carry section 3.1's original query, bounded single-article task, article ID, depth and optional focus through the authorized task input; bind it to the existing task ID, parent run/span and child span |
| Input isolation | Expose only needed authorized context and source handles, not the entire parent conversation, unrelated holdings or every article. Article content remains untrusted data and cannot enlarge permissions or change the parent task |
| Tool authorization | Effective access is constrained by the parent task, role/plugin capabilities and run permissions together. The child may choose allowed reads/re-reads but cannot grant itself more article IDs, external access or filesystem paths |
| Full-text-first | When deep reading fits, make the versioned full text available to a successful analysis input, even if acquisition used several read calls. Do not make the child spend one analysis call per read fragment just because a source tool is paginated |
| Local control | The child may return after sufficient analysis or use read tools for a concrete task-local gap; it cannot redefine the parent research goals. Broader source needs are returned to the main Agent unless already authorized within the task |
| Result | Return section 3.1's coherent understanding and evidence-bound envelope, with limits and scoped no-relevance findings. Optional follow-up needs explain a gap; they do not automatically schedule another child |
| Parent ingestion | Verify task identity, source version, actual observations and EvidenceRefs; merge valid child evidence before parent use. Keep each article's understanding distinguishable. A child result or artifact ID is not an independent source |
| Delivery | Only the main Agent publishes the user answer and decides whether to use, qualify, exclude or investigate the result; synthesis and candidate drafting may be delegated under section 4.1. Reading success does not imply market verification or final-answer completion |

Reading and verification have different purposes: the reader can explain the author's reasoning and point out a missing premise without claiming external falsification. If external checking is within the explicitly delegated scope and tools, preserve those actual external observations; otherwise report the need to the main Agent. Neither always-online reading nor a permanent ban on all external material is implied by this role choice.

Recovery follows the existing transport/structure distinction and preserves the source ledger:

- Retry a failed transport request within configured bounds without treating it as a semantic rejection. Do not concatenate partial generations into a successful result.
- Repair malformed returned structure without inventing analysis, changing valid prose or upgrading coverage. A repair call is not another source read or an independent verification.
- At timeout, exhausted budget or failed integration, retain only provenance-valid earlier material with its actual scope and failure status. Importing such material must not turn a failed/partial child into successful `DelegationEvidence`; unresolved or fabricated references cannot enter the parent evidence set.
- The main Agent chooses follow-up, direct reading or qualified synthesis. Recovery cannot silently restart the entire corpus or consume unlimited budget. If it delegates a new task, preserve lineage and do not mix source versions or count repeated imports as new evidence.

Trace must connect the dispatch, visible task/context, source snapshot, tool inputs/observations, actual analysis input ranges, returned understanding, stop reason, source import and parent disposition. Re-reading is optional; record its purpose when used rather than requiring a ceremonial second read. The numeric limit, concurrency, cancellation and durable restart policy are not selected here, and the present process-local runner is not claimed to provide durable recovery.

Existing code to evaluate for reuse: [TaskBrief/SubagentResult](../src/policy_impact/runtime/task_brief.py), [SubagentRunner](../src/policy_impact/harness/subagents.py) and [financial delegation](../src/policy_impact/harness/agent_runtime.py). Reuse of those foundations does not prove they already provide the new body/envelope, full-text assembly or partial-result path. Strict legacy outputs require an explicit versioned integration rather than undeclared extra fields.

## 4. `comparison` payload (B3)

This section describes the inherited card-based v1 interface. Section 4.1 defines the confirmed target integration separately; no existing signature or strict output schema is silently changed.

Input:

```json
{
  "question": "How did the authors' gold views change?",
  "card_ids": ["card_...", "card_..."],
  "relations": [{
    "type": "change_over_time",
    "claim_ids": ["claim_...", "claim_..."],
    "analysis": "The later article changes the stated condition...",
    "qualification": "Optional limit on this comparison"
  }],
  "limitations": "Optional missing period or author"
}
```

2-20 distinct cards must refer to at least two distinct articles. Each relation binds claims from at least two of those articles; relation types are `agreement`, `disagreement`, `change_over_time`, `complementary`. The agent may create multiple comparisons for different questions. The relationship is an analytical proposal, not an objective fact or a claim that author agreement proves truth.

`research.synthesize(question, card_ids)` is the optional model-assisted producer. It receives source-bound claims, conditions and dates from 2-20 cards (not concatenated raw articles). When there are more than 24 claims, a first model pass selects 2-24 question-relevant claim IDs, retaining at least one per article; the complete cards remain reloadable. A second pass proposes relations. Unknown claim IDs are rejected. Proposed relations whose claims all belong to one article are excluded and counted in `dropped_relation_count`. Before saving, a separate relation-audit model pass receives **all** claims from the selected cards, including contrary conditions omitted from the working selection. It may retain, revise the analysis/type, or reject each proposed relation. The result exposes `revised_relation_count`, `rejected_relation_count`, and `relation_audit`; each retained comparison artifact stores the per-relation verdict and reason. This is a reasoned source-card review, not independent market verification. If none remain, the tool returns `no_supported_relation` with counts and limitations. One invocation refuses more than 120,000 characters of card views; split very broad comparisons into bounded thematic groups rather than silently truncating evidence.

### 4.1 Target multi-article synthesis and context handoff

Status: the following behavioral semantics were confirmed on 2026-10-16, including optional delegation of the analysis itself. Section 8 specifies the implemented versioned fields, analyst selection and artifact mapping; initial budgets and offline proofs are in change 004. This section does not remove v1 comparison support or certify live synthesis quality.

The main Agent owns the question, plan, task boundaries, material selection, follow-up decisions and final delivery. It may analyze and synthesize directly, delegate a bounded comparison or synthesis, or request a candidate draft. It need not repeat the full delegated analysis or load every source into its own context to remain responsible. Receiving a result does not automatically mean accepting its conclusions, completing a parent goal or publishing an answer. This decision does not enable recursive child delegation or a fixed hierarchy.

| Handoff | Required semantics |
| --- | --- |
| Task input | Carry the original user question, current comparison/synthesis task, selected understanding/source references and authorized versions, requested scope/completeness and known limits. Optional focus, user requirements or relevant context explain the task without copying the entire parent conversation |
| Visible materials | Prefer complete task-relevant article understandings with their author, dates, conditions, limitations and source links when they fit the actual executor's call budget. Work views and selected source passages may also be supplied; their nature and limits remain explicit. Claims/cards are optional, not an input conversion requirement |
| Authorized expansion | A delegated executor may resolve only explicitly granted read-only material snapshots or handles and permitted source reads. A handle does not grant general parent-run artifact access, cross-run lookup, arbitrary paths, parent-store writes or broader browsing. Broader needs return as concrete follow-up gaps unless already authorized |
| Analysis body | Return coherent task-focused comparison or synthesis, preserving relevant agreement, disagreement, time changes, complements or lack of comparability. Do not require every relation type, a universal table, a claim per sentence or a repeated rewrite by the main Agent |
| Checkable envelope | Retain task/parent-child identity, material and source versions, important evidence links, actual input scope, considered/unprocessed/excluded material references and reasons where applicable, limitations and processing outcome. Candidate prose is optional and is not published directly by the child |
| Parent ingestion | Runtime validates identity, granted scope, versions and observed evidence, imports valid evidence and retains complete valid results through the approved artifact mapping. The main Agent decides use, qualification, follow-up or exclusion and handles material gaps or disputes |

These requirements apply whether synthesis runs in the main Agent, a tool or an authorized child. Existing parent-note isolation in section 2.1 remains the v1 behavior: the target material handoff needs an explicit versioned input/resolver, not a bypass through the parent's general artifact `get` or `list`. No new read permission is implemented by this text.

Coverage must distinguish three facts:

| Dimension | What establishes it | What it does not establish |
| --- | --- | --- |
| Source acquisition | Runtime-observed ranges of a specific article version | The source reached an analysis model or was understood |
| Task analysis | Source ranges included in successful analysis inputs and a valid task result, with partial/failed scope kept explicit | Semantic completeness or independent factual truth |
| Synthesis consideration | Executor-declared considered, unprocessed and excluded inputs, linked to actual visible inputs or authorized expansions and scoped reasons | Runtime proof that all important meanings were considered, or a requirement to cite every considered article |

The Agent supplies relevance, exclusion and analytical judgments; Runtime validates their bindings and records them without certifying their semantic completeness. Per-article result completion alone cannot mark corpus-wide synthesis complete. Explicit all-article requests require all material in the requested corpus boundary to be accounted for at the agreed reading depth and in synthesis consideration, including justified exclusions or honest gaps. Representative sampling remains allowed when consistent with the question and disclosed. These are coverage responsibilities, not a new set of mandatory state enums, fixed article counts or new success gates.

Keep three distinguishable layers: exact source observations, complete article understandings and current synthesis working views. Saving a full result is not offloading it or proof of active visibility. Do not force an extra summary when complete understandings fit; do not force clearing existing original text or duplicating every child's source text into the parent. Under actual pressure, the main Agent may prepare or delegate a question-focused working view. It retains upstream understanding/source references and versions, scope, important conditions, contrary material, limitations and pending expansion needs; it must not be the only surviving copy or a one-sentence-per-article requirement. The child's own input budget and restore permissions apply, not just the parent's.

A working view is a derived analytical aid, not independent evidence. Important synthesis statements trace through the actual understanding/view inputs to observed original EvidenceRefs; evidence links can also be direct where appropriate. The same lineage must remain available to final-answer audit without requiring every intermediate result to appear in the final prose. Runtime checks provenance and reference integrity, not entailment, author agreement or absence of omissions. Undeclared or unresolved dependencies remain gaps rather than being counted as verified use. Considered materials are not automatically final `used_articles`.

When a condition or apparent change is unclear, the executor may expand an understanding, reread relevant original passages or return a concrete follow-up need. External checking follows task need and authorization; it is neither automatic nor a compulsory step for every article. Do not claim original-text checking from repeated summaries alone. Source edits invalidate dependent interpretations and working views rather than silently mixing snapshots. These are target semantics, not evidence that existing v1 storage already tracks every dependency.

Transport/schema recovery follows section 3.2: preserve provenance-valid partial analysis and actual scope, do not join incomplete generations or upgrade failed processing to successful synthesis, and let the main Agent decide recovery or qualified delivery. Disputes and ordinary verification failures preserve useful safe analysis under section 5. A failed synthesis child does not itself mean the whole user task has no deliverable result.

Trace must connect the assigned question/task, executor and parent-child identity, material references/versions, successful analysis inputs, any view creation/offload/expansion and source rereads, returned analysis and coverage declarations, evidence import, parent disposition, follow-up and final delivery. The logs distinguish acquisition, analysis and synthesis scope without exposing hidden reasoning. No pressure means no compulsory offload; no concrete gap means no compulsory reread. Events are defined in section 8, initial thresholds in change 004; concurrency and durable scheduling remain separate designs.

## 5. `review` payload (D*)

Input:

```json
{
  "card_ids": ["card_..."],
  "checks": [{
    "claim_id": "claim_...",
    "claim_type": "objective_fact",
    "status": "insufficient_evidence",
    "evidence_refs": ["evidence_..."],
    "conflicts": [],
    "alternative_explanations": [],
    "suggested_revision": "Qualify this as the author's claim pending data.",
    "as_of": "2026-10-08",
    "reason": "What was checked, what the evidence shows, and remaining uncertainty"
  }],
  "limitations": "Optional unchecked scope"
}
```

Statuses: `viewpoint_faithful`, `supported`, `refuted`, `insufficient_evidence`, `not_due`, `reasoned`. Every check retains the article claim's original EvidenceRef. `supported` and `refuted` are restricted to checkable facts or due predictions and require a separately read public or market source available by `as_of`; reopening the very same article URL does not count. `not_due` requires a parseable future forecast horizon. An unparseable prediction horizon cannot be called `not_due`, `supported` or `refuted`. Causal explanation or model inference may be `reasoned`, not certified as facts. A conflict must cite an EvidenceRef already included in the check. The validator checks identity, date, source type and provenance, not semantic entailment or publisher independence. `suggested_revision` records the retain/qualify/change/remove proposal; external disagreement cannot silently erase a faithfully attributed viewpoint. `review_scope` is recorded as `main_agent_recorded` so it cannot be confused with independent verifier output.

The verification checkpoint is mandatory after substantive analysis, but the concrete method is chosen by the main Agent. It may be a direct comparison with already observed article lines, a public-page or market-data check, a controlled Subagent, `research.verify`, or an explicit decision that an attributed viewpoint/definition does not need external verification. The main Agent records `research.checkpoint(checks, summary?)` after analysis. Each check has `claim`, `decision` (`check|not_needed`), `outcome` (`supported|refuted|inconclusive|not_applicable|not_due`), `method`, `evidence_refs`, `reason` and `gap`. Runtime validates observed EvidenceRefs, requires a gap for inconclusive checks, and records `verification_decision`, `verification_result`, and `verification_gap` trace events. `not_due` is reserved for a checkable forecast whose outcome is not yet observable; its reason is retained as the gap when no separate gap is supplied. New evidence invalidates the checkpoint until the Agent records an updated decision. This checkpoint records the Agent's judgment; it is not an independent verdict or proof of semantic truth. `research.verify(card_ids, claim_ids?, evidence_ids?, as_of?)` remains an optional structured producer: it checks selected claims against supplied observed EvidenceRefs, preserves the typed result in a `review`, and returns follow-up queries for missing sources. It does not itself browse or certify independent source acquisition. The runtime computes each claim's `forecast_horizon_end`, `forecast_due_state`, and `allowed_statuses` and supplies them to the model. An invalid status or a `checks[].as_of` differing from the requested research date is rejected before saving. The model cannot cite an evidence ID that was not in its input. A separate model call is not an independently browsing subagent; the final trace must distinguish the two.

`research.plan` appends a Runtime-owned `runtime_delivery` goal on creation (and when loading a historical plan). The main Agent may finish or block research goals but cannot update that goal; an attempted update returns a recoverable tool-argument error directing it to submit a final answer instead. After the terminal publish decision, Runtime sets `delivery_status=done` whenever a safe user-visible result is delivered, including a complete answer, a qualified answer, or source-only material. It sets `delivery_status=blocked` only when no safe result can be delivered because of a model, tool, gate, provenance, or permission failure. `delivery_mode` records `full`, `qualified`, or `source_only`; delivery status does not certify factual correctness. If the finance turn fails before plan creation, Runtime writes a delivery-only plan using the same actual-publication rule: done for a delivered safe fallback, blocked if no safe result was delivered. `research_pending_count` and `delivery_status` are distinct: zero research pending does not mean the user received an answer. At every terminal delivery decision, remaining pending research goals become blocked with the stop reason and their previous progress retained in `interruption`; already done/blocked research goals are unchanged. This also applies when source-only material or a qualified candidate was delivered: publishing a message does not certify unfinished research, and Runtime must not guess that an unrecorded goal was completed. The per-run `research_plan.json` retains the final delivery status. `research_delivery` is emitted after the terminal commit and therefore follows `run_stopped` in Trace.

Tool action/observation Trace binds `AgentTurn.tool_call_id` to the exact gateway `ToolCallRecord.call_id`, including failed, timed-out or permission-blocked invocations. This nullable trace-only field is not added to model-visible observations or public action parameters. A pre-invocation contract/plan/budget rejection with no gateway record retains a null ID and no fabricated `tool_calls`. Finance result events resolve by ID, never by the ordinal of successful observations or by tool name alone; errors retain failed status rather than being paired with a later success. Main model events retain the same `turn_index/tool_call_id` association. An internal plan-recovery call is not the rejected action's call. See change 009 section 6 for the frozen defect and post-batch regression boundary.

Before publishing a substantive answer with read finance evidence, a final Hook checks the main Agent's selected `review_targets`, source bindings and delivery safety. The target is an important claim or reasoning node, not every sentence mechanically extracted by Runtime. A normal metadata or background statement does not require the same semantic audit as a material forecast or causal assertion. The existing `AnswerAudit.call(candidate_answer, question, personal_context, review_targets=...)` interface supports this target-oriented path; a legacy caller or model omission must not be presented as compliance with the selected-target design.

Each review target retains `target_id`, `type`, `scope`, optional `relation_type`, `claim_or_question`, `article_refs`, `evidence_refs`, `review_question`, `external_check_needed` and `importance`. Types follow section 5.1. Scope is `article|cross_article|external|personal`; importance is `core|supporting|context`. `external_check_needed` is the string enum `yes|no|undecided`, as defined by `FinanceReviewTarget` in `src/policy_impact/skills/schemas.py`, not a boolean. The main Agent selects material targets according to the question; Runtime validates structure, actual source access and evidence identity, not financial relevance by fixed keywords.

The `finance_answer_audit.v1` result retains source-bound `checks[]` (`line_start`, `line_end`, `claim_type`, `action`, `evidence_refs`, `reason`, `suggested_revision`), canonical `answer_audit_coverage`, legacy `coverage_complete` only as a read-compatible alias, `used_source_ids` and `limitations`. Range/quote anchors locate an already chosen target; they do not require all answer sentences to become targets. `answer_audit_coverage` measures important-target review coverage, not whether the research scope is complete. Source metadata and evidence bindings remain deterministic identity checks.

Explicit citations, author/date/path anchors and actually used EvidenceRefs determine `used_source_ids`. Unmentioned articles read during research do not create a coverage defect. Cross-article checking must preserve the relevant author, date, condition and contrary evidence; independent calls or author agreement do not prove external factual support. Each candidate records `answer_claim_audit` with checksum, targets, coverage, actual sources, issues, revisions and limitations. The Hook is a traceability and safety check, not a semantic truth guarantee.

Bounded batching may be used for a chosen target, but a four-line or character-count chunk is not itself the unit of financial meaning. Explicitly labeled, evidence-supported model analysis need not have been voiced by a source author; important omitted conditions or misleading certainty still require revision. The pre-migration contract records the old chunk/selector heuristics as implementation history, not mandatory research methods.

When a selected target synthesizes different articles, its review binds the participating article claims, actual dates/conditions and observed EvidenceRefs. A missing statement in one passage is not proof that no other observed source supports it. Retain supported counterexamples and unresolved disagreements without requiring every candidate article to appear in the answer. If `cross_review` or a controlled Subagent is used, preserve actual scope, source reads, objections and disposition in Trace; absence of that specific plugin is not by itself a design failure. Independent answer-quality review remains part of live acceptance.

The selected-target implementation retains each chosen EvidenceRef's complete observed excerpt, subject to the actual request input budget, rather than applying legacy keyword/number passage selection. Explicit refs are supplemented with observed excerpts for participating `article_refs` not yet represented; an explicit article range is not automatically expanded. Unobserved participants, incomplete source text and the existing 12-source-per-target bound remain visible coverage gaps. Whole-audit and successful per-target caches bind the answer, question, target, personal context and actual source payload; changes invalidate reuse. Returned records are independent copies, with `cache_hit` and `cached_target_ids` identifying reuse in the audit Trace. Failed/partial model responses are not cached as successful checks. Completed target checks survive another target's transport, structure or budget failure; no valid check means failed coverage. An explicit empty selection is `review_status=not_requested`, not proof of verification or a completed separate semantic review.

Final-audit eligibility follows the merged current-run EvidenceLedger's observed article/page/document/market/Wiki fact sources, including legally ingested child observations. It does not require the main Agent to repeat a child's source read. An `article_catalog` observation alone remains metadata and does not trigger this semantic-audit path.

When current finance chat omits `review_targets` or supplies a non-list top-level value, the same main model performs one bounded metadata-completion decision under `finance_review_targets.v1`. The output is strictly `review_targets` (at most 40 `FinanceReviewTarget` objects) and nonblank `selection_reason`; an empty list needs a reason. The input preserves the candidate answer, original question and an identity/range catalog of existing source observations, not another concatenation of all article text. The catalog is for target binding, not proof; actual review receives the selected observed text. Completion cannot request tools, add sources or rewrite prose. The original-schema correction (at most two attempts) and shape-only `finance_review_targets.normalize.v1` pass (at most one attempt) share the existing logical-decision/root budgets. `answer_review_target_selection` records the candidate checksum and selected targets/reason or failure; completion is not itself a semantic audit. A failed completion immediately preserves safe complete analysis with failed review metadata, rather than repeatedly triggering legacy whole-answer audit. The old API's omitted/None compatibility remains available only to explicit old callers, not as the current chat fallback.

Review and repair attempts are bounded and recorded. Repeated objections to the same target may be grouped while retaining their reasons. An accepted candidate may be delivered as-is. Insufficient evidence, a reviewer failure, incomplete review or a dispute does not automatically discard the complete analysis or force `source_only`: retain the complete safe draft or its safe parts, qualify unverified claims and present the author view, contrary evidence, review status and unresolved gap separately. Only provenance, permissions or other actual safety defects restrict affected content. `source_only` remains available when no analysis can safely be retained; `blocked` means no safe user-visible result can be delivered. `answer_review_resolution` records what was retained, restricted or disputed and why. The inherited default attempt count is not evidence that review succeeded.

For malformed structured fields, first provide the original model with the concrete schema errors while preserving the answer body. On continued failure, use the bounded `review.normalize` repair capability with the existing draft and evidence; it may repair structure, not invent claims, change prose or claim independent factual verification. If repair fails, retain the safe analysis and record incomplete review. Transport failures retry the original request separately; they are not schema failures. These are required recovery semantics; the concrete repair implementation and all failure branches require their own compliance tests.

Before final claim audit, Runtime also checks arithmetic consistency when one answer line states a market start price, end price and period return. A mismatch returns a repair request to the main Agent; it does not infer a new market observation. In particular, a first in-period quote must not be paired with a percentage computed from an earlier baseline close without an explicit separate label. This is a generic numerical consistency boundary, not a mandatory analysis path.

### 5.1 Unified state and type semantics

The following fields are canonical and describe different dimensions of one run:

```json
{
  "delivery_status": "done",
  "delivery_mode": "full|qualified|source_only",
  "verification_status": "verified|partial|disputed|failed",
  "evidence_coverage": "complete|partial|uncovered",
  "answer_audit_coverage": "complete|partial|failed",
  "trace_contract": "pass|fail",
  "semantic_review": "complete|partial|failed"
}
```

`delivery_status` only answers whether safe user-visible material was delivered. It does not certify correctness. `evidence_coverage` describes whether the user's research scope was covered; `answer_audit_coverage` describes whether material claims in the final answer were audited. A run may therefore be `done + partial`, and complete research coverage does not imply a correct causal conclusion.

The finance loop's six terminal fields must survive the general-chat wrapper, SkillResult, public response and Run metadata without being replaced by successful-delivery defaults. Missing verification/coverage metadata cannot certify success: a delivered answer defaults to qualified/partial, while no delivered answer defaults to source_only/failed/uncovered. The per-run plan persists its `delivery` object and restores it on load. At an interrupted terminal checkpoint, unfinished pending research goals become blocked with the actual stop reason while preserving prior progress notes; already done or blocked goals are not rewritten. This is interruption bookkeeping, not a Runtime judgment that the financial research was completed.

An interrupted goal may carry Runtime-owned `interruption: {reason, previous_note}` to retain the original progress note without truncation; its bounded `note` remains a status summary. This field is persisted and restored with the goal, not required from the model.

`coverage_complete` is a legacy read-compatible alias for `answer_audit_coverage` only. It must not be used as an alias for `evidence_coverage`, and new writes must use the explicit field name.

`trace_contract` describes the completeness of required events and source relationships. `semantic_review` describes review execution; completion does not imply a `supported` verdict. Claim-level outcomes such as `supported`, `refuted`, `viewpoint_faithful` or `reasoned` must not be substituted for these run-level fields. These are semantic dimensions; they do not imply a new database table or new mandatory final-answer keys without an approved implementation change.

Claim types are limited to `author_view`, `objective_fact`, `prediction`, `causal_explanation`, `model_inference` and `personal_context`. Cross-article synthesis is not a claim type. It is represented by `scope="cross_article"`, an optional `relation_type` of `agreement`, `disagreement`, `change_over_time` or `complementary`, and a claim type describing the resulting assertion. A model-generated comparison is normally `model_inference`; it must not be presented as an objective fact merely because multiple authors agree.

Research confidence uses `claim_confidence_label` or `research_confidence` with `high`, `medium` or `low`, plus `confidence_reason`. Wiki or other fact records may retain numeric `fact_confidence` values in their own schema. The bare field `confidence` is deprecated for new research records; it must not silently mix numeric fact confidence with a semantic research label.

### 5.2 Review convergence and candidate checkpoints (change 008, option A)

The user approved option A on 2026-10-17. Preserve all section 5.1 enums. Separate actual audit execution/input coverage, the target's support verdict and the delivery disposition. `limitations` is explanatory text, not an automatic failure switch. A completed `insufficient_evidence` result stays inconclusive; `viewpoint_faithful`, `reasoned` and `not_due` are not independent factual verification. Genuine missing participants, truncated inputs or missing/invalid results keep coverage partial/failed. Source authenticity and permission checks are not relaxed.

The main Agent may return optional `review_disposition=continue|deliver_with_limits` (default `continue`) with a specific `review_resolution_reason` when choosing delivery despite unresolved review. This never bypasses the initial audit or local provenance checks. When problems remain, Runtime returns target-specific feedback and this choice rather than mandating three whole-answer rewrites. Ordinary audit transport failure or exhausted shared budget delivers an eligible complete candidate with limitations. Identical unresolved inputs are reused or reported as no progress; new IDs do not reset shared budgets. An empty target list cannot erase previously unresolved issues.

For optional incremental review, a target may include `answer_context`, a list of verbatim, unambiguous candidate spans retaining the full semantic unit, conditions and qualifications chosen by the main Agent. Runtime supplies the containing paragraphs and adjacent context; missing/ambiguous spans fall back to the complete candidate. Cache keys bind the actual review text, target semantics, question, personal context, source contents/versions and audit contract. Without a reliable bounded context, use the full candidate conservatively. Renaming a target is not new evidence. Changes in any relevant dependency invalidate reuse; ordinary paragraph order changes alone need not. Input coverage details and completed/unchecked target IDs are retained with the audit result. Metadata contradictions receive at most one metadata-only clarification within the shared budget; failure is disclosed, not converted to a passing check.

Selected-target results add `checked_target_ids`, `unchecked_target_ids` and `coverage_detail.targets[]`. Each target detail records requested/available/provided article refs, required/provided/checked/unchecked/missing/truncated evidence IDs, actual execution status, gap types, context scope, input signature and cache/history information. A target check's Runtime-owned `fact_verified` is true only for a completed supported objective fact with a bound non-author public/official/document/market source; it is not supplied by the model and does not itself prove source independence or semantic truth. Metadata clarification uses `finance_review.coverage_metadata.v1`, returns coverage/limitations only, and cannot manufacture missing checks or alter verdicts.

Runtime-generated invalid-target, failed-call and missing-check diagnostics may carry `issues[].issue_kind="review_execution_gap"`. This internal optional tag is not accepted in the model's financial check Schema. It identifies an unchecked target, not a refutation: complete-candidate delivery lists the target and cause under incomplete review rather than substantive disputes. Execution gaps still prevent factual verification and retain their actual coverage/failure state. Untagged, unknown-kind and historical objections remain conservatively visible as disputes; a later failed call cannot erase them. The tag and detailed `coverage_detail.targets[].gap_types` survive candidate history and terminal fallback; no reason-text guessing or financial claim reclassification is performed. Legacy rendering without candidate preservation is unchanged.

`finance.answer_candidate.v1` is an internal Runtime-owned immutable checkpoint, not a new model tool or evidence source. It records `candidate_version`, `parent_version`, `answer_checksum`, complete public `output`, source snapshot hashes and local-check outcome. Associated immutable review/selection events retain coverage, checks, unresolved issues, invalidation and selection reasons. Files live under the existing run directory; failed saves have no recoverable checkpoint path. In-memory valid candidates remain usable with the storage gap disclosed. No incomplete JSON, hidden reasoning or partial stream is a candidate.

On any terminal interruption Runtime selects a still-eligible candidate, rechecks local provenance and source snapshots, and carries forward applicable unresolved issues, even from newer rejected drafts. If applicability cannot be reliably mapped, disclose the issue at document scope rather than silently clear it. A completed reassessment can close an objection only when its actual supplied context includes the disputed passage. Switching `answer_context` to unrelated text is not resolution. If a rewritten passage cannot be mechanically mapped, reassess that target with full-answer context. Nested and flattened target diagnostics use the same task identity; adding observed evidence to repair an input gap is allowed, but narrowing participating articles does not silently resolve the original gap. Completed targets may close their own issues within a partial batch; unchecked targets may not. A candidate with a known concrete local safety defect is ineligible until corrected; a disclaimer is not a repair. Independent-review provenance is checked before normal publication and when selecting terminal fallbacks. Missing requested review or its disclosure alone is not fabricated provenance: preserve an otherwise safe complete candidate and attach the actual incomplete-review status at terminal delivery. Explicit fabricated completion declarations remain ineligible even with an unrelated general disclaimer. A newer invalid candidate cannot destroy an earlier eligible one. If no candidate is eligible, preserve only genuine observations or block when nothing safe can be delivered. This does not provide crash recovery, transactional scheduling or exactly-once delivery.

Trace uses `answer_candidate_checkpoint` for checkpoint/save results and `answer_candidate_selection` for eligibility/selection, alongside existing `answer_claim_audit`, `answer_review_resolution` and `research_delivery`. Audit events identify the version/checksum and actual sources; they do not certify financial truth. Delivery remains recorded after commit. First-phase acceptance covers review/delivery; context working-view and legacy analyst notes improvements remain a separate second phase.

## 6. Context and trust boundaries (E*)

The target full-understanding and working-view behavior is defined in section 4.1. Change 006 replaces the inherited absolute newest-four protection with budget-aware observation offload. Thresholds are configured engineering limits, not any model's optimal context size. Versioned delegated material access is defined separately in section 8; same-run `context.open` does not grant a child access to the entire parent store.

[Change 009](changes/009-context-capacity-and-v1-baseline.md) supersedes change 004's initial numeric input defaults. `ResearchLimits.input_tokens/context_tokens` default to 1,000,000; the effective project ceiling is at most 1M and smaller explicit limits win. `context_tokens=0` still means unknown provider capacity, not unlimited input. `input_limit(requested_output)` deducts output and the 4096 safety reserve from the bounded window, also honoring the separate input quota. `context_pressure_percent/context_target_percent` default to 70/60 and require `0 < target < pressure <= 100`. The finance main loop computes thresholds with its output cap; if the safe input boundary clamps the trigger, reduce the target proportionally to retain hysteresis. Trace retains raw limits, effective ceiling/input, trigger/target, estimator, `capacity_source=configuration` and `capacity_verified=false`. This is not GPT relay capacity certification. Generic `AgentLoopConfig.context_target_tokens` is optional, requires a larger pressure threshold, and preserves the legacy 75%-of-trigger target when omitted. Offload events add `target_tokens`; exact storage, permissions and restore behavior are unchanged. No new model summarization or waiting/retry policy is implied.

Source article versions and `EvidenceLedger` entries are the provenance layer. Cards, comparisons and reviews are derived working state, kept separate and reloaded by generated IDs. `list` returns summaries rather than concatenating article bodies; `get` loads one selected record. `article.read` remains the path to exact source text. There is no default compression or batch concatenation. The existing analyst delegation can handle bounded article batches; child evidence must be merged and audited before use. A source edit invalidates cards and any comparison/review depending on them.

The finance main AgentLoop estimates active context before each call. Above the configured pressure threshold it first offloads older tool observations, then larger recent observations if necessary; it does not replace small results with larger markers. Each exact original is saved atomically under `<company>/runs/<run_id>/context_observations/context_<hash>.json`; the conversation receives a generated ID, checksum, tool name and restore instruction. `context.open(context_id, offset=0, limit=None)` verifies the run-local ID and checksum. Small unpaged records retain the exact v1 object. Large or explicitly paged records return `run.context_view.v1`: context_id, original checksum, tool_name, offset, returned_chars, total_chars, text and next_offset. Text is a continuous slice of the serialized original content; its view budget is at most one quarter of the configured input limit, and limit is capped at 12,000 characters. No paths or cross-run IDs are accepted. The trace records thresholds, before/after estimates and IDs. Restoring the same full record or identical page repeatedly adds an advisory after the second restore; reading distinct successive pages is not a repeated restore. The advisory alone does not block restoration.

If repeated restoration of the same full record or identical page is accompanied by a *new* context-pressure offload, the finance loop enters `context_progress_required` checkpoint. Reading distinct successive pages does not activate this repeated-restore checkpoint. `context_progress_gate` records activation, denied `context.open` and release. Progress can be a new source-bound v1 `research_note` with entries, a new validated/committed v2 understanding/synthesis/view from main save or child ingestion, or a changed plan goal from pending to done/blocked with a concrete note (at least 20 characters). A truthful final answer remains possible. Failed writes, duplicate IDs, reading existing records, plan `get` and empty/unchanged updates do not release the pause; no forced legacy note is required for v2. The third denied `context.open` in one checkpoint ends as `gate_blocked`; unrelated tools do not reset the count. After release, a fresh restore/pressure sequence is needed to reactivate it. This does not cap article count, automatically summarize sources or choose an analysis workflow. Only the active model-facing copy of earlier observations is swapped; original text, evidence and saved research records remain unchanged. Estimates are not exact provider token counts; restore when exact details matter.

For every substantive answer, final guards validate article-line citations and any declared subagent review subject to the architecture's autonomous-delegation and safe-delivery rules. A stored card does not bypass source validation. `research.artifact` never reads arbitrary local files; company/run scope is constructed by Runtime, article IDs are resolved through `ArticleCorpus`, and symlink artifact paths are rejected.

An explicit source boundary in the user question is represented by `FinanceResearchGateway.source_scope`. `local_only` denies web/market tools for the main AgentLoop and delegated finance readers. A request naming discrete article dates filters `article.list` and denies `article.read`, `article.study`, or `article.claims` for other publication dates; an explicit date range is not treated as a discrete whitelist. These are user-request scope constraints, not an automatic choice of analysis method. The scope is visible in the research trace and must be tested against natural-language paraphrases before broadening the parser.

### 6.1 Runtime stability boundaries

- Article time scope is not market intent. Temporal qualifiers such as recent, this month or this year do not by themselves require market tools when an asset is mentioned. Explicit requests for prices, returns, trends or market-fact checks retain the existing evidence boundary. A grounded no-article answer may be delivered without collecting unrelated quotes.
- Transport failures retry the current request within the configured attempt limit, without appending JSON-repair instructions. Schema repair is reserved for an actual response that violates the output contract. Trace distinguishes transport failures from schema failures.
- OpenAI-compatible `finish_reason=length` is handled as `max_tokens` truncation, including empty content. A truncated response cannot be published as a complete action or final answer even if its partial JSON parses. Retry exhaustion retains observed sources and an explicit incomplete-delivery status.
- Model-call `attempts` records the actual count supplied by the adapter; unknown counts remain null. `max_attempts` is the configured limit, not a substitute for observed attempts. Passing code regression, successful model transport and passing financial-answer acceptance are separate claims.

### 6.2 Optional model-side SSE transport

`AGENT_WORKBENCH_MODEL_STREAM=true` enables SSE on OpenAI-compatible Chat Completions or Responses. It defaults to false outside named profiles. `AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS` defaults to 600 and accepts 1-900 seconds. The model timeout bounds connection/idle reads; comments/heartbeats do not reset elapsed time. Each OpenAI socket read is tightened to the remaining response duration and any finance run deadline, including nonstream reads. The inherited Anthropic adapter keeps its existing scoped behavior. System DNS and native blocking calls are not hard-cancellable; do not promise a hard wall-clock cancellation SLA. An event is capped at 1 MiB and a whole response at 8 MiB. Configuration is inherited by copied finance/child adapters and exposed without secrets.

Request: existing model/messages/temperature/max_tokens plus `stream=true`, `stream_options.include_usage=true` and `Accept: text/event-stream`. Response path: UTF-8 SSE events -> JSON data -> choice 0 `delta.content` accumulation -> finish reason and `[DONE]` -> existing normalized `ModelResponse` -> unchanged JSON/action schema and evidence checks. Reasoning deltas are not answer content. No fragment is published or executed as a tool action. A complete transport does not itself mean the model output or financial assertions passed review.

EOF without both completion signals, corrupt events and disconnection discard that attempt's buffer and retry the same request within the existing bounded attempts/backoff. Attempts are never concatenated. `length` remains truncation even after `[DONE]`; authentication failures remain non-retryable. There is no silent non-streaming fallback or proxy bypass. Success metadata includes `transport`, `stream`, `stream_complete`, `stream_event_count`, `stream_content_chars`, `time_to_first_token_ms`, `stream_duration_ms`, `stream_max_seconds`. `transport_attempts` retains per-attempt diagnostics on success and failure, including actual attempt numbers, but no partial content or credentials. Both main-loop and direct article/audit model failures retain these diagnostics in Trace.

Scoped research calls also retain `failed_public_responses` for fully received, non-truncated responses that fail JSON/Schema validation, on both eventual success and failure. Each record includes attempt, response ID, schema, stop reason, credential-redacted public text, original character count, retained count and a truncation flag; diagnostic text is capped at 65,536 characters per attempt. Stream fragments and protocol reasoning fields are excluded. These diagnostics never execute an action, certify a result, add evidence or extend retries. The main-loop Trace action list is derived from the shared action contract, not a separately hardcoded subset. See [change 004 section 8.1](changes/004-versioned-research-implementation.md#81-验收发现的记录缺陷与修复边界).

Contract tests cover framing, UTF-8, heartbeats, multiline data, usage tails, missing completion signals, disconnection/retry without buffer contamination, duration/size limits, redaction, configuration and non-streaming compatibility. Live no-author, single-article and multi-author checks remain separate business acceptance; receiving streamed tokens alone is not a pass.

### 6.3 Named model profiles

User-confirmed on 2026-10-17; see [change 005](changes/005-model-profiles-and-revalidation.md) and the optional transport addition [change 007](changes/007-responses-transport-and-delivery.md). `config/model_profiles.json` uses `schema_version=finance.model_profiles.v1` and a `profiles` map. Each entry has `display_name`, `model_id`, `base_url`, `protocol=openai-compatible|openai-responses`, `auth_env`, `stream`, optional nullable `thinking`, and optional `reasoning_effort=low|high|max` (only with thinking enabled on Chat Completions). Responses profiles reject non-null DeepSeek thinking/effort settings. Unknown fields, malformed URLs, non-boolean switches and unknown profile names fail before execution. Credentials are environment references only, never literal values in this file.

`run_finance.py` and the opt-in live acceptance runner accept `--model-profile`. CLI selection follows local environment loading and overrides `AGENT_WORKBENCH_MODEL_PROFILE`; a selected profile owns its endpoint/model/protocol/auth route and does not inherit another provider's credential. Missing selected credentials make that profile unavailable. When no profile is selected, retain the legacy environment path. The acceptance runner rejects combining `--model-profile` with `--model`. The active process selects one configuration; frontend model editing, per-session selection, automatic fallback and in-flight switching remain unsupported.

DeepSeek uses `deepseek-flash` at `https://api.deepseek.com`, `DEEPSEEK_API_KEY`, SSE, explicit thinking enabled and high effort. Its request omits temperature in thinking mode. GPT keeps the existing endpoint/auth route and does not receive DeepSeek-specific parameters. OpenAI thinking null means provider default, not explicitly disabled; the legacy Anthropic adapter still disables thinking. Only public content enters action parsing and Trace. Model metadata records profile/requested/returned model and thinking settings without credentials; independent reruns under another model get separate sessions, batches and evidence.

### 6.4 Optional Responses text transport

The opt-in `gpt-responses` profile uses the same model, base URL and credential variable as `gpt`; existing defaults are unchanged. `ResponsesModelAdapter` shares HTTP handling, deadlines, physical-request budgets, retry/repair and action validation with the existing OpenAI adapter. It posts `input` text messages, optional `instructions`, `max_output_tokens`, `temperature`, `stream` and `store=false` to `/responses`. No `tools`, remote conversation, previous-response linkage, background execution, automatic endpoint fallback or provider switching is used.

JSON must carry a valid terminal response ID and `status=completed|incomplete|failed`. SSE requires a matching `response.completed|response.incomplete|response.failed` event and response status; `[DONE]` alone is not completion. Public text comes only from assistant message `output_text` parts, never reasoning or native tool items. Text deltas, when present, must match the terminal output item IDs, indices and text. Refusal, content filtering, malformed/missing terminal data and inconsistent IDs/sequence/text do not yield an executable action. An `incomplete/max_output_tokens` response maps to the existing truncation branch, even if partial JSON would parse; completed but empty public text maps to the existing `empty_public_response` correction branch. Permanent provider rejections do not receive format correction; transient HTTP/provider failures keep existing bounded retry rules. Each attempt's partial buffer is discarded, never joined to another attempt.

### 6.5 Transport diagnostics and acceptance

Both OpenAI adapters expose `api_style=chat_completions|responses`, `response_complete`, `completion_state`, optional `incomplete_reason` and `response_id` alongside existing per-attempt diagnostics. `response_complete=true` means a valid terminal outcome was received, not that it was successful: truncation/failure are distinct terminal states. Chat JSON also requires `finish_reason=stop|length`; unknown/missing/native-tool/filtered finish reasons do not count as a normal text answer. No stream fragments or reasoning are stored in diagnostics. A fully received candidate that passes existing source checks remains eligible for qualified delivery if later review fails, unchanged from change 006.

`scripts/finance_transport_comparison.py --live` performs paired, bounded, synthetic-only single-request probes; it never executes proposed tools or assigns financial quality scores. It records raw transport completion, public-action validity and requested-output checks separately for short text, long input, long output, JSON actions and complete-context multiple turns. Repeated same-model probes precede business Query checks on independent frozen snapshots. Results and omissions are reported separately; no default switch or production reliability claim follows automatically. See change 007 for the test matrix and stopping policy.

## 7. Verification

`tests/test_finance_research_artifacts.py` exercises source-line and exact-quote binding, deep/skim coverage, comparison references, typed review restrictions, ID reload, stale article invalidation, run isolation, schema-repair behavior and denied subagent access. `tests/test_finance_answer_audit.py` checks final claim validation, source sufficiency, cross-passage quote anchors, unsupported IDs, forecast boundary correction (including mislabelled facts, method discussions, negation and historical comparisons), empty-selector fallback, slash/full-Chinese date rows, and corpora above twelve sources with per-source call coverage. `tests/test_agent_loop.py` checks pressure offload, exact restore, cross-path rejection and tamper detection. `scripts/finance_artifact_acceptance.py` runs live A/B/D passes, including typed checks of claim IDs used in the comparison. `scripts/finance_acceptance.py` runs or replays live chat answers and can replay a historical answer's cross-review. No live-model quality claim follows from contract tests alone; single-article, author-summary and multi-article time-change end-to-end runs are required to judge research quality.

## 8. Versioned research integration contract

Status: accepted on 2026-10-16 and implemented as an optional path under [change 004](changes/004-versioned-research-implementation.md) on 2026-10-17. Strict schemas, explicit analyst v2 registration, scoped execution, persistence, main-Agent plugin integration and offline regression are present; live provider capacity and financial quality are not validated. Sections 3.1, 3.2 and 4.1 retain behavioral/ownership rules; concrete fields and compatibility are defined here only. Initial budgets are in change 004; durable scheduling, cancellation and cross-run reuse remain outside scope. Do not inject this entire document into the research Agent.

### 8.1 Existing foundations and selected reuse

| Existing implementation | Selected reuse and remaining work |
| --- | --- |
| `TaskBrief`, `SubagentResult`, parent/child spans and `SubagentRoleRegistry` | Keep generic task identity, execution limits and stop reasons; explicit version selection now preserves `analyst@1.0.0` and routes `2.0.0` separately |
| Task-bound `artifact_read` and `TaskArtifactResolver` | Supply allowlisted immutable task/material snapshots through the existing scoped reader; add finance version/source validation and explicit nested material grants. An ID in a task's prose must not implicitly grant access |
| `AnalystOutput` | Preserve the strict claims-only legacy output. A new versioned research output must carry coherent public explanations; undeclared extra fields are not a migration strategy |
| `ResearchArtifacts` | Reuse same-run isolation, generated IDs, content integrity and atomic record writes; explicitly dispatch v2 results to ResearchRecords instead of casting them to v1 kinds |

Implementation references: [task contracts](../src/policy_impact/runtime/task_brief.py), [role registry](../src/policy_impact/runtime/subagent_roles.py), [task materials](../src/policy_impact/runtime/task_artifacts.py), [runner](../src/policy_impact/harness/subagents.py), [legacy role outputs](../src/policy_impact/runtime/subagent_outputs.py) and [research store](../src/policy_impact/domains/finance/research_artifacts.py). These are static code observations, not live acceptance results.

### 8.2 Task input

Keep execution identity, effective permissions, selected contract version and resource limits Runtime-owned. Give the executor a typed finance task snapshot through its authorized inputs; it is not a writable parent plan or a copy of the entire conversation. The `finance.research_task.v1` descriptor is:

```json
{
  "schema_version": "finance.research_task.v1",
  "original_query": "Explain the author's argument and its potential weaknesses.",
  "objective": "Explain this article's contribution to the question, retaining its assumptions and limits.",
  "material_refs": ["material_example"],
  "requested_result_kind": "article_understanding",
  "completion_expectation": "Address the article as a whole; report anything that could not be processed.",
  "reading_request": {"material_ref": "material_example", "depth": "deep"},
  "focus": [],
  "user_requirements": []
}
```

`objective` and `completion_expectation` express the main Agent's task, not a Runtime-selected financial method. `reading_request` applies only when reading depth/range is relevant; a skim needs the explicit permitted range or authorized range-selection mechanism already required by section 3.1. `focus` and `user_requirements` are optional. For synthesis, the task supplies the relevant understanding/view/source handles, and does not require per-article reading parameters.

Result kinds are `article_understanding`, `synthesis` and `working_view`: these describe the product, not three mandatory stages, Query classes or a fixed sequence. They share the analyst research-body contract. Task-specific reading/synthesis Skills describe capabilities rather than routing Questions by keywords. The executor remains a tool loop; one sufficient full-text analysis may return immediately.

Every material handle must be explicitly present in the Runtime-granted task scope as well as the descriptor. Nested references explain lineage but do not transitively grant source reads, external tools, unrelated notes or other runs. Permissions are the intersection of parent authorization, the task's material scope and the selected plugin's tools; model-supplied instructions cannot broaden them.

### 8.3 Material handles and visibility

A Runtime-issued material handle resolves a specific source or derived result version, its kind, integrity checksum and origin. It is a scoped read capability, not a filesystem path or permission inferred merely from an ID format. To request a grant, the main Agent may submit a known article ID, current-run artifact ID or already granted material handle obtained from tools; it need not invent a material ID. Runtime resolves and authorizes the lookup, binds its version and records the input-ID-to-handle mapping. Original lookup IDs are not capabilities; unknown, stale or unauthorized references fail without a filesystem fallback. Source identity/author/date/version and actual observations come from trusted metadata; the model cannot overwrite them. The executor sees normalized handles and may read only authorized handles through `artifact_read` or separately allowed source tools. Granting article metadata does not count as reading its body or create an EvidenceRef.

Complete task snapshots may be preloaded when their budget fits, or expanded with the scoped reader. Trace must distinguish granted material, material actually included in a model input, and model-declared use. An ID-only placeholder is not evidence of model visibility. Preloading does not entail a second ceremonial read; the same actual content, hash and evidence provenance must be recorded whichever path delivers it.

Evidence carried by an authorized understanding is inherited source evidence, not a new independent observation. The executor may cite it without rereading every original passage, provided its canonical source binding survives validation. New child reads retain child observation IDs and source metadata until validated parent import; any ID translation is deterministic and traceable. An evidence string appearing only in generated text creates no authority. Expansion to the original article still requires the corresponding read grant.

Version mismatch returns a stale-material error with the affected reference and reason; it does not silently substitute current text. Existing exact observations may remain available for historical inspection, but cannot be mixed into a current-version understanding without explicit handling. Source version checks use the current synchronized corpus index; this does not promise live filesystem watching or restart recovery.

### 8.4 Result body and Runtime envelope

Use a shared outer record, not one universal analysis table. Reading and synthesis share the `finance.research_result.v1` body; verification keeps its typed verdict/check contract rather than being flattened into prose. Example research body:

```json
{
  "schema_version": "finance.research_result.v1",
  "content": "The author offers a conditional view. The model's assessment remains limited by the supplied evidence.",
  "evidence_links": [{
    "text": "The author offers a conditional view.",
    "evidence_refs": ["evidence_example"]
  }],
  "material_disposition": [{
    "material_ref": "material_example",
    "decision": "considered",
    "reason": "It addresses the assigned question."
  }],
  "gaps": [],
  "limitations": []
}
```

Examples illustrate the normative fields, not real source claims. A supplied `evidence_links[].text` must locate a passage in `content` or the optional `candidate_answer`; source refs still require provenance checks. The `content` field is a public evidence-based explanation, never private model reasoning. Optional quotes must be verbatim and source-bound; optional existing card refs or a candidate answer follow section 8.7 and are not required for every result. Fine-grained claims remain available through the retained card capabilities rather than undeclared fields in this body. No-relevance findings may have no analytical evidence links, but retain actual inspected scope and cannot assert corpus-wide absence from an excerpt.

Material decisions are `considered`, `excluded` and `unprocessed`, with reasons. These do not replace article discovery/reading/use states, and `considered` does not mean final-answer use or semantic proof. Missing materials remain pending/unknown, not implicitly excluded. Scope declarations are model judgments checked against real inputs and later reviewed, not Runtime-certified completeness.

After validating model output and source bindings, Runtime wraps it with:

| Runtime-owned component | Purpose |
| --- | --- |
| Record schema, generated artifact ID, kind and checksum | Identify an immutable stored result; a new result does not overwrite a previous one |
| Session/run, task, parent/child spans, role/plugin and contract version | Link responsibility and execution without model-authored replacement IDs |
| Original source versions and upstream material/result refs | Preserve source lineage for synthesis, working views and final audit |
| Acquired ranges, successful analysis-input coverage and material visibility | Separate acquisition from processing and Agent-declared synthesis consideration |
| Actual stop reason, validation/processing failures and import disposition | Preserve failed/partial execution independently of a useful body or an answered parent task |

The runner stop reason remains separate from task relevance, coverage, review verdict and user delivery. A child can successfully return an honest limited/no-relevance result; a failed child can leave valid prior observations/results. Do not extract partial JSON or hidden intermediate thought as a completed analysis. If no validated analytical result exists, preserve observations and the failure, not an invented partial understanding. Successful `DelegationEvidence` remains restricted to successful child execution; partial-result import must not call `evidence()` to upgrade failure.

### 8.5 Storage and read API mapping

Use explicitly versioned `finance.research_artifact.v2` records for the three result kinds alongside existing v1 card/comparison/review/note records under the Runtime-bound run research directory. This avoids forcing coherent explanations into claim lists or `research_note.entries`. Parent Runtime validates and stores returned child material; children do not gain direct parent-store write access. Raw observations retain their own versioned source/restore chain rather than being duplicated into every result.

The versioned artifact capability exposes full-record `get` and paginated metadata `list` with `next_cursor` and an honest completion flag. The main Agent can inspect the full body after return or later by ID; actual delivery is budget-aware under section 4.1, not always a tiny summary or unconditional full-corpus injection. Delegated reads use only scoped material handles, not the parent's unrestricted artifact API. Section 8.8 defines the API and ID compatibility rules.

Use atomic immutable run-local files and existing execution identity for this integration, without a new service or database table solely for these analytical products. Persist a validated record before advertising it as recoverable; write failures remain explicit and must not invent a recoverable artifact ID or turn storage failure into successful recovery. File presence alone does not make a result visible, accepted, verified or delivered. Listing/index/Trace references must be reconciled with committed records; multiple atomic files do not form a database transaction, and this contract does not promise durable task scheduling or restart recovery.

This contract covers only explicitly authorized run-local reuse. Session-wide or cross-run reuse, cache freshness and cancellation/queue semantics are separate design questions, not automatic consequences of writing a file. Source changes invalidate affected dependencies; reanalysis creates a new record linked to the task/source version rather than silently updating the old body.

### 8.6 Confirmed integration choices

| Decision | Selected direction | Alternative not selected for this integration |
| --- | --- | --- |
| Executor organization | Explicitly select `analyst@2.0.0`, sharing the controlled runner with legacy `analyst@1.0.0`; use task-directed reading/synthesis Skills, not new fixed reader/synthesizer roles | Separate reader/synthesizer registrations; neither form would justify a fixed pipeline |
| Product representation | Versioned coherent research results and Runtime source/coverage envelope; retain optional cards and typed review outputs | Claims-only handoff or forcing complete understanding into today's `research_note.entries` |
| Persistence | Immutable Runtime-written run-local files, scoped handles and paginated reload/list; no database replacement | A dedicated persistent task service before its lifecycle and transaction semantics are designed |

Reading/synthesis capability descriptions belong in finance Skills; the new analyst prompt only states the execution role, task adherence, permissions, public explanation and return contract. Shared safety rules are referenced instead of copied into every prompt. The main Agent selects tasks and whether to delegate; the Runtime never chooses research depth or delegates from financial keywords. The implemented modules are listed in section 8.9; they do not add a mandatory research sequence.

### 8.7 Field and routing definitions

All new model-facing objects are strict: unknown keys and wrong types produce recoverable contract feedback, not automatic financial rejection. Strings required below are nonblank. Optional arrays default to empty; omitted optional objects stay absent, not fabricated. IDs, source metadata, permissions and execution status are never model-writable fields. No arbitrary metadata bag is accepted in place of these fields.

| `finance.research_task.v1` field | Definition |
| --- | --- |
| `schema_version` | Required literal `finance.research_task.v1` |
| `original_query`, `objective`, `completion_expectation` | Required strings; the parent task and its delivery expectation, not hidden model reasoning |
| `material_refs` | Required array of unique known lookup/grant IDs; the main Agent does not submit filesystem paths. Binding must succeed before the child uses the normalized handles |
| `requested_result_kind` | Required `article_understanding`, `synthesis` or `working_view`; Runtime binds the stored result kind from this value |
| `reading_request` | Optional object; required for `article_understanding`. Contains `material_ref`, `depth=skim|deep`, optional `ranges` and optional `select_ranges` (default false). The focal reference must resolve to one authorized article version; other inputs can provide task background |
| `reading_request.ranges` | Array of nonoverlapping inclusive `start_line/end_line` positive-integer ranges within the granted source. Skim requires these or `select_ranges=true`, not both. Deep omits both and requests the whole focal version; restricted access or missing processing is reported as a gap, not silently converted to skim |
| `focus`, `user_requirements` | Optional arrays of strings; no required financial checklist |

An empty material list does not itself grant any access or establish source-based coverage. A task may report a gap, acquire evidence through separately authorized tools, or offer clearly labeled general knowledge/inference; it must not claim to have analyzed an unobserved article. `article_understanding` still requires its focal article grant. Multiple materials do not by themselves make a task a synthesis or require multiple children. Inconsistent focal refs/ranges return a concrete correction request; Runtime does not guess a different article or depth.

New research delegate action profile:

```json
{
  "type": "delegate",
  "role": "analyst",
  "role_version": "2.0.0",
  "research_task": {
    "schema_version": "finance.research_task.v1",
    "original_query": "Explain the author's conclusion and its weaknesses.",
    "objective": "Read the selected article and explain its argument with source links.",
    "material_refs": ["article_example"],
    "requested_result_kind": "article_understanding",
    "completion_expectation": "Explain the argument and disclose missing evidence or processing.",
    "reading_request": {"material_ref": "article_example", "depth": "deep"}
  }
}
```

This explicit v2 action profile does not also require the legacy top-level `task` and `input_artifact_refs`; do not make the model duplicate its instruction. Runtime validates the registered role/version, binds material IDs to task-local handles, creates the immutable task snapshot and derives `TaskBrief.task` from `objective`. Internal TaskBrief carries the validated role version and a task-snapshot ref; its authorized input refs include that snapshot and the normalized granted materials. Task/run/span IDs, allowed tools and limits come from Runtime. The selected output schema is `finance.research_result.v1`.

Legacy delegate actions that omit a version, or explicitly select `1.0.0`, retain their existing `role/task/input_artifact_refs` profile and output schema; they are not upgraded by a new default. A v2 profile requires explicit `2.0.0`; unknown/unregistered versions fail with contract feedback, never downgrade or use a permissive generic output. `DelegateAction` and `SubagentRunner` now expose this explicit branch; their legacy behavior is regression-tested separately.

| `finance.research_result.v1` field | Definition |
| --- | --- |
| `schema_version` | Required literal `finance.research_result.v1` |
| `content` | Required nonblank freely organized public explanation; may state a scoped no-relevance finding or limited result |
| `evidence_links` | Required array, possibly empty. Each item has nonblank `text` that occurs in `content` and unique nonempty `evidence_refs` of authorized inherited or newly observed evidence; optional `quotes` contains objects with `evidence_ref` and exact source `text`, with the ref also in that item's refs |
| `material_disposition` | Required array, possibly empty; each item has a granted `material_ref`, `decision=considered|excluded|unprocessed` and nonblank `reason`. One decision per normalized handle; missing entries remain unknown rather than automatically considered/excluded |
| `gaps` | Required array of objects with nonblank `description`, optional granted `material_refs`, and optional nonblank `next_need` explaining follow-up; it cannot authorize a follow-up itself |
| `limitations` | Required array of nonblank strings, possibly empty; model limits remain distinguishable from Runtime failures |
| `supporting_artifact_refs` | Optional unique refs to authorized existing cards/other supporting artifacts; references do not grant access or replace the coherent result |
| `candidate_answer` | Optional nonblank draft text, not a child publication. Its important assertions need source links; `evidence_links[].text` may locate a passage in `content` or this field when it is supplied |

Source quotes are checked against their actual EvidenceRef's observed content/locators. A considered declaration requires actual body/input visibility; exclusion can be based on disclosed metadata-only scope without pretending to have read the body. Missing relevance, missing evidence links or disputed interpretations are evaluated with their actual scope: structure checks do not certify financial meaning or impose a claim per sentence. Important unsupported assertions remain review gaps under sections 4-5, not proof that all safe analysis must be removed.

### 8.8 Artifact API, persistence and receipts

Extend `research.artifact` only through explicit version selection:

| Operation | Version/inputs | Return and compatibility |
| --- | --- | --- |
| `save` legacy | Omit `schema_version` or use `finance.research_artifact.v1`; existing kinds/payload | Existing validation and response unchanged |
| `save` research | `schema_version=finance.research_artifact.v2`, `kind=research_task.requested_result_kind`, typed `research_task`, and `payload=finance.research_result.v1` | Runtime binds the main Agent's actual observations and source/task metadata, validates and persists a v2 record. A main Agent can therefore use the same contract without delegating |
| `get` | Generated `artifact_id`, no path or caller-supplied run | Dispatch by the stored record's exact schema; return the complete valid current-run v1/v2 record, or an explicit missing/stale/integrity/permission error. A v2 record is never cast to a v1 card |
| `list` legacy | Omit version/use v1 | Existing v1 listing stays unchanged; it does not claim to list v2 results |
| `list` research | Explicit v2, optional selected kind and opaque cursor | Return `schema_version=finance.research_artifact.v2`, `artifacts` metadata, page `count`, `next_cursor` or null, and `listing_complete`. Page size is Runtime-configured. The cursor binds run/filter and collection snapshot; additions or incompatible snapshot changes return `stale_cursor` and require relisting, not silent missing records |

Internal parent ingestion uses the same validator/persistence path as v2 save; the child receives no general parent `research.artifact(save/list/get)` capability. Normalize equivalent input lookup IDs into the task's granted handles before validating result refs. The parent evidence import must preserve inherited/new-observation provenance and canonical ID mappings before final-answer use. No silently dropping invalid source bindings while publishing affected claims as supported.

A v2 stored record has required `schema_version`, generated `artifact_id`, `kind`, `session_id`, `run_id`, `task_id`, `producer`, `task_snapshot`, `material_bindings`, `source_bindings`, `dependencies`, `processing`, `execution`, `payload` and `checksum`. `producer` retains main/child identity, parent/child spans when present, analyst contract version when applicable and context/Prompt/Skill hashes. Source bindings retain canonical source IDs, versions and known metadata; unknown/conflicting dates remain explicit. Dependencies retain upstream artifact IDs and checksums; `processing` retains actual observed ranges, successful analysis-input call refs/ranges and input visibility, not a semantic-completeness certificate. `execution.stop_reason` keeps the real runner result. The payload is the validated research result, not an invented failed-output body. Session-wide or arbitrary cross-run records are rejected.

ID prefixes are `understanding_`, `synthesis_` and `view_` with 24 lowercase hexadecimal hash characters. Compute the identity from canonical compact, sorted-key UTF-8 JSON of the record excluding `artifact_id/checksum`; the full SHA-256 is the checksum of that same canonical identity payload. Bind source/task/execution metadata before hashing; a different task, source version or revised result yields a different ID. On collision/existing ID, compare the full checksum and record, never overwrite. Cyclic dependencies, unknown versions, changed source hashes or invalid checksums are explicit errors. A stale upstream record makes dependent views stale; it does not become a new observation.

Commit the complete validated record atomically under the existing Runtime-bound run research directory, then advertise its ID. Final receipts distinguish `persistence_status=saved|failed|not_attempted`, actual `stop_reason` and an optional committed `artifact_id`; only saved records have a recoverable artifact ID. Successful record persistence does not turn a failed child into `DelegationEvidence`. Storage failure can leave a validated inline result and valid observations available for safe qualified delivery, with a disclosed recovery gap; it does not imply user delivery must be blocked. If no valid body exists, return failure/observations and any previously committed valid result refs without manufacturing a new result record.

The parent normally receives the complete valid result when its input budget permits. Under actual pressure, a committed full result may be represented in the active context by its scoped reference and a clearly labeled working view; record what was actually delivered. No unconditional short-summary-only return, truncation of the sole copy or implicit parent acceptance is allowed. The main Agent records use/qualification/exclusion/follow-up before final delivery. This is not a durable queue, exactly-once provider execution guarantee or restartable task scheduler.

Trace mapping for the implemented optional path (real model quality still requires acceptance):

| Event or observation | Required linkage |
| --- | --- |
| Existing `subagent_context`, model/tool records and `subagent_delegate` | Selected role/version/output schema, task snapshot/hash, permitted handles, actual model-input visibility and source reads, parent/child spans and real stop reason |
| `research_result_ingest` | Task/span, result schema/kind, validation outcome, inherited/new evidence mappings, persistence status and committed artifact ID only if saved; errors retain affected refs and actual prior results |
| `research_result_disposition` | Parent task/run, result or validated inline-result checksum, `use|qualify|exclude|follow_up`, and reason; it is a parent decision, not a successful verification certificate |
| Existing restore/offload and final audit/delivery records | Original result and working-view IDs/versions, actually expanded inputs, selected review targets, final original-source evidence and truthful delivery/coverage states |

### 8.9 Parent disposition, configuration and implementation map

The main-only tool `research.disposition(result_ref, decision, reason)` accepts a saved current-run result ID or an ingested inline result checksum, `decision=use|qualify|exclude|follow_up`, and a nonblank reason. It emits `research_result_disposition`; every ingested valid result needs a parent decision before ordinary final delivery. This is neither verification nor a demand that every read article enter the answer. Versioned final audit derives `used_articles` from actual final EvidenceRefs/inline article locators; it does not append all read articles as final-answer sources. A complete late candidate may instead be locally checked and delivered with timeout and incomplete-review status; no further model review is started beyond the deadline.

No-valid-body receipts retain stop reason, observed processing and sanitized diagnostics without inventing an artifact ID. Valid-body write failures preserve inline content and checksum with `persistence_status=failed`. Ingest rejection removes any success proof; a failed child cannot gain `DelegationEvidence` just because its useful body was saved.

| Responsibility | Implementation |
| --- | --- |
| Strict task/result/range contracts | [research_contracts.py](../src/policy_impact/runtime/research_contracts.py) |
| Grants, full-text assembly, actual-input provenance, ingestion and final linkage | [research_execution.py](../src/policy_impact/domains/finance/research_execution.py) |
| Immutable records, dependency validation and pagination | [research_records.py](../src/policy_impact/domains/finance/research_records.py) |
| Parent/child time and shared physical request ledger | [research_budget.py](../src/policy_impact/harness/research_budget.py) |
| Main capability, child role and task-directed reading/synthesis guidance | [main Skill](../plugins/skills/finance/versioned_research.md), [role registrations](../plugins/subagents/roles.json), [analyst prompt](../plugins/prompts/subagents/analyst_v2.md) |

Configurable `FINANCE_RESEARCH_*` initial limits, exact recovery policy, offline evidence and untested cases are centralized in [change 004](changes/004-versioned-research-implementation.md#7-实现映射与验证记录). These are engineering caps, not proven model comfort limits or production SLAs. Versioned research remains synchronous and run-local; it does not add database tables, a queue, cross-run memory or a new Agent framework.

## 9. Progressive capability discovery

User-approved change [010](changes/010-progressive-capability-loading.md) replaces eager finance capability injection. `SkillManifest.capability_catalog` is an optional plugin-relative JSON path under `plugins/skills`; absent/blank retains legacy loading. The catalog has `schema_version=finance.skill_catalog.v1` and `skills` entries with unique `skill_id`, nonblank `description`, a declared `module`, and related `tools`. Catalog modules must exactly match the manifest's allowlisted `skill_modules`. Neither a model nor a source document supplies a filesystem path.

The main prompt contains the short catalog. Initial tool descriptions expose names and purposes, with full typed parameters only for `research.plan`, `skill.open` and `tool.describe`. Runtime's tool allowlist, source restrictions and typed execution validation remain unchanged. Loaded guidance is not source evidence or proof of verification.

| Tool | Input | Output and limits |
| --- | --- | --- |
| `skill.open` | `skill_id: string` | `skill_id`, `module`, exact nonempty `content`, SHA-256 `checksum`, `already_loaded`, related `tool_descriptors` containing only name/purpose, and applicable capability-specific `schemas`. Tool parameters still require `tool.describe`; opening source guidance must not load every market/web/article Schema. Only configured IDs; run-local immutable first-read snapshot; no arbitrary paths |
| `tool.describe` | `name: string` | One exact current-run allowed descriptor with full input Schema; unavailable tools fail, never become authorized by discovery. `name` avoids collision with the gateway's dispatch parameter `tool_name` |

Both discovery tools are main-only read-only metadata operations and may precede planning to clarify the contract; business research cannot. Failed loading remains explicit and must not fall back to all-module injection. Repeat loading can re-deliver the same complete snapshot after context offload; it never auto-appends a second system module. Target-review Schema is supplied with the evidence-review Skill rather than every initial input. `plugin_load` reports composition and initial-loaded modules separately from available modules; `skill_load`/`tool_describe` plus ordinary tool/input Trace show actual disclosure. No database, queue, model transport or semantic acceptance threshold changes.
