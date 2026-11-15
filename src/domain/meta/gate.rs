//! A gate is a backticked command that proves a capability. The check
//! reads what it can decide from the checkout and keeps the rest.

use super::ports::{CargoIndex, MetaRepo};
use super::report::Finding;

const RULE: &str = "R8";

/// `cargo test` flags that take a value.
const VALUE_FLAGS: [&str; 15] = [
    "-p",
    "--package",
    "--exclude",
    "--features",
    "-F",
    "--test",
    "--bin",
    "--example",
    "--bench",
    "--manifest-path",
    "--target",
    "--profile",
    "--target-dir",
    "-j",
    "--jobs",
];

fn cargo_gate(
    command: &str,
    line: usize,
    index: &CargoIndex,
    fallback_package: Option<&str>,
) -> Vec<Finding> {
    let Some(tokens) = shlex::split(command) else {
        return vec![Finding::new(
            RULE,
            line,
            "gate command cannot be parsed: No closing quotation",
        )];
    };
    if tokens.len() < 2 || tokens[0] != "cargo" || tokens[1] != "test" {
        return Vec::new();
    }
    let mut packages: Vec<String> = Vec::new();
    let mut targets: Vec<String> = Vec::new();
    let mut filters: Vec<String> = Vec::new();
    let mut pending = "";
    for token in &tokens[2..] {
        if token == "--" {
            break;
        }
        if !pending.is_empty() {
            match pending {
                "-p" | "--package" => packages.push(token.clone()),
                "--test" => targets.push(token.clone()),
                _ => {}
            }
            pending = "";
            continue;
        }
        if let Some(flag) = VALUE_FLAGS.iter().find(|f| **f == token) {
            pending = flag;
            continue;
        }
        if token.starts_with('-') {
            continue;
        }
        filters.push(token.clone());
    }
    let mut findings = Vec::new();
    if !pending.is_empty() {
        findings.push(Finding::new(
            RULE,
            line,
            format!("gate flag '{pending}' has no value"),
        ));
    }
    for name in filters {
        findings.push(Finding::new(
            RULE,
            line,
            format!("'{name}' is a bare test-name filter; use --test <target> or --lib"),
        ));
    }
    let effective: Vec<String> = if packages.is_empty() {
        fallback_package.map(String::from).into_iter().collect()
    } else {
        packages
    };
    for package in &effective {
        if !index.contains_key(package) {
            findings.push(Finding::new(
                RULE,
                line,
                format!("cargo gate names no package '{package}'"),
            ));
        }
    }
    let known: Vec<&String> = effective
        .iter()
        .filter(|p| index.contains_key(*p))
        .collect();
    for target in &targets {
        if !known.is_empty() && !known.iter().any(|p| index[*p].contains(target)) {
            let names: Vec<&str> = known.iter().map(|p| p.as_str()).collect();
            findings.push(Finding::new(
                RULE,
                line,
                format!(
                    "cargo gate names no --test target '{target}' in {}",
                    names.join(", ")
                ),
            ));
        }
    }
    findings
}

/// Check one gate command. A command shape that the check cannot resolve is
/// a warning, not a finding.
pub fn validate(
    repo: &dyn MetaRepo,
    dir: &str,
    command: &str,
    line: usize,
    index: &CargoIndex,
    warnings: &mut Vec<String>,
) -> Vec<Finding> {
    let local = repo.local_package(dir);
    let mut findings = cargo_gate(command, line, index, local.as_deref());
    let Some(tokens) = shlex::split(command) else {
        return findings;
    };
    if tokens.is_empty() {
        findings.push(Finding::new(RULE, line, "gate command is empty"));
        return findings;
    }
    if tokens.len() >= 2 && tokens[0] == "cargo" && tokens[1] == "test" {
        return findings;
    }
    let candidate = if (tokens[0] == "bash" || tokens[0] == "sh") && tokens.len() > 1 {
        Some(&tokens[1])
    } else if tokens[0].contains('/') || tokens[0].ends_with(".sh") {
        Some(&tokens[0])
    } else {
        None
    };
    if let Some(candidate) = candidate {
        if !repo.exists_from("", candidate) {
            findings.push(Finding::new(
                RULE,
                line,
                format!("gate script or executable does not exist: {candidate}"),
            ));
        }
        return findings;
    }
    warnings.push(format!(
        "line {line}: gate command shape was retained but not mechanically resolved: {command}"
    ));
    findings
}
