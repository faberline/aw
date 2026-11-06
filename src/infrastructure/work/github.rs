use std::cell::OnceCell;

use serde::Deserialize;

use super::process::run;
use crate::domain::work::issue::{Comment, Issue};
use crate::domain::work::ports::{PortError, PortResult, Repo, Tracker};
use crate::domain::work::pull_request::PullRequest;

/// GitHub through the `gh` CLI, for the repository of the current checkout.
#[derive(Default)]
pub struct GhCli {
    repo: OnceCell<Repo>,
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

#[derive(Deserialize)]
struct CommentsJson {
    comments: Vec<CommentJson>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommentJson {
    body: String,
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
        let out = self.gh(
            &["repo", "view", "--json", "nameWithOwner,defaultBranchRef"],
            None,
        )?;
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
        let repo = self.name()?;
        let out = self.gh(
            &[
                "issue",
                "view",
                &issue.to_string(),
                "--repo",
                &repo,
                "--json",
                "comments",
            ],
            None,
        )?;
        let parsed: CommentsJson = json(&out, "issue comments")?;
        Ok(parsed
            .comments
            .into_iter()
            .map(|c| Comment {
                body: c.body,
                created_at: c.created_at,
            })
            .collect())
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
