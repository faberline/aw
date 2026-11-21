//! `aw security check`: each rule has a passing and a failing fixture against
//! an in-memory file list; the config is tested the same way.

use std::collections::BTreeMap;

use aw::domain::security::ports::{Config, Exception, SecurityFiles};
use aw::domain::security::rules::{self, Report, Status};
use aw::domain::work::ports::PortResult;
use aw::infrastructure::security::{date_of, parse_config};

const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

#[derive(Default)]
struct Files {
    files: BTreeMap<String, String>,
    config: Config,
}

impl Files {
    fn with(mut self, path: &str, text: &str) -> Self {
        self.files.insert(path.to_string(), text.to_string());
        self
    }
}

impl SecurityFiles for Files {
    fn config(&self) -> PortResult<Config> {
        Ok(self.config.clone())
    }

    fn paths(&self) -> PortResult<Vec<String>> {
        Ok(self.files.keys().cloned().collect())
    }

    fn text(&self, path: &str) -> PortResult<Option<String>> {
        Ok(self.files.get(path).cloned())
    }

    fn repo_name(&self) -> String {
        "lumen".to_string()
    }

    fn today(&self) -> String {
        "2026-11-21".to_string()
    }
}

fn run(files: &Files) -> Report {
    rules::check(files, false).expect("the fixture can be read")
}

fn warns(report: &Report, rule: &str) -> Vec<String> {
    report
        .findings
        .iter()
        .filter(|f| f.rule == rule && f.status == Status::Warn)
        .map(|f| format!("{}: {}", f.path, f.detail))
        .collect()
}

const GOOD_FLOW: &str = "name: ci\non: [push]\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v4\n      - run: cargo deny check advisories\n";

const SCHEDULE: &str = "name: advisories\non:\n  schedule:\n    - cron: '3 3 * * *'\npermissions:\n  contents: read\njobs:\n  a:\n    uses: faberline/aw/.github/workflows/advisories.yml@main\n";

fn rust_repo() -> Files {
    Files::default()
        .with("Cargo.toml", "[package]\nname = \"lumen\"\n")
        .with("deny.toml", "[advisories]\nignore = []\n")
        .with(".github/workflows/ci.yml", GOOD_FLOW)
        .with(".github/workflows/advisories.yml", SCHEDULE)
}

#[test]
fn a_clean_repo_has_no_warning_and_two_manual_rules() {
    let report = run(&rust_repo());
    assert_eq!(report.warnings, 0, "{:?}", report.findings);
    assert!(report.ok);
    let manual: Vec<&str> = report
        .findings
        .iter()
        .filter(|f| f.status == Status::Manual)
        .map(|f| f.rule)
        .collect();
    assert_eq!(manual, vec!["SEC4", "SEC6"]);
}

#[test]
fn sec1_wants_an_advisories_step_in_ci() {
    let files = rust_repo().with(
        ".github/workflows/ci.yml",
        "permissions: {}\njobs:\n  a:\n    steps:\n      - run: cargo test\n      - run: cargo deny check licenses\n",
    );
    let found = warns(&run(&files), "SEC1");
    assert_eq!(found.len(), 1, "{found:?}");
    assert!(found[0].contains("no CI step runs `cargo deny check advisories`"));
    // `cargo deny check` with no subcommand also checks advisories.
    let all = rust_repo().with(
        ".github/workflows/ci.yml",
        "permissions: {}\njobs:\n  a:\n    steps:\n      - run: cargo deny check\n",
    );
    assert!(warns(&run(&all), "SEC1").is_empty());
}

#[test]
fn sec1_a_commented_step_does_not_count() {
    let files = rust_repo().with(
        ".github/workflows/ci.yml",
        "permissions: {}\njobs:\n  a:\n    steps:\n      # - run: cargo deny check advisories\n      - run: cargo test\n",
    );
    assert_eq!(warns(&run(&files), "SEC1").len(), 1);
}

#[test]
fn sec1_wants_a_deny_toml() {
    let mut files = rust_repo();
    files.files.remove("deny.toml");
    let found = warns(&run(&files), "SEC1");
    assert_eq!(found, vec!["deny.toml: no deny.toml"]);
}

#[test]
fn sec1_an_ignore_needs_a_reason_and_a_future_end_date() {
    let deny = r#"[advisories]
ignore = [
  "RUSTSEC-2024-0001",
  { id = "RUSTSEC-2024-0002", reason = "no fix yet" },
  { id = "RUSTSEC-2024-0003", reason = "no fix yet; expires 2026-11-01" },
  { id = "RUSTSEC-2024-0004", reason = "no fix yet; expires 2027-01-31" },
  { id = "RUSTSEC-2024-0005" },
]
"#;
    let found = warns(&run(&rust_repo().with("deny.toml", deny)), "SEC1");
    assert_eq!(found.len(), 4, "{found:?}");
    assert!(found[0].contains("RUSTSEC-2024-0001 has no reason"));
    assert!(found[1].contains("RUSTSEC-2024-0002 has no end date"));
    assert!(found[2].contains("RUSTSEC-2024-0003 ended on 2026-11-01"));
    assert!(found[3].contains("RUSTSEC-2024-0005 has no reason"));
}

