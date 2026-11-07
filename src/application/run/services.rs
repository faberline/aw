use std::path::Path;

use crate::domain::run::ports::RunLedger;
use crate::domain::work::ports::Clock;

/// The ports the run use cases use. The composition root builds it.
#[derive(Clone, Copy)]
pub struct RunServices<'a> {
    pub ledger: &'a dyn RunLedger,
    pub clock: &'a dyn Clock,
    /// The workers directory. A session in a clone under it is a worker run.
    pub root: &'a Path,
}
