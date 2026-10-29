# Guard cutover draft

This code is not a rollout procedure. Do not add the cutover marker to a
repository yet. The policy publisher refuses that change. The rollout
verifier also refuses guarded writes.

The proposed marker is `issue_guard_cutover` with version `1`, phase
`drain`, and a unique 32-character lowercase hexadecimal nonce. The marker
cannot coexist with `issue_guard`. A worker that sees it refuses new claims.
An existing unguarded owner can finish its own lease. A guarded owner must
preserve its lease and guard.

The trusted writer scans the complete open PR queue during drain. It withdraws
its own previous approval and native merge request. It holds a PR if a request,
status, head, or base cannot be verified. It does not cancel another actor's
request. It does not change issue labels or authorize a merge. Failure outcomes
are deferred during this stage.

A drain result is not proof that cutover is safe. A worker can have read the
old policy before the marker appeared. Old worker versions can ignore the
marker. A passing drain result also does not prove that an existing lease has
ended, or that a remote actor has stopped. Before activation, a reviewed
procedure must prove the installed versions and launcher inventory, drain all
in-flight operations and owned leases, verify provider protection and requests,
and bind that evidence to the exact policy and runtime. Unknown evidence must
hold. This proof and a real hosted merge and issue-close test are not complete.

Do not change planner schedules as part of this work.
