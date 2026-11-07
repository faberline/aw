use std::path::Path;

use super::services::RunServices;
use crate::domain::run::hook::{HookInput, clone_of, is_worker_prompt};
use crate::domain::run::ports::RunResult;
use crate::domain::run::record::{
    Client, Event, MESSAGE_LIMIT, PROMPT_LIMIT, RunRecord, first_line, rfc3339, shorten,
};
use crate::domain::run::summary::{RunSummary, summarize};

#[derive(Debug, PartialEq, Eq)]
pub enum Recorded {
    Written(Box<RunRecord>),
    /// Not a worker run, so nothing was written.
    Skipped(&'static str),
}

/// Record one hook event, but only for a worker run.
///
/// A start is a worker run when the session works in a clone under the
/// workers directory, or when its prompt is the worker automation prompt.
/// An end is a worker run when it is in a clone, or when the ledger already
/// holds a start for the same session. Claude's `Stop` input has no prompt.
pub fn record(
    s: RunServices<'_>,
    client: Client,
    event: Event,
    payload: &str,
) -> RunResult<Recorded> {
    let input = HookInput::parse(payload)?;
    let Some(session) = input.session.clone() else {
        return Ok(Recorded::Skipped("the hook input has no session id"));
    };
    let clone = input
        .dirs
        .iter()
        .find_map(|dir| clone_of(s.root, Path::new(dir)));
    let worker = clone.is_some()
        || match event {
            Event::Start => input.prompt.as_deref().is_some_and(is_worker_prompt),
            Event::End => s
                .ledger
                .read_all()?
                .iter()
                .any(|r| r.client == client && r.session == session && r.event == Event::Start),
        };
    if !worker {
        return Ok(Recorded::Skipped("not a worker run"));
    }
    let prompt = match event {
        Event::Start => input
            .prompt
            .as_deref()
            .and_then(|p| first_line(p, PROMPT_LIMIT)),
        Event::End => None,
    };
    let record = RunRecord {
        at: rfc3339(s.clock.now()),
        client,
        event,
        session,
        clone,
        cwd: input.cwd,
        prompt,
        transcript: input.transcript,
        reason: input.reason.map(|r| shorten(&r, PROMPT_LIMIT)),
        error: input.error.map(|e| shorten(&e, MESSAGE_LIMIT)),
        last_message: input.last_message.map(|m| shorten(&m, MESSAGE_LIMIT)),
    };
    s.ledger.append(&record)?;
    Ok(Recorded::Written(Box::new(record)))
}

/// The last `limit` runs, oldest first.
pub fn runs(s: RunServices<'_>, limit: usize) -> RunResult<Vec<RunSummary>> {
    Ok(summarize(&s.ledger.read_all()?, limit))
}
