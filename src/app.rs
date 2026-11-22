//! Composition root: wire the real adapters into the use cases.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use crate::application::ci::CiServices;
use crate::application::meta::MetaServices;
use crate::application::release::ReleaseServices;
use crate::application::release_contract::ContractServices;
use crate::application::release_receipt::ReceiptServices;
use crate::application::repo::RepoServices;
use crate::application::run::RunServices;
use crate::application::security::SecurityServices;
use crate::application::standards::{RepoTarget, StandardsServices};
use crate::application::work::Services;
use crate::domain::ci::ports::{BranchRules, CiFiles};
use crate::domain::work::ports::Clock;
use crate::infrastructure::ci::files::origin_repo;
use crate::infrastructure::ci::{GhBranchRules, GitHubFiles, LocalFiles};
use crate::infrastructure::meta::LocalMeta;
use crate::infrastructure::release::{GhRelease, YamlWorkflows};
use crate::infrastructure::release_contract::LocalContract;
use crate::infrastructure::release_receipt::LocalFiles as ReceiptFiles;
use crate::infrastructure::repo::TrackedFiles;
use crate::infrastructure::run::LedgerFile;
use crate::infrastructure::standards::{LocalCore, LocalRepo, is_checkout};
use crate::infrastructure::work::{GhCli, GitCli, SystemClock};
use crate::interfaces::work::cli::{
    self, CiTarget, ContractTarget, Exit, HygieneTarget, MetaTarget, PatrolTarget, PromptTarget,
    ReceiptTarget, ReleaseCall, SecurityTarget, StandardsTarget,
};

