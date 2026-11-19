//! `aw release-receipt` (backend `gke-acceptance`): the port of
//! `make-gke-release-receipt.py` of defer. Each negative control of the
//! Python `--self-test` is a Rust test here, and no row is missing.
//!
//! Python control -> Rust test (31 negative controls, 1 positive)
//!
//!   positive: receipt, run id, backend, evidence keys, no kubeconfig bytes,
//!             sidecar text                  -> a_good_run_makes_the_receipt,
//!                                              the_receipt_keeps_no_kubeconfig,
//!                                              the_sidecar_hashes_the_receipt
//!   wrong backend for defer                 -> wrong_backend
//!   wrong output name                       -> wrong_output_name
//!   run conclusion failure                  -> gh_run_conclusion_failure
//!   run not completed                       -> gh_run_not_completed
//!   headSha mismatch                        -> gh_head_sha_mismatch
//!   displayTitle without image              -> gh_title_without_image
//!   displayTitle other app                  -> gh_title_other_app
//!   other workflow                          -> gh_other_workflow
//!   push event                              -> gh_push_event
//!   foreign url                             -> gh_foreign_url
//!   build job ran                           -> gh_build_job_ran
//!   park step failed                        -> gh_park_step_failed
//!   verify image step skipped               -> gh_verify_image_step_skipped
//!   harness step absent                     -> gh_harness_step_absent
//!   acceptance job failed                   -> gh_acceptance_job_failed
//!   two acceptance jobs                     -> gh_two_acceptance_jobs
//!   verdict FAIL                            -> evidence_verdict_fail
//!   results FAIL                            -> evidence_results_fail
//!   results extra app                       -> evidence_results_extra_app
//!   manifests other image                   -> evidence_manifests_other_image
//!   teardown missing                        -> evidence_teardown_missing
//!   verdict missing                         -> evidence_verdict_missing
//!   candidate job failed                    -> candidate_job_failed
//!   candidate job missing                   -> candidate_job_missing
//!   root equals amd64                       -> candidate_root_equals_amd64
//!   foreign image repository                -> candidate_foreign_image_repository
//!   tag mismatch                            -> candidate_tag_mismatch
//!   built from a branch                     -> candidate_built_from_a_branch
//!   candidate tag not run scoped            -> candidate_tag_not_run_scoped
//!   extra target                            -> candidate_extra_target
//!   unknown app                             -> unknown_app
//!
//! Controls that the Python script did not have are listed after the table
//! in this file: a symbolic link, bad JSON, a missing field, a run that is
//! not a number, and the same app facts from `aw.toml`.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use aw::domain::release::facts::AppFacts;
use aw::domain::release::receipt::{self, GcpPaths, Inputs, ReceiptFiles};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const COMMIT: &str = "0123456789abcdef0123456789abcdef01234567";
const ACCEPTANCE_JOB: &str = "deploy + verify on GKE";
const VERIFY: &str = "Verify prebuilt image input";
const HARNESS: &str = "Run acceptance harness";
const PARK: &str = "Park node pool (belt and suspenders)";

fn sha256(raw: &[u8]) -> String {
    Sha256::digest(raw)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn defer() -> AppFacts {
    let mut app = AppFacts::shared("defer");
    app.targets = vec![
        "x86_64-unknown-linux-musl".into(),
        "aarch64-unknown-linux-musl".into(),
    ];
    app
}

fn digest(seed: &str) -> String {
    format!("sha256:{}", sha256(format!("defer-{seed}").as_bytes()))
}

fn candidate() -> Value {
    json!({
        "schema": "cclab.defer.candidate-manifest.v1",
        "repository": "faberline/defer",
        "workflow_path": ".github/workflows/defer-release-candidate.yml",
        "workflow_id": 42,
        "run_id": "12345",
        "run_attempt": "1",
        "run_url": "https://github.com/faberline/defer/actions/runs/12345/attempts/1",
        "source_ref": "refs/heads/main",
        "workflow_ref": "faberline/defer/.github/workflows/defer-release-candidate.yml@refs/heads/main",
        "commit": COMMIT,
        "version": "0.4.13",
        "tag": "defer@0.4.13",
        "candidate_tag": "release-candidate-12345-1",
        "pr": {"number": 7, "url": "https://github.com/faberline/defer/pull/7"},
        "image": {
            "repository": "ghcr.io/faberline/defer",
            "root_digest": digest("root"),
            "amd64_digest": digest("amd64"),
            "arm64_digest": digest("arm64"),
        },
        "artifacts": [
            {"target": "x86_64-unknown-linux-musl", "archive": "a", "archive_sha256": "0", "sidecar": "b", "sidecar_sha256": "1"},
            {"target": "aarch64-unknown-linux-musl", "archive": "c", "archive_sha256": "0", "sidecar": "d", "sidecar_sha256": "1"},
        ],
        "sboms": {
            "amd64": {"file": "spdx-amd64.json", "sha256": "2"},
            "arm64": {"file": "spdx-arm64.json", "sha256": "3"},
        },
        "jobs": {
            "identity": "success",
            "build": "success",
            "defer-release-gates": "success",
            "ghcr-image-and-attest": "success",
            "manifest": "success",
            "verify-candidate": "success",
            "result": "success",
        },
    })
}

fn image() -> String {
    format!("ghcr.io/faberline/defer@{}", digest("root"))
}

fn step(name: &str) -> Value {
    json!({"name": name, "status": "completed", "conclusion": "success", "number": 1})
}

fn gh_run() -> Value {
    let skipped = |name: &str| json!({"name": name, "status": "completed", "conclusion": "skipped", "steps": []});
    json!({
        "databaseId": 99001,
        "attempt": 1,
        "conclusion": "success",
        "status": "completed",
        "event": "workflow_dispatch",
        "workflowName": "gke-acceptance",
        "url": "https://github.com/faberline/defer/actions/runs/99001",
        "headSha": COMMIT,
        "displayTitle": format!("gke-acceptance defer @ {COMMIT} image={}", image()),
        "jobs": [
            skipped("build-keep"),
            skipped("build-defer"),
            skipped("build-relay"),
            skipped("build-loom"),
            {
                "name": ACCEPTANCE_JOB,
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    step("Set up job"),
                    step(VERIFY),
                    step("Prepare evidence directory"),
                    step(HARNESS),
                    step("Upload evidence bundle"),
                    step(PARK),
                ],
            },
        ],
    })
}

