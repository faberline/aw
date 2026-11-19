//! Use case: check the release workflows of the apps of a checkout.

use crate::domain::release::app_check::{self, ContractRepo};
use crate::domain::work::ports::PortResult;

pub use crate::domain::release::app_check::{ContractReport, render};

/// The port the check uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct ContractServices<'a> {
    pub repo: &'a dyn ContractRepo,
}

/// Check every app of `[release.apps.*]`, or only `app`.
pub fn check(s: ContractServices<'_>, app: Option<&str>) -> PortResult<ContractReport> {
    app_check::check(s.repo, app)
}
