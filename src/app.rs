//! Composition root: wire the real adapters into the use cases.

use std::path::PathBuf;
use std::process::ExitCode;

use crate::application::run::RunServices;
use crate::application::work::Services;
use crate::infrastructure::run::LedgerFile;
use crate::infrastructure::work::{ClaimFile, GhCli, GitCli, SystemClock};
use crate::interfaces::work::cli::{self, Exit};

pub fn run() -> ExitCode {
    let tracker = GhCli::default();
    let workspace = GitCli;
    let store = ClaimFile::in_git_dir();
    let clock = SystemClock;
    let services = Services {
        tracker: &tracker,
        workspace: &workspace,
        store: &store,
        clock: &clock,
    };
    let root = workers_dir();
    let ledger = LedgerFile::in_dir(&root);
    let runs = RunServices {
        ledger: &ledger,
        clock: &clock,
        root: &root,
    };
    match cli::run(services, runs, std::env::args_os()) {
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
