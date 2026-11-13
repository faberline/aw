# Test coverage

Standard S8. State: **active since 2026-11-10** in `aw`; the other
repositories adopt it one at a time (see [Rollout](#rollout)). The checker is
the coverage action, `.github/actions/coverage` in `aw`; see the
[definition](README.md#what-a-standard-is).

## Why

Agents write almost all of the code. A test is the only proof that a line
does what the spec says. Coverage shows the lines that no test runs.

- **Target: 100 % line coverage** in every Rust repository.
- **Ratchet now.** No repository reaches 100 % today, so there is no fixed
  threshold. Each repository measures its value once. After that, a change
  cannot lower it, and a change that raises it records the new value.
- **Features first.** Tests for old code come as `p2` issues, one file at a
  time (see [The work](#the-work)). `p0` and `p1` work goes first.

## What is measured

- Line coverage from `cargo llvm-cov` over the workspace tests that
  `cargo test` runs: unit tests and the `tests/` integration tests.
- `cargo llvm-cov` does not count the files under `tests/`, `examples/` and
  `benches/`, or the dependencies.
- e2e suites on GKE do not count yet. A file that only e2e can run is an
  [exclude](#excludes) with that reason.
- The value is the covered lines divided by all lines of the counted files,
  times 100, rounded down to two decimals.

## The baseline

The baseline is in the repository's `aw.toml`:

```toml
[standards.coverage]
line = 81.24   # the measured value when it was last raised
target = 100   # fixed
```

## The CI job

The job is a `test-*` job of the [CI stages](ci.md). It runs the same tests
as a `test-workspace` job, so it can replace that job. It does the
repository's own setup first, as any `test-*` job does.

```yaml
  test-coverage:
    name: "test: workspace, coverage"
    needs: [lint]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: dtolnay/rust-toolchain@4360b52568e2003a75bf9bc1d59f33a8e3fc893c # stable
        with:
          toolchain: 1.99.1
          components: llvm-tools-preview
      - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6 # v2.9.2
      - uses: taiki-e/install-action@5488ac5a951645ce95062214902ac4b6bc1a1037 # v2.87.32
        with:
          tool: cargo-llvm-cov@0.9.1
      - run: cargo llvm-cov --workspace --locked --json --summary-only --output-path target/coverage.json
      - uses: faberline/aw/.github/actions/coverage@<aw commit SHA> # S8
        with:
          summary: target/coverage.json
```

- Add `--all-features` or `--features` when the repository's tests need
  them.
- The action needs `bash`, `jq`, `gh` and `python3` 3.11 or later. The
  GitHub Ubuntu runners have them.
- On a pull request the action reads `aw.toml` of the base branch with the
  job's token (`contents: read`).
- The action writes a report to the job summary: the checks and the ten
  files with the most uncovered lines. It keeps the data as the artifact
  `coverage` for 30 days: `coverage-summary.json` (the `cargo llvm-cov`
  output) and `coverage-report.json` (the checks).

## Checks

| ID | Check |
|---|---|
| COV1 | `aw.toml` has `[standards.coverage]` with a number `line` and `target = 100`. When it fails, the detail gives the measured value to write. |
| COV2 | Every `[[standards.coverage.exclude]]` has a `path` and a `reason`. |
| COV3 | The measured value is at least `line` minus 0.1. The 0.1 is for test noise, for example a branch that a thread race runs only sometimes. |
| COV4 | The measured value is at most `line` plus 0.5. When it fails, the detail gives the new `line`. Write it in the same pull request. |
| COV5 | On a pull request, `line` is at least `line` on the base branch. Skipped on a push and when the base branch has no `line`. |

A failed check fails the job, and `gate` fails with it.

The script is `.github/actions/coverage/check.sh`. It prints the report as
JSON, so it also runs on a laptop:

```bash
cargo llvm-cov --workspace --locked --json --summary-only --output-path target/coverage.json
bash <aw checkout>/.github/actions/coverage/check.sh target/coverage.json aw.toml
```

## Excludes

An exclude takes files out of the count. It is the exception path of this
standard. The checker reads it and the report shows it.

```toml
[[standards.coverage.exclude]]
path = '^src/main\.rs$'    # a regex on the path from the repository root
reason = "three lines that call app::run; the it tests run the binary"
```

An exclude is allowed only for code that a unit or integration test cannot
run:

- a thin entry point, for example `main.rs`;
- generated code;
- code for another platform than the CI runner;
- code that only an e2e suite can run, until e2e coverage counts.

"Hard to test" is not a reason. Put the hard part behind a port of the
[layers](rust-layout.md) and test it with a fake. The report lists each
exclude that a pull request adds, and review checks its reason.

## The work

The [aw-worker skill](../../plugins/faberline/skills/aw-worker/SKILL.md) does
this in the pipeline:

- **plan.** When the repository has `[standards.coverage]` and no open issue
  whose title starts with `test(coverage):`, the planner opens one. It takes
  the file with the most uncovered lines from the newest `coverage` artifact
  of `main`. Triage gives it `p2`. So each repository has at most one
  coverage issue at a time.
- **tdd.** New and changed code comes with tests that run its lines. A
  coverage issue adds tests only and does not change behavior; a bug that a
  new test finds is a new issue. When COV4 fails, set `line` to the value it
  names.
- **review.** A lower `line` or an exclude that this page does not allow is
  a finding.

Diff coverage, the coverage of only the changed lines, is not checked now.
It can become a check when the totals are near 100 %.

## Rollout

Each repository adopts S8 in one pull request: the `test-coverage` job,
`[standards.coverage]` with the measured `line`, and any exclude with its
reason.

| Order | Repository | State, 2026-11-10 |
|---|---|---|
| 1 | `aw` | adopted: 81.24 % (macOS measure; CI may differ a little) |
| 2 | `lumen` | next |
| 3 | `core`, `som`, `nlp`, `defer`, `reach`, `tape`, `relay`, `keep`, `sift` | after `lumen` |
| — | `surface` | paused |
