# Codex post-merge worktree maintenance

AGY creates/reuses an implementation tree and hands off a PR. Codex owns review
and any separately authorized merge; after **verified MERGED**, Codex can request
this shared deterministic maintenance command. AGY `finish` does not remove a
tree or merge anything. Cleanup status is separate from merge/review/CI status.
This draft adds no schedule, standing merge authority, live installation,
permission, label, branch deletion, main synchronization or package upgrade.

## Discovery and provenance

`git worktree list --porcelain -z` is the complete registered discovery source,
not directory scanning. A path prefix is neither ownership nor deletion proof.
Current AW creation stays at `<repo>/.claude/worktrees/issue-N`; existing exact
registered branch paths are still reused without resets. No root override,
worktree migration or rewrite of historical paths is introduced.

Only a newly created AW worker tree gets controller-owned creation provenance
in the common Git directory's `issue-work.trees.json`: exact repo/origin/common
identity, path/admin inode identity, issue/branch and owner process identity.
Claims update owner history under the existing guard. Submission records PR,
submitted head and release of coding ownership, never cleanup eligibility.
Historical/other-app trees without verified provenance remain report-only;
there is deliberately no name-based adoption command. Independently verified
records may bind an arbitrary registered path: cleanup itself does not require
moving it into `.claude`. Main, Codex/Claude app-owned unknown trees, Mamba
clones, active Lumen work and missing registrations are not inferred eligible.

Every recorded owner must have released work, have known same-host process
identity, and its recorded process must be dead. A live/reused PID, denied or
unknown process probe, foreign host, unknown history or relevant persistent
lease holds the tree. Absence of `issue-work.lock` alone grants nothing.
`ps` failure records unknown identity, not inactive; it does not request more
permissions. This is cooperating AW ownership/process evidence, not a claim
that the tool can discover every unrelated desktop session. The Codex caller
must coordinate native owners; untracked/uncertain ownership is report-only.

## Two explicit operations, one tree per receipt

From the primary checkout, using the installed trusted script path:

```sh
/path/to/issue-work/scripts/maintain-worktrees.sh --receipt /outside/tree/merge.json > /outside/tree/plan.json
/path/to/issue-work/scripts/maintain-worktrees.sh apply --plan /outside/tree/plan.json
```

The first/default operation is a dry-run: local Git reads plus ordinary trusted
GitHub REST reads; JSON reports eligible/held and registered inventory. It does
not fetch objects, change refs, remove/prune/move a tree, write registry state,
close an issue or write a label. Opening the shared kernel guard may create its
lock inode. Exit 3 means held; an error/status failure is never permission.

Codex supplies an exact merge binding (not an authorization token/signature):

```json
{
  "repo": "owner/repository",
  "pr": 123,
  "reviewed_head": "<40-hex PR head>",
  "base_branch": "main",
  "base_sha": "<40-hex actual first parent of merge>",
  "merge_sha": "<40-hex verified merge commit>",
  "merge_tree": "<40-hex verified landed tree>"
}
```

No receipt/hash/artifact independently authorizes deletion or proves a review.
The caller must already have user authority for the merge and cleanup. The tool
freshly re-reads exact repository, PR head/base/merged state/merge commit/tree,
default branch and every open PR. Queued/auto-merge/open PRs do not qualify.
The associated issue must also be closed: partial work left open preserves reuse.
No other open PR may use the same branch/head. Review or `done` labels are not
substitutes. Never use legacy `review.sh pass` cleanup or a merge helper's exit
alone as permission; this command performs no merge or lifecycle action.

## Content and landed-history proof

All objects must already exist locally, and the merge must be reachable from
fresh GitHub default HEAD. Missing objects/stale remote data hold; refresh via
the normal authorized workflow separately. There is no fallback to old refs,
an arbitrary artifact, patch-id, changed-check names or PR-controlled code.

Local registered branch HEAD must equal BOTH submitted and merged PR head;
any additional local commit, including an empty same-tree commit, holds.
For an ordinary merge, PR head must be reachable from that actual merge and
fresh default. For a single-parent squash (or equivalent single-parent result),
compare the entire raw aggregate change from the unique PR merge base to head
against actual merge parent→merge: full old/new blob IDs, modes and paths,
binary content and additions/deletions, with renames disabled. Empty, changed
or ambiguous aggregates hold. Multi-commit rebase results not covered by this
exact proof hold; it never assumes ancestry failure means loss or success.

Every HEAD reflog/ORIG_HEAD commit must be preserved in the submitted history or
fresh default; unknown unique history holds. Branches are kept regardless.
Assume-unchanged/skip-worktree (including lowercase index tags), sparse settings
or patterns hold before status inspection; flags are never automatically cleared.
The shared read-only index guard is bound into plan evidence and rechecked
immediately before removal: a clean status cannot certify hidden tracked bytes.
Tracked/staged/untracked/**all ignored** content holds, including caches with
unknown value. No cache deletion policy is inferred. Submodule/gitlink trees,
locked/detached/prunable registrations, moved paths/admin inode, symlinks,
foreign ownership and unknown/in-progress admin state hold. Status errors and
path absence never permit pruning. No reset/clean/force/remove-branch exists.

Hooks/fsmonitor and external diff/textconv cannot execute PR-selected programs.
Any configured clean/smudge/process filter holds before status/removal, using the
same shared predicate as main FF. Names are read without command values; neither
disabling filters nor cached filtered stats certify raw worktree bytes. A
separate reviewed filter-aware content policy is required even for a globally
configured driver. No persistent configuration changes or product code run.

## Apply, recovery and concurrency

Explicit `apply` accepts only an eligible dry-run from the same repo/controller
version within ten minutes. Plan hashes bind data, not authority. Under the
same `issue-work.guard`, re-read registry, ownership, GitHub and all local state.
Any change aborts. Preserve a verified self-contained branch bundle and allowed
worktree admin files (HEAD/index/reflog/ORIG_HEAD/commit message/pointers), hashes
and receipt under `.git/issue-work.maintenance/<proof-digest>/`, with private modes.
Both the reachable-object storage estimate and actual bundle are capped at
256 MiB; larger/unsupported cases hold rather than remove without recovery.
No valuable dirty/untracked/ignored content is moved into this path: it was held.

Revalidate again after preservation, verify recovery bytes, then immediately
recheck local state before **non-force** `git worktree remove`. Verify path and
registration disappeared and branch stayed exact. Retire provenance into its
recovery journal so a later reopened issue can create a new proven tree.
A failed/uncertain removal keeps recovery and journal, never automatically retries;
inspect actual path/registration before making any new plan. Editing plan timestamps
or reports cannot replay the same uncertain proof journal. Missing-path
registration pruning remains a separate review scope.

The guard serializes cooperating AW operations; it is not a filesystem CAS or
proof against an unrelated noncooperating writer after the last check. Unknown
native owners must stay held, not be bypassed by a checkbox. Recovery and branch
preservation remain mandatory. The 185-tree audit's preliminary clean counts
are **not** certified deletion eligibility, and no live cleanup occurs in this PR.

## Deployment

Install the worker, new state/index helpers and maintenance entrypoints/docs coherently
only after independent review/approval. The existing issue-loop transport module
is reused; no second evaluator, cron, credential or SDK is added. AGY creation,
claim and finish only maintain provenance; Codex invokes cleanup after an
independently authorized verified merge. Existing registered worktree reuse,
priority-based planned work, CI repair and CI-owned done remain unchanged.
