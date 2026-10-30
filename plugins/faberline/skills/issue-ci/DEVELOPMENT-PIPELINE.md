# Development pipeline adapter

This adapter implements metadata and stage-order checks for
[DEV-PIPELINE-1](../../../../docs/standards/development-pipeline.md).
Consumers opt in through their reviewed default-branch policy. Publishing the
adapter does not activate a consumer or replace its product tests.

## Policy

Add `development_pipeline` to `.github/issue-ci-required-gates.json` through
the existing protected policy adoption path. For example:

```json
{
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

The job IDs are examples, not an approved gate inventory. List every test job
from that workflow exactly once. Keep the full required workflow/job names,
feature expansion and issue contracts in the qualification policy. The final
review and result writer remain separate trusted workflows.

The checker verifies transitive `needs` dependencies. Every unit/static job
waits for impact and preflight. Both expensive groups wait for every unit
job. Cycles, unassigned jobs, failure-ignoring steps/jobs and matrix fail-fast
are refused. The default-branch policy and exact workflow blobs remain the
authority; a stage label cannot approve an edited workflow.

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
failed expanded jobs, including authenticated failed review setup jobs.
Each job/run attempt gets one `aw-job-failure` issue comment with the failed
step, observed conclusion, exact commits, workflow/run/attempt/job identity,
and direct evidence links. No PR logs or artifacts are executed. A missing
failed-step detail is reported as missing, not invented as a root cause.

These comments do not own lifecycle state. The existing coding-epoch journal
returns work to `todo` once. A later matrix failure can add its comment after
that handoff, while preserving a worker that already claimed `todo`. Unknown
comment responses remain retryable through a fresh comment read. Old/new
review producer metadata is passed separately from any preserved CI journal
policy, so adding review observation does not silently alter journal identity.

An unlinked or ambiguous PR gets a deduplicated PR comment instead of a guess
at an issue. No issue label is changed by that diagnostic.

The writer must also receive default-branch `push` events after adoption.
Its immutable source and event identity are checked before it re-evaluates
open PRs. Legacy consumers do not gain a new wake or lifecycle behavior until
they explicitly adopt the development policy and matching producer source.

## Rollout

Run the complete isolated control suite, then the consumer's unchanged
required product tests and independent review. Retain the protected policy,
writer identity, ownership and merge/close checks. Use
[AW #129](https://github.com/faberline/aw/issues/129) for shared enforcement and
the six linked adoption issues for live integration proof.

AW itself has no Claude-review gate. Release pipelines and schedules are
outside this adapter.
