//! `aw release-contract`: each rule C1 to C8, P1 to P7 and A2 has a failing
//! fixture. The passing fixture is the real pair of defer workflows (defer
//! `main`, 2026-11-20), which the Python script `verify-release-contract.py`
//! also accepts. A failing fixture changes one thing in that text.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use aw::domain::release::app_check::{self, ContractRepo};
use aw::domain::release::app_rules::Fail;
use aw::domain::release::app_tables::{Maker, MakerApp};
use aw::domain::release::facts::AppFacts;
use aw::domain::work::ports::PortResult;

const CANDIDATE: &str = include_str!("release_contract_candidate.yml");
const PROMOTION: &str = include_str!("release_contract_promotion.yml");
const COMPATIBILITY: &str = "- Compatibility: no HTTP, CLI, wire format, on-disk format, or Kubernetes manifest shape changed in this release.";

const CI: &str = include_str!("release_contract_ci.yml");
const ACCEPTANCE: &str = include_str!("release_contract_acceptance.yml");
const APPS_SH: &str = include_str!("release_contract_apps.sh");
const MAKER: &str = include_str!("release_contract_maker.py");

/// What the repository script `apps.sh` prints for `defer`, as the facts say.
fn shell_answer(app: &AppFacts, function: &str) -> Option<Vec<String>> {
    let one = |v: &str| Some(vec![v.to_string()]);
    match function {
        "release_app_root" => one(&app.root),
        "release_app_scripts_dir" => one(&app.scripts_dir),
        "release_app_targets" => Some(app.targets.clone()),
        "release_app_gke_backend" => one(&app.gke_backend),
        "release_app_receipt_name" => one(&app.receipt_name),
        "release_app_receipt_schema" => one(&app.receipt_schema),
        "release_app_promotion_input_prefix" => one(&app.promotion_prefix),
        "release_app_manifest_schema" => one(&app.manifest_schema),
        "release_app_candidate_jobs" => Some(app.candidate_jobs.clone()),
        "release_app_functional_fields" => Some(app.functional.clone()),
        _ => None,
    }
}

struct Fake {
    files: BTreeMap<String, String>,
    apps: Vec<AppFacts>,
    /// Answers that differ from the facts: function -> lines.
    shell: BTreeMap<String, Vec<String>>,
    /// Predicates that differ from the facts: function -> answer.
    predicate: BTreeMap<String, bool>,
    maker: Maker,
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
    fn shell_values(&self, _: &str, function: &str, _: &str) -> Result<Vec<String>, String> {
        if let Some(lines) = self.shell.get(function) {
            return Ok(lines.clone());
        }
        shell_answer(&self.apps[0], function).ok_or_else(|| format!("no function {function}"))
    }
    fn shell_predicate(&self, _: &str, function: &str, app: &str) -> Result<bool, String> {
        if let Some(answer) = self.predicate.get(function) {
            return Ok(*answer);
        }
        Ok(match function {
            "release_app_known" => app != "loom",
            "release_app_uses_shared_scripts" => self.apps[0].shared_scripts,
            _ => self.apps[0].takes_attempt,
        })
    }
    fn maker(&self, _: &str, _: &[String]) -> Result<Maker, String> {
        Ok(self.maker.clone())
    }
}

fn defer() -> AppFacts {
    let mut app = AppFacts::shared("defer");
    app.targets = vec![
        "x86_64-unknown-linux-musl".into(),
        "aarch64-unknown-linux-musl".into(),
    ];
    app.compatibility_line = COMPATIBILITY.into();
    app.gates_step = "Run declared Defer gates".into();
    app.acceptance_run_name =
        "run-name: gke-acceptance {app} @ ${{ inputs.commit }} image=${{ inputs.image }}".into();
    app.acceptance_names = ACCEPTANCE_NAMES.map(String::from).to_vec();
    app.maker_constants = [
        ("ACCEPTANCE_JOB", ACCEPTANCE_NAMES[0]),
        ("VERIFY_IMAGE_STEP", ACCEPTANCE_NAMES[1]),
        ("HARNESS_STEP", ACCEPTANCE_NAMES[2]),
        ("PARK_STEP", ACCEPTANCE_NAMES[3]),
    ]
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .into();
    app.candidate_gates = CANDIDATE_GATES.map(String::from).to_vec();
    app.ci_gates = CI_GATES.map(String::from).to_vec();
    app
}