/// The evidence files, relative to the evidence folder `ev`.
fn evidence() -> BTreeMap<String, Vec<u8>> {
    let manifests = format!(
        "apiVersion: apps/v1\nkind: StatefulSet\nspec:\n  template:\n    spec:\n      containers:\n      - name: defer\n        image: {}\n",
        image()
    );
    BTreeMap::from([
        ("results.txt".into(), b"defer PASS\n".to_vec()),
        (
            "defer/verdict.txt".into(),
            b"[defer] PASS: readyz + round-trip + durability\n".to_vec(),
        ),
        ("defer/manifests.yaml".into(), manifests.into_bytes()),
        ("defer/teardown.log".into(), b"namespace deleted\n".to_vec()),
        (
            "kubeconfig".into(),
            b"apiVersion: v1\nkind: Config\nusers: [{name: redacted}]\n".to_vec(),
        ),
    ])
}

/// An in-memory file system.
#[derive(Default)]
struct Mem {
    files: RefCell<BTreeMap<PathBuf, Vec<u8>>>,
    links: BTreeSet<PathBuf>,
    dirs: BTreeSet<PathBuf>,
    reads: RefCell<Vec<PathBuf>>,
}

impl ReceiptFiles for Mem {
    fn regular(&self, path: &Path) -> Result<Vec<u8>, String> {
        self.reads.borrow_mut().push(path.to_path_buf());
        if self.links.contains(path) {
            return Err("link".into());
        }
        self.files
            .borrow()
            .get(path)
            .cloned()
            .ok_or_else(|| "missing".into())
    }
    fn real_dir(&self, path: &Path) -> bool {
        self.dirs.contains(path) && !self.links.contains(path)
    }
    fn is_symlink(&self, path: &Path) -> bool {
        self.links.contains(path)
    }
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), String> {
        self.files
            .borrow_mut()
            .insert(path.to_path_buf(), bytes.to_vec());
        Ok(())
    }
}

/// One run of the receipt maker: the candidate, the gh record, the evidence.
struct Case {
    candidate: Value,
    gh: Value,
    evidence: BTreeMap<String, Vec<u8>>,
    output: PathBuf,
    backend: String,
    links: BTreeSet<PathBuf>,
}

impl Case {
    fn good() -> Self {
        Case {
            candidate: candidate(),
            gh: gh_run(),
            evidence: evidence(),
            output: "out/defer-gke-receipt.json".into(),
            backend: "gke-acceptance".into(),
            links: BTreeSet::new(),
        }
    }

    fn mem(&self) -> Mem {
        let mut mem = Mem::default();
        let put = |mem: &Mem, path: &str, value: &Value| {
            mem.files
                .borrow_mut()
                .insert(path.into(), serde_json::to_vec_pretty(value).expect("json"));
        };
        put(&mem, "in/final-candidate-manifest.json", &self.candidate);
        put(&mem, "in/gh-run.json", &self.gh);
        for (name, bytes) in &self.evidence {
            mem.files
                .borrow_mut()
                .insert(Path::new("in/evidence").join(name), bytes.clone());
        }
        mem.dirs.insert("in/evidence".into());
        mem.links = self.links.clone();
        mem
    }

    fn make(&self) -> (Result<receipt::Receipt, receipt::Refusal>, Mem) {
        let mem = self.mem();
        let result = receipt::make(
            &defer(),
            &Inputs {
                backend: &self.backend,
                candidate_manifest: Path::new("in/final-candidate-manifest.json"),
                output: &self.output,
                gh_run: Some(Path::new("in/gh-run.json")),
                evidence_dir: Some(Path::new("in/evidence")),
                gcp: GcpPaths::default(),
            },
            &mem,
        );
        (result, mem)
    }

    fn refused(&self, label: &str) -> String {
        match self.make().0 {
            Ok(_) => panic!("{label}: the negative control was accepted"),
            Err(refusal) => {
                let text = refusal.to_string();
                assert!(text.starts_with("GKE receipt refused: "), "{label}: {text}");
                text
            }
        }
    }
}

fn expect(case: &Case, label: &str, part: &str) {
    let text = case.refused(label);
    assert!(text.contains(part), "{label}: {text}");
}

fn job_mut(gh: &mut Value) -> &mut Value {
    gh["jobs"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|j| j["name"] == ACCEPTANCE_JOB)
        .unwrap()
}

fn step_mut<'a>(gh: &'a mut Value, name: &str) -> &'a mut Value {
    job_mut(gh)["steps"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|s| s["name"] == name)
        .unwrap()
}

// ---- positive ---------------------------------------------------------------

#[test]
fn a_good_run_makes_the_receipt() {
    let (result, _) = Case::good().make();
    let made = result.unwrap();
    let r = &made.value;
    assert_eq!(r["gke"]["run_id"], "gha-99001-1");
    assert_eq!(r["gke"]["backend"], "gke-acceptance");
    assert_eq!(
        r["gke"]["run_url"],
        "https://github.com/faberline/defer/actions/runs/99001"
    );
    assert_eq!(r["gke"]["image"], image());
    assert_eq!(r["schema"], "defer.gke-release-receipt/v1");
    assert_eq!(r["candidate"]["version"], "0.4.13");
    assert_eq!(r["gke"]["functional"]["durability"], "passed");
    let mut keys: Vec<&String> = r["evidence"].as_object().unwrap().keys().collect();
    keys.sort();
    assert_eq!(
        keys,
        [
            "gh_run_sha256",
            "manifests_sha256",
            "results_sha256",
            "verdict_sha256"
        ]
    );
    assert_eq!(r["redaction"]["kubeconfig_retained"], false);
}

