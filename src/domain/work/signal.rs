use std::fmt;

use serde::{Deserialize, Serialize};

use super::stage::Stage;

/// What an agent reports at the end of a stage.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Outcome {
    /// spec or tdd is finished and waits for the planner.
    Ready,
    /// The agent cannot finish this stage; the message says why.
    Blocked,
    /// review squash-merged the pull request.
    Passed,
    /// review asked for changes.
    Failed,
    /// review closed the pull request without a merge; tdd starts again
    /// from the default branch.
    Closed,
}

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Ready => "ready",
            Outcome::Blocked => "blocked",
            Outcome::Passed => "passed",
            Outcome::Failed => "failed",
            Outcome::Closed => "closed",
        }
    }

    pub fn parse(value: &str) -> Option<Outcome> {
        match value {
            "ready" => Some(Outcome::Ready),
            "blocked" => Some(Outcome::Blocked),
            "passed" => Some(Outcome::Passed),
            "failed" => Some(Outcome::Failed),
            "closed" => Some(Outcome::Closed),
            _ => None,
        }
    }

    pub fn allowed_in(self, stage: Stage) -> bool {
        matches!(
            (stage, self),
            (_, Outcome::Blocked)
                | (Stage::Spec | Stage::Tdd, Outcome::Ready)
                | (
                    Stage::Review,
                    Outcome::Passed | Outcome::Failed | Outcome::Closed
                )
        )
    }

    pub fn allowed(stage: Stage) -> &'static [Outcome] {
        match stage {
            Stage::Spec | Stage::Tdd => &[Outcome::Ready, Outcome::Blocked],
            Stage::Review => &[
                Outcome::Passed,
                Outcome::Failed,
                Outcome::Closed,
                Outcome::Blocked,
            ],
        }
    }

    pub fn needs_message(self) -> bool {
        matches!(self, Outcome::Blocked | Outcome::Failed | Outcome::Closed)
    }
}

/// The label change that a report makes on the issue.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LabelMove {
    pub remove: Option<String>,
    pub add: Vec<String>,
}

impl Outcome {
    /// The next stage label after `stage` ends with this outcome.
    /// `blocked` keeps the stage and adds `blocked` for a person.
    pub fn label_move(self, stage: Stage) -> LabelMove {
        let to = match (stage, self) {
            (_, Outcome::Blocked) => {
                return LabelMove {
                    remove: None,
                    add: vec!["blocked".into()],
                };
            }
            (Stage::Spec, _) => "stage:tdd",
            (Stage::Tdd, _) => "stage:review",
            (Stage::Review, Outcome::Failed | Outcome::Closed) => "stage:tdd",
            (Stage::Review, _) => "stage:done",
        };
        LabelMove {
            remove: Some(format!("stage:{stage}")),
            add: vec![to.into()],
        }
    }
}

impl fmt::Display for Outcome {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A machine-readable issue comment that only `aw` writes.
///
/// The first line is an HTML comment, so it is hidden on GitHub and a
/// person's comment cannot match it by accident.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Signal {
    pub stage: Stage,
    pub agent: String,
    pub outcome: Outcome,
}

const OPEN: &str = "<!-- aw:signal ";
const CLOSE: &str = " -->";

impl Signal {
    pub fn render(&self, message: &str, link: Option<&str>) -> String {
        let mut body = format!(
            "{OPEN}stage={} agent={} outcome={}{CLOSE}\n**aw** · `{}` · {} · **{}**",
            self.stage, self.agent, self.outcome, self.agent, self.stage, self.outcome
        );
        if let Some(link) = link {
            body.push_str("\n\n");
            body.push_str(link);
        }
        let message = message.trim();
        if !message.is_empty() {
            body.push_str("\n\n");
            body.push_str(message);
        }
        body
    }

    pub fn parse(body: &str) -> Option<Signal> {
        let line = body.lines().next()?.trim();
        let inner = line.strip_prefix(OPEN)?.strip_suffix(CLOSE)?;
        let (mut stage, mut agent, mut outcome) = (None, None, None);
        for token in inner.split_whitespace() {
            let (key, value) = token.split_once('=')?;
            match key {
                "stage" => stage = Stage::parse(value),
                "agent" => agent = Some(value.to_string()),
                "outcome" => outcome = Outcome::parse(value),
                _ => {}
            }
        }
        Some(Signal {
            stage: stage?,
            agent: agent?,
            outcome: outcome?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_then_parse_round_trips() {
        let signal = Signal {
            stage: Stage::Tdd,
            agent: "claude".into(),
            outcome: Outcome::Ready,
        };
        let body = signal.render("details", Some("https://example/pr/1"));
        assert_eq!(Signal::parse(&body), Some(signal));
    }

    #[test]
    fn plain_text_is_not_a_signal() {
        assert_eq!(Signal::parse("tdd ready for review"), None);
        assert_eq!(Signal::parse("<!-- aw:signal stage=tdd -->"), None);
    }

    #[test]
    fn outcomes_follow_the_stage() {
        assert!(Outcome::Ready.allowed_in(Stage::Spec));
        assert!(!Outcome::Ready.allowed_in(Stage::Review));
        assert!(Outcome::Passed.allowed_in(Stage::Review));
        assert!(!Outcome::Passed.allowed_in(Stage::Tdd));
        assert!(Outcome::Blocked.allowed_in(Stage::Tdd));
    }

    #[test]
    fn each_report_moves_to_the_next_stage() {
        let to = |stage, outcome: Outcome| {
            let m = outcome.label_move(stage);
            (m.remove, m.add)
        };
        let some = |s: &str| Some(s.to_string());
        let one = |s: &str| vec![s.to_string()];
        assert_eq!(
            to(Stage::Spec, Outcome::Ready),
            (some("stage:spec"), one("stage:tdd"))
        );
        assert_eq!(
            to(Stage::Tdd, Outcome::Ready),
            (some("stage:tdd"), one("stage:review"))
        );
        assert_eq!(
            to(Stage::Review, Outcome::Failed),
            (some("stage:review"), one("stage:tdd"))
        );
        assert_eq!(
            to(Stage::Review, Outcome::Closed),
            (some("stage:review"), one("stage:tdd"))
        );
        assert!(Outcome::Closed.allowed_in(Stage::Review));
        assert!(!Outcome::Closed.allowed_in(Stage::Tdd));
        assert!(Outcome::Closed.needs_message());
        assert_eq!(Outcome::parse("closed"), Some(Outcome::Closed));
        assert_eq!(
            to(Stage::Review, Outcome::Passed),
            (some("stage:review"), one("stage:done"))
        );
        assert_eq!(to(Stage::Tdd, Outcome::Blocked), (None, one("blocked")));
    }
}
