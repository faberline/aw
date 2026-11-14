//! The rules CR0 to CR8 of `docs/standards/core-reuse.md` as pure functions.

use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

use super::repo::{BuildScript, Dep, Exception, Manifest};

pub const SCHEMA: &str = "core-reuse-report/1";

/// Every rule id and what it requires.
pub const CHECKS: [(&str, &str); 9] = [
    ("CR0", "every exception names a check and a reason"),
    (
        "CR1",
        "core is pinned by git tag, never by path, branch, or rev",
    ),
    ("CR2", "one core tag per repository"),
    ("CR3", "the pinned tag is the current core release"),
    (
        "CR4",
        "a workspace member takes core from [workspace.dependencies]",
    ),
    (
        "CR5",
        "a repository that builds a binary depends on cli-std",
    ),
    ("CR6", "a build.rs that stamps GIT_SHA uses build-stamp"),
    ("CR7", "no direct dependency that core already replaces"),
    ("CR8", "core depends on no other faberline repository"),
];

/// Crates that `core` replaces, and the crate to use instead.
const DENIED: [(&str, &str); 3] = [
    ("prometheus", "metrics-prometheus"),
    ("openraft", "raft-core"),
    ("raft", "raft-core"),
];

/// The current `core` release.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CoreRelease {
    /// The highest `vX.Y.Z` tag.
    pub tag: String,
    /// The package names under `crates/`.
    pub crates: BTreeSet<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Finding {
    pub check: String,
    pub path: String,
    pub message: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Excepted {
    pub check: String,
    pub path: String,
    pub message: String,
    pub reason: String,
}

/// The result for one repository.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct RepoReport {
    pub repo: String,
    pub ok: bool,
    pub findings: Vec<Finding>,
    pub excepted: Vec<Excepted>,
}

/// What the check reads of one repository.
pub struct RepoInput {
    pub name: String,
    /// True when the repository is the `core` checkout itself.
    pub is_core: bool,
    pub manifests: Vec<Manifest>,
    pub build_scripts: Vec<BuildScript>,
    pub exceptions: Vec<Exception>,
}

fn finding(check: &str, path: &str, message: String) -> Finding {
    Finding {
        check: check.into(),
        path: path.into(),
        message,
    }
}

/// Check one repository against `release`, then apply its exceptions.
pub fn check(input: &RepoInput, release: &CoreRelease) -> RepoReport {
    let (valid, mut failing) = exceptions(&input.exceptions);
    let mut excepted = Vec::new();
    for f in findings(input, release) {
        match valid
            .iter()
            .find(|e| e.check == f.check && e.path.as_deref().unwrap_or(&f.path) == f.path)
        {
            Some(e) => excepted.push(Excepted {
                check: f.check,
                path: f.path,
                message: f.message,
                reason: e.reason.clone(),
            }),
            None => failing.push(f),
        }
    }
    RepoReport {
        repo: input.name.clone(),
        ok: failing.is_empty(),
        findings: failing,
        excepted,
    }
}

/// CR0: split the exceptions into usable ones and findings.
fn exceptions(entries: &[Exception]) -> (Vec<&Exception>, Vec<Finding>) {
    let mut valid = Vec::new();
    let mut broken = Vec::new();
    for (i, e) in entries.iter().enumerate() {
        if CHECKS.iter().any(|(id, _)| *id == e.check) && !e.reason.trim().is_empty() {
            valid.push(e);
        } else {
            broken.push(finding(
                "CR0",
                "aw.toml",
                format!("exception {} needs a known check and a reason", i + 1),
            ));
        }
    }
    (valid, broken)
}

