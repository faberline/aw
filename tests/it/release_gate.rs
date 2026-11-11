//! The milestone release gate against an in-memory code host. Each test
//! changes one input and checks the guard that must see it.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};

use aw::application::release::{Advanced, ReleaseServices, advance, preflight, status};
use aw::domain::release::contract::{CONTRACT_PATH, goal_identity};
use aw::domain::release::digest::{blob_sha, digest};
use aw::domain::release::policy::POLICY_PATH;
use aw::domain::release::ports::{Provider, WorkflowParser};
use aw::domain::work::ports::{PortError, PortResult};
use aw::infrastructure::release::YamlWorkflows;
use aw::interfaces::work::cli::{Exit, ReleaseCall, run_release};
use base64::Engine as _;
use serde_json::{Value, json};

const REPO: &str = "o/r";
const ROOT: &str = "repos/o/r";
const WORKFLOW_PATH: &str = ".github/workflows/release.yml";
const WORKFLOW: &str = "name: release\non:\n  push:\n    tags: ['v*']\njobs: {}\n";
const RC1: &str = "v0.5.0-rc.1";
const RC2: &str = "v0.5.0-rc.2";
const FINAL: &str = "v0.5.0";
const JOBS: [&str; 3] = ["unit", "e2e", "perf"];

fn sha(c: char) -> String {
    c.to_string().repeat(40)
}

/// The default branch at the start.
fn head() -> String {
    sha('a')
}

/// The merge commit of pull request 5.
fn merge() -> String {
    sha('5')
}

