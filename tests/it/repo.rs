//! `aw repo check`: each rule has a passing and a failing fixture against an
//! in-memory file list; the ratchet and the config are tested the same way;
//! the binary runs on real git checkouts.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use aw::domain::repo::ports::{Allow, Config, Entry, RepoFiles};
use aw::domain::repo::rules::{self, Violation};
use aw::domain::work::ports::PortResult;
use aw::infrastructure::repo::parse_config;

/// Files by path: the size and the text.
#[derive(Default)]
struct Files(BTreeMap<String, (u64, Option<String>)>);

impl Files {
    fn with(paths: &[&str]) -> Self {
        let mut files = Files::default();
        for path in paths {
            files = files.text(path, "");
        }
        files
    }

    fn text(mut self, path: &str, text: &str) -> Self {
        self.0.insert(
            path.to_string(),
            (text.len() as u64, Some(text.to_string())),
        );
        self
    }

    fn sized(mut self, path: &str, size: u64) -> Self {
        self.0.insert(path.to_string(), (size, None));
        self
    }
}

impl RepoFiles for Files {
    fn config(&self) -> PortResult<Config> {
        Ok(Config::default())
    }

    fn entries(&self) -> PortResult<Vec<Entry>> {
        Ok(self
            .0
            .iter()
            .map(|(path, (size, _))| Entry {
                path: path.clone(),
                size: *size,
            })
            .collect())
    }

    fn text(&self, path: &str) -> PortResult<Option<String>> {
        Ok(self.0.get(path).and_then(|(_, text)| text.clone()))
    }
}

fn allow(path: &str) -> Allow {
    Allow {
        path: path.to_string(),
        reason: "test".to_string(),
    }
}

fn scan(files: &Files, config: &Config) -> Vec<Violation> {
    rules::scan(files, config).unwrap()
}

fn keys(files: &Files, config: &Config) -> Vec<String> {
    scan(files, config).iter().map(Violation::key).collect()
}

fn home(user: &str) -> String {
    format!("/{}/{user}/project", "Users")
}

#[test]
fn rh1_passes_the_built_in_root_entries() {
    let files = Files::with(&[
        "README.md",
        "LICENSE-MIT",
        "Dockerfile.ci",
        ".dockerignore",
        "Cargo.toml",
        "src/lib.rs",
        "crates/a/src/lib.rs",
        "docs/a.md",
        ".github/workflows/ci.yml",
    ]);
    assert!(keys(&files, &Config::default()).is_empty());
}

#[test]
fn rh1_reports_one_violation_for_each_root_entry() {
    let files = Files::with(&["STATUS.md", "extra/a.txt", "extra/b.txt", "extra/c/d.txt"]);
    assert_eq!(
        keys(&files, &Config::default()),
        ["RH1:STATUS.md", "RH1:extra"]
    );
}

#[test]
fn rh1_lets_an_extra_root_entry_through() {
    let files = Files::with(&["infra/a.yaml", "install.sh"]);
    let config = Config {
        root: vec![allow("infra"), allow("install.sh")],
        ..Config::default()
    };
    assert!(keys(&files, &config).is_empty());
}

#[test]
fn rh2_reports_each_forbidden_name() {
    let bad = [
        "RUST_HANDOFF.md",
        "docs/M1_CLOSEOUT.md",
        "docs/RUST_REPORT.md",
        "docs/M3_STATUS.md",
        "docs/PLAN.md",
        "docs/NOTES.md",
        "src/a.rs.bak",
        "src/a.rs.orig",
        "docs/.codex/agents/a.md",
        "docs/.agy/a.md",
        "src/tmp/a.rs",
        "src/scratch/a.rs",
        "src/old/a.rs",
    ];
    for path in bad {
        let found = scan(&Files::with(&[path]), &Config::default());
        assert!(
            found.iter().any(|v| v.rule == "RH2" && v.path == path),
            "{path} should break RH2"
        );
    }
}

#[test]
fn rh2_passes_similar_names() {
    let files = Files::with(&[
        "docs/STATUS.md",
        "docs/plan.md",
        "docs/planning.md",
        "src/template.rs",
        "src/older.rs",
        "docs/status-report.md",
    ]);
    assert!(keys(&files, &Config::default()).is_empty());
}

