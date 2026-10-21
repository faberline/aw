---
name: agy-review-issue
description: "Claude's side of the AGY issue loop: take the oldest GitHub issue labelled agy:done in the current repository, run the acceptance checks beyond unit tests and hunt for bugs, then merge the PR or send it back to agy:todo with a review. With nothing to review, plan the next direction and open new agy:todo issues. Run by hand, it starts a self-paced /loop that repeats it. Use when a scheduled Claude loop or the user asks to review, accept, or feed the AGY issue loop."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh *)
---

# /faberline:agy-review-issue

`${CLAUDE_SKILL_DIR}` is the directory that holds this SKILL.md. Claude Code
fills it in. AGY does not, and the shell expands an unset `${CLAUDE_SKILL_DIR}`
to nothing. In AGY, run the script by the absolute path of
[scripts/review.sh](scripts/review.sh), next to this file.

The AGY loop (`/faberline:agy-work-issue`) implements issues and runs their
unit tests. This skill is the acceptance gate after it, and the planner in
front of it. Review one issue per run. If there is none, open new issues. The
mechanics live in `${CLAUDE_SKILL_DIR}/scripts/review.sh`; your job is the
judgement.

## Loop

This skill keeps itself running with a self-paced `/loop`:

- Started by hand (not by a `/loop` wakeup) and no `/loop` of this skill is
  active in the session: first invoke `/loop /faberline:agy-review-issue`,
  with no interval, then do the run below as its first iteration.
- Started by a `/loop` wakeup, or a loop is already active: never start
  another. Do the run, then schedule the next one (step 6).
- In AGY there is no `/loop`: do one run and stop.

## Rules

- One issue per run. Read the issue's Goal, Acceptance, and Never sections
  first: they are the contract the PR must meet.
- Never edit the PR branch. The review worktree is for reading and running
  checks. A fix goes back to AGY as a failed review.
- Unit tests passing is not acceptance. Run what AGY may not: the build, lint,
  e2e, integration, and behavior checks the repository's docs and the issue
  name. Follow the repository's own rules on ports, protected files, and
  services. A check you could not run is reported as not run, never as passed.
- Merge only through `review.sh pass`. It merges with `/faberline:gh-merge-pr`
  and only at the commit you reviewed.
- New issues stay inside the repository's stated goals and the user's
  instructions. Never open an issue for work that needs a human decision:
  releases, paid or cloud services, deleting data, or changing a frozen
  contract. Report those to the user instead.

## Instructions

1. Pick the next issue, from the repository's main checkout:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh next
```

   - Exit `3`: nothing to review. Go to step 5.
   - Exit `0` with `kind=question`: the last issue comment is AGY's question.
     Go to step 4.
   - Exit `0` with `kind=pr`: it printed `issue=`, `pr=`, `head=`,
     `worktree=` (detached at `head`), and `base=`.

2. Review the PR in the printed worktree:

```bash
gh issue view <N> --comments
gh pr view <pr> --comments
git -C <worktree> diff <base>..<head>
```

   - Check every Acceptance item and every Never item against the diff.
   - Run the unit-test gate yourself, then the checks beyond it.
   - Hunt for bugs the tests miss: edge cases, error paths, wrong defaults,
     changes outside the issue's scope, and files the repository protects.
   - Write down each check with its exact command and result.

3. Decide:
   - Pass: every Acceptance item holds, no Never item is broken, and no bug
     was found. Then merge:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh pass <N> <head>
```

   - Fail: write the review to a file outside the worktree. List each
     problem with how to reproduce it and what done looks like, plus the
     checks you ran. Then:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh fail <N> <review-file>
```

   The loop picks the issue up again and continues on the same branch.

4. Question: answer it in a file. If the answer needs a human decision, do
   not answer: stop the loop (step 6) and ask the user. Otherwise:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh answer <N> <answer-file>
```

5. Nothing to review: plan the next work.
   - If the `idle` line shows `agy:todo` at `todo_max` or more, the queue is
     full. Report it and go to step 6.
   - Read the repository's goals and status (README, roadmap, status or
     handoff docs), the open issues, and recent commits. Pick the next
     direction that extends the project: a gap, a missing case, a friction
     point you met during review, or the next roadmap step.
   - Open at most `todo_max` minus the open `agy:todo` count. Each issue is
     one PR AGY can finish with unit tests. Skip anything an open issue
     already covers.
   - Write each body with these sections:
     - `## Goal`: one paragraph on the outcome and why.
     - `## Acceptance`: checkable items.
     - `## Never`: what the PR must not touch or do.
     - `## Gate`: the exact unit-test commands AGY runs.

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh new "<title>" <body-file>
```

6. Schedule the next run of the loop, with `ScheduleWakeup` (`prompt`:
   `/faberline:agy-review-issue`):
   - Reviewed, answered, or opened issues: about 900 seconds. AGY's loop
     runs every 10 minutes and needs one turn to react.
   - Idle: an `agy:doing` issue is open, so AGY is working: about 1200
     seconds.
   - Idle with the `agy:todo` queue full, or nothing worth opening: about
     1800 seconds.
   - Stop the loop (`stop: true`) and report to the user when a human
     decision blocks the next step: a question only the user can answer, a
     direction that needs a frozen contract changed, or a refusal you cannot
     fix. Otherwise the next wakeup picks the same issue again.

## Exit codes

- `0` — done; report the issue, the decision, and each check with its result.
- `2` — refused; report the printed reason. Fix it and run the step again.
- `3` — nothing to review (`next` only); continue with step 5.

Every run inside the loop ends with step 6, whatever the exit code.
