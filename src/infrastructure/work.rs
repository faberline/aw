//! Adapters for the work ports: `gh`, `git` and the clock.

pub mod clock;
pub mod git;
pub mod github;
pub mod process;

pub use clock::SystemClock;
pub use git::GitCli;
pub use github::GhCli;
