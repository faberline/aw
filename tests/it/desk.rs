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

fn comment(body: &str) -> Vec<CommentFact> {
    vec![CommentFact {
        body: body.to_string(),
        created_at: NOW.to_string(),
    }]
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
            ..RunFact::default()
        },
        RunFact {
            workflow: "docs".into(),
            id: 8,
            sha: "abcdef0123456".into(),
            conclusion: "success".into(),
            ..RunFact::default()
        },
        RunFact {
            workflow: "nightly".into(),
            id: 9,
            sha: "1234567890".into(),
            conclusion: "cancelled".into(),
            ..RunFact::default()
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
    recent.comments = comment("<!-- aw -->\n\nWaits for: #5\nmore");
    let mut old = issue(3, &["blocked"], "2026-11-20T01:00:00Z");
    old.comments = comment("Waits for: #5");
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
    said.comments = comment("Decision: the owner must pick A or B");
    let mut silent = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    silent.comments = comment("looking at it");
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
        issue: None,
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
            .starts_with("To look at: decision-open 2, pipeline-open 2, unknown 1")
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
    assert_eq!(f.issues[0].newest_comment().unwrap().body, "Waits for: #9");
    assert!(f.issues[1].comments.is_empty());
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
fn without_a_desk_table_the_built_in_list_is_used() {
    use aw::infrastructure::desk::{DeskConfig, load_config};
    let dir = std::env::temp_dir().join(format!("aw-desk-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    // No aw.toml in this folder or above it (the temp dir): no config.
    assert_eq!(load_config(&dir).unwrap(), None);
    std::fs::write(dir.join("aw.toml"), "[meta]\nx = 1\n").unwrap();
    assert_eq!(load_config(&dir).unwrap(), None);
    std::fs::write(
        dir.join("aw.toml"),
        "[desk]\norg = \"o\"\nrepos = [\"a\"]\n",
    )
    .unwrap();
    assert_eq!(load_config(&dir).unwrap().unwrap().repos, ["a"]);
    std::fs::write(dir.join("aw.toml"), "not toml [").unwrap();
    assert!(load_config(&dir).is_err());
    std::fs::remove_dir_all(&dir).ok();
    let builtin = DeskConfig::builtin();
    assert_eq!(builtin.org, "faberline");
    assert_eq!(builtin.aw, "aw");
    assert_eq!(builtin.repos.len(), 9);
}

fn blocked(n: u64, body: &str) -> IssueFact {
    let mut i = issue(n, &["blocked"], "2026-11-20T01:00:00Z");
    i.body = body.into();
    i
}

fn done_or_split(f: &RepoFacts) -> Vec<String> {
    repo_section(f, &settings())
        .items
        .into_iter()
        .filter(|i| i.check.starts_with("waits-for"))
        .map(|i| format!("{} {}", i.check, i.what))
        .collect()
}

#[test]
fn a_parent_in_brackets_is_not_a_blocker() {
    // "(child A2 of #401)" names the parent. The blocker is #402, still open.
    let mut f = facts(vec![blocked(1, "Waits for: #402 (child A2 of #401)")]);
    f.closed_refs = vec!["#401".into()];
    assert!(done_or_split(&f).is_empty());
    f.closed_refs = vec!["#402".into()];
    assert_eq!(done_or_split(&f), ["waits-for-done #1 issue 1"]);
}

#[test]
fn a_wait_closed_as_not_planned_is_a_split_and_not_done() {
    let mut f = facts(vec![blocked(1, "Waits for: #300, #301")]);
    f.closed_refs = vec!["#301".into()];
    f.split_refs = vec!["#300".into()];
    assert_eq!(done_or_split(&f), ["waits-for-split #1 issue 1"]);
    let why = repo_section(&f, &settings())
        .items
        .into_iter()
        .find(|i| i.check == "waits-for-split")
        .unwrap()
        .why;
    assert!(why.contains("#300") && why.contains("not planned"), "{why}");
}

#[test]
fn a_comment_that_says_now_waits_for_replaces_the_body_line() {
    // lumen#383: the body names #379 (closed); the comment says #382 (open).
    let mut i = blocked(1, "Waits for: #379");
    i.comments = comment("Supervisor sweep: now waits for #382");
    let mut f = facts(vec![i.clone()]);
    f.closed_refs = vec!["#379".into()];
    assert!(done_or_split(&f).is_empty());
    f.closed_refs = vec!["#382".into()];
    assert_eq!(done_or_split(&f), ["waits-for-done #1 issue 1"]);
    // som#304: the desk says blocked until two issues are merged.
    i.comments = comment("desk: blocked until #308, #309 are merged");
    assert_eq!(
        i.waits().iter().map(|p| p.show()).collect::<Vec<_>>(),
        ["#308", "#309"]
    );
    // A comment that ends a wait changes nothing.
    i.comments = comment("it no longer waits for #5");
    assert_eq!(i.waits()[0].show(), "#379");
}

#[test]
fn a_reason_in_any_comment_or_label_counts() {
    // lumen#515: the desk comment is not the newest one.
    let mut desk = blocked(1, "");
    desk.comments = vec![
        CommentFact {
            body: "desk: blocked until #325 is merged".into(),
            created_at: NOW.into(),
        },
        CommentFact {
            body: "looking at it".into(),
            created_at: NOW.into(),
        },
    ];
    let mut label = blocked(2, "");
    label.labels.push("decision".into());
    let mut owner = blocked(3, "");
    owner.comments = comment("the owner must pay for the run");
    let silent = blocked(4, "");
    let f = facts(vec![desk, label, owner, silent]);
    let section = repo_section(&f, &settings());
    let none: Vec<_> = section
        .items
        .iter()
        .filter(|i| i.check == "blocked-no-reason")
        .collect();
    assert_eq!(none.len(), 1);
    assert_eq!(none[0].what, "#4 issue 4");
    let r = section.counts.unwrap().blocked_reasons;
    assert_eq!((r.waits, r.comment, r.label, r.none), (1, 1, 1, 1));
    let md = Report::new(vec![repo_section(&f, &settings())]).markdown();
    assert!(md.contains("blocked 4 (waits-for 1, comment 1, label 1, no reason 1)"));
}

#[test]
fn main_red_skips_old_hand_started_runs() {
    let run = |id, event: &str, at: &str| RunFact {
        workflow: format!(".github/workflows/w{id}.yml"),
        id,
        event: event.into(),
        created_at: at.into(),
        conclusion: "failure".into(),
        ..RunFact::default()
    };
    let mut f = facts(vec![issue(1, &["stage:tdd"], fresh())]);
    f.main_runs = vec![
        run(1, "push", "2026-11-01T00:00:00Z"),
        run(2, "schedule", "2026-11-01T00:00:00Z"),
        run(3, "workflow_dispatch", "2026-11-01T00:00:00Z"),
        run(4, "workflow_dispatch", "2026-11-21T11:30:00Z"),
    ];
    let reds: Vec<_> = repo_section(&f, &settings())
        .items
        .into_iter()
        .map(|i| i.what)
        .collect();
    assert_eq!(
        reds,
        [
            "run 1 of workflow .github/workflows/w1.yml",
            "run 2 of workflow .github/workflows/w2.yml",
            "run 4 of workflow .github/workflows/w4.yml",
        ]
    );
}

#[test]
fn stuck_stage_is_one_line_for_each_stage_except_review_and_expedite() {
    let mut issues = vec![
        issue(1, &["stage:spec"], "2026-11-10T00:00:00Z"),
        issue(2, &["stage:spec"], "2026-11-12T00:00:00Z"),
        issue(3, &["stage:spec"], "2026-11-11T00:00:00Z"),
        issue(4, &["stage:spec"], "2026-11-13T00:00:00Z"),
        issue(5, &["stage:tdd"], "2026-11-20T00:00:00Z"),
        issue(6, &["stage:review"], "2026-11-20T00:00:00Z"),
        issue(7, &["stage:tdd", "expedite"], "2026-11-20T00:00:00Z"),
        issue(8, &["stage:tdd"], fresh()),
    ];
    issues.push(issue(9, &["stage:spec", "blocked"], "2026-11-01T00:00:00Z"));
    let items: Vec<_> = repo_section(&facts(issues), &settings())
        .items
        .into_iter()
        .filter(|i| i.check == "stuck-stage")
        .collect();
    let whats: Vec<&str> = items.iter().map(|i| i.what.as_str()).collect();
    assert_eq!(
        whats,
        [
            "#6 issue 6",
            "#7 issue 7",
            "stage spec: 4 issues",
            "stage tdd: 1 issues"
        ]
    );
    assert!(
        items[2]
            .why
            .contains("oldest #1 (276h), #3 (252h), #2 (228h)"),
        "{}",
        items[2].why
    );
}

#[test]
fn a_pr_of_a_blocked_or_closed_issue_is_an_old_pr() {
    let pr = |n, issue: Option<u64>| PrFact {
        number: n,
        title: format!("pr {n}"),
        updated_at: "2026-11-01T00:00:00Z".into(),
        merge_state: "dirty".into(),
        failed_checks: false,
        issue,
    };
    let mut f = facts(vec![
        issue(1, &["stage:tdd"], fresh()),
        issue(2, &["blocked"], fresh()),
    ]);
    f.prs = vec![
        pr(10, Some(1)),
        pr(11, Some(2)),
        pr(12, Some(99)),
        pr(13, None),
    ];
    let items: Vec<_> = repo_section(&f, &settings())
        .items
        .into_iter()
        .filter(|i| i.check == "pr-stuck")
        .map(|i| i.what)
        .collect();
    assert_eq!(items, ["PR #10 pr 10", "PR #13 pr 13", "old PRs: #11, #12"]);
}

#[test]
fn only_keeps_the_named_checks_and_the_summary_comes_first() {
    let mut f = facts(vec![
        blocked(1, ""),
        issue(2, &["stage:tdd"], "2026-11-01T00:00:00Z"),
    ]);
    f.main_runs = vec![RunFact {
        workflow: "ci".into(),
        id: 1,
        conclusion: "failure".into(),
        ..RunFact::default()
    }];
    let all = Report::new(vec![repo_section(&f, &settings())]);
    assert!(all.summary.contains("main-red 1"));
    let one = all.only(&["blocked-no-reason".to_string()]);
    assert_eq!(one.summary, "To look at: blocked-no-reason 1");
    assert!(
        one.markdown()
            .starts_with("To look at: blocked-no-reason 1\n")
    );
    assert!(aw::domain::desk::checks::CHECKS.contains(&"waits-for-split"));
}

#[test]
fn the_gh_reader_reads_the_split_state_the_workflow_path_and_the_pr_issue() {
    let mut m = gh_fixture();
    m.insert(
        "repos/o/r/issues?state=open".into(),
        r#"[{"number":1,"title":"a","updated_at":"2026-11-21T11:00:00Z",
             "body":"Waits for: #300 (child A2 of #401)","labels":[{"name":"blocked"}]}]"#
            .into(),
    );
    m.insert(
        "repos/o/r/issues/1/comments?sort=created".into(),
        "[]".into(),
    );
    // #401 is not in the map: asking for it would fail the read.
    m.insert(
        "repos/o/r/issues/300".into(),
        r#"{"state":"closed","state_reason":"not_planned"}"#.into(),
    );
    m.insert(
        "repos/o/r/pulls/5".into(),
        r#"{"mergeable_state":"clean","body":"Closes #1\n\nmore"}"#.into(),
    );
    m.insert(
        "repos/o/r/actions/runs?branch=main&status=completed".into(),
        r#"{"workflow_runs":[
          {"name":"gke: fix a thing","path":".github/workflows/g.yml","event":"push","created_at":"2026-11-21T01:00:00Z","id":3,"head_sha":"s3","conclusion":"failure"},
          {"name":"gke: older","path":".github/workflows/g.yml","event":"push","created_at":"2026-11-20T01:00:00Z","id":2,"head_sha":"s2","conclusion":"failure"}]}"#
            .into(),
    );
    let f = GhDesk::with_api(Paths(m)).facts("o/r").unwrap();
    assert!(f.closed_refs.is_empty());
    assert_eq!(f.split_refs, ["#300"]);
    assert_eq!(f.prs[0].issue, Some(1));
    assert_eq!(f.main_runs.len(), 1);
    assert_eq!(f.main_runs[0].workflow, ".github/workflows/g.yml");
    assert_eq!(f.main_runs[0].id, 3);
    assert_eq!(f.main_runs[0].event, "push");
}