const ACCEPTANCE_NAMES: [&str; 4] = [
    "deploy + verify on GKE",
    "Verify prebuilt image input",
    "Run acceptance harness",
    "Park node pool (belt and suspenders)",
];
const CANDIDATE_GATES: [&str; 6] = [
    "cargo fmt --all -- --check",
    "cargo clippy --locked --workspace --all-targets --all-features -- -D warnings",
    "cargo test --locked --workspace",
    "cargo test --locked -p defer --features operator --test it -- operator::",
    "python3 -B scripts/meta/project_docs_contract.py check .",
    "uv run --python 3.13 --no-project scripts/release/verify-release-contract.py --app defer",
];
const CI_GATES: [&str; 5] = [
    "cargo fmt --all -- --check",
    "cargo clippy --workspace --all-targets --locked -- -D warnings",
    "cargo test --locked --workspace",
    "cargo test --locked -p defer --features operator --test it -- operator::",
    "python3 -B scripts/meta/project_docs_contract.py check .",
];

/// The maker tables that match `defer()`.
fn maker_of(app: &AppFacts) -> Maker {
    Maker {
        apps: [(
            app.name.clone(),
            MakerApp {
                targets: app.targets.clone(),
                backend: app.gke_backend.clone(),
                functional: app.functional.clone(),
                candidate_schema: app.manifest_schema.clone(),
                receipt_schema: app.receipt_schema.clone(),
                final_jobs: app.candidate_jobs.clone(),
            },
        )]
        .into(),
        constants: [
            ("REPOSITORY", app.repository.as_str()),
            ("IMAGE_OWNER", app.image_owner.as_str()),
            ("ACCEPTANCE_JOB", ACCEPTANCE_NAMES[0]),
            ("VERIFY_IMAGE_STEP", ACCEPTANCE_NAMES[1]),
            ("HARNESS_STEP", ACCEPTANCE_NAMES[2]),
            ("PARK_STEP", ACCEPTANCE_NAMES[3]),
        ]
        .map(|(k, v)| (k.to_string(), Some(v.to_string())))
        .into(),
    }
}

fn fake_with(candidate: &str, promotion: &str, ci: &str, acceptance: &str) -> Fake {
    let app = defer();
    let files = [
        (app.candidate_workflow(), candidate.to_string()),
        (app.promotion_workflow(), promotion.to_string()),
        (".github/workflows/ci.yml".to_string(), ci.to_string()),
        (
            ".github/workflows/gke-acceptance.yml".to_string(),
            acceptance.to_string(),
        ),
        ("scripts/release/apps.sh".to_string(), String::new()),
        (
            "scripts/release/make-gke-release-receipt.py".to_string(),
            String::new(),
        ),
    ];
    Fake {
        files: files.into_iter().collect(),
        maker: maker_of(&app),
        apps: vec![app],
        shell: BTreeMap::new(),
        predicate: BTreeMap::new(),
    }
}

fn fake(candidate: &str, promotion: &str) -> Fake {
    fake_with(candidate, promotion, CI, ACCEPTANCE)
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
    std::fs::write(flows.join("ci.yml"), CI).unwrap();
    std::fs::write(flows.join("gke-acceptance.yml"), ACCEPTANCE).unwrap();
    std::fs::create_dir_all(dir.join("scripts/release")).unwrap();
    std::fs::write(dir.join("scripts/release/apps.sh"), APPS_SH).unwrap();
    std::fs::write(
        dir.join("scripts/release/make-gke-release-receipt.py"),
        MAKER,
    )
    .unwrap();
    git(&dir, &["init", "-q"]);
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-q", "-m", "x"]);
    dir
}

fn quoted(items: &[&str]) -> String {
    let items: Vec<String> = items.iter().map(|i| format!("{i:?}")).collect();
    format!("[{}]", items.join(", "))
}

