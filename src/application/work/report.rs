use anyhow::{Context, Result, bail};
use serde::Serialize;

use super::active::{active, in_checkout};
use super::release::finish;
use super::services::Services;
use crate::domain::work::claim::Claim;
use crate::domain::work::ports::{RequiredChecks, judge_required};
use crate::domain::work::pull_request::{body_closing, find_for_issue};
use crate::domain::work::signal::{Outcome, Signal, names_spec_gap};
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
    /// review passed and the branch was behind, but another review pull
    /// request already ran CI on the current base: `aw` left this branch as
    /// it is, so the two do not race. The next review run tries again.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub waiting_for: Option<u64>,
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
    // tdd cannot fix a spec, but the spec stage can: no person is needed.
    // A `blocked` label here would leave the issue where no routine takes it.
    let outcome =
        if claim.stage == Stage::Tdd && outcome == Outcome::Blocked && names_spec_gap(message) {
            warnings.push(format!(
                "#{}: the `blocked` message says the spec is the cause; reported `respec` \
                 instead, so the issue goes back to stage:spec without `blocked`",
                claim.issue
            ));
            Outcome::Respec
        } else {
            outcome
        };
    let link = match (claim.stage, outcome) {
        (Stage::Tdd, Outcome::Ready) => Some(open_pr(s, &claim, message)?),
        (Stage::Review, Outcome::Passed) => match merge(s, &claim, message, &mut warnings)? {
            Merge::Merged(url) => Some(url),
            Merge::Updated(url) => {
                return updated(s, held.comment_id, &claim, message, url, warnings);
            }
            Merge::Waiting { url, holder } => {
                return waiting(s, held.comment_id, &claim, message, url, holder, warnings);
            }
        },
        (Stage::Review, Outcome::Failed) => Some(request_changes(s, &claim, message)?),
        (Stage::Review, Outcome::Closed) => Some(close(s, &claim, message, &mut warnings)?),
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
        waiting_for: None,
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
    if let Some(unread) = warnings.iter().find(|w| w.starts_with(REQUIRED_UNREAD)) {
        note.push_str(&format!("\n\nNote: {unread}."));
    }
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
        waiting_for: None,
        warnings,
    })
}

/// review passed on a branch behind the base branch, but another review
/// pull request is already up to date with the base. Leave this branch as
/// it is, replace the claim with a note that is not a signal, and keep the
/// stage label. The next review run tries again.
fn waiting(
    s: Services<'_>,
    comment_id: u64,
    claim: &Claim,
    message: &str,
    url: String,
    holder: u64,
    mut warnings: Vec<String>,
) -> Result<Reported> {
    let base = &claim.default_branch;
    let mut note = format!(
        "**aw** · `{}` · review · **waiting to update**\n\n{url}\n\n\
         Review passed, but pull request #{holder} goes first. It is up to date with \
         `{base}`, and every check the ruleset requires is on its head and not failed \
         (or it has a higher priority and waits for them). `aw` updates or merges this \
         branch when no such pull request is ahead; the next review run tries again.",
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
        "#{} waits for #{holder}, which is ahead in the queue; \
         left the branch as it is. The issue stays at review.",
        claim.issue
    ));
    warnings.extend(finish(s, claim)?);
    Ok(Reported {
        issue: claim.issue,
        stage: claim.stage,
        outcome: Outcome::Passed,
        link: Some(url),
        updated_branch: false,
        waiting_for: Some(holder),
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
    /// The branch was behind, but the review pull request `holder` is
    /// already up to date with the base branch; `aw` waits for it.
    Waiting {
        url: String,
        holder: u64,
    },
}

/// Close the reviewed pull request without a merge and delete its branch,
/// so the next tdd run starts again from the default branch.
fn close(
    s: Services<'_>,
    claim: &Claim,
    message: &str,
    warnings: &mut Vec<String>,
) -> Result<String> {
    let (number, url) = reviewed_pr(s, claim)?;
    let head = reviewed_head(s, number)?;
    s.tracker
        .pr_comment(number, message)
        .with_context(|| format!("could not comment on #{number}"))?;
    s.tracker
        .close_pr(number)
        .with_context(|| format!("could not close #{number}"))?;
    if head == claim.default_branch {
        warnings.push(format!("`{head}` is the default branch; it stays"));
    } else if let Err(err) = s.tracker.delete_branch(&head) {
        warnings.push(format!("could not delete the branch `{head}`: {err}"));
    }
    Ok(url)
}

/// The warning (and note text) when the required checks cannot be read.
/// The old rule stays in force then: any up-to-date pull request without a
/// failed check holds the others back.
const REQUIRED_UNREAD: &str = "could not read the required checks of the ruleset; \
     kept the old queue rule (an up-to-date pull request without a failed check goes first)";

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
    let required = read_required(s, claim, warnings);
    // A higher-priority pull request was just updated and its required
    // checks still run. Merging this one now would leave it behind again.
    if let Some(required) = required.as_deref()
        && let Some(holder) = higher_priority_running_pr(s, claim, number, required, warnings)?
    {
        return Ok(Merge::Waiting { url, holder });
    }
    let Err(err) = s.tracker.squash_merge(number, &subject, &body) else {
        return Ok(Merge::Merged(url));
    };
    // A strict ruleset refuses a branch that is behind the base branch.
    // Update it, so CI runs on the current base, instead of failing review.
    let head = reviewed_head(s, number)?;
    match s.tracker.behind_by(&claim.default_branch, &head) {
        Ok(0) => {}
        Ok(_) => {
            if let Some(holder) =
                up_to_date_review_pr(s, claim, number, required.as_deref(), warnings)?
            {
                return Ok(Merge::Waiting { url, holder });
            }
            s.tracker.update_branch(number).with_context(|| {
                format!("could not merge #{number} ({err}) or update its branch")
            })?;
            return Ok(Merge::Updated(url));
        }
        Err(cmp) => warnings.push(format!("could not compare #{number} with its base: {cmp}")),
    }
    Err(anyhow::Error::new(err).context(format!("could not merge #{number}")))
}

