//! `aw desk patrol`: each check on fixture facts, and the `gh` reader on
//! fixture JSON. No network.

use std::collections::HashMap;

use aw::application::desk::{patrol, repo_names};
use aw::domain::desk::checks::{Settings, repo_section};
use aw::domain::desk::facts::{CommentFact, IssueFact, PrFact, RepoFacts, RunFact};
use aw::domain::desk::ports::{DeskSource, ReadError};
use aw::domain::desk::report::Report;
use aw::infrastructure::desk::{Api, GhDesk, parse_config};

const NOW: &str = "2026-11-21T12:00:00Z";

fn settings() -> Settings {
    Settings {
        now: aw::domain::desk::time::epoch(NOW).unwrap(),
        since_secs: 70 * 60,
        stale_secs: 6 * 3600,
    }
}

fn issue(number: u64, labels: &[&str], updated: &str) -> IssueFact {
    IssueFact {
        number,
        title: format!("issue {number}"),
        labels: labels.iter().map(|l| l.to_string()).collect(),
        updated_at: updated.to_string(),
        ..IssueFact::default()
    }
}

fn comment(body: &str) -> Option<CommentFact> {
    Some(CommentFact {
        body: body.to_string(),
        created_at: NOW.to_string(),
    })
}

fn facts(issues: Vec<IssueFact>) -> RepoFacts {
    RepoFacts {
        repo: "faberline/keep".to_string(),
        issues,
        last_commit: Some("2026-11-21T11:30:00Z".to_string()),
        ..RepoFacts::default()
    }
}

fn checks_of(f: &RepoFacts) -> Vec<String> {
    repo_section(f, &settings())
        .items
        .iter()
        .map(|i| format!("{} {}", i.check, i.what))
        .collect()
}

fn fresh() -> &'static str {
    "2026-11-21T11:50:00Z"
}

#[test]
fn main_red_names_workflow_run_and_sha() {
    let mut f = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    f.main_runs = vec![
        RunFact {
            workflow: "ci".into(),
            id: 7,
            sha: "abcdef0123456".into(),
            conclusion: "failure".into(),
        },
        RunFact {
            workflow: "docs".into(),
            id: 8,
            sha: "abcdef0123456".into(),
            conclusion: "success".into(),
        },
        RunFact {
            workflow: "nightly".into(),
            id: 9,
            sha: "1234567890".into(),
            conclusion: "cancelled".into(),
        },
    ];
    let items = repo_section(&f, &settings()).items;
    assert_eq!(items.len(), 2);
    assert_eq!(items[0].check, "main-red");
    assert_eq!(items[0].what, "run 7 of workflow ci");
    assert!(items[0].why.contains("failure at abcdef0"));
    assert!(items[1].why.contains("cancelled at 1234567"));
}

#[test]
fn blocked_new_shows_the_first_comment_line() {
    let mut recent = issue(2, &["blocked"], "2026-11-21T11:30:00Z");
    recent.newest_comment = comment("<!-- aw -->\n\nWaits for: #5\nmore");
    let mut old = issue(3, &["blocked"], "2026-11-20T01:00:00Z");
    old.newest_comment = comment("Waits for: #5");
    let items = repo_section(&facts(vec![recent, old]), &settings()).items;
    let new: Vec<_> = items.iter().filter(|i| i.check == "blocked-new").collect();
    assert_eq!(new.len(), 1);
    assert_eq!(new[0].what, "#2 issue 2");
    assert!(new[0].why.ends_with("Waits for: #5"));
}

#[test]
fn blocked_new_without_a_comment_is_still_listed() {
    let i = issue(2, &["blocked"], "2026-11-21T11:30:00Z");
    let items = repo_section(&facts(vec![i]), &settings()).items;
    assert!(
        items
            .iter()
            .any(|i| i.check == "blocked-new" && i.why.contains("no comment"))
    );
}

#[test]
fn blocked_no_reason_needs_a_wait_in_comment_or_body() {
    let mut said = issue(1, &["blocked"], "2026-11-20T01:00:00Z");
    said.newest_comment = comment("Decision: the owner must pick A or B");
    let mut silent = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    silent.newest_comment = comment("looking at it");
    let mut body = issue(3, &["blocked"], "2026-11-20T01:00:00Z");
    body.body = "text\nWaits for: #9".into();
    let none = issue(4, &["blocked"], "2026-11-20T01:00:00Z");
    let got = checks_of(&facts(vec![said, silent, body, none]));
    let reasons: Vec<_> = got
        .iter()
        .filter(|g| g.starts_with("blocked-no-reason"))
        .collect();
    assert_eq!(
        reasons,
        [
            "blocked-no-reason #2 issue 2",
            "blocked-no-reason #4 issue 4"
        ]
    );
}

