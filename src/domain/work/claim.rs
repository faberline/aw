use serde::{Deserialize, Serialize};

use super::issue::{Comment, Issue};
use super::ports::Repo;
use super::stage::Stage;

/// A claim lives longer than one run only when the run died. After this
/// many seconds a new `aw claim` replaces it.
pub const STALE_AFTER_SECONDS: i64 = 3 * 60 * 60;

const MARK_PREFIX: &str = "<!-- aw:claim ";

/// The one issue an agent works on now in one repository. `aw claim`
/// posts it as an issue comment, `aw report` turns that comment into the
/// signal and `aw release` deletes it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    pub repo: String,
    pub agent: String,
    pub issue: u64,
    pub stage: Stage,
    pub title: String,
    pub url: String,
    /// spec: the default branch. tdd: the head of the open pull request
    /// that closes the issue, else `<agent>/issue-<n>`. review: the pull
    /// request head.
    pub branch: String,
    pub pr: Option<u64>,
    pub default_branch: String,
    /// Unix seconds.
    pub claimed_at: i64,
}

impl Claim {
    pub fn is_stale(&self, now: i64) -> bool {
        now - self.claimed_at >= STALE_AFTER_SECONDS
    }

    /// The part of the claim that the issue comment stores.
    pub fn mark(&self) -> ClaimMark {
        ClaimMark {
            agent: self.agent.clone(),
            stage: self.stage,
            branch: self.branch.clone(),
            pr: self.pr,
            claimed_at: self.claimed_at,
        }
    }

    /// Rebuild the claim from its comment and the issue it is on.
    pub fn from_mark(mark: ClaimMark, issue: &Issue, repo: &Repo) -> Claim {
        Claim {
            repo: repo.name.clone(),
            agent: mark.agent,
            issue: issue.number,
            stage: mark.stage,
            title: issue.title.clone(),
            url: issue.url.clone(),
            branch: mark.branch,
            pr: mark.pr,
            default_branch: repo.default_branch.clone(),
            claimed_at: mark.claimed_at,
        }
    }
}

/// The claim comment. Its first line is machine readable:
/// `<!-- aw:claim stage=tdd agent=agy branch=agy/issue-7 pr=- at=1800000000 -->`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClaimMark {
    pub agent: String,
    pub stage: Stage,
    pub branch: String,
    pub pr: Option<u64>,
    /// Unix seconds.
    pub claimed_at: i64,
}

impl ClaimMark {
    pub fn render(&self) -> String {
        let pr = self.pr.map_or("-".to_string(), |n| n.to_string());
        let shown_pr = self
            .pr
            .map_or(String::new(), |n| format!(", pull request #{n}"));
        format!(
            "{MARK_PREFIX}stage={} agent={} branch={} pr={pr} at={} -->\n\
             **aw** · `{}` · {} · **claimed** (branch `{}`{shown_pr})",
            self.stage,
            self.agent,
            self.branch,
            self.claimed_at,
            self.agent,
            self.stage,
            self.branch,
        )
    }

    /// Read a claim mark from the first line of a comment body.
    pub fn parse(body: &str) -> Option<ClaimMark> {
        let line = body.lines().next()?.trim();
        let fields = line.strip_prefix(MARK_PREFIX)?.strip_suffix("-->")?;
        let (mut stage, mut agent, mut branch, mut pr, mut at) = (None, None, None, None, None);
        for pair in fields.split_whitespace() {
            let (key, value) = pair.split_once('=')?;
            match key {
                "stage" => stage = Some(Stage::parse(value)?),
                "agent" => agent = Some(value.to_string()),
                "branch" => branch = Some(value.to_string()),
                "pr" if value == "-" => pr = Some(None),
                "pr" => pr = Some(Some(value.parse().ok()?)),
                "at" => at = Some(value.parse().ok()?),
                _ => {}
            }
        }
        Some(ClaimMark {
            agent: agent?,
            stage: stage?,
            branch: branch?,
            pr: pr?,
            claimed_at: at?,
        })
    }
}

/// The newest claim comment by `agent`, with its comment id.
pub fn latest_mark(comments: &[Comment], agent: &str) -> Option<(u64, ClaimMark)> {
    comments
        .iter()
        .filter_map(|c| ClaimMark::parse(&c.body).map(|m| (c, m)))
        .filter(|(_, m)| m.agent == agent)
        .max_by(|a, b| a.0.created_at.cmp(&b.0.created_at))
        .map(|(c, m)| (c.id, m))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mark(pr: Option<u64>) -> ClaimMark {
        ClaimMark {
            agent: "agy".into(),
            stage: Stage::Tdd,
            branch: "agy/issue-7".into(),
            pr,
            claimed_at: 1_800_000_000,
        }
    }

    #[test]
    fn render_and_parse_round_trip() {
        for pr in [None, Some(51)] {
            let m = mark(pr);
            assert_eq!(ClaimMark::parse(&m.render()), Some(m));
        }
    }

    #[test]
    fn the_first_line_is_the_marker() {
        let text = mark(Some(51)).render();
        assert_eq!(
            text.lines().next().unwrap(),
            "<!-- aw:claim stage=tdd agent=agy branch=agy/issue-7 pr=51 at=1800000000 -->"
        );
        assert!(text.contains("**claimed**"));
    }

    #[test]
    fn other_comments_are_not_claims() {
        assert_eq!(ClaimMark::parse("hello"), None);
        assert_eq!(
            ClaimMark::parse("<!-- aw:signal stage=tdd agent=agy outcome=ready -->"),
            None
        );
        assert_eq!(
            ClaimMark::parse("<!-- aw:claim stage=tdd agent=agy -->"),
            None
        );
    }

    #[test]
    fn latest_mark_picks_the_newest_for_the_agent() {
        let comment = |id, m: ClaimMark, at: &str| Comment {
            id,
            body: m.render(),
            created_at: at.into(),
        };
        let mut other = mark(None);
        other.agent = "codex".into();
        let comments = vec![
            comment(1, mark(None), "2026-01-01T00:00:00Z"),
            comment(2, mark(Some(5)), "2026-01-02T00:00:00Z"),
            comment(3, other, "2026-01-03T00:00:00Z"),
        ];
        assert_eq!(latest_mark(&comments, "agy").map(|(id, _)| id), Some(2));
        assert_eq!(latest_mark(&comments, "claude"), None);
    }
}
