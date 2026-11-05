# AW repository instructions

AW is the shared source for Faberline standards and agent skills.
Codex, Claude Code and Antigravity use the same skill logic from this repository.
Client-specific files control discovery and installation only.

## Output style

Use the repository's ELI5 style for ordinary user updates and answers.
Use ASD-STE100 Simplified Technical English and approved plain words.
Use active voice and short sentences and paragraphs.
Put one instruction or idea in each sentence.
Explain a technical term when it is needed.
Use the user's language and keep paths, commands and exact output contracts intact.
Report what changed, what passed, what remains unknown and the next action.
Offer at most two choices and recommend one.
These presentation rules never weaken implementation or acceptance requirements.

## One shared skill source

- Edit shared skills in `plugins/faberline/skills/`.
- Keep one implementation for all three clients.
- Put shared behavior in the skill or its existing support module.
- Do not maintain separate skill logic in client caches or personal directories.
- Installed copies are generated outputs from a recorded AW commit.
- Use native marketplace or plugin updates when they support the required source.
- Keep deliberate repository pins until a separately authorized cutover.
- Do not replace a pinned plugin or change another session's schedule during a skill update.
- Never overwrite a dirty marketplace checkout to refresh an installed copy.
- Preserve unrelated worktrees, WIP, rules, credentials and plugin settings.

The marketplace catalog is `.claude-plugin/marketplace.json`.
The plugin root is `plugins/faberline/`.
Claude's manifest is `.claude-plugin/plugin.json` inside that root.
Antigravity's manifest is `.antigravity-plugin/plugin.json` inside that root.
Use the client's supported loader; the manifest alone does not prove activation.

## Install and update

Read the current [plugin guide](plugins/faberline/README.md).
Check the actual client version, enabled plugin, source and installation scope.
Do not assume that all clients use the same command or cache location.

| Client | Targeted update or verification |
|---|---|
| Codex | `codex plugin marketplace upgrade faberline`; read back the installed plugin and source. |
| Claude Code | `claude plugin marketplace update faberline`, then update the intended `faberline@faberline` installation with its explicit scope. Preserve repository-specific pins. |
| Antigravity | Use its supported GitHub/local plugin loader. Verify with `agy plugin list --json` and `agy plugin validate --strict <plugin-directory>` when the CLI is available. |

Do not refresh every marketplace when only Faberline needs an update.
If native update is unavailable, an authorized file sync must record its source commit.
Back up the installed files before replacement and check for concurrent changes.
Verify file hashes, executable modes, manifests and relative support-file links.
Keep the backup and report the exact changed paths.
Mark unavailable native validation or activation checks as unrun.
Never report file installation as a completed live skill run.
Do not start a skill, agent or timer just to prove installation.

## Issue work

Agents work on issues only through the [`aw` CLI](README.md).
One run is `aw claim`, the work the issue asks for, then `aw report`.
`aw` does every git and GitHub change for the stage: branch, push,
pull request, merge, review and the signal comment.
Do not edit labels; the planner owns them.
Do not merge, push or comment outside `aw` for a claimed issue.
If the issue is too large or unclear, report `blocked` with the reason.
The planner splits it.

## Rust

Follow the [Rust layout standard](docs/standards/rust-layout.md).
Before a commit, run `cargo fmt --all -- --check`,
`cargo clippy --all-targets -- -D warnings` and `cargo test`.
