//! `aw claim --check`: is there work for this agent and stage now? It runs
//! the selection code of `aw claim` (`claim::select`) in `Mode::Check`, and
//! the backpressure rule of the aw-worker skill, and writes nothing.

use std::fmt;

use anyhow::Result;

use super::active::board;
use super::claim::{Mode, Selected, select};
use super::services::Services;
use crate::domain::work::issue::{
    BACKPRESSURE_REVIEW_LIMIT, BACKPRESSURE_TDD_LIMIT, Issue, plan_attention, valid_agent,
};
use crate::domain::work::stage::Stage;

#[derive(Debug, PartialEq, Eq)]
pub enum Verdict {
    /// A claim would take this issue (`aw claim` exits 0).
    Claim { issue: u64, stage: Stage },
    /// The agent still holds a live claim: continue it.
    Held { issue: u64, stage: Stage },
    /// Spec (or an all-stage run) has plan work: triage, a label move or a
    /// blocked issue that can be answered.
    Plan { issue: u64, why: &'static str },
    /// Nothing to do (`aw claim` exits 2).
    NoWork(NoWork),
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum NoWork {
    /// No issue of the stage is open for the agent.
    EmptyQueue,
    /// Backpressure holds new work, and nothing was sent back.
    Backpressure,
    /// Every issue of the stage has the `blocked` label.
    AllBlocked,
    /// Issues wait, but a claim passes them over (waits for an open issue,
    /// `needs:` label, answered signal, no clone for tdd).
    PassedOver,
}

impl NoWork {
    fn as_str(self) -> &'static str {
        match self {
            NoWork::EmptyQueue => "empty-queue",
            NoWork::Backpressure => "backpressure",
            NoWork::AllBlocked => "all-blocked",
            NoWork::PassedOver => "passed-over",
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Check {
    pub verdict: Verdict,
    pub stage: Option<Stage>,
    pub review_waiting: usize,
    pub tdd_waiting: usize,
}

impl Check {
    pub fn has_work(&self) -> bool {
        !matches!(self.verdict, Verdict::NoWork(_))
    }
}

impl fmt::Display for Check {
    /// One line of `key=value` words; the first word is `work` or `no-work`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let stage = self.stage.map_or("all", Stage::as_str);
        match &self.verdict {
            Verdict::Claim { issue, stage } => {
                write!(f, "work stage={stage} reason=claim issue={issue}")?;
            }
            Verdict::Held { issue, stage } => {
                write!(f, "work stage={stage} reason=held issue={issue}")?;
            }
            Verdict::Plan { issue, why } => {
                write!(f, "work stage={stage} reason=plan issue={issue} why={why}")?;
            }
            Verdict::NoWork(why) => write!(f, "no-work stage={stage} reason={}", why.as_str())?,
        }
        write!(
            f,
            " review_waiting={} tdd_waiting={}",
            self.review_waiting, self.tdd_waiting
        )
    }
}

/// Count the issues in `stage:<label>` that are not blocked, as the
/// skill's `gh api` call does (the issues already carry the agent label).
fn waiting(issues: &[Issue], stage: Stage) -> usize {
    issues
        .iter()
        .filter(|i| Stage::from_labels(&i.labels) == Some(stage) && !i.has_label("blocked"))
        .count()
}

/// Does backpressure hold new work of `stage`? Review has no rule.
pub fn backpressure(stage: Option<Stage>, review_waiting: usize, tdd_waiting: usize) -> bool {
    match stage {
        Some(Stage::Tdd) => review_waiting >= BACKPRESSURE_REVIEW_LIMIT,
        Some(Stage::Spec) => {
            review_waiting >= BACKPRESSURE_REVIEW_LIMIT || tdd_waiting >= BACKPRESSURE_TDD_LIMIT
        }
        Some(Stage::Review) | None => false,
    }
}

pub fn check(
    s: Services<'_>,
    agent: &str,
    only: Option<Stage>,
    skip: &[u64],
    has: &[String],
) -> Result<Check> {
    if !valid_agent(agent) {
        anyhow::bail!("agent `{agent}` must use only a-z, 0-9 and -");
    }
    let issues = board(s, agent)?.issues;
    let review_waiting = waiting(&issues, Stage::Review);
    let tdd_waiting = waiting(&issues, Stage::Tdd);
    let held_back = backpressure(only, review_waiting, tdd_waiting);
    let done = |verdict| {
        Ok(Check {
            verdict,
            stage: only,
            review_waiting,
            tdd_waiting,
        })
    };

    match select(s, agent, only, skip, has, held_back, Mode::Check)? {
        Selected::Held { issue, stage, .. } => return done(Verdict::Held { issue, stage }),
        Selected::Available { issue, stage } => return done(Verdict::Claim { issue, stage }),
        Selected::Claimed(_) => unreachable!("a check claims nothing"),
        Selected::Nothing(_) => {}
    }
    if held_back {
        return done(Verdict::NoWork(NoWork::Backpressure));
    }
    // The spec run (and a run of every stage) plans before it claims.
    if matches!(only, None | Some(Stage::Spec)) {
        for issue in s.tracker.all_open_issues()? {
            let comments = if issue.labels.iter().any(|l| l.starts_with("stage:"))
                || issue.has_label("blocked")
            {
                s.tracker.comments(issue.number)?
            } else {
                Vec::new()
            };
            if let Some(why) = plan_attention(&issue.labels, &comments) {
                return done(Verdict::Plan {
                    issue: issue.number,
                    why,
                });
            }
        }
    }
    let mine: Vec<&Issue> = issues
        .iter()
        .filter(|i| only.is_none_or(|o| i.labels.iter().any(|l| l == &format!("stage:{o}"))))
        .filter(|i| i.labels.iter().any(|l| l.starts_with("stage:")))
        .collect();
    let why = if mine.is_empty() {
        NoWork::EmptyQueue
    } else if mine.iter().all(|i| i.has_label("blocked")) {
        NoWork::AllBlocked
    } else {
        NoWork::PassedOver
    };
    done(Verdict::NoWork(why))
}
