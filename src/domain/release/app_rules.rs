//! The static rules of the candidate and promotion workflows of one app:
//! C1 to C8 and P1 to P7 of `aw release-contract`. Ported from the
//! `check_candidate` and `check_promotion` functions of the defer script
//! `verify-release-contract.py`; the facts come from [`AppFacts`].

use std::collections::BTreeSet;

use regex::Regex;
use serde::Serialize;

use super::facts::AppFacts;
use super::workflow::{self as wf, Reason};

/// One broken rule. The first broken rule of a workflow stops its check,
/// as the Python script does.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Fail {
    pub rule: &'static str,
    pub message: String,
}

type Check = Result<(), Fail>;

fn fail(rule: &'static str, message: impl Into<String>) -> Check {
    Err(Fail {
        rule,
        message: message.into(),
    })
}

/// Tie a reader error to a rule.
fn at<T>(rule: &'static str, value: Result<T, Reason>) -> Result<T, Fail> {
    value.map_err(|message| Fail { rule, message })
}

fn sorted(values: &[String]) -> Vec<String> {
    let mut out = values.to_vec();
    out.sort();
    out.dedup();
    out
}

/// A Rust target triple.
fn triple(name: &str) -> bool {
    Regex::new(r"^[a-z0-9_]+-(?:unknown|apple|pc)-[a-z]+(?:-[a-z0-9]+)?$")
        .expect("a fixed pattern is valid")
        .is_match(name)
}

/// The triples in `<app>-candidate-<triple>-${{ github.run_id }}` names.
fn referenced_targets(app: &AppFacts, text: &str) -> BTreeSet<String> {
    let pattern = format!(
        r"{}-candidate-([A-Za-z0-9_-]+?)-\$\{{\{{ github\.run_id \}}\}}",
        regex::escape(&app.name)
    );
    Regex::new(&pattern)
        .expect("a built pattern is valid")
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .filter(|name| triple(name))
        .collect()
}

/// C8: every surface that names targets names exactly the target set of the
/// app: the build matrix, each job that downloads per-target archives and
/// each `for target in` loop. A matrix trimmed without its download steps
/// passes the job-set test and fails only at run time.
fn candidate_targets(app: &AppFacts, text: &str) -> Check {
    let label = format!("{} candidate", app.name);
    let expected: BTreeSet<String> = app.targets.iter().cloned().collect();
    let matrix_row = Regex::new(r"(?m)^\s*- \{ target: ([A-Za-z0-9_-]+),").expect("fixed");
    let matrix: BTreeSet<String> = matrix_row
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .collect();
    if matrix != expected {
        return fail(
            "C8",
            format!(
                "{label}: build matrix targets {matrix:?} differ from the release facts {expected:?}"
            ),
        );
    }
    for job in ["manifest", "verify-candidate", "result"] {
        let seen = referenced_targets(app, &at("C8", wf::job_block(text, job))?);
        if !seen.is_empty() && seen != expected {
            return fail(
                "C8",
                format!(
                    "{label}: job {job} downloads targets {seen:?}, the release facts say {expected:?}"
                ),
            );
        }
    }
    let loops: Vec<String> = Regex::new(r"for target in ([A-Za-z0-9_ -]+); do")
        .expect("fixed")
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .collect();
    if loops.is_empty() {
        return fail(
            "C8",
            format!(
                "{label}: no `for target in ...; do` loop binds the archives into the manifest"
            ),
        );
    }
    for each in loops {
        let set: BTreeSet<String> = each.split_whitespace().map(String::from).collect();
        if set != expected {
            return fail(
                "C8",
                format!(
                    "{label}: loop targets {:?} differ from the release facts {expected:?}",
                    each.split_whitespace().collect::<Vec<_>>()
                ),
            );
        }
    }
    Ok(())
}

