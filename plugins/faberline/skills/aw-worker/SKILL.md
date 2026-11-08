---
name: aw-worker
description: "Run one scheduled issue cycle with the Rust `aw` CLI. One agent, `claude` (Claude Code), plans, writes the spec, does tdd and reviews. Use when an automation or the user asks to run the aw-worker skill."
user-invocable: true
---

# aw-worker

One agent does all the work: `claude`. It runs on an hourly schedule.
Each run plans first. Then it takes issues through their stages, one claim
at a time, until it must wait for CI or has finished three stages.
`aw report` moves the `stage:` label, so the next claim can continue at once.

Each kind of work has its own model:

| Work | Model |
|---|---|
| plan, spec, and every `aw` command | the run's own model (Sonnet) |
| tdd implementation | a subagent with `model: "haiku"` |
| review verdict | a subagent with `model: "opus"` |

The run's own model gives the work to the subagent with the Agent tool,
checks the result and runs `aw`. Subagents never run `aw`, commit or push.
When the Agent tool is not available, the run does the work itself.
A small model implements the spec, so a spec must be complete enough for
that model to do the work from the spec alone.

When this skill runs in a client other than Claude Code, stop and report
`aw-worker runs only as claude`.

## Stage runs

The automation can run one worker per stage, each on its own schedule
and model. Then the run's prompt names one stage:

| Stage run | Model of the run | Plan (section 2) |
|---|---|---|
| `spec` | Sonnet | yes |
| `tdd` | Haiku | no |
| `review` | Opus | no |

In a stage run:

- Add `--stage <stage>` to every `claim`, `report`, `release` and `status`
  command (it is part of `<who>`). `aw` then takes and holds only claims of
  that stage, so the three runs do not block each other.
- Only the spec run plans. The tdd and review runs skip section 2.
- The run's own model does the stage work. Do not give it to a subagent.
- `$AW --version` must print `aw 0.7.2` or newer.

Without a stage in the prompt, one run does every stage as described below.

## Stages

| Labels on the issue | Work |
|---|---|
| no `stage:` label | triage (plan) |
| `stage:spec` | write the spec |
| `stage:tdd` | tests first, then code; `aw` opens the pull request |
| `stage:review` | review a green pull request; `aw` merges it or sends it back to tdd |

Every issue in the flow has the label `agent:claude`.

## 1. Find the repositories, the clone and `aw`

- The repositories are the faberline repositories with the GitHub topic `aw`:
  `gh api --paginate "orgs/faberline/repos?per_page=100" --jq '.[] | select(.archived | not) | select(.topics | index("aw")) | .full_name'`.
  When that call is refused (a cloud session can be limited to its own
  repositories), use the repositories that the session checked out.
  Use them in name order. When the list is empty, stop and report `no aw repository`.
- tdd needs a clone. In a cloud session, use the checkout of the repository
  that the session started with. Otherwise use
  `~/faberlines/.aw-workers/claude/<name>` (`<name>` is the repository name
  without the owner). When it is missing, create it:
  `gh repo clone <owner>/<name> ~/faberlines/.aw-workers/claude/<name>`.
- Never work in a person's checkout, such as `~/faberlines/<repo>`.

Use the Rust `aw`: `AW=~/.local/share/aw-rs/bin/aw`.
`$AW --version` must print `aw 0.7.0` or newer.
`~/.local/bin/aw` can be an old Python tool with the same name; do not use it.
When no binary passes the check, stop and report it.
Install command: `cargo install --git https://github.com/faberline/aw --root ~/.local/share/aw-rs aw`.

Give every `claim`, `report`, `release` and `status` command the agent and
the repository: `--agent claude --repo <owner>/<name>`, and in a stage run
also `--stage <stage>`. Below, `<who>` means these options.

Use only the GitHub REST API: `gh api <path>`. Claude Code cloud sessions
block GraphQL, and most `gh issue`, `gh pr` and `gh repo` commands use it.
`gh search code` and `gh repo clone` also work. Common calls, where `<repo>`
is `<owner>/<name>`:

