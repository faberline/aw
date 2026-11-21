//! The named checks of the desk patrol. Each check reads facts only.

use super::facts::{IssueFact, RepoFacts};
use super::report::{BlockedReasons, Counts, Item, Section};
use super::time::epoch;
use crate::domain::work::waits;

/// The names of the checks, for `--only`.
pub const CHECKS: [&str; 13] = [
    "main-red",
    "blocked-new",
    "blocked-no-reason",
    "stuck-stage",
    "pr-stuck",
    "queue-empty",
    "waits-for-done",
    "waits-for-split",
    "waits-unknown",
    "blocker-done-elsewhere",
    "routine-quiet",
    "decision-open",
    "pipeline-open",
];

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
        "blocked until",
        "blocked:",
        "owner",
        "aw#",
        "faberline/aw/issues",
    ]
    .iter()
    .any(|w| lower.contains(w))
}

fn is_work(i: &IssueFact) -> bool {
    i.stage().is_some() && !i.blocked()
}

/// How a blocked issue says what it waits for.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Reason {
    Hold,
    Waits,
    Comment,
    Label,
}

fn reason_of(i: &IssueFact) -> Option<Reason> {
    if i.on_hold() {
        return Some(Reason::Hold);
    }
    if !i.waits().is_empty() || !waits::parse(&i.body).without_number.is_empty() {
        return Some(Reason::Waits);
    }
    if i.blocked_signal_reason() {
        return Some(Reason::Comment);
    }
    if i.comments.iter().any(|c| names_a_wait(&c.body)) {
        return Some(Reason::Comment);
    }
    let held = ["decision", "paid", "owner", "hold"];
    if i.labels
        .iter()
        .any(|l| l != "blocked" && held.iter().any(|h| l.contains(h)))
    {
        return Some(Reason::Label);
    }
    None
}

fn blocked_reasons(f: &RepoFacts) -> BlockedReasons {
    let mut r = BlockedReasons::default();
    for i in f.issues.iter().filter(|i| i.blocked()) {
        match reason_of(i) {
            Some(Reason::Hold) => r.hold += 1,
            Some(Reason::Waits) => r.waits += 1,
            Some(Reason::Comment) => r.comment += 1,
            Some(Reason::Label) => r.label += 1,
            None => r.none += 1,
        }
    }
    r
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
        blocked_reasons: blocked_reasons(f),
        open_prs: f.prs.len(),
        last_merge: f.last_merge.clone(),
    };
    let mut items = Vec::new();
    main_red(f, s, &mut items);
    blocked_new(f, s, &mut items);
    blocked_no_reason(f, &mut items);
    stuck_stage(f, s, &mut items);
    pr_stuck(f, s, &mut items);
    queue_empty(f, &mut items);
    waits_for_done(f, &mut items);
    waits_unknown(f, &mut items);
    blocker_done_elsewhere(f, &mut items);
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

/// Check 1: The newest completed run of a workflow file on main failed or was
/// cancelled. A run started by hand (`workflow_dispatch`) never counts. A
/// newer green run hides an older red one (the reader keeps only the newest
/// push or schedule run of each workflow file).
fn main_red(f: &RepoFacts, _s: &Settings, out: &mut Vec<Item>) {
    for r in &f.main_runs {
        if r.conclusion != "failure" && r.conclusion != "cancelled" {
            continue;
        }
        if r.event == "workflow_dispatch" {
            continue;
        }
        let sha: String = r.sha.chars().take(7).collect();
        out.push(item(
            "main-red",
            format!("run {} of workflow {}", r.id, r.workflow),
            format!("main is {} at {sha}", r.conclusion),
        ));
    }
}

