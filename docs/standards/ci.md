# CI stages

Standard S7. State: **active since 2026-11-09**. `aw ci check` checks the
workflow and the ruleset (see [Checker](#checker)); see the
[definition](README.md#what-a-standard-is).

The idea is the same as GitLab CI stages. **The stages are the same in every
repository. Each repository cuts its own jobs inside a stage.** One fixed job,
`gate`, collects the results. The ruleset requires only `gate`, so a
repository can add, split or remove jobs without a ruleset change.

GitHub Actions has no stages. A stage here is a job id prefix and a `needs:`
order.

## The stages

| Order | Stage | Job ids | Who decides the jobs | Content |
|---|---|---|---|---|
| 1 | `lint` | `lint` | fixed | `cargo fmt --all -- --check`, then `cargo clippy --workspace --all-targets --locked -- -D warnings` |
| 2 | `test` | `test-<name>` | the repository | `cargo test --locked`, cut as the repository needs: by crate, feature set, batch or [kind](rust-tests.md#four-kinds-of-test) (`lib`, `it`). Short `perf` tests can go here. |
| 3 | `e2e` | `e2e-<name>` | the repository | e2e suites on the [shared GKE cluster](test-cluster.md) or a container on the runner. Optional: a repository with no e2e has no job in this stage. |
| 4 | `gate` | `gate` | fixed | Fails when any job of stages 1 to 3 failed or was cancelled. |

- **Order.** Every `test-*` job needs `lint`. Every `e2e-*` job needs the
  `test-*` jobs it depends on, or all of them. `gate` needs every other job
  of the workflow. A stage starts only when the stage before it passed, so a
  format error costs one short job, not a cluster run.
- **Job names.** The display name is `<stage>: <what>`, for example
  `lint: fmt, clippy`, `test: lib (all features)`,
  `e2e: operator_recovery`. The `gate` job is named `gate`.
- **Helper jobs.** A job that only prepares a later job, for example an image
  build or a [test selection](test-scopes.md#the-ci-shape), takes the prefix
  of the stage that uses it: `e2e-image`, `test-select`.
- **Selection.** A `test-*` or `e2e-*` job may skip itself with an `if:` that
  reads the changed paths ([test-scopes.md](test-scopes.md)). `gate` accepts
  `skipped`. A job that is skipped because a job it needs failed does not
  hide anything: `gate` sees that failure.

## The workflow

- One file: `.github/workflows/ci.yml`, workflow name `ci`.
- Triggers: `pull_request`, `push` to `main`, and `workflow_dispatch` if
  needed. A push to `main` runs every stage
  ([scope 3](test-scopes.md#3-full-in-the-cloud-before-every-merge-to-main)).
- `permissions: contents: read` at the top. A job widens only what it uses,
  for example `id-token: write` on an `e2e-*` job that signs in to GKE.
- Concurrency: one run per ref. Cancel an older run only on a pull request,
  so a push to `main` always finishes.
- Actions are pinned to a full commit SHA with the version in a comment.
- Cargo commands use `--locked`.
- CI reads only the code. It does not read the issue, the pull request body,
  labels or comments. `aw` owns the issue flow. Remove the issue-contract
  steps and the DEV-PIPELINE-1 gate files (`issue-ci.yml`,
  `.github/issue-ci-required-gates.json`, `scripts/issue-ci/`,
  `scripts/pr-gates/`).

The skeleton:

```yaml
# CI stages: lint -> test -> e2e -> gate.
# See https://github.com/faberline/aw/blob/main/docs/standards/ci.md
# The ruleset requires only the "gate" job. Keep its name fixed.
name: ci

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  lint:
    name: "lint: fmt, clippy"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: dtolnay/rust-toolchain@4360b52568e2003a75bf9bc1d59f33a8e3fc893c # stable
        with:
          toolchain: stable
          components: rustfmt, clippy
      - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6 # v2.9.2
      - run: cargo fmt --all -- --check
      - run: cargo clippy --workspace --all-targets --locked -- -D warnings

  # The repository cuts its own test jobs. One job is enough to start.
  test-workspace:
    name: "test: workspace"
    needs: [lint]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: dtolnay/rust-toolchain@4360b52568e2003a75bf9bc1d59f33a8e3fc893c # stable
        with:
          toolchain: stable
      - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6 # v2.9.2
      - run: cargo test --workspace --locked

  # e2e-<suite> jobs go here, with needs: [test-...].

  gate:
    name: gate
    if: always()
    needs: [lint, test-workspace]
    runs-on: ubuntu-latest
    steps:
      - name: Check every stage
        env:
          RESULTS: ${{ toJSON(needs) }}
        run: |
          echo "$RESULTS" | jq -r 'to_entries[] | "\(.key): \(.value.result)"'
          echo "$RESULTS" | jq -e '.lint.result == "success"' > /dev/null
          echo "$RESULTS" | jq -e 'all(.[]; .result == "success" or .result == "skipped")' > /dev/null
```

## Other workflows

A repository has `ci.yml` and may have these. None of them is a required
check.

| Workflow | When it runs | What it does |
|---|---|---|
| `release.yml` | a release tag or a manual run | The release pipeline, with the whole performance matrix. It is defined separately. |
| A scheduled maintenance workflow | `schedule` | For example a cluster reaper. The file says why in its first comment. |
| A manual e2e or performance workflow | `workflow_dispatch` | Reuses the `e2e-*` jobs to diagnose. It does not write a status that a ruleset reads. |

Delete a one-off workflow, for example a recovery or diagnostic run for one
version, when its job is done. The history keeps it.

## The ruleset

- The default branch has one ruleset that requires the status check `gate`
  and nothing else.
- Other rules of the ruleset stay as they are.
- A change to a ruleset needs the user's approval. The issue that moves a
  repository to this standard lists the ruleset change as its own step.
- During the move, the old required checks block the new workflow. Add
  `gate` to the ruleset first, merge the new `ci.yml`, then remove the old
  checks.

## Checker

```bash
aw ci check                         # the current checkout and its origin's ruleset
aw ci check --repo faberline/core   # the default branch on GitHub
aw ci check --dir . --no-ruleset    # local files only
```

It prints a JSON report and exits 1 when a check fails.

| ID | Check |
|---|---|
| CI1 | `.github/workflows/ci.yml` exists. |
| CI2 | The workflow name is `ci`. |
| CI3 | Every job id is `lint`, `gate`, `test-*` or `e2e-*`; `lint`, `gate` and at least one `test-*` exist. |
| CI4 | `lint` runs the two fixed commands. Spacing does not matter; a `\` line break does. |
| CI5 | Every `test-*` job needs `lint` directly. |
| CI6 | Every `e2e-*` job waits for a `test-*` job, directly or through other jobs. |
| CI7 | `gate` has `name: gate` and `if: always()`, needs every other job, and its steps check `.lint.result == "success"` and `.result == "success" or .result == "skipped"`. |
| CI8 | The default-branch ruleset requires `gate` and no other status check. Classic branch protection is not read. |
| CI9 | Every exception names a check `CI1`–`CI8` and has a reason. |

### Exceptions

A repository opts out of one check in its own `aw.toml`. The reason is
required.

```toml
[[standards.ci.exception]]
check = "CI8"
reason = "no ruleset until the first release"
```

An excepted check is still printed. It does not fail the run.

## Measured state, 2026-11-09

| Repository | Workflows on `main` | Required checks | Main gap |
|---|---|---|---|
| `aw` | `ci.yml`, `gke-test.yml` (reusable), `gke-test-reaper.yml`, `ci-image.yml` | none | follows the stages; no ruleset yet |
| `core` | `ci.yml`, `development-ci.yml`, `issue-ci.yml` | 20 | DEV-PIPELINE-1 contract gates, "candidate" jobs |
| `lumen` | 13, with 4 one-off release recovery files | 32 | contract gates, about 3 hours per pull request |
| `som` | 13 (`m6-*`, `python-*`, `rust-*`, `som-service.yml`) | none | old M6 workflows, no ruleset |
| `defer` | 7 | 1 (`fmt, clippy, tests, release contract`) | `issue-ci.yml`, `claude-final-review.yml`, GKE e2e in a second file |
| `nlp` | `ci.yml` | 1 (`fmt, clippy, tests`) | one job; split into `lint`, `test-*`, `gate` |
| `reach` | none on `main` | none | the first CI; macOS jobs for the capture helper |
| `surface` | `ci.yml` | none | paused |
