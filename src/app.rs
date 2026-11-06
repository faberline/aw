//! Composition root: wire the real adapters into the use cases.

use std::process::ExitCode;

use crate::application::work::Services;
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
    match cli::run(services, std::env::args_os()) {
        Exit::Ok => ExitCode::SUCCESS,
        Exit::Nothing => ExitCode::from(2),
        Exit::Failed => ExitCode::FAILURE,
    }
}
