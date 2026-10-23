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
| `/faberline:issue-loop` | Runs the issue loop ([below](#issue-loop)): plan, work, review, again. |
| `/faberline:issue-plan` | Opens issues and sets their priority. |
| `/faberline:issue-work` | Takes the next issue, implements it, and opens a PR. |
| `/faberline:issue-review` | Tests the PR, then merges it or sends it back. |

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

## Issue loop

Three steps make one cycle. Run `/faberline:issue-loop` once by hand in the
repository's Claude Code session. It starts a self-paced `/loop` of itself.
Each iteration runs one step, in this order:

1. **review** (`/faberline:issue-review`), when an issue is `done`: run the
   unit-test gate again, then the checks beyond it (build, lint, e2e,
   integration, behavior), and look for bugs. It runs in a fresh subagent, so
   it does not share the work step's reasoning. Pass merges the PR through
   `/faberline:gh-merge-pr`; the merge closes the issue. Fail writes the
   review on the PR and moves the issue back to `todo`.
2. **work** (`/faberline:issue-work`), when an issue is `todo` or `doing`:
   take the highest-priority one, implement it in its own worktree, run its
   unit-test gate, and open a PR with `Closes #<N>`.
3. **plan** (`/faberline:issue-plan`), when nothing else is open: read the
   repository's goals and status, set priorities, and open new `todo`
   issues, at most `ISSUE_TODO_MAX` (default 3) at once. Each issue has Goal,
   Acceptance, Never, and Gate (the exact unit-test commands) sections.

The loop wakes again after 60 seconds when a step changed an issue, and
after 30 minutes when there is nothing to do.

The issue label is the state:

| Label | Set by | Meaning |
|---|---|---|
| `todo` | plan, review | Ready. Work takes the highest priority, then the oldest. |
| `doing` | work | Work is on it, in `.claude/worktrees/issue-<N>` on branch `issue/<N>`. |
| `done` | work | A PR (`Closes #<N>`) waits for review. |
| `blocked` | any step | A human must decide. Answer in the issue, then move it back to `todo`. |

The priority labels are `priority:p0` (do now) to `priority:p3` (some day).
An issue without one counts as `priority:p2`.

Create the labels once per repository. This also renames the old
`agy:todo`, `agy:doing`, and `agy:done` labels, so their issues keep their
state. Run it from that repository's checkout:

```bash
~/faberlines/aw/plugins/faberline/skills/issue-loop/scripts/loop.sh labels
```

## Check before you merge

```bash
claude plugin validate .
```

Run them from the aw repository root.
