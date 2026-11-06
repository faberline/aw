//! Adapters for the work ports: `gh`, `git`, a claim file and the clock.

pub mod claim_file;
pub mod clock;
pub mod git;
pub mod github;
pub mod process;

pub use claim_file::ClaimFile;
pub use clock::SystemClock;
pub use git::GitCli;
pub use github::GhCli;