- issues with a label: `gh api --paginate "repos/<repo>/issues?state=open&labels=<label>&per_page=100" --jq '.[] | select(.pull_request | not)'`
- one issue: `gh api repos/<repo>/issues/<n>`
- its comments: `gh api --paginate "repos/<repo>/issues/<n>/comments?per_page=100"`
- add labels: `gh api -X POST repos/<repo>/issues/<n>/labels -f 'labels[]=<a>' -f 'labels[]=<b>'`
- remove a label: `gh api -X DELETE repos/<repo>/issues/<n>/labels/<label>` (encode `:` as `%3A`)
- comment: `gh api -X POST repos/<repo>/issues/<n>/comments -F body=@<file>`
- close: `gh api -X PATCH repos/<repo>/issues/<n> -f state=closed -f state_reason=<completed or not_planned>`
- create an issue: `gh api -X POST repos/<repo>/issues -f title=<t> -F body=@<file>`
- sub-issue link: `gh api -X POST repos/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child issue id>`
- sub-issues: `gh api repos/<repo>/issues/<n>/sub_issues`
- open pull requests: `gh api --paginate "repos/<repo>/pulls?state=open&per_page=100"`
- one pull request: `gh api repos/<repo>/pulls/<pr>`
- its diff: `gh api -H 'Accept: application/vnd.github.diff' repos/<repo>/pulls/<pr>`
- its checks: `gh api "repos/<repo>/commits/<head sha>/check-runs?per_page=100" --jq '.check_runs[] | {name, status, conclusion}'`
- its review comments: `gh api repos/<repo>/pulls/<pr>/comments`

## 2. Plan

Do this in each repository before the first claim.

A signal is a comment whose first line is
`<!-- aw:signal stage=<stage> agent=<agent> outcome=<outcome> -->`.
Only `aw` writes signals.
An open issue in the flow keeps exactly one `stage:` label and the label
`agent:claude`.

1. Move old agent labels: replace `agent:agy` and `agent:codex` with
   `agent:claude` on every open issue.
2. Finish old label moves. `aw` 0.7.0 moves labels itself, but older signals
   did not. When the newest signal has the same stage as the issue's
   `stage:` label, move it:

   | Stage label | Newest signal outcome | New stage label |
   |---|---|---|
   | `stage:spec` | `ready` | `stage:tdd` |
   | `stage:tdd` | `ready` | `stage:review` |
   | `stage:review` | `failed` | `stage:tdd` |
   | `stage:review` | `passed` | `stage:done`; close the issue if it is still open |

3. For an issue with the `blocked` label or a newest `blocked` signal, fix
   the cause when planning can fix it, then remove `blocked`:
   - No open pull request closes the issue: `stage:tdd`.
   - The spec is unclear or too large: `stage:spec`.
   - Otherwise keep `blocked` and comment one question for a person.
4. Triage at most three issues: open, no `stage:` label, no `blocked`
   label and no open sub-issues. Take `p0` first, then the oldest.
   Always add `agent:claude`.
   - Add `pN` from a legacy `priority:pN` label, or `p2` when there is none.
     Remove the legacy `doing` and `todo` labels.
   - An open pull request already closes it: `stage:review`.
   - The body already meets every point of the spec checklist (section 4,
     spec), and one pull request is enough: `stage:tdd`.
   - The goal is clear but the body does not meet the spec checklist:
     `stage:spec`.
   - One pull request is not enough: create child issues, one pull request
     each, with acceptance criteria. Link them as sub-issues of the parent
     and leave the parent without a stage. Close the parent when all
     children are closed.
   - A person must decide the product direction: add `blocked` and comment
     one question.
   - Obsolete or duplicate: close it with a one-line reason.

Do not edit an issue's title or body. Write specs and notes as comments.
Each shell command writes to at most one issue. Do not use `for` or
`while` loops, and do not name two issue numbers in one write command.
A permission reviewer refuses a command whose effect it cannot see. For
three issues, run three separate commands, one after another.
When a write is refused, run it again once as one command for one issue.
Finish the plan before the first claim. Do not run plan writes and
`aw claim` at the same time.

## 3. Claim

The claim is a comment on the issue that `aw` writes. It starts with
`<!-- aw:claim`. `aw` finds it again from the agent and the repository.

For each repository, in name order:

1. `cd` to the clone (section 1). `aw` needs it for tdd.
2. When `$AW status <who>` exits 0, a past run stopped early and still holds
   the claim. Continue it: read the JSON and go to section 4.
