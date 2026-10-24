# Trusted PR qualification contract v1

This is the shared implementation for aw #73. Defer #42 owns the first real
consumer mapping and thin integration. No consumer is activated by this change.
All six live consumers remain failure-only with their real blockers. aw has no
AGY line. No new cloud job, infrastructure or permission is installed here.

## Three validation boundaries

Local work runs only affected isolated unit/static tests. PR qualification uses
applicable cloud unit, functional/e2e and relevant short performance checks.
Release full qualification and its performance matrix are separate; a PR does
not need release qualification. This contract evaluates evidence; it does not
select shell commands, launch jobs, or prove test assertions by parsing source.
The consumer review owns exact commands, actual assertions, selector coverage,
job identities and performance relevance. A broad full-workspace unit check can
cover affected units without implementing a new selector.

## Reviewed policy

`qualified-policy.example.json` is a placeholder schema, not live evidence.
`workflows` retains the explicit workflow-path/job-name allowlist. Optional
`qualification.version: 1` adds:

- `checks`: stable IDs mapped to allowlisted exact workflow/job names and one
  kind: `unit`, `functional_e2e`, `performance`, or `policy`.
- `trusted_workflows`: numeric Actions workflow IDs and reviewed Git blob SHA
  of each workflow definition. The definition must match at both frozen PR
  head and base. A workflow-definition change cannot approve itself.
- `always`: nonempty real `policy` checks, required even for documentation.
  This forbids vacuous zero-check success. The anchor must be a completed
  independent policy validation job, not the evaluator waiting on itself.
- `rules`: safe repository-relative path globs, explicit required check IDs and
  category exemptions with reviewed reasons. Every changed path independently
  requires or exempts all three test categories. All matching rules union their
  requirements; required wins across overlaps. An exemption for docs cannot
  cover another source path. `*` stays in a directory; `**` crosses directories.
- `protected_paths`: all transitive local gate infrastructure, including test
  selection/enforcement scripts and local actions. Changes to these paths or
  any `.github/workflows/` path remain pending for separate policy review.

Unknown/unmapped paths fail closed; there is no fallback to an invented full
matrix. Renames and copies include both paths; deletions include the old path.
Unknown status, missing previous path, duplicate/incomplete inventory, zero
changes or the GitHub 3000-file cap cannot qualify. A performance exemption is
specific reviewed applicability, never inferred from an absent/skipped job.

All gate selection/enforcement must be immutable or covered by protected paths.
Workflow blob equality alone does not establish transitive script trust. The
consumer pilot must document pinned reusable/action sources, protected local
gate roots, head/merge checkout behavior and fixed job-to-test meanings. A job
whose selected tests can change without changing its declared identity needs a
reviewed selection proof; this interface does not trust PR outputs/artifacts as
such proof. Product/test changes still need ordinary independent review.

## Evidence and lifecycle

Only GitHub API metadata is used. No PR config, code or artifact is executed by
the privileged evaluator. Each selected workflow uses the latest run and exact
latest attempt for the current PR head and same repository, `pull_request`
event, allowlisted path, trusted numeric identity and matching PR number/head/
base association. Unknown association or base binding is pending. A newer
untrusted run cannot fall back to an older green one. The pilot must establish
that its actual runner metadata provides this binding; absent proof stays
pending and must not be replaced by an arbitrary uploaded receipt.

Every selected job must be completed success. Missing, skipped, cancelled,
neutral, pending and stale results never produce done. Confirmed trusted failure
keeps the existing todo handoff and diagnostic links even if other required
evidence is missing. Applicability/definition gaps prevent done. Existing global
`qualification_blockers` and failure-only mode independently prevent done too.

The existing full-mode controller already supplies journal recovery, serialized
reconciliation and label writes; there is no second lifecycle engine. Success
receipts bind the policy, frozen base/files/selection fingerprint and current
attempts. Failure keeps the existing source-policy coding epoch so base changes
do not undo an AGY reclaim. Before labels, the controller rechecks evidence,
recomputes applicability, then refreshes head/lifecycle immediately before target
addition and old-label removal. GitHub has no atomic cross-resource CAS; the
existing documented final API race remains, with serialized controller writers.

## Shared distribution and validation

Use `faberline/aw/.github/actions/issue-ci@<reviewed 40-hex commit>` in the
consumer's trusted default-branch workflow. Mutable references are refused.
The action freezes the consumer default-branch commit and fetches its policy
directly through GitHub API. No checkout or six copies of evaluator code are
needed. `shared-action.yml.example` retains the existing repository-wide
concurrency group and permissions: contents/actions/PR read, issues write.
There are no new credentials or checks-write permissions.

Operations:

- `validate`: shared schema validation of default-branch policy; no qualification
  claim or label mutation. Offline equivalent:
  `python3 plugins/faberline/skills/issue-ci/scripts/validate-policy.py POLICY`.
- `qualify`: read-only evaluation for `pr` and exact `head`. Done exits 0;
  pending/failure exits 2. Use read-only permissions when only evaluating.
- `handoff`: existing serialized reconcile-all `workflow_run` operation using
  `run-id`; current PR metadata decides each handoff, not the wake's old result.

Consumers must not run the read-only evaluator as its own required anchor.
It reports qualification and is not evidence for the checks it is awaiting.
Existing legacy configs without the optional contract keep their behavior.
The shared action/offline adoption validator additionally requires explicit
mode and blockers; shared full adoption requires v1, so removing the contract
cannot silently fall back to legacy full. Legacy direct-CLI behavior remains
compatible; existing live failure-only copies are not changed.
Full-mode adoption is a separate reviewed pilot change with real positive and
negative evidence; never merely clear current blockers or turn off failure-only.


## Read-only review readiness (first phase)

[REVIEW-READINESS.md](REVIEW-READINESS.md) defines the separate versioned
ready/wait/fail contract and authenticated receipt verifier. It reuses this
qualification selection and latest-attempt collector without lifecycle writes.
Failure-only does not itself prevent objective review readiness; true coverage
blockers still do. No live consumer, index publisher, reviewer dispatcher/lease,
frequency or done activation is installed by that interface.
