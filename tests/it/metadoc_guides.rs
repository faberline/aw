//! `aw metadoc check`, guides: D1 to D4 and the project rules P2, P5, P6, P7.

use aw::domain::meta::guides::{GUIDES, MIGRATION};
use aw::domain::meta::lumen;
use aw::domain::meta::ports::MetaRepo;
use aw::domain::meta::project::{self, MetadocReport};

use super::metadoc::{Fake, README, ROADMAP, STATUS, project_report};

/// A valid guide for `spec`, with the table rows given.
fn guide_text(h2: &[&str], table_heading: &str, header: &[&str], rows: &[&str]) -> String {
    let mut out = String::from("# Guide\n");
    for name in h2 {
        out.push_str(&format!("\n## {name}\n\n"));
        if *name == table_heading {
            out.push_str(&format!("| {} |\n", header.join(" | ")));
            out.push_str(&format!("| {} |\n", vec!["---"; header.len()].join(" | ")));
            for row in rows {
                out.push_str(&format!("| {row} |\n"));
            }
        } else if *name == "Supporting documents" {
            out.push_str("- [Readme](../README.md)\n");
        } else {
            out.push_str("Text.\n");
        }
    }
    out
}

fn protocol() -> String {
    let spec = &GUIDES[0];
    guide_text(
        spec.h2,
        spec.table_heading,
        spec.table_header,
        &["Wire | `proto/a.proto` | `GET /a`"],
    )
}

fn migration() -> String {
    guide_text(
        MIGRATION.h2,
        MIGRATION.table_heading,
        &["Surface", "0.4.x", "0.5.0", "Required action"],
        &["Search | old | new | change it"],
    )
}

fn readme_with(links: &[&str]) -> String {
    let extra: String = links.iter().map(|l| format!("- [Doc]({l})\n")).collect();
    README.replace(
        "- [Roadmap](ROADMAP.md)\n",
        &format!("- [Roadmap](ROADMAP.md)\n{extra}"),
    )
}

fn fake(files: &[(&str, String)]) -> Fake {
    let mut fake = Fake::with(STATUS);
    for (path, text) in files {
        fake.0.insert((*path).to_string(), text.clone());
    }
    fake
}

fn findings(report: &MetadocReport) -> Vec<String> {
    let r = &report.reports[0];
    let mut out: Vec<String> = r
        .supporting_docs
        .iter()
        .flat_map(|g| {
            g.findings
                .iter()
                .map(|f| format!("{} {}", f.rule, f.message))
        })
        .collect();
    out.extend(
        r.findings
            .iter()
            .map(|f| format!("{} {}", f.rule, f.message)),
    );
    out
}

fn guide_findings(text: &str) -> Vec<String> {
    let fake = fake(&[
        ("README.md", readme_with(&["docs/protocol.md"])),
        ("docs/protocol.md", text.to_string()),
    ]);
    findings(&project_report(&fake))
}

#[test]
fn a_valid_protocol_guide_passes_and_is_listed() {
    let fake = fake(&[
        ("README.md", readme_with(&["docs/protocol.md"])),
        ("docs/protocol.md", protocol()),
    ]);
    let report = project_report(&fake);
    assert!(report.ok, "{:?}", findings(&report));
    assert_eq!(report.reports[0].supporting_docs[0].kind, "protocol");
    let text = project::render(&report);
    assert!(
        text.contains("  SUPPORTING kind=protocol path=docs/protocol.md sha256=0"),
        "{text}"
    );
    assert!(fake.is_file("docs/protocol.md"));
}

#[test]
fn every_fixed_guide_has_its_own_path_and_kind() {
    let paths: Vec<&str> = GUIDES.iter().filter_map(|g| g.path).collect();
    assert_eq!(
        paths,
        [
            "docs/protocol.md",
            "clients/README.md",
            "docs/indexing.md",
            "docs/querying.md",
            "docs/gke.md",
            "docs/client-integration.md"
        ]
    );
    for spec in &GUIDES {
        let path = spec.path.unwrap();
        let text = guide_text(
            spec.h2,
            spec.table_heading,
            spec.table_header,
            &[&vec!["x"; spec.table_header.len()].join(" | ")],
        );
        let fake = fake(&[("README.md", readme_with(&[path])), (path, text)]);
        let report = project_report(&fake);
        assert!(report.ok, "{path}: {:?}", findings(&report));
        assert_eq!(report.reports[0].supporting_docs[0].kind, spec.kind);
    }
}