type Hook = (&'static str, usize, fn(&mut State));

#[derive(Default)]
struct State {
    head: String,
    files: BTreeMap<(String, String), Vec<u8>>,
    milestone: Value,
    issues: Vec<Value>,
    pulls: Vec<Value>,
    /// `(commit, target)`: the commit is an ancestor of the target.
    ancestors: BTreeSet<(String, String)>,
    tags: Vec<(String, String)>,
    branches: BTreeSet<String>,
    runs: Vec<Value>,
    jobs: BTreeMap<(u64, u64), Vec<Value>>,
    release: Option<Value>,
    /// An exact path and its answer, over every route.
    overrides: BTreeMap<String, Option<Value>>,
    /// Before the n-th `GET` of a path that contains the text, change the
    /// state.
    hooks: Vec<Hook>,
    hits: BTreeMap<&'static str, usize>,
    /// Every tag write, in order.
    created: Vec<(String, String)>,
    /// The tag write answers with this, and stores the tag at this commit.
    write_as: Option<String>,
    write_fails: bool,
}

struct Fake {
    repo: String,
    s: RefCell<State>,
}

fn page(rows: &[Value], query: &str) -> Vec<Value> {
    let page: usize = query
        .split('&')
        .find_map(|pair| pair.strip_prefix("page="))
        .and_then(|n| n.parse().ok())
        .unwrap_or(1);
    rows.iter()
        .skip((page - 1) * 100)
        .take(100)
        .cloned()
        .collect()
}

impl State {
    fn route(&self, route: &str, query: &str) -> Option<Value> {
        let parts: Vec<&str> = route.split('/').skip(1).collect();
        match parts.as_slice() {
            [] => Some(json!({"default_branch": "main"})),
            ["commits", "main"] => Some(json!({"sha": self.head})),
            ["contents", ..] => {
                let path = route.strip_prefix("/contents/")?;
                let revision = query.strip_prefix("ref=")?;
                let raw = self.files.get(&(revision.to_string(), path.to_string()))?;
                let text = base64::engine::general_purpose::STANDARD.encode(raw);
                let lines: Vec<&str> = text
                    .as_bytes()
                    .chunks(60)
                    .map(|c| std::str::from_utf8(c).unwrap())
                    .collect();
                Some(json!({
                    "type": "file", "encoding": "base64", "size": raw.len(),
                    "content": lines.join("\n"), "sha": blob_sha(raw),
                }))
            }
            ["milestones", n] => (self.milestone["number"].as_u64() == n.parse().ok())
                .then(|| self.milestone.clone()),
            ["issues"] => Some(json!(page(&self.issues, query))),
            ["pulls"] => Some(json!(page(&self.pulls, query))),
            ["tags"] => {
                let rows: Vec<Value> = self
                    .tags
                    .iter()
                    .map(|(name, sha)| json!({"name": name, "commit": {"sha": sha}}))
                    .collect();
                Some(json!(page(&rows, query)))
            }
            ["compare", pair] => {
                let (commit, target) = pair.split_once("...")?;
                let holds = self
                    .ancestors
                    .contains(&(commit.to_string(), target.to_string()));
                Some(json!({
                    "behind_by": u64::from(!holds),
                    "merge_base_commit": {"sha": if holds { commit.to_string() } else { sha('0') }},
                }))
            }
            ["git", "ref", "heads", name] => {
                self.branches.contains(*name).then(|| json!({"ref": name}))
            }
            ["git", "ref", "tags", name] => self
                .tags
                .iter()
                .find(|(tag, _)| tag == name)
                .map(|(_, sha)| json!({"object": {"sha": sha}})),
            ["actions", "workflows", _, "runs"] => {
                Some(json!({"workflow_runs": page(&self.runs, query)}))
            }
            ["actions", "runs", id] => self
                .runs
                .iter()
                .find(|r| r["id"].as_u64() == id.parse().ok())
                .cloned(),
            ["actions", "runs", id, "attempts", n, "jobs"] => {
                let rows = self
                    .jobs
                    .get(&(id.parse().ok()?, n.parse().ok()?))
                    .cloned()
                    .unwrap_or_default();
                Some(json!({"jobs": page(&rows, query)}))
            }
            ["releases", "tags", _] => self.release.clone(),
            _ => panic!("the fake has no route {route}"),
        }
    }
}

impl Provider for Fake {
    fn repo(&self) -> &str {
        &self.repo
    }

    fn get(&self, path: &str) -> PortResult<Option<Value>> {
        let mut s = self.s.borrow_mut();
        for (needle, nth, change) in s.hooks.clone() {
            if path.contains(needle) {
                let hits = s.hits.entry(needle).or_default();
                *hits += 1;
                if *hits == nth {
                    change(&mut s);
                }
            }
        }
        if let Some(answer) = s.overrides.get(path) {
            return Ok(answer.clone());
        }
        let tail = path.strip_prefix(ROOT).expect("a path of this repository");
        let (route, query) = tail.split_once('?').unwrap_or((tail, ""));
        Ok(s.route(route, query))
    }

    fn create_tag(&self, tag: &str, sha: &str) -> PortResult<Value> {
        let mut s = self.s.borrow_mut();
        s.created.push((tag.to_string(), sha.to_string()));
        if s.write_fails || s.tags.iter().any(|(name, _)| name == tag) {
            return Err(PortError("Reference already exists (HTTP 422)".to_string()));
        }
        let stored = s.write_as.clone().unwrap_or_else(|| sha.to_string());
        s.tags.push((tag.to_string(), stored.clone()));
        Ok(json!({"ref": format!("refs/tags/{tag}"), "object": {"sha": stored}}))
    }
}

fn policy() -> Value {
    json!({
        "version": 1, "repo": REPO,
        "workflows": [{
            "path": WORKFLOW_PATH, "id": 77, "definition_sha": blob_sha(WORKFLOW.as_bytes()),
            "jobs": {"unit": "unit", "e2e": "functional_e2e", "perf": "performance"},
        }],
    })
}

fn milestone() -> Value {
    json!({"number": 8, "title": "core@0.5.0", "description": "The goal", "state": "open"})
}

fn contract() -> Value {
    json!({
        "version": 1, "repo": REPO,
        "milestones": {"8": {
            "version": "0.5.0",
            "goal_sha256": digest(&goal_identity(&milestone())),
            "acceptance": [{"goal": "It works", "issues": [3], "checks": JOBS}],
            "publication": "github-release",
        }},
    })
}

impl Fake {
    /// Milestone 8 with one closed issue, one landed pull request and no
    /// tag: the state is `ready_rc`.
    fn new() -> Fake {
        let fake = Fake {
            repo: REPO.to_string(),
            s: RefCell::new(State {
                head: head(),
                milestone: milestone(),
                issues: vec![json!({"number": 3, "state": "closed", "body": "Do it"})],
                pulls: vec![json!({
                    "number": 5, "body": "The work.\n\nCloses #3", "milestone": null,
                    "merged_at": "2026-11-01T00:00:00Z", "merge_commit_sha": merge(),
                })],
                ..State::default()
            }),
        };
        fake.commit(&head());
        fake
    }

    /// Give the commit the reviewed files, and the work of pull request 5.
    fn commit(&self, commit: &str) {
        self.file(commit, CONTRACT_PATH, &contract().to_string());
        self.file(commit, POLICY_PATH, &policy().to_string());
        self.file(commit, WORKFLOW_PATH, WORKFLOW);
        self.edit(|s| {
            s.ancestors.insert((merge(), commit.to_string()));
        });
    }

    fn file(&self, commit: &str, path: &str, text: &str) {
        self.edit(|s| {
            s.files.insert(
                (commit.to_string(), path.to_string()),
                text.as_bytes().to_vec(),
            );
        });
    }

    fn edit<T>(&self, change: impl FnOnce(&mut State) -> T) -> T {
        change(&mut self.s.borrow_mut())
    }

    fn tag(&self, name: &str, commit: &str) {
        self.edit(|s| s.tags.push((name.to_string(), commit.to_string())));
    }

    /// A finished, green run of the tag with the three jobs of the policy.
    fn run(&self, id: u64, tag: &str, commit: &str) {
        self.edit(|s| {
            s.runs.push(json!({
                "id": id, "event": "push", "head_branch": tag, "head_sha": commit,
                "workflow_id": 77, "path": WORKFLOW_PATH, "run_attempt": 1,
                "head_repository": {"full_name": REPO},
                "status": "completed", "conclusion": "success",
            }));
            let jobs = JOBS
                .iter()
                .enumerate()
                .map(|(i, name)| job(id, 1, id * 10 + i as u64, name, commit));
            s.jobs.insert((id, 1), jobs.collect());
        });
    }

    fn hook(&self, needle: &'static str, nth: usize, change: fn(&mut State)) {
        self.edit(|s| s.hooks.push((needle, nth, change)));
    }

    fn answer(&self, tail: &str, answer: Option<Value>) {
        self.edit(|s| s.overrides.insert(format!("{ROOT}{tail}"), answer));
    }

    fn services(&self) -> ReleaseServices<'_> {
        ReleaseServices {
            provider: self,
            workflows: &YamlWorkflows,
        }
    }

    fn status(&self) -> Value {
        status(self.services(), 8).unwrap()
    }

    fn stop(&self) -> String {
        status(self.services(), 8).unwrap_err().0
    }

    fn created(&self) -> Vec<(String, String)> {
        self.edit(|s| s.created.clone())
    }

    /// Release candidate 1 on the default branch with a green run.
    fn green_rc() -> Fake {
        let fake = Fake::new();
        fake.tag(RC1, &head());
        fake.run(900, RC1, &head());
        fake
    }

    /// The final tag on the commit of the green candidate, with a green run.
    fn green_final() -> Fake {
        let fake = Fake::green_rc();
        fake.tag(FINAL, &head());
        fake.run(901, FINAL, &head());
        fake
    }
}

fn job(run: u64, attempt: u64, id: u64, name: &str, commit: &str) -> Value {
    json!({
        "id": id, "run_id": run, "run_attempt": attempt, "head_sha": commit, "name": name,
        "status": "completed", "conclusion": "success",
        "html_url": format!("https://github.com/{REPO}/actions/runs/{run}/job/{id}"),
        "steps": [{"name": "Build", "conclusion": "success"}],
    })
}

