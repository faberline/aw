---
name: agy-work-issue
description: "Run one step of the AGY issue loop: take one open GitHub issue labelled agy:todo in the current repository, implement it in its own worktree, run its unit tests, and open a pull request for review. Use when a scheduled AGY loop or the user asks to work the next agy issue."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/issue.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/issue.sh *)
---

# /faberline:agy-work-issue

`${CLAUDE_SKILL_DIR}` is the directory that holds this SKILL.md. Claude Code
fills it in. AGY does not, and the shell expands an unset `${CLAUDE_SKILL_DIR}`
to nothing. In AGY, run the script by the absolute path of
[scripts/issue.sh](scripts/issue.sh), next to this file.

Work one issue, then stop. Claude writes the issues, reviews the pull
requests, merges, and releases. This loop only implements. The mechanics live
in `${CLAUDE_SKILL_DIR}/scripts/issue.sh`; your job is the implementation.

## Rules

- One issue per run. When the script exits `3`, report its line and stop.
- Write only inside the worktree that `next` prints. Never edit the main
  checkout, and never switch its branch.
- Do only what the issue asks. Its Acceptance and Never sections are the
  contract. Do not widen the scope.
- Never merge, close an issue, delete a branch, or change labels by hand. The
  script moves the labels.
- Run unit tests only. Never run e2e, integration, or cluster tests: no
  `kind`, `kubectl`, `gcloud`, `terraform`, or workflow dispatch. Claude runs
  those during review.
- Never run bare `git stash`. Never use `git push --force`.
- When a decision belongs to a human, or the gate still fails after a fix you
  can justify, ask a clear question with `ask`. Do not guess.

## Instructions

1. Claim or resume one issue, from the repository's main checkout:

```bash
${CLAUDE_SKILL_DIR}/scripts/issue.sh next
```

   - Exit `3`: nothing to do. Report the line and stop.
   - Exit `0`: it printed `issue=`, `branch=`, `worktree=`, and `pr=`.

2. Read the issue and, when `pr` is not `none`, the review on that PR:

```bash
gh issue view <N> --comments
gh pr view <pr> --comments
```

   A PR means this is a rework: fix what the review asks, on the same branch.
   If `origin/main` moved, rebase first with `/faberline:git-rebase`.

3. Implement in the worktree. Run the unit-test gate commands the issue lists,
   from the worktree. All of them must pass. If the issue lists a command that
   is not a unit test, do not run it; say so in the summary.

4. Commit with `/faberline:git-commit`, from the worktree. One conventional
   commit per run is enough.

5. Write a short summary to a file outside the worktree: what changed, and
   each gate command with its result. Then finish:

```bash
${CLAUDE_SKILL_DIR}/scripts/issue.sh finish <N> <summary-file>
```

   It pushes, opens or reuses the PR (`Closes #<N>`), and moves the issue to
   `agy:done`. Claude reviews it. If the review fails, Claude moves the issue
   back to `agy:todo`, and a later run continues on the same branch.

6. To ask a question instead, write it to a file, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/issue.sh ask <N> <question-file>
```

   Commit finished work first, if there is any. The issue moves to
   `agy:done`. Claude answers in the issue and moves it back to `agy:todo`.

## Exit codes

- `0` — done; report the issue, the PR, and the gate results.
- `2` — refused; report the printed reason. Fix it and run the step again,
  or ask a question.
- `3` — nothing to do; stop.
