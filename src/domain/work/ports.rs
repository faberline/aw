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

/// What `aw` reads of one issue before it splits it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct IssueDetail {
    pub body: String,
    pub labels: Vec<String>,
    /// The number of the milestone, which the create request needs.
    pub milestone: Option<u64>,
}

/// An issue to make.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NewIssue {
    pub title: String,
    pub body: String,
    pub labels: Vec<String>,
    pub milestone: Option<u64>,
}

/// The issue tracker and code host (GitHub).
pub trait Tracker {
    fn repo(&self) -> PortResult<Repo>;
    fn open_issues(&self, label: &str) -> PortResult<Vec<Issue>>;
    fn comments(&self, issue: u64) -> PortResult<Vec<Comment>>;
    /// The comments on all issues of the repository that changed at or
    /// after `since` (RFC 3339 UTC), each with its issue number. One list
    /// request, not one request for each issue.
    fn comments_since(&self, since: &str) -> PortResult<Vec<(u64, Comment)>>;
    fn comment(&self, issue: u64, body: &str) -> PortResult<()>;
    fn edit_comment(&self, id: u64, body: &str) -> PortResult<()>;
    fn delete_comment(&self, id: u64) -> PortResult<()>;
    fn add_labels(&self, issue: u64, labels: &[String]) -> PortResult<()>;
    fn remove_label(&self, issue: u64, label: &str) -> PortResult<()>;
    /// The body, the labels and the milestone number of one issue.
    fn issue_detail(&self, issue: u64) -> PortResult<IssueDetail>;
    /// Make an issue; returns its number.
    fn create_issue(&self, new: &NewIssue) -> PortResult<u64>;
    /// Close the issue as not planned: other issues replace it.
    fn close_issue(&self, issue: u64) -> PortResult<()>;
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
    /// The newest check run of each name on the `head` branch.
    fn check_runs(&self, head: &str) -> PortResult<Vec<CheckRun>>;
    /// The check names that the repository rulesets require on `branch`
    /// (the default branch). Empty when no ruleset requires a check.
    fn required_checks(&self, branch: &str) -> PortResult<Vec<String>>;
}

/// The newest run of one check on a head commit.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CheckRun {
    pub name: String,
    /// The run has not finished (queued or in progress).
    pub running: bool,
    /// The run finished with `failure` or `timed_out`.
    pub failed: bool,
}

/// What the required checks say about the head of a pull request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RequiredChecks {
    /// Every required check exists and none failed; all have finished.
    Passed,
    /// Every required check exists and none failed; some still run.
    Running,
    /// A required check is missing or failed. The pull request cannot merge.
    Blocked,
}

/// Judge `runs` against the `required` check names.
pub fn judge_required(required: &[String], runs: &[CheckRun]) -> RequiredChecks {
    let mut running = false;
    for name in required {
        match runs.iter().find(|r| &r.name == name) {
            None => return RequiredChecks::Blocked,
            Some(r) if r.failed => return RequiredChecks::Blocked,
            Some(r) => running |= r.running,
        }
    }
    if runs.iter().any(|r| r.failed) {
        return RequiredChecks::Blocked;
    }
    if running {
        RequiredChecks::Running
    } else {
        RequiredChecks::Passed
    }
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
    /// `upstream` is already part of HEAD, so the branch is not behind it.
    fn contains(&self, upstream: &str) -> PortResult<bool>;
    /// Merge `upstream` into HEAD without a rebase. `true` when it merged
    /// cleanly. On a conflict it aborts the merge, leaves the branch as it
    /// was and returns `false`.
    fn merge(&self, upstream: &str) -> PortResult<bool>;
    fn detach(&self, start: &str) -> PortResult<()>;
    /// Push HEAD to `origin/<branch>`. Never a force push.
    fn push(&self, branch: &str) -> PortResult<()>;
}

pub trait Clock {
    /// Unix seconds.
    fn now(&self) -> i64;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(name: &str, running: bool, failed: bool) -> CheckRun {
        CheckRun {
            name: name.into(),
            running,
            failed,
        }
    }

    #[test]
    fn required_checks_are_judged_by_presence_failure_and_progress() {
        let req = vec!["gate".to_string()];
        assert_eq!(judge_required(&req, &[]), RequiredChecks::Blocked);
        assert_eq!(
            judge_required(&req, &[run("lint", false, false)]),
            RequiredChecks::Blocked
        );
        assert_eq!(
            judge_required(&req, &[run("gate", false, true)]),
            RequiredChecks::Blocked
        );
        assert_eq!(
            judge_required(&req, &[run("gate", true, false)]),
            RequiredChecks::Running
        );
        assert_eq!(
            judge_required(&req, &[run("gate", false, false), run("x", false, true)]),
            RequiredChecks::Blocked
        );
        assert_eq!(
            judge_required(&req, &[run("gate", false, false)]),
            RequiredChecks::Passed
        );
    }
}
