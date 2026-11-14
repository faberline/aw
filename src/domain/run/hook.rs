//! What the Claude Code hook sends on stdin.

use std::path::{Component, Path};

use serde_json::{Map, Value};

use super::ports::RunError;

/// The fields of one hook payload that a run record keeps.
///
/// Claude Code sends snake_case names (`session_id`, `cwd`, `prompt`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HookInput {
    pub session: Option<String>,
    pub cwd: Option<String>,
    pub prompt: Option<String>,
    pub transcript: Option<String>,
    pub reason: Option<String>,
    pub error: Option<String>,
    pub last_message: Option<String>,
}

impl HookInput {
    pub fn parse(text: &str) -> Result<Self, RunError> {
        let value: Value = serde_json::from_str(text)
            .map_err(|e| RunError(format!("hook input is not JSON: {e}")))?;
        let Value::Object(map) = value else {
            return Err(RunError("hook input is not a JSON object".into()));
        };
        Ok(HookInput {
            session: field(&map, "session_id"),
            cwd: field(&map, "cwd"),
            prompt: field(&map, "prompt"),
            transcript: field(&map, "transcript_path"),
            reason: field(&map, "reason"),
            error: field(&map, "error"),
            last_message: field(&map, "last_assistant_message"),
        })
    }
}

/// The value of `name` when it is not empty. A value that is not a string
/// is kept as JSON text.
fn field(map: &Map<String, Value>, name: &str) -> Option<String> {
    match map.get(name)? {
        Value::Null => None,
        Value::String(s) if s.trim().is_empty() => None,
        Value::String(s) => Some(s.clone()),
        Value::Array(a) if a.is_empty() => None,
        Value::Object(o) if o.is_empty() => None,
        other => Some(other.to_string()),
    }
}

/// The worker clone that holds `dir`, as `<agent>/<repo>` relative to `root`.
pub fn clone_of(root: &Path, dir: &Path) -> Option<String> {
    let rest = dir.strip_prefix(root).ok()?;
    let parts: Vec<String> = rest
        .components()
        .take(2)
        .filter_map(|c| match c {
            Component::Normal(part) => Some(part.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect();
    (!parts.is_empty()).then(|| parts.join("/"))
}

/// The prompt of an hourly worker automation. A Claude scheduled task wraps
/// its prompt in `<scheduled-task name="aw-worker-…">`. A routine sends
/// the one-line prompt itself, such as `執行 aw-worker skill。`.
pub fn is_worker_prompt(prompt: &str) -> bool {
    let prompt = prompt.trim_start();
    prompt.starts_with("<scheduled-task name=\"aw-worker")
        || (prompt.chars().count() <= 80 && prompt.contains("aw-worker skill"))
}
