# Repo hygiene

Standard S9. State: **active since `aw` 0.7.40**; see the
[definition](README.md#what-a-standard-is). `aw repo check` reads the tracked
files of a checkout (`git ls-files`) and reports clutter: session leftovers,
home paths, dated documents and oversized files. It exists because agents
and people leave files behind, and the cloud routines that write most code
do not load a local hook. A CI check covers every writer.

The owner decision of 2026-11-16 is to clean every repository before
`1.0.0` and to stop new clutter ([aw#539](https://github.com/faberline/aw/issues/539)).

## Command

```bash
aw repo check                  # the checkout of the current directory
aw repo check --dir ../relay   # another checkout
aw repo check --format json    # a `repo-report/1` document
aw repo check --print-baseline # the baseline block for a first adoption
```

Exit codes: 0 pass, 1 a new violation or a stale baseline entry, 2 a usage
error (not a git checkout, a broken `aw.toml`).

The check is a new command, not a part of `aw ci check`. `aw ci check` reads
the workflow and the ruleset; this check reads the tracked files and its own
table in `aw.toml`.

## Rules

A violation has a rule id and a path. One path breaks one rule once.

| Rule | What it forbids |
|---|---|
| RH1 | A root entry that is not in the allow-list. |
| RH2 | A forbidden name. |
| RH3 | An absolute home path in a tracked text file. |
| RH4 | A dated or per-issue name outside an evidence folder. |
| RH5 | A tracked file above the size limit. |

**RH1: root allow-list.** The built-in list is: `README.md`, `AGENTS.md`,
`CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `LICENSE*`,
`Cargo.toml`, `Cargo.lock`, `build.rs`, `aw.toml`, `rust-toolchain(.toml)`,
`rustfmt.toml`, `.rustfmt.toml`, `clippy.toml`, `.clippy.toml`, `deny.toml`,
`taplo.toml`, `.editorconfig`, `.gitignore`, `.gitattributes`,
`.dockerignore`, `Dockerfile*`, and the folders `crates/`, `src/`, `tests/`,
`docs/`, `scripts/`, `ci/`, `k8s/`, `.github/`, `.cargo/`. A repo adds its
other root entries in `aw.toml`, each with a reason. The violation names the
root entry, not each file in it.

**RH2: forbidden names.** A file name that contains `HANDOFF` or `CLOSEOUT`,
ends in `_REPORT.md`, `_STATUS.md`, `.bak` or `.orig`, or is `PLAN.md` or
`NOTES.md`; and any path with a folder named `.codex`, `.agy`, `tmp`,
`scratch` or `old`. The match is case-sensitive. There is no allow-list for
RH2: rename or delete the file. A path that must stay goes to the baseline
until it is cleaned.

**RH3: home paths.** A line with `/Users/<name>/` or `/home/<name>/` in a
tracked text file (UTF-8, up to 2 MiB). The names `runner`, `user`,
`username`, `you`, `me`, `example` and `name` are placeholders and do not
count. A test fixture that needs a real-looking path goes to
`[[hygiene.home_path]]`.

**RH4: dated and per-issue names.** A file name or folder name with
`-20YY-MM-DD` or `issue<N>` (also `issue-<N>`, `issue_<N>`, any case).
Evidence belongs in a folder that `aw.toml` lists in `[[hygiene.evidence]]`;
files below it pass RH4.

**RH5: size.** A tracked file above `max_file_bytes` (default 262144, 256 KiB)
that is not in `[[hygiene.size]]`. `Cargo.lock` is never checked.

## The `[hygiene]` table

```toml
[hygiene]
max_file_bytes = 262144          # optional
baseline = [                     # existing violations: "<rule>:<path>"
    "RH2:docs/OLD_STATUS.md",
]

[[hygiene.root]]                 # RH1: an extra root entry
path = "infra"
reason = "The shared test cluster config."

[[hygiene.evidence]]             # RH4: a folder for dated records
path = "release-evidence"
reason = "Release receipts that a verifier reads."

[[hygiene.home_path]]            # RH3: a file that may hold a home path
path = "tests/fixtures/paths.txt"
reason = "A fixture that tests the home path rule."

[[hygiene.size]]                 # RH5: a file that may be large
path = "tests/data/corpus.bin"
reason = "The test corpus; it cannot be split."
```

Every allow-list entry needs a `path` and a `reason`. A path is a file or a
folder (all files below it). An entry with no reason is a usage error.

## Ratchet

The baseline lists the violations that exist today.

- A violation in the baseline passes.
- A violation that is not in the baseline fails the check.
- A baseline entry whose file no longer violates fails the check as
  **stale**. The fix is to remove the entry. So the list only shrinks.
- **A repository needs an empty baseline for `1.0.0`.** Exceptions that stay
  are allow-list entries with a reason, not baseline entries.

## Adopt the check

1. Install `aw` 0.7.40 or later in the `lint` job.
2. Run `aw repo check --print-baseline`. Copy the block under a `[hygiene]`
   heading in `aw.toml`. Add the root entries that are legitimate as
   `[[hygiene.root]]` entries with a reason, and take them out of the block.
3. Add a step to the `lint` job of `.github/workflows/ci.yml`:

   ```yaml
   - name: repo hygiene (S9)
     run: aw repo check
   ```

   `gate` already requires `lint`, so no ruleset change is needed.
4. Clean the baseline entry by entry. Each cleanup PR removes the entries it
   fixes; the check fails if it does not.

## Add an exception

Prefer a fix: rename, move or delete the file. If the file must stay, add one
allow-list entry with a reason in the PR that adds the file. A reviewer
rejects an entry whose reason says only "needed". Do not put a new violation
in the baseline: the baseline is for what exists on the day of adoption.
