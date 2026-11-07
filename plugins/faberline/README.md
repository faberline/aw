# faberline plugin

Shared Claude Code skills for faberline repositories. aw is the marketplace
(the catalog in [`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json)).
This directory is its one plugin, `faberline`.

Change a skill here, once. Every repository that enables the plugin gets the
change. Do not copy these skills into a repository.

## Skills

| Skill | What it does |
|---|---|
| `/faberline:git-commit` | Reads the diff and makes one conventional commit. |
| `/faberline:git-rebase` | Rebases the branch on `origin/main` and resolves conflicts. |
| `/faberline:git-push` | Pushes the branch. Refuses a force push to a persistent ref. |
| `/faberline:gh-create-pr` | Opens a pull request, or reuses the open one. |
| `/faberline:gh-merge-pr` | Waits for checks, then squash-merges and verifies. |
| `/faberline:git-land` | Runs all of the above in order. |
| `/faberline:aw-worker` | Runs one scheduled `aw` issue cycle as this client's agent. |

Each skill keeps its script next to it, in `skills/<skill>/scripts/`. The skill
calls it as `${CLAUDE_SKILL_DIR}/scripts/<script>.sh`. Claude Code replaces
`${CLAUDE_SKILL_DIR}` with the skill's directory.

## Enable it in a repository

Run this once in the repository, from your shell:

```bash
claude plugin marketplace add faberline/aw --scope project
```

Then add the plugin to `enabledPlugins` in the same `.claude/settings.json`,
and commit the file. The result:

```json
{
  "extraKnownMarketplaces": {
    "faberline": {
      "source": { "source": "github", "repo": "faberline/aw" }
    }
  },
  "enabledPlugins": {
    "faberline@faberline": true
  }
}
```

A session that trusts the repository folder registers the marketplace and asks
to install the plugin.

To install it only for yourself, in a session:

```
/plugin install faberline --marketplace faberline/aw
```

## Get changes

The plugin has no `version`, so a new commit on `main` is a new version. Run
one of these to get it:

```bash
claude plugin marketplace update faberline
```

Or turn on auto-update: `/plugin` > **Marketplaces** > `faberline` >
**Enable auto-update**.

## Codex and AGY

The same skills load in Codex and AGY. These clients do not set
`${CLAUDE_SKILL_DIR}`, so each skill tells the model to use the absolute
path of its own folder instead.

Codex reads the same marketplace. Run once:

```bash
codex plugin marketplace add faberline/aw
codex plugin add faberline@faberline
```

Codex keeps a copy. Get changes with `codex plugin marketplace upgrade`.

Antigravity reads `.antigravity-plugin/plugin.json`. Its plugin loader can
install a GitHub source or a local plugin directory. For a local AW checkout:

```bash
agy plugin install /absolute/path/to/aw/plugins/faberline
agy plugin list --json
agy plugin validate --strict /absolute/path/to/installed/faberline
```

Use the path that `list` reports for the final command. Verify the installed
revision and enabled state. Check the installed client's help before using
an update command. Do not assume it accepts a custom marketplace name.
The [Antigravity plugin guide](https://antigravity.google/docs/plugins)
documents its supported loaders and validation.

Antigravity also loads global plugin directories from
`~/.gemini/config/plugins/`. If its CLI is unavailable, an authorized file
sync can update that installed directory from a verified AW commit.
Keep a full backup, refuse concurrent file changes, and verify file hashes,
script permissions and references. Do not overwrite a dirty source checkout.
Report native CLI validation and runtime activation as unrun when unavailable.

All installed copies remain outputs of the same AW source. They are not
independent skill implementations. See the repository's
[AGENTS.md](https://github.com/faberline/aw/blob/main/AGENTS.md) for update,
ownership and acceptance rules.

## Issue work

Issue work runs through the `aw` CLI in the
[repository root](../../README.md): `aw claim`, the work, then `aw report`.

`aw-worker` runs these cycles for an hourly automation. One agent,
`claude` (Claude Code), does every stage: triage, spec, tdd and review.
Each run plans first, then claims and reports until it must wait for CI or
has finished three stages. `aw report` moves the `stage:` label, so the next
claim can continue at once. In a cloud session, tdd uses the session's
checkout; on a computer it uses `~/faberlines/.aw-workers/claude/<repo>`.
In tdd a Haiku subagent writes the tests and code from the spec, and the
run's own model checks the result. So each spec follows a checklist: exact
files and names, steps, tests with expected values, commands and traps.

The worker uses the repositories with the GitHub topic `aw`. Add a
repository to the queue once:

```bash
gh repo edit faberline/<repo> --add-topic aw
```

On a computer, the worker clones a repository on its first tdd run.

The skill uses the Rust `aw` at `~/.local/share/aw-rs/bin/aw`:

```bash
cargo install --git https://github.com/faberline/aw --root ~/.local/share/aw-rs aw
```

## Check before you merge

```bash
claude plugin validate .
```

Run them from the aw repository root.
