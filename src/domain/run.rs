//! Worker runs: one ledger line per hook event, so a supervisor can see
//! which scheduled agent run started, ended, or never ended.

pub mod hook;
pub mod ports;
pub mod record;
pub mod summary;
