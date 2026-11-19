//! `aw release-contract`: each rule C1 to C8, P1 to P7 and A2 has a failing
//! fixture. The passing fixture is the real pair of defer workflows (defer
//! `main`, 2026-11-20), which the Python script `verify-release-contract.py`
//! also accepts. A failing fixture changes one thing in that text.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use aw::domain::release::app_check::{self, ContractRepo};
use aw::domain::release::app_rules::Fail;
use aw::domain::release::facts::AppFacts;
use aw::domain::work::ports::PortResult;

const CANDIDATE: &str = include_str!("release_contract_candidate.yml");
const PROMOTION: &str = include_str!("release_contract_promotion.yml");
const COMPATIBILITY: &str = "- Compatibility: no HTTP, CLI, wire format, on-disk format, or Kubernetes manifest shape changed in this release.";

struct Fake {
    files: BTreeMap<String, String>,
    apps: Vec<AppFacts>,
}

impl ContractRepo for Fake {
    fn read(&self, path: &str) -> Option<String> {
        self.files.get(path).cloned()
    }
    fn exists(&self, path: &str) -> bool {
        self.files.contains_key(path)
    }
    fn apps(&self) -> PortResult<Vec<AppFacts>> {
        Ok(self.apps.clone())
    }
}

fn defer() -> AppFacts {
    let mut app = AppFacts::shared("defer");
    app.targets = vec![
        "x86_64-unknown-linux-musl".into(),
        "aarch64-unknown-linux-musl".into(),
    ];
    app.compatibility_line = COMPATIBILITY.into();
    app
}

fn fake(candidate: &str, promotion: &str) -> Fake {
    let app = defer();
    let files = [
        (app.candidate_workflow(), candidate.to_string()),
        (app.promotion_workflow(), promotion.to_string()),
    ];
    Fake {
        files: files.into_iter().collect(),
        apps: vec![app],
    }
}

fn findings(candidate: &str, promotion: &str) -> Vec<Fail> {
    let fake = fake(candidate, promotion);
    let report = app_check::check(&fake, None).unwrap();
    report.apps[0].findings.clone()
}

/// Replace `old` by `new`; `old` must be in the text.
fn change(text: &str, old: &str, new: &str) -> String {
    assert!(text.contains(old), "the fixture has no {old:?}");
    text.replacen(old, new, 1)
}

/// Replace the last `old`.
fn change_last(text: &str, old: &str, new: &str) -> String {
    let at = text.rfind(old).expect("the fixture has the text");
    format!("{}{new}{}", &text[..at], &text[at + old.len()..])
}

fn change_all(text: &str, old: &str, new: &str) -> String {
    assert!(text.contains(old), "the fixture has no {old:?}");
    text.replace(old, new)
}

/// The only finding of a broken candidate: its rule, and a part of its text.
fn candidate_breaks(text: &str, rule: &str, part: &str) {
    let found = findings(text, PROMOTION);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].rule, rule, "{found:?}");
    assert!(found[0].message.contains(part), "{found:?}");
}

fn promotion_breaks(text: &str, rule: &str, part: &str) {
    let found = findings(CANDIDATE, text);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].rule, rule, "{found:?}");
    assert!(found[0].message.contains(part), "{found:?}");
}

#[test]
fn the_defer_workflows_pass() {
    assert_eq!(findings(CANDIDATE, PROMOTION), vec![]);
}

#[test]
fn c1_the_candidate_runs_only_on_workflow_dispatch() {
    let text = change(
        CANDIDATE,
        "on:\n  workflow_dispatch:",
        "on:\n  push:\n    branches: [main]\n  workflow_dispatch:",
    );
    candidate_breaks(&text, "C1", "only trigger must be workflow_dispatch");
}

#[test]
fn c2_the_candidate_takes_version_and_commit() {
    let text = change(
        CANDIDATE,
        "      commit:\n",
        "      extra:\n        type: string\n      commit:\n",
    );
    candidate_breaks(&text, "C2", "inputs must be exactly version and commit");
}

#[test]
fn c3_the_candidate_has_the_job_set_of_the_app() {
    let text = change(CANDIDATE, "  result:\n", "  results:\n");
    candidate_breaks(&text, "C3", "jobs must be");
}

#[test]
fn c4_candidate_actions_are_pinned() {
    let text = change(
        CANDIDATE,
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
    );
    candidate_breaks(&text, "C4", "action not pinned to a 40-hex commit");
}

#[test]
fn c5_the_candidate_keeps_its_commit_guard() {
    let text = change_all(
        CANDIDATE,
        r#"[[ "$REQUESTED_COMMIT" == "$GITHUB_SHA" ]]"#,
        "true",
    );
    candidate_breaks(&text, "C5", "required fragment absent");
}

