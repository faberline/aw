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
    /// tdd found that the spec lacks a file or a rule; the issue goes back
    /// to spec and the message names the exact gap.
    Respec,
    /// review squash-merged the pull request.
    Passed,
    /// review asked for changes.
    Failed,
    /// review closed the pull request without a merge; tdd starts again
    /// from the default branch.
    Closed,
    /// spec found the issue too large for one pull request; `aw` made the
    /// child issues that the message names and closed this issue.
    Split,
}

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Ready => "ready",
            Outcome::Blocked => "blocked",
            Outcome::Respec => "respec",
            Outcome::Passed => "passed",
            Outcome::Failed => "failed",
            Outcome::Closed => "closed",
            Outcome::Split => "split",
        }
    }

    pub fn parse(value: &str) -> Option<Outcome> {
        match value {
            "ready" => Some(Outcome::Ready),
            "blocked" => Some(Outcome::Blocked),
            "respec" => Some(Outcome::Respec),
            "passed" => Some(Outcome::Passed),
            "failed" => Some(Outcome::Failed),
            "closed" => Some(Outcome::Closed),
            "split" => Some(Outcome::Split),
            _ => None,
        }
    }

    pub fn allowed_in(self, stage: Stage) -> bool {
        matches!(
            (stage, self),
            (_, Outcome::Blocked)
                | (Stage::Spec, Outcome::Split)
                | (Stage::Spec | Stage::Tdd, Outcome::Ready)
                | (Stage::Tdd, Outcome::Respec)
                | (
                    Stage::Review,
                    Outcome::Passed | Outcome::Failed | Outcome::Closed
                )
        )
    }

    pub fn allowed(stage: Stage) -> &'static [Outcome] {
        match stage {
            Stage::Spec => &[Outcome::Ready, Outcome::Split, Outcome::Blocked],
            Stage::Tdd => &[Outcome::Ready, Outcome::Respec, Outcome::Blocked],
            Stage::Review => &[
                Outcome::Passed,
                Outcome::Failed,
                Outcome::Closed,
                Outcome::Blocked,
            ],
        }
    }

    pub fn needs_message(self) -> bool {
        matches!(
            self,
            Outcome::Blocked | Outcome::Respec | Outcome::Failed | Outcome::Closed | Outcome::Split
        )
    }
}

/// Prefixes that an agent can put before the reason of a `blocked` report.
const BLOCKED_PREFIXES: [&str; 3] = ["tdd blocked:", "blocked:", "tdd:"];

/// Words that say the spec lacks something.
const SPEC_GAP_WORDS: [&str; 6] = [
    "unclear",
    "incomplete",
    "not complete",
    "missing",
    "lacks",
    "does not meet",
];

/// True when the first line of a `blocked` message says that the spec is
/// the cause: it starts with `spec` or `the spec` and has a gap word, for
/// example `spec unclear: ...` or `tdd blocked: the spec is missing points`.
/// A tdd report with such a message is a `respec`, not a `blocked`: the
/// spec stage can fix it, so no person is needed.
pub fn names_spec_gap(message: &str) -> bool {
    let first = message.lines().next().unwrap_or("").trim().to_lowercase();
    let mut reason = first.as_str();
    for prefix in BLOCKED_PREFIXES {
        if let Some(rest) = reason.strip_prefix(prefix) {
            reason = rest.trim_start();
            break;
        }
    }
    let about_spec = reason.starts_with("spec ") || reason.starts_with("the spec ");
    about_spec && SPEC_GAP_WORDS.iter().any(|word| reason.contains(word))
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
    /// `respec` moves a tdd issue back to `stage:spec` with no `blocked`.
    pub fn label_move(self, stage: Stage) -> LabelMove {
        let to = match (stage, self) {
            (_, Outcome::Blocked) => {
                return LabelMove {
                    remove: None,
                    add: vec!["blocked".into()],
                };
            }
            // The children carry the work on; the closed parent keeps its labels.
            (_, Outcome::Split) => {
                return LabelMove {
                    remove: None,
                    add: Vec::new(),
                };
            }
            (Stage::Tdd, Outcome::Respec) => "stage:spec",
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
        assert_eq!(
            to(Stage::Tdd, Outcome::Respec),
            (some("stage:tdd"), one("stage:spec"))
        );
        assert!(Outcome::Respec.allowed_in(Stage::Tdd));
        assert!(!Outcome::Respec.allowed_in(Stage::Spec));
        assert!(!Outcome::Respec.allowed_in(Stage::Review));
        assert!(Outcome::Respec.needs_message());
        assert_eq!(Outcome::parse("respec"), Some(Outcome::Respec));
    }

    #[test]
    fn split_applies_to_spec_only_and_moves_no_label() {
        assert!(Outcome::Split.allowed_in(Stage::Spec));
        assert!(!Outcome::Split.allowed_in(Stage::Tdd));
        assert!(!Outcome::Split.allowed_in(Stage::Review));
        assert!(Outcome::allowed(Stage::Spec).contains(&Outcome::Split));
        assert!(Outcome::Split.needs_message());
        assert_eq!(Outcome::parse("split"), Some(Outcome::Split));
        assert_eq!(Outcome::Split.to_string(), "split");
        let m = Outcome::Split.label_move(Stage::Spec);
        assert_eq!(m.remove, None);
        assert!(m.add.is_empty());
    }

    #[test]
    fn a_blocked_message_about_a_spec_gap_is_found() {
        for message in [
            "spec unclear: the issue body does not meet the spec checklist.",
            "Spec incomplete: no test is named",
            "tdd blocked: the spec is missing points that the tdd checklist needs.\n\n1. x",
            "blocked: the spec lacks tests/a.rs",
            "tdd: spec does not meet the checklist",
            "  the spec is not complete",
        ] {
            assert!(names_spec_gap(message), "{message}");
        }
    }

    #[test]
    fn a_blocked_message_for_a_person_is_not_a_spec_gap() {
        for message in [
            "",
            "the push was refused for branch claude/issue-1",
            "the owner must choose between A and B",
            "the spec needs a decision of the owner",
            "cargo test fails\nthe spec is unclear too",
            "tests are missing a fixture; spec unclear",
        ] {
            assert!(!names_spec_gap(message), "{message}");
        }
    }
}
