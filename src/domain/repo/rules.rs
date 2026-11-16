//! The hygiene rules as functions over the tracked paths.

use std::collections::BTreeSet;
use std::fmt::Write;

use regex::Regex;
use serde::Serialize;

use super::ports::{Config, Entry, RepoFiles};
use crate::domain::work::ports::PortResult;

/// Every rule id, in report order.
pub const RULES: [&str; 5] = ["RH1", "RH2", "RH3", "RH4", "RH5"];

pub const SCHEMA: &str = "repo-report/1";

/// Root files that every repo may have.
const ROOT_FILES: [&str; 22] = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "LICENSE",
    "Cargo.toml",
    "Cargo.lock",
    "build.rs",
    "aw.toml",
    "rust-toolchain.toml",
    "rust-toolchain",
    "rustfmt.toml",
    ".rustfmt.toml",
    "clippy.toml",
    ".clippy.toml",
    "deny.toml",
    "taplo.toml",
    ".editorconfig",
    ".gitignore",
    ".gitattributes",
];

/// Root folders that every repo may have.
const ROOT_DIRS: [&str; 9] = [
    "crates", "src", "tests", "docs", "scripts", "ci", "k8s", ".github", ".cargo",
];

/// Files above this size are not scanned for RH3.
const SCAN_LIMIT: u64 = 2 * 1024 * 1024;

/// Placeholder user names that RH3 does not report.
const PLACEHOLDER_USERS: [&str; 7] = ["runner", "user", "username", "you", "me", "example", "name"];

/// Folder names that RH2 forbids.
const FORBIDDEN_DIRS: [&str; 5] = [".codex", ".agy", "tmp", "scratch", "old"];

/// One broken rule on one file.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Violation {
    pub rule: &'static str,
    pub path: String,
    pub detail: String,
}

impl Violation {
    /// The baseline entry that covers this violation.
    pub fn key(&self) -> String {
        format!("{}:{}", self.rule, self.path)
    }
}

/// The result of one check.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    pub schema: &'static str,
    pub ok: bool,
    /// Violations that the baseline does not cover.
    pub violations: Vec<Violation>,
    /// Baseline entries that no longer violate: remove them.
    pub stale: Vec<String>,
    /// How many violations the baseline covers.
    pub baselined: usize,
}

fn rh1(path: &str, config: &Config) -> Option<Violation> {
    let top = path.split('/').next().unwrap_or(path);
    let is_dir = path.contains('/');
    let builtin = if is_dir {
        ROOT_DIRS.contains(&top)
    } else {
        ROOT_FILES.contains(&top)
            || top.starts_with("LICENSE")
            || top.starts_with("Dockerfile")
            || top == ".dockerignore"
    };
    if builtin || config.root.iter().any(|a| a.covers(top)) {
        return None;
    }
    Some(Violation {
        rule: "RH1",
        // One violation for the root entry, not one for each file in it.
        path: top.to_string(),
        detail: format!(
            "root {} is not in the allow-list; add it with a reason to [[hygiene.root]]",
            if is_dir { "folder" } else { "file" }
        ),
    })
}

fn rh2(path: &str) -> Option<Violation> {
    let parts: Vec<&str> = path.split('/').collect();
    let (name, dirs) = parts.split_last()?;
    let forbidden_name = name.contains("HANDOFF")
        || name.contains("CLOSEOUT")
        || name.ends_with("_REPORT.md")
        || name.ends_with("_STATUS.md")
        || *name == "PLAN.md"
        || *name == "NOTES.md"
        || name.ends_with(".bak")
        || name.ends_with(".orig");
    let forbidden_dir = dirs.iter().find(|d| FORBIDDEN_DIRS.contains(d));
    let detail = match (forbidden_name, forbidden_dir) {
        (true, _) => format!("`{name}` is a session leftover or backup name"),
        (_, Some(dir)) => format!("`{dir}/` is a forbidden folder name"),
        _ => return None,
    };
    Some(Violation {
        rule: "RH2",
        path: path.to_string(),
        detail,
    })
}

