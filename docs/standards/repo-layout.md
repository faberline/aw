# Repository layout

An agent should learn what a repository is, and where to act in it, from
`git ls-files` alone. Every faberline repository uses the same names for the
same things, so an agent learns one layout instead of twenty-nine.

Status: **guideline, draft, 2026-10-14**. Most rows have no checker yet, so
they are not standards. A row becomes a standard when a checker covers it; see
the [definition](README.md#what-a-standard-is). Rust package layout is
standard S3 in [rust-layout.md](rust-layout.md), not here.

The drift numbers below were measured on 2026-10-14 from the tracked files of
all 29 local clones under `~/faberlines`.

## Repository kinds

Pick exactly one kind. The kind decides which rows of the tables below apply.

| Kind | What it ships | Repositories today |
|---|---|---|
| `service` | A long-running, network-exposed process with an image and Kubernetes manifests. | beam, courier, defer, keep, loom, lumen, mesh, pgpool, relay, sift, tape |
| `cli` | A local command-line tool released as a binary. | arena, cap, cgdb, guard, jet, meter, preview, rig, vat |
| `library` | Crates or packages other repositories depend on by tag. | core |
| `python` | A `uv`-managed Python package or tool. | aw, som, workspace (until merged into aw) |
| `incubating` | Docs and backlog only. No build yet. | cube, truce |

`jev`, `mamba`, and `model-router` are mixed today. Each declares its kind in
its README `## Brief` when it adopts this guide. *Proposed:* record the kind in
`aw.toml` as `[project] kind = "<kind>"` so a checker can read it.

## Top-level files

`R` = required once a checker covers it, `D` = default, `–` = does not apply.

| Path | service | cli | library | python | incubating | Validator |
|---|---|---|---|---|---|---|
| `README.md` | R | R | R | R | R | `scripts/meta/readme_contract.py` |
| `LICENSE` | R | R | R | R | R | policy-only |
| `.gitignore` | R | R | R | R | R | policy-only |
| `CONTRIBUTING.md` | R | R | R | R | D | root meta-doc table (no checker) |
| `aw.toml` | R | R | D | R | R | `aw` engine reads it |
| `STATUS.md`, `ROADMAP.md` | D | D | D | D | – | `scripts/meta/project_docs_contract.py` when adopted |
| `Cargo.toml` + `Cargo.lock` | R | R | R | – | – | cargo |
| `clippy.toml`, `deny.toml` | R | R | R | – | – | policy-only |
| `pyproject.toml` + `uv.lock` | – | – | – | R | – | uv |
| `build.sh` | R | R | – | – | – | root build and release contract |
| `install.sh` | – | R | – | – | – | policy-only |
| `llms.txt` | D | R | – | D | – | root CLI convention (`llm` command) |
| `Dockerfile`, `Dockerfile.release` | R | – | – | – | – | release workflows |
| `k8s/` | R | – | – | – | – | release workflows |
| `AGENTS.md`, `CLAUDE.md` | D | D | D | D | D | policy-only |

Rules:

- A repository keeps no monorepo paths. `apps/<name>/`, `libs/<name>/`,
  and `../../target/` do not exist after the 2026-09 split.
- Tool configs at the root use `<tool>.toml` for the default suite and
  `<tool>-<suite>.toml` for others, for example `meter.toml` and
  `meter-keep-performance.toml`. *Policy-only.*
- Extra top-level Markdown files (`HA.md`, `BENCHMARKS.md`) move under
  `docs/`. The root holds entry points, not topics. S3 checks this for Rust
  repositories.

## Folders

| Path | Holds | Rule |
|---|---|---|
| `src/`, `crates/`, `tests/it/` | Rust code and integration tests. | Standard S3, [rust-layout.md](rust-layout.md). |
| `e2e/` | Black-box tests that run the shipped binary or CLI. | Default for `cli` and `python`. A Rust `service` repository uses `tests/it/e2e/` ([rust-tests.md](rust-tests.md)). |
| `benches/` | `cargo bench` targets. | Cargo's default name. |
| `benchmarks/` | Cross-tool runs and their recorded results. | `benchmark/` (singular) is drift. |
| `external-contracts/` | EC sources, `ec.lock`, and evidence. | Root CONTRIBUTING, "EC gates". |
| `tech-design/` | Technical design inputs the EC generator reads. | Named in `aw.toml` `td_ref`. |
| `clients/` | Generated API clients. | `clients/README.md` per the meta-doc table. |
| `docs/` | Topic documents. | Fixed names below. |
| `scripts/` | Repository automation. | No product code. |
| `.github/workflows/` | CI and release. | Names below. |
| `.claude/`, `.agents/` | Agent roles and skills the repository owns. | Rendered by `aw`, not hand-copied. |

### `docs/` names

These names are fixed because `project_docs_contract.py` finds documents by
name: `protocol.md`, `indexing.md`, `querying.md`, `gke.md`,
`client-integration.md`, and `migration-<from>-to-<to>.md`. Other topics use a
short lowercase hyphenated name.

### Workflow names

| File | When |
|---|---|
| `ci.yml` | Default for every repository with code. Runs the `## Verification` gates from `CONTRIBUTING.md` on pull requests. |
| `<repo>-release.yml` | `cli`, `service`. Promotes a tag. |
| `<repo>-release-candidate.yml` | `service`. Builds the candidate bundle. |
| `<repo>-test-image.yml` | `service`. Builds the image the GKE gate runs. |

The workflow prefix keeps names unique when a log or an issue names a
workflow without its repository.

## Dependencies between repositories

- Rust repositories depend on `core` by git tag:
  `git = "https://github.com/faberline/core", tag = "vX.Y.Z"`. They use a
  `core` crate instead of their own copy; see [core-reuse.md](core-reuse.md).
- A dependency on a sibling app pins a git revision.
- Python repositories depend on `aw` through `uv tool install git+...`.
- The release order lives in the organization README. A repository does not
  depend on a repository that releases after it.

## Measured drift

Measured 2026-10-14 against 29 repositories.

| Rule | Conforming | Not conforming |
|---|---|---|
| README passes `readme_contract.py check` | 1 (pgpool) | 28 |
| `CONTRIBUTING.md` present | 23 | aw, cgdb, jev, sift, som, truce |
| App `CONTRIBUTING.md` has the four required `##` headings | 2 (lumen, mesh) | 20: 16 have only `## Brief` and `## Verification`; core, mamba, model-router have none; pgpool has two |
| `aw.toml` present | 23 | core, cgdb, jev, model-router, som, truce |
| No monorepo paths in `aw.toml` | 27 | lumen, relay (`apps/...`) |
| `LICENSE` present | 27 | jev, model-router |
| Pull-request CI (`ci.yml`) | 1 (vat) | every other repository with code |
| Integration tests under `tests/it/` | 4 | most Rust repositories |
| One benchmark folder name | – | `benches/`, `benchmarks/`, `benchmark/` all in use |

The root CONTRIBUTING itself still names monorepo paths: 17 `apps/` and 70
`libs/` references. It needs the same cleanup when it moves into `aw`.

## Adoption order

1. Agree the open decisions below.
2. Ship a checker in `aw` that reads this guide's `R` rows, for example
   `aw repo check`. Each row it covers becomes a standard.
3. Fix the cheap `R` rows in each repository: `LICENSE`, `.gitignore`,
   `CONTRIBUTING.md`, `aw.toml`, monorepo paths.
4. Point each app `CONTRIBUTING.md` at this folder instead of `workspace`.
5. Fix READMEs repository by repository, in release order.

## Open decisions

- **CONTRIBUTING headings.** The root meta-doc table requires four headings.
  16 app files have only `## Brief` and `## Verification`. Either the table
  drops `## Authoritative Inputs` and `## Local Workflow`, or 20 files change.
- **Repository kind field.** Add `[project] kind` to `aw.toml`, or keep the
  kind in prose only.
