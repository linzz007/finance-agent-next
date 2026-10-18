# Finance Agent Research Interfaces (v1)

This is the implementation contract for the selected A1 + B3 + C1 + D* + E* design. It describes the interfaces actually exposed by the autonomous finance workspace. It is not an instruction to always use every tool. The main AgentLoop chooses research methods based on the question; final prose has no fixed template.

## 1. Ownership and call graph

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

`research.artifact` is a working-memory plugin, not a publication or report endpoint. Its records are model-authored and source-bound, not automatically true. Existing final-answer citation and subagent audit gates remain authoritative. A `review` saved by the main agent must not be presented as independent subagent verification.
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

`article.study(article_id, question, depth, start_line?, limit?)` is an optional one-article model-assisted producer for this card. `deep` reads every line in sections of at most 100 lines and sends each section to a separate model call; `skim` requires explicit `start_line` and `limit` (1-100). It returns `card_id`, source-bound claims and limitations, or `no_relevant_claims` if no claim matches the question. Every returned claim is rechecked against the exact source quote and observed line EvidenceRef before the card is saved. The model call is visible in trace. It never certifies external factual accuracy.

## 4. `comparison` payload (B3)

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

`research.plan` appends a Runtime-owned `runtime_delivery` goal on creation (and when loading a historical plan). The main Agent may finish or block research goals but cannot update that goal; an attempted update returns a recoverable tool-argument error directing it to submit a final answer instead. After the terminal publish decision, Runtime sets `delivery_status=done` whenever a safe user-visible result is delivered, including a complete answer, a qualified answer, or source-only material. It sets `delivery_status=blocked` only when no safe result can be delivered because of a model, tool, gate, provenance, or permission failure. `delivery_mode` records `full`, `qualified`, or `source_only`; delivery status does not certify factual correctness. If the finance turn fails before plan creation, Runtime writes a delivery-only `blocked` plan instead of leaving no status. `research_pending_count` and `delivery_status` are distinct: zero research pending does not mean the user received an answer. The per-run `research_plan.json` retains the final delivery status. `research_delivery` is emitted after the terminal commit and therefore follows `run_stopped` in Trace.

Before publishing a substantive answer with read finance evidence, the Runtime calls `AnswerAudit.call(candidate_answer, question, personal_context)` as a final Hook. It splits the answer into sections of at most 900 characters, numbers the lines in each section, and reviews only that section; other answer sections are assessed in separate batches. The `finance_answer_audit.v1` result contains `checks[]` (`line_start`, `line_end`, `claim_type`, `action`, `evidence_refs`, `reason`, `suggested_revision`), canonical `answer_audit_coverage`, legacy `coverage_complete` only as a read-compatible alias, `used_source_ids` and `limitations`. `answer_audit_coverage` describes whether material claims in the candidate answer were audited; it must never be used as the research evidence-coverage status. Runtime resolves line ranges back to exact answer spans, so the model cannot rewrite the reviewed text. Author-article EvidenceRefs preserve the entire observed line-bounded excerpt and its length/hash; each selected article contributes at most 20,000 characters to one audit batch. The audit distinguishes the ledger's read candidates from the sources actually used by the answer: explicit citations, author/date/path anchors, and EvidenceRefs attached to audit claims populate `used_source_ids`; unmentioned reads do not become a coverage defect. Small unanchored corpora may be supplied as a bounded candidate set, while larger unanchored corpora use a bounded candidate set and require the model to cite only sources actually used within that set. The cross-article challenge may inspect the bounded candidate set adversarially, but it does not require the answer to mention every candidate. A truncated or integrity-failed passage, model-reported unreviewed assertion, or missing evidence for an asserted claim blocks publication of that unsafe span with a concrete limitation; safe source-only or qualified delivery remains possible. Article author/title/publication-date lines are source metadata: the local article EvidenceRef establishes their identity and they do not require external market verification. Each independent material assertion is classified as `author_view`, `objective_fact`, `prediction`, `causal_explanation`, `model_inference`, or `personal_context` and receives `retain`, `qualify`, `revise`, or `delete`; an attributed forecast is still a prediction even when the model initially labels it as fact, causal explanation, or author view. The deterministic boundary repairs this type and only forces revision when a future/conditional statement is explicitly placed in the ordinary `A` viewpoint section; it does not rewrite ordinary article summaries or discussions of prediction methods. The deterministic boundary also downgrades an unqualified public-market fact supported only by one secondary web domain unless clearly attributed to that publisher. Unknown EvidenceRefs fail before the model pass. Issues and coverage limitations return to AgentLoop for revision; only an accepted candidate is published as-is; unresolved candidates follow the bounded delivery policy below. Each candidate emits an `answer_claim_audit` trace step with answer checksum, reviewed claim count, actions, used sources, candidate sources, scope and limitations. This Hook checks traceability and publication safety; it is not the verification strategy and is not a guarantee of semantic truth. Malformed audit responses are retried with schema and semantic errors up to three times.

