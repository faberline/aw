---
name: issue-loop
description: "Run the issue loop in the current repository: plan (open and prioritize issues), work (todo -> doing -> done, a PR), and review (test, then merge the PR and close the issue, or send it back), one step per iteration, as a self-paced /loop. Use when the user asks to start, run, or continue the issue loop."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh *)
---

# /faberline:issue-loop

One cycle with three steps. Each iteration runs one step, then schedules the
next iteration. The issue label is the state:

| Step | Skill | Moves |
|---|---|---|
| plan | `/faberline:issue-plan` | opens issues as `todo`; sets `priority:p0`..`p3` |
| work | `/faberline:issue-work` | `todo` -> `doing` -> `done` (a PR with `Closes #<N>`) |
| review | `/faberline:issue-review` | `done` -> merged and closed, or back to `todo` |

Any step can move an issue to `blocked` when a human must decide. The loop
skips `blocked` issues. The user answers in the issue and moves it back to
`todo`.

## Loop

- Started by hand (not by a `/loop` wakeup) and no `/loop` of this skill is
  active in the session: first invoke `/loop /faberline:issue-loop`, with no
  interval, then do the iteration below as its first one.
- Started by a `/loop` wakeup, or a loop is already active: never start
  another. Do the iteration, then schedule the next one (step 3).

## Instructions

1. From the repository's main checkout, see which step is next:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh step
```

   It prints the counts, each `blocked` issue, and `step=`. The order is:
   review first, so a PR does not wait; then work; then plan, only when no
   issue waits for work or review.

2. Run that step once:
   - `step=review`: run `/faberline:issue-review` in a fresh subagent (the
     Agent tool), so the review does not share the work step's reasoning.
     Give it the repository path and ask it to run the skill once and report
     the issue, the decision, and each check with its result. Without the
     Agent tool, run the skill here.
   - `step=work`: run `/faberline:issue-work`.
   - `step=plan`: run `/faberline:issue-plan`.

3. Schedule the next iteration with `ScheduleWakeup` (`prompt`:
   `/faberline:issue-loop`):
   - The step changed an issue (claimed, finished, merged, failed, opened,
     or blocked): 60 seconds.
   - The work step exited `3` because another run holds the lock: 600
     seconds.
   - Nothing to do (plan found nothing worth opening, and only `blocked`
     issues are left): 1800 seconds.
   - Stop the loop (`stop: true`) when a step refused (exit `2`) twice in a
     row for the same issue, or when the user asks.

Report to the user after each iteration: the step, the issue, the result,
and each `blocked` issue that waits for them.

## Labels

Create the labels once per repository. This also renames the old
`agy:todo`, `agy:doing`, and `agy:done` labels, so their issues keep their
state:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh labels
```
