//! The source of `aw supervise --check`: GET calls through `gh api` only.

use crate::domain::desk::facts::RepoFacts;
use crate::domain::desk::ports::{DeskSource, ReadError};
use crate::domain::supervise::SuperviseSource;
use crate::infrastructure::desk::{Api, GhDesk};

impl<A: Api> SuperviseSource for GhDesk<A> {
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError> {
        DeskSource::facts(self, repo)
    }

    fn running(&self, repo: &str) -> Result<Vec<String>, ReadError> {
        let mut ids = Vec::new();
        for status in ["queued", "in_progress"] {
            for run in self.rows(&format!("repos/{repo}/actions/runs?status={status}"))? {
                ids.push(run["id"].as_u64().unwrap_or(0).to_string());
            }
        }
        Ok(ids)
    }

    fn board(&self, board_repo: &str, title: &str) -> Result<Option<String>, ReadError> {
        let rows = self.rows(&format!(
            "repos/{board_repo}/issues?state=open&labels=status"
        ))?;
        Ok(rows
            .iter()
            .find(|r| r["title"].as_str() == Some(title))
            .map(|r| r["body"].as_str().unwrap_or("").to_string()))
    }
}
