# Optional Versioned Research

After research.plan, decide whether to analyze directly, delegate, or combine
both. Explicit analyst@2.0.0 accepts task-focused coherent understanding,
synthesis or a working view. It does not require claims-only results or a fixed
sequence; omitted/1.0.0 keeps the old contract. No automatic keyword routing.

Example delegate action (replace example IDs with known tool results):

```json
{"type":"delegate","role":"analyst","role_version":"2.0.0","research_task":{"schema_version":"finance.research_task.v1","original_query":"The user's question","objective":"Explain the selected article's reasoning and important conditions","material_refs":["article_example"],"requested_result_kind":"article_understanding","completion_expectation":"Give a coherent source-linked explanation and disclose gaps","reading_request":{"material_ref":"article_example","depth":"deep"}}}
```

Do not duplicate legacy task/input_artifact_refs. Runtime normalizes known
lookup IDs into scoped material handles, binds source versions and assembles
short deep articles in full. A skim requires explicit nonoverlapping inclusive
start_line/end_line ranges or select_ranges=true, never both. Oversized input
requires you to coordinate bounded skim range tasks and article-level integration;
do not claim that excerpts equal full deep understanding.

For synthesis, supply understanding_*/synthesis_*/view_* or existing card/note IDs
as material_refs and requested_result_kind=synthesis, with the actual objective.
Original article expansion requires its separate grant, not merely a reference
inside an understanding. Returned source evidence is inherited, not independent
verification. Decide what matters, what disagrees, and whether to read more.

The public result body is finance.research_result.v1:
schema_version; nonblank content; evidence_links [{text,evidence_refs,quotes?}];
material_disposition [{material_ref,decision:considered|excluded|unprocessed,reason}];
gaps [{description,material_refs?,next_need?}]; limitations [string]. Optional
supporting_artifact_refs and candidate_answer retain authorized lineage. Link
text must occur in content/candidate_answer; quotes must match observed sources.
No fixed financial headings, sentence-by-sentence claim extraction, or forced
short explanation. Absence of a disposition stays unknown.

The parent receives full valid results when budget allows, plus a research_receipt
with saved artifact_id or explicit storage failure and inline result_checksum.
Use research.artifact(get) to restore the complete current-run result. v2 list
requires schema_version=finance.research_artifact.v2; follow next_cursor until
listing_complete, restart on stale_cursor. Saved does not mean seen or accepted.
Direct main-Agent v2 save uses the same body/task contract and actual input scope.

Before final delivery, record research.disposition for each returned valid result:
use, qualify, exclude or follow_up with reason. Continue the normal evidence
coverage, verification checkpoint and delivery policy; this capability does not
choose conclusions for you. Failed execution may leave valid analysis but is not
successful delegation or independent review. Preserve useful analysis and gaps.
