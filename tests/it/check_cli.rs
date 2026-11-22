//! `aw claim --check` through the binary, with a fake `gh`: the line on
//! stdout and the exit code that a routine prompt tests.

use std::process::Command;

const ISSUE: &str = r#"{"number":3,"title":"t","url":"u","labels":["agent:claude","stage:spec"],"created_at":"2026-01-01T00:00:00Z","milestone":null}"#;

fn run(stage: &str) -> (i32, String) {
    let dir = std::env::temp_dir().join(format!("aw-check-cli-{}-{stage}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let fake = format!(
        r#"#!/bin/bash
case "$2" in
  "repos/o/r") echo '{{"full_name":"o/r","default_branch":"main"}}' ;;
  "repos/o/r/issues?state=open&labels="*) printf '1\n%s\n' '{ISSUE}' ;;
  "repos/o/r/issues?state=open&per_page"*) printf '1\n%s\n' '{ISSUE}' ;;
  "repos/o/r/issues/3") echo '{{"body":"","labels":[],"milestone":null}}' ;;
  *) echo 0 ;;
esac
"#
    );
    std::fs::write(dir.join("gh"), fake).unwrap();
    Command::new("chmod")
        .arg("+x")
        .arg(dir.join("gh"))
        .status()
        .unwrap();
    let path = format!("{}:{}", dir.display(), std::env::var("PATH").unwrap());
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["claim", "--check", "--agent", "claude", "--repo", "o/r"])
        .args(["--stage", stage])
        .env("PATH", path)
        .env_remove("AW_STAGE")
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    (
        out.status.code().unwrap(),
        String::from_utf8(out.stdout).unwrap(),
    )
}

#[test]
fn work_exits_0_and_no_work_exits_2() {
    let (code, line) = run("spec");
    assert_eq!(code, 0, "{line}");
    assert_eq!(
        line.trim(),
        "work stage=spec reason=claim issue=3 review_waiting=0 tdd_waiting=0"
    );
    let (code, line) = run("tdd");
    assert_eq!(code, 2, "{line}");
    assert_eq!(
        line.trim(),
        "no-work stage=tdd reason=empty-queue review_waiting=0 tdd_waiting=0"
    );
}