3. When `$AW status <who>` exits 2 (no claim) and `git status --porcelain`
   is not empty, a past run stopped early. Keep its files:
   `git stash push -u -m "aw-worker leftover <UTC time>"`.
4. `$AW claim <who>`, with one `--skip <issue>` for each issue that this
   run released because its checks were still running.
   - Exit 2: nothing to do here. Try the next repository.
   - Exit 1: keep stderr for the report. Try the next repository.
   - Exit 0: read the JSON (`repo`, `issue`, `stage`, `branch`, `pr`).
     Go to section 4.

After each report, claim again in the same repository. Stop the run when:

- `aw claim` exits 2 in every repository;
- you released a claim because you could not finish it;
- this run has reported three stages.

## 4. Do the stage

First read the issue and all its comments with the REST calls above.
Read the repository rules: `AGENTS.md`, `CLAUDE.md` or `CONTRIBUTING.md`.
Follow them for code and checks. For git and GitHub, `aw` and this skill win.
Put report files in a temporary directory, never in a clone.
Write the report file with the file tool or its own plain command first.
Then run `$AW report` as a plain command. Do not add `trap`, `rm`,
`set -e` or other cleanup to it; some clients block shell forms that they
cannot inspect. Leave the temporary file; the system removes it.

Read code through the GitHub API when you do not need the clone:

- the file list: `gh api "repos/<repo>/git/trees/<ref>?recursive=1" --jq '.tree[].path'`;
- one file: `gh api -H 'Accept: application/vnd.github.raw' "repos/<repo>/contents/<path>?ref=<ref>"`;
- a search: `gh search code '<text>' --repo <repo>`.

`<ref>` is the default branch, or the pull request head branch in a review.

### spec

- Read the code; do not change it. Read every file that the change touches.
- A Haiku subagent implements the spec. It knows only the spec and the
  repository. Write the spec so that it can finish without a question.
  Use exact names and paths, not descriptions. The spec checklist:
  1. **Goal**: one or two sentences.
  2. **Files**: each file to change or create, with its path. For each one,
     the functions, types or sections to add or change, by exact name, and
     the signature of each new public item.
  3. **Steps**: the order of the work, one change per step. Name the
     existing code to copy a pattern from (path and item).
  4. **Tests**: each test by file, name and what it asserts, with the input
     and the expected value. One negative test must fail without the change.
  5. **Commands**: the exact commands that must pass, from the repository
     rules (for Rust: `cargo fmt --all -- --check`,
     `cargo clippy --all-targets -- -D warnings`, `cargo test`). Add a
     narrower command for the new tests, for example `cargo test -p <crate> <name>`.
  6. **Acceptance criteria**: each one proven by a named test or command.
  7. **Out of scope**: what not to change.
  8. **Traps**: repository rules and known failures that apply, for example
     the CI contract note in "Repository notes".
- Keep it to one pull request: at most about six files and 400 changed
  lines. When one pull request is not enough, report `blocked` and say how
  to split.
- `$AW report <who> ready -F <file>`. The spec becomes the signal comment.

### tdd

- The spec is the issue body and the newest `stage=spec` signal.
  On rework, also read the newest `stage=review` signal and the pull request
  review comments.
- Work on the branch that `aw claim` checked out. On rework it is the open
  pull request's branch. Do not copy files from another branch.
- When the branch is behind `origin/<default>` or has a conflict, run
  `git merge origin/<default>` and resolve it. Never rebase. Never force-push.
- When the spec does not meet the spec checklist, do not guess. Report
  `blocked` with the missing points; the next plan sends it back to spec.
- Give the implementation to one subagent with the Agent tool and
  `model: "haiku"`. Its brief has: the clone path and branch; the whole spec;
  on rework, the review findings; the repository rules file to read; and
  these rules: write the failing tests first, then the smallest code that
  makes them pass; stay inside the spec; run the spec's commands in the
  foreground; do not commit, push, run `aw` or `gh`, or switch branches;
  report the changed files, each command and its result, and every point
  where the spec was unclear.
- When the Agent tool is not available, do the implementation yourself with
  the same rules.
- Check the result yourself: read `git diff`, compare it with the spec, and
  run the spec's commands in the foreground. When a check fails or the diff
  leaves the spec, send the findings to the same subagent once
  (SendMessage). Fix small remaining faults yourself. When the work is still
  not right, or the subagent says the spec is unclear, report `blocked` with
  the reason.
