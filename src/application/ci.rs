//! Use case: check one repository against the CI stages standard.

use crate::domain::ci::ports::{BranchRules, CiFiles};
use crate::domain::work::ports::PortResult;

pub use crate::domain::ci::rules::{Finding, Report, Status};

/// The ports the CI check uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct CiServices<'a> {
    pub files: &'a dyn CiFiles,
    /// `None` skips the ruleset check.
    pub rules: Option<&'a dyn BranchRules>,
}

/// Read the workflow, the exceptions and the ruleset, then check them.
pub fn check(s: CiServices<'_>) -> PortResult<Report> {
    let workflow = s.files.workflow()?;
    let exceptions = s.files.exceptions()?;
    let required = s.rules.map(|r| r.required_checks()).transpose()?;
    Ok(crate::domain::ci::rules::check(
        workflow.as_ref(),
        required.as_deref(),
        &exceptions,
    ))
}
