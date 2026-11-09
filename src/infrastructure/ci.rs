//! Adapters for the CI check ports: workflow and `aw.toml` files from a
//! checkout or from GitHub, and the default-branch ruleset from GitHub.

pub mod files;
pub mod parse;
pub mod rules;

pub use files::{GitHubFiles, LocalFiles};
pub use rules::GhBranchRules;
