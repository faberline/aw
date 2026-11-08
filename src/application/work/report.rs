use anyhow::{Context, Result, bail};
use serde::Serialize;

use super::active::{active, in_checkout};
use super::release::finish;
use super::services::Services;
use crate::domain::work::claim::Claim;
use crate::domain::work::pull_request::{body_closing, find_for_issue};
use crate::domain::work::signal::{Outcome, Signal};
use crate::domain::work::stage::Stage;

#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct Reported {
    pub issue: u64,
    pub stage: Stage,
    pub outcome: Outcome,
    /// The pull request the report points at, when there is one.
    pub link: Option<String>,
    /// review passed, but the branch was behind the base branch: `aw`
    /// updated it instead of merging. The issue stays at review; the next
    /// review run merges after CI.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub updated_branch: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

/// Finish the claimed stage: do its GitHub side effect and turn the claim
/// comment into the signal.
/// With a `stage`, the claim for that stage.
pub fn report(
    s: Services<'_>,
    agent: &str,
    stage: Option<Stage>,
    outcome: Outcome,
    message: &str,
) -> Result<Reported> {
    let Some(held) = active(s, agent, stage)? else {
        bail!("`{agent}` holds no claim; run `aw claim` first");
    };
    let claim = held.claim;
    if !outcome.allowed_in(claim.stage) {
        let allowed: Vec<&str> = Outcome::allowed(claim.stage)
            .iter()
            .map(|o| o.as_str())
            .collect();
        bail!(
            "`{outcome}` does not apply to {}; use one of: {}",
            claim.stage,
            allowed.join(", ")
        );
    }
    let message = message.trim();
    if outcome.needs_message() && message.is_empty() {
        bail!("`{outcome}` needs a message (-m or -F)");
    }

    let mut warnings = Vec::new();
    let link = match (claim.stage, outcome) {
        (Stage::Tdd, Outcome::Ready) => Some(open_pr(s, &claim, message)?),
        (Stage::Review, Outcome::Passed) => match merge(s, &claim, message, &mut warnings)? {
            Merge::Merged(url) => Some(url),
            Merge::Updated(url) => {
                return updated(s, held.comment_id, &claim, message, url, warnings);
            }
        },
        (Stage::Review, Outcome::Failed) => Some(request_changes(s, &claim, message)?),
        _ => None,
    };

    let signal = Signal {
        stage: claim.stage,
        agent: claim.agent.clone(),
        outcome,
    };
    let note = match (claim.stage, outcome) {
        // The pull request body already holds the tdd message.
        (Stage::Tdd, Outcome::Ready) => "",
        _ => message,
    };
    s.tracker
        .edit_comment(held.comment_id, &signal.render(note, link.as_deref()))
        .with_context(|| format!("could not post the signal on issue #{}", claim.issue))?;

    move_labels(s, &claim, outcome, &mut warnings);
    warnings.extend(finish(s, &claim)?);
    Ok(Reported {
        issue: claim.issue,
        stage: claim.stage,
        outcome,
        link,
        updated_branch: false,
        warnings,
    })
}

/// review passed on a branch behind the base branch, and `aw` updated it.
/// Replace the claim with a note that is not a signal, keep the stage
/// label, and let the next review run merge after CI.
fn updated(
    s: Services<'_>,
    comment_id: u64,
    claim: &Claim,
    message: &str,
    url: String,
    mut warnings: Vec<String>,
) -> Result<Reported> {
    let base = &claim.default_branch;
    let mut note = format!(
        "**aw** · `{}` · review · **branch updated**\n\n{url}\n\n\
         Review passed, but the branch was behind `{base}`. `aw` merged `{base}` \
         into it. CI runs again; the next review run merges when CI passes.",
        claim.agent
    );
    if !message.is_empty() {
        note.push_str("\n\n");
        note.push_str(message);
    }
    s.tracker
        .edit_comment(comment_id, &note)
        .with_context(|| format!("could not post the note on issue #{}", claim.issue))?;
    warnings.push(format!(
        "#{} was behind `{base}`; updated the branch instead of merging. \
         The issue stays at review until CI passes.",
        claim.issue
    ));
    warnings.extend(finish(s, claim)?);
    Ok(Reported {
        issue: claim.issue,
        stage: claim.stage,
        outcome: Outcome::Passed,
        link: Some(url),
        updated_branch: true,
        warnings,
    })
}

