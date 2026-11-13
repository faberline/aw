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
| `/faberline:aw-supervisor` | Runs one supervisor pass over the issue flow of one repository. |

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

## Issue work

Issue work runs through the `aw` CLI in the
[repository root](../../README.md): `aw claim`, the work, then `aw report`.

`aw-worker` runs these cycles for an hourly automation. One agent,
`claude` (Claude Code), does every stage: triage, spec, tdd and review.
Each run plans first, then claims and reports until it must wait for CI or
has finished three stages. `aw report` moves the `stage:` label, so the next
claim can continue at once. In a cloud session, tdd uses the session's
checkout; on a computer it uses `~/faberlines/.aw-workers/claude/<repo>`.
The run's own model (Sonnet) plans, writes specs and runs `aw`. In tdd a
Haiku subagent writes the tests and code from the spec. In review an Opus
subagent gives the verdict. So each spec follows a checklist: exact files
and names, steps, tests with expected values, commands and traps.

The automation can also run one worker per stage: `spec` (Sonnet, also
plans), `tdd` (Haiku) and `review` (Opus). Each passes `--stage <stage>`
to `aw`, so each holds its own claim and the three runs do not block each
other.

When review passes but a strict ruleset refuses a branch that is behind
`main`, `aw` updates the branch instead of merging. The issue stays at
review, CI runs again and the next review run merges it.

`aw claim` takes `p0` first, then the issues with the label `ci-fix` (the
repair of a red required check on the default branch), then the issues
with the label `bar` (inside the internal-use bar), then the issues of the open milestone with
the lowest version (`<repo>@X.Y.Z` or `vX.Y.Z`), then the priority label,
then the oldest issue. A repository that does not use `ci-fix` or `bar` keeps
the order without them. A milestone is ready for release when all of its
issues are closed. The plan step names it in the run's final answer, or
follows `.github/aw-release.json` when the repository has one.

### Open an issue

Answer every question when you open the issue, so the worker does not
stop at `blocked`. Give:

- the goal: what changes and why;
- the acceptance criteria that a test can check;
- the scope: what stays out, and the files or modules when you know them;
- the decisions a person must make (names, versions, product direction),
  already made;
- the milestone, and a `pN` priority label.

One issue is one pull request. Split larger work into sub-issues.

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
