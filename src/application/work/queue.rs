//! The review queue: bring the branch of the next pull request up to date.

use anyhow::Result;

use super::report::{merge_ready, read_required};
use super::services::Services;
use crate::domain::work::issue::EXPEDITE_LABEL;
use crate::domain::work::pull_request::{PullRequest, find_for_issue};
use crate::domain::work::stage::Stage;

/// Update the branch of the next review pull request that is behind the
/// base branch, so its CI runs on the current base. Returns its number.
///
/// The order is the `expedite` label, then the priority label, then the
/// oldest issue. The rule "one update for each repo at a time" holds: when a
/// pull request that comes first is up to date and can merge (its required
/// checks are green or still run), nothing is updated. A pull request with a
/// failed check is passed over: review sends it back to tdd, and a new CI
/// run would cost money for a known failure. `skip` is the issue that the run
/// just merged.
///
/// This is best effort. The merge or the claim that called it has already
/// done its work, so a failure is a warning and not an error.
pub(super) fn update_next(
    s: Services<'_>,
    base: &str,
    skip: Option<u64>,
    warnings: &mut Vec<String>,
) -> Option<u64> {
    match try_update_next(s, base, skip, warnings) {
        Ok(next) => next,
        Err(err) => {
            warnings.push(format!("could not update the next review branch: {err:#}"));
            None
        }
    }
}

fn try_update_next(
    s: Services<'_>,
    base: &str,
    skip: Option<u64>,
    warnings: &mut Vec<String>,
) -> Result<Option<u64>> {
    let queue = queue(s, skip)?;
    if queue.is_empty() {
        return Ok(None);
    }
    let required = read_required(s, base, warnings);
    for pr in queue {
        match s.tracker.behind_by(base, &pr.head) {
            Ok(0) => match merge_ready(s, &pr.head, required.as_deref()) {
                // It merges next, or its CI runs: an update is in flight.
                Ok(true) => return Ok(None),
                Ok(false) => {}
                Err(err) => {
                    warnings.push(format!(
                        "could not read the checks of #{}: {err}",
                        pr.number
                    ));
                    return Ok(None);
                }
            },
            Ok(_) => {
                match s.tracker.has_failed_checks(&pr.head) {
                    Ok(true) => continue,
                    Ok(false) => {}
                    Err(err) => {
                        warnings.push(format!(
                            "could not read the checks of #{}: {err}",
                            pr.number
                        ));
                        return Ok(None);
                    }
                }
                if let Err(err) = s.tracker.update_branch(pr.number) {
                    warnings.push(format!(
                        "could not update the branch of #{}: {err}",
                        pr.number
                    ));
                    return Ok(None);
                }
                return Ok(Some(pr.number));
            }
            Err(err) => warnings.push(format!(
                "could not compare #{} with its base: {err}",
                pr.number
            )),
        }
    }
    Ok(None)
}

/// The open pull requests of the `stage:review` issues that are not
/// `blocked`, in queue order: `expedite`, priority, oldest issue.
fn queue(s: Services<'_>, skip: Option<u64>) -> Result<Vec<PullRequest>> {
    let mut issues = s.tracker.open_issues(&format!("stage:{}", Stage::Review))?;
    issues.retain(|i| Some(i.number) != skip && !i.has_label("blocked"));
    if issues.is_empty() {
        return Ok(Vec::new());
    }
    issues.sort_by_cached_key(|i| {
        (
            !i.has_label(EXPEDITE_LABEL),
            i.priority(),
            i.created_at.clone(),
            i.number,
        )
    });
    let prs = s.tracker.open_prs()?;
    Ok(issues
        .iter()
        .filter_map(|i| find_for_issue(&prs, i.number).cloned())
        .collect())
}
