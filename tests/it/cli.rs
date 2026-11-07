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
