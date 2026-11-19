//! The supporting guides of a project (D1 to D4): protocol, clients,
//! indexing, querying, GKE, client integration and migration. Each has fixed
//! H2 headings and one fixed table.

use std::collections::BTreeSet;

use serde::Serialize;

use super::doc::{required_heading_positions, table_after, validate_relative_links};
use super::markdown::{
    headings, is_separator_row, markdown_links, section_end, strip_inline, without_fenced_content,
};
use super::ports::{DocInput, MetaRepo};
use super::report::Finding;

/// What one kind of guide must contain.
pub struct GuideSpec {
    /// The path of the guide from the project folder, or `None` for a
    /// migration guide (any `docs/migration-*.md` that the README links).
    pub path: Option<&'static str>,
    pub kind: &'static str,
    pub label: &'static str,
    pub h2: &'static [&'static str],
    pub table_heading: &'static str,
    pub table_header: &'static [&'static str],
}

const MAP_HEADER: &[&str] = &["Fact", "Canonical source", "Discovery"];

pub const GUIDES: [GuideSpec; 6] = [
    GuideSpec {
        path: Some("docs/protocol.md"),
        kind: "protocol",
        label: "protocol guide",
        h2: &[
            "Purpose",
            "Contract map",
            "Use the protocol",
            "Current boundaries",
            "Supporting documents",
        ],
        table_heading: "Contract map",
        table_header: MAP_HEADER,
    },
    GuideSpec {
        path: Some("clients/README.md"),
        kind: "clients",
        label: "generated-client guide",
        h2: &[
            "Contract",
            "Generate",
            "Language matrix",
            "Connect",
            "Current boundaries",
            "Verification",
            "Supporting documents",
        ],
        table_heading: "Language matrix",
        table_header: &[
            "Language",
            "Generated form",
            "Transport",
            "Auth input",
            "Current limits",
        ],
    },
    GuideSpec {
        path: Some("docs/indexing.md"),
        kind: "indexing",
        label: "indexing guide",
        h2: &[
            "Purpose",
            "Contract map",
            "Data ownership",
            "Schema contract",
            "Write contract",
            "Durability",
            "Rebuild and activation",
            "Current boundaries",
            "Supporting documents",
        ],
        table_heading: "Contract map",
        table_header: MAP_HEADER,
    },
    GuideSpec {
        path: Some("docs/querying.md"),
        kind: "querying",
        label: "querying guide",
        h2: &[
            "Purpose",
            "Data ownership",
            "Contract map",
            "Search model",
            "Result controls",
            "Facets and metrics",
            "Limits and failures",
            "Compatibility and migration",
            "Current boundaries",
            "Supporting documents",
        ],
        table_heading: "Contract map",
        table_header: MAP_HEADER,
    },
    GuideSpec {
        path: Some("docs/gke.md"),
        kind: "gke",
        label: "GKE guide",
        h2: &[
            "Purpose",
            "Standalone GKE instance",
            "Contract map",
            "Support tiers",
            "Runtime size and topology",
            "Kubernetes-native contract",
            "GKE Standard Regional profile",
            "Storage, placement, and disruption",
            "Identity and networking",
            "Verification",
            "Current boundaries",
            "Supporting documents",
        ],
        table_heading: "Contract map",
        table_header: MAP_HEADER,
    },
    GuideSpec {
        path: Some("docs/client-integration.md"),
        kind: "client-integration",
        label: "client-integration guide",
        h2: &[
            "Purpose",
            "Contract map",
            "Responsibility boundary",
            "Connection profiles",
            "Generated client behavior",
            "Kubernetes workload template",
            "Source integration",
            "Failure handling",
            "Verification",
            "Current boundaries",
            "Supporting documents",
        ],
        table_heading: "Contract map",
        table_header: MAP_HEADER,
    },
];

pub const MIGRATION: GuideSpec = GuideSpec {
    path: None,
    kind: "migration",
    label: "migration guide",
    h2: &[
        "Purpose",
        "Compatibility window",
        "Schema migration",
        "Request migration",
        "Response migration",
        "Managed activation",
        "Migration tools",
        "Verification",
        "Supporting documents",
    ],
    table_heading: "Compatibility window",
    table_header: &["Surface", "0.4.x", "0.5.0", "Required action"],
};

