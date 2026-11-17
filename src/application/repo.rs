//! Use case: check the tracked files of one repository for clutter.

use crate::domain::repo::ports::RepoFiles;
use crate::domain::repo::rules;
use crate::domain::work::ports::PortResult;

pub use crate::domain::repo::rules::{Report, Violation, render, render_baseline};

/// The port the hygiene check uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct RepoServices<'a> {
    pub files: &'a dyn RepoFiles,
}

/// Check the files against the rules and the baseline in `aw.toml`.
pub fn check(s: RepoServices<'_>) -> PortResult<Report> {
    let config = s.files.config()?;
    rules::check(s.files, &config)
}

/// Every current violation, for a first baseline.
pub fn baseline(s: RepoServices<'_>) -> PortResult<Vec<Violation>> {
    let config = s.files.config()?;
    rules::scan(s.files, &config)
}
