# Test scopes

Guideline. State: **draft, 2026-10-17**. It becomes a standard when
`aw tests select` and the map checks in [Planned checks](#planned-checks)
ship; see the [definition](README.md#what-a-standard-is). Until then, a
repository follows it when it changes its CI.

The idea: **what changed** decides which tests run, not the kind of test. There
are three scopes. Each one has a fixed place and a fixed moment:

| Scope | Which changes | Where | When |
|---|---|---|---|
| 1. Change | This change: the commits and files not yet pushed | Your machine | Before every push |
| 2. Branch | Every change of the branch against `main` | The cloud: GitHub runner and the [shared GKE cluster](test-cluster.md) | Every pull request run |
| 3. Full | Everything: every test, whatever changed | The cloud | Every merge to `main`; with the whole performance matrix only on a release candidate |

**Performance tests run only in the cloud.** Scope 1 never runs them, and a
number from your machine is never evidence.

A map in the repository's `aw.toml` turns changed paths into tests. The same
map serves scopes 1 and 2. Scope 3 does not read it.

## Why scope and not kind

Kinds do not split well by cost. In `lumen`, an integration test in
`tests/it/` starts the server binary on the runner, and all 659 of them take
about 3 minutes after the build. The real costs are the builds (about 11
minutes for each feature set), the cluster, and the performance matrix (about
80 minutes for each cell). A scope says how much to run; the kind of test only says where it can
run ([rust-tests.md](rust-tests.md#four-kinds-of-test)).

## The three scopes

### 1. Change: on your machine, before push

- **Changed paths.** `git diff --name-status @{push}`, plus the working tree
  and untracked files. A branch with no push yet compares with
  `git merge-base origin/main HEAD`, like scope 2.
- **Tests.** The `lib` and `it` selectors that the map gives for those paths.
  An `e2e` selector may run against a server on your machine
  (`E2E_ENDPOINT=http://127.0.0.1:<port>`), but it is not required here.
  `perf` selectors never run here.
- **Rule.** Push only when scope 1 passed on what you push.
- **Who enforces it.** The agent's hook, not git. Before the agent runs
  `git push`, its tool hook runs scope 1 and blocks the push when a test
  fails: a `PreToolUse` hook on `Bash` in Claude Code, and the same kind of
  hook in Codex. The hook config is in the repository, so every agent session
  gets it. Do not use a git `pre-push` hook: it runs for every person and
  tool, and `git push --no-verify` skips it anyway. A person who pushes by
  hand runs scope 1 by hand; scope 2 catches what it misses.

### 2. Branch: in the cloud, on every pull request run

- **Changed paths.** `git diff --name-status $(git merge-base origin/main HEAD) HEAD`.
  It covers every commit of the branch, also the ones that another person or
  agent pushed.
- **Tests.** Every selector that the map gives for those paths: `lib`, `it`,
  `e2e`, and `perf`. `e2e` and `perf` run on the shared cluster.
- **Rule.** A pull request merges only when scope 2 passed on its last
  commit.

### 3. Full: in the cloud, on every merge to `main`

- **Tests.** All of `lib` and `it` in every feature set, and every e2e suite.
- **The performance matrix runs only on a release candidate.** A release
  candidate runs scope 3 on its commit and adds every `perf` cell. A merge to
  `main` runs no `perf` cell: the whole matrix takes hours on the shared
  cluster, and the cluster budget is shared by every repository.
- **When.** On `push` to `main`, and in the release candidate on its commit.
- **Queue.** Full runs of one repository wait for each other. Put them in one
  concurrency group without `cancel-in-progress`: GitHub keeps one waiting run
  and cancels an older waiting one, so a burst of merges runs the full scope
  on the newest `main`, not once for each merge.
- **Rule.** A red full run on `main` is a broken `main`. Fix it before the next
  release candidate.

## The map

The map is in `aw.toml` at the repository root:

```toml
[tests.scope]
# A change to one of these runs the full scope.
full = [
  "Cargo.toml",
  "Cargo.lock",
  "build.rs",
  "rust-toolchain.toml",
  "aw.toml",
  ".github/workflows/**",
]

[tests.scope.features]
# Name = cargo flags. "default" is plain `cargo test`.
default = []
operator = ["--features", "operator"]
raft-wal = ["--features", "raft-wal"]

[[tests.scope.rule]]
paths = ["src/index/**", "tests/it/segment_*.rs"]
tests = ["lib index::", "it segment_", "e2e smoke"]

[[tests.scope.rule]]
paths = ["src/engine/checkpoint*/**"]
features = ["default", "raft-wal"]
tests = ["lib engine::checkpoint", "it aof_", "e2e failover", "perf index-1000-hnsw-cpu"]

[[tests.scope.rule]]
paths = ["src/operator/**"]
features = ["operator"]
tests = ["lib operator::", "it operator_"]

[[tests.scope.rule]]
# Documents select no test.
paths = ["docs/**", "*.md"]
tests = []
```

### Paths

- `paths` are repository-relative globs. `*` stays in one folder; `**`
  crosses folders.
- A changed path selects the tests of every rule whose `paths` match it. The
  tests of one run are the union over all changed paths.
- A renamed file counts as two paths: the old one and the new one. A deleted
  file counts as its old path.
- A changed path that matches `full`, or that matches no rule, selects the
  full scope of that run: in scope 1 all of `lib` and `it` in every feature
  set, in scope 2 everything that scope 3 runs. A repository with no map therefore runs the full scope every
  time, which is safe and slow.
- `tests = []` selects nothing. Use it on purpose, for example for documents.
  An empty list is the only way to say "no test"; a missing rule means "all
  tests".

### Selectors

Each entry of `tests` is `<target> <filter>`:

| Target | Runs | Filter |
|---|---|---|
| `lib` | `cargo test --lib -- <filter>` | A libtest name filter, for example `index::` |
| `it` | `cargo test --test it -- <filter>` (tests without `#[ignore]`) | A libtest name filter, for example `segment_` |
| `e2e` | The suite's test Job on the cluster, filter `e2e::<suite>::` | The suite name, for example `smoke` |
| `perf` | One cell call on the cluster | The cell name, for example `index-1000-hnsw-cpu` |

- `<target> *` selects every test of that target, for example `e2e *`.
- Filters of one target join into one command. libtest takes several filters
  and runs a test that matches any of them:
  `cargo test --test it -- segment_ aof_`.
- A filter is a substring of the test name, as libtest matches it. Keep it
  short and stable: a module path (`index::`) is better than one test name.

### Feature sets

Each feature set is one more build, so a map names the feature sets that a
change needs:

- `[tests.scope.features]` names each feature set and its cargo flags.
  `default` is plain `cargo test`.
- A rule's `features` lists the feature sets its `lib` and `it` selectors run
  in. Each selector runs once in each listed set. Without `features`, a rule
  runs in `default` only.
- `features` does not apply to `e2e` and `perf`. They run against the image,
  and the image has one fixed feature set.
- A run builds each feature set at most once, for all of its selectors.

## The CI shape

| Event | Scope | Changed paths |
|---|---|---|
| `pull_request` | 2. Branch | Merge base with `main` to the head commit |
| `push` to `main` | 3. Full, without `perf` | Not read |
| Release candidate | 3. Full, with every `perf` cell | Not read |

A pull request workflow starts with one small `select` job. It reads the
changed paths and the map, and gives the selectors as outputs. The test jobs
read those outputs: a `cargo test` job for `lib` and `it`, and the
[test cluster](test-cluster.md#the-test-job) matrix for `e2e` suites and
`perf` cells. A job with no selector is skipped, and a skipped job counts as
passed.

The cargo build happens once for each feature set of a run. The test client image of
[rust-tests.md](rust-tests.md#the-test-client-image) holds the same test
binary for `e2e` and `perf`.

## Planned checks

`aw tests select --scope change|branch|full` will print the selectors as
JSON for one checkout, so your machine and CI use one rule. The agent hooks
of scope 1 call it too.

`aw standards check` will report:

- A glob in `paths` or `full` that matches no file.
- A `lib` or `it` filter that matches no test in `cargo test -- --list`. A
  typo matches nothing, and libtest exits 0 then.
- An `e2e` suite without its test Job folder, or a `perf` cell that the
  repository's matrix does not list.
- A rule's `features` entry that `[tests.scope.features]` does not name.
- Files that match no rule, as a count. Each one runs the full scope, so the
  count shows how much the map saves.

A repository needs no exception file: without a map it runs the full scope,
and that follows this guideline.

## Measured state, 2026-10-17

No repository has a map yet. `lumen` runs the full scope on every candidate.
Its default cargo gate takes 16 minutes: 11 minutes of build, 37 seconds for
1,176 `lib` tests, and 3 minutes for 659 `it` tests. Its other feature-set
gates take 10 to 21 minutes each, mostly build. Its 16-cell performance
matrix takes about 21 hours on the shared cluster, one cell at a time.
`lumen` moves first, as part of its one-pipeline work.
