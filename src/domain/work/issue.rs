use super::signal::Signal;
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
    pub fn priority(&self) -> u8 {
        ["p0", "p1", "p2", "p3"]
            .iter()
            .position(|p| self.has_label(p))
            .map_or(4, |i| i as u8)
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
    comments
        .iter()
        .filter_map(|c| Signal::parse(&c.body).map(|s| (c.created_at.as_str(), s)))
        .max_by(|a, b| a.0.cmp(b.0))
        .map(|(_, s)| s)
}

/// True until `agent` reports on `stage`. A signal for another stage or
/// from another agent does not count, so the planner can move the stage or
/// the agent label to make the issue work again.
pub fn awaiting_report(stage: Stage, agent: &str, comments: &[Comment]) -> bool {
    match latest_signal(comments) {
        Some(s) => !(s.stage == stage && s.agent == agent),
        None => true,
    }
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
}
