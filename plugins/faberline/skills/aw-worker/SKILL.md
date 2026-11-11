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
- `$AW --version` must print `aw 0.7.23` or newer.

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
`$AW --version` must print `aw 0.7.23` or newer.
An older `aw` has no `respec` outcome or handles it wrongly: install the new one with the command below, then check again.
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
   | `stage:tdd` | `respec` | `stage:spec` |
   | `stage:review` | `failed` or `closed` | `stage:tdd` |
   | `stage:review` | `passed` | `stage:done`; close the issue if it is still open |

3. For an issue with the `blocked` label or a newest `blocked` signal, fix
   the cause when planning can fix it. Comment the answer, then remove
   `blocked`. A comment after the `blocked` signal lets the same stage claim
   the issue again (`aw` 0.7.8 or later):
   - No open pull request closes the issue: `stage:tdd`.
   - The spec is unclear or too large: `stage:spec`. A review `blocked`
     signal that starts with `spec unclear:` is this case; the new spec
     answers each point of it.
   - Otherwise keep `blocked` and comment one question for a person.
4. Triage at most three issues: open, no `stage:` label, no `blocked`
   label and no open sub-issues. Take them in claim order: `p0` first,
   then the label `bar`, then the lowest milestone version, then the `pN` label, then the
   oldest.
   Always add `agent:claude`.
   - Replace a legacy `priority:pN` label with `pN`, or add `p2` when there
     is none. Use only `pN` labels.
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
   - An issue without a milestone: leave the milestone empty. A person
     sets it.
5. Check for a release. A release follows the milestones: a milestone is
   ready when all of its issues are closed. Find the open milestone with
   the lowest version in its title (`<repo>@X.Y.Z` or `vX.Y.Z`). It is
   ready when it has 0 open issues and at least 1 closed issue.
   - When the repository has `.github/aw-release.json`, follow it.
   - Otherwise do not release. Name the milestone in the final answer as
     ready for release.
   Do not close a milestone. The release closes it.
6. Open the next coverage issue (standard S8, `docs/standards/coverage.md`
   in aw). Do this only when the default branch's `aw.toml` has
   `[standards.coverage]` and no open issue has a title that starts with
   `test(coverage):`.
   - Find the newest artifact `coverage` of the default branch:
     `gh api "repos/<repo>/actions/artifacts?name=coverage&per_page=30" --jq '[.artifacts[] | select(.workflow_run.head_branch == "<default>" and (.expired | not))][0].id'`.
     No artifact: skip this step.
   - Download it to a temporary directory:
     `gh api repos/<repo>/actions/artifacts/<id>/zip > <dir>/coverage.zip`,
     then `unzip -o <dir>/coverage.zip -d <dir>`. Take the first entry of
     `most_uncovered` in `coverage-report.json`.
   - Create one issue with the title `test(coverage): cover <file>` and a
     body with: the file, its line percent and uncovered lines; the goal,
     tests that run every line of the file; the rules: tests only, no change
     of behavior, a bug that a test finds becomes a new issue, an exclude
     only as S8 allows; set `line` in `aw.toml` to the value that COV4
     names. Triage it in the next plan like any issue: `p2`.

`aw claim` takes `p0` first, then the issues with the label `bar`, then
the issues of the lowest milestone version, so one milestone is cleared
before the next one starts. A run does not add or remove `bar`; a
person or the service desk sets it.

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

Take the repositories in turns, so that one repository with a long queue
does not use every stage of the run. Start at the repository whose
position in the name-order list (counting from 0) is the current UTC hour
modulo the number of repositories. For each repository:

1. `cd` to the clone (section 1). `aw` needs it for tdd.
2. When `$AW status <who>` exits 0, a past run stopped early and still holds
   the claim. Continue it: read the JSON and go to section 4.
3. When `$AW status <who>` exits 2 (no claim) and `git status --porcelain`
   is not empty, a past run stopped early. Keep its files:
   `git stash push -u -m "aw-worker leftover <UTC time>"`.
