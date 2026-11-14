//! `aw` command line: parse, call one use case, print JSON.

use std::ffi::OsString;
use std::io::Read;
use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde::Serialize;

use crate::application::ci::{self, CiServices};
use crate::application::release::{self, Advanced, ReleaseServices};
use crate::application::run::{Client, Event, RunServices};
use crate::application::standards::{self, StandardsServices};
use crate::application::work::{self, ClaimOutcome, Outcome, Services};
use crate::domain::work::stage::Stage;
use crate::interfaces::run::commands;

#[derive(Parser)]
#[command(
    name = "aw",
    version,
    about = "Claim one issue, do its stage, report back.",
    long_about = "Claim one issue, do its stage, report back.\n\n\
        aw claim   takes the next issue labeled agent:<agent> and posts the claim on it\n\
        aw report  finishes the stage (push + PR, merge, or review) and turns the claim into the signal\n\
        aw release deletes the claim without a report\n\
        aw status  shows the claim the agent holds\n\
        aw hook    records a worker run start or end from a client hook\n\
        aw runs    lists the recent worker runs\n\
        aw ci check checks ci.yml and the ruleset against docs/standards/ci.md\n\
        aw standards check checks Cargo.toml files against docs/standards/core-reuse.md\n\
        aw release-status    assesses a milestone release; it only reads\n\
        aw release-advance   creates the next release tag when the assessment did not change\n\
        aw release-preflight proves a tag inside its own workflow run; it only reads\n\n\
        The claim is an issue comment on GitHub. spec and review use only GitHub;\n\
        tdd runs in a clean clone of the repository and checks out the work branch.\n\n\
        Exit codes: 0 done, 2 nothing to do, 1 error or a failed check."
)]
pub struct Cli {
    #[command(subcommand)]
    command: Command,
}

/// Who works, and in which repository.
#[derive(Args)]
struct Target {
    /// Agent id, as in the `agent:<id>` label.
    #[arg(long, env = "AW_AGENT")]
    agent: String,
    /// `owner/name`. Default: the repository of the current checkout.
    #[arg(long, env = "AW_REPO")]
    repo: Option<String>,
    /// Work on this stage only: claim, hold and report only its issues.
    /// One worker per stage can then run at the same time.
    #[arg(long, env = "AW_STAGE")]
    stage: Option<StageArg>,
}

#[derive(Clone, Copy, ValueEnum)]
enum StageArg {
    Spec,
    Tdd,
    Review,
}

impl From<StageArg> for Stage {
    fn from(s: StageArg) -> Self {
        match s {
            StageArg::Spec => Stage::Spec,
            StageArg::Tdd => Stage::Tdd,
            StageArg::Review => Stage::Review,
        }
    }
}

impl Target {
    fn stage(&self) -> Option<Stage> {
        self.stage.map(Stage::from)
    }
}

