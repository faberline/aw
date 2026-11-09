//! The rules of `docs/standards/ci.md` as pure functions.

use std::collections::{BTreeSet, VecDeque};

use serde::Serialize;

use super::workflow::{Exception, Job, Workflow};

/// The fixed commands of the `lint` job.
pub const LINT_COMMANDS: [&str; 2] = [
    "cargo fmt --all -- --check",
    "cargo clippy --workspace --all-targets --locked -- -D warnings",
];

/// The checks that the `gate` step must make on `toJSON(needs)`.
pub const GATE_CHECKS: [&str; 2] = [
    r#".lint.result == "success""#,
    r#".result == "success" or .result == "skipped""#,
];

/// Every rule id, in report order.
pub const RULES: [&str; 9] = [
    "CI1", "CI2", "CI3", "CI4", "CI5", "CI6", "CI7", "CI8", "CI9",
];

/// The result of one rule.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pass,
    Fail,
    /// The rule fails, but `aw.toml` records an exception for it.
    Excepted,
    /// The rule could not run, for example because no ruleset was read.
    Skipped,
}

/// One rule and its result.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Finding {
    #[serde(rename = "check")]
    pub rule: &'static str,
    pub status: Status,
    pub detail: String,
}

/// The full result of one check.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    /// True when no rule has the status `fail`.
    pub passed: bool,
    pub findings: Vec<Finding>,
}

/// Checks one repository against the CI stages standard.
///
/// `workflow` is `.github/workflows/ci.yml` (`None` when it does not exist).
/// `required` is the list of required status checks on the default branch
/// (`None` when the ruleset was not read).
pub fn check(
    workflow: Option<&Workflow>,
    required: Option<&[String]>,
    exceptions: &[Exception],
) -> Report {
    let mut results: Vec<(&'static str, Result<String, String>)> = Vec::new();
    match workflow {
        Some(workflow) => {
            results.push(("CI1", Ok(".github/workflows/ci.yml exists".into())));
            results.push(("CI2", name(workflow)));
            results.push(("CI3", job_ids(workflow)));
            results.push(("CI4", lint(workflow)));
            results.push(("CI5", test_needs(workflow)));
            results.push(("CI6", e2e_needs(workflow)));
            results.push(("CI7", gate(workflow)));
        }
        None => results.push(("CI1", Err(".github/workflows/ci.yml is missing".into()))),
    }

    let mut findings: Vec<Finding> = results
        .into_iter()
        .map(|(rule, result)| finding(rule, result, exceptions))
        .collect();
    if workflow.is_none() {
        for rule in &RULES[1..7] {
            findings.push(Finding {
                rule,
                status: Status::Skipped,
                detail: "no ci.yml to read".into(),
            });
        }
    }
    findings.push(match required {
        Some(required) => finding("CI8", ruleset(required), exceptions),
        None => Finding {
            rule: "CI8",
            status: Status::Skipped,
            detail: "the ruleset was not read".into(),
        },
    });
    findings.push(finding("CI9", exception_entries(exceptions), &[]));

    let passed = findings.iter().all(|f| f.status != Status::Fail);
    Report { passed, findings }
}

fn finding(
    rule: &'static str,
    result: Result<String, String>,
    exceptions: &[Exception],
) -> Finding {
    match result {
        Ok(detail) => Finding {
            rule,
            status: Status::Pass,
            detail,
        },
        Err(detail) => match exceptions.iter().find(|e| e.check == rule) {
            Some(exception) => Finding {
                rule,
                status: Status::Excepted,
                detail: format!("{detail}; exception: {}", exception.reason),
            },
            None => Finding {
                rule,
                status: Status::Fail,
                detail,
            },
        },
    }
}

fn name(workflow: &Workflow) -> Result<String, String> {
    match workflow.name.as_deref() {
        Some("ci") => Ok("the workflow name is ci".into()),
        Some(other) => Err(format!("the workflow name is {other:?}, not \"ci\"")),
        None => Err("the workflow has no name; use `name: ci`".into()),
    }
}

fn is_test(id: &str) -> bool {
    id.starts_with("test-")
}

fn is_e2e(id: &str) -> bool {
    id.starts_with("e2e-")
}

fn job_ids(workflow: &Workflow) -> Result<String, String> {
    let mut problems = Vec::new();
    let unknown: Vec<&str> = workflow
        .jobs
        .keys()
        .map(String::as_str)
        .filter(|id| !matches!(*id, "lint" | "gate") && !is_test(id) && !is_e2e(id))
        .collect();
    if !unknown.is_empty() {
        problems.push(format!(
            "job ids must be lint, gate, test-* or e2e-*: {}",
            unknown.join(", ")
        ));
    }
    for required in ["lint", "gate"] {
        if !workflow.jobs.contains_key(required) {
            problems.push(format!("job `{required}` is missing"));
        }
    }
    if !workflow.jobs.keys().any(|id| is_test(id)) {
        problems.push("no test-* job".into());
    }
    if problems.is_empty() {
        Ok(format!("jobs: {}", ids(workflow.jobs.keys())))
    } else {
        Err(problems.join("; "))
    }
}

fn normalize(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn runs_text(job: &Job) -> String {
    normalize(&job.runs.join("\n"))
}

fn lint(workflow: &Workflow) -> Result<String, String> {
    let Some(job) = workflow.jobs.get("lint") else {
        return Err("job `lint` is missing".into());
    };
    let text = runs_text(job);
    let missing: Vec<&str> = LINT_COMMANDS
        .iter()
        .copied()
        .filter(|command| !text.contains(command))
        .collect();
    if missing.is_empty() {
        Ok("lint runs fmt and clippy".into())
    } else {
        Err(format!("lint does not run: {}", missing.join("; ")))
    }
}

fn test_needs(workflow: &Workflow) -> Result<String, String> {
    let bad: Vec<&str> = workflow
        .jobs
        .iter()
        .filter(|(id, job)| is_test(id) && !job.needs.iter().any(|n| n == "lint"))
        .map(|(id, _)| id.as_str())
        .collect();
    if bad.is_empty() {
        Ok("every test-* job needs lint".into())
    } else {
        Err(format!(
            "these test-* jobs do not need lint: {}",
            bad.join(", ")
        ))
    }
}

/// Every job that `id` waits for, directly or through other jobs.
fn closure<'a>(workflow: &'a Workflow, id: &str) -> BTreeSet<&'a str> {
    let mut seen = BTreeSet::new();
    let mut queue: VecDeque<&str> = VecDeque::new();
    if let Some(job) = workflow.jobs.get(id) {
        queue.extend(job.needs.iter().map(String::as_str));
    }
    while let Some(next) = queue.pop_front() {
        let Some((key, job)) = workflow.jobs.get_key_value(next) else {
            continue;
        };
        if seen.insert(key.as_str()) {
            queue.extend(job.needs.iter().map(String::as_str));
        }
    }
    seen
}

