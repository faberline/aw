//! The gates policy, `.github/aw-release-gates.json`: the reviewed release
//! workflows and the jobs that each tag run must pass.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use super::digest::is_sha;
use super::require;
use crate::domain::work::ports::PortResult;

/// Where the policy is, in the repository.
pub const POLICY_PATH: &str = ".github/aw-release-gates.json";

/// The kinds of a job. The three test kinds must each appear.
pub const KINDS: [&str; 4] = ["unit", "functional_e2e", "performance", "policy"];
const TEST_KINDS: [&str; 3] = ["unit", "functional_e2e", "performance"];

/// One reviewed workflow.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GateWorkflow {
    /// `.github/workflows/<file>`.
    pub path: String,
    /// The workflow id that the code host gave.
    pub id: u64,
    /// The git blob id of the reviewed workflow file.
    pub definition_sha: String,
    /// Job display name to kind. A run must have exactly these jobs.
    pub jobs: BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Policy {
    pub workflows: Vec<GateWorkflow>,
}

fn keys(value: &Value, expected: &[&str]) -> bool {
    value.as_object().is_some_and(|map| {
        map.len() == expected.len() && expected.iter().all(|k| map.contains_key(*k))
    })
}

fn workflow_path(path: &str) -> bool {
    path.strip_prefix(".github/workflows/").is_some_and(|name| {
        (name.ends_with(".yml") || name.ends_with(".yaml"))
            && !name.starts_with('.')
            && !name.contains("..")
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
    })
}

impl Policy {
    /// Read version 1 of the policy for `repo`. Any unknown or missing key
    /// stops the release.
    pub fn parse(value: &Value, repo: &str) -> PortResult<Policy> {
        require(
            keys(value, &["version", "repo", "workflows"])
                && value["version"].as_u64() == Some(1)
                && value["repo"].as_str() == Some(repo),
            "invalid reviewed gates policy",
        )?;
        let rows = value["workflows"]
            .as_array()
            .filter(|rows| !rows.is_empty());
        let mut workflows: Vec<GateWorkflow> = Vec::new();
        let mut names = BTreeSet::new();
        for row in rows.into_iter().flatten() {
            require(
                keys(row, &["path", "id", "definition_sha", "jobs"]),
                "invalid gates policy workflow",
            )?;
            let path = row["path"].as_str().unwrap_or_default();
            let id = row["id"].as_u64().unwrap_or(0);
            let definition_sha = row["definition_sha"].as_str().unwrap_or_default();
            require(
                workflow_path(path)
                    && id > 0
                    && is_sha(definition_sha)
                    && workflows.iter().all(|w| w.path != path && w.id != id),
                "invalid gates policy workflow identity",
            )?;
            let mut jobs = BTreeMap::new();
            for (name, kind) in row["jobs"].as_object().into_iter().flatten() {
                let kind = kind.as_str().unwrap_or_default();
                require(
                    !name.trim().is_empty() && KINDS.contains(&kind) && names.insert(name.clone()),
                    "invalid or duplicate gates policy job",
                )?;
                jobs.insert(name.clone(), kind.to_string());
            }
            require(!jobs.is_empty(), "gates policy workflow has no jobs")?;
            workflows.push(GateWorkflow {
                path: path.to_string(),
                id,
                definition_sha: definition_sha.to_string(),
                jobs,
            });
        }
        require(!workflows.is_empty(), "gates policy has no workflows")?;
        let policy = Policy { workflows };
        require(
            TEST_KINDS
                .iter()
                .all(|kind| policy.kinds().any(|k| k == *kind)),
            "full inventory lacks a required test category",
        )?;
        Ok(policy)
    }

    fn kinds(&self) -> impl Iterator<Item = &str> {
        self.workflows
            .iter()
            .flat_map(|w| w.jobs.values().map(String::as_str))
    }

    /// The check ids: every job name of every workflow.
    pub fn checks(&self) -> BTreeSet<String> {
        self.workflows
            .iter()
            .flat_map(|w| w.jobs.keys().cloned())
            .collect()
    }

