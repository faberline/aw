//! Use cases: claim one issue, report on it, or release it.

pub mod claim;
pub mod release;
pub mod report;
pub mod services;

pub use claim::{ClaimOutcome, claim};
pub use release::{release, status};
pub use report::{Reported, report};
pub use services::Services;

pub use crate::domain::work::claim::Claim;
pub use crate::domain::work::signal::Outcome;
