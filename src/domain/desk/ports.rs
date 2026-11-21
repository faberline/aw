//! What the patrol needs from the outside.

use super::facts::RepoFacts;

/// A repository that cannot be read: the text names the call that failed.
pub type ReadError = String;

pub trait DeskSource {
    /// The facts of `owner/name`. `Err` names the GitHub call that failed.
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError>;
}
