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

The planner owns the labels. `aw` reads them and never changes them.

| Label | Meaning |
| --- | --- |
| `agent:<id>` | the agent that works on the issue, e.g. `agent:codex` |
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
until the planner moves the stage or the `agent:` label. A person's comment
never counts as a signal.

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

| Stage | Where it runs | `aw claim` checks out | `aw report` outcomes |
| --- | --- | --- | --- |
| spec | anywhere | nothing | `ready`, `blocked` |
| tdd | a clean clone of the repository | the head of the open pull request that closes the issue; else `<agent>/issue-<n>`, new from `origin/<default>` or continued from `origin` | `ready`, `blocked` |
| review | anywhere | nothing | `passed`, `failed`, `blocked` |

For tdd, `aw claim` refuses a dirty working tree (untracked files included).
Outside a clone of the repository it skips tdd issues and names them in its
exit-2 message.

`aw report` does the stage's GitHub side effect and edits the claim comment
into the signal comment. After tdd it switches back to the default branch
when the tree is clean:

- `tdd ready` needs a clean tree. It pushes the branch and opens its pull
  request (or updates the open one). The message becomes the body, after
  `Closes #<n>`.
- `review passed` approves and squash-merges with subject `<title> (#<pr>)`.
- `review failed` requests changes, or comments when GitHub refuses that.
- `blocked` and `failed` need a message: `-m TEXT`, or `-F FILE` (`-` is stdin).

A review is never claimed by the agent whose branch prefix authored the pull
request; `aw` posts a `blocked` signal for the planner instead. It does the
same when no open pull request closes the issue.

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

## Layout

The crate follows the [Rust layout standard](docs/standards/rust-layout.md):

- `src/domain/work/` — stages, signals, issues, pull requests, the claim, and
  the ports (`Tracker`, `Workspace`, `Clock`).
- `src/domain/run/` — run records, hook input, run summaries and the
  `RunLedger` port.
- `src/application/work/` — the `claim`, `report`, `release` and `status`
  use cases.
- `src/application/run/` — the `record` and `runs` use cases.
- `src/infrastructure/work/` — `gh` and `git` adapters and the clock.
- `src/infrastructure/run/` — the ledger file.
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