#[test]
fn rh2_names_the_reason_in_the_detail() {
    let by_name = scan(&Files::with(&["docs/NOTES.md"]), &Config::default());
    assert!(by_name[0].detail.contains("NOTES.md"));
    let by_dir = scan(&Files::with(&["src/tmp/a.rs"]), &Config::default());
    assert!(by_dir[0].detail.contains("tmp/"));
}

#[test]
fn rh3_reports_a_home_path_with_its_line() {
    let line = format!("PATH=a\nPATH={}/bin\n", home("alice"));
    let files = Files::default().text("docs/a.md", &line);
    let found = scan(&files, &Config::default());
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].rule, "RH3");
    assert!(found[0].detail.contains("line 2"));
    let linux = Files::default().text("a.txt", &format!("/{}/bob/x", "home"));
    assert_eq!(keys(&linux, &Config::default()), ["RH1:a.txt", "RH3:a.txt"]);
}

#[test]
fn rh3_passes_placeholders_and_binary_files() {
    let text = format!("{} {}\n/home/runner/work\n", home("<name>"), home("you"));
    let files = Files::default()
        .text("docs/a.md", &text)
        .sized("docs/b.bin", 10);
    assert!(keys(&files, &Config::default()).is_empty());
}

#[test]
fn rh3_passes_an_allow_listed_fixture_and_skips_huge_files() {
    let text = home("alice");
    let files = Files::default()
        .text("tests/fixture.txt", &text)
        .text("docs/big.md", &text);
    let config = Config {
        home_path: vec![allow("tests/fixture.txt"), allow("docs")],
        ..Config::default()
    };
    assert!(keys(&files, &config).is_empty());
    let mut huge = Files::default();
    huge.0
        .insert("docs/huge.md".into(), (3 * 1024 * 1024, Some(text)));
    let config = Config {
        max_file_bytes: u64::MAX,
        ..Config::default()
    };
    assert!(keys(&huge, &config).is_empty());
}

#[test]
fn rh4_reports_dated_and_per_issue_names() {
    let bad = [
        "docs/restore-drill-2026-11-13.md",
        "docs/issue42-notes.md",
        "docs/Issue-42.md",
        "docs/issue_7.md",
        "docs/restore-2026-11-13/a.md",
    ];
    for path in bad {
        let found = scan(&Files::with(&[path]), &Config::default());
        assert!(
            found.iter().any(|v| v.rule == "RH4" && v.path == path),
            "{path} should break RH4"
        );
    }
}

#[test]
fn rh4_passes_other_names_and_evidence_folders() {
    let files = Files::with(&[
        "docs/issues.md",
        "docs/issue-tracker.md",
        "docs/2026-plan.md",
        "evidence/run-2026-11-13.json",
        "evidence/issue12/a.json",
    ]);
    let config = Config {
        root: vec![allow("evidence")],
        evidence: vec![allow("evidence/")],
        ..Config::default()
    };
    assert!(keys(&files, &config).is_empty());
}

#[test]
fn rh5_reports_a_file_above_the_limit() {
    let files = Files::default()
        .sized("docs/big.md", 300_000)
        .sized("docs/ok.md", 262_144)
        .sized("Cargo.lock", 9_000_000);
    assert_eq!(keys(&files, &Config::default()), ["RH5:docs/big.md"]);
}

#[test]
fn rh5_follows_the_limit_and_the_allow_list() {
    let files = Files::default()
        .sized("docs/a.md", 2_000)
        .sized("tests/data/corpus.bin", 2_000);
    let small = Config {
        max_file_bytes: 1_000,
        size: vec![allow("tests/data")],
        ..Config::default()
    };
    assert_eq!(keys(&files, &small), ["RH5:docs/a.md"]);
}

#[test]
fn the_baseline_lets_a_known_violation_pass() {
    let files = Files::with(&["docs/M1_CLOSEOUT.md"]);
    let config = Config {
        baseline: vec!["RH2:docs/M1_CLOSEOUT.md".into()],
        ..Config::default()
    };
    let report = rules::check(&files, &config).unwrap();
    assert!(report.ok);
    assert_eq!(report.baselined, 1);
    assert!(report.violations.is_empty() && report.stale.is_empty());
    assert!(rules::render(&report).starts_with("pass: 0 new, 0 stale, 1 in the baseline"));
}

