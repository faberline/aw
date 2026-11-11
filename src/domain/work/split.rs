//! The split of an issue that is too large for one pull request.
//!
//! The spec stage reports `split` with a message that names the child
//! issues. `aw` makes them, so no person has to.

use std::collections::BTreeSet;

/// The least number of children. One child is the same issue again.
pub const MIN_CHILDREN: usize = 2;
/// The most children that one spec run makes.
pub const MAX_CHILDREN: usize = 5;

/// The line that starts a child in the message: `## Child: <title>`.
const CHILD_HEADING: &str = "## child:";

const MARK_OPEN: &str = "<!-- aw:child parent=";
const MARK_CLOSE: &str = " -->";

/// One child issue to make.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Child {
    pub title: String,
    pub body: String,
}

/// What a `split` message asks for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SplitPlan {
    /// The text before the first child: why the issue is too large.
    pub reason: String,
    pub children: Vec<Child>,
}

impl SplitPlan {
    /// Read a `split` message. Each child starts with a line
    /// `## Child: <title>`; the lines after it, up to the next child, are
    /// its body. The text before the first child is the reason.
    pub fn parse(message: &str) -> Result<SplitPlan, String> {
        let mut reason = String::new();
        let mut children: Vec<Child> = Vec::new();
        for line in message.lines() {
            if let Some(title) = child_title(line) {
                children.push(Child {
                    title: title.to_string(),
                    body: String::new(),
                });
                continue;
            }
            let text = match children.last_mut() {
                Some(child) => &mut child.body,
                None => &mut reason,
            };
            text.push_str(line);
            text.push('\n');
        }
        for child in &mut children {
            child.body = child.body.trim().to_string();
        }
        let plan = SplitPlan {
            reason: reason.trim().to_string(),
            children,
        };
        plan.check()?;
        Ok(plan)
    }

    fn check(&self) -> Result<(), String> {
        let count = self.children.len();
        if count < MIN_CHILDREN {
            return Err(format!(
                "a split needs at least {MIN_CHILDREN} children, each under a line \
                 `## Child: <title>`; the message has {count}"
            ));
        }
        if count > MAX_CHILDREN {
            return Err(format!(
                "a split makes at most {MAX_CHILDREN} children; the message has {count}. \
                 Make larger children, or report `blocked` and name the decision"
            ));
        }
        let mut titles = BTreeSet::new();
        for (index, child) in self.children.iter().enumerate() {
            let place = index + 1;
            if child.title.is_empty() {
                return Err(format!("child {place} has no title"));
            }
            if child.body.is_empty() {
                return Err(format!(
                    "child {place} (`{}`) has no text; write what it must do",
                    child.title
                ));
            }
            if !titles.insert(child.title.to_lowercase()) {
                return Err(format!("two children have the title `{}`", child.title));
            }
        }
        Ok(())
    }
}

/// The title of a child heading line, or `None` for any other line.
fn child_title(line: &str) -> Option<&str> {
    let line = line.trim();
    let head = line.get(..CHILD_HEADING.len())?;
    head.eq_ignore_ascii_case(CHILD_HEADING)
        .then(|| line[CHILD_HEADING.len()..].trim())
}

/// The body of a child issue: a hidden mark that names the parent, a
/// visible line with the link, and the text of the child.
pub fn child_body(parent: u64, text: &str) -> String {
    format!(
        "{MARK_OPEN}{parent}{MARK_CLOSE}\nPart of #{parent}. `aw` made this issue from the split \
         of that issue.\n\n{}",
        text.trim()
    )
}

/// The parent of a child issue, read from the mark in its body. An issue
/// that `aw` did not make from a split gives `None`.
pub fn child_parent(body: &str) -> Option<u64> {
    let start = body.find(MARK_OPEN)? + MARK_OPEN.len();
    let rest = &body[start..];
    let end = rest.find(MARK_CLOSE)?;
    rest[..end].trim().parse().ok()
}

/// The part of the signal comment that links the children.
pub fn replaced_by(reason: &str, made: &[(u64, String)]) -> String {
    let mut note = String::new();
    if !reason.is_empty() {
        note.push_str(reason);
        note.push_str("\n\n");
    }
    note.push_str("Replaced by:\n");
    for (number, title) in made {
        note.push_str(&format!("- #{number} {title}\n"));
    }
    note
}

#[cfg(test)]
mod tests {
    use super::*;

    const TWO: &str = "Too large: two crates and a migration.\n\n\
        ## Child: Add the port\nThe trait and its tests.\n\n\
        ## child:  Add the adapter \nThe `gh` adapter.\nWaits for the port.\n";

    #[test]
    fn a_message_gives_the_reason_and_the_children() {
        let plan = SplitPlan::parse(TWO).unwrap();
        assert_eq!(plan.reason, "Too large: two crates and a migration.");
        assert_eq!(
            plan.children,
            [
                Child {
                    title: "Add the port".into(),
                    body: "The trait and its tests.".into()
                },
                Child {
                    title: "Add the adapter".into(),
                    body: "The `gh` adapter.\nWaits for the port.".into()
                },
            ]
        );
    }

    fn children(count: usize) -> String {
        (1..=count)
            .map(|n| format!("## Child: Part {n}\nText {n}.\n"))
            .collect()
    }

    #[test]
    fn a_split_has_two_to_five_children() {
        assert!(SplitPlan::parse(&children(MIN_CHILDREN)).is_ok());
        assert!(SplitPlan::parse(&children(MAX_CHILDREN)).is_ok());
        let none = SplitPlan::parse("too large").unwrap_err();
        assert!(none.contains("at least 2"), "{none}");
        let one = SplitPlan::parse(&children(1)).unwrap_err();
        assert!(one.contains("the message has 1"), "{one}");
        let six = SplitPlan::parse(&children(6)).unwrap_err();
        assert!(six.contains("at most 5"), "{six}");
    }

    #[test]
    fn a_child_needs_a_title_a_text_and_its_own_name() {
        let no_title = SplitPlan::parse("## Child:\nText.\n## Child: B\nText.").unwrap_err();
        assert!(no_title.contains("child 1 has no title"), "{no_title}");
        let no_text = SplitPlan::parse("## Child: A\n\n## Child: B\nText.").unwrap_err();
        assert!(no_text.contains("child 1 (`A`) has no text"), "{no_text}");
        let twice = SplitPlan::parse("## Child: A\nText.\n## Child: a\nText.").unwrap_err();
        assert!(twice.contains("two children have the title"), "{twice}");
    }

    #[test]
    fn a_child_body_names_its_parent() {
        let body = child_body(7, "  Do the port.\n");
        assert!(body.starts_with("<!-- aw:child parent=7 -->\nPart of #7."));
        assert!(body.ends_with("\n\nDo the port."));
        assert_eq!(child_parent(&body), Some(7));
        assert_eq!(child_parent("A plain issue.\n\nPart of #7."), None);
        assert_eq!(child_parent("<!-- aw:child parent=x -->"), None);
        assert_eq!(child_parent("<!-- aw:child parent=7"), None);
    }

    #[test]
    fn the_note_links_each_child() {
        let made = [
            (8, "Add the port".to_string()),
            (9, "Add the adapter".to_string()),
        ];
        assert_eq!(
            replaced_by("Too large.", &made),
            "Too large.\n\nReplaced by:\n- #8 Add the port\n- #9 Add the adapter\n"
        );
        assert_eq!(
            replaced_by("", &made[..1]),
            "Replaced by:\n- #8 Add the port\n"
        );
    }
}
