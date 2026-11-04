# Planner acceptance mapping (draft)

The planner maps each item in its issue specification to one or more check IDs
from the trusted CI policy. The mapping is separate from the specification.
This avoids a digest that depends on itself.
The intended issue comment starts with
`<!-- issue-plan:acceptance-map:v1 -->`, followed by the JSON on the next line.

```json
{
  "schema": "AW_ACCEPTANCE_PLAN_V1",
  "spec_sha256": "<SHA-256 of the exact UTF-8 planner specification>",
  "items": [
    {"acceptance": "The exact first acceptance item", "checks": ["unit"]}
  ]
}
```

The `## Acceptance` section has one single-line unchecked Markdown item per
outcome. The mapping copies each item exactly once and in the same order. Each
item names at least one distinct trusted check. No command in this data is
executed. The planner must preserve the original human issue acceptance; it
must not drop an item to fit this format.

`scripts/acceptance_plan.py` checks this format, the specification digest, and
the check IDs. It can also check the mapping against an existing complete test
plan and its receipts. Missing, stale, skipped and zero-test receipts remain
failures. Its result is not proof that the checks cover an item. It is
not permission to mark an issue `done`. Before adoption, CI must bind the plan
to the current issue, repository, PR, head, base, policy and test receipts;
verify every required existing test; and reject stale or incomplete evidence.
The comment reader requires a complete ordered inventory and planner identities
from reviewed policy. The draft opt-in setting is
`"planner_acceptance_mapping": {"version": 1, "trusted_actor_ids": [123]}`.
Use the actual numeric GitHub IDs in reviewed policy. A newer invalid or
untrusted comment blocks processing.
It never falls back to an older mapping or specification. Before any lifecycle
write, the caller must freshly read and compare the issue, comments, PR and
trusted policy. The module compares the issue and mapping; it does not itself
authenticate the policy, PR, workflow, comment pagination or GitHub identity.
The draft execution join in `test_plan.py` requires the full existing roster,
current provider inputs and the reviewed `review_readiness.assertions` roster.
This prevents a receipt from defining its own expected test names. The caller
must still authenticate the policy source, enforcement pins and the receipt
producer. Arbitrary PR artifacts are not trusted; the draft result remains
`not_evaluated` and cannot write lifecycle labels.
An independent Codex review must then judge each item and its test coverage.
The review failure path must return the issue to `todo` with its reason.

The planner has a publication command. `planned-execution` can publish a
producer-bound execution receipt, and `verify-planned-execution` can reread
the producer log and recheck the current inputs. Both report
`qualification: not_evaluated`. The CI writer and review are not wired to
this data yet. Do not enable the policy or use these operations to clear a
qualification blocker until the complete flow has been independently reviewed.
