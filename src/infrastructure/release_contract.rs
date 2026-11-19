//! Adapter for `aw release-contract`: a git checkout on this machine and the
//! `[release.apps.<app>]` tables of its `aw.toml`.

use std::path::{Path, PathBuf};

use toml::{Table, Value};

use crate::domain::release::app_check::ContractRepo;
use crate::domain::release::facts::AppFacts;
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// The keys of an app table. Any other key is an error, so a typo is caught.
const KEYS: [&str; 11] = [
    "onboarded",
    "scripts_dir",
    "shared_scripts",
    "targets",
    "candidate_jobs",
    "promotion_prefix",
    "takes_attempt",
    "manifest_schema",
    "receipt_name",
    "image_owner",
    "compatibility_line",
];

/// A git checkout; `root` is its top folder.
pub struct LocalContract {
    root: PathBuf,
}

impl LocalContract {
    /// The checkout that holds `dir`.
    pub fn open(dir: &Path) -> PortResult<Self> {
        let out = run(
            "git",
            &["-C", &dir.to_string_lossy(), "rev-parse", "--show-toplevel"],
            None,
        )
        .map_err(|e| {
            PortError(format!(
                "{} is not inside a git checkout: {e}",
                dir.display()
            ))
        })?;
        let root = std::fs::canonicalize(out.trim())
            .map_err(|e| PortError(format!("could not read {}: {e}", out.trim())))?;
        Ok(LocalContract { root })
    }
}

fn text(table: &Table, app: &str, key: &str) -> PortResult<Option<String>> {
    match table.get(key) {
        None => Ok(None),
        Some(Value::String(s)) if !s.is_empty() => Ok(Some(s.clone())),
        Some(_) => Err(PortError(format!(
            "release.apps.{app}.{key} must be a non-empty string"
        ))),
    }
}

fn flag(table: &Table, app: &str, key: &str) -> PortResult<Option<bool>> {
    match table.get(key) {
        None => Ok(None),
        Some(Value::Boolean(b)) => Ok(Some(*b)),
        Some(_) => Err(PortError(format!(
            "release.apps.{app}.{key} must be true or false"
        ))),
    }
}

fn list(table: &Table, app: &str, key: &str) -> PortResult<Option<Vec<String>>> {
    let Some(value) = table.get(key) else {
        return Ok(None);
    };
    let items: Option<Vec<String>> = value.as_array().and_then(|a| {
        a.iter()
            .map(|v| v.as_str().filter(|s| !s.is_empty()).map(str::to_string))
            .collect()
    });
    match items {
        Some(items) if !items.is_empty() => Ok(Some(items)),
        _ => Err(PortError(format!(
            "release.apps.{app}.{key} must be a non-empty list of strings"
        ))),
    }
}

/// One `[release.apps.<name>]` table.
fn app_facts(name: &str, table: &Table) -> PortResult<AppFacts> {
    if let Some(key) = table.keys().find(|k| !KEYS.contains(&k.as_str())) {
        return Err(PortError(format!(
            "release.apps.{name}.{key} is not a known key"
        )));
    }
    let mut app = AppFacts::shared(name);
    if let Some(v) = flag(table, name, "onboarded")? {
        app.onboarded = v;
    }
    if let Some(v) = text(table, name, "scripts_dir")? {
        app.scripts_dir = v;
    }
    if let Some(v) = flag(table, name, "shared_scripts")? {
        app.shared_scripts = v;
    }
    if let Some(v) = list(table, name, "targets")? {
        app.targets = v;
    }
    if let Some(v) = list(table, name, "candidate_jobs")? {
        app.candidate_jobs = v;
    }
    if let Some(v) = text(table, name, "promotion_prefix")? {
        app.promotion_prefix = v;
    }
    if let Some(v) = flag(table, name, "takes_attempt")? {
        app.takes_attempt = v;
    }
    if let Some(v) = text(table, name, "manifest_schema")? {
        app.manifest_schema = v;
    }
    if let Some(v) = text(table, name, "receipt_name")? {
        app.receipt_name = v;
    }
    if let Some(v) = text(table, name, "image_owner")? {
        app.image_owner = v;
    }
    if let Some(v) = text(table, name, "compatibility_line")? {
        app.compatibility_line = v;
    }
    if app.onboarded && app.targets.is_empty() {
        return Err(PortError(format!(
            "release.apps.{name}.targets is required for an onboarded app"
        )));
    }
    if app.onboarded && app.shared_scripts && app.compatibility_line.is_empty() {
        return Err(PortError(format!(
            "release.apps.{name}.compatibility_line is required when shared_scripts is true"
        )));
    }
    Ok(app)
}

impl ContractRepo for LocalContract {
    fn read(&self, path: &str) -> Option<String> {
        let full = self.root.join(path);
        if !full.is_file() {
            return None;
        }
        std::fs::read_to_string(full).ok()
    }

    fn exists(&self, path: &str) -> bool {
        self.root.join(path).exists()
    }

    fn apps(&self) -> PortResult<Vec<AppFacts>> {
        let path = self.root.join("aw.toml");
        let source = std::fs::read_to_string(&path)
            .map_err(|e| PortError(format!("could not read {}: {e}", path.display())))?;
        let table = source
            .parse::<Table>()
            .map_err(|e| PortError(format!("could not parse {}: {e}", path.display())))?;
        let Some(apps) = table
            .get("release")
            .and_then(|r| r.get("apps"))
            .and_then(Value::as_table)
        else {
            return Ok(Vec::new());
        };
        apps.iter()
            .map(|(name, value)| match value.as_table() {
                Some(t) => app_facts(name, t),
                None => Err(PortError(format!("release.apps.{name} must be a table"))),
            })
            .collect()
    }
}
