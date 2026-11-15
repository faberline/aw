//! The Capabilities section of a README (R5 to R8, R10, R12): the index
//! table, the detail entries, their sources and their gates.

use std::collections::{BTreeSet, HashMap};
use std::sync::LazyLock;

use regex::Regex;

use super::config::{CORE_PREFIX, MetaConfig};
use super::gate;
use super::markdown::{is_separator_row, parse_table_row, section_end, source_blocks};
use super::ports::{CargoIndex, MetaRepo};
use super::report::{Capability, Finding};

const INDEX_HEADER: [&str; 4] = ["Capability", "ID", "User promise", "Sources"];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("fixed pattern")
}

static ID: LazyLock<Regex> = LazyLock::new(|| re(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"));
static BACKTICKED: LazyLock<Regex> = LazyLock::new(|| re(r"^`([^`]+)`$"));
static SELF_GRADE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?m)^\s*(?:[-*]\s+)?(?:Status|Maturity|Production|Feature Class|Required Verification)\s*:",
    )
});
static ISSUE: LazyLock<Regex> = LazyLock::new(|| re(r"#\d+"));
static ROOT_WI: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^\s*[-*]\s+Root WI\s*:"));
static DETAIL_ID: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^- ID:\s*`([^`]+)`\s*$"));
static DETAIL_PROMISE: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^- Promise:\s*\S"));
static FIELD_LINE: LazyLock<Regex> = LazyLock::new(|| re(r"^- [A-Z][A-Za-z ]*:"));
static GATE: LazyLock<Regex> = LazyLock::new(|| re(r"^- Gate:\s*`([^`]+)`\s*$"));
static LINK_TEXT: LazyLock<Regex> = LazyLock::new(|| re(r"\[[^\]]+\]\([^)]+\)"));
static LEAD_DASH: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*-\s*"));

/// The Capabilities H2 of one README.
pub struct Section<'a> {
    /// Every line of the README.
    pub lines: &'a [&'a str],
    /// The same lines with fenced blocks blanked.
    pub prose: &'a [String],
    /// Line index of `## Capabilities`.
    pub at: usize,
    /// Folder of the README from the repository root.
    pub dir: &'a str,
}

pub struct Outcome {
    pub findings: Vec<Finding>,
    pub warnings: Vec<String>,
    pub capabilities: Vec<Capability>,
}

/// The source token regex for the configured prefixes.
fn source_pattern(config: &MetaConfig) -> Regex {
    let mut alternatives = Vec::new();
    if !config.source_prefixes.is_empty() {
        let prefixes: Vec<String> = config
            .source_prefixes
            .iter()
            .map(|p| regex::escape(p))
            .collect();
        alternatives.push(format!("(?:{})/[a-z0-9][a-z0-9-]*", prefixes.join("|")));
    }
    if let Some(root) = &config.root_source {
        alternatives.push(regex::escape(root));
    }
    alternatives.push("external:[a-z0-9][a-z0-9-]*".into());
    re(&format!("`({})`", alternatives.join("|")))
}

fn tokens(pattern: &Regex, text: &str) -> Vec<String> {
    pattern
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .collect()
}