#[test]
fn a_comment_reference_with_a_repo_name_is_that_repo() {
    let mut i = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    i.comments = comment("Waits for aw#195 and faberline/reach#7 and #9");
    let shown: Vec<String> = i.waits().iter().map(|p| p.show()).collect();
    assert_eq!(shown, vec!["aw#195", "faberline/reach#7", "#9"]);
}

#[test]
fn a_hold_comment_without_an_issue_number_is_a_hold_not_waits_for_done() {
    let mut i = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    i.body = "Waits for: #5".into();
    i.comments = comment("Stays blocked: no paid run is approved.");
    let mut f = facts(vec![i]);
    f.closed_refs = vec!["#5".into()];
    let section = repo_section(&f, &settings());
    assert!(section.items.iter().all(|i| i.check != "waits-for-done"));
    let r = section.counts.unwrap().blocked_reasons;
    assert_eq!((r.hold, r.waits), (1, 0));
    let md = Report::new(vec![repo_section(&f, &settings())]).markdown();
    assert!(md.contains("blocked 1 (waits-for 0, hold 1, comment 0, label 0, no reason 0)"));
}

#[test]
fn a_newer_comment_with_a_wait_ends_the_hold() {
    let mut i = issue(2, &["blocked"], "2026-11-20T01:00:00Z");
    i.comments = vec![
        CommentFact {
            body: "Stays blocked: paid".into(),
            created_at: NOW.into(),
        },
        CommentFact {
            body: "Now waits for #5".into(),
            created_at: NOW.into(),
        },
    ];
    assert!(!i.on_hold());
}

#[test]
fn a_wait_with_another_human_condition_is_a_hold() {
    let hold = |body: &str| {
        IssueFact {
            comments: vec![CommentFact {
                body: body.to_string(),
                ..CommentFact::default()
            }],
            ..IssueFact::default()
        }
        .on_hold()
    };
    assert!(hold(
        "Stays blocked until #259 is merged and a second released version exists"
    ));
    assert!(hold("Waits for #259 and a second released version"));
    assert!(!hold("Now waits for #5"));
}
