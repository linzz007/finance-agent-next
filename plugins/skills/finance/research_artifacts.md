# Evidence-bound research records

`research.artifact` is an optional run-local working-memory tool. Use it when preserving a carefully read article, comparing several article viewpoints, or recording a claim-level check would help answer the user's question. Simple questions do not require it. It never replaces `article.read`, public-source inspection, or subagent review.

When one article deserves a separate model pass, use `article.study(article_id, question, depth="deep")` to read and analyze the whole article in bounded sections, or `depth="skim", start_line=..., limit=...` for a selected passage. Choose the depth based on the user's purpose, not a fixed article count. Deep study performs an optional article-level merge over the saved source-bound claims and, when that pass succeeds, saves a related `research_note`; the note never replaces segment EvidenceRefs. The tool returns its `card_id`, claims, and optional `research_note_id`. A no-relevant-claims result is legitimate; do not recast it as the author's viewpoint. This study does not verify the author's market assertions.

Call `research.artifact(operation="save", kind="article_card", payload={...})` after reading one article when composing the card yourself. Payload: `article_id`, `question`, `depth` (`skim` or `deep`), `claims` (1-50), optional `limitations`. Each claim needs `kind` (`author_view`, `objective_fact`, `prediction`, `causal_explanation`, `model_inference`), `statement`, an exact `quote` from the article, `line_start`, `line_end`, and the matching `evidence_id` returned by `article.read`; `conditions` and `horizon` are optional. Research confidence is namespaced as `claim_confidence_label` (`high`, `medium`, `low`) plus `confidence_reason`; it is not the numeric Wiki `confidence` field. A deep card requires the whole article to have been read. Save returns a `card_*` ID; `get` returns generated `claim_*` IDs.

For question-focused synthesis, call `save` with `kind="comparison"`, payload `question`, `card_ids` (at least two distinct articles), `relations` (1-30), optional `limitations`. Each relation needs `type` (`agreement`, `disagreement`, `change_over_time`, `complementary`), at least two `claim_ids` from different selected articles, `analysis`, optional `qualification`. The relationship is your interpretation, not proof that the authors are right.

Use `research.synthesize(question, card_ids)` when an independent model pass over several saved cards would help. It compares source-bound claims and dates, then saves a validated comparison record. A `no_supported_relation` result means the selected articles do not establish a reliable comparison; inspect scope or answer separately instead of inventing a consensus. The model sees the cards' claims, not a pasted batch of article bodies.

When a long research turn is repeatedly recovering old article observations, optionally save a `kind="research_note"` record as compact working memory. It is useful for preserving already extracted viewpoints, dates, conditions, open questions and evidence IDs while continuing to read other articles. Payload shape:

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
    "conditions": "Only if real yields continue to fall",
    "time_scope": "2026-09"
  }],
  "open_questions": ["Was the condition observed in independent data?"],
  "limitations": "The note records the article view; it does not verify it."
}
```

Every substantive entry must cite one or more canonical `evidence_*` IDs already observed in this run. Article EvidenceRefs are checked against the current article version when the note is saved and reloaded; an edited source makes the note stale. Notes are immutable, run-local working memory: they do not replace `article.read`, do not prove a claim, do not bypass the final answer audit, and do not write into a subagent's artifact store. The Runtime may advise saving a note after repeated `context.open` calls, but the main Agent decides whether it is useful.

For a typed check, use `research.verify(card_ids, claim_ids?, evidence_ids?, as_of?)` after selecting the important claims and, where needed, reading independent public or market evidence. It returns a review ID, typed statuses, suggested revisions and optional follow-up queries. On `insufficient_evidence`, choose whether to fetch more evidence or explicitly downgrade the final conclusion. `research.verify` is a separate model pass over observed evidence, not an independently browsing subagent.

Alternatively, save a `kind="review"` record manually with `payload={"card_ids":["card_..."],"checks":[...]}`. This is an advanced interface; if unsure of its shape, use `research.verify` instead. Each check needs `claim_id`, `claim_type`, `status` (`viewpoint_faithful`, `supported`, `refuted`, `insufficient_evidence`, `not_due`, `reasoned`), `evidence_refs` (list), `conflicts` (list of `{evidence_id, description}`), `alternative_explanations` (list of strings), `suggested_revision`, `as_of` (YYYY-MM-DD), and `reason`. Put `as_of` inside every check, not at payload level. Keep the original article EvidenceRef on every check. A decisive fact or due forecast needs a separately read public or market EvidenceRef; reopening the same article URL does not count. `not_due` requires a forecast horizon. Causal and model inference can be reasoned but are not certified facts by author agreement. A main-agent review record is not an independent subagent verdict.

Use `research.artifact(operation="list", kind=...)` for bounded IDs and summaries, or `operation="get", artifact_id="..."` to reload one record. Do not pass paths. On a stale source error, reread the changed article and create a new card. Never describe a saved card or comparison as independently verified merely because it was persisted.

When the Runtime replaces an older tool result with `offloaded_observation`, call `context.open(context_id)` if you need that exact observation again. The original tool output and article evidence remain unchanged; an offloaded marker is not a citation. The Runtime records the offload in trace and only does it when estimated model context crosses a pressure threshold.

Recent large observations can also be offloaded. Small restores return the exact object;
large restores return `run.context_view.v1` with continuous original JSON `text`,
`offset`, `returned_chars`, `total_chars`, original `checksum`, and `next_offset`.
Use `context.open` with the same ID and `offset=next_offset` for more, optionally
setting `limit` (1-12000 characters). Pages are not summaries or new evidence.
Successive different pages do not count as repeatedly restoring the same page.
Preserve a task-focused working view when useful; the original remains unchanged.

If the same old observation is restored repeatedly and further context-pressure offload occurs, Runtime raises a `context_progress_required` checkpoint. At that point `context.open` pauses; other research tools remain available. Decide whether to save a new evidence-bound `research_note`, save or receive a validated v2 understanding/synthesis/work view using `versioned_research.md`, update a research-plan goal with a concrete progress note, or answer honestly with the unfinished scope. A new validated and committed v2 result, successful new note, or status-changing plan update with a substantive note releases the checkpoint. Failed saves, reused IDs, unchanged notes, `research.plan get`, and empty updates do not. No legacy card or note is required for the v2 path. Repeated attempts to reopen without progress stop the run with a visible gate result. This is based on restoration/pressure events, not a fixed article count or mandatory analysis recipe.
