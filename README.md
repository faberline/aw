# aw

The AW workflow engine, local MCP coordinator, and Typer CLI. Managed with `uv`; requires
Python 3.13. Install it with
`uv tool install git+https://github.com/faberline/aw`; the canonical
invocation, from any repository root, is `aw <group> ...` — that exact
prefix is what the engine prints in its `next.command:` lines and what the
seven `aw-*` skills run. Inside this checkout, `uv run --project . aw` runs
the working tree.

## Capabilities

### Shared MCP coordination

Outcome: independent Codex, Claude Code, and AGY sessions use one authenticated
HTTP mailbox named `aw`. Messages and handoff cards survive service restarts.
Sessions register a logical project, a descriptive name, a scope, and a worktree.
Each session can initiate work. Native agent type is optional delivery metadata.
Preflight is required before registration or scope changes.
The stdio proxy exposes two tools before admission and all 12 after admission.
It binds calls to one session and keeps its credential private.
A thin stdio proxy connects to the same service.
The user-level installer handles Codex TOML and Claude Code/AGY JSON.
It keeps other settings, backs up existing files, and supports repeat installation.

Native receivers forward events through Claude Code Channels, an existing
Codex App Server, or an AGY desktop sidecar. Forwarding and agent acknowledgment
are separate states. Worktree write claims are advisory and require owner release.

Gate: `uv run --project . --directory . pytest e2e/test_mcp.py e2e/test_mcp_install.py`.
This gate exercises real HTTP and stdio transports. Native Codex and AGY endpoints
are protocol fixtures. Live model sessions still need integration verification.

Install client entries with `aw mcp install --client all`.
Add global session guidance with `aw mcp install-rules`.
Check both with `aw mcp doctor --rules`.
Start the service with `aw mcp serve`. See [MCP setup](docs/mcp.md) for credentials,
native receivers, and client configuration.

### CLI entry point

Outcome: `uv run --project . aw` with no arguments prints usage and
exits non-zero; `uv run --project . aw version` prints the project
version and exits zero.

Gate: `uv run --project . --directory . pytest e2e/` (run from
the repository root).

### Workflow command groups

Outcome: nine groups delegate to the engine modules under
`src/aw/scripts/`, one module per group, with argparse staying the single
source of argument validation — `change`, `milestone`, `e2e`, `impl`,
`maint`, `wis`, `meta`, `metadoc`, and `release-plan`. Every group, verb, and
option that the Typer surface accepts rebuilds an argv that the engine
module's own parser accepts.

Gate: `uv run --project . --directory . pytest e2e/` (run from
the repository root). `e2e/test_cli.py` measures the delegation with
`_delegate` stubbed; the other half of the printed protocol — that every
`next.command:` line the engine prints parses in the engine's argparse — is
`.claude/aw/verification/check_next_command.py`'s claim.

## Layout

- `src/aw/main.py` — the typer surface; `aw.main:app` is the `aw` console
  script. Each subcommand rebuilds the argv its engine module already
  parses and hands it to that module's `main(argv)`.
- `src/aw/scripts/` — the engine: the argparse scripts the `aw-*` skills
  drive (`change.py`, `milestone.py`, `e2e.py`, `impl.py`, `maint.py`,
  `wis.py`, `meta.py`, `metadoc.py`, `release_plan.py`, and their shared
  modules). Moved here from `.claude/aw/scripts/` on 2026-09-02; the
  verification suite stayed at `.claude/aw/verification/` and resolves this
  path through its `_paths.SCRIPTS`.
- `e2e/` — black-box CLI cases, run with pytest via the gate above.

### Release plans

The explicit-only legacy `grill-release` skill reuses an existing plan and its approved decisions.
Read-only preparation and validation work in any runtime mode. A validated
plan with an approved digest goes directly to Apply in Default mode.

`aw release-plan validate --plan <path|->` reads and canonicalizes one closed
`release-plan-v1` JSON document. It accepts an unsealed draft, adds
`plan_sha256`, and prints one sealed canonical plan. The digest covers the
canonical plan with `plan_sha256` omitted. Validation does not write files or
contact the tracker. Its output can be saved directly as a later `apply` file.

`apply` needs a file, one `apps/name` or `libs/name` project, and that exact
approved digest. Each project carries exact document bytes plus a complete
tracker baseline summary and digest. A release plan uses
`{{milestone_number}}` in an indexed promise heading and in that promise's
exact repository Milestone Tracking link. It uses `{{development_order}}` in
the Milestone description. The facade replaces both tokens after GitHub
assigns the real numbers. Every planned issue also carries its approved `p0`
to `p5` priority.

The project list is an execution chain. A later project can apply only after
all earlier project receipts are complete. Put the first project to apply at
the start of the list. For a new Milestone, the planning skill uses
`milestone next-version` and its normal minor bump. Only an initial release or
an explicit human exception selects another version.

Before any mutation, `apply` checks the repository, project order, Git commit,
clean working tree, document hashes, tracker summary, Milestone identity,
issue type, owner label, and order. It renders the approved documents in a
disposable clone and runs both META checks there. A new Milestone uses two
different preview numbers, so a hard-coded number cannot pass by matching one
probe. The preview also requires each bound promise to carry the exact
Milestone Tracking link in its owner field. It then creates the durable
receipt at `.aw/release-plans/<digest>/<project>.json`.

The receipt records the META commit, Milestone number, each issue number, and
the final reconciliation evidence. `resume --receipt <path>` recovers only an
exact accepted write. Zero or multiple matches stop. A complete receipt is
read again and fails if Git or tracker state drifted. Resume also refuses any
working-tree change outside exact planned META bytes. The final gap evidence
contains all G1 through G7 rows. G1 through G5 must be measured and clear.
G6 and G7 may remain as recorded delivery work for the planned issues.

## Development

```
uv run --project . aw release-plan --help
uv run --project . --directory . pytest e2e/
```
