# Rust tests

Guideline. State: **draft, 2026-10-16**. It becomes part of standard S3
([rust-layout.md](rust-layout.md)) when the S3 checker reads the rules in
[Planned checks](#planned-checks). Until then, a repository follows it when it
changes its tests.

The idea: every test of a Rust repository is Rust code that `cargo` builds.
The source builds the program (the **server binary**). The tests build a
second program (the **test binary**). For an end-to-end test, the test binary
talks to the server binary over the network, the same way a user does.

## Four kinds of test

| Kind | Where | How it reaches the code | What it needs |
|---|---|---|---|
| Unit | `#[cfg(test)]` modules in `src/` | It calls functions. | Nothing. |
| Integration | `tests/it/` | It calls the library, or it starts the server binary on the runner (`env!("CARGO_BIN_EXE_<repo>")`) and talks to it. | Nothing outside the runner. |
| End-to-end (e2e) | `tests/it/e2e/<suite>.rs` | Over the network, to a server that runs somewhere else. | `E2E_ENDPOINT` |
| Performance | `tests/it/perf/` | Like e2e. | `E2E_ENDPOINT`, and a quiet node |

In a workspace with several packages, `tests/it/` is in the main package:
`crates/<repo>/tests/it/`.

All four kinds are in the one integration binary of S3. `cargo test --test it
--no-run` builds it. Its file is the test binary.

The kind says where a test can run. It does not say when it runs: what
changed decides that, in three scopes ([test-scopes.md](test-scopes.md)).
Performance tests run only in the cloud.

## Rules

1. **Rust decides pass or fail.** A shell script may deploy, wait, and collect
   logs. It never checks a result. aw's `scripts/gke-test/run.sh` is an
   example: it deploys and waits, and the Job's exit code is the result.
2. **No shell wrappers.** A Rust test that only runs a test script and checks
   its exit code is not allowed. Move the steps into Rust. A test may run a
   script that the repository ships to users, such as `install.sh`, because
   that script is the product under test.
3. **Plain `cargo test` needs nothing outside the runner.** No cluster, no
   Docker, no other service. Mark every e2e and performance test
   `#[ignore = "e2e: needs E2E_ENDPOINT"]` (or `perf:`). Run them with
   `--ignored`.
4. **A missing endpoint is a failure.** When `E2E_ENDPOINT` is not set, an e2e
   test fails with a clear message. It never passes or skips in silence.
5. **One suite is one module.** `tests/it/e2e/smoke.rs` is the suite `smoke`.
   Its filter is `e2e::smoke::`. A suite can have several tests. They run one
   at a time (`--test-threads=1`), because they share one server.
   libtest runs them in name order, so no test may depend on another test.
   Each test makes its own data, for example its own collection. A journey
   whose steps must run in order (write, search, restart, read) is one
   `#[test]` that does the steps in order.
6. **Endpoints are base URLs.** `E2E_ENDPOINT` is the main server, for example
   `http://lumen:7373`. Another service gets `E2E_<NAME>_ENDPOINT`, for
   example `E2E_RELAY_ENDPOINT`.
   One exception: a suite that tests the image itself, for example how the
   container starts, stops, and keeps its volume, starts the container on the
   runner. Its input is the image, `E2E_<NAME>_IMAGE`, for example
   `E2E_STANDALONE_IMAGE`, not an endpoint. It is still `#[ignore]`, and rule
   4 holds for the image: a missing `E2E_<NAME>_IMAGE` is a failure.
7. **Cluster actions are steps in Rust.** A test that restarts a pod calls the
   `kube` crate or `kubectl` from Rust (`std::process::Command`), then checks
   the result in Rust.
8. **The test checks what it tests.** The run passes the expected identity in
   `test-env` (the ConfigMap `gke-test-env`): `E2E_EXPECTED_VERSION`,
   `E2E_EXPECTED_GIT_SHA`, `E2E_EXPECTED_IMAGE`, and
   `E2E_EXPECTED_RUNTIME_DIGEST`. A test reads the server's version endpoint
   and the pods' `imageID` (with `kubectl` from Rust) and compares them. Do
   not check them in a shell script on the runner.
9. **Evidence goes to stdout.** The pod log is the evidence. A test prints
   one JSON object per line with a fixed prefix, for example
   `E2E_EVIDENCE {"suite":"smoke","step":"search","ok":true}`. A script can
   collect those lines from the log later.

## The same test binary everywhere

The test binary does not know where the server runs. Only `E2E_ENDPOINT`
changes:

| Where | Server | Command |
|---|---|---|
| Your machine | `cargo run`, or `docker compose up` | `E2E_ENDPOINT=http://127.0.0.1:7373 cargo test --test it -- --ignored e2e::smoke::` |
| Shared GKE cluster | The server workload (StatefulSet or Deployment) on `pool-<repo>-server` | The test Job runs `/usr/local/bin/<repo>-it --ignored --test-threads=1 e2e::smoke::` on `pool-<repo>-client` |

There is no kind script. One set of tests replaces `scripts/kind-e2e.sh` and
any `e2e.sh` in a test Job folder. Before you delete a script, find the tests
that read its text (for example release or claim tests that name it). Point
them at the new Rust suite. Do not only delete them.

## The test client image

The test Job runs the test binary from a second image,
`ghcr.io/faberline/<repo>-test-client:sha-<git12>`. The same workflow run
builds both images from the same commit:

1. On the runner, build the server binary, for example
   `cargo build --release --locked --target x86_64-unknown-linux-musl`.
2. On the runner, build the test binary with the **same `--target`** as the
   server binary, so both fit the same small base image:

   ```bash
   cargo test --release --locked --target x86_64-unknown-linux-musl \
       --test it --no-run --message-format=json \
     | jq -r 'select(.profile.test == true and .target.name == "it") | .executable'
   ```

   The output is the path of the test binary.
3. One `Dockerfile` with two targets copies the binaries in. It does not
   compile:
   - `--target server`: the server binary.
   - `--target test-client`: the test binary as `/usr/local/bin/<repo>-it`,
     plus the tools the tests call, for example `kubectl`.

The test Job sets `test-image` and `test-image-name`, so the server keeps the
image under test. See [test-cluster.md](test-cluster.md#the-test-job).

A test Job folder for the suite `smoke`, `k8s/gke-test-job/smoke/job.yaml`
(placement, anti-affinity, and requests as in test-cluster.md):

```yaml
containers:
  - name: test
    image: lumen-test-client
    command:
      - /usr/local/bin/lumen-it
      - --ignored
      - --test-threads=1
      - e2e::smoke::
    env:
      - name: E2E_ENDPOINT
        value: http://lumen:7373
    envFrom:
      - configMapRef:
          name: gke-test-env   # E2E_EXPECTED_* from the workflow input test-env
```

## Planned checks

The S3 checker will report:

- A test under `tests/` that starts `sh` or `bash` with a script from
  `scripts/`, `tests/`, or `k8s/`.
- A shell script under `scripts/` or `k8s/gke-test-job/` whose name has
  `e2e`, `smoke`, or `acceptance`.
- A test Job whose command is not the repository's test binary.
- An e2e or performance test without `#[ignore]`.

aw's `run.sh` fails a test Job whose log shows `running 0 tests`. A filter
with a typo matches no test, and libtest exits 0 in that case.

## Measured state, 2026-10-16

Local checkouts of 24 Rust repositories:

| Rule | Repositories |
|---|---|
| `tests/it/` exists | lumen, model-router, pgpool, sift |
| e2e, smoke, or acceptance shell scripts | defer (1), loom (8), lumen (4), relay (1), tape (1) |
| Tests that start `sh` or `bash` (some test a shipped script, which is allowed) | 13 repositories; most in jet (57), lumen (21), preview (14), cap (11) |
| e2e in Rust against `E2E_ENDPOINT` | none |
| Performance in Rust in the test binary | lumen (`tests/it/perf_gate.rs`, run in GKE from `lumen-perf-it`) |

`lumen` moves first, after its 0.6.1 release, as part of its one-pipeline
work.