Each audit section also has a four-line cap. This prevents one model check from absorbing an entire multi-line article comparison under a single claim classification. Explicitly labeled, evidence-supported analysis by the answerer may be retained without requiring the source author to have voiced that analysis; important omitted conditions or misleading certainty still require revision.

After atomic sections pass, answers drawing on two or more observed articles receive a second cross-article challenge. A model first selects broad summary/conclusion line numbers; dated comparison-table rows are included structurally so a model omission cannot leave those rows unchecked. If the selector returns no lines, a deterministic fallback selects headings, dated rows, conclusion/change/condition lines, and the boundary lines. Dated rows accept `YYYY-MM-DD`, slash-separated dates, short dates, and full Chinese `YYYY年MM月DD日` forms. Each bounded candidate passage is matched to relevant dated and global answer lines, then checked in batches of at most six lines; corpora larger than twelve articles are handled as one-source review calls rather than skipped. An issue needs exact `answer_quote` and `source_quote` substrings plus the observed EvidenceRef. Missing support from that single passage is not a contradiction; other passages are checked separately. A further issue-adjudication pass dismisses off-topic or already-addressed proposals. `cross_review` records selected lines, per-source line counts, batch count, proposed/retained/dismissed counts and verdict reasons in trace. The atomic gate's coverage is based on material assertions and their used EvidenceRefs, not on whether every article read during research appears in the answer; the cross challenge may inspect extra bounded candidates without converting them into citation obligations. Retained issues feed the same revise/research loop. This catches some misleading summaries, including conditional predictions, unrealized scenarios presented as settled, and material omitted observations, but is not proof that every semantic omission was found; independent answer-quality review remains part of live acceptance.

The final-answer audit permits at most three audit attempts. Repeated objections to the same exact answer span are grouped into one dispute while retaining every objection. Acceptance publishes the audited answer. If all three attempts fail but the last review covers all material assertions and identifies unique disputed spans, Runtime moves those spans out of the conclusion and delivers the remaining candidate with a separate dispute section: the original draft wording, a cited author passage when available, the reviewer's objection, and an explicit statement that this objection has not been independently adjudicated. If coverage is incomplete, the audit fails, or a disputed span cannot be located uniquely, Runtime does not publish unchecked candidate prose; it delivers observed source excerpts and market observations only, plus any source-bound review objection that can be shown without certifying it. Thus a completed run still has an answer, but `verification_status=partial|disputed|failed` does not imply factual validation; `delivery_mode=source_only` describes what was delivered. `answer_review_resolution` records the bounded delivery and attempt count in Trace. `delivery_status=done` means the answer was delivered, not that its financial conclusions were proven. A reviewer disagreement cannot silently erase a faithfully attributed author position; the author position and contrary evidence must be distinguishable in the output.

Before final claim audit, Runtime also checks arithmetic consistency when one answer line states a market start price, end price and period return. A mismatch returns a repair request to the main Agent; it does not infer a new market observation. In particular, a first in-period quote must not be paired with a percentage computed from an earlier baseline close without an explicit separate label. This is a generic numerical consistency boundary, not a mandatory analysis path.

### 5.1 Unified state and type semantics

The following fields are canonical and describe different dimensions of one run:

```json
{
  "delivery_status": "done",
  "delivery_mode": "full|qualified|source_only",
  "verification_status": "verified|partial|disputed|failed",
  "evidence_coverage": "complete|partial|uncovered",
  "answer_audit_coverage": "complete|partial|failed"
}
```