#[test]
fn stuck_stage_skips_blocked_and_fresh_issues() {
    let stuck = issue(1, &["stage:review"], "2026-11-21T01:00:00Z");
    let fresh_one = issue(2, &["stage:tdd"], fresh());
    let blocked = issue(3, &["stage:spec", "blocked"], "2026-11-01T01:00:00Z");
    let got = repo_section(&facts(vec![stuck, fresh_one, blocked]), &settings()).items;
    let stuck: Vec<_> = got.iter().filter(|i| i.check == "stuck-stage").collect();
    assert_eq!(stuck.len(), 1);
    assert_eq!(stuck[0].what, "#1 issue 1");
    assert_eq!(stuck[0].why, "stage review, not updated for 11h");
}

#[test]
fn pr_stuck_covers_behind_dirty_failed_and_quiet() {
    let pr = |n, state: &str, failed, at: &str| PrFact {
        number: n,
        title: format!("pr {n}"),
        updated_at: at.into(),
        merge_state: state.into(),
        failed_checks: failed,
    };
    let mut f = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    f.prs = vec![
        pr(1, "behind", false, fresh()),
        pr(2, "dirty", false, fresh()),
        pr(3, "clean", true, fresh()),
        pr(4, "clean", false, "2026-11-20T00:00:00Z"),
        pr(5, "clean", false, fresh()),
    ];
    let items: Vec<_> = repo_section(&f, &settings())
        .items
        .into_iter()
        .filter(|i| i.check == "pr-stuck")
        .collect();
    assert_eq!(items.len(), 4);
    assert!(items[0].why.contains("BEHIND"));
    assert!(items[1].why.contains("DIRTY"));
    assert!(items[2].why.contains("check failed"));
    assert!(items[3].why.contains("no update for 36h"));
}

#[test]
fn queue_empty_names_what_each_blocked_issue_waits_for() {
    let mut a = issue(1, &["blocked"], fresh());
    a.body = "Waits for: #9, faberline/core#4".into();
    let b = issue(2, &["blocked"], fresh());
    let items = repo_section(&facts(vec![a, b]), &settings()).items;
    let q = items.iter().find(|i| i.check == "queue-empty").unwrap();
    assert!(q.why.contains("#1 waits for #9 faberline/core#4"));
    assert!(q.why.contains("#2 waits for nothing named"));
}

#[test]
fn queue_empty_with_no_issue_and_with_none_blocked() {
    let items = repo_section(&facts(vec![]), &settings()).items;
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].why, "no open issue");
    let unstaged = issue(1, &[], fresh());
    let items = repo_section(&facts(vec![unstaged]), &settings()).items;
    assert_eq!(items[0].why, "no unblocked issue in any stage");
}

#[test]
fn queue_is_not_empty_when_work_exists() {
    let f = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    assert!(checks_of(&f).is_empty());
}

#[test]
fn waits_for_done_needs_every_prerequisite_closed() {
    let mut all = issue(1, &["blocked"], "2026-11-20T01:00:00Z");
    all.body = "Waits for: #5, faberline/core#6".into();
    let mut part = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    part.body = "Waits for: #5, #7".into();
    let mut f = facts(vec![all, part]);
    f.closed_refs = vec!["#5".into(), "faberline/core#6".into()];
    let items = repo_section(&f, &settings()).items;
    let done: Vec<_> = items
        .iter()
        .filter(|i| i.check == "waits-for-done")
        .collect();
    assert_eq!(done.len(), 1);
    assert_eq!(done[0].what, "#1 issue 1");
    assert!(done[0].why.contains("all closed, still blocked"));
}

#[test]
fn routine_quiet_fires_only_when_work_waits_and_nothing_moved() {
    let work = issue(1, &["stage:tdd"], "2026-11-20T01:00:00Z");
    let mut f = facts(vec![work.clone()]);
    f.last_commit = Some("2026-11-20T01:00:00Z".into());
    assert!(checks_of(&f).iter().any(|c| c.starts_with("routine-quiet")));
    // A recent commit means the pipeline is moving.
    f.last_commit = Some("2026-11-21T11:00:00Z".into());
    assert!(!checks_of(&f).iter().any(|c| c.starts_with("routine-quiet")));
    // No unblocked work: nothing to be quiet about.
    let mut blocked = facts(vec![issue(2, &["blocked"], "2026-11-01T00:00:00Z")]);
    blocked.last_commit = Some("2026-11-01T00:00:00Z".into());
    assert!(
        !checks_of(&blocked)
            .iter()
            .any(|c| c.starts_with("routine-quiet"))
    );
    // Facts with no usable time give no finding.
    let mut none = facts(vec![issue(3, &["stage:tdd"], "bad")]);
    none.last_commit = None;
    assert!(
        !checks_of(&none)
            .iter()
            .any(|c| c.starts_with("routine-quiet"))
    );
}

