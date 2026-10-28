# H2 Follow-Up Context Handoff

Date: 2026-10-19. Status: implemented; offline verified against the existing contract.

## Authority And Defect

[Frozen baseline](013-harness-v2-dispatch-baseline.md) and
[interface sections 3 and 5](../harness-v2/interface-contracts.md) require a
terminal Thread follow-up to create a new Task with its new objective, explicit
success criteria and budget. The previous result is an authorized reference,
not a replay of the old assignment or automatic source verification.

The repository stores success criteria inside the follow_up brief, but does not
normalize them into the TaskBrief consumed by research roles. Finance state
initialization also reopens an inherited legacy child_request as the assignment,
and the model context omits the previous result reference. Consequently the
new task can receive a generic objective, empty criteria, and the old task text,
despite the follow-up message being present in its mailbox.

This finding is independent of R9 S02's failed correction transport. It does not
prove the cause of any frozen live failure or authorize another paid run.

## Repair Boundary

- Normalize existing stored follow-up criteria into the runtime TaskBrief;
  maintain compatibility with already stored briefs, without a migration.
- Initialize new research follow-ups from the actual follow-up message. Unwrap
  inherited material grants, but do not reuse the old research assignment.
- Preserve the previous Task/result handles and new criteria as compact
  follow_up_context across Checkpoints and expose them to Root/research models.
- Previous result bodies remain on demand through authorized result.get. A
  reference alone is not a read, a citation or evidence coverage.
- Keep the original Thread/Task/Attempt separation, grants, deadlines, model,
  permissions, one-correction policy and default 1.0 path unchanged.

## Verification

Before runtime edits, nine targeted checks failed with missing criteria,
missing result handles and replay of the original assignment. After repair,
13 focused checks passed, including direct/legacy role follow-ups, missing
messages, Root visibility, persisted continuation and an actual fixture
same-Thread/new-Task flow with an explicit prior-result read.

The complete H2/state/compatibility/live-driver collection then passed
**619 / 7 skipped**, 245.42 seconds. This adds ten net cases over 045, with one
existing repository case strengthened. Read auditing distinguishes a retained
handle from actual Agent consumption; no automatic old-result body injection
or evidence promotion was introduced.

These checks made no paid requests and used no real Redis. Default-finance
compatibility is rerun with the subsequent 048 repair and recorded separately;
the 619 result is not a new default-finance or live-model acceptance result.
