---
name: aw-worker
description: "Run one scheduled issue cycle with the Rust `aw` CLI as this client's agent: Claude Code is `claude` (planner and spec), Antigravity is `agy` (tdd), Codex is `codex` (review). Use when an automation or the user asks to run the aw-worker skill."
user-invocable: true
---

# aw-worker

One run takes at most one issue through one stage.
Each AI runs this skill on its own hourly schedule and pulls its own work.
No controller assigns work.
The `claude` worker is also the planner. Only the planner changes labels.

## Roles

| Labels on the issue | Agent | Work |
|---|---|---|
| no `stage:` label | `claude` | triage (planner) |
| `stage:spec` | `claude` | write the spec |
| `stage:tdd` | `agy` | tests first, then code; `aw` opens the pull request |
| `stage:review` | `codex` | review a green pull request; `aw` merges it or sends it back |

## 1. Find the agent id, the clones and `aw`

- The agent id is the client you run in: Claude Code `claude`, Antigravity `agy`, Codex `codex`.
- Worker clones are in `~/faberlines/.aw-workers/<agent>/`.
  Each subdirectory is a dedicated clone of one faberline repository.
- When the current directory is inside a clone with `git config aw.agent`,
  use only that clone. Its value is the agent id.
- Use a clone only when its `git config aw.agent` is the agent id.
  When no clone is usable, stop and report `no worker clone`.
- Never work in a person's checkout, such as `~/faberlines/<repo>`.

Use the Rust `aw`: `AW=~/.local/share/aw-rs/bin/aw`.
`$AW --version` must print `aw 0.4.0` or newer.
`~/.local/bin/aw` can be an old Python tool with the same name; do not use it.
When no binary passes the check, stop and report it.
Install command: `cargo install --git https://github.com/faberline/aw --root ~/.local/share/aw-rs aw`.

## 2. Plan (`claude` only)

Do this in each clone before the claim. Use `gh ... -R <owner>/<repo>`.

A signal is a comment whose first line is
`<!-- aw:signal stage=<stage> agent=<agent> outcome=<outcome> -->`.
Only `aw` writes signals.
Move labels only when the newest signal has the same stage and agent as the
issue's current labels.
An open issue keeps exactly one `stage:` label and one `agent:` label.

| Stage label | Newest signal outcome | New labels |
|---|---|---|
| `stage:spec` | `ready` | `stage:tdd`, `agent:agy` |
| `stage:tdd` | `ready` | `stage:review`, `agent:codex` |
| `stage:review` | `failed` | `stage:tdd`, `agent:agy` |
| `stage:review` | `passed` | `stage:done`; close the issue if it is still open |
| any | `blocked` | see the next list |

For a `blocked` signal, fix the cause when planning can fix it:

- No open pull request closes the issue: `stage:tdd`, `agent:agy`.
- The reviewer authored the pull request: set `agent:` to an agent that did
  not author it (`codex` first, then `claude`).
- The spec is unclear or too large: `stage:spec`, `agent:claude`.
- Otherwise add `blocked` and comment one question for a person.

Then triage at most three issues: open, no `stage:` label, no `blocked`
label and no open sub-issues. Take `p0` first, then the oldest.

- Add `pN` from a legacy `priority:pN` label, or `p2` when there is none.
  Remove the legacy `doing` and `todo` labels.
- An open pull request already closes it: `stage:review`, `agent:codex`.
  Use `agent:claude` when the head branch starts with `codex/`.
- The body has a clear goal and testable acceptance criteria, and one pull
  request is enough: `stage:tdd`, `agent:agy`.
- The goal is clear but acceptance criteria are missing: `stage:spec`,
  `agent:claude`.
- One pull request is not enough: create child issues, one pull request each,
  with acceptance criteria. Link them as sub-issues of the parent and leave
  the parent without a stage. Close the parent when all children are closed.
- A person must decide the product direction: add `blocked` and comment one
  question.
- Obsolete or duplicate: close it with a one-line reason.

## 3. Claim

For each usable clone, in name order, until one claim succeeds:

