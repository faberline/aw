---
name: issue-plan-loop
description: Run issue-plan repeatedly at a user-specified interval, such as /issue-plan-loop 1h. Use for repeated issue planning in one repository; issue-plan alone still runs once.
---

# /faberline:issue-plan-loop

Usage: `/issue-plan-loop 1h` or `/faberline:issue-plan-loop 1h`.

Repeat [issue-plan](../issue-plan/SKILL.md) at the given interval.
Read and follow the [interval loop rules](../issue-loop/INTERVAL-LOOPS.md).
The target of each tick is `issue-plan`, without the interval argument.
Do not schedule this wrapper as the tick: that would create nested loops.

Each tick runs the original planning pass, including its milestone release
assessment. Keep all admission guards, human decisions and release gates.
This wrapper adds timing only. It adds no release or issue-close authority.
