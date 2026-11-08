use std::cell::OnceCell;

use serde::Deserialize;
use serde_json::{Value, json as payload};

use super::process::run;
use crate::domain::work::issue::{Comment, Issue};
use crate::domain::work::ports::{PortError, PortResult, Repo, Tracker};
use crate::domain::work::pull_request::PullRequest;

/// GitHub through `gh api` (REST only), for the named repository or,
/// without a name, the repository of the current checkout.
///
/// Every call uses the REST API. Claude Code cloud sessions block GitHub
/// GraphQL, and most `gh issue` and `gh pr` commands use GraphQL.
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

fn parse<T: for<'de> Deserialize<'de>>(text: &str, what: &str) -> PortResult<T> {
    serde_json::from_str(text)
        .map_err(|e| PortError(format!("unexpected `gh` output for {what}: {e}")))
}

/// One JSON value per line, as `gh api --paginate --jq '.[] | {...}'` prints.
fn parse_lines<T: for<'de> Deserialize<'de>>(text: &str, what: &str) -> PortResult<Vec<T>> {
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|line| parse(line, what))
        .collect()
}

/// Percent-encode a query value. Label names can hold spaces and other
/// reserved characters.
fn encode(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

#[derive(Deserialize)]
struct RepoJson {
    full_name: String,
    default_branch: String,
}

#[derive(Deserialize)]
struct IssueJson {
    number: u64,
    title: String,
    url: String,
    labels: Vec<String>,
    created_at: String,
    #[serde(default)]
    milestone: Option<String>,
}

impl From<IssueJson> for Issue {
    fn from(i: IssueJson) -> Self {
        Issue {
            number: i.number,
            title: i.title,
            url: i.url,
            labels: i.labels,
            created_at: i.created_at,
            milestone: i.milestone,
        }
    }
}

/// `.[]` keeps pull requests out (the issues endpoint lists both) and
/// flattens the label objects to names.
const ISSUE_JQ: &str = ".[] | select(.pull_request | not) \
    | {number, title, url: .html_url, labels: [.labels[].name], created_at, \
      milestone: .milestone.title}";

/// One issue comment from the REST API.
#[derive(Deserialize)]
struct CommentJson {
    id: u64,
    body: Option<String>,
    created_at: String,
}

#[derive(Debug, Deserialize)]
struct PrJson {
    number: u64,
    head: String,
    body: Option<String>,
    url: String,
}

impl From<PrJson> for PullRequest {
    fn from(p: PrJson) -> Self {
        PullRequest {
            number: p.number,
            head: p.head,
            body: p.body.unwrap_or_default(),
            url: p.url,
        }
    }
}

const PR_JQ: &str = "{number, head: .head.ref, body, url: .html_url}";

impl GhCli {
    fn name(&self) -> PortResult<String> {
        Ok(self.repo()?.name)
    }

    /// `gh api <path>`, optionally with `--jq`.
    fn get(&self, path: &str, jq: Option<&str>) -> PortResult<String> {
        let mut args = vec!["api", path];
        if let Some(jq) = jq {
            args.extend(["--jq", jq]);
        }
        run("gh", &args, None)
    }

    /// Every page of a list endpoint, one `jq` result per line.
    fn get_all(&self, path: &str, jq: &str) -> PortResult<String> {
        run("gh", &["api", "--paginate", path, "--jq", jq], None)
    }

    /// `gh api --method <method> <path>` with a JSON body on stdin.
    fn send(&self, method: &str, path: &str, body: Option<Value>) -> PortResult<String> {
        match body {
            Some(body) => run(
                "gh",
                &["api", "--method", method, path, "--input", "-"],
                Some(&body.to_string()),
            ),
            None => run("gh", &["api", "--method", method, path], None),
        }
    }
}

impl Tracker for GhCli {
    fn repo(&self) -> PortResult<Repo> {
        if let Some(repo) = self.repo.get() {
            return Ok(repo.clone());
        }
        // `{owner}/{repo}` makes `gh` fill in the current checkout.
        let path = match &self.name {
            Some(name) => format!("repos/{name}"),
            None => "repos/{owner}/{repo}".to_string(),
        };
        let out = self.get(&path, Some("{full_name, default_branch}"))?;
        let parsed: RepoJson = parse(&out, "repository")?;
        let repo = Repo {
            name: parsed.full_name,
            default_branch: parsed.default_branch,
        };
        Ok(self.repo.get_or_init(|| repo).clone())
    }

    fn open_issues(&self, label: &str) -> PortResult<Vec<Issue>> {
        let repo = self.name()?;
        let path = format!(
            "repos/{repo}/issues?state=open&labels={}&per_page=100",
            encode(label)
        );
        let out = self.get_all(&path, ISSUE_JQ)?;
        let issues: Vec<IssueJson> = parse_lines(&out, "issue list")?;
        Ok(issues.into_iter().map(Issue::from).collect())
    }

    fn comments(&self, issue: u64) -> PortResult<Vec<Comment>> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}/comments?per_page=100");
        let out = self.get_all(&path, ".[] | {id, body, created_at}")?;
        let comments: Vec<CommentJson> = parse_lines(&out, "issue comments")?;
        Ok(comments
            .into_iter()
            .map(|c| Comment {
                id: c.id,
                body: c.body.unwrap_or_default(),
                created_at: c.created_at,
            })
            .collect())
    }

    fn edit_comment(&self, id: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/comments/{id}");
        self.send("PATCH", &path, Some(payload!({ "body": body })))
            .map(|_| ())
    }

    fn delete_comment(&self, id: u64) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/comments/{id}");
        self.send("DELETE", &path, None).map(|_| ())
    }

    fn comment(&self, issue: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}/comments");
        self.send("POST", &path, Some(payload!({ "body": body })))
            .map(|_| ())
    }

    fn add_labels(&self, issue: u64, labels: &[String]) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}/labels");
        self.send("POST", &path, Some(payload!({ "labels": labels })))
            .map(|_| ())
    }

    fn remove_label(&self, issue: u64, label: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}/labels/{}", encode(label));
        self.send("DELETE", &path, None).map(|_| ())
    }

    fn open_prs(&self) -> PortResult<Vec<PullRequest>> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls?state=open&per_page=100");
        let out = self.get_all(&path, &format!(".[] | {PR_JQ}"))?;
        let prs: Vec<PrJson> = parse_lines(&out, "pull request list")?;
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
        let path = format!("repos/{repo}/pulls");
        let created = self.send(
            "POST",
            &path,
            Some(payload!({ "head": head, "base": base, "title": title, "body": body })),
        )?;
        let created: Value = parse(&created, "pull request create")?;
        let number = created["number"]
            .as_u64()
            .ok_or_else(|| PortError("pull request create returned no number".into()))?;
        let out = self.get(&format!("repos/{repo}/pulls/{number}"), Some(PR_JQ))?;
        let pr: PrJson = parse(&out, "pull request")?;
        Ok(pr.into())
    }

    fn edit_pr_body(&self, pr: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}");
        self.send("PATCH", &path, Some(payload!({ "body": body })))
            .map(|_| ())
    }

    fn approve(&self, pr: u64) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}/reviews");
        self.send("POST", &path, Some(payload!({ "event": "APPROVE" })))
            .map(|_| ())
    }

    fn request_changes(&self, pr: u64, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}/reviews");
        self.send(
            "POST",
            &path,
            Some(payload!({ "event": "REQUEST_CHANGES", "body": body })),
        )
        .map(|_| ())
    }

    fn pr_comment(&self, pr: u64, body: &str) -> PortResult<()> {
        // A pull request comment is an issue comment on the same number.
        self.comment(pr, body)
    }

    fn squash_merge(&self, pr: u64, subject: &str, body: &str) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}/merge");
        self.send(
            "PUT",
            &path,
            Some(payload!({
                "merge_method": "squash",
                "commit_title": subject,
                "commit_message": body,
            })),
        )
        .map(|_| ())
    }

    fn behind_by(&self, base: &str, head: &str) -> PortResult<u64> {
        let repo = self.name()?;
        // Branch names keep their `/`; the compare path accepts it.
        let path = format!("repos/{repo}/compare/{base}...{head}");
        let out = self.get(&path, Some(".behind_by"))?;
        out.trim()
            .parse()
            .map_err(|_| PortError(format!("compare returned `{}`", out.trim())))
    }

    fn update_branch(&self, pr: u64) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/pulls/{pr}/update-branch");
        self.send("PUT", &path, None).map(|_| ())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_keeps_plain_characters_and_escapes_the_rest() {
        assert_eq!(encode("agent:claude"), "agent%3Aclaude");
        assert_eq!(encode("good first issue"), "good%20first%20issue");
        assert_eq!(encode("p0-a_b.c~"), "p0-a_b.c~");
    }

    #[test]
    fn issue_lines_become_issues() {
        let out = r#"{"number":7,"title":"T","url":"https://github.com/o/r/issues/7","labels":["agent:agy","stage:tdd"],"created_at":"2026-01-01T00:00:00Z"}

{"number":8,"title":"U","url":"u","labels":[],"created_at":"2026-01-02T00:00:00Z"}
"#;
        let issues: Vec<IssueJson> = parse_lines(out, "issue list").unwrap();
        let issues: Vec<Issue> = issues.into_iter().map(Issue::from).collect();
        assert_eq!(issues.len(), 2);
        assert_eq!(issues[0].number, 7);
        assert_eq!(issues[0].labels, ["agent:agy", "stage:tdd"]);
        assert_eq!(issues[0].milestone, None);
    }

    #[test]
    fn a_pull_request_without_a_body_has_an_empty_body() {
        let out = r#"{"number":3,"head":"agy/issue-7","body":null,"url":"https://github.com/o/r/pull/3"}"#;
        let prs: Vec<PrJson> = parse_lines(out, "pull request list").unwrap();
        let pr = PullRequest::from(prs.into_iter().next().unwrap());
        assert_eq!(pr.head, "agy/issue-7");
        assert_eq!(pr.body, "");
    }

    #[test]
    fn bad_output_names_the_call() {
        let err = parse_lines::<PrJson>("not json", "pull request list").unwrap_err();
        assert!(err.0.contains("pull request list"));
    }
}
