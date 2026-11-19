//! `aw release-receipt`: the public, redacted GKE release receipt of one app.
//!
//! The receipt binds one verified release candidate (its final manifest
//! bytes, commit, run and image digests) to one digest-pinned GKE acceptance
//! run. This is the port of `make-gke-release-receipt.py`, backend
//! `gke-acceptance`: one `gke-acceptance` workflow run of the repository,
//! dispatched with the candidate image, and the downloaded evidence bundle.
//! The bundle carries a kubeconfig; nothing here reads, hashes or copies it.
//!
//! The rules are pure. Files come through [`ReceiptFiles`]. Each refusal is a
//! [`Refusal`] with the message `GKE receipt refused: <reason>`.

use std::fmt;
use std::path::Path;

use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use super::facts::AppFacts;

const ACCEPTANCE_JOB: &str = "deploy + verify on GKE";
const VERIFY_IMAGE_STEP: &str = "Verify prebuilt image input";
const HARNESS_STEP: &str = "Run acceptance harness";
const PARK_STEP: &str = "Park node pool (belt and suspenders)";

/// The backend of the harness that this module proves.
pub const GKE_ACCEPTANCE: &str = "gke-acceptance";

/// The reason a receipt is refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal(pub String);

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "GKE receipt refused: {}", self.0)
    }
}

impl std::error::Error for Refusal {}

type Checked<T> = Result<T, Refusal>;

fn fail<T>(message: impl Into<String>) -> Checked<T> {
    Err(Refusal(message.into()))
}

/// The files of the evidence, read by the adapter.
pub trait ReceiptFiles {
    /// The bytes of a regular file; a symbolic link or a folder is an `Err`.
    fn regular(&self, path: &Path) -> Result<Vec<u8>, String>;
    /// Whether `path` is a folder and not a symbolic link.
    fn real_dir(&self, path: &Path) -> bool;
    /// Whether `path` is a symbolic link.
    fn is_symlink(&self, path: &Path) -> bool;
    /// Write `bytes` to `path`, and make its folder first.
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), String>;
}

/// What the caller gives.
#[derive(Clone, Debug)]
pub struct Inputs<'a> {
    pub backend: &'a str,
    pub candidate_manifest: &'a Path,
    pub output: &'a Path,
    pub gh_run: Option<&'a Path>,
    pub evidence_dir: Option<&'a Path>,
}

/// The receipt: its bytes, the sidecar text and the name of the sidecar.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Receipt {
    pub value: Value,
    pub data: Vec<u8>,
    pub sidecar_name: String,
    pub sidecar: String,
}

fn sha256(raw: &[u8]) -> String {
    Sha256::digest(raw)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn matches(pattern: &str, text: &str) -> bool {
    Regex::new(&format!("^(?:{pattern})$"))
        .expect("a fixed pattern is valid")
        .is_match(text)
}

fn load_json(files: &dyn ReceiptFiles, path: &Path, label: &str) -> Checked<(Vec<u8>, Value)> {
    let raw = files.regular(path).or_else(|_| {
        fail(format!(
            "{label} must be a regular file: {}",
            path.display()
        ))
    })?;
    let value: Value = match serde_json::from_slice(&raw) {
        Ok(v) => v,
        Err(e) => return fail(format!("{label} is not valid JSON: {e}")),
    };
    if !value.is_object() {
        return fail(format!("{label} must be a JSON object"));
    }
    Ok((raw, value))
}

fn require_digest(value: Option<&Value>, label: &str) -> Checked<String> {
    match value.and_then(Value::as_str) {
        Some(s) if matches("sha256:[0-9a-f]{64}", s) => Ok(s.to_string()),
        _ => fail(format!("{label} is not an immutable SHA-256 digest")),
    }
}

fn positive_int(value: Option<&Value>) -> Option<i64> {
    value.and_then(Value::as_i64).filter(|n| *n > 0)
}

fn str_of<'a>(map: &'a Value, key: &str) -> Option<&'a str> {
    map.get(key).and_then(Value::as_str)
}

fn digits(value: Option<&Value>) -> Option<&str> {
    value
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
}

