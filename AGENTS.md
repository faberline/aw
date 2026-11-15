# AW repository instructions

AW is the shared source for Faberline standards and agent skills.

## Output style

- Write in the user's language. Keep paths, commands and exact output contracts intact.
- Use ASD-STE100 Simplified Technical English: active voice, short sentences, one idea in each sentence.
- Explain a technical term when you first use it.
- Report what changed, what passed, what is unknown and the next action.
- Offer at most two choices and recommend one.

## Skills

- Edit shared skills only in `plugins/faberline/skills/`. Installed copies and client caches are generated output. Do not edit them.
- To install or update the plugin, follow the [plugin guide](plugins/faberline/README.md).
- Update only the Faberline marketplace. Keep repository pins and the schedules of other sessions.
- Never overwrite a dirty marketplace checkout.
- A file copy is not a live skill run. Report a validation that you did not run as unrun.
- Do not start a skill, agent or timer to prove an installation.

## Issue work

- Work on a claimed issue only through the [`aw` CLI](README.md): `aw claim`, the work, `aw report`.
  `aw` does the branch, push, pull request, merge and signal comment.
- Edit labels only in the plan step of the `aw-worker` skill.
- Use the outcome that `aw-worker` names: `split` for an issue that is too large,
  `respec` for an unclear spec in tdd, `blocked` only for a decision that a person must make.

## Cost control

The shared GKE test cluster costs the owner money. Cost control is a standing
requirement. Full rules: [cost control standard](docs/standards/cost-control.md).

- A change that starts GKE nodes states its expected cost.
- A pull request runs only the GKE suites its diff can affect. A docs-only
  pull request starts no GKE node. `main` and release candidates run all.
- Use the cheapest machine and Spot nodes. Write the reason for any larger or
  on-demand machine in `infra/gke-test/pools.yaml`.
- The minimum of every node pool is 0. A slower cold start is accepted and is
  not a reason to keep a node. A minimum above 0 needs a written reason in
  `infra/gke-test/pools.yaml` and the owner's approval.
- A job deletes its namespace at the end. Rerun only failed work.
- A new paid run, node pool, quota, bucket or cost above the cap needs the
  owner's approval first, with a number in TWD. Stop and report at 80% of the cap.
- Never weaken a test, threshold, coverage ratchet or timeout to save cost.

## Rust

- Layout: [Rust layout standard](docs/standards/rust-layout.md).
- Before a commit: `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
