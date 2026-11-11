use std::cell::OnceCell;

use serde::Deserialize;
use serde_json::{Value, json as payload};

use super::process::run;
use crate::domain::work::issue::{Comment, Issue};
use crate::domain::work::ports::{
    CheckRun, IssueDetail, NewIssue, PortError, PortResult, Repo, Tracker,
};
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

#[derive(Deserialize)]
struct DetailJson {
    body: Option<String>,
    labels: Vec<String>,
    milestone: Option<u64>,
}

impl From<DetailJson> for IssueDetail {
    fn from(d: DetailJson) -> Self {
        IssueDetail {
            body: d.body.unwrap_or_default(),
            labels: d.labels,
            milestone: d.milestone,
        }
    }
}

const DETAIL_JQ: &str = "{body, labels: [.labels[].name], milestone: .milestone.number}";

/// The JSON body of the request that makes an issue. GitHub refuses a
/// `null` milestone on create, so the key is absent without one.
fn new_issue_body(new: &NewIssue) -> Value {
    let mut body = payload!({ "title": new.title, "body": new.body, "labels": new.labels });
    if let Some(milestone) = new.milestone {
        body["milestone"] = payload!(milestone);
    }
    body
}

/// The number in the answer to a create request.
fn created_number(out: &str, what: &str) -> PortResult<u64> {
    let created: Value = parse(out, what)?;
    created["number"]
        .as_u64()
        .ok_or_else(|| PortError(format!("{what} returned no number")))
}

/// One issue comment from the REST API.
#[derive(Deserialize)]
struct CommentJson {
    id: u64,
    body: Option<String>,
    created_at: String,
}

/// A comment from the list of the whole repository, with its issue.
#[derive(Debug, Deserialize)]
struct RecentCommentJson {
    id: u64,
    body: Option<String>,
    created_at: String,
    issue: u64,
}

const RECENT_COMMENT_JQ: &str =
    ".[] | {id, body, created_at, issue: (.issue_url | split(\"/\") | last | tonumber)}";

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

const PAGE_SIZE: usize = 100;

