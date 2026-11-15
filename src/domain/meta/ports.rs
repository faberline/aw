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

/// A git checkout.
pub trait MetaRepo {
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
