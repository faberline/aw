//! `aw meta check`: each README rule has a passing and a failing fixture
//! against an in-memory checkout; the binary runs on real git checkouts.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use aw::domain::meta::config::MetaConfig;
use aw::domain::meta::ports::{CargoIndex, DocInput, MetaRepo, ReadmeInput};
use aw::domain::meta::readme;
use aw::domain::meta::report::ReadmeReport;
use aw::domain::work::ports::PortResult;

/// A valid README: it follows every rule.
const BASE: &str = "# Demo

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

- [Guide](docs/guide.md)
";

/// An in-memory checkout.
struct Fake;

impl MetaRepo for Fake {
    fn root(&self) -> String {
        "/repo".into()
    }
    fn project_dir(&self, _target: &str) -> PortResult<String> {
        unreachable!("the fixtures call the rules directly")
    }
    fn document(&self, _path: &str) -> PortResult<Option<DocInput>> {
        unreachable!("the fixtures call the rules directly")
    }
    fn is_file(&self, _path: &str) -> bool {
        false
    }
    fn read_text(&self, _path: &str) -> Option<String> {
        None
    }
    fn config(&self) -> PortResult<MetaConfig> {
        Ok(MetaConfig::default())
    }
    fn readme(&self, _target: &str) -> PortResult<ReadmeInput> {
        unreachable!("the fixtures call the rules directly")
    }
    fn exists_from(&self, base: &str, target: &str) -> bool {
        let full = if base.is_empty() {
            target.to_string()
        } else {
            format!("{base}/{target}")
        };
        ["crates/demo", "docs/guide.md", "scripts/gate.sh"].contains(&full.as_str())
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
        BTreeSet::from(["cli-std".to_string()])
    }
}

fn run(text: &str) -> ReadmeReport {
    let input = ReadmeInput {
        path: "README.md".into(),
        dir: String::new(),
        text: text.into(),
        sha256: String::new(),
    };
    let cargo = Fake.cargo_packages().unwrap();
    readme::check(&input, &MetaConfig::default(), &Fake, &cargo)
}

fn rules(text: &str) -> Vec<&'static str> {
    let mut rules: Vec<&'static str> = run(text).findings.iter().map(|f| f.rule).collect();
    rules.dedup();
    rules
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
fn the_base_readme_passes() {
    let report = run(BASE);
    assert!(report.ok, "{:?}", report.findings);
    assert_eq!(report.capabilities.len(), 1);
    assert_eq!(
        report.capabilities[0].gates,
        ["cargo test -p demo --test it"]
    );
    assert_eq!(report.product_sections, ["Demo features"]);
    assert_eq!(report.gate_count, 1);
    assert_eq!(report.relative_links, 1);
}

#[test]
fn r1_one_h1_and_each_required_heading_once() {
    rule_case("R1", BASE, &BASE.replace("# Demo\n", "# Demo\n\n# Again\n"));
    rule_case("R1", BASE, &BASE.replace("## Brief", "## Summary"));
    let twice = BASE.replace(
        "## Contract discovery",
        "## Brief\n\nx\n\n## Contract discovery",
    );
    assert!(messages(&twice, "R1")[0].contains("'## Brief' appears 2 times"));
}

#[test]
fn r2_required_headings_in_order() {
    let swapped = BASE
        .replace("## Brief", "## TMP")
        .replace("## Primary workflow", "## Brief")
        .replace("## TMP", "## Primary workflow");
    rule_case("R2", BASE, &swapped);
}

#[test]
fn r3_brief_workflow_and_supporting_documents_have_content() {
    rule_case("R3", BASE, &BASE.replace("Demo does one thing.\n", ""));
    rule_case("R3", BASE, &BASE.replace("2. Read the result.\n", ""));
    rule_case(
        "R3",
        BASE,
        &BASE.replace("- [Guide](docs/guide.md)", "- Guide"),
    );
}

#[test]
fn r4_a_product_section_sits_between_workflow_and_discovery() {
    rule_case(
        "R4",
        BASE,
        &BASE.replace("## Demo features\n\nText.\n\n", ""),
    );
}

#[test]
fn r5_the_capability_index_table() {
    rule_case(
        "R5",
        BASE,
        &BASE.replace("### Capability index\n", "### Index\n"),
    );
    rule_case("R5", BASE, &BASE.replace("User promise", "Promise"));
    rule_case("R5", BASE, &BASE.replace("| `run` |", "| `Run Now` |"));
    rule_case(
        "R5",
        BASE,
        &BASE.replace("| It runs. | `crates/demo` |", "| It runs. |"),
    );
    let twice = BASE.replace(
        "| Run | `run` | It runs. | `crates/demo` |",
        "| Run | `run` | It runs. | `crates/demo` |\n| Run | `run` | It runs. | `crates/demo` |",
    );
    assert!(
        messages(&twice, "R5")
            .iter()
            .any(|m| m.contains("must be unique"))
    );
}