#[test]
fn the_receipt_is_sorted_compact_json_with_a_newline() {
    let made = Case::good().make().0.unwrap();
    let text = String::from_utf8(made.data).unwrap();
    assert!(text.ends_with("}\n"));
    assert!(
        text.starts_with(r#"{"candidate":{"amd64_digest":"sha256:"#),
        "{text}"
    );
    assert!(!text.contains(": ") && !text.contains(", "));
}

#[test]
fn the_receipt_keeps_no_kubeconfig() {
    let case = Case::good();
    let made = case.make().0.unwrap();
    let text = String::from_utf8(made.data).unwrap();
    assert!(!text.contains(&sha256(&case.evidence["kubeconfig"])));
    assert!(!text.contains("apiVersion: v1") && !text.contains("kind: Config"));
    assert!(!text.contains("kubeconfig_sha"));
    let (_, mem) = case.make();
    assert!(
        mem.reads
            .borrow()
            .iter()
            .all(|p| !p.to_string_lossy().contains("kubeconfig")),
        "the kubeconfig was read"
    );
}

#[test]
fn the_sidecar_hashes_the_receipt() {
    let made = Case::good().make().0.unwrap();
    assert_eq!(made.sidecar_name, "defer-gke-receipt.json.sha256");
    assert_eq!(
        made.sidecar,
        format!("{}  defer-gke-receipt.json\n", sha256(&made.data))
    );
}

// ---- the backend and the output ----------------------------------------------

#[test]
fn wrong_backend() {
    let mut case = Case::good();
    case.backend = "gcp".into();
    expect(
        &case,
        "wrong backend",
        "proves GKE acceptance with backend gke-acceptance, not gcp",
    );
}

#[test]
fn an_unknown_backend_is_refused_after_the_facts_agree() {
    let mut app = defer();
    app.gke_backend = "bogus".into();
    let mem = Case::good().mem();
    let err = receipt::make(
        &app,
        &Inputs {
            backend: "bogus",
            candidate_manifest: Path::new("in/final-candidate-manifest.json"),
            output: Path::new("out/defer-gke-receipt.json"),
            gh_run: None,
            evidence_dir: None,
            gcp: GcpPaths::default(),
        },
        &mem,
    )
    .unwrap_err();
    assert!(err.to_string().contains("unknown backend: bogus"), "{err}");
}

#[test]
fn wrong_output_name() {
    let mut case = Case::good();
    case.output = "out/receipt.json".into();
    expect(
        &case,
        "wrong output name",
        "output must be named defer-gke-receipt.json",
    );
}

#[test]
fn an_output_that_is_a_symlink_is_refused() {
    let mut case = Case::good();
    case.links.insert(case.output.clone());
    expect(&case, "output link", "output must not be a symlink");
}

#[test]
fn a_sidecar_that_is_a_symlink_is_refused() {
    let mut case = Case::good();
    case.links
        .insert("out/defer-gke-receipt.json.sha256".into());
    expect(
        &case,
        "sidecar link",
        "sidecar output must not be a symlink",
    );
}

#[test]
fn the_gh_run_and_the_evidence_folder_are_needed() {
    let mem = Case::good().mem();
    let err = receipt::make(
        &defer(),
        &Inputs {
            backend: "gke-acceptance",
            candidate_manifest: Path::new("in/final-candidate-manifest.json"),
            output: Path::new("out/defer-gke-receipt.json"),
            gh_run: None,
            evidence_dir: None,
            gcp: GcpPaths::default(),
        },
        &mem,
    )
    .unwrap_err();
    assert!(
        err.to_string()
            .contains("needs --gh-run and --evidence-dir"),
        "{err}"
    );
}

// ---- the gh run record ---------------------------------------------------------

fn gh_breaks(label: &str, part: &str, change: impl FnOnce(&mut Value)) {
    let mut case = Case::good();
    change(&mut case.gh);
    expect(&case, label, part);
}

#[test]
fn gh_run_conclusion_failure() {
    gh_breaks("conclusion failure", "did not conclude success", |r| {
        r["conclusion"] = json!("failure");
    });
}

#[test]
fn gh_run_not_completed() {
    gh_breaks("not completed", "did not conclude success", |r| {
        r["status"] = json!("in_progress");
    });
}

#[test]
fn gh_head_sha_mismatch() {
    gh_breaks("headSha", "head commit is not the candidate commit", |r| {
        r["headSha"] = json!("f".repeat(40));
    });
}

#[test]
fn gh_title_without_image() {
    gh_breaks(
        "title without image",
        "dispatched for this app with the candidate image",
        |r| {
            r["displayTitle"] = json!(format!("gke-acceptance defer @ {COMMIT}"));
        },
    );
}

#[test]
fn gh_title_other_app() {
    gh_breaks(
        "title other app",
        "dispatched for this app with the candidate image",
        |r| {
            r["displayTitle"] = json!(format!("gke-acceptance relay @ {COMMIT} image={}", image()));
        },
    );
}

#[test]
fn gh_other_workflow() {
    gh_breaks("other workflow", "not a gke-acceptance workflow run", |r| {
        r["workflowName"] = json!("defer-test-image");
    });
}

#[test]
fn gh_push_event() {
    gh_breaks("push event", "not dispatched by hand", |r| {
        r["event"] = json!("push");
    });
}

#[test]
fn gh_foreign_url() {
    gh_breaks("foreign url", "run URL does not belong", |r| {
        r["url"] = json!("https://github.com/someone/else/actions/runs/99001");
    });
}

#[test]
fn gh_build_job_ran() {
    gh_breaks("build job ran", "a build job ran", |r| {
        r["jobs"][0]["conclusion"] = json!("success");
    });
}

#[test]
fn gh_park_step_failed() {
    gh_breaks(
        "park step failed",
        "acceptance step did not conclude success: Park node pool",
        |r| {
            step_mut(r, PARK)["conclusion"] = json!("failure");
        },
    );
}

#[test]
fn gh_verify_image_step_skipped() {
    gh_breaks(
        "verify image skipped",
        "step did not conclude success: Verify prebuilt image input",
        |r| {
            step_mut(r, VERIFY)["conclusion"] = json!("skipped");
        },
    );
}

#[test]
fn gh_harness_step_absent() {
    gh_breaks(
        "harness step absent",
        "step did not conclude success: Run acceptance harness",
        |r| {
            job_mut(r)["steps"]
                .as_array_mut()
                .unwrap()
                .retain(|s| s["name"] != HARNESS);
        },
    );
}

#[test]
fn gh_acceptance_job_failed() {
    gh_breaks(
        "acceptance job failed",
        "acceptance job did not conclude success",
        |r| {
            job_mut(r)["conclusion"] = json!("failure");
        },
    );
}

#[test]
fn gh_two_acceptance_jobs() {
    gh_breaks("two acceptance jobs", "exactly one acceptance job", |r| {
        let again = job_mut(r).clone();
        r["jobs"].as_array_mut().unwrap().push(again);
    });
}

// Controls the Python script did not have.

#[test]
fn gh_a_missing_field_is_named_with_the_command_to_run() {
    gh_breaks(
        "missing field",
        "gh run record is missing fields; use --json databaseId",
        |r| {
            r.as_object_mut().unwrap().remove("headSha");
        },
    );
}

#[test]
fn gh_a_run_number_that_is_not_a_positive_integer_is_refused() {
    for bad in [json!(0), json!(-3), json!("99001"), json!(true), json!(1.5)] {
        gh_breaks("run number", "gh run identity is invalid", |r| {
            r["databaseId"] = bad.clone();
        });
    }
    gh_breaks("attempt", "gh run identity is invalid", |r| {
        r["attempt"] = json!(0)
    });
}

#[test]
fn gh_an_empty_job_list_is_refused() {
    gh_breaks("no jobs", "run has no job inventory", |r| {
        r["jobs"] = json!([])
    });
    gh_breaks("jobs not a list", "run has no job inventory", |r| {
        r["jobs"] = json!("x")
    });
}

#[test]
fn gh_a_job_that_is_not_an_object_ran() {
    gh_breaks("odd job", "a build job ran", |r| {
        r["jobs"].as_array_mut().unwrap().push(json!(3));
    });
}

#[test]
fn gh_the_record_must_be_valid_json_and_an_object() {
    let case = Case::good();
    for (bytes, part) in [
        (&b"not json"[..], "gh run record is not valid JSON"),
        (&b"[1]"[..], "gh run record must be a JSON object"),
    ] {
        let mem = case.mem();
        mem.files
            .borrow_mut()
            .insert("in/gh-run.json".into(), bytes.to_vec());
        let err = receipt::make(
            &defer(),
            &Inputs {
                backend: "gke-acceptance",
                candidate_manifest: Path::new("in/final-candidate-manifest.json"),
                output: &case.output,
                gh_run: Some(Path::new("in/gh-run.json")),
                evidence_dir: Some(Path::new("in/evidence")),
                gcp: GcpPaths::default(),
            },
            &mem,
        )
        .unwrap_err();
        assert!(err.to_string().contains(part), "{err}");
    }
}

#[test]
fn a_record_that_is_a_symlink_is_refused() {
    let mut case = Case::good();
    case.links.insert("in/gh-run.json".into());
    expect(&case, "record link", "gh run record must be a regular file");
}

// ---- the evidence bundle --------------------------------------------------------

fn evidence_breaks(label: &str, part: &str, change: impl FnOnce(&mut BTreeMap<String, Vec<u8>>)) {
    let mut case = Case::good();
    change(&mut case.evidence);
    expect(&case, label, part);
}

#[test]
fn evidence_verdict_fail() {
    evidence_breaks("verdict FAIL", "verdict is not the exact PASS line", |e| {
        e.insert(
            "defer/verdict.txt".into(),
            b"[defer] FAIL: readyz\n".to_vec(),
        );
    });
}

#[test]
fn evidence_results_fail() {
    evidence_breaks("results FAIL", "exactly one PASS for this app", |e| {
        e.insert("results.txt".into(), b"defer FAIL\n".to_vec());
    });
}

#[test]
fn evidence_results_extra_app() {
    evidence_breaks("results extra app", "exactly one PASS for this app", |e| {
        e.insert("results.txt".into(), b"defer PASS\nrelay PASS\n".to_vec());
    });
}

#[test]
fn evidence_manifests_other_image() {
    evidence_breaks(
        "manifests other image",
        "do not deploy the candidate image",
        |e| {
            e.insert(
                "defer/manifests.yaml".into(),
                b"image: ghcr.io/faberline/defer:latest\n".to_vec(),
            );
        },
    );
}

#[test]
fn evidence_teardown_missing() {
    evidence_breaks(
        "teardown missing",
        "teardown log must be a regular file",
        |e| {
            e.remove("defer/teardown.log");
        },
    );
}

#[test]
fn evidence_verdict_missing() {
    evidence_breaks(
        "verdict missing",
        "harness verdict must be a regular file",
        |e| {
            e.remove("defer/verdict.txt");
        },
    );
}

// Controls the Python script did not have.

#[test]
fn evidence_results_missing() {
    evidence_breaks(
        "results missing",
        "harness results must be a regular file",
        |e| {
            e.remove("results.txt");
        },
    );
}

#[test]
fn evidence_manifests_missing_or_not_utf8() {
    evidence_breaks(
        "manifests missing",
        "rendered manifests must be a regular file",
        |e| {
            e.remove("defer/manifests.yaml");
        },
    );
    evidence_breaks(
        "manifests not utf-8",
        "rendered manifests are not UTF-8",
        |e| {
            e.insert("defer/manifests.yaml".into(), vec![0xff, 0xfe]);
        },
    );
}

#[test]
fn evidence_the_image_line_may_be_indented() {
    let mut case = Case::good();
    case.evidence.insert(
        "defer/manifests.yaml".into(),
        format!("a:\n      image: {}   \n", image()).into_bytes(),
    );
    assert!(case.make().0.is_ok());
}

#[test]
fn evidence_the_folder_must_be_a_real_folder() {
    let mut case = Case::good();
    case.links.insert("in/evidence".into());
    expect(&case, "folder link", "evidence directory is invalid");
}

#[test]
fn evidence_the_hashes_are_of_the_bytes() {
    let case = Case::good();
    let r = case.make().0.unwrap().value;
    assert_eq!(r["evidence"]["results_sha256"], sha256(b"defer PASS\n"));
    assert_eq!(
        r["evidence"]["verdict_sha256"],
        sha256(&case.evidence["defer/verdict.txt"])
    );
    assert_eq!(
        r["evidence"]["manifests_sha256"],
        sha256(&case.evidence["defer/manifests.yaml"])
    );
}

// ---- the candidate manifest -------------------------------------------------------

fn candidate_breaks(label: &str, part: &str, change: impl FnOnce(&mut Value)) {
    let mut case = Case::good();
    change(&mut case.candidate);
    expect(&case, label, part);
}

#[test]
fn candidate_job_failed() {
    candidate_breaks(
        "job failed",
        "does not bind every required successful job",
        |c| {
            c["jobs"]["verify-candidate"] = json!("failure");
        },
    );
}

#[test]
fn candidate_job_missing() {
    candidate_breaks(
        "job missing",
        "does not bind every required successful job",
        |c| {
            c["jobs"]
                .as_object_mut()
                .unwrap()
                .remove("defer-release-gates");
        },
    );
}

#[test]
fn candidate_root_equals_amd64() {
    candidate_breaks(
        "root equals amd64",
        "root and child digests must be distinct",
        |c| {
            c["image"]["root_digest"] = c["image"]["amd64_digest"].clone();
        },
    );
}

#[test]
fn candidate_foreign_image_repository() {
    candidate_breaks(
        "foreign image repository",
        "candidate image repository changed",
        |c| {
            c["image"]["repository"] = json!("ghcr.io/faberline/relay");
        },
    );
}

#[test]
fn candidate_tag_mismatch() {
    candidate_breaks("tag mismatch", "candidate release tag changed", |c| {
        c["tag"] = json!("defer@0.4.14");
    });
}

#[test]
fn candidate_built_from_a_branch() {
    candidate_breaks(
        "built from a branch",
        "candidate was not built from main",
        |c| {
            c["source_ref"] = json!("refs/heads/feature");
        },
    );
}

#[test]
fn candidate_tag_not_run_scoped() {
    candidate_breaks(
        "tag not run scoped",
        "candidate image tag is not run scoped",
        |c| {
            c["candidate_tag"] = json!("release-candidate-1-1");
        },
    );
}

#[test]
fn candidate_extra_target() {
    candidate_breaks("extra target", "candidate artifact targets changed", |c| {
        let first = c["artifacts"][0].clone();
        c["artifacts"].as_array_mut().unwrap().push(first);
    });
}

// Controls the Python script did not have.

#[test]
fn candidate_a_key_added_or_lost() {
    candidate_breaks("extra key", "candidate manifest keys changed", |c| {
        c["extra"] = json!(1);
    });
    candidate_breaks("lost key", "candidate manifest keys changed", |c| {
        c.as_object_mut().unwrap().remove("sboms");
    });
}

#[test]
fn candidate_schema_repository_and_workflow_identity() {
    candidate_breaks("schema", "candidate manifest schema changed", |c| {
        c["schema"] = json!("other/v1");
    });
    candidate_breaks("repository", "candidate repository changed", |c| {
        c["repository"] = json!("faberline/relay");
    });
    candidate_breaks("workflow path", "candidate workflow path changed", |c| {
        c["workflow_path"] = json!(".github/workflows/x.yml");
    });
    candidate_breaks("workflow ref", "candidate workflow identity changed", |c| {
        c["workflow_ref"] =
            json!("faberline/defer/.github/workflows/defer-release-candidate.yml@refs/heads/dev");
    });
}

#[test]
fn candidate_version_commit_and_run_are_shaped() {
    candidate_breaks("version", "candidate version is invalid", |c| {
        c["version"] = json!("0.4");
    });
    candidate_breaks("commit", "candidate commit is invalid", |c| {
        c["commit"] = json!("ABC");
    });
    candidate_breaks("run id", "candidate run ID is invalid", |c| {
        c["run_id"] = json!(12345);
    });
    candidate_breaks("run id empty", "candidate run ID is invalid", |c| {
        c["run_id"] = json!("");
    });
    candidate_breaks("attempt", "candidate run attempt is invalid", |c| {
        c["run_attempt"] = json!("x");
    });
}

#[test]
fn candidate_image_shape_and_digests() {
    candidate_breaks("image shape", "candidate image shape changed", |c| {
        c["image"]["extra"] = json!(1);
    });
    candidate_breaks(
        "image not an object",
        "candidate image shape changed",
        |c| {
            c["image"] = json!("x");
        },
    );
    candidate_breaks(
        "root digest",
        "candidate root digest is not an immutable SHA-256 digest",
        |c| {
            c["image"]["root_digest"] = json!("sha256:abc");
        },
    );
    candidate_breaks(
        "amd64 digest",
        "candidate amd64 digest is not an immutable",
        |c| {
            c["image"]["amd64_digest"] = json!(3);
        },
    );
    candidate_breaks(
        "arm64 digest",
        "candidate arm64 digest is not an immutable",
        |c| {
            c["image"]["arm64_digest"] = json!("sha256:".to_string() + &"A".repeat(64));
        },
    );
    candidate_breaks("arm64 equals amd64", "must be distinct", |c| {
        c["image"]["arm64_digest"] = c["image"]["amd64_digest"].clone();
    });
}

#[test]
fn candidate_artifacts_must_be_a_list_of_the_targets_in_order() {
    candidate_breaks("not a list", "candidate artifact targets changed", |c| {
        c["artifacts"] = json!("x");
    });
    candidate_breaks("order", "candidate artifact targets changed", |c| {
        c["artifacts"].as_array_mut().unwrap().reverse();
    });
    candidate_breaks("not objects", "candidate artifact targets changed", |c| {
        c["artifacts"] = json!([1, 2]);
    });
}

#[test]
fn the_candidate_manifest_bytes_are_bound() {
    let case = Case::good();
    let mem = case.mem();
    let raw = mem
        .files
        .borrow()
        .get(Path::new("in/final-candidate-manifest.json"))
        .cloned()
        .unwrap();
    let r = case.make().0.unwrap().value;
    assert_eq!(r["candidate"]["manifest_sha256"], sha256(&raw));
    assert_eq!(r["candidate"]["root_digest"], digest("root"));
}

// ---- the facts come from aw.toml ----------------------------------------------------

#[test]
fn another_app_with_other_targets_and_jobs_is_checked_by_its_facts() {
    let mut app = defer();
    app.targets = vec!["x86_64-unknown-linux-musl".into()];
    let mem = Case::good().mem();
    let err = receipt::make(
        &app,
        &Inputs {
            backend: "gke-acceptance",
            candidate_manifest: Path::new("in/final-candidate-manifest.json"),
            output: Path::new("out/defer-gke-receipt.json"),
            gh_run: Some(Path::new("in/gh-run.json")),
            evidence_dir: Some(Path::new("in/evidence")),
            gcp: GcpPaths::default(),
        },
        &mem,
    )
    .unwrap_err();
    assert!(
        err.to_string().contains("artifact targets changed"),
        "{err}"
    );
}

// ---- unknown app and the binary -----------------------------------------------------

const TOML: &str = r#"[release.apps.defer]
targets = ["x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl"]
compatibility_line = "- Compatibility: x"
"#;

fn workdir(name: &str, case: &Case) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-receipt-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("in/evidence/defer")).unwrap();
    std::fs::write(dir.join("aw.toml"), TOML).unwrap();
    let git = |args: &[&str]| {
        let out = Command::new("git")
            .args(args)
            .current_dir(&dir)
            .output()
            .unwrap();
        assert!(out.status.success(), "git {args:?}");
    };
    git(&["init", "-q"]);
    std::fs::write(
        dir.join("in/final-candidate-manifest.json"),
        serde_json::to_vec_pretty(&case.candidate).unwrap(),
    )
    .unwrap();
    std::fs::write(
        dir.join("in/gh-run.json"),
        serde_json::to_vec_pretty(&case.gh).unwrap(),
    )
    .unwrap();
    for (name, bytes) in &case.evidence {
        std::fs::write(dir.join("in/evidence").join(name), bytes).unwrap();
    }
    dir
}

