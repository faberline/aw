//! Use case: check the README contract of one or more projects.

use crate::domain::meta::ports::MetaRepo;
use crate::domain::meta::readme;
use crate::domain::work::ports::PortResult;

pub use crate::domain::meta::report::{MetaReport, render};

/// The port the meta check uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct MetaServices<'a> {
    pub repo: &'a dyn MetaRepo,
}

/// Read each target, then check it. A target is a README or a folder
/// with one, from the repository root.
pub fn check(s: MetaServices<'_>, targets: &[String]) -> PortResult<MetaReport> {
    let config = s.repo.config()?;
    let cargo = s.repo.cargo_packages()?;
    let mut reports = Vec::new();
    for target in targets {
        let input = s.repo.readme(target)?;
        reports.push(readme::check(&input, &config, s.repo, &cargo));
    }
    Ok(crate::domain::meta::report::report(reports))
}
