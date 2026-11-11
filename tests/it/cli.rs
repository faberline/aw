use std::process::Command;

fn aw(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(args)
        .env_remove("AW_AGENT")
        .env_remove("AW_REPO")
        .output()
        .unwrap()
}

#[test]
fn version_prints_the_package_version() {
    let out = aw(&["version"]);
    assert!(out.status.success());
    assert_eq!(
        String::from_utf8_lossy(&out.stdout).trim(),
        format!("aw {}", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn no_arguments_print_usage_and_fail() {
    let out = aw(&[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("Usage"));
}

#[test]
fn a_bad_agent_id_fails_before_touching_github() {
    let out = aw(&["claim", "--agent", "Not Valid"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("must use only"));
}

#[test]
fn report_rejects_an_unknown_outcome() {
    let out = aw(&["report", "--agent", "codex", "done"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("invalid value"));
}

/// Each outcome a skill tells a run to report is a value the binary takes.
/// `respec` was an outcome of the use case that the binary refused.
#[test]
fn report_takes_each_outcome_of_each_stage() {
    for outcome in [
        "ready", "split", "respec", "blocked", "passed", "failed", "closed",
    ] {
        // Read the arguments only: no request leaves this test.
        let args = ["aw", "report", "--agent", "claude", outcome, "-m", "x"];
        let parsed = aw::interfaces::work::cli::parse(args.iter().map(Into::into));
        assert!(parsed.is_ok(), "{outcome}");
    }
    let help = aw(&["report", "--help"]);
    let text = String::from_utf8_lossy(&help.stdout);
    for outcome in ["split", "respec"] {
        assert!(text.contains(outcome), "{outcome}: {text}");
    }
}

#[test]
fn claim_commands_need_an_agent() {
    for command in ["status", "release", "claim"] {
        let out = aw(&[command]);
        assert_eq!(out.status.code(), Some(1), "{command}");
        assert!(
            String::from_utf8_lossy(&out.stderr).contains("--agent"),
            "{command}"
        );
    }
}

#[test]
fn the_release_gate_needs_an_exact_milestone_tag_and_repository() {
    let usage: [&[&str]; 5] = [
        &["release-status"],
        &["release-status", "--milestone", "0"],
        &["release-advance", "--milestone", "8"],
        &["release-preflight", "--tag", "v0.5.0"],
        &["release-preflight", "--commit", "abc"],
    ];
    for args in usage {
        let out = aw(args);
        assert_eq!(out.status.code(), Some(1), "{args:?}");
        assert!(out.stdout.is_empty(), "{args:?}");
    }
    // A repository name that is not `owner/name` stops before any request.
    let calls: [&[&str]; 3] = [
        &["release-status", "--milestone", "8", "--repo", "o/r/x"],
        &[
            "release-advance",
            "--milestone",
            "8",
            "--expected",
            "0",
            "--repo",
            "o/r/x",
        ],
        &[
            "release-preflight",
            "--tag",
            "v0.5.0",
            "--commit",
            "main",
            "--repo",
            "o/r/x",
        ],
    ];
    for args in calls {
        let out = aw(args);
        assert_eq!(out.status.code(), Some(1), "{args:?}");
        assert!(out.stdout.is_empty(), "{args:?}");
        assert!(
            String::from_utf8_lossy(&out.stderr).contains("release:"),
            "{args:?}"
        );
    }
}

#[test]
fn stage_rejects_an_unknown_stage() {
    let out = aw(&["claim", "--agent", "claude", "--stage", "done"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("invalid value"));
}
