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
| `ci-fix` | repairs a red required check on the default branch; claimed after `p0` and before `bar`, so the repair does not wait behind issues that cannot pass until it merges |
| `bar` | inside the internal-use bar of the repository; claimed after `p0` and `ci-fix`, before `p1` and the milestone order |
| `blocked` | never claimed |

`aw claim` posts a claim comment on the issue. Its first line is a hidden
marker that only `aw` writes:

```text
<!-- aw:claim stage=tdd agent=claude branch=claude/issue-7 pr=- at=1800000000 -->
```

`aw report` edits that comment into the signal comment:

```text
<!-- aw:signal stage=tdd agent=claude outcome=ready -->
```

After `agent` reports on a stage, `aw claim` skips the issue for that agent
until the stage or the `agent:` label changes. A person's comment never counts
as a signal.

## One run

```bash
aw claim --agent claude --repo faberline/core   # or AW_AGENT, AW_REPO
# … do what the issue asks …
aw report --agent claude --repo faberline/core ready -F summary.md
```

`claim`, `report`, `release` and `status` take `--agent` (or `AW_AGENT`) and
`--repo owner/name` (or `AW_REPO`). Without `--repo`, `aw` uses the
repository of the current checkout.

`aw claim` takes the first eligible issue (priority, then oldest; with
`--stage tdd`, an issue that review sent back, which has an open pull request,
comes before new work of the same priority, the longest wait first), posts the
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
| spec | anywhere | nothing | `ready`, `split`, `blocked` |
| tdd | a clean clone of the repository | the head of the open pull request that closes the issue; else `<agent>/issue-<n>`, new from `origin/<default>` or continued from `origin` | `ready`, `respec`, `blocked` |
| review | anywhere | nothing | `passed`, `failed`, `closed`, `blocked` |

For tdd, `aw claim` refuses a dirty working tree (untracked files included).
Outside a clone of the repository it skips tdd issues and names them in its
exit-2 message.

The work branch on `origin` is the truth. A local work branch that only lags
fast-forwards. A local work branch with commits that `origin` lacks is kept
under `refs/aw/backup/<branch>-<time>` and reset to `origin/<branch>`; `aw
claim` says so on stderr. If the tree turns dirty first, the issue gets a
blocked signal that names `git branch -f <branch> origin/<branch>`.

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
- `spec split` makes the child issues of an issue that is too large for one
  pull request, links them in the signal comment and closes the issue as
  not planned. See "Split" below.
- `blocked`, `respec`, `split`, `failed` and `closed` need a message: `-m TEXT`,
  or `-F FILE` (`-` is stdin).

Then `aw report` moves the `stage:` label, so the next `aw claim` can take the
issue at once. The `agent:` label stays. A label failure is a warning.

| Report | Labels after it |
| --- | --- |
| spec `ready` | `stage:tdd` |
| tdd `ready` | `stage:review` |
| review `failed` or `closed` | `stage:tdd` |
| review `passed` | `stage:done` |
| tdd `respec` | `stage:spec`, no `blocked` |
| spec `split` | no change; the issue is closed |
| `blocked` | the same stage, plus `blocked` |

`blocked` is for a case that needs a person. An incomplete spec is not one:
tdd reports `respec` and the message lists the missing points. When a tdd
`blocked` message says on its first line that the spec is the cause (it
starts with `spec` or `the spec`, after an optional `blocked:`, `tdd:` or
`tdd blocked:`, and has `unclear`, `incomplete`, `not complete`, `missing`,
`lacks` or `does not meet`), `aw` reports `respec` instead and prints a
warning. No routine takes an issue with `stage:tdd` and `blocked`.

### Split

`aw report <who> split -F <file>` is for a spec run that finds the issue too
large for one pull request, when the split needs no decision of a person.
The message names the children:

```text
Too large: two crates and a migration.

## Child: Add the port
The trait and its tests.

## Child: Add the adapter
The `gh` adapter. Waits for: Add the port.
```

- The text before the first `## Child:` line is the reason.
- A split has 2 to 5 children. Each has a title of its own and a text.
- Each child gets the milestone and the priority label (`p0` to `p3`) of the
  issue, `agent:<agent>` and `stage:spec`. Its body starts with
  `<!-- aw:child parent=<n> -->` and `Part of #<n>`.
- The signal comment on the issue lists the children. `aw` then closes the
  issue as not planned. When it cannot close it, it adds `blocked` and
  prints a warning.
- A child (depth 1) may split once more into grandchildren (depth 2). The
  grandchildren carry `<!-- aw:child parent=<child> -->`, the milestone and
  the priority label of the child, and the child closes as replaced. A
  grandchild is not split again: `aw` refuses the report and the claim stays.
  Report `blocked` and name the decision.
- A bad message or a failure on the first child changes nothing; the claim
  stays. When a later child fails, `aw` reports `blocked` and the signal
  lists the children that exist and those that do not.
- The JSON output has `children`, the numbers of the issues.

When the split needs a decision (two designs, a change of an acceptance
criterion, money), report `blocked` and name the decision in the first line.

The same agent may review its own pull request in a later run. When no open
pull request closes the issue, `aw` posts a `blocked` signal for review.