#[test]
fn r6_details_match_the_index() {
    rule_case("R6", BASE, &BASE.replace("### Run\n", "### Walk\n"));
    rule_case("R6", BASE, &BASE.replace("- ID: `run`", "- ID: `walk`"));
    rule_case("R6", BASE, &BASE.replace("- Promise: It runs.\n", ""));
}

#[test]
fn r7_sources_exist_and_match() {
    rule_case(
        "R7",
        BASE,
        &BASE.replace(
            "- Sources:\n  - `crates/demo` runs",
            "- Sources:\n  - `crates/none` runs",
        ),
    );
    rule_case(
        "R7",
        BASE,
        &BASE.replace("- Sources:\n", "- Source list:\n"),
    );
    rule_case(
        "R7",
        BASE,
        &BASE.replace("runs the whole program here.", "runs."),
    );
    rule_case(
        "R7",
        BASE,
        &BASE.replace("| `crates/demo` |", "| `crates/demo` `crates/demo` |"),
    );
    rule_case(
        "R7",
        BASE,
        &BASE.replace("| It runs. | `crates/demo` |", "| It runs. | |"),
    );
    rule_case(
        "R7",
        BASE,
        &BASE.replace(
            "  - `crates/demo` runs the whole program here.",
            "  - `crates/demo` and `libs/x` run it.",
        ),
    );
}

#[test]
fn r7_a_core_source_must_be_a_core_package() {
    let core = |name: &str| BASE.replace("`crates/demo`", &format!("`core/{name}`"));
    rule_case("R7", &core("cli-std"), &core("nothing"));
}

#[test]
fn r7_the_root_source_comes_from_the_config() {
    let text = BASE.replace("`crates/demo`", "`demo`");
    let with_root = MetaConfig {
        root_source: Some("demo".into()),
        ..MetaConfig::default()
    };
    let input = ReadmeInput {
        path: "README.md".into(),
        dir: String::new(),
        text,
        sha256: String::new(),
    };
    let cargo = Fake.cargo_packages().unwrap();
    assert!(readme::check(&input, &with_root, &Fake, &cargo).ok);
    let without = readme::check(&input, &MetaConfig::default(), &Fake, &cargo);
    assert!(without.findings.iter().any(|f| f.rule == "R7"));
}

#[test]
fn r8_every_capability_has_a_resolvable_gate() {
    rule_case(
        "R8",
        BASE,
        &BASE.replace("- Gate: `cargo test -p demo --test it`\n", ""),
    );
    rule_case(
        "R8",
        BASE,
        &BASE
            .replace("- Gate: `cargo test", "- Gate: cargo test")
            .replace("it`", "it"),
    );
}

#[test]
fn r8_cargo_test_gates() {
    let gate = |g: &str| BASE.replace("cargo test -p demo --test it", g);
    rule_case(
        "R8",
        &gate("cargo test -p demo --lib"),
        &gate("cargo test -p demo it_works"),
    );
    rule_case(
        "R8",
        &gate("cargo test -p demo"),
        &gate("cargo test -p nothing"),
    );
    rule_case(
        "R8",
        &gate("cargo test -p demo --test it"),
        &gate("cargo test -p demo --test nothing"),
    );
    rule_case(
        "R8",
        &gate("cargo test -p demo --test it"),
        &gate("cargo test -p"),
    );
    rule_case(
        "R8",
        &gate("cargo test -p demo --test it"),
        &gate("cargo test -p demo \"it"),
    );
}

#[test]
fn r8_script_gates_and_unresolved_shapes() {
    let gate = |g: &str| BASE.replace("cargo test -p demo --test it", g);
    rule_case(
        "R8",
        &gate("bash scripts/gate.sh"),
        &gate("bash scripts/none.sh"),
    );
    let unresolved = run(&gate("make check"));
    assert!(unresolved.ok);
    assert_eq!(unresolved.warnings.len(), 1);
    assert!(unresolved.warnings[0].contains("not mechanically resolved: make check"));
}

#[test]
fn r9_relative_links_exist() {
    rule_case("R9", BASE, &BASE.replace("docs/guide.md", "docs/none.md"));
    let outside = BASE.replace(
        "## Supporting documents",
        "## Supporting documents\n\n- [Web](https://example.test/x) and [Here](#brief)",
    );
    assert!(run(&outside).ok, "{:?}", run(&outside).findings);
    assert_eq!(run(&outside).relative_links, 1);
}

#[test]
fn r10_capabilities_stay_free_of_grades_and_work_items() {
    rule_case(
        "R10",
        BASE,
        &BASE.replace("- Promise: It runs.", "- Promise: It runs.\n- Status: done"),
    );
    rule_case(
        "R10",
        BASE,
        &BASE.replace("- Promise: It runs.", "- Promise: It runs. See #12."),
    );
    rule_case(
        "R10",
        BASE,
        &BASE.replace("- Promise: It runs.", "- Promise: It runs.\n- Root WI: x"),
    );
    let fenced = BASE.replace(
        "- Promise: It runs.",
        "- Promise: It runs.\n\n```text\nStatus: done #12\n```",
    );
    assert!(run(&fenced).ok, "{:?}", run(&fenced).findings);
}