#[test]
fn sec1_and_sec2_do_not_apply_to_a_repo_without_cargo() {
    let files = Files::default().with("README.md", "x");
    let report = run(&files);
    assert!(warns(&report, "SEC1").is_empty());
    assert!(warns(&report, "SEC2").is_empty());
}

#[test]
fn sec2_wants_a_scheduled_advisory_run() {
    let mut files = rust_repo();
    files.files.remove(".github/workflows/advisories.yml");
    let found = warns(&run(&files), "SEC2");
    assert_eq!(found.len(), 1);
    // A schedule that runs the command itself counts.
    let own = files.with(
        ".github/workflows/nightly.yml",
        "on:\n  schedule:\n    - cron: '1 1 * * *'\npermissions: {}\njobs:\n  a:\n    steps:\n      - run: cargo deny check advisories\n",
    );
    assert!(warns(&run(&own), "SEC2").is_empty());
    // A schedule of another job does not.
    let other = rust_repo().with(
        ".github/workflows/advisories.yml",
        "on:\n  schedule:\n    - cron: '1 1 * * *'\npermissions: {}\njobs:\n  a:\n    steps:\n      - run: cargo test\n",
    );
    assert_eq!(warns(&run(&other), "SEC2").len(), 1);
}

#[test]
fn sec3_a_from_line_needs_a_digest() {
    let docker = format!(
        "FROM rust:1-bookworm AS build\nFROM --platform=linux/amd64 debian:bookworm-slim@sha256:{h}\nFROM build AS test\nFROM scratch\nFROM lumen-base:ci\nFROM ghcr.io/faberline/lumen-runtime:1\nFROM $BASE\nFROM gcr.io/distroless/static:nonroot\n",
        h = "a".repeat(64)
    );
    let found = warns(&run(&rust_repo().with("Dockerfile", &docker)), "SEC3");
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(found.iter().any(|f| f.contains("rust:1-bookworm")));
    assert!(
        found
            .iter()
            .any(|f| f.contains("gcr.io/distroless/static:nonroot"))
    );
}

#[test]
fn sec3_a_manifest_image_needs_a_digest() {
    let manifest = "spec:\n  containers:\n    - image: \"nats:2.10\"\n      name: a\n    - image: busybox@sha256:abc\n    - image: ghcr.io/faberline/ci-rust:1.99.1\n    - image: REPLACE_ME__IMAGE\n    - image: '{{ .Values.image }}'\n    - image: { type: string }\n    - image: lumen-server:dev # built in the same run\n";
    let found = warns(&run(&rust_repo().with("k8s/a.yaml", manifest)), "SEC3");
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(found.iter().any(|f| f.contains("nats:2.10")));
    assert!(
        found
            .iter()
            .any(|f| f.contains("ghcr.io/faberline/ci-rust:1.99.1"))
    );
}

#[test]
fn sec3_built_images_in_aw_toml_are_allowed() {
    let mut files = rust_repo().with("k8s/a.yaml", "image: quay.io/mine/tool:1\n");
    assert_eq!(warns(&run(&files), "SEC3").len(), 1);
    files.config.built_images = vec!["quay.io/mine/".to_string()];
    assert!(warns(&run(&files), "SEC3").is_empty());
}

#[test]
fn sec3_a_workflow_image_is_not_a_manifest() {
    let files = rust_repo().with(
        ".github/workflows/x.yml",
        &format!("{GOOD_FLOW}    services:\n      a:\n        image: redis:7\n"),
    );
    assert!(warns(&run(&files), "SEC3").is_empty());
}

#[test]
fn sec5_third_party_actions_need_a_sha() {
    let flow = format!(
        "permissions: {{}}\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n      - uses: docker/login-action@{SHA}\n      - uses: ./.github/actions/local\n      - uses: faberline/aw/.github/workflows/x.yml@main\n      - uses: 'docker://alpine:3'\n      - uses: swatinem/rust-cache@main # @{SHA}\n"
    );
    let found = warns(
        &run(&rust_repo().with(".github/workflows/b.yml", &flow)),
        "SEC5",
    );
    assert_eq!(found.len(), 3, "{found:?}");
    assert!(found[0].contains("actions/checkout@v4"));
    assert!(found[1].contains("docker://alpine:3"));
    assert!(found[2].contains("swatinem/rust-cache@main"));
}

#[test]
fn sec5_a_composite_action_is_scanned_for_uses_only() {
    let files = rust_repo().with(
        ".github/actions/x/action.yml",
        "runs:\n  using: composite\n  steps:\n    - uses: actions/cache@v4\n",
    );
    let found = warns(&run(&files), "SEC5");
    assert_eq!(found.len(), 1, "{found:?}");
    assert!(found[0].contains("actions/cache@v4"));
}

