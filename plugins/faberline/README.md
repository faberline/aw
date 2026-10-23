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
| `/faberline:issue-loop` | Shows the issue loop ([below](#issue-loop)) and creates its labels. |
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

AGY reads `.antigravity-plugin/plugin.json`. To follow a local aw checkout
without a copy, list its `plugins` folder in `~/.gemini/config/plugins.json`:

```json
{ "entries": [ { "path": "~/faberlines/aw/plugins" } ] }
```

Remove an older copy in `~/.gemini/config/plugins/faberline` first, with
`agy plugin uninstall faberline`.

## Issue loop

Three steps, each in its own loop. Run each step skill once, in its own
conversation, in the repository:

```
/faberline:issue-plan
/faberline:issue-work
/faberline:issue-review
```

Each skill starts its own loop, which wakes that same conversation. It uses
the scheduler of the client: Claude Code `CronCreate`, a Codex heartbeat
automation, or the AGY `schedule` tool. A Claude Code loop stops after 7
days; run the skill again to start a new one. The three loops run side by
side. Each run does one step and ends. A run with nothing to do ends at
once.

- **plan** (`/faberline:issue-plan`), every 30 minutes: read the
  repository's goals and status, set priorities, and open new `todo` issues,
  at most `ISSUE_TODO_MAX` (default 3) at once. Each issue has Goal,
  Acceptance, Never, and Gate (the exact unit-test commands) sections.
- **work** (`/faberline:issue-work`), every 15 minutes: take the
  highest-priority `todo` issue, implement it in its own worktree, run its
  unit-test gate, and open a PR with `Closes #<N>`. One issue at a time.
- **review** (`/faberline:issue-review`), every 15 minutes: take the oldest
  `done` issue. Read the issue, the PR, its diff, and its CI checks, and
  look for bugs. Review runs no tests: the PR checks do, and each repository
  decides what its CI runs, also whether e2e goes to the shared GKE cluster.
  Its own conversation keeps it apart from the work step's reasoning. Pass
  merges the PR through `/faberline:gh-merge-pr` after the checks pass; the
  merge closes the issue. A PR without checks blocks the issue until the
  repository has CI. Fail writes the review on the PR and moves the issue
  back to `todo`.

`/faberline:issue-loop` shows the counts and the `blocked` issues.

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
