use anyhow::{Context, Result, bail};

use super::active::{board, in_checkout};
use super::services::Services;
use crate::domain::work::claim::{Claim, STALE_AFTER_SECONDS, latest_mark};
use crate::domain::work::issue::{Issue, awaiting_report, redo_requested, valid_agent};
use crate::domain::work::ports::Repo;
use crate::domain::work::pull_request::{PullRequest, find_for_issue, rework_branch, work_branch};
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
/// Issues in `skip` are passed over, for example a review that waits on
/// running checks. With `only`, the agent takes and holds claims of that
/// stage only, so one worker per stage can run at the same time.
/// An issue with a `needs:<thing>` label is passed over unless `has`
/// names that thing, so a cloud worker leaves Mac-only work to a Mac.
pub fn claim(
    s: Services<'_>,
    agent: &str,
    only: Option<Stage>,
    skip: &[u64],
    has: &[String],
) -> Result<ClaimOutcome> {
    if !valid_agent(agent) {
        bail!("agent `{agent}` must use only a-z, 0-9 and -");
    }
    let now = s.clock.now();
    let board = board(s, agent)?;
    if let Some(active) = board.active(agent, only) {
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
    let mut needs_other = Vec::new();

    for issue in &board.issues {
        if skip.contains(&issue.number) {
            continue;
        }
        let Some(stage) = issue.actionable_stage(agent) else {
            continue;
        };
        if only.is_some_and(|o| o != stage) {
            continue;
        }
        // Read the comments of one issue only when its labels let the
        // agent take it. Most claims read one issue.
        let comments = s.tracker.comments(issue.number)?;
        if !awaiting_report(stage, agent, &comments) {
            continue;
        }
        if let Some(need) = issue.missing_need(has) {
            needs_other.push(format!("#{} needs `{need}`", issue.number));
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
                // One agent may review its own pull request in a later run.
                Some(pr) => Some(pr),
            }
        } else {
            // A tdd rework continues the branch of the open pull request.
            found
        };

        let mut claim = Claim {
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
        let mut superseded = None;
        if stage == Stage::Tdd {
            superseded = prepare_work_branch(s, &mut claim, pr.as_ref(), &comments)?;
        }
        // A claim of a dead run that is older than the recent comments is
        // still on the issue. Remove it, so the issue shows one claim.
        if let Some((old, mark)) = latest_mark(&comments, agent)
            && now - mark.claimed_at >= STALE_AFTER_SECONDS
        {
            s.tracker.delete_comment(old).with_context(|| {
                format!("could not remove the stale claim on #{}", issue.number)
            })?;
        }
        s.tracker
            .comment(issue.number, &claim.mark().render())
            .with_context(|| format!("could not post the claim on issue #{}", issue.number))?;
        if let Some(old) = superseded {
            close_superseded(s, &old, &claim.branch)?;
        }
        return Ok(ClaimOutcome::Claimed(claim));
    }

    let mut why = match only {
        Some(o) => format!("no open {o} issue for `{agent}` is waiting on it"),
        None => format!("no open issue for `{agent}` is waiting on it"),
    };
    if !needs_checkout.is_empty() {
        why.push_str(&format!(
            "; tdd on {} needs a clone of {}: run `aw claim` in it",
            needs_checkout.join(", "),
            repo.name
        ));
    }
    if !needs_other.is_empty() {
        why.push_str(&format!(
            "; passed over {}: run `aw claim --has <thing>` on a machine that has it",
            needs_other.join(", ")
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
///
/// An open pull request branch that is behind the default branch gets
/// `git merge origin/<default>`: no rebase, no force push. A redo that the
/// spec asked for (`aw:redo`), or a merge that conflicts, starts a new
/// branch `<head>-r<k>` from the default branch instead. The pull request
/// of the old branch is returned; the caller closes it after the claim.
fn prepare_work_branch(
    s: Services<'_>,
    claim: &mut Claim,
    pr: Option<&PullRequest>,
    comments: &[crate::domain::work::issue::Comment],
) -> Result<Option<PullRequest>> {
    let ws = s.workspace;
    if !ws.is_clean()? {
        bail!("the working tree has changes; commit, stash or remove them before a tdd claim");
    }
    ws.fetch().context("git fetch failed")?;
    let upstream = format!("origin/{}", claim.default_branch);
    let Some(pr) = pr.filter(|p| p.head != claim.default_branch) else {
        open_branch(s, claim)?;
        return Ok(None);
    };
    if !redo_requested(comments) {
        open_branch(s, claim)?;
        if ws.contains(&upstream)? || ws.merge(&upstream)? {
            return Ok(None);
        }
    }
    // Redo: a fresh branch from the default branch, never a force push.
    let mut branch = rework_branch(&pr.head);
    while ws.has_local_branch(&branch)? || ws.has_remote_branch(&branch)? {
        branch = rework_branch(&branch);
    }
    claim.branch = branch;
    claim.pr = None;
    ws.create_branch(&claim.branch, &upstream)?;
    Ok(Some(pr.clone()))
}

fn open_branch(s: Services<'_>, claim: &Claim) -> Result<()> {
    let ws = s.workspace;
    if ws.has_local_branch(&claim.branch)? {
        switch_to(s, &claim.branch)?;
    } else if ws.has_remote_branch(&claim.branch)? {
        ws.create_branch(&claim.branch, &format!("origin/{}", claim.branch))?;
    } else {
        ws.create_branch(&claim.branch, &format!("origin/{}", claim.default_branch))?;
    }
    Ok(())
}

/// Point the old pull request at the new branch and close it.
fn close_superseded(s: Services<'_>, old: &PullRequest, branch: &str) -> Result<()> {
    s.tracker
        .pr_comment(
            old.number,
            &format!("Superseded: the work restarts from the default branch on `{branch}`."),
        )
        .with_context(|| format!("could not comment on #{}", old.number))?;
    s.tracker
        .close_pr(old.number)
        .with_context(|| format!("could not close #{}", old.number))?;
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
