# H2 Deterministic Read Cache

Date: 2026-10-21

Status: MVP design; scoped to the fixed H2 finance action loop.

## Problem

Q01 completed successfully after the decision-guidance change, but the Root
requested `article.list` three times with the same effective parameters. The
model had already received the result. This is not a permission or evidence
failure, but it adds latency and can consume backend work while the model is
trying to converge.

## Rule

Within one Task, Runtime may reuse a successful result when all of the
following match:

- the tool is a local metadata/artifact read whose version is stable for the
  execution: `article.list` or `material.get`;
- the arguments have passed the existing strict schema and their canonical JSON
  is identical;
- the prior result belongs to the same durable Task execution and is still
  loadable.

The cache is an execution-state index from canonical tool arguments to the
original tool-result artifact. It is not a cross-run cache and does not grant
new source permissions. Article body reads, claim extraction, search, failed
results, public web calls, scheduler actions, and calls with different
arguments are never reused. Body reads stay uncached because an article can be
reindexed during a durable Task; the cache key does not yet carry a
source-version fingerprint.

On a hit, Runtime records a normal logical successful tool observation with
`cache_hit=true` and the original result reference, then feeds that payload
through the existing evidence/state update path. It does not call the corpus
again. The model still decides what to do next; Runtime only avoids an
identical deterministic read.

## Verification

Offline coverage must prove exact-argument reuse, changed-argument execution,
failed-result non-caching and checkpoint persistence. The next focused live
Q01 run should show at most one physical `article.list` for the same argument
set. This optimization does not certify answer semantics or real Redis.
