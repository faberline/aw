---
name: issue-loop
description: "Show the issue loop of the current repository: plan (open and prioritize issues), work (todo -> doing -> done, a PR), and review (test, then merge the PR and close the issue, or send it back). Each step skill starts its own loop in its own conversation. Prints the counts and the blocked issues; creates the labels. Use when the user asks to set up or check the issue loop."
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

Run each step skill once, in its own conversation, in the repository. The
skill starts its own loop with the scheduler of the client (Claude Code
`CronCreate`, a Codex heartbeat, or the AGY `schedule` tool). The loop wakes
that same conversation. A separate conversation keeps the review apart from
the work step's reasoning, and keeps each context small.

Run one loop of each step per repository. Each run does one step and ends.
A run with nothing to do ends at once.

## Instructions

1. From the repository's main checkout, show the state:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh status
```

   It prints the counts and each `blocked` issue.

2. Report the counts and each `blocked` issue that waits for the user. When
   the user asked to start the loop, tell them to run each step skill in
   its own conversation. Do not start the loops in this conversation.

## Labels

Create the labels once per repository. This also renames the old
`agy:todo`, `agy:doing`, and `agy:done` labels, so their issues keep their
state:

```bash
${CLAUDE_SKILL_DIR}/scripts/loop.sh labels
```
