# aw

`aw` is the issue worker CLI for faberline agents, written in Rust. One run
takes one issue, posts a claim on it, and reports back. Every step that
changes GitHub or git goes through `aw`; the agent only does what the issue
asks between `aw claim` and `aw report`.

Install it with `cargo install --git https://github.com/faberline/aw aw`.
It needs an authenticated `gh` on `PATH`. Only the tdd stage touches code: it
needs `git` and runs from the root of a clean clone of the repository. The
spec and review stages use only GitHub and run anywhere.

## The issue queue

The planner triages issues and sets the first labels. After that, `aw report`
moves the `stage:` label (see below).

| Label | Meaning |
| --- | --- |
| `agent:<id>` | the agent that works on the issue, e.g. `agent:claude` |
| `stage:spec`, `stage:tdd`, `stage:review` | the stage to work on; exactly one |
| `stage:done` | finished; never claimed |
| `p0` … `p3` | priority; `p0` first, no label last |
| `blocked` | never claimed |

`aw claim` posts a claim comment on the issue. Its first line is a hidden
marker that only `aw` writes:

```text
<!-- aw:claim stage=tdd agent=agy branch=agy/issue-7 pr=- at=1800000000 -->
```

`aw report` edits that comment into the signal comment:

```text
<!-- aw:signal stage=tdd agent=agy outcome=ready -->
```

After `agent` reports on a stage, `aw claim` skips the issue for that agent
until the stage or the `agent:` label changes. A person's comment never counts
as a signal.

## One run

```bash
aw claim --agent codex --repo faberline/core   # or AW_AGENT, AW_REPO
# … do what the issue asks …
aw report --agent codex --repo faberline/core ready -F summary.md
```

`claim`, `report`, `release` and `status` take `--agent` (or `AW_AGENT`) and
`--repo owner/name` (or `AW_REPO`). Without `--repo`, `aw` uses the
repository of the current checkout.

`aw claim` takes the first eligible issue (priority, then oldest), posts the
claim comment and prints the claim as JSON. It exits 2 when there is nothing
to do. An agent holds at most one claim in a repository.
`aw claim --skip <issue>` passes over that issue; repeat it for more. A
worker uses it for a review whose checks are still running.

An issue with a `needs:<thing>` label is work that only some machines can
do, for example `needs:mac` for a model training run on the local Mac.
`aw claim` passes over it unless the worker says it has the thing:
`aw claim --has mac` (or `AW_HAS=mac`; separate more with commas). A cloud
worker runs without `--has`, so it leaves that issue to a Mac.

| Stage | Where it runs | `aw claim` checks out | `aw report` outcomes |
| --- | --- | --- | --- |
| spec | anywhere | nothing | `ready`, `blocked` |
| tdd | a clean clone of the repository | the head of the open pull request that closes the issue; else `<agent>/issue-<n>`, new from `origin/<default>` or continued from `origin` | `ready`, `respec`, `blocked` |
| review | anywhere | nothing | `passed`, `failed`, `closed`, `blocked` |

For tdd, `aw claim` refuses a dirty working tree (untracked files included).
Outside a clone of the repository it skips tdd issues and names them in its
exit-2 message.

`aw report` does the stage's GitHub side effect and edits the claim comment
into the signal comment. After tdd it switches back to the default branch
when the tree is clean:

- `tdd ready` needs a clean tree. It pushes the branch and opens its pull
  request (or updates the open one). The message becomes the body, after
  `Closes #<n>`.
- `review passed` squash-merges, without an approval, with subject `<title> (#<pr>)`.
- `review failed` requests changes, or comments when GitHub refuses that.
- `review closed` comments the message on the pull request, closes it
  without a merge and deletes its branch. The next tdd run starts again
  from the default branch. Use it when rework cannot save the pull request.
- `blocked`, `respec`, `failed` and `closed` need a message: `-m TEXT`, or `-F FILE`
  (`-` is stdin).

