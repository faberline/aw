//! `aw metadoc check`: each STATUS rule has a passing and a failing fixture
//! against an in-memory checkout; the binary runs on real git checkouts.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use aw::domain::meta::config::MetaConfig;
use aw::domain::meta::ports::{CargoIndex, DocInput, MetaRepo, ReadmeInput};
use aw::domain::meta::project::{self, MetadocReport};
use aw::domain::meta::status::{self, StatusReport};
use aw::domain::work::ports::{PortError, PortResult};

const README: &str = "# Demo

## Brief

Demo does one thing.

## Primary workflow

1. Run it.
2. Read the result.

## Demo features

Text.

## Contract discovery

Read the guide.

## Capabilities

### Capability index

| Capability | ID | User promise | Sources |
| --- | --- | --- | --- |
| Run | `run` | It runs. | `crates/demo` |

### Run

- ID: `run`
- Promise: It runs.
- Sources:
  - `crates/demo` runs the whole program here.
- Gate: `cargo test -p demo --test it`

## Supporting documents

- [Status](STATUS.md)
- [Roadmap](ROADMAP.md)
";

const STATUS: &str = "# Demo status

## Scope

The demo command line.

## State definitions

| State | Meaning |
| --- | --- |
| Supported | Works and has a gate. |
| Limited | Works inside a stated limit. |
| Not supported | Planned and not built. |

## Support matrix