4. `$AW claim <who>`, with one `--skip <issue>` for each issue that this
   run released because its checks were still running.
   On a Mac (`uname` prints `Darwin`), add `--has mac`. Then `aw` (0.7.13
   or later) also offers the issues labeled `needs:mac`. On any other
   machine do not add it; `aw` passes over those issues.
   - Exit 2: nothing to do here. Try the next repository.
   - Exit 1: keep stderr for the report. Try the next repository.
   - Exit 0: read the JSON (`repo`, `issue`, `stage`, `branch`, `pr`).
     Go to section 4.

After each report, go to the next repository in name order, and after the
last one go back to the first. Pass over a repository where `aw claim`
exited 1 or 2 in this run. Stop the run when:

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
     When the change touches `.github/workflows/ci.yml`, add
     `$AW ci check --dir . --no-ruleset` (standard S7).
  6. **Acceptance criteria**: each one proven by a named test or command.
  7. **Out of scope**: what not to change.
  8. **Traps**: repository rules and known failures that apply, for example
     the CI contract note in "Repository notes".
- Keep it to one pull request: at most about six files and 400 changed
  lines. When one pull request is not enough, report `blocked` and say how
  to split.
- The pull request closes the issue: `aw report ready` always writes
  `Closes #<n>`. Do not write a spec for a slice that must leave the issue
  open. When the issue needs more than one pull request, report `blocked`
  and name the child issues, one pull request each.
- A parent issue can have to stay open, for example a tracker that waits
  for other work. Then split the work into child issues. Each pull request
  has `Closes #<child>`. A pull request must not close the tracker. `aw`
  cannot link a pull request to an issue without closing it. The tracker
  closes when its children are closed (faberline/aw issue 261).

- When the spec must make tdd start again from the newest default branch
  (the old pull request branch is wrong or dirty), write the line
  `aw:redo` alone on its own line in the spec file. Do not only write
  "start from main" in prose: `aw claim` reads only this line. Then
  `aw claim` for tdd gives a new branch `<head>-r2` (later `-r3`) from the
  default branch and closes the old pull request with a comment. The redo
  happens once, at the next tdd claim; later claims continue the new branch.
- `$AW report <who> ready -F <file>`. The spec becomes the signal comment.

### tdd

- The spec is the issue body and the newest `stage=spec` signal.
  On rework, also read the newest `stage=review` signal and the pull request
  review comments.
- Work on the branch that `aw claim` checked out. On rework it is the open
  pull request's branch. Do not copy files from another branch.
- `aw claim` already ran `git merge origin/<default>` when that branch was
  behind and the merge was clean. When the merge conflicts, or the spec
  has an `aw:redo` line, `aw claim` gives a new branch `<head>-r2` (or
  `-r3`) from the default branch and closes the old pull request. Use the
  branch in the claim JSON; never switch branches yourself. Never rebase.
  Never force-push. The net diff must still hold only the spec's files.
- If you must merge yourself (for example `main` moved during the run), run
  `git merge origin/<default>`, not rebase, and resolve it.
- First compare the spec with the spec checklist, before any other work.
  When the spec does not meet it, do not guess. Report `respec`:
  `$AW report <who> respec -F <file>`. The file lists the missing points,
  one numbered line each: name each missing file, rule, test or value
  exactly. `aw` moves the issue to `stage:spec` and adds no `blocked`.
- Never report `blocked` for an incomplete or unclear spec. `blocked` keeps
  `stage:tdd` and adds `blocked`: the spec run does not read `stage:tdd`,
  and the tdd run does not take `blocked`, so no run takes the issue.
  As a safety net, `aw` (0.7.22 or later) changes a tdd `blocked` report to
  `respec` when its first line says the spec is the cause (for example
  `spec unclear: ...`). Do not rely on it.
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
  not right, report `blocked` with the reason. When the subagent says the
  spec is unclear or lacks a file or a rule, report `respec` instead.
