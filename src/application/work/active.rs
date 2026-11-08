//! Find an agent's claim on GitHub. The claim is an issue comment, so any
//! machine can see it; nothing is kept on disk.

use anyhow::Result;

use super::services::Services;
use crate::domain::work::claim::{Claim, latest_mark};
use crate::domain::work::issue::{Comment, Issue, agent_label, claim_order};
use crate::domain::work::ports::Repo;
use crate::domain::work::stage::Stage;

/// One open issue for an agent, with its comments.
pub(crate) struct Open {
    pub issue: Issue,
    pub comments: Vec<Comment>,
}

/// The open issues of one agent in one repository, in claim order.
pub(crate) struct Board {
    pub repo: Repo,
    pub open: Vec<Open>,
}

/// A claim and the id of the comment that holds it.
pub(crate) struct Active {
    pub claim: Claim,
    pub comment_id: u64,
}

pub(crate) fn board(s: Services<'_>, agent: &str) -> Result<Board> {
    let repo = s.tracker.repo()?;
    let mut issues = s.tracker.open_issues(&agent_label(agent))?;
    claim_order(&mut issues);
    let mut open = Vec::with_capacity(issues.len());
    for issue in issues {
        let comments = s.tracker.comments(issue.number)?;
        open.push(Open { issue, comments });
    }
    Ok(Board { repo, open })
}

impl Board {
    /// The newest claim comment by `agent` on any of its open issues.
    /// With a `stage`, only a claim for that stage counts, so one agent can
    /// hold one claim per stage.
    pub fn active(&self, agent: &str, stage: Option<Stage>) -> Option<Active> {
        self.open
            .iter()
            .filter_map(|o| {
                latest_mark(&o.comments, agent).map(|(comment_id, mark)| Active {
                    claim: Claim::from_mark(mark, &o.issue, &self.repo),
                    comment_id,
                })
            })
            .filter(|a| stage.is_none_or(|s| a.claim.stage == s))
            .max_by_key(|a| a.claim.claimed_at)
    }
}

pub(crate) fn active(s: Services<'_>, agent: &str, stage: Option<Stage>) -> Result<Option<Active>> {
    Ok(board(s, agent)?.active(agent, stage))
}

/// True when the current directory is a checkout of `repo`. tdd needs one.
pub(crate) fn in_checkout(s: Services<'_>, repo: &str) -> Result<bool> {
    Ok(s.workspace
        .origin_repo()?
        .is_some_and(|origin| origin.eq_ignore_ascii_case(repo)))
}