/// Whether a file name is a migration guide: `migration-*.md`.
pub fn is_migration_guide(name: &str) -> bool {
    name.starts_with("migration-") && name.ends_with(".md")
}

/// The result of one supporting guide.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct GuideReport {
    pub kind: String,
    pub path: String,
    pub sha256: String,
    pub ok: bool,
    pub findings: Vec<Finding>,
}

/// A migration table names distinct source and target columns.
fn valid_migration_header(cells: &[String]) -> bool {
    if cells.len() != 4 {
        return false;
    }
    let n: Vec<String> = cells
        .iter()
        .map(|c| strip_inline(c).trim().to_string())
        .collect();
    n[0] == "Surface"
        && n[3] == "Required action"
        && !n[1].is_empty()
        && !n[2].is_empty()
        && n[1] != n[2]
}

/// Check one guide against its spec.
pub fn check(input: &DocInput, repo: &dyn MetaRepo, spec: &GuideSpec) -> GuideReport {
    let lines: Vec<&str> = input.text.lines().collect();
    let prose = without_fenced_content(&lines);
    let mut findings: Vec<Finding> = Vec::new();

    let h1 = headings(&prose, 1).len();
    if h1 != 1 {
        findings.push(Finding::new(
            "D1",
            1,
            format!("{} must contain exactly one H1; found {h1}", spec.label),
        ));
    }
    let (positions, heading_findings) =
        required_heading_positions(&prose, spec.h2, spec.label, "D1");
    findings.extend(heading_findings);

    for section in spec.h2 {
        let Some(&start) = positions.get(*section) else {
            continue;
        };
        let end = section_end(&prose, start, 2);
        if !prose[start + 1..end].iter().any(|l| !l.trim().is_empty()) {
            findings.push(Finding::new(
                "D2",
                start + 1,
                format!("{section} cannot be empty"),
            ));
        }
    }

    if let Some(&at) = positions.get(spec.table_heading) {
        let rows = table_after(&prose, at, 2);
        let header_ok = match rows.first() {
            Some((_, cells)) if spec.kind == "migration" => valid_migration_header(cells),
            Some(_) => rows[0]
                .1
                .iter()
                .map(String::as_str)
                .eq(spec.table_header.iter().copied()),
            None => false,
        };
        if rows.len() < 3 || !header_ok || !is_separator_row(&rows[1].1) {
            let expected = if spec.kind == "migration" {
                "Surface | <source version> | <target version> | Required action".to_string()
            } else {
                spec.table_header.join(" | ")
            };
            findings.push(Finding::new(
                "D3",
                at + 1,
                format!("{} columns must be: {expected}", spec.table_heading),
            ));
        } else {
            let width = spec.table_header.len();
            let mut seen: BTreeSet<String> = BTreeSet::new();
            for (row_at, cells) in &rows[2..] {
                if cells.len() != width || cells.iter().any(String::is_empty) {
                    findings.push(Finding::new(
                        "D3",
                        row_at + 1,
                        format!(
                            "{} rows must have {width} non-empty cells",
                            spec.table_heading
                        ),
                    ));
                    continue;
                }
                let key = strip_inline(&cells[0]).trim().to_lowercase();
                if !seen.insert(key) {
                    findings.push(Finding::new(
                        "D3",
                        row_at + 1,
                        format!("duplicate {}: {}", spec.table_header[0], cells[0]),
                    ));
                }
            }
        }
    }

    if let Some(&start) = positions.get("Supporting documents") {
        let end = section_end(&prose, start, 2);
        if markdown_links(&lines[start + 1..end]).is_empty() {
            findings.push(Finding::new(
                "D2",
                start + 1,
                "Supporting documents must contain at least one Markdown link",
            ));
        }
    }

    findings.extend(validate_relative_links(
        repo,
        &input.path,
        &input.dir,
        &lines,
        "D4",
    ));
    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    GuideReport {
        kind: spec.kind.to_string(),
        path: input.path.clone(),
        sha256: input.sha256.clone(),
        ok: findings.is_empty(),
        findings,
    }
}
