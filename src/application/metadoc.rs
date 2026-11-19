//! Use case: check the project documents of one or more project folders.

use crate::application::meta::MetaServices;
use crate::domain::meta::project;
use crate::domain::work::ports::PortResult;

pub use crate::domain::meta::project::{MetadocReport, render};

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
