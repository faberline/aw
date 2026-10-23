---
name: issue-plan
description: "The plan step of the issue loop, run every 30 minutes as /loop 30m /faberline:issue-plan: read the current repository's goals, status, and open issues, set the priority of the issues in the loop, and open new GitHub issues labelled todo, each one PR the work step can finish. Use when the loop or the user asks to plan, prioritize, or fill the issue queue."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh *)
---

# /faberline:issue-plan

Fill the `todo` queue and set priorities, then stop. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/plan.sh`; your job is the direction.

## Loop

This step runs every 30 minutes in its own session:
`/loop 30m /faberline:issue-plan`. The work and review loops run at the same
time. Each run plans once and ends. Never schedule a wakeup yourself.

## Rules

- New issues stay inside the repository's stated goals and the user's
  instructions.
- Never open an issue for work that needs a human decision: releases, paid or
  cloud services, deleting data, or changing a frozen contract. Report those
  to the user instead.
- Each issue is one PR that the work step can finish and test with unit
  tests. Split bigger work into several issues.
- Skip anything an open issue already covers. Adopt that issue instead, when
  its body has the four sections below.
- Never edit an issue's body that a human wrote. Open a new issue that links
  to it instead.

## Priority

| Label | Meaning |
|---|---|
| `priority:p0` | Do now: something is broken, or it blocks other work. |
| `priority:p1` | Do next: the next roadmap step. |
| `priority:p2` | Normal. An issue without a priority label counts as this. |
| `priority:p3` | Some day. |

The work step takes the `todo` issue with the highest priority, then the
oldest.

## Instructions

1. See the loop's open issues and the room in the queue:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh status
```

2. Read the repository's goals and status (README, roadmap, status or
   handoff docs), all open issues (`gh issue list --state open`), and the
   recent commits. Pick the next direction that extends the project: a gap, a
   missing case, a friction point met during review, or the next roadmap
   step.

3. Set priorities. Change the priority of a loop issue when the order is
   wrong:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh prioritize <N> <p0-p3>
```

4. Fill the queue, up to the `room` that `status` printed. With `room=0`,
   skip this and end the run. For an existing
   open issue that fits and has the four sections:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh adopt <N> <p0-p3>
```

   For new work, write the body to a file with these sections:
   - `## Goal`: one paragraph on the outcome and why.
   - `## Acceptance`: checkable items.
   - `## Never`: what the PR must not touch or do.
   - `## Gate`: the exact unit-test commands the work step runs.

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh new "<title>" <body-file> <p0-p3>
```

## Exit codes

- `0` — done; report each issue opened, adopted, or reprioritized, and why.
- `2` — refused; report the printed reason. Fix it and run the step again.

Nothing worth opening is a valid result. Report it with the reason.
