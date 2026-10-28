# H2 Result Read Coverage Across Durable Actions

Date: 2026-10-19

Status: implementation audit against existing contracts. No new financial
policy, mandatory full read, paid test or default-path switch is authorized.

## Authority And Gap

[013 section 8](013-harness-v2-dispatch-baseline.md),
[interface section 7.2](../harness-v2/interface-contracts.md) and approved
[032](032-h2-durable-action-continuation.md) together require:

- result.get pages retain immutable result/view/section identities;
- full_viewed is recorded only after contiguous complete coverage;
- every ordinary action checkpoints and continues in a new Attempt of the
  same Task; a new Attempt does not erase its persisted research history;
- a user's export/read is not proof that the parent Agent read a child result.

Code currently accumulates page ranges only for identical reader_attempt_id.
Consequently, two consecutive result.get actions can cover a long result in
the same Task while the completion event is never recorded. Direct tests using
one unchanged caller did not exercise this durable-action boundary.

## Existing-Contract Repair

- Accumulate ranges by result version/ref, reader Thread and Task, reader kind
  (agent versus trusted user), view and section. Preserve each page's actual
  reader_attempt_id, and place the completion event on the completing Attempt.
- Do not combine other Tasks in the same Thread, sibling readers, trusted user
  exports, different result versions, views or sections. Gaps still prevent a
  completion marker. No event certifies model understanding or citation use.
- Deduplicate completion per stable read scope, not per transient Attempt.
  Preserve old page events and all immutable result/artifact records.
- Old agent page events lacking explicit reader Task/kind can be scoped using
  their recorded Attempt's actual Task/Thread. Unscoped old trusted-user reads
  must not be attributed to an Agent or inferred to belong to a follow-up Task.
- No DDL, data migration, permission expansion, synthesized evidence or forced
  parent reading. The model still decides which authorized result pages to get.

## Offline Acceptance

- Read a prefix, continue the Task to a fresh Attempt, read the suffix and assert
  exactly one completion event with truthful page/Attempt associations.
- Cover summary, full and section views, and deduplication after another Continue.
- Keep trusted-user export, separate reader Tasks (including a follow-up in the
  same Thread), disjoint view/section and result identity isolated.
- Verify older recorded agent pages can be recovered without rewriting history;
  old unscoped user reads cannot complete an Agent's coverage.
- Exercise actual model-selected result.get actions through the opt-in finance
  action loop with a fixture adapter. This is not live-model acceptance.

## Verification

Before the repair, the three cross-Continue view tests and the old-agent-page
recovery case each reproduced a missing completion event. Five isolation cases
kept their existing no-false-completion assertions but lacked the newly explicit
reader_task_id audit field; the old unscoped user-export control passed. Overall
the new checks were nine failures and one pass, not nine independent leakage
defects. After repair, 13 foundation cases and one direct action-loop case cover
this boundary; the latter also runs notification-first after 040. The final H2
collection passed 536 cases with seven real-Redis skips in 206.81 seconds;
default finance/shared-runtime passed 1606 cases with 30 skips in 123.39 seconds.
All requests used isolated SQLite/artifacts/FakeRedis and fixture adapters.
The manually supplied fixture result isolates retrieval, not child-model analysis.
Real Redis and post-037 paid return validation remain unproved.
