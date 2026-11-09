//! Parse `ci.yml` and the `aw.toml` exceptions into domain types.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_norway::Value;

use crate::domain::ci::workflow::{Exception, Job, Workflow};
use crate::domain::work::ports::{PortError, PortResult};

#[derive(Deserialize)]
struct RawWorkflow {
    name: Option<String>,
    #[serde(default)]
    jobs: BTreeMap<String, RawJob>,
}

#[derive(Deserialize)]
struct RawJob {
    name: Option<String>,
    #[serde(default)]
    needs: Needs,
    #[serde(rename = "if")]
    condition: Option<Value>,
    #[serde(default)]
    steps: Vec<RawStep>,
}

/// `needs:` is one job id or a list of job ids.
#[derive(Deserialize)]
#[serde(untagged)]
enum Needs {
    One(String),
    Many(Vec<String>),
}

impl Default for Needs {
    fn default() -> Self {
        Needs::Many(Vec::new())
    }
}

#[derive(Deserialize)]
struct RawStep {
    run: Option<String>,
}

fn scalar(value: Value) -> Option<String> {
    match value {
        Value::String(s) => Some(s),
        Value::Bool(b) => Some(b.to_string()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

/// Parse the text of a workflow file.
pub fn workflow(text: &str) -> PortResult<Workflow> {
    let raw: RawWorkflow = serde_norway::from_str(text)
        .map_err(|e| PortError(format!("could not parse ci.yml: {e}")))?;
    let jobs = raw
        .jobs
        .into_iter()
        .map(|(id, job)| {
            let needs = match job.needs {
                Needs::One(one) => vec![one],
                Needs::Many(many) => many,
            };
            let job = Job {
                name: job.name,
                needs,
                condition: job.condition.and_then(scalar),
                runs: job.steps.into_iter().filter_map(|s| s.run).collect(),
            };
            (id, job)
        })
        .collect();
    Ok(Workflow {
        name: raw.name,
        jobs,
    })
}

#[derive(Default, Deserialize)]
struct AwToml {
    #[serde(default)]
    standards: Standards,
}

#[derive(Default, Deserialize)]
struct Standards {
    #[serde(default)]
    ci: CiStandard,
}

#[derive(Default, Deserialize)]
struct CiStandard {
    #[serde(default)]
    exception: Vec<RawException>,
}

#[derive(Deserialize)]
struct RawException {
    check: String,
    #[serde(default)]
    reason: String,
}

/// `[[standards.ci.exception]]` entries from the text of `aw.toml`.
pub fn exceptions(text: &str) -> PortResult<Vec<Exception>> {
    let raw: AwToml =
        toml::from_str(text).map_err(|e| PortError(format!("could not parse aw.toml: {e}")))?;
    Ok(raw
        .standards
        .ci
        .exception
        .into_iter()
        .map(|e| Exception {
            check: e.check,
            reason: e.reason,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    const CI: &str = r#"
name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  lint:
    name: "lint: fmt, clippy"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo fmt --all -- --check
      - name: clippy
        run: |
          cargo clippy --workspace --all-targets --locked -- -D warnings
  test-workspace:
    needs: lint
    steps:
      - run: cargo test --workspace --locked
  gate:
    name: gate
    if: ${{ always() }}
    needs: [lint, test-workspace]
    steps:
      - run: echo done
"#;

    #[test]
    fn a_workflow_becomes_jobs_with_needs_conditions_and_runs() {
        let parsed = workflow(CI).unwrap();
        assert_eq!(parsed.name.as_deref(), Some("ci"));
        let ids: Vec<&str> = parsed.jobs.keys().map(String::as_str).collect();
        assert_eq!(ids, ["gate", "lint", "test-workspace"]);
        let lint = &parsed.jobs["lint"];
        assert_eq!(lint.name.as_deref(), Some("lint: fmt, clippy"));
        assert_eq!(lint.runs.len(), 2);
        assert_eq!(parsed.jobs["test-workspace"].needs, ["lint"]);
        let gate = &parsed.jobs["gate"];
        assert_eq!(gate.needs, ["lint", "test-workspace"]);
        assert_eq!(gate.condition.as_deref(), Some("${{ always() }}"));
    }

    #[test]
    fn bad_yaml_names_the_file() {
        let err = workflow("jobs: [").unwrap_err();
        assert!(err.0.contains("ci.yml"), "{err}");
    }

    #[test]
    fn exceptions_come_from_the_ci_table_only() {
        let text = r#"
[project]
kind = "saas-service"

[[standards.core_reuse.exception]]
check = "CR3"
reason = "other standard"

[[standards.ci.exception]]
check = "CI8"
reason = "no ruleset yet"
"#;
        let parsed = exceptions(text).unwrap();
        assert_eq!(
            parsed,
            [Exception {
                check: "CI8".into(),
                reason: "no ruleset yet".into()
            }]
        );
        assert!(exceptions("").unwrap().is_empty());
    }
}
