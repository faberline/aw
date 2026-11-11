//! Milestone release gate: the contract, the gates policy and the decision
//! for the one next action. `docs/standards/release-tooling.md` is the
//! standard. Nothing here reads or writes the outside world.

pub mod contract;
pub mod digest;
pub mod policy;
pub mod ports;
pub mod state;
pub mod tag;

use crate::domain::work::ports::{PortError, PortResult};

/// The error `release: <reason>`.
pub fn fail(reason: &str) -> PortError {
    PortError(format!("release: {reason}"))
}

/// Stop with `release: <reason>` when `ok` is false. A stop is never a pass.
pub fn require(ok: bool, reason: &str) -> PortResult<()> {
    if ok { Ok(()) } else { Err(fail(reason)) }
}
