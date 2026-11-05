use std::path::PathBuf;

use super::process::{run, succeeds};
use crate::domain::work::ports::{PortResult, Workspace};

/// The git checkout in the current directory.
#[derive(Default)]
pub struct GitCli;

impl GitCli {
    pub fn git_dir(&self) -> PortResult<PathBuf> {
        Ok(PathBuf::from(
            run("git", &["rev-parse", "--absolute-git-dir"], None)?.trim(),
        ))
    }

    fn git(&self, args: &[&str]) -> PortResult<()> {
        run("git", args, None).map(|_| ())
    }
}

impl Workspace for GitCli {
    fn is_clean(&self) -> PortResult<bool> {
        Ok(run(
            "git",
            &["status", "--porcelain", "--untracked-files=all"],
            None,
        )?
        .trim()
        .is_empty())
    }

    fn fetch(&self) -> PortResult<()> {
        self.git(&["fetch", "--prune", "--quiet", "origin"])
    }

    fn has_local_branch(&self, branch: &str) -> PortResult<bool> {
        succeeds(
            "git",
            &[
                "rev-parse",
                "--verify",
                "--quiet",
                &format!("refs/heads/{branch}"),
            ],
        )
    }

    fn has_remote_branch(&self, branch: &str) -> PortResult<bool> {
        succeeds(
            "git",
            &[
                "rev-parse",
                "--verify",
                "--quiet",
                &format!("refs/remotes/origin/{branch}"),
            ],
        )
    }

    fn switch(&self, branch: &str) -> PortResult<()> {
        self.git(&["switch", "--quiet", branch])
    }

    fn create_branch(&self, branch: &str, start: &str) -> PortResult<()> {
        self.git(&["switch", "--quiet", "--no-track", "-c", branch, start])
    }

    fn fast_forward(&self, upstream: &str) -> PortResult<()> {
        self.git(&["merge", "--ff-only", "--quiet", upstream])
    }

    fn detach(&self, start: &str) -> PortResult<()> {
        self.git(&["switch", "--quiet", "--detach", start])
    }

    fn push(&self, branch: &str) -> PortResult<()> {
        self.git(&[
            "push",
            "--quiet",
            "origin",
            &format!("HEAD:refs/heads/{branch}"),
        ])
    }
}