#[test]
fn c6_the_candidate_verifies_in_full_mode() {
    let text = change(CANDIDATE, "--mode full", "--mode local");
    candidate_breaks(&text, "C6", "full mode");
}

#[test]
fn c6_the_shared_verifier_is_told_the_app() {
    let text = change(
        CANDIDATE,
        "scripts/release/verify-release-candidate.sh --app defer \\\n            --repo",
        "scripts/release/verify-release-candidate.sh \\\n            --repo",
    );
    candidate_breaks(&text, "C6", "--app defer");
}

#[test]
fn c7_the_result_job_needs_every_other_job() {
    let text = change(
        CANDIDATE,
        "needs: [identity, build, defer-release-gates, manifest, ghcr-image-and-attest, verify-candidate]",
        "needs: [identity, build, defer-release-gates, manifest, ghcr-image-and-attest]",
    );
    candidate_breaks(&text, "C7", "result must need exactly");
}

#[test]
fn c7_the_manifest_binds_each_job_to_its_result() {
    let text = change(
        CANDIDATE,
        r#""verify-candidate":"${{ needs.verify-candidate.result }}""#,
        r#""verify-candidate":"success""#,
    );
    candidate_breaks(&text, "C7", "must bind job verify-candidate");
}

#[test]
fn c8_the_matrix_names_the_targets_of_the_app() {
    let text = change(
        CANDIDATE,
        "          - { target: aarch64-unknown-linux-musl, os: ubuntu-24.04-arm }\n",
        "",
    );
    candidate_breaks(&text, "C8", "build matrix targets");
}

#[test]
fn c8_a_download_job_names_the_targets_of_the_app() {
    // One download of the result job names another target.
    let text = change_last(
        CANDIDATE,
        "defer-candidate-aarch64-unknown-linux-musl-${{ github.run_id }}",
        "defer-candidate-riscv64-unknown-linux-musl-${{ github.run_id }}",
    );
    candidate_breaks(&text, "C8", "downloads targets");
}

#[test]
fn c8_a_for_loop_names_the_targets_of_the_app() {
    let text = change(
        CANDIDATE,
        "for target in x86_64-unknown-linux-musl aarch64-unknown-linux-musl; do",
        "for target in x86_64-unknown-linux-musl; do",
    );
    candidate_breaks(&text, "C8", "loop targets");
}

#[test]
fn p1_the_promotion_runs_only_on_workflow_dispatch() {
    let text = change(
        PROMOTION,
        "on:\n  workflow_dispatch:",
        "on:\n  push:\n    tags: ['*']\n  workflow_dispatch:",
    );
    promotion_breaks(&text, "P1", "only trigger must be workflow_dispatch");
}

#[test]
fn p2_the_promotion_takes_the_inputs_of_the_app() {
    let text = change(
        PROMOTION,
        "      candidate_run_attempt:\n        description: Exact successful defer-release-candidate run attempt.\n        required: true\n        type: string\n",
        "",
    );
    promotion_breaks(&text, "P2", "inputs must be");
}

#[test]
fn p3_the_promotion_has_two_jobs() {
    let text = change(PROMOTION, "  publish-release:\n", "  publish-extra:\n");
    promotion_breaks(
        &text,
        "P3",
        "jobs must be verify-inputs and publish-release",
    );
}

#[test]
fn p4_promotion_actions_are_pinned() {
    let text = change(
        PROMOTION,
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
    );
    promotion_breaks(&text, "P4", "action not pinned to a 40-hex commit");
}

#[test]
fn p4_the_promotion_does_not_rebuild() {
    let text = format!("{PROMOTION}      - run: cargo build --release\n");
    promotion_breaks(&text, "P4", "must not rebuild: cargo build");
}

#[test]
fn p5_the_promotion_verifies_the_public_release() {
    let text = change_all(PROMOTION, "--mode public", "--mode publ1c");
    promotion_breaks(&text, "P5", "required fragment absent");
}

#[test]
fn p6_the_shared_verifier_is_told_the_app() {
    let text = change_all(
        PROMOTION,
        "verify-release-artifacts.sh --app defer",
        "verify-release-artifacts.sh",
    );
    promotion_breaks(&text, "P6", "must be told --app defer");
}

#[test]
fn p6_the_notes_carry_the_compatibility_line_once() {
    let text = change(
        PROMOTION,
        "Compatibility: no HTTP, CLI",
        "Compatibility: none",
    );
    promotion_breaks(&text, "P6", "exactly once, got 0");
}

#[test]
fn p6_the_verifier_receives_the_candidate_run_attempt() {
    let text = change_all(PROMOTION, "--candidate-run-attempt", "--candidate-attempt");
    promotion_breaks(&text, "P6", "candidate run attempt");
}

#[test]
fn p7_there_are_two_retags() {
    let text = change(
        PROMOTION,
        r#"docker buildx imagetools create --tag "${image_repo}:latest" "$root_ref""#,
        r#"echo skip "$root_ref""#,
    );
    promotion_breaks(&text, "P7", "exactly two imagetools create lines");
}

