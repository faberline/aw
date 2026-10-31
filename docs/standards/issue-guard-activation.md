# Historical cloud completion protocol

The 2026-10-31 revision of [DEV-PIPELINE-1](development-pipeline.md) moves
review and completion out of cloud CI. This file records the retired cloud
activation design. Do not enable it for the normal-development pipeline.
Preserve existing owners, leases, ledger records and historical evidence.

# Guard activation for normal development

This document supports [DEV-PIPELINE-1](development-pipeline.md).
AW owns the shared verifier. Each product owns its deployment inventory.
This document does not activate a repository.

The initial scope is Core, Lumen, Keep, Tape, Defer and Relay, in that order.
AW has no Claude review gate. Product CI and independent Claude review remain
required. Release pipelines and schedules are outside this change.

## Enter drain

Drain stops new work while existing owners finish safely. Deploy the reviewed
writer, the inactive version 2 recovery workflow, and the complete actor
manifest. An actor is a worker or service that can claim or write issue work.
List every supported worker launcher, writer, recovery service and controller
in `.github/issue-guard-actors.json`.

Use one stable 32-character hexadecimal nonce for the rollout. Set
`issue_guard_cutover` to version 1, phase `drain`, and that nonce. Do not set
`issue_guard` or enable recovery yet. Both policies must retain full version 2
qualification with no unresolved blockers. Existing guard policies cannot
return to legacy claims through this path.

Capture the actual default-branch writer run. It must withdraw all native
merge requests and publish an authenticated drain record. The live verifier
also reads every open PR. Every PR must have a blocking verified-merge status
and no queued merge request. It reads the complete queue twice to detect
changes. Empty local lock files do not prove this condition.

## Audit actors

Check each supported launcher and actor after drain completes. Install the
exact guarded worker, provider, ledger and entry code. Fence old entry points
so they cannot create an unguarded claim. Preserve current work and leases.
Do not remove another session's lock or infer ownership from activity times.

Record each actor's identity, rollout nonce, actor manifest blob, exact runtime
hashes, audit digest, observed time and drain source. Activation requires no
remaining old in-flight work or leases. An unknown actor keeps activation
blocked. A report alone is not proof that a launcher was fenced. The deployment
audit must establish this fact before the operator publishes authorization.

The guard namespace must have its verified genesis and active no-bypass rules.
The actual writer and recovery workflows must be active and match their
reviewed IDs and definitions. Version 2 recovery uses writer completion and
explicit wake events. It adds no schedule. Start from the shared
[version 2 template](../../plugins/faberline/skills/issue-ci/templates/issue-recovery-v2.yml.example).
Use actual provider IDs and immutable action and definition hashes.

## Authorize the exact deployment

Publish the audited record in `.github/issue-guard-rollouts.json` through a
data-only AW PR. The record binds one product, one nonce, the exact policy
digest, actor manifest, actor audit, drain run and conformance run. Its
`authorization` contains only the actual AW PR number. It must not contain
its own future CI run ID.

The complete AW control suite must pass with zero skipped controls. Its final
step records the actual clean checkout commit and tree. The verifier discovers
the latest control run for the merged data PR's exact head. It checks the
actual tested tree against the immutable authorization source. An older green
run cannot replace a newer failure or an unknown run.

The conformance receipt binds the exact named passing controls in
`rollout-conformance.json`. These controls cover merge, close-only retry,
ownership changes, failure return, unknown provider outcomes and source
identity. They supplement the product's full six-stage acceptance. They do
not replace product tests or set a product job count.

The candidate policy points to the merged AW data commit with
`issue_guard_rollout`, version 1, `source` and the same `epoch` nonce. The
verifier checks the current AW registry again. A revoked or changed record
cannot authorize a write. Recheck the live drain before activation.

## Complete and recover

Product adoption still requires exact-head full CI, independent Claude PASS,
the reviewed grant and the guarded publisher. Merge must be confirmed before
the canonical issue can close. Read back the issue after closure. An uncertain
merge or close response requires observation before another mutation.

The guarded publisher requires `issues: write` for explicit issue closure.
Its exact reviewed workflow and guard policy must authorize this permission.
Live rollout and ownership checks still run before a write. A publisher with
only `issues: read` cannot enter the guarded completion path.

GitHub can clear a CI run's PR links after merge. The dedicated read-only
merged path authenticates the actual merged PR, repository, default target,
head, base and current run attempt. It also checks the original review's exact
gate records. It cannot qualify an open PR with missing links or authorize a
new merge. A closed issue can be read as historical applicability only after
actual merge identity is established. Ownership and close permission are
checked separately against the real current issue.

Keep the rollout issue open until the deployed product proves actual merge,
explicit issue closure and safe recovery. A passing framework suite or an
empty registry alone is not deployment acceptance.