`delivery_status` only answers whether safe user-visible material was delivered. It does not certify correctness. `evidence_coverage` describes whether the user's research scope was covered; `answer_audit_coverage` describes whether material claims in the final answer were audited. A run may therefore be `done + partial`, and complete research coverage does not imply a correct causal conclusion.

`coverage_complete` is a legacy read-compatible alias for `answer_audit_coverage` only. It must not be used as an alias for `evidence_coverage`, and new writes must use the explicit field name.

Claim types are limited to `author_view`, `objective_fact`, `prediction`, `causal_explanation`, `model_inference` and `personal_context`. Cross-article synthesis is not a claim type. It is represented by `scope="cross_article"`, an optional `relation_type` of `agreement`, `disagreement`, `change_over_time` or `complementary`, and a claim type describing the resulting assertion. A model-generated comparison is normally `model_inference`; it must not be presented as an objective fact merely because multiple authors agree.

Research confidence uses `claim_confidence_label` or `research_confidence` with `high`, `medium` or `low`, plus `confidence_reason`. Wiki or other fact records may retain numeric `fact_confidence` values in their own schema. The bare field `confidence` is deprecated for new research records; it must not silently mix numeric fact confidence with a semantic research label.

## 6. Context and trust boundaries (E*)

Source article versions and `EvidenceLedger` entries are the provenance layer. Cards, comparisons and reviews are derived working state, kept separate and reloaded by generated IDs. `list` returns summaries rather than concatenating article bodies; `get` loads one selected record. `article.read` remains the path to exact source text. There is no default compression or batch concatenation. The existing analyst delegation can handle bounded article batches; child evidence must be merged and audited before use. A source edit invalidates cards and any comparison/review depending on them.

The finance main AgentLoop estimates the active message context before each model call. Above 60,000 estimated tokens, it offloads older tool observations while keeping the four newest observations visible. Each exact, unmodified observation is saved atomically under `<company>/runs/<run_id>/context_observations/context_<hash>.json`; the conversation gets a marker with generated `context_id`, checksum, tool name and `context.open` restore instruction. `context.open(context_id)` is a read-only, same-run tool returning the exact stored content after ID and checksum validation. It accepts no paths or cross-run IDs. The `context_offload` trace step records threshold, token estimates and IDs. If the same context ID is restored repeatedly in one run, Runtime records a `context_restore_advisory` trace and exposes an advisory field to the model after the second restore. This advisory alone does not block restoration.

If repeated restoration of the same ID is accompanied by a *new* context-pressure offload, the finance loop enters `context_progress_required` checkpoint. The `context_progress_gate` trace records activation, each denied `context.open`, and release. The model can still use other tools, save a new current-run EvidenceRef-bound `research_note`, update a plan goal from pending to done/blocked with a concrete progress note (at least 20 characters), or submit a truthful final answer. Only a successful new note with entries or a changed goal status and note releases the pause; a failed save, a duplicate note, a plan `get`, or an empty/unchanged update does not. The third denied `context.open` attempt within one checkpoint ends the loop as `gate_blocked`, rather than spending the full model-call budget. Other tool calls do not reset that count; accepted progress does. After release, a fresh repeat-restoration/pressure sequence is needed to reactivate it. This does not cap article count, automatically summarize sources, or select an analysis workflow. Original article files, the EvidenceLedger and saved research artifacts are never replaced or summarized by this mechanism; only the model-facing copy of earlier tool observations is swapped. The estimate is not the provider's exact token count, and the model must explicitly restore a view when exact old details matter.

For every substantive answer, the existing final guard still governs article-line citations and declared subagent review. A stored card does not bypass that guard. `research.artifact` never reads arbitrary local files; company/run scope is constructed by Runtime, article IDs are resolved through `ArticleCorpus`, and symlink artifact paths are rejected.

An explicit source boundary in the user question is represented by `FinanceResearchGateway.source_scope`. `local_only` denies web/market tools for the main AgentLoop and delegated finance readers. A request naming discrete article dates filters `article.list` and denies `article.read`, `article.study`, or `article.claims` for other publication dates; an explicit date range is not treated as a discrete whitelist. These are user-request scope constraints, not an automatic choice of analysis method. The scope is visible in the research trace and must be tested against natural-language paraphrases before broadening the parser.

