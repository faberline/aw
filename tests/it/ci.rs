use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::Value;

const CI: &str = r#"name: ci
on:
  pull_request:
  push:
    branches: [main]
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  lint:
    name: "lint: fmt, clippy"
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: cargo fmt --all -- --check
      - run: cargo clippy --workspace --all-targets --locked -- -D warnings
  test-workspace:
    name: "test: workspace"
    needs: [lint]
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - run: cargo test --workspace --locked
  gate:
    name: gate
    if: always()
    needs: [lint, test-workspace]
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check every stage
        env:
          RESULTS: ${{ toJSON(needs) }}
        run: |
          echo "$RESULTS" | jq -e '.lint.result == "success"' > /dev/null
          echo "$RESULTS" | jq -e 'all(.[]; .result == "success" or .result == "skipped")' > /dev/null
"#;

/// A fresh directory with an optional `ci.yml` and `aw.toml`. Each test
/// uses its own name, so the directories never clash.
fn checkout(name: &str, ci: Option<&str>, aw_toml: Option<&str>) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-ci-{}-{name}", std::process::id()));
    let workflows = dir.join(".github/workflows");
    std::fs::create_dir_all(&workflows).unwrap();
    let workflow = workflows.join("ci.yml");
    match ci {
        Some(ci) => std::fs::write(&workflow, ci).unwrap(),
        None => assert!(!workflow.exists()),
    }
    if let Some(text) = aw_toml {
        std::fs::write(dir.join("aw.toml"), text).unwrap();
    }
    dir
}

fn check(dir: &Path, extra: &[&str]) -> (Output, Value) {
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["ci", "check", "--dir"])
        .arg(dir)
        .args(extra)
        .env_remove("AW_REPO")
        .output()
        .unwrap();
    let report = serde_json::from_slice(&out.stdout).unwrap_or(Value::Null);
    (out, report)
}

fn status<'a>(report: &'a Value, check: &str) -> &'a str {
    report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["check"] == check)
        .and_then(|f| f["status"].as_str())
        .unwrap()
}

#[test]
fn a_standard_ci_yml_passes_without_the_ruleset() {
    let dir = checkout("pass", Some(CI), None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert!(out.status.success(), "{report:#}");
    assert_eq!(report["passed"], true);
    assert_eq!(status(&report, "CI7"), "pass");
    assert_eq!(status(&report, "CI8"), "skipped");
}

#[test]
fn a_broken_ci_yml_prints_the_report_and_exits_1() {
    let broken = CI.replace("    needs: [lint]\n", "");
    let dir = checkout("broken", Some(&broken), None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(report["passed"], false);
    assert_eq!(status(&report, "CI5"), "fail");
}

#[test]
fn a_pull_request_workflow_without_concurrency_fails_ci10() {
    let start = CI.find("concurrency:").unwrap();
    let end = CI.find("jobs:").unwrap();
    let without = format!("{}{}", &CI[..start], &CI[end..]);
    let dir = checkout("noconc", Some(&without), None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "CI10"), "fail");
    assert_eq!(status(&report, "CI11"), "pass");
}

#[test]
fn a_concurrency_group_that_never_cancels_fails_ci10() {
    let never = CI.replace(
        "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        "cancel-in-progress: false",
    );
    let dir = checkout("nocancel", Some(&never), None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "CI10"), "fail");
}

#[test]
fn a_job_without_timeout_fails_ci11() {
    let without = CI.replace("    timeout-minutes: 5\n", "");
    let dir = checkout("notimeout", Some(&without), None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "CI11"), "fail");
    assert_eq!(status(&report, "CI10"), "pass");
}

#[test]
fn a_missing_ci_yml_fails_check_ci1() {
    let dir = checkout("missing", None, None);
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "CI1"), "fail");
    assert_eq!(status(&report, "CI2"), "skipped");
}

#[test]
fn an_aw_toml_exception_is_excepted() {
    let renamed = CI.replace("name: ci\n", "name: rust\n");
    let toml = "[[standards.ci.exception]]\ncheck = \"CI2\"\nreason = \"renamed next release\"\n";
    let dir = checkout("excepted", Some(&renamed), Some(toml));
    let (out, report) = check(&dir, &["--no-ruleset"]);
    assert!(out.status.success(), "{report:#}");
    assert_eq!(status(&report, "CI2"), "excepted");
}

#[test]
fn the_ruleset_needs_a_github_repository() {
    let dir = checkout("norepo", Some(CI), None);
    let (out, _) = check(&dir, &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("--no-ruleset"));
}
