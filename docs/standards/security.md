# Security

Standard S10. State: **candidate; `aw security check` warns, it does not fail**.
See the [definition](README.md#what-a-standard-is). The standard guards the
supply chain: the crates, the images and the CI actions that every repository
takes from other people. It exists because of the owner request of 2026-11-21:
recent incidents (the owner named nginx) show that a component we did not write
can break us. The tracking issue is [aw#680](https://github.com/faberline/aw/issues/680).
The first audit of ten repositories is in that issue.

Free tools only. A paid scanner or a paid GitHub feature needs the owner's
approval with a number in TWD.

## Command

```bash
aw security check                  # the checkout of the current directory
aw security check --dir ../relay   # another checkout
aw security check --format json    # a `security-report/1` document
aw security check --strict         # a warning fails the check (exit 1)
```

Exit codes: 0 pass (warnings do not fail), 1 a warning with `--strict`, 2 a usage
error (not a git checkout, a broken `aw.toml`).

**Warn first.** Every finding is a warning. Each repository fixes its gaps, then
the desk makes the check required: `aw security check --strict` in the `lint`
job, in the same way as COV4. Until then, no ruleset, gate or required check
changes.

## Rules

| Rule | Rule text | `aw` checks | A person checks |
|---|---|---|---|
| SEC1 | Advisories in CI | A CI step runs `cargo deny check advisories`; `deny.toml` exists; each `[advisories] ignore` has an id, a reason and a future end date | That the CI step is in the required `lint` job |
| SEC2 | Advisories on a schedule | A workflow with a `schedule:` trigger runs the check, for example by calling `advisories.yml` of `aw` | That the issue it opens gets an owner |
| SEC3 | Images pinned by digest | Every `FROM` of a Dockerfile and every `image:` of a manifest has `@sha256:` | That runtime images are minimal, run as non-root, and that CI scans the built image |
| SEC4 | Third-party components in the request path | nothing | The threat model lists each ingress, proxy and sidecar with version and update owner, or says none |
| SEC5 | GitHub Actions | Each third-party `uses:` has a 40-character commit SHA; each workflow has a top-level `permissions:`; no workflow uses `pull_request_target` | That the permissions are the least that the job needs |
| SEC6 | Kubernetes manifests | nothing | Default-deny NetworkPolicy, no host mounts, read-only root filesystem, dropped capabilities |

`aw security check` prints SEC4 and SEC6 as `manual` on every run, so nobody
forgets them. A `manual` line is never a warning.

### SEC1: advisories in CI

Every Rust repository (a tracked `Cargo.toml`) runs `cargo deny check advisories`
in CI, as a part of the `lint` stage of [CI stages](ci.md). `aw` looks in the
workflows, the composite actions and the `.sh` files. A line with `cargo deny` and
`check` counts when it names `advisories` or names no other check. `cargo audit`
and `rustsec/audit-check` count too.

An ignore in `deny.toml` needs three things: the advisory id, a reason, and an end date.
`cargo-deny` has no field for the date, so write it in the reason:

```toml
[advisories]
ignore = [
  { id = "RUSTSEC-2025-0141", reason = "no fixed release; only a build tool uses it; expires 2026-12-31" },
]
```

An ignore with a plain string, with no reason, with no `expires YYYY-MM-DD`, or
with a date in the past is a warning. When the date comes, fix the crate or write
a new reason and a new date. A person chooses; `aw` only reports.

### SEC2: new advisories without a pull request

An advisory can appear when nobody changes the repository. A scheduled run finds
it. `aw` owns the reusable workflow
[`advisories.yml`](../../.github/workflows/advisories.yml). The repository adds
the [caller](examples/advisories-caller.yml) and keeps it on a commit of `aw`.
The run happens once a day on the default branch. It costs one short job. When it
finds an advisory, it opens one issue with the title `security: new advisories on main`, or
adds a comment to the open one. When the check is clean again, it closes the issue.

### SEC3: images

Every `FROM` of a Dockerfile (`Dockerfile`, `Dockerfile.*`, `*.Dockerfile`) and every
`image:` of a YAML manifest has a digest: `name:tag@sha256:<64 hex>`. A tag can move; a digest cannot.

`aw` does not report:

- `FROM scratch` and a `FROM` of an earlier stage of the same Dockerfile.
- An image with a variable (`$IMAGE`, `{{ .Values.image }}`) or a placeholder
  (`REPLACE_ME__IMAGE`). A script writes the value later; check that script by hand.
- An image that the repository builds itself in the same run: a name that starts
  with the repository name and has no registry, a `ghcr.io/faberline/` path or
  `localhost`. Other prefixes go in `built_images` in `aw.toml`.

An image from `aw` (for example `ghcr.io/faberline/ci-rust`) is not built by the
consumer. The consumer pins its digest.

By hand: runtime images are minimal (`distroless`, `scratch` or a slim base), they run as a non-root
`USER`, and CI scans the built image for known vulnerabilities and fails on a high or critical finding with a
fix. Use a free scanner (Trivy, Grype). Do not add a paid one.

### SEC4: third-party components in the request path

The threat model of each repository has a list of the components that sit between
the user and the code and that the repository does not write: an ingress, a proxy
such as nginx, a sidecar. For each component, the list gives the version and the
person or routine that updates it. If there are none, the model says `none`.
`aw` cannot read prose; the desk checks the model when it opens the gap issues.

### SEC5: GitHub Actions

- A third-party action is pinned by a 40-character commit SHA, with the tag in
  a comment: `uses: actions/checkout@3d3c42e5... # v7.0.1`. A tag or a branch can move.
  An action of `faberline/` and a local action (`./...`) may use a tag or a branch.
  A `docker://` action needs a digest.
- Each workflow has a top-level `permissions:`. Write `permissions: {}` or
  `contents: read`, and give more to the one job that needs it.
- No workflow uses `pull_request_target`. It runs with write rights on code from a fork.
  Use `pull_request`.

### SEC6: Kubernetes

The shipped manifests have a default-deny `NetworkPolicy`, no `hostPath` mount, `readOnlyRootFilesystem: true`
and `capabilities: drop: [ALL]` for each container. No `aw` check exists. The
test manifests under `scripts/` or `tests/` may differ; the shipped ones may not.

## Exceptions

A repository opts out of one rule in `aw.toml`. An exception needs a reason and an
end date. After the date, the exception covers nothing and is a warning itself.

```toml
[[standards.security.exception]]
rule = "SEC5"
path = ".github/workflows/foo-release.yml"   # optional: a file or a folder; no path covers the rule
reason = "Needs a top-level `permissions:` first; faberline/foo#12."
expires = "2026-12-31"

[standards.security]
built_images = ["quay.io/faberline-mirror/"]   # optional: images this repository builds itself
```

An exception for a rule appears in the report as `excepted`, so it stays visible and counted.

## How a repository adopts it

1. Run `aw security check` and read the warnings.
2. Fix them, one rule for each pull request. Pin SHAs and digests first (SEC5, SEC3): they
   are mechanical. Then SEC1 and SEC2.
3. Add `aw security check --strict` to the `lint` job when no warning is left.
   A warning that cannot be fixed gets an exception with an end date.
