//! What a client's hook sends on stdin, read the same way for all clients.

use std::path::{Component, Path};

use serde_json::{Map, Value};

use super::ports::RunError;

/// The fields of one hook payload that a run record keeps.
///
/// Claude Code and Codex send snake_case names (`session_id`, `cwd`,
/// `prompt`). Antigravity sends camelCase names (`conversationId`,
/// `workspacePaths` as `file://` URIs, `lastUserInput`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HookInput {
    pub session: Option<String>,
    pub cwd: Option<String>,
    /// `cwd` and every workspace path, as plain paths.
    pub dirs: Vec<String>,
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
        let cwd = field(&map, &["cwd"]);
        let mut dirs: Vec<String> = cwd.iter().cloned().collect();
        if let Some(Value::Array(paths)) = map.get("workspacePaths") {
            dirs.extend(
                paths
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|p| p.strip_prefix("file://").unwrap_or(p).to_string()),
            );
        }
        Ok(HookInput {
            session: field(&map, &["session_id", "conversationId"]),
            cwd,
            dirs,
            prompt: field(&map, &["prompt", "lastUserInput"]),
            transcript: field(&map, &["transcript_path", "transcriptPath"]),
            reason: field(&map, &["terminationReason", "reason"]),
            error: field(&map, &["error"]),
            last_message: field(&map, &["last_assistant_message"]),
        })
    }
}

/// The first non-empty value among `names`. A value that is not a string
/// is kept as JSON text.
fn field(map: &Map<String, Value>, names: &[&str]) -> Option<String> {
    names
        .iter()
        .filter_map(|name| map.get(*name))
        .find_map(|value| match value {
            Value::Null => None,
            Value::String(s) if s.trim().is_empty() => None,
            Value::String(s) => Some(s.clone()),
            Value::Array(a) if a.is_empty() => None,
            Value::Object(o) if o.is_empty() => None,
            other => Some(other.to_string()),
        })
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
/// its prompt in `<scheduled-task name="aw-worker-…">`. Other clients send
/// the one-line prompt itself, such as `執行 aw-worker skill。`.
pub fn is_worker_prompt(prompt: &str) -> bool {
    let prompt = prompt.trim_start();
    prompt.starts_with("<scheduled-task name=\"aw-worker")
        || (prompt.chars().count() <= 80 && prompt.contains("aw-worker skill"))
}
