use crate::domain::work::ports::{Clock, Tracker, Workspace};

/// The ports one run of `aw` uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct Services<'a> {
    pub tracker: &'a dyn Tracker,
    /// Only tdd uses the working tree; spec and review work from GitHub.
    pub workspace: &'a dyn Workspace,
    pub clock: &'a dyn Clock,
}