- Format Rust with `cargo fmt`. It reads each crate's edition. When
  `cargo fmt --all -- --check` also fails on files the branch did not
  change, format only the changed files with
  `rustfmt --edition <edition in that crate's Cargo.toml> <file>`.
  Never guess the edition: a wrong edition sorts imports differently and
  review fails on CI's `cargo fmt --all -- --check`.
- When the branch changes `.github/workflows/ci.yml`, run
  `$AW ci check --dir . --no-ruleset` in the clone (`aw` 0.7.11 or later).
  It checks the CI stages standard, `docs/standards/ci.md` in aw. Every
  check must be `pass`, `excepted` or `skipped`. Fix each `fail`. Put the
  JSON output in a fenced block in the report file below.
- When the repository's `aw.toml` has `[standards.coverage]` (standard S8,
  `docs/standards/coverage.md` in aw), the tests run every new and changed
  line. Put this rule in the subagent's brief. On rework after a CI coverage
  failure, follow the check's detail: COV3 needs more tests; COV4 names the
  new `line` for `aw.toml`. Never lower `line`.
- Commit with conventional messages.
- On rework, do not remove required items from the pull request body.
  The body keeps the one `Closes #<n>` line. It also keeps each item that
  `aw ci check` or the review requires. Add to the body; do not drop items.
- With a clean tree, run `$AW report <who> ready -F <file>` in the clone.
  The file becomes the pull request body after `Closes #<n>`: summary,
  changes, the checks you ran and their results. `aw` writes the
  `Closes #<n>` line. Do not write it in the file. Do not write `close`,
  `fix` or `resolve` before an issue number or a link anywhere in it: CI
  then finds no linked issue and fails `development preflight`.
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
    conflict. No subagent is needed, except on the third review (below).
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
  - a lower `line` in `[standards.coverage]` of `aw.toml`, or a new
    `[[standards.coverage.exclude]]` whose reason the S8 page
    (`docs/standards/coverage.md` in aw, section "Excludes") does not
    allow, is a finding;
  - read only; do not run `aw`, `git` writes or `gh` writes;
  - the first line of the pull request body, `Closes #<n>`, is written by
    `aw` and links the issue; it is required, not a finding;
  - answer with the first line `VERDICT: passed`, `VERDICT: failed` or
    `VERDICT: closed` (on the third review also `VERDICT: blocked`), then
    the findings per file with a fix for each;
  - `closed` means that rework cannot save the pull request and a new start
    from the default branch costs less. Use it only for one of these, and
    name the one that applies with its evidence:
    - the default branch already has the change, or another pull request
      replaced this one;
    - the diff does not build what the spec asks for, so most of it must be
      written again;
    - the issue has two or more `stage=review` `failed` signals for this
      pull request and the same findings are still there;
    - the branch is so far behind that its conflicts touch most of the diff.
- Use the subagent's verdict. Do not change `failed` to `passed`. You may
  change `passed` to `failed` only for a fact you can show (a red check, a
  conflict, a file outside the spec). Only the subagent gives `closed`; do
  not choose it yourself.
- The third review is a diagnosis. When the issue already has two or more
  `stage=review` `failed` signals for this pull request, always give it to
  the subagent, also when a check failed or there is a conflict (the quick
  gates do not apply). Add to its brief the failed checks or the conflict,
  the earlier `failed` signals, and this task: read the issue, the newest
  `stage=spec` signal and the diff, and find why the rework does not
  converge. It answers with one cause on the first line:
  - `VERDICT: failed` — the spec is clear and the code is close. Give each
    remaining finding with the exact fix, so the next tdd run cannot miss it.
  - `VERDICT: closed` — the spec is clear but the pull request cannot be
    saved (the `closed` cases above).
  - `VERDICT: blocked` — the issue or the spec is the cause: it is unclear,
    it contradicts itself or the repository, it is too large, or a criterion
    cannot be tested. Name the sentence or criterion and say what is missing.
  - `VERDICT: passed` — all checks are green and the earlier findings are
    fixed.