fn toml() -> String {
    let names = quoted(&ACCEPTANCE_NAMES);
    format!(
        r#"[release.apps.defer]
targets = ["x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl"]
compatibility_line = "{COMPATIBILITY}"
gates_step = "Run declared Defer gates"
acceptance_run_name = "run-name: gke-acceptance {{app}} @ ${{{{ inputs.commit }}}} image=${{{{ inputs.image }}}}"
acceptance_names = {names}
candidate_gates = {}
ci_gates = {}

[release.apps.defer.maker_constants]
ACCEPTANCE_JOB = "deploy + verify on GKE"
VERIFY_IMAGE_STEP = "Verify prebuilt image input"
HARNESS_STEP = "Run acceptance harness"
PARK_STEP = "Park node pool (belt and suspenders)"
"#,
        quoted(&CANDIDATE_GATES),
        quoted(&CI_GATES)
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
    let dir = checkout(
        "key",
        &change(
            &toml(),
            "[release.apps.defer]\n",
            "[release.apps.defer]\ntarget = 1\n",
        ),
        CANDIDATE,
    );
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

// ---- PR2: A1, G1, G2, T1, T2 -------------------------------------------------
//
// Python control -> Rust test (verify-release-contract.py `standalone_controls`
// and the A2 control of `self_test`):
//
// | Python control                                      | Rust test                                  |
// |-----------------------------------------------------|--------------------------------------------|
// | defer candidate: gate dropped (x5)                  | g1_each_candidate_gate_is_required         |
// | ci: gate dropped (x5)                               | g2_each_ci_gate_is_required                |
// | defer candidate: release contract no longer checked | g1_the_contract_check_stays_in_the_gates   |
// | ci: pull_request trigger dropped                    | g2_ci_runs_on_pull_request_and_push        |
// | ci: push no longer limited to main                  | g2_push_is_limited_to_main                 |
// | ci: action unpinned                                 | g2_ci_actions_are_pinned                   |
// | ci: write permission granted                        | g2_ci_is_read_only                         |
// | defer acceptance workflow: push trigger added       | a1_the_acceptance_workflow_has_no_push     |
// | table: a non-onboarded app is checked as onboarded  | a2_an_app_that_is_not_onboarded_has_no_candidate_workflow |

fn with_ci(ci: &str) -> Vec<Fail> {
    let fake = fake_with(CANDIDATE, PROMOTION, ci, ACCEPTANCE);
    app_check::check(&fake, None).unwrap().apps[0]
        .findings
        .clone()
}

fn with_acceptance(text: &str) -> Vec<Fail> {
    let fake = fake_with(CANDIDATE, PROMOTION, CI, text);
    app_check::check(&fake, None).unwrap().apps[0]
        .findings
        .clone()
}

fn only(found: Vec<Fail>, rule: &str, part: &str) {
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].rule, rule, "{found:?}");
    assert!(found[0].message.contains(part), "{found:?}");
}

#[test]
fn the_good_defer_files_pass_every_rule() {
    let fake = fake(CANDIDATE, PROMOTION);
    let report = app_check::check(&fake, None).unwrap();
    assert!(report.ok, "{report:?}");
}

#[test]
fn a1_the_acceptance_workflow_has_no_push() {
    let text = change(
        ACCEPTANCE,
        "workflow_dispatch:",
        "workflow_dispatch:\n  push:",
    );
    only(
        with_acceptance(&text),
        "A1",
        "a trigger other than workflow_dispatch",
    );
}

#[test]
fn a1_the_run_name_matches_the_receipt() {
    let text = change(ACCEPTANCE, "image=${{ inputs.image }}", "image=x");
    only(with_acceptance(&text), "A1", "run-name does not match");
}

#[test]
fn a1_the_top_level_name_is_gke_acceptance() {
    let text = change(ACCEPTANCE, "\nname: gke-acceptance\n", "\nname: other\n");
    only(with_acceptance(&text), "A1", "top-level name");
}

#[test]
fn a1_the_job_and_steps_are_named() {
    let text = change(ACCEPTANCE, "name: Run acceptance harness", "name: Run it");
    only(
        with_acceptance(&text),
        "A1",
        "missing name: Run acceptance harness",
    );
}

#[test]
fn a1_a_missing_acceptance_workflow_is_a_finding() {
    let mut fake = fake(CANDIDATE, PROMOTION);
    fake.files.remove(".github/workflows/gke-acceptance.yml");
    let found = app_check::check(&fake, None).unwrap().apps[0]
        .findings
        .clone();
    only(found, "W1", "gke-acceptance.yml");
}

#[test]
fn g1_each_candidate_gate_is_required() {
    for gate in CANDIDATE_GATES {
        let text = change(CANDIDATE, gate, "true");
        let found = findings(&text, PROMOTION);
        assert!(
            found
                .iter()
                .any(|f| f.rule == "G1" && f.message.contains(gate)),
            "{gate}: {found:?}"
        );
    }
}

#[test]
fn g1_the_contract_check_stays_in_the_gates() {
    let text = change(
        CANDIDATE,
        "verify-release-contract.py --app defer",
        "verify-release-contract.py --self-test",
    );
    let found = findings(&text, PROMOTION);
    assert!(
        found
            .iter()
            .any(|f| f.rule == "G1" && f.message.contains("--app defer")),
        "{found:?}"
    );
}

