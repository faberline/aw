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
the same repository-wide concurrency group. The controller token needs only
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
exact links. Reruns are separate attempts. Stale/unconfigured event runs
cannot mutate. The PR must use `issue/N` or `agy/issue-N` and link `Closes #N`.
Fork PRs and custom branch conventions are deliberately refused.

Each handoff journals an authenticated Actions-bot comment keyed by head,
manifest and result; success also keys latest runs/attempts. A failed head
is returned only once, so later workflow completions/reruns cannot undo
a later AGY claim before it pushes a corrected head. API errors fail closed and are reported; an interruption after the
journal but before all label writes requires human reconciliation after
checking the current head and lease. Do not delete the journal to replay
blindly. GitHub has no atomic compare-and-swap across head, comments and
labels: the controller rechecks the head and complete run snapshot directly
before label writes; consumers must retain the serialized writer and rerun
on subsequent CI completions. Review always rechecks the current head.

Local policy: affected unit/static only. MR cloud qualification still needs
affected unit/functional/e2e/performance; release needs complete declared
functional/performance qualification. This interface does not redesign test
selection, milestones or performance policy, and dispatches no test jobs.
