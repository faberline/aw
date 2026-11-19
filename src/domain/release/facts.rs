//! The facts of one app that the release workflows must match. The service
//! repository sets them under `[release.apps.<app>]` in `aw.toml`; they
//! replace the `APPS` table of the Python scripts and `apps.sh`.

use std::collections::BTreeMap;

/// The workflow folder of a checkout.
pub const WORKFLOWS: &str = ".github/workflows";

/// The facts of one app.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AppFacts {
    pub name: String,
    /// False until the release workflows of the app land.
    pub onboarded: bool,
    /// The folder of the release scripts, from the repository root.
    pub scripts_dir: String,
    /// The scripts take `--app <name>`.
    pub shared_scripts: bool,
    /// The Rust target triples of the release archives.
    pub targets: Vec<String>,
    /// The job ids of the candidate workflow.
    pub candidate_jobs: Vec<String>,
    /// The promotion inputs are `<prefix>_b64`, `<prefix>_sha256`, ...
    pub promotion_prefix: String,
    /// The promotion takes `candidate_run_attempt`.
    pub takes_attempt: bool,
    pub manifest_schema: String,
    pub receipt_name: String,
    /// The registry folder of the image: `ghcr.io/<owner>`.
    pub image_owner: String,
    /// The one line of the release notes that the public verifier expects.
    pub compatibility_line: String,
    /// The folder of the app in the repository (`.` for a one-app repository).
    pub root: String,
    /// The GitHub repository, `owner/name`.
    pub repository: String,
    /// The receipt schema, `<app>.gke-release-receipt/v1`.
    pub receipt_schema: String,
    /// The harness that makes the receipt: `gke-acceptance` or `gcp`.
    pub gke_backend: String,
    /// The functional fields that a receipt carries as passed.
    pub functional: Vec<String>,
    /// The name of the step of the gates job that runs the declared gates.
    pub gates_step: String,
    /// Names (jobs and steps) the acceptance workflow must carry. Empty:
    /// the acceptance workflow is not checked (A1).
    pub acceptance_names: Vec<String>,
    /// The `run-name:` line of the acceptance workflow, `{app}` for the app
    /// name. Empty: not checked.
    pub acceptance_run_name: String,
    /// Constants of the receipt maker and their values, besides
    /// `REPOSITORY` and `IMAGE_OWNER`, which come from the facts above.
    pub maker_constants: BTreeMap<String, String>,
    /// The lines the candidate gates job must run. Empty: not checked.
    pub candidate_gates: Vec<String>,
    /// The lines `ci.yml` must run. Empty: not checked.
    pub ci_gates: Vec<String>,
    /// Backend `gcp`: the schema of the acceptance block of the app.
    pub acceptance_schema: String,
    /// Backend `gcp`: companion images that the run must carry, by name.
    pub image_companions: Vec<String>,
    /// Backend `gcp`: companions that the functional receipt must carry.
    pub acceptance_companions: Vec<String>,
    /// Backend `gcp`: the keys of the acceptance block besides `functional`.
    pub acceptance_extra_keys: Vec<String>,
}

impl AppFacts {
    /// The facts that every app of the shared lineage has, from its name.
    pub fn shared(name: &str) -> Self {
        AppFacts {
            name: name.to_string(),
            onboarded: true,
            scripts_dir: "scripts/release".into(),
            shared_scripts: true,
            targets: Vec::new(),
            candidate_jobs: [
                "identity",
                "build",
                &format!("{name}-release-gates"),
                "ghcr-image-and-attest",
                "manifest",
                "verify-candidate",
                "result",
            ]
            .map(String::from)
            .to_vec(),
            promotion_prefix: "gke_receipt".into(),
            takes_attempt: true,
            manifest_schema: format!("cclab.{name}.candidate-manifest.v1"),
            receipt_name: format!("{name}-gke-receipt.json"),
            image_owner: "ghcr.io/faberline".into(),
            compatibility_line: String::new(),
            root: ".".into(),
            repository: format!("faberline/{name}"),
            receipt_schema: format!("{name}.gke-release-receipt/v1"),
            gke_backend: "gke-acceptance".into(),
            functional: ["readyz", "round_trip", "durability"]
                .map(String::from)
                .to_vec(),
            acceptance_names: Vec::new(),
            acceptance_run_name: String::new(),
            maker_constants: BTreeMap::new(),
            gates_step: String::new(),
            candidate_gates: Vec::new(),
            ci_gates: Vec::new(),
            acceptance_schema: String::new(),
            image_companions: Vec::new(),
            acceptance_companions: Vec::new(),
            acceptance_extra_keys: Vec::new(),
        }
    }

    /// The inputs of the promotion workflow, sorted.
    pub fn promotion_inputs(&self) -> Vec<String> {
        let prefix = &self.promotion_prefix;
        let mut names = vec!["version".to_string(), "candidate_run_id".to_string()];
        if self.takes_attempt {
            names.push("candidate_run_attempt".into());
        }
        for tail in ["b64", "sha256", "sidecar_b64", "sidecar_sha256"] {
            names.push(format!("{prefix}_{tail}"));
        }
        names.sort();
        names
    }

    /// The job that runs the declared gates of the candidate.
    pub fn gates_job(&self) -> String {
        format!("{}-release-gates", self.name)
    }

    /// The path of the candidate workflow, from the repository root.
    pub fn candidate_workflow(&self) -> String {
        format!("{WORKFLOWS}/{}-release-candidate.yml", self.name)
    }

    /// The path of the promotion workflow, from the repository root.
    pub fn promotion_workflow(&self) -> String {
        format!("{WORKFLOWS}/{}-release.yml", self.name)
    }
}
