//! What the meta check needs from the checkout.

use std::collections::{BTreeMap, BTreeSet};

use super::config::MetaConfig;
use crate::domain::work::ports::PortResult;

/// Cargo packages of the checkout and their integration-test targets.
pub type CargoIndex = BTreeMap<String, BTreeSet<String>>;

/// One README to check, already read.
#[derive(Clone, Debug)]
pub struct ReadmeInput {
    /// Path from the repository root, with `/`: `apps/x/README.md`.
    pub path: String,
    /// Folder of the README from the repository root; empty for the root.
    pub dir: String,
    pub text: String,
    pub sha256: String,
}

/// One project document, already read.
#[derive(Clone, Debug)]
pub struct DocInput {
    /// Path from the repository root, with `/`: `apps/x/STATUS.md`.
    pub path: String,
    /// Folder of the document from the repository root; empty for the root.
    pub dir: String,
    pub text: String,
    pub sha256: String,
}

/// A git checkout.
pub trait MetaRepo {
    /// Resolve a user path (a project folder or one of its documents) to the
    /// project folder, from the repository root; empty for the root.
    fn project_dir(&self, target: &str) -> PortResult<String>;
    /// Read the file at `path` (from the repository root). `None` when it is
    /// not a file.
    fn document(&self, path: &str) -> PortResult<Option<DocInput>>;
    /// Whether `path` (from the repository root) is a file.
    fn is_file(&self, path: &str) -> bool;
    /// The text of the file at `path`, if it is a readable text file.
    fn read_text(&self, path: &str) -> Option<String>;
    /// The absolute path of the checkout's top folder.
    fn root(&self) -> String;
    fn config(&self) -> PortResult<MetaConfig>;
    /// Resolve a user path (a README or a folder with one) and read it.
    fn readme(&self, target: &str) -> PortResult<ReadmeInput>;
    /// Whether `target` exists, read from the folder `base` (a path from
    /// the repository root). An absolute `target` stands alone.
    fn exists_from(&self, base: &str, target: &str) -> bool;
    /// The tracked `Cargo.toml` packages.
    fn cargo_packages(&self) -> PortResult<CargoIndex>;
    /// The package name of `<dir>/Cargo.toml`, if it has one.
    fn local_package(&self, dir: &str) -> Option<String>;
    /// The packages of `Cargo.lock` that come from `core_source`.
    fn core_crates(&self, core_source: &str) -> BTreeSet<String>;
}
