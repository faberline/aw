//! The security rules as functions over the tracked files.

use std::fmt::Write;

use regex::Regex;
use serde::Serialize;

use super::ports::{Config, SecurityFiles};
use crate::domain::work::ports::PortResult;

pub const SCHEMA: &str = "security-report/1";

/// Every rule id, in report order.
pub const RULES: [&str; 6] = ["SEC1", "SEC2", "SEC3", "SEC4", "SEC5", "SEC6"];

/// What a finding means. A `Warn` finding becomes a failure with `--strict`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Warn,
    /// An exception in `aw.toml` covers it.
    Excepted,
    /// No `aw` check exists. A person looks at it.
    Manual,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Finding {
    pub rule: &'static str,
    pub path: String,
    pub status: Status,
    pub detail: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    pub schema: &'static str,
    /// `--strict` was given: a warning fails the check.
    pub strict: bool,
    /// False only when `strict` is set and a warning exists.
    pub ok: bool,
    pub warnings: usize,
    pub excepted: usize,
    pub findings: Vec<Finding>,
}

const MANUAL: [(&str, &str); 2] = [
    (
        "SEC4",
        "third-party components in the request path (ingress, proxy, sidecar) are listed in the threat model with version and update owner, or the model says none",
    ),
    (
        "SEC6",
        "shipped manifests: default-deny NetworkPolicy, no host mounts, read-only root filesystem, dropped capabilities",
    ),
];

fn is_workflow(path: &str) -> bool {
    let yaml = path.ends_with(".yml") || path.ends_with(".yaml");
    let flow = path
        .strip_prefix(".github/workflows/")
        .is_some_and(|rest| !rest.contains('/'));
    yaml && flow
}

fn is_action(path: &str) -> bool {
    path.starts_with(".github/actions/")
        && (path.ends_with("/action.yml") || path.ends_with("/action.yaml"))
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn is_dockerfile(path: &str) -> bool {
    let name = file_name(path).to_lowercase();
    name.starts_with("dockerfile") || name.ends_with(".dockerfile")
}

fn is_manifest(path: &str) -> bool {
    (path.ends_with(".yml") || path.ends_with(".yaml")) && !is_workflow(path) && !is_action(path)
}

/// The text of `raw` without `#` comments. A `#` starts a comment at the line
/// start or after a space.
fn strip_comments(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for line in raw.lines() {
        let bytes = line.as_bytes();
        let cut = line
            .match_indices('#')
            .find(|(i, _)| *i == 0 || bytes[i - 1] == b' ' || bytes[i - 1] == b'\t')
            .map_or(line.len(), |(i, _)| i);
        out.push_str(line[..cut].trim_end());
        out.push('\n');
    }
    out
}

struct Scan<'a> {
    files: &'a dyn SecurityFiles,
    config: &'a Config,
    today: String,
    found: Vec<Finding>,
}

impl Scan<'_> {
    fn add(&mut self, rule: &'static str, path: &str, detail: String) {
        let excepted = self
            .config
            .exceptions
            .iter()
            .any(|e| e.covers(rule, path, &self.today));
        let finding = Finding {
            rule,
            path: path.to_string(),
            status: if excepted {
                Status::Excepted
            } else {
                Status::Warn
            },
            detail,
        };
        if !self.found.contains(&finding) {
            self.found.push(finding);
        }
    }

    fn text(&self, path: &str) -> PortResult<String> {
        Ok(self
            .files
            .text(path)?
            .map(|t| strip_comments(&t))
            .unwrap_or_default())
    }
}

/// Check the files against the rules and the exceptions in `aw.toml`.
pub fn check(files: &dyn SecurityFiles, strict: bool) -> PortResult<Report> {
    let config = files.config()?;
    let today = files.today();
    let paths = files.paths()?;
    let mut scan = Scan {
        files,
        config: &config,
        today: today.clone(),
        found: Vec::new(),
    };
    for e in &config.exceptions {
        if e.expires.as_str() < today.as_str() {
            scan.found.push(Finding {
                rule: RULES
                    .iter()
                    .find(|r| **r == e.rule)
                    .copied()
                    .unwrap_or("SEC1"),
                path: "aw.toml".to_string(),
                status: Status::Warn,
                detail: format!(
                    "the exception for {} ended on {}; remove it or renew it with a reason",
                    e.rule, e.expires
                ),
            });
        }
    }
    if paths.iter().any(|p| file_name(p) == "Cargo.toml") {
        advisories(&mut scan, &paths)?;
    }
    images(&mut scan, &paths)?;
    actions(&mut scan, &paths)?;
    let mut findings = scan.found;
    for (rule, detail) in MANUAL {
        findings.push(Finding {
            rule,
            path: "-".to_string(),
            status: Status::Manual,
            detail: detail.to_string(),
        });
    }
    findings.sort();
    let warnings = findings.iter().filter(|f| f.status == Status::Warn).count();
    let excepted = findings
        .iter()
        .filter(|f| f.status == Status::Excepted)
        .count();
    Ok(Report {
        schema: SCHEMA,
        strict,
        ok: !strict || warnings == 0,
        warnings,
        excepted,
        findings,
    })
}

