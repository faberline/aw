//! What the CI check needs from the outside world.

use super::workflow::{Exception, Workflow};
use crate::domain::work::ports::PortResult;

/// The repository files that the check reads: a local checkout or the
/// default branch on GitHub.
pub trait CiFiles {
    /// `.github/workflows/ci.yml`, or `None` when the file does not exist.
    fn workflow(&self) -> PortResult<Option<Workflow>>;
    /// `[[standards.ci.exception]]` of `aw.toml`; empty when the file or
    /// the table does not exist.
    fn exceptions(&self) -> PortResult<Vec<Exception>>;
}

/// The rules of the default branch on the code host.
pub trait BranchRules {
    /// The status check names that the default branch requires.
    fn required_checks(&self) -> PortResult<Vec<String>>;
}
