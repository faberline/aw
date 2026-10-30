# Milestone release lifecycle

Progress each repository's current release milestone on each planning pass.
Use Semantic Versioning for the release version. The Git tags are
`vMAJOR.MINOR.PATCH-rc.N` for a release candidate and `vMAJOR.MINOR.PATCH` for
the final release. Start `N` at 1 and never move, overwrite or reuse a tag for a
different commit. Reconcile existing tags and runs before creating anything.

## Decide if the milestone goal is complete

Read the milestone's documented goal, acceptance, existing decisions, full
paginated issue inventory and related PRs, including closed issues and merged
PRs. Check actual merged commits and dependencies against the goal. Wait while
any milestone issue is open or any related PR is unmerged. A closed issue or a
merged partial PR alone does not prove the milestone goal is met.

If the goal is not met, create the missing, scoped implementation or test
issues in the same milestone. Check existing issues and PRs first to avoid
duplicates. Use `plan.sh new-backlog` when the todo queue is full; normal
planner admission later selects work. The issue must describe the real gap,
its acceptance and test gate. Do not invent a product contract or overwrite a
human's issue. Continue this cycle until the goal and all associated work pass.

## Create and test an RC

After all milestone issues close, related PRs merge and the goal is met, bind
the next RC to the exact landed default-branch commit and the milestone's
release version. Verify the version against the repository's actual release
contract and package metadata. If the mapping is missing or inconsistent,
create the appropriate issue in the milestone; do not guess a version.

Before creating a tag, verify that the repository accepts the `v...-rc.N`
format and has a full release pipeline bound to that exact tag and commit. If
either is missing, create a milestone issue to add it and do not create an RC
yet. Do not substitute a build-only candidate or partial CI for full tests.

Create the next unique RC tag at that commit. Trigger the repository's
**complete** release test pipeline for that exact tag and commit. A tag push is
not proof that a pipeline ran: confirm the workflow identity, run ID, attempt,
tag, commit, required matrix and every result. Dispatch the repository's
workflow when required. Follow the existing functional, E2E, performance,
security, platform and artifact gates; preserve their thresholds. Pending
runs stay pending. Do not create another RC for the same unchanged candidate
merely because a run is slow, queued or its result is unknown.

On a confirmed test or goal failure, create nonduplicate issues in this same
milestone with the exact RC, commit, failing run, evidence and required fix.
Wait for those issues to close and related PRs to merge. Recheck the milestone
goal, then create the next RC at the fixed landed candidate and run the full
pipeline again. If the fix is entirely outside the repository, the candidate
commit may be unchanged only when the issue records and verifies that fix.
Keep failed RC tags as history.

## Promote the passing candidate

Only promote an RC after all milestone issues are closed, related PRs are
merged, the goal is still met and every required full release gate passed for
the exact RC and commit. Recheck the immutable tag, candidate, run attempt and
milestone immediately before promotion. Create the final `vMAJOR.MINOR.PATCH`
tag at that same tested commit. Use the repository's release/promotion workflow
and existing approvals and verify the actual tag and required publication
results. Do not rebuild a different candidate and call it the tested release.

Close the milestone only after the final tag and all required release results
are verified. Read the milestone back and report the actual outcome.

## Safety and recovery

Keep the existing coordinator and issue ownership checks. A pending, cancelled,
skipped, stale, partial or unknown result never passes. Do not change another
session's worktree or schedule. Do not bypass branch protection or alter an
existing tag. Before each write, re-read the milestone, candidate and relevant
ownership. If a tag, issue, workflow dispatch, promotion or closure request has
an unknown outcome, inspect provider state before any retry; do not blindly
repeat a write. An existing tag at another commit is a blocker.

Report the milestone, version, current phase, exact RC and commit, full test
runs, linked gap issues, final tag and any blocker. A `READY` assessment is not
proof that publication or milestone closure occurred.
