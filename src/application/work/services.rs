use crate::domain::work::ports::{ClaimStore, Clock, Tracker, Workspace};

/// The ports one run of `aw` uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct Services<'a> {
    pub tracker: &'a dyn Tracker,
    pub workspace: &'a dyn Workspace,
    pub store: &'a dyn ClaimStore,
    pub clock: &'a dyn Clock,
}
