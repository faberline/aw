//! Read `ci.yml` and `aw.toml` from a local checkout or from GitHub.

use std::io::ErrorKind;
use std::path::PathBuf;

use super::parse;
use crate::domain::ci::ports::CiFiles;
use crate::domain::ci::workflow::{Exception, Workflow};
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// The workflow path that the standard fixes.
pub const WORKFLOW_PATH: &str = ".github/workflows/ci.yml";
/// The file that holds the exceptions.
pub const AW_TOML: &str = "aw.toml";

/// Files in a directory on this machine.
pub struct LocalFiles {
    dir: PathBuf,
}

impl LocalFiles {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        LocalFiles { dir: dir.into() }
    }

    fn read(&self, path: &str) -> PortResult<Option<String>> {
        let full = self.dir.join(path);
        match std::fs::read_to_string(&full) {
            Ok(text) => Ok(Some(text)),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
            Err(e) => Err(PortError(format!("could not read {}: {e}", full.display()))),
        }
    }
}

impl CiFiles for LocalFiles {
    fn workflow(&self) -> PortResult<Option<Workflow>> {
        self.read(WORKFLOW_PATH)?
            .map(|text| parse::workflow(&text))
            .transpose()
    }

    fn exceptions(&self) -> PortResult<Vec<Exception>> {
        Ok(match self.read(AW_TOML)? {
            Some(text) => parse::exceptions(&text)?,
            None => Vec::new(),
        })
    }
}

/// Files on the default branch of a GitHub repository, through `gh api`.
pub struct GitHubFiles {
    repo: String,
}

impl GitHubFiles {
    /// `repo` is `owner/name`.
    pub fn new(repo: impl Into<String>) -> Self {
        GitHubFiles { repo: repo.into() }
    }

    fn read(&self, path: &str) -> PortResult<Option<String>> {
        let endpoint = format!("repos/{}/contents/{path}", self.repo);
        match run(
            "gh",
            &["api", &endpoint, "-H", "Accept: application/vnd.github.raw"],
            None,
        ) {
            Ok(text) => Ok(Some(text)),
            Err(e) if e.0.contains("404") => Ok(None),
            Err(e) => Err(e),
        }
    }
}

impl CiFiles for GitHubFiles {
    fn workflow(&self) -> PortResult<Option<Workflow>> {
        self.read(WORKFLOW_PATH)?
            .map(|text| parse::workflow(&text))
            .transpose()
    }

    fn exceptions(&self) -> PortResult<Vec<Exception>> {
        Ok(match self.read(AW_TOML)? {
            Some(text) => parse::exceptions(&text)?,
            None => Vec::new(),
        })
    }
}

/// `owner/name` of the `origin` remote of the checkout at `dir`, if any.
pub fn origin_repo(dir: &std::path::Path) -> Option<String> {
    let dir = dir.to_str()?;
    run("git", &["-C", dir, "remote", "get-url", "origin"], None)
        .ok()
        .and_then(|url| crate::infrastructure::work::git::github_repo(&url))
}