#[test]
fn p7_each_retag_takes_the_candidate_root() {
    let text = change(
        PROMOTION,
        r#"docker buildx imagetools create --tag "$semver_ref" "$root_ref""#,
        r#"docker buildx imagetools create --tag "$semver_ref" "$other_ref""#,
    );
    promotion_breaks(&text, "P7", "source the candidate root digest");
}

#[test]
fn a2_an_app_that_is_not_onboarded_has_no_candidate_workflow() {
    let mut fake = fake(CANDIDATE, PROMOTION);
    fake.apps[0].onboarded = false;
    let report = app_check::check(&fake, None).unwrap();
    assert_eq!(report.apps[0].findings.len(), 1);
    assert_eq!(report.apps[0].findings[0].rule, "A2");
    let name = defer().candidate_workflow();
    fake.files.remove(&name);
    assert!(app_check::check(&fake, None).unwrap().ok);
}

#[test]
fn w1_a_missing_workflow_is_a_finding() {
    let mut fake = fake(CANDIDATE, PROMOTION);
    let name = defer().promotion_workflow();
    fake.files.remove(&name);
    let report = app_check::check(&fake, None).unwrap();
    assert_eq!(report.apps[0].findings.len(), 1);
    assert_eq!(report.apps[0].findings[0].rule, "W1");
    assert!(report.apps[0].findings[0].message.contains("missing file"));
}

#[test]
fn both_workflows_are_reported_when_both_break() {
    let candidate = change(CANDIDATE, "--mode full", "--mode local");
    let promotion = change_all(PROMOTION, "--mode public", "--mode publ1c");
    let rules: Vec<&str> = findings(&candidate, &promotion)
        .iter()
        .map(|f| f.rule)
        .collect();
    assert_eq!(rules, ["C6", "P5"]);
}

#[test]
fn an_unknown_app_name_is_an_error() {
    let fake = fake(CANDIDATE, PROMOTION);
    assert!(app_check::check(&fake, Some("nope")).is_err());
}

fn git(dir: &Path, args: &[&str]) {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.test"])
        .args(args)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}");
}

/// A git checkout with `aw.toml` and the two workflows.
fn checkout(name: &str, toml: &str, candidate: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-contract-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join(".github/workflows")).unwrap();
    std::fs::write(dir.join("aw.toml"), toml).unwrap();
    let flows = dir.join(".github/workflows");
    std::fs::write(flows.join("defer-release-candidate.yml"), candidate).unwrap();
    std::fs::write(flows.join("defer-release.yml"), PROMOTION).unwrap();
    git(&dir, &["init", "-q"]);
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-q", "-m", "x"]);
    dir
}

fn toml() -> String {
    format!(
        "[release.apps.defer]\ntargets = [\"x86_64-unknown-linux-musl\", \"aarch64-unknown-linux-musl\"]\ncompatibility_line = \"{COMPATIBILITY}\"\n"
    )
}

fn run(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .arg("release-contract")
        .arg("--dir")
        .arg(dir)
        .args(args)
        .output()
        .unwrap()
}

#[test]
fn the_binary_passes_on_a_good_checkout() {
    let dir = checkout("pass", &toml(), CANDIDATE);
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "PASS defer");
}

#[test]
fn the_binary_exits_1_with_the_finding_when_a_rule_fails() {
    let candidate = change(CANDIDATE, "--mode full", "--mode local");
    let dir = checkout("fail", &toml(), &candidate);
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(1));
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.starts_with("FAIL defer\n  C6 "), "{text}");
}

#[test]
fn the_binary_prints_a_json_report() {
    let dir = checkout("json", &toml(), CANDIDATE);
    let out = run(&dir, &["--format", "json"]);
    let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(value["schema"], "release-contract-report/1");
    assert_eq!(value["ok"], true);
    assert_eq!(value["apps"][0]["app"], "defer");
}

#[test]
fn the_binary_stops_on_an_unknown_key_in_the_facts() {
    let dir = checkout("key", &format!("{}target = 1\n", toml()), CANDIDATE);
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(2));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains("release.apps.defer.target is not a known key"),
        "{err}"
    );
}

#[test]
fn the_binary_needs_the_compatibility_line_of_an_onboarded_shared_app() {
    let dir = checkout(
        "compat",
        "[release.apps.defer]\ntargets = [\"x86_64-unknown-linux-musl\"]\n",
        CANDIDATE,
    );
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(2));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("compatibility_line is required"), "{err}");
}

#[test]
fn the_binary_stops_when_aw_toml_has_no_release_app() {
    let dir = checkout("none", "[hygiene]\n", CANDIDATE);
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(2));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("no [release.apps.<app>] table"), "{err}");
}