### 6.1 Runtime stability boundaries

- Article time scope is not market intent. Temporal qualifiers such as recent, this month or this year do not by themselves require market tools when an asset is mentioned. Explicit requests for prices, returns, trends or market-fact checks retain the existing evidence boundary. A grounded no-article answer may be delivered without collecting unrelated quotes.
- Transport failures retry the current request within the configured attempt limit, without appending JSON-repair instructions. Schema repair is reserved for an actual response that violates the output contract. Trace distinguishes transport failures from schema failures.
- OpenAI-compatible `finish_reason=length` is handled as `max_tokens` truncation, including empty content. A truncated response cannot be published as a complete action or final answer even if its partial JSON parses. Retry exhaustion retains observed sources and an explicit incomplete-delivery status.
- Model-call `attempts` records the actual count supplied by the adapter; unknown counts remain null. `max_attempts` is the configured limit, not a substitute for observed attempts. Passing code regression, successful model transport and passing financial-answer acceptance are separate claims.

### 6.2 Optional model-side SSE transport

`AGENT_WORKBENCH_MODEL_STREAM=true` enables SSE on OpenAI-compatible Chat Completions only. It defaults to false; Anthropic transport is unchanged. `AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS` defaults to 600 and accepts 1-900 seconds. The existing model timeout bounds connection/idle reads; elapsed stream time is checked before and after each read, so a blocked read can extend the total by at most one idle window. Comments/heartbeats do not reset the elapsed budget. An event is capped at 1 MiB and a whole stream at 8 MiB. Configuration is inherited by copied finance/child adapters and exposed in model status without secrets.

Request: existing model/messages/temperature/max_tokens plus `stream=true`, `stream_options.include_usage=true` and `Accept: text/event-stream`. Response path: UTF-8 SSE events -> JSON data -> choice 0 `delta.content` accumulation -> finish reason and `[DONE]` -> existing normalized `ModelResponse` -> unchanged JSON/action schema and evidence checks. Reasoning deltas are not answer content. No fragment is published or executed as a tool action. A complete transport does not itself mean the model output or financial assertions passed review.

EOF without both completion signals, corrupt events and disconnection discard that attempt's buffer and retry the same request within the existing bounded attempts/backoff. Attempts are never concatenated. `length` remains truncation even after `[DONE]`; authentication failures remain non-retryable. There is no silent non-streaming fallback or proxy bypass. Success metadata includes `transport`, `stream`, `stream_complete`, `stream_event_count`, `stream_content_chars`, `time_to_first_token_ms`, `stream_duration_ms`, `stream_max_seconds`. `transport_attempts` retains per-attempt diagnostics on success and failure, including actual attempt numbers, but no partial content or credentials. Both main-loop and direct article/audit model failures retain these diagnostics in Trace.

Contract tests cover framing, UTF-8, heartbeats, multiline data, usage tails, missing completion signals, disconnection/retry without buffer contamination, duration/size limits, redaction, configuration and non-streaming compatibility. Live no-author, single-article and multi-author checks remain separate business acceptance; receiving streamed tokens alone is not a pass.

## 7. Verification

`tests/test_finance_research_artifacts.py` exercises source-line and exact-quote binding, deep/skim coverage, comparison references, typed review restrictions, ID reload, stale article invalidation, run isolation, schema-repair behavior and denied subagent access. `tests/test_finance_answer_audit.py` checks final claim validation, source sufficiency, cross-passage quote anchors, unsupported IDs, forecast boundary correction (including mislabelled facts, method discussions, negation and historical comparisons), empty-selector fallback, slash/full-Chinese date rows, and corpora above twelve sources with per-source call coverage. `tests/test_agent_loop.py` checks pressure offload, exact restore, cross-path rejection and tamper detection. `scripts/finance_artifact_acceptance.py` runs live A/B/D passes, including typed checks of claim IDs used in the comparison. `scripts/finance_acceptance.py` runs or replays live chat answers and can replay a historical answer's cross-review. No live-model quality claim follows from contract tests alone; single-article, author-summary and multi-article time-change end-to-end runs are required to judge research quality.