fn first_run(s: &mut State) -> &mut Value {
    &mut s.runs[0]
}

fn first_job(s: &mut State) -> &mut Value {
    &mut s.jobs.get_mut(&(900, 1)).unwrap()[0]
}

// ---- states -------------------------------------------------------------

#[test]
fn no_tag_is_ready_for_the_first_candidate() {
    let fake = Fake::new();
    let a = fake.status();
    assert_eq!(a["schema"], "AW_RELEASE_ASSESSMENT_V1");
    assert_eq!(
        (a["state"].as_str(), a["action"].as_str()),
        (Some("ready_rc"), Some("tag"))
    );
    assert_eq!(a["tag"], RC1);
    assert_eq!(a["release_head"], head());
    assert!(fake.created().is_empty(), "status only reads");
}

#[test]
fn the_assessment_is_stable_and_follows_every_input() {
    let fake = Fake::new();
    let first = fake.status();
    assert_eq!(first, fake.status());
    fake.edit(|s| s.issues[0]["body"] = json!("Do it now"));
    assert_ne!(first["fingerprint"], fake.status()["fingerprint"]);
}

#[test]
fn open_work_holds_the_release() {
    let fake = Fake::new();
    fake.edit(|s| {
        s.issues
            .push(json!({"number": 4, "state": "open", "body": ""}))
    });
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].is_null()),
        (Some("work"), true)
    );
    assert!(a["reasons"][0].as_str().unwrap().contains("[4]"));

    let fake = Fake::new();
    fake.edit(|s| s.ancestors.clear()); // Merged, but not on the default branch.
    assert!(
        fake.status()["reasons"][1]
            .as_str()
            .unwrap()
            .contains("[5]")
    );

    let fake = Fake::new();
    fake.edit(|s| {
        s.pulls
            .push(json!({"number": 6, "body": "", "milestone": {"number": 8}, "merged_at": null}));
    });
    assert!(
        fake.status()["reasons"][1]
            .as_str()
            .unwrap()
            .contains("[6]")
    );
}

#[test]
fn a_pull_request_in_the_issue_list_is_not_an_issue() {
    let fake = Fake::new();
    fake.edit(|s| {
        s.issues
            .push(json!({"number": 5, "state": "open", "pull_request": {}}))
    });
    assert_eq!(fake.status()["state"], "ready_rc");
}

#[test]
fn a_goal_issue_needs_a_landed_pull_request() {
    let fake = Fake::new();
    fake.edit(|s| s.pulls[0]["body"] = json!("See #3"));
    fake.edit(|s| s.pulls[0]["milestone"] = json!({"number": 8}));
    assert!(
        fake.stop()
            .contains("closed goal issue lacks a merged landed PR")
    );

    let fake = Fake::new();
    fake.edit(|s| s.pulls[0]["merge_commit_sha"] = json!(null));
    assert!(fake.stop().contains("merged PR has no exact commit"));
}

#[test]
fn a_candidate_without_a_run_is_testing() {
    let fake = Fake::new();
    fake.tag(RC1, &head());
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].is_null()),
        (Some("testing"), true)
    );
    assert_eq!(a["tag"], RC1);
    assert!(
        a["reasons"][0]
            .as_str()
            .unwrap()
            .contains("full tag run is missing")
    );
}

#[test]
fn a_running_attempt_is_testing() {
    let fake = Fake::green_rc();
    fake.edit(|s| {
        first_run(s)["status"] = json!("in_progress");
        first_run(s)["conclusion"] = json!(null);
    });
    let a = fake.status();
    assert_eq!(a["state"], "testing");
    assert!(
        a["reasons"][0]
            .as_str()
            .unwrap()
            .contains("latest attempt is running")
    );
}

#[test]
fn a_green_candidate_is_ready_for_the_final_tag() {
    let a = Fake::green_rc().status();
    assert_eq!(
        (a["state"].as_str(), a["action"].as_str()),
        (Some("ready_final"), Some("tag"))
    );
    assert_eq!(
        (a["tag"].as_str(), a["release_head"].as_str()),
        (Some(FINAL), Some(head().as_str()))
    );
}

#[test]
fn only_exactly_the_reviewed_green_jobs_pass() {
    let changes: [fn(&mut State); 8] = [
        |s| first_job(s)["conclusion"] = json!("skipped"),
        |s| first_job(s)["conclusion"] = json!("cancelled"),
        |s| first_job(s)["conclusion"] = json!("neutral"),
        |s| first_job(s)["status"] = json!("queued"),
        |s| first_job(s)["name"] = json!("lint"),
        |s| first_job(s)["name"] = json!("e2e"),
        |s| drop(s.jobs.get_mut(&(900, 1)).unwrap().pop()),
        |s| {
            s.jobs
                .get_mut(&(900, 1))
                .unwrap()
                .push(job(900, 1, 9, "extra", &head()))
        },
    ];
    for (i, change) in changes.into_iter().enumerate() {
        let fake = Fake::green_rc();
        fake.edit(change);
        let a = fake.status();
        assert_eq!(a["state"], "testing", "change {i}");
        assert!(
            a["reasons"][0]
                .as_str()
                .unwrap()
                .contains("missing, cancelled, skipped or unknown")
        );
    }
    let run_changes: [fn(&mut State); 3] = [
        |s| first_run(s)["conclusion"] = json!("cancelled"),
        |s| first_run(s)["conclusion"] = json!(null),
        |s| drop(s.jobs.remove(&(900, 1))),
    ];
    for (i, change) in run_changes.into_iter().enumerate() {
        let fake = Fake::green_rc();
        fake.edit(change);
        assert_eq!(fake.status()["state"], "testing", "run change {i}");
    }
}

