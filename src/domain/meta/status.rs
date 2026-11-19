//! The STATUS contract (S1 to S7): one flat support matrix with a state, a
//! scope, a limit and the evidence of each surface.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;

use super::doc::{
    has_table_header, required_heading_positions, table_after, validate_relative_links,
};
use super::gate;
use super::markdown::{
    code_spans, headings, is_separator_row, link_targets, section_end, without_fenced_content,
};
use super::ports::{CargoIndex, DocInput, MetaRepo};
use super::report::Finding;

pub const STATUS_H2: [&str; 4] = [
    "Scope",
    "State definitions",
    "Support matrix",
    "Evidence policy",
];
const DEFINITION_HEADER: [&str; 2] = ["State", "Meaning"];
const HEADER: [&str; 6] = [
    "Surface",
    "ID",
    "State",
    "Supported scope",
    "Limits",
    "Evidence",
];
const STATES: [&str; 3] = ["Supported", "Limited", "Not supported"];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("fixed pattern")
}

static BANNED_FIELD: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?mi)^\s*(?:[-*]\s+)?(?:Status|Maturity|Production|Progress|Percent complete|Owner|Due|Target date|ETA)\s*:",
    )
});
static PERCENT: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)(?:\b\d{1,3}\s*%\s*(?:complete|done)|(?:progress|complete)\s*[:=]?\s*\d{1,3}\s*%)")
});
static ID: LazyLock<Regex> = LazyLock::new(|| re(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"));
static BACKTICKED: LazyLock<Regex> = LazyLock::new(|| re(r"^`([^`]+)`$"));

/// One row of the support matrix.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Surface {
    pub name: String,
    pub id: String,
    pub state: String,
    pub scope: String,
    pub limits: String,
    pub evidence: String,
    pub gates: Vec<String>,
    pub roadmap_id: Option<String>,
}

/// The result of one STATUS document.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct StatusReport {
    pub path: String,
    pub sha256: String,
    pub ok: bool,
    pub surfaces: Vec<Surface>,
    pub warnings: Vec<String>,
    pub findings: Vec<Finding>,
}

/// Whether a document carries a self-graded field, an owner, a date or a
/// percent-complete figure (S2, M2).
pub fn self_graded(text: &str) -> bool {
    BANNED_FIELD.is_match(text) || PERCENT.is_match(text)
}

/// The ROADMAP anchors that the links of `cell` name.
pub fn roadmap_link_ids(cell: &str) -> Vec<String> {
    link_targets(cell)
        .into_iter()
        .filter_map(|target| {
            let (path, anchor) = target.split_once('#')?;
            let name = path.rsplit('/').next().unwrap_or(path);
            (name == "ROADMAP.md" && !anchor.is_empty()).then(|| anchor.to_string())
        })
        .collect()
}

pub fn parse_id(cell: &str) -> String {
    BACKTICKED
        .captures(cell)
        .map(|c| c[1].to_string())
        .unwrap_or_default()
}