1. `cd <clone>`.
2. When `$AW status` exits 0, a past run stopped early and still holds the
   claim. Continue it: read the JSON, keep the files in the tree, and go to
   step 4. Claim nothing more in this run.
   When `$AW status` exits 2 (no claim) and `git status --porcelain` is not
   empty, a past run stopped early. Keep its files:
   `git stash push -u -m "aw-worker leftover <UTC time>"`.
3. `$AW claim --agent <agent>`.
   - Exit 2: nothing to do here. Try the next clone.
   - Exit 1: keep stderr for the report. Try the next clone.
   - Exit 0: read the JSON (`repo`, `issue`, `stage`, `branch`, `pr`).
     Go to step 4 and claim nothing more in this run.

When no clone gives a claim, go to step 6.

## 4. Do the stage

First read the issue and all comments: `gh issue view <n> -R <repo> --comments`.
Read the repository rules: `AGENTS.md`, `CLAUDE.md` or `CONTRIBUTING.md`.
Follow them for code and checks. For git and GitHub, `aw` and this skill win.
Put report files in a temporary directory, never in the clone.

### spec

- Do not change files in the clone.
- Write the spec: the goal; acceptance criteria that a test or command can
  prove; the tests to write, with one negative case that must fail without
  the change; the files to change; what is out of scope.
- When one pull request is not enough, report `blocked` and say how to split.
- `$AW report ready -F <file>`. The spec becomes the signal comment.

### tdd

- The spec is the issue body and the newest `stage=spec` signal.
  On rework, also read the newest `stage=review` signal and the pull request
  review comments.
- Work on the branch that `aw claim` checked out. On rework it is the open
  pull request's branch. Do not copy files from another branch.
- When the branch is behind `origin/<default>` or has a conflict, run
  `git merge origin/<default>` and resolve it. Never rebase. Never force-push.
- Write the failing tests first. Then write the smallest code that makes them
  pass. Stay inside the spec.
- Commit with conventional messages. Run the repository's checks. For Rust:
  `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test`.
- With a clean tree, run `$AW report ready -F <file>`. The file becomes the
  pull request body after `Closes #<n>`: summary, changes, the checks you ran
  and their results.
- When you cannot finish in this run, commit the work in progress and run
  `$AW release`. The next run continues on the same local branch.

### review

- Read the pull request: `gh pr view <pr> -R <repo>`, `gh pr checks <pr> -R <repo>`,
  `gh pr diff <pr> -R <repo>`.
- Checks are still running: `$AW release` and stop. The next run tries again.
- A check failed or the pull request has a merge conflict:
  `$AW report failed -F <file>` with the failed checks or the conflict.
- Compare the diff with the spec and its acceptance criteria. Do not change
  code. You can run the tests; the clone is detached at the pull request head.
- Every criterion is proven and all checks are green:
  `$AW report passed -F <file>`. `aw` approves and squash-merges.
- Otherwise `$AW report failed -F <file>` with findings per file.

## 5. After the report (`claude` only)

Do the label moves of step 2 again for the issue you reported.
Then the next agent does not wait one more hour.

## Rules

- `aw` does every push, pull request, review, merge and signal comment.
  Do not do them with `git` or `gh`.
- Only the planner (`claude`) changes labels and creates or closes issues.
- Work only on the issue that `aw claim` gives. One issue in each run.
- When you stop with an active claim and cannot finish, run `$AW release`.
- Never edit the claim file (`<git dir>/aw/claim.json`). Only `aw` writes it.
- Run builds and checks in the foreground and wait for them. Do not end the
  run while a command still runs.
- When a command is denied by a permission rule, do not work around it. Run
  `$AW release` and name the command and the rule in the final answer.
- `blocked` is for a decision that only a person can make. Write the reason.

## Final answer

Use the user's language. Give:

- the agent id and the clones you used;
- the claim result for each clone;
- the work: issue, stage, outcome and pull request;
- the label moves (planner only);
- for each clone, the count of open issues with your `agent:` label;
- the health: `正常` (normal) or `異常` (abnormal) with the reason.