fn rh3(path: &str, text: &str, re: &Regex) -> Option<Violation> {
    for (i, line) in text.lines().enumerate() {
        for cap in re.captures_iter(line) {
            if !PLACEHOLDER_USERS.contains(&&cap[2]) {
                return Some(Violation {
                    rule: "RH3",
                    path: path.to_string(),
                    detail: format!("line {}: absolute home path `{}`", i + 1, &cap[0]),
                });
            }
        }
    }
    None
}

fn rh4(path: &str, config: &Config, dated: &Regex) -> Option<Violation> {
    if config.evidence.iter().any(|a| a.covers(path)) {
        return None;
    }
    let hit = path.split('/').find_map(|part| dated.find(part))?;
    Some(Violation {
        rule: "RH4",
        path: path.to_string(),
        detail: format!(
            "`{}` is a dated or per-issue name; keep it in an evidence folder",
            hit.as_str()
        ),
    })
}

fn rh5(entry: &Entry, config: &Config) -> Option<Violation> {
    let name = entry.path.rsplit('/').next().unwrap_or(&entry.path);
    if entry.size <= config.max_file_bytes
        || name == "Cargo.lock"
        || config.size.iter().any(|a| a.covers(&entry.path))
    {
        return None;
    }
    Some(Violation {
        rule: "RH5",
        path: entry.path.clone(),
        detail: format!(
            "{} bytes is above the limit of {} bytes",
            entry.size, config.max_file_bytes
        ),
    })
}

/// All violations of the tracked files, before the baseline. One violation
/// for each rule and path, sorted.
pub fn scan(files: &dyn RepoFiles, config: &Config) -> PortResult<Vec<Violation>> {
    let home = Regex::new(r"/(Users|home)/([A-Za-z0-9._-]+)/").expect("valid pattern");
    let dated = Regex::new(r"(?i)-20\d\d-\d\d-\d\d|issue[-_]?\d+").expect("valid pattern");
    let mut found = BTreeSet::new();
    for entry in files.entries()? {
        let path = entry.path.as_str();
        found.extend(rh1(path, config));
        found.extend(rh2(path));
        found.extend(rh4(path, config, &dated));
        found.extend(rh5(&entry, config));
        if entry.size <= SCAN_LIMIT
            && !config.home_path.iter().any(|a| a.covers(path))
            && let Some(text) = files.text(path)?
        {
            found.extend(rh3(path, &text, &home));
        }
    }
    Ok(found.into_iter().collect())
}

/// Check the tracked files against the rules and the baseline.
pub fn check(files: &dyn RepoFiles, config: &Config) -> PortResult<Report> {
    let all = scan(files, config)?;
    let baseline: BTreeSet<&str> = config.baseline.iter().map(String::as_str).collect();
    let keys: BTreeSet<String> = all.iter().map(Violation::key).collect();
    let stale: Vec<String> = baseline
        .iter()
        .filter(|k| !keys.contains(**k))
        .map(|k| (*k).to_string())
        .collect();
    let baselined = all
        .iter()
        .filter(|v| baseline.contains(v.key().as_str()))
        .count();
    let violations: Vec<Violation> = all
        .into_iter()
        .filter(|v| !baseline.contains(v.key().as_str()))
        .collect();
    Ok(Report {
        schema: SCHEMA,
        ok: violations.is_empty() && stale.is_empty(),
        violations,
        stale,
        baselined,
    })
}

/// The `baseline = [...]` block that covers every current violation.
pub fn render_baseline(all: &[Violation]) -> String {
    let mut out = String::from("baseline = [\n");
    for v in all {
        let _ = writeln!(out, "    \"{}\",", v.key());
    }
    out.push_str("]\n");
    out
}

/// The text report.
pub fn render(report: &Report) -> String {
    let mut out = String::new();
    for v in &report.violations {
        let _ = writeln!(out, "{} {}: {}", v.rule, v.path, v.detail);
    }
    for key in &report.stale {
        let _ = writeln!(
            out,
            "baseline {key}: no longer violates; remove it from the baseline of [hygiene]"
        );
    }
    let _ = write!(
        out,
        "{}: {} new, {} stale, {} in the baseline",
        if report.ok { "pass" } else { "fail" },
        report.violations.len(),
        report.stale.len(),
        report.baselined
    );
    out
}
