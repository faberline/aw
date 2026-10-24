# Primary main checkout freshness

This is a small pre-work maintenance step, separate from Codex post-merge
worktree cleanup. `next` retains the existing repository guard and owner/lease
checks, explicitly fetches `refs/heads/main:refs/remotes/origin/main`, binds that
exact commit, then attempts ordinary primary-checkout fast-forward maintenance.
A failed fetch refuses new selection; it never silently uses stale origin/main.

Update the primary checkout only when it is on main, has no staged/tracked or
untracked changes, has no issue owner lease/in-progress Git operation, is behind
without local-only commits, and its exclusive primary owner is positively
coordinated. The caller supplies that coordination by setting
`ISSUE_MAIN_EXCLUSIVE_OWNER` to the existing `ISSUE_WORK_OWNER` token only after
confirming no human/other-app primary writer. This is an explicit caller
attestation, not a credential, proof from lease absence, automatic authority or
permission expansion. Without it the step reports held; no native-session
visibility or desktop lock is invented.

Immediately recheck primary HEAD/branch/status and origin/main under the same
guard. Use `git merge --ff-only --no-autostash --no-overwrite-ignore` at the exact
fetched SHA, with post-merge hooks disabled for this invocation. Ignored paths
that do not collide are preserved; Git refuses an ignored incoming collision.
No stash/archive/exclusion of user files, reset, clean, force, branch switch,
update-ref shortcut, auto-rebase or product test hook runs. An error/uncertain
outcome reports held and is not automatically retried in the command.

A `main_update` hold skips ONLY primary checkout maintenance. Safe planned issue
work still uses fresh origin/main; an existing issue/CI-repair branch and its
registered dirty worktree remain untouched. A coding lease never gets deleted
to make main updating possible. Already-current main requires no mutation.

The live audit found all six primary checkouts behind while remote-tracking
main was current. Every primary contained untracked local files; Core had two
incoming path collisions, and Lumen had an existing lease. This patch does not
preserve/move those files or update any live checkout. Those holds require
separate deliberate owner coordination/content preservation before FF can run.

Deployment: review the worker and affected tests, then update the shared worker
and this documentation through the single installation owner. No new runtime
dependency, schedule, permissions, package version, CI/review or done change.
The existing local guard is not a filesystem CAS against noncooperating writers.
