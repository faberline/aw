# Shared GKE test cluster

Every faberline repository that tests on Kubernetes uses one shared GKE
cluster instead of a local kind cluster. Each repository gets its own
namespace and its own node pools. Nothing runs between tests: every pool scales
to 0 nodes.

Status: **guideline, draft, 2026-10-15**. No checker reads a repository's
overlay yet, so this is not a standard; see the
[definition](README.md#what-a-standard-is). `scripts/gke-test/run.sh` refuses
an overlay or a test Job that breaks the rules in [The overlay](#the-overlay)
or [The test Job](#the-test-job), but only at test time. It becomes standard S5 when `aw standards check` checks the overlay.

## Who owns what

| Owner | Owns |
|---|---|
| `aw` | The cluster, every node pool and its max size, identities and permissions (`infra/gke-test/`), the shared workflow (`.github/workflows/gke-test.yml`), the scripts (`scripts/gke-test/`), the parker (`infra/gke-test/parker.tf`), and the reaper (`.github/workflows/gke-test-reaper.yml`). |
| Each repository | Its overlay `k8s/overlays/gke-test/`, its test Job, its image build, and its test command if it has one. |
| GitHub Actions | Builds the image and pushes it to `ghcr.io/faberline/<repo>`. |

## What runs where

| Test | Where | Why |
|---|---|---|
| Unit and integration tests | Your machine, or the GitHub runner (`cargo test` and the like) | They need no Kubernetes. |
| End-to-end (e2e) | This cluster: the server as a cluster, then a test Job | It tests the real deploy: StatefulSet, PVC, Service DNS, restarts. |
| Performance | This cluster, on a non-Spot pool | A Spot node can stop in the middle, and the numbers need a quiet node. |

This table says where each kind can run. Which tests run on a pull request
or on a merge to `main` is set by what changed, in
[test-scopes.md](test-scopes.md). Performance tests run only here, never on
your machine.

An e2e run has two parts:

1. **The server.** The overlay deploys it the way a real cluster does: a
   StatefulSet with one replica on each node of the repository's server pool.
2. **The test client.** A Kubernetes Job on a second pool of the same
   repository, for example `pool-lumen-client`. It reaches the server through
   the Service DNS name. It does not use `kubectl port-forward`: a
   port-forward goes through the API server and the runner, so it is slow,
   it drops when a pod restarts, and it measures the network to GitHub, not
   the server.

The GitHub runner only starts things and waits. It applies the overlay, waits
for the rollout, applies the Job, waits for the Job, collects the logs, and
tears down.

## The cluster

- Cluster `faberline-test`, zone `asia-east1-a`, project
  `axiom-502607`. The free tier covers the management fee of one zonal cluster
  only, so there is no second cluster.
- `system-pool`: one e2-standard-2 Spot node for kube-system. It has no
  autoscaling. A test run sets it to 1 node (`scripts/gke-test/wake.sh`).
  The parker sets it to 0 when no test namespace is left; see
  [The parker](#the-parker). Teardown leaves it up, so the next call (for
  example the next test suite) does not wait for a new system node. Idle cost:
  one node for 5 to 15 minutes after the last run.
- `pool-<pool>`: one pool per row of `infra/gke-test/pools.yaml`. Its machine
  type is e2-standard-2 or bigger. It has 0 to
  `max_nodes` nodes, label `faberline.dev/pool=<pool>`, and taint
  `faberline.dev/pool=<pool>:NoSchedule`. The taint keeps every other pod off
  it, so repositories do not compete for CPU and memory.
- A repository can have more than one pool, one per role. The pool name is
  `<repo>-<role>`, for example `lumen-server` for the server and
  `lumen-client` for the test Job. Terraform refuses another name.
- Cost cap: the sum of `max_nodes` over all rows, while tests run. Idle cost
  is 0 nodes.

## Add a repository

1. **Pools.** Open a pull request on `aw` that adds rows to
   `infra/gke-test/pools.yaml`: usually `<repo>-server` for the server and
   `<repo>-client` for the test Job. Pick the smallest `machine_type` on
   which the biggest pod fits, and at least e2-standard-2: a shared-core type
   (e2-micro, e2-small, e2-medium) leaves too little CPU after GKE's own
   DaemonSets, and Terraform refuses it. Set `max_nodes` of a pool to at least
   the number of pods on it, because each pod gets its own node.
   For performance tests, use `spot: false` and a machine series with one CPU
   generation, for example `n4-highmem-4`. An E2 node can have one of several
   Intel or AMD generations, so E2 numbers do not compare from run to run.
   An `aw` maintainer runs `terraform apply` after merge. Until then a run
   stops at its checks with "the cluster has no node pool", or with "pools.yaml
   says <type>" when the row changed the machine type. The first row of a
   repository also creates the GCP service account `gke-test-<repo>`; without
   it the repository's workflow cannot sign in.
2. **Overlay.** Add `k8s/overlays/gke-test/kustomization.yaml`. See
   [The overlay](#the-overlay).
3. **Test Job.** Add a kustomize folder with one Job, for example
   `k8s/gke-test-job/`. See [The test Job](#the-test-job).
   A test command (a script on the runner) is still possible, for short
   checks after the Job. It runs from the repository root, gets `NAMESPACE`,
   `IMAGE`, `EVIDENCE_DIR`, `RUN_LABEL`, and `KUBECONFIG`, and must not
   send test load through `kubectl port-forward`.
4. **Workflow.** Add `.github/workflows/gke-test.yml`. See
   [The workflow](#the-workflow).

## The overlay

The overlay is `k8s/overlays/gke-test/`, built on `../../base`. `run.sh`
renders it with `kustomize build` and refuses it before it touches the
cluster when one of these is false:

- **Namespace.** Every `Namespace` object is the repository name, or the
  workflow's `namespace` input.
- **Placement.** Every pod template (Pod, Deployment, StatefulSet, DaemonSet,
  ReplicaSet, Job, CronJob) has:
  - `nodeSelector: {faberline.dev/pool: <pool>}`, where `pools.yaml` gives
    `<pool>` to this repository, and the cluster has that pool
  - the toleration `faberline.dev/pool=<pool>:NoSchedule`
  - `imagePullSecrets: [{name: ghcr-pull}]`. The run makes this secret from
    its own `GITHUB_TOKEN`, so the package can stay private.
- **Requests.** Every container and init container sets
  `resources.requests.cpu` and `resources.requests.memory`. The autoscaler
  uses them to check that the pod fits on a new node.
- **One replica per node.** A StatefulSet or Deployment with more than 1
  replica has `topologySpreadConstraints` with `topologyKey:
  kubernetes.io/hostname`, `maxSkew: 1`, and `whenUnsatisfiable:
  DoNotSchedule` (a required pod anti-affinity on the hostname also passes).
  Its replicas are not more than `max_nodes` of its pool. This makes the test
  cluster fail the way a real one does: one node down takes one replica.
- **Storage on N4 and C4.** These series take only Hyperdisk. On a pool of
  type `n4-*`, `n4a-*`, `n4d-*`, `c4-*`, `c4a-*`, or `c4d-*`, every
  `volumeClaimTemplates` entry, and every rendered PVC that a pod mounts, sets
  `storageClassName: dynamic-rwo`. GKE's `dynamic-rwo` makes a Hyperdisk
  Balanced disk on these nodes and a PD Balanced disk on other nodes, so it is
  safe to set before the pool changes. The default `standard-rwo` makes a PD
  disk that an N4 node cannot attach, and the pod stays Pending. Terraform
  gives these pools Hyperdisk boot disks on its own.
- **Image tag.** No `REPLACE_ME` is left. The run sets the image with
  `kustomize edit set image <repo>=<image>`.

### One pod per node, requests without limits

Each pod on a repository pool gets a node of its own: each server replica,
each other server-side pod (for example a database), and the test Job pod.
The pod can then use the whole node, and a test measures the code, not a
neighbour. The pod anti-affinity keeps other pods off the node, not the
requests: do not make requests big to fill the node. The pool taint keeps
other repositories off. Do this for every pod template of the overlay and of
the test Job:

- **No shared node.** Add a required pod anti-affinity on the hostname that
  matches every pod of the namespace. An empty `labelSelector` matches every
  pod; without `namespaceSelector` it looks only in the pod's own namespace:

  ```yaml
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - topologyKey: kubernetes.io/hostname
          labelSelector: {}
  ```

  It also passes the "one replica per node" check.
- **Requests, no limits.** Set small `resources.requests`: what the pod
  needs to start. The check above needs them. Set no `resources.limits`: a
  limit caps the pod below the node size.
  When the base sets limits, the overlay removes them, for example with a
  JSON 6902 patch `op: remove`, `path:
  /spec/template/spec/containers/0/resources/limits`.
- **Enough nodes.** `max_nodes` of the pool is at least the number of pods on
  it. Otherwise a pod stays Pending and the run fails at its deadline.

`run.sh` does not check these three rules yet. Follow them anyway.

Example, from `keep`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [../../base]
images:
  - name: keep
    newName: ghcr.io/faberline/keep
    newTag: REPLACE_ME__TAG
patches:
  - target:
      kind: StatefulSet
      name: keep
    patch: |-
      apiVersion: apps/v1
      kind: StatefulSet
      metadata:
        name: keep
      spec:
        template:
          spec:
            nodeSelector:
              faberline.dev/pool: keep-server
            tolerations:
              - key: faberline.dev/pool
                operator: Equal
                value: keep-server
                effect: NoSchedule
            imagePullSecrets:
              - name: ghcr-pull
            affinity:
              podAntiAffinity:
                requiredDuringSchedulingIgnoredDuringExecution:
                  - topologyKey: kubernetes.io/hostname
                    labelSelector: {}
  # keep's base sets limits; remove them so the pod can use the whole node.
  - target:
      kind: StatefulSet
      name: keep
    patch: |-
      - op: remove
        path: /spec/template/spec/containers/0/resources/limits
```

**Pods that an operator creates.** `run.sh` sees only the rendered
manifests. When an operator creates pods at run time, the operator must put
the same nodeSelector, toleration, and pull secret on them, for example from a
field of the custom resource. A pod without them stays Pending, because every
other pool has a taint and `system-pool` has no room.

**Cluster-scoped objects.** CRDs, ClusterRoles, and ClusterRoleBindings are
shared by the whole cluster. Give them names that start with the repository
name or its API group, so two repositories never write the same object. The
run applies CRDs first and deletes them last.

**Cluster RBAC made at run time.** `run.sh` deletes only the objects in the
rendered manifests. When an operator creates a ClusterRole or a
ClusterRoleBinding for one namespace, it must put the label
`faberline.dev/owner-namespace=<namespace>` on it. Teardown deletes the objects
with that label after it deletes the namespace. The reaper deletes them when
their namespace no longer exists. An object without the label stays on the
cluster after the run.

## The test Job

The workflow input `test-job` names a kustomize folder, for example
`k8s/gke-test-job/`. The run sets its namespace and its image, renders it,
and checks it before it touches the cluster:

- It renders exactly one Job.
- It has only these kinds: Job, ConfigMap, Secret, ServiceAccount, Role,
  RoleBinding. They are all in the namespace, so they go away with it.
- Its pod template follows [Placement](#the-overlay) and
  [Requests](#the-overlay), on a pool of this repository, normally
  `<repo>-client`.
- It follows [One pod per node](#one-pod-per-node-requests-without-limits):
  requests without limits, and a node of its own.

After every rollout is done, the run applies the folder and waits until the
Job has the condition `Complete` (pass) or `Failed` (fail). The wait ends
15 minutes before `ttl-minutes`, so the run can still collect evidence and
tear down. The Job's logs go into the run log and into the evidence.

Write the Job so that:

- `backoffLimit: 0`. A retry hides a flaky failure.
- It reaches the server by the Service name, for example `http://keep:7117`.
- It prints its results to stdout. The pod logs are the evidence.
- It gets a ServiceAccount and a Role only for what it does, for example
  `delete` on pods to test a restart.
- Its image has the test tools. For a Rust repository, it is the test client
  image, and the Job runs the repository's test binary with one suite filter
  ([rust-tests.md](rust-tests.md)). Other repositories can use their own
  image, a client image, or a public tool image such as `alpine/k8s`.

**Client image.** The run sets `<image-name>=<image>` in the test Job too.
For a separate client image, also set the inputs `test-image` and
`test-image-name`. The run then sets `<test-image-name>=<test-image>` in the
test Job only, so the server keeps the image under test. The two names must
differ. A client image that a run of the same repository pushes to
`ghcr.io/faberline/<repo>-<name>` can be read with `ghcr-pull`.

**Values for one call.** The input `test-env` takes lines of `KEY=VALUE`, for
example a cell name, a commit, or an expected image digest. The run puts them
in the ConfigMap `gke-test-env` in the test Job. It makes the ConfigMap also
when `test-env` is empty, so the Job can always read it with `envFrom`. The
values go into the evidence: never put a secret there.

Example, from `keep` (`k8s/gke-test-job/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: keep
resources:
  - rbac.yaml   # ServiceAccount, Role (pods: get, list, watch, delete), RoleBinding
  - job.yaml    # Job keep-test on pool keep-client, runs /scripts/verify.sh
configMapGenerator:
  - name: keep-test-script
    files:
      - verify.sh
```

**Test suites.** Split the tests into suites: one folder with one Job per
suite, for example `k8s/gke-test-job/smoke/` and
`k8s/gke-test-job/failover/`. Each suite is one call of this workflow, from a
matrix with `max-parallel: 1`, so the calls run one at a time. Each call
deploys a new server, so a suite never sees the state of another suite. To
test one part only, run the workflow by hand and pick one suite. A pull
request runs the suites that its changes select
([test-scopes.md](test-scopes.md)); the merge queue runs all of them. `fail-fast: false` runs the other suites when one
fails.

```yaml
on:
  pull_request:
  workflow_dispatch:
    inputs:
      suite:
        type: choice
        options: [all, smoke, failover]
        default: all

jobs:
  gke-test:
    needs: image
    strategy:
      max-parallel: 1
      fail-fast: false
      matrix:
        # A pull request has no inputs, so it runs all suites.
        suite: ${{ fromJSON((inputs.suite || 'all') == 'all' && '["smoke","failover"]' || format('["{0}"]', inputs.suite)) }}
    uses: faberline/aw/.github/workflows/gke-test.yml@main
    with:
      image: ${{ needs.image.outputs.image }}
      test-job: k8s/gke-test-job/${{ matrix.suite }}
      evidence-suffix: ${{ matrix.suite }}
```

Every call pays for its rollout. A node that the autoscaler added stays
about 10 minutes after the call, so the next suite usually reuses it.

**Long tests.** A GitHub-hosted job stops after 6 hours, so `ttl-minutes`
is at most 360. Split a longer test into more than one call, for example one
call per cell of a performance matrix. Calls of one repository run one after
the other. In a matrix, set `strategy.max-parallel: 1`: a concurrency group
keeps only one waiting run and cancels the others.
Give each call its own `evidence-suffix`, for example the cell name. All
calls of a matrix share one workflow run, and two artifacts with the same name
fail the upload.

```yaml
  perf:
    needs: [image, client-image]
    strategy:
      max-parallel: 1
      matrix:
        cell: [a1, a2]
    uses: faberline/aw/.github/workflows/gke-test.yml@main
    with:
      image: ${{ needs.image.outputs.image }}
      overlay: k8s/overlays/gke-perf
      test-job: k8s/gke-perf-job
      test-image: ${{ needs.client-image.outputs.image }}
      test-image-name: <repo>-perf-client
      test-env: |
        CELL=${{ matrix.cell }}
        COMMIT=${{ github.sha }}
      evidence-suffix: ${{ matrix.cell }}
      ttl-minutes: 150
```

## The workflow

```yaml
name: gke-test
on:
  pull_request:
  workflow_dispatch:
jobs:
  image:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    outputs:
      image: ${{ steps.push.outputs.image }}
    steps:
      # Build and push ghcr.io/faberline/<repo>:sha-<git>, then
      # echo "image=ghcr.io/faberline/<repo>:sha-<git>@<digest>" >>"$GITHUB_OUTPUT"
  gke-test:
    needs: image
    permissions: { id-token: write, contents: read, packages: read }
    uses: faberline/aw/.github/workflows/gke-test.yml@main
    with:
      image: ${{ needs.image.outputs.image }}
      test-job: k8s/gke-test-job
```

- Runs of one repository wait for each other (concurrency group
  `gke-test-<owner>/<repo>`). Runs of different repositories run at the same
  time.
- Each run uploads the artifact `gke-test-evidence-<run>-<attempt>`, plus
  `-<evidence-suffix>` when set: the rendered manifests, nodes, events,
  `describe pods`, and logs.
- A pull request from a fork gets no `id-token`, so it cannot run this.

## What a run does

1. Renders the overlay and the test Job and checks them. It also checks
   that the cluster has every pool they select.
2. Deletes a namespace that an earlier, dead run left behind.
3. Creates the namespace with the labels `faberline.dev/test-run=<run>` and
   `faberline.dev/expires=<epoch>` (job timeout plus 15 minutes).
4. Wakes `system-pool`, creates `ghcr-pull`, applies CRDs, then applies the
   rest with server-side apply.
5. Waits for every rollout. The first pod on a pool waits a few minutes for
   the autoscaler to add a node.
6. Applies the test Job and waits until it is `Complete` or `Failed`. A
   `Complete` Job whose log shows `running 0 tests` fails the run: its filter
   matched no test.
7. Runs the test command, if there is one.
8. Always: collects evidence, deletes the objects in reverse order, deletes
   the namespace, deletes the cluster RBAC labelled
   `faberline.dev/owner-namespace=<namespace>`, deletes the CRDs, and waits
   until no PersistentVolume of the namespace is left. A run whose cleanup
   fails is a failed run. It does not park `system-pool`; the parker does.

## The parker

Cloud Scheduler starts the Cloud Workflows workflow `gke-test-park`
(`infra/gke-test/park.yaml`) every 10 minutes. Both run in GCP, outside the
cluster, so they run when the cluster has no node. The workflow:

1. Stops when `system-pool` has no node.
2. Stops when a namespace carries `faberline.dev/test-run`.
3. Waits 5 minutes and checks the namespaces again. The suites of one
   workflow run follow each other with a short gap; the wait keeps the parker
   from parking in that gap.
4. Sets `system-pool` to 0 nodes. When another operation runs on the
   cluster, it stops, and the next run tries again.

A run can start while a park runs. Its node is still Ready while GKE deletes
it. So `wake.sh` always resizes `system-pool` to 1: GKE refuses the resize
until the park ends, `resize_pool` retries, and then the node comes back.

See its executions with:

```bash
gcloud workflows executions list gke-test-park --location asia-east1 --project axiom-502607 --limit 10
```

The reaper also parks (step 2 below). It is the second net, because GitHub
runs its schedule hours late.

## Cleanup when a run dies

The reaper (`scripts/gke-test/reap.sh`) runs from a GitHub schedule. The
schedule says every 30 minutes; GitHub runs it every 2.5 to 5 hours. It:

1. Deletes every test namespace whose `faberline.dev/expires` is in the past.
2. Parks `system-pool` when no test namespace is left.
3. Deletes orphan disks: `pvc-*` disks that GKE made for a PersistentVolume,
   that no VM uses, that are older than 1 hour, and that no PersistentVolume
   names.
4. Reports pods outside test and system namespaces. It does not delete them,
   because they can be someone's manual work.
5. Deletes ClusterRoles and ClusterRoleBindings labelled
   `faberline.dev/owner-namespace=<namespace>` when that namespace no longer
   exists.

When step 1, 3, or 5 fails, or step 4 finds a pod, the run fails and opens one
issue titled "gke-test reaper needs attention" on `faberline/aw`. The next
clean run closes it.

GitHub runs a schedule only from the default branch, so the reaper runs only
after this workflow is on `main`. GitHub also turns off a scheduled workflow
after 60 days without activity in `aw`; turn it on again under the Actions tab
of `faberline/aw`.

## Budget alert

Terraform creates a monthly budget on the project (`google_billing_budget.test`,
`monthly_budget` in TWD, default 2000, about US$62, a little over normal test use). Billing emails the billing
admins at 50%, 90%, and 100% of actual spend, and at 100% of forecast spend.
The alert only reports; it does not stop anything.

To clean up by hand:

```bash
gcloud container clusters get-credentials faberline-test --zone asia-east1-a --project axiom-502607
```

```bash
kubectl get namespaces -l faberline.dev/test-run
```

```bash
kubectl delete namespace <repo> --wait=true
```

## Permissions

Each repository signs in as `gke-test-<repo>` through the Workload Identity
pool `faberline-github`. The service account may get the cluster and resize
`system-pool`. In Kubernetes it is `cluster-admin`, because a run creates
CRDs, ClusterRoles, and ClusterRoleBindings, and Kubernetes allows that only
with `bind` and `escalate`, which equal `cluster-admin`. This is a test cluster
with no real data, and every repository belongs to the same owner, so
repositories are not isolated from each other beyond namespaces and pools.
