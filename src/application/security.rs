//! Use case: check the tracked files of one repository against the security
//! standard.

use crate::domain::security::ports::SecurityFiles;
use crate::domain::security::rules;
use crate::domain::work::ports::PortResult;

pub use crate::domain::security::rules::{Finding, Report, Status, render};

/// The port the security check uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct SecurityServices<'a> {
    pub files: &'a dyn SecurityFiles,
}

/// Check the files. `strict` makes a warning fail the check.
pub fn check(s: SecurityServices<'_>, strict: bool) -> PortResult<Report> {
    rules::check(s.files, strict)
}