/// Check one STATUS document.
pub fn check(input: &DocInput, repo: &dyn MetaRepo, cargo: &CargoIndex) -> StatusReport {
    let lines: Vec<&str> = input.text.lines().collect();
    let prose = without_fenced_content(&lines);
    let mut findings: Vec<Finding> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    let mut surfaces: Vec<Surface> = Vec::new();

    let h1 = headings(&prose, 1).len();
    if h1 != 1 {
        findings.push(Finding::new(
            "S1",
            1,
            format!("STATUS must contain exactly one H1; found {h1}"),
        ));
    }
    if !headings(&prose, 3).is_empty() {
        findings.push(Finding::new(
            "S1",
            1,
            "STATUS uses the flat Support matrix; H3 sections are not allowed",
        ));
    }
    let (positions, heading_findings) =
        required_heading_positions(&prose, &STATUS_H2, "STATUS", "S1");
    findings.extend(heading_findings);

    let text = prose.join("\n");
    if self_graded(&text) {
        findings.push(Finding::new(
            "S2",
            1,
            "STATUS must not contain self-graded, owner, schedule, or percent-complete fields",
        ));
    }

    if let Some(&at) = positions.get("State definitions")
        && !definitions_ok(&table_after(&prose, at, 2))
    {
        findings.push(Finding::new(
            "S3",
            at + 1,
            "State definitions must define Supported, Limited, and Not supported in that order",
        ));
    }
    for section in ["Scope", "Evidence policy"] {
        if let Some(&start) = positions.get(section) {
            let end = section_end(&prose, start, 2);
            if !prose[start + 1..end].iter().any(|l| !l.trim().is_empty()) {
                findings.push(Finding::new(
                    "S3",
                    start + 1,
                    format!("{section} cannot be empty"),
                ));
            }
        }
    }

    if let Some(&at) = positions.get("Support matrix") {
        let mut rows = table_after(&prose, at, 2);
        if rows.len() < 3 || !has_table_header(&rows, &HEADER) {
            findings.push(Finding::new(
                "S4",
                at + 1,
                format!("Support matrix columns must be: {}", HEADER.join(" | ")),
            ));
            rows.clear();
        }
        let mut seen_names: BTreeSet<String> = BTreeSet::new();
        let mut seen_ids: BTreeSet<String> = BTreeSet::new();
        for (row_at, cells) in rows.iter().skip(2) {
            let line = row_at + 1;
            if cells.len() != 6 {
                findings.push(Finding::new(
                    "S4",
                    line,
                    "Support matrix row must have six cells",
                ));
                continue;
            }
            let [name, raw_id, state, scope, limits, evidence] = &cells[..] else {
                continue;
            };
            let id = parse_id(raw_id);
            if name.is_empty() || scope.is_empty() || limits.is_empty() || evidence.is_empty() {
                findings.push(Finding::new(
                    "S4",
                    line,
                    "Support matrix cells cannot be empty",
                ));
            }
            if !ID.is_match(&id) {
                findings.push(Finding::new(
                    "S4",
                    line,
                    format!("invalid support ID: {raw_id}"),
                ));
            }
            if seen_names.contains(name) || seen_ids.contains(&id) {
                findings.push(Finding::new(
                    "S4",
                    line,
                    "Support names and IDs must be unique",
                ));
            }
            if !STATES.contains(&state.as_str()) {
                findings.push(Finding::new(
                    "S4",
                    line,
                    format!("invalid support state: {state}"),
                ));
            }
            seen_names.insert(name.clone());
            seen_ids.insert(id.clone());

            let mut gates: Vec<String> = Vec::new();
            let mut roadmap_id: Option<String> = None;
            if state == "Supported" || state == "Limited" {
                gates = code_spans(evidence);
                if gates.is_empty() {
                    findings.push(Finding::new(
                        "S5",
                        line,
                        format!("{state} row must name an executable backticked gate"),
                    ));
                }
                for command in &gates {
                    let found =
                        gate::validate(repo, &input.dir, command, line, cargo, &mut warnings);
                    findings.extend(
                        found
                            .into_iter()
                            .map(|f| Finding::new("S5", f.line, f.message)),
                    );
                }
                if state == "Limited" {
                    let ids = roadmap_link_ids(limits);
                    roadmap_id = (ids.len() == 1).then(|| ids[0].clone());
                    if ids.len() != 1 {
                        findings.push(Finding::new(
                            "S6",
                            line,
                            "Limited row must link its boundary to exactly one ROADMAP outcome or non-goal",
                        ));
                    }
                }
            } else if state == "Not supported" {
                let ids = roadmap_link_ids(evidence);
                roadmap_id = (ids.len() == 1).then(|| ids[0].clone());
                if ids.len() != 1 {
                    findings.push(Finding::new(
                        "S6",
                        line,
                        "Not supported row must link to exactly one ROADMAP outcome or non-goal",
                    ));
                }
                if !code_spans(evidence).is_empty() {
                    findings.push(Finding::new(
                        "S6",
                        line,
                        "Not supported evidence must not claim a current gate",
                    ));
                }
            }
            surfaces.push(Surface {
                name: name.clone(),
                id,
                state: state.clone(),
                scope: scope.clone(),
                limits: limits.clone(),
                evidence: evidence.clone(),
                gates,
                roadmap_id,
            });
        }
    }

    findings.extend(validate_relative_links(
        repo,
        &input.path,
        &input.dir,
        &lines,
        "S7",
    ));
    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    StatusReport {
        path: input.path.clone(),
        sha256: input.sha256.clone(),
        ok: findings.is_empty(),
        surfaces,
        warnings,
        findings,
    }
}

/// The State definitions table: header, separator and the three states in
/// order, each with a meaning.
fn definitions_ok(rows: &[(usize, Vec<String>)]) -> bool {
    if rows.len() != 5 || !has_table_header(rows, &DEFINITION_HEADER) {
        return false;
    }
    if rows
        .get(1)
        .is_none_or(|(_, cells)| !is_separator_row(cells))
    {
        return false;
    }
    let body = &rows[2..];
    let names: Vec<&str> = body
        .iter()
        .filter(|(_, cells)| cells.len() == 2)
        .map(|(_, cells)| cells[0].as_str())
        .collect();
    names == STATES && body.iter().all(|(_, c)| c.len() == 2 && !c[1].is_empty())
}
