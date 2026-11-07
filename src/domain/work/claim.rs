use serde::{Deserialize, Serialize};

use super::stage::Stage;

/// A claim lives longer than one run only when the run died. After this
/// many seconds a new `aw claim` replaces it.
pub const STALE_AFTER_SECONDS: i64 = 3 * 60 * 60;

/// The one issue this checkout works on now. `aw claim` writes it and
/// `aw report` / `aw release` remove it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    pub repo: String,
    pub agent: String,
    pub issue: u64,
    pub stage: Stage,
    pub title: String,
    pub url: String,
    /// spec: the default branch. tdd: the head of the open pull request
    /// that closes the issue, else `<agent>/issue-<n>`. review: the pull
    /// request head, checked out detached.
    pub branch: String,
    pub pr: Option<u64>,
    pub default_branch: String,
    /// Unix seconds.
    pub claimed_at: i64,
}

impl Claim {
    pub fn is_stale(&self, now: i64) -> bool {
        now - self.claimed_at >= STALE_AFTER_SECONDS
    }
}
