# SaaS service skeleton

`lumen`, `tape`, `defer`, `relay`, and `keep` are the same kind of product. Each
one is a SaaS service: an HTTP API with auth, a raft cluster that replicates
the data, its own data on disk, and a Kubernetes operator that runs it. They use
the same `core` crates (`service-auth`, `service-http`, `raft-runtime`,
`service-backup`, `openapi-codegen`, `cli-std`).

So they use one skeleton. The packages have the same names and the same jobs in
every repository. Only the product part differs.

Status: **candidate standard S5, 2026-10-22**. The checker is
`aw standards saas-service check`. It reports only: no repository gates CI on
it yet. It becomes active when the five repositories pass or have an exception
for each finding. See the [definition](README.md#what-a-standard-is).

This standard adds to [S3 Rust layout](rust-layout.md). It uses the
[one package per context](rust-layout.md#one-package-per-context) shape.

## Scope

A repository is in scope when its `aw.toml` says so:

```toml
[project]
name = "tape"
kind = "saas-service"
```

The checker takes the package prefix `<repo>` from `[project] name`.

## Packages

Every package is directly under `crates/`. The root `Cargo.toml` is a virtual
workspace.

| Package | Required | Job | Must depend on |
|---|---|---|---|
| `<repo>` | yes | the binary; assembly only. It builds the HTTP router from the `interfaces` layers. | |
| `<repo>-shared-kernel` | yes | small types that every context may use | |
| `<repo>-access` | yes | who may do what; wraps `service-auth` | `service-auth` |
| `<repo>-replication` | yes | the raft cluster; wraps `raft-runtime` | `raft-runtime` |
| `<repo>-storage` | yes | the data on disk: the log, snapshots, segments, blobs | |
| `<repo>-operator` | yes | the Kubernetes operator: CRD, reconcile, render. It goes away later, see [Moving operations into core](#moving-operations-into-core). | `kube` |
| `<repo>-client` | no | the generated client that other repositories use | |
| `<repo>-bench` | no | the benchmark driver | |
| `<repo>-<context>` | 1 to 3 | the product: what makes this service different | |

Rules:

- **No package per technology.** There is no `-http`, `-core`, `-engine`,
  `-api`, or `-common` package. HTTP handlers live in the `interfaces` layer of
  the context that owns them. Domain types live in the `domain` layer of their
  context, or in the shared kernel when every context needs them.
- **Every package except the assembly is one context.** `access`,
  `replication`, `storage`, `operator`, and each product context have one
  `[[contexts]]` entry in `ddd.toml`. Its layer paths are inside its own
  package, for example `crates/tape-storage/src`.
- **At most three product contexts.** More than three means the product is
  two products, or the contexts are too small.

## Other folders

| Path | What it holds |
|---|---|
| `k8s/base/` | the server: StatefulSet, Service, config |
| `k8s/operator/` | the operator: Deployment, RBAC, CRD |
| `k8s/overlays/{dev,staging,prod,template,gke-test}/` | one overlay per place it runs. `gke-test` is the shared test cluster, see [test-cluster.md](test-cluster.md). |
| `clients/codegen.toml` | the OpenAPI client generation input for `openapi-codegen` |

## Checks

| ID | Check |
|---|---|
| SV0 | Every exception names a known check and a reason. |
| SV1 | `aw.toml` declares `[project] kind = "saas-service"`. |
| SV2 | The root `Cargo.toml` is a virtual workspace. Every member is `crates/<package>/`, and the folder name equals the package name. |
| SV3 | The six required packages exist. |
| SV4 | Every other package is `-client`, `-bench`, or a context in `ddd.toml`. There are at most three product contexts. |
| SV5 | `-access` depends on `service-auth`, `-replication` on `raft-runtime`, and `-operator` on `kube`. |
| SV6 | Each context package has one `ddd.toml` context, and its layer paths are inside the package. |
| SV7 | `k8s/base`, `k8s/operator`, and the five overlays exist. |
| SV8 | `clients/codegen.toml` exists. |

Run it:

```bash
aw standards saas-service check --repo ../tape --repo ../defer
```

Add `--format json` for a machine-readable report. Exit code 0 is a pass, 1
is at least one finding, and 2 is a usage error.

## Exceptions

A repository opts out of one check in its own `aw.toml`. The reason is
required. `path` is optional and limits the exception to one finding path.

```toml
[[standards.saas_service.exception]]
check = "SV8"
reason = "no public API until the 0.3 release"
path = "clients/codegen.toml"
```

## Where each repository is (2026-10-23)

The checker ran on each repository's `origin/main`, then on the branch of
its S5 pull request. Each pull request passed. All of them merged on
2026-10-23.

| Repository | Findings before S5 | Pull request | Exception left |
|---|---|---|---|
| `tape` | SV1 | faberline/tape#29 adds `kind`. | none |
| `defer` | SV1, SV3 (`defer-storage`), SV8 | faberline/defer#29 adds `kind`, `defer-storage`, and `clients/`. | none |
| `relay` | SV1, SV2, SV3 (all six) | faberline/relay#57 splits the package into the six crates. | none |
| `lumen` | SV1, SV2, SV3 (all six) | faberline/lumen#159 splits the package into `lumen`, `lumen-shared-kernel`, `lumen-access`, `lumen-operator`, and `lumen-index`. faberline/lumen#160 adds `lumen-replication`. faberline/lumen#161 adds `lumen-storage`. Its rust-arch exceptions rely on the checker fix in faberline/aw#66. | none |
| `keep` | SV1, SV3, SV4, SV6, SV7 | faberline/keep#7 replaces `keep-core`, `keep-engine`, and `keep-http` with contexts and adds `ddd.toml`. | SV7: the `gke-test` overlay lands in faberline/keep#6. |

## Moving operations into core

Core [ADR 0002](https://github.com/faberline/core/blob/main/docs/adr/0002-core-owned-service-operations.md)
moves operations into `core`. An app keeps its product logic. `core` decides
how the service runs: topology, scaling, membership, resharding, routing,
rollout, certificates, backup, placement, and monitoring.

This changes the skeleton. The checker does not change yet.

### Target shape

- There is no `<repo>-operator` package. Each app still runs its own operator
  process, but that process runs the engine from `core`. The CRD kind does not
  change.
- The assembly `<repo>` has `src/profile.rs`. It implements the
  `ServiceProfile` of `core`: the kind, the env prefix, the product config, the
  capabilities, and the alert rules.
- The assembly also implements the data-plane hooks: request classification,
  the merge of scattered results, bucket data, and domain readiness.
- No app package depends on `kube` or `k8s-openapi` directly.
- The app's `k8s` command generates `k8s/base/` and `k8s/operator/`. The
  repository commits the output, and a drift test compares the two.

### When the checker changes

The checker changes when the first repository removes its `-operator`
package. The ADR plans this for `tape`, in phase 3. That change does these
things:

- `-operator` is no longer a required package (`REQUIRED` in
  `saas_service.py`), and SV5 no longer checks it. While repositories move,
  `-operator` is a transition slot. It is not one of the three product
  contexts of SV4.
- New check SV9: no app package depends on `kube` or `k8s-openapi` directly.
- New check SV10: `crates/<repo>/src/profile.rs` exists.

Each repository that has not moved then adds an `aw.toml` exception for each
new finding. The reason names the ADR phase in which the repository moves.

The checker does not change now because the engine does not exist yet. No
repository can pass SV9 or SV10 today. The new checks would stop S5 from
becoming active.

[Core reuse](core-reuse.md) changes when the engine and the `shard-routing`
crate exist, in phases 1 and 4 of the ADR.