- Commit with conventional messages.
- With a clean tree, run `$AW report <who> ready -F <file>` in the clone.
  The file becomes the pull request body after `Closes #<n>`: summary,
  changes, the checks you ran and their results.
- When you cannot finish in this run, commit the work in progress. In a cloud
  session the clone is gone after the run, so push it:
  `git push origin <branch>` (never force). Then run `$AW release <who>`.
  The next claim continues from that branch.
- When the push is refused for that branch, report `blocked` with the branch
  name and the error.

### review

- CI is the test evidence; do not run tests.
- First do the quick gates yourself with the REST calls above. The pull
  request JSON has `head.sha`, `mergeable` and `mergeable_state`.
  - Checks are still running: `$AW release <who>`. Remember the issue
    number. Claim again with `--skip` for it (section 3, step 4). A later
    run reviews it.
  - A check failed or the pull request has a merge conflict:
    `$AW report <who> failed -F <file>` with the failed checks or the
    conflict. No subagent is needed.
- All checks are green and there is no conflict: give the verdict to one
  subagent with the Agent tool and `model: "opus"`. Its brief has: the
  repository and pull request number; the issue number; the REST calls for
  the pull request, its diff, its checks and whole files at the head branch;
  the repository rules file; and these rules:
  - review as a stranger would: trust only the issue, the newest
    `stage=spec` signal, the diff and CI;
  - compare the diff with the spec and each acceptance criterion; check that
    a test in the diff proves each criterion and that CI ran it;
  - a small model wrote the code: look for tests that assert nothing,
    skipped or ignored tests, and changes outside the spec's files;
  - read only; do not run `aw`, `git` writes or `gh` writes;
  - answer with the first line `VERDICT: passed` or `VERDICT: failed`, then
    the findings per file with a fix for each.
- Use the subagent's verdict. Do not change `failed` to `passed`. You may
  change `passed` to `failed` only for a fact you can show (a red check, a
  conflict, a file outside the spec).
- `passed`: `$AW report <who> passed -F <file>` with the summary.
  `aw` approves and squash-merges. GitHub refuses an approval of your own
  pull request; `aw` reports that as a warning and still merges.
- `failed`: `$AW report <who> failed -F <file>` with the findings per file.
  `aw` sends the issue back to tdd.

## Repository notes

- `faberline/core` binds each issue to a reviewed CI contract in
  `.github/issue-ci-required-gates.json` (`qualification.contracts`).
  When CI says `linked issue has no reviewed contract`, the tdd pull request
  adds the contract for its own issue. Copy the shape of contract `100`:
  every category `required` with all existing checks of that kind,
  `additional` and `unresolved` empty, and `title_sha256` and `body_sha256`
  as the SHA-256 of the issue's exact title and body text (UTF-8).
  Put the same entry in `ci_handoff_policy.qualification.contracts`.
  Change nothing else in that file.

## Rules

- `aw` does every pull request, review, merge and signal comment, and the
  push of a finished stage. Do not do them with `git` or `gh`.
  The only other push is the work-in-progress push in tdd.
- Work only on the issue that `aw claim` gives. One claim at a time.
- When you stop with an active claim and cannot finish, run `$AW release <who>`.
- Never edit or delete a claim or signal comment (`<!-- aw:`). Only `aw` writes them.
- The claim comment is the only lock in this skill. Do not use the AW session
  service (MCP `list_sessions`, `read_inbox`, `claim_worktree`) to check for
  other workers. When that service is unavailable, do not stop: `aw claim`
  already prevents overlap.
- Run builds and checks in the foreground and wait for them. Do not end the
  run while a command still runs.
- When a command is denied by a permission rule, do not work around it. Run
  `$AW release <who>` and name the command and the rule in the final answer.
  Exception: when a guard rejects only the shell form (for example a `trap`
  or a compound command), run the same action once more as one plain
  command. When that is also denied, release.
- `blocked` is for a decision that only a person can make, or for a refusal
  that this run cannot fix. Write the reason.

## Final answer

Use the user's language. Give:

- the repositories you checked;
- the plan changes: label moves, triage, closed issues;
- each stage you did: issue, stage, outcome and pull request;
- why the run stopped;
- for each repository, the count of open issues per `stage:` label;
- the health: `正常` (normal) or `異常` (abnormal) with the reason.
