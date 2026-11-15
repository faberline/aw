//! Adapter for the meta check: a git checkout on this machine and the
//! `[meta]` table of its `aw.toml`.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use toml::Table;

use crate::domain::meta::config::MetaConfig;
use crate::domain::meta::ports::{CargoIndex, MetaRepo, ReadmeInput};
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// A git checkout; `root` is its top folder.
pub struct LocalMeta {
    root: PathBuf,
}

impl LocalMeta {
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
        Ok(LocalMeta { root })
    }

    fn table(&self, rel: &Path) -> Option<Table> {
        std::fs::read_to_string(self.root.join(rel))
            .ok()?
            .parse::<Table>()
            .ok()
    }
}

fn strings(value: Option<&toml::Value>) -> Option<Vec<String>> {
    Some(
        value?
            .as_array()?
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
    )
}

impl MetaRepo for LocalMeta {
    fn config(&self) -> PortResult<MetaConfig> {
        let mut config = MetaConfig::default();
        let path = self.root.join("aw.toml");
        let Ok(text) = std::fs::read_to_string(&path) else {
            return Ok(config);
        };
        let table = text
            .parse::<Table>()
            .map_err(|e| PortError(format!("could not parse {}: {e}", path.display())))?;
        let name = |t: Option<&toml::Value>, key: &str| {
            t.and_then(|v| v.get(key))
                .and_then(toml::Value::as_str)
                .map(str::to_string)
        };
        let meta = table.get("meta");
        config.root_source =
            name(meta, "root_source").or_else(|| name(table.get("project"), "name"));
        if let Some(prefixes) = strings(meta.and_then(|m| m.get("source_prefixes"))) {
            config.source_prefixes = prefixes;
        }
        if let Some(source) = name(meta, "core_source") {
            config.core_source = source;
        }
        Ok(config)
    }

    fn readme(&self, target: &str) -> PortResult<ReadmeInput> {
        let mut path = PathBuf::from(target);
        if !path.is_absolute() {
            path = self.root.join(path);
        }
        path = std::fs::canonicalize(&path).unwrap_or(path);
        if path.is_dir() {
            path = path.join("README.md");
        }
        let rel = path
            .strip_prefix(&self.root)
            .map_err(|_| PortError(format!("target is outside the checkout: {target}")))?
            .to_path_buf();
        if !path.is_file() {
            return Err(PortError(format!(
                "README does not exist: {}",
                path.display()
            )));
        }
        if path.file_name().and_then(|n| n.to_str()) != Some("README.md") {
            return Err(PortError(format!(
                "target must be a README.md: {}",
                path.display()
            )));
        }
        let raw = std::fs::read(&path)
            .map_err(|e| PortError(format!("could not read {}: {e}", path.display())))?;
        let text = String::from_utf8(raw.clone())
            .map_err(|_| PortError(format!("{} is not UTF-8", path.display())))?;
        let slashed = |p: &Path| {
            p.components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/")
        };
        Ok(ReadmeInput {
            path: slashed(&rel),
            dir: rel.parent().map(slashed).unwrap_or_default(),
            text,
            sha256: format!("{:x}", Sha256::digest(&raw)),
        })
    }

    fn exists_from(&self, base: &str, target: &str) -> bool {
        self.root.join(base).join(target).exists()
    }

    fn cargo_packages(&self) -> PortResult<CargoIndex> {
        let out = run(
            "git",
            &[
                "-C",
                &self.root.to_string_lossy(),
                "-c",
                "core.fsmonitor=false",
                "ls-files",
                "-z",
                "--",
                "*Cargo.toml",
            ],
            None,
        )
        .map_err(|e| PortError(format!("git ls-files failed: {e}")))?;
        let mut index = CargoIndex::new();
        for rel in out.split('\0').filter(|p| !p.is_empty()) {
            let Some(manifest) = self.table(Path::new(rel)) else {
                continue;
            };
            let Some(package) = manifest.get("package").and_then(toml::Value::as_table) else {
                continue;
            };
            let Some(name) = package.get("name").and_then(toml::Value::as_str) else {
                continue;
            };
            let mut targets: BTreeSet<String> = manifest
                .get("test")
                .and_then(toml::Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|t| t.get("name").and_then(toml::Value::as_str))
                .map(str::to_string)
                .collect();
            if package.get("autotests").and_then(toml::Value::as_bool) != Some(false) {
                let tests = self
                    .root
                    .join(Path::new(rel).parent().unwrap_or(Path::new("")))
                    .join("tests");
                targets.extend(auto_tests(&tests));
            }
            index.insert(name.to_string(), targets);
        }
        Ok(index)
    }

    fn local_package(&self, dir: &str) -> Option<String> {
        let manifest = self.table(&Path::new(dir).join("Cargo.toml"))?;
        manifest
            .get("package")?
            .get("name")?
            .as_str()
            .map(str::to_string)
    }

    fn core_crates(&self, core_source: &str) -> BTreeSet<String> {
        let Some(lock) = self.table(Path::new("Cargo.lock")) else {
            return BTreeSet::new();
        };
        lock.get("package")
            .and_then(toml::Value::as_array)
            .into_iter()
            .flatten()
            .filter(|p| {
                p.get("source")
                    .and_then(toml::Value::as_str)
                    .is_some_and(|s| s.starts_with(core_source))
            })
            .filter_map(|p| p.get("name").and_then(toml::Value::as_str))
            .map(str::to_string)
            .collect()
    }
}

/// The test targets Cargo finds by itself: `tests/*.rs` by file stem and
/// `tests/<dir>/main.rs` by folder name.
fn auto_tests(tests: &Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(tests) else {
        return Vec::new();
    };
    let mut names = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if path.is_file() && name.ends_with(".rs") {
            names.push(name.trim_end_matches(".rs").to_string());
        } else if path.join("main.rs").is_file() {
            names.push(name.to_string());
        }
    }
    names
}
