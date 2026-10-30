# Guard cutover and activation

Use the reviewed [activation contract](../../../../docs/standards/issue-guard-activation.md)
with [DEV-PIPELINE-1](../../../../docs/standards/development-pipeline.md).
The runtime now authenticates a finite activation record. Missing evidence
still refuses guarded writes. Publishing the code does not activate a repo.

A complete full-version-2 policy can enter drain through separately reviewed
protected-policy adoption. Require its exact-head full CI and independent
product review. Entering drain removes authority. It cannot enable a guard,
reset an existing drain nonce, or enable recovery. AW itself has no Claude gate.

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
hold. Each consumer must supply the actual proof and a real hosted merge,
canonical issue closure/readback and safe recovery result. A passing framework
suite or a declaration of actors does not supply deployment acceptance.

`scripts/rollout_activation.py` authenticates the merged data-only AW record,
exact runtime, actor audit, completed drain, current provider rules, active
workflow definitions and named conformance controls. It rechecks the live
queue fence and current AW revocation state. The data registry is
`.github/issue-guard-rollouts.json`. Do not publish an authorized record from
an empty local inventory or an unverified actor report.

New normal-development deployments use the schedule-free
[`issue-recovery-v2.yml.example`](templates/issue-recovery-v2.yml.example).
Bind actual workflow IDs and exact definition blobs. Its policy can remain
explicitly disabled during preparation. The authenticated disabled entry is
an inactive no-op, not recovery evidence or permission to activate a guard.

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