struct Fake(HashMap<String, Result<RepoFacts, ReadError>>);

impl DeskSource for Fake {
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError> {
        self.0.get(repo).cloned().unwrap()
    }
}

#[test]
fn patrol_adds_the_aw_section_and_marks_a_failed_repo_unknown() {
    let mut aw = facts(vec![
        {
            let mut d = issue(10, &["decision"], fresh());
            d.body = "\n類型：需要使用者\nmore".into();
            d
        },
        issue(11, &["pipeline"], fresh()),
        issue(12, &["decision", "pipeline"], fresh()),
    ]);
    aw.repo = "faberline/aw".into();
    let mut ok = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    ok.repo = "faberline/sift".into();
    let source = Fake(HashMap::from([
        ("faberline/sift".to_string(), Ok(ok)),
        (
            "faberline/core".to_string(),
            Err("gh api repos/faberline/core/issues: HTTP 502".to_string()),
        ),
        ("faberline/aw".to_string(), Ok(aw)),
    ]));
    let repos = vec!["faberline/sift".to_string(), "faberline/core".to_string()];
    let report = patrol(&source, &repos, "faberline/aw", &settings());
    assert!(report.has_unknown());
    assert_eq!(report.sections.len(), 3);
    assert!(
        report.sections[1]
            .unknown
            .as_deref()
            .unwrap()
            .contains("502")
    );
    let aw_items = &report.sections[2].items;
    assert_eq!(aw_items.len(), 4);
    assert_eq!(aw_items[0].why, "類型：需要使用者");
    assert_eq!(aw_items[1].check, "pipeline-open");
    let md = report.markdown();
    assert!(md.contains("## faberline/sift"));
    assert!(md.contains("open 1, spec 0, tdd 1, review 0, blocked 0, open PRs 0, last merge none"));
    assert!(md.contains("- [ ] unknown: cannot read gh api repos/faberline/core/issues"));
    assert!(md.contains("nothing to look at"));
    assert!(
        md.trim_end()
            .ends_with("To look at: decision-open 2, pipeline-open 2, unknown 1")
    );
    let json: serde_json::Value = serde_json::from_str(&report.json()).unwrap();
    assert!(
        json["sections"][1]["unknown"]
            .as_str()
            .unwrap()
            .contains("502")
    );
    assert!(json["summary"].as_str().unwrap().starts_with("To look at"));
}

#[test]
fn a_clean_patrol_says_nothing_to_look_at() {
    let mut ok = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    ok.repo = "faberline/sift".into();
    let source = Fake(HashMap::from([
        ("faberline/sift".to_string(), Ok(ok.clone())),
        ("faberline/aw".to_string(), Ok(ok)),
    ]));
    let report = patrol(
        &source,
        &["faberline/sift".to_string()],
        "faberline/aw",
        &settings(),
    );
    assert!(!report.has_unknown());
    assert_eq!(report.summary, "nothing to look at");
    assert_eq!(Report::new(vec![]).summary, "nothing to look at");
}

#[test]
fn repo_names_use_the_list_or_the_override() {
    let configured = vec!["sift".to_string(), "core".to_string()];
    let (repos, aw) = repo_names("faberline", &configured, "aw", &[]);
    assert_eq!(repos, ["faberline/sift", "faberline/core"]);
    assert_eq!(aw, "faberline/aw");
    let only = vec!["keep".to_string(), "other/x".to_string()];
    let (repos, _) = repo_names("faberline", &configured, "aw", &only);
    assert_eq!(repos, ["faberline/keep", "other/x"]);
}

#[test]
fn the_config_comes_from_the_desk_table() {
    let config =
        parse_config("[desk]\norg = \"faberline\"\nrepos = [\"sift\", \"core\"]\naw = \"aw\"\n")
            .unwrap();
    assert_eq!(config.org, "faberline");
    assert_eq!(config.repos, ["sift", "core"]);
    assert!(parse_config("[hygiene]\n").is_err());
    assert!(parse_config("[desk]\nrepos = []\n").is_err());
    assert!(parse_config("[desk]\norg = \"o\"\n").is_err());
    assert!(parse_config("not toml [").is_err());
    let default_aw = parse_config("[desk]\norg = \"o\"\nrepos = []\n").unwrap();
    assert_eq!(default_aw.aw, "aw");
}

/// A fake of `gh api`: a path to a JSON text, or an error.
struct Paths(HashMap<String, String>);

impl Api for Paths {
    fn get(&self, path: &str) -> Result<String, String> {
        let key = path.split("&per_page").next().unwrap_or(path);
        let key = key.split("?per_page").next().unwrap_or(key);
        self.0
            .get(key)
            .cloned()
            .ok_or_else(|| format!("gh api {path}: HTTP 404"))
    }
}