fn e2e_needs(workflow: &Workflow) -> Result<String, String> {
    let e2e: Vec<&str> = workflow
        .jobs
        .keys()
        .map(String::as_str)
        .filter(|id| is_e2e(id))
        .collect();
    if e2e.is_empty() {
        return Ok("no e2e-* job".into());
    }
    let bad: Vec<&str> = e2e
        .into_iter()
        .filter(|id| !closure(workflow, id).iter().any(|n| is_test(n)))
        .collect();
    if bad.is_empty() {
        Ok("every e2e-* job waits for a test-* job".into())
    } else {
        Err(format!(
            "these e2e-* jobs do not wait for a test-* job: {}",
            bad.join(", ")
        ))
    }
}

fn gate(workflow: &Workflow) -> Result<String, String> {
    let Some(job) = workflow.jobs.get("gate") else {
        return Err("job `gate` is missing".into());
    };
    let mut problems = Vec::new();
    if job.name.as_deref() != Some("gate") {
        problems.push("gate must have `name: gate`".to_string());
    }
    if !job
        .condition
        .as_deref()
        .is_some_and(|c| c.contains("always()"))
    {
        problems.push("gate must have `if: always()`".into());
    }
    let others: BTreeSet<&str> = workflow
        .jobs
        .keys()
        .map(String::as_str)
        .filter(|id| *id != "gate")
        .collect();
    let needs: BTreeSet<&str> = job.needs.iter().map(String::as_str).collect();
    let missing: Vec<&str> = others.difference(&needs).copied().collect();
    if !missing.is_empty() {
        problems.push(format!("gate does not need: {}", missing.join(", ")));
    }
    let text = runs_text(job);
    let unchecked: Vec<&str> = GATE_CHECKS
        .iter()
        .copied()
        .filter(|check| !text.contains(check))
        .collect();
    if !unchecked.is_empty() {
        problems.push(format!("gate does not check: {}", unchecked.join("; ")));
    }
    if problems.is_empty() {
        Ok("gate always runs and checks every job".into())
    } else {
        Err(problems.join("; "))
    }
}

