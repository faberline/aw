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

/// `owner/name` from a GitHub remote URL in SSH or HTTPS form.
fn github_repo(url: &str) -> Option<String> {
    let url = url.trim();
    let path = url
        .strip_prefix("git@github.com:")
        .or_else(|| url.strip_prefix("ssh://git@github.com/"))
        .or_else(|| url.strip_prefix("https://github.com/"))
        .or_else(|| url.strip_prefix("http://github.com/"))?;
    let path = path.trim_end_matches('/');
    let path = path.strip_suffix(".git").unwrap_or(path);
    let (owner, name) = path.split_once('/')?;
    if owner.is_empty() || name.is_empty() || name.contains('/') {
        return None;
    }
    Some(format!("{owner}/{name}"))
}

impl Workspace for GitCli {
    fn origin_repo(&self) -> PortResult<Option<String>> {
        // Outside a checkout, or without `origin`, git fails: no repo.
        Ok(run("git", &["remote", "get-url", "origin"], None)
            .ok()
            .and_then(|url| github_repo(&url)))
    }

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

#[cfg(test)]
mod tests {
    use super::github_repo;

    #[test]
    fn reads_owner_and_name_from_github_urls() {
        for url in [
            "git@github.com:faberline/core.git",
            "git@github.com:faberline/core",
            "ssh://git@github.com/faberline/core.git",
            "https://github.com/faberline/core.git\n",
            "https://github.com/faberline/core/",
        ] {
            assert_eq!(github_repo(url).as_deref(), Some("faberline/core"), "{url}");
        }
    }

    #[test]
    fn other_hosts_are_not_github() {
        assert_eq!(github_repo("git@gitlab.com:o/r.git"), None);
        assert_eq!(github_repo("https://github.com/o"), None);
        assert_eq!(github_repo("/srv/git/r.git"), None);
    }
}