fn gh_fixture() -> HashMap<String, String> {
    let mut m = HashMap::new();
    let mut put = |k: &str, v: &str| {
        m.insert(k.to_string(), v.to_string());
    };
    put(
        "repos/o/r/issues?state=open",
        r#"[
          {"number":1,"title":"a","updated_at":"2026-11-21T11:00:00Z","body":"Waits for: #9, p/q#2",
           "labels":[{"name":"blocked"},{"name":"stage:tdd"}]},
          {"number":2,"title":"pr","updated_at":"2026-11-21T11:00:00Z","body":null,"labels":[],"pull_request":{}},
          {"number":3,"title":"c","updated_at":"2026-11-21T11:00:00Z","body":null,"labels":[{"name":"stage:spec"}]}
        ]"#,
    );
    put(
        "repos/o/r/issues/1/comments?sort=created",
        r#"[{"body":"old","created_at":"2026-11-20T00:00:00Z"},{"body":"Waits for: #9","created_at":"2026-11-21T00:00:00Z"}]"#,
    );
    put("repos/o/r/issues/9", r#"{"state":"closed"}"#);
    put("repos/p/q/issues/2", r#"{"state":"open"}"#);
    put(
        "repos/o/r/pulls?state=open",
        r#"[{"number":5,"title":"p","updated_at":"2026-11-21T11:00:00Z","head":{"sha":"abc"}}]"#,
    );
    put("repos/o/r/pulls/5", r#"{"mergeable_state":"behind"}"#);
    put(
        "repos/o/r/commits/abc/check-runs",
        r#"{"check_runs":[{"conclusion":"success"},{"conclusion":"timed_out"}]}"#,
    );
    put(
        "repos/o/r/actions/runs?branch=main&status=completed",
        r#"{"workflow_runs":[
          {"name":"ci","id":3,"head_sha":"s3","conclusion":"failure"},
          {"name":"ci","id":2,"head_sha":"s2","conclusion":"success"},
          {"name":"docs","id":1,"head_sha":"s1","conclusion":"success"}]}"#,
    );
    put(
        "repos/o/r/pulls?state=closed&sort=updated&direction=desc",
        r#"[{"merged_at":null},{"merged_at":"2026-11-20T01:00:00Z"},{"merged_at":"2026-11-21T01:00:00Z"}]"#,
    );
    put(
        "repos/o/r/commits",
        r#"[{"commit":{"committer":{"date":"2026-11-21T10:00:00Z"}}}]"#,
    );
    m
}

#[test]
fn the_gh_reader_builds_the_facts_from_json() {
    let desk = GhDesk::with_api(Paths(gh_fixture()));
    let f = desk.facts("o/r").unwrap();
    assert_eq!(f.issues.len(), 2, "a pull request is not an issue");
    assert!(f.issues[0].blocked());
    assert_eq!(f.issues[0].stage(), Some("tdd"));
    assert_eq!(
        f.issues[0].newest_comment.as_ref().unwrap().body,
        "Waits for: #9"
    );
    assert!(f.issues[1].newest_comment.is_none());
    assert_eq!(f.closed_refs, ["#9"]);
    assert_eq!(f.prs.len(), 1);
    assert_eq!(f.prs[0].merge_state, "behind");
    assert!(f.prs[0].failed_checks);
    assert_eq!(f.main_runs.len(), 2);
    assert_eq!(f.main_runs[0].id, 3);
    assert_eq!(f.last_merge.as_deref(), Some("2026-11-21T01:00:00Z"));
    assert_eq!(f.last_commit.as_deref(), Some("2026-11-21T10:00:00Z"));
}

#[test]
fn a_failed_call_is_named_and_not_a_clean_repo() {
    let mut m = gh_fixture();
    m.remove("repos/o/r/commits");
    let err = GhDesk::with_api(Paths(m)).facts("o/r").unwrap_err();
    assert!(err.contains("repos/o/r/commits"), "{err}");
    let err = GhDesk::with_api(Paths(HashMap::new()))
        .facts("o/r")
        .unwrap_err();
    assert!(err.contains("HTTP 404"));
    let mut bad = gh_fixture();
    bad.insert("repos/o/r/issues?state=open".into(), "not json".into());
    let err = GhDesk::with_api(Paths(bad)).facts("o/r").unwrap_err();
    assert!(err.contains("not JSON"));
}

#[test]
fn the_cli_patrol_needs_a_config_or_a_repo() {
    let bin = env!("CARGO_BIN_EXE_aw");
    let dir = std::env::temp_dir().join(format!("aw-desk-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let out = std::process::Command::new(bin)
        .args(["desk", "patrol", "--dir"])
        .arg(&dir)
        .output()
        .unwrap();
    // No aw.toml in this folder or above it (the temp dir): exit 2.
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("aw.toml"));
    std::fs::remove_dir_all(&dir).ok();
}
