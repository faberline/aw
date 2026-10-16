# Rust layout and layers

Standard S3. State: **candidate**. The checker is
`scripts/meta/rust_arch_contract.py` in `faberline/workspace` PR #37. Each
repository configures it with a `ddd.toml` at its root. `core` follows every
rule today; its [architecture page](https://github.com/faberline/core/blob/main/docs/architecture.md)
is the worked example.

## Vocabulary

- **Package** (often called a crate): one `Cargo.toml` with a `[package]`
  table. It builds one library, one or more binaries, or both.
- **Workspace**: a root `Cargo.toml` with a `[workspace]` table. It builds
  several packages together with one `Cargo.lock` and one `target/`.
- **Virtual workspace**: a workspace whose root `Cargo.toml` has no
  `[package]`. The root only lists members and shared settings.

## Repository shape

Pick one of two shapes.

### One package

Use this when the repository ships one package.

```text
<repo>/
├── Cargo.toml          [package] only, no [workspace]
├── Cargo.lock
├── ddd.toml
├── clippy.toml
├── deny.toml
├── docs/             README.md, architecture.md, glossary.md,
│                       domain/, adr/, operations/
├── src/
│   ├── lib.rs          only `mod` and `pub use`
│   ├── main.rs         thin: parse arguments, call the library
│   └── <layer>/<concept>/...
└── tests/
    └── it/
        └── main.rs     one integration binary
```

A single package wrapped in `[workspace] members = ["."]` is an error.

### Several packages

Use this when the repository ships more than one package.

```text
<repo>/
├── Cargo.toml          virtual: [workspace], [workspace.package],
│                       [workspace.dependencies]; no [package]
├── Cargo.lock
├── ddd.toml
├── clippy.toml
├── deny.toml
├── docs/               same folders as the one-package shape
└── crates/
    ├── <repo>/          the main binary; wires the others together
    ├── <repo>-core/     domain types and rules
    ├── <repo>-http/     the HTTP surface
    ├── <repo>-operator/ the Kubernetes operator
    └── <repo>-client/   the generated client other repositories use
```

Rules:

- Every member is directly under `crates/`. The folder name equals the
  package name.
- The root `[workspace.dependencies]` names every shared version once,
  including each `core` crate with its git tag. Members use
  `<dep>.workspace = true`.
- Split a package out only when it has its own consumer or its own build
  reason: another repository depends on it (`-client`), it deploys separately
  (`-operator`), or it is a separate binary (`-cli`). Do not split by layer
  alone. Layers live inside a package.

`keep` and `tape` already use this shape.

## Inside a package

Every package takes one of four forms. `ddd.toml` records the form.

| Form | `src/` layout | Use it for |
|---|---|---|
| Layered | `src/{domain,application,infrastructure,interfaces}/<concept>/` | a package with its own rules and use cases |
| Domain only | `src/domain/<concept>/` | pure logic with no I/O |
| Whole-src adapter | concept folders directly under `src/` | a package that wraps one technology, such as a file system or a wire format |
| Shared kernel | all of `src/` | one small package that every context may use |

The four layers, from the inside out:

| Layer | Holds | May use |
|---|---|---|
| `domain` | types and rules; no clock, no file system, no network | itself and an allowlist (`serde`, `thiserror`, `chrono`, and a few more) |
| `application` | use cases that combine domain rules | `domain` |
| `infrastructure` | adapters for disks, networks, clocks, and other services | `domain` |
| `interfaces` | entry points: HTTP handlers, CLI commands, `llm` topics | `application` only |

Other rules the checker applies:

- `src/lib.rs` contains only `mod` declarations and `pub use` re-exports.
  Assembly code may also live in `src/app/` (the composition root) and public
  API modules in `src/api/`. Neither belongs to a layer.
- No `mod.rs` files. Set `mod_module_files = "deny"` in clippy.
- A file in `src/` above 400 lines is a warning. Above 1000 lines is an error.
- Integration tests live in `tests/it/`, as one binary. e2e and performance
  tests are in the same binary; see [rust-tests.md](rust-tests.md).
- Another context uses a layered package only through its `application`
  layer.
- The root holds only these Markdown files: README, CONTRIBUTING, LICENSE,
  CHANGELOG, SECURITY, CODE_OF_CONDUCT, CLAUDE, AGENTS, and for now STATUS and
  ROADMAP. Other topics go under `docs/`.
- Every `ddd.toml` copies the shared `[policy]` table exactly. The canonical
  copy is `rust_arch/policy.toml` next to the checker.

## Exceptions

A rule break that is not fixed yet goes into `ddd.toml` as an exception with
a reason. While a repository migrates, `[migration] enforce = false` makes
the checker report findings without failing. Set it to `true` when the
migration ends.

## Measured state, 2026-10-14

| Shape today | Repositories | Under this standard |
|---|---|---|
| One package, no workspace | lumen, pgpool, sift | correct |
| Virtual workspace under `crates/` | core, defer, jev, keep, mamba, tape, vat | correct shape; folder names still to check |
| One package wrapped in `[workspace] members = ["."]` | beam, cap, courier, guard, loom, mesh, preview, relay | error: remove the `[workspace]` table, or move shared versions into the package |
| Root package plus extra members | arena, cgdb, jet, meter, rig | reported: move to a virtual workspace |

`ddd.toml` exists in 8 repositories. It enforces in core, mamba,
model-router, pgpool, sift, and tape. It only reports in defer and lumen.
