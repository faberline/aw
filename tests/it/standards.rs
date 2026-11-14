use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::Value;

const CORE_GIT: &str = "https://github.com/faberline/core";

fn git(dir: &Path, args: &[&str]) {
    let status = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.test"])
        .args(["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"])
        .args(args)
        .status()
        .unwrap();
    assert!(status.success(), "git {args:?}");
}

/// A git checkout in a fresh folder; `files` are (path, text), all tracked.
fn checkout(root: &Path, name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = root.join(name);
    std::fs::create_dir_all(&dir).unwrap();
    for (path, text) in files {
        let full = dir.join(path);
        std::fs::create_dir_all(full.parent().unwrap()).unwrap();
        std::fs::write(full, text).unwrap();
    }
    git(&dir, &["init", "-q"]);
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-q", "-m", "x"]);
    dir
}

/// A folder for one test, holding a `core` checkout tagged `v1.2.0` with
/// the crates `cli-std` and `build-stamp`.
fn world(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!("aw-it-std-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let pkg = |n: &str| format!("[package]\nname = \"{n}\"\nversion = \"0.1.0\"\n");
    let core = checkout(
        &root,
        "core",
        &[
            ("crates/cli-std/Cargo.toml", &pkg("cli-std")),
            ("crates/build-stamp/Cargo.toml", &pkg("build-stamp")),
        ],
    );
    git(&core, &["tag", "v1.0.0"]);
    git(&core, &["tag", "v1.2.0"]);
    git(&core, &["tag", "v1.10.0-rc.1"]);
    root
}

fn manifest(tag: &str) -> String {
    format!(
        "[workspace]\nmembers = [\"crates/*\"]\n\n[workspace.dependencies]\n\
         cli-std = {{ git = \"{CORE_GIT}\", tag = \"{tag}\" }}\n"
    )
}

const MEMBER: &str = "[package]\nname = \"m\"\nversion = \"0.1.0\"\n\n\
    [dependencies]\ncli-std.workspace = true\n";

fn check(root: &Path, repo: &str, extra: &[&str]) -> (Output, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["standards", "check", "--repo"])
        .arg(root.join(repo))
        .args(extra)
        .output()
        .unwrap();
    let text = String::from_utf8_lossy(&out.stdout).into_owned();
    (out, text)
}

#[test]
fn a_repository_that_follows_core_reuse_passes() {
    let root = world("pass");
    checkout(
        &root,
        "app",
        &[
            ("Cargo.toml", &manifest("v1.2.0")),
            ("crates/m/Cargo.toml", MEMBER),
            ("crates/m/src/main.rs", "fn main() {}\n"),
        ],
    );
    let (out, text) = check(&root, "app", &[]);
    assert_eq!(out.status.code(), Some(0), "{text}");
    assert!(text.contains("core v1.2.0 (2 crates)"), "{text}");
    assert!(text.contains("PASS"), "{text}");
}

#[test]
fn an_old_tag_a_branch_and_a_member_source_fail() {
    let root = world("fail");
    let member = format!(
        "[package]\nname = \"m\"\nversion = \"0.1.0\"\n\n[dependencies]\n\
         cli-std = {{ git = \"{CORE_GIT}\", branch = \"main\" }}\n"
    );
    checkout(
        &root,
        "app",
        &[
            ("Cargo.toml", &manifest("v1.0.0")),
            ("crates/m/Cargo.toml", &member),
            ("vendor/x/Cargo.toml", "this is not toml"),
        ],
    );
    let (out, text) = check(&root, "app", &["--format", "json"]);
    assert_eq!(out.status.code(), Some(1), "{text}");
    let report: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(report["schema"], "core-reuse-report/1");
    assert_eq!(report["ok"], false);
    let checks: Vec<&str> = report["repos"][0]["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["check"].as_str().unwrap())
        .collect();
    assert_eq!(checks, ["CR1", "CR3", "CR4"]);
}

#[test]
fn an_exception_with_a_reason_turns_a_finding_into_a_note() {
    let root = world("excepted");
    let aw_toml = "[[standards.core_reuse.exception]]\ncheck = \"CR3\"\n\
        reason = \"pinned until the index migration lands\"\n";
    checkout(
        &root,
        "app",
        &[
            ("Cargo.toml", &manifest("v1.0.0")),
            ("crates/m/Cargo.toml", MEMBER),
            ("aw.toml", aw_toml),
        ],
    );
    let (out, text) = check(&root, "app", &[]);
    assert_eq!(out.status.code(), Some(0), "{text}");
    assert!(
        text.contains("excepted CR3 Cargo.toml: pinned until the index migration lands"),
        "{text}"
    );
}

#[test]
fn an_exception_without_a_reason_is_cr0() {
    let root = world("cr0");
    checkout(
        &root,
        "app",
        &[
            ("Cargo.toml", &manifest("v1.2.0")),
            (
                "aw.toml",
                "[[standards.core_reuse.exception]]\ncheck = \"CR3\"\n",
            ),
        ],
    );
    let (out, text) = check(&root, "app", &[]);
    assert_eq!(out.status.code(), Some(1), "{text}");
    assert!(text.contains("CR0 aw.toml"), "{text}");
}

#[test]
fn a_hand_written_stamp_and_a_binary_without_cli_std_fail() {
    let root = world("stamp");
    checkout(
        &root,
        "app",
        &[
            (
                "Cargo.toml",
                "[package]\nname = \"a\"\nversion = \"0.1.0\"\n",
            ),
            ("src/main.rs", "fn main() {}\n"),
            (
                "build.rs",
                "fn main() { println!(\"cargo:rustc-env=APP_GIT_SHA=x\"); }\n",
            ),
        ],
    );
    let (out, text) = check(&root, "app", &[]);
    assert_eq!(out.status.code(), Some(1), "{text}");
    assert!(text.contains("CR5 Cargo.toml"), "{text}");
    assert!(text.contains("CR6 build.rs"), "{text}");
}

#[test]
fn core_is_checked_for_other_faberline_repositories() {
    let root = world("core");
    let bad = "[package]\nname = \"c\"\nversion = \"0.1.0\"\n\n[dependencies]\n\
        k = { git = \"https://github.com/faberline/keep\", tag = \"v1\" }\n";
    let core = root.join("core");
    std::fs::write(core.join("crates/cli-std/Cargo.toml"), bad).unwrap();
    git(&core, &["add", "-A"]);
    git(&core, &["commit", "-q", "-m", "y"]);
    let (out, text) = check(&root, "core", &["--core", core.to_str().unwrap()]);
    assert_eq!(out.status.code(), Some(1), "{text}");
    assert!(text.contains("CR8 crates/cli-std/Cargo.toml"), "{text}");
}

#[test]
fn a_folder_that_is_not_a_checkout_is_a_usage_error() {
    let root = world("usage");
    std::fs::create_dir_all(root.join("plain")).unwrap();
    let (out, _) = check(&root, "plain", &[]);
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("not a git checkout"));
}

#[test]
fn a_core_without_a_release_tag_is_a_usage_error() {
    let root = world("notag");
    checkout(&root, "app", &[("Cargo.toml", &manifest("v1.2.0"))]);
    let bare = checkout(&root, "other", &[("README", "x")]);
    let (out, _) = check(&root, "app", &["--core", bare.to_str().unwrap()]);
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("no vX.Y.Z tag"));
}

#[test]
fn help_lists_the_command() {
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["standards", "check", "--help"])
        .output()
        .unwrap();
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(
        text.contains("--core") && text.contains("--format"),
        "{text}"
    );
}