fn binary(dir: &Path, app: &str, output: &str) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .current_dir(dir)
        .args([
            "release-receipt",
            "--app",
            app,
            "--backend",
            "gke-acceptance",
        ])
        .args(["--candidate-manifest", "in/final-candidate-manifest.json"])
        .args(["--output", output])
        .args([
            "--gh-run",
            "in/gh-run.json",
            "--evidence-dir",
            "in/evidence",
        ])
        .output()
        .unwrap()
}

#[test]
fn unknown_app() {
    let dir = workdir("unknown", &Case::good());
    let out = binary(&dir, "loom", "out/loom-gke-receipt.json");
    assert_eq!(out.status.code(), Some(1));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains("GKE receipt refused: unknown shared-script app: loom (known: defer)"),
        "{err}"
    );
    assert!(!dir.join("out").exists());
}

#[test]
fn the_binary_writes_the_receipt_and_the_sidecar() {
    let dir = workdir("write", &Case::good());
    let out = binary(&dir, "defer", "out/defer-gke-receipt.json");
    assert_eq!(
        out.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&out.stdout).trim(),
        "GKE receipt written: out/defer-gke-receipt.json"
    );
    let data = std::fs::read(dir.join("out/defer-gke-receipt.json")).unwrap();
    let sidecar = std::fs::read_to_string(dir.join("out/defer-gke-receipt.json.sha256")).unwrap();
    assert_eq!(
        sidecar,
        format!("{}  defer-gke-receipt.json\n", sha256(&data))
    );
    let value: Value = serde_json::from_slice(&data).unwrap();
    assert_eq!(value["result"], "passed");
}

