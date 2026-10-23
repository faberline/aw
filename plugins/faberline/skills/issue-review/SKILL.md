---
name: issue-review
description: "The review step of the issue loop. It starts its own 15-minute loop in the current conversation (Claude Code, Codex, or AGY), then each run takes the oldest open GitHub issue labelled done in the current repository, run the checks beyond its unit-test gate and hunt for bugs, then merge the PR (which closes the issue) or send it back to todo with a review. Use when the loop or the user asks to review, accept, or merge the next done issue."
user-invocable: true
argument-hint: "[loop]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/review.sh *)
---

# /faberline:issue-review

Review one issue per run, then stop. This is the acceptance gate after
`/faberline:issue-work`. The mechanics live in
`${CLAUDE_SKILL_DIR}/scripts/review.sh`; your job is the judgement.

## Loop

This step starts its own loop. The loop wakes this conversation every 15
minutes. Work runs in another conversation, so this review does not share
its reasoning. Plan also runs in its own conversation. A run that the loop
started has the argument `loop`.

- With the argument `loop`: the loop runs. Go to Instructions.
- Without it: start the loop, then do one run now.

Start the loop with the scheduler of the client you run in:

| Client | Tool | Arguments |
|---|---|---|
| Claude Code | `CronCreate` | `cron: "11,26,41,56 * * * *"`, `prompt: "/faberline:issue-review loop"` |
| Codex | `automation_update` | `mode: "create"`, `kind: "heartbeat"`, `destination: "thread"`, `name: "issue-review <repo>"`, `rrule: "FREQ=MINUTELY;INTERVAL=15"`, `status: "ACTIVE"`, `prompt: "Run the issue-review skill with the argument loop."` |
| AGY | `schedule` | `CronExpression: "11,26,41,56 * * * *"`, `IsDaemon: true`, `Prompt: "Run the issue-review skill with the argument loop."` |

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

Each run reviews one issue and ends its turn, so the next wakeup can fire.

When `pass` refuses for the same reason as in the last run of this
conversation, block the issue with that reason. Do not try a third time.

## Rules

- One issue per run. Read the issue's Goal, Acceptance, and Never sections
  first: they are the contract the PR must meet.
- Never edit the PR branch. The review worktree is for reading and running
  checks. A fix goes back to the work step as a failed review.
- Passing the unit-test gate is not acceptance. Run what the work step may
  not: the build, lint, e2e, integration, and behavior checks the
  repository's docs and the issue name. Follow the repository's own rules on
  ports, protected files, and services. A check you could not run is
  reported as not run, never as passed.
- Do not create cloud resources or dispatch workflows by hand. The PR checks
  run what CI runs; `pass` waits for them.
- Merge only through `review.sh pass`. It merges with `/faberline:gh-merge-pr`
  and only at the commit you reviewed.

## Instructions

1. Pick the next issue, from the repository's main checkout:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh next
```

   - Exit `3`: nothing to review. Report the line and end the run.
   - Exit `0`: it printed `issue=`, `pr=`, `head=`, `worktree=` (detached
     at `head`), and `base=`.

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

     The work step picks the issue up again and continues on the same
     branch.

   - Blocked: the decision needs a human (a frozen contract, a release, a
     paid service, deleting data). Write the question to a file, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/review.sh block <N> <question-file>
```

## Exit codes

- `0` — done; report the issue, the decision, and each check with its result.
- `2` — refused; report the printed reason. Fix it and run the step again.
- `3` — nothing to review; end the run.
