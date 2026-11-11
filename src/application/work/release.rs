use anyhow::{Context, Result, bail};

use super::active::{active, board, in_checkout};
use super::claim::switch_to;
use super::services::Services;
use crate::domain::work::claim::Claim;
use crate::domain::work::stage::Stage;

/// The claim `agent` holds in the repository, if any.
/// With a `stage`, only a claim for that stage.
pub fn status(s: Services<'_>, agent: &str, stage: Option<Stage>) -> Result<Option<Claim>> {
    Ok(board(s, agent)?.active(agent, stage).map(|a| a.claim))
}

/// Delete the claim comment without reporting. The issue stays eligible,
/// so the next `aw claim` may take it again.
pub fn release(s: Services<'_>, agent: &str, stage: Option<Stage>) -> Result<(Claim, Vec<String>)> {
    let Some(held) = active(s, agent, stage)? else {
        bail!("`{agent}` holds no claim");
    };
    s.tracker
        .delete_comment(held.comment_id)
        .with_context(|| format!("could not remove the claim on #{}", held.claim.issue))?;
    let warnings = finish(s, &held.claim)?;
    Ok((held.claim, warnings))
}

/// After a tdd claim, go back to the default branch when the tree is
/// clean. spec and review never changed the working tree.
pub(crate) fn finish(s: Services<'_>, claim: &Claim) -> Result<Vec<String>> {
    let mut warnings = Vec::new();
    if claim.stage != Stage::Tdd {
        return Ok(warnings);
    }
    if !in_checkout(s, &claim.repo)? {
        warnings.push(format!(
            "this directory is not a clone of {}; not switching branches",
            claim.repo
        ));
        return Ok(warnings);
    }
    match s.workspace.is_clean() {
        Ok(true) => {
            if let Err(err) = switch_to(s, &claim.default_branch) {
                // A linked worktree cannot switch to a branch that another
                // worktree has checked out. Leave the work branch anyway.
                let upstream = format!("origin/{}", claim.default_branch);
                match s.workspace.detach(&upstream) {
                    Ok(()) => warnings.push(format!(
                        "could not switch back to `{}` ({err:#}); detached at `{upstream}`",
                        claim.default_branch
                    )),
                    Err(detach_err) => warnings.push(format!(
                        "could not switch back to `{}`: {err:#}; could not detach at `{upstream}`: {detach_err:#}",
                        claim.default_branch
                    )),
                }
            }
        }
        Ok(false) => warnings.push(format!(
            "the working tree has changes; not switching back to `{}`",
            claim.default_branch
        )),
        Err(err) => warnings.push(format!("could not read git status: {err}")),
    }
    Ok(warnings)
}
