//! `aw` command line: parse, call one use case, print JSON.

use std::ffi::OsString;
use std::io::Read;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand, ValueEnum};
use serde::Serialize;

use crate::application::work::{self, ClaimOutcome, Outcome, Services};

#[derive(Parser)]
#[command(
    name = "aw",
    version,
    about = "Claim one issue, do its stage, report back.",
    long_about = "Claim one issue, do its stage, report back.\n\n\
        aw claim   takes the next issue labeled agent:<agent> and checks out its branch\n\
        aw report  finishes the stage (push + PR, merge, or review) and posts the signal\n\
        aw release drops the claim without a report\n\n\
        Exit codes: 0 done, 2 nothing to do, 1 error."
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Take the next issue and check out its branch.
    Claim {
        /// Agent id, as in the `agent:<id>` label.
        #[arg(long, env = "AW_AGENT")]
        agent: String,
    },
    /// Finish the claimed stage.
    Report {
        /// spec/tdd: ready|blocked. review: passed|failed|blocked.
        outcome: OutcomeArg,
        /// Report text. tdd ready: the pull request body.
        #[arg(short, long, conflicts_with = "file")]
        message: Option<String>,
        /// Read the report text from a file, or `-` for stdin.
        #[arg(short = 'F', long)]
        file: Option<String>,
    },
    /// Drop the claim without reporting.
    Release,
    /// Show the active claim.
    Status,
    /// Print the version.
    Version,
}

#[derive(Clone, Copy, ValueEnum)]
enum OutcomeArg {
    Ready,
    Blocked,
    Passed,
    Failed,
}

impl From<OutcomeArg> for Outcome {
    fn from(o: OutcomeArg) -> Self {
        match o {
            OutcomeArg::Ready => Outcome::Ready,
            OutcomeArg::Blocked => Outcome::Blocked,
            OutcomeArg::Passed => Outcome::Passed,
            OutcomeArg::Failed => Outcome::Failed,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Exit {
    Ok,
    Nothing,
    Failed,
}

/// Parse `args` (the program name first) and run one command.
pub fn run(services: Services<'_>, args: impl IntoIterator<Item = OsString>) -> Exit {
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(err) => {
            let _ = err.print();
            return if err.use_stderr() {
                Exit::Failed
            } else {
                Exit::Ok
            };
        }
    };
    match execute(services, cli.command) {
        Ok(exit) => exit,
        Err(err) => {
            eprintln!("aw: {err:#}");
            Exit::Failed
        }
    }
}

fn print(value: &impl Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

fn warn(warnings: &[String]) {
    for w in warnings {
        eprintln!("aw: warning: {w}");
    }
}

fn read_message(message: Option<String>, file: Option<String>) -> Result<String> {
    match (message, file) {
        (Some(m), _) => Ok(m),
        (None, Some(path)) if path == "-" => {
            let mut text = String::new();
            std::io::stdin()
                .read_to_string(&mut text)
                .context("could not read stdin")?;
            Ok(text)
        }
        (None, Some(path)) => {
            std::fs::read_to_string(&path).with_context(|| format!("could not read {path}"))
        }
        (None, None) => Ok(String::new()),
    }
}

fn execute(s: Services<'_>, command: Command) -> Result<Exit> {
    match command {
        Command::Claim { agent } => match work::claim(s, &agent)? {
            ClaimOutcome::Claimed(claim) => {
                print(&claim)?;
                Ok(Exit::Ok)
            }
            ClaimOutcome::Nothing(why) => {
                eprintln!("aw: {why}");
                Ok(Exit::Nothing)
            }
        },
        Command::Report {
            outcome,
            message,
            file,
        } => {
            let text = read_message(message, file)?;
            let reported = work::report(s, outcome.into(), &text)?;
            warn(&reported.warnings);
            print(&reported)?;
            Ok(Exit::Ok)
        }
        Command::Release => {
            let (claim, warnings) = work::release(s)?;
            warn(&warnings);
            print(&claim)?;
            Ok(Exit::Ok)
        }
        Command::Status => match work::status(s)? {
            Some(claim) => {
                print(&claim)?;
                Ok(Exit::Ok)
            }
            None => {
                eprintln!("aw: nothing is claimed");
                Ok(Exit::Nothing)
            }
        },
        Command::Version => {
            println!("aw {}", env!("CARGO_PKG_VERSION"));
            Ok(Exit::Ok)
        }
    }
}
