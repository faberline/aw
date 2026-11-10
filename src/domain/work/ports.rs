//! What the work use cases need from the outside world.

use super::issue::{Comment, Issue};
use super::pull_request::PullRequest;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct PortError(pub String);

pub type PortResult<T> = Result<T, PortError>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Repo {
    /// `owner/name`.
    pub name: String,
    pub default_branch: String,
}

/// The issue tracker and code host (GitHub).
pub trait Tracker {
    fn repo(&self) -> PortResult<Repo>;
    fn open_issues(&self, label: &str) -> PortResult<Vec<Issue>>;
    fn comments(&self, issue: u64) -> PortResult<Vec<Comment>>;
    fn comment(&self, issue: u64, body: &str) -> PortResult<()>;
    fn edit_comment(&self, id: u64, body: &str) -> PortResult<()>;
    fn delete_comment(&self, id: u64) -> PortResult<()>;
    fn add_labels(&self, issue: u64, labels: &[String]) -> PortResult<()>;
    fn remove_label(&self, issue: u64, label: &str) -> PortResult<()>;
    fn open_prs(&self) -> PortResult<Vec<PullRequest>>;
    fn create_pr(&self, head: &str, base: &str, title: &str, body: &str)
    -> PortResult<PullRequest>;
    fn edit_pr_body(&self, pr: u64, body: &str) -> PortResult<()>;
    fn request_changes(&self, pr: u64, body: &str) -> PortResult<()>;
    fn pr_comment(&self, pr: u64, body: &str) -> PortResult<()>;
    /// Close the pull request without a merge.
    fn close_pr(&self, pr: u64) -> PortResult<()>;
    /// Delete a branch of the repository.
    fn delete_branch(&self, branch: &str) -> PortResult<()>;
    fn squash_merge(&self, pr: u64, subject: &str, body: &str) -> PortResult<()>;
    /// How many commits of `base` the `head` branch does not have.
    fn behind_by(&self, base: &str, head: &str) -> PortResult<u64>;
    /// Merge the base branch into the pull request branch; CI runs again.
    fn update_branch(&self, pr: u64) -> PortResult<()>;
    /// The newest check run of some name on the `head` branch failed or
    /// timed out. CI must run again before that branch can merge.
    fn has_failed_checks(&self, head: &str) -> PortResult<bool>;
}

/// The local git checkout. Remote refs are `origin/<branch>`.
pub trait Workspace {
    /// `owner/name` of the GitHub `origin`, or `None` when the current
    /// directory is not a git checkout with such a remote.
    fn origin_repo(&self) -> PortResult<Option<String>>;
    /// No staged, unstaged or untracked changes.
    fn is_clean(&self) -> PortResult<bool>;
    fn fetch(&self) -> PortResult<()>;
    fn has_local_branch(&self, branch: &str) -> PortResult<bool>;
    fn has_remote_branch(&self, branch: &str) -> PortResult<bool>;
    fn switch(&self, branch: &str) -> PortResult<()>;
    fn create_branch(&self, branch: &str, start: &str) -> PortResult<()>;
    fn fast_forward(&self, upstream: &str) -> PortResult<()>;
    fn detach(&self, start: &str) -> PortResult<()>;
    /// Push HEAD to `origin/<branch>`. Never a force push.
    fn push(&self, branch: &str) -> PortResult<()>;
}

pub trait Clock {
    /// Unix seconds.
    fn now(&self) -> i64;
}
