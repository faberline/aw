//! What the core-reuse check reads from the outside world.

use super::core_reuse::CoreRelease;
use super::repo::{BuildScript, Exception, Manifest};
use crate::domain::work::ports::PortResult;

/// The tracked files of one repository checkout that the check reads.
pub trait RepoFiles {
    /// Every tracked `Cargo.toml`, parsed.
    fn manifests(&self) -> PortResult<Vec<Manifest>>;
    /// Every tracked `build.rs`.
    fn build_scripts(&self) -> PortResult<Vec<BuildScript>>;
    /// `[[standards.core_reuse.exception]]` of `aw.toml`; empty when the
    /// file or the table does not exist.
    fn exceptions(&self) -> PortResult<Vec<Exception>>;
}

/// The `faberline/core` checkout.
pub trait CoreSource {
    /// The current release tag and the crate names under `crates/`.
    fn release(&self) -> PortResult<CoreRelease>;
}
