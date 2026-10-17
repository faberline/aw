# Core reuse

`faberline/core` has a special role. It is the shared Rust library of the
organization. Every Rust repository uses a `core` crate when one does the job.
A repository does not write its own copy.

Status: **active standard S4, 2026-10-15**. The checker is
`aw standards check`. It is on `aw` main. See the
[definition](README.md#what-a-standard-is).

## Rules

1. **Use core first.** Before you write shared-looking code, look in
   [`core/crates/`](https://github.com/faberline/core/tree/main/crates). If a
   crate does the job, depend on it.
2. **Do not copy core.** A local copy of what a `core` crate does is a defect.
   Fix it by moving to the crate.
3. **Pin by tag.** Depend on `core` by git tag:
   `{ git = "https://github.com/faberline/core", tag = "vX.Y.Z" }`. Never use a
   path, a branch, or a bare revision.
4. **One tag per repository, declared once.** A virtual workspace names each
   `core` crate once in `[workspace.dependencies]`. Members use
   `<crate>.workspace = true`. See [rust-layout.md](rust-layout.md).
5. **One tag across the organization.** Every repository pins the current
   `core` release. An older pin needs an exception with a reason.
6. **Grow core, not copies.** When two repositories need the same thing and
   `core` does not have it, add a crate to `core`. Do not copy it from one
   repository to the other.
7. **Core depends on no app.** `core` never depends on another faberline
   repository.

## Which crate does what

| Need | Crate |
|---|---|
| `llm`, `upgrade`, and `issue` commands for a CLI | `cli-std` |
| Git SHA, build time, and target stamped in `build.rs` | `build-stamp` (as a build dependency) |
| Bind, drain, readiness, shutdown | `server-lifecycle` |
| TCP accept loop and connection budget | `server-tcp` |
| HTTP/1.1 and h2c serving | `server-http` |
| Probe routes, `{error, message}` envelope, readiness wiring | `service-http` |
| Request authentication middleware | `service-auth` |
| Signed claim-check tokens | `claim-token` |
| Logs, OTLP traces, metrics, process sampling | `service-observability` |
| Prometheus counters, gauges, latency, text encoder | `metrics-prometheus` |
| Prometheus Remote Write | `metrics-remote-write` |
| OTLP HTTP and gRPC transport | `transport-otlp` |
| HTTP/2 cleartext client and pooling | `transport-h2c` |
| Peer mTLS material | `peer-tls` |
| Raft consensus | `raft-core`, driven by `raft-runtime` |
| Kubernetes operator, CRDs, leader election, resize | `service-k8s` |
| Backup schema, sinks, runner | `service-backup` |
| fsync, atomic replace, CRC logs, snapshots | `storage-durable` |
| Local and cloud object store | `storage-object` |
| Immutable segments and manifests | `storage-segment` |
| Text index base | `index-text` |
| Bounded async execution | `service-executor` |
| Checkpointed collectors | `service-collector` |
| Projections, catch-up, rebuild | `service-projection` |
| MCP stdio and streamable HTTP transport | `service-mcp` |
| Typed API clients from OpenAPI | `openapi-codegen` |
| Code navigation and analysis | `compass` |
| UI element model and component runtime | `surface`, `ui-runtime` |

A service that runs Raft gets its replica mode from
`raft_runtime::cluster::replica_mode()` and its peers from
`ClusterTopology::from_env`. An operator renders through
`service_k8s::ClusterSpec`, `ManagedService`, and the render helpers.

## Checker

Run it from any folder:

```bash
aw standards check --core ~/faberlines/core --repo ~/faberlines/keep
```

`--repo` repeats. `--core` defaults to a `core` folder next to the first
repository. `--format json` prints a `core-reuse-report/1` document. The exit
code is 0 for pass, 1 for a finding, and 2 for a usage error.

The checker reads only tracked `Cargo.toml` and `build.rs` files. It skips
`vendor/`, `fixtures/`, `testdata/`, and `target/`. The current release is the
highest `vX.Y.Z` tag in the `core` checkout.

| Check | Fails when |
|---|---|
| CR0 | an exception has no known check or no reason |
| CR1 | a `core` dependency uses `branch`, `rev`, no tag, or a path outside the repository |
| CR2 | a repository pins more than one `core` tag |
| CR3 | a repository pins a tag other than the current `core` release |
| CR4 | a workspace member names the `core` git source instead of `<crate>.workspace = true` |
| CR5 | a repository builds a binary but does not depend on `cli-std` |
| CR6 | a `build.rs` prints `cargo:rustc-env=*GIT_SHA` but its package has no `build-stamp` build dependency |
| CR7 | a manifest names `prometheus`, `openraft`, or `raft` directly |
| CR8 | `core` itself depends on another faberline repository |

### Exceptions

A repository opts out of one check in its own `aw.toml`. The reason is
required. `path` is optional and limits the exception to one file.

```toml
[[standards.core_reuse.exception]]
check = "CR3"
reason = "pinned until the index migration lands"
path = "Cargo.toml"
```

An excepted finding is still printed and counted. It does not fail the run.
`aw.toml` holds the exceptions because `aw` owns this standard. `ddd.toml`
stays owned by the S3 checker.

## Measured state, 2026-10-14

`aw standards check` on 25 local checkouts: 8 pass, 17 fail.

| Check | Repositories that fail |
|---|---|
| Pass | core, courier, defer, mesh, pgpool, relay, sift, tape |
| CR3 old tag | lumen (`v0.4.13`) |
| CR4 member names the git source | jet, mamba, meter, vat |
| CR5 binary without `cli-std` | arena, cgdb, guard, jev, model-router, preview, rig, som |
| CR6 hand-written build stamp | beam, cap, jet, keep, loom, mamba, vat |
| CR7 direct `prometheus` | mamba (`crates/queuekit`) |

The cheapest fixes are CR6 and CR4. Seven repositories carry the same
`build.rs` that `build-stamp` already ships. CR4 is a one-line change per
member.
