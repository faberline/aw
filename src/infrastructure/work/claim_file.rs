use std::cell::OnceCell;
use std::fs;
use std::io::ErrorKind;
use std::path::PathBuf;

use super::git::GitCli;
use crate::domain::work::claim::Claim;
use crate::domain::work::ports::{ClaimStore, PortError, PortResult};

/// The claim as JSON at `<git dir>/aw/claim.json`. Inside the git dir it is
/// per checkout (each worktree has its own) and never committed.
pub struct ClaimFile {
    path: OnceCell<PathBuf>,
    locate: Option<GitCli>,
}

impl ClaimFile {
    /// Find the file under the git dir of the current checkout, on first use.
    pub fn in_git_dir() -> Self {
        ClaimFile {
            path: OnceCell::new(),
            locate: Some(GitCli),
        }
    }

    pub fn at(path: PathBuf) -> Self {
        let cell = OnceCell::new();
        let _ = cell.set(path);
        ClaimFile {
            path: cell,
            locate: None,
        }
    }

    fn path(&self) -> PortResult<&PathBuf> {
        if let Some(path) = self.path.get() {
            return Ok(path);
        }
        let git = self
            .locate
            .as_ref()
            .ok_or_else(|| PortError("claim file has no location".into()))?;
        let path = git.git_dir()?.join("aw").join("claim.json");
        Ok(self.path.get_or_init(|| path))
    }
}

fn io(what: &str, err: std::io::Error) -> PortError {
    PortError(format!("{what}: {err}"))
}

impl ClaimStore for ClaimFile {
    fn load(&self) -> PortResult<Option<Claim>> {
        let path = self.path()?;
        match fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text)
                .map(Some)
                .map_err(|e| PortError(format!("{} is not a valid claim: {e}", path.display()))),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
            Err(e) => Err(io(&format!("could not read {}", path.display()), e)),
        }
    }

    fn save(&self, claim: &Claim) -> PortResult<()> {
        let path = self.path()?;
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir)
                .map_err(|e| io(&format!("could not create {}", dir.display()), e))?;
        }
        let text = serde_json::to_string_pretty(claim).map_err(|e| PortError(e.to_string()))?;
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, text + "\n")
            .map_err(|e| io(&format!("could not write {}", tmp.display()), e))?;
        fs::rename(&tmp, path).map_err(|e| io(&format!("could not write {}", path.display()), e))
    }

    fn clear(&self) -> PortResult<()> {
        let path = self.path()?;
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(()),
            Err(e) => Err(io(&format!("could not remove {}", path.display()), e)),
        }
    }
}
