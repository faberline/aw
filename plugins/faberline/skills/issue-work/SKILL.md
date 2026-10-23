---
name: issue-work
description: "The work step of the issue loop: take the highest-priority open GitHub issue labelled todo in the current repository (or resume the one labelled doing), implement it in its own worktree, run its unit-test gate, and open a pull request that closes it, labelled done. Use when /faberline:issue-loop or the user asks to work the next issue."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh *)
---

# /faberline:issue-work

Work one issue per run, then stop. `/faberline:issue-loop` runs this step
when no PR waits for review. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/work.sh`; your job is the implementation.

## Rules

- One issue per run. When the script exits `3`, report its line and end the
  run.
- Write only inside the worktree that `next` prints. Never edit the main
  checkout, and never switch its branch.
- Do only what the issue asks. Its Acceptance and Never sections are the
  contract. Do not widen the scope.
- Never merge, close an issue, delete a branch, or change labels by hand. The
  scripts move the labels.
- Run the unit-test gate only. Never run e2e, integration, or cluster tests:
  no `kind`, `kubectl`, `gcloud`, `terraform`, or workflow dispatch. The
  review step and the PR checks run those.
- Never run bare `git stash`. Never use `git push --force`.
- When a decision belongs to a human, or the gate still fails after a fix you
  can justify, block the issue with a clear question. Do not guess.

## Instructions

1. Claim or resume one issue, from the repository's main checkout:

```bash
${CLAUDE_SKILL_DIR}/scripts/work.sh next
```

   - Exit `3`: nothing to do. Report the line and end the run.
   - Exit `0`: it printed `issue=`, `branch=`, `worktree=`, and `pr=`.

2. Read the issue and, when `pr` is not `none`, the review on that PR:

```bash
gh issue view <N> --comments
gh pr view <pr> --comments
```

   A PR means this is a rework: fix what the review asks, on the same branch.
   If `origin/main` moved, rebase first with `/faberline:git-rebase`.

3. Implement in the worktree. Run the gate commands from the issue's Gate
   section, from the worktree. All of them must pass. If the Gate lists a
   command that is not a unit test, do not run it; say so in the summary.

4. Commit with `/faberline:git-commit`, from the worktree. One conventional
   commit per run is enough.

5. Write a short summary to a file outside the worktree: what changed, and
   each gate command with its result. Then finish:

```bash
${CLAUDE_SKILL_DIR}/scripts/work.sh finish <N> <summary-file>
```

   It pushes, opens or reuses the PR (`Closes #<N>`), and moves the issue to
   `done`. The review step merges it, or moves it back to `todo`; a later
   run then continues on the same branch.

6. To block instead, write the question to a file, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/work.sh block <N> <question-file>
```

   Commit finished work first, if there is any. The issue moves to
   `blocked`. The user answers in the issue and moves it back to `todo`.

## Exit codes

- `0` — done; report the issue, the PR, and the gate results.
- `2` — refused; report the printed reason. Fix it and run the step again,
  or block the issue.
- `3` — nothing to do; end the run.