fn findings(input: &RepoInput, release: &CoreRelease) -> Vec<Finding> {
    let deps: Vec<&Dep> = input.manifests.iter().flat_map(|m| &m.deps).collect();
    let mut out = Vec::new();
    if input.is_core {
        for d in deps.iter().filter(|d| d.from_faberline() && !d.from_core()) {
            let git = d.git.as_deref().unwrap_or_default();
            out.push(finding(
                "CR8",
                &d.manifest,
                format!("{} comes from {git}", d.name),
            ));
        }
        return out;
    }
    let pinned = pins(&deps, &release.crates, &mut out);
    if pinned.len() > 1 {
        let tags: Vec<&str> = pinned.keys().map(String::as_str).collect();
        out.push(finding(
            "CR2",
            "Cargo.toml",
            format!("pins {}", tags.join(", ")),
        ));
    }
    for (tag, manifests) in pinned.iter().filter(|(t, _)| **t != release.tag) {
        for m in manifests {
            out.push(finding(
                "CR3",
                m,
                format!("pins {tag}; current is {}", release.tag),
            ));
        }
    }
    members(input, &deps, &mut out);
    binaries(input, &deps, release, &mut out);
    stamps(input, &deps, &mut out);
    for d in deps.iter().filter(|d| !d.from_core()) {
        if let Some((_, to)) = DENIED.iter().find(|(n, _)| *n == d.package) {
            out.push(finding(
                "CR7",
                &d.manifest,
                format!("{}; use {to}", d.package),
            ));
        }
    }
    out
}

/// CR1; returns each pinned tag with the manifests that pin it.
fn pins(
    deps: &[&Dep],
    crates: &BTreeSet<String>,
    out: &mut Vec<Finding>,
) -> BTreeMap<String, BTreeSet<String>> {
    let mut pinned: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for d in deps {
        if d.from_core() {
            let bad: Vec<&str> = [(d.branch, "branch"), (d.rev, "rev")]
                .into_iter()
                .filter_map(|(on, name)| on.then_some(name))
                .collect();
            if !bad.is_empty() || d.tag.is_none() {
                let how = if bad.is_empty() {
                    "no tag".into()
                } else {
                    bad.join(", ")
                };
                out.push(finding(
                    "CR1",
                    &d.manifest,
                    format!("{} uses {how}", d.name),
                ));
            }
            if let Some(tag) = &d.tag {
                pinned
                    .entry(tag.clone())
                    .or_default()
                    .insert(d.manifest.clone());
            }
        } else if let Some(path) = d.path.as_deref().filter(|_| crates.contains(&d.package))
            && escapes(&d.manifest, path)
        {
            out.push(finding(
                "CR1",
                &d.manifest,
                format!("{} is a path outside the repository", d.name),
            ));
        }
    }
    pinned
}

/// True when `path`, relative to the folder of `manifest`, leaves the root.
fn escapes(manifest: &str, path: &str) -> bool {
    let mut depth: i64 = 0;
    let folder = manifest.rsplit_once('/').map_or("", |(dir, _)| dir);
    for part in folder.split('/').chain(path.split('/')) {
        match part {
            "" | "." => {}
            ".." => depth -= 1,
            _ => depth += 1,
        }
        if depth < 0 {
            return true;
        }
    }
    false
}

/// CR4: a workspace member names the core git source.
fn members(input: &RepoInput, deps: &[&Dep], out: &mut Vec<Finding>) {
    let Some(workspace) = input
        .manifests
        .iter()
        .find(|m| m.path == "Cargo.toml")
        .and_then(|m| m.workspace.as_ref())
    else {
        return;
    };
    for d in deps.iter().filter(|d| d.from_core()) {
        let Some((folder, _)) = d.manifest.rsplit_once('/') else {
            continue;
        };
        let hit = |globs: &[String]| globs.iter().any(|g| glob(g.trim_end_matches('/'), folder));
        if hit(&workspace.members) && !hit(&workspace.exclude) {
            out.push(finding(
                "CR4",
                &d.manifest,
                format!(
                    "{} names the core git source; use {}.workspace = true",
                    d.name, d.name
                ),
            ));
        }
    }
}

/// `*` matches any text, `?` any one character, as in Python `fnmatch`.
fn glob(pattern: &str, text: &str) -> bool {
    fn go(p: &[char], t: &[char]) -> bool {
        match p.split_first() {
            None => t.is_empty(),
            Some(('*', rest)) => (0..=t.len()).any(|i| go(rest, &t[i..])),
            Some((c, rest)) => t
                .split_first()
                .is_some_and(|(x, tail)| (*c == '?' || c == x) && go(rest, tail)),
        }
    }
    let p: Vec<char> = pattern.chars().collect();
    let t: Vec<char> = text.chars().collect();
    go(&p, &t)
}

