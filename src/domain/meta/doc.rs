//! Shared rules of the project documents: the heading list, the first table
//! after a heading and the relative links.

use std::collections::{BTreeMap, BTreeSet};

use super::markdown::{
    github_slug, has_scheme, headings, is_separator_row, markdown_links, parse_table_row,
    section_end, without_fenced_content,
};
use super::ports::MetaRepo;
use super::report::Finding;

/// The line of each H2 that appears once, and the findings for the required
/// H2 headings that do not appear exactly once or are out of order.
pub fn required_heading_positions(
    lines: &[String],
    required: &[&str],
    doc: &str,
    rule: &'static str,
) -> (BTreeMap<String, usize>, Vec<Finding>) {
    let h2 = headings(lines, 2);
    let mut positions: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, name) in &h2 {
        positions.entry(name.clone()).or_default().push(*index);
    }
    let mut findings = Vec::new();
    for name in required {
        let count = positions.get(*name).map_or(0, Vec::len);
        if count != 1 {
            findings.push(Finding::new(
                rule,
                1,
                format!("{doc} heading '## {name}' appears {count} times"),
            ));
        }
    }
    let resolved: BTreeMap<String, usize> = positions
        .into_iter()
        .filter(|(_, found)| found.len() == 1)
        .map(|(name, found)| (name, found[0]))
        .collect();
    if required.iter().all(|name| resolved.contains_key(*name)) {
        let actual: Vec<&str> = h2.iter().map(|(_, name)| name.as_str()).collect();
        if actual != required {
            findings.push(Finding::new(
                rule,
                1,
                format!(
                    "{doc} H2 headings must be exactly: {}",
                    required.join(" | ")
                ),
            ));
        }
    }
    (resolved, findings)
}

/// The rows of the first table in the section of the heading at
/// `heading_at`: line index and cells.
pub fn table_after(
    lines: &[String],
    heading_at: usize,
    heading_level: usize,
) -> Vec<(usize, Vec<String>)> {
    let end = section_end(lines, heading_at, heading_level);
    let mut rows = Vec::new();
    let mut started = false;
    for (index, line) in lines.iter().enumerate().take(end).skip(heading_at + 1) {
        if line.trim_start().starts_with('|') {
            rows.push((index, parse_table_row(line)));
            started = true;
        } else if started && !line.trim().is_empty() {
            break;
        }
    }
    rows
}

/// Whether a table has the header row `header` and a separator row after it.
pub fn has_table_header(rows: &[(usize, Vec<String>)], header: &[&str]) -> bool {
    rows.len() >= 2
        && rows[0]
            .1
            .iter()
            .map(String::as_str)
            .eq(header.iter().copied())
        && is_separator_row(&rows[1].1)
}

/// The path of `rel`, read from the folder `dir`; `None` when it leaves the
/// checkout.
fn resolve(dir: &str, rel: &str) -> Option<String> {
    let mut parts: Vec<&str> = dir.split('/').filter(|p| !p.is_empty()).collect();
    for part in rel.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            other => parts.push(other),
        }
    }
    Some(parts.join("/"))
}

/// The relative links of a document: each must stay inside the checkout,
/// name a file and, for a Markdown file, name an existing heading.
pub fn validate_relative_links(
    repo: &dyn MetaRepo,
    doc_path: &str,
    doc_dir: &str,
    lines: &[&str],
    rule: &'static str,
) -> Vec<Finding> {
    let mut findings = Vec::new();
    let mut anchors_of: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (index, raw) in markdown_links(lines) {
        if raw.is_empty() || raw.starts_with('#') || raw.starts_with('/') || has_scheme(&raw) {
            continue;
        }
        if raw.contains(['<', '>', '{', '}']) {
            continue;
        }
        let (path_part, anchor) = raw.split_once('#').unwrap_or((raw.as_str(), ""));
        let path_part = path_part.split('?').next().unwrap_or("");
        let target = if path_part.is_empty() {
            Some(doc_path.to_string())
        } else {
            resolve(doc_dir, path_part)
        };
        let Some(target) = target else {
            findings.push(Finding::new(
                rule,
                index + 1,
                format!("relative link leaves the checkout: {raw}"),
            ));
            continue;
        };
        if !repo.is_file(&target) {
            findings.push(Finding::new(
                rule,
                index + 1,
                format!("relative link target does not exist: {raw}"),
            ));
            continue;
        }
        if !anchor.is_empty() && target.to_lowercase().ends_with(".md") {
            let anchors = anchors_of.entry(target.clone()).or_insert_with(|| {
                let text = repo.read_text(&target).unwrap_or_default();
                let target_lines: Vec<&str> = text.lines().collect();
                let prose = without_fenced_content(&target_lines);
                (1..=4)
                    .flat_map(|level| headings(&prose, level))
                    .map(|(_, name)| github_slug(&name))
                    .collect()
            });
            if !anchors.contains(anchor) {
                findings.push(Finding::new(
                    rule,
                    index + 1,
                    format!("Markdown anchor does not exist: {raw}"),
                ));
            }
        }
    }
    findings
}
