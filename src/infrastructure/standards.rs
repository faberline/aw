//! Adapters for the core-reuse check: tracked files and tags of local
//! checkouts.

use std::collections::BTreeSet;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

use toml::{Table, Value};

use crate::domain::standards::core_reuse::CoreRelease;
use crate::domain::standards::ports::{CoreSource, RepoFiles};
use crate::domain::standards::repo::{BuildScript, Dep, Exception, Manifest, Workspace};
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// Folders whose files the check never reads.
const SKIP_PARTS: [&str; 4] = ["vendor", "fixtures", "testdata", "target"];
const DEP_TABLES: [&str; 3] = ["dependencies", "dev-dependencies", "build-dependencies"];

/// A git checkout on this machine.
pub struct LocalRepo {
    dir: PathBuf,
}

impl LocalRepo {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        LocalRepo { dir: dir.into() }
    }

    /// Tracked files named exactly `name`, outside the skipped folders.
    fn tracked(&self, name: &str) -> PortResult<Vec<String>> {
        let dir = self.dir.to_string_lossy();
        let out = run("git", &["-C", &dir, "ls-files", "-z"], None)?;
        let mut paths: Vec<String> = out
            .split('\0')
            .filter(|p| !p.is_empty())
            .filter(|p| p.rsplit('/').next() == Some(name))
            .filter(|p| !p.split('/').any(|part| SKIP_PARTS.contains(&part)))
            .filter(|p| self.dir.join(p).is_file())
            .map(str::to_string)
            .collect();
        paths.sort();
        Ok(paths)
    }

    fn read(&self, rel: &str) -> PortResult<String> {
        let full = self.dir.join(rel);
        std::fs::read_to_string(&full)
            .map_err(|e| PortError(format!("could not read {}: {e}", full.display())))
    }
}

fn parse(rel: &str, text: &str) -> PortResult<Table> {
    text.parse::<Table>()
        .map_err(|e| PortError(format!("could not parse {rel}: {e}")))
}

fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str().map(str::to_string))
        .collect()
}

fn deps_of(manifest: &str, table_name: &str, entries: Option<&Value>, out: &mut Vec<Dep>) {
    for (name, spec) in entries.and_then(Value::as_table).into_iter().flatten() {
        let field = |key: &str| spec.get(key).and_then(Value::as_str).map(str::to_string);
        out.push(Dep {
            manifest: manifest.into(),
            table: table_name.into(),
            name: name.clone(),
            package: field("package").unwrap_or_else(|| name.clone()),
            git: field("git"),
            tag: field("tag"),
            branch: spec.get("branch").is_some(),
            rev: spec.get("rev").is_some(),
            path: field("path"),
            workspace: spec.get("workspace").and_then(Value::as_bool) == Some(true),
        });
    }
}

/// Every dependency table of a manifest, including `target.*` ones and
/// `[workspace.dependencies]`.
fn deps(rel: &str, doc: &Table) -> Vec<Dep> {
    let mut out = Vec::new();
    for name in DEP_TABLES {
        deps_of(rel, name, doc.get(name), &mut out);
    }
    for (target, body) in doc
        .get("target")
        .and_then(Value::as_table)
        .into_iter()
        .flatten()
    {
        for name in DEP_TABLES {
            deps_of(
                rel,
                &format!("target.{target}.{name}"),
                body.get(name),
                &mut out,
            );
        }
    }
    let workspace = doc.get("workspace");
    deps_of(
        rel,
        "workspace.dependencies",
        workspace.and_then(|w| w.get("dependencies")),
        &mut out,
    );
    out
}

