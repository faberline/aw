use anyhow::{Context, Result, bail};

use super::services::Services;
use crate::domain::work::claim::Claim;
use crate::domain::work::issue::{Issue, agent_label, awaiting_report, claim_order, valid_agent};
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

/// Take the first issue `agent` may work on and check out its branch.
pub fn claim(s: Services<'_>, agent: &str) -> Result<ClaimOutcome> {
    if !valid_agent(agent) {
        bail!("agent `{agent}` must use only a-z, 0-9 and -");
    }
    let now = s.clock.now();
    if let Some(active) = s.store.load()? {
        if !active.is_stale(now) {
            return Ok(ClaimOutcome::Nothing(format!(
                "issue #{} ({}) is still claimed; run `aw report` or `aw release` first",
                active.issue, active.stage
            )));
        }
        s.store.clear()?;
    }
    if !s.workspace.is_clean()? {
        bail!("the working tree has changes; commit, stash or remove them before `aw claim`");
    }

    let repo = s.tracker.repo()?;
    let mut issues = s.tracker.open_issues(&agent_label(agent))?;
    claim_order(&mut issues);
    let mut prs: Option<Vec<PullRequest>> = None;

    for issue in issues {
        let Some(stage) = issue.actionable_stage(agent) else {
            continue;
        };
        let comments = s.tracker.comments(issue.number)?;
        if !awaiting_report(stage, agent, &comments) {
            continue;
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
                    block(s, &issue, agent, "No open pull request closes this issue.")?;
                    continue;
                }
                Some(pr) if pr.authored_by(agent) => {
                    let text = format!(
                        "Pull request #{} was authored by `{agent}`; another agent must review it.",
                        pr.number
                    );
                    block(s, &issue, agent, &text)?;
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
            branch: branch_for(stage, agent, &issue, &repo, pr.as_ref()),
            pr: pr.as_ref().map(|p| p.number),
            default_branch: repo.default_branch.clone(),
            claimed_at: now,
        };
        prepare(s, &claim)?;
        s.store.save(&claim)?;
        return Ok(ClaimOutcome::Claimed(claim));
    }
    Ok(ClaimOutcome::Nothing(format!(
        "no open issue for `{agent}` is waiting on it"
    )))
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

/// Check out the branch the claimed stage works on.
fn prepare(s: Services<'_>, claim: &Claim) -> Result<()> {
    let ws = s.workspace;
    ws.fetch().context("git fetch failed")?;
    match claim.stage {
        Stage::Spec => switch_to(s, &claim.default_branch)?,
        Stage::Tdd => {
            if ws.has_local_branch(&claim.branch)? {
                switch_to(s, &claim.branch)?;
            } else if ws.has_remote_branch(&claim.branch)? {
                ws.create_branch(&claim.branch, &format!("origin/{}", claim.branch))?;
            } else {
                ws.create_branch(&claim.branch, &format!("origin/{}", claim.default_branch))?;
            }
        }
        Stage::Review => ws.detach(&format!("origin/{}", claim.branch))?,
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
