//! The coverage ratchet script of standard S8,
//! `.github/actions/coverage/check.sh`. It needs bash, jq and python3 3.11
//! or later, as on the GitHub runners.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::{Value, json};

const SCRIPT: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/.github/actions/coverage/check.sh"
);

/// A fresh directory with a `cargo llvm-cov --json --summary-only` file.
/// `files` are (path, lines, covered lines).
fn workspace(name: &str, files: &[(&str, u64, u64)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-cov-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let root = dir.display().to_string();
    let files: Vec<Value> = files
        .iter()
        .map(|(path, count, covered)| {
            json!({
                "filename": format!("{root}/{path}"),
                "summary": {"lines": {
                    "count": count,
                    "covered": covered,
                    "percent": *covered as f64 / *count as f64 * 100.0,
                }},
            })
        })
        .collect();
    let summary = json!({"data": [{"files": files}], "type": "llvm.coverage.json.export"});
    std::fs::write(dir.join("summary.json"), summary.to_string()).unwrap();
    dir
}

fn write(dir: &Path, name: &str, text: &str) -> String {
    let path = dir.join(name);
    std::fs::write(&path, text).unwrap();
    path.display().to_string()
}

fn check(dir: &Path, config: &str, base: Option<&str>) -> (Output, Value) {
    let mut command = Command::new("bash");
    command
        .arg(SCRIPT)
        .arg(dir.join("summary.json"))
        .arg(config)
        .args(base)
        .env("COVERAGE_ROOT", dir)
        .env_remove("GITHUB_STEP_SUMMARY");
    let out = command.output().unwrap();
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

const FILES: &[(&str, u64, u64)] = &[("src/a.rs", 100, 90), ("src/main.rs", 100, 60)];

#[test]
fn a_baseline_at_the_measured_value_passes() {
    let dir = workspace("pass", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.0\ntarget = 100\n",
    );
    let base = write(
        &dir,
        "base.toml",
        "[standards.coverage]\nline = 74.5\ntarget = 100\n",
    );
    let (out, report) = check(&dir, &config, Some(&base));
    assert!(out.status.success(), "{report}");
    assert_eq!(report["measured"], 75.0);
    for id in ["COV1", "COV2", "COV3", "COV4", "COV5"] {
        assert_eq!(status(&report, id), "pass", "{id}: {report}");
    }
    assert_eq!(report["most_uncovered"][0]["file"], "src/main.rs");
    assert_eq!(report["most_uncovered"][0]["uncovered"], 40);
}

#[test]
fn coverage_below_the_baseline_fails_cov3() {
    let dir = workspace("drop", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.2\ntarget = 100\n",
    );
    let (out, report) = check(&dir, &config, None);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "COV3"), "fail");
    assert_eq!(status(&report, "COV5"), "skipped");
}

#[test]
fn a_drop_inside_the_tolerance_passes() {
    let dir = workspace("noise", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.1\ntarget = 100\n",
    );
    let (out, report) = check(&dir, &config, None);
    assert!(out.status.success(), "{report}");
}

#[test]
fn a_stale_baseline_fails_cov4_with_the_new_value() {
    let dir = workspace("stale", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 74.0\ntarget = 100\n",
    );
    let (out, report) = check(&dir, &config, None);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "COV4"), "fail");
    let detail = report["findings"][3]["detail"].as_str().unwrap();
    assert!(detail.contains("raise line to 75"), "{detail}");
}

#[test]
fn a_lower_baseline_than_the_base_fails_cov5() {
    let dir = workspace("lowered", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.0\ntarget = 100\n",
    );
    let base = write(
        &dir,
        "base.toml",
        "[standards.coverage]\nline = 80.0\ntarget = 100\n",
    );
    let (out, report) = check(&dir, &config, Some(&base));
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "COV5"), "fail");
}

#[test]
fn a_base_without_the_section_skips_cov5() {
    let dir = workspace("first", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.0\ntarget = 100\n",
    );
    let missing = dir.join("no-base.toml").display().to_string();
    let (out, report) = check(&dir, &config, Some(&missing));
    assert!(out.status.success(), "{report}");
    assert_eq!(status(&report, "COV5"), "skipped");
}

#[test]
fn no_section_fails_cov1_and_names_the_measured_value() {
    let dir = workspace("none", FILES);
    let config = write(&dir, "aw.toml", "[project]\nname = \"x\"\n");
    let (out, report) = check(&dir, &config, None);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "COV1"), "fail");
    let detail = report["findings"][0]["detail"].as_str().unwrap();
    assert!(detail.contains("line = 75"), "{detail}");
}

#[test]
fn a_target_other_than_100_fails_cov1() {
    let dir = workspace("target", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 75.0\ntarget = 80\n",
    );
    let (_, report) = check(&dir, &config, None);
    assert_eq!(status(&report, "COV1"), "fail");
}

#[test]
fn an_exclude_removes_its_files_from_the_total() {
    let dir = workspace("exclude", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 90.0\ntarget = 100\n\n\
         [[standards.coverage.exclude]]\npath = '^src/main\\.rs$'\nreason = \"covered by e2e\"\n",
    );
    let (out, report) = check(&dir, &config, None);
    assert!(out.status.success(), "{report}");
    assert_eq!(report["measured"], 90.0);
    assert_eq!(report["excluded_files"], 1);
    assert_eq!(report["new_excludes"][0], "^src/main\\.rs$");
}

#[test]
fn an_exclude_without_a_reason_fails_cov2() {
    let dir = workspace("reason", FILES);
    let config = write(
        &dir,
        "aw.toml",
        "[standards.coverage]\nline = 90.0\ntarget = 100\n\n\
         [[standards.coverage.exclude]]\npath = '^src/main\\.rs$'\n",
    );
    let (out, report) = check(&dir, &config, None);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(status(&report, "COV2"), "fail");
}
