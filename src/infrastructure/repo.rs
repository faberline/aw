//! Adapter for the hygiene check: the tracked files of a git checkout and
//! the `[hygiene]` table of its `aw.toml`.

use std::path::{Path, PathBuf};

use toml::Table;

use crate::domain::repo::ports::{Allow, Config, Entry, RepoFiles};
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// A git checkout; `root` is its top folder.
pub struct TrackedFiles {
    root: PathBuf,
}

impl TrackedFiles {
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
        Ok(TrackedFiles { root })
    }
}

fn allows(table: &Table, key: &str) -> PortResult<Vec<Allow>> {
    let Some(value) = table.get(key) else {
        return Ok(Vec::new());
    };
    let items = value
        .as_array()
        .ok_or_else(|| PortError(format!("aw.toml: hygiene.{key} must be an array of tables")))?;
    items
        .iter()
        .map(|item| {
            let field = |name: &str| {
                item.get(name)
                    .and_then(toml::Value::as_str)
                    .filter(|s| !s.trim().is_empty())
                    .map(str::to_string)
            };
            match (field("path"), field("reason")) {
                (Some(path), Some(reason)) => Ok(Allow { path, reason }),
                _ => Err(PortError(format!(
                    "aw.toml: each hygiene.{key} entry needs a `path` and a `reason`"
                ))),
            }
        })
        .collect()
}

/// The `[hygiene]` table of an `aw.toml` text.
pub fn parse_config(text: &str) -> PortResult<Config> {
    let table = text
        .parse::<Table>()
        .map_err(|e| PortError(format!("could not parse aw.toml: {e}")))?;
    let mut config = Config::default();
    let Some(hygiene) = table.get("hygiene").and_then(toml::Value::as_table) else {
        return Ok(config);
    };
    config.root = allows(hygiene, "root")?;
    config.evidence = allows(hygiene, "evidence")?;
    config.home_path = allows(hygiene, "home_path")?;
    config.size = allows(hygiene, "size")?;
    if let Some(value) = hygiene.get("max_file_bytes") {
        config.max_file_bytes = value
            .as_integer()
            .and_then(|n| u64::try_from(n).ok())
            .ok_or_else(|| PortError("aw.toml: hygiene.max_file_bytes must be a number".into()))?;
    }
    if let Some(value) = hygiene.get("baseline") {
        config.baseline = value
            .as_array()
            .and_then(|a| {
                a.iter()
                    .map(|v| v.as_str().map(str::to_string))
                    .collect::<Option<Vec<_>>>()
            })
            .ok_or_else(|| {
                PortError("aw.toml: hygiene.baseline must be an array of strings".into())
            })?;
    }
    Ok(config)
}

impl RepoFiles for TrackedFiles {
    fn config(&self) -> PortResult<Config> {
        match std::fs::read_to_string(self.root.join("aw.toml")) {
            Ok(text) => parse_config(&text),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
            Err(e) => Err(PortError(format!("could not read aw.toml: {e}"))),
        }
    }

    fn entries(&self) -> PortResult<Vec<Entry>> {
        let out = run(
            "git",
            &[
                "-C",
                &self.root.to_string_lossy(),
                "ls-files",
                "-z",
                "--full-name",
            ],
            None,
        )?;
        Ok(out
            .split('\0')
            .filter(|p| !p.is_empty())
            .map(|path| Entry {
                path: path.to_string(),
                size: std::fs::symlink_metadata(self.root.join(path))
                    .ok()
                    .filter(std::fs::Metadata::is_file)
                    .map_or(0, |m| m.len()),
            })
            .collect())
    }

    fn text(&self, path: &str) -> PortResult<Option<String>> {
        let full = self.root.join(path);
        if !std::fs::symlink_metadata(&full).is_ok_and(|m| m.is_file()) {
            return Ok(None);
        }
        let bytes = std::fs::read(&full)
            .map_err(|e| PortError(format!("could not read {}: {e}", full.display())))?;
        Ok(String::from_utf8(bytes).ok())
    }
}
