# Six issue planners: hosted migration candidate

Status: implementation candidate; hosted cutover is **not ready**.

Scope: Core, Defer, Keep, Lumen, Relay and Tape issue planning. Keep the existing
two-hour cadence. Do not change product issues or owners as part of validation.

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
| Actual hosted runtime, authorized shell `gh`, native dependency API | **not_run** |
| Live shared-guard acquire/contention/release | **not_run** |
| Live writer adoption and hosted-readable sessions/inbox/leases/cutover | **not_verified** |
| Six hosted task IDs/status/cadence and first natural runs | **not_created / not_run** |

The existing main checkout has unrelated README and roadmap WIP. It was not
changed. The candidate is isolated in `codex/cloud-issue-planners`.

## Current external blockers

The GitHub connector metadata request failed at its MCP transport endpoint, so
publication and real hosted validation have not succeeded. No issue/API write
was attempted by that failed read.

The AW proxy has this chat's identity, but only its bootstrap tools are exposed.
`register_session` refused a duplicate and directed the client to `update_session`.
The installed direct client could not connect to its loopback AW endpoint. No
scope update, inbox read or worktree claim was claimed successful.

Current native session/inbox/lease and old-planner pause evidence still need a
hosted-readable source adopted by the live local actors. The shared guard alone
does not implement that source. Missing evidence continues to block mutations.

## Resume

1. Finish the follow-up source review and preserve its findings and actual exits.
2. Publish the candidate at an immutable AW commit. Build and verify the portable
   bundle from that commit. Do not claim publication from a local commit alone.
3. Establish hosted-readable peer/owner/lease/cutover context without copying AW
   session credentials. Deploy coherent shared-guard support to all live writers
   only after their existing ownership has been reconciled. Preserve all leases.
4. Initialize dedicated workflow-free coordination branches and run the real
   hosted acceptance in `issue-plan/CLOUD.md`. A missing capability blocks cutover.
5. Only after acceptance, prepare the six hosted definitions paused. Pause the
   corresponding local definitions and verify no run remains in flight. Enable
   hosted schedules at two-hour intervals, then verify their first natural runs.

The six local schedules remain active every two hours. There is no hosted
duplicate, deployment, coordination-branch write, product issue write, or cutover.
