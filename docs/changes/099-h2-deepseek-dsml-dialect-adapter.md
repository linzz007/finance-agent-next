# H2 DeepSeek DSML Dialect Adapter

Date: 2026-10-21

Status: implemented as a bounded offline MVP.

## Finding

The shared text-action parser already accepted the older DeepSeek-style form:

```text
<DSML calls><DSML invoke name="tool_call">
<DSML parameter name="tool_name" string="true">article.read</DSML parameter>
<DSML parameter name="arguments" string="false">{...}</DSML parameter>
</DSML invoke></DSML calls>
```

The latest live trace contained the equivalent dialect with
`invoke name="tool.call"` and `parameter name="name"`. H2 rejected that
response before the normal action/schema/permission checks. This is a provider
text-protocol compatibility defect, not a reason to enable native function
calling or to loosen JSON validation.

## Contract

- Accept exactly one complete DSML calls envelope.
- Accept `tool_call` and `tool.call` as the invoke name.
- Accept `tool_name` and `name` as aliases for the internal `tool_name` field.
- Accept `arguments` only as a JSON object when `string="false"`.
- Reject duplicate aliases, unknown parameters, multiple invokes, extra body
  text, malformed nested JSON, and all unrecognized XML/prose.
- Convert the result to the existing internal `type=tool_call` JSON action.
- Continue through the existing action model, tool schema, permission and
  business validation. DSML is never executed directly.
- Keep the raw provider text in the audit artifact; no provider-specific text
  becomes evidence or an observation by itself.

## Scope

This fixes a conventional provider-compatibility boundary only. It does not
change evidence-anchor semantics, research stopping rules, model selection,
budget rules, or the rule that native provider tool calls are unsupported.

## Verification

The offline suite covers both dialects, ASCII and full-width markers, valid
arguments, invalid JSON arguments, duplicate aliases and extra parameters.
A live-model rerun is intentionally deferred until the model account has
available quota.
