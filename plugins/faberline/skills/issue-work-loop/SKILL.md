---
name: issue-work-loop
description: Run issue-work repeatedly at a user-specified interval, such as /issue-work-loop 15m. Use for repeated issue implementation in one repository; issue-work alone still runs once.
---

# /faberline:issue-work-loop

Usage: `/issue-work-loop 15m` or `/faberline:issue-work-loop 15m`.

Repeat [issue-work](../issue-work/SKILL.md) at the given interval.
Read and follow the [interval loop rules](../issue-loop/INTERVAL-LOOPS.md).
The target of each tick is `issue-work`, without the interval argument.
Do not schedule this wrapper as the tick: that would create nested loops.

Each tick works at most one issue under the original ownership rules.
Retain the same conversation's `ISSUE_WORK_OWNER` across ticks.
Resume only that owner's work, with the same branch, PR and worktree.
A busy result skips the tick. It never permits taking another owner's lease.
Submission leaves `doing`; CI alone sets `done` or `todo`.
