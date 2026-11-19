//! One project document set: README, STATUS and the rules that tie them.

use std::collections::BTreeSet;
use std::fmt::Write;

use serde::Serialize;

use super::config::MetaConfig;
use super::doc::resolve;
use super::guides::{self, GUIDES, GuideReport, GuideSpec, MIGRATION};
use super::lumen;
use super::markdown::{has_scheme, headings, markdown_links, section_end, without_fenced_content};
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
    pub supporting_docs: Vec<GuideReport>,
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

/// The files that the README links from its Supporting documents section.
fn supporting_targets(readme: &DocInput) -> BTreeSet<String> {
    let lines: Vec<&str> = readme.text.lines().collect();
    let prose = without_fenced_content(&lines);
    let found: Vec<usize> = headings(&prose, 2)
        .into_iter()
        .filter(|(_, name)| name == "Supporting documents")
        .map(|(at, _)| at)
        .collect();
    let [start] = found[..] else {
        return BTreeSet::new();
    };
    let end = section_end(&prose, start, 2);
    markdown_links(&lines[start + 1..end])
        .into_iter()
        .map(|(_, target)| target)
        .filter(|t| !t.is_empty() && !t.starts_with(['#', '/']) && !has_scheme(t))
        .filter_map(|t| {
            let path = t.split('#').next().unwrap_or("");
            resolve(&readme.dir, path.split('?').next().unwrap_or(""))
        })
        .collect()
}

/// The supporting guides, the links to them (P2, P6) and the Lumen claims
/// (P7). `texts` holds each adopted document as (file name, text).
fn supporting(
    repo: &dyn MetaRepo,
    dir: &str,
    readme: Option<&DocInput>,
    findings: &mut Vec<Finding>,
    texts: &mut Vec<(String, String)>,
) -> Result<Vec<GuideReport>, crate::domain::work::ports::PortError> {
    let targets = readme.map(supporting_targets).unwrap_or_default();
    if readme.is_some() {
        for required in ["STATUS.md", "ROADMAP.md"] {
            if !targets.contains(&join(dir, required)) {
                findings.push(Finding::new(
                    "P2",
                    1,
                    format!("README must link {required} from Supporting documents"),
                ));
            }
        }
    }
    let mut reports = Vec::new();
    let mut run = |input: &DocInput, spec: &GuideSpec, reports: &mut Vec<GuideReport>| {
        texts.push((file_name(&input.path), input.text.clone()));
        reports.push(guides::check(input, repo, spec));
    };
    let mut fixed: BTreeSet<String> = BTreeSet::new();
    for spec in &GUIDES {
        let relative = spec.path.unwrap_or_default();
        let path = join(dir, relative);
        fixed.insert(path.clone());
        let linked = targets.contains(&path);
        if let Some(doc) = repo.document(&path)? {
            if !linked {
                findings.push(Finding::new(
                    "P6",
                    1,
                    format!("README must link {relative} from Supporting documents"),
                ));
            }
            run(&doc, spec, &mut reports);
        } else if linked {
            findings.push(Finding::new(
                "P6",
                1,
                format!("linked supporting document does not exist: {relative}"),
            ));
        }
    }
    let prefix = if dir.is_empty() {
        String::new()
    } else {
        format!("{dir}/")
    };
    for target in &targets {
        if fixed.contains(target) {
            continue;
        }
        let Some(relative) = target.strip_prefix(&prefix) else {
            continue;
        };
        let Some(name) = relative.strip_prefix("docs/") else {
            continue;
        };
        if name.contains('/') || !guides::is_migration_guide(name) {
            continue;
        }
        match repo.document(target)? {
            Some(doc) => run(&doc, &MIGRATION, &mut reports),
            None => findings.push(Finding::new(
                "P6",
                1,
                format!("linked supporting document does not exist: {relative}"),
            )),
        }
    }
    Ok(reports)
}

fn file_name(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).to_string()
}

/// P5: STATUS and ROADMAP titles follow the README title.
fn check_titles(docs: &[Option<DocInput>], findings: &mut Vec<Finding>) {
    if docs.iter().any(Option::is_none) {
        return;
    }
    let titles: Vec<Vec<(usize, String)>> = docs
        .iter()
        .flatten()
        .map(|d| {
            let lines: Vec<&str> = d.text.lines().collect();
            headings(&without_fenced_content(&lines), 1)
        })
        .collect();
    if titles[0].len() != 1 {
        return;
    }
    let product = &titles[0][0].1;
    for (n, word) in [(1, "status"), (2, "roadmap")] {
        let expected = format!("{product} {word}");
        if titles[n].len() == 1 && titles[n][0].1 != expected {
            findings.push(Finding::new(
                "P5",
                1,
                format!("{} H1 must be '# {expected}'", DOC_NAMES[n]),
            ));
        }
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

    let mut texts: Vec<(String, String)> = docs
        .iter()
        .flatten()
        .map(|d| (file_name(&d.path), d.text.clone()))
        .collect();
    let supporting_docs = supporting(repo, dir, docs[0].as_ref(), &mut findings, &mut texts)?;
    if lumen::is_lumen(dir) {
        for (name, text) in &texts {
            findings.extend(lumen::check(name, text));
        }
    }
    check_titles(&docs, &mut findings);

    findings.sort_by(|a, b| (a.line, a.rule, &a.message).cmp(&(b.line, b.rule, &b.message)));
    let ok = findings.is_empty()
        && supporting_docs.iter().all(|g| g.ok)
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
        supporting_docs,
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
        for g in &r.supporting_docs {
            let _ = writeln!(
                out,
                "  SUPPORTING kind={} path={} sha256={}",
                g.kind, g.path, g.sha256
            );
            for f in &g.findings {
                let _ = writeln!(
                    out,
                    "  SUPPORTING {} line {}: {}",
                    f.rule, f.line, f.message
                );
            }
        }
        for f in &r.findings {
            let _ = writeln!(out, "  SET {} line {}: {}", f.rule, f.line, f.message);
        }
    }
    out.trim_end().to_string()
}
