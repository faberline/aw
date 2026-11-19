//! Use cases: claim one issue, report on it, or release it.

mod active;
pub mod claim;
mod queue;
pub mod release;
pub mod report;
pub mod services;

pub use claim::{ClaimOutcome, claim, claim_with};
pub use release::{release, release_issue, status};
pub use report::{Reported, report, report_issue};
pub use services::Services;

pub use crate::domain::work::claim::Claim;
pub use crate::domain::work::signal::Outcome;
