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
Only tdd touches code in a clone. Planning, spec and review use only GitHub.

## Roles

| Labels on the issue | Agent | Work |
|---|---|---|
| no `stage:` label | `claude` | triage (planner) |
| `stage:spec` | `claude` | write the spec |
| `stage:tdd` | `agy` | tests first, then code; `aw` opens the pull request |
| `stage:review` | `codex` | review a green pull request; `aw` merges it or sends it back |

## 1. Find the agent id, the repositories and `aw`

- The agent id is the client you run in: Claude Code `claude`, Antigravity `agy`, Codex `codex`.
- The repositories are the faberline repositories with the GitHub topic `aw`:
  `gh api --paginate "orgs/faberline/repos?per_page=100" --jq '.[] | select(.archived | not) | select(.topics | index("aw")) | .full_name'`.
  Use them in name order. When the list is empty, stop and report `no aw repository`.
- `claude` and `codex` need no clone. Work from any directory.
- `agy` works in a dedicated clone: `~/faberlines/.aw-workers/agy/<name>`,
  where `<name>` is the repository name without the owner.
  When the clone is missing, create it:
  `gh repo clone <owner>/<name> ~/faberlines/.aw-workers/agy/<name>`.
- Never work in a person's checkout, such as `~/faberlines/<repo>`.

Use the Rust `aw`: `AW=~/.local/share/aw-rs/bin/aw`.
`$AW --version` must print `aw 0.6.1` or newer.
`~/.local/bin/aw` can be an old Python tool with the same name; do not use it.
When no binary passes the check, stop and report it.
Install command: `cargo install --git https://github.com/faberline/aw --root ~/.local/share/aw-rs aw`.

Give every `claim`, `report`, `release` and `status` command the agent and
the repository: `--agent <agent> --repo <owner>/<name>`. Below, `<who>`
means these two options.

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

## 2. Plan (`claude` only)

Do this in each repository before the claim. Use the REST calls above.

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

The claim is a comment on the issue that `aw` writes. It starts with
`<!-- aw:claim`. `aw` finds it again from the agent and the repository.

For each repository, in name order, until one claim succeeds:

1. `agy` only: `cd ~/faberlines/.aw-workers/agy/<name>`.
2. When `$AW status <who>` exits 0, a past run stopped early and still holds
   the claim. Continue it: read the JSON and go to step 4. `agy` keeps the
   files in the tree. Claim nothing more in this run.
3. `agy` only: when `$AW status <who>` exits 2 (no claim) and
   `git status --porcelain` is not empty, a past run stopped early. Keep its
   files: `git stash push -u -m "aw-worker leftover <UTC time>"`.
4. `$AW claim <who>`.
   - Exit 2: nothing to do here. Try the next repository.
   - Exit 1: keep stderr for the report. Try the next repository.
   - Exit 0: read the JSON (`repo`, `issue`, `stage`, `branch`, `pr`).
     Go to step 4 and claim nothing more in this run.

When no repository gives a claim, go to the final answer.

## 4. Do the stage

First read the issue and all its comments with the REST calls above.
Read the repository rules: `AGENTS.md`, `CLAUDE.md` or `CONTRIBUTING.md`.
Follow them for code and checks. For git and GitHub, `aw` and this skill win.
Put report files in a temporary directory, never in a clone.
Write the report file with the file tool or its own plain command first.
Then run `$AW report` as a plain command. Do not add `trap`, `rm`,
`set -e` or other cleanup to it; some clients block shell forms that they
cannot inspect. Leave the temporary file; the system removes it.

Without a clone, read code through the GitHub API:

- the file list: `gh api "repos/<repo>/git/trees/<ref>?recursive=1" --jq '.tree[].path'`;
- one file: `gh api -H 'Accept: application/vnd.github.raw' "repos/<repo>/contents/<path>?ref=<ref>"`;
- a search: `gh search code '<text>' --repo <repo>`.

`<ref>` is the default branch, or the pull request head branch in a review.

### spec

- Do not clone the repository. Read the code through the GitHub API.
- Write the spec: the goal; acceptance criteria that a test or command can
  prove; the tests to write, with one negative case that must fail without
  the change; the files to change; what is out of scope.
- When one pull request is not enough, report `blocked` and say how to split.
- `$AW report <who> ready -F <file>`. The spec becomes the signal comment.

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
- With a clean tree, run `$AW report <who> ready -F <file>` in the clone.
  The file becomes the pull request body after `Closes #<n>`: summary,
  changes, the checks you ran and their results.
- When you cannot finish in this run, commit the work in progress and run
  `$AW release <who>`. The next run continues on the same local branch.

### review

- Do not clone the repository. CI is the test evidence; do not run tests.
- Read the pull request, its checks and its diff with the REST calls above.
  The pull request JSON has `head.sha`, `mergeable` and `mergeable_state`.
  Read whole files at the head branch through the GitHub API when the diff
  is not enough.
- Checks are still running: `$AW release <who>` and stop. The next run tries again.
- A check failed or the pull request has a merge conflict:
  `$AW report <who> failed -F <file>` with the failed checks or the conflict.
- Compare the diff with the spec and its acceptance criteria. Check that the
  tests in the diff prove each criterion and that CI ran them.
- Every criterion is proven and all checks are green:
  `$AW report <who> passed -F <file>`. `aw` approves and squash-merges.
- Otherwise `$AW report <who> failed -F <file>` with findings per file.

## 5. After the report (`claude` only)

Do the label moves of step 2 again for the issue you reported.
Then the next agent does not wait one more hour.

## Rules

- `aw` does every push, pull request, review, merge and signal comment.
  Do not do them with `git` or `gh`.
- Only the planner (`claude`) changes labels and creates or closes issues.
- Work only on the issue that `aw claim` gives. One issue in each run.
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
- `blocked` is for a decision that only a person can make. Write the reason.

## Final answer

Use the user's language. Give:

- the agent id and the repositories you checked;
- the claim result for each repository;
- the work: issue, stage, outcome and pull request;
- the label moves (planner only);
- for each repository, the count of open issues with your `agent:` label;
- the health: `正常` (normal) or `異常` (abnormal) with the reason.