/// CR5: a binary without `cli-std`.
fn binaries(input: &RepoInput, deps: &[&Dep], release: &CoreRelease, out: &mut Vec<Finding>) {
    let uses_cli_std = release.crates.contains("cli-std")
        && deps
            .iter()
            .any(|d| (d.from_core() || d.workspace) && d.package == "cli-std");
    let first = input
        .manifests
        .iter()
        .filter(|m| m.package && (m.bin || m.main_rs))
        .map(|m| m.path.as_str())
        .min();
    if let Some(path) = first
        && !uses_cli_std
    {
        out.push(finding(
            "CR5",
            path,
            "builds a binary without cli-std".into(),
        ));
    }
}

/// CR6: a hand-written build stamp.
fn stamps(input: &RepoInput, deps: &[&Dep], out: &mut Vec<Finding>) {
    for script in input.build_scripts.iter().filter(|s| s.stamps_git_sha) {
        let manifest = match script.path.rsplit_once('/') {
            Some((dir, _)) => format!("{dir}/Cargo.toml"),
            None => "Cargo.toml".into(),
        };
        let has = deps.iter().any(|d| {
            d.manifest == manifest
                && d.table.ends_with("build-dependencies")
                && d.package == "build-stamp"
        });
        if !has {
            out.push(finding(
                "CR6",
                &script.path,
                "stamps GIT_SHA by hand; use build-stamp".into(),
            ));
        }
    }
}

/// The report for every repository.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    pub schema: &'static str,
    pub core: CoreSummary,
    pub repos: Vec<RepoReport>,
    pub ok: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct CoreSummary {
    pub path: String,
    pub tag: String,
    pub crates: usize,
}

