//! What the run use cases need from the outside world.

use super::record::RunRecord;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct RunError(pub String);

pub type RunResult<T> = Result<T, RunError>;

/// The append-only run ledger.
pub trait RunLedger {
    fn append(&self, record: &RunRecord) -> RunResult<()>;
    /// Every record, oldest first. Lines that do not parse are skipped.
    fn read_all(&self) -> RunResult<Vec<RunRecord>>;
}
