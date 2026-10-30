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
are deferred during this stage. When no review status exists, the writer first
publishes and reads back a blocking pending status. An unknown write result
holds without a blind retry.

A drain result is not proof that cutover is safe. A worker can have read the
old policy before the marker appeared. Old worker versions can ignore the
marker. A passing drain result also does not prove that an existing lease has
ended, or that a remote actor has stopped. Before activation, a reviewed
procedure must prove the installed versions and launcher inventory, drain all
in-flight operations and owned leases, verify provider protection and requests,
and bind that evidence to the exact policy and runtime. Unknown evidence must
hold. This proof and a real hosted merge and issue-close test are not complete.

Do not change planner schedules as part of this work.

`scripts/rollout_inventory.py` provides a read-only Actions inventory for a
controller. It reads every page for queued, in-progress, requested, waiting and
pending runs twice. It refuses incomplete, changed or malformed results. It
always reports actor rollout as unknown and cannot authorize a write. Even an
empty result does not prove that local or cloud launchers stopped. The caller
must independently identify all launchers, their installed versions and their
in-flight work before considering a cutover.

`scripts/local_rollout_inventory.py` can compare one supplied local skills
directory with four caller-supplied SHA-256 values: the worker entry point,
worker guard, GitHub guard provider, and guard ledger. It reads a supplied Git
common directory's `issue-work.lock` without changing it. A matching entry
point is needed because the guard files alone do not show that the launcher
uses them. The expected hashes are inputs, not an authenticated policy.

The local report distinguishes missing and different files, and reports an
observed lease without exposing its owner. It refuses ambiguous or oversized
evidence, symlinked evidence files, and changes it detects between reads. A
file can change and be restored between reads. The report never proves a
process has stopped, that a missing lease means no worker exists, or that all
launchers were observed.
Every report states `actor_rollout: unknown`, `quiescence_proven: false`, and
`authorizes_writes: false`. It does not enable `verify_rollout` or change any
schedule, lock, issue, or repository setting.
