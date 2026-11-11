# Shared release tooling

Guideline. State: **design, 2026-11-11** (faberline/aw#284, from the
architecture review faberline/aw#283). It becomes a standard when its checker,
`aw release check`, ships; see the [definition](README.md#what-a-standard-is).

`aw` supplies the release tooling of the service repositories once. A service
repository calls it and keeps only its own facts. **This design moves files.
It does not change a gate, an evidence requirement or a numeric threshold.**

## The problem

Six service repositories release the same way: landed `main`, then an
immutable candidate, then digest-pinned GKE acceptance, then a protected
annotated tag, then a promotion that does not build. Each repository carries
its own copy of the workflows and scripts that do this.

Measured on 2026-11-11 at these `main` commits: `keep` 12782a5, `sift`
137f89b, `relay` d73b0eb, `defer` c11ffb6, `tape` f85e230, `lumen` 76db3d2.
The number is the share of equal lines after the app name is replaced by one
placeholder (1.00 = the same file).

| File | keep, relay, defer, sift between them | tape against them | lumen against them |
|---|---|---|---|
| `<app>-release.yml` | 1.00 (sift has 1 more line) | 0.98 to 0.99 | 0.76 |
| `<app>-release-candidate.yml` | 0.93 to 1.00 | 0.80 to 0.85 | 0.63 to 0.70 |
| `candidate.sh` | 0.97 to 0.99 | no such file | no such file |
| `promote.sh` | 0.90 to 0.99 | no such file | no such file |
| `verify-release-artifacts.sh` | 0.98 to 0.99 | 0.71 | 0.56 |
| `verify-release-candidate.sh` | 0.98 to 0.99 | 0.82 | 0.82 (0.99 against tape) |
| `make-gke-release-receipt.py` | 0.82 to 0.99 | 0.27 | no such file |
| `verify-release-contract.py` | 0.85 to 0.98 | 0.11 | no such file |
| `apps.sh` | 0.72 to 0.99 | no such file | no such file |
| `scripts/project-build-lib.sh` | byte-equal in keep, sift, relay, defer, lumen | no such file | byte-equal |

There are three lineages:

- **Shared-script lineage**: `keep`, `sift`, `relay`, `defer`. The scripts are
  in `scripts/release/` and take `--app <app>`. Each copy already says it
  serves all four apps; `apps.sh` holds the facts of one app.
- **tape**: the same scripts in `scripts/`, without `--app`, with the tape
  facts written into them.
- **lumen**: its own promotion evidence (an optional standalone GKE receipt,
  durable performance evidence, no run attempt input).

## What becomes shared

| Piece | Today | Shared form in `aw` | State |
|---|---|---|---|
| Promotion workflow | `.github/workflows/<app>-release.yml`, about 370 lines in each repository | `.github/workflows/service-release.yml`, a `workflow_call` workflow | **in `aw`**; no repository calls it yet |
| Candidate workflow frame: the jobs `identity`, `build`, `ghcr-image-and-attest`, `manifest`, `verify-candidate`, `result` | `.github/workflows/<app>-release-candidate.yml` | `workflow_call` workflows around the repository's own gate jobs | design open, see [Open points](#open-points) |
| Release scripts: `candidate.sh`, `promote.sh`, `verify-release-candidate.sh`, `verify-release-artifacts.sh`, `make-gke-release-receipt.py`, `verify-release-contract.py` | `scripts/release/` in four repositories, `scripts/` in tape and lumen | one directory, `scripts/release/` in `aw` | not started |
| Build helper | `scripts/project-build-lib.sh`, byte-equal in five repositories | `scripts/release/` in `aw` | not started |

These stay in the repository. They are product decisions, not tooling:

- The gate jobs of the candidate (`<app>-release-gates`, the lumen
  `gates-*`, `perf-coarse` and `container-smoke` jobs, the tape `gke-e2e`
  job) and every threshold in them.
- The facts of the app that `apps.sh` holds now: targets, receipt name and
  schema, GKE backend, functional receipt fields, candidate job names, the
  compatibility line.
- `Dockerfile.release`, `build.sh` and `deny.toml`. They are alike in relay
  and defer, but this design does not cover them.

### Where the shared scripts live

**One shared directory, `scripts/release/` in `aw`. Not the `aw` CLI yet.**

- The scripts are bash and Python today. Their fixtures and self-tests prove
  the gates. A move keeps those bytes and those tests. A port to Rust is a
  rewrite of every gate; do it later, one script at a time, each behind the
  same fixtures.
- A shared workflow gets the scripts with a second checkout of
  `faberline/aw` at the commit that the caller pins, into a folder outside
  the repository checkout. The caller passes that commit as an input
  (`aw_ref`), because a called workflow cannot read its own commit without
  `id-token: write`. `aw release check` fails when `aw_ref` and the commit in
  `uses:` differ.
- The facts of the app move from `apps.sh` to a `[release]` table in the
  repository's `aw.toml`. The shared scripts read that table. Until a script
  is shared, the shared workflow runs the repository's own copy (the
  `verify_script` input below).

## The promotion workflow

`faberline/aw/.github/workflows/service-release.yml`. It replaces the body of
`<app>-release.yml` in keep, sift, relay, defer and tape.

The repository keeps `.github/workflows/<app>-release.yml` as a caller. The
file name must not change: the workflow checks that
`GITHUB_WORKFLOW_REF` is `<repo>/.github/workflows/<app>-release.yml@refs/tags/<app>@<version>`.

```yaml
name: keep-release

on:
  workflow_dispatch:
    inputs:
      version: { description: Exact Keep semver without the keep@ prefix., required: true, type: string }
      candidate_run_id: { description: Exact successful keep-release-candidate run ID., required: true, type: string }
      candidate_run_attempt: { description: Exact successful keep-release-candidate run attempt., required: true, type: string }
      gke_receipt_b64: { description: Base64 of the sanitized keep-gke-receipt.json bytes., required: true, type: string }
      gke_receipt_sha256: { description: Lowercase SHA-256 of the sanitized keep-gke-receipt.json bytes., required: true, type: string }
      gke_receipt_sidecar_b64: { description: Base64 of the exact keep-gke-receipt.json.sha256 sidecar bytes., required: true, type: string }
      gke_receipt_sidecar_sha256: { description: Lowercase SHA-256 of the exact keep-gke-receipt.json.sha256 sidecar bytes., required: true, type: string }

concurrency:
  group: keep-release-promotion-${{ inputs.version }}
  cancel-in-progress: false

jobs:
  release:
    permissions:
      actions: read
      attestations: read
      contents: write
      packages: write
      pull-requests: read
    uses: faberline/aw/.github/workflows/service-release.yml@<40-hex commit> # aw <version>
    with:
      app: keep
      version: ${{ inputs.version }}
      candidate_run_id: ${{ inputs.candidate_run_id }}
      candidate_run_attempt: ${{ inputs.candidate_run_attempt }}
      gke_receipt_b64: ${{ inputs.gke_receipt_b64 }}
      gke_receipt_sha256: ${{ inputs.gke_receipt_sha256 }}
      gke_receipt_sidecar_b64: ${{ inputs.gke_receipt_sidecar_b64 }}
      gke_receipt_sidecar_sha256: ${{ inputs.gke_receipt_sidecar_sha256 }}
      compatibility_note: "- Compatibility: no HTTP, CLI, wire format, on-disk format, or Kubernetes manifest shape changed in this release."
```

- The trigger, the seven dispatch inputs and the concurrency group stay in
  the caller. They are the same as today.
- The caller pins the shared workflow to a full commit SHA, like an action.
- The caller grants the five permissions above and no more. In the shared
  workflow the `verify-inputs` job takes only read permissions; only
  `publish-release` writes.

### Inputs each repository passes

| Input | keep, relay, defer | sift | tape |
|---|---|---|---|
| `app` | the app name | `sift` | `tape` |
| `version`, `candidate_run_id`, `candidate_run_attempt`, `gke_receipt_b64`, `gke_receipt_sha256`, `gke_receipt_sidecar_b64`, `gke_receipt_sidecar_sha256` | the dispatch inputs, unchanged | the same | the same |
| `compatibility_note` | the `RELEASE_COMPATIBILITY_LINE` of `apps.sh` | the same | the compatibility line that `scripts/verify-release-artifacts.sh` expects |
| `release_notes_extra` | not set | the `- Install: ...` line | not set |
| `image_note` | not set (`true`) | not set (`true`) | `false` |
| `verify_script` | not set (`scripts/release/verify-release-artifacts.sh`) | not set | `scripts/verify-release-artifacts.sh` |
| `verify_takes_app` | not set (`true`) | not set (`true`) | `false` |

With these values the release notes are byte-equal to the notes that each
repository's workflow writes today.

### Gates

Every gate of the copies is in the shared workflow. `tests/it/release_workflow.rs`
in `aw` checks each one, so `aw` CI fails when a gate goes missing.

| Gate | Where |
|---|---|
| The event is `workflow_dispatch`; the version is `N.N.N`; the ref is the exact tag `refs/tags/<app>@<version>`; the workflow identity is the repository's `<app>-release.yml` at that tag | `verify-inputs`, first step |
| The receipt and its sidecar are canonical base64, and their decoded bytes have the SHA-256 that the inputs give | both jobs |
| The repository's artifact verifier passes in `--mode candidate` with the exact run ID and run attempt: tag ruleset, candidate receipt, image, attestations | `verify-inputs`, and again in `publish-release` before any write |
| The contract binds the commit, run ID, attempt, both receipt hashes and three `sha256:` digests; the second query equals the first | both jobs |
| An existing public release is only verified (`--mode public`); nothing is written | `publish-release` |
| The candidate artifact has the final manifest and its sidecar | `publish-release` |
| The semver tag is absent or already the candidate root; `latest` moves only when this is the newest published semver and only from a root that a published semver proves; `latest` is read again before the write; each tag is read back after the write | `publish-release` |
| Both `imagetools create` lines take `$root_ref`; the workflow has no build command | `publish-release` |
| The release carries the candidate tarballs, their sidecars, both SPDX files, the receipt and its sidecar, and notes with the commit, pull request, candidate run, promotion run, three digests, both receipt hashes, the release path and the compatibility line | `publish-release` |
| The artifact verifier passes in `--mode public` after the release exists | `publish-release`, last step |

The shared workflow adds four checks. None replaces an old one:

- `app` is a lower-case name and the repository is `faberline/<app>`. The
  copies wrote the repository name as a constant.
- `verify_script` is an executable regular file under `scripts/` and is not
  a symbolic link.
- `compatibility_note` is one line that starts with `- Compatibility: `;
  `release_notes_extra` is at most one line.
- Workflow inputs reach a script only through the environment. The copies
  wrote `${{ inputs.* }}` into the script text.

## How a repository moves

One pull request in the repository, written by that repository's own session:

1. Replace the jobs of `<app>-release.yml` with the caller above.
2. Change the promotion check of the repository's contract checker.
   `verify-release-contract.py` reads the workflow text now: it requires the
   two job ids, pinned actions, no build command, two `imagetools create`
   lines and several fragments. tape also pins the SHA-256 of the whole file.
   After the move the checker requires the caller shape instead: the only
   trigger, the exact inputs, the concurrency group, one job that uses
   `service-release.yml` at a 40-hex commit, the permissions, and the `with:`
   values (the compatibility line equal to the one the public verifier
   expects). The checks of the workflow body are in `aw` from then on.
3. Leave the scripts, the candidate workflow and every gate as they are.

The pull request does not start a release. The repository's CI and its
contract checker self-test are the proof before merge. The first real
promotion after the move is the first live run of the shared workflow for
that repository; a promotion dispatches the workflow file at the release tag,
so an older tag still runs the copy that it carries.

### Order

| Order | Repository | Why here |
|---|---|---|
| 1 | `keep` | Shared-script lineage, one app, no extra input. It is the reference caller. |
| 2 | `relay` | The same shape as keep. |
| 3 | `defer` | The same shape as keep. |
| 4 | `sift` | One extra notes line. It also has a production receipt that the promotion does not read. |
| 5 | `tape` | Its own script lineage, and a SHA-256 allowlist of the promotion workflow in its contract checker. |
| 6 | `lumen` | A different evidence shape. Decide then: optional inputs in `service-release.yml`, or a second shared workflow. Do not bend the first five to fit it. |

One repository moves at a time. The next one starts when the one before is
merged. The scripts and the candidate frame follow in the same order, after
their own design step.

## Checker

**Not built yet.** `aw release check` runs in a repository checkout, like
`aw ci check`. It reads files only.

| Check | Rule |
|---|---|
| `release.caller` | `.github/workflows/<app>-release.yml` has exactly one job. The job uses `faberline/aw/.github/workflows/service-release.yml@<40-hex>`. The file has no `steps:` and no `run:`. |
| `release.caller-shape` | The only trigger is `workflow_dispatch`; the concurrency group is `<app>-release-promotion-${{ inputs.version }}` with `cancel-in-progress: false`; the job grants exactly the five permissions. |
| `release.pin` | The pinned commit is on `aw` `main`. When the caller passes `aw_ref`, it equals the commit in `uses:`. |
| `release.own-copy` | No file of the repository has a name from the shared list (`verify-release-artifacts.sh`, `verify-release-candidate.sh`, `verify-release-contract.py`, `make-gke-release-receipt.py`, `candidate.sh`, `promote.sh`, `project-build-lib.sh`) once `aw` supplies that file. |
| `release.own-copy-content` | No shell, Python or workflow file of the repository is a renamed copy: after the app name is replaced by a placeholder, the share of lines equal to a shared `aw` file is below a limit. The limit is set when the checker is built, from the table in [The problem](#the-problem). |

- The shared list is one table in `aw`. A file enters the list in the pull
  request that adds it to `aw`.
- A repository that has not moved yet records it:
  `[[standards.release.exception]]` in `aw.toml` with the check id, the path
  and a reason that names its follow-up issue. The checker prints and counts
  the exceptions, so a copy is visible until it is deleted.
- The S4 checker stays as it is. It reads `Cargo.toml` and `build.rs`; it is
  the wrong place for workflow and script files.

## Open points

- **Candidate frame.** The final candidate manifest binds the ids and the
  display names of the candidate jobs (`release_app_candidate_jobs`,
  `release_app_candidate_job_names`). A job inside a called workflow gets the
  name `<caller job> / <job>`. So the candidate frame cannot move without a
  change to the manifest check. This needs its own design step, and it must
  keep the rule that one successful attempt holds every job.
- **Live proof.** No shared workflow run exists yet. The first promotion of
  `keep` after its move is the first one. That `GITHUB_WORKFLOW_REF`,
  `GITHUB_REF` and `GITHUB_EVENT_NAME` hold the caller's values inside a
  called workflow is from the GitHub documentation, not from a run.
- **`aw` `main` protection.** A caller trusts the pinned `aw` commit with
  `contents: write` and `packages: write`. The pin is a full SHA, so a later
  change of `aw` does not reach a repository without a pull request there.
