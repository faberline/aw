use std::cell::{Cell, RefCell};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use aw::application::run::{
    Client, Event, Recorded, RunError, RunRecord, RunResult, RunServices, record, runs,
};
use aw::domain::run::hook::is_worker_prompt;
use aw::domain::run::ports::RunLedger;
use aw::domain::run::record::{rfc3339, shorten};

use crate::support::{FixedClock, NOW};

#[derive(Default)]
struct MemLedger(RefCell<Vec<RunRecord>>);

impl RunLedger for MemLedger {
    fn append(&self, record: &RunRecord) -> RunResult<()> {
        self.0.borrow_mut().push(record.clone());
        Ok(())
    }

    fn read_all(&self) -> RunResult<Vec<RunRecord>> {
        Ok(self.0.borrow().clone())
    }
}

struct Fixture {
    ledger: MemLedger,
    clock: FixedClock,
}

impl Fixture {
    fn new() -> Self {
        Fixture {
            ledger: MemLedger::default(),
            clock: FixedClock(Cell::new(NOW)),
        }
    }

    fn services(&self) -> RunServices<'_> {
        RunServices {
            ledger: &self.ledger,
            clock: &self.clock,
            root: Path::new("/w"),
        }
    }

    fn hook(&self, client: Client, event: Event, payload: &str) -> Recorded {
        record(self.services(), client, event, payload).unwrap()
    }

    fn written(&self, client: Client, event: Event, payload: &str) -> RunRecord {
        match self.hook(client, event, payload) {
            Recorded::Written(r) => *r,
            Recorded::Skipped(why) => panic!("expected a record, skipped: {why}"),
        }
    }
}

const SCHEDULED: &str = "<scheduled-task name=\"aw-worker-core\" file=\"/x/SKILL.md\">\nThis is an automated run.\n\n執行 aw-worker skill。\n</scheduled-task>";

#[test]
fn rfc3339_formats_unix_seconds_in_utc() {
    assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
    assert_eq!(rfc3339(951_782_400), "2000-02-29T00:00:00Z");
    assert_eq!(rfc3339(NOW), "2027-01-15T08:00:00Z");
    assert_eq!(rfc3339(NOW + 3_661), "2027-01-15T09:01:01Z");
}

#[test]
fn a_start_in_a_worker_clone_is_recorded() {
    let f = Fixture::new();
    let r = f.written(
        Client::Codex,
        Event::Start,
        r#"{"session_id":"s1","cwd":"/w/codex/core","prompt":"anything","transcript_path":"/t.jsonl"}"#,
    );
    assert_eq!(r.at, "2027-01-15T08:00:00Z");
    assert_eq!(r.session, "s1");
    assert_eq!(r.clone.as_deref(), Some("codex/core"));
    assert_eq!(r.prompt.as_deref(), Some("anything"));
    assert_eq!(r.transcript.as_deref(), Some("/t.jsonl"));
    assert_eq!(f.ledger.0.borrow().len(), 1);
}

