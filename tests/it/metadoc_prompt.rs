//! `aw metadoc prompt`: the clean-reader task equals the text of the defer
//! script (golden files made by its `clean_reader_prompt`).

use aw::application::meta::MetaServices;
use aw::application::metadoc::{self, Prompt};
use aw::domain::meta::guides::{GUIDES, MIGRATION};

use std::process::{Command, Output};

use super::metadoc::{Fake, README, ROADMAP, STATUS, checkout};

const CORE: &str = include_str!("metadoc_prompt_core.txt");
const ALL: &str = include_str!("metadoc_prompt_all.txt");

fn guide(h2: &[&str], table_heading: &str, header: &[&str]) -> String {
    let mut out = String::from("# Guide\n");
    for name in h2 {
        out.push_str(&format!("\n## {name}\n\n"));
        if *name == table_heading {
            out.push_str(&format!("| {} |\n", header.join(" | ")));
            out.push_str(&format!("| {} |\n", vec!["---"; header.len()].join(" | ")));
            out.push_str(&format!("| {} |\n", vec!["x"; header.len()].join(" | ")));
        } else if *name == "Supporting documents" {
            out.push_str("- [Readme](../README.md)\n");
        } else {
            out.push_str("Text.\n");
        }
    }
    out
}

/// A project at `apps/lumen` with every guide.
fn full() -> Fake {
    let mut links = String::new();
    let mut fake = Fake(Default::default());
    for spec in &GUIDES {
        let path = spec.path.unwrap();
        links.push_str(&format!("- [Doc]({path})\n"));
        fake.0.insert(
            format!("apps/lumen/{path}"),
            guide(spec.h2, spec.table_heading, spec.table_header),
        );
    }
    links.push_str("- [Migration](docs/migration-0-5.md)\n");
    fake.0.insert(
        "apps/lumen/docs/migration-0-5.md".into(),
        guide(
            MIGRATION.h2,
            MIGRATION.table_heading,
            &["Surface", "0.4.x", "0.5.0", "Required action"],
        ),
    );
    let readme = README.replace(
        "- [Roadmap](ROADMAP.md)\n",
        &format!("- [Roadmap](ROADMAP.md)\n{links}"),
    );
    for (name, text) in [
        ("README.md", readme.as_str()),
        ("STATUS.md", STATUS),
        ("ROADMAP.md", ROADMAP),
    ] {
        fake.0
            .insert(format!("apps/lumen/{name}"), text.to_string());
    }
    fake.0
        .insert("apps/lumen/docs/guide.md".into(), "# Guide\n".into());
    fake
}

fn prompt(fake: &Fake, target: &str) -> Prompt {
    metadoc::prompt(MetaServices { repo: fake }, target).unwrap()
}

#[test]
fn the_core_prompt_equals_the_defer_text() {
    let Prompt::Text(text) = prompt(&Fake::with(STATUS), "") else {
        panic!("the base set passes");
    };
    assert_eq!(text, CORE);
}

#[test]
fn the_prompt_with_every_guide_equals_the_defer_text() {
    let text = match prompt(&full(), "apps/lumen") {
        Prompt::Text(text) => text,
        Prompt::Findings(report) => panic!("the full set passes: {:?}", report.reports[0]),
    };
    assert_eq!(text, ALL);
}

#[test]
fn a_failing_check_gives_the_findings_and_no_prompt() {
    let mut fake = Fake::with(STATUS);
    fake.0.remove("ROADMAP.md");
    let Prompt::Findings(report) = prompt(&fake, "") else {
        panic!("a missing document is a finding");
    };
    assert!(!report.ok);
    assert_eq!(
        report.reports[0].findings[0].message,
        "project document does not exist: ROADMAP.md"
    );
}

fn run(dir: &std::path::Path, path: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["metadoc", "prompt", "--dir"])
        .arg(dir)
        .arg(path)
        .output()
        .unwrap()
}

#[test]
fn the_binary_prints_the_prompt_with_absolute_paths_and_exits_0() {
    let dir = checkout("prompt-pass", STATUS);
    let out = run(&dir, ".");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert_eq!(out.status.code(), Some(0), "{text}");
    let root = std::fs::canonicalize(&dir).unwrap();
    assert!(
        text.starts_with(&format!(
            "Read only these 3 files:\n- {0}/README.md\n- {0}/STATUS.md\n- {0}/ROADMAP.md\n",
            root.display()
        )),
        "{text}"
    );
    assert!(text.contains("Return JSON only:"), "{text}");
}

#[test]
fn the_binary_exits_1_with_the_findings_when_the_check_fails() {
    let dir = checkout(
        "prompt-fail",
        &STATUS.replace("# Demo status", "# Other status"),
    );
    let out = run(&dir, ".");
    assert_eq!(out.status.code(), Some(1));
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.starts_with("FAIL ."), "{text}");
    assert!(!text.contains("Read only these"), "{text}");
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains(
            "error: clean-reader review is skipped until deterministic findings are fixed"
        ),
        "{err}"
    );
}