Then `aw report` moves the `stage:` label, so the next `aw claim` can take the
issue at once. The `agent:` label stays. A label failure is a warning.

| Report | Labels after it |
| --- | --- |
| spec `ready` | `stage:tdd` |
| tdd `ready` | `stage:review` |
| review `failed` or `closed` | `stage:tdd` |
| review `passed` | `stage:done` |
| tdd `respec` | `stage:spec`, no `blocked` |
| `blocked` | the same stage, plus `blocked` |

The same agent may review its own pull request in a later run. When no open
pull request closes the issue, `aw` posts a `blocked` signal for review.

`aw release` deletes the claim comment without a signal, so the issue stays
eligible. `aw status` prints the agent's claim (exit 2 when none). `aw` finds
the claim in the comments of the open issues with the agent's `agent:` label.
A claim older than three hours is treated as a dead run: the next `aw claim`
deletes it and claims again.

Exit codes: 0 done, 2 nothing to do, 1 error. JSON goes to stdout; messages
and warnings go to stderr.

## Worker runs

Each agent client runs `aw hook` from its own hooks. The command writes one
line per worker run event to the run ledger, `<workers dir>/runs.jsonl`. The
workers directory is `$AW_WORKERS_DIR`, or `~/faberlines/.aw-workers`.

```bash
aw hook claude start   # Claude Code UserPromptSubmit
aw hook claude end     # Claude Code Stop
aw hook codex start    # Codex UserPromptSubmit
aw hook codex end      # Codex Stop
aw hook agy start      # Antigravity SessionStart
aw hook agy end        # Antigravity Stop
aw runs -n 20          # the last 20 runs as JSON, oldest first
```

`aw hook` reads the hook JSON from stdin. It records only worker runs:

- a session whose working directory is a clone under the workers directory;
- a start whose prompt is a worker automation prompt, such as a Claude
  scheduled task named `aw-worker-…`;
- an end of a session that already has a recorded start.

`aw hook` always exits 0, so it never blocks a prompt. Errors go to stderr.
It prints nothing for Claude Code and Codex, because their prompt hooks add
stdout to the model context. For Antigravity it prints the JSON reply.

`aw runs` groups the ledger by session. A run is `ended` when its last
record is an end, else `open`. An old `open` run crashed or hangs. No new run
for more than one hour means the automation did not start.

## CI check

`aw ci check` checks one repository against the
[CI stages standard](docs/standards/ci.md) and prints a JSON report.

```bash
aw ci check                              # the current checkout and its origin's ruleset
aw ci check --dir ../core --no-ruleset   # local files only
aw ci check --repo faberline/core        # the default branch on GitHub
```

It reads `.github/workflows/ci.yml`, the `[[standards.ci.exception]]` entries
of `aw.toml` and the required status checks of the default-branch ruleset.
Each check `CI1`–`CI9` is `pass`, `fail`, `excepted` or `skipped`. It exits 1
when a check fails.

## Milestone release gate

Three commands decide when a milestone gets a release tag. They read one
repository through `gh`. `--repo` (or `AW_REPO`) names it; the default is the
`origin` of the current checkout.

```bash
aw release-status --milestone 8                       # read only; prints the assessment
aw release-advance --milestone 8 --expected <fingerprint>   # the one write: create the tag
aw release-preflight --tag v0.5.0-rc.1 --commit <sha>   # inside the tag run; read only
```

The repository keeps two reviewed files on its default branch:

- `.github/aw-release.json` — the contract. For each milestone: the version,
  the hash of the milestone goal, the acceptance goals with their issues and
  checks, and the publication. One version belongs to one milestone only.
- `.github/aw-release-gates.json` — the policy. For each full-test workflow:
  the path, the workflow id, the git blob hash of the reviewed definition,
  and each job name with its kind (`unit`, `functional_e2e`, `performance` or
  `policy`). The three test kinds must all appear.