#[test]
fn an_antigravity_payload_uses_its_camel_case_fields() {
    let f = Fixture::new();
    let r = f.written(
        Client::Agy,
        Event::Start,
        r#"{"conversationId":"c1","workspacePaths":["file:///w/agy/core"],"lastUserInput":"執行 aw-worker skill。","transcriptPath":"/t"}"#,
    );
    assert_eq!(r.session, "c1");
    assert_eq!(r.clone.as_deref(), Some("agy/core"));
    assert_eq!(r.prompt.as_deref(), Some("執行 aw-worker skill。"));

    let end = f.written(
        Client::Agy,
        Event::End,
        r#"{"conversationId":"c1","workspacePaths":["file:///w/agy/core"],"terminationReason":"completed","error":{"code":3}}"#,
    );
    assert_eq!(end.reason.as_deref(), Some("completed"));
    assert_eq!(end.error.as_deref(), Some(r#"{"code":3}"#));
    assert_eq!(end.prompt, None);
}

#[test]
fn a_scheduled_worker_prompt_outside_the_clones_is_recorded() {
    let f = Fixture::new();
    let payload = serde_json::json!({
        "session_id": "s2",
        "cwd": "/Users/me/faberlines/aw",
        "prompt": SCHEDULED,
    })
    .to_string();
    let r = f.written(Client::Claude, Event::Start, &payload);
    assert_eq!(r.clone, None);
    assert_eq!(
        r.prompt.as_deref(),
        Some("<scheduled-task name=\"aw-worker-core\" file=\"/x/SKILL.md\">")
    );
}

#[test]
fn an_ordinary_session_is_skipped() {
    let f = Fixture::new();
    let start = f.hook(
        Client::Claude,
        Event::Start,
        r#"{"session_id":"s3","cwd":"/Users/me/faberlines/aw","prompt":"fix the bug"}"#,
    );
    assert!(matches!(start, Recorded::Skipped(_)));
    let end = f.hook(
        Client::Claude,
        Event::End,
        r#"{"session_id":"s3","cwd":"/Users/me/faberlines/aw","stop_hook_active":false}"#,
    );
    assert!(matches!(end, Recorded::Skipped(_)));
    assert!(f.ledger.0.borrow().is_empty());
}

#[test]
fn an_end_outside_the_clones_needs_a_recorded_start() {
    let f = Fixture::new();
    let payload = serde_json::json!({"session_id": "s4", "cwd": "/a", "prompt": SCHEDULED});
    f.written(Client::Claude, Event::Start, &payload.to_string());
    f.clock.0.set(NOW + 600);
    let end = f.written(
        Client::Claude,
        Event::End,
        r#"{"session_id":"s4","cwd":"/a","last_assistant_message":"done"}"#,
    );
    assert_eq!(end.at, "2027-01-15T08:10:00Z");
    assert_eq!(end.last_message.as_deref(), Some("done"));
    // The same session id from another client is a different run.
    let other = f.hook(
        Client::Codex,
        Event::End,
        r#"{"session_id":"s4","cwd":"/a"}"#,
    );
    assert!(matches!(other, Recorded::Skipped(_)));
}

#[test]
fn input_without_a_session_id_is_skipped() {
    let f = Fixture::new();
    let r = f.hook(Client::Codex, Event::Start, r#"{"cwd":"/w/codex/core"}"#);
    assert!(matches!(r, Recorded::Skipped(_)));
}

#[test]
fn input_that_is_not_a_json_object_is_an_error() {
    let f = Fixture::new();
    let err: RunError = record(f.services(), Client::Claude, Event::Start, "nope").unwrap_err();
    assert!(err.to_string().contains("not JSON"));
    assert!(record(f.services(), Client::Claude, Event::Start, "[1]").is_err());
}

#[test]
fn worker_prompts_are_the_automation_prompts_only() {
    assert!(is_worker_prompt(SCHEDULED));
    assert!(is_worker_prompt("執行 aw-worker skill。"));
    assert!(!is_worker_prompt(
        "<scheduled-task name=\"issue-plan-core\">"
    ));
    assert!(!is_worker_prompt(&format!(
        "please review how the aw-worker skill works {}",
        "x".repeat(100)
    )));
}

#[test]
fn shorten_cuts_long_text_on_a_character_boundary() {
    assert_eq!(shorten("  short  ", 10), "short");
    assert_eq!(shorten("一二三四五", 3), "一二三…");
}

#[test]
fn runs_group_records_by_session_and_keep_the_last_ones() {
    let f = Fixture::new();
    let start =
        |id: &str| format!(r#"{{"session_id":"{id}","cwd":"/w/claude/core","prompt":"p {id}"}}"#);
    let end =
        |id: &str| format!(r#"{{"session_id":"{id}","cwd":"/w/claude/core","reason":"exit"}}"#);
    f.written(Client::Claude, Event::Start, &start("a"));
    f.written(Client::Claude, Event::Start, &start("b"));
    f.clock.0.set(NOW + 60);
    f.written(Client::Claude, Event::End, &end("a"));
    f.written(Client::Claude, Event::Start, &start("c"));

    let all = runs(f.services(), 10).unwrap();
    let ids: Vec<_> = all.iter().map(|r| r.session.as_str()).collect();
    assert_eq!(ids, ["a", "b", "c"]);
    assert_eq!(all[0].status, "ended");
    assert_eq!(all[0].started.as_deref(), Some("2027-01-15T08:00:00Z"));
    assert_eq!(all[0].ended.as_deref(), Some("2027-01-15T08:01:00Z"));
    assert_eq!(all[0].prompt.as_deref(), Some("p a"));
    assert_eq!(all[0].reason.as_deref(), Some("exit"));
    assert_eq!(all[1].status, "open");
    assert_eq!(all[1].ended, None);

    let last = runs(f.services(), 2).unwrap();
    let ids: Vec<_> = last.iter().map(|r| r.session.as_str()).collect();
    assert_eq!(ids, ["b", "c"]);
}

/// A fresh workers directory for one binary test.
fn workers_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("aw-it-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn aw(dir: &Path, args: &[&str], stdin: &str) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(args)
        .env("AW_WORKERS_DIR", dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(stdin.as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

#[test]
fn the_hook_command_writes_the_ledger_and_runs_reads_it() {
    let dir = workers_dir("ledger");
    let empty = aw(&dir, &["runs"], "");
    assert_eq!(empty.status.code(), Some(2));

    let clone = dir.join("claude").join("core");
    let payload = serde_json::json!({"session_id": "s1", "cwd": clone, "prompt": "go"});
    let out = aw(&dir, &["hook", "claude", "start"], &payload.to_string());
    assert_eq!(out.status.code(), Some(0));
    assert!(out.stdout.is_empty(), "claude hooks must print nothing");

    let ledger = std::fs::read_to_string(dir.join("runs.jsonl")).unwrap();
    assert_eq!(ledger.lines().count(), 1);
    assert!(ledger.contains(r#""clone":"claude/core""#));

    let listed = aw(&dir, &["runs", "-n", "5"], "");
    assert_eq!(listed.status.code(), Some(0));
    let summaries: serde_json::Value = serde_json::from_slice(&listed.stdout).unwrap();
    assert_eq!(summaries[0]["session"], "s1");
    assert_eq!(summaries[0]["status"], "open");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_hook_command_exits_0_on_bad_input_and_answers_antigravity() {
    let dir = workers_dir("bad");
    let end = aw(&dir, &["hook", "agy", "end"], "not json");
    assert_eq!(end.status.code(), Some(0));
    assert_eq!(
        String::from_utf8_lossy(&end.stdout).trim(),
        r#"{"decision":""}"#
    );
    assert!(String::from_utf8_lossy(&end.stderr).contains("not JSON"));

    let start = aw(&dir, &["hook", "agy", "start"], "{}");
    assert_eq!(start.status.code(), Some(0));
    assert_eq!(String::from_utf8_lossy(&start.stdout).trim(), "{}");

    let codex = aw(&dir, &["hook", "codex", "start"], "");
    assert_eq!(codex.status.code(), Some(0));
    assert!(codex.stdout.is_empty());
    assert!(!dir.join("runs.jsonl").exists());
    let _ = std::fs::remove_dir_all(&dir);
}
