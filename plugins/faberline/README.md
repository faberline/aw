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

Each skill keeps its script next to it, in `skills/<skill>/scripts/`. The skill
calls it as `${CLAUDE_SKILL_DIR}/scripts/<script>.sh`.

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

## Check before you merge

```bash
claude plugin validate .
```

Run it from the aw repository root.
