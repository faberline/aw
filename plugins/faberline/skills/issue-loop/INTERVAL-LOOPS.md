# Interval loop companions

`issue-plan`, `issue-work` and `issue-review` still run one pass each.
Their `-loop` companions repeat that same pass on request.
Creating or installing a companion does not start it.

## Interval and scope

Accept one positive whole number followed by `s`, `m`, `h` or `d`.
For example, `30s`, `15m`, `1h` and `1d` are intervals.
Ask for the interval if it is missing or invalid. Do not guess a default.
If the timer cannot represent it, report that before starting.
Do not silently round it or choose a different interval.

Bind the loop to the verified repository and this conversation.
Use one step per conversation. Keep review separate from code authorship.
Read the original skill before the first pass and after its version changes.
Use its real commands and judgement; running `plan.sh status` alone is not
an `issue-plan` pass. A shell cannot invoke a skill by printing its name.

Run the first pass immediately. Later passes are due at the start time plus
whole multiples of the interval. Run only one pass at a time.
If a pass is still active, skip that due tick. Do not queue catch-up passes.
Host timers can be late; report the real cadence and any timer limits.
The first pass returns control to this wrapper; its instruction to end a run
does not end setup. If it requires stopping, do not create a timer.

## Start the timer

Prefer an available native recurring-task tool. Discover its current schema.
For Codex, find `automation_update` and use the supported chat heartbeat.
For a client with a session timer, use that timer's exposed tools.
Use the original skill's absolute installed path and the repository's exact
path in the task prompt. Include the interval, start time and serial-run rule.
The repeated prompt must call the original skill once, then end.
It must not call the `-loop` companion or create another timer.

Inspect existing timers before creating one. Reuse a matching companion timer
owned by this conversation, repository and step. Do not edit an unrelated or
other conversation's schedule. If another active timer runs the same step
in the repository, report the overlap before starting a duplicate.
Do the overlap check before the immediate pass too.
Create and read back the task. Report its ID, interval, next run and lifetime.
Do not claim a schedule exists after a failed or unknown write.
If a native timer cannot prevent overlapping passes, use the session mode
below. The underlying skill's guards still apply in either mode.

When no suitable native timer is available, keep the loop in this active
session. Run the original skill as one pass, then return control here.
The original skill's instruction to end the run ends that pass, not the loop.
Use an interruptible wait tool between due ticks. Use waits of at most
60 seconds, recheck the time and user input, and continue until the next tick.
If needed, a bounded `sleep` command may wait; it must not invoke an agent.
Do not busy-poll, launch a detached agent, or build a shell daemon.
Do not send a final answer while this session loop is still active.
State that this mode stops when the active session ends and cannot wake it.
If neither timer nor wait tools are available, report that the loop did not
start. Do not pretend a written instruction is an active timer.

## Each pass and stop

Retain the original skill's labels, ownership, test scope, CI checks, review
receipts and release rules. The wrapper adds no GitHub or cloud authority.
It does not restore CI review/merge/close stages or add an AW Claude gate.
Keep stable owner tokens across ticks; never take a peer's identity or lease.
Check current repository inputs each pass. Do not reuse a stale head as proof.

An empty queue or busy result ends only the pass; wait for the next tick.
Honor original refusal and retry limits. A review finding is not permission
to code, merge, close, or relabel. Stop and report when credentials or required
configuration are missing, an action needs a human decision, or the original
skill requires stopping on a repeated refusal. Do not retry it forever.
Pause or delete only this loop's verified timer when stopping.
Preserve leases, worktrees, partial work and unrelated schedules.

User cancellation stops future ticks. If a pass is active, stop safely under
the original skill's rules. A request to change the interval updates this
loop only. Report changes, completion, failure or required decisions.
For unchanged empty/busy passes, keep user notifications quiet when the host
supports that setting. Do not claim the loop is still running after it stops.
