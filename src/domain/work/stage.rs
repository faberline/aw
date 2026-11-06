use std::fmt;

use serde::{Deserialize, Serialize};

/// The stage of a work item. The planner sets it with one `stage:*` label.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Stage {
    Spec,
    Tdd,
    Review,
}

impl Stage {
    pub fn as_str(self) -> &'static str {
        match self {
            Stage::Spec => "spec",
            Stage::Tdd => "tdd",
            Stage::Review => "review",
        }
    }

    pub fn parse(value: &str) -> Option<Stage> {
        match value {
            "spec" => Some(Stage::Spec),
            "tdd" => Some(Stage::Tdd),
            "review" => Some(Stage::Review),
            _ => None,
        }
    }

    /// The stage named by exactly one `stage:spec|tdd|review` label.
    /// `None` when there is no such label, more than one, or `stage:done`.
    pub fn from_labels(labels: &[String]) -> Option<Stage> {
        if labels.iter().any(|l| l == "stage:done") {
            return None;
        }
        let mut found = labels
            .iter()
            .filter_map(|l| l.strip_prefix("stage:"))
            .filter_map(Stage::parse);
        let first = found.next()?;
        match found.next() {
            Some(_) => None,
            None => Some(first),
        }
    }
}

impl fmt::Display for Stage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels(names: &[&str]) -> Vec<String> {
        names.iter().map(|n| n.to_string()).collect()
    }

    #[test]
    fn one_stage_label_names_the_stage() {
        assert_eq!(
            Stage::from_labels(&labels(&["p1", "stage:tdd"])),
            Some(Stage::Tdd)
        );
    }

    #[test]
    fn zero_several_or_done_is_not_actionable() {
        assert_eq!(Stage::from_labels(&labels(&["p1"])), None);
        assert_eq!(
            Stage::from_labels(&labels(&["stage:spec", "stage:tdd"])),
            None
        );
        assert_eq!(
            Stage::from_labels(&labels(&["stage:tdd", "stage:done"])),
            None
        );
    }
}
