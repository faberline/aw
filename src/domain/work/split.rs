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

/// Turn each `Waits for: <title>` line that names the title of a child made
/// earlier into `Waits for: #<number>`, so `aw claim` can read it. A line
/// that already has a number, or names no earlier child, stays as it is.
pub fn number_waits(text: &str, made: &[(u64, String)]) -> String {
    let lines: Vec<String> = text
        .lines()
        .flat_map(own_line_waits)
        .map(|line| {
            let Some(rest) = line.trim_start().strip_prefix_ignore_case("waits for:") else {
                return line.to_string();
            };
            if rest.contains('#') {
                return line.to_string();
            }
            let want = rest.trim().trim_end_matches('.').to_lowercase();
            let numbers: Vec<String> = made
                .iter()
                .filter(|(_, title)| !title.is_empty() && want.contains(&title.to_lowercase()))
                .map(|(n, _)| format!("#{n}"))
                .collect();
            if numbers.is_empty() {
                line.to_string()
            } else {
                format!("Waits for: {}", numbers.join(", "))
            }
        })
        .collect();
    lines.join("\n")
}

/// The lines of a child text that hold a `Waits for:` with no issue number:
/// their text after the colon.
fn title_waits(text: &str) -> Vec<String> {
    text.lines()
        .flat_map(own_line_waits)
        .filter_map(|line| {
            let rest = line
                .trim_start()
                .strip_prefix_ignore_case("waits for:")?
                .to_string();
            (!rest.contains('#')).then(|| rest.trim().trim_end_matches('.').to_lowercase())
        })
        .collect()
}

/// The positions of the children whose title a waits text names, except
/// the child itself.
fn named(children: &[Child], want: &str, me: usize) -> Vec<usize> {
    children
        .iter()
        .enumerate()
        .filter(|(at, c)| {
            *at != me && !c.title.is_empty() && want.contains(&c.title.to_lowercase())
        })
        .map(|(at, _)| at)
        .collect()
}

/// The order in which to make the children, so that each child is made after
/// the children it waits for and `aw` can write their numbers into its body.
/// A `Waits for:` line with no number must name a sibling; a line that names
/// none, or a circle of waits, is an error and nothing is made.
pub fn creation_order(children: &[Child]) -> Result<Vec<usize>, String> {
    let mut deps: Vec<Vec<usize>> = Vec::new();
    for (me, child) in children.iter().enumerate() {
        let mut mine = Vec::new();
        for want in title_waits(&child.body) {
            let found = named(children, &want, me);
            if found.is_empty() {
                return Err(format!(
                    "child {} (`{}`) has a `Waits for:` line with no issue number that names \
                     no other child (`{}`). Write the exact title of a child of this split, \
                     or `Waits for: #<number>` for an issue outside it",
                    me + 1,
                    child.title,
                    want
                ));
            }
            mine.extend(found);
        }
        deps.push(mine);
    }
    let mut order: Vec<usize> = Vec::new();
    while order.len() < children.len() {
        let next = (0..children.len())
            .find(|at| !order.contains(at) && deps[*at].iter().all(|d| order.contains(d)));
        let Some(at) = next else {
            return Err(
                "the children wait for each other in a circle; no child can come \
                        first. Remove one `Waits for:` line"
                    .to_string(),
            );
        };
        order.push(at);
    }
    Ok(order)
}

/// The numbers of the earlier children that the `Waits for: <title>` lines of
/// a child text name. These are the issues that the child waits for.
pub fn waits_on(text: &str, made: &[(u64, String)]) -> Vec<u64> {
    let mut numbers: Vec<u64> = Vec::new();
    for want in title_waits(text) {
        for (n, title) in made {
            if !title.is_empty() && want.contains(&title.to_lowercase()) && !numbers.contains(n) {
                numbers.push(*n);
            }
        }
    }
    numbers
}

