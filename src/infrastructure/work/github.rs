use std::cell::OnceCell;

use serde::Deserialize;

use super::process::run;
use crate::domain::work::issue::{Comment, Issue};
use crate::domain::work::ports::{PortError, PortResult, Repo, Tracker};
use crate::domain::work::pull_request::PullRequest;

/// GitHub through the `gh` CLI, for the named repository or, without a
/// name, the repository of the current checkout.
#[derive(Default)]
pub struct GhCli {
    name: Option<String>,
    repo: OnceCell<Repo>,
}

impl GhCli {
    /// `name` is `owner/name`; `None` asks `gh` for the current checkout.
    pub fn new(name: Option<String>) -> Self {
        GhCli {
            name: name.filter(|n| !n.is_empty()),
            repo: OnceCell::new(),
        }
    }
}

fn json<T: for<'de> Deserialize<'de>>(text: &str, what: &str) -> PortResult<T> {
    serde_json::from_str(text)
        .map_err(|e| PortError(format!("unexpected `gh` output for {what}: {e}")))
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RepoJson {
    name_with_owner: String,
    default_branch_ref: BranchRef,
}

#[derive(Deserialize)]
struct BranchRef {
    name: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct IssueJson {
    number: u64,
    title: String,
    url: String,
    labels: Vec<LabelJson>,
    created_at: String,
}

#[derive(Deserialize)]
struct LabelJson {
    name: String,
}

/// One issue comment from the REST API (snake_case fields).
#[derive(Deserialize)]
struct CommentJson {
    id: u64,
    body: Option<String>,
    created_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PrJson {
    number: u64,
    head_ref_name: String,
    body: String,
    url: String,
}

impl From<PrJson> for PullRequest {
    fn from(p: PrJson) -> Self {
        PullRequest {
            number: p.number,
            head: p.head_ref_name,
            body: p.body,
            url: p.url,
        }
    }
}

const PR_FIELDS: &str = "number,headRefName,body,url";

impl GhCli {
    fn name(&self) -> PortResult<String> {
        Ok(self.repo()?.name)
    }

    fn gh(&self, args: &[&str], stdin: Option<&str>) -> PortResult<String> {
        run("gh", args, stdin)
    }
}

impl Tracker for GhCli {
    fn repo(&self) -> PortResult<Repo> {
        if let Some(repo) = self.repo.get() {
            return Ok(repo.clone());
        }
        let mut args = vec!["repo", "view"];
        if let Some(name) = &self.name {
            args.push(name);
        }
        args.extend(["--json", "nameWithOwner,defaultBranchRef"]);
        let out = self.gh(&args, None)?;
        let parsed: RepoJson = json(&out, "repo view")?;
        let repo = Repo {
            name: parsed.name_with_owner,
            default_branch: parsed.default_branch_ref.name,
        };
        Ok(self.repo.get_or_init(|| repo).clone())
    }

    fn open_issues(&self, label: &str) -> PortResult<Vec<Issue>> {
        let repo = self.name()?;
        let out = self.gh(
            &[
                "issue",
                "list",
                "--repo",
                &repo,
                "--state",
                "open",
                "--label",
                label,
                "--limit",
                "200",
                "--json",
                "number,title,url,labels,createdAt",
            ],
            None,
        )?;
        let issues: Vec<IssueJson> = json(&out, "issue list")?;
        Ok(issues
            .into_iter()
            .map(|i| Issue {
                number: i.number,
                title: i.title,
                url: i.url,
                labels: i.labels.into_iter().map(|l| l.name).collect(),
                created_at: i.created_at,
            })
            .collect())
    }

    fn comments(&self, issue: u64) -> PortResult<Vec<Comment>> {
        // REST, not `gh issue view`: only REST gives the numeric id that
        // edit and delete need. `--jq` prints one comment per line.
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}/comments?per_page=100");
        let out = self.gh(
            &[
                "api",
                "--paginate",
                &path,
                "--jq",
                ".[] | {id, body, created_at}",
            ],
            None,
        )?;
        out.lines()
            .filter(|l| !l.trim().is_empty())
            .map(|line| {
                let c: CommentJson = json(line, "issue comments")?;
                Ok(Comment {
                    id: c.id,
                    body: c.body.unwrap_or_default(),
                    created_at: c.created_at,
                })
            })
            .collect()
    }

    fn edit_comment(&self, id: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/comments/{id}");
        let payload = serde_json::json!({ "body": body }).to_string();
        self.gh(
            &["api", "--method", "PATCH", &path, "--input", "-"],
            Some(&payload),
        )
        .map(|_| ())
    }

    fn delete_comment(&self, id: u64) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/comments/{id}");
        self.gh(&["api", "--method", "DELETE", &path], None)
            .map(|_| ())
    }

    fn comment(&self, issue: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        self.gh(
            &[
                "issue",
                "comment",
                &issue.to_string(),
                "--repo",
                &repo,
                "--body-file",
                "-",
            ],
            Some(body),
        )
        .map(|_| ())
    }

    fn open_prs(&self) -> PortResult<Vec<PullRequest>> {
        let repo = self.name()?;
        let out = self.gh(
            &[
                "pr", "list", "--repo", &repo, "--state", "open", "--limit", "200", "--json",
                PR_FIELDS,
            ],
            None,
        )?;
        let prs: Vec<PrJson> = json(&out, "pr list")?;
        Ok(prs.into_iter().map(PullRequest::from).collect())
    }

    fn create_pr(
        &self,
        head: &str,
        base: &str,
        title: &str,
        body: &str,
    ) -> PortResult<PullRequest> {
        let repo = self.name()?;
        self.gh(
            &[
                "pr",
                "create",
                "--repo",
                &repo,
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body-file",
                "-",
            ],
            Some(body),
        )?;
        let out = self.gh(
            &["pr", "view", head, "--repo", &repo, "--json", PR_FIELDS],
            None,
        )?;
        let pr: PrJson = json(&out, "pr view")?;
        Ok(pr.into())
    }

    fn edit_pr_body(&self, pr: u64, body: &str) -> PortResult<()> {
        // REST, not `gh pr edit`: that command also reads classic project
        // cards, which GitHub has removed, and then fails.
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}");
        let payload = serde_json::json!({ "body": body }).to_string();
        self.gh(
            &["api", "--method", "PATCH", &path, "--input", "-"],
            Some(&payload),
        )
        .map(|_| ())
    }

    fn approve(&self, pr: u64) -> PortResult<()> {
        let repo = self.name()?;
        self.gh(
            &[
                "pr",
                "review",
                &pr.to_string(),
                "--repo",
                &repo,
                "--approve",
            ],
            None,
        )
        .map(|_| ())
    }

    fn request_changes(&self, pr: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        self.gh(
            &[
                "pr",
                "review",
                &pr.to_string(),
                "--repo",
                &repo,
                "--request-changes",
                "--body-file",
                "-",
            ],
            Some(body),
        )
        .map(|_| ())
    }

    fn pr_comment(&self, pr: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        self.gh(
            &[
                "pr",
                "comment",
                &pr.to_string(),
                "--repo",
                &repo,
                "--body-file",
                "-",
            ],
            Some(body),
        )
        .map(|_| ())
    }

    fn squash_merge(&self, pr: u64, subject: &str, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        self.gh(
            &[
                "pr",
                "merge",
                &pr.to_string(),
                "--repo",
                &repo,
                "--squash",
                "--subject",
                subject,
                "--body-file",
                "-",
            ],
            Some(body),
        )
        .map(|_| ())
    }
}
