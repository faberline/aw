//! The parts of a GitHub Actions workflow that the CI rules read.

use std::collections::BTreeMap;

/// One workflow file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Workflow {
    /// The top-level `name:`.
    pub name: Option<String>,
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
}

/// One opt-out from `[[standards.ci.exception]]` in `aw.toml`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exception {
    /// A check id, for example `CI8`.
    pub check: String,
    pub reason: String,
}
