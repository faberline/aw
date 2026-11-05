# Primary main checkout freshness

This is a small pre-work maintenance step, separate from Codex post-merge
worktree cleanup. `next` retains the existing repository guard and owner/lease
checks, fetches all `refs/heads/*:refs/remotes/origin/*` (preserving new remote
PR branches), binds exact origin/main, then attempts ordinary primary-checkout fast-forward maintenance.
A failed fetch refuses new selection; it never silently uses stale origin/main.
An associated existing PR whose branch/ref is unavailable after fetch holds;
its repair is never created from main.

Before status or checkout, the main-only absence proof rejects every relevant
attribute source, including an empty/non-filter file or macro: index/HEAD/incoming
`.gitattributes`, physical root/nested path ancestors, info/global/system sources,
and attributesFile/attribute environment overrides. Global media/LFS driver
**definitions alone** may remain configured only when all those sources are
proven absent. No driver is disabled, executed, or declared safe; repositories
with attributes need a separate reviewed policy. Cleanup's existing configured-
filter hold is unchanged and never uses this main-only exception.

The proof binds exact HEAD, staged index records, incoming commit/path inventory,
relevant effective config with origins/scopes, environment, source absence and
resolved source parent identity. Built-in Git check-attr must return complete
`filter: unspecified` results for every path in worktree/index/HEAD/incoming.
Recheck sources/config/index/refs after those reads, before each status, and
again immediately before FF. Read errors, overrides, unsupported Git, symlink
sources/repository ancestors, submodule or unknown modes, missing/unknown results
and any identity change hold. Standard platform default directory aliases such
as macOS /etc are resolved and bound; the attribute file itself must be absent.
Every inventory path component must be printable ASCII, and `.gitattributes`
case aliases are rejected in HEAD/index/incoming at any depth, regardless of
core.ignorecase. Byte-sensitive Git tree/attribute reads cannot certify that
checkout names will remain distinct on a case-insensitive filesystem. Unicode,
control-byte or other uncertain name equivalence holds rather than guessing at
normalization. No Unicode normalization or attributes parser is introduced.

Also hold assume-unchanged/skip-worktree index tags and sparse settings/patterns;
do not clear flags. The shared read-only index guard is rechecked immediately
before FF. A clean status alone cannot certify visible tracked content.

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
and this documentation through the single installation owner. The worker,
main_filter_safety.py and shared index guard must be available coherently; no new
package dependency, schedule, permissions, package version, CI/review or done
change.
The existing local guard is not a filesystem CAS against noncooperating writers.

### Bounded metadata-command transport

The main-only filter helper sends finite binary input through a private unlinked
regular temporary file, rewound before launch. Git receives EOF at its file end;
parent descriptor lifetime cannot keep a pipe open. Stdout/stderr use private
regular files too, not pipes. A monotonic controller deadline and size monitor
operate independently of communicate/selector IO. Input and accepted stdout/
stderr are each capped at 16 MB, including bounded final reads; output growth is
polled while running (not a hard instantaneous disk quota). Deadline/overflow/
read failure refuses the proof. Only that invocation's Popen child can be killed,
with a bounded reap; uncertainty holds without retry. No process-group/caller
signal or guard/lease rewrite is performed. close_fds prevents guard inheritance.

Isolated Python 3.12.11 controls reproduced multi-pipe/input hangs beyond the
inner timeout and verified file-backed controls, including exact synthetic
10,516-byte check-attr input and 16,064-byte output. This does not prove the exact
blocked syscall of any active worker. Process creation/kernel file IO are not
preemptible by a Python deadline; the fix removes the demonstrated pipe path,
not every possible OS stall. Qualification, source/index/alias safety, cleanup
transport and existing owner/guard behavior are unchanged.