`aw release` deletes the claim comment without a signal, so the issue stays
eligible. `aw status` prints the agent's claim (exit 2 when none). `aw` finds
the claim in the comments of the open issues with the agent's `agent:` label.
A claim older than three hours is treated as a dead run: the next `aw claim`
deletes it and claims again.

To find a claim, `aw` reads one list: the comments of the repository from the
last six hours. It does not read each open issue. `aw status` and `aw claim`
do not see a claim older than six hours; `aw claim` deletes such a claim when
it takes that issue. `aw report` and `aw release` still find a tdd or review
claim of that age: when the list has no claim, they read each open issue.
That fallback could meet the dead claim of another run (an old claim on
another issue), so it accepts a claim only when its branch is the current
branch of the checkout of the repository. A spec claim names the default
branch and a spec run never changes the checkout, so the fallback refuses
every old spec claim: `aw report` and `aw release` say "holds no claim".

Exit codes: 0 done, 2 nothing to do, 1 error. JSON goes to stdout; messages
and warnings go to stderr.

## Worker runs

Claude Code runs `aw hook` from its hooks. The command writes one
line per worker run event to the run ledger,
`~/.local/share/aw-rs/runs.jsonl`. A missing ledger is an empty ledger.

`AW_WORKERS_DIR` is an optional override. When it names a directory, the
ledger is `$AW_WORKERS_DIR/runs.jsonl`, and that directory is the workers
directory. There is no default workers directory.

```bash
aw hook claude start   # Claude Code UserPromptSubmit
aw hook claude end     # Claude Code Stop
aw runs -n 20          # the last 20 runs as JSON, oldest first
```

`aw hook` reads the hook JSON from stdin. It records only worker runs:

- a session whose working directory is a clone under the workers directory,
  when `AW_WORKERS_DIR` is set;
- a start whose prompt is a worker automation prompt, such as a Claude
  scheduled task named `aw-worker-…`;
- an end of a session that already has a recorded start.

`aw hook` always exits 0, so it never blocks a prompt. Errors go to stderr.
It prints nothing on stdout, because a Claude Code prompt hook adds stdout
to the model context. `claude` is the only client.

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
Each check `CI1`–`CI11` is `pass`, `fail`, `excepted` or `skipped`. It exits 1
when a check fails.

## Standards check

`aw standards check` checks local checkouts against the
[core reuse standard](docs/standards/core-reuse.md) (S4, rules `CR0`–`CR8`).
It reads only tracked `Cargo.toml` and `build.rs` files.

```bash
aw standards check                                    # the current checkout; core in ../core
aw standards check --core ~/faberlines/core --repo ~/faberlines/keep
aw standards check --repo ../keep --repo ../tape --format json
```

It exits 0 for a pass, 1 for a finding and 2 for a usage error.
`[[standards.core_reuse.exception]]` entries of `aw.toml` waive one check.

## Meta check

`aw meta check` checks the `README.md` of a project against the README
contract: the required headings, the capability index and details, the
sources, the gates and the links (rules `R1`–`R12`). It is the Rust port of
the defer `scripts/meta/readme_contract.py`. It only reads.

```bash
aw meta check                  # README.md of the current checkout
aw meta check apps/x crates/y  # folders or README.md files, from the top of the checkout
aw meta check --dir ../defer --format json
```

It exits 0 for a pass, 1 for a finding and 2 for a usage error. A gate shape
that the check cannot resolve is a warning. `aw.toml` sets the repository
values under `[meta]`: `source_prefixes` (default `apps`, `libs`, `crates`,
`core`, `models`), `root_source` (default `[project] name`, the token for the
repository root) and `core_source` (the `Cargo.lock` source prefix of the
`core` crates).

## Security check

`aw security check` checks the tracked files of a checkout against the
[security standard](docs/standards/security.md): advisories in CI and on a
schedule (SEC1, SEC2), images pinned by digest (SEC3) and GitHub Actions pinned
by SHA with least-privilege `permissions` (SEC5). SEC4 and SEC6 print as
`manual`. Every finding is a warning; the exit code is 0 unless `--strict` is
given. `--format json` prints a `security-report/1` document. Exceptions
(`rule`, `reason`, `expires`) go in `aw.toml`. The reusable workflow
`.github/workflows/advisories.yml` runs the advisory check every day and opens
one issue for each repository.

```bash
aw security check --dir ../sift
```

## Metadoc check

`aw metadoc check` checks the core documents of a project folder: the
README (rules `R1`–`R12`), the `STATUS.md` (rules `S1`–`S7`: one flat support
matrix, a gate for each supported surface, a ROADMAP link for each limit), the
`ROADMAP.md` (rules `M1`–`M6`: flat H3 items under three horizons with
fixed fields) and the rules that tie them (`P1` each core document exists,
`P2` the README links STATUS and ROADMAP, `P3` a STATUS link names a known
ROADMAP item, `P4` a supported surface is not future work, `P5` the STATUS and
ROADMAP titles follow the README title, `P6` a guide and its README link go
together, `P7` no reversed Lumen claim in `apps/lumen`). The supporting guides
(`docs/protocol.md`, `clients/README.md`, `docs/indexing.md`,
`docs/querying.md`, `docs/gke.md`, `docs/client-integration.md` and each linked
`docs/migration-*.md`) have fixed H2 headings and one fixed table (rules
`D1`–`D4`). It is the Rust port of the defer
`scripts/meta/project_docs_contract.py`. It only reads.

