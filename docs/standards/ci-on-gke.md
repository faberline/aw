# CI on GKE

GitHub Actions is the control plane. The real CI work runs to completion as
Kubernetes Jobs in the [shared GKE test cluster](test-cluster.md).

Status: **draft, 2026-11-14**. Nothing in this file is in use yet. No
workflow, script or infra file follows it. It is not a standard and has no
checker; see the [definition](README.md#what-a-standard-is). Design issue:
[aw#390](https://github.com/faberline/aw/issues/390).

## Why

- The `faberline` organization is on GitHub Free. It has 20 concurrent
  GitHub Actions jobs for all repositories together. One pull request with
  many jobs makes the runs of other repositories wait
  ([aw#372](https://github.com/faberline/aw/issues/372)).
- Each repository keeps its own copy of its CI
  ([aw#284](https://github.com/faberline/aw/issues/284)).

The fix: a pull request holds few GitHub jobs, and the work fans out in the
cluster, where `aw` sets the size and the cost.

## Terms

- **Stage.** One of `lint`, `test`, `e2e`, `gate`, as in [ci.md](ci.md).
- **Stage job.** The one GitHub job of a stage.
- **Work Job.** One Kubernetes `Job` that does one unit of work, for example
  `cargo clippy`, one `cargo test` group, one image build or one e2e suite.
- **Batch.** All work Jobs of one stage of one workflow run.
- **Manifest.** The file in the repository that lists the work Jobs of each
  stage.
- **Batch runner.** The `aw` script that the stage job runs. It applies a
  batch, waits and collects the results.

## One GitHub job per stage

Every repository that adopts this file has exactly these jobs in `ci.yml`:

| Order | Job id | Name | Content |
|---|---|---|---|
| 1 | `lint` | `lint` | The batch of stage `lint`. |
| 2 | `test-gke` | `test` | The batch of stage `test`. Image builds for e2e go here too. |
| 3 | `e2e-gke` | `e2e` | The batch of stage `e2e`. Optional. |
| 4 | `gate` | `gate` | Unchanged from [ci.md](ci.md#the-workflow). |

- The stages run in order with `needs:`, so one pull request holds about one
  of the 20 slots at a time.
- The job ids keep the S7 prefixes (`test-*`, `e2e-*`), so the `needs:`
  order and `gate` stay valid under `aw ci check`.
- Each stage job calls the reusable workflow `ci-stage.yml` in `aw` with the
  stage name. A repository does not copy the steps.

### What the stage job may do

1. Check out the manifest only (a sparse checkout of one file).
2. Sign in to GKE through Workload Identity Federation as `gke-test-<repo>`.
3. Wake the cluster (`scripts/gke-test/wake.sh`).
4. Create the namespace of the batch and the source Secret (see
   [Source and token](#source-and-token)).
5. Apply the batch.
6. Wait until every work Job of the batch has finished or timed out.
7. Collect logs and results (see [Results and logs](#results-and-logs)).
8. Delete the namespace, also when the run fails or is cancelled.

### What the stage job may not do

- Compile, lint or run tests.
- Build or push an image.
- Install a toolchain, `cargo`, `docker` or language runtime.
- Read secrets other than its own `GITHUB_TOKEN` and its GKE identity.

A stage job that needs more than `kubectl`, `gcloud`, `jq` and the `aw`
scripts is doing work that belongs in a work Job.

## The manifest

One file in each repository: `.github/ci/jobs.yaml`. `aw` reads only this
file and the reusable workflow. The test content stays with the repository.

```yaml
# Work Jobs of each CI stage. Format: aw docs/standards/ci-on-gke.md
version: 1
image: ghcr.io/faberline/ci-rust:1.99.1   # default image of every Job
jobs:
  - name: fmt-clippy                       # unique in the file; [a-z0-9-], at most 40 characters
    stage: lint
    command: |
      cargo fmt --all -- --check
      cargo clippy --workspace --all-targets --locked -- -D warnings
    resources: { cpu: "4", memory: 8Gi }
    timeout: 20m

  - name: lib-all-features
    stage: test
    command: cargo test --workspace --all-features --locked
    resources: { cpu: "4", memory: 12Gi }
    timeout: 40m

  - name: image-server
    stage: test
    kind: image                            # see "Image builds"
    context: .
    dockerfile: Dockerfile
    push: ghcr.io/faberline/<repo>
    timeout: 30m

  - name: operator-recovery
    stage: e2e
    kind: suite                            # an existing gke-test overlay and test Job
    overlay: k8s/overlays/gke-test
    test-job: k8s/gke-test-job
    image-from: image-server
    timeout: 45m
```

Fields:

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The work Job name. The Kubernetes Job is `<stage>-<name>`. |
| `stage` | yes | `lint`, `test` or `e2e`. |
| `kind` | no | `command` (default), `image` or `suite`. |
| `command` | for `command` | Bash, run from the repository root with `set -euo pipefail`. |
| `image` | no | Overrides the default image. Pinned by tag or digest, never `latest`. |
| `pool` | no | The node pool. Default `aw-ci`. Only `aw-ci` and the repository's own pools are allowed. |
| `resources` | no | `cpu` and `memory` requests. No limits, as in [test-cluster.md](test-cluster.md#one-pod-per-node-requests-without-limits). Default `cpu: "2"`, `memory: 6Gi`. A request must fit one node of the pool. |
| `timeout` | yes | `activeDeadlineSeconds` of the Job, as `<n>m`. About three times the normal run time, as in [ci.md](ci.md#queue). |
| `paths` | no | A [test-scopes.md](test-scopes.md#the-map) selector. The Job is skipped when no changed path matches. A push to `main` ignores it. |
| `env` | no | A map of plain environment values. Never a secret. |

Rules:

- The stage job's `timeout-minutes` is the largest `timeout` of the stage
  plus 20 minutes for the queue and the node scale-up.
- A stage with no Job after selection passes at once.
- A manifest that breaks a rule fails the stage before anything is applied.

## Source and token

The work Job gets the source of the exact commit of the run. It does not get
any other credential of the repository.

1. The stage job creates the namespace `ci-<repo>-<run_id>-<attempt>-<stage>`.
2. It creates the Secret `ci-source` in that namespace with the repository
   name, the commit SHA and the run's `GITHUB_TOKEN`. The token has
   `contents: read` only (plus `packages: write` in a stage that has an
   `image` Job).
3. An init container of each work Job fetches that one commit
   (`git fetch --depth 1 origin <sha>`) into an `emptyDir` volume. The main
   container runs in that folder.
4. The token stops working when the stage job ends. The namespace and the
   Secret are deleted at the end of the stage, and the reaper deletes them
   after `faberline.dev/expires` if the runner dies.

### Security rules

- **Private repositories only.** A public repository can get pull requests
  from forks, and their code would run in the cluster. `kit` is public and
  stays on GitHub-hosted runners.
- Only `pull_request` from the same repository, `push` to `main` and
  `workflow_dispatch` start a batch. A pull request from a fork never does.
- **A work Job has no cluster rights.** It runs as the namespace's
  `default` ServiceAccount with `automountServiceAccountToken: false`, no
  Role and no RoleBinding. The `cluster-admin` identity of the repository
  stays with the stage job. A `suite` Job keeps the rights that
  [test-cluster.md](test-cluster.md#the-test-job) gives it today.
- A work Job runs as a non-root user, with `allowPrivilegeEscalation: false`,
  except an `image` Job (see [Image builds](#image-builds)).
- Egress from a work Job is limited by a `NetworkPolicy` to GitHub,
  `crates.io` and its CDN, `ghcr.io`, and Cloud Storage. The cluster already
  has FQDN network policy on.
- No secret goes into `env`, the manifest, a log or an artifact.

## Cache

Without a shared cache every work Job compiles from zero, and CI gets slower
than today. Two caches:

1. **sccache on Cloud Storage.** One bucket for all repositories (name chosen
   in phase 1). Each work Job sets `RUSTC_WRAPPER=sccache`,
   `SCCACHE_GCS_BUCKET`, `SCCACHE_GCS_KEY_PREFIX=<repo>/<toolchain>` and
   `CARGO_INCREMENTAL=0`. A Kubernetes ServiceAccount of the `aw-ci` pool maps
   to one Google service account with object read and write on the bucket.
   Objects older than 14 days are deleted by a lifecycle rule.
2. **Cargo registry cache.** A `tar.zst` of `~/.cargo/registry/index` and
   `~/.cargo/registry/cache`, stored in the same bucket under
   `<repo>/cargo-registry/<sha256 of Cargo.lock>`. A Job restores it when it
   exists and fetches from `crates.io` when it does not. Only a push to
   `main` writes it.

All writers are repositories of the same owner, so a pull request may write
to sccache. Each repository has its own prefix.

## Results and logs

For each work Job the stage job collects the pod log, the exit code, the
start and end time, and the node pool.

- **Log.** One folded group per work Job in the GitHub log:
  `::group::<stage>/<name>: <result> (<minutes> min)`, the log,
  `::endgroup::`. Failed Jobs come first.
- **Artifact.** `ci-<stage>-logs`: one `<name>.log` per work Job and
  `results.json` with one row per Job (`name`, `result`, `exit_code`,
  `started_at`, `finished_at`, `pool`, `retries`). Kept 14 days.
- **Job summary.** One table: Job, result, time, pool. The pull request page
  shows only the 4 stage jobs, so this table is where a reader starts.
- **Result.** The stage job fails when any work Job failed, timed out or was
  not scheduled before its timeout. A skipped Job (by `paths`) does not fail
  it.
- **Spot preemption.** A pod that GKE stops for a Spot preemption does not
  count as a failure. The Job's `podFailurePolicy` ignores the
  `DisruptionTarget` condition, so Kubernetes starts the pod again, at most
  twice. A test failure is never retried.

### Re-run

- Version 1: "Re-run failed jobs" in GitHub re-runs the whole stage job, and
  that applies every work Job of the stage again. The cache makes the
  passed Jobs fast.
- Version 2 (open point): the batch runner writes each result to the bucket
  under `<repo>/results/<sha>/<stage>/<name>` with a hash of the manifest
  entry. On attempt 2 or later it skips a Job whose result for the same
  commit and the same entry hash is `success`.

## Image builds

An `image` Job builds and pushes the image inside the cluster. The runner
does not run `docker`.

- The builder is rootless BuildKit in a work Job on `aw-ci`. It needs
  `seccomp` and `apparmor` set to `Unconfined`; it does not need a privileged
  pod.
- It pushes to `ghcr.io/faberline/<repo>` with the run's token
  (`packages: write`), tag `sha-<git12>`, and uses the registry as its
  layer cache (`--cache-to type=registry,mode=max`).
- It writes the image digest to its result. A `suite` Job names it with
  `image-from`, and the e2e stage deploys `<image>@<digest>`.
- An image Job sits in stage `test`, so the image is ready when `e2e` starts
  and no extra GitHub job is needed. This replaces the `e2e-image` helper job
  of [ci.md](ci.md#the-stages).

## How it fits the other standards

### ci.md (S7)

- The stages, their order, the `gate` job and its checks, and the ruleset
  (only `gate` required) do not change.
- CI3, CI5, CI6, CI7, CI10 and CI11 hold with the job ids above.
- CI4 (the two fixed `lint` commands in `ci.yml`) does not hold, because the
  commands move to the manifest. Until `aw ci check` reads the manifest, a
  repository that adopts this file sets an exception for CI4 in `aw.toml`.
- Concurrency: a new push still cancels the older run on a pull request. A
  cancelled stage job deletes its namespace in an `always()` step, and the
  reaper is the second net.

### test-cluster.md

- **Pool.** A new pool `aw-ci` (Spot, label `faberline.dev/pool=aw-ci`, taint
  `faberline.dev/pool=aw-ci:NoSchedule`, 0 to `max_nodes` nodes). `aw` owns
  it; the name keeps the `<repo>-<role>` rule. Machine type and `max_nodes`
  need the user's approval (aw#390).
- **Namespace labels.** A CI namespace carries `faberline.dev/test-run`,
  `faberline.dev/expires` (start plus stage timeout plus 15 minutes) and
  `faberline.dev/ci-stage=<stage>`. So the parker does not park
  `system-pool` while a batch runs, and the reaper deletes a dead batch.
- **Lease.** Stages `lint` and `test` do not take the repository's Lease:
  they create only namespaced objects in their own namespace. Stage `e2e`
  takes the Lease (or a lane Lease) as `run.sh` does today, because suites
  share CRDs and the repository namespace.
- **Quota.** Every node of `aw-ci` counts against `CPUS_ALL_REGIONS` with the
  test pools. See the numbers on aw#390.

### test-scopes.md

- Scope 2 (branch): the batch runner reads the changed paths and the `paths`
  of each Job. This replaces the `test-select` helper job.
- Scope 3 (full): a push to `main` runs every Job.
- A selected Job that runs zero tests still fails, as before.

### rust-tests.md

The four kinds of test and the test client image do not change. Only the
place where they run changes.

## Open points

- **core kind e2e.** core starts a kind cluster on the runner. In a work Job
  this needs nested containers. Options: run those suites in a namespace of
  the shared cluster (a `suite` Job), or keep them on a GitHub-hosted runner
  as an extra `e2e-*` job.
- **macOS jobs.** A Job that needs macOS (for example a `reach` helper) stays
  on a GitHub-hosted runner as its own job in the right stage.
- **Re-run of only the failed Jobs.** See [Re-run](#re-run), version 2.
- **Checker.** `aw ci check` needs rules for the manifest and the 4-job
  shape, and a way to drop the CI4 exception.
- **GitHub minutes.** The stage job still uses runner minutes while it waits.
  This file saves concurrent slots, not minutes.
- **Build speed without warm cache.** Not measured. The phase 2 pilot
  (`som`, then `core`) measures it.