#[derive(Subcommand)]
enum Command {
    /// Take the next issue and post the claim on it. tdd also checks out
    /// the work branch.
    Claim {
        #[command(flatten)]
        target: Target,
        /// Pass over this issue number. Repeat it for more issues.
        #[arg(long, value_name = "ISSUE")]
        skip: Vec<u64>,
        /// What this machine has, for issues labeled `needs:<THING>`
        /// (for example `mac`). Without it, an issue with a `needs:`
        /// label is passed over. Repeat it or separate with commas.
        #[arg(long, env = "AW_HAS", value_name = "THING", value_delimiter = ',')]
        has: Vec<String>,
    },
    /// Finish the claimed stage.
    Report {
        #[command(flatten)]
        target: Target,
        /// spec: ready|split|blocked. tdd: ready|respec|blocked. review: passed|failed|closed|blocked.
        /// tdd with an incomplete spec: respec, not blocked.
        /// spec with an issue too large for one pull request: split; the
        /// message names 2 to 5 children, each under a line `## Child: <title>`.
        outcome: OutcomeArg,
        /// Report text. tdd ready: the pull request body.
        #[arg(short, long, conflicts_with = "file")]
        message: Option<String>,
        /// Read the report text from a file, or `-` for stdin.
        #[arg(short = 'F', long)]
        file: Option<String>,
    },
    /// Delete the claim without reporting.
    Release {
        #[command(flatten)]
        target: Target,
    },
    /// Show the claim the agent holds.
    Status {
        #[command(flatten)]
        target: Target,
    },
    /// Record a worker run event from a client hook. Reads the hook JSON
    /// from stdin and always exits 0.
    Hook { client: ClientArg, event: EventArg },
    /// List the recent worker runs from the run ledger.
    Runs {
        /// How many runs to show, newest last.
        #[arg(short = 'n', long, default_value_t = 10)]
        limit: usize,
    },
    /// Check the repository against a standard.
    Ci {
        #[command(subcommand)]
        command: CiCommand,
    },
    /// Check repositories against the shared standards of docs/standards/.
    Standards {
        #[command(subcommand)]
        command: StandardsCommand,
    },
    /// Assess a milestone release: the state, the one next action and the
    /// fingerprint of the inputs. It only reads.
    ReleaseStatus {
        #[command(flatten)]
        milestone: MilestoneArg,
    },
    /// Do the one next action of a milestone release. It writes only a
    /// release-candidate tag or the final tag, and never moves a tag.
    ReleaseAdvance {
        #[command(flatten)]
        milestone: MilestoneArg,
        /// The `fingerprint` of the assessment you approved. The command
        /// stops when the inputs changed.
        #[arg(long, value_name = "SHA256")]
        expected: String,
    },
    /// Prove, inside the workflow run of a tag, that the tag is at the
    /// commit and that the milestone is ready for it. It only reads.
    ReleasePreflight {
        /// `vX.Y.Z` or `vX.Y.Z-rc.N`.
        #[arg(long)]
        tag: String,
        /// The exact 40-character commit of the tag.
        #[arg(long)]
        commit: String,
        /// The milestone number. Default: the one milestone of the release
        /// contract with the version of the tag.
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        milestone: Option<u64>,
        /// `owner/name`. Default: the repository of the current checkout.
        #[arg(long, env = "AW_REPO")]
        repo: Option<String>,
    },
    /// Print the version.
    Version,
}

/// One milestone of one repository.
#[derive(Args)]
struct MilestoneArg {
    /// The number of an existing milestone.
    #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
    milestone: u64,
    /// `owner/name`. Default: the repository of the current checkout.
    #[arg(long, env = "AW_REPO")]
    repo: Option<String>,
}

/// One call of the milestone release gate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReleaseCall {
    Status {
        milestone: u64,
    },
    Advance {
        milestone: u64,
        expected: String,
    },
    Preflight {
        tag: String,
        commit: String,
        milestone: Option<u64>,
    },
}

#[derive(Subcommand)]
enum CiCommand {
    /// Check `.github/workflows/ci.yml`, the `[[standards.ci.exception]]`
    /// entries of `aw.toml` and the default-branch ruleset against
    /// docs/standards/ci.md. Prints the report; exits 1 when a check fails.
    Check(CiTarget),
}

/// Where `aw ci check` reads from.
#[derive(Args, Clone, Debug, Default)]
pub struct CiTarget {
    /// The checkout to read. Default: the current directory, or GitHub when
    /// only `--repo` is given.
    #[arg(long)]
    pub dir: Option<PathBuf>,
    /// `owner/name`. Without `--dir`, the files come from its default branch
    /// on GitHub. With `--dir`, it names the repository for the ruleset.
    /// Default: the `origin` of the checkout.
    #[arg(long)]
    pub repo: Option<String>,
    /// Do not read the ruleset; its check is skipped.
    #[arg(long)]
    pub no_ruleset: bool,
}

#[derive(Subcommand)]
enum StandardsCommand {
    /// Check the core-reuse standard (docs/standards/core-reuse.md, CR0 to
    /// CR8) on the tracked Cargo.toml and build.rs files of local checkouts.
    /// Exit codes: 0 pass, 1 a finding, 2 a usage error.
    Check(StandardsTarget),
}

/// Where `aw standards check` reads from.
#[derive(Args, Clone, Debug, Default)]
pub struct StandardsTarget {
    /// A repository checkout. Repeat it for more. Default: `.`.
    #[arg(long, value_name = "DIR")]
    pub repo: Vec<PathBuf>,
    /// The `faberline/core` checkout. Default: the `core` folder next to
    /// the first repository.
    #[arg(long, value_name = "DIR")]
    pub core: Option<PathBuf>,
    /// `text` prints one line per finding; `json` prints a
    /// `core-reuse-report/1` document.
    #[arg(long, value_enum, default_value_t = ReportFormat::Text)]
    pub format: ReportFormat,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, ValueEnum)]
