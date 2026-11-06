# aw

`aw` is the issue worker CLI for faberline agents, written in Rust. One run
takes one issue, checks out the branch its stage works on, and reports back.
Every step that changes GitHub or git goes through `aw`; the agent only does
what the issue asks between `aw claim` and `aw report`.

Install it with `cargo install --git https://github.com/faberline/aw aw`.
It needs `git` and an authenticated `gh` on `PATH` and runs from the root of
a repository checkout.

## The issue queue

The planner owns the labels. `aw` reads them and never changes them.

| Label | Meaning |
| --- | --- |
| `agent:<id>` | the agent that works on the issue, e.g. `agent:codex` |
| `stage:spec`, `stage:tdd`, `stage:review` | the stage to work on; exactly one |
| `stage:done` | finished; never claimed |
| `p0` … `p3` | priority; `p0` first, no label last |
| `blocked` | never claimed |

An agent reports with a signal comment. Its first line is a hidden marker
that only `aw` writes:

```text
<!-- aw:signal stage=tdd agent=codex outcome=ready -->
```

After `agent` reports on a stage, `aw claim` skips the issue for that agent
until the planner moves the stage or the `agent:` label. A person's comment
never counts as a signal.

## One run

```bash
aw claim --agent codex        # or AW_AGENT=codex aw claim
# … do what the issue asks …
aw report ready -F summary.md
```

`aw claim` refuses a dirty working tree (untracked files included), takes the
first eligible issue (priority, then oldest) and prints the claim as JSON.
It exits 2 when there is nothing to do.

| Stage | `aw claim` checks out | `aw report` outcomes |
| --- | --- | --- |
| spec | the default branch, fast-forwarded | `ready`, `blocked` |
| tdd | `<agent>/issue-<n>`, new from `origin/<default>` or continued from `origin` | `ready`, `blocked` |
| review | the head of the open pull request that closes the issue, detached | `passed`, `failed`, `blocked` |

`aw report` does the stage's GitHub side effect, posts the signal comment,
switches back to the default branch when the tree is clean, and drops the
claim:

- `tdd ready` needs a clean tree. It pushes the branch and opens its pull
  request (or updates the open one). The message becomes the body, after
  `Closes #<n>`.
- `review passed` approves and squash-merges with subject `<title> (#<pr>)`.
- `review failed` requests changes, or comments when GitHub refuses that.
- `blocked` and `failed` need a message: `-m TEXT`, or `-F FILE` (`-` is stdin).

A review is never claimed by the agent whose branch prefix authored the pull
request; `aw` posts a `blocked` signal for the planner instead. It does the
same when no open pull request closes the issue.

`aw release` drops the claim without a signal, so the issue stays eligible.
`aw status` prints the active claim (exit 2 when none). The claim lives at
`<git dir>/aw/claim.json`, one per checkout; a claim older than three hours
is treated as a dead run and replaced.

Exit codes: 0 done, 2 nothing to do, 1 error. JSON goes to stdout; messages
and warnings go to stderr.

## Layout

The crate follows the [Rust layout standard](docs/standards/rust-layout.md):

- `src/domain/work/` — stages, signals, issues, pull requests, the claim, and
  the ports (`Tracker`, `Workspace`, `ClaimStore`, `Clock`).
- `src/application/work/` — the `claim`, `report`, `release` and `status`
  use cases.
- `src/infrastructure/work/` — `gh` and `git` adapters, the claim file and
  the clock.
- `src/interfaces/work/cli.rs` — the clap command line.
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