fn ruleset(required: &[String]) -> Result<String, String> {
    if required.len() == 1 && required[0] == "gate" {
        Ok("the ruleset requires only gate".into())
    } else if required.is_empty() {
        Err("the default branch requires no status check; require only gate".into())
    } else {
        Err(format!(
            "the ruleset must require only gate, not: {}",
            ids(required.iter())
        ))
    }
}

fn exception_entries(exceptions: &[Exception]) -> Result<String, String> {
    let mut problems = Vec::new();
    for exception in exceptions {
        if !RULES[..8].contains(&exception.check.as_str()) {
            problems.push(format!("unknown check {:?}", exception.check));
        }
        if exception.reason.trim().is_empty() {
            problems.push(format!("exception for {} has no reason", exception.check));
        }
    }
    if problems.is_empty() {
        Ok(format!("{} exception(s)", exceptions.len()))
    } else {
        Err(problems.join("; "))
    }
}

fn ids<'a>(items: impl Iterator<Item = &'a String>) -> String {
    items.map(String::as_str).collect::<Vec<_>>().join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(name: &str, needs: &[&str], condition: Option<&str>, runs: &[&str]) -> Job {
        Job {
            name: Some(name.into()),
            needs: needs.iter().map(|s| s.to_string()).collect(),
            condition: condition.map(str::to_string),
            runs: runs.iter().map(|s| s.to_string()).collect(),
        }
    }

    const GATE_RUN: &str = r#"echo "$RESULTS" | jq -r 'to_entries[] | "\(.key): \(.value.result)"'
echo "$RESULTS" | jq -e '.lint.result == "success"' > /dev/null
echo "$RESULTS" | jq -e 'all(.[]; .result == "success" or .result == "skipped")' > /dev/null"#;

    fn standard() -> Workflow {
        let mut workflow = Workflow {
            name: Some("ci".into()),
            ..Workflow::default()
        };
        workflow.jobs.insert(
            "lint".into(),
            job(
                "lint: fmt, clippy",
                &[],
                None,
                &[
                    "cargo fmt --all -- --check",
                    "cargo clippy --workspace --all-targets --locked -- -D warnings",
                ],
            ),
        );
        workflow.jobs.insert(
            "test-workspace".into(),
            job(
                "test: workspace",
                &["lint"],
                None,
                &["cargo test --workspace --locked"],
            ),
        );
        workflow.jobs.insert(
            "e2e-cluster".into(),
            job("e2e: cluster", &["test-workspace"], None, &["make e2e"]),
        );
        workflow.jobs.insert(
            "gate".into(),
            job(
                "gate",
                &["lint", "test-workspace", "e2e-cluster"],
                Some("always()"),
                &[GATE_RUN],
            ),
        );
        workflow
    }

    fn gate_only() -> Vec<String> {
        vec!["gate".into()]
    }

    fn status(report: &Report, rule: &str) -> Status {
        report
            .findings
            .iter()
            .find(|f| f.rule == rule)
            .map(|f| f.status)
            .unwrap()
    }

    #[test]
    fn the_standard_skeleton_passes() {
        let report = check(Some(&standard()), Some(&gate_only()), &[]);
        assert!(report.passed, "{report:#?}");
        assert_eq!(report.findings.len(), RULES.len());
        assert!(report.findings.iter().all(|f| f.status == Status::Pass));
    }

    #[test]
    fn a_missing_file_fails_and_skips_the_workflow_rules() {
        let report = check(None, Some(&gate_only()), &[]);
        assert!(!report.passed);
        assert_eq!(status(&report, "CI1"), Status::Fail);
        assert_eq!(status(&report, "CI7"), Status::Skipped);
        assert_eq!(status(&report, "CI8"), Status::Pass);
        let rules: Vec<&str> = report.findings.iter().map(|f| f.rule).collect();
        assert_eq!(rules, RULES);
    }

    #[test]
    fn a_wrong_name_fails() {
        let mut workflow = standard();
        workflow.name = Some("rust".into());
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI2"), Status::Fail);
        assert_eq!(status(&report, "CI8"), Status::Skipped);
    }

    #[test]
    fn an_unknown_job_id_fails() {
        let mut workflow = standard();
        let extra = job("build", &["lint"], None, &[]);
        workflow.jobs.insert("build".into(), extra);
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI3"), Status::Fail);
        // gate does not need build either.
        assert_eq!(status(&report, "CI7"), Status::Fail);
    }

    #[test]
    fn lint_needs_both_fixed_commands_but_ignores_spacing() {
        let mut workflow = standard();
        workflow.jobs.get_mut("lint").unwrap().runs = vec![
            "cargo  fmt --all -- --check".into(),
            "cargo clippy --workspace \\\n  --all-targets --locked -- -D warnings".into(),
        ];
        // A line continuation is not the same command text.
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI4"), Status::Fail);

        workflow.jobs.get_mut("lint").unwrap().runs = vec![
            "cargo  fmt --all -- --check".into(),
            "cargo clippy --workspace\n  --all-targets --locked -- -D warnings".into(),
        ];
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI4"), Status::Pass);
    }

    #[test]
    fn a_test_job_must_need_lint() {
        let mut workflow = standard();
        workflow
            .jobs
            .get_mut("test-workspace")
            .unwrap()
            .needs
            .clear();
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI5"), Status::Fail);
        // The e2e job still waits for test-workspace.
        assert_eq!(status(&report, "CI6"), Status::Pass);
    }

    #[test]
    fn an_e2e_job_may_wait_for_a_test_job_through_another_job() {
        let mut workflow = standard();
        workflow.jobs.insert(
            "e2e-image".into(),
            job("e2e: image", &["test-workspace"], None, &[]),
        );
        workflow.jobs.get_mut("e2e-cluster").unwrap().needs = vec!["e2e-image".into()];
        workflow
            .jobs
            .get_mut("gate")
            .unwrap()
            .needs
            .push("e2e-image".into());
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI6"), Status::Pass);

        workflow.jobs.get_mut("e2e-image").unwrap().needs = vec!["lint".into()];
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI6"), Status::Fail);
    }

    #[test]
    fn the_gate_must_always_run_and_need_every_job() {
        let mut workflow = standard();
        let gate = workflow.jobs.get_mut("gate").unwrap();
        gate.condition = None;
        gate.needs = vec!["lint".into()];
        let report = check(Some(&workflow), None, &[]);
        let finding = report.findings.iter().find(|f| f.rule == "CI7").unwrap();
        assert_eq!(finding.status, Status::Fail);
        assert!(finding.detail.contains("always()"));
        assert!(finding.detail.contains("e2e-cluster, test-workspace"));
    }

    #[test]
    fn the_gate_must_check_the_results() {
        let mut workflow = standard();
        workflow.jobs.get_mut("gate").unwrap().runs = vec!["true".into()];
        let report = check(Some(&workflow), None, &[]);
        assert_eq!(status(&report, "CI7"), Status::Fail);
    }

    #[test]
    fn the_ruleset_must_require_only_gate() {
        let old = vec!["gate".to_string(), "workspace-contracts".to_string()];
        let report = check(Some(&standard()), Some(&old), &[]);
        assert_eq!(status(&report, "CI8"), Status::Fail);
        let report = check(Some(&standard()), Some(&[]), &[]);
        assert_eq!(status(&report, "CI8"), Status::Fail);
    }

    #[test]
    fn an_exception_turns_a_failure_into_excepted() {
        let exceptions = vec![Exception {
            check: "CI8".into(),
            reason: "no ruleset on a private repository".into(),
        }];
        let report = check(Some(&standard()), Some(&[]), &exceptions);
        assert!(report.passed);
        let finding = report.findings.iter().find(|f| f.rule == "CI8").unwrap();
        assert_eq!(finding.status, Status::Excepted);
        assert!(finding.detail.contains("private repository"));
    }

    #[test]
    fn a_bad_exception_entry_fails() {
        let exceptions = vec![
            Exception {
                check: "CI99".into(),
                reason: "x".into(),
            },
            Exception {
                check: "CI4".into(),
                reason: " ".into(),
            },
        ];
        let report = check(Some(&standard()), Some(&gate_only()), &exceptions);
        assert!(!report.passed);
        let finding = report.findings.iter().find(|f| f.rule == "CI9").unwrap();
        assert!(finding.detail.contains("CI99"));
        assert!(finding.detail.contains("no reason"));
    }
}
