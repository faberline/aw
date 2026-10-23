---
name: issue-work
description: "The work step of the issue loop. It starts its own 15-minute loop in the current conversation (Claude Code, Codex, or AGY), then each run takes the highest-priority open GitHub issue labelled todo in the current repository (or resume the one labelled doing), implement it in its own worktree, run its unit-test gate, and open a pull request that closes it, labelled done. Use when the loop or the user asks to work the next issue."
user-invocable: true
argument-hint: "[loop]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/work.sh *)
---

# /faberline:issue-work

Work one issue per run, then stop. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/work.sh`; your job is the implementation.

`${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md. Claude Code sets it.
In Codex or AGY, use the absolute path of that folder instead.

## Loop

This step starts its own loop. The loop wakes this conversation every 15
minutes. Plan and review run in other conversations, each in its own loop.
A run that the loop started has the argument `loop`.

- With the argument `loop`: the loop runs. Go to Instructions.
- Without it: start the loop, then do one run now.

Start the loop with the scheduler of the client you run in:

| Client | Tool | Arguments |
|---|---|---|
| Claude Code | `CronCreate` | `cron: "4,19,34,49 * * * *"`, `prompt: "/faberline:issue-work loop"` |
| Codex | `automation_update` | `mode: "create"`, `kind: "heartbeat"`, `destination: "thread"`, `name: "issue-work <repo>"`, `rrule: "FREQ=MINUTELY;INTERVAL=15"`, `status: "ACTIVE"`, `prompt: "Run the issue-work skill with the argument loop."` |
| AGY | `schedule` | `CronExpression: "4,19,34,49 * * * *"`, `IsDaemon: true`, `Prompt: "Run the issue-work skill with the argument loop."` |

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

Each run works one issue and ends its turn, so the next wakeup can fire.

A lock lets only one run work at a time. When `next` says `busy` and this
conversation claimed that issue in an earlier run and did not finish or
block it, run `work.sh next --mine` to go on with it. Never use `--mine` for
an issue that this conversation did not claim.

When `finish` refuses for the same reason as in the last run of this
conversation, block the issue with that reason. Do not try a third time.

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