pub fn check(
    s: &Section<'_>,
    config: &MetaConfig,
    repo: &dyn MetaRepo,
    cargo: &CargoIndex,
) -> Outcome {
    let source_re = source_pattern(config);
    let mut findings = Vec::new();
    let mut warnings = Vec::new();
    let mut capabilities = Vec::new();
    let cap_at = s.at;
    let cap_end = section_end(s.prose, cap_at, 2);
    let cap_lines = &s.prose[cap_at + 1..cap_end];
    let cap_text = cap_lines.join("\n");

    if SELF_GRADE.is_match(&cap_text) {
        findings.push(Finding::new(
            "R10",
            cap_at + 1,
            "Capabilities contains a self-graded field",
        ));
    }
    if ISSUE.is_match(&cap_text) {
        findings.push(Finding::new(
            "R10",
            cap_at + 1,
            "Capabilities must not cite work-item numbers",
        ));
    }
    if ROOT_WI.is_match(&cap_text) {
        findings.push(Finding::new(
            "R10",
            cap_at + 1,
            "Capabilities must not contain Root WI fields",
        ));
    }
    for (offset, line) in cap_lines.iter().enumerate() {
        if line.starts_with("#### ") {
            findings.push(Finding::new(
                "R12",
                cap_at + offset + 2,
                "capabilities are flat H3 entries; H4 capability hierarchy is not allowed",
            ));
        }
    }

    let h3: Vec<(usize, String)> = (cap_at + 1..cap_end)
        .filter(|&i| s.prose[i].starts_with("### "))
        .map(|i| (i, s.prose[i][4..].trim().to_string()))
        .collect();
    if h3
        .first()
        .is_none_or(|(_, name)| name != "Capability index")
    {
        findings.push(Finding::new(
            "R5",
            cap_at + 1,
            "Capabilities must start with '### Capability index'",
        ));
        return Outcome {
            findings,
            warnings,
            capabilities,
        };
    }

    let index_at = h3[0].0;
    let table_end = h3.get(1).map_or(cap_end, |(i, _)| *i);
    let mut rows: Vec<(usize, Vec<String>)> = Vec::new();
    for (offset, line) in s.prose[index_at + 1..table_end].iter().enumerate() {
        if line.trim_start().starts_with('|') {
            rows.push((index_at + 1 + offset, parse_table_row(line)));
        } else if !rows.is_empty() && !line.trim().is_empty() {
            break;
        }
    }
    if rows.len() < 3 || rows[0].1 != INDEX_HEADER || !is_separator_row(&rows[1].1) {
        findings.push(Finding::new(
            "R5",
            index_at + 1,
            "Capability index must use columns: Capability | ID | User promise | Sources",
        ));
        rows.clear();
    }

    let mut seen_ids: BTreeSet<String> = BTreeSet::new();
    let mut seen_names: BTreeSet<String> = BTreeSet::new();
    let mut indexed: Vec<Capability> = Vec::new();
    for (row_at, cells) in rows.iter().skip(2) {
        let line = row_at + 1;
        if cells.len() != 4 {
            findings.push(Finding::new(
                "R5",
                line,
                "Capability index row must have four cells",
            ));
            continue;
        }
        let (name, raw_id, promise, raw_sources) = (&cells[0], &cells[1], &cells[2], &cells[3]);
        let matched = BACKTICKED.captures(raw_id);
        let id = matched
            .as_ref()
            .map_or("", |c| c.get(1).map_or("", |m| m.as_str()));
        let sources = tokens(&source_re, raw_sources);
        if name.is_empty() || promise.is_empty() {
            findings.push(Finding::new(
                "R5",
                line,
                "Capability name and promise cannot be empty",
            ));
        }
        if matched.is_none() || !ID.is_match(id) {
            findings.push(Finding::new(
                "R5",
                line,
                format!("invalid capability ID: {raw_id}"),
            ));
        }
        if seen_names.contains(name) || seen_ids.contains(id) {
            findings.push(Finding::new(
                "R5",
                line,
                "Capability names and IDs must be unique",
            ));
        }
        if sources.is_empty() {
            findings.push(Finding::new(
                "R7",
                line,
                "Capability index row has no source",
            ));
        }
        if sources.iter().collect::<BTreeSet<_>>().len() != sources.len() {
            findings.push(Finding::new(
                "R7",
                line,
                "Capability index row repeats a source",
            ));
        }
        seen_names.insert(name.clone());
        seen_ids.insert(id.to_string());
        indexed.push(Capability {
            name: name.clone(),
            id: id.to_string(),
            promise: promise.clone(),
            sources,
            gates: Vec::new(),
        });
    }

    let details = &h3[1..];
    let detail_names: Vec<&String> = details.iter().map(|(_, n)| n).collect();
    let index_names: Vec<&String> = indexed.iter().map(|c| &c.name).collect();
    if detail_names != index_names {
        findings.push(Finding::new(
            "R6",
            index_at + 1,
            "Capability detail headings must exactly match the index order",
        ));
    }

    let detail_by_name: HashMap<&String, usize> = details.iter().map(|(i, n)| (n, *i)).collect();
    let mut core_packages: Option<BTreeSet<String>> = None;
    for mut capability in indexed {
        let Some(&start) = detail_by_name.get(&capability.name) else {
            continue;
        };
        let end = details
            .iter()
            .map(|(i, _)| *i)
            .filter(|i| *i > start)
            .min()
            .unwrap_or(cap_end);
        let detail = s.prose[start + 1..end].join("\n");
        let ctx = Detail {
            start,
            end,
            prose: s.prose,
        };
        if DETAIL_ID
            .captures(&detail)
            .is_none_or(|c| c[1] != capability.id)
        {
            findings.push(Finding::new(
                "R6",
                start + 1,
                format!("detail ID must be `{}`", capability.id),
            ));
        }
        if !DETAIL_PROMISE.is_match(&detail) {
            findings.push(Finding::new(
                "R6",
                start + 1,
                "capability detail has no Promise",
            ));
        }
        let detail_sources =
            ctx.sources(&source_re, config, repo, &mut core_packages, &mut findings);
        if detail_sources != capability.sources {
            findings.push(Finding::new(
                "R7",
                start + 1,
                "detail Sources must exactly match the Capability index sources",
            ));
        }
        let mut gates = Vec::new();
        for index in start + 1..end {
            if !s.prose[index].starts_with("- Gate:") {
                continue;
            }
            let Some(m) = GATE.captures(&s.prose[index]) else {
                findings.push(Finding::new(
                    "R8",
                    index + 1,
                    "Gate must contain one backticked command",
                ));
                continue;
            };
            let command = m[1].to_string();
            findings.extend(gate::validate(
                repo,
                s.dir,
                &command,
                index + 1,
                cargo,
                &mut warnings,
            ));
            gates.push(command);
        }
        if gates.is_empty() {
            findings.push(Finding::new(
                "R8",
                start + 1,
                "capability detail has no executable Gate",
            ));
        }
        capability.gates = gates;
        capabilities.push(capability);
    }
    Outcome {
        findings,
        warnings,
        capabilities,
    }
}