#[test]
fn the_binary_refuses_with_exit_1_and_writes_nothing() {
    let mut case = Case::good();
    case.gh["event"] = json!("push");
    let dir = workdir("refuse", &case);
    let out = binary(&dir, "defer", "out/defer-gke-receipt.json");
    assert_eq!(out.status.code(), Some(1));
    assert!(
        String::from_utf8_lossy(&out.stderr)
            .contains("GKE receipt refused: run was not dispatched by hand")
    );
    assert!(!dir.join("out").exists());
}

#[test]
fn the_binary_stops_with_exit_2_outside_a_checkout() {
    let dir = std::env::temp_dir().join(format!("aw-it-receipt-nogit-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let out = binary(&dir, "defer", "out/defer-gke-receipt.json");
    assert_eq!(out.status.code(), Some(2));
}

#[test]
fn the_binary_needs_its_arguments() {
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["release-receipt", "--app", "defer"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("required arguments"));
}

// ---- backend gcp ------------------------------------------------------------
//
// Python control (sift `--self-test`, gcp backend) -> Rust test
//
//   positive: receipt, run id, backend, evidence keys -> gcp_good_run_makes_the_receipt
//   run schema changed                    -> gcp_run_schema_changed
//   project id invalid                    -> gcp_project_invalid
//   region invalid                        -> gcp_region_invalid
//   zone outside region                   -> gcp_zone_outside_region
//   git sha not the candidate             -> gcp_git_sha_mismatch
//   dirty tree                            -> gcp_git_dirty
//   rebuilt image                         -> gcp_image_rebuilt
//   run id invalid                        -> gcp_run_id_invalid
//   image set wrong                       -> gcp_image_set_wrong
//   image not root digest                 -> gcp_image_not_root_digest
//   companion image empty                 -> gcp_companion_image_empty
//   acceptance schema changed             -> gcp_acceptance_schema_changed
//   acceptance other run                  -> gcp_acceptance_other_run
//   acceptance other project              -> gcp_acceptance_other_project
//   acceptance set wrong                  -> gcp_acceptance_set_wrong
//   app block keys wrong                  -> gcp_block_keys_wrong
//   block schema changed                  -> gcp_block_schema_changed
//   a functional field not passed         -> gcp_functional_not_passed
//   gcs object absent                     -> gcp_gcs_object_absent
//   gcs object empty                      -> gcp_gcs_object_empty
//   topology claimed                      -> gcp_topology_claimed
//   cleanup schema changed                -> gcp_cleanup_schema_changed
//   cleanup not clean                     -> gcp_cleanup_not_clean
//   cleanup other zone                    -> gcp_cleanup_other_zone
//   cleanup preserved wrong               -> gcp_cleanup_preserved_wrong
//   missing flags                         -> gcp_needs_all_four_files

fn sift() -> AppFacts {
    let mut app = AppFacts::shared("sift");
    app.gke_backend = "gcp".into();
    app.targets = vec![
        "x86_64-unknown-linux-musl".into(),
        "aarch64-unknown-linux-musl".into(),
    ];
    app.functional = [
        "operator_reconcile_1x1",
        "standard_gke_cri_collector",
        "lumen_structured_stdout_materialized",
        "scheduled_backup",
        "gcs_backup",
    ]
    .map(String::from)
    .to_vec();
    app.acceptance_schema = "axiom.gcp.sift.acceptance.v1".into();
    app.image_companions = vec!["lumen".into()];
    app.acceptance_companions = vec!["lumen".into(), "auth".into()];
    app.acceptance_extra_keys = [
        "schema",
        "gcs_object",
        "gcs_object_bytes",
        "topology_beyond_1x1",
    ]
    .map(String::from)
    .to_vec();
    app
}

fn sift_candidate() -> Value {
    let mut c = candidate();
    c["repository"] = json!("faberline/sift");
    c["workflow_path"] = json!(".github/workflows/sift-release-candidate.yml");
    c["workflow_ref"] =
        json!("faberline/sift/.github/workflows/sift-release-candidate.yml@refs/heads/main");
    c["tag"] = json!("sift@0.1.2");
    c["version"] = json!("0.1.2");
    c["schema"] = json!("cclab.sift.candidate-manifest.v1");
    c["image"]["repository"] = json!("ghcr.io/faberline/sift");
    c["run_url"] = json!("https://github.com/faberline/sift/actions/runs/12345/attempts/1");
    c["pr"]["url"] = json!("https://github.com/faberline/sift/pull/7");
    c["jobs"] = json!({
        "identity": "success", "build": "success", "sift-release-gates": "success",
        "ghcr-image-and-attest": "success", "manifest": "success",
        "verify-candidate": "success", "result": "success",
    });
    c
}

struct Gcp {
    candidate: Value,
    run: Value,
    images: Value,
    acceptance: Value,
    cleanup: Value,
}

impl Gcp {
    fn good() -> Self {
        let functional = [
            "operator_reconcile_1x1",
            "standard_gke_cri_collector",
            "lumen_structured_stdout_materialized",
            "scheduled_backup",
            "gcs_backup",
        ];
        let mut block = json!({
            "schema": "axiom.gcp.sift.acceptance.v1",
            "gcs_object": "gs://bucket/object",
            "gcs_object_bytes": 10,
            "topology_beyond_1x1": "not_claimed",
        });
        for f in functional {
            block[f] = json!("passed");
        }
        Gcp {
            candidate: sift_candidate(),
            run: json!({
                "schema": "axiom.gcp.operator.run.v1",
                "project_id": "axiom-accept-1",
                "region": "asia-east1",
                "gke_zone": "asia-east1-a",
                "git_sha": &COMMIT[..12],
                "git_dirty": false,
                "image_provenance": "prebuilt",
                "run_id": "rc-99001-1",
            }),
            images: json!({
                "sift": format!("ghcr.io/faberline/sift@{}", digest("root")),
                "lumen": "ghcr.io/faberline/lumen@sha256:abc",
            }),
            acceptance: json!({
                "schema": "axiom.gcp.operator.acceptance.v1",
                "run_id": "rc-99001-1",
                "project_id": "axiom-accept-1",
                "region": "asia-east1",
                "acceptance": {"sift": block, "lumen": {}, "auth": {}},
            }),
            cleanup: json!({
                "schema": "axiom.gcp.operator.cleanup.v1",
                "run_id": "rc-99001-1",
                "status": "clean",
                "project_id": "axiom-accept-1",
                "region": "asia-east1",
                "gke_zone": "asia-east1-a",
                "preserved": {"artifact_registry": true, "preexisting_apis": true},
            }),
        }
    }

    fn mem(&self) -> Mem {
        let mem = Mem::default();
        for (name, value) in [
            ("cand.json", &self.candidate),
            ("run.json", &self.run),
            ("images.json", &self.images),
            ("acceptance.json", &self.acceptance),
            ("cleanup.json", &self.cleanup),
        ] {
            mem.files.borrow_mut().insert(
                Path::new("in").join(name),
                serde_json::to_vec_pretty(value).expect("json"),
            );
        }
        mem
    }

    fn make(&self) -> Result<receipt::Receipt, receipt::Refusal> {
        let mem = self.mem();
        receipt::make(
            &sift(),
            &Inputs {
                backend: "gcp",
                candidate_manifest: Path::new("in/cand.json"),
                output: Path::new("out/sift-gke-receipt.json"),
                gh_run: None,
                evidence_dir: None,
                gcp: GcpPaths {
                    run: Some(Path::new("in/run.json")),
                    images: Some(Path::new("in/images.json")),
                    acceptance: Some(Path::new("in/acceptance.json")),
                    cleanup: Some(Path::new("in/cleanup.json")),
                },
            },
            &mem,
        )
    }

    fn block(&mut self) -> &mut Value {
        &mut self.acceptance["acceptance"]["sift"]
    }
}

fn gcp_expect(g: &Gcp, label: &str, part: &str) {
    match g.make() {
        Ok(_) => panic!("{label}: the negative control was accepted"),
        Err(r) => {
            let text = r.to_string();
            assert!(text.starts_with("GKE receipt refused: "), "{label}: {text}");
            assert!(text.contains(part), "{label}: {text}");
        }
    }
}

#[test]
fn gcp_good_run_makes_the_receipt() {
    let made = Gcp::good().make().unwrap();
    let r = &made.value;
    assert_eq!(r["gke"]["backend"], "gcp");
    assert_eq!(r["gke"]["run_id"], "rc-99001-1");
    assert_eq!(r["gke"]["image_provenance"], "prebuilt");
    assert_eq!(r["gke"]["cleanup"]["status"], "clean");
    assert_eq!(r["gke"]["functional"]["gcs_backup"], "passed");
    let ev = r["evidence"].as_object().unwrap();
    let mut names: Vec<&str> = ev.keys().map(String::as_str).collect();
    names.sort_unstable();
    assert_eq!(
        names,
        [
            "acceptance_sha256",
            "cleanup_sha256",
            "images_sha256",
            "run_sha256"
        ]
    );
}

#[test]
fn gcp_needs_all_four_files() {
    let mem = Gcp::good().mem();
    let err = receipt::make(
        &sift(),
        &Inputs {
            backend: "gcp",
            candidate_manifest: Path::new("in/cand.json"),
            output: Path::new("out/sift-gke-receipt.json"),
            gh_run: None,
            evidence_dir: None,
            gcp: GcpPaths::default(),
        },
        &mem,
    )
    .unwrap_err();
    assert!(err.to_string().contains("--run, --images"), "{err}");
}

#[test]
fn gcp_run_schema_changed() {
    let mut g = Gcp::good();
    g.run["schema"] = json!("x");
    gcp_expect(&g, "run schema", "run schema changed");
}

#[test]
fn gcp_project_invalid() {
    let mut g = Gcp::good();
    g.run["project_id"] = json!("A");
    gcp_expect(&g, "project", "project identity");
}

#[test]
fn gcp_region_invalid() {
    let mut g = Gcp::good();
    g.run["region"] = json!("mars");
    gcp_expect(&g, "region", "region");
}

#[test]
fn gcp_zone_outside_region() {
    let mut g = Gcp::good();
    g.run["gke_zone"] = json!("us-central1-a");
    gcp_expect(&g, "zone", "zone");
}

#[test]
fn gcp_git_sha_mismatch() {
    let mut g = Gcp::good();
    g.run["git_sha"] = json!("ffffffffffff");
    gcp_expect(&g, "sha", "clean candidate commit");
}

#[test]
fn gcp_git_dirty() {
    let mut g = Gcp::good();
    g.run["git_dirty"] = json!(true);
    gcp_expect(&g, "dirty", "clean candidate commit");
}

#[test]
fn gcp_image_rebuilt() {
    let mut g = Gcp::good();
    g.run["image_provenance"] = json!("built");
    gcp_expect(&g, "rebuilt", "rebuilt");
}

#[test]
fn gcp_run_id_invalid() {
    let mut g = Gcp::good();
    g.run["run_id"] = json!("-bad");
    gcp_expect(&g, "run id", "run ID");
}

#[test]
fn gcp_image_set_wrong() {
    let mut g = Gcp::good();
    g.images = json!({"sift": format!("ghcr.io/faberline/sift@{}", digest("root"))});
    gcp_expect(&g, "image set", "image set");
}

#[test]
fn gcp_image_not_root_digest() {
    let mut g = Gcp::good();
    g.images["sift"] = json!("ghcr.io/faberline/sift@sha256:other");
    gcp_expect(&g, "root digest", "root digest");
}

#[test]
fn gcp_companion_image_empty() {
    let mut g = Gcp::good();
    g.images["lumen"] = json!("");
    gcp_expect(&g, "companion", "companion image lumen");
}

#[test]
fn gcp_acceptance_schema_changed() {
    let mut g = Gcp::good();
    g.acceptance["schema"] = json!("x");
    gcp_expect(&g, "acceptance schema", "functional receipt schema");
}

#[test]
fn gcp_acceptance_other_run() {
    let mut g = Gcp::good();
    g.acceptance["run_id"] = json!("other");
    gcp_expect(&g, "acceptance run", "different run");
}

#[test]
fn gcp_acceptance_other_project() {
    let mut g = Gcp::good();
    g.acceptance["project_id"] = json!("other-project");
    gcp_expect(&g, "acceptance project", "identity changed");
}

#[test]
fn gcp_acceptance_set_wrong() {
    let mut g = Gcp::good();
    g.acceptance["acceptance"]
        .as_object_mut()
        .unwrap()
        .remove("auth");
    gcp_expect(&g, "acceptance set", "expected acceptance set");
}

#[test]
fn gcp_block_keys_wrong() {
    let mut g = Gcp::good();
    g.block()
        .as_object_mut()
        .unwrap()
        .remove("scheduled_backup");
    gcp_expect(&g, "block keys", "incomplete");
}

#[test]
fn gcp_block_schema_changed() {
    let mut g = Gcp::good();
    g.block()["schema"] = json!("x");
    gcp_expect(&g, "block schema", "acceptance schema changed");
}

#[test]
fn gcp_functional_not_passed() {
    let mut g = Gcp::good();
    g.block()["gcs_backup"] = json!("failed");
    gcp_expect(&g, "functional", "not fully passed");
}

#[test]
fn gcp_gcs_object_absent() {
    let mut g = Gcp::good();
    g.block()["gcs_object"] = json!("");
    gcp_expect(&g, "gcs object", "object evidence is absent");
}

#[test]
fn gcp_gcs_object_empty() {
    let mut g = Gcp::good();
    g.block()["gcs_object_bytes"] = json!(0);
    gcp_expect(&g, "gcs bytes", "object is empty");
}

#[test]
fn gcp_topology_claimed() {
    let mut g = Gcp::good();
    g.block()["topology_beyond_1x1"] = json!("proved");
    gcp_expect(&g, "topology", "topology");
}

#[test]
fn gcp_cleanup_schema_changed() {
    let mut g = Gcp::good();
    g.cleanup["schema"] = json!("x");
    gcp_expect(&g, "cleanup schema", "cleanup schema");
}

#[test]
fn gcp_cleanup_not_clean() {
    let mut g = Gcp::good();
    g.cleanup["status"] = json!("dirty");
    gcp_expect(&g, "cleanup status", "not clean");
}

#[test]
fn gcp_cleanup_other_zone() {
    let mut g = Gcp::good();
    g.cleanup["gke_zone"] = json!("asia-east1-b");
    gcp_expect(&g, "cleanup zone", "gke_zone");
}

#[test]
fn gcp_cleanup_preserved_wrong() {
    let mut g = Gcp::good();
    g.cleanup["preserved"]["artifact_registry"] = json!(false);
    gcp_expect(&g, "preserved", "preserve");
}