#[test]
fn g2_each_ci_gate_is_required() {
    for gate in CI_GATES {
        let text = change(CI, gate, "true");
        let found = with_ci(&text);
        assert!(
            found
                .iter()
                .any(|f| f.rule == "G2" && f.message.contains(gate)),
            "{gate}: {found:?}"
        );
    }
}

#[test]
fn g2_ci_runs_on_pull_request_and_push() {
    let text = change(CI, "  pull_request:\n", "  workflow_dispatch:\n");
    only(
        with_ci(&text),
        "G2",
        "triggers must be pull_request and push",
    );
}

#[test]
fn g2_push_is_limited_to_main() {
    let text = change(CI, "    branches: [main]", "    branches: ['**']");
    only(with_ci(&text), "G2", "push must be limited to main");
}

#[test]
fn g2_ci_actions_are_pinned() {
    let text = change(
        CI,
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
    );
    only(with_ci(&text), "G2", "action not pinned");
}

#[test]
fn g2_ci_is_read_only() {
    let text = change(
        CI,
        "permissions:\n  contents: read",
        "permissions:\n  contents: write",
    );
    only(with_ci(&text), "G2", "read-only contents permission");
}

fn table_breaks(f: impl FnOnce(&mut Fake), rule: &str, part: &str) {
    let mut fake = fake(CANDIDATE, PROMOTION);
    f(&mut fake);
    let found = app_check::check(&fake, None).unwrap().apps[0]
        .findings
        .clone();
    only(found, rule, part);
}

#[test]
fn t1_apps_sh_names_the_targets_of_the_facts() {
    table_breaks(
        |f| {
            f.shell.insert(
                "release_app_targets".into(),
                vec!["x86_64-unknown-linux-musl".into()],
            );
        },
        "T1",
        "release_app_targets defer",
    );
}

#[test]
fn t1_apps_sh_agrees_on_the_shared_scripts() {
    table_breaks(
        |f| {
            f.predicate
                .insert("release_app_uses_shared_scripts".into(), false);
        },
        "T1",
        "uses the shared scripts",
    );
}

#[test]
fn t1_apps_sh_does_not_know_loom() {
    table_breaks(
        |f| {
            f.predicate.insert("release_app_known".into(), true);
        },
        "T1",
        "must not know loom",
    );
}

#[test]
fn t1_a_missing_apps_sh_is_a_finding() {
    table_breaks(
        |f| {
            f.files.remove("scripts/release/apps.sh");
        },
        "T1",
        "missing file",
    );
}

#[test]
fn t2_the_receipt_maker_names_the_targets_of_the_facts() {
    table_breaks(
        |f| {
            f.maker.apps.get_mut("defer").unwrap().targets.pop();
        },
        "T2",
        "targets for defer differ",
    );
}

#[test]
fn t2_the_receipt_maker_knows_only_the_shared_apps() {
    table_breaks(
        |f| {
            let entry = f.maker.apps["defer"].clone();
            f.maker.apps.insert("loom".into(), entry);
        },
        "T2",
        "receipt maker knows",
    );
}

#[test]
fn t2_a_constant_of_the_receipt_maker_matches() {
    table_breaks(
        |f| {
            f.maker
                .constants
                .insert("HARNESS_STEP".into(), Some("Run it".into()));
        },
        "T2",
        "HARNESS_STEP",
    );
}

#[test]
fn t2_the_job_inventory_matches() {
    table_breaks(
        |f| {
            f.maker.apps.get_mut("defer").unwrap().final_jobs.pop();
        },
        "T2",
        "job inventory",
    );
}

#[test]
fn the_binary_reads_apps_sh_with_bash() {
    let dir = checkout("apps-sh", &toml(), CANDIDATE);
    let path = dir.join("scripts/release/apps.sh");
    let text = std::fs::read_to_string(&path).unwrap();
    std::fs::write(
        &path,
        text.replace(
            "x86_64-unknown-linux-musl aarch64-unknown-linux-musl",
            "x86_64-unknown-linux-musl",
        ),
    )
    .unwrap();
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(1));
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(
        text.contains("T1 apps.sh release_app_targets defer"),
        "{text}"
    );
}

#[test]
fn the_binary_reads_the_receipt_maker_with_python() {
    let dir = checkout("maker", &toml(), CANDIDATE);
    let path = dir.join("scripts/release/make-gke-release-receipt.py");
    let text = std::fs::read_to_string(&path).unwrap();
    std::fs::write(&path, text.replace("Run acceptance harness", "Run it")).unwrap();
    let out = run(&dir, &[]);
    assert_eq!(out.status.code(), Some(1));
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("T2 receipt maker HARNESS_STEP"), "{text}");
}