fn advisory_step(text: &str) -> bool {
    let lower = text.to_lowercase();
    if lower.contains("rustsec/audit-check") || lower.contains("cargo audit") {
        return true;
    }
    lower.lines().any(|line| {
        let deny = line.contains("cargo deny") || line.contains("cargo-deny");
        let check = line.contains(" check") || line.contains("cargo-deny-action");
        let other = ["bans", "licenses", "sources"]
            .iter()
            .any(|w| line.contains(w));
        deny && check && (line.contains("advisories") || !other)
    })
}

fn reusable_advisories(text: &str) -> bool {
    text.contains("faberline/aw/.github/workflows/advisories.yml")
}

fn advisories(scan: &mut Scan<'_>, paths: &[String]) -> PortResult<()> {
    let mut ci_text = String::new();
    let mut scheduled = false;
    for path in paths
        .iter()
        .filter(|p| is_workflow(p) || is_action(p) || p.ends_with(".sh"))
    {
        let text = scan.text(path)?;
        if is_workflow(path)
            && text
                .lines()
                .any(|l| l.trim_start().starts_with("schedule:"))
        {
            scheduled |= advisory_step(&text) || reusable_advisories(&text);
        }
        ci_text.push_str(&text);
    }
    if !advisory_step(&ci_text) {
        scan.add(
            "SEC1",
            ".github/workflows",
            "no CI step runs `cargo deny check advisories`".to_string(),
        );
    }
    if !scheduled {
        scan.add(
            "SEC2",
            ".github/workflows",
            "no scheduled workflow checks advisories on main; call faberline/aw/.github/workflows/advisories.yml on a daily schedule".to_string(),
        );
    }
    let denies: Vec<&String> = paths
        .iter()
        .filter(|p| file_name(p) == "deny.toml")
        .collect();
    if denies.is_empty() {
        scan.add("SEC1", "deny.toml", "no deny.toml".to_string());
    }
    for path in denies {
        ignores(scan, path)?;
    }
    Ok(())
}

