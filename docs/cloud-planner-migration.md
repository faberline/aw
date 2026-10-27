# Six issue planners: hosted migration candidate

Status: published implementation candidate, [draft PR #90](https://github.com/faberline/aw/pull/90);
hosted cutover is **not ready**.

Scope: Core, Defer, Keep, Lumen, Relay and Tape issue planning. Keep the existing
two-hour cadence. Do not change product issues or owners as part of validation.

## Scope correction

Issue planning reads product source through GitHub and writes issue metadata or
spec comments. It does not require a local product checkout or shell GitHub
credentials. The initial hosted probe over-constrained the target by requiring
shell `gh`. A native connector adapter now injects canonical REST requests into
the existing deterministic planner and shared guard. Its issue PATCH scope is
limited to labels/milestone and refuses body overwrite, closure and cross-repo
writes. The previous gh-only probe cannot qualify or disqualify this adapter.

The native adapter and current guard passed 28 planner and 75 loop controls.
The native path rejects PR targets and requires operation-specific success
identity/results. Empty responses, error envelopes and mismatched fields retain
the guard for reconciliation instead of reporting success.
Actual hosted connector bindings, endpoint coverage and coordination context
still need live verification. Neither the fixture transport nor tool descriptions
prove the connected GitHub tools can satisfy every canonical API request.

## Candidate

- Portable issue-plan rules, milestone policy, wrapper, Python helpers and
  SHA-256 manifest. Bundle construction checks each file against its immutable
  source commit. Hosted runs must also check against the hashes in their saved
  prompts; a supplied manifest is not self-authenticating.
- `plan.sh --cloud status` has no local checkout, Git-directory or local-lock
  dependency. It remains a read-only, non-atomic snapshot with no write authority.
- Opt-in GitHub Contents API shared guard on a dedicated `aw-coordination`
  branch. Expected blob SHA protects acquisition. New generation UUIDs protect
  against reuse of old free snapshots. No automatic expiry, stealing or retry.
- Matching local planner/worker adapters retain existing flock and issue leases.
  Before an external mutation, they verify the shared owner. Pre-write validation
  refusals release; errors after attempted mutations preserve the lock.

The protocol covers short mutation commands, not the whole coding session.
When `work next` returns, an issue can remain owned under its existing local
lease. A free GitHub guard cannot prove that a coding owner is absent.

## Verification on 2026-10-27

| Check | Actual result |
|---|---|
| `python3 -m unittest discover -s plugins/faberline/skills/issue-loop/tests -q` | 75 passed, exit 0 |
| `python3 -m unittest discover -s plugins/faberline/skills/issue-plan/tests -q` | 16 passed, exit 0 |
| `python3 -m unittest discover -s plugins/faberline/skills/issue-work/tests -q` | 81 passed, exit 0 |
| Independent forward review | Pre-write lock retention and omitted wrapper corrected; guard follow-up passed; malformed SHA error normalized |
| Skill Creator `quick_validate.py` | Rejected pre-existing Claude `user-invocable` metadata on both original and candidate; not a passing Codex schema check |
| Actual hosted runtime, authorized shell `gh`, native dependency API | One read-only hosted probe dispatched; result could not be read, so capabilities remain **unknown** |
| Live shared-guard acquire/contention/release | **not_run** |
| Live writer adoption and hosted-readable sessions/inbox/leases/cutover | **not_verified** |
| Six production hosted task IDs/status/cadence and first natural runs | **not_created / not_run** |
| Immutable portable bundle | Built and verified 8 files at `3b32e84bfde994ab8fe820e85c5df34550922116` |

The existing main checkout has unrelated README and roadmap WIP. It was not
changed. The candidate is isolated in `codex/cloud-issue-planners`.

## Current external blockers

The GitHub connector metadata request failed at its MCP transport endpoint.
The existing `/opt/homebrew/bin/gh` provided a working fallback for publishing
the candidate branch and creating draft PR #90. A later read of the candidate's
check-runs failed with `connect: can't assign requested address`; CI remains
unknown. No issue write was attempted by either failed read.

The published source revision is `3b32e84bfde994ab8fe820e85c5df34550922116`.
The portable context is `/private/tmp/aw-cloud-planners-20261027-3b32e84`.

## Hosted probe

A single read-only capability validation was created and manually dispatched:

- Cloud automation ID: `6ae0c76939b08190bf590bf4c500829b`.
- Run chat: https://chatgpt.com/c/6ae0c77f-9adc-83e8-bf07-b8c941e5fb37
- Its future trigger was paused after dispatch. The paused state was verified
  in the Scheduled UI. It is not a production planner or a recurring duplicate.
- The run record reports a completed turn. The reader returned only a
  `chatgpt-content-reference`, and the browser showed an unavailable response.
  A reload failed with `ERR_ADDRESS_INVALID`. No shell, auth, hash, queue or
  peer result was readable, so none counts as passed.
- A screenshot was saved to this chat's visualization output directory as
  `cloud-validation-error-detail.png`.

This is an attempted hosted validation with an unknown result, not evidence of
either working or absent cloud capabilities. The paused probe can be reused
after results become readable; do not blindly rerun any mutating operation.

The AW proxy has this chat's identity, but only its bootstrap tools are exposed.
`register_session` refused a duplicate and directed the client to `update_session`.
The installed direct client could not connect to its loopback AW endpoint. No
scope update, inbox read or worktree claim was claimed successful.

Current native session/inbox/lease and old-planner pause evidence still need a
hosted-readable source adopted by the live local actors. The shared guard alone
does not implement that source. Missing evidence continues to block mutations.

## Resume

1. Read the actual hosted probe result when available and preserve its outputs.
2. Use the published immutable source and verified bundle above. Preserve the
   source review findings and actual test exits; they do not prove hosted readiness.
3. Establish hosted-readable peer/owner/lease/cutover context without copying AW
   session credentials. Deploy coherent shared-guard support to all live writers
   only after their existing ownership has been reconciled. Preserve all leases.
4. Initialize dedicated workflow-free coordination branches and run the real
   hosted acceptance in `issue-plan/CLOUD.md`. A missing capability blocks cutover.
5. Only after acceptance, prepare the six hosted definitions paused. Pause the
   corresponding local definitions and verify no run remains in flight. Enable
   hosted schedules at two-hour intervals, then verify their first natural runs.

The six local definitions were read back as active, local, and every two hours.
There is no production hosted duplicate, deployment, coordination-branch write,
product issue write, or cutover. The one read-only validation definition is paused.
