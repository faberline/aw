//! `aw desk patrol`: read every repository and list what the desk must look at.

use crate::domain::desk::checks::{self, Settings};
use crate::domain::desk::ports::DeskSource;
use crate::domain::desk::report::Report;

/// Patrol `repos` (full `owner/name`) and the aw repository `aw_repo`.
pub fn patrol(
    source: &(dyn DeskSource + Sync),
    repos: &[String],
    aw_repo: &str,
    settings: &Settings,
) -> Report {
    // aw is one section, the aw one. A list that names aw does not add a second.
    let others: Vec<&String> = repos.iter().filter(|r| r.as_str() != aw_repo).collect();
    // Each repository is read in its own thread: ten repositories take the
    // time of the slowest one.
    let sections: Vec<_> = std::thread::scope(|scope| {
        let handles: Vec<_> = others
            .iter()
            .map(|repo| {
                scope.spawn(move || match source.facts(repo) {
                    Ok(facts) => checks::repo_section(&facts, settings),
                    Err(call) => checks::unknown_section(repo, &call),
                })
            })
            .collect();
        let aw = scope.spawn(|| match source.facts(aw_repo) {
            Ok(facts) => {
                let mut section = checks::repo_section(&facts, settings);
                section
                    .items
                    .retain(|i| matches!(i.check.as_str(), "main-red" | "pr-stuck"));
                section.items.extend(checks::aw_section(&facts).items);
                section
            }
            Err(call) => checks::unknown_section(aw_repo, &call),
        });
        let mut all: Vec<_> = handles
            .into_iter()
            .zip(others.iter())
            .map(|(h, repo)| {
                h.join()
                    .unwrap_or_else(|_| checks::unknown_section(repo, "the read panicked"))
            })
            .collect();
        all.push(
            aw.join()
                .unwrap_or_else(|_| checks::unknown_section(aw_repo, "the read panicked")),
        );
        all
    });
    Report::new(sections)
}

/// The full `owner/name` of each repository to patrol and of aw. A name in
/// `only` replaces the configured list; a name with a slash is kept as is.
pub fn repo_names(
    org: &str,
    configured: &[String],
    aw: &str,
    only: &[String],
) -> (Vec<String>, String) {
    let full = |name: &str| {
        if name.contains('/') {
            name.to_string()
        } else {
            format!("{org}/{name}")
        }
    };
    let list = if only.is_empty() { configured } else { only };
    (list.iter().map(|n| full(n)).collect(), full(aw))
}