#[test]
fn r11_fenced_blocks_are_closed() {
    let closed = format!("{BASE}\n```text\nx\n```\n");
    rule_case("R11", &closed, &format!("{BASE}\n```text\nx\n"));
}

#[test]
fn r12_capabilities_are_flat() {
    rule_case(
        "R12",
        BASE,
        &BASE.replace("- ID: `run`", "#### Deeper\n\n- ID: `run`"),
    );
}

#[test]
fn findings_are_sorted_by_line_rule_and_message() {
    let text = BASE
        .replace("docs/guide.md", "docs/none.md")
        .replace("# Demo\n", "# Demo\n\n# Again\n");
    let report = run(&text);
    let keys: Vec<_> = report.findings.iter().map(|f| (f.line, f.rule)).collect();
    assert!(keys.is_sorted(), "{keys:?}");
    assert_eq!(rules(&text), ["R1", "R9"]);
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

fn checkout(name: &str, readme: &str, extra: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-meta-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let files = [
        ("README.md", readme),
        ("docs/guide.md", "# Guide\n"),
        (
            "crates/demo/Cargo.toml",
            "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n",
        ),
        ("crates/demo/tests/it.rs", "#[test]\nfn t() {}\n"),
    ];
    for (path, text) in files.iter().chain(extra) {
        let full = dir.join(path);
        std::fs::create_dir_all(full.parent().unwrap()).unwrap();
        std::fs::write(full, text).unwrap();
    }
    git(&dir, &["init", "-q"]);
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-q", "-m", "x"]);
    dir
}

fn meta(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["meta", "check", "--dir"])
        .arg(dir)
        .args(args)
        .output()
        .unwrap()
}

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

#[test]
fn the_binary_passes_a_valid_readme_and_prints_the_defer_text_form() {
    let dir = checkout("pass", BASE, &[]);
    let out = meta(&dir, &[]);
    assert_eq!(out.status.code(), Some(0), "{}", stdout(&out));
    let text = stdout(&out);
    assert!(text.starts_with("PASS README.md sha256="), "{text}");
    assert!(text.contains("  product_sections=1 capabilities=1 relative_links=1 gates=1"));
}

#[test]
fn the_binary_fails_a_broken_readme_with_exit_1() {
    let dir = checkout("fail", &BASE.replace("docs/guide.md", "docs/none.md"), &[]);
    let out = meta(&dir, &["README.md"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        stdout(&out).contains("  R9 line 38: relative link target does not exist: docs/none.md")
    );
}

#[test]
fn the_binary_prints_json_with_the_schema() {
    let dir = checkout("json", BASE, &[]);
    let out = meta(&dir, &["--format", "json"]);
    let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(value["schema"], "meta-report/1");
    assert_eq!(value["ok"], true);
    assert_eq!(value["reports"][0]["capabilities"][0]["id"], "run");
}

#[test]
fn the_binary_exits_2_for_a_target_it_cannot_read() {
    let dir = checkout("usage", BASE, &[]);
    assert_eq!(meta(&dir, &["docs/none"]).status.code(), Some(2));
    assert_eq!(meta(&dir, &["../outside"]).status.code(), Some(2));
    assert_eq!(meta(&dir, &["docs/guide.md"]).status.code(), Some(2));
    let nowhere = std::env::temp_dir().join(format!("aw-it-meta-{}-nogit", std::process::id()));
    std::fs::create_dir_all(&nowhere).unwrap();
    assert_eq!(meta(&nowhere, &[]).status.code(), Some(2));
}

#[test]
fn the_binary_reads_the_meta_table_of_aw_toml() {
    let text = BASE.replace("`crates/demo`", "`demo`");
    let plain = checkout("root-no", &text, &[]);
    assert_eq!(meta(&plain, &[]).status.code(), Some(1));
    let named = checkout(
        "root-name",
        &text,
        &[("aw.toml", "[project]\nname = \"demo\"\n")],
    );
    assert_eq!(meta(&named, &[]).status.code(), Some(0));
    let prefixes = checkout(
        "prefixes",
        &BASE.replace("`crates/demo`", "`pkg/demo`"),
        &[
            ("aw.toml", "[meta]\nsource_prefixes = [\"pkg\"]\n"),
            ("pkg/demo/x", ""),
        ],
    );
    let out = meta(&prefixes, &[]);
    assert_eq!(out.status.code(), Some(0), "{}", stdout(&out));
}

#[test]
fn the_binary_finds_cargo_test_targets_in_the_checkout() {
    let bad = BASE.replace("--test it", "--test nothing");
    let dir = checkout("targets", &bad, &[]);
    let out = meta(&dir, &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(stdout(&out).contains("cargo gate names no --test target 'nothing' in demo"));
    let good = checkout("targets-ok", BASE, &[]);
    assert_eq!(meta(&good, &[]).status.code(), Some(0));
}
