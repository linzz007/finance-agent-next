# H2 Required Review Time Window

Date: 2026-10-28. Status: implemented; offline and focused live verification recorded separately.

## Finding

The r6 live SCH-02 trace had a 20-minute Analyst Task window. After a first Verifier
returned `revision_required`, the Analyst revised the draft near the end of its
window and spawned a second Verifier with only about eight seconds available.
The Worker then timed it out. The Run itself still had time and calls remaining;
the failure came from per-Task window allocation, which silently shortened an
omitted default to whatever time remained after the parent's finalization reserve.

## Change

- New fixed-policy Runs keep the ordinary child default at 20 minutes and give an
  Analyst dispatched with `review_mode=required` a 45-minute default window.
- The 45-minute request is within the Root's 60-minute Run deadline and 10-minute
  closing reserve, leaving five additional minutes beyond the protected Root reserve.
  Root/child per-Task call limits and the 600-call Run ceiling are unchanged.
- New fixed-policy Runs may shorten an ordinary child window to the parent's
  remaining research time, but enforce a 10-minute minimum. Below that floor,
  `agent.spawn` fails before creating a Task and returns bounded `required_ms`,
  `available_ms` and `window_kind` fields. The 45-minute required-review window
  is never silently shortened. A deliberately shorter explicit `wall_time_ms`
  remains available when the complete bounded task can fit.
- The selected window policy is saved in the immutable Run Brief. Older fixed
  Runs without this field keep their previous shrink-to-fit behavior; hierarchical
  Runs are unchanged.
- Required-review Prompt guidance asks Analysts to begin the review cycle early
  enough for the Verifier, revision and any required re-review. A five-round cap
  is still a maximum, not a guarantee that five full rounds fit in one Run.

## Acceptance Boundary

Offline tests must show that required review receives the longer allocation,
ordinary tasks retain the 20-minute default when available, ordinary nested
tasks can use a shortened window above the floor, windows below the floor or
unavailable required-review windows fail before Task creation with accurate
remaining-time diagnostics, and an explicit shorter window still works. Live SCH-02 must be judged on actual Verifier completion,
revision and a `passed` verdict bound to the exact final Analyst payload. Root
delivery or a successful Verifier Task alone does not pass the case.