    /// Does this (current) policy keep every workflow and every job of the
    /// `tagged` policy, with the same workflow id and job kind?
    pub fn retains(&self, tagged: &Policy) -> bool {
        tagged.workflows.iter().all(|old| {
            self.workflows.iter().any(|new| {
                new.path == old.path
                    && new.id == old.id
                    && old
                        .jobs
                        .iter()
                        .all(|(name, kind)| new.jobs.get(name) == Some(kind))
            })
        })
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn good() -> Value {
        json!({
            "version": 1,
            "repo": "o/r",
            "workflows": [{
                "path": ".github/workflows/release.yml",
                "id": 7,
                "definition_sha": "a".repeat(40),
                "jobs": {"preflight": "policy", "unit": "unit", "e2e": "functional_e2e", "perf": "performance"}
            }]
        })
    }

    fn broken(change: impl FnOnce(&mut Value)) -> String {
        let mut value = good();
        change(&mut value);
        Policy::parse(&value, "o/r").unwrap_err().0
    }

    #[test]
    fn reads_the_reviewed_shape() {
        let policy = Policy::parse(&good(), "o/r").unwrap();
        assert_eq!(policy.workflows[0].id, 7);
        assert_eq!(policy.checks().len(), 4);
        assert!(policy.retains(&policy));
    }

    #[test]
    fn refuses_any_other_shape() {
        assert!(Policy::parse(&good(), "o/other").is_err());
        assert!(broken(|v| v["version"] = json!(2)).contains("invalid reviewed gates policy"));
        assert!(broken(|v| v["extra"] = json!(1)).contains("invalid reviewed gates policy"));
        assert!(broken(|v| v["workflows"] = json!([])).contains("no workflows"));
        assert!(broken(|v| v["workflows"] = json!("x")).contains("no workflows"));
        assert!(
            broken(|v| v["workflows"][0]["more"] = json!(1))
                .contains("invalid gates policy workflow")
        );
        for path in [
            "ci.yml",
            ".github/workflows/../x.yml",
            ".github/workflows/a.txt",
            ".github/workflows/a b.yml",
        ] {
            assert!(
                broken(|v| v["workflows"][0]["path"] = json!(path)).contains("identity"),
                "{path}"
            );
        }
        assert!(broken(|v| v["workflows"][0]["id"] = json!(0)).contains("identity"));
        assert!(broken(|v| v["workflows"][0]["id"] = json!("7")).contains("identity"));
        assert!(
            broken(|v| v["workflows"][0]["definition_sha"] = json!("abc")).contains("identity")
        );
        assert!(broken(|v| v["workflows"][0]["jobs"] = json!({})).contains("no jobs"));
        assert!(broken(|v| v["workflows"][0]["jobs"] = json!([])).contains("no jobs"));
        assert!(broken(|v| v["workflows"][0]["jobs"]["unit"] = json!("fast")).contains("job"));
        assert!(broken(|v| v["workflows"][0]["jobs"][" "] = json!("unit")).contains("job"));
        assert!(
            broken(|v| {
                v["workflows"][0]["jobs"]
                    .as_object_mut()
                    .unwrap()
                    .remove("perf");
            })
            .contains("required test category")
        );
    }

    #[test]
    fn refuses_a_repeated_workflow_or_job() {
        let second = |path: &str, id: u64, job: &str| json!({"path": path, "id": id, "definition_sha": "b".repeat(40), "jobs": {job: "policy"}});
        let with = |row: Value| broken(|v| v["workflows"].as_array_mut().unwrap().push(row));
        assert!(with(second(".github/workflows/release.yml", 8, "x")).contains("identity"));
        assert!(with(second(".github/workflows/other.yml", 7, "x")).contains("identity"));
        assert!(with(second(".github/workflows/other.yml", 8, "unit")).contains("duplicate"));
        let mut two = good();
        two["workflows"].as_array_mut().unwrap().push(second(
            ".github/workflows/other.yaml",
            8,
            "x",
        ));
        assert_eq!(Policy::parse(&two, "o/r").unwrap().workflows.len(), 2);
    }

    #[test]
    fn retains_needs_every_old_workflow_and_job() {
        let tagged = Policy::parse(&good(), "o/r").unwrap();
        let change = |f: &dyn Fn(&mut Value)| {
            let mut value = good();
            f(&mut value);
            Policy::parse(&value, "o/r").unwrap()
        };
        let more = change(&|v| v["workflows"][0]["jobs"]["new"] = json!("unit"));
        assert!(more.retains(&tagged));
        assert!(!tagged.retains(&more));
        assert!(
            change(&|v| v["workflows"][0]["definition_sha"] = json!("c".repeat(40)))
                .retains(&tagged)
        );
        assert!(!change(&|v| v["workflows"][0]["id"] = json!(9)).retains(&tagged));
        assert!(
            !change(&|v| v["workflows"][0]["path"] = json!(".github/workflows/x.yml"))
                .retains(&tagged)
        );
        assert!(
            !change(&|v| {
                v["workflows"][0]["jobs"]["preflight"] = json!("unit");
            })
            .retains(&tagged)
        );
    }
}
