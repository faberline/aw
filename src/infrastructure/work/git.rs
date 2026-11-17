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
pub(crate) fn github_repo(url: &str) -> Option<String> {
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

/// `git merge upstream` through `git`. A failed merge that left `MERGE_HEAD`
/// is a conflict: abort it and return `false`. A failed merge without
/// `MERGE_HEAD` has nothing to abort and is not a conflict (for example an
/// untracked file in the way, unrelated histories or a hook): its own
/// error goes up, so the cause is not lost behind `git merge --abort`.
fn merge_with(git: &dyn Fn(&[&str]) -> PortResult<String>, upstream: &str) -> PortResult<bool> {
    let merge = ["merge", "--no-ff", "--no-edit", "--quiet", upstream];
    let Err(error) = git(&merge) else {
        return Ok(true);
    };
    if git(&["rev-parse", "--quiet", "--verify", "MERGE_HEAD"]).is_err() {
        return Err(error);
    }
    git(&["merge", "--abort"])?;
    Ok(false)
}

/// `git fetch`, then complete the history when the clone is shallow. A
/// shallow clone (the cloud routines clone with a depth) cuts the history,
/// so `git merge origin/main` finds no merge base and fails with
/// "refusing to merge unrelated histories" (aw#552). The flag
/// `--allow-unrelated-histories` is never used.
fn fetch_with(git: &dyn Fn(&[&str]) -> PortResult<String>) -> PortResult<()> {
    git(&["fetch", "--prune", "--quiet", "origin"])?;
    // An old git without the flag fails here: treat it as not shallow.
    let shallow = git(&["rev-parse", "--is-shallow-repository"])
        .map(|out| out.trim() == "true")
        .unwrap_or(false);
    if shallow {
        git(&["fetch", "--unshallow", "--prune", "--quiet", "origin"])?;
    }
    Ok(())
}

impl Workspace for GitCli {
    fn origin_repo(&self) -> PortResult<Option<String>> {
        // Outside a checkout, or without `origin`, git fails: no repo.
        Ok(run("git", &["remote", "get-url", "origin"], None)
            .ok()
            .and_then(|url| github_repo(&url)))
    }

    fn current_branch(&self) -> PortResult<Option<String>> {
        // Detached HEAD, or outside a checkout: git fails, so no branch.
        Ok(
            run("git", &["symbolic-ref", "--short", "--quiet", "HEAD"], None)
                .ok()
                .map(|branch| branch.trim().to_string())
                .filter(|branch| !branch.is_empty()),
        )
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
        fetch_with(&|args| run("git", args, None))
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

    fn contains(&self, upstream: &str) -> PortResult<bool> {
        succeeds("git", &["merge-base", "--is-ancestor", upstream, "HEAD"])
    }

    fn merge(&self, upstream: &str) -> PortResult<bool> {
        merge_with(&|args| run("git", args, None), upstream)
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
    use std::cell::RefCell;

    use super::{GitCli, fetch_with, github_repo, merge_with};
    use crate::domain::work::ports::{PortError, PortResult, Workspace};

    /// A fake `git`: `merge` fails with `merge_error`, and `MERGE_HEAD`
    /// exists when `conflict` is set. Returns the commands it saw.
    fn merge_run(merge_error: Option<&str>, conflict: bool) -> (PortResult<bool>, Vec<String>) {
        let seen = RefCell::new(Vec::new());
        let git = |args: &[&str]| -> PortResult<String> {
            seen.borrow_mut().push(args.join(" "));
            match args[0] {
                "merge" if args[1] == "--abort" => Ok(String::new()),
                "merge" => merge_error
                    .map(|e| Err(PortError(e.into())))
                    .unwrap_or(Ok(String::new())),
                _ if conflict => Ok(String::new()),
                _ => Err(PortError("no MERGE_HEAD".into())),
            }
        };
        let result = merge_with(&git, "origin/main");
        (result, seen.into_inner())
    }

    #[test]
    fn a_clean_merge_runs_no_abort() {
        let (result, seen) = merge_run(None, false);
        assert!(result.unwrap());
        assert_eq!(seen.len(), 1);
    }

    #[test]
    fn a_conflict_is_aborted_and_is_not_an_error() {
        let (result, seen) = merge_run(Some("conflict"), true);
        assert!(!result.unwrap());
        assert_eq!(seen.last().unwrap(), "merge --abort");
    }

    #[test]
    fn a_failed_merge_without_merge_head_reports_its_own_error_and_does_not_abort() {
        let (result, seen) = merge_run(Some("untracked files would be overwritten"), false);
        assert_eq!(
            result.unwrap_err().0,
            "untracked files would be overwritten"
        );
        assert!(!seen.iter().any(|c| c == "merge --abort"), "{seen:?}");
    }

    fn fetch_run(shallow: &str) -> Vec<String> {
        let seen = RefCell::new(Vec::new());
        let git = |args: &[&str]| -> PortResult<String> {
            seen.borrow_mut().push(args.join(" "));
            Ok(if args[0] == "rev-parse" {
                shallow.into()
            } else {
                String::new()
            })
        };
        fetch_with(&git).unwrap();
        seen.into_inner()
    }

    #[test]
    fn a_shallow_clone_is_unshallowed_after_the_fetch() {
        let seen = fetch_run("true\n");
        assert!(
            seen.iter().any(|c| c.contains("fetch --unshallow")),
            "{seen:?}"
        );
        assert!(
            !seen.iter().any(|c| c.contains("allow-unrelated")),
            "{seen:?}"
        );
    }

    #[test]
    fn a_full_clone_is_not_unshallowed() {
        let seen = fetch_run("false\n");
        assert!(!seen.iter().any(|c| c.contains("unshallow")), "{seen:?}");
    }

    #[test]
    fn the_current_branch_is_a_name_or_none_for_a_detached_head() {
        // A checkout on a branch, or detached (CI checks out a merge
        // commit): never an empty name, never an error.
        let branch = GitCli.current_branch().unwrap();
        assert!(branch.is_none_or(|name| !name.is_empty() && !name.contains('\n')));
    }

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
