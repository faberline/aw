//! The supervisor quick exit: a fingerprint of everything the supervisor acts
//! on, and the rule that says when the last full run is still good enough.
//!
//! The supervisor stores `<!-- aw:supervise fp=<hex> full=<UTC time> -->` in
//! the body of its status board. A run whose fingerprint is the same, within
//! [`BACKSTOP_HOURS`] of the last full run, has nothing new to decide.

use sha2::{Digest, Sha256};

use crate::domain::desk::facts::RepoFacts;
use crate::domain::desk::ports::ReadError;
use crate::domain::desk::time::epoch;

/// A full run is due at least this often, whatever the fingerprint says. It
/// covers what a fingerprint cannot see: time-based items (stuck stages,
/// quiet routines) and a run that is stuck without change.
pub const BACKSTOP_HOURS: i64 = 6;

/// The UTC hour when the day of the fingerprint changes. The first check after
/// it is a full run, so a new RustSec advisory waits at most one day.
pub const DAILY_HOUR: i64 = 4;

/// Issues the supervisor and the desk answer: a change is an event.
pub const CONTROL_LABELS: [&str; 3] = ["decision", "pipeline", "needs-human"];

/// What the quick exit needs from the outside.
pub trait SuperviseSource {
    /// The facts of the patrol for one repository.
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError>;
    /// The ids of the queued and running workflow runs.
    fn running(&self, repo: &str) -> Result<Vec<String>, ReadError>;
    /// The body of the open status board issue named `title` in `board_repo`.
    fn board(&self, board_repo: &str, title: &str) -> Result<Option<String>, ReadError>;
}

/// The marker the supervisor keeps in the board body.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Marker {
    pub fp: String,
    pub full: String,
}

impl Marker {
    pub fn render(&self) -> String {
        format!("<!-- aw:supervise fp={} full={} -->", self.fp, self.full)
    }

    /// The marker in a board body, or `None` when there is none.
    pub fn parse(body: &str) -> Option<Marker> {
        let start = body.find("<!-- aw:supervise")?;
        let rest = &body[start + "<!-- aw:supervise".len()..];
        let inner = &rest[..rest.find("-->")?];
        let value = |key: &str| {
            inner
                .split_whitespace()
                .find_map(|w| w.strip_prefix(key))
                .map(str::to_string)
        };
        Some(Marker {
            fp: value("fp=")?,
            full: value("full=")?,
        })
    }
}

/// The fingerprint of one repository and a short readable summary.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub fp: String,
    pub summary: String,
}

fn is_red(conclusion: &str) -> bool {
    matches!(conclusion, "failure" | "timed_out" | "startup_failure")
}

/// The fingerprint at the Unix time `now`.
pub fn snapshot(facts: &RepoFacts, running: &[String], now: i64) -> Snapshot {
    let issues: Vec<_> = facts.issues.iter().filter(|i| !i.has("status")).collect();
    let stage = |name: &str| issues.iter().filter(|i| i.stage() == Some(name)).count();
    let blocked = issues.iter().filter(|i| i.blocked()).count();

    // Main: the newest run sha and the result of the newest run of each
    // workflow (G28).
    let mut runs: Vec<String> = facts
        .main_runs
        .iter()
        .map(|r| format!("{}={}", r.workflow, r.conclusion))
        .collect();
    runs.sort();
    let main_sha = facts
        .main_runs
        .iter()
        .max_by(|a, b| a.created_at.cmp(&b.created_at))
        .map(|r| r.sha.clone())
        .unwrap_or_default();
    let red = facts.main_runs.iter().any(|r| is_red(&r.conclusion));

    // Issues the desk answers, and blocked issues with the blockers that are
    // closed now (G21, G53, G54).
    let mut control: Vec<String> = issues
        .iter()
        .filter(|i| CONTROL_LABELS.iter().any(|l| i.has(l)))
        .map(|i| format!("{}@{}", i.number, i.updated_at))
        .collect();
    control.sort();
    let mut waits: Vec<String> = issues
        .iter()
        .filter(|i| i.blocked())
        .map(|i| format!("{}@{}", i.number, i.updated_at))
        .collect();
    waits.sort();
    let mut closed: Vec<String> = facts
        .closed_refs
        .iter()
        .chain(&facts.split_refs)
        .cloned()
        .collect();
    closed.sort();

    let mut prs: Vec<String> = facts
        .prs
        .iter()
        .map(|p| {
            format!(
                "{}@{}:{}:{}",
                p.number, p.updated_at, p.merge_state, p.failed_checks
            )
        })
        .collect();
    prs.sort();
    let mut run_ids = running.to_vec();
    run_ids.sort();

    // The day changes at DAILY_HOUR UTC (G47).
    let day = (now - DAILY_HOUR * 3600).div_euclid(86_400);

    let canonical = [
        format!("main={main_sha};{}", runs.join(",")),
        format!(
            "stages={},{},{};blocked={blocked}",
            stage("spec"),
            stage("tdd"),
            stage("review")
        ),
        format!("control={}", control.join(",")),
        format!("blocked={};closed={}", waits.join(","), closed.join(",")),
        format!("running={}", run_ids.join(",")),
        format!("prs={}", prs.join(",")),
        format!("day={day}"),
    ]
    .join("\n");
    let digest = Sha256::digest(canonical.as_bytes());
    let fp: String = digest.iter().take(8).map(|b| format!("{b:02x}")).collect();

    let newest_control = issues
        .iter()
        .filter(|i| CONTROL_LABELS.iter().any(|l| i.has(l)))
        .map(|i| i.updated_at.as_str())
        .max()
        .unwrap_or("none");
    let short: String = main_sha.chars().take(7).collect();
    let summary = format!(
        "main={}:{} spec={} tdd={} review={} blocked={blocked} control_newest={newest_control} running={} prs={}",
        if short.is_empty() { "none" } else { &short },
        if red { "red" } else { "ok" },
        stage("spec"),
        stage("tdd"),
        stage("review"),
        run_ids.len(),
        prs.len(),
    );
    Snapshot { fp, summary }
}

