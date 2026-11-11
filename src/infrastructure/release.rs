//! Adapters of the release gate: the GitHub REST API through `gh`, and the
//! YAML reader for the trigger of a workflow file.

use serde_json::{Value, json};
use serde_norway::Value as Yaml;

use crate::domain::release::digest::strict_json;
use crate::domain::release::fail;
use crate::domain::release::ports::{Provider, WorkflowParser};
use crate::domain::release::tag::PushTrigger;
use crate::domain::work::ports::PortResult;
use crate::infrastructure::work::process;

/// `gh api` for one repository.
pub struct GhRelease {
    repo: String,
}

impl GhRelease {
    pub fn new(repo: String) -> Self {
        GhRelease { repo }
    }
}

impl Provider for GhRelease {
    fn repo(&self) -> &str {
        &self.repo
    }

    fn get(&self, path: &str) -> PortResult<Option<Value>> {
        match process::run("gh", &["api", "--method", "GET", path], None) {
            Ok(text) => strict_json(&text).map(Some),
            Err(err) if err.0.contains("(HTTP 404)") => Ok(None),
            Err(err) => Err(err),
        }
    }

    fn create_tag(&self, tag: &str, sha: &str) -> PortResult<Value> {
        let path = format!("repos/{}/git/refs", self.repo);
        let body = json!({"ref": format!("refs/tags/{tag}"), "sha": sha}).to_string();
        let text = process::run(
            "gh",
            &["api", "--method", "POST", &path, "--input", "-"],
            Some(&body),
        )?;
        strict_json(&text)
    }
}

/// Reads `on.push` of a workflow with a YAML parser.
pub struct YamlWorkflows;

/// The text of a mapping key. A key that is not text gets a name that no
/// rule allows, so the trigger is refused.
fn key(value: &Yaml) -> String {
    match value {
        Yaml::String(text) => text.clone(),
        other => format!("<{other:?}>"),
    }
}

impl WorkflowParser for YamlWorkflows {
    fn push_trigger(&self, text: &str) -> PortResult<Option<PushTrigger>> {
        let document: Yaml = serde_norway::from_str(text)
            .map_err(|e| fail(&format!("workflow is not one YAML document: {e}")))?;
        let Yaml::Mapping(document) = document else {
            return Err(fail("workflow is not a YAML mapping"));
        };
        // YAML 1.1 readers take the key `on` as the boolean `true`.
        let on = document
            .iter()
            .find(|(k, _)| {
                matches!(k, Yaml::String(s) if s == "on") || matches!(k, Yaml::Bool(true))
            })
            .map(|(_, v)| v);
        let Some(Yaml::Mapping(on)) = on else {
            return Ok(None);
        };
        let push = on
            .iter()
            .find(|(k, _)| matches!(k, Yaml::String(s) if s == "push"))
            .map(|(_, v)| v);
        let Some(Yaml::Mapping(push)) = push else {
            return Ok(None);
        };
        let tags = push
            .iter()
            .find(|(k, _)| matches!(k, Yaml::String(s) if s == "tags"))
            .and_then(|(_, v)| v.as_sequence())
            .and_then(|rows| {
                rows.iter()
                    .map(|row| row.as_str().map(str::to_string))
                    .collect::<Option<Vec<String>>>()
            });
        Ok(Some(PushTrigger {
            keys: push.keys().map(key).collect(),
            tags,
        }))
    }
}
