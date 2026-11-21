//! `aw release-contract`: check the release workflows of each app of a
//! checkout against the release facts, and report.

use std::collections::BTreeSet;
use std::fmt::Write;

use serde::Serialize;

use super::app_rules::{self, Fail};
use super::app_tables;
use super::facts::{AppFacts, WORKFLOWS};
use crate::domain::work::ports::PortResult;

/// What the check reads from the checkout.
pub trait ContractRepo {
    /// The text of the file at `path` (from the repository root); `None`
    /// when it is not a readable file.
    fn read(&self, path: &str) -> Option<String>;
    /// Whether anything is at `path`.
    fn exists(&self, path: &str) -> bool;
    /// The apps of `[release.apps.*]` in `aw.toml`.
    fn apps(&self) -> PortResult<Vec<AppFacts>>;
    /// The lines that `source <apps_sh>; <function> <app>` prints. `Err` has
    /// the reason when the shell fails.
    fn shell_values(&self, apps_sh: &str, function: &str, app: &str)
    -> Result<Vec<String>, String>;
    /// Whether `source <apps_sh>; <function> <app>` exits 0. Exit 1 is false;
    /// any other exit is an `Err`.
    fn shell_predicate(&self, apps_sh: &str, function: &str, app: &str) -> Result<bool, String>;
    /// The tables of the receipt maker script at `path`.
    fn maker(&self, path: &str, constants: &[String]) -> Result<super::app_tables::Maker, String>;
}

/// The result of one app.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AppReport {
    pub app: String,
    pub ok: bool,
    pub findings: Vec<Fail>,
}

/// The result of `aw release-contract`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ContractReport {
    pub schema: &'static str,
    pub ok: bool,
    pub apps: Vec<AppReport>,
}

/// One workflow: read it, then run its rules. A missing file is rule `W1`.
fn workflow(
    repo: &dyn ContractRepo,
    path: &str,
    rules: impl Fn(&str) -> Result<(), Fail>,
) -> Option<Fail> {
    match repo.read(path) {
        None => Some(Fail {
            rule: "W1",
            message: format!("missing file: {path}"),
        }),
        Some(text) => rules(&text).err(),
    }
}

/// Check one app. A not-onboarded app must have no candidate workflow (A2);
/// an onboarded app must have both workflows and pass C1 to C8 and P1 to P7.
pub fn check_app(repo: &dyn ContractRepo, app: &AppFacts, shared: &BTreeSet<String>) -> AppReport {
    let mut findings = Vec::new();
    if app.onboarded {
        let candidate = app.candidate_workflow();
        findings.extend(workflow(repo, &candidate, |t| app_rules::candidate(app, t)));
        let promotion = app.promotion_workflow();
        findings.extend(workflow(repo, &promotion, |t| app_rules::promotion(app, t)));
        if app.gke_backend == "gke-acceptance" && !app.acceptance_names.is_empty() {
            let path = format!("{WORKFLOWS}/gke-acceptance.yml");
            findings.extend(workflow(repo, &path, |t| app_rules::acceptance(app, t)));
        }
        if app.shared_scripts {
            let caller = repo
                .read(&promotion)
                .is_some_and(|t| app_rules::is_caller(&t));
            findings.extend(app_tables::tables(repo, app, shared, caller));
        }
        if !app.candidate_gates.is_empty() {
            findings.extend(workflow(repo, &app.candidate_workflow(), |t| {
                app_rules::candidate_gates(app, t)
            }));
        }
        if !app.ci_gates.is_empty() {
            let path = format!("{WORKFLOWS}/ci.yml");
            findings.extend(workflow(repo, &path, |t| app_rules::ci_gates(app, t)));
        }
    } else if repo.exists(&app.candidate_workflow()) {
        findings.push(Fail {
            rule: "A2",
            message: format!(
                "{} exists but {} is still marked onboarded = false here",
                app.candidate_workflow(),
                app.name
            ),
        });
    }
    AppReport {
        app: app.name.clone(),
        ok: findings.is_empty(),
        findings,
    }
}

/// Check the apps of the checkout; `only` limits it to one app.
pub fn check(repo: &dyn ContractRepo, only: Option<&str>) -> PortResult<ContractReport> {
    let mut apps = repo.apps()?;
    if let Some(name) = only {
        apps.retain(|a| a.name == name);
        if apps.is_empty() {
            return Err(crate::domain::work::ports::PortError(format!(
                "no [release.apps.{name}] in aw.toml"
            )));
        }
    }
    if apps.is_empty() {
        return Err(crate::domain::work::ports::PortError(
            "no [release.apps.<app>] table in aw.toml".into(),
        ));
    }
    let shared: BTreeSet<String> = apps
        .iter()
        .filter(|a| a.onboarded && a.shared_scripts)
        .map(|a| a.name.clone())
        .collect();
    let reports: Vec<AppReport> = apps.iter().map(|a| check_app(repo, a, &shared)).collect();
    Ok(ContractReport {
        schema: "release-contract-report/1",
        ok: reports.iter().all(|r| r.ok),
        apps: reports,
    })
}

/// The text report: one line for each app, one line for each finding.
pub fn render(report: &ContractReport) -> String {
    let mut out = String::new();
    for app in &report.apps {
        let verdict = if app.ok { "PASS" } else { "FAIL" };
        let _ = writeln!(out, "{verdict} {}", app.app);
        for finding in &app.findings {
            let _ = writeln!(out, "  {} {}", finding.rule, finding.message);
        }
    }
    out.trim_end().to_string()
}
