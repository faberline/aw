# Faberline standards

This folder holds the shared standards for every repository in the
[faberline](https://github.com/faberline) organization. `aw` owns them.
App repositories link here. They do not copy these files.

Status: **draft, 2026-10-14**.

## What a standard is

A rule is a **standard** only when all four of these are true:

1. **Scope.** It applies to every repository of one kind, for example every
   Rust repository. It is not one repository's own choice.
2. **Owner.** `aw` owns it. A change lands in `aw` first.
3. **Checker.** A tool checks it and gives a pass or a fail. A rule with no
   checker is not a standard.
4. **Exception path.** A repository can opt out of one rule. It writes the
   reason in a file that the checker reads, so the opt-out is visible and
   counted.

A rule that fails any of the four is one of these instead:

- **Guideline.** A good default with no checker. Guidelines live in this
  folder and say so at the top. A guideline becomes a standard when its
  checker ships.
- **Product decision.** A choice that belongs to one repository, for example
  which `core` tag it pins. It stays in that repository.

## When a rule can become a standard

Promote a guideline only when all three are true:

- At least two repositories repeat the same thing.
- One shape saves agents or people real work, such as fewer files to open or
  one command to learn.
- Different shapes cause real failures or waste today.

## Standards

| ID | Standard | Applies to | Checker | Exception file | State |
|---|---|---|---|---|---|
| S1 | README contract | every repository | `scripts/meta/readme_contract.py check` | none yet | active; 1 of 29 repositories passes |
| S2 | Project document set (README, STATUS, ROADMAP, and the fixed `docs/` guides) | repositories that adopt STATUS and ROADMAP | `scripts/meta/project_docs_contract.py` | none yet | active; 7 repositories adopted |
| S3 | [Rust layout and layers](rust-layout.md) | every Rust repository | `rust_arch_contract.py` with the repository's `ddd.toml` | `ddd.toml` exception entries | candidate: the checker is in `faberline/workspace` PR #37, not merged |
| S4 | [Core reuse](core-reuse.md) | every Rust repository, and `core` itself | `aw standards check` | `[[standards.core_reuse.exception]]` in `aw.toml` | candidate: 8 of 25 pass; the checker is on this branch, not on `aw` main |

A candidate becomes active when its checker is on `aw` main.

## Guidelines

| Document | What it covers |
|---|---|
| [repo-layout.md](repo-layout.md) | Top-level files and folders by repository kind. Each row becomes a standard when a checker covers it. |
| [rust-tests.md](rust-tests.md) | Every test of a Rust repository is Rust: unit, integration, e2e, and performance. e2e is a test binary that talks to the server over the network. Becomes part of S3 when its checker ships. |
| [test-scopes.md](test-scopes.md) | Which tests run when, by what changed: this change on your machine before push, the branch against `main` in the cloud, and everything in the cloud on every merge to `main`. Performance runs only in the cloud, and the whole performance matrix only on a release candidate. The path-to-test map in `aw.toml`, with feature sets. Becomes a standard when `aw tests select` and its map checks ship. |
| [test-cluster.md](test-cluster.md) | Testing on the shared GKE cluster instead of kind: the `gke-test` overlay, the node pool row, and the workflow. Becomes S5 when `aw standards check` reads the overlay. |

The authoring, service, CLI, meta-doc, and release conventions are still in
the root [CONTRIBUTING.md](https://github.com/faberline/workspace/blob/main/CONTRIBUTING.md)
in `workspace`. Most of them have no checker, so under this definition they
are guidelines. They move here when `workspace` merges into `aw`.
