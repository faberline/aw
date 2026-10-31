---
name: issue-review-loop
description: Run issue-review repeatedly at a user-specified interval, such as /issue-review-loop 15m. Use for repeated independent review in one repository; issue-review alone still runs once.
---

# /faberline:issue-review-loop

Usage: `/issue-review-loop 15m` or `/faberline:issue-review-loop 15m`.

Repeat [issue-review](../issue-review/SKILL.md) at the given interval.
Read and follow the [interval loop rules](../issue-loop/INTERVAL-LOOPS.md).
The target of each tick is `issue-review`, without the interval argument.
Do not schedule this wrapper as the tick: that would create nested loops.

Use a conversation separate from the code author and `issue-work-loop`.
If this conversation wrote the candidate, do not accept it as independent.
Each tick reviews at most one eligible issue and keeps the existing receipts.
Do not re-review the same unchanged head to fill a tick.
Review runs no tests, merges no PR and changes no lifecycle labels.
