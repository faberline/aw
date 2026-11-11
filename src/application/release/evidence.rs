//! The proof of one tag: the reviewed workflows at the tagged commit and
//! the runs, attempts and jobs that the tag push started.

use std::collections::BTreeSet;

use serde_json::{Value, json};

use super::api::Api;
use crate::domain::release::policy::{GateWorkflow, Policy};
use crate::domain::release::ports::WorkflowParser;
use crate::domain::release::require;
use crate::domain::release::state::FAILURES;
use crate::domain::release::tag::{accepts_tag, encode};
use crate::domain::work::ports::PortResult;

/// What the reads of the release gate need.
pub struct Gate<'a> {
    pub api: &'a Api<'a>,
    pub workflows: &'a dyn WorkflowParser,
}

/// The fields of a run that must not change between the list and the read
/// of the one run.
const STABLE: [&str; 7] = [
    "id",
    "workflow_id",
    "head_sha",
    "head_branch",
    "run_attempt",
    "status",
    "conclusion",
];

fn failure(conclusion: &Value) -> bool {
    conclusion.as_str().is_some_and(|c| FAILURES.contains(&c))
}

/// Stop unless the workflow file at `commit` is the reviewed definition and
/// a push of `tag` starts it. `unreviewed` is the reason for a changed file.
fn reviewed(
    g: &Gate<'_>,
    workflow: &GateWorkflow,
    commit: &str,
    tag: &str,
    unreviewed: &str,
) -> PortResult<()> {
    let (text, sha) = g.api.text(&workflow.path, commit)?;
    require(sha == workflow.definition_sha, unreviewed)?;
    accepts_tag(g.workflows.push_trigger(&text)?.as_ref(), tag)
}

/// Stop unless every workflow of `policy` at `commit` is the reviewed
/// definition and a push of `tag` starts it. Do this before a tag write: a
/// tag that cannot start the full tests can never pass.
pub fn entry(g: &Gate<'_>, policy: &Policy, commit: &str, tag: &str) -> PortResult<()> {
    policy
        .workflows
        .iter()
        .try_for_each(|w| reviewed(g, w, commit, tag, "unreviewed release workflow"))
}

/// The verdict for `tag` at the commit `head`: `success`, `pending` or
/// `failure`, with the reasons, the failed jobs and the runs that were
/// read. Only a complete, successful latest attempt of every workflow is
/// `success`. A missing, running, cancelled, skipped or unknown job is
/// `pending`: it never passes.
pub fn full_evidence(g: &Gate<'_>, policy: &Policy, tag: &str, head: &str) -> PortResult<Value> {
    let api = g.api;
    // The run API reports `head_branch`, so a branch with the name of the
    // tag makes the runs ambiguous.
    require(
        api.optional(&format!("/git/ref/heads/{}", encode(tag)))?
            .is_none(),
        "tag name is ambiguous with a branch",
    )?;
    let mut status = "success";
    let mut reasons: Vec<String> = Vec::new();
    let mut failures: Vec<Value> = Vec::new();
    let mut runs: Vec<Value> = Vec::new();
    for workflow in &policy.workflows {
        let path = workflow.path.as_str();
        reviewed(
            g,
            workflow,
            head,
            tag,
            "unreviewed tagged workflow definition",
        )?;
        let rows = api.pages(
            &format!(
                "/actions/workflows/{}/runs?head_sha={head}&event=push",
                workflow.id
            ),
            Some("workflow_runs"),
        )?;
        let ids: BTreeSet<u64> = rows
            .iter()
            .filter_map(|r| r["id"].as_u64().filter(|id| *id > 0))
            .collect();
        require(ids.len() == rows.len(), "unstable test-run inventory")?;
        let run = rows
            .iter()
            .filter(|r| {
                r["event"] == "push"
                    && r["head_branch"] == tag
                    && r["head_sha"] == head
                    && r["workflow_id"].as_u64() == Some(workflow.id)
                    && r["path"].as_str().and_then(|p| p.split('@').next()) == Some(path)
                    && r["head_repository"]["full_name"] == api.repo()
            })
            .max_by_key(|r| r["id"].as_u64());
        let Some(run) = run else {
            reasons.push(format!("{path}: full tag run is missing"));
            if status != "failure" {
                status = "pending";
            }
            continue;
        };
        let id = run["id"].as_u64().unwrap_or(0);
        let current = api.get(&format!("/actions/runs/{id}"))?;
        require(
            STABLE.iter().all(|key| current[key] == run[key]),
            "test run changed during verification",
        )?;
        let attempt = run["run_attempt"].as_u64().unwrap_or(0);
        require(attempt > 0, "missing test attempt")?;
        let jobs = api.pages(
            &format!("/actions/runs/{id}/attempts/{attempt}/jobs"),
            Some("jobs"),
        )?;
        require(
            jobs.iter().all(|j| {
                j["run_id"].as_u64() == Some(id)
                    && j["run_attempt"].as_u64() == Some(attempt)
                    && j["head_sha"] == head
            }),
            "stale or foreign job attempt",
        )?;
        runs.push(json!({
            "id": id, "attempt": attempt, "workflow": path, "head": head, "tag": tag,
        }));
        let mut failed = Vec::new();
        for job in jobs.iter().filter(|j| failure(&j["conclusion"])) {
            let job_id = job["id"].as_u64().unwrap_or(0);
            let url = format!(
                "https://github.com/{}/actions/runs/{id}/job/{job_id}",
                api.repo()
            );
            require(
                job_id > 0 && job["html_url"] == url,
                "failure job URL differs",
            )?;
            let steps: Vec<&Value> = job["steps"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|s| failure(&s["conclusion"]))
                .map(|s| &s["name"])
                .collect();
            failed.push(json!({
                "workflow": path, "run": id, "attempt": attempt, "job": job_id,
                "name": job["name"], "conclusion": job["conclusion"], "url": url,
                "steps": steps,
            }));
        }
        if run["status"] != "completed" {
            reasons.push(format!("{path}: latest attempt is running"));
            if status != "failure" {
                status = "pending";
            }
            continue;
        }
        if !failed.is_empty() || failure(&run["conclusion"]) {
            failures.extend(failed);
            status = "failure";
            reasons.push(format!("{path}: confirmed full-test failure"));
            continue;
        }
        let names: BTreeSet<&str> = jobs.iter().filter_map(|j| j["name"].as_str()).collect();
        let complete = !jobs.is_empty()
            && names.len() == jobs.len()
            && names.len() == workflow.jobs.len()
            && names.iter().all(|name| workflow.jobs.contains_key(*name))
            && jobs
                .iter()
                .all(|j| j["status"] == "completed" && j["conclusion"] == "success")
            && run["conclusion"] == "success";
        if !complete {
            reasons.push(format!(
                "{path}: missing, cancelled, skipped or unknown full-test jobs"
            ));
            if status != "failure" {
                status = "pending";
            }
        }
    }
    Ok(json!({
        "status": status, "reasons": reasons, "failures": failures, "runs": runs,
    }))
}
