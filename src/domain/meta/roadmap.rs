//! The ROADMAP contract (M1 to M6): flat H3 items under three horizons,
//! each with fixed fields.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;

use super::doc::{required_heading_positions, validate_relative_links};
use super::markdown::{
    github_slug, has_link, headings, section_end, strip_inline, without_fenced_content,
};
use super::ports::{DocInput, MetaRepo};
use super::report::Finding;
use super::status::{parse_id, self_graded};

pub const ROADMAP_H2: [&str; 4] = [
    "Purpose",
    "Near-term outcomes",
    "Later outcomes",
    "Non-goals",
];
const OUTCOME_FIELDS: [&str; 5] = [
    "ID",
    "Outcome",
    "Boundary",
    "Completion evidence",
    "Tracking",
];
const NON_GOAL_FIELDS: [&str; 2] = ["ID", "Reason"];

static FIELD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^- ([A-Za-z][A-Za-z ]*):\s*(.*)$").expect("fixed pattern"));
static ID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").expect("fixed pattern"));

/// One outcome or non-goal.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Item {
    pub name: String,
    pub id: String,
    pub horizon: String,
    pub fields: BTreeMap<String, String>,
}

/// The result of one ROADMAP document.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct RoadmapReport {
    pub path: String,
    pub sha256: String,
    pub ok: bool,
    pub items: Vec<Item>,
    pub findings: Vec<Finding>,
}

/// The fields of an item and their order. A line that starts with two spaces
/// continues the field above it.
fn detail_fields(
    lines: &[String],
    start: usize,
    end: usize,
) -> (BTreeMap<String, String>, Vec<String>) {
    let mut fields: BTreeMap<String, String> = BTreeMap::new();
    let mut order = Vec::new();
    let mut current = String::new();
    for line in &lines[start + 1..end] {
        if let Some(c) = FIELD.captures(line) {
            current = c[1].to_string();
            order.push(current.clone());
            fields.insert(current.clone(), c[2].trim().to_string());
            continue;
        }
        if !current.is_empty() && line.starts_with("  ") && !line.trim().is_empty() {
            let joined = format!("{} {}", fields[&current], line.trim());
            fields.insert(current.clone(), joined.trim().to_string());
        } else if !line.trim().is_empty() {
            current.clear();
        }
    }
    (fields, order)
}

/// Check one ROADMAP document.
pub fn check(input: &DocInput, repo: &dyn MetaRepo) -> RoadmapReport {
    let lines: Vec<&str> = input.text.lines().collect();
    let prose = without_fenced_content(&lines);
    let mut findings: Vec<Finding> = Vec::new();
    let mut items: Vec<Item> = Vec::new();

    let h1 = headings(&prose, 1).len();
    if h1 != 1 {
        findings.push(Finding::new(
            "M1",
            1,
            format!("ROADMAP must contain exactly one H1; found {h1}"),
        ));
    }
    if !headings(&prose, 4).is_empty() {
        findings.push(Finding::new(
            "M1",
            1,
            "ROADMAP items are flat H3 sections; H4 hierarchy is not allowed",
        ));
    }
    let (positions, heading_findings) =
        required_heading_positions(&prose, &ROADMAP_H2, "ROADMAP", "M1");
    findings.extend(heading_findings);

    if self_graded(&prose.join("\n")) {
        findings.push(Finding::new(
            "M2",
            1,
            "ROADMAP must leave work state, owners, schedules, and percentages in the tracker",
        ));
    }

    if let Some(&start) = positions.get("Purpose") {
        let end = section_end(&prose, start, 2);
        if !prose[start + 1..end].iter().any(|l| !l.trim().is_empty()) {
            findings.push(Finding::new("M3", start + 1, "Purpose cannot be empty"));
        }
    }

    let mut seen_ids: BTreeSet<String> = BTreeSet::new();
    let mut seen_names: BTreeSet<String> = BTreeSet::new();
    for horizon in &ROADMAP_H2[1..] {
        let Some(&start) = positions.get(*horizon) else {
            continue;
        };
        let end = section_end(&prose, start, 2);
        let h3: Vec<(usize, String)> = headings(&prose, 3)
            .into_iter()
            .filter(|(at, _)| *at > start && *at < end)
            .collect();
        let body: Vec<&str> = prose[start + 1..end]
            .iter()
            .filter(|l| !l.trim().is_empty() && !l.starts_with("### "))
            .map(|l| l.trim())
            .collect();
        if h3.is_empty() && body != ["No items."] {
            findings.push(Finding::new(
                "M3",
                start + 1,
                format!("{horizon} must contain H3 items or exactly 'No items.'"),
            ));
        }
        let non_goals = *horizon == "Non-goals";
        let required: &[&str] = if non_goals {
            &NON_GOAL_FIELDS
        } else {
            &OUTCOME_FIELDS
        };
        let content: &[&str] = if non_goals {
            &["Reason"]
        } else {
            &["Outcome", "Boundary", "Completion evidence"]
        };
        for (n, (at, name)) in h3.iter().enumerate() {
            let line = at + 1;
            let item_end = h3.get(n + 1).map_or(end, |next| next.0);
            let (fields, order) = detail_fields(&prose, *at, item_end);
            if order != required {
                findings.push(Finding::new(
                    "M4",
                    line,
                    format!(
                        "{horizon} item fields must be exactly: {}",
                        required.join(" | ")
                    ),
                ));
            }
            let raw_id = fields.get("ID").cloned().unwrap_or_default();
            let id = parse_id(&raw_id);
            if !ID.is_match(&id) {
                findings.push(Finding::new(
                    "M4",
                    line,
                    format!("invalid roadmap ID: {raw_id}"),
                ));
            }
            if !id.is_empty() && github_slug(name) != id {
                findings.push(Finding::new(
                    "M4",
                    line,
                    "roadmap ID must match the H3 Markdown anchor",
                ));
            }
            if seen_names.contains(name) || seen_ids.contains(&id) {
                findings.push(Finding::new(
                    "M4",
                    line,
                    "roadmap names and IDs must be unique",
                ));
            }
            seen_names.insert(name.clone());
            seen_ids.insert(id.clone());
            for field in content {
                let text = fields.get(*field).map_or("", String::as_str);
                if strip_inline(text).split_whitespace().count() < 3 {
                    findings.push(Finding::new(
                        "M4",
                        line,
                        format!("{field} must state a concrete boundary or outcome"),
                    ));
                }
            }
            if !non_goals {
                let tracking = fields.get("Tracking").map_or("", String::as_str);
                if tracking != "Not assigned." && !has_link(tracking) {
                    findings.push(Finding::new(
                        "M5",
                        line,
                        "Tracking must be 'Not assigned.' or a Markdown link",
                    ));
                }
            }
            items.push(Item {
                name: name.clone(),
                id,
                horizon: (*horizon).to_string(),
                fields,
            });
        }
    }

    findings.extend(validate_relative_links(
        repo,
        &input.path,
        &input.dir,
        &lines,
        "M6",
    ));
    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    RoadmapReport {
        path: input.path.clone(),
        sha256: input.sha256.clone(),
        ok: findings.is_empty(),
        items,
        findings,
    }
}
