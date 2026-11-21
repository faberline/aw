//! The facts one repository gives to the patrol checks. The infrastructure
//! reads them from GitHub; the checks only read these values.

/// One issue comment.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CommentFact {
    pub body: String,
    pub created_at: String,
}

/// One open issue (not a pull request).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct IssueFact {
    pub number: u64,
    pub title: String,
    pub labels: Vec<String>,
    pub updated_at: String,
    pub body: String,
    /// Read for `blocked` issues only.
    pub newest_comment: Option<CommentFact>,
}

impl IssueFact {
    pub fn has(&self, label: &str) -> bool {
        self.labels.iter().any(|l| l == label)
    }

    /// `spec`, `tdd` or `review`, from the `stage:` label.
    pub fn stage(&self) -> Option<&str> {
        self.labels
            .iter()
            .find_map(|l| l.strip_prefix("stage:"))
            .filter(|s| matches!(*s, "spec" | "tdd" | "review"))
    }

    pub fn blocked(&self) -> bool {
        self.has("blocked")
    }
}

/// One open pull request.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PrFact {
    pub number: u64,
    pub title: String,
    pub updated_at: String,
    /// GitHub `mergeable_state`: `clean`, `behind`, `dirty`, `blocked`, ...
    pub merge_state: String,
    /// A check run of the head commit failed or timed out.
    pub failed_checks: bool,
}

/// The newest completed run of one workflow on the default branch.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RunFact {
    pub workflow: String,
    pub id: u64,
    pub sha: String,
    /// `success`, `failure`, `cancelled`, ...
    pub conclusion: String,
}

/// Everything the checks read about one repository.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RepoFacts {
    pub repo: String,
    pub issues: Vec<IssueFact>,
    pub prs: Vec<PrFact>,
    pub main_runs: Vec<RunFact>,
    /// The time of the newest merged pull request.
    pub last_merge: Option<String>,
    /// The time of the newest commit on the default branch.
    pub last_commit: Option<String>,
    /// `Waits for:` references (`#7`, `owner/name#7`) that are closed.
    pub closed_refs: Vec<String>,
}
