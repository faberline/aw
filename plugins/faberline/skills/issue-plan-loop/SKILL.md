---
name: issue-plan-loop
description: Run issue-plan repeatedly at a user-specified interval, such as /issue-plan-loop 1h. Use for repeated issue planning in one repository; issue-plan alone still runs once.
---

# /faberline:issue-plan-loop

Usage: `/issue-plan-loop 1h [--tasks triage,queue,release] [--milestone N] [--role shared]`.
The namespaced `/faberline:issue-plan-loop` accepts the same inputs.

Repeat [issue-plan](../issue-plan/SKILL.md) at the given interval.
Read and follow the [interval loop rules](../issue-loop/INTERVAL-LOOPS.md).
Parse the planning options once with the original skill's `plan.sh options`.
The target of each tick is `issue-plan` with the same validated planning
options, without the interval argument. Freeze the resolved tasks, role and
existing milestone in the timer prompt. Do not silently reset these defaults.
Do not schedule this wrapper as the tick: that would create nested loops.

Each tick runs the selected original planning tasks. Release assessment is in
both default profiles and runs when `release` is selected. Keep all admission
guards, human decisions and release gates.
This wrapper adds timing only. It adds no release or issue-close authority.
