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

## Rust

- Layout: [Rust layout standard](docs/standards/rust-layout.md).
- Before a commit: `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
