---
name: issue-loop
description: "Show the issue loop of the current repository: plan issues and milestone releases, work (todo -> doing, a PR), CI (done or todo), and review (independent acceptance without merge). Each step skill does one run; the controller sets their schedules. Prints the counts and the blocked issues; creates the labels. Use when the user asks to set up or check the issue loop."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/loop.sh *)
---

# /faberline:issue-loop

Three steps. Each runs on its own schedule, side by side. The issue label is
the state:

| Step | Skill | Moves | Every |
|---|---|---|---|
| plan | `/faberline:issue-plan` | triages the backlog, fills `todo`, and progresses milestone releases | 10 min |
| work | `/faberline:issue-work` | `todo` -> `doing` (submit PR, then wait for CI) | 15 min |
| review | `/faberline:issue-review` | review `done`; no lifecycle writes or merge | 15 min |

CI alone sets `done` after all required current-head gates pass or `todo`
on failure; see the issue-ci skill for explicit consumer wiring. Work and
review report human-decision evidence without changing lifecycle. Existing
blocked issues require human triage. Schedules remain externally controlled.

`${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md. Claude Code sets it.
In Codex or AGY, use the absolute path of that folder instead.

## Schedules

The skills never start a loop or a schedule. The controller sets one
schedule per step and repository. A separate conversation for each step
keeps the review apart from the work step's reasoning, and keeps each
context small. Each run does one step and ends.
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

## Planner milestones and planned work

See the [planner admission contract](../issue-loop/MILESTONE-QUEUE.md).
Claude plan uses milestone versions before priority for new/adopt admission;
empty milestones do not block, while earlier unresolved work remains visible.
AGY consumes already-planned todo by priority and creation time, without a
milestone frontier or dependency-parser eligibility filter. Preserve active
leases, dirty worktrees and same-PR CI repairs. Core milestones are repo batches,
not crate versions. Release uses the documented full gates and exact candidate;
see [RELEASE-READINESS.md](../issue-plan/RELEASE-READINESS.md). Do not change
schedules, weaken release/test gates, or change done policy.