#[test]
fn only_the_push_run_of_this_tag_and_commit_counts() {
    let changes: [fn(&mut State); 6] = [
        |s| first_run(s)["event"] = json!("workflow_dispatch"),
        |s| first_run(s)["head_branch"] = json!("main"),
        |s| first_run(s)["head_sha"] = json!(sha('b')),
        |s| first_run(s)["workflow_id"] = json!(78),
        |s| first_run(s)["path"] = json!(".github/workflows/other.yml"),
        |s| first_run(s)["head_repository"]["full_name"] = json!("fork/r"),
    ];
    for (i, change) in changes.into_iter().enumerate() {
        let fake = Fake::green_rc();
        fake.edit(change);
        let a = fake.status();
        assert_eq!(a["state"], "testing", "change {i}");
        assert!(
            a["reasons"][0]
                .as_str()
                .unwrap()
                .contains("full tag run is missing")
        );
    }
    let fake = Fake::green_rc();
    fake.edit(|s| first_run(s)["path"] = json!(format!("{WORKFLOW_PATH}@refs/tags/{RC1}")));
    assert_eq!(fake.status()["state"], "ready_final");
}

#[test]
fn the_newest_run_and_its_latest_attempt_decide() {
    let fake = Fake::green_rc();
    fake.run(950, RC1, &head());
    fake.edit(|s| s.jobs.get_mut(&(950, 1)).unwrap()[1]["conclusion"] = json!("failure"));
    fake.edit(|s| s.runs[1]["conclusion"] = json!("failure"));
    assert_eq!(
        fake.status()["state"],
        "repair",
        "an older green run does not pass"
    );

    // Attempt 1 failed, attempt 2 is green: only attempt 2 is read.
    let fake = Fake::green_rc();
    fake.edit(|s| {
        let mut jobs = s.jobs.remove(&(900, 1)).unwrap();
        let mut failed = jobs.clone();
        failed[0]["conclusion"] = json!("failure");
        s.jobs.insert((900, 1), failed);
        jobs.iter_mut().for_each(|j| j["run_attempt"] = json!(2));
        s.jobs.insert((900, 2), jobs);
        first_run(s)["run_attempt"] = json!(2);
    });
    assert_eq!(fake.status()["state"], "ready_final");
}

#[test]
fn a_failed_job_asks_for_a_repair_issue() {
    let fake = Fake::green_rc();
    fake.edit(|s| {
        first_job(s)["conclusion"] = json!("failure");
        first_job(s)["steps"] = json!([
            {"name": "Build", "conclusion": "success"}, {"name": "Test", "conclusion": "failure"},
        ]);
        first_run(s)["conclusion"] = json!("failure");
    });
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].as_str()),
        (Some("repair"), Some("failure_issue"))
    );
    assert_eq!(a["failures"][0]["name"], "unit");
    assert_eq!(a["failures"][0]["steps"], json!(["Test"]));
    assert_eq!(
        a["failures"][0]["url"],
        "https://github.com/o/r/actions/runs/900/job/9000"
    );

    fake.edit(|s| first_job(s)["html_url"] = json!("https://example.com/job/9000"));
    assert!(fake.stop().contains("failure job URL differs"));
}

#[test]
fn a_failed_run_without_a_failed_job_is_a_failure() {
    for conclusion in ["failure", "timed_out", "action_required", "startup_failure"] {
        let fake = Fake::green_rc();
        fake.edit(|s| first_run(s)["conclusion"] = json!("x"));
        fake.edit(|s| s.runs[0]["conclusion"] = json!("failure"));
        fake.edit(move |s| s.runs[0]["conclusion"] = json!(conclusion));
        assert_eq!(fake.status()["state"], "repair", "{conclusion}");
    }
}

#[test]
fn new_work_after_a_green_candidate_needs_a_new_candidate() {
    // The candidate is on an older commit; pull request 7 landed after it.
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    fake.tag(RC1, &old);
    fake.run(900, RC1, &old);
    fake.edit(|s| {
        s.ancestors.insert((sha('b'), head()));
        s.issues
            .push(json!({"number": 6, "state": "closed", "body": ""}));
        s.pulls.push(json!({
            "number": 7, "body": "Fixes #6", "milestone": null,
            "merged_at": "2026-11-02T00:00:00Z", "merge_commit_sha": sha('7'),
        }));
        s.ancestors.insert((sha('7'), head()));
    });
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["tag"].as_str()),
        (Some("ready_rc"), Some(RC2))
    );
    assert_eq!(a["release_head"], head());

    // Without the new work, the older commit still holds all of it.
    fake.edit(|s| {
        s.pulls.pop();
        s.issues.pop();
    });
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["release_head"].as_str()),
        (Some("ready_final"), Some(old.as_str()))
    );
}

#[test]
fn a_changed_contract_after_the_candidate_needs_a_new_candidate() {
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    let mut earlier = contract();
    earlier["milestones"]["8"]["acceptance"][0]["goal"] = json!("It worked");
    fake.file(&old, CONTRACT_PATH, &earlier.to_string());
    fake.tag(RC1, &old);
    fake.run(900, RC1, &old);
    fake.edit(|s| s.ancestors.insert((sha('b'), head())));
    assert_eq!(fake.status()["tag"], RC2);

    fake.file(&old, CONTRACT_PATH, "{\"version\":2}");
    assert!(fake.stop().contains("invalid tagged release configuration"));
}

#[test]
fn the_final_tag_goes_to_publish_close_and_complete() {
    let fake = Fake::green_final();
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].as_str()),
        (Some("ready_publish"), Some("publish"))
    );

    fake.edit(|s| {
        s.release = Some(json!({
            "tag_name": FINAL, "draft": false, "prerelease": false,
            "published_at": "2026-11-03T00:00:00Z",
        }));
    });
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].as_str()),
        (Some("ready_close"), Some("close"))
    );

    fake.edit(|s| s.milestone["state"] = json!("closed"));
    let a = fake.status();
    assert_eq!(
        (a["state"].as_str(), a["action"].is_null()),
        (Some("complete"), true)
    );

    fake.edit(|s| s.release.as_mut().unwrap()["draft"] = json!(true));
    assert!(fake.stop().contains("not a final published release"));
}

