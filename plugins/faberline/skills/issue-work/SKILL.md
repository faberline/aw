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

`next` selects highest-priority todo then oldest, or resumes the recoverable
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