#[test]
fn d1_one_h1_and_the_h2_headings_in_order() {
    assert!(guide_findings(&protocol()).is_empty());
    let two = format!("{}\n# Other\n", protocol());
    assert!(
        guide_findings(&two)
            .contains(&"D1 protocol guide must contain exactly one H1; found 2".to_string())
    );
    let missing = protocol().replace("## Purpose\n\nText.\n", "");
    assert!(
        guide_findings(&missing)
            .contains(&"D1 protocol guide heading '## Purpose' appears 0 times".to_string())
    );
    let swapped = protocol()
        .replace("## Purpose", "## @@")
        .replace("## Current boundaries", "## Purpose")
        .replace("## @@", "## Current boundaries");
    assert!(
        guide_findings(&swapped)
            .iter()
            .any(|m| m.starts_with("D1 protocol guide H2 headings must be exactly"))
    );
}

#[test]
fn d2_each_section_has_content_and_supporting_documents_has_a_link() {
    let empty = protocol().replace("## Purpose\n\nText.\n", "## Purpose\n");
    assert!(guide_findings(&empty).contains(&"D2 Purpose cannot be empty".to_string()));
    let no_link = protocol().replace("- [Readme](../README.md)\n", "None.\n");
    assert!(
        guide_findings(&no_link).contains(
            &"D2 Supporting documents must contain at least one Markdown link".to_string()
        )
    );
}

#[test]
fn d3_the_table_header_rows_and_keys() {
    let wrong = protocol().replace(
        "| Fact | Canonical source | Discovery |",
        "| Fact | Source | Discovery |",
    );
    assert!(guide_findings(&wrong).contains(
        &"D3 Contract map columns must be: Fact | Canonical source | Discovery".to_string()
    ));
    let short = protocol().replace(
        "| Wire | `proto/a.proto` | `GET /a` |",
        "| Wire | `proto/a.proto` |",
    );
    assert!(
        guide_findings(&short)
            .contains(&"D3 Contract map rows must have 3 non-empty cells".to_string())
    );
    let empty_cell = protocol().replace(
        "| Wire | `proto/a.proto` | `GET /a` |",
        "| Wire |  | `GET /a` |",
    );
    assert!(
        guide_findings(&empty_cell)
            .contains(&"D3 Contract map rows must have 3 non-empty cells".to_string())
    );
    let dup = protocol().replace(
        "| Wire | `proto/a.proto` | `GET /a` |\n",
        "| Wire | `proto/a.proto` | `GET /a` |\n| wire | `proto/b.proto` | `GET /b` |\n",
    );
    assert!(guide_findings(&dup).contains(&"D3 duplicate Fact: wire".to_string()));
    let no_rows = protocol().replace("| Wire | `proto/a.proto` | `GET /a` |\n", "");
    assert!(guide_findings(&no_rows).contains(
        &"D3 Contract map columns must be: Fact | Canonical source | Discovery".to_string()
    ));
}

#[test]
fn d4_relative_links_exist() {
    let bad = protocol().replace("../README.md", "../MISSING.md");
    assert!(
        guide_findings(&bad)
            .iter()
            .any(|m| m.starts_with("D4 relative link target does not exist"))
    );
}

#[test]
fn a_linked_migration_guide_has_a_distinct_source_and_target_column() {
    let files = |text: String| {
        fake(&[
            ("README.md", readme_with(&["docs/migration-0-5.md"])),
            ("docs/migration-0-5.md", text),
        ])
    };
    let ok = project_report(&files(migration()));
    assert!(ok.ok, "{:?}", findings(&ok));
    assert_eq!(ok.reports[0].supporting_docs[0].kind, "migration");
    let same = migration().replace("| 0.5.0 |", "| 0.4.x |");
    let bad = findings(&project_report(&files(same)));
    assert!(bad.contains(
        &"D3 Compatibility window columns must be: Surface | <source version> | <target version> | Required action"
            .to_string()
    ), "{bad:?}");
}

#[test]
fn p2_the_readme_links_status_and_roadmap() {
    let none = README
        .replace("- [Status](STATUS.md)\n", "")
        .replace("- [Roadmap](ROADMAP.md)\n", "- [Guide](docs/guide.md)\n");
    let report = project_report(&fake(&[("README.md", none)]));
    let found = findings(&report);
    assert!(
        found.contains(&"P2 README must link STATUS.md from Supporting documents".to_string()),
        "{found:?}"
    );
    assert!(
        found.contains(&"P2 README must link ROADMAP.md from Supporting documents".to_string()),
        "{found:?}"
    );
    assert!(project_report(&Fake::with(STATUS)).ok);
}