#[test]
fn sec5_a_workflow_needs_top_level_permissions() {
    let flow = "name: x\non: [push]\njobs:\n  a:\n    permissions:\n      contents: read\n    steps:\n      - run: echo\n";
    let found = warns(
        &run(&rust_repo().with(".github/workflows/c.yml", flow)),
        "SEC5",
    );
    assert_eq!(found.len(), 1);
    assert!(found[0].contains("c.yml: no top-level `permissions:`"));
}

#[test]
fn sec5_pull_request_target_is_a_warning_but_a_comment_is_not() {
    let bad = "on:\n  pull_request_target:\npermissions: {}\njobs: {}\n";
    let ok = "# pull_request_target is not used here\non: [push]\npermissions: {}\njobs: {}\n";
    let found = warns(
        &run(&rust_repo().with(".github/workflows/d.yml", bad)),
        "SEC5",
    );
    assert_eq!(found.len(), 1);
    assert!(found[0].contains("pull_request_target"));
    assert!(
        warns(
            &run(&rust_repo().with(".github/workflows/d.yml", ok)),
            "SEC5"
        )
        .is_empty()
    );
}

#[test]
fn an_exception_with_a_future_date_marks_the_finding_excepted() {
    let mut files = rust_repo().with(".github/workflows/e.yml", "on: [push]\njobs: {}\n");
    files.config.exceptions = vec![Exception {
        rule: "SEC5".into(),
        path: Some(".github/workflows/e.yml".into()),
        reason: "release workflow, fixed in lumen#9".into(),
        expires: "2026-12-31".into(),
    }];
    let report = run(&files);
    assert_eq!(report.warnings, 0);
    assert_eq!(report.excepted, 1);
}

#[test]
fn an_expired_exception_is_a_warning_and_covers_nothing() {
    let mut files = rust_repo().with(".github/workflows/e.yml", "on: [push]\njobs: {}\n");
    files.config.exceptions = vec![Exception {
        rule: "SEC5".into(),
        path: None,
        reason: "r".into(),
        expires: "2026-11-01".into(),
    }];
    let report = run(&files);
    assert_eq!(report.excepted, 0);
    let found = warns(&report, "SEC5");
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(found.iter().any(|f| f.contains("ended on 2026-11-01")));
}

#[test]
fn strict_makes_a_warning_fail_and_warn_only_does_not() {
    let files = rust_repo().with(".github/workflows/e.yml", "on: [push]\njobs: {}\n");
    assert!(rules::check(&files, false).unwrap().ok);
    let strict = rules::check(&files, true).unwrap();
    assert!(!strict.ok);
    assert!(rules::render(&strict).contains("fail: 1 warnings"));
    assert!(rules::render(&strict).contains("SEC5 warn .github/workflows/e.yml"));
}

#[test]
fn the_report_is_json_with_a_schema() {
    let json = serde_json::to_value(run(&rust_repo())).unwrap();
    assert_eq!(json["schema"], "security-report/1");
    assert_eq!(json["findings"][0]["status"], "manual");
}

#[test]
fn the_config_reads_built_images_and_exceptions() {
    let text = r#"
[standards.security]
built_images = ["quay.io/mine/"]

[[standards.security.exception]]
rule = "SEC3"
path = "k8s/"
reason = "pinned by the release script"
expires = "2026-12-31"
"#;
    let config = parse_config(text).unwrap();
    assert_eq!(config.built_images, vec!["quay.io/mine/"]);
    assert_eq!(config.exceptions.len(), 1);
    assert_eq!(config.exceptions[0].path.as_deref(), Some("k8s/"));
    assert_eq!(parse_config("[hygiene]\n").unwrap(), Config::default());
}

#[test]
fn an_exception_without_a_reason_or_a_date_is_a_usage_error() {
    for body in [
        "rule = \"SEC3\"\nexpires = \"2026-12-31\"",
        "rule = \"SEC3\"\nreason = \"r\"",
        "rule = \"SEC3\"\nreason = \"r\"\nexpires = \"soon\"",
    ] {
        let text = format!("[[standards.security.exception]]\n{body}\n");
        let err = parse_config(&text).unwrap_err();
        assert!(
            err.0
                .contains("needs a `rule`, a `reason` and an `expires`")
        );
    }
    assert!(parse_config("[standards.security]\nbuilt_images = 3\n").is_err());
    assert!(parse_config("[standards.security]\nexception = 3\n").is_err());
    assert!(parse_config("not toml [").is_err());
}

#[test]
fn a_unix_time_is_a_utc_date() {
    assert_eq!(date_of(0), "1970-01-01");
    assert_eq!(date_of(1_795_219_200), "2026-11-21");
    assert_eq!(date_of(1_709_208_001), "2024-02-29");
}