fn keys(value: &Value) -> Vec<&str> {
    let mut found: Vec<&str> = value
        .as_object()
        .map(|m| m.keys().map(String::as_str).collect())
        .unwrap_or_default();
    found.sort_unstable();
    found
}

fn workflow_ref(app: &AppFacts) -> String {
    format!(
        "{}/.github/workflows/{}-release-candidate.yml@refs/heads/main",
        app.repository, app.name
    )
}

fn final_jobs(app: &AppFacts) -> Value {
    let jobs: Map<String, Value> = app
        .candidate_jobs
        .iter()
        .map(|j| (j.clone(), json!("success")))
        .collect();
    Value::Object(jobs)
}

fn validate_candidate(app: &AppFacts, raw: &[u8], candidate: &Value) -> Checked<Value> {
    let expected = [
        "artifacts",
        "candidate_tag",
        "commit",
        "image",
        "jobs",
        "pr",
        "repository",
        "run_attempt",
        "run_id",
        "run_url",
        "sboms",
        "schema",
        "source_ref",
        "tag",
        "version",
        "workflow_id",
        "workflow_path",
        "workflow_ref",
    ];
    if keys(candidate) != expected {
        return fail("candidate manifest keys changed");
    }
    if str_of(candidate, "schema") != Some(app.manifest_schema.as_str()) {
        return fail("candidate manifest schema changed");
    }
    if str_of(candidate, "repository") != Some(app.repository.as_str()) {
        return fail("candidate repository changed");
    }
    let version = str_of(candidate, "version").filter(|v| matches(r"[0-9]+\.[0-9]+\.[0-9]+", v));
    let Some(version) = version else {
        return fail("candidate version is invalid");
    };
    let commit = str_of(candidate, "commit").filter(|c| matches("[0-9a-f]{40}", c));
    let Some(commit) = commit else {
        return fail("candidate commit is invalid");
    };
    let Some(run_id) = digits(candidate.get("run_id")) else {
        return fail("candidate run ID is invalid");
    };
    let Some(attempt) = digits(candidate.get("run_attempt")) else {
        return fail("candidate run attempt is invalid");
    };
    if str_of(candidate, "tag") != Some(format!("{}@{version}", app.name).as_str()) {
        return fail("candidate release tag changed");
    }
    if str_of(candidate, "candidate_tag")
        != Some(format!("release-candidate-{run_id}-{attempt}").as_str())
    {
        return fail("candidate image tag is not run scoped");
    }
    if str_of(candidate, "source_ref") != Some("refs/heads/main") {
        return fail("candidate was not built from main");
    }
    let path = format!(".github/workflows/{}-release-candidate.yml", app.name);
    if str_of(candidate, "workflow_path") != Some(path.as_str()) {
        return fail("candidate workflow path changed");
    }
    if str_of(candidate, "workflow_ref") != Some(workflow_ref(app).as_str()) {
        return fail("candidate workflow identity changed");
    }
    if candidate.get("jobs") != Some(&final_jobs(app)) {
        return fail("candidate does not bind every required successful job");
    }
    let targets: Option<Vec<Option<&str>>> = candidate
        .get("artifacts")
        .and_then(Value::as_array)
        .map(|a| a.iter().map(|x| str_of(x, "target")).collect());
    let want: Vec<Option<&str>> = app.targets.iter().map(|t| Some(t.as_str())).collect();
    if targets != Some(want) {
        return fail("candidate artifact targets changed");
    }
    let image = candidate
        .get("image")
        .filter(|i| keys(i) == ["amd64_digest", "arm64_digest", "repository", "root_digest"]);
    let Some(image) = image else {
        return fail("candidate image shape changed");
    };
    if str_of(image, "repository") != Some(format!("{}/{}", app.image_owner, app.name).as_str()) {
        return fail("candidate image repository changed");
    }
    let root = require_digest(image.get("root_digest"), "candidate root digest")?;
    let amd64 = require_digest(image.get("amd64_digest"), "candidate amd64 digest")?;
    let arm64 = require_digest(image.get("arm64_digest"), "candidate arm64 digest")?;
    if root == amd64 || root == arm64 || amd64 == arm64 {
        return fail("candidate root and child digests must be distinct");
    }
    Ok(json!({
        "repository": app.repository,
        "version": version,
        "commit": commit,
        "run_id": run_id,
        "run_attempt": attempt,
        "workflow_ref": workflow_ref(app),
        "manifest_sha256": sha256(raw),
        "root_digest": root,
        "amd64_digest": amd64,
        "arm64_digest": arm64,
    }))
}

