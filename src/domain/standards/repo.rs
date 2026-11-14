//! The parts of a repository that the core-reuse rules read.

/// One dependency entry of one `Cargo.toml` table.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Dep {
    /// The manifest path, relative to the repository root.
    pub manifest: String,
    /// For example `dependencies` or `target.x.build-dependencies`.
    pub table: String,
    pub name: String,
    /// The `package =` rename, else the name.
    pub package: String,
    pub git: Option<String>,
    pub tag: Option<String>,
    pub branch: bool,
    pub rev: bool,
    pub path: Option<String>,
    /// `workspace = true`.
    pub workspace: bool,
}

impl Dep {
    /// True when the git source is the `faberline/core` repository.
    pub fn from_core(&self) -> bool {
        self.git.as_deref().and_then(faberline_repo) == Some("core")
    }

    /// True when the git source is any faberline repository.
    pub fn from_faberline(&self) -> bool {
        self.git.as_deref().and_then(faberline_repo).is_some()
    }
}

/// The repository name in `github.com[:/]faberline/<name>[.git][/]`.
fn faberline_repo(git: &str) -> Option<&str> {
    let rest = &git[git.find("github.com")? + "github.com".len()..];
    let rest = rest.strip_prefix([':', '/'])?.strip_prefix("faberline/")?;
    let rest = rest.trim_end_matches('/');
    Some(rest.strip_suffix(".git").unwrap_or(rest))
}

/// `[workspace]` of a root manifest.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Workspace {
    pub members: Vec<String>,
    pub exclude: Vec<String>,
}

/// One tracked `Cargo.toml`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Manifest {
    pub path: String,
    /// Has a `[package]` table.
    pub package: bool,
    /// Has a `[[bin]]` target.
    pub bin: bool,
    /// `src/main.rs` exists next to it.
    pub main_rs: bool,
    pub workspace: Option<Workspace>,
    pub deps: Vec<Dep>,
}

/// One tracked `build.rs`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuildScript {
    pub path: String,
    /// It prints `cargo:rustc-env=*GIT_SHA`.
    pub stamps_git_sha: bool,
}

/// One opt-out from `[[standards.core_reuse.exception]]` in `aw.toml`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Exception {
    pub check: String,
    pub reason: String,
    /// Limits the exception to one file; `None` covers every path.
    pub path: Option<String>,
}