#[test]
fn a_milestone_closed_too_early_is_on_hold() {
    let fake = Fake::green_rc();
    fake.edit(|s| s.milestone["state"] = json!("closed"));
    let a = fake.status();
    assert_eq!(a["state"], "hold");
    assert!(
        a["reasons"][0]
            .as_str()
            .unwrap()
            .contains("closed without verified final release")
    );
}

#[test]
fn the_final_tag_must_be_the_green_candidate() {
    let fake = Fake::green_rc();
    fake.commit(&sha('c'));
    fake.edit(|s| s.ancestors.insert((sha('c'), head())));
    fake.tag(FINAL, &sha('c'));
    fake.run(901, FINAL, &sha('c'));
    assert!(
        fake.stop()
            .contains("formal tag is not the latest RC commit")
    );

    let fake = Fake::green_final();
    fake.edit(|s| first_job(s)["conclusion"] = json!("skipped"));
    assert!(fake.stop().contains("formal tag lacks passing RC evidence"));

    let fake = Fake::green_final();
    fake.edit(|s| s.runs[1]["conclusion"] = json!("failure"));
    let a = fake.status();
    assert_eq!(a["state"], "repair");
    assert!(
        a["reasons"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r.as_str().unwrap().contains("Keep the formal tag"))
    );
}

// ---- guards of the reads --------------------------------------------------

#[test]
fn the_files_come_from_the_exact_default_commit() {
    let fake = Fake::new();
    fake.edit(|s| drop(s.files.remove(&(head(), POLICY_PATH.to_string()))));
    assert!(
        fake.stop().contains("not found"),
        "no policy file, no release"
    );

    let fake = Fake::new();
    fake.edit(|s| drop(s.files.remove(&(head(), CONTRACT_PATH.to_string()))));
    assert!(fake.stop().contains("not found"));

    let fake = Fake::new();
    fake.file(&head(), CONTRACT_PATH, "{\"version\":1,\"version\":1}");
    assert!(
        fake.stop().contains("invalid JSON"),
        "a duplicate key is refused"
    );

    let fake = Fake::new();
    fake.edit(|s| s.head = "main".to_string());
    assert!(fake.stop().contains("default branch has no exact commit"));

    let fake = Fake::new();
    fake.answer("", Some(json!({})));
    assert!(fake.stop().contains("no default branch"));
}

#[test]
fn a_blob_must_be_the_bytes_it_names() {
    let path = format!("/contents/{POLICY_PATH}?ref={}", head());
    let good = Fake::new()
        .edit(|s| {
            s.route(
                &format!("/contents/{POLICY_PATH}"),
                &format!("ref={}", head()),
            )
        })
        .unwrap();
    type Change = fn(&mut Value);
    let cases: [(Change, &str); 7] = [
        (
            |v| v["sha"] = json!(sha('d')),
            "source blob identity differs",
        ),
        (
            |v| v["size"] = json!(v["size"].as_u64().unwrap() + 1),
            "source blob identity differs",
        ),
        (|v| v["size"] = json!(0), "invalid source blob"),
        (|v| v["size"] = json!(1_048_577), "invalid source blob"),
        (|v| v["type"] = json!("symlink"), "invalid source blob"),
        (|v| v["encoding"] = json!("none"), "invalid source blob"),
        (|v| v["content"] = json!("***"), "invalid source blob"),
    ];
    for (change, reason) in cases {
        let fake = Fake::new();
        let mut answer = good.clone();
        change(&mut answer);
        fake.answer(&path, Some(answer));
        assert!(fake.stop().contains(reason), "{reason}");
    }
    let fake = Fake::new();
    fake.file(&head(), POLICY_PATH, "");
    assert!(fake.stop().contains("invalid source blob"));
    let fake = Fake::new();
    fake.edit(|s| {
        drop(
            s.files
                .insert((head(), POLICY_PATH.to_string()), vec![0xff, 0xfe]),
        )
    });
    assert!(fake.stop().contains("not UTF-8"));
}

#[test]
fn the_policy_and_the_contract_must_be_the_reviewed_shape() {
    let fake = Fake::new();
    let mut weak = policy();
    weak["workflows"][0]["jobs"]
        .as_object_mut()
        .unwrap()
        .remove("perf");
    fake.file(&head(), POLICY_PATH, &weak.to_string());
    assert!(fake.stop().contains("lacks a required test category"));

    let fake = Fake::new();
    let mut other = policy();
    other["repo"] = json!("o/x");
    fake.file(&head(), POLICY_PATH, &other.to_string());
    assert!(fake.stop().contains("invalid reviewed gates policy"));

    let fake = Fake::new();
    fake.edit(|s| s.milestone["description"] = json!("Another goal"));
    assert!(fake.stop().contains("milestone goal changed"));

    let fake = Fake::new();
    let mut named = contract();
    named["milestones"]["8"]["acceptance"][0]["checks"] = json!(["lint"]);
    fake.file(&head(), CONTRACT_PATH, &named.to_string());
    assert!(fake.stop().contains("real full-plan checks"));

    let fake = Fake::new();
    let mut outside = contract();
    outside["milestones"]["8"]["acceptance"][0]["issues"] = json!([99]);
    fake.file(&head(), CONTRACT_PATH, &outside.to_string());
    assert!(fake.stop().contains("outside this milestone"));
}

#[test]
fn the_milestone_must_exist_and_be_the_one_asked_for() {
    let fake = Fake::new();
    assert!(
        status(fake.services(), 9)
            .unwrap_err()
            .0
            .contains("not found")
    );
    assert!(
        status(fake.services(), 0)
            .unwrap_err()
            .0
            .contains("milestone number required")
    );
    fake.answer("/milestones/9", Some(milestone()));
    assert!(
        status(fake.services(), 9)
            .unwrap_err()
            .0
            .contains("milestone identity differs")
    );

    let fake = Fake::new();
    fake.edit(|s| s.issues.clear());
    assert!(fake.stop().contains("issue inventory is empty"));
}