/// What the evidence of one acceptance run proves.
struct Acceptance {
    run_id: String,
    run_url: String,
    evidence: Value,
}

fn acceptance_job(jobs: &[Value]) -> Checked<&Value> {
    let found: Vec<&Value> = jobs
        .iter()
        .filter(|j| str_of(j, "name") == Some(ACCEPTANCE_JOB))
        .collect();
    match found.as_slice() {
        [one] => Ok(one),
        _ => fail("run does not contain exactly one acceptance job"),
    }
}

fn concluded(job: &Value) -> bool {
    str_of(job, "status") == Some("completed") && str_of(job, "conclusion") == Some("success")
}

fn step_ok(job: &Value, name: &str) -> bool {
    job.get("steps")
        .and_then(Value::as_array)
        .is_some_and(|steps| {
            steps.iter().filter(|s| s.is_object()).any(|s| {
                str_of(s, "name") == Some(name) && str_of(s, "conclusion") == Some("success")
            })
        })
}

fn validate_run(
    app: &AppFacts,
    candidate: &Value,
    run: &Value,
    evidence_dir: &Path,
    files: &dyn ReceiptFiles,
) -> Checked<Acceptance> {
    let root = candidate["root_digest"].as_str().unwrap_or_default();
    let image = format!("{}/{}@{root}", app.image_owner, app.name);
    let commit = candidate["commit"].as_str().unwrap_or_default();
    let required = [
        "databaseId",
        "attempt",
        "conclusion",
        "status",
        "url",
        "headSha",
        "event",
        "workflowName",
        "displayTitle",
        "jobs",
    ];
    if !required.iter().all(|k| run.get(*k).is_some()) {
        return fail(
            "gh run record is missing fields; use --json databaseId,attempt,conclusion,status,url,headSha,event,workflowName,displayTitle,jobs",
        );
    }
    let (Some(number), Some(attempt)) = (
        positive_int(run.get("databaseId")),
        positive_int(run.get("attempt")),
    ) else {
        return fail("gh run identity is invalid");
    };
    if str_of(run, "workflowName") != Some("gke-acceptance") {
        return fail("run is not a gke-acceptance workflow run");
    }
    if str_of(run, "event") != Some("workflow_dispatch") {
        return fail("run was not dispatched by hand");
    }
    if !concluded(run) {
        return fail("run did not conclude success");
    }
    if str_of(run, "headSha") != Some(commit) {
        return fail("run head commit is not the candidate commit");
    }
    let run_url = format!(
        "https://github.com/{}/actions/runs/{number}",
        app.repository
    );
    if str_of(run, "url") != Some(run_url.as_str()) {
        return fail("run URL does not belong to this repository and run");
    }
    if str_of(run, "displayTitle")
        != Some(format!("gke-acceptance {} @ {commit} image={image}", app.name).as_str())
    {
        return fail("run was not dispatched for this app with the candidate image");
    }
    let jobs = run["jobs"].as_array().filter(|j| !j.is_empty());
    let Some(jobs) = jobs else {
        return fail("run has no job inventory");
    };
    let job = acceptance_job(jobs)?;
    if !concluded(job) {
        return fail("acceptance job did not conclude success");
    }
    for name in [VERIFY_IMAGE_STEP, HARNESS_STEP, PARK_STEP] {
        if !step_ok(job, name) {
            return fail(format!("acceptance step did not conclude success: {name}"));
        }
    }
    for other in jobs.iter().filter(|j| !std::ptr::eq(*j, job)) {
        if str_of(other, "conclusion") != Some("skipped") {
            return fail("a build job ran; the candidate image must be the only image in play");
        }
    }
    if !files.real_dir(evidence_dir) {
        return fail("evidence directory is invalid");
    }
    let read = |name: &str, label: &str| -> Checked<Vec<u8>> {
        let path = evidence_dir.join(name);
        files.regular(&path).or_else(|_| {
            fail(format!(
                "{label} must be a regular file: {}",
                path.display()
            ))
        })
    };
    let results = read("results.txt", "harness results")?;
    if results != format!("{} PASS\n", app.name).as_bytes() {
        return fail("harness results do not record exactly one PASS for this app");
    }
    let verdict = read(&format!("{}/verdict.txt", app.name), "harness verdict")?;
    if verdict != format!("[{}] PASS: readyz + round-trip + durability\n", app.name).as_bytes() {
        return fail("harness verdict is not the exact PASS line");
    }
    let manifests = read(
        &format!("{}/manifests.yaml", app.name),
        "rendered manifests",
    )?;
    let Ok(text) = std::str::from_utf8(&manifests) else {
        return fail("rendered manifests are not UTF-8");
    };
    let wanted = format!("image: {image}");
    if !text.lines().any(|l| l.trim() == wanted) {
        return fail("rendered manifests do not deploy the candidate image");
    }
    read(&format!("{}/teardown.log", app.name), "teardown log")?;
    Ok(Acceptance {
        run_id: format!("gha-{number}-{attempt}"),
        run_url,
        evidence: json!({
            "manifests_sha256": sha256(&manifests),
            "results_sha256": sha256(&results),
            "verdict_sha256": sha256(&verdict),
        }),
    })
}

