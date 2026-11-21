//! The desk patrol source: reads one repository through `gh api`. It only
//! reads. An error names the call that failed and never prints a token.

use std::path::Path;

use anyhow::{Context, Result, bail};
use serde_json::Value;

use crate::domain::desk::facts::{CommentFact, IssueFact, PrFact, RepoFacts, RunFact};
use crate::domain::desk::ports::{DeskSource, ReadError};
use crate::domain::work::waits;
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
                newest_comment: None,
            };
            if issue.blocked() {
                issue.newest_comment = self.newest_comment(repo, issue.number)?;
            }
            issues.push(issue);
        }
        Ok(issues)
    }

    fn newest_comment(&self, repo: &str, number: u64) -> Result<Option<CommentFact>, ReadError> {
        let rows = self.rows(&format!(
            "repos/{repo}/issues/{number}/comments?sort=created"
        ))?;
        Ok(rows.last().map(|c| CommentFact {
            body: text(&c["body"]),
            created_at: text(&c["created_at"]),
        }))
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
            });
        }
        Ok(prs)
    }

    /// The newest completed run of each workflow on main.
    fn main_runs(&self, repo: &str) -> Result<Vec<RunFact>, ReadError> {
        let path = format!("repos/{repo}/actions/runs?branch=main&status=completed");
        let value = self.json(&format!("{path}&per_page={PAGE}"))?;
        let mut runs: Vec<RunFact> = Vec::new();
        for r in value["workflow_runs"].as_array().into_iter().flatten() {
            let workflow = text(&r["name"]);
            if runs.iter().any(|x| x.workflow == workflow) {
                continue;
            }
            runs.push(RunFact {
                workflow,
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

    /// The `Waits for:` references of blocked issues that are closed.
    fn closed_refs(&self, repo: &str, issues: &[IssueFact]) -> Result<Vec<String>, ReadError> {
        let org = repo.split('/').next().unwrap_or("");
        let mut closed = Vec::new();
        for issue in issues.iter().filter(|i| i.blocked()) {
            for p in waits::parse(&issue.body).prerequisites {
                let shown = p.show();
                if closed.contains(&shown) {
                    continue;
                }
                let target = match &p.repo {
                    Some(r) if r.contains('/') => r.clone(),
                    Some(r) => format!("{org}/{r}"),
                    None => repo.to_string(),
                };
                let one = self.json(&format!("repos/{target}/issues/{}", p.number))?;
                if one["state"].as_str() == Some("closed") {
                    closed.push(shown);
                }
            }
        }
        Ok(closed)
    }
}

fn text(v: &Value) -> String {
    v.as_str().unwrap_or("").to_string()
}

impl<A: Api> DeskSource for GhDesk<A> {
    fn facts(&self, repo: &str) -> Result<RepoFacts, ReadError> {
        let issues = self.issues(repo)?;
        let closed_refs = self.closed_refs(repo, &issues)?;
        Ok(RepoFacts {
            repo: repo.to_string(),
            prs: self.prs(repo)?,
            main_runs: self.main_runs(repo)?,
            last_merge: self.last_merge(repo)?,
            last_commit: self.last_commit(repo)?,
            closed_refs,
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

/// Read `[desk]` of the `aw.toml` in `dir` (or a parent folder).
pub fn load_config(dir: &Path) -> Result<DeskConfig> {
    let mut at = Some(dir);
    while let Some(d) = at {
        let file = d.join("aw.toml");
        if file.is_file() {
            let raw = std::fs::read_to_string(&file)
                .with_context(|| format!("could not read {}", file.display()))?;
            return parse_config(&raw);
        }
        at = d.parent();
    }
    bail!("no aw.toml found; run `aw desk patrol` inside the aw checkout or give --repo")
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
