use std::io::Read;

use anyhow::{Context, Result};

use crate::application::run::{self, Client, Event, RunServices, RunSummary};

/// Read one hook payload from stdin and record it. A hook must never block
/// or break the agent, so every error only goes to stderr.
///
/// The hook prints nothing on stdout: on a Claude Code prompt hook, stdout
/// becomes model context.
pub fn hook(s: RunServices<'_>, client: Client, event: Event) {
    if let Err(err) = read_and_record(s, client, event) {
        eprintln!("aw: hook: {err:#}");
    }
}

fn read_and_record(s: RunServices<'_>, client: Client, event: Event) -> Result<()> {
    let mut payload = String::new();
    std::io::stdin()
        .read_to_string(&mut payload)
        .context("could not read stdin")?;
    run::record(s, client, event, &payload)?;
    Ok(())
}

pub fn runs(s: RunServices<'_>, limit: usize) -> Result<Vec<RunSummary>> {
    Ok(run::runs(s, limit)?)
}
