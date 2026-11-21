//! The named checks of the desk patrol. Each check reads facts only.

use super::facts::{IssueFact, RepoFacts};
use super::report::{Counts, Item, Section};
use super::time::epoch;
use crate::domain::work::waits;

/// The limits of one patrol.
#[derive(Clone, Copy, Debug)]
pub struct Settings {
    /// Unix seconds of "now".
    pub now: i64,
    /// `blocked-new` looks at changes newer than this.
    pub since_secs: i64,
    /// `stuck-stage`, `pr-stuck` and `routine-quiet` look at ages above this.
    pub stale_secs: i64,
}

fn item(check: &str, what: String, why: String) -> Item {
    Item {
        check: check.to_string(),
        what,
        why,
    }
}

fn age(now: i64, at: &str) -> Option<i64> {
    epoch(at).map(|t| now - t)
}

fn hours(secs: i64) -> i64 {
    secs / 3600
}

/// The first line of a comment that is not empty and not an HTML comment.
pub fn first_line(text: &str) -> String {
    let line = text
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !l.starts_with("<!--"))
        .unwrap_or("");
    line.chars().take(200).collect()
}

/// True when the comment says what the issue waits for.
fn names_a_wait(text: &str) -> bool {
    let lower = text.to_lowercase();
    [
        "waits for",
        "decision",
        "needs",
        "waiting",
        "blocked on",
        "blocked by",
    ]
    .iter()
    .any(|w| lower.contains(w))
}

fn is_work(i: &IssueFact) -> bool {
    i.stage().is_some() && !i.blocked()
}

/// The section of one repository.
pub fn repo_section(f: &RepoFacts, s: &Settings) -> Section {
    let count_stage = |stage: &str| f.issues.iter().filter(|i| i.stage() == Some(stage)).count();
    let counts = Counts {
        open: f.issues.len(),
        spec: count_stage("spec"),
        tdd: count_stage("tdd"),
        review: count_stage("review"),
        blocked: f.issues.iter().filter(|i| i.blocked()).count(),
        open_prs: f.prs.len(),
        last_merge: f.last_merge.clone(),
    };
    let mut items = Vec::new();
    main_red(f, &mut items);
    blocked_new(f, s, &mut items);
    blocked_no_reason(f, &mut items);
    stuck_stage(f, s, &mut items);
    pr_stuck(f, s, &mut items);
    queue_empty(f, &mut items);
    waits_for_done(f, &mut items);
    routine_quiet(f, s, &mut items);
    Section {
        name: f.repo.clone(),
        counts: Some(counts),
        unknown: None,
        items,
    }
}

/// A repository that could not be read is `unknown`, never clean.
pub fn unknown_section(repo: &str, call: &str) -> Section {
    Section {
        name: repo.to_string(),
        counts: None,
        unknown: Some(call.to_string()),
        items: Vec::new(),
    }
}

/// 1. The newest completed run of a workflow on main failed or was cancelled.
fn main_red(f: &RepoFacts, out: &mut Vec<Item>) {
    for r in &f.main_runs {
        if r.conclusion == "failure" || r.conclusion == "cancelled" {
            let sha: String = r.sha.chars().take(7).collect();
            out.push(item(
                "main-red",
                format!("run {} of workflow {}", r.id, r.workflow),
                format!("main is {} at {sha}", r.conclusion),
            ));
        }
    }
}

/// 2. A blocked issue changed within the `--since` window.
fn blocked_new(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let recent = age(s.now, &i.updated_at).is_some_and(|a| a <= s.since_secs);
        if recent {
            let line = i
                .newest_comment
                .as_ref()
                .map(|c| first_line(&c.body))
                .unwrap_or_default();
            let why = if line.is_empty() {
                "blocked, changed in the window, no comment".to_string()
            } else {
                format!("blocked, changed in the window: {line}")
            };
            out.push(item(
                "blocked-new",
                format!("#{} {}", i.number, i.title),
                why,
            ));
        }
    }
}

/// 3. A blocked issue whose newest comment does not say what it waits for.
fn blocked_no_reason(f: &RepoFacts, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let has_reason = i
            .newest_comment
            .as_ref()
            .is_some_and(|c| names_a_wait(&c.body))
            || !waits::parse(&i.body).prerequisites.is_empty();
        if !has_reason {
            out.push(item(
                "blocked-no-reason",
                format!("#{} {}", i.number, i.title),
                "blocked, and no comment says what it waits for".to_string(),
            ));
        }
    }
}

