//! The README contract (R1 to R12): the headings, the workflow, the
//! links and the code fences. The capability rules are in `capability`.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use regex::Regex;

use super::capability;
use super::config::MetaConfig;
use super::markdown::{
    fence_left_open, has_link, has_scheme, markdown_links, section_end, without_fenced_content,
};
use super::ports::{CargoIndex, MetaRepo, ReadmeInput};
use super::report::{Finding, ReadmeReport};

/// The required H2 headings, in order.
pub const REQUIRED_H2: [&str; 5] = [
    "Brief",
    "Primary workflow",
    "Contract discovery",
    "Capabilities",
    "Supporting documents",
];

static NUMBERED_STEP: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\d+\.\s+\S").expect("fixed pattern"));

/// Check one README against the contract.
pub fn check(
    input: &ReadmeInput,
    config: &MetaConfig,
    repo: &dyn MetaRepo,
    cargo: &CargoIndex,
) -> ReadmeReport {
    let lines: Vec<&str> = input.text.lines().collect();
    let prose = without_fenced_content(&lines);
    let mut findings: Vec<Finding> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    let mut product_sections: Vec<String> = Vec::new();
    let mut capabilities = Vec::new();

    let h1 = prose.iter().filter(|l| l.starts_with("# ")).count();
    if h1 != 1 {
        findings.push(Finding::new(
            "R1",
            1,
            format!("README must contain exactly one H1; found {h1}"),
        ));
    }

    let h2: Vec<(usize, String)> = prose
        .iter()
        .enumerate()
        .filter(|(_, l)| l.starts_with("## "))
        .map(|(i, l)| (i, l[3..].trim().to_string()))
        .collect();
    let mut positions: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (index, name) in &h2 {
        positions.entry(name.as_str()).or_default().push(*index);
    }
    let count = |name: &str| positions.get(name).map_or(0, Vec::len);
    for name in REQUIRED_H2 {
        if count(name) != 1 {
            findings.push(Finding::new(
                "R1",
                1,
                format!("required heading '## {name}' appears {} times", count(name)),
            ));
        }
    }

    if REQUIRED_H2.iter().all(|n| count(n) == 1) {
        let at = |name: &str| positions[name][0];
        let ordered: Vec<usize> = REQUIRED_H2.iter().map(|n| at(n)).collect();
        if !ordered.is_sorted() {
            findings.push(Finding::new(
                "R2",
                1,
                "required H2 sections are not in the contract order",
            ));
        }
        let workflow_at = at("Primary workflow");
        let discovery_at = at("Contract discovery");
        product_sections = h2
            .iter()
            .filter(|(i, _)| workflow_at < *i && *i < discovery_at)
            .map(|(_, n)| n.clone())
            .collect();
        if product_sections.is_empty() {
            findings.push(Finding::new(
                "R4",
                workflow_at + 1,
                "add at least one app- or library-specific functional H2 between \
                 Primary workflow and Contract discovery",
            ));
        }

        let brief_at = at("Brief");
        let brief_end = section_end(&prose, brief_at, 2);
        if !prose[brief_at + 1..brief_end]
            .iter()
            .any(|l| !l.trim().is_empty())
        {
            findings.push(Finding::new("R3", brief_at + 1, "Brief is empty"));
        }

        let workflow_end = section_end(&prose, workflow_at, 2);
        let steps = prose[workflow_at + 1..workflow_end]
            .iter()
            .filter(|l| NUMBERED_STEP.is_match(l))
            .count();
        if steps < 2 {
            findings.push(Finding::new(
                "R3",
                workflow_at + 1,
                "Primary workflow must contain at least two numbered steps",
            ));
        }

        let supporting_at = at("Supporting documents");
        let supporting_end = section_end(&prose, supporting_at, 2);
        if !has_link(&prose[supporting_at + 1..supporting_end].join("\n")) {
            findings.push(Finding::new(
                "R3",
                supporting_at + 1,
                "Supporting documents must link to at least one maintained document",
            ));
        }
    }

    if count("Capabilities") == 1 {
        let outcome = capability::check(
            &capability::Section {
                lines: &lines,
                prose: &prose,
                at: positions["Capabilities"][0],
                dir: &input.dir,
            },
            config,
            repo,
            cargo,
        );
        findings.extend(outcome.findings);
        warnings.extend(outcome.warnings);
        capabilities = outcome.capabilities;
    }

    let (relative_links, link_findings) = relative_links(input, &lines, repo);
    findings.extend(link_findings);
    if fence_left_open(&lines) {
        findings.push(Finding::new(
            "R11",
            lines.len(),
            "fenced code block is not closed",
        ));
    }

    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    ReadmeReport {
        path: input.path.clone(),
        sha256: input.sha256.clone(),
        ok: findings.is_empty(),
        product_sections,
        gate_count: capabilities.iter().map(|c| c.gates.len()).sum(),
        capabilities,
        relative_links,
        warnings,
        findings,
    }
}

/// R9: every relative link target exists. Fenced blocks and code spans are
/// not read. Returns the number of relative links too.
fn relative_links(
    input: &ReadmeInput,
    lines: &[&str],
    repo: &dyn MetaRepo,
) -> (usize, Vec<Finding>) {
    let mut count = 0;
    let mut findings = Vec::new();
    for (index, raw) in markdown_links(lines) {
        if raw.is_empty() || raw.starts_with(['#', '/']) || has_scheme(&raw) {
            continue;
        }
        if raw.contains(['<', '>', '{', '}']) {
            continue;
        }
        count += 1;
        let target = raw
            .split('#')
            .next()
            .unwrap_or("")
            .split('?')
            .next()
            .unwrap_or("");
        if !target.is_empty() && !repo.exists_from(&input.dir, target) {
            findings.push(Finding::new(
                "R9",
                index + 1,
                format!("relative link target does not exist: {raw}"),
            ));
        }
    }
    (count, findings)
}