/// Plain text: one line per repository, then its findings.
pub fn render(report: &Report) -> String {
    let mut lines = vec![format!(
        "core {} ({} crates)",
        report.core.tag, report.core.crates
    )];
    for repo in &report.repos {
        lines.push(format!(
            "{} {}",
            if repo.ok { "PASS" } else { "FAIL" },
            repo.repo
        ));
        for f in &repo.findings {
            lines.push(format!("  {} {}: {}", f.check, f.path, f.message));
        }
        for f in &repo.excepted {
            lines.push(format!("  excepted {} {}: {}", f.check, f.path, f.reason));
        }
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn core_dep(manifest: &str, name: &str, tag: Option<&str>) -> Dep {
        Dep {
            manifest: manifest.into(),
            table: "dependencies".into(),
            name: name.into(),
            package: name.into(),
            git: Some("https://github.com/faberline/core".into()),
            tag: tag.map(Into::into),
            ..Dep::default()
        }
    }

    fn release() -> CoreRelease {
        CoreRelease {
            tag: "v1.2.0".into(),
            crates: ["cli-std", "build-stamp", "raft-core"]
                .map(String::from)
                .into(),
        }
    }

    fn manifest(path: &str, deps: Vec<Dep>) -> Manifest {
        Manifest {
            path: path.into(),
            deps,
            ..Manifest::default()
        }
    }

    fn input(manifests: Vec<Manifest>) -> RepoInput {
        RepoInput {
            name: "r".into(),
            is_core: false,
            manifests,
            build_scripts: vec![],
            exceptions: vec![],
        }
    }

    fn checks(report: &RepoReport) -> Vec<&str> {
        report.findings.iter().map(|f| f.check.as_str()).collect()
    }

    #[test]
    fn a_clean_repository_passes() {
        let m = manifest(
            "Cargo.toml",
            vec![core_dep("Cargo.toml", "cli-std", Some("v1.2.0"))],
        );
        let report = check(&input(vec![m]), &release());
        assert!(report.ok, "{report:?}");
    }

    #[test]
    fn cr1_rejects_a_branch_a_rev_and_no_tag() {
        let mut branch = core_dep("Cargo.toml", "a", None);
        branch.branch = true;
        let mut rev = core_dep("Cargo.toml", "b", None);
        rev.rev = true;
        let none = core_dep("Cargo.toml", "c", None);
        let report = check(
            &input(vec![manifest("Cargo.toml", vec![branch, rev, none])]),
            &release(),
        );
        let messages: Vec<&str> = report.findings.iter().map(|f| f.message.as_str()).collect();
        assert_eq!(messages, ["a uses branch", "b uses rev", "c uses no tag"]);
    }

    #[test]
    fn cr1_rejects_a_core_crate_by_path_outside_the_repository() {
        let mut inside = core_dep("crates/x/Cargo.toml", "raft-core", None);
        inside.git = None;
        inside.path = Some("../y".into());
        let mut outside = inside.clone();
        outside.path = Some("../../../core/crates/raft-core".into());
        outside.name = "raft-core".into();
        let report = check(
            &input(vec![manifest("crates/x/Cargo.toml", vec![inside, outside])]),
            &release(),
        );
        assert_eq!(checks(&report), ["CR1"]);
    }

    #[test]
    fn cr2_and_cr3_report_two_tags_and_an_old_tag() {
        let a = core_dep("Cargo.toml", "cli-std", Some("v1.2.0"));
        let b = core_dep("sub/Cargo.toml", "build-stamp", Some("v1.0.0"));
        let report = check(
            &input(vec![
                manifest("Cargo.toml", vec![a]),
                manifest("sub/Cargo.toml", vec![b]),
            ]),
            &release(),
        );
        assert_eq!(checks(&report), ["CR2", "CR3"]);
        assert_eq!(report.findings[0].message, "pins v1.0.0, v1.2.0");
        assert_eq!(report.findings[1].path, "sub/Cargo.toml");
    }

    #[test]
    fn cr4_rejects_a_member_that_names_the_git_source() {
        let root = Manifest {
            path: "Cargo.toml".into(),
            workspace: Some(crate::domain::standards::repo::Workspace {
                members: vec!["crates/*".into()],
                exclude: vec!["crates/skip".into()],
            }),
            deps: vec![core_dep("Cargo.toml", "cli-std", Some("v1.2.0"))],
            ..Manifest::default()
        };
        let member = manifest(
            "crates/a/Cargo.toml",
            vec![core_dep("crates/a/Cargo.toml", "cli-std", Some("v1.2.0"))],
        );
        let skipped = manifest(
            "crates/skip/Cargo.toml",
            vec![core_dep(
                "crates/skip/Cargo.toml",
                "cli-std",
                Some("v1.2.0"),
            )],
        );
        let report = check(&input(vec![root, member, skipped]), &release());
        assert_eq!(checks(&report), ["CR4"]);
        assert_eq!(report.findings[0].path, "crates/a/Cargo.toml");
        assert!(
            report.findings[0]
                .message
                .contains("cli-std.workspace = true")
        );
    }

    #[test]
    fn a_member_that_uses_workspace_true_passes_cr4() {
        let root = Manifest {
            path: "Cargo.toml".into(),
            workspace: Some(crate::domain::standards::repo::Workspace {
                members: vec!["crates/*".into()],
                exclude: vec![],
            }),
            deps: vec![core_dep("Cargo.toml", "cli-std", Some("v1.2.0"))],
            ..Manifest::default()
        };
        let mut dep = Dep {
            manifest: "crates/a/Cargo.toml".into(),
            table: "dependencies".into(),
            name: "cli-std".into(),
            package: "cli-std".into(),
            workspace: true,
            ..Dep::default()
        };
        dep.git = None;
        let report = check(
            &input(vec![root, manifest("crates/a/Cargo.toml", vec![dep])]),
            &release(),
        );
        assert!(report.ok, "{report:?}");
    }

    #[test]
    fn cr5_needs_cli_std_for_a_binary() {
        let mut bin = manifest("Cargo.toml", vec![]);
        bin.package = true;
        bin.main_rs = true;
        let report = check(&input(vec![bin.clone()]), &release());
        assert_eq!(checks(&report), ["CR5"]);
        bin.deps = vec![core_dep("Cargo.toml", "cli-std", Some("v1.2.0"))];
        assert!(check(&input(vec![bin]), &release()).ok);
    }

    #[test]
    fn cr6_needs_build_stamp_for_a_hand_written_stamp() {
        let mut i = input(vec![manifest("a/Cargo.toml", vec![])]);
        i.build_scripts = vec![
            BuildScript {
                path: "a/build.rs".into(),
                stamps_git_sha: true,
            },
            BuildScript {
                path: "b/build.rs".into(),
                stamps_git_sha: false,
            },
        ];
        assert_eq!(checks(&check(&i, &release())), ["CR6"]);
        let mut dep = core_dep("a/Cargo.toml", "build-stamp", Some("v1.2.0"));
        dep.table = "target.x.build-dependencies".into();
        i.manifests = vec![manifest("a/Cargo.toml", vec![dep])];
        assert!(check(&i, &release()).ok);
        i.build_scripts = vec![BuildScript {
            path: "build.rs".into(),
            stamps_git_sha: true,
        }];
        assert_eq!(checks(&check(&i, &release())), ["CR6"]);
    }

    #[test]
    fn cr7_rejects_a_denied_crate_unless_it_comes_from_core() {
        let direct = Dep {
            manifest: "Cargo.toml".into(),
            table: "dependencies".into(),
            name: "p".into(),
            package: "prometheus".into(),
            ..Dep::default()
        };
        let report = check(
            &input(vec![manifest("Cargo.toml", vec![direct])]),
            &release(),
        );
        assert_eq!(
            report.findings[0].message,
            "prometheus; use metrics-prometheus"
        );
        let from_core = Dep {
            package: "prometheus".into(),
            ..core_dep("Cargo.toml", "prometheus", Some("v1.2.0"))
        };
        assert!(
            check(
                &input(vec![manifest("Cargo.toml", vec![from_core])]),
                &release()
            )
            .ok
        );
    }

    #[test]
    fn cr8_rejects_core_that_depends_on_another_faberline_repository() {
        let mut other = core_dep("Cargo.toml", "x", Some("v1"));
        other.git = Some("git@github.com:faberline/keep.git".into());
        let own = core_dep("Cargo.toml", "y", Some("v1"));
        let mut i = input(vec![manifest("Cargo.toml", vec![other, own])]);
        i.is_core = true;
        let report = check(&i, &release());
        assert_eq!(checks(&report), ["CR8"]);
        assert!(report.findings[0].message.starts_with("x comes from"));
    }

    #[test]
    fn exceptions_waive_a_finding_and_cr0_flags_a_broken_one() {
        let old = core_dep("Cargo.toml", "cli-std", Some("v1.0.0"));
        let mut i = input(vec![manifest("Cargo.toml", vec![old])]);
        i.exceptions = vec![Exception {
            check: "CR3".into(),
            reason: "pinned".into(),
            path: Some("Cargo.toml".into()),
        }];
        let report = check(&i, &release());
        assert!(report.ok);
        assert_eq!(report.excepted.len(), 1);
        assert!(
            render(&Report {
                schema: SCHEMA,
                core: CoreSummary {
                    path: "c".into(),
                    tag: "v1.2.0".into(),
                    crates: 3
                },
                repos: vec![report],
                ok: true,
            })
            .contains("excepted CR3 Cargo.toml: pinned")
        );
        // The path differs: the exception does not apply.
        i.exceptions[0].path = Some("other/Cargo.toml".into());
        assert_eq!(checks(&check(&i, &release())), ["CR3"]);
        // No reason, unknown check: CR0.
        i.exceptions = vec![
            Exception {
                check: "CR3".into(),
                reason: " ".into(),
                path: None,
            },
            Exception {
                check: "CR99".into(),
                reason: "x".into(),
                path: None,
            },
        ];
        let report = check(&i, &release());
        assert_eq!(checks(&report), ["CR0", "CR0", "CR3"]);
        assert!(
            render(&Report {
                schema: SCHEMA,
                core: CoreSummary {
                    path: "c".into(),
                    tag: "v1.2.0".into(),
                    crates: 3
                },
                repos: vec![report],
                ok: false,
            })
            .contains("FAIL r")
        );
    }

    #[test]
    fn glob_matches_like_fnmatch() {
        assert!(glob("crates/*", "crates/a/b"));
        assert!(glob("a?c", "abc"));
        assert!(!glob("a?c", "ac"));
        assert!(!glob("crates/*", "other/a"));
    }

    #[test]
    fn repository_names_come_from_both_url_forms() {
        let dep = |git: &str| Dep {
            git: Some(git.into()),
            ..Dep::default()
        };
        assert!(dep("https://github.com/faberline/core.git/").from_core());
        assert!(dep("git@github.com:faberline/core").from_core());
        assert!(!dep("https://github.com/faberline/core-x").from_core());
        assert!(dep("https://github.com/faberline/core-x").from_faberline());
        assert!(!dep("https://example.com/faberline/core").from_faberline());
        assert!(!Dep::default().from_core());
    }
}
