# H2 Isolated Correction Input And Single JSON Fence

Date: 2026-10-20. Status: user approved; implemented and offline verified.

## Approval And Scope

The user explicitly selected both refinements after R16: isolate rejected-output
correction input, and normalize one outer Markdown fence around one JSON object.
This supersedes the pending-choice notes in 060-064, not their recorded results.
The existing [030](030-h2-action-contract-handoff.md),
[037](037-h2-child-action-envelope-and-budget-errors.md) and
[048](048-h2-correction-transport-retry.md) repair limits remain unchanged.
Root and research children share this behavior on the explicit H2 path only.
No default-1.0 parser, provider-native calling, financial research method,
permission, model, DDL, budget or reserve-backed closing policy changes.

R14's fabricated-source analysis and R16's fabricated tool description motivate
this boundary. They do not prove that isolation will cure every contract or
semantic failure. A rejected response is not a real observation, even when it
contains convincing tool markup or a plausible analysis.

## Correction Input Contract

Later refinement: [073](073-h2-framework-informed-anchor-repair.md) introduces a
research-only, field-validated, immutable-draft view for anchor-only failures.
Its patch cannot rewrite analysis, refs, quotes or execute tools. All other
rejected-output isolation and existing correction limits below remain in force.

1. Retain the original trusted messages, task, actual source observations and
   granted context unchanged. Append one correction instruction and the current
   action schema; research children also receive the final.output schema.
2. Do not append the complete rejected body, an assistant replay of that body,
   or a handle inviting the model to load it. This also applies to short outputs.
   The original public-output artifact remains intact under existing credential
   redaction; the audit links the original call/output and correction call/output.
3. Expose bounded validator field paths/types/messages through the existing
   allowlist (at most 12 errors), never validator input or arbitrary error context.
   These locate schema and evidence-anchor errors; do not quote rejected analysis
   or evidence values as source material.
4. If the rejected body fails JSON decoding, expose its decoder message, raw-text
   zero-based character offset and one-based line/column. Provide at most one
   160-character local syntax excerpt around the position. Preserve only JSON/
   fence/markup punctuation and whitespace; replace every other character with
   `?`. It is explicitly untrusted syntax, never a tool result or factual evidence.
   Omit at least one character of the original, even for short bodies; a body of
   zero or one character has no excerpt. Valid JSON schema failures need no text
   excerpt. A non-object JSON value receives a top-level-object diagnostic;
   other decoder value limits receive a generic json-value-rejected diagnostic,
   without echoing their values or turning diagnostic preparation into a failure.
5. Reconstruct the corrected action solely from the original task, real context
   and schemas. Preserve useful analysis from that real context, not fabricated
   content in the rejected response. A useful rejected candidate stays in audit
   for inspection, not automatically promoted into evidence or accepted delivery.
6. Exactly one semantic correction in the same Attempt/model/original budget and
   deadline. Only its first transport_error can retransmit identical messages
   once under 048. All physical requests are charged. No original-decision retry,
   additional research, model fallback, automatic budget increase or hidden call.

The repair audit records the input-policy version, bounded diagnostics and
original output reference, without making that reference part of model feedback.
This reduces contamination, not a claim of complete prompt-injection resistance.

## Single-Fence Parsing Contract

After the complete provider response and terminal status are validated, the H2
adapter may remove exactly one full outer fence: a bare triple-backtick opener
or `json` opener (case insensitive), newline, JSON-object body, newline, and a
triple-backtick closer. Outer whitespace and CRLF are allowed. No other fence
delimiter may occur inside that wrapper. The body must begin with `{`; normal
strict JSON parsing must consume the entire body and return one object.

Do not extract an object from surrounding prose, fix broken JSON, accept extra
text/XML, multiple objects, arrays, nested/repeated fences or a different language
tag. Existing narrow unfenced text-tool compatibility is unchanged; a fence does
not add a new markup envelope. Existing action/schema/parameter/permission checks
still run unchanged after normalization. A fenced invalid action stays invalid.

Normalization changes parsing only. Store the original extracted public text
(fences included) in the output artifact, not the normalized JSON. Existing wire
codecs trim outer whitespace; this is not byte-for-byte transport capture.
No tool is executed before complete parsing,
validation and the normal execution boundary. A valid fence uses the same one
physical request, without consuming a correction. Truncated streams cannot
produce an action. Research inner schema validation and optional malformed-final
state_update body retention keep their existing boundaries.

## Verification Plan

- Red/green regressions: Root/child correction, fake XML tool result, rejected
  financial prose, short/large bodies, schema/anchor paths, raw audit retention,
  syntax positions/bounds, identical transport retransmission and terminal limits.
- Chat/Responses, streamed/nonstreamed adapter checks: accepted bare/json fences,
  strict negative cases, original text retained, unchanged schemas, no extra call,
  no early streamed action or implicit native tools.
- Complete H2/state/compatibility/live-driver regression; default finance baseline,
  configuration separation, scoped full Ruff and diff/link checks. All offline
  checks use fixtures and temporary databases, not live corpus or credentials.
- Freeze a separate bounded paid protocol after offline completion. Keep old
  batches immutable; report delivery, scheduling/lineage and semantics separately.
  Real Redis, missing business adapters, review quality, production load and the
  unapproved closing policy are not certified by this change.

## Actual Offline Verification

The pre-implementation run reproduced rejection of valid bare/json fences and
whole-body replay in correction feedback. Test-fixture long parameter IDs and
format placeholders were repaired separately, not counted as runtime defects.
There are 63 net added cases. The final four affected modules pass 447 tests
(273.03 seconds). Full H2/state/compatibility/live-driver regression passes
**888 tests / 7 skipped** (474.38 seconds). Default finance/shared-runtime passes
**1638 / 30 skipped**, 52 modules (208.90 seconds). Configuration/isolation is
10/10, with zero model calls. Scoped full Ruff and git diff checks pass; four
relevant documents initially resolve 155 local file links.

The skips remain existing environment/optional coverage limits, not new passes.
Both broad runs report the existing Starlette/httpx deprecation warning. Default
1.0 is unchanged. No live user data, credentials, Docker, database migration or
Git commit/push is involved. Paid revalidation uses a separate fixed
[066 protocol](066-h2-isolation-live-protocol.md); offline success is not a claim
that R14/R16 semantics, real Redis or production reliability are solved.