/// Make the receipt of `app`, or refuse. Nothing is written.
pub fn make(app: &AppFacts, input: &Inputs<'_>, files: &dyn ReceiptFiles) -> Checked<Receipt> {
    if app.gke_backend != input.backend {
        return fail(format!(
            "{} proves GKE acceptance with backend {}, not {}",
            app.name, app.gke_backend, input.backend
        ));
    }
    if input.backend != GKE_ACCEPTANCE {
        return fail(format!("unknown backend: {}", input.backend));
    }
    let (raw, manifest) = load_json(files, input.candidate_manifest, "candidate manifest")?;
    let candidate = validate_candidate(app, &raw, &manifest)?;
    let (Some(gh_run), Some(evidence_dir)) = (input.gh_run, input.evidence_dir) else {
        return fail("backend gke-acceptance needs --gh-run and --evidence-dir");
    };
    let (gh_raw, gh_json) = load_json(files, gh_run, "gh run record")?;
    let proved = validate_run(app, &candidate, &gh_json, evidence_dir, files)?;
    let mut evidence = proved.evidence;
    evidence["gh_run_sha256"] = json!(sha256(&gh_raw));
    let image = format!(
        "{}/{}@{}",
        app.image_owner,
        app.name,
        candidate["root_digest"].as_str().unwrap_or_default()
    );
    let functional: Map<String, Value> = app
        .functional
        .iter()
        .map(|f| (f.clone(), json!("passed")))
        .collect();
    let value = json!({
        "schema": app.receipt_schema,
        "complete": true,
        "result": "passed",
        "candidate": candidate,
        "gke": {
            "backend": GKE_ACCEPTANCE,
            "run_id": proved.run_id,
            "run_url": proved.run_url,
            "image": image,
            "image_provenance": "prebuilt",
            "functional": functional,
            "cleanup": {
                "schema": "axiom.gke-harness.cleanup.v1",
                "status": "clean",
                "namespace_deleted": true,
                "node_pool_parked": true,
            },
        },
        "evidence": evidence,
        "redaction": {
            "kubeconfig_retained": false,
            "token_retained": false,
            "secret_retained": false,
            "cluster_identity_retained": false,
            "command_output_retained": false,
        },
    });
    let mut data = serde_json::to_string(&value).expect("a value always serializes");
    data.push('\n');
    let data = data.into_bytes();
    if files.is_symlink(input.output) {
        return fail("output must not be a symlink");
    }
    let name = input.output.file_name().and_then(|n| n.to_str());
    if name != Some(app.receipt_name.as_str()) {
        return fail(format!("output must be named {}", app.receipt_name));
    }
    let sidecar_name = format!("{}.sha256", app.receipt_name);
    if files.is_symlink(&input.output.with_file_name(&sidecar_name)) {
        return fail("sidecar output must not be a symlink");
    }
    let sidecar = format!("{}  {}\n", sha256(&data), app.receipt_name);
    Ok(Receipt {
        value,
        data,
        sidecar_name,
        sidecar,
    })
}
