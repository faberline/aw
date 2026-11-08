//! In-memory ports that record what the use cases do.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;

use aw::application::work::Services;
use aw::domain::work::claim::ClaimMark;
use aw::domain::work::issue::{Comment, Issue};
use aw::domain::work::ports::{Clock, PortError, PortResult, Repo, Tracker, Workspace};
use aw::domain::work::pull_request::PullRequest;
use aw::domain::work::signal::Signal;

pub const NOW: i64 = 1_800_000_000;

#[derive(Default)]
pub struct FakeTracker {
    pub issues: RefCell<Vec<Issue>>,
    pub comments: RefCell<HashMap<u64, Vec<Comment>>>,
    pub prs: RefCell<Vec<PullRequest>>,
    /// Everything the use case changed, in order, e.g. `merge #5`.
    pub calls: RefCell<Vec<String>>,
    pub fail_request_changes: Cell<bool>,
    /// The merge fails, as a strict ruleset refuses a branch behind base.
    pub fail_merge: Cell<bool>,
    /// What `behind_by` returns.
    pub behind: Cell<u64>,
    next_comment_id: Cell<u64>,
}

impl FakeTracker {
    pub fn issue(&self, number: u64, labels: &[&str]) {
        self.issues.borrow_mut().push(Issue {
            number,
            title: format!("Issue {number}"),
            url: format!("https://github.com/o/r/issues/{number}"),
            labels: labels.iter().map(|l| l.to_string()).collect(),
            created_at: format!("2026-01-{:02}T00:00:00Z", number.min(28)),
            milestone: None,
        });
    }

    pub fn pr(&self, number: u64, head: &str, body: &str) {
        self.prs.borrow_mut().push(PullRequest {
            number,
            head: head.into(),
            body: body.into(),
            url: format!("https://github.com/o/r/pull/{number}"),
        });
    }

    pub fn add_comment(&self, issue: u64, body: &str, at: &str) -> u64 {
        let id = self.next_comment_id.get() + 1;
        self.next_comment_id.set(id);
        self.comments
            .borrow_mut()
            .entry(issue)
            .or_default()
            .push(Comment {
                id,
                body: body.into(),
                created_at: at.into(),
            });
        id
    }

    /// The claim marks on `issue`, oldest first.
    pub fn claims(&self, issue: u64) -> Vec<ClaimMark> {
        self.comments
            .borrow()
            .get(&issue)
            .map(|cs| {
                cs.iter()
                    .filter_map(|c| ClaimMark::parse(&c.body))
                    .collect()
            })
            .unwrap_or_default()
    }

    pub fn clear_calls(&self) {
        self.calls.borrow_mut().clear();
    }

    fn issue_of(&self, id: u64) -> Option<u64> {
        self.comments
            .borrow()
            .iter()
            .find(|(_, cs)| cs.iter().any(|c| c.id == id))
            .map(|(n, _)| *n)
    }

    pub fn signals(&self, issue: u64) -> Vec<Signal> {
        self.comments
            .borrow()
            .get(&issue)
            .map(|cs| cs.iter().filter_map(|c| Signal::parse(&c.body)).collect())
            .unwrap_or_default()
    }

    pub fn last_comment(&self, issue: u64) -> String {
        self.comments.borrow()[&issue].last().unwrap().body.clone()
    }

    pub fn calls(&self) -> Vec<String> {
        self.calls.borrow().clone()
    }

    fn record(&self, call: String) {
        self.calls.borrow_mut().push(call);
    }
}

impl Tracker for FakeTracker {
    fn repo(&self) -> PortResult<Repo> {
        Ok(Repo {
            name: "o/r".into(),
            default_branch: "main".into(),
        })
    }

    fn open_issues(&self, label: &str) -> PortResult<Vec<Issue>> {
        Ok(self
            .issues
            .borrow()
            .iter()
            .filter(|i| i.has_label(label))
            .cloned()
            .collect())
    }

    fn comments(&self, issue: u64) -> PortResult<Vec<Comment>> {
        Ok(self
            .comments
            .borrow()
            .get(&issue)
            .cloned()
            .unwrap_or_default())
    }

    fn comment(&self, issue: u64, body: &str) -> PortResult<()> {
        self.record(format!("comment #{issue}"));
        self.add_comment(issue, body, "2099-01-01T00:00:00Z");
        Ok(())
    }

    fn edit_comment(&self, id: u64, body: &str) -> PortResult<()> {
        let issue = self
            .issue_of(id)
            .ok_or_else(|| PortError(format!("no comment {id}")))?;
        self.record(format!("edit_comment #{issue}"));
        for c in self.comments.borrow_mut().values_mut().flatten() {
            if c.id == id {
                c.body = body.into();
            }
        }
        Ok(())
    }

    fn delete_comment(&self, id: u64) -> PortResult<()> {
        let issue = self
            .issue_of(id)
            .ok_or_else(|| PortError(format!("no comment {id}")))?;
        self.record(format!("delete_comment #{issue}"));
        for cs in self.comments.borrow_mut().values_mut() {
            cs.retain(|c| c.id != id);
        }
        Ok(())
    }

    fn add_labels(&self, issue: u64, labels: &[String]) -> PortResult<()> {
        self.record(format!("add_labels #{issue} {}", labels.join(",")));
        for i in self
            .issues
            .borrow_mut()
            .iter_mut()
            .filter(|i| i.number == issue)
        {
            for l in labels {
                if !i.labels.contains(l) {
                    i.labels.push(l.clone());
                }
            }
        }
        Ok(())
    }

