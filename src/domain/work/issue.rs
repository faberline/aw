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
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Comment {
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

/// Claim order: `p0` first, unlabeled last, then the oldest issue.
pub fn claim_order(issues: &mut [Issue]) {
    issues.sort_by(|a, b| {
        a.priority()
            .cmp(&b.priority())
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