#[test]
fn the_workflow_must_be_reviewed_and_start_on_the_tag() {
    let fake = Fake::new();
    fake.file(&head(), WORKFLOW_PATH, &format!("{WORKFLOW}# changed\n"));
    assert!(fake.stop().contains("unreviewed release workflow"));

    let fake = Fake::green_rc();
    fake.file(&head(), WORKFLOW_PATH, &format!("{WORKFLOW}# changed\n"));
    assert!(
        fake.stop()
            .contains("unreviewed tagged workflow definition")
    );

    let weak = [
        (
            "on:\n  push:\n    tags: ['v*']\n    paths: ['src/**']\n",
            "unfiltered tag trigger",
        ),
        (
            "on:\n  push:\n    tags: ['v*']\n    tags-ignore: ['*-rc.*']\n",
            "unfiltered tag trigger",
        ),
        (
            "on:\n  push:\n    branches: [main]\n",
            "unfiltered tag trigger",
        ),
        ("on: push\n", "unfiltered tag trigger"),
        ("on: [push]\n", "unfiltered tag trigger"),
        ("on:\n  pull_request: {}\n", "unfiltered tag trigger"),
        ("on:\n  push:\n    tags: v*\n", "unfiltered tag trigger"),
        (
            "on:\n  push:\n    tags: ['v*', 7]\n",
            "unfiltered tag trigger",
        ),
        (
            "on:\n  push:\n    tags: ['v*', '!v0.5.0-rc.*']\n",
            "not accepted by full tests",
        ),
        (
            "on:\n  push:\n    tags: ['v1.*']\n",
            "not accepted by full tests",
        ),
        (
            "on:\n  push:\n    tags: ['v*']\n  push:\n    tags: ['x']\n",
            "not one YAML document",
        ),
        ("- on\n", "not a YAML mapping"),
        ("on: [\n", "not one YAML document"),
    ];
    for (text, reason) in weak {
        let fake = Fake::new();
        let mut reviewed = policy();
        reviewed["workflows"][0]["definition_sha"] = json!(blob_sha(text.as_bytes()));
        fake.file(&head(), POLICY_PATH, &reviewed.to_string());
        fake.file(&head(), WORKFLOW_PATH, text);
        assert!(fake.stop().contains(reason), "{text}");
    }
}

#[test]
fn the_yaml_reader_refuses_keys_it_does_not_know() {
    let read = |text: &str| YamlWorkflows.push_trigger(text).unwrap();
    let trigger = read("true:\n  push:\n    tags: ['v*']\n    branches: [main]\n").unwrap();
    assert_eq!(trigger.tags.as_deref(), Some(&["v*".to_string()][..]));
    assert_eq!(trigger.keys, ["tags", "branches"]);
    let anchored = read("x: &t {tags: ['v*']}\non:\n  push:\n    <<: *t\n").unwrap();
    assert!(
        anchored.keys.iter().all(|key| key != "tags"),
        "a merge key is not a tag trigger"
    );
    let odd = read("on:\n  push:\n    tags: ['v*']\n    7: x\n").unwrap();
    assert!(odd.keys.iter().any(|key| key.starts_with('<')));
    assert_eq!(read("name: x\n"), None);
}

#[test]
fn the_current_policy_must_keep_every_gate_of_the_tag() {
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    fake.tag(RC1, &old);
    fake.run(900, RC1, &old);
    fake.edit(|s| s.ancestors.insert((sha('b'), head())));
    let mut wider = policy();
    wider["workflows"][0]["jobs"]["smoke"] = json!("policy");
    fake.file(&old, POLICY_PATH, &wider.to_string());
    assert!(
        fake.stop()
            .contains("current policy dropped or changed a prior release gate")
    );
}

#[test]
fn a_branch_with_the_name_of_the_tag_stops_the_release() {
    let fake = Fake::green_rc();
    fake.edit(|s| s.branches.insert(RC1.to_string()));
    assert!(fake.stop().contains("tag name is ambiguous with a branch"));
}

#[test]
fn runs_and_jobs_must_be_stable_and_of_this_attempt() {
    let fake = Fake::green_rc();
    fake.edit(|s| {
        let again = s.runs[0].clone();
        s.runs.push(again);
    });
    assert!(fake.stop().contains("unstable test-run inventory"));

    let fake = Fake::green_rc();
    fake.edit(|s| s.runs.push(json!({"id": 0})));
    assert!(fake.stop().contains("unstable test-run inventory"));

    let fake = Fake::green_rc();
    fake.hook("/actions/runs/900", 1, |s| {
        first_run(s)["conclusion"] = json!("failure")
    });
    assert!(fake.stop().contains("test run changed during verification"));

    let fake = Fake::green_rc();
    fake.edit(|s| first_run(s)["run_attempt"] = json!(null));
    assert!(fake.stop().contains("missing test attempt"));

    let stale: [fn(&mut State); 3] = [
        |s| first_job(s)["run_attempt"] = json!(2),
        |s| first_job(s)["run_id"] = json!(901),
        |s| first_job(s)["head_sha"] = json!(sha('b')),
    ];
    for change in stale {
        let fake = Fake::green_rc();
        fake.edit(change);
        assert!(fake.stop().contains("stale or foreign job attempt"));
    }
}

#[test]
fn a_list_is_read_to_its_end_or_not_used() {
    let fake = Fake::new();
    fake.edit(|s| {
        for n in 100..250 {
            s.issues
                .push(json!({"number": n, "state": "closed", "body": ""}));
        }
        s.issues
            .push(json!({"number": 999, "state": "open", "body": ""}));
    });
    assert!(
        fake.status()["reasons"][0]
            .as_str()
            .unwrap()
            .contains("[999]"),
        "the last page is read"
    );

    let fake = Fake::new();
    fake.answer(
        "/pulls?state=all&per_page=100&page=1",
        Some(json!({"message": "x"})),
    );
    assert!(fake.stop().contains("incomplete paginated inventory"));

    let fake = Fake::green_rc();
    fake.answer(
        &format!(
            "/actions/workflows/77/runs?head_sha={}&event=push&per_page=100&page=1",
            head()
        ),
        Some(json!({"total_count": 1})),
    );
    assert!(fake.stop().contains("incomplete paginated inventory"));

    let fake = Fake::new();
    fake.tag(RC1, &head());
    fake.tag(RC1, &sha('b'));
    assert!(fake.stop().contains("duplicate tag inventory"));

    let fake = Fake::new();
    fake.tag("nightly", "main");
    assert!(fake.stop().contains("tag inventory"));
}