#[test]
fn p5_status_and_roadmap_titles_follow_the_readme_title() {
    let wrong = STATUS.replace("# Demo status", "# Other status");
    let found = findings(&project_report(&Fake::with(&wrong)));
    assert!(
        found.contains(&"P5 STATUS.md H1 must be '# Demo status'".to_string()),
        "{found:?}"
    );
    let mut fake = Fake::with(STATUS);
    fake.0.insert(
        "ROADMAP.md".into(),
        ROADMAP.replace("# Demo roadmap", "# Demo plan"),
    );
    let found = findings(&project_report(&fake));
    assert!(
        found.contains(&"P5 ROADMAP.md H1 must be '# Demo roadmap'".to_string()),
        "{found:?}"
    );
}

#[test]
fn p6_a_guide_and_its_link_go_together() {
    let unlinked = fake(&[("docs/protocol.md", protocol())]);
    let found = findings(&project_report(&unlinked));
    assert!(
        found.contains(
            &"P6 README must link docs/protocol.md from Supporting documents".to_string()
        ),
        "{found:?}"
    );
    let missing = fake(&[(
        "README.md",
        readme_with(&["docs/gke.md", "docs/migration-0-5.md"]),
    )]);
    let found = findings(&project_report(&missing));
    assert!(
        found.contains(&"P6 linked supporting document does not exist: docs/gke.md".to_string()),
        "{found:?}"
    );
    assert!(
        found.contains(
            &"P6 linked supporting document does not exist: docs/migration-0-5.md".to_string()
        ),
        "{found:?}"
    );
    let ignored = fake(&[
        ("README.md", readme_with(&["docs/other.md"])),
        ("docs/other.md", migration()),
    ]);
    assert!(
        project_report(&ignored).reports[0]
            .supporting_docs
            .is_empty()
    );
}

#[test]
fn p7_a_lumen_project_refuses_reversed_contract_claims() {
    assert!(lumen::is_lumen("apps/lumen"));
    assert!(lumen::is_lumen("x/apps/lumen"));
    assert!(!lumen::is_lumen("apps/lumen2"));
    assert!(!lumen::is_lumen(""));
    let bad = lumen::check(
        "README.md",
        "ok\nA `Fleet` is HA.\nfleet IS autoscaling today\n",
    );
    assert_eq!(bad.iter().map(|f| f.line).collect::<Vec<_>>(), [2, 3]);
    assert_eq!(
        bad[1].message,
        "forbidden Lumen contract assertion in README.md: fleet IS autoscaling today"
    );
    let negative =
        "Fleet is not HA.\n```\nFleet is HA\n```\nGenerated clients do not yet rotate tokens.\n";
    assert!(lumen::check("README.md", negative).is_empty());
}

#[test]
fn p7_applies_only_to_the_lumen_project_folder() {
    let prefixed = |text: &str| {
        let mut fake = Fake(Default::default());
        for (name, body) in [
            ("README.md", README),
            ("STATUS.md", STATUS),
            ("ROADMAP.md", ROADMAP),
        ] {
            let body = if name == "README.md" {
                format!("{body}\n{text}\n")
            } else {
                body.to_string()
            };
            fake.0.insert(format!("apps/lumen/{name}"), body);
        }
        fake.0
            .insert("apps/lumen/docs/guide.md".into(), "# Guide\n".into());
        fake
    };
    let run = |fake: &Fake, dir: &str| {
        let config = fake.config().unwrap();
        let cargo = fake.cargo_packages().unwrap();
        project::report(vec![project::check(fake, &config, &cargo, dir).unwrap()])
    };
    let claim = "The current runtime Kustomize template is Managed.";
    let fake = prefixed(claim);
    let lumen = findings(&run(&fake, "apps/lumen"));
    assert!(
        lumen
            .iter()
            .any(|m| m.starts_with("P7 forbidden Lumen contract assertion in README.md")),
        "{lumen:?}"
    );
    let plain = findings(&run(&prefixed("A calm sentence."), "apps/lumen"));
    assert!(plain.iter().all(|m| !m.starts_with("P7")), "{plain:?}");
}