pub enum ReportFormat {
    #[default]
    Text,
    Json,
}

#[derive(Clone, Copy, ValueEnum)]
enum ClientArg {
    Claude,
    Codex,
    Agy,
}

impl From<ClientArg> for Client {
    fn from(c: ClientArg) -> Self {
        match c {
            ClientArg::Claude => Client::Claude,
            ClientArg::Codex => Client::Codex,
            ClientArg::Agy => Client::Agy,
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
enum EventArg {
    Start,
    End,
}

impl From<EventArg> for Event {
    fn from(e: EventArg) -> Self {
        match e {
            EventArg::Start => Event::Start,
            EventArg::End => Event::End,
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
enum OutcomeArg {
    Ready,
    Split,
    Respec,
    Blocked,
    Passed,
    Failed,
    Closed,
}

impl From<OutcomeArg> for Outcome {
    fn from(o: OutcomeArg) -> Self {
        match o {
            OutcomeArg::Ready => Outcome::Ready,
            OutcomeArg::Split => Outcome::Split,
            OutcomeArg::Respec => Outcome::Respec,
            OutcomeArg::Blocked => Outcome::Blocked,
            OutcomeArg::Passed => Outcome::Passed,
            OutcomeArg::Failed => Outcome::Failed,
            OutcomeArg::Closed => Outcome::Closed,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Exit {
    Ok,
    Nothing,
    Failed,
}

/// Parse `args` (the program name first). On `--help`, `--version` or a
/// usage error it prints and returns the exit.
pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Cli, Exit> {
    Cli::try_parse_from(args).map_err(|err| {
        let _ = err.print();
        if err.use_stderr() {
            Exit::Failed
        } else {
            Exit::Ok
        }
    })
}

impl Cli {
    /// The `--repo` the command names, if any. The composition root points
    /// the GitHub adapter at it.
    pub fn repo(&self) -> Option<String> {
        match &self.command {
            Command::Claim { target, .. }
            | Command::Report { target, .. }
            | Command::Release { target }
            | Command::Status { target } => target.repo.clone(),
            _ => None,
        }
    }

    /// The target of `aw ci check`, if that is the command. The composition
    /// root builds the CI adapters from it and calls [`run_ci`].
    pub fn ci_target(&self) -> Option<&CiTarget> {
        match &self.command {
            Command::Ci {
                command: CiCommand::Check(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The target of `aw standards check`, if that is the command.
    pub fn standards_target(&self) -> Option<&StandardsTarget> {
        match &self.command {
            Command::Standards {
                command: StandardsCommand::Check(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The `--repo` and the call of a release gate command, if that is the
    /// command. The composition root builds the adapters and calls
    /// [`run_release`].
    pub fn release_call(&self) -> Option<(Option<String>, ReleaseCall)> {
        match &self.command {
            Command::ReleaseStatus { milestone: m } => Some((
                m.repo.clone(),
                ReleaseCall::Status {
                    milestone: m.milestone,
                },
            )),
            Command::ReleaseAdvance {
                milestone: m,
                expected,
            } => Some((
                m.repo.clone(),
                ReleaseCall::Advance {
                    milestone: m.milestone,
                    expected: expected.clone(),
                },
            )),
            Command::ReleasePreflight {
                tag,
                commit,
                milestone,
                repo,
            } => Some((
                repo.clone(),
                ReleaseCall::Preflight {
                    tag: tag.clone(),
                    commit: commit.clone(),
                    milestone: *milestone,
                },
            )),
            _ => None,
        }
    }
}

/// Run one release gate command: print the JSON result. Exit 2 when
/// `release-advance` has no write to do in this version.
pub fn run_release(services: ReleaseServices<'_>, call: &ReleaseCall) -> Exit {
    let result = match call {
        ReleaseCall::Status { milestone } => {
            release::status(services, *milestone).map(|value| (value, Exit::Ok))
        }
        ReleaseCall::Advance {
            milestone,
            expected,
        } => release::advance(services, *milestone, expected).map(|done| match done {
            Advanced::Done(record) => (record, Exit::Ok),
            Advanced::Nothing(assessment) => {
                eprintln!(
                    "aw: no write: state `{}`, action `{}`; this version writes only a tag",
                    assessment["state"].as_str().unwrap_or("unknown"),
                    assessment["action"].as_str().unwrap_or("none"),
                );
                (assessment, Exit::Nothing)
            }
        }),
        ReleaseCall::Preflight {
            tag,
            commit,
            milestone,
        } => release::preflight(services, tag, commit, *milestone).map(|value| (value, Exit::Ok)),
    };
    match result
        .map_err(anyhow::Error::from)
        .and_then(|(value, exit)| print(&value).map(|()| exit))
    {
        Ok(exit) => exit,
        Err(err) => {
            eprintln!("aw: {err:#}");
            Exit::Failed
        }
    }
}

/// Run `aw ci check`: print the report, exit 1 when a check fails.
pub fn run_ci(services: CiServices<'_>) -> Exit {
    let result = ci::check(services)
        .map_err(anyhow::Error::from)
        .and_then(|report| {
            print(&report)?;
            Ok(report.passed)
        });
    match result {
        Ok(true) => Exit::Ok,
        Ok(false) => Exit::Failed,
        Err(err) => {
            eprintln!("aw: {err:#}");
            Exit::Failed
        }
    }
}

/// Run `aw standards check`: print the report; exit 1 when a repository
/// has a finding and 2 when a checkout cannot be read.
pub fn run_standards(services: StandardsServices<'_>, format: ReportFormat) -> Exit {
    let result = standards::check(services).map_err(anyhow::Error::from);
    let report = match result {
        Ok(report) => report,
        Err(err) => {
            eprintln!("aw: {err:#}");
            return Exit::Nothing;
        }
    };
    let printed = match format {
        ReportFormat::Json => print(&report),
        ReportFormat::Text => {
            println!("{}", standards::render(&report));
            Ok(())
        }
    };
    match printed {
        Ok(()) if report.ok => Exit::Ok,
        Ok(()) => Exit::Failed,
        Err(err) => {
            eprintln!("aw: {err:#}");
            Exit::Nothing
        }
    }
}

/// Run one parsed command.
pub fn run(services: Services<'_>, runs: RunServices<'_>, cli: Cli) -> Exit {
    match execute(services, runs, cli.command) {
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

fn execute(s: Services<'_>, r: RunServices<'_>, command: Command) -> Result<Exit> {
    match command {
        Command::Claim { target, skip, has } => {
            match work::claim(s, &target.agent, target.stage(), &skip, &has)? {
                ClaimOutcome::Claimed(claim) => {
                    print(&claim)?;
                    Ok(Exit::Ok)
                }
                ClaimOutcome::Nothing(why) => {
                    eprintln!("aw: {why}");
                    Ok(Exit::Nothing)
                }
            }
        }
        Command::Report {
            target,
            outcome,
            message,
            file,
        } => {
            let text = read_message(message, file)?;
            let reported = work::report(s, &target.agent, target.stage(), outcome.into(), &text)?;
            warn(&reported.warnings);
            print(&reported)?;
            Ok(Exit::Ok)
        }
        Command::Release { target } => {
            let (claim, warnings) = work::release(s, &target.agent, target.stage())?;
            warn(&warnings);
            print(&claim)?;
            Ok(Exit::Ok)
        }
        Command::Status { target } => match work::status(s, &target.agent, target.stage())? {
            Some(claim) => {
                print(&claim)?;
                Ok(Exit::Ok)
            }
            None => {
                eprintln!("aw: `{}` holds no claim", target.agent);
                Ok(Exit::Nothing)
            }
        },
        Command::Hook { client, event } => {
            commands::hook(r, client.into(), event.into());
            Ok(Exit::Ok)
        }
        Command::Runs { limit } => {
            let runs = commands::runs(r, limit)?;
            if runs.is_empty() {
                eprintln!("aw: no runs recorded");
                return Ok(Exit::Nothing);
            }
            print(&runs)?;
            Ok(Exit::Ok)
        }
        Command::Ci { .. } => {
            anyhow::bail!("`aw ci` needs the CI adapters; the composition root runs it")
        }
        Command::Standards { .. } => {
            anyhow::bail!("`aw standards` needs its adapters; the composition root runs it")
        }
        Command::ReleaseStatus { .. }
        | Command::ReleaseAdvance { .. }
        | Command::ReleasePreflight { .. } => {
            anyhow::bail!("the release gate needs its adapters; the composition root runs it")
        }
        Command::Version => {
            println!("aw {}", env!("CARGO_PKG_VERSION"));
            Ok(Exit::Ok)
        }
    }
}
