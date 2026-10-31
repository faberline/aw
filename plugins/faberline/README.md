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
| `/faberline:issue-review` | Reads the PR and required CI, then records an independent review. |
| `/faberline:issue-plan-loop 1h` | Repeats `issue-plan` every hour. |
| `/faberline:issue-work-loop 15m` | Repeats `issue-work` every 15 minutes. |
| `/faberline:issue-review-loop 15m` | Repeats `issue-review` every 15 minutes in a separate review conversation. |

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

Three steps. Each step skill does one run and ends. A controller can run each
step in the repository:

```
/faberline:issue-plan
/faberline:issue-work
/faberline:issue-review
```

For repeated runs, use a companion in each step's own conversation:

```text
/faberline:issue-plan-loop 1h
/faberline:issue-work-loop 15m
/faberline:issue-review-loop 15m
```

Codex also accepts these skills by their installed names, such as
`$issue-plan-loop 1h`. Each companion runs its original skill once at a time.
It starts with one immediate pass. It then uses the requested interval.
The examples do not set global defaults or start any existing schedules.
See the [interval loop rules](skills/issue-loop/INTERVAL-LOOPS.md) for timer
support, session lifetime, ownership and stopping rules.
The three original skills still run once and never start timers themselves.

- **plan** (`/faberline:issue-plan`): read the
  repository's goals and status, triage and prioritize issues, and fill the
  `todo` queue from the backlog first, then with new issues,
  at most `ISSUE_TODO_MAX` (default 3) at once. Each issue has Goal,
  Acceptance, Never, and Gate (the exact unit-test commands) sections.
- **work** (`/faberline:issue-work`): take the
  already-planned `todo` issue by priority and age, implement it in its own
  worktree, run its
  unit-test gate, and open a PR with `Closes #<N>`. One issue at a time.
- **review** (`/faberline:issue-review`): take the oldest
  `done` issue. Read the issue, the PR, its diff, and its CI checks, and
  look for bugs. Review runs no tests: the PR checks do, and each repository
  decides what its CI runs, also whether e2e goes to the shared GKE cluster.
  Its own conversation keeps it apart from the work step's reasoning.
  After independently verifying every required CI gate on the same current
  head, pass records acceptance. Review never merges or changes labels;
  findings needing a human are reported as evidence.

`/faberline:issue-loop` shows the counts and existing `blocked` issues.
The shared [milestone queue contract](skills/issue-loop/MILESTONE-QUEUE.md)
gives Claude plan version ordering and admission checks. AGY consumes planned
todo without reapplying that frontier; existing coding leases and same-PR CI
repairs remain protected.

| Label | Set by | Meaning |
|---|---|---|
| `todo` | plan (initial queue), CI (failed current-head gates) | Ready for coding; retry uses the same PR/branch/worktree. |
| `doing` | work, only from todo | Coding or waiting for CI; submitted doing issues are not recoded each cycle. |
| `done` | CI only | All explicit required workflow/job gates passed for the current PR head; ready for independent review. |
| `blocked` | existing human triage / backlog planning | Work and review report questions without setting this label. |

See [issue-ci](skills/issue-ci/SKILL.md) for the trusted controller interface,
required-gate manifest and consumer workflow template. These are not deployed
by this PR: tape/keep/core/lumen/defer/relay each still need explicit wiring.
No paused schedules are restored. Work now requires a stable conversation
`ISSUE_WORK_OWNER`; review requires a trusted `ISSUE_CI_CONFIG`.

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