/// One capability detail entry.
struct Detail<'a> {
    start: usize,
    end: usize,
    prose: &'a [String],
}

impl Detail<'_> {
    /// R7: the `- Sources:` list of the detail. Returns the sources in
    /// order.
    fn sources(
        &self,
        source_re: &Regex,
        config: &MetaConfig,
        repo: &dyn MetaRepo,
        core_packages: &mut Option<BTreeSet<String>>,
        findings: &mut Vec<Finding>,
    ) -> Vec<String> {
        let prose = self.prose;
        let marker = (self.start + 1..self.end).find(|&i| prose[i].trim() == "- Sources:");
        let Some(marker) = marker else {
            findings.push(Finding::new(
                "R7",
                self.start + 1,
                "capability detail has no Sources list",
            ));
            return Vec::new();
        };
        let source_end = (marker + 1..self.end)
            .find(|&i| FIELD_LINE.is_match(&prose[i]))
            .unwrap_or(self.end);
        let mut found = Vec::new();
        for (block_at, block) in source_blocks(prose, marker + 1, source_end) {
            let line = block_at + 1;
            let tokens = tokens(source_re, &block);
            if tokens.len() != 1 {
                findings.push(Finding::new(
                    "R7",
                    line,
                    format!(
                        "each source bullet must name exactly one {} source",
                        source_kinds(config)
                    ),
                ));
                continue;
            }
            let source = tokens[0].clone();
            let contribution = LINK_TEXT
                .replace_all(&block, "")
                .replace(&format!("`{source}`"), "");
            let contribution = LEAD_DASH.replace(&contribution, "");
            let contribution = contribution.trim().trim_matches([' ', '.']);
            if contribution.split_whitespace().count() < 3 {
                findings.push(Finding::new(
                    "R7",
                    line,
                    format!("source '{source}' must state its direct contribution"),
                ));
            }
            let (prefix, name) = source.split_once('/').unwrap_or(("", ""));
            if prefix == CORE_PREFIX {
                let packages =
                    core_packages.get_or_insert_with(|| repo.core_crates(&config.core_source));
                if !packages.contains(name) {
                    findings.push(Finding::new(
                        "R7",
                        line,
                        format!(
                            "source crate is not a faberline/core package in Cargo.lock: {source}"
                        ),
                    ));
                }
            } else if config.source_prefixes.iter().any(|p| p == prefix)
                && !repo.exists_from("", &source)
            {
                findings.push(Finding::new(
                    "R7",
                    line,
                    format!("source path does not exist: {source}"),
                ));
            }
            found.push(source);
        }
        found
    }
}

/// The source kinds, for a message: `apps/, libs/, core/, or external:`.
fn source_kinds(config: &MetaConfig) -> String {
    let mut kinds: Vec<String> = config
        .source_prefixes
        .iter()
        .map(|p| format!("{p}/"))
        .collect();
    kinds.push("external:".into());
    match kinds.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{}, or {last}", rest.join(", ")),
        _ => kinds.join(""),
    }
}
