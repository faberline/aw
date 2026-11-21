//! `aw` command line: parse, call one use case, print JSON.

use std::ffi::OsString;
use std::io::Read;
use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde::Serialize;

use crate::application::ci::{self, CiServices};
use crate::application::meta::{self, MetaServices};
use crate::application::metadoc;
use crate::application::release::{self, Advanced, ReleaseServices};
use crate::application::release_contract::{self, ContractServices};
use crate::application::release_receipt::{self, ReceiptRequest, ReceiptServices};
use crate::application::repo::{self, RepoServices};
use crate::application::run::{Client, Event, RunServices};
use crate::application::standards::{self, StandardsServices};
use crate::application::work::{self, ClaimOutcome, Outcome, Services};
use crate::domain::desk::report::Report;
use crate::domain::release::receipt::GcpPaths;
use crate::domain::work::ports::PortError;
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
        aw meta check checks project README files against the README contract\n\
        aw metadoc check checks a project's README, STATUS and ROADMAP against their contracts\n\
        aw metadoc prompt prints the clean-reader task for a project that passes the check\n\
        aw repo check checks the tracked files for clutter (docs/standards/repo-hygiene.md)\n\
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
        /// Take only an issue that a later stage sent back (tdd: an open
        /// pull request or a review `failed`; spec: a tdd `respec`). New
        /// work is left. Needs `--stage tdd` or `--stage spec`. Used
        /// under backpressure.
        #[arg(long)]
        rework_only: bool,
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
        /// The issue this run worked on. Its claim counts, not the newest
        /// claim of the agent. With a lost claim, a tdd run whose checkout
        /// is on the work branch of the issue still reports.
        #[arg(long)]
        issue: Option<u64>,
    },
    /// Delete the claim without reporting.
    Release {
        #[command(flatten)]
        target: Target,
        /// The issue this run worked on: release the claim of that issue.
        #[arg(long)]
        issue: Option<u64>,
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
    /// Check the project documents of a checkout against their contract.
    Meta {
        #[command(subcommand)]
        command: MetaCommand,
    },
    /// What the desk looks at: read-only checks over the pipeline repositories.
    Desk {
        #[command(subcommand)]
        command: DeskCommand,
    },
    /// Check the core documents of a project folder against their contracts.
    Metadoc {
        #[command(subcommand)]
        command: MetadocCommand,
    },
    /// Check the tracked files of a checkout for clutter.
    Repo {
        #[command(subcommand)]
        command: RepoCommand,
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
    /// Check the candidate and promotion workflows of each app of
    /// `[release.apps.*]` in `aw.toml` against the release contract (rules C1
    /// to C8, P1 to P7 and A2). It only reads. Exit codes: 0 pass, 1 a rule
    /// failed, 2 a usage error.
    ReleaseContract(ContractTarget),
    /// Make the public, redacted GKE release receipt of one app: it binds the
    /// final candidate manifest to one `gke-acceptance` workflow run and its
    /// evidence bundle, or (backend `gcp`) to the four files of a GCP
    /// acceptance run, and writes `<app>-gke-receipt.json` and its
    /// `.sha256`. It reads the facts from `[release.apps.<app>]` in `aw.toml`.
    /// Exit codes: 0 written, 1 refused or a usage error, 2 no checkout or no
    /// `aw.toml`.
    ReleaseReceipt(ReceiptTarget),
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

/// What `aw release-contract` reads.
#[derive(Args, Clone, Debug, Default)]
pub struct ContractTarget {
    /// Check only this app. Default: every app of `[release.apps.*]`.
    #[arg(long)]
    pub app: Option<String>,
    /// A folder inside the checkout. Default: the current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
    /// `text` prints one line for each app and each finding; `json` prints a
    /// `release-contract-report/1` document.
    #[arg(long, value_enum, default_value_t = ReportFormat::Text)]
    pub format: ReportFormat,
}

/// What `aw release-receipt` reads and writes.
#[derive(Args, Clone, Debug)]
pub struct ReceiptTarget {
    /// The app of `[release.apps.*]`.
    #[arg(long)]
    pub app: String,
    /// The harness backend. It must be the `gke_backend` of the app.
    #[arg(long)]
    pub backend: String,
    /// The final candidate manifest, as the candidate workflow made it.
    #[arg(long, value_name = "FILE")]
    pub candidate_manifest: PathBuf,
    /// The receipt to write. Its name must be the `receipt_name` of the app.
    #[arg(long, value_name = "FILE")]
    pub output: PathBuf,
    /// `gh run view <id> --json databaseId,attempt,conclusion,status,url,headSha,event,workflowName,displayTitle,jobs`
    #[arg(long, value_name = "FILE")]
    pub gh_run: Option<PathBuf>,
    /// The downloaded `gke-acceptance-evidence-<run>-<attempt>` bundle.
    #[arg(long, value_name = "DIR")]
    pub evidence_dir: Option<PathBuf>,
    /// Backend `gcp`: the `run.json` of the acceptance run.
    #[arg(long, value_name = "FILE")]
    pub run: Option<PathBuf>,
    /// Backend `gcp`: the `images.json` of the acceptance run.
    #[arg(long, value_name = "FILE")]
    pub images: Option<PathBuf>,
    /// Backend `gcp`: the `acceptance.json` of the acceptance run.
    #[arg(long, value_name = "FILE")]
    pub acceptance: Option<PathBuf>,
    /// Backend `gcp`: the `cleanup.json` of the acceptance run.
    #[arg(long, value_name = "FILE")]
    pub cleanup: Option<PathBuf>,
    /// Backend `gcp`, from `production_since` on: the `acceptance.json` of
    /// the production gates. The raw evidence of each gate is in
    /// `evidence/<gate>.json` next to it.
    #[arg(long, value_name = "FILE")]
    pub production_acceptance: Option<PathBuf>,
    /// A folder inside the checkout that holds `aw.toml`. Default: the
    /// current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
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
enum RepoCommand {
    /// Check the tracked files (`git ls-files`) against the hygiene rules
    /// RH1 to RH5 of docs/standards/repo-hygiene.md and the `[hygiene]`
    /// table of `aw.toml`. Exit codes: 0 pass, 1 a new violation or a stale
    /// baseline entry, 2 a usage error.
    Check(HygieneTarget),
}

/// What `aw repo check` reads.
#[derive(Args, Clone, Debug, Default)]
pub struct HygieneTarget {
    /// A folder inside the checkout. Default: the current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
    /// `text` prints one line for each finding; `json` prints a
    /// `repo-report/1` document.
    #[arg(long, value_enum, default_value_t = ReportFormat::Text)]
    pub format: ReportFormat,
    /// Print the `baseline = [...]` block for every current violation and
    /// exit 0. Use it once, to adopt the check.
    #[arg(long)]
    pub print_baseline: bool,
}

#[derive(Subcommand)]
enum MetaCommand {
    /// Check a README against the README contract (rules R1 to R12): the
    /// required headings, the capability index and details, the sources,
    /// the gates and the links. Repository values come from the `[meta]`
    /// table of `aw.toml`. Exit codes: 0 pass, 1 a finding, 2 a usage error.
    Check(MetaTarget),
}

#[derive(Subcommand)]
enum DeskCommand {
    /// Read each pipeline repository through `gh` and print a Markdown
    /// checklist of what the desk must look at (main-red, blocked-new,
    /// blocked-no-reason, stuck-stage, pr-stuck, queue-empty, waits-for-done,
    /// waits-for-split, decision-open, pipeline-open, routine-quiet). The
    /// summary line comes first. Outside the aw checkout it reads the
    /// built-in repository list. It changes nothing.
    /// Exit codes: 0 done, 1 a repository could not be read, 2 a usage error.
    Patrol(PatrolTarget),
}

/// What `aw desk patrol` reads.
#[derive(Args, Clone, Debug, Default)]
pub struct PatrolTarget {
    /// Patrol only this repository (`name` or `owner/name`). Repeat it.
    /// Default: `[desk] repos` of `aw.toml`.
    #[arg(long, value_name = "REPO")]
    pub repo: Vec<String>,
    /// Show only the items of this check. Repeat it.
    #[arg(long, value_name = "CHECK")]
    pub only: Vec<String>,
    /// `blocked-new` looks at blocked issues changed in this many minutes.
    #[arg(long, value_name = "MINUTES", default_value_t = 70)]
    pub since: u64,
    /// `stuck-stage`, `pr-stuck` and `routine-quiet` fire after this many
    /// hours without an update.
    #[arg(long, value_name = "HOURS", default_value_t = 6)]
    pub stale_hours: u64,
    /// Print the same data as JSON.
    #[arg(long)]
    pub json: bool,
    /// A folder inside the aw checkout. Default: the current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
}

#[derive(Subcommand)]
enum MetadocCommand {
    /// Check the documents of a project folder: README (R1 to R12), STATUS
    /// (S1 to S7), ROADMAP (M1 to M6), the supporting guides (D1 to D4) and the
    /// rules that tie them (P1 to P7). Repository values come
    /// from the `[meta]` table of `aw.toml`. Exit codes: 0 pass, 1 a finding,
    /// 2 a usage error.
    Check(MetaTarget),
    /// Print the clean-reader task for a project folder: a prompt that asks a
    /// fresh reader to restate the product from its adopted documents. It
    /// prints nothing else and exits 1 with the findings when the
    /// deterministic check fails.
    Prompt(PromptTarget),
}

/// What `aw metadoc prompt` reads.
#[derive(Args, Clone, Debug, Default)]
pub struct PromptTarget {
    /// A project folder, or one of its core documents, relative to the top of
    /// the checkout.
    pub path: String,
    /// A folder inside the checkout. Default: the current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
}

/// What `aw meta check` and `aw metadoc check` read.
#[derive(Args, Clone, Debug, Default)]
pub struct MetaTarget {
    /// A README.md, or a folder that holds one, relative to the top of the
    /// checkout. Default: `.`.
    pub paths: Vec<String>,
    /// A folder inside the checkout. Default: the current directory.
    #[arg(long, value_name = "DIR")]
    pub dir: Option<PathBuf>,
    /// `text` prints one block per README; `json` prints a
    /// `meta-report/1` document.
    #[arg(long, value_enum, default_value_t = ReportFormat::Text)]
    pub format: ReportFormat,
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
}

impl From<ClientArg> for Client {
    fn from(c: ClientArg) -> Self {
        match c {
            ClientArg::Claude => Client::Claude,
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
            | Command::Release { target, .. }
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

    /// The target of `aw repo check`, if that is the command.
    pub fn repo_target(&self) -> Option<&HygieneTarget> {
        match &self.command {
            Command::Repo {
                command: RepoCommand::Check(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The target of `aw meta check`, if that is the command.
    pub fn meta_target(&self) -> Option<&MetaTarget> {
        match &self.command {
            Command::Meta {
                command: MetaCommand::Check(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The target of `aw metadoc check`, if that is the command.
    pub fn metadoc_target(&self) -> Option<&MetaTarget> {
        match &self.command {
            Command::Metadoc {
                command: MetadocCommand::Check(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The target of `aw desk patrol`, if that is the command.
    pub fn desk_target(&self) -> Option<&PatrolTarget> {
        match &self.command {
            Command::Desk {
                command: DeskCommand::Patrol(target),
            } => Some(target),
            _ => None,
        }
    }

    /// The target of `aw release-contract`, if that is the command.
    pub fn release_contract_target(&self) -> Option<&ContractTarget> {
        match &self.command {
            Command::ReleaseContract(target) => Some(target),
            _ => None,
        }
    }

    /// The target of `aw release-receipt`, if that is the command.
    pub fn release_receipt_target(&self) -> Option<&ReceiptTarget> {
        match &self.command {
            Command::ReleaseReceipt(target) => Some(target),
            _ => None,
        }
    }

    /// The target of `aw metadoc prompt`, if that is the command.
    pub fn metadoc_prompt_target(&self) -> Option<&PromptTarget> {
        match &self.command {
            Command::Metadoc {
                command: MetadocCommand::Prompt(target),
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

/// Run `aw repo check`: print the report; exit 1 when a violation is new or
/// a baseline entry is stale. With `print_baseline`, print the baseline.
pub fn run_repo(services: RepoServices<'_>, target: &HygieneTarget) -> Exit {
    let result = if target.print_baseline {
        repo::baseline(services).map(|all| {
            print!("{}", repo::render_baseline(&all));
            true
        })
    } else {
        repo::check(services).and_then(|report| {
            match target.format {
                ReportFormat::Json => print(&report).map_err(|e| PortError(format!("{e:#}")))?,
                ReportFormat::Text => println!("{}", repo::render(&report)),
            }
            Ok(report.ok)
        })
    };
    match result {
        Ok(true) => Exit::Ok,
        Ok(false) => Exit::Failed,
        Err(err) => {
            eprintln!("aw: {err}");
            Exit::Nothing
        }
    }
}

/// Run `aw metadoc check`: print the report; exit 1 when a document set has
/// a finding and 2 when a target cannot be read.
pub fn run_metadoc(services: MetaServices<'_>, paths: &[String], format: ReportFormat) -> Exit {
    let report = match metadoc::check(services, paths) {
        Ok(report) => report,
        Err(err) => {
            eprintln!("aw: {:#}", anyhow::Error::from(err));
            return Exit::Nothing;
        }
    };
    let printed = match format {
        ReportFormat::Json => print(&report),
        ReportFormat::Text => {
            println!("{}", metadoc::render(&report));
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

/// Print the patrol report. Exit 1 when a repository could not be read.
pub fn run_desk_patrol(report: &Report, json: bool) -> Exit {
    if json {
        println!("{}", report.json());
    } else {
        print!("{}", report.markdown());
    }
    if report.has_unknown() {
        Exit::Failed
    } else {
        Exit::Ok
    }
}

/// Run `aw release-contract`: print the report; exit 1 when a rule fails.
pub fn run_release_contract(services: ContractServices<'_>, target: &ContractTarget) -> Exit {
    let report = match release_contract::check(services, target.app.as_deref()) {
        Ok(report) => report,
        Err(err) => {
            eprintln!("aw: {:#}", anyhow::Error::from(err));
            return Exit::Nothing;
        }
    };
    let printed = match target.format {
        ReportFormat::Json => print(&report),
        ReportFormat::Text => {
            println!("{}", release_contract::render(&report));
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

/// Run `aw release-receipt`: write the receipt, or print the refusal and
/// exit 1.
pub fn run_release_receipt(services: ReceiptServices<'_>, target: &ReceiptTarget) -> Exit {
    let request = ReceiptRequest {
        app: &target.app,
        backend: &target.backend,
        candidate_manifest: &target.candidate_manifest,
        output: &target.output,
        gh_run: target.gh_run.as_deref(),
        evidence_dir: target.evidence_dir.as_deref(),
        gcp: GcpPaths {
            run: target.run.as_deref(),
            images: target.images.as_deref(),
            acceptance: target.acceptance.as_deref(),
            cleanup: target.cleanup.as_deref(),
            production: target.production_acceptance.as_deref(),
        },
    };
    match release_receipt::make(services, &request) {
        Ok(_) => {
            println!("GKE receipt written: {}", target.output.display());
            Exit::Ok
        }
        Err(message) => {
            eprintln!("{message}");
            Exit::Failed
        }
    }
}

/// Run `aw metadoc prompt`: print the clean-reader task, or the findings and
/// exit 1 when the check fails.
pub fn run_metadoc_prompt(services: MetaServices<'_>, path: &str) -> Exit {
    match metadoc::prompt(services, path) {
        Ok(metadoc::Prompt::Text(text)) => {
            println!("{text}");
            Exit::Ok
        }
        Ok(metadoc::Prompt::Findings(report)) => {
            println!("{}", metadoc::render(&report));
            eprintln!(
                "error: clean-reader review is skipped until deterministic findings are fixed"
            );
            Exit::Failed
        }
        Err(err) => {
            eprintln!("aw: {:#}", anyhow::Error::from(err));
            Exit::Nothing
        }
    }
}

/// Run `aw meta check`: print the report; exit 1 when a README has a
/// finding and 2 when a README cannot be read.
pub fn run_meta(services: MetaServices<'_>, paths: &[String], format: ReportFormat) -> Exit {
    let report = match meta::check(services, paths) {
        Ok(report) => report,
        Err(err) => {
            eprintln!("aw: {:#}", anyhow::Error::from(err));
            return Exit::Nothing;
        }
    };
    let printed = match format {
        ReportFormat::Json => print(&report),
        ReportFormat::Text => {
            println!("{}", meta::render(&report));
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
        Command::Claim {
            target,
            skip,
            has,
            rework_only,
        } => match work::claim_with(s, &target.agent, target.stage(), &skip, &has, rework_only)? {
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
            target,
            outcome,
            message,
            file,
            issue,
        } => {
            let text = read_message(message, file)?;
            let reported = work::report_issue(
                s,
                &target.agent,
                target.stage(),
                issue,
                outcome.into(),
                &text,
            )?;
            warn(&reported.warnings);
            print(&reported)?;
            Ok(Exit::Ok)
        }
        Command::Release { target, issue } => {
            let (claim, warnings) = work::release_issue(s, &target.agent, target.stage(), issue)?;
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
        Command::Repo { .. } => {
            anyhow::bail!("`aw repo` needs its adapter; the composition root runs it")
        }
        Command::Meta { .. } => {
            anyhow::bail!("`aw meta` needs its adapter; the composition root runs it")
        }
        Command::Desk { .. } => {
            anyhow::bail!("`aw desk` needs its adapter; the composition root runs it")
        }
        Command::Metadoc { .. } => {
            anyhow::bail!("`aw metadoc` needs its adapter; the composition root runs it")
        }
        Command::Standards { .. } => {
            anyhow::bail!("`aw standards` needs its adapters; the composition root runs it")
        }
        Command::ReleaseReceipt(_) => {
            anyhow::bail!("`aw release-receipt` needs its adapter; the composition root runs it")
        }
        Command::ReleaseContract(_) => {
            anyhow::bail!("`aw release-contract` needs its adapter; the composition root runs it")
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
