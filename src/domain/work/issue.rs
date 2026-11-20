use super::claim::ClaimMark;
use super::pull_request::is_rework_branch;
use super::signal::{Outcome, Signal};
use super::stage::Stage;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Issue {
    pub number: u64,
    pub title: String,
    pub url: String,
    pub labels: Vec<String>,
    /// RFC 3339 UTC time, as GitHub returns it. It sorts as text.
    pub created_at: String,
    /// The title of the issue's milestone, such as `core@0.5.0`.
    pub milestone: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Comment {
    /// GitHub's numeric comment id, used to edit or delete it.
    pub id: u64,
    pub body: String,
    pub created_at: String,
}

pub fn agent_label(agent: &str) -> String {
    format!("agent:{agent}")
}

/// Agent ids are short lowercase names such as `claude`.
pub fn valid_agent(agent: &str) -> bool {
    !agent.is_empty()
        && agent
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

impl Issue {
    pub fn has_label(&self, name: &str) -> bool {
        self.labels.iter().any(|l| l == name)
    }

    /// 0 for `p0` … 3 for `p3`, 4 when there is no priority label.
    pub fn priority(&self) -> u8 {
        ["p0", "p1", "p2", "p3"]
            .iter()
            .position(|p| self.has_label(p))
            .map_or(4, |i| i as u8)
    }

    /// The first `needs:<thing>` label whose thing the worker does not
    /// have, such as `mac` for an issue that only a Mac can run. A worker
    /// passes over such an issue; a worker that has the thing takes it.
    pub fn missing_need(&self, has: &[String]) -> Option<&str> {
        self.labels
            .iter()
            .filter_map(|l| l.strip_prefix("needs:"))
            .find(|need| !has.iter().any(|h| h == need))
    }

    /// The stage `agent` may work on, judged from labels only.
    pub fn actionable_stage(&self, agent: &str) -> Option<Stage> {
        if !self.has_label(&agent_label(agent)) || self.has_label("blocked") {
            return None;
        }
        Stage::from_labels(&self.labels)
    }
}

/// The version in a milestone title: `core@0.5.0`, `v1.2.3` or `1.2`.
/// Missing parts are 0. A title without a version gives `None`.
pub fn milestone_version(title: &str) -> Option<(u64, u64, u64)> {
    let tail = title.rsplit('@').next()?.trim();
    let tail = tail.strip_prefix('v').unwrap_or(tail);
    let mut parts = tail.split('.');
    let mut next = |required: bool| match parts.next() {
        Some(p) => p.parse::<u64>().ok(),
        None if required => None,
        None => Some(0),
    };
    let version = (next(true)?, next(false)?, next(false)?);
    parts.next().is_none().then_some(version)
}

impl Issue {
    /// Issues of the lowest milestone version come first, so a milestone
    /// is cleared before the next one starts. Then an issue in a milestone
    /// without a version, then an issue without a milestone.
    fn milestone_rank(&self) -> (u8, (u64, u64, u64)) {
        match self.milestone.as_deref() {
            Some(t) => milestone_version(t).map_or((1, (0, 0, 0)), |v| (0, v)),
            None => (2, (0, 0, 0)),
        }
    }
}

/// The label of an issue that is inside the internal-use bar of its
/// repository. A person or the service desk sets it.
pub const BAR_LABEL: &str = "bar";

/// The label of an issue that repairs a red required check on the default
/// branch. While that check is red no pull request of the repository can
/// merge, so the repair must not wait behind `bar` issues that cannot pass
/// until it merges. A person or the supervisor sets it.
pub const CI_FIX_LABEL: &str = "ci-fix";

/// The label of a new issue that backpressure must not hold back: a fix of
/// a red default branch, or the fix that a release waits for. With it,
/// `aw claim --rework-only` takes the issue in spec and tdd. It stops
/// nothing else: the stages still obey their limits for all other new
/// work. The supervisor sets it on a red-main fix; for a release blocker
/// it proposes the label in a decision (faberline/aw#538).
pub const EXPEDITE_LABEL: &str = "expedite";

/// Claim order: `p0` first, then the issues with the `expedite` label, then
/// the issues with the `ci-fix` label, then
/// the issues with the `bar` label, then `p1` (with or without a
/// milestone), then the lowest milestone version (compared as semver), then the
/// priority label (unlabeled last), then the issues that came back from
/// review, then the oldest issue.
pub fn claim_order(issues: &mut [Issue]) {
    claim_order_returned(issues, &[]);
}

/// `claim_order`, where the issues in `returned` (an issue with an open
/// pull request that review sent back to tdd) come before the other
/// issues of the same priority, whatever their milestone. A returned issue
/// never passes an issue of a higher priority, so the order of new work
/// does not change. Each entry of `returned` is the issue number and the
/// time of its newest review `failed` signal (`""` when there is none).
/// Among returned issues the one that has waited longest comes first, so
/// an old pull request does not starve behind returned issues of a lower
/// milestone that come back again and again (lumen#12 and #17, aw#536).
pub fn claim_order_returned(issues: &mut [Issue], returned: &[(u64, String)]) {
    issues.sort_by_cached_key(|i| {
        let back = returned.iter().find(|(n, _)| *n == i.number);
        (
            i.priority() != 0,
            !i.has_label(EXPEDITE_LABEL),
            !i.has_label(CI_FIX_LABEL),
            !i.has_label(BAR_LABEL),
            i.priority() > 1,
            // A returned issue goes before the new work of its group.
            match back {
                Some(_) => (0, (0, (0, 0, 0))),
                None => {
                    let (rank, version) = i.milestone_rank();
                    (1, (rank, version))
                }
            },
            i.priority(),
            // No failed signal: the pull request was just submitted, so it
            // waits behind the ones that review sent back.
            back.map(|(_, at)| {
                if at.is_empty() {
                    "~".to_string()
                } else {
                    at.clone()
                }
            }),
            i.created_at.clone(),
            i.number,
        )
    });
}

/// The time of the newest signal when it comes from `stage` with `outcome`.
pub fn newest_signal_time(comments: &[Comment], stage: Stage, outcome: Outcome) -> Option<String> {
    latest_signal_at(comments)
        .filter(|(_, s)| s.stage == stage && s.outcome == outcome)
        .map(|(at, _)| at.to_string())
}

/// The newest `aw` signal in the comments.
pub fn latest_signal(comments: &[Comment]) -> Option<Signal> {
    latest_signal_at(comments).map(|(_, s)| s)
}

/// The newest `aw` signal and the time of its comment.
fn latest_signal_at(comments: &[Comment]) -> Option<(&str, Signal)> {
    comments
        .iter()
        .filter_map(|c| Signal::parse(&c.body).map(|s| (c.created_at.as_str(), s)))
        .max_by(|a, b| a.0.cmp(b.0))
}

/// The newest signal on the issue comes from `stage` with `outcome`.
pub fn newest_signal_is(comments: &[Comment], stage: Stage, outcome: Outcome) -> bool {
    latest_signal(comments).is_some_and(|s| s.stage == stage && s.outcome == outcome)
}

/// True until `agent` reports on `stage`. A signal for another stage or
/// from another agent does not count, so the planner can move the stage or
/// the agent label to make the issue work again. A `blocked` signal stops
/// counting when a person or the planner comments after it: that comment is
/// the answer, so the same stage can claim the issue again.
pub fn awaiting_report(stage: Stage, agent: &str, comments: &[Comment]) -> bool {
    let Some((at, s)) = latest_signal_at(comments) else {
        return true;
    };
    if s.stage != stage || s.agent != agent {
        return true;
    }
    (s.outcome == Outcome::Blocked
        && comments
            .iter()
            .any(|c| c.created_at.as_str() > at && !is_aw_comment(&c.body)))
        || desk_sent_back(comments)
}

/// A comment that starts with `desk:` or `desk,` is newer than the newest
/// `aw` signal. It is how the desk sends an issue back to the stage that
/// its label names: the label alone is not enough when the last signal is
/// `ready` of that stage (faberline/aw#596).
pub fn desk_sent_back(comments: &[Comment]) -> bool {
    let Some((at, _)) = latest_signal_at(comments) else {
        return false;
    };
    comments.iter().any(|c| {
        let body = c.body.trim_start().to_lowercase();
        c.created_at.as_str() > at
            && !is_aw_comment(&c.body)
            && (body.starts_with("desk:") || body.starts_with("desk,"))
    })
}

/// The line a spec writes in its message to ask for a redo from the
/// newest default branch.
pub const REDO_MARKER: &str = "aw:redo";

/// The newest spec signal asks for a redo, and it is not done yet. The redo
/// is done by a claim that gave a new branch, or by a tdd or review signal
/// that is not `blocked`. A `blocked` signal, or a claim that kept the old
/// branch, does not end it (faberline/aw#620).
pub fn redo_requested(comments: &[Comment]) -> bool {
    let newest_spec = comments
        .iter()
        .filter(|c| Signal::parse(&c.body).is_some_and(|s| s.stage == Stage::Spec))
        .max_by(|a, b| a.created_at.cmp(&b.created_at));
    let Some(spec) = newest_spec else {
        return false;
    };
    let asked = spec
        .body
        .lines()
        .skip(1)
        .any(|l| l.trim().eq_ignore_ascii_case(REDO_MARKER));
    // The branch of the last work claim before the spec: a claim on another
    // branch made a new one.
    let before = comments
        .iter()
        .filter(|c| c.created_at < spec.created_at)
        .filter_map(|c| Some((&c.created_at, ClaimMark::parse(&c.body)?)))
        .filter(|(_, m)| m.stage != Stage::Spec)
        .max_by_key(|(at, _)| *at)
        .map(|(_, m)| m.branch);
    asked
        && !comments
            .iter()
            .filter(|c| c.created_at >= spec.created_at)
            .any(|c| {
                Signal::parse(&c.body)
                    .is_some_and(|s| s.stage != Stage::Spec && s.outcome != Outcome::Blocked)
                    || ClaimMark::parse(&c.body).is_some_and(|m| {
                        m.stage != Stage::Spec
                            && before.as_ref() != Some(&m.branch)
                            && (before.is_some() || is_rework_branch(&m.branch))
                    })
            })
}

/// After this many tdd `respec` signals with no answer of a person between
/// them, the spec stage stops and asks the desk (faberline/aw#659).
pub const RESPEC_LIMIT: usize = 3;

/// The tdd `respec` signals that came after the newest comment of a person.
/// A comment of a person (the desk, the planner) is the answer: it starts the
/// count again.
pub fn respecs_since_answer(comments: &[Comment]) -> usize {
    let answered = comments
        .iter()
        .filter(|c| !is_aw_comment(&c.body))
        .map(|c| c.created_at.as_str())
        .max()
        .unwrap_or("");
    comments
        .iter()
        .filter(|c| c.created_at.as_str() > answered)
        .filter_map(|c| Signal::parse(&c.body))
        .filter(|s| s.stage == Stage::Tdd && s.outcome == Outcome::Respec)
        .count()
}

/// A signal or a claim that `aw` wrote.
fn is_aw_comment(body: &str) -> bool {
    Signal::parse(body).is_some() || ClaimMark::parse(body).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn issue(number: u64, labels: &[&str], milestone: Option<&str>) -> Issue {
        Issue {
            number,
            title: String::new(),
            url: String::new(),
            labels: labels.iter().map(|l| l.to_string()).collect(),
            created_at: format!("2026-01-{number:02}T00:00:00Z"),
            milestone: milestone.map(String::from),
        }
    }

    fn order_of(mut v: Vec<Issue>) -> Vec<u64> {
        claim_order(&mut v);
        v.iter().map(|i| i.number).collect()
    }

    #[test]
    fn p1_without_milestone_beats_p2_with_milestone() {
        let v = vec![
            issue(1, &["p2"], Some("defer@1.0.0")),
            issue(2, &["p1"], None),
        ];
        assert_eq!(order_of(v), vec![2, 1]);
    }

    #[test]
    fn a_lower_milestone_beats_a_higher_one_at_the_same_priority() {
        let v = vec![
            issue(1, &["p2"], Some("defer@1.0.0")),
            issue(2, &["p2"], Some("defer@0.7.0")),
        ];
        assert_eq!(order_of(v), vec![2, 1]);
    }

    #[test]
    fn milestone_0_10_0_sorts_after_0_9_0() {
        let v = vec![
            issue(1, &["p2"], Some("core@0.10.0")),
            issue(2, &["p2"], Some("core@0.9.0")),
        ];
        assert_eq!(order_of(v), vec![2, 1]);
    }

    fn comment(body: &str, at: &str) -> Comment {
        Comment {
            id: 0,
            body: body.to_string(),
            created_at: at.to_string(),
        }
    }

    const SPEC_READY: &str = "<!-- aw:signal stage=spec agent=claude outcome=ready -->";
    const SPEC_BLOCKED: &str = "<!-- aw:signal stage=spec agent=claude outcome=blocked -->";

    #[test]
    fn a_blocked_signal_waits_for_an_answer() {
        let blocked = [comment(SPEC_BLOCKED, "2026-11-08T16:10:00Z")];
        assert!(!awaiting_report(Stage::Spec, "claude", &blocked));
    }

    #[test]
    fn an_answer_after_a_blocked_signal_reopens_the_stage() {
        let answered = [
            comment("Planner: do option (a).", "2026-11-08T16:00:00Z"),
            comment(SPEC_BLOCKED, "2026-11-08T16:10:00Z"),
            comment("Planner: the needs are closed now.", "2026-11-08T18:11:00Z"),
        ];
        assert!(awaiting_report(Stage::Spec, "claude", &answered));
    }

    #[test]
    fn only_aw_comments_after_a_blocked_signal_do_not_reopen_it() {
        let only_aw = [
            comment(SPEC_BLOCKED, "2026-11-08T16:10:00Z"),
            comment(
                "<!-- aw:claim stage=spec agent=claude branch=- pr=- at=1800000000 -->",
                "2026-11-08T17:00:00Z",
            ),
        ];
        assert!(!awaiting_report(Stage::Spec, "claude", &only_aw));
    }

    #[test]
    fn an_answer_after_a_ready_signal_does_not_reopen_the_stage() {
        let ready = [
            comment(
                "<!-- aw:signal stage=spec agent=claude outcome=ready -->",
                "2026-11-08T16:10:00Z",
            ),
            comment("Looks good.", "2026-11-08T18:11:00Z"),
        ];
        assert!(!awaiting_report(Stage::Spec, "claude", &ready));
    }

    #[test]
    fn only_a_new_branch_or_a_non_blocked_signal_ends_a_redo() {
        let claim = |branch: &str, at: &str| {
            comment(
                &format!("<!-- aw:claim stage=tdd agent=claude branch={branch} pr=- at=1 -->"),
                at,
            )
        };
        let old = claim("c/issue-7", "2026-11-08T15:00:00Z");
        let asked = comment(
            &format!("{SPEC_READY}\n**aw**\n\naw:redo\n"),
            "2026-11-08T16:00:00Z",
        );
        // A claim on the old branch keeps the redo; one on another branch ends it.
        let same = claim("c/issue-7", "2026-11-08T17:00:00Z");
        assert!(redo_requested(&[old.clone(), asked.clone(), same]));
        let other = claim("c/issue-7-r2", "2026-11-08T17:00:00Z");
        assert!(!redo_requested(&[old.clone(), asked.clone(), other]));
        // With no claim before the spec, a plain branch keeps it too.
        let plain = claim("c/issue-7", "2026-11-08T17:00:00Z");
        assert!(redo_requested(&[asked.clone(), plain]));
        // A tdd `ready` signal ends it.
        let ready = comment(
            "<!-- aw:signal stage=tdd agent=claude outcome=ready -->",
            "2026-11-08T18:00:00Z",
        );
        assert!(!redo_requested(&[old, asked, ready]));
    }

    #[test]
    fn a_spec_redo_marker_asks_once() {
        let spec = |msg: &str| format!("{SPEC_READY}\n**aw**\n\n{msg}");
        let asked = comment(&spec("Start fresh.\naw:redo\n"), "2026-11-08T16:00:00Z");
        assert!(redo_requested(std::slice::from_ref(&asked)));
        let plain = comment(&spec("Start fresh."), "2026-11-08T16:00:00Z");
        assert!(!redo_requested(&[plain]));
        assert!(!redo_requested(&[]));
        let claimed = comment(
            "<!-- aw:claim stage=tdd agent=claude branch=issue/7-r2 pr=- at=1800000000 -->",
            "2026-11-08T17:00:00Z",
        );
        assert!(!redo_requested(&[asked.clone(), claimed]));
        let blocked = comment(
            "<!-- aw:signal stage=tdd agent=claude outcome=blocked -->",
            "2026-11-08T17:00:00Z",
        );
        // A `blocked` signal does not end the redo (faberline/aw#620).
        assert!(redo_requested(&[asked.clone(), blocked.clone()]));
        // A newer spec that does not ask supersedes the old request.
        let newer = comment(&spec("Keep the branch."), "2026-11-08T18:00:00Z");
        assert!(!redo_requested(&[asked, newer]));
    }

    #[test]
    fn a_needs_label_is_missing_until_the_worker_has_it() {
        let i = issue(1, &["agent:claude", "needs:mac", "needs:gpu"], None);
        assert_eq!(i.missing_need(&[]), Some("mac"));
        assert_eq!(i.missing_need(&["mac".into()]), Some("gpu"));
        assert_eq!(i.missing_need(&["gpu".into(), "mac".into()]), None);
        assert_eq!(issue(2, &["p1"], None).missing_need(&[]), None);
    }

    #[test]
    fn milestone_titles_give_versions() {
        assert_eq!(milestone_version("core@0.5.0"), Some((0, 5, 0)));
        assert_eq!(milestone_version("lumen@0.10.0"), Some((0, 10, 0)));
        assert_eq!(milestone_version("v1.2"), Some((1, 2, 0)));
        assert_eq!(milestone_version("Shared observability runtime"), None);
        assert_eq!(milestone_version("x@1.2.3.4"), None);
    }

    #[test]
    fn p1_then_the_lowest_milestone_come_first_after_p0() {
        let mut issues = vec![
            issue(1, &["p1"], None),
            issue(2, &["p1"], Some("lumen@0.10.0")),
            issue(3, &["p3"], Some("lumen@0.7.0")),
            issue(4, &["p2"], Some("Backlog")),
            issue(5, &["p0"], None),
            issue(6, &["p1"], Some("lumen@0.7.0")),
        ];
        claim_order(&mut issues);
        let order: Vec<u64> = issues.iter().map(|i| i.number).collect();
        assert_eq!(order, [5, 6, 2, 1, 3, 4]);
    }

    #[test]
    fn a_returned_issue_comes_before_older_issues_of_its_own_priority_only() {
        let mut issues = vec![
            issue(1, &["p1"], None),
            issue(2, &["p2"], None),
            issue(3, &["p1"], None),
            issue(4, &["p2"], None),
        ];
        claim_order_returned(&mut issues, &[(3, String::new()), (4, String::new())]);
        let order: Vec<u64> = issues.iter().map(|i| i.number).collect();
        assert_eq!(order, [3, 1, 4, 2]);
    }

    fn order(mut issues: Vec<Issue>) -> Vec<u64> {
        claim_order(&mut issues);
        issues.iter().map(|i| i.number).collect()
    }

    #[test]
    fn bar_work_comes_after_p0_and_before_the_milestone_version() {
        // The milestones inside the bar have the higher versions.
        let issues = vec![
            issue(1, &["p1"], Some("lumen@0.9.0")),
            issue(2, &["p3", "bar"], Some("lumen@0.22.0")),
            issue(3, &["p0"], Some("lumen@0.12.0")),
            issue(4, &["p1", "bar"], Some("lumen@0.21.0")),
            issue(5, &["p2", "bar"], None),
            issue(6, &["p0", "bar"], Some("lumen@0.29.0")),
            issue(7, &["p1", "bar"], Some("lumen@0.22.0")),
            issue(8, &[], Some("lumen@0.9.0")),
        ];
        // p0 first (the mark, then the version); then the marked issues by
        // version, priority and age; then the other issues.
        assert_eq!(order(issues), [6, 3, 4, 7, 2, 5, 1, 8]);
    }

    #[test]
    fn a_ci_fix_comes_after_p0_and_before_bar_work() {
        // relay, 2026-11-12: the repair of a red `lint` on main (relay#155,
        // p1, no `bar`) waited behind `bar` issues that could not pass.
        let issues = vec![
            issue(1, &["p1", "bar"], Some("relay@0.9.0")),
            issue(2, &["p2", "bar"], Some("relay@0.9.0")),
            issue(155, &["p1", "ci-fix"], None),
            issue(3, &["p0"], None),
            issue(4, &["p3"], Some("relay@0.1.0")),
            issue(5, &["p0", "ci-fix"], None),
            issue(6, &["p3", "ci-fix", "bar"], Some("relay@0.9.0")),
        ];
        // p0 (a repair first), then the repairs by the usual order (the
        // `bar` and the milestone, then the priority), then the `bar`
        // issues, then the rest.
        assert_eq!(order(issues), [5, 3, 6, 155, 1, 2, 4]);
    }

    #[test]
    fn a_ci_fix_does_not_pass_p0_and_a_lookalike_label_does_not_count() {
        let issues = vec![
            issue(1, &["p1", "ci-fix"], None),
            issue(2, &["p0"], None),
            issue(3, &["p1", "bar"], None),
            issue(4, &["p1", "ci-fixes"], None),
            issue(5, &["p1", "ci"], None),
        ];
        assert_eq!(order(issues), [2, 1, 3, 4, 5]);
    }

    #[test]
    fn without_the_ci_fix_label_the_order_does_not_change() {
        let issues = vec![
            issue(1, &["p1"], Some("lumen@0.9.0")),
            issue(2, &["p3", "bar"], Some("lumen@0.22.0")),
            issue(3, &["p0"], Some("lumen@0.12.0")),
            issue(4, &["p2"], None),
        ];
        assert_eq!(order(issues), [3, 2, 1, 4]);
    }

    #[test]
    fn without_the_bar_label_the_order_does_not_change() {
        fn issues(mark: Option<&str>) -> Vec<Issue> {
            let with = |own: &str, milestone: Option<&str>, number: u64| {
                let labels: Vec<&str> = [own].into_iter().chain(mark).collect();
                issue(number, &labels, milestone)
            };
            vec![
                with("p1", None, 1),
                with("p1", Some("lumen@0.10.0"), 2),
                with("p3", Some("lumen@0.7.0"), 3),
                with("p2", Some("Backlog"), 4),
                with("p0", None, 5),
                with("p1", Some("lumen@0.7.0"), 6),
            ]
        }
        assert_eq!(order(issues(None)), [5, 6, 2, 1, 3, 4]);
        // Also when every issue has the mark, and for a label that only
        // looks like it.
        assert_eq!(order(issues(Some("bar"))), [5, 6, 2, 1, 3, 4]);
        assert_eq!(order(issues(Some("bars"))), [5, 6, 2, 1, 3, 4]);
    }
}
