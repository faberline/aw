# Development pipeline adapter

This adapter implements metadata and stage-order checks for
[DEV-PIPELINE-1](../../../../docs/standards/development-pipeline.md).
Consumers opt in through their reviewed default-branch policy. Publishing the
adapter does not activate a consumer or replace its product tests.

The 2026-10-31 contract has four CI stages. Claude review runs locally.
CI must not review, merge or close an issue. All necessary current-input CI
success adds the `done` label to the OPEN issue for local `issue-review`. Old cloud review,
merge and recovery adapters remain compatibility code, not activation work
for the new normal-development design.

## Policy

Add `development_pipeline` to `.github/issue-ci-required-gates.json` through
the existing protected policy adoption path. For example:

```json
{
  "completion_owner": "local",
  "development_pipeline": {
    "version": 1,
    "workflow": ".github/workflows/ci.yml",
    "stages": {
      "preflight": ["development-preflight"],
      "impact": ["unit-plan"],
      "unit": ["workspace-format", "workspace-unit-static"],
      "functional": ["workspace-contracts"],
      "performance": ["measured-performance"]
    }
  }
}
```

AW defines stages and their success barriers. Each repository defines its
job count and batch layout. There is no shared fixed count. Preflight and
impact can each use several jobs. Every later stage waits for all required
jobs in its earlier stages.

The job IDs are examples, not an approved gate inventory. List every test job
from that workflow exactly once. Keep the full required check inventory,
feature expansion and issue contracts in the qualification policy. Independent
review runs locally after complete necessary CI. One trusted
cloud CI label writer retains comments, qualified-success `done`, and
current-attempt failure-to-`todo` handoff.

For `completion_owner: "local"`, remove `claude_final_review`,
`native_auto_merge`, `issue_recovery`, `issue_guard_cutover` and
`issue_guard_rollout` producer configuration. The shared validator refuses
mixed local/cloud completion configuration. Existing active ownership data
and unrelated work must still be preserved during the reviewed transition.

The CI handoff keeps full current-input test qualification readable to the
local reviewer. Its apply path adds `done` only after complete required
current-input CI succeeds. The issue stays open. Missing, skipped, cancelled,
stale, failed or unresolved qualification cannot produce `done`.
Failures still use the existing durable comment/todo journal. Duplicate
failure delivery cannot reset a later worker claim. The local reviewer and
controller retain exact-input, negative-result and merge-before-close checks.

The checker verifies transitive `needs` dependencies. Every unit/static job
waits for impact and preflight. Both expensive groups wait for every unit
job. Cycles, unassigned jobs, failure-ignoring steps/jobs and matrix fail-fast
are refused. The default-branch policy and exact workflow blobs remain the
authority; a stage label cannot approve an edited workflow.

Repositories can group several required cases in one physical job. A reviewed
adoption lists every such check ID in `development_pipeline.grouped_checks`.
Each check keeps its ID and category, adds `case_id` equal to that ID, and
keeps its prior job label in `case_label`. Its `job` names the new batch.
Every check in a batch needs this mapping. The batch runner must reject
missing or duplicate cases, execute every case, retain each result, and fail
if any case fails. A job with zero selected cases cannot pass. Source changes
to the runner remain protected and need independent product review.

An explicit `replaces` map can move one required workflow path to another.
It cannot combine two old workflows or discard an old check. These mappings
do not approve themselves. Protected policy adoption still authenticates the
exact source, complete CI and local independent review before publication.

AW does not require Claude review. The former product-evidence grant and
cloud guarded publisher protocol is historical compatibility code. It is not
a prerequisite for the new local-review normal-development pipeline.

## CI label writer entry

Use the [CI label template](templates/development-report.yml.example) with
real test workflow names, an immutable AW revision and the consumer's actual
`development_reporter` identity (`path`, `id`, `definition_sha`, `job`). The
`development-report` operation authenticates the current default source,
actual run/attempt, exact workflow blob and single-writer permissions.

Its token has read access plus `issues: write`. It has no contents, PR or
status write permission. A request filter allows only CI comments,
`todo`/`done` labels and removal of conflicting lifecycle labels. Before a
`done` label write it rechecks the current PR and complete qualified CI. It
refuses merge, issue closure and status publication. Every wake scans the
complete current PR queue. Main updates can therefore return stale PR work to todo
without launching tests or paid review. Reuse the existing failure journal.

## Read-only action operations

The pinned `.github/actions/issue-ci` action supports:

- `development-preflight`: read PR/main ancestry, mergeability and one
  canonical open issue. Inputs are `pr`, `head`, and optional frozen `base`.
- `development-plan`: require successful preflight, select the trusted
  qualification plan, validate its workflow DAG, and reject unresolved
  required coverage. It does not execute candidate programs.

Outputs include `base`, `head`, `issue`, metadata-only `result`, and, when
planning completes, `plan-sha256`. The action prints
`AW_DEVELOPMENT_PIPELINE_V1` with the same JSON. Main, PR or issue movement
during planning refuses. No operation here has issue or merge-write authority.

Required unit/functional/performance test rosters and execution remain the
consumer's existing enforcers. Missing checks, zero tests and changed
thresholds cannot be converted into a success by the metadata adapter.

## Single-writer failure reporting

For opted-in consumers the existing unified writer also observes individual
failed expanded jobs, without invoking a review producer.
Each job/run attempt gets one `aw-job-failure` issue comment with the failed
step, observed conclusion, exact commits, workflow/run/attempt/job identity,
and direct evidence links. No PR logs or artifacts are executed. A missing
failed-step detail is reported as missing, not invented as a root cause.

These comments do not own lifecycle state. The existing coding-epoch journal
returns work to `todo` once. A later matrix failure can add its comment after
that handoff, while preserving a worker that already claimed `todo`. Unknown
comment responses remain retryable through a fresh comment read. Local completion configuration contains no cloud review producer. Retain old
journal identities where their verified deployed semantics are unchanged.

An unlinked or ambiguous PR gets a deduplicated PR comment instead of a guess
at an issue. No issue label is changed by that diagnostic.

The writer must also receive default-branch `push` events after adoption.
Its immutable source and event identity are checked before it re-evaluates
open PRs. Legacy consumers do not gain a new wake or lifecycle behavior until
they explicitly adopt the development policy and matching producer source.

## Rollout

Run the complete isolated control suite, then the consumer's unchanged
required product tests and local independent review. Retain the protected policy,
CI label-writer identity, ownership and local merge/close checks. Use
[AW #129](https://github.com/faberline/aw/issues/129) for shared enforcement and
the six linked adoption issues for live integration proof.

AW itself has no Claude-review gate. Release pipelines and schedules are
outside this adapter.
