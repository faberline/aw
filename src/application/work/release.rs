use anyhow::{Result, bail};

use super::claim::switch_to;
use super::services::Services;
use crate::domain::work::claim::Claim;

/// The active claim, if any.
pub fn status(s: Services<'_>) -> Result<Option<Claim>> {
    Ok(s.store.load()?)
}

/// Drop the claim without reporting. The issue stays eligible, so the
/// next `aw claim` may take it again.
pub fn release(s: Services<'_>) -> Result<(Claim, Vec<String>)> {
    let Some(claim) = s.store.load()? else {
        bail!("nothing is claimed");
    };
    let warnings = finish(s, &claim)?;
    Ok((claim, warnings))
}

/// Go back to the default branch when the tree is clean and drop the claim.
pub(crate) fn finish(s: Services<'_>, claim: &Claim) -> Result<Vec<String>> {
    let mut warnings = Vec::new();
    match s.workspace.is_clean() {
        Ok(true) => {
            if let Err(err) = switch_to(s, &claim.default_branch) {
                warnings.push(format!(
                    "could not switch back to `{}`: {err:#}",
                    claim.default_branch
                ));
            }
        }
        Ok(false) => warnings.push(format!(
            "the working tree has changes; not switching back to `{}`",
            claim.default_branch
        )),
        Err(err) => warnings.push(format!("could not read git status: {err}")),
    }
    s.store.clear()?;
    Ok(warnings)
}
