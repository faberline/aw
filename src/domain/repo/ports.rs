//! What the hygiene check needs from the outside world.

use crate::domain::work::ports::PortResult;

/// One tracked file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    /// Slash path from the top of the checkout.
    pub path: String,
    pub size: u64,
}

/// A path that a rule lets through, and why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Allow {
    /// A file, or a folder: every path below it.
    pub path: String,
    pub reason: String,
}

impl Allow {
    pub fn covers(&self, path: &str) -> bool {
        let base = self.path.trim_end_matches('/');
        path == base || path.strip_prefix(base).is_some_and(|r| r.starts_with('/'))
    }
}

/// The `[hygiene]` table of `aw.toml`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Config {
    /// Root entries that the repo adds to the built-in list (RH1).
    pub root: Vec<Allow>,
    /// Folders that may hold dated and per-issue files (RH4).
    pub evidence: Vec<Allow>,
    /// Files that may hold an absolute home path (RH3).
    pub home_path: Vec<Allow>,
    /// Files that may be larger than the limit (RH5).
    pub size: Vec<Allow>,
    /// The largest tracked file in bytes (RH5).
    pub max_file_bytes: u64,
    /// Known violations, each `<rule>:<path>`. The list only shrinks.
    pub baseline: Vec<String>,
}

pub const DEFAULT_MAX_FILE_BYTES: u64 = 256 * 1024;

impl Default for Config {
    fn default() -> Self {
        Config {
            root: Vec::new(),
            evidence: Vec::new(),
            home_path: Vec::new(),
            size: Vec::new(),
            max_file_bytes: DEFAULT_MAX_FILE_BYTES,
            baseline: Vec::new(),
        }
    }
}

/// The tracked files of a checkout and its configuration.
pub trait RepoFiles {
    fn config(&self) -> PortResult<Config>;
    /// Every file that `git ls-files` lists.
    fn entries(&self) -> PortResult<Vec<Entry>>;
    /// The text of a tracked file; `None` for a binary or missing file.
    fn text(&self, path: &str) -> PortResult<Option<String>>;
}