/// Why a run is quick or full.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    Same,
    NoBoard,
    NoMarker,
    Changed,
    Stale,
}

impl Reason {
    pub fn word(self) -> &'static str {
        match self {
            Reason::Same => "same",
            Reason::NoBoard => "no-board",
            Reason::NoMarker => "no-marker",
            Reason::Changed => "changed",
            Reason::Stale => "stale",
        }
    }
}

/// Quick exit (`true`) or a full run, with the age of the last full run in
/// minutes. A marker with a time that cannot be read is stale.
pub fn decide(fp: &str, board: Option<&str>, now: i64) -> (bool, Reason, Option<i64>) {
    let Some(body) = board else {
        return (false, Reason::NoBoard, None);
    };
    let Some(marker) = Marker::parse(body) else {
        return (false, Reason::NoMarker, None);
    };
    let age = epoch(&marker.full).map(|t| (now - t).div_euclid(60));
    if marker.fp != fp {
        return (false, Reason::Changed, age);
    }
    match age {
        Some(minutes) if (0..BACKSTOP_HOURS * 60).contains(&minutes) => (true, Reason::Same, age),
        _ => (false, Reason::Stale, age),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::desk::facts::{IssueFact, PrFact, RunFact};

    fn issue(n: u64, labels: &[&str], at: &str) -> IssueFact {
        IssueFact {
            number: n,
            labels: labels.iter().map(|l| l.to_string()).collect(),
            updated_at: at.into(),
            ..IssueFact::default()
        }
    }

    fn base() -> RepoFacts {
        RepoFacts {
            repo: "o/r".into(),
            issues: vec![issue(1, &["stage:spec"], "2026-11-22T01:00:00Z")],
            main_runs: vec![RunFact {
                workflow: "ci.yml".into(),
                created_at: "2026-11-22T00:00:00Z".into(),
                sha: "abcdef123456".into(),
                conclusion: "success".into(),
                ..RunFact::default()
            }],
            ..RepoFacts::default()
        }
    }

    const NOON: i64 = 1_795_000_000;

    fn fp(f: &RepoFacts) -> String {
        snapshot(f, &[], NOON).fp
    }

    #[test]
    fn the_same_facts_give_the_same_fingerprint() {
        assert_eq!(fp(&base()), fp(&base()));
        assert_eq!(fp(&base()).len(), 16);
    }

    #[test]
    fn each_thing_the_supervisor_acts_on_changes_it() {
        let start = fp(&base());
        let mut red = base();
        red.main_runs[0].conclusion = "failure".into();
        assert_ne!(fp(&red), start, "red main");
        let mut moved = base();
        moved.main_runs[0].sha = "999".into();
        assert_ne!(fp(&moved), start, "new main commit");
        let mut stage = base();
        stage.issues[0].labels = vec!["stage:tdd".into()];
        assert_ne!(fp(&stage), start, "stage count");
        let mut decision = base();
        decision.issues.push(issue(2, &["decision"], "t1"));
        let with = fp(&decision);
        assert_ne!(with, start, "new decision");
        decision.issues[1].updated_at = "t2".into();
        assert_ne!(fp(&decision), with, "decision answered");
        let mut blocked = base();
        blocked.issues.push(issue(3, &["blocked"], "t"));
        let wait = fp(&blocked);
        blocked.closed_refs.push("#9".into());
        assert_ne!(fp(&blocked), wait, "blocker closed");
        blocked.split_refs.push("#8".into());
        assert_ne!(fp(&blocked), wait, "blocker split");
        let mut pr = base();
        pr.prs.push(PrFact {
            number: 5,
            updated_at: "t".into(),
            merge_state: "clean".into(),
            ..PrFact::default()
        });
        let open = fp(&pr);
        assert_ne!(open, start, "open pr");
        pr.prs[0].failed_checks = true;
        assert_ne!(fp(&pr), open, "pr check red");
        assert_ne!(
            snapshot(&base(), &["7".into()], NOON).fp,
            start,
            "running run"
        );
    }

    #[test]
    fn the_status_board_and_its_order_do_not_matter() {
        let start = fp(&base());
        let mut board = base();
        board.issues.push(issue(50, &["status"], "t"));
        assert_eq!(fp(&board), start);
        let mut a = base();
        a.issues.push(issue(2, &["decision"], "t"));
        a.issues.push(issue(3, &["pipeline"], "t"));
        let mut b = a.clone();
        b.issues.swap(1, 2);
        assert_eq!(fp(&a), fp(&b));
        assert_eq!(
            snapshot(&a, &["1".into(), "2".into()], NOON).fp,
            snapshot(&a, &["2".into(), "1".into()], NOON).fp
        );
    }

    #[test]
    fn the_day_changes_at_the_daily_hour() {
        let day = 1_794_960_000; // a UTC midnight
        let before = snapshot(&base(), &[], day + 4 * 3600 - 1).fp;
        let after = snapshot(&base(), &[], day + 4 * 3600).fp;
        assert_ne!(before, after);
        assert_eq!(after, snapshot(&base(), &[], day + 23 * 3600).fp);
    }

    #[test]
    fn the_summary_says_main_and_counts() {
        let mut f = base();
        f.main_runs[0].conclusion = "timed_out".into();
        f.issues
            .push(issue(2, &["decision"], "2026-11-22T02:00:00Z"));
        let s = snapshot(&f, &["1".into()], NOON).summary;
        assert_eq!(
            s,
            "main=abcdef1:red spec=1 tdd=0 review=0 blocked=0 control_newest=2026-11-22T02:00:00Z running=1 prs=0"
        );
        let empty = snapshot(&RepoFacts::default(), &[], NOON).summary;
        assert!(empty.starts_with("main=none:ok "), "{empty}");
        assert!(empty.contains("control_newest=none"));
    }

    #[test]
    fn the_marker_round_trips_and_is_found_in_a_body() {
        let m = Marker {
            fp: "0123456789abcdef".into(),
            full: "2026-11-22T01:00:00Z".into(),
        };
        let body = format!("更新：x\n{}\n## CI", m.render());
        assert_eq!(Marker::parse(&body), Some(m));
        assert_eq!(Marker::parse("no marker"), None);
        assert_eq!(Marker::parse("<!-- aw:supervise fp=a -->"), None);
        assert_eq!(Marker::parse("<!-- aw:supervise fp=a full=b"), None);
    }

    #[test]
    fn a_run_is_quick_only_for_the_same_fingerprint_and_a_fresh_full_run() {
        let full = "2026-11-22T00:00:00Z";
        let t0 = epoch(full).unwrap();
        let board = |fp: &str, at: &str| {
            Marker {
                fp: fp.into(),
                full: at.into(),
            }
            .render()
        };
        let b = board("aa", full);
        assert_eq!(
            decide("aa", Some(&b), t0 + 3600),
            (true, Reason::Same, Some(60))
        );
        assert_eq!(
            decide("bb", Some(&b), t0 + 3600),
            (false, Reason::Changed, Some(60))
        );
        assert_eq!(
            decide("aa", Some(&b), t0 + 6 * 3600),
            (false, Reason::Stale, Some(360))
        );
        assert_eq!(
            decide("aa", Some(&board("aa", "junk")), t0),
            (false, Reason::Stale, None)
        );
        assert_eq!(
            decide("aa", Some(&b), t0 - 600),
            (false, Reason::Stale, Some(-10))
        );
        assert_eq!(decide("aa", None, t0), (false, Reason::NoBoard, None));
        assert_eq!(decide("aa", Some("x"), t0), (false, Reason::NoMarker, None));
        for r in [
            Reason::Same,
            Reason::NoBoard,
            Reason::NoMarker,
            Reason::Changed,
            Reason::Stale,
        ] {
            assert!(!r.word().is_empty());
        }
    }
}
