//! The desk patrol source: reads one repository through `gh api`. It only
//! reads. An error names the call that failed and never prints a token.

use std::path::Path;

use anyhow::{Context, Result};
use serde_json::Value;

use crate::domain::desk::facts::{CommentFact, IssueFact, PrFact, RepoFacts, RunFact};
use crate::domain::desk::ports::{DeskSource, ReadError};
use crate::infrastructure::work::process::run;

const PAGE: usize = 100;
const MAX_PAGES: usize = 10;

/// One GET of the GitHub API. Tests give a fake.
pub trait Api {
    fn get(&self, path: &str) -> Result<String, String>;
}

/// The real API: `gh api <path>`.
pub struct GhApi;

impl Api for GhApi {
    fn get(&self, path: &str) -> Result<String, String> {
        run("gh", &["api", path], None).map_err(|e| format!("gh api {path}: {}", e.0))
    }
}

pub struct GhDesk<A: Api> {
    api: A,
}

impl GhDesk<GhApi> {
    pub fn new() -> Self {
        GhDesk { api: GhApi }
    }
}

impl Default for GhDesk<GhApi> {
    fn default() -> Self {
        Self::new()
    }
}

impl<A: Api> GhDesk<A> {
    pub fn with_api(api: A) -> Self {
        GhDesk { api }
    }

    fn json(&self, path: &str) -> Result<Value, ReadError> {
        let text = self.api.get(path)?;
        serde_json::from_str(&text).map_err(|_| format!("gh api {path}: not JSON"))
    }

    /// Every row of a list endpoint, page by page (`gh api --paginate` is
    /// refused by the cloud proxy). `path` has a query already.
    fn rows(&self, path: &str) -> Result<Vec<Value>, ReadError> {
        let mut all = Vec::new();
        for page in 1..=MAX_PAGES {
            let value = self.json(&format!("{path}&per_page={PAGE}&page={page}"))?;
            let rows = match value {
                Value::Array(rows) => rows,
                Value::Object(mut o) => match o.remove("workflow_runs") {
                    Some(Value::Array(rows)) => rows,
                    _ => Vec::new(),
                },
                _ => Vec::new(),
            };
            let n = rows.len();
            all.extend(rows);
            if n < PAGE {
                break;
            }
        }
        Ok(all)
    }

    fn issues(&self, repo: &str) -> Result<Vec<IssueFact>, ReadError> {
        let mut issues = Vec::new();
        for row in self.rows(&format!("repos/{repo}/issues?state=open"))? {
            if row.get("pull_request").is_some() {
                continue;
            }
            let mut issue = IssueFact {
                number: row["number"].as_u64().unwrap_or(0),
                title: text(&row["title"]),
                updated_at: text(&row["updated_at"]),
                body: text(&row["body"]),
                labels: row["labels"]
                    .as_array()
                    .map(|a| a.iter().map(|l| text(&l["name"])).collect())
                    .unwrap_or_default(),
                comments: Vec::new(),
            };
            if issue.blocked() {
                issue.comments = self.comments(repo, issue.number)?;
            }
            issues.push(issue);
        }
        Ok(issues)
    }

    fn comments(&self, repo: &str, number: u64) -> Result<Vec<CommentFact>, ReadError> {
        let rows = self.rows(&format!(
            "repos/{repo}/issues/{number}/comments?sort=created"
        ))?;
        Ok(rows
            .iter()
            .map(|c| CommentFact {
                body: text(&c["body"]),
                created_at: text(&c["created_at"]),
            })
            .collect())
    }

    fn prs(&self, repo: &str) -> Result<Vec<PrFact>, ReadError> {
        let mut prs = Vec::new();
        for row in self.rows(&format!("repos/{repo}/pulls?state=open"))? {
            let number = row["number"].as_u64().unwrap_or(0);
            let one = self.json(&format!("repos/{repo}/pulls/{number}"))?;
            let sha = text(&row["head"]["sha"]);
            let checks = self.json(&format!(
                "repos/{repo}/commits/{sha}/check-runs?per_page=100"
            ))?;
            let failed = checks["check_runs"].as_array().is_some_and(|runs| {
                runs.iter().any(|r| {
                    matches!(
                        r["conclusion"].as_str(),
                        Some("failure" | "timed_out" | "startup_failure")
                    )
                })
            });
            prs.push(PrFact {
                number,
                title: text(&row["title"]),
                updated_at: text(&row["updated_at"]),
                merge_state: text(&one["mergeable_state"]),
                failed_checks: failed,
                issue: linked_issue(&text(&one["body"])),
            });
        }
        Ok(prs)
    }

    /// The newest completed run of each workflow file on main. The title of a
    /// run can hold the commit message, so the file path is the key.
    fn main_runs(&self, repo: &str) -> Result<Vec<RunFact>, ReadError> {
        let path = format!("repos/{repo}/actions/runs?branch=main&status=completed");
        let value = self.json(&format!("{path}&per_page={PAGE}"))?;
        let mut runs: Vec<RunFact> = Vec::new();
        for r in value["workflow_runs"].as_array().into_iter().flatten() {
            // Only a push or a schedule run says main is red. A run started by
            // hand (`workflow_dispatch`) is a drill or a retry.
            if !matches!(r["event"].as_str(), None | Some("push" | "schedule")) {
                continue;
            }
            let path = text(&r["path"]);
            let workflow = if path.is_empty() {
                text(&r["name"])
            } else {
                path
            };
            if runs.iter().any(|x| x.workflow == workflow) {
                continue;
            }
            runs.push(RunFact {
                workflow,
                event: text(&r["event"]),
                created_at: text(&r["created_at"]),
                id: r["id"].as_u64().unwrap_or(0),
                sha: text(&r["head_sha"]),
                conclusion: text(&r["conclusion"]),
            });
        }
        Ok(runs)
    }