#[test]
fn a_new_violation_fails_next_to_a_baseline() {
    let files = Files::with(&["docs/M1_CLOSEOUT.md", "docs/NOTES.md"]);
    let config = Config {
        baseline: vec!["RH2:docs/M1_CLOSEOUT.md".into()],
        ..Config::default()
    };
    let report = rules::check(&files, &config).unwrap();
    assert!(!report.ok);
    assert_eq!(report.violations.len(), 1);
    let text = rules::render(&report);
    assert!(text.contains("RH2 docs/NOTES.md"));
    assert!(text.contains("fail: 1 new, 0 stale, 1 in the baseline"));
}

#[test]
fn a_stale_baseline_entry_fails_the_check() {
    let files = Files::with(&["README.md"]);
    let config = Config {
        baseline: vec!["RH2:docs/gone_STATUS.md".into()],
        ..Config::default()
    };
    let report = rules::check(&files, &config).unwrap();
    assert!(!report.ok);
    assert_eq!(report.stale, ["RH2:docs/gone_STATUS.md"]);
    assert!(rules::render(&report).contains("remove it"));
}

#[test]
fn the_baseline_block_lists_every_violation() {
    let files = Files::with(&["docs/a_STATUS.md", "docs/b.bak"]);
    let all = scan(&files, &Config::default());
    let block = rules::render_baseline(&all);
    assert_eq!(
        block,
        "baseline = [\n    \"RH2:docs/a_STATUS.md\",\n    \"RH2:docs/b.bak\",\n]\n"
    );
    // The block is a valid `[hygiene]` body once it sits under its heading.
    let config = parse_config(&format!("[hygiene]\n{block}")).unwrap();
    assert_eq!(config.baseline, ["RH2:docs/a_STATUS.md", "RH2:docs/b.bak"]);
    assert!(rules::check(&files, &config).unwrap().ok);
}

#[test]
fn the_config_reads_every_key() {
    let config = parse_config(
        r#"
[hygiene]
max_file_bytes = 100
baseline = ["RH2:a_STATUS.md"]

[[hygiene.root]]
path = "infra"
reason = "cluster"

[[hygiene.evidence]]
path = "ev"
reason = "records"

[[hygiene.home_path]]
path = "t.txt"
reason = "fixture"

[[hygiene.size]]
path = "big.bin"
reason = "corpus"
"#,
    )
    .unwrap();
    assert_eq!(config.max_file_bytes, 100);
    assert_eq!(config.baseline, ["RH2:a_STATUS.md"]);
    for (list, path) in [
        (&config.root, "infra"),
        (&config.evidence, "ev"),
        (&config.home_path, "t.txt"),
        (&config.size, "big.bin"),
    ] {
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].path, path);
        assert!(!list[0].reason.is_empty());
    }
}

#[test]
fn the_config_defaults_without_a_table() {
    assert_eq!(parse_config("[other]\na = 1\n").unwrap(), Config::default());
}

#[test]
fn the_config_rejects_bad_input() {
    for text in [
        "not toml [",
        "[hygiene]\nroot = 3\n",
        "[[hygiene.root]]\npath = \"a\"\n",
        "[[hygiene.root]]\npath = \"a\"\nreason = \"  \"\n",
        "[hygiene]\nmax_file_bytes = \"big\"\n",
        "[hygiene]\nbaseline = [1]\n",
        "[hygiene]\nbaseline = \"x\"\n",
    ] {
        assert!(parse_config(text).is_err(), "{text:?} should fail");
    }
}

#[test]
fn an_allow_entry_covers_the_file_and_the_folder_only() {
    let folder = allow("docs/");
    assert!(folder.covers("docs/a/b.md") && folder.covers("docs"));
    assert!(!folder.covers("docs2/a.md") && !folder.covers("src/docs/a.md"));
    let file = allow("a.txt");
    assert!(file.covers("a.txt") && !file.covers("a.txt.bak"));
}

// The binary on real git checkouts.

fn aw(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap()
}

fn git(dir: &Path, args: &[&str]) {
    let out = Command::new("git")
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}: {out:?}");
}

/// A git checkout with these tracked files.
fn checkout(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-repo-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    git(&dir, &["init", "-q"]);
    for (path, text) in files {
        let full = dir.join(path);
        std::fs::create_dir_all(full.parent().unwrap()).unwrap();
        std::fs::write(full, text).unwrap();
    }
    git(&dir, &["add", "-A"]);
    dir
}

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