    fn remove_label(&self, issue: u64, label: &str) -> PortResult<()> {
        self.record(format!("remove_label #{issue} {label}"));
        for i in self
            .issues
            .borrow_mut()
            .iter_mut()
            .filter(|i| i.number == issue)
        {
            i.labels.retain(|l| l != label);
        }
        Ok(())
    }

    fn open_prs(&self) -> PortResult<Vec<PullRequest>> {
        Ok(self.prs.borrow().clone())
    }

    fn create_pr(
        &self,
        head: &str,
        base: &str,
        title: &str,
        body: &str,
    ) -> PortResult<PullRequest> {
        self.record(format!("create_pr {head} -> {base}: {title}"));
        let number = 100 + self.prs.borrow().len() as u64;
        self.pr(number, head, body);
        Ok(self.prs.borrow().last().unwrap().clone())
    }

    fn edit_pr_body(&self, pr: u64, body: &str) -> PortResult<()> {
        self.record(format!("edit_pr #{pr}"));
        for p in self.prs.borrow_mut().iter_mut().filter(|p| p.number == pr) {
            p.body = body.into();
        }
        Ok(())
    }

    fn request_changes(&self, pr: u64, _body: &str) -> PortResult<()> {
        if self.fail_request_changes.get() {
            return Err(PortError(
                "cannot request changes on your own pull request".into(),
            ));
        }
        self.record(format!("request_changes #{pr}"));
        Ok(())
    }

    fn pr_comment(&self, pr: u64, _body: &str) -> PortResult<()> {
        self.record(format!("pr_comment #{pr}"));
        Ok(())
    }

    fn squash_merge(&self, pr: u64, subject: &str, body: &str) -> PortResult<()> {
        if self.fail_merge.get() {
            return Err(PortError("required status checks are expected".into()));
        }
        self.record(format!("merge #{pr}: {subject} | {body}"));
        Ok(())
    }

    fn behind_by(&self, base: &str, head: &str) -> PortResult<u64> {
        let _ = (base, head);
        Ok(self.behind.get())
    }

    fn update_branch(&self, pr: u64) -> PortResult<()> {
        self.record(format!("update_branch #{pr}"));
        Ok(())
    }
}

pub struct FakeWorkspace {
    /// `owner/name` of `origin`, or `None` outside a git checkout.
    pub origin: RefCell<Option<String>>,
    pub clean: Cell<bool>,
    pub local: RefCell<Vec<String>>,
    pub remote: RefCell<Vec<String>>,
    pub calls: RefCell<Vec<String>>,
}

impl Default for FakeWorkspace {
    fn default() -> Self {
        FakeWorkspace {
            origin: RefCell::new(Some("o/r".into())),
            clean: Cell::new(true),
            local: RefCell::new(vec!["main".into()]),
            remote: RefCell::new(vec!["main".into()]),
            calls: RefCell::new(Vec::new()),
        }
    }
}

impl FakeWorkspace {
    pub fn calls(&self) -> Vec<String> {
        self.calls.borrow().clone()
    }

    fn record(&self, call: String) {
        self.calls.borrow_mut().push(call);
    }
}

impl Workspace for FakeWorkspace {
    fn origin_repo(&self) -> PortResult<Option<String>> {
        Ok(self.origin.borrow().clone())
    }

    fn is_clean(&self) -> PortResult<bool> {
        self.record("status".into());
        Ok(self.clean.get())
    }

    fn fetch(&self) -> PortResult<()> {
        self.record("fetch".into());
        Ok(())
    }

    fn has_local_branch(&self, branch: &str) -> PortResult<bool> {
        Ok(self.local.borrow().iter().any(|b| b == branch))
    }

    fn has_remote_branch(&self, branch: &str) -> PortResult<bool> {
        Ok(self.remote.borrow().iter().any(|b| b == branch))
    }

    fn switch(&self, branch: &str) -> PortResult<()> {
        self.record(format!("switch {branch}"));
        Ok(())
    }

    fn create_branch(&self, branch: &str, start: &str) -> PortResult<()> {
        self.record(format!("create {branch} from {start}"));
        self.local.borrow_mut().push(branch.into());
        Ok(())
    }

    fn fast_forward(&self, upstream: &str) -> PortResult<()> {
        self.record(format!("ff {upstream}"));
        Ok(())
    }

    fn detach(&self, start: &str) -> PortResult<()> {
        self.record(format!("detach {start}"));
        Ok(())
    }

    fn push(&self, branch: &str) -> PortResult<()> {
        self.record(format!("push {branch}"));
        Ok(())
    }
}

pub struct FixedClock(pub Cell<i64>);

impl Clock for FixedClock {
    fn now(&self) -> i64 {
        self.0.get()
    }
}

pub struct World {
    pub tracker: FakeTracker,
    pub workspace: FakeWorkspace,
    pub clock: FixedClock,
}

impl World {
    pub fn new() -> Self {
        World {
            tracker: FakeTracker::default(),
            workspace: FakeWorkspace::default(),
            clock: FixedClock(Cell::new(NOW)),
        }
    }

    pub fn services(&self) -> Services<'_> {
        Services {
            tracker: &self.tracker,
            workspace: &self.workspace,
            clock: &self.clock,
        }
    }
}
