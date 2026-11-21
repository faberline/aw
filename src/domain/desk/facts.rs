//! The facts one repository gives to the patrol checks. The infrastructure
//! reads them from GitHub; the checks only read these values.

use regex::Regex;
use std::sync::OnceLock;

use crate::domain::work::waits::{self, Prerequisite};

/// One issue comment.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CommentFact {
    pub body: String,
    pub created_at: String,
}

/// One open issue (not a pull request).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct IssueFact {
    pub number: u64,
    pub title: String,
    pub labels: Vec<String>,
    pub updated_at: String,
    pub body: String,
    /// Every comment, oldest first. Read for `blocked` issues only.
    pub comments: Vec<CommentFact>,
}

impl IssueFact {
    pub fn has(&self, label: &str) -> bool {
        self.labels.iter().any(|l| l == label)
    }

    /// `spec`, `tdd` or `review`, from the `stage:` label.
    pub fn stage(&self) -> Option<&str> {
        self.labels
            .iter()
            .find_map(|l| l.strip_prefix("stage:"))
            .filter(|s| matches!(*s, "spec" | "tdd" | "review"))
    }

    pub fn blocked(&self) -> bool {
        self.has("blocked")
    }

    pub fn newest_comment(&self) -> Option<&CommentFact> {
        self.comments.last()
    }

    /// What the issue waits for now. The newest comment that names a wait
    /// (`now waits for #A, #B`, `blocked until #A is merged`) replaces the
    /// `Waits for:` line of the body. Text in parentheses names no blocker
    /// (`Waits for: #7 (child A2 of #4)` waits for #7 only).
    pub fn waits(&self) -> Vec<Prerequisite> {
        for c in self.comments.iter().rev() {
            let found = comment_waits(&c.body);
            if !found.is_empty() {
                return found;
            }
        }
        waits::parse(&strip_parens(&self.body)).prerequisites
    }
}

/// `text` without `( ... )` parts. An open bracket ends at the line end.
fn strip_parens(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut depth = 0usize;
    for c in text.chars() {
        match c {
            '\n' => {
                depth = 0;
                out.push(c);
            }
            '(' => depth += 1,
            ')' if depth > 0 => depth -= 1,
            _ if depth == 0 => out.push(c),
            _ => {}
        }
    }
    out
}

fn comment_waits(text: &str) -> Vec<Prerequisite> {
    static PHRASE: OnceLock<Regex> = OnceLock::new();
    let phrase = PHRASE.get_or_init(|| {
        Regex::new(r"(?i)(?:waits\s+for|blocked\s+until|blocked\s+by)\s*:?(.*)")
            .expect("the wait phrase pattern is valid")
    });
    let mut found: Vec<Prerequisite> = Vec::new();
    for line in text.lines() {
        let lower = line.to_lowercase();
        if lower.contains("no longer") || lower.contains("not waiting") {
            continue;
        }
        let line = strip_parens(line);
        if let Some(cap) = phrase.captures(&line) {
            for p in waits::references(&cap[1]) {
                if !found.contains(&p) {
                    found.push(p);
                }
            }
        }
    }
    found
}

/// One open pull request.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PrFact {
    pub number: u64,
    pub title: String,
    pub updated_at: String,
    /// GitHub `mergeable_state`: `clean`, `behind`, `dirty`, `blocked`, ...
    pub merge_state: String,
    /// A check run of the head commit failed or timed out.
    pub failed_checks: bool,
    /// The issue of a `Closes #N` / `Refs #N` line of the body.
    pub issue: Option<u64>,
}

/// The newest completed run of one workflow file on the default branch.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RunFact {
    /// The workflow file path (the name when the path is unknown).
    pub workflow: String,
    /// `push`, `schedule`, `workflow_dispatch`, ... Empty is `push`.
    pub event: String,
    pub created_at: String,
    pub id: u64,
    pub sha: String,
    /// `success`, `failure`, `cancelled`, ...
    pub conclusion: String,
}

/// Everything the checks read about one repository.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RepoFacts {
    pub repo: String,
    pub issues: Vec<IssueFact>,
    pub prs: Vec<PrFact>,
    pub main_runs: Vec<RunFact>,
    /// The time of the newest merged pull request.
    pub last_merge: Option<String>,
    /// The time of the newest commit on the default branch.
    pub last_commit: Option<String>,
    /// References (`#7`, `owner/name#7`) of waits that closed as completed.
    pub closed_refs: Vec<String>,
    /// References of waits that closed as not planned (split) or duplicate.
    pub split_refs: Vec<String>,
}
