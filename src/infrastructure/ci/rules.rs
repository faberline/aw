//! The required status checks of a GitHub default branch.

use std::collections::BTreeSet;

use crate::domain::ci::ports::BranchRules;
use crate::domain::work::ports::PortResult;
use crate::infrastructure::work::process::run;

/// Repository rulesets through `gh api` (REST). Classic branch protection
/// is not read: the faberline repositories use rulesets.
pub struct GhBranchRules {
    repo: String,
}

impl GhBranchRules {
    /// `repo` is `owner/name`.
    pub fn new(repo: impl Into<String>) -> Self {
        GhBranchRules { repo: repo.into() }
    }
}

/// One context per line, as the `--jq` filter prints them.
fn contexts(text: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(str::to_string)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

impl BranchRules for GhBranchRules {
    fn required_checks(&self) -> PortResult<Vec<String>> {
        let repo = format!("repos/{}", self.repo);
        let branch = run("gh", &["api", &repo, "--jq", ".default_branch"], None)?;
        let endpoint = format!("{repo}/rules/branches/{}", branch.trim());
        let text = run(
            "gh",
            &[
                "api",
                "--paginate",
                &endpoint,
                "--jq",
                r#".[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context"#,
            ],
            None,
        )?;
        Ok(contexts(&text))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contexts_are_unique_and_sorted() {
        assert_eq!(contexts("gate\n\nlint\ngate\n"), ["gate", "lint"]);
        assert!(contexts("").is_empty());
    }
}
