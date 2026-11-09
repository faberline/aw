use super::claim::ClaimMark;
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

/// Agent ids are short lowercase names such as `claude` or `codex`.
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
    /// A legacy `priority:pN` label counts when no `pN` label is set.
    pub fn priority(&self) -> u8 {
        let find = |prefix: &str| (0..4u8).find(|n| self.has_label(&format!("{prefix}p{n}")));
        find("").or_else(|| find("priority:")).unwrap_or(4)
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

/// Claim order: `p0` first, then the lowest milestone version, then the
/// priority label (unlabeled last), then the oldest issue.
pub fn claim_order(issues: &mut [Issue]) {
    issues.sort_by(|a, b| {
        (a.priority() != 0)
            .cmp(&(b.priority() != 0))
            .then_with(|| a.milestone_rank().cmp(&b.milestone_rank()))
            .then_with(|| a.priority().cmp(&b.priority()))
            .then_with(|| a.created_at.cmp(&b.created_at))
            .then_with(|| a.number.cmp(&b.number))
    });
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
    s.outcome == Outcome::Blocked
        && comments
            .iter()
            .any(|c| c.created_at.as_str() > at && !is_aw_comment(&c.body))
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

    fn comment(body: &str, at: &str) -> Comment {
        Comment {
            id: 0,
            body: body.to_string(),
            created_at: at.to_string(),
        }
    }

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
    fn milestone_titles_give_versions() {
        assert_eq!(milestone_version("core@0.5.0"), Some((0, 5, 0)));
        assert_eq!(milestone_version("lumen@0.10.0"), Some((0, 10, 0)));
        assert_eq!(milestone_version("v1.2"), Some((1, 2, 0)));
        assert_eq!(milestone_version("Shared observability runtime"), None);
        assert_eq!(milestone_version("x@1.2.3.4"), None);
    }

    #[test]
    fn the_lowest_milestone_comes_first_after_p0() {
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
        assert_eq!(order, [5, 6, 3, 2, 4, 1]);
    }

    #[test]
    fn a_legacy_priority_label_counts_when_no_short_one_is_set() {
        assert_eq!(issue(1, &["priority:p1"], None).priority(), 1);
        assert_eq!(issue(2, &["p3", "priority:p0"], None).priority(), 3);
        assert_eq!(issue(3, &["priority"], None).priority(), 4);
        let mut issues = vec![
            issue(4, &["priority:p2"], Some("som@0.6.0")),
            issue(5, &["priority:p1"], Some("som@0.6.0")),
            issue(6, &["priority:p0"], Some("som@0.7.0")),
        ];
        claim_order(&mut issues);
        let order: Vec<u64> = issues.iter().map(|i| i.number).collect();
        assert_eq!(order, [6, 5, 4]);
    }
}
