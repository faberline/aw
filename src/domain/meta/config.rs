//! The repository-specific values of the meta check. `aw.toml` sets them
//! under `[meta]`; the defaults are the faberline conventions.

/// Where a capability source may point.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetaConfig {
    /// Source folders: a source token is `<prefix>/<name>`. `core` is a
    /// faberline/core crate; every other prefix is a path in the checkout.
    pub source_prefixes: Vec<String>,
    /// The token that names the repository root as the product, if any.
    pub root_source: Option<String>,
    /// The `Cargo.lock` source prefix of the `core` crates.
    pub core_source: String,
}

/// The prefix whose sources are `Cargo.lock` packages, not paths.
pub const CORE_PREFIX: &str = "core";

impl Default for MetaConfig {
    fn default() -> Self {
        MetaConfig {
            source_prefixes: ["apps", "libs", "crates", "core", "models"]
                .map(String::from)
                .to_vec(),
            root_source: None,
            core_source: "git+https://github.com/faberline/core?".into(),
        }
    }
}
