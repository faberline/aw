---
name: issue-work
description: "Implement one todo issue in an isolated worktree, claim todo -> doing, run affected unit tests, and open/update its PR while CI owns completion."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh *)
---

# /faberline:issue-work

Work one issue per run, then stop. `${CLAUDE_SKILL_DIR}` is this skill's
folder (use its absolute path in Codex/AGY). Never start or restore schedules.

## Ownership and recovery

Set `ISSUE_WORK_OWNER` to a stable unique token for this conversation, and
retain it across invocations. Run `scripts/work.sh next` from the repository
checkout. A kernel guard serializes commands; a persistent lease preserves
coding ownership between commands. Exit 3 means busy/idle: report and end.
Only this conversation may use `next --mine` with its token to resume its
own lease. TTL never steals a live PID's lease. Automatic stale recovery
requires an expired lease, the same hostname, and a dead recorded session
PID. Legacy/malformed/foreign leases refuse; verify the old session ended
before manually reconciling metadata, preserving its branch and worktree.
Never bypass a lock just because a run took longer than expected.

`next` selects already-planned todo by priority, then creation time, or
resumes the recoverable
lease's doing issue in coding phase (including an active CI retry with a PR). Submitted doing issues wait for CI and are
not selected again as coding work. A CI-failed todo reuses the SAME open PR,
branch and registered worktree, including local commits and uncommitted
work. Multiple matching PRs refuse. No branch reset, forced push, destructive
cleanup, or unrequested rebase. If remote/local history diverges, report it
and resolve deliberately while preserving both writers' work.

## Work

1. Run `scripts/work.sh next`; read `issue=`, `branch=`, `worktree=`, `pr=`.
2. Read `gh issue view N --comments` and the associated PR comments/check
   failures. The issue's Goal, Acceptance and Never sections are the contract.
3. Write only in the printed worktree. Keep scope bounded. Run affected unit
   and static checks only. Do not run local full suites, e2e/integration,
   cluster commands, or workflow dispatch. Report cloud validation still
   needed: MR affected unit/functional/e2e/performance; release complete
   declared functional/performance qualification.
   Classify tests by behavior, not the command name or filter. A pure helper
   assertion with isolated in-memory inputs is a local unit. A test that launches
   the product CLI (e.g. `relay profile`, `relay render`, `--out`), checks CLI
   exit/output/files, starts a server/child product process, or exercises network,
   Raft/cluster or performance behavior is functional/integration and belongs in
   authorized cloud CI, even when offline or selected by `cargo test -- ...`.
   Running the isolated unit/static harness itself is permitted; no blanket
   subprocess ban or new command permissions are introduced. Inspect selected
   test bodies/fixtures before running them locally.
4. Commit completed work, write a concise summary outside the worktree with
   changes, tests passed and tests not run. Call
   `scripts/work.sh finish N summary-file`. It pushes normally and opens or
   updates the same PR with `Closes #N`, releases the lease, and leaves doing.
   CI alone marks done after required current-head gates pass, or todo on
   failure. Finish never calls a lifecycle-writing push helper.
5. If blocked by a decision or repeat failure, write evidence/a clear question
   to a file and call `scripts/work.sh block N evidence-file`. This compatibility
   command comments only: state and lease stay unchanged. Stop and report;
   never mark blocked, done, or todo yourself.

Never merge, close issues, delete branches, force-push, manually change
lifecycle labels, or run bare `git stash`. AGY's sole lifecycle transition
is `todo -> doing`. Exit 2 means refused: preserve work and report evidence;
fix a justified scoped problem or wait for human direction.

## Planner milestones and planned work

See the [planner admission contract](../issue-loop/MILESTONE-QUEUE.md).
Claude plan uses milestone versions before priority for new/adopt admission;
empty milestones do not block, while earlier unresolved work remains visible.
AGY consumes already-planned todo by priority and creation time, without a
milestone frontier or dependency-parser eligibility filter. Preserve active
leases, dirty worktrees and same-PR CI repairs. Core milestones are repo batches,
not crate versions. No schedule, release/test gate or done change is implied.

## Primary main freshness

After guard/owner checks, `next` fetches all remote heads, binds exact origin/main and reports a
[primary checkout FF attempt](MAIN-CHECKOUT.md). Update only clean, checked-out
main with positive exclusive owner coordination (`ISSUE_MAIN_EXCLUSIVE_OWNER`
matches the existing owner token), no lease/conflict and fast-forward history.
Dirty/untracked, ahead/diverged, non-main, active/unknown owner or concurrent
change holds maintenance; never stash/reset/clean/rebase or hide user files.
That hold does not stop safe fresh-origin issue work or change its original PR
worktree. Do not set the coordination token merely because a lease is absent.

Configured Git filters hold main maintenance before status/checkout; hidden
index flags and sparse state also hold and are never cleared automatically.
An existing PR with no available branch after fetch must hold, never fall back
to creating its repair from main. Install the shared index guard with the worker.
