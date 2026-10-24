---
name: issue-review
description: "The review step of the issue loop. Each run takes the oldest open GitHub issue labelled done in the current repository, reads the issue, the PR, its diff, and its CI checks (it runs no tests), then independently verifies current-head CI and records its review without merging or changing lifecycle. Use when a schedule or the user asks to review the next done issue."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh *)
---

# /faberline:issue-review

Review one issue per run, then stop. This is the acceptance gate after
`/faberline:issue-work`. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/review.sh`; your job is the judgement.

`${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md. Claude Code sets it.
In Codex or AGY, use the absolute path of that folder instead.

## Runs

Each run does one pass, then ends. This skill never starts a loop or a
schedule. The controller sets the schedule outside the skill. Ignore any
argument, such as `loop` or `once`.

Each run reviews one issue and ends its turn. Durable PR comments contain
a per-issue/head receipt authenticated against the reviewer's current `gh`
user identity. `next` skips that identity's already-reviewed heads (pass,
fail or block), scans the next done issue, and considers changed heads again.
Recording the same head twice is idempotent under a local repository guard;
keep one reviewer controller per repository across hosts. Missing/pending CI
or an orphan done issue is reported and skipped so it cannot starve others.

When `pass` refuses for the same reason as in the last run of this
conversation, report evidence with that reason; leave lifecycle unchanged. Do not try a third time.

## Rules

- One issue per run. Read the Goal, Acceptance, and Never sections of the
  issue's spec first: they are the contract the PR must meet.
- Review reads; CI tests. Read the issue, the PR, its diff, and its checks.
  Do not run tests, builds, or e2e yourself. The PR checks run them, and
  each repository decides what its CI runs, also whether e2e goes to the
  shared GKE cluster.
- Never edit the PR branch. The review worktree is for reading only. Report needed fixes for explicit human triage; do not change lifecycle labels.
- Do not create cloud resources or dispatch or rerun workflows by hand.
- Never merge, close issues, delete coding worktrees/branches, or change
  lifecycle labels. CI alone sets done/todo. Set `ISSUE_CI_CONFIG` to the
  approved manifest from the trusted default branch (never from the PR).
  Both next and pass independently verify ALL configured current-head gates.
  Missing/pending/cancelled/skipped checks refuse acceptance. Missing gate
  configuration is a deployment blocker, not permission to accept all green
  visible checks.

## Instructions

1. Pick the next issue, from the repository's main checkout:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh next
```

   - Exit `3`: nothing to review. Report the line and end the run.
   - Exit `0`: it printed `issue=`, `pr=`, `head=`, `worktree=` (detached
     at `head`), `checks=` (how many checks the PR has), and `base=`.

2. Read the issue and the PR. The worktree gives the code around the diff:

```bash
gh issue view <N> --comments
gh pr view <pr> --comments
gh pr checks <pr>
git -C <worktree> diff <base>..<head>
```

   - Check every Acceptance item and every Never item against the diff.
   - Check that the PR adds or changes tests for the Acceptance items. A
     green check proves only what a test covers.
   - Hunt for bugs the tests miss: edge cases, error paths, wrong defaults,
     changes outside the issue's scope, and files the repository protects.
   - A failed check is a failed review. Name the check and quote the
     failing lines from `gh run view <run-id> --log-failed`.

3. Decide:
   - Pass: every Acceptance item holds, no Never item is broken, no check
     failed, and no bug was found. Then record acceptance (no merge):

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh pass <N> <head>
```

   - Fail: write the review to a file outside the worktree. List each
     problem with where it is and what done looks like. Then:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh fail <N> <head> <review-file>
```

     This reports evidence only. A human decides any next coding action;
     CI lifecycle labels remain unchanged.

   - Blocked: the decision needs a human (a frozen contract, a release, a
     paid service, deleting data). Write the question to a file, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh block <N> <head> <question-file>
```

## Exit codes

- `0` — done; report the issue, the decision, and the PR checks.
- `2` — refused; report the printed reason. Fix it and run the step again.
  Leave lifecycle unchanged and end the run when evidence/config is missing.
- `3` — nothing to review; end the run.

## Separate post-merge maintenance

Review acceptance is not merge or cleanup authority. After a separately
authorized merge is read back as MERGED at its exact head/base/merge commit,
Codex can request the shared [worktree maintenance](../issue-work/WORKTREE-MAINTENANCE.md)
dry-run, then an explicitly authorized apply. Do not reuse legacy `review.sh
pass` deletion or a queued merge state as cleanup permission. The shared helper
keeps branches/recovery, checks AGY owner release and never changes labels or
merges. AGY finish only hands off a PR; it does not own post-merge deletion.