/// 4. A staged, unblocked issue that has not changed for a long time.
fn stuck_stage(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| is_work(i)) {
        if let Some(a) = age(s.now, &i.updated_at).filter(|a| *a > s.stale_secs) {
            out.push(item(
                "stuck-stage",
                format!("#{} {}", i.number, i.title),
                format!(
                    "stage {}, not updated for {}h",
                    i.stage().unwrap_or("?"),
                    hours(a)
                ),
            ));
        }
    }
}

/// 5. An open PR that is behind, dirty, failing, or quiet.
fn pr_stuck(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    for p in &f.prs {
        let mut why: Vec<String> = Vec::new();
        match p.merge_state.as_str() {
            "behind" => why.push("BEHIND main".to_string()),
            "dirty" => why.push("DIRTY (conflict)".to_string()),
            _ => {}
        }
        if p.failed_checks {
            why.push("a check failed".to_string());
        }
        if let Some(a) = age(s.now, &p.updated_at).filter(|a| *a > s.stale_secs) {
            why.push(format!("no update for {}h", hours(a)));
        }
        if !why.is_empty() {
            out.push(item(
                "pr-stuck",
                format!("PR #{} {}", p.number, p.title),
                why.join(", "),
            ));
        }
    }
}

/// 6. No unblocked issue in any stage.
fn queue_empty(f: &RepoFacts, out: &mut Vec<Item>) {
    if f.issues.iter().any(is_work) {
        return;
    }
    if f.issues.is_empty() {
        out.push(item(
            "queue-empty",
            f.repo.clone(),
            "no open issue".to_string(),
        ));
        return;
    }
    let waits: Vec<String> = f
        .issues
        .iter()
        .filter(|i| i.blocked())
        .map(|i| {
            let w: Vec<String> = waits::parse(&i.body)
                .prerequisites
                .iter()
                .map(|p| p.show())
                .collect();
            if w.is_empty() {
                format!("#{} waits for nothing named", i.number)
            } else {
                format!("#{} waits for {}", i.number, w.join(" "))
            }
        })
        .collect();
    let why = if waits.is_empty() {
        "no unblocked issue in any stage".to_string()
    } else {
        format!("all open issues are blocked: {}", waits.join("; "))
    };
    out.push(item("queue-empty", f.repo.clone(), why));
}

/// 7. `Waits for: #N` where every #N is closed and the issue is still blocked.
fn waits_for_done(f: &RepoFacts, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let w = waits::parse(&i.body);
        if w.prerequisites.is_empty() {
            continue;
        }
        if w.prerequisites
            .iter()
            .all(|p| f.closed_refs.contains(&p.show()))
        {
            let names: Vec<String> = w.prerequisites.iter().map(|p| p.show()).collect();
            out.push(item(
                "waits-for-done",
                format!("#{} {}", i.number, i.title),
                format!("waits for {}, all closed, still blocked", names.join(" ")),
            ));
        }
    }
}

/// 10. Unblocked work exists but nothing changed for a long time.
fn routine_quiet(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    if !f.issues.iter().any(is_work) {
        return;
    }
    let mut times: Vec<i64> = Vec::new();
    times.extend(f.last_commit.as_deref().and_then(epoch));
    times.extend(f.issues.iter().filter_map(|i| epoch(&i.updated_at)));
    times.extend(f.prs.iter().filter_map(|p| epoch(&p.updated_at)));
    let Some(newest) = times.into_iter().max() else {
        return;
    };
    let quiet = s.now - newest;
    if quiet > s.stale_secs {
        out.push(item(
            "routine-quiet",
            f.repo.clone(),
            format!(
                "no commit, comment or label change for {}h, work waits",
                hours(quiet)
            ),
        ));
    }
}

/// The aw section: open decision and pipeline issues.
pub fn aw_section(f: &RepoFacts) -> Section {
    let mut items = Vec::new();
    for i in &f.issues {
        for (label, check) in [("decision", "decision-open"), ("pipeline", "pipeline-open")] {
            if i.has(label) {
                let line = first_line(&i.body);
                let why = if label == "decision" && !line.is_empty() {
                    line
                } else {
                    format!("open, label {label}")
                };
                items.push(item(check, format!("#{} {}", i.number, i.title), why));
            }
        }
    }
    Section {
        name: f.repo.clone(),
        counts: None,
        unknown: None,
        items,
    }
}