pub fn run() -> ExitCode {
    let parsed = match cli::parse(std::env::args_os()) {
        Ok(parsed) => parsed,
        Err(exit) => return exit_code(exit),
    };
    if let Some(target) = parsed.ci_target() {
        return exit_code(ci_check(target));
    }
    if let Some(target) = parsed.repo_target() {
        return exit_code(repo_check(target));
    }
    if let Some(target) = parsed.security_target() {
        return exit_code(security_check(target));
    }
    if let Some(target) = parsed.meta_target() {
        return exit_code(meta_check(target));
    }
    if let Some(target) = parsed.metadoc_target() {
        return exit_code(metadoc_check(target));
    }
    if let Some(target) = parsed.desk_target() {
        return exit_code(desk_patrol(target));
    }
    if let Some(target) = parsed.release_contract_target() {
        return exit_code(release_contract(target));
    }
    if let Some(target) = parsed.release_receipt_target() {
        return exit_code(release_receipt(target));
    }
    if let Some(target) = parsed.metadoc_prompt_target() {
        return exit_code(metadoc_prompt(target));
    }
    if let Some(target) = parsed.standards_target() {
        return exit_code(standards_check(target));
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
    let workers = workers_dir();
    let ledger = LedgerFile::in_dir(&workers.clone().unwrap_or_else(data_dir));
    let runs = RunServices {
        ledger: &ledger,
        clock: &clock,
        root: workers.as_deref(),
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

/// `aw repo check`: the tracked files of the checkout that holds `--dir`
/// (default: the current directory).
fn repo_check(target: &HygieneTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match TrackedFiles::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    cli::run_repo(RepoServices { files: &repo }, target)
}

/// `aw security check`: the tracked files of the checkout that holds `--dir`
/// (default: the current directory).
fn security_check(target: &SecurityTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match TrackedFiles::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    cli::run_security(SecurityServices { files: &repo }, target)
}

/// `aw standards check`: the repositories of `--repo` (default `.`) against
/// the `core` checkout of `--core` (default: `core` next to the first one).
fn standards_check(target: &StandardsTarget) -> Exit {
    let dirs = if target.repo.is_empty() {
        vec![PathBuf::from(".")]
    } else {
        target.repo.clone()
    };
    let core_dir = target.core.clone().unwrap_or_else(|| {
        let first = std::fs::canonicalize(&dirs[0]).unwrap_or_else(|_| dirs[0].clone());
        first
            .parent()
            .map_or_else(|| PathBuf::from("core"), |p| p.join("core"))
    });
    for dir in dirs.iter().chain(std::iter::once(&core_dir)) {
        if !is_checkout(dir) {
            eprintln!("aw: {} is not a git checkout", dir.display());
            return Exit::Nothing;
        }
    }
    let core_id = std::fs::canonicalize(&core_dir).ok();
    let files: Vec<LocalRepo> = dirs.iter().map(LocalRepo::new).collect();
    let repos = dirs
        .iter()
        .zip(&files)
        .map(|(dir, files)| RepoTarget {
            name: dir.display().to_string(),
            is_core: core_id.is_some() && std::fs::canonicalize(dir).ok() == core_id,
            files,
        })
        .collect();
    let core = LocalCore::new(&core_dir);
    cli::run_standards(
        StandardsServices {
            core: &core,
            core_name: core_dir.display().to_string(),
            repos,
        },
        target.format,
    )
}

/// `aw meta check`: the READMEs of `paths` (default `.`) in the checkout
/// that holds `--dir` (default: the current directory).
fn meta_check(target: &MetaTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match LocalMeta::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    let paths = if target.paths.is_empty() {
        vec![".".to_string()]
    } else {
        target.paths.clone()
    };
    cli::run_meta(MetaServices { repo: &repo }, &paths, target.format)
}

/// `aw metadoc check`: the project folders of `paths` (default `.`) in the
/// checkout that holds `--dir` (default: the current directory).
fn metadoc_check(target: &MetaTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match LocalMeta::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    let paths = if target.paths.is_empty() {
        vec![".".to_string()]
    } else {
        target.paths.clone()
    };
    cli::run_metadoc(MetaServices { repo: &repo }, &paths, target.format)
}

/// `aw desk patrol`: what the desk must look at, read through `gh`.
fn desk_patrol(target: &PatrolTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let config = match crate::infrastructure::desk::load_config(&dir) {
        Ok(Some(config)) => config,
        Ok(None) => crate::infrastructure::desk::DeskConfig::builtin(),
        Err(err) => {
            eprintln!("aw: {err:#}");
            return Exit::Nothing;
        }
    };
    if let Some(bad) = target
        .only
        .iter()
        .find(|c| !crate::domain::desk::checks::CHECKS.contains(&c.as_str()))
    {
        eprintln!(
            "aw: unknown check `{bad}`; the checks are {}",
            crate::domain::desk::checks::CHECKS.join(", ")
        );
        return Exit::Nothing;
    }
    let (repos, aw) =
        crate::application::desk::repo_names(&config.org, &config.repos, &config.aw, &target.repo);
    let now = Clock::now(&SystemClock);
    let settings = crate::domain::desk::checks::Settings {
        now,
        since_secs: target.since as i64 * 60,
        stale_secs: target.stale_hours as i64 * 3600,
    };
    let source = crate::infrastructure::desk::GhDesk::new();
    let report =
        crate::application::desk::patrol(&source, &repos, &aw, &settings).only(&target.only);
    cli::run_desk_patrol(&report, target.json)
}

/// `aw release-contract`: the release workflows of the apps of one checkout.
fn release_contract(target: &ContractTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match LocalContract::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    cli::run_release_contract(ContractServices { repo: &repo }, target)
}

/// `aw release-receipt`: the GKE release receipt of one app.
fn release_receipt(target: &ReceiptTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match LocalContract::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    cli::run_release_receipt(
        ReceiptServices {
            repo: &repo,
            files: &ReceiptFiles,
        },
        target,
    )
}

/// `aw metadoc prompt`: the clean-reader task of one project folder.
fn metadoc_prompt(target: &PromptTarget) -> Exit {
    let dir = target.dir.clone().unwrap_or_else(|| PathBuf::from("."));
    let repo = match LocalMeta::open(&dir) {
        Ok(repo) => repo,
        Err(err) => {
            eprintln!("aw: {err}");
            return Exit::Nothing;
        }
    };
    cli::run_metadoc_prompt(MetaServices { repo: &repo }, &target.path)
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

/// `AW_WORKERS_DIR`, when it is set: a directory that holds worker clones
/// and the run ledger. There is no default workers directory.
fn workers_dir() -> Option<PathBuf> {
    std::env::var_os("AW_WORKERS_DIR")
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
}

/// `~/.local/share/aw-rs`: the default place of the run ledger. A build
/// cannot make the ledger again, so it does not go below `/tmp`.
fn data_dir() -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".local").join("share").join("aw-rs")
}
