//! The parts of a GitHub Actions workflow that the CI rules read.

use std::collections::BTreeMap;

/// One workflow file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Workflow {
    /// The top-level `name:`.
    pub name: Option<String>,
    /// True when `on:` lists the `pull_request` event.
    pub pull_request: bool,
    /// True when the workflow has a top-level `concurrency:` group.
    pub concurrency: bool,
    /// Job id to job.
    pub jobs: BTreeMap<String, Job>,
}

/// One job of a workflow.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Job {
    /// The display `name:`.
    pub name: Option<String>,
    /// The job ids in `needs:`.
    pub needs: Vec<String>,
    /// The job-level `if:`.
    pub condition: Option<String>,
    /// The `run:` text of every step, in order.
    pub runs: Vec<String>,
    /// True when the job sets `timeout-minutes:`.
    pub timeout: bool,
    /// True when the job calls a reusable workflow (`uses:`). GitHub does not
    /// allow `timeout-minutes:` on such a job; the called workflow has its own.
    pub reusable: bool,
}

/// One opt-out from `[[standards.ci.exception]]` in `aw.toml`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exception {
    /// A check id, for example `CI8`.
    pub check: String,
    pub reason: String,
}
