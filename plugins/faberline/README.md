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
| `/faberline:agy-work-issue` | One step of the AGY issue loop ([below](#agy-issue-loop)). |
| `/faberline:agy-review-issue` | Claude's side of the loop: review, merge or send back, and open new issues. |

Each skill keeps its script next to it, in `skills/<skill>/scripts/`. The skill
calls it as `${CLAUDE_SKILL_DIR}/scripts/<script>.sh`. Claude Code replaces
`${CLAUDE_SKILL_DIR}`. Each SKILL.md also says what it means, for AGY.

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

## Use it in AGY

The plugin is also an Antigravity (AGY) plugin:
[`.antigravity-plugin/plugin.json`](.antigravity-plugin/plugin.json). Install
it into AGY from a local aw checkout:

```bash
agy plugin install ~/faberlines/aw/plugins/faberline
agy plugin list
```

`agy plugin list` must show `faberline` with its skills. Then restart AGY.

AGY copies the plugin into `~/.gemini/config/plugins/faberline`. It does not
read a symlink there, and it did not pick up a `plugins.json` entry that
points at the checkout. The copy does not update: after aw changes, update
the checkout, then reinstall:

```bash
agy plugin uninstall faberline
agy plugin install ~/faberlines/aw/plugins/faberline
```

## AGY issue loop

Claude writes the issues, reviews the pull requests, merges, and releases. AGY
implements. Each repository has its own AGY session with a scheduled loop
(every 5 to 15 minutes) that runs `/faberline:agy-work-issue`. A loop works on
one issue at a time.

The issue label is the state:

| Label | Set by | Meaning |
|---|---|---|
| `agy:todo` | Claude | Ready. The loop takes the oldest one. |
| `agy:doing` | the loop | The loop is working on it, in `.agents/worktrees/issue-<N>` on branch `agy/issue-<N>`. |
| `agy:done` | the loop | The loop's turn is over. A PR (`Closes #<N>`) waits for Claude's review, or the last issue comment is a question. |

Claude's side is `/faberline:agy-review-issue`. One run takes the oldest
`agy:done` issue:

- PR: run the unit-test gate again, then the checks beyond it (e2e,
  integration, cluster, behavior), and look for bugs. Pass merges the PR
  through `/faberline:gh-merge-pr`; the merge closes the issue. Fail writes
  the review on the PR and moves the issue back to `agy:todo`. The loop
  continues on the same branch.
- Question: answer in the issue, then move it back to `agy:todo`.
- Nothing to review: plan the next work and open new `agy:todo` issues, at
  most `AGY_TODO_MAX` (default 3) open at once. Each issue has Goal,
  Acceptance, Never, and Gate (the exact unit-test commands) sections.

Create the three labels once per repository. Run this from that repository's checkout:

```bash
~/faberlines/aw/plugins/faberline/skills/agy-work-issue/scripts/issue.sh labels
```

## Check before you merge

```bash
claude plugin validate .
agy plugin validate plugins/faberline
```

Run them from the aw repository root.
