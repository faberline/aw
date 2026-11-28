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

    /// The newest comment that names a wait or a hold gives a hold reason
    /// with no issue number (`stays blocked`, `paid`, `needs the owner`,
    /// `out of <milestone>`). Such an issue waits for a person, not for an issue.
    pub fn on_hold(&self) -> bool {
        for c in self.comments.iter().rev() {
            if !comment_waits(&c.body).is_empty() {
                // A wait with one more human condition (`and a second released
                // version exists`) is a hold: the issue number alone does not end it.
                return says_hold(&c.body) || has_extra_condition(&c.body);
            }
            if says_hold(&c.body) {
                return true;
            }
        }
        false
    }

    /// The newest comment is a `blocked` signal (`<!-- aw:signal ...
    /// outcome=blocked -->`) with text after the marker: the text is the reason.
    pub fn blocked_signal_reason(&self) -> bool {
        let Some(c) = self.newest_comment() else {
            return false;
        };
        let body = c.body.trim_start();
        if !body.starts_with("<!-- aw:signal") {
            return false;
        }
        let Some((marker, rest)) = body.split_once("-->") else {
            return false;
        };
        marker.contains("outcome=blocked") && !rest.trim().is_empty()
    }

    /// References to issues of another repository in the newest comment, when
    /// the issue names no wait. Such an issue may wait for them with no wait phrase.
    pub fn other_repo_mentions(&self) -> Vec<Prerequisite> {
        if !self.waits().is_empty() {
            return Vec::new();
        }
        let Some(c) = self.newest_comment() else {
            return Vec::new();
        };
        comment_references(&c.body)
            .into_iter()
            .filter(|p| p.repo.is_some())
            .collect()
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

fn has_extra_condition(text: &str) -> bool {
    let lower = text.to_lowercase();
    [
        "second released",
        "released version",
        "and a second",
        "owner decides",
        "owner approves",
    ]
    .iter()
    .any(|w| lower.contains(w))
}

fn says_hold(text: &str) -> bool {
    let lower = text.to_lowercase();
    [
        "stays blocked",
        "stay blocked",
        "paid",
        "needs the owner",
        "need the owner",
        "needs the user",
        "no paid run",
        "out of m",
        "out of the milestone",
    ]
    .iter()
    .any(|w| lower.contains(w))
}

/// Every `#N`, `name#N` and `owner/name#N` of a comment line. `name#N`
/// (`aw#195`) is the repository `name` of the same owner.
fn comment_references(text: &str) -> Vec<Prerequisite> {
    static REF: OnceLock<Regex> = OnceLock::new();
    let re = REF.get_or_init(|| {
        Regex::new(r"(?:([A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9._-]+)?))?#(\d+)")
            .expect("the comment reference pattern is valid")
    });
    let mut found: Vec<Prerequisite> = Vec::new();
    // `core#600, #601, #602 and #603`: a bare number in a comma list after a
    // named one is in that repository, not in the repository of the issue.
    // `reach#7 and #9` is two repositories: a bare number after only `and`
    // is the repository of the issue, unless the list already used commas.
    let mut last: Option<String> = None;
    let mut chain = false;
    let mut end = 0;
    for cap in re.captures_iter(text) {
        let whole = cap.get(0).expect("a match has a whole");
        let Ok(number) = cap[2].parse::<u64>() else {
            continue;
        };
        let named = cap.get(1).map(|m| m.as_str().to_string());
        let between = text[end..whole.start()].trim();
        end = whole.end();
        let repo = if named.is_some() {
            last.clone_from(&named);
            chain = false;
            named
        } else {
            let list = matches!(between, "," | ", and" | ", &" | ",and");
            let joined = list || (chain && matches!(between, "and" | "&"));
            if last.is_some() && joined {
                chain = true;
                last.clone()
            } else {
                last = None;
                chain = false;
                None
            }
        };
        let p = Prerequisite { repo, number };
        if !found.contains(&p) {
            found.push(p);
        }
    }
    found
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
            // `Waits for: none. #214 waits for this issue.` names no wait.
            if waits::says_none(&cap[1]) {
                continue;
            }
            for p in comment_references(&cap[1]) {
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
    /// References that could not be read, with the failed call.
    pub unreadable_refs: Vec<(String, String)>,
}