#[test]
fn a_moving_default_branch_stops_the_assessment() {
    let fake = Fake::new();
    fake.hook("/commits/main", 2, |s| s.head = sha('e'));
    assert!(
        fake.stop()
            .contains("default branch changed during release assessment")
    );
}

#[test]
fn only_one_repository_and_its_own_paths_are_read() {
    for repo in ["o", "o/r/x", "../r", "o/..", "o/r?x", " o/r"] {
        let fake = Fake::new();
        let fake = Fake {
            repo: repo.to_string(),
            ..fake
        };
        assert!(fake.stop().contains("invalid repository"), "{repo}");
    }
}

// ---- advance --------------------------------------------------------------

fn advanced(fake: &Fake, expected: &Value) -> PortResult<Advanced> {
    advance(fake.services(), 8, expected.as_str().unwrap())
}

#[test]
fn advance_creates_the_candidate_tag_and_reads_it_back() {
    let fake = Fake::new();
    let a = fake.status();
    let Advanced::Done(record) = advanced(&fake, &a["fingerprint"]).unwrap() else {
        panic!("a tag write");
    };
    assert_eq!(
        record,
        json!({
            "action": "tag", "milestone": 8, "head": head(), "release_head": head(),
            "tag": RC1, "verified": true,
        })
    );
    assert_eq!(fake.created(), [(RC1.to_string(), head())]);

    // The same fingerprint cannot write a second time.
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("release inputs changed")
    );
    assert_eq!(fake.created().len(), 1);
}

#[test]
fn advance_creates_the_final_tag_on_the_candidate_commit() {
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    fake.tag(RC1, &old);
    fake.run(900, RC1, &old);
    fake.edit(|s| s.ancestors.insert((sha('b'), head())));
    let a = fake.status();
    assert!(matches!(
        advanced(&fake, &a["fingerprint"]).unwrap(),
        Advanced::Done(_)
    ));
    assert_eq!(
        fake.created(),
        [(FINAL.to_string(), old)],
        "not the default branch head"
    );
}

#[test]
fn advance_needs_the_fingerprint_of_the_current_inputs() {
    let fake = Fake::new();
    let a = fake.status();
    for bad in ["", "abc", &"A".repeat(64), &"g".repeat(64)] {
        let err = advance(fake.services(), 8, bad).unwrap_err().0;
        assert!(err.contains("invalid assessment fingerprint"), "{bad}");
    }
    let err = advance(fake.services(), 8, &"0".repeat(64)).unwrap_err().0;
    assert!(err.contains("release inputs changed; assess again"));

    fake.edit(|s| s.issues[0]["body"] = json!("Changed after the approval"));
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("release inputs changed")
    );
    assert!(fake.created().is_empty());
}

#[test]
fn advance_reads_again_right_before_the_write() {
    let fake = Fake::new();
    let a = fake.status();
    // The first read of `advance` sees the approved inputs; the second sees
    // new open work.
    fake.hook("/milestones/8", 2, |s| {
        s.issues
            .push(json!({"number": 4, "state": "open", "body": ""}));
    });
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("release changed before write")
    );
    assert!(fake.created().is_empty());
}

#[test]
fn advance_never_moves_or_trusts_an_unproven_tag() {
    let fake = Fake::new();
    let a = fake.status();
    fake.edit(|s| s.write_fails = true);
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("already exists")
    );

    let fake = Fake::new();
    let a = fake.status();
    fake.edit(|s| s.write_as = Some(sha('b')));
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("tag write outcome unknown")
    );

    let fake = Fake::new();
    let a = fake.status();
    fake.hook("/git/ref/tags/", 1, |s| s.tags[0].1 = sha('b'));
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("tag readback differs")
    );

    let fake = Fake::new();
    let a = fake.status();
    fake.hook("/git/ref/tags/", 1, |s| s.tags.clear());
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("not found")
    );
}

#[test]
fn advance_does_not_create_a_tag_that_cannot_start_the_full_tests() {
    // The candidate is green on an older commit. The default branch has new
    // work and a workflow that no review covers: no `rc.2`.
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    fake.tag(RC1, &old);
    fake.run(900, RC1, &old);
    fake.edit(|s| {
        s.ancestors.insert((sha('b'), head()));
        s.pulls.push(json!({
            "number": 7, "body": "", "milestone": {"number": 8},
            "merged_at": "2026-11-02T00:00:00Z", "merge_commit_sha": sha('7'),
        }));
        s.ancestors.insert((sha('7'), head()));
    });
    fake.file(
        &head(),
        WORKFLOW_PATH,
        "on:\n  push:\n    tags: ['v*']\n    paths: ['src/**']\n",
    );
    let a = fake.status();
    assert_eq!(a["tag"], RC2);
    assert!(
        advanced(&fake, &a["fingerprint"])
            .unwrap_err()
            .0
            .contains("unreviewed release workflow")
    );
    assert!(fake.created().is_empty());
}

#[test]
fn advance_writes_nothing_for_the_other_actions() {
    let testing = Fake::new();
    testing.tag(RC1, &head());
    let failed = Fake::green_rc();
    failed.edit(|s| first_run(s)["conclusion"] = json!("failure"));
    let work = Fake::new();
    work.edit(|s| {
        s.issues
            .push(json!({"number": 4, "state": "open", "body": ""}))
    });
    for (fake, state) in [
        (testing, "testing"),
        (failed, "repair"),
        (work, "work"),
        (Fake::green_final(), "ready_publish"),
    ] {
        let a = fake.status();
        let Advanced::Nothing(same) = advanced(&fake, &a["fingerprint"]).unwrap() else {
            panic!("{state}: no write");
        };
        assert_eq!((same["state"].as_str(), &same), (Some(state), &a));
        assert!(fake.created().is_empty(), "{state}");
    }
}