    fn last_merge(&self, repo: &str) -> Result<Option<String>, ReadError> {
        let value = self.json(&format!(
            "repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=30"
        ))?;
        Ok(value
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|p| p["merged_at"].as_str())
            .max()
            .map(str::to_string))
    }

    fn last_commit(&self, repo: &str) -> Result<Option<String>, ReadError> {
        let value = self.json(&format!("repos/{repo}/commits?per_page=1"))?;
        Ok(value[0]["commit"]["committer"]["date"]
            .as_str()
            .map(str::to_string))
    }

    /// The waits of blocked issues that are closed: first those closed as
    /// completed, then those closed as not planned (split) or duplicate.
    fn closed_refs(&self, repo: &str, issues: &[IssueFact]) -> Result<ClosedRefs, ReadError> {
        let org = repo.split('/').next().unwrap_or("");
        let mut out = ClosedRefs::default();
        let mut seen: Vec<String> = Vec::new();
        for issue in issues.iter().filter(|i| i.blocked()) {
            let mut refs = issue.waits();
            refs.extend(issue.other_repo_mentions());
            for p in refs {
                let shown = p.show();
                if seen.contains(&shown) {
                    continue;
                }
                seen.push(shown.clone());
                let target = match &p.repo {
                    Some(r) if r.contains('/') => r.clone(),
                    Some(r) => format!("{org}/{r}"),
                    None => repo.to_string(),
                };
                // A reference that cannot be read is one `unknown` item for
                // its issue, not a failed repository.
                let one = match self.json(&format!("repos/{target}/issues/{}", p.number)) {
                    Ok(one) => one,
                    Err(call) => {
                        out.unreadable.push((shown, call));
                        continue;
                    }
                };
                if one["state"].as_str() != Some("closed") {
                    continue;
                }
                match one["state_reason"].as_str() {
                    Some("not_planned" | "duplicate") => out.split.push(shown),
                    _ => out.done.push(shown),
                }
            }
        }
        Ok(out)
    }
}

/// What the references of blocked issues turned out to be.
#[derive(Default)]
struct ClosedRefs {
    done: Vec<String>,
    split: Vec<String>,
    unreadable: Vec<(String, String)>,
}

/// The issue of the first `Closes #N` / `Fixes #N` / `Refs #N` line of a PR
/// body. A reference with a repository name is another repository.
fn linked_issue(body: &str) -> Option<u64> {
    let pattern = regex::Regex::new(r"(?i)\b(?:closes|fixes|resolves|refs?)\s+#(\d+)")
        .expect("the issue link pattern is valid");
    pattern.captures(body).and_then(|c| c[1].parse().ok())
}

fn text(v: &Value) -> String {
    v.as_str().unwrap_or("").to_string()
}

impl<A: Api> DeskSource for GhDesk<A> {
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError> {
        let issues = self.issues(repo)?;
        let refs = self.closed_refs(repo, &issues)?;
        Ok(RepoFacts {
            repo: repo.to_string(),
            prs: self.prs(repo)?,
            main_runs: self.main_runs(repo)?,
            last_merge: self.last_merge(repo)?,
            last_commit: self.last_commit(repo)?,
            closed_refs: refs.done,
            split_refs: refs.split,
            unreadable_refs: refs.unreadable,
            issues,
        })
    }
}

/// The `[desk]` table of `aw.toml`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeskConfig {
    pub org: String,
    pub repos: Vec<String>,
    pub aw: String,
}

impl DeskConfig {
    /// The repositories of the pipeline, for a run outside the aw checkout.
    pub fn builtin() -> Self {
        DeskConfig {
            org: "faberline".to_string(),
            repos: [
                "sift", "core", "lumen", "som", "defer", "tape", "relay", "keep", "kit",
            ]
            .iter()
            .map(|r| r.to_string())
            .collect(),
            aw: "aw".to_string(),
        }
    }
}

/// Read `[desk]` of the `aw.toml` in `dir` (or a parent folder). `None` when
/// no `aw.toml` there has a `[desk]` table: the caller uses the built-in list.
pub fn load_config(dir: &Path) -> Result<Option<DeskConfig>> {
    let mut at = Some(dir);
    while let Some(d) = at {
        let file = d.join("aw.toml");
        if file.is_file() {
            let raw = std::fs::read_to_string(&file)
                .with_context(|| format!("could not read {}", file.display()))?;
            let table: toml::Table = raw.parse().context("aw.toml is not valid TOML")?;
            if table.contains_key("desk") {
                return parse_config(&raw).map(Some);
            }
        }
        at = d.parent();
    }
    Ok(None)
}

pub fn parse_config(raw: &str) -> Result<DeskConfig> {
    let table: toml::Table = raw.parse().context("aw.toml is not valid TOML")?;
    let desk = table
        .get("desk")
        .and_then(|v| v.as_table())
        .context("aw.toml has no [desk] table")?;
    let org = desk
        .get("org")
        .and_then(|v| v.as_str())
        .context("[desk] needs `org`")?
        .to_string();
    let repos = desk
        .get("repos")
        .and_then(|v| v.as_array())
        .context("[desk] needs `repos`")?
        .iter()
        .filter_map(|v| v.as_str().map(str::to_string))
        .collect();
    let aw = desk
        .get("aw")
        .and_then(|v| v.as_str())
        .unwrap_or("aw")
        .to_string();
    Ok(DeskConfig { org, repos, aw })
}
