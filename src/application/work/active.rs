//! Find an agent's claim on GitHub. The claim is an issue comment, so any
//! machine can see it; nothing is kept on disk.

use anyhow::Result;

use super::services::Services;
use crate::domain::run::record::rfc3339;
use crate::domain::work::claim::{Claim, ClaimMark, STALE_AFTER_SECONDS, latest_mark};
use crate::domain::work::issue::{Comment, Issue, agent_label, claim_order};
use crate::domain::work::ports::Repo;
use crate::domain::work::stage::Stage;

/// How far back the board reads the comments of the repository. A claim
/// that is not stale is younger than `STALE_AFTER_SECONDS`, so this window
/// holds every such claim, and a stale claim for one more stale period.
pub(crate) const RECENT_SECONDS: i64 = 2 * STALE_AFTER_SECONDS;

/// The open issues of one agent in one repository, in claim order, and
/// the recent comments on them.
///
/// The board costs a fixed number of requests: the repository, the issue
/// list and the recent comments. It does not read each issue.
pub(crate) struct Board {
    pub repo: Repo,
    pub issues: Vec<Issue>,
    /// The comments since `RECENT_SECONDS` ago, with the issue number.
    recent: Vec<(u64, Comment)>,
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
    let since = rfc3339(s.clock.now() - RECENT_SECONDS);
    let recent = s.tracker.comments_since(&since)?;
    Ok(Board {
        repo,
        issues,
        recent,
    })
}

/// The newest of the claims in `marks`, each with its issue and the id of
/// its comment. With a `stage`, only a claim for that stage counts.
fn newest<'a>(
    repo: &Repo,
    marks: impl Iterator<Item = (&'a Issue, u64, ClaimMark)>,
    stage: Option<Stage>,
) -> Option<Active> {
    marks
        .map(|(issue, comment_id, mark)| Active {
            claim: Claim::from_mark(mark, issue, repo),
            comment_id,
        })
        .filter(|a| stage.is_none_or(|s| a.claim.stage == s))
        .max_by_key(|a| a.claim.claimed_at)
}

impl Board {
    /// The newest claim comment by `agent` among the recent comments on
    /// its open issues. With a `stage`, only a claim for that stage
    /// counts, so one agent can hold one claim per stage.
    pub fn active(&self, agent: &str, stage: Option<Stage>) -> Option<Active> {
        let marks = self.issues.iter().filter_map(|issue| {
            let comments: Vec<Comment> = self
                .recent
                .iter()
                .filter(|(number, _)| *number == issue.number)
                .map(|(_, comment)| comment.clone())
                .collect();
            latest_mark(&comments, agent).map(|(id, mark)| (issue, id, mark))
        });
        newest(&self.repo, marks, stage)
    }
}

/// The claim that `agent` holds, for `aw report` and `aw release`. A
/// claim older than the recent comments is rare (a run that took longer
/// than `RECENT_SECONDS`). Only then read each open issue, so the report
/// of a long run still finds its claim. `aw status` and `aw claim` run
/// in every worker run, so they read the recent comments only.
pub(crate) fn active(s: Services<'_>, agent: &str, stage: Option<Stage>) -> Result<Option<Active>> {
    let board = board(s, agent)?;
    if let Some(active) = board.active(agent, stage) {
        return Ok(Some(active));
    }
    let mut marks = Vec::new();
    for issue in &board.issues {
        let comments = s.tracker.comments(issue.number)?;
        if let Some((id, mark)) = latest_mark(&comments, agent) {
            marks.push((issue, id, mark));
        }
    }
    Ok(newest(&board.repo, marks.into_iter(), stage))
}

/// True when the current directory is a checkout of `repo`. tdd needs one.
pub(crate) fn in_checkout(s: Services<'_>, repo: &str) -> Result<bool> {
    Ok(s.workspace
        .origin_repo()?
        .is_some_and(|origin| origin.eq_ignore_ascii_case(repo)))
}
