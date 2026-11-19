//! One project document set: README, STATUS and the rules that tie them.

use std::fmt::Write;

use serde::Serialize;

use super::config::MetaConfig;
use super::ports::{CargoIndex, DocInput, MetaRepo, ReadmeInput};
use super::readme;
use super::report::{Finding, ReadmeReport};
use super::roadmap::{self, RoadmapReport};
use super::status::{self, StatusReport};

pub const SCHEMA: &str = "metadoc-report/1";

/// The core documents of a project, in the order they are read.
pub const DOC_NAMES: [&str; 3] = ["README.md", "STATUS.md", "ROADMAP.md"];

/// The result of one project folder.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ProjectReport {
    pub path: String,
    pub ok: bool,
    pub readme: Option<ReadmeReport>,
    pub status: Option<StatusReport>,
    pub roadmap: Option<RoadmapReport>,
    pub findings: Vec<Finding>,
}

/// The JSON document that `aw metadoc check --format json` prints.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct MetadocReport {
    pub schema: &'static str,
    pub ok: bool,
    pub reports: Vec<ProjectReport>,
}

pub fn report(reports: Vec<ProjectReport>) -> MetadocReport {
    MetadocReport {
        schema: SCHEMA,
        ok: reports.iter().all(|r| r.ok),
        reports,
    }
}

fn join(dir: &str, name: &str) -> String {
    if dir.is_empty() {
        name.to_string()
    } else {
        format!("{dir}/{name}")
    }
}

/// Check the documents of the project folder `dir`.
pub fn check(
    repo: &dyn MetaRepo,
    config: &MetaConfig,
    cargo: &CargoIndex,
    dir: &str,
) -> Result<ProjectReport, crate::domain::work::ports::PortError> {
    let mut findings: Vec<Finding> = Vec::new();
    let mut docs: Vec<Option<DocInput>> = Vec::new();
    for name in DOC_NAMES {
        let doc = repo.document(&join(dir, name))?;
        if doc.is_none() {
            findings.push(Finding::new(
                "P1",
                1,
                format!("project document does not exist: {name}"),
            ));
        }
        docs.push(doc);
    }
    let readme_report = docs[0].as_ref().map(|doc| {
        let input = ReadmeInput {
            path: doc.path.clone(),
            dir: doc.dir.clone(),
            text: doc.text.clone(),
            sha256: doc.sha256.clone(),
        };
        readme::check(&input, config, repo, cargo)
    });
    let status_report = docs[1].as_ref().map(|doc| status::check(doc, repo, cargo));
    let roadmap_report = docs[2].as_ref().map(|doc| roadmap::check(doc, repo));

    let roadmap_ids: Vec<&str> = roadmap_report
        .iter()
        .flat_map(|r| r.items.iter().map(|i| i.id.as_str()))
        .collect();
    for surface in status_report.iter().flat_map(|s| &s.surfaces) {
        if let Some(id) = &surface.roadmap_id
            && !roadmap_ids.contains(&id.as_str())
        {
            findings.push(Finding::new(
                "P3",
                1,
                format!(
                    "STATUS surface '{}' links unknown ROADMAP ID '{id}'",
                    surface.id
                ),
            ));
        }
        if roadmap_ids.contains(&surface.id.as_str()) && surface.state == "Supported" {
            findings.push(Finding::new(
                "P4",
                1,
                format!(
                    "current surface '{}' also appears as future work",
                    surface.id
                ),
            ));
        }
    }

    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    let ok = findings.is_empty()
        && readme_report.as_ref().is_some_and(|r| r.ok)
        && status_report.as_ref().is_some_and(|s| s.ok)
        && roadmap_report.as_ref().is_some_and(|r| r.ok);
    Ok(ProjectReport {
        path: if dir.is_empty() {
            ".".into()
        } else {
            dir.into()
        },
        ok,
        readme: readme_report,
        status: status_report,
        roadmap: roadmap_report,
        findings,
    })
}

/// The text form, as the defer script printed it.
pub fn render(report: &MetadocReport) -> String {
    let mut out = String::new();
    for r in &report.reports {
        let state = if r.ok { "PASS" } else { "FAIL" };
        let _ = writeln!(out, "{state} {}", r.path);
        if let Some(readme) = &r.readme {
            let _ = writeln!(
                out,
                "  README sha256={} capabilities={}",
                readme.sha256,
                readme.capabilities.len()
            );
            for f in &readme.findings {
                let _ = writeln!(out, "  README {} line {}: {}", f.rule, f.line, f.message);
            }
        }
        if let Some(status) = &r.status {
            let _ = writeln!(
                out,
                "  STATUS sha256={} surfaces={}",
                status.sha256,
                status.surfaces.len()
            );
            for w in &status.warnings {
                let _ = writeln!(out, "  STATUS WARN {w}");
            }
            for f in &status.findings {
                let _ = writeln!(out, "  STATUS {} line {}: {}", f.rule, f.line, f.message);
            }
        }
        if let Some(roadmap) = &r.roadmap {
            let _ = writeln!(
                out,
                "  ROADMAP sha256={} items={}",
                roadmap.sha256,
                roadmap.items.len()
            );
            for f in &roadmap.findings {
                let _ = writeln!(out, "  ROADMAP {} line {}: {}", f.rule, f.line, f.message);
            }
        }
        for f in &r.findings {
            let _ = writeln!(out, "  SET {} line {}: {}", f.rule, f.line, f.message);
        }
    }
    out.trim_end().to_string()
}
