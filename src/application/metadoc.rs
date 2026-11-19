//! Use case: check the project documents of one or more project folders.

use crate::application::meta::MetaServices;
use crate::domain::meta::{project, prompt};
use crate::domain::work::ports::PortResult;

pub use crate::domain::meta::project::{MetadocReport, render};

/// The result of `aw metadoc prompt`.
pub enum Prompt {
    /// The clean-reader task.
    Text(String),
    /// The deterministic check failed; no prompt is made.
    Findings(MetadocReport),
}

/// Check one project folder; when it passes, build the clean-reader task.
pub fn prompt(s: MetaServices<'_>, target: &str) -> PortResult<Prompt> {
    let config = s.repo.config()?;
    let cargo = s.repo.cargo_packages()?;
    let dir = s.repo.project_dir(target)?;
    let report = project::check(s.repo, &config, &cargo, &dir)?;
    if report.ok
        && let Some(text) = prompt::clean_reader(&report, &s.repo.root(), &dir)
    {
        return Ok(Prompt::Text(text));
    }
    Ok(Prompt::Findings(project::report(vec![report])))
}

/// Resolve each target to a project folder, then check its documents. A
/// target is a project folder or one of its core documents.
pub fn check(s: MetaServices<'_>, targets: &[String]) -> PortResult<MetadocReport> {
    let config = s.repo.config()?;
    let cargo = s.repo.cargo_packages()?;
    let mut reports = Vec::new();
    for target in targets {
        let dir = s.repo.project_dir(target)?;
        reports.push(project::check(s.repo, &config, &cargo, &dir)?);
    }
    Ok(project::report(reports))
}