`release-status` reads both files at one exact commit of the default branch
and checks each git blob hash. It prints `AW_RELEASE_ASSESSMENT_V1` with a
`state`, an `action` and a `fingerprint` of every input.

| State | Meaning | Action |
|---|---|---|
| `work` | open issues, or pull requests that are not on the default branch | none |
| `ready_rc` | all work is landed and no candidate holds it | `tag` (the next `-rc.N`) |
| `testing` | the latest tag has no complete full-test result | none |
| `repair` | a full-test job of the latest tag failed | `failure_issue` |
| `ready_final` | the latest candidate is green | `tag` (the final tag, on the candidate commit) |
| `ready_publish` | the final tag is green | `publish` |
| `ready_close` | the release is published | `close` |
| `complete` | the milestone is closed | none |
| `hold` | the milestone is closed without a verified final release | none |

A tag passes only when the newest `push` run of that tag and commit, in its
latest attempt, has exactly the jobs of the policy and each job is
`completed` with `success`. A missing, skipped, cancelled, extra, stale or
unknown job does not pass. A run that is not the reviewed workflow at the
reviewed definition stops the command.

Only an open pull request holds the milestone at `work`. A pull request
that was closed and never merged is dropped work; it does not hold the
milestone and it does not prove a goal issue.

A green candidate is good for the final tag only when its full tests ran
every workflow and every job of the policy on the default branch. When the
policy gains a workflow or a job after the candidate, the state is
`ready_rc` again: the release needs a new candidate.

`release-advance` reads everything again. It stops when the fingerprint is
not `--expected`. For the action `tag` it checks that the reviewed workflow
at the tag commit starts on this tag, reads everything one more time, creates
the tag and reads it back. It never moves or deletes a tag. For every other
action it prints the assessment and exits 2 without a write.

`release-preflight` is the first job of a tag run. It proves that the tag is
on `--commit`, that the commit holds the approved work, and that the
milestone waits for this tag. It prints `AW_RELEASE_PREFLIGHT_V1`.

## Layout

The crate follows the [Rust layout standard](docs/standards/rust-layout.md):

- `src/domain/work/` — stages, signals, issues, pull requests, the claim, and
  the ports (`Tracker`, `Workspace`, `Clock`).
- `src/domain/ci/` — the CI stages checks and their ports (`CiFiles`,
  `BranchRules`).
- `src/domain/run/` — run records, hook input, run summaries and the
  `RunLedger` port.
- `src/application/work/` — the `claim`, `report`, `release` and `status`
  use cases.
- `src/domain/release/` — the release contract, the gates policy, the tag
  rules, the state decision and the ports (`Provider`, `WorkflowParser`).
- `src/application/release/` — the `release-status`, `release-advance` and
  `release-preflight` use cases.
- `src/infrastructure/release.rs` — the `gh` adapter and the workflow YAML
  reader of the release gate.
- `src/application/run/` — the `record` and `runs` use cases.
- `src/application/ci.rs` — the `ci check` use case.
- `src/infrastructure/work/` — `gh` and `git` adapters and the clock.
- `src/infrastructure/run/` — the ledger file.
- `src/infrastructure/ci/` — `ci.yml` and `aw.toml` parsing, local and GitHub
  file readers, and the ruleset reader.
- `src/interfaces/work/cli.rs` — the clap command line.
- `src/interfaces/run/` — the `hook` and `runs` commands.
- `src/app.rs` — the composition root; `src/main.rs` only calls it.
- `tests/it/` — use cases against in-memory ports, and the binary.

Other parts of the repository:

- `docs/standards/` — the shared standards for every faberline repository
  ([index](docs/standards/README.md)).
- `.claude-plugin/marketplace.json` and `plugins/faberline/` — the Claude
  Code plugin with the shared git and pull-request skills
  ([how](plugins/faberline/README.md)).
- `infra/gke-test/` and `scripts/gke-test/` — the shared GKE test cluster
  ([standard](docs/standards/test-cluster.md)).

## Development

```bash
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test
```
