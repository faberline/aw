---
name: issue-loop
description: "Show the issue loop of the current repository: plan (open and prioritize issues), work (todo -> doing -> done, a PR), and review (test, then merge the PR and close the issue, or send it back). Each step runs as its own /loop. Prints the counts, the blocked issues, and the three /loop commands; creates the labels. Use when the user asks to start, set up, or check the issue loop."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh *)
---

# /faberline:issue-loop

Three steps, each in its own loop. They run side by side. The issue label is
the state:

| Step | Skill | Moves | Every |
|---|---|---|---|
| plan | `/faberline:issue-plan` | opens issues as `todo`; sets `priority:p0`..`p3` | 30 min |
| work | `/faberline:issue-work` | `todo` -> `doing` -> `done` (a PR with `Closes #<N>`) | 15 min |
| review | `/faberline:issue-review` | `done` -> merged and closed, or back to `todo` | 15 min |

Any step can move an issue to `blocked` when a human must decide. The loops
skip `blocked` issues. The user answers in the issue and moves it back to
`todo`.

## Start the loops

Run each loop in its own Claude Code session, in the repository. A separate
session keeps the review apart from the work step's reasoning, and keeps each
context small:

```
/loop 30m /faberline:issue-plan
/loop 15m /faberline:issue-work
/loop 15m /faberline:issue-review
```

Run one loop of each step per repository. Each run does one step and ends;
it never schedules its own wakeup. A run with nothing to do ends at once.

## Instructions

1. From the repository's main checkout, show the state:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh status
```

   It prints the counts and each `blocked` issue.

2. Report the counts and each `blocked` issue that waits for the user. When
   the user asked to start the loop, give them the three `/loop` commands
   above. Do not start the loops in this session.

## Labels

Create the labels once per repository. This also renames the old
`agy:todo`, `agy:doing`, and `agy:done` labels, so their issues keep their
state:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh labels
```
