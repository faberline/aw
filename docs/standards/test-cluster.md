# Shared GKE test cluster

Every faberline repository that tests on Kubernetes uses one shared GKE
cluster instead of a local kind cluster. Each repository gets its own
namespace and its own node pool. Nothing runs between tests: every pool scales
to 0 nodes.

Status: **guideline, draft, 2026-10-15**. No checker reads a repository's
overlay yet, so this is not a standard; see the
[definition](README.md#what-a-standard-is). `scripts/gke-test/run.sh` refuses
an overlay that breaks the rules in [The overlay](#the-overlay), but only at
test time. It becomes standard S5 when `aw standards check` checks the overlay.

## Who owns what

| Owner | Owns |
|---|---|
| `aw` | The cluster, every node pool and its max size, identities and permissions (`infra/gke-test/`), the shared workflow (`.github/workflows/gke-test.yml`), the scripts (`scripts/gke-test/`), and the reaper (`.github/workflows/gke-test-reaper.yml`). |
| Each repository | Its overlay `k8s/overlays/gke-test/`, its image build, and its test command. |
| GitHub Actions | Builds the image and pushes it to `ghcr.io/faberline/<repo>`. |

## The cluster

- Cluster `axiom-operator-acceptance`, zone `asia-east1-a`, project
  `axiom-502607`. The free tier covers the management fee of one zonal cluster
  only, so there is no second cluster.
- `system-pool`: one e2-standard-2 Spot node for kube-system. It has no
  autoscaling. A test run sets it to 1 node (`scripts/gke-test/wake.sh`). The
  reaper sets it to 0 when no test namespace is left
  (`scripts/gke-test/park.sh`).
- `pool-<repo>`: one pool per row of `infra/gke-test/pools.yaml`. It has 0 to
  `max_nodes` nodes, label `faberline.dev/pool=<repo>`, and taint
  `faberline.dev/pool=<repo>:NoSchedule`. The taint keeps every other pod off
  it, so repositories do not compete for CPU and memory.
- Cost cap: the sum of `max_nodes` over all rows, while tests run. Idle cost
  is 0 nodes.

## Add a repository

1. **Pool.** Open a pull request on `aw` that adds a row to
   `infra/gke-test/pools.yaml`. Pick the smallest `machine_type` on which the
   biggest pod fits. An `aw` maintainer runs `terraform apply` after merge.
   The row also creates the GCP service account `gke-test-<repo>`; without it
   the repository's workflow cannot sign in.
2. **Overlay.** Add `k8s/overlays/gke-test/kustomization.yaml`. See
   [The overlay](#the-overlay).
3. **Test command.** Add a script, for example `scripts/gke-test/verify.sh`.
   It runs from the repository root after every workload is rolled out. It
   gets `NAMESPACE`, `IMAGE`, `EVIDENCE_DIR`, `RUN_LABEL`, and `KUBECONFIG`.
   Reach services with `kubectl port-forward` or `kubectl exec`; the cluster
   has no ingress for tests. Write extra evidence into `EVIDENCE_DIR`.
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
  - `nodeSelector: {faberline.dev/pool: <repo>}`
  - the toleration `faberline.dev/pool=<repo>:NoSchedule`
  - `imagePullSecrets: [{name: ghcr-pull}]`. The run makes this secret from
    its own `GITHUB_TOKEN`, so the package can stay private.
- **Requests.** Every container and init container sets
  `resources.requests.cpu` and `resources.requests.memory`. The autoscaler
  picks the node count from requests.
- **Image tag.** No `REPLACE_ME` is left. The run sets the image with
  `kustomize edit set image <repo>=<image>`.

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
              faberline.dev/pool: keep
            tolerations:
              - key: faberline.dev/pool
                operator: Equal
                value: keep
                effect: NoSchedule
            imagePullSecrets:
              - name: ghcr-pull
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
      test-command: scripts/gke-test/verify.sh
```

- Runs of one repository wait for each other (concurrency group
  `gke-test-<owner>/<repo>`). Runs of different repositories run at the same
  time.
- Each run uploads the artifact `gke-test-evidence-<run>-<attempt>`: the
  rendered manifests, nodes, events, `describe pods`, and logs.
- A pull request from a fork gets no `id-token`, so it cannot run this.

## What a run does

1. Renders the overlay and checks it.
2. Deletes a namespace that an earlier, dead run left behind.
3. Creates the namespace with the labels `faberline.dev/test-run=<run>` and
   `faberline.dev/expires=<epoch>` (job timeout plus 15 minutes).
4. Wakes `system-pool`, creates `ghcr-pull`, applies CRDs, then applies the
   rest with server-side apply.
5. Waits for every rollout. The first pod on `pool-<repo>` waits a few minutes
   for the autoscaler to add a node.
6. Runs the test command.
7. Always: collects evidence, deletes the objects in reverse order, deletes
   the namespace, deletes the CRDs, and waits until no PersistentVolume of the
   namespace is left. A run whose cleanup fails is a failed run.

## Cleanup when a run dies

The reaper runs every 30 minutes. It deletes every test namespace whose
`faberline.dev/expires` is in the past, then parks `system-pool` when no test
namespace is left. GitHub turns off a scheduled workflow after 60 days without
activity in `aw`; turn it on again under the Actions tab of `faberline/aw`.

To clean up by hand:

```bash
gcloud container clusters get-credentials axiom-operator-acceptance --zone asia-east1-a --project axiom-502607
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