/// Read `path` page by page. `fetch(path, jq)` runs one request.
///
/// The jq filter may drop rows, so each request first prints the raw row
/// count of the page (`length`) and then the filtered rows. The loop stops
/// when a page holds fewer than `PAGE_SIZE` rows. A page of exactly
/// `PAGE_SIZE` rows makes one more request.
fn read_pages(
    path: &str,
    jq: &str,
    mut fetch: impl FnMut(&str, &str) -> PortResult<String>,
) -> PortResult<String> {
    let page_jq = format!("length, ({jq})");
    let mut rows = String::new();
    for page in 1.. {
        let page_path = format!("{path}&page={page}");
        let out = fetch(&page_path, &page_jq)?;
        let (count, rest) = out.split_once('\n').unwrap_or((out.as_str(), ""));
        let count: usize = count
            .trim()
            .parse()
            .map_err(|_| PortError(format!("unexpected page output from `gh api {page_path}`")))?;
        rows.push_str(rest);
        if count < PAGE_SIZE {
            break;
        }
    }
    Ok(rows)
}

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
    ///
    /// `gh api --paginate` follows the Link header to `repositories/{id}/...`
    /// URLs, which the cloud proxy refuses. So read page by page on the
    /// `repos/{owner}/{repo}/...` path instead.
    fn get_all(&self, path: &str, jq: &str) -> PortResult<String> {
        read_pages(path, jq, |page_path, page_jq| {
            run("gh", &["api", page_path, "--jq", page_jq], None)
        })
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

    fn comments_since(&self, since: &str) -> PortResult<Vec<(u64, Comment)>> {
        let repo = self.name()?;
        // Oldest first: a comment that arrives between two pages goes to
        // the end and moves no row to another page.
        let path = format!(
            "repos/{repo}/issues/comments?since={}&sort=created&direction=asc&per_page=100",
            encode(since)
        );
        let out = self.get_all(&path, RECENT_COMMENT_JQ)?;
        let comments: Vec<RecentCommentJson> = parse_lines(&out, "recent comments")?;
        Ok(comments
            .into_iter()
            .map(|c| {
                let comment = Comment {
                    id: c.id,
                    body: c.body.unwrap_or_default(),
                    created_at: c.created_at,
                };
                (c.issue, comment)
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

    fn issue_detail(&self, issue: u64) -> PortResult<IssueDetail> {
        let repo = self.name()?;
        let out = self.get(&format!("repos/{repo}/issues/{issue}"), Some(DETAIL_JQ))?;
        let detail: DetailJson = parse(&out, "issue")?;
        Ok(detail.into())
    }

    fn create_issue(&self, new: &NewIssue) -> PortResult<u64> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues");
        let out = self.send("POST", &path, Some(new_issue_body(new)))?;
        created_number(&out, "issue create")
    }

    fn close_issue(&self, issue: u64) -> PortResult<()> {
        let repo = self.name()?;
        let path = format!("repos/{repo}/issues/{issue}");
        let body = payload!({ "state": "closed", "state_reason": "not_planned" });
        self.send("PATCH", &path, Some(body)).map(|_| ())
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
        let number = created_number(&created, "pull request create")?;
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

    fn close_pr(&self, pr: u64) -> PortResult<()> {
        let path = format!("repos/{}/pulls/{pr}", self.name()?);
        let body = payload!({ "state": "closed" });
        self.send("PATCH", &path, Some(body)).map(|_| ())
    }

    fn delete_branch(&self, branch: &str) -> PortResult<()> {
        // The ref keeps its `/`; the git refs path accepts it.
        let path = format!("repos/{}/git/refs/heads/{branch}", self.name()?);
        self.send("DELETE", &path, None).map(|_| ())
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

    fn has_failed_checks(&self, head: &str) -> PortResult<bool> {
        let repo = self.name()?;
        // The ref is a path segment, so its `/` is escaped.
        let path = format!(
            "repos/{repo}/commits/{}/check-runs?per_page=100",
            encode(head)
        );
        // A re-run replaces an older run of the same name.
        let jq = "[.check_runs | group_by(.name)[] | max_by(.id) \
                  | select(.conclusion == \"failure\" or .conclusion == \"timed_out\")] | length";
        let out = self.get(&path, Some(jq))?;
        out.trim()
            .parse::<u64>()
            .map(|n| n > 0)
            .map_err(|_| PortError(format!("check runs returned `{}`", out.trim())))
    }

    fn check_runs(&self, head: &str) -> PortResult<Vec<CheckRun>> {
        let repo = self.name()?;
        let path = format!(
            "repos/{repo}/commits/{}/check-runs?per_page=100",
            encode(head)
        );
        // A re-run replaces an older run of the same name.
        let jq = ".check_runs | group_by(.name)[] | max_by(.id) \
                  | [.name, (.status != \"completed\"), \
                     (.conclusion == \"failure\" or .conclusion == \"timed_out\")] | @tsv";
        let out = self.get(&path, Some(jq))?;
        parse_check_runs(&out)
    }

    fn required_checks(&self, branch: &str) -> PortResult<Vec<String>> {
        let repo = self.name()?;
        // The active rules that apply to the branch, from every ruleset.
        let path = format!("repos/{repo}/rules/branches/{}", encode(branch));
        let jq = ".[] | select(.type == \"required_status_checks\") \
                  | .parameters.required_status_checks[].context";
        let out = self.get(&path, Some(jq))?;
        let mut names: Vec<String> = out
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .map(String::from)
            .collect();
        names.sort();
        names.dedup();
        Ok(names)
    }
}

fn parse_check_runs(out: &str) -> PortResult<Vec<CheckRun>> {
    out.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|line| {
            let mut f = line.split('\t');
            match (f.next(), f.next(), f.next()) {
                (Some(name), Some(running), Some(failed)) => Ok(CheckRun {
                    name: name.into(),
                    running: running == "true",
                    failed: failed == "true",
                }),
                _ => Err(PortError(format!("check run returned `{line}`"))),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn check_run_lines_become_check_runs() {
        let out = "gate\tfalse\tfalse\nlint\ttrue\tfalse\ntest\tfalse\ttrue\n";
        let runs = parse_check_runs(out).unwrap();
        assert_eq!(runs.len(), 3);
        assert!(runs[1].running && !runs[1].failed);
        assert!(runs[2].failed);
        assert!(parse_check_runs("broken").is_err());
    }

    #[test]
    fn encode_keeps_plain_characters_and_escapes_the_rest() {
        assert_eq!(encode("agent:claude"), "agent%3Aclaude");
        assert_eq!(encode("good first issue"), "good%20first%20issue");
        assert_eq!(encode("p0-a_b.c~"), "p0-a_b.c~");
    }

    #[test]
    fn recent_comment_lines_carry_the_issue_number() {
        let out = r#"{"id":5,"body":"hello","created_at":"2026-01-01T00:00:00Z","issue":7}
{"id":6,"body":null,"created_at":"2026-01-02T00:00:00Z","issue":12}
"#;
        let rows: Vec<RecentCommentJson> = parse_lines(out, "recent comments").unwrap();
        let rows: Vec<(u64, u64, Option<&str>)> = rows
            .iter()
            .map(|c| (c.issue, c.id, c.body.as_deref()))
            .collect();
        assert_eq!(rows, [(7, 5, Some("hello")), (12, 6, None)]);
        // The filter reads the number from the issue URL of the comment.
        assert!(RECENT_COMMENT_JQ.contains("issue_url"));
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
    fn an_issue_detail_keeps_the_body_the_labels_and_the_milestone_number() {
        let out = r#"{"body":"Text","labels":["p1","stage:spec"],"milestone":4}"#;
        let detail: IssueDetail = parse::<DetailJson>(out, "issue").unwrap().into();
        assert_eq!(detail.body, "Text");
        assert_eq!(detail.labels, ["p1", "stage:spec"]);
        assert_eq!(detail.milestone, Some(4));
        let bare = r#"{"body":null,"labels":[],"milestone":null}"#;
        let detail: IssueDetail = parse::<DetailJson>(bare, "issue").unwrap().into();
        assert_eq!(detail, IssueDetail::default());
    }

    #[test]
    fn a_new_issue_names_its_milestone_only_when_it_has_one() {
        let mut new = NewIssue {
            title: "T".into(),
            body: "B".into(),
            labels: vec!["stage:spec".into()],
            milestone: Some(4),
        };
        let body = new_issue_body(&new);
        assert_eq!(body["title"], "T");
        assert_eq!(body["body"], "B");
        assert_eq!(body["labels"][0], "stage:spec");
        assert_eq!(body["milestone"], 4);
        new.milestone = None;
        assert!(new_issue_body(&new).get("milestone").is_none());
    }

    #[test]
    fn a_create_answer_gives_the_number() {
        assert_eq!(
            created_number(r#"{"number":12}"#, "issue create").unwrap(),
            12
        );
        let err = created_number(r#"{"message":"no"}"#, "issue create").unwrap_err();
        assert!(
            err.0.contains("issue create returned no number"),
            "{}",
            err.0
        );
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

    fn fake_pages(sizes: Vec<usize>) -> (Vec<String>, String) {
        let mut paths = Vec::new();
        let out = read_pages("repos/o/r/issues?per_page=100", ".[]", |p, jq| {
            assert_eq!(jq, "length, (.[])");
            paths.push(p.to_string());
            let n = sizes.get(paths.len() - 1).copied().unwrap_or(0);
            let mut s = format!("{n}\n");
            for i in 0..n {
                s.push_str(&format!("{{\"p\":{},\"i\":{i}}}\n", paths.len()));
            }
            Ok(s)
        })
        .unwrap();
        (paths, out)
    }

    #[test]
    fn all_pages_are_read_on_repos_paths() {
        let (paths, out) = fake_pages(vec![100, 100, 7]);
        assert_eq!(paths.len(), 3);
        assert_eq!(out.lines().count(), 207);
        assert!(paths.iter().all(|p| p.starts_with("repos/")));
        assert!(paths[2].ends_with("per_page=100&page=3"));
    }

    #[test]
    fn exactly_one_hundred_rows_reads_one_empty_page() {
        let (paths, out) = fake_pages(vec![100]);
        assert_eq!(paths.len(), 2);
        assert_eq!(out.lines().count(), 100);
    }

    #[test]
    fn a_short_first_page_is_the_only_page() {
        let (paths, _) = fake_pages(vec![3]);
        assert_eq!(paths.len(), 1);
    }

    #[test]
    fn rows_dropped_by_the_filter_still_count() {
        // The page has 100 raw rows but the filter kept none.
        let mut calls = 0;
        let out = read_pages("repos/o/r/issues?per_page=100", ".[]", |_, _| {
            calls += 1;
            Ok(if calls == 1 { "100\n" } else { "0\n" }.to_string())
        })
        .unwrap();
        assert_eq!(calls, 2);
        assert_eq!(out, "");
    }

    #[test]
    fn bad_page_output_is_an_error() {
        let err = read_pages(
            "repos/o/r/x?per_page=100",
            ".[]",
            |_, _| Ok("oops\n".into()),
        )
        .unwrap_err();
        assert!(err.0.contains("unexpected page output"));
    }
}