impl RepoFiles for LocalRepo {
    fn manifests(&self) -> PortResult<Vec<Manifest>> {
        self.tracked("Cargo.toml")?
            .into_iter()
            .map(|rel| {
                let doc = parse(&rel, &self.read(&rel)?)?;
                let folder = rel.rsplit_once('/').map_or("", |(dir, _)| dir);
                Ok(Manifest {
                    package: doc.contains_key("package"),
                    bin: doc
                        .get("bin")
                        .is_some_and(|b| b.as_array().is_some_and(|a| !a.is_empty())),
                    main_rs: self.dir.join(folder).join("src/main.rs").is_file(),
                    workspace: doc.get("workspace").map(|w| Workspace {
                        members: strings(w.get("members")),
                        exclude: strings(w.get("exclude")),
                    }),
                    deps: deps(&rel, &doc),
                    path: rel,
                })
            })
            .collect()
    }

    fn build_scripts(&self) -> PortResult<Vec<BuildScript>> {
        Ok(self
            .tracked("build.rs")?
            .into_iter()
            .map(|path| {
                let text = std::fs::read(self.dir.join(&path)).unwrap_or_default();
                let text = String::from_utf8_lossy(&text);
                BuildScript {
                    stamps_git_sha: stamps_git_sha(&text),
                    path,
                }
            })
            .collect())
    }

    fn exceptions(&self) -> PortResult<Vec<Exception>> {
        let text = match std::fs::read_to_string(self.dir.join("aw.toml")) {
            Ok(text) => text,
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
            Err(e) => return Err(PortError(format!("could not read aw.toml: {e}"))),
        };
        let doc = parse("aw.toml", &text)?;
        let entries = doc
            .get("standards")
            .and_then(|s| s.get("core_reuse"))
            .and_then(|c| c.get("exception"))
            .and_then(Value::as_array);
        Ok(entries
            .into_iter()
            .flatten()
            .map(|e| {
                let field = |key: &str| e.get(key).and_then(Value::as_str).map(str::to_string);
                Exception {
                    check: field("check").unwrap_or_default(),
                    reason: field("reason").unwrap_or_default(),
                    path: field("path"),
                }
            })
            .collect())
    }
}

/// True when the script prints `cargo:rustc-env=<NAME>GIT_SHA`, where the
/// name is capital letters, digits and underscores.
fn stamps_git_sha(text: &str) -> bool {
    text.match_indices("cargo:rustc-env=").any(|(at, key)| {
        let rest = &text[at + key.len()..];
        let name: &str = rest
            .split(|c: char| !(c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_'))
            .next()
            .unwrap_or_default();
        name.ends_with("GIT_SHA")
    })
}

/// A local `faberline/core` checkout.
pub struct LocalCore {
    dir: PathBuf,
}

impl LocalCore {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        LocalCore { dir: dir.into() }
    }
}

/// `vX.Y.Z` as numbers; anything else is not a release tag.
fn version(tag: &str) -> Option<(u64, u64, u64)> {
    let mut parts = tag.strip_prefix('v')?.split('.');
    let v = (
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
    );
    parts.next().is_none().then_some(v)
}

impl CoreSource for LocalCore {
    fn release(&self) -> PortResult<CoreRelease> {
        let dir = self.dir.to_string_lossy();
        let tags = run("git", &["-C", &dir, "tag", "--list", "v*"], None)?;
        let tag = tags
            .split_whitespace()
            .filter_map(|t| version(t).map(|v| (v, t)))
            .max()
            .map(|(_, t)| t.to_string())
            .ok_or_else(|| PortError(format!("{}: no vX.Y.Z tag", self.dir.display())))?;
        let mut crates = BTreeSet::new();
        if let Ok(entries) = std::fs::read_dir(self.dir.join("crates")) {
            for entry in entries.flatten() {
                let manifest = entry.path().join("Cargo.toml");
                let Ok(text) = std::fs::read_to_string(&manifest) else {
                    continue;
                };
                let doc = parse(&manifest.display().to_string(), &text)?;
                if let Some(name) = doc
                    .get("package")
                    .and_then(|p| p.get("name"))
                    .and_then(Value::as_str)
                {
                    crates.insert(name.to_string());
                }
            }
        }
        Ok(CoreRelease { tag, crates })
    }
}

/// True when `dir` is a git checkout (has `.git`).
pub fn is_checkout(dir: &Path) -> bool {
    dir.join(".git").exists()
}
