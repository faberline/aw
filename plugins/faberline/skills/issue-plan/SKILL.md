---
name: issue-plan
description: "Plan one repository's issues and drive its milestone release lifecycle. Triage and fill todo, create milestone gap issues, validate RC candidates, and promote a fully tested release. Use when the schedule or user asks to plan or progress a milestone."
user-invocable: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh) Bash(${CLAUDE_SKILL_DIR}/scripts/plan.sh *)
---

# /faberline:issue-plan

Triage issues, fill the `todo` queue, and progress the current milestone's
release lifecycle. Use existing repository release gates and stop safely on
unknown results.
The mechanics live in `${CLAUDE_SKILL_DIR}/scripts/plan.sh`; your job is
the direction.

`${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md. Claude Code sets it.
Otherwise, use the absolute path of that folder.

## Runs

Each run does one pass, then ends. This skill never starts a loop or a
schedule. The controller sets the schedule outside the skill. Ignore any
argument, such as `loop` or `once`.

## Rules

- New issues and specs stay inside the repository's stated goals and the
  user's instructions.
- Milestone release is part of planning. Follow [RELEASE-READINESS.md](RELEASE-READINESS.md):
  create RC tags, verify the full release pipeline, then promote the exact
  passing candidate and close its milestone. Create milestone issues for
  unmet goals or failed tests. Do not lower any existing gate.
- Never open an issue or write a spec for work that needs a human decision:
  unapproved paid or cloud services, deleting data, or changing a frozen
  contract. When an open backlog or `todo` issue needs one, block it with
  one question (see Blocked). Only a `blocked` issue reaches the controller;
  a note in your run report does not.
- Each queued issue is one PR that the work step can finish and test with
  unit tests. When a backlog issue is bigger, open smaller issues that link
  to it instead of writing its spec.
- The backlog comes before new work. Open a new issue only when no backlog
  issue fits the next direction.
- Never edit an issue's body that a human wrote. Write its spec as a comment
  with `plan.sh spec` instead.
- Never close an issue. When one looks done or obsolete, set it to `p3` and
  comment why, with the commit or issue that covers it.

## Priority

| Label | Meaning |
|---|---|
| `priority:p0` | Do now: something is broken, or it blocks other work. |
| `priority:p1` | Do next: the next roadmap step. |
| `priority:p2` | Normal. An issue without a priority label counts as this. |
| `priority:p3` | Some day. |

The plan step admits work from the earliest unresolved version milestone, then
priority within that batch. Empty milestones do not block admission. AGY consumes
already-planned todo by priority and age; it does not re-run milestone ordering.

## Spec

The work and review steps read these four sections of a queued issue:

- `## Goal`: one paragraph on the outcome and why.
- `## Acceptance`: checkable items.
- `## Never`: what the PR must not touch or do.
- `## Gate`: the exact unit-test commands the work step runs.

They are in the latest comment that starts with `<!-- issue-plan:spec -->`,
else in the body. `plan.sh spec` writes that comment. Read the code before
you write a spec: name the files, and check that each Gate command exists.
Inspect selected test behavior, not just a cargo filter: pure isolated helper
units/static checks may run locally; product CLI subprocess/output/file checks,
server/network/cluster and performance tests belong in authorized cloud CI.
Do not classify an offline `relay render --out ...` contract as a pure unit.

## Blocked

Block an issue only for a human decision, not for a wait on another issue
or PR: name that wait in a comment and leave the issue in the backlog. The
question comment says:

- What must be decided, in one or two sentences, and why the plan step
  cannot decide it.
- Two options at most, each with its effect, and the one you recommend.

The controller answers in the issue and takes it out of `blocked`. The
issue is then in the backlog again: read the answer before you write its
spec or adopt it.

## Instructions

1. See the queue and the backlog:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh status
```

   It prints a shared queue decision with explicit blockers, `room=`,
   `backlog=` and `untriaged=` counts, then every open issue in milestone order.
   Missing or invalid assignments require milestone triage before new admission.
   `status` is a read-only, non-atomic remote snapshot. It does not acquire or
   create the local work guard, even while a writer holds it. Its output sets
   `mutation_authorized=false`: a selected issue, free queue room or successful
   read is not permission to write or proof of exclusive planner activity.
   Failed/incomplete reads remain errors, never an empty queue. Every mutation
   must use its normal guarded command and fresh revalidation.
   If status fails, do not mutate the queue. Still attempt the independent
   read-only release assessment in step 6, and report `HOLD` when its evidence
   is incomplete.

2. When there is something to do (`room` above 0, an untriaged issue, or a
   backlog issue to spec), read the repository's goals and status (README,
   roadmap, status or handoff docs), the complete open issue inventory from
   `status`, and the recent commits. Otherwise skip to step 6.

3. Triage. Give each untriaged issue that `status` printed a priority:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh prioritize <N> <p0-p3>
```

   Also fix the priority of a loop or backlog issue when the order is wrong.
   When an issue needs a human decision, write the question to a file, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh block <N> <question-file>
```

4. Fill the queue, up to `room`. Take backlog from the earliest unresolved
   milestone first, then priority within that batch. Do not skip a blocked or
   awaiting-review batch to fill the queue from a later release:
   - A backlog issue without a spec: write the spec to a file, then
     `plan.sh spec <N> <spec-file>`.
   - Then put it in the queue:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh adopt <N> <p0-p3> [--milestone <existing-number>]
```

   - Only when no backlog issue fits, open new work. Write the body with the
     four spec sections, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh new "<title>" <body-file> <p0-p3> --milestone <existing-number>
```

5. With `room=0`, prepare instead: write the spec of the top backlog issue
   that has none, at most one per run, so the next free place is quick to
   fill.

6. Assess the current milestone's release readiness, including when the queue
   is full or no issues changed. Follow [RELEASE-READINESS.md](RELEASE-READINESS.md).
   Report its phase, exact milestone, candidate and evidence. A missing release
   contract or unknown result is a hold, not a pass. If the goal or a release
   test is unmet, create scoped issues in the same milestone. When the queue
   is full, create the issue without claiming it:

```bash
${CLAUDE_SKILL_DIR}/scripts/plan.sh new-backlog "<title>" <body-file> <p0-p3> --milestone <existing-number>
```

7. End the run. Report any issue changes and the milestone verdict. If no
   milestone can be identified, report `HOLD` and the missing information.

## Exit codes

- `0` — done; report each issue triaged, specced, adopted, opened,
  reprioritized, or blocked, and why.
- `2` — refused; report the printed reason. Fix it and run the step again.
- `3` — a mutating command is busy; preserve the existing writer and stop.

Nothing worth doing is a valid result. Report it with the reason.

## Planner milestones and planned work

See the [planner admission contract](../issue-loop/MILESTONE-QUEUE.md).
Claude plan uses milestone versions before priority for new/adopt admission;
empty milestones do not block, while earlier unresolved work remains visible.
AGY consumes already-planned todo by priority and creation time, without a
milestone frontier or dependency-parser eligibility filter. Preserve active
leases, dirty worktrees and same-PR CI repairs. Core milestones are repo batches,
not crate versions. No schedule, release/test gate or done change is implied.