- `blocked` from the diagnosis: `$AW report <who> blocked -F <file>`. Start
  the file with `spec unclear:` and the subagent's reason. The next spec run
  reads it, writes the spec again and moves the issue to `stage:spec`
  (section 2, step 3). The pull request stays open; the new spec says to
  keep it or to start again.
- `passed`: `$AW report <who> passed -F <file>` with the summary.
  `aw` squash-merges. It does not approve: the verdict is the review.
  A strict ruleset refuses a branch that is behind the default branch.
  Then `aw` (0.7.3 or later) updates the branch instead of merging. Its JSON
  has `"updated_branch": true`, the issue stays at `stage:review` and the
  claim becomes a `branch updated` note. CI runs again. This is not a
  failure; do not report `failed` for it.
- Only one review pull request per repository is brought up to date at a
  time. When another `stage:review` pull request is already up to date with
  the default branch, `aw` (0.7.7 or later) does not update this branch. Its
  JSON has `"waiting_for": <pr>`, the issue stays at `stage:review` and the
  claim becomes a `waiting to update` note. This is not a failure either.
  An up-to-date pull request counts only when every check that the repo
  ruleset requires exists on its head and none failed; a required check may
  still run (0.7.19 or later). A pull request with a required check missing
  or failed cannot merge next, so it does not count and the behind branch
  is updated. When `aw` cannot read the required checks, it keeps the older
  rule (only a failed check frees the update) and the `branch updated` note
  says so. An up-to-date pull request of a lower
  priority label (for example `p2` against `p0`) does not count either; the
  higher-priority branch is updated at once (0.7.12 or later).
- The same applies to a merge. When a pull request of a higher priority was
  just updated and its required checks still run, `aw` (0.7.19 or later)
  does not merge a lower-priority pull request of the repo. Its JSON has
  `"waiting_for": <pr>` and the claim becomes a `waiting to update` note.
  Merging it would make the `p0` branch behind again.
- `mergeable_state` `behind` alone is not a failure. Review as usual; `aw`
  updates the branch on `passed`.
- The newest `aw` comment on the issue is a `branch updated` or
  `waiting to update` note, and the head has no commit after it except an
  update merge from the default branch: the code was already reviewed. When
  all checks are green, report `passed` again without a subagent.
- `failed`: `$AW report <who> failed -F <file>` with the findings per file.
  `aw` sends the issue back to tdd.
- `closed`: `$AW report <who> closed -F <file>` with the reason and its
  evidence (`aw` 0.7.14 or later). `aw` comments the reason on the pull
  request, closes it without a merge, deletes its branch and sends the issue
  back to tdd. The next tdd run starts again from the default branch. When
  the default branch already has the change, say so in the file; the next
  spec run closes the issue. With an older `aw`, report `failed`.

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
  A spec that tdd finds incomplete or unclear is not such a case: the spec
  run can fix it. In tdd, report `respec`.
- Local runs only: when `aw report` cannot push over HTTPS (no git
  credentials, or a token without the `workflow` scope for a change under
  `.github/workflows/`) and an SSH key works, run the same command once more
  with SSH for that command only:
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.git@github.com:.insteadOf GIT_CONFIG_VALUE_0=https://github.com/ $AW report ...`.
  Do not change git or `gh` configuration files.

## Final answer

Use the user's language. Give:

- the repositories you checked;
- the plan changes: label moves, triage, closed issues;
- each stage you did: issue, stage, outcome and pull request;
- why the run stopped;
- for each repository, the count of open issues per `stage:` label;
- a milestone that is ready for release (section 2, step 5);
- the health: `正常` (normal) or `異常` (abnormal) with the reason.
