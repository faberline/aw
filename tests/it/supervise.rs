//! `aw supervise --check`: the check with a fake source, the `gh` reads with a
//! fake API, and the line and exit code through the binary.

use std::collections::HashMap;
use std::process::Command;

use aw::application::supervise::{board_title, check};
use aw::domain::desk::facts::{IssueFact, RepoFacts};
use aw::domain::desk::ports::ReadError;
use aw::domain::supervise::{Marker, SuperviseSource};
use aw::infrastructure::desk::{Api, GhDesk};

struct Source {
    board: Option<String>,
    fail: Option<&'static str>,
}

impl SuperviseSource for Source {
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError> {
        if self.fail == Some("facts") {
            return Err("facts failed".into());
        }
        Ok(RepoFacts {
            repo: repo.into(),
            issues: vec![IssueFact {
                number: 1,
                labels: vec!["stage:spec".into()],
                ..IssueFact::default()
            }],
            ..RepoFacts::default()
        })
    }
    fn running(&self, _: &str) -> Result<Vec<String>, ReadError> {
        if self.fail == Some("running") {
            return Err("running failed".into());
        }
        Ok(Vec::new())
    }
    fn board(&self, board_repo: &str, title: &str) -> Result<Option<String>, ReadError> {
        assert_eq!(board_repo, "o/aw");
        assert_eq!(title, "status(r)");
        if self.fail == Some("board") {
            return Err("board failed".into());
        }
        Ok(self.board.clone())
    }
}

const NOW: i64 = 1_795_000_000;

fn run(source: &Source) -> Result<aw::application::supervise::Check, String> {
    check(source, "o/r", "o/aw", NOW)
}

#[test]
fn the_title_of_the_board_uses_the_name() {
    assert_eq!(board_title("faberline/core"), "status(core)");
    assert_eq!(board_title("core"), "status(core)");
}

#[test]
fn a_matching_fresh_marker_is_a_quick_exit() {
    let none = run(&Source {
        board: None,
        fail: None,
    })
    .unwrap();
    assert!(!none.quick);
    assert_eq!(none.reason.word(), "no-board");
    assert_eq!(none.last_fp, None);
    assert!(
        none.to_string().starts_with("full reason=no-board fp="),
        "{none}"
    );
    assert!(
        none.to_string()
            .contains("last_fp=none full_age_min=none main=none:ok")
    );

    let marker = Marker {
        fp: none.fp.clone(),
        full: "2026-11-18T03:00:00Z".into(),
    };
    // 1_795_000_000 is 2026-11-18T11:06:40Z: the full run is 8 hours old.
    let stale = run(&Source {
        board: Some(marker.render()),
        fail: None,
    })
    .unwrap();
    assert_eq!(stale.reason.word(), "stale");
    let marker = Marker {
        full: "2026-11-18T09:10:40Z".into(),
        ..marker
    };
    let quick = run(&Source {
        board: Some(format!("x\n{}", marker.render())),
        fail: None,
    })
    .unwrap();
    assert!(quick.quick, "{quick}");
    assert_eq!(quick.last_fp.as_deref(), Some(quick.fp.as_str()));
    assert_eq!(quick.full_age_min, Some(116));
    let changed = Marker {
        fp: "0".into(),
        ..marker
    };
    let changed = run(&Source {
        board: Some(changed.render()),
        fail: None,
    })
    .unwrap();
    assert_eq!(changed.reason.word(), "changed");
}

#[test]
fn a_read_error_is_an_error() {
    for part in ["facts", "running", "board"] {
        let err = run(&Source {
            board: None,
            fail: Some(part),
        })
        .unwrap_err();
        assert_eq!(err, format!("{part} failed"));
    }
}

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

#[test]
fn gh_reads_running_runs_and_the_board() {
    let mut m = HashMap::new();
    m.insert(
        "repos/o/r/actions/runs?status=queued".to_string(),
        r#"{"workflow_runs":[{"id":5}]}"#.to_string(),
    );
    m.insert(
        "repos/o/r/actions/runs?status=in_progress".to_string(),
        r#"{"workflow_runs":[{"id":9},{"id":7}]}"#.to_string(),
    );
    m.insert(
        "repos/o/aw/issues?state=open&labels=status".to_string(),
        r#"[{"title":"status(x)","body":"no"},{"title":"status(r)","body":"B"},{"title":"status(s)","body":null}]"#
            .to_string(),
    );
    let desk = GhDesk::with_api(Paths(m));
    assert_eq!(desk.running("o/r").unwrap(), ["5", "9", "7"]);
    assert_eq!(
        desk.board("o/aw", "status(r)").unwrap().as_deref(),
        Some("B")
    );
    assert_eq!(
        desk.board("o/aw", "status(s)").unwrap().as_deref(),
        Some("")
    );
    assert_eq!(desk.board("o/aw", "status(z)").unwrap(), None);
    assert!(desk.running("o/none").is_err());
    assert!(SuperviseSource::facts(&desk, "o/none").is_err());
}

fn cli(extra: &[&str], gh_ok: bool) -> (i32, String, String) {
    let dir = std::env::temp_dir().join(format!(
        "aw-supervise-cli-{}-{}-{gh_ok}",
        std::process::id(),
        extra.len()
    ));
    std::fs::create_dir_all(&dir).unwrap();
    let fake = if gh_ok {
        r#"#!/bin/bash
case "$2" in
  *"/actions/runs?"*) echo '{"workflow_runs":[]}' ;;
  "repos/o/r/issues?state=open"*) echo '[{"number":1,"title":"a","updated_at":"t","body":"","labels":[{"name":"stage:tdd"}]}]' ;;
  "repos/o/aw/issues?state=open&labels=status"*) echo '[]' ;;
  "repos/o/r/pulls?state=open"*) echo '[]' ;;
  "repos/o/r/pulls?state=closed"*) echo '[]' ;;
  "repos/o/r/commits?per_page=1"*) echo '[]' ;;
  *) echo '[]' ;;
esac
"#
    } else {
        "#!/bin/bash\necho nope >&2\nexit 1\n"
    };
    std::fs::write(dir.join("gh"), fake).unwrap();
    Command::new("chmod")
        .arg("+x")
        .arg(dir.join("gh"))
        .status()
        .unwrap();
    let path = format!("{}:{}", dir.display(), std::env::var("PATH").unwrap());
    let out = Command::new(env!("CARGO_BIN_EXE_aw"))
        .args(["supervise", "--check"])
        .args(extra)
        .env("PATH", path)
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    (
        out.status.code().unwrap(),
        String::from_utf8(out.stdout).unwrap(),
        String::from_utf8(out.stderr).unwrap(),
    )
}

#[test]
fn the_binary_prints_one_line_and_exits_by_the_verdict() {
    let (code, line, _) = cli(&["--repo", "o/r", "--board-repo", "o/aw"], true);
    assert_eq!(code, 2, "{line}");
    assert!(line.starts_with("full reason=no-board fp="), "{line}");
    assert_eq!(line.lines().count(), 1);
    assert!(line.contains("spec=0 tdd=1 review=0"), "{line}");
    let (code, line, err) = cli(&["--repo", "o/r", "--board-repo", "o/aw"], false);
    assert_eq!(code, 1, "{line}");
    assert!(err.contains("gh api"), "{err}");
    assert!(line.is_empty());
}
