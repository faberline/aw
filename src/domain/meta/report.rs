//! The result of a meta check.

use std::fmt::Write;

use serde::Serialize;

/// One broken rule.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Finding {
    pub rule: &'static str,
    pub line: usize,
    pub message: String,
}

impl Finding {
    pub fn new(rule: &'static str, line: usize, message: impl Into<String>) -> Self {
        Finding {
            rule,
            line,
            message: message.into(),
        }
    }
}

/// One capability of the README.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Capability {
    pub name: String,
    pub id: String,
    pub promise: String,
    pub sources: Vec<String>,
    pub gates: Vec<String>,
}

/// The result of one README.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ReadmeReport {
    pub path: String,
    pub sha256: String,
    pub ok: bool,
    pub product_sections: Vec<String>,
    pub capabilities: Vec<Capability>,
    pub relative_links: usize,
    pub gate_count: usize,
    pub warnings: Vec<String>,
    pub findings: Vec<Finding>,
}

/// The JSON document that `aw meta check --format json` prints.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct MetaReport {
    pub schema: &'static str,
    pub ok: bool,
    pub reports: Vec<ReadmeReport>,
}

pub const SCHEMA: &str = "meta-report/1";

pub fn report(reports: Vec<ReadmeReport>) -> MetaReport {
    MetaReport {
        schema: SCHEMA,
        ok: reports.iter().all(|r| r.ok),
        reports,
    }
}

/// The text form: one block per README, as the defer script printed it.
pub fn render(report: &MetaReport) -> String {
    let mut out = String::new();
    for r in &report.reports {
        let state = if r.ok { "PASS" } else { "FAIL" };
        let _ = writeln!(out, "{state} {} sha256={}", r.path, r.sha256);
        let _ = writeln!(
            out,
            "  product_sections={} capabilities={} relative_links={} gates={}",
            r.product_sections.len(),
            r.capabilities.len(),
            r.relative_links,
            r.gate_count
        );
        for w in &r.warnings {
            let _ = writeln!(out, "  WARN {w}");
        }
        for f in &r.findings {
            let _ = writeln!(out, "  {} line {}: {}", f.rule, f.line, f.message);
        }
    }
    out.trim_end().to_string()
}