/// Check 2: A blocked issue changed within the `--since` window.
fn blocked_new(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let recent = age(s.now, &i.updated_at).is_some_and(|a| a <= s.since_secs);
        if recent {
            let line = i
                .newest_comment()
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

/// Check 3: A blocked issue with no reason: no `Waits for:` line, no comment that
/// names a wait or a decision, no hold label. The reasons found are counted
/// in the counts line of the repository.
fn blocked_no_reason(f: &RepoFacts, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        if reason_of(i).is_none() {
            out.push(item(
                "blocked-no-reason",
                format!("#{} {}", i.number, i.title),
                "blocked, and no comment says what it waits for".to_string(),
            ));
        }
    }
}

/// Check 4: A staged, unblocked issue that has not changed for a long time. A
/// review issue and an `expedite` issue show one by one. The rest show as one
/// line for each stage: the count and the three oldest.
fn stuck_stage(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    let mut crowd: Vec<(&str, Vec<(i64, u64)>)> = vec![("spec", vec![]), ("tdd", vec![])];
    for i in f.issues.iter().filter(|i| is_work(i)) {
        let Some(a) = age(s.now, &i.updated_at).filter(|a| *a > s.stale_secs) else {
            continue;
        };
        let stage = i.stage().unwrap_or("?");
        if stage == "review" || i.has("expedite") {
            out.push(item(
                "stuck-stage",
                format!("#{} {}", i.number, i.title),
                format!("stage {stage}, not updated for {}h", hours(a)),
            ));
        } else if let Some((_, list)) = crowd.iter_mut().find(|(n, _)| *n == stage) {
            list.push((a, i.number));
        }
    }
    for (stage, mut list) in crowd {
        if list.is_empty() {
            continue;
        }
        list.sort_by(|x, y| y.0.cmp(&x.0).then(x.1.cmp(&y.1)));
        let oldest: Vec<String> = list
            .iter()
            .take(3)
            .map(|(a, n)| format!("#{n} ({}h)", hours(*a)))
            .collect();
        out.push(item(
            "stuck-stage",
            format!("stage {stage}: {} issues", list.len()),
            format!("not updated for a long time; oldest {}", oldest.join(", ")),
        ));
    }
}

/// Check 5: An open PR that is behind, dirty, failing, or quiet. A PR whose issue is
/// blocked or closed is an old PR: one line with the numbers.
fn pr_stuck(f: &RepoFacts, s: &Settings, out: &mut Vec<Item>) {
    let mut old: Vec<u64> = Vec::new();
    for p in &f.prs {
        let issue_gone = p
            .issue
            .is_some_and(|n| match f.issues.iter().find(|i| i.number == n) {
                None => true,
                Some(i) => i.blocked(),
            });
        if issue_gone {
            old.push(p.number);
            continue;
        }
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
    if !old.is_empty() {
        let numbers: Vec<String> = old.iter().map(|n| format!("#{n}")).collect();
        out.push(item(
            "pr-stuck",
            format!("old PRs: {}", numbers.join(", ")),
            "the issue of each is blocked or closed".to_string(),
        ));
    }
}

/// Check 6: No unblocked issue in any stage.
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
            let w: Vec<String> = i.waits().iter().map(|p| p.show()).collect();
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

/// Check 7: A blocked issue whose waits are all closed as completed (`waits-for-done`),
/// or one of whose waits closed as not planned, that is, split
/// (`waits-for-split`: the children are the new blockers).
fn waits_for_done(f: &RepoFacts, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let w = i.waits();
        if w.is_empty() || i.on_hold() {
            continue;
        }
        let names: Vec<String> = w.iter().map(|p| p.show()).collect();
        let split: Vec<&str> = names
            .iter()
            .filter(|n| f.split_refs.contains(n))
            .map(|n| n.as_str())
            .collect();
        if !split.is_empty() {
            out.push(item(
                "waits-for-split",
                format!("#{} {}", i.number, i.title),
                format!(
                    "waits for {}, closed as not planned (split); name the children",
                    split.join(" ")
                ),
            ));
        } else if names.iter().all(|n| f.closed_refs.contains(n)) {
            out.push(item(
                "waits-for-done",
                format!("#{} {}", i.number, i.title),
                format!("waits for {}, all closed, still blocked", names.join(" ")),
            ));
        }
    }
}

/// Check: A blocked issue that waits for a reference that could not be read
/// (a deleted issue, a repository with no access). The issue is `unknown`; the
/// repository is still read.
fn waits_unknown(f: &RepoFacts, out: &mut Vec<Item>) {
    for i in f.issues.iter().filter(|i| i.blocked()) {
        let mut refs = i.waits();
        refs.extend(i.other_repo_mentions());
        let bad: Vec<String> = refs
            .iter()
            .map(|p| p.show())
            .filter(|n| f.unreadable_refs.iter().any(|(r, _)| r == n))
            .collect();
        if bad.is_empty() {
            continue;
        }
        let call = f
            .unreadable_refs
            .iter()
            .find(|(r, _)| *r == bad[0])
            .map(|(_, c)| c.as_str())
            .unwrap_or("");
        out.push(item(
            "waits-unknown",
            format!("#{} {}", i.number, i.title),
            format!("unknown: could not read {} ({call})", bad.join(" ")),
        ));
    }
}

/// Check: A blocked issue that names no wait, but whose newest comment points
/// at issues of other repositories, all closed as completed. A closed one that
/// was split (not planned) is listed too: its open children are the new blockers.
fn blocker_done_elsewhere(f: &RepoFacts, out: &mut Vec<Item>) {
    let unreadable = |n: &String| f.unreadable_refs.iter().any(|(r, _)| r == n);
    for i in f.issues.iter().filter(|i| i.blocked() && !i.on_hold()) {
        let names: Vec<String> = i.other_repo_mentions().iter().map(|p| p.show()).collect();
        if names.is_empty() || names.iter().any(unreadable) {
            continue;
        }
        let split: Vec<&str> = names
            .iter()
            .filter(|n| f.split_refs.contains(n))
            .map(|n| n.as_str())
            .collect();
        if !split.is_empty() {
            out.push(item(
                "blocker-done-elsewhere",
                format!("#{} {}", i.number, i.title),
                format!(
                    "the newest comment names {}, closed as not planned (split); name the children",
                    split.join(" ")
                ),
            ));
        } else if names.iter().all(|n| f.closed_refs.contains(n)) {
            out.push(item(
                "blocker-done-elsewhere",
                format!("#{} {}", i.number, i.title),
                format!(
                    "the newest comment names {}, all closed as completed, still blocked",
                    names.join(" ")
                ),
            ));
        }
    }
}

/// Check 10: Unblocked work exists but nothing changed for a long time.
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
