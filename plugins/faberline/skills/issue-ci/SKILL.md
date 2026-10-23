---
name: issue-ci
description: "Trusted CI handoff interface; CI alone moves submitted doing issues to done or failed current-head issues to todo."
user-invocable: false
---

# CI lifecycle handoff

AGY owns only `todo -> doing`. Finish opens/updates the same PR and leaves
`doing`. CI alone writes `done` after ALL configured workflow/job gates pass
for the current PR head; a failed required gate returns the issue to `todo`
with exact run/job links. Reviewer independently verifies the same gates and
head after `done`, reports a decision, and never merges or changes labels.

## Consumer wiring (not deployed by this change)

This aw change supplies common scripts and a template. It does not wire or
change tape, keep, core, lumen, defer, or relay, their deployed workflows,
branch protection, credentials, permissions, or six paused AGY schedules.
For each consumer, explicitly approve the required workflow paths and exact
job names (including each matrix expansion) and install a nonempty trusted
`.github/issue-ci-required-gates.json`, based on the example. Existing branch
protection alone is not inferred: an empty or unspecified gate set refuses.
The names in the example are placeholders, not an assertion of live gates.

Choose the source before rollout: either maintain this explicit manifest as
the reviewed gate contract, or implement a separate tested adapter for that
repository's branch-protection/ruleset requirements. Do not guess that all
currently visible green checks constitute the required set.

Copy/adapt `templates/issue-ci.yml.example` onto the default branch. Include
every required workflow display name in its trigger. Vendor these scripts
from a reviewed aw revision into the trusted default branch; do not fetch a
mutable controller at runtime. All privileged controllers must serialize in
the same repository-wide concurrency group. Every wake uses `--reconcile`
to paginate ALL managed open PRs and re-evaluate each current head. Default
pending replacement therefore coalesces wake-ups without dropping a PR;
an event naming C also reconciles B whose pending wake was replaced. A
failure/stale head on one PR is reported after other PRs are reconciled. The controller token needs only
contents/actions/PR read and issues write; no PR write, credential creation,
or branch-protection changes. The reviewer uses a read-only token for CI
verification, and normal PR-comment permission for reporting its review.

The controller uses only GitHub API metadata. Never check out a PR SHA,
execute its scripts, load its gate manifest, or download/evaluate its
artifacts with the privileged token. Workflows are aggregated by exact path,
latest run and current attempt, `pull_request` event, same repository and
head SHA. Only completed success counts. Missing/pending/cancelled/neutral/
skipped checks cannot produce done. Skips currently have no approved bypass;
consumers must produce real qualification checks. Run/job failures include
exact links. Reruns are separate attempts. Single-head stale/unconfigured events refuse. Reconciliation validates the
wake workflow/repository but never uses its result or old SHA as evidence;
it recomputes every PR from fresh current-head metadata. The PR must use `issue/N` or `agy/issue-N` and link `Closes #N`.
Fork PRs and custom branch conventions are deliberately refused.

Each handoff journals an authenticated Actions-bot comment keyed by head,
manifest and result. Success also keys the latest run attempts. Failure
keys the coding epoch: the latest authenticated, applied done receipt for
this PR/head/policy, or the initial epoch. Additional workflow failures on
the same head cannot repeat a handoff or undo a claimed AGY retry. After
CI successfully applies done, a later failure belongs to a new epoch and
can legitimately return the issue to todo again. Journal phases are:
`pending` (retryable intent), `ready` (desired label durably known present,
recorded before any old label deletion), and `applied` (completed receipt).
Snapshot/head failures after intent do not dedupe future handoffs. After a
partial API failure, retry completes the checkpoint/removal sequence. If a
ready receipt's desired label has since been consumed (AGY claimed todo),
recovery never re-adds it or removes that actor's state. Workers refuse
mixed lifecycle labels, protecting the add/checkpoint/delete boundary.

GitHub has no atomic compare-and-swap across head, comments and labels.
The controller rechecks the head, issue lifecycle and complete run snapshot
before writes, and head/lifecycle before each old-label removal. Consumers
must retain the serialized writer. Review always checks the current head.

This aw controller repair is validated with affected local unit/static
checks; it does not require an added aw cloud workflow. Consumer product MR
qualification separately needs affected unit/functional/e2e/performance;
product release needs complete declared functional/performance qualification. This interface does not redesign test
selection, milestones or performance policy, and dispatches no test jobs.
