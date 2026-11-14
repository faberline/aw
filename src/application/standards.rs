//! Use case: check repositories against the core-reuse standard.

use crate::domain::standards::core_reuse::{self, CoreSummary, RepoInput};
use crate::domain::standards::ports::{CoreSource, RepoFiles};
use crate::domain::work::ports::PortResult;

pub use crate::domain::standards::core_reuse::{Report, render};

/// One repository to check.
pub struct RepoTarget<'a> {
    pub name: String,
    /// True when it is the `core` checkout itself.
    pub is_core: bool,
    pub files: &'a dyn RepoFiles,
}

/// The ports the check uses. The composition root builds it.
pub struct StandardsServices<'a> {
    pub core: &'a dyn CoreSource,
    pub core_name: String,
    pub repos: Vec<RepoTarget<'a>>,
}

/// Read the `core` release and every repository, then check them.
pub fn check(s: StandardsServices<'_>) -> PortResult<Report> {
    let release = s.core.release()?;
    let mut repos = Vec::new();
    for target in &s.repos {
        let input = RepoInput {
            name: target.name.clone(),
            is_core: target.is_core,
            manifests: target.files.manifests()?,
            build_scripts: target.files.build_scripts()?,
            exceptions: target.files.exceptions()?,
        };
        repos.push(core_reuse::check(&input, &release));
    }
    let ok = repos.iter().all(|r| r.ok);
    Ok(Report {
        schema: core_reuse::SCHEMA,
        core: CoreSummary {
            path: s.core_name,
            tag: release.tag,
            crates: release.crates.len(),
        },
        repos,
        ok,
    })
}