/// A `Waits for:` that follows a sentence in the same line (`Part of #7.
/// Waits for: #9`) moves to a line of its own. The child text that an agent
/// writes made this form (faberline/aw#601).
fn own_line_waits(line: &str) -> Vec<String> {
    let lower = line.to_ascii_lowercase();
    if let Some(at) = lower.find("waits for:")
        && at > 0
        && line[..at].ends_with(' ')
        && line[..at].trim_end().ends_with(['.', ';', '!', '?'])
    {
        return vec![line[..at].trim_end().to_string(), line[at..].to_string()];
    }
    vec![line.to_string()]
}

trait StripPrefixIgnoreCase {
    fn strip_prefix_ignore_case(&self, prefix: &str) -> Option<&str>;
}

impl StripPrefixIgnoreCase for str {
    fn strip_prefix_ignore_case(&self, prefix: &str) -> Option<&str> {
        let head = self.get(..prefix.len())?;
        head.eq_ignore_ascii_case(prefix)
            .then(|| &self[prefix.len()..])
    }
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
    fn a_waits_text_after_a_sentence_gets_a_line_of_its_own() {
        let text = "Part of #161. Waits for: #182\nWaits for: #185\nIt waits for: no one.";
        assert_eq!(
            number_waits(text, &[]),
            "Part of #161.\nWaits for: #182\nWaits for: #185\nIt waits for: no one."
        );
    }

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

    #[test]
    fn a_waits_title_becomes_the_number_of_the_earlier_child() {
        let made = vec![(21, "Add the port".to_string())];
        let out = number_waits(
            "Text\nWaits for: Add the port.\nWaits for: #5\nWaits for: other",
            &made,
        );
        assert_eq!(out, "Text\nWaits for: #21\nWaits for: #5\nWaits for: other");
    }

    fn kids(list: &[(&str, &str)]) -> Vec<Child> {
        list.iter()
            .map(|(t, b)| Child {
                title: t.to_string(),
                body: b.to_string(),
            })
            .collect()
    }

    #[test]
    fn children_without_waits_keep_their_order() {
        let c = kids(&[("A", "Text."), ("B", "Text.")]);
        assert_eq!(creation_order(&c).unwrap(), [0, 1]);
    }

    #[test]
    fn a_child_is_made_after_the_child_it_waits_for() {
        let c = kids(&[
            ("Use it", "Text.\nWaits for: Add the port, Add the adapter"),
            ("Add the port", "Text."),
            ("Add the adapter", "Text.\nWaits for: Add the port"),
        ]);
        assert_eq!(creation_order(&c).unwrap(), [1, 2, 0]);
    }

    #[test]
    fn a_waits_title_that_names_no_child_is_an_error() {
        let c = kids(&[("A", "Text.\nWaits for: the port"), ("B", "Text.")]);
        let err = creation_order(&c).unwrap_err();
        assert!(err.contains("child 1 (`A`)"), "{err}");
        assert!(err.contains("names no other child"), "{err}");
        // A child does not wait for itself.
        let own = kids(&[("A", "Text.\nWaits for: A"), ("B", "Text.")]);
        assert!(creation_order(&own).is_err());
    }

    #[test]
    fn a_circle_of_waits_is_an_error() {
        let c = kids(&[("A", "Waits for: B"), ("B", "Waits for: A")]);
        let err = creation_order(&c).unwrap_err();
        assert!(err.contains("in a circle"), "{err}");
    }

    #[test]
    fn a_numbered_waits_line_needs_no_sibling() {
        let c = kids(&[("A", "Waits for: #5, other/repo#2"), ("B", "Text.")]);
        assert_eq!(creation_order(&c).unwrap(), [0, 1]);
    }

    #[test]
    fn the_issues_a_child_waits_for_are_the_numbers_of_its_titles() {
        let made = vec![(21, "Add the port".to_string()), (22, "Other".to_string())];
        assert_eq!(
            waits_on("Waits for: Add the port.\nWaits for: #5", &made),
            [21]
        );
        assert!(waits_on("Text.", &made).is_empty());
    }
}