| Surface | ID | State | Supported scope | Limits | Evidence |
| --- | --- | --- | --- | --- | --- |
| Run | `run` | Supported | Runs the demo. | None. | `cargo test -p demo --test it` |
| Wide | `wide` | Limited | Runs small input. | See [limit](ROADMAP.md#wide-input). | `cargo test -p demo --test it` |
| Remote | `remote` | Not supported | Not built. | Not built. | [plan](ROADMAP.md#remote) |

## Evidence policy

Each gate is a command that a person can run.
";

const ROADMAP: &str = "# Demo roadmap\n\n### Wide input\n\n### Remote\n";

/// An in-memory checkout.
struct Fake(BTreeMap<String, String>);

impl Fake {
    fn with(status: &str) -> Self {
        Fake(BTreeMap::from([
            ("README.md".to_string(), README.to_string()),
            ("STATUS.md".to_string(), status.to_string()),
            ("ROADMAP.md".to_string(), ROADMAP.to_string()),
            ("docs/guide.md".to_string(), "# Guide\n".to_string()),
        ]))
    }
}

impl MetaRepo for Fake {
    fn project_dir(&self, target: &str) -> PortResult<String> {
        Ok(target.trim_start_matches('.').to_string())
    }
    fn document(&self, path: &str) -> PortResult<Option<DocInput>> {
        Ok(self.0.get(path).map(|text| DocInput {
            path: path.to_string(),
            dir: path.rsplit_once('/').map(|p| p.0).unwrap_or("").to_string(),
            text: text.clone(),
            sha256: "0".into(),
        }))
    }
    fn is_file(&self, path: &str) -> bool {
        self.0.contains_key(path)
    }
    fn read_text(&self, path: &str) -> Option<String> {
        self.0.get(path).cloned()
    }
    fn config(&self) -> PortResult<MetaConfig> {
        Ok(MetaConfig::default())
    }
    fn readme(&self, _target: &str) -> PortResult<ReadmeInput> {
        Err(PortError("not used".into()))
    }
    fn exists_from(&self, base: &str, target: &str) -> bool {
        let full = if base.is_empty() {
            target.to_string()
        } else {
            format!("{base}/{target}")
        };
        full == "crates/demo" || full == "scripts/gate.sh" || self.0.contains_key(&full)
    }
    fn cargo_packages(&self) -> PortResult<CargoIndex> {
        Ok(BTreeMap::from([(
            "demo".to_string(),
            BTreeSet::from(["it".to_string()]),
        )]))
    }
    fn local_package(&self, _dir: &str) -> Option<String> {
        None
    }
    fn core_crates(&self, _core_source: &str) -> BTreeSet<String> {
        BTreeSet::new()
    }
}

fn run(text: &str) -> StatusReport {
    let fake = Fake::with(text);
    let doc = fake.document("STATUS.md").unwrap().unwrap();
    status::check(&doc, &fake, &fake.cargo_packages().unwrap())
}

fn messages(text: &str, rule: &str) -> Vec<String> {
    run(text)
        .findings
        .into_iter()
        .filter(|f| f.rule == rule)
        .map(|f| f.message)
        .collect()
}

/// `pass` raises no finding; `fail` raises one of `rule`.
fn rule_case(rule: &str, pass: &str, fail: &str) {
    let ok = run(pass);
    assert!(
        ok.findings.iter().all(|f| f.rule != rule),
        "{rule} on the passing fixture: {:?}",
        ok.findings
    );
    let bad = run(fail);
    assert!(
        bad.findings.iter().any(|f| f.rule == rule),
        "{rule} not found on the failing fixture: {:?}",
        bad.findings
    );
}

#[test]
fn the_base_status_passes() {
    let report = run(STATUS);
    assert!(report.ok, "{:?}", report.findings);
    let ids: Vec<&str> = report.surfaces.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids, ["run", "wide", "remote"]);
    assert_eq!(report.surfaces[0].gates, ["cargo test -p demo --test it"]);
    assert_eq!(report.surfaces[1].roadmap_id.as_deref(), Some("wide-input"));
    assert_eq!(report.surfaces[2].roadmap_id.as_deref(), Some("remote"));
}

#[test]
fn s1_one_h1_no_h3_and_the_four_h2_headings_in_order() {
    rule_case(
        "S1",
        STATUS,
        &STATUS.replace("# Demo status\n", "# Demo status\n\n# Again\n"),
    );
    rule_case("S1", STATUS, &STATUS.replace("## Scope\n", "### Scope\n"));
    rule_case(
        "S1",
        STATUS,
        &STATUS.replace("## Evidence policy", "## Policy"),
    );
    let swapped = STATUS
        .replace("## Scope", "## @@")
        .replace("## State definitions", "## Scope")
        .replace("## @@", "## State definitions");
    let found = messages(&swapped, "S1");
    assert_eq!(
        found,
        [
            "STATUS H2 headings must be exactly: Scope | State definitions | Support matrix | Evidence policy"
        ]
    );
    assert_eq!(
        messages(&STATUS.replace("## Evidence policy", "## Policy"), "S1"),
        ["STATUS heading '## Evidence policy' appears 0 times"]
    );
}

#[test]
fn s2_no_grades_owners_dates_or_percent_complete() {
    rule_case(
        "S2",
        STATUS,
        &STATUS.replace("The demo command line.", "Owner: me"),
    );
    rule_case(
        "S2",
        STATUS,
        &STATUS.replace("The demo command line.", "- Status: ok"),
    );
    rule_case(
        "S2",
        STATUS,
        &STATUS.replace("The demo command line.", "It is 80% complete."),
    );
    rule_case(
        "S2",
        STATUS,
        &STATUS.replace("The demo command line.", "Progress: 40%"),
    );
    // A fenced example is not read.
    let fenced = STATUS.replace("The demo command line.", "```\nOwner: me\n```");
    assert!(messages(&fenced, "S2").is_empty());
}

#[test]
fn s3_the_state_definitions_and_the_two_prose_sections() {
    rule_case(
        "S3",
        STATUS,
        &STATUS.replace("| Limited | Works inside a stated limit. |\n", ""),
    );
    rule_case(
        "S3",
        STATUS,
        &STATUS.replace("| Supported | Works and has a gate. |", "| Supported |  |"),
    );
    rule_case(
        "S3",
        STATUS,
        &STATUS
            .replace("| Supported |", "| @@ |")
            .replace("| Limited |", "| Supported |")
            .replace("| @@ |", "| Limited |"),
    );
    rule_case(
        "S3",
        STATUS,
        &STATUS.replace("The demo command line.\n", ""),
    );
    rule_case(
        "S3",
        STATUS,
        &STATUS.replace("Each gate is a command that a person can run.\n", ""),
    );
    assert_eq!(
        messages(&STATUS.replace("The demo command line.\n", ""), "S3"),
        ["Scope cannot be empty"]
    );
}

#[test]
fn s4_the_support_matrix_table() {
    rule_case(
        "S4",
        STATUS,
        &STATUS.replace("| Surface | ID |", "| Area | ID |"),
    );
    rule_case(
        "S4",
        STATUS,
        &STATUS.replace(
            "| Run | `run` | Supported | Runs the demo. | None.",
            "| Run | `run` | Supported | Runs the demo.",
        ),
    );
    rule_case("S4", STATUS, &STATUS.replace("| `run` |", "| run |"));
    rule_case("S4", STATUS, &STATUS.replace("| `run` |", "| `Run_It` |"));
    rule_case("S4", STATUS, &STATUS.replace("| `wide` |", "| `run` |"));
    rule_case("S4", STATUS, &STATUS.replace("| Wide |", "| Run |"));
    rule_case("S4", STATUS, &STATUS.replace("| Runs the demo. |", "|  |"));
    rule_case(
        "S4",
        STATUS,
        &STATUS.replace("| Supported | Runs", "| Done | Runs"),
    );
    assert_eq!(
        messages(&STATUS.replace("| Supported | Runs", "| Done | Runs"), "S4"),
        ["invalid support state: Done"]
    );
    // Only the header and the separator: no row.
    let empty = STATUS
        .lines()
        .filter(|l| {
            !l.starts_with("| Run |") && !l.starts_with("| Wide |") && !l.starts_with("| Remote |")
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        messages(&empty, "S4"),
        [
            "Support matrix columns must be: Surface | ID | State | Supported scope | Limits | Evidence"
        ]
    );
}

#[test]
fn s5_supported_and_limited_rows_name_a_resolvable_gate() {
    rule_case(
        "S5",
        STATUS,
        &STATUS.replace(
            "| None. | `cargo test -p demo --test it` |",
            "| None. | By hand. |",
        ),
    );
    rule_case(
        "S5",
        STATUS,
        &STATUS.replace(
            "| None. | `cargo test -p demo --test it` |",
            "| None. | `cargo test -p ghost` |",
        ),
    );
    rule_case(
        "S5",
        STATUS,
        &STATUS.replace(
            "| None. | `cargo test -p demo --test it` |",
            "| None. | `bash scripts/missing.sh` |",
        ),
    );
    let found = messages(
        &STATUS.replace(
            "| None. | `cargo test -p demo --test it` |",
            "| None. | By hand. |",
        ),
        "S5",
    );
    assert_eq!(
        found,
        ["Supported row must name an executable backticked gate"]
    );
    // A gate that the check cannot resolve is a warning, not a finding.
    let report = run(&STATUS.replace(
        "`cargo test -p demo --test it` |\n| Wide",
        "`make check` |\n| Wide",
    ));
    assert!(report.ok, "{:?}", report.findings);
    assert_eq!(report.warnings.len(), 1, "{:?}", report.warnings);
    // The gate finding keeps the line of its row.
    let bad = run(&STATUS.replace(
        "`cargo test -p demo --test it` |\n| Wide",
        "`cargo test -p ghost` |\n| Wide",
    ));
    let lines: Vec<usize> = bad
        .findings
        .iter()
        .filter(|f| f.rule == "S5")
        .map(|f| f.line)
        .collect();
    assert_eq!(lines.len(), 1);
    assert!(
        STATUS
            .lines()
            .nth(lines[0] - 1)
            .unwrap()
            .starts_with("| Run |")
    );
}

#[test]
fn s6_limited_and_not_supported_rows_link_one_roadmap_item() {
    rule_case(
        "S6",
        STATUS,
        &STATUS.replace("See [limit](ROADMAP.md#wide-input).", "Small input only."),
    );
    rule_case(
        "S6",
        STATUS,
        &STATUS.replace(
            "See [limit](ROADMAP.md#wide-input).",
            "See [a](ROADMAP.md#wide-input) and [b](ROADMAP.md#remote).",
        ),
    );
    rule_case(
        "S6",
        STATUS,
        &STATUS.replace("[plan](ROADMAP.md#remote)", "Later."),
    );
    rule_case(
        "S6",
        STATUS,
        &STATUS.replace("[plan](ROADMAP.md#remote)", "[plan](OTHER.md#remote)"),
    );
    assert_eq!(
        messages(
            &STATUS.replace(
                "[plan](ROADMAP.md#remote)",
                "[plan](ROADMAP.md#remote) `cargo test`"
            ),
            "S6"
        ),
        ["Not supported evidence must not claim a current gate"]
    );
}

#[test]
fn s7_relative_links_exist_and_name_a_heading() {
    rule_case(
        "S7",
        STATUS,
        &STATUS.replace("ROADMAP.md#wide-input", "ROADMAP.md#nothing"),
    );
    rule_case(
        "S7",
        STATUS,
        &STATUS.replace("ROADMAP.md#remote", "MISSING.md#remote"),
    );
    rule_case(
        "S7",
        STATUS,
        &STATUS.replace("ROADMAP.md#remote", "../outside.md"),
    );
    assert_eq!(
        messages(
            &STATUS.replace("ROADMAP.md#wide-input", "ROADMAP.md#nothing"),
            "S7"
        ),
        ["Markdown anchor does not exist: ROADMAP.md#nothing"]
    );
    assert_eq!(
        messages(&STATUS.replace("ROADMAP.md#remote", "../outside.md"), "S7"),
        ["relative link leaves the checkout: ../outside.md"]
    );
    // A web link, a mail link and an anchor in the same file are not read.
    let skipped = STATUS.replace(
        "Each gate is a command that a person can run.",
        "[a](https://example.test/x) [b](mailto:a@b.test) [c](#scope) [d](/abs)",
    );
    assert!(messages(&skipped, "S7").is_empty());
}

#[test]
fn findings_are_sorted_by_line_rule_and_message() {
    let text = STATUS
        .replace("# Demo status\n", "# Demo status\n\n# Again\n")
        .replace("ROADMAP.md#remote", "MISSING.md#remote");
    let report = run(&text);
    let keys: Vec<_> = report.findings.iter().map(|f| (f.line, f.rule)).collect();
    assert!(keys.is_sorted(), "{keys:?}");
    assert!(keys.len() >= 2, "{keys:?}");
}

fn project_report(fake: &Fake) -> MetadocReport {
    let config = fake.config().unwrap();
    let cargo = fake.cargo_packages().unwrap();
    project::report(vec![project::check(fake, &config, &cargo, "").unwrap()])
}

#[test]
fn p1_each_core_document_must_exist() {
    let ok = project_report(&Fake::with(STATUS));
    assert!(ok.ok, "{:?}", ok.reports[0]);
    let mut fake = Fake::with(STATUS);
    fake.0.remove("ROADMAP.md");
    fake.0.remove("STATUS.md");
    let bad = project_report(&fake);
    assert!(!bad.ok);
    let found: Vec<&str> = bad.reports[0]
        .findings
        .iter()
        .map(|f| f.message.as_str())
        .collect();
    assert_eq!(
        found,
        [
            "project document does not exist: ROADMAP.md",
            "project document does not exist: STATUS.md"
        ]
    );
    assert!(bad.reports[0].status.is_none());
}

#[test]
fn the_text_form_names_each_document_like_the_defer_script() {
    let text = project::render(&project_report(&Fake::with(STATUS)));
    assert!(
        text.starts_with("PASS .\n  README sha256=0 capabilities=1\n  STATUS sha256=0 surfaces=3"),
        "{text}"
    );
    let broken = STATUS.replace("ROADMAP.md#remote", "MISSING.md#remote");
    let text = project::render(&project_report(&Fake::with(&broken)));
    assert!(text.starts_with("FAIL ."), "{text}");
    assert!(
        text.contains(
            "  STATUS S7 line 21: relative link target does not exist: MISSING.md#remote"
        ),
        "{text}"
    );
}

// ---- the binary, on real git checkouts ----

fn git(dir: &Path, args: &[&str]) {
    let status = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.test"])
        .args(["-c", "commit.gpgsign=false"])
        .args(args)
        .status()
        .unwrap();
    assert!(status.success(), "git {args:?}");
}

fn checkout(name: &str, status: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-metadoc-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let files = [
        ("README.md", README),
        ("STATUS.md", status),
        ("ROADMAP.md", ROADMAP),
        ("docs/guide.md", "# Guide\n"),
        (
            "crates/demo/Cargo.toml",
            "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n",
        ),
        ("crates/demo/tests/it.rs", "#[test]\nfn t() {}\n"),
    ];
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

fn metadoc(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["metadoc", "check", "--dir"])
        .arg(dir)
        .args(args)
        .output()
        .unwrap()
}

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

#[test]
fn the_binary_passes_a_valid_set_and_accepts_a_document_as_target() {
    let dir = checkout("pass", STATUS);
    for target in [".", "STATUS.md", "README.md"] {
        let out = metadoc(&dir, &[target]);
        assert_eq!(out.status.code(), Some(0), "{target}: {}", stdout(&out));
        let text = stdout(&out);
        assert!(text.starts_with("PASS .\n  README sha256="), "{text}");
        assert!(text.contains("  STATUS sha256="), "{text}");
    }
    let json = metadoc(&dir, &["--format", "json"]);
    let value: serde_json::Value = serde_json::from_str(&stdout(&json)).unwrap();
    assert_eq!(value["schema"], "metadoc-report/1");
    assert_eq!(value["ok"], true);
    assert_eq!(value["reports"][0]["status"]["surfaces"][0]["id"], "run");
}

#[test]
fn the_binary_exits_1_on_a_finding_and_2_on_a_usage_error() {
    let dir = checkout(
        "fail",
        &STATUS.replace("The demo command line.", "Owner: me"),
    );
    let out = metadoc(&dir, &[]);
    assert_eq!(out.status.code(), Some(1), "{}", stdout(&out));
    assert!(
        stdout(&out).contains("  STATUS S2 line 1:"),
        "{}",
        stdout(&out)
    );
    let usage = metadoc(&dir, &["docs/guide.md"]);
    assert_eq!(usage.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&usage.stderr)
            .contains("target file must be a core project document"),
        "{}",
        String::from_utf8_lossy(&usage.stderr)
    );
    let outside = metadoc(&dir, &["/"]);
    assert_eq!(outside.status.code(), Some(2));
}
