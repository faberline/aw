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

AW defines stages and their success barriers. Each repository defines its
job count and batch layout. There is no shared fixed count. Preflight and
impact can each use several jobs. Every later stage waits for all required
jobs in its earlier stages.

The job IDs are examples, not an approved gate inventory. List every test job
from that workflow exactly once. Keep the full required check inventory,
feature expansion and issue contracts in the qualification policy. The final
review and result writer remain separate trusted workflows.

An authenticated `fail` or `block` review also needs a readable issue comment.
The result writer includes every finding, uncertainty, and unmet requirement,
with the exact head, base, run, attempt, job, and receipt link. A successful
workflow that records a negative review is still a failed review stage.
Long reasons use numbered comments. No reason is silently truncated.

Reason comments are separate from the durable `todo` recovery journal.
Existing journal identities and pending/ready/applied bodies stay unchanged.
A retry can add the missing reason to an old journal without resetting an
agent's later `doing` claim. Duplicate delivery reuses each bot reason comment.
Unknown comment writes remain retryable before any new label change.

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
exact source, complete CI, independent review, and grant before publication.

AW does not require Claude review, including a PR that only changes product
adoption grant data. A new grant can use `grant_authorization` with exactly
`{"version":1,"kind":"product-evidence","pr":<AW grant PR number>}`.
The verifier requires that AW PR to have merged into main, change only the
grant data file, retain its full control workflow, and pass the complete AW
controls at its exact head. It authenticates the grant's current default
source and verifies the product's full CI and latest independent Claude PASS
again. No new AW model call is involved. The legacy attestation paths remain
available for historical evidence. An unmarked grant remains held. This mode
does not waive guarded actor/recovery rollout, protected adoption, or provider
requirements. Its product scope is Core, Lumen, Keep, Tape, Defer, and Relay.

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
