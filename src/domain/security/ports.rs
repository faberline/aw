//! What the security check needs from the outside world.

use crate::domain::work::ports::PortResult;

/// A rule that a repository opts out of, with a reason and an end date.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exception {
    /// `SEC1` to `SEC6`.
    pub rule: String,
    /// A file, or a folder: every path below it. `None` covers the rule.
    pub path: Option<String>,
    pub reason: String,
    /// `YYYY-MM-DD`. The exception stops on this day.
    pub expires: String,
}

impl Exception {
    pub fn covers(&self, rule: &str, path: &str, today: &str) -> bool {
        if self.rule != rule || self.expires.as_str() < today {
            return false;
        }
        match &self.path {
            None => true,
            Some(p) => {
                let base = p.trim_end_matches('/');
                path == base || path.strip_prefix(base).is_some_and(|r| r.starts_with('/'))
            }
        }
    }
}

/// The `[standards.security]` table of `aw.toml`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Config {
    /// Image name prefixes that the repo builds itself in the same run (SEC3).
    pub built_images: Vec<String>,
    pub exceptions: Vec<Exception>,
}

/// The tracked files of a checkout and its configuration.
pub trait SecurityFiles {
    fn config(&self) -> PortResult<Config>;
    /// Every tracked path (`git ls-files`), slash paths.
    fn paths(&self) -> PortResult<Vec<String>>;
    /// The text of a tracked file; `None` for a binary or missing file.
    fn text(&self, path: &str) -> PortResult<Option<String>>;
    /// The repository name, for example `lumen`.
    fn repo_name(&self) -> String;
    /// Today, `YYYY-MM-DD`, UTC.
    fn today(&self) -> String;
}