```bash
aw metadoc check                 # the project folder at the top of the checkout
aw metadoc check apps/x          # a project folder, or one of its core documents
aw metadoc check --dir ../defer --format json
aw metadoc prompt apps/x         # the clean-reader task for a project that passes
```

`aw metadoc prompt` runs the same check on one project folder. When it passes,
it prints the clean-reader task: a prompt that lists the adopted documents
with their absolute paths and SHA-256 hashes, and asks a fresh reader to
restate the product, the support state and the future work as JSON. When the
check fails, it prints the findings and exits 1; no prompt is made.

It exits 0 for a pass, 1 for a finding and 2 for a usage error. It reads the
`[meta]` table of `aw.toml` like `aw meta check`. The JSON document has the
schema `metadoc-report/1`.

## Desk patrol

`aw desk patrol` is the checklist for a desk round. It reads the pipeline
repositories through `gh` and changes nothing. It prints a Markdown checklist:
one section for each repository and one for aw. Each repository section starts
with a counts line (open, spec, tdd, review, blocked, open PRs, last merge).
Each item is `- [ ] <what to look at>: <why> (<check>)`.

```bash
aw desk patrol                       # every repository of [desk] in aw.toml
aw desk patrol --repo sift --repo core
aw desk patrol --since 120 --stale-hours 12 --json
```

The repositories, in order, are `[desk] repos` of `aw.toml` (`org`, `repos`,
`aw`). `--repo` (repeatable) replaces that list for one run. Outside the aw
checkout (no `aw.toml` with a `[desk]` table in the folder or above it) the
built-in list is used. The first line counts the items for each check, or says
`nothing to look at`. `--only <check>` (repeatable) keeps the items of the
named checks.

Checks (the repositories are read in parallel; aw is one section even when the list names it):

- `main-red`: the newest completed run of each workflow file on main failed. Only
  a push or schedule run counts; a run started by hand (`workflow_dispatch`) never does.
- `blocked-new` (`--since` minutes, default 70).
- `blocked-no-reason`: no `Waits for:` line, no comment that names a wait, a
  decision or an owner, no hold label. A newest comment that is an `aw:signal ... outcome=blocked`
  with text after the marker is a reason (`comment`). The reasons found are counted in the
  counts line: `blocked 12 (waits-for 7, comment 4, label 1, no reason 0)`; `hold N` appears when a
  comment holds an issue for a person. A wait with one more human condition
  (`#259 is merged and a second released version exists`) is a hold too.
- `stuck-stage` (`--stale-hours`, default 6): one line for each stage (count and
  the three oldest). A `stage:review` issue and an `expedite` issue show one by
  one.
- `pr-stuck`: a PR whose issue is blocked or closed is one `old PRs` line.
- `queue-empty`.
- `waits-for-done`: every wait closed as completed. `waits-for-split`: a wait
  closed as not planned (split); name the children. A wait is read from the
  newest comment that says `waits for #A, #B` or `blocked until #A`, else from
  the `Waits for:` line; text in brackets (`(child A2 of #401)`) names no
  blocker. `aw#195` and `owner/repo#7` name an issue of that repository. When
  the newest such comment gives a hold reason with no issue number (`stays
  blocked`, `paid`, `needs the owner`, `out of <milestone>`), the issue is a
  `hold` in the counts line, not a `waits-for-done`. In `core#600, #601 and #602` the
  bare numbers are in `core`. A reference that cannot be read is one
  `waits-unknown` item for its issue; the repository is still read.
- `blocker-done-elsewhere`: a blocked issue with no wait phrase whose newest comment
  names issues of other repositories that are all closed as completed (or split).
- `routine-quiet`, and in the aw section `decision-open` and `pipeline-open`.

It exits 0 when every call worked, even if it lists items. It exits 1 when a
repository cannot be read; that repository is listed as `unknown`, never as
clean. The list is a start: the desk still reads each item.

## Milestone release gate

Three commands decide when a milestone gets a release tag. They read one
repository through `gh`. `--repo` (or `AW_REPO`) names it; the default is the
`origin` of the current checkout.

```bash
aw release-status --milestone 8                       # read only; prints the assessment
aw release-advance --milestone 8 --expected <fingerprint>   # the one write: create the tag
aw release-preflight --tag v0.5.0-rc.1 --commit <sha>   # inside the tag run; read only
aw release-contract --app <name>                        # static check of an app's release workflows
aw release-receipt --app <name> --backend gke-acceptance|gcp [--production-acceptance <file>] ...   # make the GKE release receipt; see docs/standards/release-tooling.md
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
- `src/domain/meta/`, `src/application/meta.rs`, `src/infrastructure/meta.rs` — the
  README contract of `meta check`, its use case and the checkout adapter.
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
