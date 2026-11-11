//! Composition root: wire the real adapters into the use cases.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use crate::application::ci::CiServices;
use crate::application::release::ReleaseServices;
use crate::application::run::RunServices;
use crate::application::work::Services;
use crate::domain::ci::ports::{BranchRules, CiFiles};
use crate::infrastructure::ci::files::origin_repo;
use crate::infrastructure::ci::{GhBranchRules, GitHubFiles, LocalFiles};
use crate::infrastructure::release::{GhRelease, YamlWorkflows};
use crate::infrastructure::run::LedgerFile;
use crate::infrastructure::work::{GhCli, GitCli, SystemClock};
use crate::interfaces::work::cli::{self, CiTarget, Exit, ReleaseCall};

pub fn run() -> ExitCode {
    let parsed = match cli::parse(std::env::args_os()) {
        Ok(parsed) => parsed,
        Err(exit) => return exit_code(exit),
    };
    if let Some(target) = parsed.ci_target() {
        return exit_code(ci_check(target));
    }
    if let Some((repo, call)) = parsed.release_call() {
        return exit_code(release_gate(repo, &call));
    }
    let tracker = GhCli::new(parsed.repo());
    let workspace = GitCli;
    let clock = SystemClock;
    let services = Services {
        tracker: &tracker,
        workspace: &workspace,
        clock: &clock,
    };
    let root = workers_dir();
    let ledger = LedgerFile::in_dir(&root);
    let runs = RunServices {
        ledger: &ledger,
        clock: &clock,
        root: &root,
    };
    exit_code(cli::run(services, runs, parsed))
}

/// `aw ci check`: local files from `--dir` (default `.`), or GitHub files
/// when only `--repo` is given. The ruleset needs a GitHub repository.
fn ci_check(target: &CiTarget) -> Exit {
    let (files, repo): (Box<dyn CiFiles>, Option<String>) = match (&target.dir, &target.repo) {
        (None, Some(repo)) => (Box::new(GitHubFiles::new(repo.clone())), Some(repo.clone())),
        (dir, repo) => {
            let dir = dir.clone().unwrap_or_else(|| PathBuf::from("."));
            let repo = repo.clone().or_else(|| origin_repo(&dir));
            (Box::new(LocalFiles::new(dir)), repo)
        }
    };
    let rules = match (target.no_ruleset, repo) {
        (true, _) => None,
        (false, Some(repo)) => Some(GhBranchRules::new(repo)),
        (false, None) => {
            eprintln!("aw: no GitHub repository for the ruleset; pass --repo or --no-ruleset");
            return Exit::Failed;
        }
    };
    cli::run_ci(CiServices {
        files: files.as_ref(),
        rules: rules.as_ref().map(|r| r as &dyn BranchRules),
    })
}

/// The milestone release gate, for `--repo` or the `origin` of the current
/// checkout.
fn release_gate(repo: Option<String>, call: &ReleaseCall) -> Exit {
    let Some(repo) = repo.or_else(|| origin_repo(Path::new("."))) else {
        eprintln!("aw: no GitHub repository; pass --repo");
        return Exit::Failed;
    };
    let provider = GhRelease::new(repo);
    cli::run_release(
        ReleaseServices {
            provider: &provider,
            workflows: &YamlWorkflows,
        },
        call,
    )
}

fn exit_code(exit: Exit) -> ExitCode {
    match exit {
        Exit::Ok => ExitCode::SUCCESS,
        Exit::Nothing => ExitCode::from(2),
        Exit::Failed => ExitCode::FAILURE,
    }
}

/// `AW_WORKERS_DIR`, else `~/faberlines/.aw-workers`: the worker clones and
/// the run ledger.
fn workers_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("AW_WORKERS_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join("faberlines").join(".aw-workers")
}
