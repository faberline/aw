//! Use cases: record one hook event of a worker run, and list recent runs.

pub mod record;
pub mod services;

pub use record::{Recorded, record, runs};
pub use services::RunServices;

pub use crate::domain::run::ports::{RunError, RunResult};
pub use crate::domain::run::record::{Client, Event, RunRecord};
pub use crate::domain::run::summary::RunSummary;