/// Each `[advisories] ignore` entry names the advisory, says why, and says
/// when it ends (`expires YYYY-MM-DD` in the reason).
fn ignores(scan: &mut Scan<'_>, path: &str) -> PortResult<()> {
    let Some(raw) = scan.files.text(path)? else {
        return Ok(());
    };
    let Ok(table) = raw.parse::<toml::Table>() else {
        scan.add("SEC1", path, "deny.toml does not parse".to_string());
        return Ok(());
    };
    let Some(list) = table
        .get("advisories")
        .and_then(|a| a.get("ignore"))
        .and_then(toml::Value::as_array)
    else {
        return Ok(());
    };
    let expires = Regex::new(r"(?i)expires?\s*:?\s*(\d{4}-\d{2}-\d{2})").expect("valid pattern");
    for item in list {
        let (id, reason) = match item {
            toml::Value::String(s) => (s.clone(), String::new()),
            toml::Value::Table(t) => (
                t.get("id")
                    .or_else(|| t.get("crate"))
                    .and_then(toml::Value::as_str)
                    .unwrap_or("?")
                    .to_string(),
                t.get("reason")
                    .and_then(toml::Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            ),
            _ => continue,
        };
        if reason.trim().is_empty() {
            scan.add("SEC1", path, format!("ignore of {id} has no reason"));
            continue;
        }
        match expires.captures(&reason) {
            None => scan.add(
                "SEC1",
                path,
                format!("ignore of {id} has no end date; write `expires YYYY-MM-DD` in the reason"),
            ),
            Some(c) if c[1] < *scan.today => {
                scan.add("SEC1", path, format!("ignore of {id} ended on {}", &c[1]));
            }
            Some(_) => {}
        }
    }
    Ok(())
}

fn digest_pinned(image: &str) -> bool {
    image.contains("@sha256:")
}

/// An image the repo builds in its own run: a name that starts with the
/// repo name, with no registry, a faberline GHCR path or `localhost`, or a
/// prefix that `built_images` lists.
fn self_built(image: &str, repo: &str, built: &[String]) -> bool {
    if built.iter().any(|b| image.starts_with(b.as_str())) {
        return true;
    }
    let name = image.split('@').next().unwrap_or(image);
    let name = match name.rfind(':') {
        Some(i) if !name[i..].contains('/') => &name[..i],
        _ => name,
    };
    let local = !name.contains('/')
        || name.starts_with("ghcr.io/faberline/")
        || name.starts_with("localhost");
    let last = name.rsplit('/').next().unwrap_or(name).to_lowercase();
    local && !repo.is_empty() && last.starts_with(&repo.to_lowercase())
}

/// A word an image reference is made of. `{`, `[` and the like are YAML
/// schema text, not an image.
fn plausible(image: &str) -> bool {
    image.starts_with(|c: char| c.is_ascii_alphanumeric())
        && image
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._/:@-".contains(c))
}

/// `REPLACE_ME__IMAGE` or `TESTER_IMAGE`: a name for a value that a script
/// writes later.
fn placeholder(image: &str) -> bool {
    !image
        .chars()
        .any(|c| c.is_ascii_lowercase() || "/:.@".contains(c))
}

fn images(scan: &mut Scan<'_>, paths: &[String]) -> PortResult<()> {
    let repo = scan.files.repo_name();
    let built = scan.config.built_images.clone();
    let from = Regex::new(r"(?i)^\s*from\s+(?:--\S+\s+)*(\S+)(?:\s+as\s+(\S+))?").expect("valid");
    let image_line = Regex::new(r#"^\s*(?:-\s*)?image:\s*['"]?([^\s'"]+)"#).expect("valid");
    for path in paths {
        let dockerfile = is_dockerfile(path);
        if !dockerfile && !is_manifest(path) {
            continue;
        }
        let text = scan.text(path)?;
        let mut stages: Vec<String> = Vec::new();
        for line in text.lines() {
            let (image, alias) = if dockerfile {
                match from.captures(line) {
                    Some(c) => (
                        c[1].to_string(),
                        c.get(2).map(|m| m.as_str().to_lowercase()),
                    ),
                    None => continue,
                }
            } else {
                match image_line.captures(line) {
                    Some(c) => (c[1].to_string(), None),
                    None => continue,
                }
            };
            let skip = image.eq_ignore_ascii_case("scratch")
                || !plausible(&image)
                || placeholder(&image)
                || stages.contains(&image.to_lowercase())
                || ["$", "{{", "<"].iter().any(|m| image.contains(m))
                || digest_pinned(&image)
                || self_built(&image, &repo, &built);
            if let Some(a) = alias {
                stages.push(a);
            }
            if !skip {
                scan.add(
                    "SEC3",
                    path,
                    format!("image `{image}` is not pinned by digest (`@sha256:...`)"),
                );
            }
        }
    }
    Ok(())
}

fn is_sha(reference: &str) -> bool {
    reference.len() == 40 && reference.bytes().all(|b| b.is_ascii_hexdigit())
}

fn actions(scan: &mut Scan<'_>, paths: &[String]) -> PortResult<()> {
    let uses = Regex::new(r#"^\s*(?:-\s*)?uses:\s*['"]?([^\s'"]+)"#).expect("valid");
    let target = Regex::new(r"\bpull_request_target\b").expect("valid");
    for path in paths.iter().filter(|p| is_workflow(p) || is_action(p)) {
        let text = scan.text(path)?;
        for line in text.lines() {
            let Some(c) = uses.captures(line) else {
                continue;
            };
            let spec = &c[1];
            if spec.starts_with("./") {
                continue;
            }
            if let Some(image) = spec.strip_prefix("docker://") {
                if !digest_pinned(image) {
                    scan.add("SEC5", path, format!("`{spec}` is not pinned by digest"));
                }
                continue;
            }
            let (name, reference) = spec.split_once('@').unwrap_or((spec, ""));
            if name.starts_with("faberline/") || is_sha(reference) {
                continue;
            }
            scan.add(
                "SEC5",
                path,
                format!("`{spec}` is third-party and not pinned by a 40-character commit SHA"),
            );
        }
        if is_workflow(path) {
            if !text.lines().any(|l| l.starts_with("permissions:")) {
                scan.add(
                    "SEC5",
                    path,
                    "no top-level `permissions:`; write `permissions: {}` or `contents: read`"
                        .to_string(),
                );
            }
            if target.is_match(&text) {
                scan.add(
                    "SEC5",
                    path,
                    "uses `pull_request_target`; use `pull_request`".to_string(),
                );
            }
        }
    }
    Ok(())
}

/// One line for each finding, then a summary line.
pub fn render(report: &Report) -> String {
    let mut out = String::new();
    for f in &report.findings {
        let tag = match f.status {
            Status::Warn => "warn",
            Status::Excepted => "excepted",
            Status::Manual => "manual",
        };
        let _ = writeln!(out, "{} {tag} {}: {}", f.rule, f.path, f.detail);
    }
    let manual = report
        .findings
        .iter()
        .filter(|f| f.status == Status::Manual)
        .count();
    let _ = write!(
        out,
        "{}: {} warnings, {} excepted, {} manual{}",
        if report.ok { "pass" } else { "fail" },
        report.warnings,
        report.excepted,
        manual,
        if report.strict {
            " (strict)"
        } else {
            " (warn only; --strict fails on a warning)"
        }
    );
    out
}