/// C1 to C8: the candidate workflow `<app>-release-candidate.yml`.
pub fn candidate(app: &AppFacts, text: &str) -> Check {
    let label = format!("{} candidate", app.name);
    let triggers = at("C1", wf::triggers(text))?;
    if triggers != ["workflow_dispatch"] {
        return fail(
            "C1",
            format!("{label}: the only trigger must be workflow_dispatch, got {triggers:?}"),
        );
    }
    let inputs = at("C2", wf::inputs(text))?;
    if inputs != ["commit", "version"] {
        return fail(
            "C2",
            format!("{label}: inputs must be exactly version and commit, got {inputs:?}"),
        );
    }
    let jobs = at("C3", wf::job_ids(text))?;
    if jobs != sorted(&app.candidate_jobs) {
        return fail(
            "C3",
            format!(
                "{label}: jobs must be {:?}, got {jobs:?}",
                sorted(&app.candidate_jobs)
            ),
        );
    }
    at("C4", wf::require_pinned(&label, text))?;
    let name = &app.name;
    let required = [
        format!("group: {name}-release-candidate-${{{{ inputs.version }}}}"),
        app.manifest_schema.clone(),
        format!("{}/{name}", app.image_owner),
        format!("refs/tags/{name}@"),
        r#"[[ "$GITHUB_REF" == "refs/heads/main" ]]"#.to_string(),
        r#"[[ "$REQUESTED_COMMIT" == "$GITHUB_SHA" ]]"#.to_string(),
        format!(".github/workflows/{name}-release-candidate.yml@refs/heads/main"),
    ];
    for fragment in required {
        if !text.contains(&fragment) {
            return fail(
                "C5",
                format!("{label}: required fragment absent: {fragment:?}"),
            );
        }
    }
    let verify = at("C6", wf::job_block(text, "verify-candidate"))?;
    let verifier = format!("{}/verify-release-candidate.sh", app.scripts_dir);
    if !verify.contains(&verifier) {
        return fail(
            "C6",
            format!("{label}: verify-candidate must run {verifier}"),
        );
    }
    if !verify.contains("--mode full") {
        return fail(
            "C6",
            format!("{label}: verify-candidate must run the verifier in full mode"),
        );
    }
    if app.shared_scripts && !verify.contains(&format!("--app {name}")) {
        return fail(
            "C6",
            format!("{label}: the shared verifier must be told --app {name}"),
        );
    }
    let result = at("C7", wf::job_block(text, "result"))?;
    let needs = at("C7", wf::needs(&result))?;
    let expected: BTreeSet<&String> = app
        .candidate_jobs
        .iter()
        .filter(|j| *j != "result")
        .collect();
    let seen: BTreeSet<&String> = needs.iter().collect();
    if needs.len() != seen.len() || seen != expected {
        return fail(
            "C7",
            format!("{label}: result must need exactly {expected:?}, got {needs:?}"),
        );
    }
    for job in &app.candidate_jobs {
        let pattern = if job == "result" {
            r#"(?:"result"|result):"success""#.to_string()
        } else {
            let j = regex::escape(job);
            format!(r#"(?:"{j}"|{j}):"\$\{{\{{ needs\.{j}\.result \}}\}}""#)
        };
        if !Regex::new(&pattern).expect("built").is_match(&result) {
            return fail(
                "C7",
                format!("{label}: the final manifest must bind job {job} to its own result"),
            );
        }
    }
    candidate_targets(app, text)
}

/// P1 to P7: the promotion workflow `<app>-release.yml`.
pub fn promotion(app: &AppFacts, text: &str) -> Check {
    let label = format!("{} promotion", app.name);
    let name = &app.name;
    let triggers = at("P1", wf::triggers(text))?;
    if triggers != ["workflow_dispatch"] {
        return fail(
            "P1",
            format!("{label}: the only trigger must be workflow_dispatch, got {triggers:?}"),
        );
    }
    let inputs = at("P2", wf::inputs(text))?;
    if inputs != app.promotion_inputs() {
        return fail(
            "P2",
            format!(
                "{label}: inputs must be {:?}, got {inputs:?}",
                app.promotion_inputs()
            ),
        );
    }
    let jobs = at("P3", wf::job_ids(text))?;
    if jobs != ["publish-release", "verify-inputs"] {
        return fail(
            "P3",
            format!("{label}: jobs must be verify-inputs and publish-release, got {jobs:?}"),
        );
    }
    at("P4", wf::require_pinned(&label, text))?;
    at("P4", wf::require_no_rebuild(&label, text))?;
    let required = [
        format!("group: {name}-release-promotion-${{{{ inputs.version }}}}"),
        format!("refs/tags/{name}@${{{{ inputs.version }}}}"),
        app.receipt_name.clone(),
        format!("{}/{name}", app.image_owner),
        format!("{}/verify-release-artifacts.sh", app.scripts_dir),
        "--mode candidate".to_string(),
        "--mode public".to_string(),
    ];
    for fragment in required {
        if !text.contains(&fragment) {
            return fail(
                "P5",
                format!("{label}: required fragment absent: {fragment:?}"),
            );
        }
    }
    if app.shared_scripts {
        if !text.contains(&format!("--app {name}")) {
            return fail(
                "P6",
                format!("{label}: the shared artifact verifier must be told --app {name}"),
            );
        }
        // The shared public verifier refuses notes without exactly one
        // compatibility line; catch that before a promotion run.
        let count = text
            .lines()
            .filter(|l| l.trim() == app.compatibility_line)
            .count();
        if count != 1 {
            return fail(
                "P6",
                format!(
                    "{label}: release notes must carry the compatibility line of the release facts exactly once, got {count}"
                ),
            );
        }
    }
    if app.takes_attempt && !text.contains("--candidate-run-attempt") {
        return fail(
            "P6",
            format!("{label}: the artifact verifier must receive the candidate run attempt"),
        );
    }
    let creates: Vec<&str> = text
        .lines()
        .filter(|l| l.contains("docker buildx imagetools create"))
        .map(str::trim)
        .collect();
    if creates.len() != 2 {
        return fail(
            "P7",
            format!(
                "{label}: expected exactly two imagetools create lines (semver, latest), got {}",
                creates.len()
            ),
        );
    }
    for line in creates {
        if !line.ends_with(r#""$root_ref""#) {
            return fail(
                "P7",
                format!("{label}: every retag must source the candidate root digest: {line}"),
            );
        }
    }
    Ok(())
}
