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
///
/// That fallback can meet a dead claim of another run: an old claim on
/// another issue, left when the live claim was missing from the recent
/// comments. So the fallback accepts a claim only when it is certainly
/// the claim of this run (see `is_own_old_claim`).
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
    newest_own(s, &board.repo, marks, stage)
}

/// The newest of the old claims in `marks` that belong to this run.
fn newest_own(
    s: Services<'_>,
    repo: &Repo,
    marks: Vec<(&Issue, u64, ClaimMark)>,
    stage: Option<Stage>,
) -> Result<Option<Active>> {
    // Ask git only when there is an old claim to judge.
    let mut head: Option<Option<String>> = None;
    let mut own = Vec::new();
    for (issue, id, mark) in marks {
        if stage.is_some_and(|wanted| mark.stage != wanted) {
            continue;
        }
        // spec and review never change the checkout, tdd does: the
        // checkout is the only evidence of which run holds a claim.
        if mark.stage == Stage::Spec {
            continue;
        }
        if head.is_none() {
            head = Some(if in_checkout(s, &repo.name)? {
                s.workspace.current_branch()?
            } else {
                None
            });
        }
        if is_own_old_claim(
            mark.stage,
            &mark.branch,
            head.as_ref().and_then(Option::as_deref),
        ) {
            own.push((issue, id, mark));
        }
    }
    Ok(newest(repo, own.into_iter(), stage))
}

/// Does an old claim (found only by reading each open issue) belong to
/// the run that reports now? `head` is the branch of the checkout of the
/// repository, `None` when there is no such checkout or HEAD is detached.
///
/// - spec: no. A spec claim names the default branch, and a spec run
///   never changes the checkout, so nothing tells this run from a dead
///   run on another issue. Refuse: the report says "holds no claim".
/// - tdd and review: only when the branch of the claim is the branch of
///   the checkout. A claim for another issue has another branch.
fn is_own_old_claim(stage: Stage, branch: &str, head: Option<&str>) -> bool {
    match stage {
        Stage::Spec => false,
        Stage::Tdd | Stage::Review => head == Some(branch),
    }
}

/// True when the current directory is a checkout of `repo`. tdd needs one.
pub(crate) fn in_checkout(s: Services<'_>, repo: &str) -> Result<bool> {
    Ok(s.workspace
        .origin_repo()?
        .is_some_and(|origin| origin.eq_ignore_ascii_case(repo)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_old_spec_claim_is_never_own() {
        assert!(!is_own_old_claim(Stage::Spec, "main", Some("main")));
        assert!(!is_own_old_claim(Stage::Spec, "main", None));
    }

    #[test]
    fn an_old_tdd_or_review_claim_is_own_only_on_the_branch_of_the_checkout() {
        for stage in [Stage::Tdd, Stage::Review] {
            assert!(is_own_old_claim(
                stage,
                "claude/issue-2",
                Some("claude/issue-2")
            ));
            assert!(!is_own_old_claim(
                stage,
                "claude/issue-2",
                Some("claude/issue-1")
            ));
            assert!(!is_own_old_claim(
                stage,
                "claude/issue-2",
                Some("claude/issue-2-x")
            ));
            assert!(!is_own_old_claim(stage, "claude/issue-2", None));
        }
    }
}
