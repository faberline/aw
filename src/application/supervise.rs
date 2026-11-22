//! `aw supervise --check`: one line that says whether the supervisor has
//! anything new to decide. It reads and writes nothing else.

use std::fmt;

use crate::domain::supervise::{Reason, SuperviseSource, decide, snapshot};

/// The answer of the check.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Check {
    pub quick: bool,
    pub reason: Reason,
    pub fp: String,
    pub last_fp: Option<String>,
    pub full_age_min: Option<i64>,
    pub summary: String,
}

impl fmt::Display for Check {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let age = self
            .full_age_min
            .map_or("none".to_string(), |m| m.to_string());
        write!(
            f,
            "{} reason={} fp={} last_fp={} full_age_min={age} {}",
            if self.quick { "quick" } else { "full" },
            self.reason.word(),
            self.fp,
            self.last_fp.as_deref().unwrap_or("none"),
            self.summary
        )
    }
}

/// The repository name for the board title: `status(<name>)`.
pub fn board_title(repo: &str) -> String {
    format!("status({})", repo.rsplit('/').next().unwrap_or(repo))
}

/// Read `repo` and its board in `board_repo` at the Unix time `now`.
pub fn check(
    source: &dyn SuperviseSource,
    repo: &str,
    board_repo: &str,
    now: i64,
) -> Result<Check, String> {
    let facts = source.facts(repo)?;
    let running = source.running(repo)?;
    let board = source.board(board_repo, &board_title(repo))?;
    let snap = snapshot(&facts, &running, now);
    let (quick, reason, full_age_min) = decide(&snap.fp, board.as_deref(), now);
    let last_fp = board
        .as_deref()
        .and_then(crate::domain::supervise::Marker::parse)
        .map(|m| m.fp);
    Ok(Check {
        quick,
        reason,
        fp: snap.fp,
        last_fp,
        full_age_min,
        summary: snap.summary,
    })
}