#[test]
fn the_binary_passes_a_clean_checkout() {
    let dir = checkout("clean", &[("README.md", "hi\n"), ("src/lib.rs", "")]);
    let out = aw(&dir, &["repo", "check"]);
    assert_eq!(out.status.code(), Some(0), "{out:?}");
    assert!(stdout(&out).starts_with("pass: 0 new"));
}

#[test]
fn the_binary_fails_on_a_violation_and_reads_the_config() {
    let files = [
        ("README.md", "hi\n"),
        ("docs/HANDOFF.md", "x"),
        ("infra/a.yaml", "x"),
        ("docs/b.md", &format!("/{}/carol/x", "home")),
        ("docs/big.bin", &"x".repeat(2000)),
        ("tests/big.txt", &"y".repeat(2000)),
    ];
    let dir = checkout("dirty", &files);
    let out = aw(&dir, &["repo", "check"]);
    assert_eq!(out.status.code(), Some(1));
    let text = stdout(&out);
    for id in ["RH1 infra", "RH2 docs/HANDOFF.md", "RH3 docs/b.md"] {
        assert!(text.contains(id), "{text}");
    }
    let toml = "[hygiene]\nmax_file_bytes = 1000\nbaseline = [\"RH2:docs/HANDOFF.md\"]\n\
        [[hygiene.root]]\npath = \"infra\"\nreason = \"cluster\"\n\
        [[hygiene.home_path]]\npath = \"docs/b.md\"\nreason = \"fixture\"\n\
        [[hygiene.size]]\npath = \"tests\"\nreason = \"data\"\n\
        [[hygiene.size]]\npath = \"docs/big.bin\"\nreason = \"data\"\n";
    std::fs::write(dir.join("aw.toml"), toml).unwrap();
    git(&dir, &["add", "-A"]);
    let out = aw(&dir, &["repo", "check"]);
    assert_eq!(out.status.code(), Some(0), "{}", stdout(&out));
}

#[test]
fn the_binary_prints_a_baseline_and_a_json_report() {
    let dir = checkout("baseline", &[("README.md", "x"), ("docs/a.bak", "x")]);
    let out = aw(&dir, &["repo", "check", "--print-baseline"]);
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(stdout(&out), "baseline = [\n    \"RH2:docs/a.bak\",\n]\n");
    let out = aw(&dir, &["repo", "check", "--format", "json"]);
    assert_eq!(out.status.code(), Some(1));
    let json: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
    assert_eq!(json["schema"], "repo-report/1");
    assert_eq!(json["ok"], false);
    assert_eq!(json["violations"][0]["rule"], "RH2");
}

#[test]
fn the_binary_fails_on_a_stale_baseline_entry() {
    let dir = checkout(
        "stale",
        &[
            ("README.md", "x"),
            ("aw.toml", "[hygiene]\nbaseline = [\"RH2:gone.bak\"]\n"),
        ],
    );
    let out = aw(&dir, &["repo", "check"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(stdout(&out).contains("baseline RH2:gone.bak"));
}

#[test]
fn the_binary_reports_usage_errors_with_exit_2() {
    let dir = checkout("broken", &[("aw.toml", "[hygiene]\n[[hygiene.root]]\n")]);
    assert_eq!(aw(&dir, &["repo", "check"]).status.code(), Some(2));
    let outside = std::env::temp_dir().join(format!("aw-it-repo-{}-none", std::process::id()));
    std::fs::create_dir_all(&outside).unwrap();
    // `--dir` outside any checkout (the temp folder is not a git checkout).
    let out = aw(
        &dir,
        &["repo", "check", "--dir", &outside.to_string_lossy()],
    );
    assert_eq!(out.status.code(), Some(2), "{out:?}");
}

#[test]
fn the_binary_skips_a_missing_tracked_file_and_a_symlink() {
    let dir = checkout("odd", &[("README.md", "x"), ("docs/a.md", "y")]);
    std::fs::remove_file(dir.join("docs/a.md")).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink("README.md", dir.join("link.md")).unwrap();
        git(&dir, &["add", "link.md"]);
        // The link is a root file that is not in the allow-list.
        let out = aw(&dir, &["repo", "check"]);
        assert!(stdout(&out).contains("RH1 link.md"), "{}", stdout(&out));
    }
}