// ---- preflight ------------------------------------------------------------

#[test]
fn preflight_proves_the_tag_inside_its_run() {
    let fake = Fake::new();
    fake.tag(RC1, &head());
    fake.run(900, RC1, &head());
    fake.edit(|s| {
        first_run(s)["status"] = json!("in_progress");
        first_run(s)["conclusion"] = json!(null);
    });
    let proof = preflight(fake.services(), RC1, &head(), None).unwrap();
    assert_eq!(
        proof,
        json!({
            "schema": "AW_RELEASE_PREFLIGHT_V1", "status": "success", "mode": "all",
            "repo": REPO, "head": head(), "base": head(), "tag": RC1, "milestone": 8,
            "plan_sha256": digest(&policy()),
            "release_blob": blob_sha(contract().to_string().as_bytes()),
            "policy_blob": blob_sha(policy().to_string().as_bytes()),
            "qualification": "not_evaluated",
        })
    );
    assert_eq!(
        preflight(fake.services(), RC1, &head(), Some(8)).unwrap(),
        proof
    );
    assert!(fake.created().is_empty(), "preflight only reads");

    // The run of the final tag, also after the milestone is closed.
    let fake = Fake::green_final();
    assert_eq!(
        preflight(fake.services(), FINAL, &head(), None).unwrap()["tag"],
        FINAL
    );
    fake.edit(|s| s.milestone["state"] = json!("closed"));
    assert_eq!(
        preflight(fake.services(), FINAL, &head(), None).unwrap()["status"],
        "success"
    );
}

#[test]
fn preflight_refuses_a_wrong_tag_commit_or_state() {
    let fake = Fake::green_rc();
    let stop = |tag: &str, commit: &str, milestone| {
        preflight(fake.services(), tag, commit, milestone)
            .unwrap_err()
            .0
    };
    for tag in [
        "0.5.0",
        "v0.5",
        "v0.5.0-rc.0",
        "v0.5.0-beta.1",
        "refs/tags/v0.5.0",
    ] {
        assert!(
            stop(tag, &head(), None).contains("invalid release tag"),
            "{tag}"
        );
    }
    assert!(stop(RC1, "main", None).contains("exact release commit required"));
    assert!(stop(RC1, &sha('b'), None).contains("tag or frozen goal delivery changed"));
    assert!(stop(RC2, &head(), None).contains("tag or frozen goal delivery changed"));
    assert!(stop("v0.6.0-rc.1", &head(), None).contains("no unique existing milestone"));
    assert!(stop(RC1, &head(), Some(9)).contains("not found"));

    fake.edit(|s| {
        s.issues
            .push(json!({"number": 4, "state": "open", "body": ""}))
    });
    let err = stop(RC1, &head(), None);
    assert!(
        err.contains("milestone work or release inputs are not ready: open issues: [4]"),
        "{err}"
    );

    // An old candidate is not the tag that the milestone waits for.
    let fake = Fake::green_rc();
    fake.tag(RC2, &head());
    let err = preflight(fake.services(), RC1, &head(), None)
        .unwrap_err()
        .0;
    assert!(err.contains("not ready"), "{err}");

    // A candidate that lost later work does not prove the goal.
    let fake = Fake::new();
    let old = sha('b');
    fake.commit(&old);
    fake.tag(RC1, &old);
    fake.edit(|s| {
        s.ancestors.insert((sha('b'), head()));
        s.pulls.push(json!({
            "number": 7, "body": "", "milestone": {"number": 8},
            "merged_at": "2026-11-02T00:00:00Z", "merge_commit_sha": sha('7'),
        }));
        s.ancestors.insert((sha('7'), head()));
    });
    let err = preflight(fake.services(), RC1, &old, None).unwrap_err().0;
    assert!(err.contains("tag or frozen goal delivery changed"), "{err}");
}

#[test]
fn preflight_needs_one_milestone_for_the_version() {
    let fake = Fake::green_rc();
    let mut two = contract();
    two["milestones"]["9"] = two["milestones"]["8"].clone();
    fake.file(&head(), CONTRACT_PATH, &two.to_string());
    let err = preflight(fake.services(), RC1, &head(), None)
        .unwrap_err()
        .0;
    assert!(err.contains("no unique existing milestone"));
    assert_eq!(
        preflight(fake.services(), RC1, &head(), Some(8)).unwrap()["milestone"],
        8
    );
}

// ---- command line -----------------------------------------------------------

#[test]
fn the_commands_exit_with_the_result() {
    let fake = Fake::new();
    let a = fake.status();
    let fingerprint = a["fingerprint"].as_str().unwrap().to_string();
    assert_eq!(
        run_release(fake.services(), &ReleaseCall::Status { milestone: 8 }),
        Exit::Ok
    );
    assert_eq!(
        run_release(fake.services(), &ReleaseCall::Status { milestone: 9 }),
        Exit::Failed
    );
    let write = ReleaseCall::Advance {
        milestone: 8,
        expected: fingerprint,
    };
    assert_eq!(run_release(fake.services(), &write), Exit::Ok);
    assert_eq!(run_release(fake.services(), &write), Exit::Failed);
    let waiting = fake.status()["fingerprint"].as_str().unwrap().to_string();
    let wait = ReleaseCall::Advance {
        milestone: 8,
        expected: waiting,
    };
    assert_eq!(run_release(fake.services(), &wait), Exit::Nothing);
    let proof = ReleaseCall::Preflight {
        tag: RC1.to_string(),
        commit: head(),
        milestone: None,
    };
    assert_eq!(run_release(fake.services(), &proof), Exit::Ok);
    assert_eq!(fake.created().len(), 1);
}