/// The check names the ruleset requires on the default branch, or `None`
/// (with a warning) when they cannot be read.
fn read_required(
    s: Services<'_>,
    claim: &Claim,
    warnings: &mut Vec<String>,
) -> Option<Vec<String>> {
    match s.tracker.required_checks(&claim.default_branch) {
        Ok(required) => Some(required),
        Err(err) => {
            warnings.push(format!("{REQUIRED_UNREAD}: {err}"));
            None
        }
    }
}

/// The other open pull requests of `stage:review` issues that are not
/// `blocked`, with the priority of their issue, most important first.
fn other_review_prs(
    s: Services<'_>,
    claim: &Claim,
    own: u64,
) -> Result<Vec<(u8, crate::domain::work::pull_request::PullRequest)>> {
    let issues = s.tracker.open_issues(&format!("stage:{}", Stage::Review))?;
    let prs = s.tracker.open_prs()?;
    let mut found = Vec::new();
    for issue in issues {
        if issue.number == claim.issue || issue.has_label("blocked") {
            continue;
        }
        let Some(pr) = find_for_issue(&prs, issue.number) else {
            continue;
        };
        if pr.number != own {
            found.push((issue.priority(), pr.clone()));
        }
    }
    found.sort_by_key(|(priority, pr)| (*priority, pr.number));
    Ok(found)
}

fn own_priority(s: Services<'_>, claim: &Claim) -> Result<u8> {
    Ok(s.tracker
        .open_issues(&format!("stage:{}", Stage::Review))?
        .iter()
        .find(|i| i.number == claim.issue)
        .map_or(4, |i| i.priority()))
}

/// Another open pull request of a `stage:review` issue that is up to date
/// with the base branch and can merge next: every check the ruleset
/// requires exists on its head and none failed (a required check may still
/// run). Its CI runs on the current base, so it merges next. Updating a
/// second branch now only races it: whichever merges first leaves the other
/// behind, and its CI ran for nothing.
///
/// A pull request with a required check missing or failed cannot merge, so
/// it does not hold the others back. When the required checks cannot be
/// read (`required` is `None`), only a failed check frees the others.
///
/// A pull request of a lower priority label does not hold back this one:
/// a `p0` branch is updated at once, and the race costs the lower one a CI run.
fn up_to_date_review_pr(
    s: Services<'_>,
    claim: &Claim,
    own: u64,
    required: Option<&[String]>,
    warnings: &mut Vec<String>,
) -> Result<Option<u64>> {
    let own_priority = own_priority(s, claim)?;
    for (priority, pr) in other_review_prs(s, claim, own)? {
        if priority > own_priority {
            continue;
        }
        match s.tracker.behind_by(&claim.default_branch, &pr.head) {
            Ok(0) => match merge_ready(s, &pr.head, required) {
                Ok(true) => return Ok(Some(pr.number)),
                Ok(false) => {}
                // When its checks cannot be read, wait.
                Err(err) => {
                    warnings.push(format!(
                        "could not read the checks of #{}: {err}",
                        pr.number
                    ));
                    return Ok(Some(pr.number));
                }
            },
            Ok(_) => {}
            Err(err) => warnings.push(format!(
                "could not compare #{} with its base: {err}",
                pr.number
            )),
        }
    }
    Ok(None)
}

/// The head can merge once its running checks pass.
fn merge_ready(s: Services<'_>, head: &str, required: Option<&[String]>) -> Result<bool> {
    match required {
        None => Ok(!s.tracker.has_failed_checks(head)?),
        Some(required) => {
            let runs = s.tracker.check_runs(head)?;
            Ok(judge_required(required, &runs) != RequiredChecks::Blocked)
        }
    }
}

/// A pull request of a strictly higher priority that is up to date and
/// waits for its required checks to finish. `aw` updated it a moment ago;
/// a merge of this pull request would make it behind again.
fn higher_priority_running_pr(
    s: Services<'_>,
    claim: &Claim,
    own: u64,
    required: &[String],
    warnings: &mut Vec<String>,
) -> Result<Option<u64>> {
    if required.is_empty() {
        return Ok(None);
    }
    let own_priority = own_priority(s, claim)?;
    for (priority, pr) in other_review_prs(s, claim, own)? {
        if priority >= own_priority {
            continue;
        }
        if !matches!(s.tracker.behind_by(&claim.default_branch, &pr.head), Ok(0)) {
            continue;
        }
        match s.tracker.check_runs(&pr.head) {
            Ok(runs) if judge_required(required, &runs) == RequiredChecks::Running => {
                return Ok(Some(pr.number));
            }
            Ok(_) => {}
            Err(err) => warnings.push(format!(
                "could not read the checks of #{}: {err}",
                pr.number
            )),
        }
    }
    Ok(None)
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
