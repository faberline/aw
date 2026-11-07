use anyhow::{Context, Result, bail};

use super::active::{board, in_checkout};
use super::services::Services;
use crate::domain::work::claim::Claim;
use crate::domain::work::issue::{Issue, awaiting_report, valid_agent};
use crate::domain::work::ports::Repo;
use crate::domain::work::pull_request::{PullRequest, find_for_issue, work_branch};
use crate::domain::work::signal::{Outcome, Signal};
use crate::domain::work::stage::Stage;

#[derive(Debug, PartialEq, Eq)]
pub enum ClaimOutcome {
    Claimed(Claim),
    /// Nothing to do; the text says why.
    Nothing(String),
}

/// Take the first issue `agent` may work on and record the claim as an
/// issue comment. Only tdd touches the working tree: it must be a clean
/// checkout of the repository, and the work branch is checked out.
pub fn claim(s: Services<'_>, agent: &str) -> Result<ClaimOutcome> {
    if !valid_agent(agent) {
        bail!("agent `{agent}` must use only a-z, 0-9 and -");
    }
    let now = s.clock.now();
    let board = board(s, agent)?;
    if let Some(active) = board.active(agent) {
        if !active.claim.is_stale(now) {
            return Ok(ClaimOutcome::Nothing(format!(
                "issue #{} ({}) is still claimed by `{agent}`; run `aw report` or `aw release` first",
                active.claim.issue, active.claim.stage
            )));
        }
        s.tracker
            .delete_comment(active.comment_id)
            .with_context(|| {
                format!(
                    "could not remove the stale claim on #{}",
                    active.claim.issue
                )
            })?;
    }

    let repo = &board.repo;
    let mut prs: Option<Vec<PullRequest>> = None;
    let mut checkout: Option<bool> = None;
    let mut needs_checkout = Vec::new();

    for open in &board.open {
        let issue = &open.issue;
        let Some(stage) = issue.actionable_stage(agent) else {
            continue;
        };
        if !awaiting_report(stage, agent, &open.comments) {
            continue;
        }
        if stage == Stage::Tdd {
            if checkout.is_none() {
                checkout = Some(in_checkout(s, &repo.name)?);
            }
            if checkout == Some(false) {
                needs_checkout.push(format!("#{}", issue.number));
                continue;
            }
        }
        let found = match stage {
            Stage::Spec => None,
            Stage::Tdd | Stage::Review => {
                if prs.is_none() {
                    prs = Some(s.tracker.open_prs()?);
                }
                prs.as_deref()
                    .and_then(|all| find_for_issue(all, issue.number))
                    .cloned()
            }
        };
        let pr = if stage == Stage::Review {
            match found {
                None => {
                    block(s, issue, agent, "No open pull request closes this issue.")?;
                    continue;
                }
                Some(pr) if pr.authored_by(agent) => {
                    let text = format!(
                        "Pull request #{} was authored by `{agent}`; another agent must review it.",
                        pr.number
                    );
                    block(s, issue, agent, &text)?;
                    continue;
                }
                Some(pr) => Some(pr),
            }
        } else {
            // A tdd rework continues the branch of the open pull request.
            found
        };

        let claim = Claim {
            repo: repo.name.clone(),
            agent: agent.to_string(),
            issue: issue.number,
            stage,
            title: issue.title.clone(),
            url: issue.url.clone(),
            branch: branch_for(stage, agent, issue, repo, pr.as_ref()),
            pr: pr.as_ref().map(|p| p.number),
            default_branch: repo.default_branch.clone(),
            claimed_at: now,
        };
        if stage == Stage::Tdd {
            check_out_work_branch(s, &claim)?;
        }
        s.tracker
            .comment(issue.number, &claim.mark().render())
            .with_context(|| format!("could not post the claim on issue #{}", issue.number))?;
        return Ok(ClaimOutcome::Claimed(claim));
    }

    let mut why = format!("no open issue for `{agent}` is waiting on it");
    if !needs_checkout.is_empty() {
        why.push_str(&format!(
            "; tdd on {} needs a clone of {}: run `aw claim` in it",
            needs_checkout.join(", "),
            repo.name
        ));
    }
    Ok(ClaimOutcome::Nothing(why))
}

fn branch_for(
    stage: Stage,
    agent: &str,
    issue: &Issue,
    repo: &Repo,
    pr: Option<&PullRequest>,
) -> String {
    match (stage, pr) {
        (Stage::Spec, _) => repo.default_branch.clone(),
        (Stage::Tdd, Some(pr)) => pr.head.clone(),
        (Stage::Tdd, None) => work_branch(agent, issue.number),
        (Stage::Review, Some(pr)) => pr.head.clone(),
        (Stage::Review, None) => unreachable!("review claims always carry a pull request"),
    }
}

/// Record a blocked signal so the planner sees why the issue was skipped.
fn block(s: Services<'_>, issue: &Issue, agent: &str, text: &str) -> Result<()> {
    let signal = Signal {
        stage: Stage::Review,
        agent: agent.to_string(),
        outcome: Outcome::Blocked,
    };
    s.tracker
        .comment(issue.number, &signal.render(text, None))
        .with_context(|| format!("could not comment on issue #{}", issue.number))?;
    Ok(())
}

/// Check out the tdd work branch in a clean checkout.
fn check_out_work_branch(s: Services<'_>, claim: &Claim) -> Result<()> {
    let ws = s.workspace;
    if !ws.is_clean()? {
        bail!("the working tree has changes; commit, stash or remove them before a tdd claim");
    }
    ws.fetch().context("git fetch failed")?;
    if ws.has_local_branch(&claim.branch)? {
        switch_to(s, &claim.branch)?;
    } else if ws.has_remote_branch(&claim.branch)? {
        ws.create_branch(&claim.branch, &format!("origin/{}", claim.branch))?;
    } else {
        ws.create_branch(&claim.branch, &format!("origin/{}", claim.default_branch))?;
    }
    Ok(())
}

/// Switch to a local branch and bring it up to `origin` when it has one.
pub(crate) fn switch_to(s: Services<'_>, branch: &str) -> Result<()> {
    let ws = s.workspace;
    if ws.has_local_branch(branch)? {
        ws.switch(branch)?;
        if ws.has_remote_branch(branch)? {
            ws.fast_forward(&format!("origin/{branch}"))
                .with_context(|| format!("`{branch}` has diverged from origin/{branch}"))?;
        }
    } else {
        ws.create_branch(branch, &format!("origin/{branch}"))?;
    }
    Ok(())
}