/// Put the issue on its next stage, so the next claim can take it at once.
/// The signal is already posted; a label failure is only a warning.
fn move_labels(s: Services<'_>, claim: &Claim, outcome: Outcome, warnings: &mut Vec<String>) {
    let change = outcome.label_move(claim.stage);
    if let Err(err) = s.tracker.add_labels(claim.issue, &change.add) {
        warnings.push(format!(
            "could not add {} to #{}: {err}",
            change.add.join(", "),
            claim.issue
        ));
        return;
    }
    if let Some(old) = change.remove
        && let Err(err) = s.tracker.remove_label(claim.issue, &old)
    {
        warnings.push(format!(
            "could not remove {old} from #{}: {err}",
            claim.issue
        ));
    }
}

fn pr_url(s: Services<'_>, claim: &Claim) -> Result<Option<(u64, String)>> {
    let prs = s.tracker.open_prs()?;
    Ok(find_for_issue(&prs, claim.issue).map(|p| (p.number, p.url.clone())))
}

/// Push the work branch and open its pull request, or update the body of
/// the one that is already open.
fn open_pr(s: Services<'_>, claim: &Claim, message: &str) -> Result<String> {
    if !in_checkout(s, &claim.repo)? {
        bail!(
            "run `aw report ready` in the clone of {} that holds the work branch",
            claim.repo
        );
    }
    if !s.workspace.is_clean()? {
        bail!("commit your changes before `aw report ready`; only commits are pushed");
    }
    s.workspace
        .push(&claim.branch)
        .with_context(|| format!("could not push `{}`", claim.branch))?;
    let prs = s.tracker.open_prs()?;
    let existing = prs.iter().find(|p| p.head == claim.branch);
    match existing {
        Some(pr) => {
            if !message.is_empty() {
                s.tracker
                    .edit_pr_body(pr.number, &body_closing(claim.issue, message))?;
            }
            Ok(pr.url.clone())
        }
        None => {
            let pr = s.tracker.create_pr(
                &claim.branch,
                &claim.default_branch,
                &claim.title,
                &body_closing(claim.issue, message),
            )?;
            Ok(pr.url)
        }
    }
}

fn reviewed_pr(s: Services<'_>, claim: &Claim) -> Result<(u64, String)> {
    let Some(number) = claim.pr else {
        bail!("the claim has no pull request");
    };
    let url = pr_url(s, claim)?
        .filter(|(n, _)| *n == number)
        .map(|(_, url)| url)
        .with_context(|| format!("pull request #{number} is no longer open"))?;
    Ok((number, url))
}

enum Merge {
    Merged(String),
    /// The merge was refused and the branch was behind the base branch;
    /// `aw` updated the branch instead.
    Updated(String),
}

fn merge(
    s: Services<'_>,
    claim: &Claim,
    message: &str,
    warnings: &mut Vec<String>,
) -> Result<Merge> {
    let (number, url) = reviewed_pr(s, claim)?;
    let subject = format!("{} (#{number})", claim.title);
    let body = if message.is_empty() {
        format!("Closes #{}", claim.issue)
    } else {
        message.to_string()
    };
    let Err(err) = s.tracker.squash_merge(number, &subject, &body) else {
        return Ok(Merge::Merged(url));
    };
    // A strict ruleset refuses a branch that is behind the base branch.
    // Update it, so CI runs on the current base, instead of failing review.
    let head = reviewed_head(s, number)?;
    match s.tracker.behind_by(&claim.default_branch, &head) {
        Ok(0) => {}
        Ok(_) => {
            s.tracker.update_branch(number).with_context(|| {
                format!("could not merge #{number} ({err}) or update its branch")
            })?;
            return Ok(Merge::Updated(url));
        }
        Err(cmp) => warnings.push(format!("could not compare #{number} with its base: {cmp}")),
    }
    Err(anyhow::Error::new(err).context(format!("could not merge #{number}")))
}

fn reviewed_head(s: Services<'_>, number: u64) -> Result<String> {
    s.tracker
        .open_prs()?
        .into_iter()
        .find(|p| p.number == number)
        .map(|p| p.head)
        .with_context(|| format!("pull request #{number} is no longer open"))
}

fn request_changes(s: Services<'_>, claim: &Claim, message: &str) -> Result<String> {
    let (number, url) = reviewed_pr(s, claim)?;
    if s.tracker.request_changes(number, message).is_err() {
        // GitHub refuses a change request on your own pull request; a
        // comment still carries the review.
        s.tracker
            .pr_comment(number, message)
            .with_context(|| format!("could not review #{number}"))?;
    }
    Ok(url)
}
