---
name: issue-plan
description: "The plan step of the issue loop. It starts its own 30-minute loop in the current conversation (Claude Code, Codex, or AGY), then each run reads the current repository's goals, status, and open issues, set the priority of the issues in the loop, and open new GitHub issues labelled todo, each one PR the work step can finish. Use when the loop or the user asks to plan, prioritize, or fill the issue queue."
user-invocable: true
argument-hint: "[loop]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh *)
---

# /faberline:issue-plan

Fill the `todo` queue and set priorities, then stop. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/plan.sh`; your job is the direction.

## Loop

This step starts its own loop. The loop wakes this conversation every 30
minutes. Work and review run in other conversations, each in its own loop.
A run that the loop started has the argument `loop`.

- With the argument `loop`: the loop runs. Go to Instructions.
- Without it: start the loop, then do one run now.

Start the loop with the scheduler of the client you run in:

| Client | Tool | Arguments |
|---|---|---|
| Claude Code | `CronCreate` | `cron: "13,43 * * * *"`, `prompt: "/faberline:issue-plan loop"` |
| Codex | `automation_update` | `mode: "create"`, `kind: "heartbeat"`, `destination: "thread"`, `name: "issue-plan <repo>"`, `rrule: "FREQ=MINUTELY;INTERVAL=30"`, `status: "ACTIVE"`, `prompt: "Run the issue-plan skill with the argument loop."` |
| AGY | `schedule` | `CronExpression: "13,43 * * * *"`, `IsDaemon: true`, `Prompt: "Run the issue-plan skill with the argument loop."` |

- One loop per conversation. When this conversation has a loop of this step
  already, do not start another. Claude Code: look in `CronList`. Codex:
  look for the name in `$CODEX_HOME/automations/*/automation.toml`.
- Use only these schedulers. They wake this conversation. Never use one that
  starts a new conversation for each run: Claude Code scheduled tasks or
  routines, Codex `cron` automations, or AGY sidecars.
- Report the loop's ID and how to stop it: Claude Code `CronDelete <id>`;
  Codex `automation_update` with `status: "PAUSED"`; AGY `manage_task` with
  the task ID.
- A Claude Code loop stops after 7 days, or when the session closes. Run
  the skill again without `loop` to start a new one.
- When the client has none of these tools, say so and do one run.

Each run plans once and ends its turn, so the next wakeup can fire.

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
